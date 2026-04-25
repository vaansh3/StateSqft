from __future__ import annotations

import pandas as pd

from bia652p.config import DATA_CSV

ID_COLS = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName"]


def load_wide(path=None) -> pd.DataFrame:
    path = path or DATA_CSV
    df = pd.read_csv(path)
    return df


def melt_inventory_long(df: pd.DataFrame) -> pd.DataFrame:
    """Wide metro table -> long format with parsed month-end dates."""
    date_cols = [c for c in df.columns if c not in ID_COLS]
    long_df = df.melt(
        id_vars=ID_COLS,
        value_vars=date_cols,
        var_name="month",
        value_name="inventory",
    )
    long_df["month"] = pd.to_datetime(long_df["month"], errors="coerce")
    long_df["inventory"] = pd.to_numeric(long_df["inventory"], errors="coerce") # ensures that the inventory column is a number
    long_df = long_df.dropna(subset=["month"])
    long_df = long_df.sort_values(["RegionID", "month"]).reset_index(drop=True)
    return long_df


def profile_dataset(long_df: pd.DataFrame) -> dict:
    """Summary stats for reports and sanity checks."""
    inv = long_df["inventory"]
    return {
        "n_rows": len(long_df), # count rows
        "n_metros": long_df["RegionID"].nunique(),
        "date_min": long_df["month"].min(),
        "date_max": long_df["month"].max(),
        "inventory_missing_pct": float(inv.isna().mean() * 100),
        "inventory_min": float(inv.min(skipna=True)) if inv.notna().any() else None,
        "inventory_max": float(inv.max(skipna=True)) if inv.notna().any() else None,
        "inventory_mean": float(inv.mean(skipna=True)) if inv.notna().any() else None,
    }
