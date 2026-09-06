"""Supervised classification engine with rolling time-series validation and feature ranking."""

import logging
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, GradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    matthews_corrcoef,
    f1_score,
    precision_recall_curve,
    auc,
    brier_score_loss,
    roc_auc_score,
)
from pydantic import BaseModel, Field

logger = logging.getLogger("nemo.classifier")


class EvaluationMetrics(BaseModel):
    mcc: float
    f1_positive: float
    pr_auc: float
    roc_auc: float
    brier_score: float
    train_samples: int
    test_samples: int


class FeatureImportance(BaseModel):
    feature: str
    importance_gain: float
    importance_split: int


class RugPredictionResult(BaseModel):
    mint: str
    rug_probability: float  # 0.0 to 1.0
    predicted_label: int    # 1 for rug, 0 for good
    risk_tier: str          # LOW, MEDIUM, HIGH, CRITICAL
    top_contributing_features: List[Tuple[str, float]] = Field(default_factory=list)


class RugPullClassifier:
    """Histogram-based Gradient Boosting classifier adhering to the arXiv methodology on Solana memecoins."""

    def __init__(self, max_iter: int = 100, learning_rate: float = 0.05, max_depth: int = 5):
        self.max_iter = max_iter
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.model: Optional[HistGradientBoostingClassifier] = None
        self.feature_names: List[str] = []
        self._importances: List[FeatureImportance] = []

    def train_with_rolling_cv(
        self,
        df: pd.DataFrame,
        feature_cols: List[str],
        target_col: str = "is_rug_pull",
        time_col: str = "created_at",
        n_splits: int = 4
    ) -> Tuple[List[EvaluationMetrics], List[FeatureImportance]]:
        """Train model using Forward Rolling Time-Series Cross-Validation (arXiv §IV-C)."""
        if df.empty or len(df) < 15:
            raise ValueError(f"Insufficient data for rolling CV ({len(df)} rows). Need at least 15.")

        self.feature_names = feature_cols
        # Sort chronologically to prevent look-ahead bias
        df_sorted = df.sort_values(time_col).reset_index(drop=True)

        split_size = len(df_sorted) // (n_splits + 1)
        fold_metrics: List[EvaluationMetrics] = []

        for fold in range(1, n_splits + 1):
            train_idx = range(0, fold * split_size)
            test_idx = range(fold * split_size, (fold + 1) * split_size)

            train_df = df_sorted.iloc[train_idx]
            test_df = df_sorted.iloc[test_idx]

            X_train = train_df[feature_cols]
            y_train = train_df[target_col]
            X_test = test_df[feature_cols]
            y_test = test_df[target_col]

            # Check positive sample guarantee
            if y_train.nunique() < 2 or y_test.nunique() < 2:
                continue

            fold_model = HistGradientBoostingClassifier(
                max_iter=self.max_iter,
                learning_rate=self.learning_rate,
                max_depth=self.max_depth,
                random_state=42 + fold
            )
            fold_model.fit(X_train, y_train)

            y_pred_proba = fold_model.predict_proba(X_test)[:, 1]
            y_pred = (y_pred_proba >= 0.5).astype(int)

            # Calculate robust imbalanced metrics
            mcc = float(matthews_corrcoef(y_test, y_pred))
            f1 = float(f1_score(y_test, y_pred, pos_label=1, zero_division=0))
            precision, recall, _ = precision_recall_curve(y_test, y_pred_proba)
            pr_auc = float(auc(recall, precision))
            try:
                roc_auc = float(roc_auc_score(y_test, y_pred_proba))
            except ValueError:
                roc_auc = 0.5
            brier = float(brier_score_loss(y_test, y_pred_proba))

            fold_metrics.append(EvaluationMetrics(
                mcc=mcc,
                f1_positive=f1,
                pr_auc=pr_auc,
                roc_auc=roc_auc,
                brier_score=brier,
                train_samples=len(train_df),
                test_samples=len(test_df)
            ))

        # Final fit on entire dataset for production inference
        self.model = HistGradientBoostingClassifier(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            random_state=42
        )
        self.model.fit(df_sorted[feature_cols], df_sorted[target_col])

        # Calculate permutation importance on training dataset
        perm = permutation_importance(self.model, df_sorted[feature_cols], df_sorted[target_col], n_repeats=5, random_state=42)
        importances = []
        for name, mean_imp in zip(self.feature_names, perm.importances_mean):
            importances.append(FeatureImportance(
                feature=name,
                importance_gain=float(max(0.0, mean_imp)),
                importance_split=1
            ))
        importances.sort(key=lambda x: x.importance_gain, reverse=True)
        self._importances = importances

        return fold_metrics, importances

    def get_feature_importances(self) -> List[FeatureImportance]:
        """Extract permutation importance ranking."""
        return self._importances

    def predict(self, feature_row: Dict[str, Any], mint: str = "") -> RugPredictionResult:
        """Run inference on a single token feature vector."""
        if not self.model:
            raise RuntimeError("Classifier has not been trained yet.")

        # Align features
        X_df = pd.DataFrame([[feature_row.get(col, 0.0) for col in self.feature_names]], columns=self.feature_names)
        proba = float(self.model.predict_proba(X_df)[0, 1])
        pred_label = 1 if proba >= 0.5 else 0

        if proba >= 0.75:
            tier = "CRITICAL"
        elif proba >= 0.50:
            tier = "HIGH"
        elif proba >= 0.25:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        top_contribs = []
        for imp in self._importances[:5]:
            val = feature_row.get(imp.feature, 0.0)
            top_contribs.append((imp.feature, float(val)))

        return RugPredictionResult(
            mint=mint,
            rug_probability=proba,
            predicted_label=pred_label,
            risk_tier=tier,
            top_contributing_features=top_contribs
        )
