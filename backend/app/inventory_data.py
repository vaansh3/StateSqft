from __future__ import annotations

import logging
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
import statsmodels.api as sm

logger = logging.getLogger(__name__)

# Minimum months of history to fit OLS trend on month index.
_OLS_TREND_MIN_MONTHS = 6
# Minimum month-over-month pairs to attempt logistic regression.
_LOGIT_MIN_PAIRS = 8

from app import settings
from app.schemas_chat import MetroVizPayload, PieChartSpec, TimeSeriesChartSpec, VerdictPayload

ID_COLS = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName"]

def _normalize_message_for_matching(message: str) -> str:
    """Undo common glued-word typos so tokens like *new york* still match the metro catalog."""
    s = message
    # Missing space before a capital letter (e.g. "inventory forNew york" -> "for New york")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    # "fornew …" -> "for new …" (lookahead: literal "new" then word boundary / space)
    s = re.sub(r"(?i)for(?=new(?:\s|,|$|[!?]))", "for ", s)
    # "yorkincrease" / "yorkdecrease" -> "york increase"
    s = re.sub(r"(?i)(york)(increase|decrease)", r"\1 \2", s)
    return s


STOPWORDS = frozenset(
    """
    the a an and or for to of in on at is are was were be been being have has had
    do does did will would could should may might must shall can need ought used
    hi hello hey show me please want get give tell about from data number numbers
    how what when where which who why with that this these those it its my your
    our their last year years month months week weeks day days time some any all
    there here more most less few many much very just only also than then not
    but if so as into out up down over under again further once both each few
    other such no nor too very can will just don should now listings listing
    compare comparison compared versus between level inventory
    january february march april may june july august september october november december
    jan feb mar apr jun jul aug sep sept oct nov dec
    """.split()
)


def _metro_phrase_regex(phrase: str) -> str:
    """Whole-word match only — avoids *level* matching *Clevel*and*."""
    p = phrase.strip().lower()
    if not p:
        return ""
    esc = re.escape(p)
    parts = esc.split(r"\ ")
    core = r"\s+".join(parts) if len(parts) > 1 else parts[0]
    return rf"(?i)\b{core}\b"


def _and_clause_is_date_comparison_only(message: str) -> bool:
    """True for e.g. *March 2020 and March 2024* (time compare, not two cities)."""
    m = message.lower()
    if " and " not in m:
        return False
    if re.search(r"20\d{2}\s+and\s+20\d{2}", m):
        return True
    return bool(
        re.search(
            r"(january|february|march|april|may|june|july|august|september|october|november|december|"
            r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+20\d{2}\s+and\s+"
            r"(january|february|march|april|may|june|july|august|september|october|november|december|"
            r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+20\d{2}",
            m,
        )
    )


_MONTH_NAME_TO_NUM: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _parse_month_year_points(message: str) -> list[tuple[int, int, str]]:
    specs: list[tuple[int, int, str]] = []
    pat = re.compile(
        r"(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+(20\d{2})\b"
    )
    for mm in pat.finditer(message):
        mon_s = mm.group(1).lower()
        y = int(mm.group(2))
        mo = _MONTH_NAME_TO_NUM.get(mon_s)
        if mo is None:
            continue
        label = f"{mm.group(1)} {y}"
        specs.append((y, mo, label))
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int, str]] = []
    for y, mo, lab in specs:
        k = (y, mo)
        if k not in seen:
            seen.add(k)
            out.append((y, mo, lab))
    return sorted(out, key=lambda x: (x[0], x[1]))


def _format_requested_month_year_lines(message: str, series: pd.DataFrame) -> str:
    specs = _parse_month_year_points(message)
    if not specs:
        return ""
    lines = ["  ### Requested months (spot inventory for comparison)"]
    for y, mo, label in specs:
        ts = pd.Timestamp(y, mo, 1)
        sub = series[series["month"].dt.to_period("M") == ts.to_period("M")]
        if sub.empty:
            lines.append(f"  - **{label}:** (no data for this month in the selected window)")
        else:
            v = float(sub.iloc[0]["inventory"])
            lines.append(f"  - **{label}:** {v:,.0f} homes for sale")
    if len(specs) >= 2:
        lines.append(
            "  - *Compare these two values directly; the line chart covers the full selected period.*"
        )
    return "\n".join(lines)


def _explicit_metro_ids_in_message(message: str, catalog: pd.DataFrame) -> list[int]:
    """
    If the user typed an exact catalog name (e.g. *New York, NY*), use only that metro.
    Longest names are checked first to prefer *New York, NY* over shorter collisions.
    """
    msg_l = message.lower()
    catu = catalog.drop_duplicates("RegionID")
    pairs: list[tuple[str, int]] = [
        (str(r["RegionName"]), int(r["RegionID"])) for _, r in catu.iterrows()
    ]
    pairs.sort(key=lambda x: -len(x[0]))
    found: list[tuple[int, int]] = []  # (position, region_id)
    seen: set[int] = set()
    for name, rid in pairs:
        if rid in seen:
            continue
        nl = name.lower()
        if len(nl) < 8:
            continue
        pos = msg_l.find(nl)
        if pos >= 0:
            seen.add(rid)
            found.append((pos, rid))
    found.sort(key=lambda x: x[0])
    return [rid for _, rid in found]


def _two_explicit_metro_intent(message: str) -> bool:
    m = message.lower()
    if " vs " in m or " versus " in m:
        return True
    if " and " not in m:
        return False
    if _and_clause_is_date_comparison_only(message):
        return False
    if "compare" in m or "comparison" in m:
        return True
    return False


def _narrow_uniq_by_state_abbrev_in_message(message: str, uniq: pd.DataFrame) -> pd.DataFrame:
    abbrevs = [
        m.group(1).upper()
        for m in re.finditer(r"\b([A-Z]{2})\b", message)
        if m.group(1) in _US_STATE_ABBREVS
    ]
    if len(abbrevs) != 1:
        return uniq
    st = abbrevs[0]
    suf = f", {st}"
    sub = uniq[uniq["RegionName"].str.upper().str.endswith(suf, na=False)]
    return sub if not sub.empty else uniq


def _cap_metro_matches(uniq: pd.DataFrame, message: str, *, explicit_count: int) -> pd.DataFrame:
    if uniq.empty:
        return uniq
    if explicit_count >= 2:
        return uniq.head(2)
    if explicit_count == 1:
        return uniq.head(1)
    m = message.lower()
    if " vs " in m or " versus " in m:
        return uniq.head(2)
    return uniq.head(1)


