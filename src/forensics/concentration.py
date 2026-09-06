"""Holder concentration and wealth inequality metrics (HHI & Gini)."""

import numpy as np
from typing import List, Dict
from pydantic import BaseModel, Field


class ConcentrationMetrics(BaseModel):
    mint: str
    total_holders_analyzed: int
    top5_supply_pct: float
    top10_supply_pct: float
    hhi_score: float
    gini_coefficient: float
    distribution_tier: str  # DECENTRALIZED, MODERATE, CONCENTRATED, MONOPOLY
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    flags: List[str] = Field(default_factory=list)


class ConcentrationCalculator:
    """Calculates HHI, Gini coefficient, and supply concentration across token holders."""

    def __init__(
        self,
        total_supply: float = 1_000_000_000.0,
        hhi_warning_threshold: float = 2500.0,
        top10_warning_pct: float = 25.0
    ):
        self.total_supply = total_supply
        self.hhi_warning_threshold = hhi_warning_threshold
        self.top10_warning_pct = top10_warning_pct

    def compute(self, mint: str, balances: List[float]) -> ConcentrationMetrics:
        """Calculate inequality metrics across a list of holder balances."""
        if not balances:
            return ConcentrationMetrics(
                mint=mint,
                total_holders_analyzed=0,
                top5_supply_pct=0.0,
                top10_supply_pct=0.0,
                hhi_score=0.0,
                gini_coefficient=0.0,
                distribution_tier="UNKNOWN",
                risk_level="UNKNOWN",
                flags=["NO_HOLDER_DATA"]
            )

        # Sort descending
        sorted_balances = sorted(balances, reverse=True)
        total_held = sum(sorted_balances)
        effective_supply = max(self.total_supply, total_held)

        # Calculate percentage shares (0 - 100)
        shares = [(b / effective_supply) * 100.0 for b in sorted_balances]

        top5_pct = sum(shares[:5])
        top10_pct = sum(shares[:10])

        # Herfindahl-Hirschman Index (HHI)
        # Sum of squared percentage shares
        hhi = sum(s ** 2 for s in shares)

        # Gini Coefficient
        gini = self._calculate_gini(np.array(sorted_balances, dtype=np.float64))

        # Determine tiers
        flags: List[str] = []
        if hhi >= 2500.0 or top10_pct >= 40.0:
            tier = "MONOPOLY"
            risk = "CRITICAL"
            flags.append(f"EXTREME_SUPPLY_CONCENTRATION (HHI: {hhi:.0f}, Top 10: {top10_pct:.1f}%)")
        elif hhi >= 1500.0 or top10_pct >= self.top10_warning_pct:
            tier = "CONCENTRATED"
            risk = "HIGH"
            flags.append(f"HIGH_SUPPLY_CONCENTRATION (HHI: {hhi:.0f}, Top 10: {top10_pct:.1f}%)")
        elif hhi >= 800.0:
            tier = "MODERATE"
            risk = "MEDIUM"
        else:
            tier = "DECENTRALIZED"
            risk = "LOW"

        if gini > 0.85:
            flags.append(f"HIGH_GINI_INEQUALITY ({gini:.2f})")
            if risk == "LOW":
                risk = "MEDIUM"

        return ConcentrationMetrics(
            mint=mint,
            total_holders_analyzed=len(sorted_balances),
            top5_supply_pct=top5_pct,
            top10_supply_pct=top10_pct,
            hhi_score=hhi,
            gini_coefficient=gini,
            distribution_tier=tier,
            risk_level=risk,
            flags=flags
        )

    @staticmethod
    def _calculate_gini(arr: np.ndarray) -> float:
        """Compute the Gini coefficient of a numpy array."""
        if len(arr) == 0:
            return 0.0
        if np.amin(arr) < 0:
            arr -= np.amin(arr)  # Values must be non-negative
        arr += 0.0000001  # Avoid division by zero
        arr = np.sort(arr)
        index = np.arange(1, arr.shape[0] + 1)
        n = arr.shape[0]
        return float(((np.sum((2 * index - n - 1) * arr)) / (n * np.sum(arr))))
