from __future__ import annotations

import numpy as np
from sklearn import metrics


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """RMSE, MAE, R² for continuous targets."""
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    yt, yp = y_true[mask], y_pred[mask]
    if len(yt) == 0:
        return {"rmse": float("nan"), "mae": float("nan"), "r2": float("nan")}
    return {
        "rmse": float(np.sqrt(metrics.mean_squared_error(yt, yp))),
        "mae": float(metrics.mean_absolute_error(yt, yp)),
        "r2": float(metrics.r2_score(yt, yp)),
    }


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Accuracy, precision, recall, F1 for binary (or macro multiclass)."""
    mask = np.isfinite(y_true.astype(float))
    yt = y_true[mask].astype(int)
    yp = y_pred[mask].astype(int)
    if len(yt) == 0:
        return {
            "accuracy": float("nan"),
            "precision": float("nan"),
            "recall": float("nan"),
            "f1": float("nan"),
        }
    return {
        "accuracy": float(metrics.accuracy_score(yt, yp)),
        "precision": float(metrics.precision_score(yt, yp, zero_division=0)),
        "recall": float(metrics.recall_score(yt, yp, zero_division=0)),
        "f1": float(metrics.f1_score(yt, yp, zero_division=0)),
    }


def classification_metrics_proba(
    y_true: np.ndarray,
    y_score: np.ndarray,
) -> dict[str, float]:
    """ROC-AUC when model outputs probability of positive class."""
    mask = np.isfinite(y_true.astype(float)) & np.isfinite(y_score)
    yt = y_true[mask].astype(int)
    ys = y_score[mask]
    if len(np.unique(yt)) < 2:
        return {"roc_auc": float("nan")}
    return {"roc_auc": float(metrics.roc_auc_score(yt, ys))}
