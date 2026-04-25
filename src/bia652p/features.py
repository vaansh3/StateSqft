from __future__ import annotations

import numpy as np
import pandas as pd


def add_time_features(long_df: pd.DataFrame) -> pd.DataFrame:
    """Per-metro sorted frame: calendar month as sin/cos for seasonality."""
    df = long_df.copy()
    df["cal_month"] = df["month"].dt.month
    df["month_sin"] = np.sin(2 * np.pi * df["cal_month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["cal_month"] / 12)
    return df


def add_lags_and_targets(df: pd.DataFrame, lag_months: tuple[int, ...] = (1, 2, 3)) -> pd.DataFrame:
    """
    For each RegionID, add lagged inventory and regression target (next month inventory).
    Rows with NaN lags or target are dropped later.
    """
    out = []
    for rid, g in df.groupby("RegionID", sort=False):
        g = g.sort_values("month").copy()
        for lag in lag_months:
            g[f"inv_lag_{lag}"] = g["inventory"].shift(lag)
        g["target_next_inv"] = g["inventory"].shift(-1)
        g["mom_change"] = g["inventory"].diff()
        g["target_next_mom_up"] = (g["inventory"].shift(-1) > g["inventory"]).astype(
            "Int64"
        )
        out.append(g)
    return pd.concat(out, ignore_index=True)


def time_based_split(
    df: pd.DataFrame,
    date_col: str = "month",
    test_fraction: float = 0.2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Last test_fraction of distinct months (globally) go to test — simple time split."""
    months = df[date_col].drop_duplicates().sort_values()
    n_test = max(1, int(len(months) * test_fraction))
    cutoff = months.iloc[-n_test]
    train = df[df[date_col] < cutoff]
    test = df[df[date_col] >= cutoff]
    return train, test
