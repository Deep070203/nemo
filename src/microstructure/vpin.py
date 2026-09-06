"""Volume-Synchronized Probability of Toxicity (VPIN) calculator."""

from typing import List, Optional
from pydantic import BaseModel, Field


class VPINResult(BaseModel):
    mint: str
    vpin_score: float  # 0.0 to 1.0
    total_buckets_filled: int
    bucket_volume: float
    toxicity_tier: str  # BALANCED, MODERATE, TOXIC
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    flags: List[str] = Field(default_factory=list)


class VPINCalculator:
    """Calculates VPIN to measure order flow toxicity and informed insider liquidation."""

    def __init__(self, num_buckets: int = 20, default_bucket_volume_sol: float = 1.0):
        self.num_buckets = num_buckets
        self.default_bucket_volume_sol = default_bucket_volume_sol

    def compute(self, mint: str, trades: List[dict], bucket_size: Optional[float] = None) -> VPINResult:
        """Calculate VPIN from a sequence of trades.

        Each trade dict should contain:
          - 'tx_type': 'buy' or 'sell'
          - 'sol_amount': float
        """
        if not trades:
            return VPINResult(
                mint=mint,
                vpin_score=0.0,
                total_buckets_filled=0,
                bucket_volume=0.0,
                toxicity_tier="INSUFFICIENT_DATA",
                risk_level="UNKNOWN",
                flags=["NO_TRADES"]
            )

        total_sol_vol = sum(t.get("sol_amount", 0.0) for t in trades)
        if total_sol_vol <= 0:
            return VPINResult(
                mint=mint,
                vpin_score=0.0,
                total_buckets_filled=0,
                bucket_volume=0.0,
                toxicity_tier="INSUFFICIENT_DATA",
                risk_level="UNKNOWN",
                flags=["ZERO_VOLUME"]
            )

        # Determine bucket size V
        V = bucket_size or max(self.default_bucket_volume_sol, total_sol_vol / max(5, self.num_buckets))

        bucket_buy = 0.0
        bucket_sell = 0.0
        current_bucket_vol = 0.0
        bucket_imbalances: List[float] = []

        for trade in trades:
            tx_type = trade.get("tx_type", "buy")
            sol = max(0.0, trade.get("sol_amount", 0.0))
            is_buy = (tx_type.lower() == "buy")

            remaining_trade = sol
            while remaining_trade > 0:
                space_in_bucket = V - current_bucket_vol
                fill_amount = min(remaining_trade, space_in_bucket)

                if is_buy:
                    bucket_buy += fill_amount
                else:
                    bucket_sell += fill_amount

                current_bucket_vol += fill_amount
                remaining_trade -= fill_amount

                # Bucket completed
                if current_bucket_vol >= V:
                    imbalance = abs(bucket_buy - bucket_sell)
                    bucket_imbalances.append(imbalance)

                    # Reset for next bucket
                    bucket_buy = 0.0
                    bucket_sell = 0.0
                    current_bucket_vol = 0.0

        if not bucket_imbalances:
            # If volume was less than one full bucket, calculate partial imbalance
            partial_vol = bucket_buy + bucket_sell
            if partial_vol > 0:
                vpin = abs(bucket_buy - bucket_sell) / partial_vol
            else:
                vpin = 0.0
            buckets_filled = 0
        else:
            # Calculate rolling average over last N buckets
            active_imbalances = bucket_imbalances[-self.num_buckets:]
            vpin = sum(active_imbalances) / (len(active_imbalances) * V)
            buckets_filled = len(bucket_imbalances)

        vpin = min(1.0, max(0.0, vpin))

        flags = []
        if vpin >= 0.70 and buckets_filled >= 3:
            tier = "TOXIC"
            risk = "CRITICAL"
            flags.append(f"HIGH_ORDER_FLOW_TOXICITY (VPIN: {vpin:.2f})")
        elif vpin >= 0.50:
            tier = "MODERATE"
            risk = "HIGH"
            flags.append(f"ELEVATED_ORDER_FLOW_IMBALANCE (VPIN: {vpin:.2f})")
        else:
            tier = "BALANCED"
            risk = "LOW"

        return VPINResult(
            mint=mint,
            vpin_score=vpin,
            total_buckets_filled=buckets_filled,
            bucket_volume=V,
            toxicity_tier=tier,
            risk_level=risk,
            flags=flags
        )
