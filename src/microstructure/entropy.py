"""Trade entropy and wash-trading detection module."""

import math
from typing import List, Dict, Optional
import numpy as np
from pydantic import BaseModel, Field


class EntropyResult(BaseModel):
    mint: str
    total_trades_analyzed: int
    shannon_entropy: float
    sign_autocorrelation: float
    most_common_size_pct: float
    micro_trade_pct: float = 0.0
    circular_wallet_ratio: float = 0.0
    is_micro_cadence_ladder: bool = False
    is_circular_ring: bool = False
    is_wash_trading: bool
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    flags: List[str] = Field(default_factory=list)


class TradeEntropyDetector:
    """Calculates Shannon entropy, trade cadence, and Sybil wallet loops to unmask wash-trading syndicates."""

    def __init__(self, min_trades_required: int = 10, low_entropy_threshold: float = 2.0):
        self.min_trades_required = min_trades_required
        self.low_entropy_threshold = low_entropy_threshold

    def compute(self, mint: str, trades: List[dict]) -> EntropyResult:
        """Analyze trade sizes, sequence signs, and wallet cycles for wash trading fingerprints.

        Each trade dict may contain:
          - 'tx_type' or 'txType': 'buy' or 'sell'
          - 'sol_amount' or 'solAmount': float
          - 'user' or 'wallet' or 'signer': str
          - 'timestamp' or 'slot': float/int
        """
        if len(trades) < self.min_trades_required:
            return EntropyResult(
                mint=mint,
                total_trades_analyzed=len(trades),
                shannon_entropy=0.0,
                sign_autocorrelation=0.0,
                most_common_size_pct=0.0,
                micro_trade_pct=0.0,
                circular_wallet_ratio=0.0,
                is_micro_cadence_ladder=False,
                is_circular_ring=False,
                is_wash_trading=False,
                risk_level="UNKNOWN",
                flags=["INSUFFICIENT_TRADES_FOR_ENTROPY"]
            )

        sol_sizes = [
            max(0.00001, float(t.get("sol_amount") or t.get("solAmount") or 0.0))
            for t in trades
        ]
        tx_types = [
            str(t.get("tx_type") or t.get("txType") or "buy").lower()
            for t in trades
        ]
        tx_signs = [1 if tx_type == "buy" else -1 for tx_type in tx_types]

        # 1. Calculate Shannon Entropy on discretized trade sizes
        entropy = self._calculate_shannon_entropy(sol_sizes)

        # 2. Calculate Lag-1 Autocorrelation of trade signs
        autocorr = self._calculate_sign_autocorrelation(tx_signs)

        # 3. Check for repetitive identical order sizes (mode dominance)
        rounded_sizes = [round(s, 3) for s in sol_sizes]
        counts: Dict[float, int] = {}
        for s in rounded_sizes:
            counts[s] = counts.get(s, 0) + 1

        most_common_count = max(counts.values()) if counts else 0
        most_common_pct = (most_common_count / len(rounded_sizes)) * 100.0

        # 4. Micro-Cadence Wash Ladder Detection (< 0.001 SOL trades)
        micro_trades = [s for s in sol_sizes if s < 0.001]
        micro_trade_pct = (len(micro_trades) / len(sol_sizes)) * 100.0

        # 5. Wallet Distribution & Circular Ping-Pong Ring Analysis
        wallets = [
            t.get("user") or t.get("wallet") or t.get("signer")
            for t in trades
            if t.get("user") or t.get("wallet") or t.get("signer")
        ]
        circular_ratio = 0.0
        is_circular = False
        is_micro_ladder = False

        flags: List[str] = []
        is_wash = False
        risk = "LOW"

        # Check circular ring if wallet data is present
        if len(wallets) >= 15:
            from collections import Counter
            w_counts = Counter(wallets)
            unique_w = len(w_counts)
            circular_ratio = len(wallets) / max(1, unique_w)

            # Circular Ping-Pong: small wallet ring cycling high trade volume
            if unique_w <= 30 and len(wallets) >= 20 and circular_ratio >= 1.8:
                is_circular = True
                is_wash = True
                risk = "CRITICAL"
                flags.append(
                    f"CIRCULAR_PING_PONG_RING ({unique_w} wallets cycling {len(wallets)} txs, {circular_ratio:.1f}x density)"
                )
            # Sybil Swarm: high number of wallets doing exactly 1 micro-trade
            elif unique_w >= 15 and micro_trade_pct >= 40.0:
                singles = sum(1 for c in w_counts.values() if c == 1)
                if (singles / unique_w) >= 0.70:
                    is_wash = True
                    risk = "CRITICAL"
                    flags.append(
                        f"SYBIL_SWARM_FARM ({unique_w} burner wallets executing single micro-buys)"
                    )

        # Micro-Cadence Wash Ladder heuristic
        if micro_trade_pct >= 35.0 and len(trades) >= 12:
            is_micro_ladder = True
            is_wash = True
            risk = "CRITICAL"
            flags.append(
                f"MICRO_CADENCE_WASH_LADDER ({micro_trade_pct:.1f}% trades < 0.001 SOL)"
            )

        # Standard Entropy heuristics
        if entropy < self.low_entropy_threshold and len(trades) >= 15:
            is_wash = True
            if risk != "CRITICAL":
                risk = "HIGH"
            flags.append(f"LOW_TRADE_ENTROPY ({entropy:.2f} < {self.low_entropy_threshold})")

        if most_common_pct >= 40.0 and len(trades) >= 15:
            is_wash = True
            risk = "CRITICAL"
            flags.append(f"REPETITIVE_ORDER_SIZES ({most_common_pct:.1f}% trades identical)")

        # Negative autocorrelation indicates alternating buy/sell bot churn
        if autocorr < -0.40 and len(trades) >= 20:
            is_wash = True
            if risk != "CRITICAL":
                risk = "HIGH"
            flags.append(f"ALTERNATING_BOT_CHURN (Autocorr: {autocorr:.2f})")

        return EntropyResult(
            mint=mint,
            total_trades_analyzed=len(trades),
            shannon_entropy=entropy,
            sign_autocorrelation=autocorr,
            most_common_size_pct=most_common_pct,
            micro_trade_pct=micro_trade_pct,
            circular_wallet_ratio=circular_ratio,
            is_micro_cadence_ladder=is_micro_ladder,
            is_circular_ring=is_circular,
            is_wash_trading=is_wash,
            risk_level=risk,
            flags=flags
        )

    def _calculate_shannon_entropy(self, sizes: List[float], num_bins: int = 15) -> float:
        """Compute Shannon entropy across log-binned order sizes."""
        if not sizes:
            return 0.0

        # Use log10 scale so 0.01 SOL, 0.1 SOL, 1.0 SOL, 10 SOL map evenly
        log_sizes = np.log10(np.array(sizes) + 1e-6)
        hist, _ = np.histogram(log_sizes, bins=num_bins)

        total = np.sum(hist)
        if total == 0:
            return 0.0

        probabilities = hist / total
        probabilities = probabilities[probabilities > 0]  # Filter zero bins

        entropy = -np.sum(probabilities * np.log2(probabilities))
        return float(entropy)

    def _calculate_sign_autocorrelation(self, signs: List[int]) -> float:
        """Calculate lag-1 autocorrelation of trade signs (+1 for buy, -1 for sell)."""
        if len(signs) < 5:
            return 0.0

        arr = np.array(signs, dtype=np.float64)
        mean = np.mean(arr)
        variance = np.var(arr)

        if variance == 0:
            return 1.0  # All buys or all sells

        # Lag-1 covariance
        lag1_cov = np.mean((arr[:-1] - mean) * (arr[1:] - mean))
        return float(lag1_cov / variance)
