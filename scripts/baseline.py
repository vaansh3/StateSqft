#!/usr/bin/env python3
"""
Train two baseline models on metro inventory data and print evaluation metrics.

Model 1 — Linear regression: predict next month's inventory.
Model 2 — Logistic regression: predict whether inventory rises next month (binary).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running without pip install -e .
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from sklearn.linear_model import LinearRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.metrics import confusion_matrix
from sklearn.preprocessing import StandardScaler

from bia652p.data.load import load_wide
from bia652p.data.load import melt_inventory_long
from bia652p.data.load import profile_dataset
from bia652p.evaluate import classification_metrics
from bia652p.evaluate import classification_metrics_proba
from bia652p.evaluate import regression_metrics
from bia652p.features import add_lags_and_targets
from bia652p.features import add_time_features
from bia652p.features import time_based_split

FEATURE_COLS = [
    "inv_lag_1",
    "inv_lag_2",
    "inv_lag_3",
    "month_sin",
    "month_cos",
    "SizeRank",
]


def main() -> None:
    print("Loading CSV…")
    wide = load_wide()
    long_df = melt_inventory_long(wide)
    profile = profile_dataset(long_df)
    print("Dataset profile:", profile)

    long_df = add_time_features(long_df)
    long_df = add_lags_and_targets(long_df, lag_months=(1, 2, 3))

    modeling = long_df.dropna(
        subset=FEATURE_COLS + ["target_next_inv", "target_next_mom_up"]
    ).copy()
    modeling["target_next_mom_up"] = modeling["target_next_mom_up"].astype(int)

    print(f"\nRows after dropping missing lags/targets: {len(modeling):,}")

    # Class balance (classification task)
    vc = modeling["target_next_mom_up"].value_counts().sort_index()
    print("\nClass balance (target_next_mom_up: 0=down/same, 1=up):")
    for cls, cnt in vc.items():
        pct = 100 * cnt / len(modeling)
        print(f"  class {cls}: {cnt:,} ({pct:.1f}%)")

    train_df, test_df = time_based_split(modeling, test_fraction=0.2)
    print(
        f"\nTime split — train months < test; train rows: {len(train_df):,}, test: {len(test_df):,}"
    )

    X_train, X_test = train_df[FEATURE_COLS], test_df[FEATURE_COLS]
    y_reg_train = train_df["target_next_inv"].values
    y_reg_test = test_df["target_next_inv"].values
    y_cls_train = train_df["target_next_mom_up"].values
    y_cls_test = test_df["target_next_mom_up"].values

    # --- Model 1: Linear regression (scale features for fair comparison with SVM later) ---
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    reg = LinearRegression()
    reg.fit(X_train_s, y_reg_train)
    pred_reg = reg.predict(X_test_s)
    reg_scores = regression_metrics(y_reg_test, pred_reg)
    print("\n=== Model 1: LinearRegression (target = next month inventory) ===")
    for k, v in reg_scores.items():
        print(f"  {k}: {v:.4f}")

    # --- Model 2: Logistic regression ---
    # class_weight='balanced' if you want to counter skew; report both raw and balanced in writeup
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)
    clf.fit(X_train_s, y_cls_train)
    pred_cls = clf.predict(X_test_s)
    proba_cls = clf.predict_proba(X_test_s)[:, 1]

    cls_scores = classification_metrics(y_cls_test, pred_cls)
    cls_scores.update(classification_metrics_proba(y_cls_test, proba_cls))
    print("\n=== Model 2: LogisticRegression (target = inventory up next month) ===")
    for k, v in cls_scores.items():
        print(f"  {k}: {v:.4f}")

    print("\nConfusion matrix [rows=true, cols=pred]:\n", confusion_matrix(y_cls_test, pred_cls))
    print("\nClassification report:\n", classification_report(y_cls_test, pred_cls, digits=4))

    # Feature names for optional coefficient inspection
    print("\nLinearRegression coef (standardized features):")
    for name, coef in zip(FEATURE_COLS, reg.coef_, strict=True):
        print(f"  {name}: {coef:.6f}")
    print(f"  intercept: {reg.intercept_:.4f}")


if __name__ == "__main__":
    main()
