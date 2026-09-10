"""Integration test for Cohort Audit FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient
from src.dashboard.server import app

client = TestClient(app)


def test_cohort_summary_endpoint():
    """Verify GET /api/cohorts/summary returns valid schema."""
    response = client.get("/api/cohorts/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_audits" in data
    assert "confirmed_rugs" in data
    assert "surviving_candidates" in data
    assert "learning_metrics" in data


def test_cohort_bucket_endpoint():
    """Verify GET /api/cohorts/bucket/rugs returns list."""
    response = client.get("/api/cohorts/bucket/rugs?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["bucket"] == "rugs"
    assert isinstance(data["tokens"], list)


def test_cohort_learning_metrics_endpoint():
    """Verify GET /api/cohorts/learning-metrics."""
    response = client.get("/api/cohorts/learning-metrics")
    assert response.status_code == 200
    data = response.json()
    assert "learning_metrics" in data
    assert "rule_attribution" in data


def test_cohort_matrix_tokens_endpoint():
    """Verify GET /api/cohorts/matrix-tokens returns tokens with metadata."""
    response = client.get("/api/cohorts/matrix-tokens?type=tp&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "tp"
    assert isinstance(data["tokens"], list)
    if len(data["tokens"]) > 0:
        first = data["tokens"][0]
        assert "mint" in first
        assert "symbol" in first
        assert "initial_risk_tier" in first
        assert "status" in first

    # Invalid type should return 400
    bad_res = client.get("/api/cohorts/matrix-tokens?type=invalid")
    assert bad_res.status_code == 400

