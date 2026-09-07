"""Survival analysis engine for modeling token lifespans and hazard ratios."""

import logging
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm
from pydantic import BaseModel, Field

logger = logging.getLogger("nemo.survival_model")


class HazardRatioResult(BaseModel):
    feature_name: str
    coef_beta: float
    hazard_ratio: float  # exp(beta)
    std_err: float
    z_score: float
    p_value: float
    ci_lower: float
    ci_upper: float


class KaplanMeierCurve(BaseModel):
    time_points: List[float]
    survival_probabilities: List[float]
    at_risk_counts: List[int]
    event_counts: List[int]


class SurvivalAnalysisEngine:
    """Estimates Kaplan-Meier survival curves and fits Cox Proportional Hazards regression."""

    def fit_kaplan_meier(self, durations: np.ndarray, events: np.ndarray) -> KaplanMeierCurve:
        """Compute non-parametric Kaplan-Meier survival curve S(t)."""
        df = pd.DataFrame({"time": durations, "event": events}).sort_values("time")

        unique_times = np.sort(df["time"].unique())
        n_at_risk = len(durations)

        s_t = 1.0
        time_points = [0.0]
        survival_probs = [1.0]
        at_risk_list = [n_at_risk]
        event_list = [0]

        for t in unique_times:
            sub = df[df["time"] == t]
            d = sub["event"].sum()  # Events at time t
            c = len(sub) - d        # Censored at time t

            if n_at_risk > 0:
                s_t *= (1.0 - (d / n_at_risk))

            time_points.append(float(t))
            survival_probs.append(float(s_t))
            at_risk_list.append(int(n_at_risk))
            event_list.append(int(d))

            n_at_risk -= (d + c)

        return KaplanMeierCurve(
            time_points=time_points,
            survival_probabilities=survival_probs,
            at_risk_counts=at_risk_list,
            event_counts=event_list
        )

    def fit_cox_ph(
        self,
        X: pd.DataFrame,
        durations: np.ndarray,
        events: np.ndarray
    ) -> List[HazardRatioResult]:
        """Fit Cox Proportional Hazards model using partial likelihood maximization."""
        n_samples, n_features = X.shape
        feature_names = list(X.columns)

        # Standardize features for numerical stability
        X_mat = X.values.astype(np.float64)
        means = np.mean(X_mat, axis=0)
        stds = np.std(X_mat, axis=0)
        stds[stds == 0] = 1.0
        X_norm = (X_mat - means) / stds

        # Sort by duration descending for efficient Breslow partial likelihood
        order = np.argsort(-durations)
        X_ordered = X_norm[order]
        events_ordered = events[order]

        def neg_log_partial_likelihood(beta):
            theta = np.dot(X_ordered, beta)
            # Clip for numerical stability
            theta = np.clip(theta, -20.0, 20.0)
            exp_theta = np.exp(theta)

            # Cumulative sum of risk set
            cumsum_exp = np.cumsum(exp_theta)
            # Log partial likelihood
            log_lik = np.sum(events_ordered * (theta - np.log(cumsum_exp + 1e-12)))
            # Add mild L2 regularization
            l2_reg = 0.01 * np.sum(beta ** 2)
            return -(log_lik - l2_reg)

        init_beta = np.zeros(n_features)
        res = minimize(
            neg_log_partial_likelihood,
            init_beta,
            method="BFGS",
            options={"maxiter": 300, "disp": False}
        )

        beta_hat = res.x
        # Invert Hessian approximation to compute standard errors
        try:
            cov_mat = res.hess_inv
            if isinstance(cov_mat, np.ndarray):
                var_diag = np.diag(cov_mat)
            else:
                var_diag = np.diag(cov_mat.todense())
            std_errs = np.sqrt(np.maximum(1e-4, var_diag))
        except Exception:
            std_errs = np.ones(n_features) * 0.1

        results = []
        for i, name in enumerate(feature_names):
            # Scale back coefficient to original feature scale with numerical clamping
            beta_orig = beta_hat[i] / stds[i]
            se_orig = std_errs[i] / stds[i]
            z = beta_orig / (se_orig + 1e-8)
            p_val = 2 * (1 - norm.cdf(abs(z)))
            hr = float(np.exp(np.clip(beta_orig, -4.0, 4.0)))
            ci_low = float(np.exp(np.clip(beta_orig - 1.96 * se_orig, -4.0, 4.0)))
            ci_high = float(np.exp(np.clip(beta_orig + 1.96 * se_orig, -4.0, 4.0)))

            results.append(HazardRatioResult(
                feature_name=name,
                coef_beta=float(beta_orig),
                hazard_ratio=hr,
                std_err=float(se_orig),
                z_score=float(z),
                p_value=float(p_val),
                ci_lower=ci_low,
                ci_upper=ci_high
            ))

        # Sort by impact (hazard ratio distance from 1.0)
        results.sort(key=lambda r: abs(r.hazard_ratio - 1.0), reverse=True)
        return results