def _melt_inventory_long(df: pd.DataFrame) -> pd.DataFrame:
    date_cols = [c for c in df.columns if c not in ID_COLS]
    long_df = df.melt(
        id_vars=ID_COLS,
        value_vars=date_cols,
        var_name="month",
        value_name="inventory",
    )
    long_df["month"] = pd.to_datetime(long_df["month"], errors="coerce")
    long_df["inventory"] = pd.to_numeric(long_df["inventory"], errors="coerce")
    long_df = long_df.dropna(subset=["month"])
    long_df = long_df.sort_values(["RegionID", "month"]).reset_index(drop=True)
    return long_df


@lru_cache(maxsize=1)
def _load_long(csv_path: str) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    wide = pd.read_csv(path)
    return _melt_inventory_long(wide)


def get_long_inventory() -> pd.DataFrame:
    return _load_long(str(settings.METRO_CSV))


def _candidate_phrases(message: str) -> list[str]:
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", message.lower())
        if len(w) >= 2 and w not in STOPWORDS
    ]
    if not words:
        return []
    n = len(words)
    phrases: list[str] = []
    for length in range(min(5, n), 0, -1):
        for i in range(n - length + 1):
            phrases.append(" ".join(words[i : i + length]))
    seen: set[str] = set()
    out: list[str] = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _metro_catalog(long_df: pd.DataFrame) -> pd.DataFrame:
    return long_df[ID_COLS].drop_duplicates().sort_values("SizeRank")


def _format_global_dataset_snapshot(long_df: pd.DataFrame) -> str:
    """
    Always-on summary so every chat turn is grounded in actual file stats before the LLM runs.
    """
    inv = long_df["inventory"]
    data_min = pd.Timestamp(long_df["month"].min())
    data_max = pd.Timestamp(long_df["month"].max())
    n_rows = int(len(long_df))
    n_metros = int(long_df["RegionID"].nunique())
    cat = long_df[ID_COLS].drop_duplicates()
    type_break = cat["RegionType"].fillna("unknown").value_counts().head(8)
    type_lines = [f"  - {str(k)}: {int(v)} regions" for k, v in type_break.items()]

    snap_min = float(inv.min(skipna=True)) if inv.notna().any() else float("nan")
    snap_max = float(inv.max(skipna=True)) if inv.notna().any() else float("nan")
    snap_mean = float(inv.mean(skipna=True)) if inv.notna().any() else float("nan")

    latest_mask = long_df["month"] == data_max
    latest_rows = long_df.loc[latest_mask]
    country_latest = latest_rows[latest_rows["RegionType"].str.lower() == "country"]
    us_line = ""
    if not country_latest.empty:
        v = country_latest.iloc[0]["inventory"]
        name = country_latest.iloc[0].get("RegionName", "Country")
        if pd.notna(v):
            us_line = f"\n- **National / country row ({name})**, latest month inventory: **{float(v):,.0f}** homes."

    top5 = cat.nsmallest(5, "SizeRank")[["RegionName", "SizeRank"]]
    top_lines = [f"  {i}. {r['RegionName']} (SizeRank {int(r['SizeRank'])})" for i, (_, r) in enumerate(top5.iterrows(), 1)]

    lines = [
        "### Global dataset snapshot (computed from the loaded CSV)",
        f"- **Rows (long format):** {n_rows:,}",
        f"- **Distinct regions:** {n_metros:,}",
        f"- **Month range:** {data_min.date()} .. {data_max.date()}",
        f"- **Inventory values (all months, all regions):** min **{snap_min:,.0f}**, max **{snap_max:,.0f}**, mean **{snap_mean:,.0f}** (homes for sale).",
        "- **RegionType breakdown (catalog):**",
        *type_lines,
        "- **Five largest markets by SizeRank (catalog order):**",
        *top_lines,
    ]
    if us_line:
        lines.append(us_line.strip())
    lines.append(
        "- **Metric:** monthly **for-sale inventory** (single-family + condo), not sold prices or new listings."
    )
    return "\n".join(lines)


def _phrase_ok_for_match(phrase: str) -> bool:
    if len(phrase) < 3:
        return False
    parts = phrase.split()
    if len(parts) == 1 and len(parts[0]) < 4:
        return False
    return True


def _match_metros(message: str, catalog: pd.DataFrame) -> pd.DataFrame:
    msg_l = message.lower()
    want_us = "united states" in msg_l or "national" in msg_l or "nationwide" in msg_l
    phrases = _candidate_phrases(message)
    phrase_ids: list[int] = []
    names = catalog["RegionName"].str.lower()
    states = catalog["StateName"].fillna("").str.lower()

    for phrase in phrases:
        if not _phrase_ok_for_match(phrase):
            continue
        pat = _metro_phrase_regex(phrase)
        if not pat:
            continue
        mask = names.str.contains(pat, regex=True, na=False)
        phrase_ids.extend(catalog.loc[mask, "RegionID"].tolist())

    state_ids: list[int] = []
    for m in re.finditer(r"\b([A-Z]{2})\b", message):
        st = m.group(1)
        if st in _US_STATE_ABBREVS:
            mask = states == st.lower()
            state_ids.extend(catalog.loc[mask, "RegionID"].tolist())

    pset, sset = set(phrase_ids), set(state_ids)
    if pset and sset:
        inter = pset & sset
        hit_ids = inter if inter else pset
    elif pset:
        hit_ids = pset
    elif sset:
        hit_ids = sset
    else:
        return catalog.iloc[0:0].copy()

    uniq = catalog[catalog["RegionID"].isin(hit_ids)].copy()
    if not want_us:
        uniq = uniq[uniq["RegionType"].str.lower() != "country"]
    if uniq.empty:
        uniq = catalog[catalog["RegionID"].isin(hit_ids)].copy()
    uniq = uniq.sort_values("SizeRank").drop_duplicates(subset=["RegionID"])
    if "new york" in msg_l:
        ny = uniq[uniq["RegionName"].str.lower().str.contains("new york", na=False)]
        if not ny.empty:
            uniq = ny
    if "los angeles" in msg_l:
        la = uniq[uniq["RegionName"].str.lower().str.contains("los angeles", na=False)]
        if not la.empty:
            uniq = la
    uniq = _narrow_uniq_by_state_abbrev_in_message(message, uniq)
    return uniq


