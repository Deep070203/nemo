"""Unit and integration tests for Phase 4 machine learning and survival analysis."""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

from src.models.survival_model import SurvivalAnalysisEngine
from src.models.classifier import RugPullClassifier
from src.models.dataset_builder import DatasetBuilder


def test_kaplan_meier_survival_estimator():
    engine = SurvivalAnalysisEngine()
    durations = np.array([5.0, 10.0, 15.0, 30.0, 60.0, 60.0])
    events = np.array([1, 1, 1, 1, 0, 0])  # 4 rugs, 2 censored

    km = engine.fit_kaplan_meier(durations, events)

    assert len(km.time_points) >= 5
    assert km.survival_probabilities[0] == 1.0
    # Survival probability should decrease over time
    assert km.survival_probabilities[-1] < km.survival_probabilities[1]
    assert km.survival_probabilities[-1] > 0.0


def test_cox_proportional_hazards():
    engine = SurvivalAnalysisEngine()
    np.random.seed(42)
    n = 100

    # Feature 1: High Jito tip (strongly increases hazard)
    x1 = np.random.binomial(1, 0.4, size=n)
    # Feature 2: High VPIN toxicity
    x2 = np.random.uniform(0.1, 0.9, size=n)

    # Durations: shorter when x1=1 or x2 is high
    base_duration = np.random.exponential(scale=30.0, size=n)
    durations = base_duration / (1.0 + 2.0 * x1 + 3.0 * x2)
    durations = np.maximum(1.0, durations)
    events = np.random.binomial(1, 0.85, size=n)

    X = pd.DataFrame({"is_jito_mev": x1, "vpin_score": x2})
    results = engine.fit_cox_ph(X, durations, events)

    assert len(results) == 2
    # Jito MEV should have Hazard Ratio > 1.0 (higher risk of collapse)
    jito_hr = next(r for r in results if r.feature_name == "is_jito_mev")
    assert jito_hr.hazard_ratio > 1.0
    assert jito_hr.coef_beta > 0.0


def test_rug_pull_classifier_rolling_cv():
    np.random.seed(42)
    n_samples = 60

    # Generate synthetic historical dataset with timestamps
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    times = [base_time + timedelta(hours=i) for i in range(n_samples)]

    # Predictor features
    vpin = np.random.uniform(0.1, 0.9, size=n_samples)
    jito = np.random.binomial(1, 0.3, size=n_samples)
    entropy = np.random.uniform(0.5, 4.0, size=n_samples)
    dev_buy = np.random.uniform(0.0, 30.0, size=n_samples)

    # Target: High vpin, jito, or dev_buy leads to rug
    rug_prob = 1.0 / (1.0 + np.exp(-(3.0 * vpin + 2.5 * jito + 0.1 * dev_buy - 1.5 * entropy - 1.0)))
    targets = (np.random.rand(n_samples) < rug_prob).astype(int)

    df = pd.DataFrame({
        "created_at": times,
        "vpin_score": vpin,
        "is_jito_mev": jito,
        "shannon_entropy": entropy,
        "dev_buy_supply_pct": dev_buy,
        "is_rug_pull": targets
    })

    feature_cols = ["vpin_score", "is_jito_mev", "shannon_entropy", "dev_buy_supply_pct"]
    classifier = RugPullClassifier(max_iter=30, learning_rate=0.1, max_depth=3)

    metrics_list, importances = classifier.train_with_rolling_cv(
        df,
        feature_cols=feature_cols,
        target_col="is_rug_pull",
        time_col="created_at",
        n_splits=2
    )

    assert len(metrics_list) >= 1
    assert len(importances) == 4
    # All features should have positive gain
    assert importances[0].importance_gain > 0

    # Test single-token inference
    sample_scam = {
        "vpin_score": 0.85,
        "is_jito_mev": 1,
        "shannon_entropy": 0.8,
        "dev_buy_supply_pct": 25.0
    }
    pred = classifier.predict(sample_scam, mint="ScamToken123")
    assert pred.rug_probability > 0.5
    assert pred.predicted_label == 1
    assert pred.risk_tier in ("HIGH", "CRITICAL")
