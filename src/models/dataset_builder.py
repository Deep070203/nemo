"""Dataset builder for extracting 5-minute feature vectors and empirical rug pull targets."""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np

from src.ingestion.storage import DuckDBStorage
from src.microstructure.vpin import VPINCalculator
from src.microstructure.entropy import TradeEntropyDetector

logger = logging.getLogger("nemo.dataset_builder")


class DatasetBuilder:
    """Builds tabular feature sets and ground-truth labels following the arXiv/ScienceDirect methodology."""

    def __init__(self, storage: DuckDBStorage, observation_window_minutes: int = 5, label_horizon_minutes: int = 60):
        self.storage = storage
        self.observation_window_minutes = observation_window_minutes
        self.label_horizon_minutes = label_horizon_minutes
        self.vpin_calc = VPINCalculator()
        self.entropy_calc = TradeEntropyDetector()

    def build_dataset_from_storage(self) -> pd.DataFrame:
        """Extract multi-modal features and labels for all tokens in DuckDB."""
        # Query tokens with creation timestamps
        tokens_df = self.storage._conn.execute("""
            SELECT mint, name, symbol, creator, initial_buy, sol_amount, created_at
            FROM tokens
            ORDER BY created_at ASC;
        """).df()

        if tokens_df.empty:
            logger.warning("No tokens found in database to construct dataset.")
            return pd.DataFrame()

        rows = []
        for _, token_row in tokens_df.iterrows():
            mint = token_row["mint"]
            t0 = pd.to_datetime(token_row["created_at"])
            t_obs = t0 + timedelta(minutes=self.observation_window_minutes)
            t_label = t0 + timedelta(minutes=self.label_horizon_minutes)

            # Query trades up to the label horizon
            trades_df = self.storage._conn.execute("""
                SELECT tx_type, sol_amount, token_amount, price_sol, v_sol, timestamp
                FROM trades
                WHERE mint || '' = $1 AND timestamp <= $2
                ORDER BY timestamp ASC;
            """, [mint, t_label]).df()

            feature_dict = self.extract_features_and_label(token_row, trades_df, t0, t_obs, t_label)
            if feature_dict:
                rows.append(feature_dict)

        return pd.DataFrame(rows)

    def extract_features_and_label(
        self,
        token_meta: Dict[str, Any],
        trades_df: pd.DataFrame,
        t0: datetime,
        t_obs: datetime,
        t_label: datetime
    ) -> Optional[Dict[str, Any]]:
        """Compute the 5-minute predictor vector and ground-truth 1-hour survival target."""
        mint = token_meta["mint"]
        dev_buy = float(token_meta.get("initial_buy") or 0.0)
        dev_sol = float(token_meta.get("sol_amount") or 0.0)

        # 1. Split trades into 5-minute observation window vs subsequent outcome window
        if not trades_df.empty:
            trades_df["dt"] = pd.to_datetime(trades_df["timestamp"])
            obs_trades = trades_df[trades_df["dt"] <= t_obs]
            outcome_trades = trades_df[(trades_df["dt"] > t_obs) & (trades_df["dt"] <= t_label)]
        else:
            obs_trades = pd.DataFrame()
            outcome_trades = pd.DataFrame()

        # 2. Extract 5-minute Microstructure Features
        obs_trade_count = len(obs_trades)
        obs_trades_list = obs_trades.to_dict(orient="records") if not obs_trades.empty else []

        buys = obs_trades[obs_trades["tx_type"] == "buy"] if not obs_trades.empty else pd.DataFrame()
        sells = obs_trades[obs_trades["tx_type"] == "sell"] if not obs_trades.empty else pd.DataFrame()

        total_sol_vol = float(obs_trades["sol_amount"].sum()) if not obs_trades.empty else 0.0
        buy_sol_vol = float(buys["sol_amount"].sum()) if not buys.empty else 0.0
        sell_sol_vol = float(sells["sol_amount"].sum()) if not sells.empty else 0.0

        buy_vol_ratio = (buy_sol_vol / total_sol_vol) if total_sol_vol > 0 else 0.5
        buy_count_ratio = (len(buys) / obs_trade_count) if obs_trade_count > 0 else 0.5

        # Price dynamics over first 5 minutes
        prices = obs_trades["price_sol"].dropna().values if not obs_trades.empty else np.array([])
        if len(prices) > 0:
            p_start = prices[0]
            p_max = float(np.max(prices))
            p_min = float(np.min(prices))
            p_end = prices[-1]
            return_5m = (p_end - p_start) / (p_start + 1e-12)
            price_volatility = float(np.std(prices))
        else:
            p_start = p_max = p_min = p_end = return_5m = price_volatility = 0.0

        # 3. Microstructure Toxicity & Wash Trading
        vpin_res = self.vpin_calc.compute(mint, obs_trades_list)
        entropy_res = self.entropy_calc.compute(mint, obs_trades_list)

        # 4. On-Chain Forensic Features (from DuckDB forensic_flags)
        flag_rows = self.storage._conn.execute("""
            SELECT flag_type FROM forensic_flags WHERE mint = $1;
        """, [mint]).fetchall()
        flags = [r[0] for r in flag_rows]

        is_jito = int(any("JITO_MEV_TIP" in f for f in flags))
        is_block0_cornered = int(any("BLOCK0_SUPPLY_CORNERED" in f for f in flags))
        is_sybil_ring = int(any("SYBIL" in f for f in flags))
        is_image_clone = int(any("REUSED_MEME_IMAGE_CLONE" in f for f in flags))

        dev_buy_supply_pct = (dev_buy / 1_000_000_000.0) * 100.0

        # 5. Dual Rug Pull Ground-Truth Label Definition (arXiv §IV-B)
        # Combine TVL Drop (MDD < -0.90) and Idle Inactivity (> 80% duration)
        all_trades_count = len(trades_df)
        is_tvl_rug = 0
        is_idle_rug = 0
        survival_minutes = float(self.label_horizon_minutes)

        if not trades_df.empty and "v_sol" in trades_df.columns:
            v_sols = trades_df["v_sol"].dropna().values
            if len(v_sols) > 0:
                peak_sol = np.max(v_sols)
                end_sol = v_sols[-1]
                # TVL Maximum Drawdown (MDD)
                mdd = (end_sol - peak_sol) / peak_sol
                if mdd < -0.90:
                    is_tvl_rug = 1
                    # Find time of drop
                    drop_idx = np.where((v_sols - peak_sol) / peak_sol < -0.90)[0]
                    if len(drop_idx) > 0:
                        first_drop_dt = pd.to_datetime(trades_df.iloc[drop_idx[0]]["timestamp"])
                        survival_minutes = max(1.0, (first_drop_dt - t0).total_seconds() / 60.0)

        # Inactivity/Idle check: If trading ceased after first few minutes
        if not outcome_trades.empty:
            outcome_tx_count = len(outcome_trades)
            if outcome_tx_count <= 2 and obs_trade_count > 0:
                is_idle_rug = 1
        elif obs_trade_count > 0:
            # Completely idle during outcome window
            is_idle_rug = 1
            survival_minutes = min(survival_minutes, float(self.observation_window_minutes))

        # Target label: 1 if either TVL collapsed or trading went idle, 0 if sustainable
        is_rug_pull = int(is_tvl_rug == 1 or is_idle_rug == 1)

        return {
            "mint": mint,
            "created_at": t0,
            # Microstructure predictors (5m)
            "obs_trade_count": obs_trade_count,
            "total_sol_vol": total_sol_vol,
            "buy_vol_ratio": buy_vol_ratio,
            "buy_count_ratio": buy_count_ratio,
            "price_return_5m": return_5m,
            "price_volatility": price_volatility,
            "vpin_score": vpin_res.vpin_score,
            "shannon_entropy": entropy_res.shannon_entropy,
            "sign_autocorrelation": entropy_res.sign_autocorrelation,
            "most_common_order_pct": entropy_res.most_common_size_pct,
            # On-chain forensic predictors
            "dev_buy_supply_pct": dev_buy_supply_pct,
            "dev_sol_amount": dev_sol,
            "is_jito_mev": is_jito,
            "is_block0_cornered": is_block0_cornered,
            "is_sybil_ring": is_sybil_ring,
            "is_image_clone": is_image_clone,
            # Ground truth targets
            "is_tvl_rug": is_tvl_rug,
            "is_idle_rug": is_idle_rug,
            "is_rug_pull": is_rug_pull,
            "survival_minutes": survival_minutes,
        }