def _resolve_date_window(message: str, data_max: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Infer the chart/metrics window from the message (and optional pasted conversation context).

    If **multiple years** appear (e.g. “March 2020 and March 2024”), span from Jan 1 of the
    earliest to Dec 31 of the latest (clamped to the dataset’s latest month) so both periods
    are included. A single **first** year match was the old behavior and broke cross-year comparisons.
    """
    msg = message.lower()
    years = sorted({int(m.group(1)) for m in re.finditer(r"\b(20\d{2})\b", message)})

    if len(years) >= 2:
        y0, y1 = years[0], years[-1]
        start = pd.Timestamp(y0, 1, 1)
        end = pd.Timestamp(y1, 12, 31)
        end = min(end, data_max)
        if start > end:
            end = data_max
            start = end - pd.DateOffset(months=11)
        return start, end

    if len(years) == 1:
        y = years[0]
        start = pd.Timestamp(y, 1, 1)
        end = min(pd.Timestamp(y, 12, 31), data_max)
        if start > end:
            end = data_max
            start = end - pd.DateOffset(months=11)
        return start, end

    if "last year" in msg or "previous year" in msg or "past year" in msg:
        y = data_max.year - 1
        start = pd.Timestamp(y, 1, 1)
        end = min(pd.Timestamp(y, 12, 31), data_max)
        return start, end
    end = data_max
    start = end - pd.DateOffset(months=11)
    return start, end


def _series_for_metro(
    long_df: pd.DataFrame,
    region_id: int,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    sub = long_df[
        (long_df["RegionID"] == region_id)
        & (long_df["month"] >= start)
        & (long_df["month"] <= end)
    ].copy()
    return sub.sort_values("month")


_US_STATE_ABBREVS = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
    "OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


def _linear_inventory_ols(inv_series: pd.Series) -> dict | None:
    """
    Ordinary least squares: inventory_t = β₀ + β₁·t + ε, t = 0..n−1.

    **trend** for verdict rules: up / down / flat from slope sign and p-value on β₁=0 (flat if p>0.10).
    """
    y = np.asarray(inv_series.dropna().values, dtype=float)
    n = int(y.size)
    if n < _OLS_TREND_MIN_MONTHS:
        return None
    t = np.arange(n, dtype=float)
    X = sm.add_constant(t)
    try:
        res = sm.OLS(y, X).fit()
    except Exception as e:
        logger.debug("OLS trend fit failed: %s", e)
        return None

    slope = float(res.params[1])
    icept = float(res.params[0])
    try:
        pval = float(res.pvalues[1])
    except (TypeError, IndexError, KeyError):
        pval = 1.0
    r2 = float(res.rsquared) if np.isfinite(res.rsquared) else 0.0

    if pval > 0.10 or not np.isfinite(slope):
        tlab: str = "flat"
    elif slope > 0:
        tlab = "up"
    else:
        tlab = "down"

    eq = f"inventory ≈ {icept:,.1f} + ({slope:,.2f})×month_index"
    return {
        "linear_intercept": icept,
        "linear_slope_per_month": slope,
        "linear_slope_pvalue": pval,
        "linear_r_squared": r2,
        "linear_n_months": n,
        "linear_equation_display": eq,
        "linear_trend_label": tlab,
    }


def _logistic_mom_increase_on_time(inv_series: pd.Series) -> dict:
    """
    Logistic regression: Y_i = 1{inventory rose vs prior month} ~ logit(γ₀ + γ₁·t_i).

    t_i is the month index at the **end** of each pair (1..n−1). Informational only (not used in verdict).
    """
    base: dict = {
        "logistic_attempted": True,
        "logistic_coef_time": None,
        "logistic_pvalue_time": None,
        "logistic_pseudo_r2": None,
        "logistic_n_pairs": 0,
        "logistic_pct_up": None,
        "logistic_fit_note": None,
    }
    y_raw = np.asarray(inv_series.dropna().values, dtype=float)
    n = int(y_raw.size)
    if n < 2:
        base["logistic_fit_note"] = "Need at least 2 months to form month-over-month pairs."
        return base

    y_list: list[float] = []
    t_list: list[float] = []
    for i in range(1, n):
        y_list.append(1.0 if y_raw[i] > y_raw[i - 1] else 0.0)
        t_list.append(float(i))

    y = np.asarray(y_list, dtype=float)
    t_arr = np.asarray(t_list, dtype=float)
    n_pairs = int(y.size)
    base["logistic_n_pairs"] = n_pairs
    base["logistic_pct_up"] = round(100.0 * float(np.mean(y)), 1)

    if n_pairs < _LOGIT_MIN_PAIRS:
        base["logistic_fit_note"] = (
            f"Only {n_pairs} consecutive pairs (logit fit skipped; use ≥{_LOGIT_MIN_PAIRS} for a stable fit)."
        )
        return base

    if np.all(y == 0) or np.all(y == 1):
        base["logistic_fit_note"] = "Perfect separation (all months up or all down); maximum likelihood not unique."
        return base

    X = sm.add_constant(t_arr)
    try:
        res = sm.Logit(y, X).fit(disp=False, maxiter=200)
    except Exception as e:
        logger.debug("Logit fit failed: %s", e)
        base["logistic_fit_note"] = f"Logistic regression did not converge: {e!s}"
        return base

    try:
        g1 = float(res.params[1])
        pv = float(res.pvalues[1])
    except (TypeError, IndexError, KeyError):
        g1, pv = float("nan"), 1.0

    pr2 = getattr(res, "prsquared", None)
    pr2f = float(pr2) if pr2 is not None and np.isfinite(pr2) else None

    base["logistic_coef_time"] = g1
    base["logistic_pvalue_time"] = pv
    base["logistic_pseudo_r2"] = pr2f
    base["logistic_fit_note"] = None
    return base


def _endpoint_trend_last3(m: pd.DataFrame) -> str:
    last3 = m["inventory"].tail(3).dropna()
    if len(last3) < 2:
        return "flat"
    a, b = float(last3.iloc[0]), float(last3.iloc[-1])
    if b > a * 1.02:
        return "up"
    if b < a * 0.98:
        return "down"
    return "flat"


def _metro_signal_metrics_frame(m: pd.DataFrame) -> dict | None:
    """Signals from one metro’s rows (any subset of columns, must include month + inventory)."""
    m = m.sort_values("month").dropna(subset=["month", "inventory"])
    if len(m) < 2:
        return None

    latest = m.iloc[-1]
    latest_t = pd.Timestamp(latest["month"])
    latest_v = float(latest["inventory"])

    prior = m.iloc[:-1].tail(6)
    prior_inv = prior["inventory"].dropna()
    avg_prior: float | None = float(prior_inv.mean()) if len(prior_inv) > 0 else None
    vs_prior_pct: float | None = None
    if avg_prior is not None and avg_prior > 0:
        vs_prior_pct = (latest_v - avg_prior) / avg_prior * 100

    yoy_pct: float | None = None
    yoy_v: float | None = None
    yoy_t = latest_t - pd.DateOffset(years=1)
    yoy_rows = m[m["month"].dt.to_period("M") == yoy_t.to_period("M")]
    if not yoy_rows.empty:
        yoy_v = float(yoy_rows.iloc[0]["inventory"])
        if yoy_v > 0:
            yoy_pct = (latest_v - yoy_v) / yoy_v * 100

    lin = _linear_inventory_ols(m["inventory"])
    logi = _logistic_mom_increase_on_time(m["inventory"])
    if lin is not None:
        trend = str(lin["linear_trend_label"])
        trend_source = "linear_ols"
    else:
        trend = _endpoint_trend_last3(m)
        trend_source = "endpoint"

    out = {
        "latest_v": latest_v,
        "latest_date": str(latest_t.date()),
        "latest_ts": latest_t,
        "avg_prior": avg_prior,
        "prior_n": int(len(prior_inv)),
        "vs_prior_pct": vs_prior_pct,
        "yoy_pct": yoy_pct,
        "yoy_v": yoy_v,
        "yoy_t": yoy_t,
        "trend": trend,
        "trend_source": trend_source,
    }
    if lin is not None:
        out.update({k: v for k, v in lin.items()})
    out.update(logi)
    return out


def _metro_signal_metrics(long_df: pd.DataFrame, region_id: int) -> dict | None:
    """Latest-month signals vs prior six months + YoY + trend (OLS or last-3-month fallback) + logistic MoM info."""
    sub = long_df[long_df["RegionID"] == region_id]
    return _metro_signal_metrics_frame(sub)


def is_inventory_trend_or_forecast_question(message: str) -> bool:
    """
    True when the user is asking about future direction, momentum, or next-period inventory change.
    Used to attach cross-metro comparison + full-history trend context to the DATA block.
    """
    m = message.lower()
    if "next month" in m or "next few months" in m or "coming month" in m:
        return True
    if "increase or decrease" in m or "go up or down" in m or "going up or down" in m:
        return True
    if "will " in m and "inventory" in m:
        return True
    if any(w in m for w in ("forecast", "predict", "projection", "trend next")):
        return True
    return False


class PeerInventoryStats(NamedTuple):
    """Snapshot over metros (country excluded) at dataset latest month: vs-prior % and trend labels."""

    n_metros: int
    median_vs_prior_pct: float
    by_id: dict[int, tuple[float, str, str]]
    trend_counts: dict[str, int]


def _compute_peer_inventory_stats(long_df: pd.DataFrame, catalog: pd.DataFrame) -> PeerInventoryStats | None:
    base = catalog[catalog["RegionType"].str.lower() != "country"]
    allowed_set = set(base["RegionID"].astype(int).tolist())
    by_id: dict[int, tuple[float, str, str]] = {}
    min_prior = 4
    for region_id, grp in long_df.groupby("RegionID", sort=False):
        rid = int(region_id)
        if rid not in allowed_set:
            continue
        sig = _metro_signal_metrics_frame(grp)
        if not sig or sig.get("vs_prior_pct") is None:
            continue
        if int(sig.get("prior_n", 0)) < min_prior:
            continue
        by_id[rid] = (
            float(sig["vs_prior_pct"]),
            str(sig["trend"]),
            str(sig.get("trend_source", "endpoint")),
        )
    if not by_id:
        return None
    vs_values = [t[0] for t in by_id.values()]
    median_vs = float(np.median(np.asarray(vs_values, dtype=float)))
    trend_counts = dict(Counter(t[1] for t in by_id.values()))
    return PeerInventoryStats(
        n_metros=len(by_id),
        median_vs_prior_pct=median_vs,
        by_id=by_id,
        trend_counts=trend_counts,
    )


def _format_cross_dataset_block(
    metro_name: str,
    region_id: int,
    sig_metrics: dict | None,
    peers: PeerInventoryStats,
) -> str:
    lines = [
        f"### Cross-metro + full-history context (for **{metro_name}**)",
        "Same definitions as the ranking table: **latest month in file** vs **average of prior month-ends** "
        "(excluding latest), and **trend** = OLS on month index when enough history, else last-three-month rule. "
        "Country aggregate row excluded from peer set.",
    ]
    if region_id not in peers.by_id:
        lines.append(
            f"This metro is **not** in the peer rank (insufficient prior months vs the rule). "
            f"Still use its monthly series and any OLS/trend lines in the metro block above."
        )
        return "\n".join(lines)

    target_vs, tr, ts = peers.by_id[region_id]
    all_vs = sorted(t[0] for t in peers.by_id.values())
    n = len(all_vs)
    rank = sum(1 for v in all_vs if v < target_vs) + 1
    pctile = 100.0 * (rank - 1) / (n - 1) if n > 1 else 50.0
    ts_plain = "OLS on **full** monthly history in the file" if ts == "linear_ols" else "last-three-month endpoint rule on full history"

    lines.append(
        f"- **This metro vs its own recent months:** latest inventory is **{target_vs:+.1f}%** vs the average of "
        f"prior month-ends; labeled trend **{tr}** ({ts_plain})."
    )
    if sig_metrics:
        if sig_metrics.get("yoy_pct") is not None:
            lines.append(f"- **YoY** (same calendar month): **{float(sig_metrics['yoy_pct']):+.1f}%** inventory.")
        if (
            sig_metrics.get("trend_source") == "linear_ols"
            and sig_metrics.get("linear_slope_per_month") is not None
        ):
            b1 = float(sig_metrics["linear_slope_per_month"])
            pv = float(sig_metrics["linear_slope_pvalue"])
            nmo = int(sig_metrics.get("linear_n_months", 0))
            lines.append(
                f"- **Full-history OLS** (n={nmo} months): slope **{b1:,.1f}** homes/month vs month index, "
                f"p={pv:.4f} on slope. Positive slope means historical **average drift up** over the series; "
                f"it is **not** a guaranteed next-month forecast."
            )

    lines.append(
        f"- **Vs all {n} metros** with the same peer rule: **rank {rank} / {n}** by “% vs prior avg” "
        f"(rank **1** = **lowest** % vs prior = **tightest** inventory vs its own recent months **among metros**). "
        f"Approx. percentile along that spectrum: **{pctile:.0f}** (0 = tightest vs peers, 100 = loosest). "
        f"Cross-metro **median** “% vs prior avg”: **{peers.median_vs_prior_pct:+.1f}%**."
    )
    tc = peers.trend_counts
    lines.append(
        f"- **Peer trend mix** (same labels): **up** {tc.get('up', 0)}, **down** {tc.get('down', 0)}, "
        f"**flat** {tc.get('flat', 0)}."
    )
    lines.append(
        "- For **“next month / increase or decrease”** questions: give a **qualitative** call from **momentum** "
        "(recent change + trend + how this metro sits vs peers). **Do not** claim certainty or a literal forecast; "
        "inventory only — not prices, demand, or sales speed."
    )
    return "\n".join(lines)


def is_best_place_ranking_question(message: str) -> bool:
    """
    Broad “where / which / best … sell” without naming a specific metro for phrase-match.

    Not used when the user is clearly comparing two named places (those should stay on per-metro charts).
    """
    m = message.lower()
    if "compare" in m and (" and " in m or " vs " in m or " versus " in m):
        return False
    patterns = (
        "best place",
        "best city",
        "best metro",
        "best area",
        "best market",
        "best cities",
        "best metros",
        "where to sell",
        "where should i sell",
        "where is the best",
        "where's the best",
        "where are the best",
        "which city",
        "which metro",
        "which area",
        "which market",
        "which cities",
        "which metros",
        "top cities",
        "top metros",
        "top markets",
        "top places",
        "good place to sell",
        "better place to sell",
        "best locations",
        "best location to sell",
    )
    return any(p in m for p in patterns)


def _first_us_state_abbrev(message: str) -> str | None:
    for mm in re.finditer(r"\b([A-Z]{2})\b", message):
        st = mm.group(1)
        if st in _US_STATE_ABBREVS:
            return st
    return None


def _catalog_for_ranking(catalog: pd.DataFrame, message: str) -> tuple[pd.DataFrame, str | None]:
    """Metros only; optionally restrict to `, ST` suffix when a state code appears in the message."""
    base = catalog[catalog["RegionType"].str.lower() != "country"].copy()
    st = _first_us_state_abbrev(message)
    if not st:
        return base, None
    suf = f", {st}"
    sub = base[base["RegionName"].str.endswith(suf, na=False)]
    if sub.empty:
        return base, None
    return sub, st


def _rank_score_for_sort(sig: dict) -> float:
    vs = sig.get("vs_prior_pct")
    if vs is None:
        return 1e9
    tr = sig.get("trend", "flat")
    adj = float(vs)
    if tr == "up":
        adj += 4.0
    elif tr == "down":
        adj -= 1.0
    return adj


def _compute_metro_ranking_rows(
    long_df: pd.DataFrame,
    catalog: pd.DataFrame,
    *,
    top_n: int = 18,
    min_prior_months: int = 4,
) -> list[dict]:
    """Lower sort score ≈ fewer listings vs recent months (inventory-only heuristic)."""
    allowed = set(catalog["RegionID"].astype(int).tolist())
    scored: list[tuple[float, dict]] = []
    name_by_id = (
        catalog.drop_duplicates("RegionID").set_index("RegionID")["RegionName"].to_dict()
    )
    for region_id, grp in long_df.groupby("RegionID", sort=False):
        rid = int(region_id)
        if rid not in allowed:
            continue
        sig = _metro_signal_metrics_frame(grp)
        if not sig or sig.get("vs_prior_pct") is None:
            continue
        if int(sig.get("prior_n", 0)) < min_prior_months:
            continue
        row = {
            "region_id": rid,
            "region_name": str(name_by_id.get(rid, f"Region {rid}")),
            "vs_prior_pct": float(sig["vs_prior_pct"]),
            "trend": str(sig["trend"]),
            "trend_source": str(sig.get("trend_source", "endpoint")),
            "latest_v": float(sig["latest_v"]),
            "latest_date": str(sig["latest_date"]),
            "prior_n": int(sig["prior_n"]),
            "yoy_pct": sig.get("yoy_pct"),
        }
        scored.append((_rank_score_for_sort(sig), row))
    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored[:top_n]]


def _short_chart_label(name: str, max_len: int = 22) -> str:
    if len(name) <= max_len:
        return name
    return name[: max_len - 1] + "…"


def _format_ranking_block_for_llm(rows: list[dict], *, scope_note: str) -> str:
    lines = [
        "### Cross-metro ranking (inventory-only; latest month vs average of prior month-ends, excluding latest)",
        scope_note,
        "Sort key: lower **% vs prior 6** means fewer listings vs those recent months (less seller competition on "
        "this metric). **Trend** up/down/flat adjusts the sort (OLS on month index when n≥6 months, else last-3-month rule).",
        "",
        "| Rank | Metro | Latest month | Inventory | vs prior avg % | Trend | YoY inv % |",
        "| --- | --- | --- | ---: | ---: | --- | ---: |",
    ]
    for i, r in enumerate(rows, start=1):
        yoy = r.get("yoy_pct")
        yoy_s = f"{yoy:+.1f}" if yoy is not None else "n/a"
        ts = "OLS" if r.get("trend_source") == "linear_ols" else "·"
        tr = r["trend"]
        lines.append(
            f"| {i} | {r['region_name']} | {r['latest_date']} | {r['latest_v']:,.0f} | "
            f"{r['vs_prior_pct']:+.1f}% | {tr} ({ts}) | {yoy_s} |"
        )
    lines.append("")
    lines.append(
        "*Trend column: **(OLS)** = from linear regression on full history; **(·)** = last-three-month endpoint rule.*"
    )
    lines.append("")
    lines.append(
        "When answering **which place is best to sell**, start by **naming metros** from this table (highest rank "
        "first), then explain caveats: inventory ≠ prices, demand, or speed of sale; not financial advice."
    )
    return "\n".join(lines)


def _ranking_assistant_footer() -> list[str]:
    return [
        "",
        "Instructions for the assistant: the user asked for **which metros** look relatively better for sellers on "
        "**for-sale inventory only**. In **## Summary**, open with a **clear list of metro full names** (e.g. top 5–10 "
        "from the table), then interpret. Use **only** the ranking table for those numbers. "
        "Markdown section order: ## Summary (metro names first), ## Output (interpret the ranking / pie chart), "
        "## Takeaways — **end there**. No `## Verdict` for this ranking-only view. "
        "Do not claim price or demand rankings.",
    ]


def _synthetic_ranking_metro_viz(
    rows: list[dict],
    *,
    data_max: pd.Timestamp,
    state_abbrev: str | None,
    chart_top_n: int = 10,
) -> MetroVizPayload:
    chart_rows = rows[:chart_top_n]
    categories = [_short_chart_label(r["region_name"]) for r in chart_rows]
    raw_vs = [float(r["vs_prior_pct"]) for r in chart_rows]
    mn = min(raw_vs)
    pie_vals = [v - mn + 1.0 for v in raw_vs]
    title = (
        f"Top {len(chart_rows)} metros — latest vs prior 6-month average (%)"
        + (f" · {state_abbrev}" if state_abbrev else "")
    )
    pie = PieChartSpec(
        title=title,
        subtitle="Slice size ∝ (metric − min) + 1 so all segments are positive; lower raw % vs prior = relatively tighter inventory.",
        categories=categories,
        values=pie_vals,
    )
    scope = f"Ranking snapshot (dataset latest month {data_max.date()})"
    if state_abbrev:
        scope += f", filtered to **{state_abbrev}** metros when possible"
    return MetroVizPayload(
        region_id=-1,
        region_name="Top metros (by inventory vs recent months)",
        region_type="ranking",
        size_rank=0,
        period_label=scope,
        timing_as_of=str(data_max.date()),
        selling_insights=[],
        charts=[pie],
        verdict=None,
    )


def is_sell_timing_question(message: str) -> bool:
    m = message.lower()
    triggers = (
        "sell",
        "selling",
        "list my",
        "list the",
        "buy",
        "buying",
        "purchase",
        "should i buy",
        "good time to buy",
        "time to buy",
        "good time",
        "right time",
        "when should",
        "when to",
        "when is",
        "timing",
        "wait to",
        "should i sell",
        "offload",
        "put on the market",
        "time to list",
    )
    return any(t in m for t in triggers)


def compute_selling_verdict(
    user_message: str,
    metrics: dict | None,
    metro_name: str,
) -> VerdictPayload:
    """
    Yes/No/Unclear from **for-sale inventory only** (not prices or demand).

    Rules (also listed in `basis`):
    - **Yes**: latest month is at least 2% *below* the average of the six prior month-ends
      (excluding latest), AND **trend** is not **up** (trend from **linear regression (OLS)** on month index when
      enough history, else a last-three-month endpoint check).
    - **No**: latest is at least 2% *above* that prior average, OR (trend is up AND latest is
      above that average).
    - **Unclear**: otherwise, or insufficient data, or not a sell/timing question.
    """
    basis: list[str] = [
        "Verdict uses only **metro for-sale inventory** (counts of homes listed), not sold prices, "
        "days on market, or mortgage rates.",
        "We compare the **latest month in the dataset** to the **average of up to six** prior month-ends "
        "(excluding the latest). **Trend** (up / down / flat): with **≥6** months we use **OLS** on month index; "
        "with fewer months or a failed fit, we use the **last three** month-ends (≈±2% change).",
    ]

    if not is_sell_timing_question(user_message):
        basis.append(
            "Your message was not treated as a sell/buy/timing question (no sell, buy, or timing keywords detected)."
        )
        return VerdictPayload(
            answer="unclear",
            headline="No Yes / No verdict for this question",
            reason=(
                "A clear **Yes** or **No** verdict is only shown for sell-, buy-, or timing-style questions "
                "(for example: “Is now a good time to sell in New York?” or “Should I buy now?”). For other questions, "
                "use the summary and charts above."
            ),
            basis=basis,
        )

    basis.append(
        "Applied rule (sell/timing intent detected): **Yes** if latest inventory is at least **2% below** the average "
        "of up to **six** prior month-ends (excluding the latest) **and** **trend** is **not up** (OLS on month index or "
        "last-3-month fallback); **No** if latest is at least **2% above** that average **or** (trend is **up** and "
        "latest is still **above** that average); otherwise **Unclear**."
    )
    basis.append(
        f"Scope: this verdict is for **{metro_name}** only. Your exact words (beyond sell/timing intent) do not change "
        "the rule — only that metro’s inventory series does."
    )

    if metrics is None:
        basis.append("Not enough consecutive months with inventory data to compare.")
        return VerdictPayload(
            answer="unclear",
            headline="Unclear — not enough data",
            reason=f"Could not compute latest-vs-prior comparison for **{metro_name}**.",
            basis=basis,
        )

    avg_prior = metrics.get("avg_prior")
    vs = metrics.get("vs_prior_pct")
    trend = metrics.get("trend", "flat")
    trend_src = metrics.get("trend_source", "endpoint")
    latest_v = metrics["latest_v"]
    latest_date = metrics["latest_date"]
    prior_n = metrics.get("prior_n", 0)
    yoy_pct = metrics.get("yoy_pct")

    basis.append(
        f"Latest month **{latest_date}**: **{latest_v:,.0f}** homes for sale (for-sale inventory)."
    )
    if avg_prior is not None and prior_n > 0:
        basis.append(
            f"Average of the **{prior_n}** month-end(s) before that (excluding latest): **{avg_prior:,.0f}**."
        )
    if vs is not None:
        basis.append(f"Latest vs that prior average: **{vs:+.1f}%**.")
    if trend_src == "linear_ols" and metrics.get("linear_slope_per_month") is not None:
        b1 = float(metrics["linear_slope_per_month"])
        pv = float(metrics["linear_slope_pvalue"])
        nmo = int(metrics["linear_n_months"])
        r2 = float(metrics["linear_r_squared"])
        basis.append(
            f"Trend **{trend}** from **OLS** (n={nmo} months): slope β₁ = **{b1:,.2f}** homes/month, "
            f"p = **{pv:.4f}**, R² = **{r2:.3f}** (flat if p>0.10)."
        )
    else:
        basis.append(
            f"Trend **{trend}** from **last ~3 month-ends** (≈±2% change rule); OLS skipped (not enough months or fit failed)."
        )
    if yoy_pct is not None:
        basis.append(f"Year-over-year same month inventory change: **{yoy_pct:+.1f}%**.")

    TH = 2.0
    if vs is None or avg_prior is None or prior_n < 1:
        return VerdictPayload(
            answer="unclear",
            headline="Unclear — incomplete comparison",
            reason=(
                f"There aren’t enough prior months to compare the latest inventory for **{metro_name}** "
                "against a six-month trailing average."
            ),
            basis=basis,
        )

    if vs <= -TH and trend != "up":
        return VerdictPayload(
            answer="yes",
            headline=(
                f"Yes — **{metro_name}**: latest inventory is **{abs(vs):.1f}% below** its prior-{prior_n}-month "
                f"average and trend is **not up** (inventory-only)"
            ),
            reason=(
                f"For **{metro_name}**, latest inventory (**{latest_v:,.0f}** in {latest_date}) is **{abs(vs):.1f}% below** "
                f"the average of the prior {prior_n} month(s), and inventory **trend** is **not rising** "
                f"(OLS on month index when possible, else a short window rule). "
                "That usually means **fewer competing listings than those recent months on this metric**. "
                "Different questions with the same metro can get the same verdict because we **only** use these numbers, "
                "not adjectives in your message or sold prices / demand."
            ),
            basis=basis,
        )

    if vs >= TH or (trend == "up" and vs > 0):
        return VerdictPayload(
            answer="no",
            headline=(
                f"No — **{metro_name}**: inventory is **not** in the “lighter than recent months” band on this rule "
                f"(latest vs prior avg **{vs:+.1f}%**, trend **{trend}**)"
            ),
            reason=(
                f"For **{metro_name}**, either latest inventory is **{max(vs, 0):.1f}% or more above** the prior-month "
                f"average (threshold {TH:g}%), or inventory is **trending up** while still above that average. "
                "That points to **more or rising seller competition on this metric** vs those months. "
                "Wording like “bad time” vs “good time” does not change the verdict — only the inventory comparison does."
            ),
            basis=basis,
        )

    return VerdictPayload(
        answer="unclear",
        headline=(
            f"Unclear — **{metro_name}**: latest vs prior average is **{vs:+.1f}%** (inside about ±{TH:g}%) "
            f"and/or trend **{trend}** does not clearly land Yes or No"
        ),
        reason=(
            f"For **{metro_name}**, latest inventory vs the prior-month average is inside about **±{TH:g}%**, "
            "or the trend and level conflict for a clean call. Use the **line** and **pie** charts and the "
            "verdict panel for the numbers behind this."
        ),
        basis=basis,
    )


def _format_metro_block(
    row: pd.Series,
    series: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> str:
    if series.empty:
        return f"- {row['RegionName']}: no rows in selected period ({start.date()}–{end.date()})."

    inv = series["inventory"]
    lines = [
        f"- Metro: {row['RegionName']} (type={row['RegionType']}, rank={int(row['SizeRank'])})"
    ]
    lines.append(f"  Period: {series['month'].min().date()} .. {series['month'].max().date()}")
    lines.append(
        "  Note: values are monthly **for-sale inventory** (SF + condo), not new-construction permits."
    )
    lines.append("  Monthly inventory:")
    for _, r in series.iterrows():
        v = r["inventory"]
        vs = f"{v:,.0f}" if pd.notna(v) else "n/a"
        lines.append(f"    {pd.Timestamp(r['month']).date()}: {vs}")

    valid = inv.dropna()
    if len(valid) > 0:
        mean_v = float(valid.mean())
        lines.append(f"  Mean over period: {mean_v:,.0f}")
        lines.append(f"  Min: {float(valid.min()):,.0f} | Max: {float(valid.max()):,.0f}")
        last = float(valid.iloc[-1])
        tail = valid.tail(min(6, len(valid)))
        avg6 = float(tail.mean()) if len(tail) else mean_v
        lines.append(f"  Latest month in window: {last:,.0f}")
        lines.append(f"  Average of last up-to-6 months in window: {avg6:,.0f}")
        if avg6 > 0:
            pct = (last - avg6) / avg6 * 100
            lines.append(
                f"  Latest vs that average: {pct:+.1f}% "
                f"(positive = more inventory vs recent months in this window)"
            )
    return "\n".join(lines)


def retrieve_inventory_for_chat(
    user_message: str,
    conversation_context: str = "",
) -> tuple[str, list[MetroVizPayload], str, str]:
    """
    Build LLM context text + structured metro payloads for charts/metrics UI.

    ``conversation_context`` should be recent chat turns (plain text). It widens the **date window**
    when the user mentions years only in prior messages, and helps **metro matching** on short
    follow-ups (“that comparison”, “March 2024 is in the file”) by reusing names from history.

    Returns: (context_text, metros, data_window_label, dataset_note)
    """
    try:
        long_df = get_long_inventory()
    except FileNotFoundError:
        msg = (
            f"=== Data error ===\n"
            f"Metro CSV not found at {settings.METRO_CSV}. "
            f"Set METRO_CSV in backend/.env or place the file at the repo root."
        )
        return msg, [], "", settings.METRO_CSV.name
    except Exception as e:
        return f"=== Data error ===\nCould not load inventory CSV: {e}", [], "", ""

    data_max = pd.Timestamp(long_df["month"].max())
    catalog = _metro_catalog(long_df)
    ctx_tail = (conversation_context or "").strip()[-12000:]
    window_source = f"{ctx_tail}\n{user_message}" if ctx_tail else user_message
    match_text = _normalize_message_for_matching(user_message)
    start, end = _resolve_date_window(window_source, data_max)

    window_label = f"{start.date()} to {end.date()}"
    dataset_note = (
        f"{settings.METRO_CSV.name} (latest month in file: {data_max.date()})"
    )

    header = [
        "=== Retrieved from dataset (Metro monthly for-sale inventory) ===",
        f"CSV: {settings.METRO_CSV.name}",
        f"Dataset latest month in file: {data_max.date()}",
        f"Selected window for metrics: {start.date()} .. {end.date()}",
        "",
        _format_global_dataset_snapshot(long_df),
        "",
    ]

    # Ranking / “best place” questions run first so vague tokens (e.g. “house” → Houston) do not skip this path.
    ranking_q = is_best_place_ranking_question(match_text)
    if ranking_q:
        cat_rank, st = _catalog_for_ranking(catalog, match_text)
        ranked = _compute_metro_ranking_rows(
            long_df,
            cat_rank,
            top_n=18,
            min_prior_months=4,
        )
        if ranked:
            if st:
                scope_note = (
                    f"Scope: metros in **{st}** (region name ends with `, {st}`), latest month in file "
                    f"**{data_max.date()}**."
                )
            else:
                scope_note = (
                    f"Scope: **all metros** in the file (country aggregate excluded), latest month **{data_max.date()}**."
                )
            block = _format_ranking_block_for_llm(ranked, scope_note=scope_note)
            footer = _ranking_assistant_footer()
            viz = _synthetic_ranking_metro_viz(
                ranked,
                data_max=data_max,
                state_abbrev=st,
                chart_top_n=10,
            )
            ctx = "\n".join(header) + "\n" + block + "\n".join(footer)
            return ctx, [viz], window_label, dataset_note

    explicit_ids = _explicit_metro_ids_in_message(user_message, catalog)
    if explicit_ids:
        two_metro = _two_explicit_metro_intent(user_message) and len(explicit_ids) >= 2
        pick = explicit_ids[:2] if two_metro else explicit_ids[:1]
        cat_u = catalog.drop_duplicates("RegionID")
        row_by_id = {int(r["RegionID"]): r for _, r in cat_u.iterrows()}
        ordered_rows = [row_by_id[rid] for rid in pick if rid in row_by_id]
        matches = pd.DataFrame(ordered_rows) if ordered_rows else catalog.iloc[0:0].copy()
    else:
        matches = _match_metros(match_text, catalog)
        if matches.empty and ctx_tail:
            combined_match = _normalize_message_for_matching(f"{user_message}\n{ctx_tail}")
            matches = _match_metros(combined_match, catalog)
        matches = _cap_metro_matches(matches, user_message, explicit_count=0)

    if matches.empty:
        header.append(
            "No specific metro matched the message (try naming a metro, e.g. 'New York, NY' or 'Austin, TX')."
        )
        header.append(
            f"Overall: {long_df['RegionID'].nunique()} metros, "
            f"{long_df['month'].min().date()} .. {data_max.date()}."
        )
        header.append(
            "Instructions for the assistant: **Every turn loads real rows from this CSV first.** Use the "
            "**Global dataset snapshot** for file-level facts (coverage, date span, national row if present). "
            "Do **not** invent metro-level numbers unless they appear in another section of this DATA block. "
            "If the user needs a specific metro time series or charts, ask them to name the metro or state. "
            "For questions that are only loosely related to housing, still acknowledge what the dataset contains, "
            "then answer the non-data parts from general knowledge — clearly separate **what the data shows** vs **general reasoning**."
        )
        ctx = "\n".join(header)
        return ctx, [], window_label, dataset_note

    blocks: list[str] = []
    metros_out: list[MetroVizPayload] = []
    peer_stats: PeerInventoryStats | None = None
    if is_inventory_trend_or_forecast_question(user_message):
        peer_stats = _compute_peer_inventory_stats(long_df, catalog)

    for _, row in matches.iterrows():
        ser = _series_for_metro(long_df, int(row["RegionID"]), start, end)
        blocks.append(_format_metro_block(row, ser, start, end))
        spot_lines = _format_requested_month_year_lines(user_message, ser)
        if spot_lines:
            blocks.append(spot_lines)

        sig_metrics = _metro_signal_metrics(long_df, int(row["RegionID"]))
        if peer_stats is not None:
            blocks.append(
                _format_cross_dataset_block(
                    str(row["RegionName"]),
                    int(row["RegionID"]),
                    sig_metrics,
                    peer_stats,
                )
            )
        timing_as_of = str(sig_metrics["latest_date"]) if sig_metrics else None

        x_labels: list[str] = []
        values: list[float | None] = []
        for _, r in ser.iterrows():
            x_labels.append(pd.Timestamp(r["month"]).strftime("%b %Y"))
            v = r["inventory"]
            values.append(float(v) if pd.notna(v) else None)

        inv = ser["inventory"]
        valid = inv.dropna()
        mean_v = float(valid.mean()) if len(valid) else None
        min_v = float(valid.min()) if len(valid) else None
        max_v = float(valid.max()) if len(valid) else None
        latest_v = float(valid.iloc[-1]) if len(valid) else None
        tail = valid.tail(min(6, len(valid)))
        avg6 = float(tail.mean()) if len(tail) else None
        vs_pct: float | None = None
        if avg6 is not None and avg6 > 0 and latest_v is not None:
            vs_pct = (latest_v - avg6) / avg6 * 100

        ts = TimeSeriesChartSpec(
            title=f"{row['RegionName']} — monthly for-sale inventory",
            subtitle=f"{window_label} · higher = more homes on the market",
            x_labels=x_labels,
            values=values,
            y_axis_label="Homes for sale (inventory)",
            mean_reference=mean_v,
        )
        pie = PieChartSpec(
            title="Level comparison (same period)",
            subtitle="Slice size ∝ inventory count for min / mean / max / latest in the selected window.",
            categories=["Minimum", "Mean", "Maximum", "Latest month"],
            values=[min_v, mean_v, max_v, latest_v],
        )

        verdict = compute_selling_verdict(
            user_message,
            sig_metrics,
            str(row["RegionName"]),
        )

        metros_out.append(
            MetroVizPayload(
                region_id=int(row["RegionID"]),
                region_name=str(row["RegionName"]),
                region_type=str(row["RegionType"]),
                size_rank=int(row["SizeRank"]),
                period_label=window_label,
                timing_as_of=timing_as_of,
                selling_insights=[],
                metric_mean=mean_v,
                metric_min=min_v,
                metric_max=max_v,
                metric_latest=latest_v,
                metric_avg_6m=avg6,
                metric_vs_avg_6m_pct=vs_pct,
                charts=[ts, pie],
                verdict=verdict,
            )
        )

    footer = [
        "",
        "Instructions for the assistant: use ONLY the numbers above for factual claims; "
        "if the user asked for 'new listings' but the column is inventory, explain that mapping briefly. "
        "Be balanced; state limitations; not financial advice. "
        "Markdown sections in order: ## Summary, ## Output (interpret the **line** and **pie** charts), "
        "## Takeaways — **end there**. Do **not** add `## How to read the charts` or `## Verdict`: the UI shows "
        "**Verdict** (Yes / No / Unclear plus a short reason) **after** the charts; do not contradict it.",
    ]
    if peer_stats is not None:
        footer.insert(
            1,
            "The user asked about **inventory direction / next month** (or similar): you **must** ground the answer in "
            "the **### Cross-metro + full-history context** section(s) plus the metro time series — compare this metro’s "
            "trend and level **to the peer distribution** (rank, median, peer trend mix). Frame “next month” as **uncertain** "
            "informed momentum, not a guarantee.",
        )
    ctx = "\n".join(header) + "\n" + "\n\n".join(blocks) + "\n" + "\n".join(footer)
    return ctx, metros_out, window_label, dataset_note


def build_inventory_context(user_message: str, conversation_context: str = "") -> str:
    """Text block for the LLM (backwards-compatible)."""
    ctx, _, _, _ = retrieve_inventory_for_chat(user_message, conversation_context=conversation_context)
    return ctx
