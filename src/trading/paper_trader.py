"""Automated Paper Trading Engine with Dual-Strategy Execution.

Strategies:
1. SEAL_SCALPER: Rapid momentum scalper (inspired by glowingseal9825).
   - Enter on high velocity & ultra-low forensic risk (<=30).
   - Position size: 0.50 SOL.
   - TP1 at +30% (sell 50%), TP2 at +80% (sell 50%), Trailing Stop -12%, Hard Stop -15%.

2. OLDWHALE_FREEROLLER: Conviction accumulator (inspired by oldwhale06352).
   - Enter on high-dispersion, organic holder curves (risk <=25, dev <=5%).
   - Position size: 1.00 SOL.
   - At +100% (2x), sell 50% of tokens to retrieve 100% initial SOL invested ($0 cost basis).
   - Retain remaining 50% as a zero-risk moonbag.
   - Emergency dump if forensic risk spikes to CRITICAL (>65).
"""

import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

from src.ingestion.storage import DuckDBStorage
from src.forensics.engine import TokenForensicReport

logger = logging.getLogger("nemo.paper_trader")


@dataclass
class PaperPosition:
    mint: str
    symbol: str
    name: str
    strategy: str  # "SEAL_SCALPER" or "OLDWHALE_FREEROLLER"
    entry_price_sol: float
    current_price_sol: float
    peak_price_sol: float
    initial_sol_invested: float
    remaining_tokens: float
    initial_tokens: float
    entry_timestamp: float
    last_updated: float
    tp1_executed: bool = False
    freeroll_executed: bool = False
    realized_sol_profit: float = 0.0
    status: str = "OPEN"  # "OPEN", "CLOSED"

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price_sol <= 0:
            return 0.0
        return ((self.current_price_sol - self.entry_price_sol) / self.entry_price_sol) * 100.0

    @property
    def current_market_value_sol(self) -> float:
        return self.remaining_tokens * self.current_price_sol

    @property
    def total_pnl_sol(self) -> float:
        # realized from partial sales + current value - initial investment
        return self.realized_sol_profit + self.current_market_value_sol - self.initial_sol_invested

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["unrealized_pnl_pct"] = round(self.unrealized_pnl_pct, 2)
        d["current_market_value_sol"] = round(self.current_market_value_sol, 6)
        d["total_pnl_sol"] = round(self.total_pnl_sol, 6)
        return d


@dataclass
class PaperTradeLog:
    trade_id: str
    timestamp: str
    mint: str
    symbol: str
    strategy: str
    action: str  # "BUY", "PARTIAL_TP1", "FULL_TP2", "FREE_ROLL_2X", "STOP_LOSS", "TRAILING_STOP", "EMERGENCY_RUG_EXIT"
    price_sol: float
    tokens_transacted: float
    sol_amount: float
    realized_pnl_sol: float
    pnl_pct: float
    reason: str


class PaperTradingEngine:
    """Manages virtual portfolios, signal evaluation, and automated trade execution."""

    INITIAL_CAPITAL_PER_STRATEGY = 10.0  # 10 SOL per strategy
    SCALPER_SIZE_SOL = 0.50
    FREEROLLER_SIZE_SOL = 1.00

    def __init__(self, storage: Optional[DuckDBStorage] = None):
        self.storage = storage
        self.cash_balances = {
            "SEAL_SCALPER": self.INITIAL_CAPITAL_PER_STRATEGY,
            "OLDWHALE_FREEROLLER": self.INITIAL_CAPITAL_PER_STRATEGY,
        }
        self.positions: Dict[str, PaperPosition] = {}  # key: f"{strategy}:{mint}"
        self.trades_history: List[PaperTradeLog] = []
        self._init_db_tables()

    def _init_db_tables(self):
        """Create paper trading tables in DuckDB if storage is provided."""
        if not self.storage or not hasattr(self.storage, "_conn"):
            return
        try:
            self.storage._conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    trade_id VARCHAR PRIMARY KEY,
                    timestamp TIMESTAMP,
                    mint VARCHAR,
                    symbol VARCHAR,
                    strategy VARCHAR,
                    action VARCHAR,
                    price_sol DOUBLE,
                    tokens_transacted DOUBLE,
                    sol_amount DOUBLE,
                    realized_pnl_sol DOUBLE,
                    pnl_pct DOUBLE,
                    reason VARCHAR
                );
            """)
        except Exception as e:
            logger.debug(f"DuckDB paper_trades table creation notice: {e}")

    def evaluate_token(
        self,
        mint: str,
        symbol: str,
        name: str,
        price_sol: float,
        forensic_report: TokenForensicReport,
        bonding_curve_progress_pct: float = 0.0,
        recent_trade_count: int = 0,
        token_age_seconds: float = 0.0,
        is_token_creation: bool = False
    ) -> List[PaperTradeLog]:
        """Evaluate a token against both strategy criteria.
        
        CRITICAL GUARD:
        Never buy a coin at token creation (block 0). >99.5% of newly created
        coins on pump.fun rug or collapse immediately. We require demonstrable
        momentum on the bonding curve (progress >= 8.0%) and at least 8 real
        transactions before entering.
        """
        executed_trades = []
        if is_token_creation:
            logger.debug(f"[PAPER TRADING] Skipping block-0 token creation for {mint}: no survival proof yet.")
            return executed_trades

        # Minimum survival / momentum hurdle for ANY paper trading entry:
        # Must have at least 8 trades and reached >= 8.0% on bonding curve (MCap > ~$7,500)
        if bonding_curve_progress_pct < 8.0 or recent_trade_count < 8:
            return executed_trades

        if price_sol <= 0:
            price_sol = 0.00000003  # Standard pump.fun starting price floor (~30 lamports)

        risk_score = getattr(forensic_report, "composite_risk_score", None)
        if risk_score is None:
            risk_score = getattr(forensic_report, "forensic_risk_score", 0.0)
        flags = forensic_report.all_flags

        # Anti-rug sanity check: NO trades on critical rugs or sybil wash rings
        has_sybil = any(f in flags for f in [
            "WASH_LADDER_SYNDICATE", "CIRCULAR_PING_PONG_RING", "BLOCK0_SUPPLY_CORNERED", 
            "DEV_DUMP_CASCADE", "MONOPOLISTIC_SUPPLY"
        ])
        if has_sybil or risk_score >= 35:
            return executed_trades

        dev_buy_pct = 0.0
        if forensic_report.bundle_analysis and hasattr(forensic_report.bundle_analysis, "block0_supply_pct"):
            dev_buy_pct = (forensic_report.bundle_analysis.block0_supply_pct or 0.0) * 100.0
        elif hasattr(forensic_report, "dev_initial_buy_ratio"):
            dev_buy_pct = (getattr(forensic_report, "dev_initial_buy_ratio", 0.0) or 0.0) * 100.0

        # -----------------------------------------------------------------
        # 1. Strategy A: SEAL_SCALPER Gate (glowingseal9825 Momentum Scalper)
        # -----------------------------------------------------------------
        scalper_key = f"SEAL_SCALPER:{mint}"
        if scalper_key not in self.positions:
            # Entry condition:
            # - Active breakout: bonding curve between 8.0% and 65.0%
            # - At least 8 confirmed trades
            # - Forensic Risk Score <= 25 (ultra clean)
            # - Dev holding <= 8.0%
            # - High entropy (>= 1.8) indicating diverse, organic trade sizes
            entropy_ok = True
            if forensic_report.entropy_analysis:
                entropy_ok = forensic_report.entropy_analysis.shannon_entropy >= 1.8

            if (8.0 <= bonding_curve_progress_pct <= 65.0 and
                recent_trade_count >= 8 and
                risk_score <= 25 and
                dev_buy_pct <= 8.0 and
                len(flags) <= 2 and
                entropy_ok):
                if self.cash_balances["SEAL_SCALPER"] >= self.SCALPER_SIZE_SOL:
                    tokens_bought = self.SCALPER_SIZE_SOL / price_sol
                    self.cash_balances["SEAL_SCALPER"] -= self.SCALPER_SIZE_SOL

                    pos = PaperPosition(
                        mint=mint,
                        symbol=symbol or "PUMP",
                        name=name or mint[:8],
                        strategy="SEAL_SCALPER",
                        entry_price_sol=price_sol,
                        current_price_sol=price_sol,
                        peak_price_sol=price_sol,
                        initial_sol_invested=self.SCALPER_SIZE_SOL,
                        remaining_tokens=tokens_bought,
                        initial_tokens=tokens_bought,
                        entry_timestamp=time.time(),
                        last_updated=time.time()
                    )
                    self.positions[scalper_key] = pos

                    reason = (
                        f"[SEAL-SCALP MOMENTUM ENTRY] Active breakout on curve ({bonding_curve_progress_pct:.1f}%, {recent_trade_count} trades). "
                        f"Clean forensics (Risk: {risk_score}/100, Flags: {len(flags)}). Entering 0.50 SOL."
                    )
                    trade = self._record_trade(
                        mint=mint,
                        symbol=pos.symbol,
                        strategy="SEAL_SCALPER",
                        action="BUY",
                        price_sol=price_sol,
                        tokens_transacted=tokens_bought,
                        sol_amount=self.SCALPER_SIZE_SOL,
                        realized_pnl_sol=0.0,
                        pnl_pct=0.0,
                        reason=reason
                    )
                    executed_trades.append(trade)

        # -----------------------------------------------------------------
        # 2. Strategy B: OLDWHALE_FREEROLLER Gate (oldwhale06352 Conviction Accumulator)
        # -----------------------------------------------------------------
        freeroll_key = f"OLDWHALE_FREEROLLER:{mint}"
        if freeroll_key not in self.positions:
            # Entry condition:
            # - Established survivor: Bonding curve progress >= 15.0% OR age >= 300s (5 minutes)
            # - At least 12 confirmed trades
            # - Ultra clean: Risk Score <= 20
            # - Dev bought <= 4.0% of supply
            # - Zero suspicious flags
            survivor_proven = (bonding_curve_progress_pct >= 15.0 or token_age_seconds >= 300.0)
            if (survivor_proven and
                recent_trade_count >= 12 and
                risk_score <= 20 and
                dev_buy_pct <= 4.0 and
                len(flags) == 0):
                if self.cash_balances["OLDWHALE_FREEROLLER"] >= self.FREEROLLER_SIZE_SOL:
                    tokens_bought = self.FREEROLLER_SIZE_SOL / price_sol
                    self.cash_balances["OLDWHALE_FREEROLLER"] -= self.FREEROLLER_SIZE_SOL

                    pos = PaperPosition(
                        mint=mint,
                        symbol=symbol or "PUMP",
                        name=name or mint[:8],
                        strategy="OLDWHALE_FREEROLLER",
                        entry_price_sol=price_sol,
                        current_price_sol=price_sol,
                        peak_price_sol=price_sol,
                        initial_sol_invested=self.FREEROLLER_SIZE_SOL,
                        remaining_tokens=tokens_bought,
                        initial_tokens=tokens_bought,
                        entry_timestamp=time.time(),
                        last_updated=time.time()
                    )
                    self.positions[freeroll_key] = pos

                    reason = (
                        f"[OLDWHALE-FREEROLL ENTRY] Proven survivor ({bonding_curve_progress_pct:.1f}% curve, {recent_trade_count} trades). "
                        f"Dev bought only {dev_buy_pct:.1f}% (Risk: {risk_score}/100). Entering 1.00 SOL targeting 2x break-even."
                    )
                    trade = self._record_trade(
                        mint=mint,
                        symbol=pos.symbol,
                        strategy="OLDWHALE_FREEROLLER",
                        action="BUY",
                        price_sol=price_sol,
                        tokens_transacted=tokens_bought,
                        sol_amount=self.FREEROLLER_SIZE_SOL,
                        realized_pnl_sol=0.0,
                        pnl_pct=0.0,
                        reason=reason
                    )
                    executed_trades.append(trade)

        return executed_trades

    def update_price(self, mint: str, current_price_sol: float) -> List[PaperTradeLog]:
        """Update active positions for a mint with the latest market price and evaluate exit triggers."""
        if current_price_sol <= 0:
            return []

        executed_trades = []
        keys_to_check = [k for k in self.positions.keys() if k.endswith(f":{mint}")]

        for key in keys_to_check:
            pos = self.positions.get(key)
            if not pos or pos.status != "OPEN":
                continue

            pos.current_price_sol = current_price_sol
            pos.last_updated = time.time()
            if current_price_sol > pos.peak_price_sol:
                pos.peak_price_sol = current_price_sol

            gain_pct = pos.unrealized_pnl_pct
            pullback_from_peak = ((pos.peak_price_sol - current_price_sol) / pos.peak_price_sol) * 100.0

            # -----------------------------------------------------------------
            # SEAL_SCALPER Exit Evaluation
            # -----------------------------------------------------------------
            if pos.strategy == "SEAL_SCALPER":
                # Trigger 1: TP1 at +30% (Sell 50%)
                if gain_pct >= 30.0 and not pos.tp1_executed:
                    sell_tokens = pos.remaining_tokens * 0.50
                    sol_received = sell_tokens * current_price_sol
                    pnl_sol = sol_received - (pos.initial_sol_invested * 0.50)

                    self.cash_balances["SEAL_SCALPER"] += sol_received
                    pos.remaining_tokens -= sell_tokens
                    pos.realized_sol_profit += pnl_sol
                    pos.tp1_executed = True

                    reason = f"[SCALP TP1 HIT] +{gain_pct:.1f}% gain reached! Sold 50% position to lock in profit (+{pnl_sol:.4f} SOL)."
                    trade = self._record_trade(
                        mint=mint,
                        symbol=pos.symbol,
                        strategy="SEAL_SCALPER",
                        action="PARTIAL_TP1",
                        price_sol=current_price_sol,
                        tokens_transacted=sell_tokens,
                        sol_amount=sol_received,
                        realized_pnl_sol=pnl_sol,
                        pnl_pct=gain_pct,
                        reason=reason
                    )
                    executed_trades.append(trade)

                # Trigger 2: TP2 at +80% (Sell remaining 50%)
                elif gain_pct >= 80.0:
                    sell_tokens = pos.remaining_tokens
                    sol_received = sell_tokens * current_price_sol
                    allocated_cost = pos.initial_sol_invested * 0.50 if pos.tp1_executed else pos.initial_sol_invested
                    pnl_sol = sol_received - allocated_cost

                    self.cash_balances["SEAL_SCALPER"] += sol_received
                    pos.realized_sol_profit += pnl_sol
                    pos.remaining_tokens = 0.0
                    pos.status = "CLOSED"

                    reason = f"[SCALP TP2 HIT] Moon target +{gain_pct:.1f}% reached! Fully liquidated 100% position flat (+{pnl_sol:.4f} SOL)."
                    trade = self._record_trade(
                        mint=mint,
                        symbol=pos.symbol,
                        strategy="SEAL_SCALPER",
                        action="FULL_TP2",
                        price_sol=current_price_sol,
                        tokens_transacted=sell_tokens,
                        sol_amount=sol_received,
                        realized_pnl_sol=pnl_sol,
                        pnl_pct=gain_pct,
                        reason=reason
                    )
                    executed_trades.append(trade)
                    del self.positions[key]

                # Trigger 3: Trailing Stop (-12% from peak if profit > 15%)
                elif pos.peak_price_sol > pos.entry_price_sol * 1.15 and pullback_from_peak >= 12.0:
                    sell_tokens = pos.remaining_tokens
                    sol_received = sell_tokens * current_price_sol
                    allocated_cost = pos.initial_sol_invested * 0.50 if pos.tp1_executed else pos.initial_sol_invested
                    pnl_sol = sol_received - allocated_cost

                    self.cash_balances["SEAL_SCALPER"] += sol_received
                    pos.realized_sol_profit += pnl_sol
                    pos.remaining_tokens = 0.0
                    pos.status = "CLOSED"

                    reason = f"[TRAILING STOP] Momentum stalled: pulled back {pullback_from_peak:.1f}% from peak. Sold remaining ({pnl_sol:+.4f} SOL)."
                    trade = self._record_trade(
                        mint=mint,
                        symbol=pos.symbol,
                        strategy="SEAL_SCALPER",
                        action="TRAILING_STOP",
                        price_sol=current_price_sol,
                        tokens_transacted=sell_tokens,
                        sol_amount=sol_received,
                        realized_pnl_sol=pnl_sol,
                        pnl_pct=gain_pct,
                        reason=reason
                    )
                    executed_trades.append(trade)
                    del self.positions[key]

                # Trigger 4: Hard Stop Loss (-15%)
                elif gain_pct <= -15.0:
                    sell_tokens = pos.remaining_tokens
                    sol_received = sell_tokens * current_price_sol
                    pnl_sol = sol_received - pos.initial_sol_invested

                    self.cash_balances["SEAL_SCALPER"] += sol_received
                    pos.realized_sol_profit += pnl_sol
                    pos.remaining_tokens = 0.0
                    pos.status = "CLOSED"

                    reason = f"[STOP LOSS HIT] Price fell {gain_pct:.1f}% below entry. Cut position to protect principal ({pnl_sol:.4f} SOL)."
                    trade = self._record_trade(
                        mint=mint,
                        symbol=pos.symbol,
                        strategy="SEAL_SCALPER",
                        action="STOP_LOSS",
                        price_sol=current_price_sol,
                        tokens_transacted=sell_tokens,
                        sol_amount=sol_received,
                        realized_pnl_sol=pnl_sol,
                        pnl_pct=gain_pct,
                        reason=reason
                    )
                    executed_trades.append(trade)
                    del self.positions[key]

            # -----------------------------------------------------------------
            # OLDWHALE_FREEROLLER Exit Evaluation
            # -----------------------------------------------------------------
            elif pos.strategy == "OLDWHALE_FREEROLLER":
                # Trigger 1: 2x Break-Even Rule (+100%)
                # Sell exactly 50% of original tokens.
                # Since price doubled, 50% tokens = 100% of initial SOL invested!
                if gain_pct >= 100.0 and not pos.freeroll_executed:
                    sell_tokens = pos.initial_tokens * 0.50
                    sol_received = sell_tokens * current_price_sol
                    # Initial investment is 100% recovered!
                    self.cash_balances["OLDWHALE_FREEROLLER"] += sol_received
                    pos.remaining_tokens -= sell_tokens
                    pos.freeroll_executed = True

                    reason = (
                        f"[2X FREE-ROLL HIT] 100% price surge (+{gain_pct:.1f}%)! Sold 50% tokens to retrieve "
                        f"100% initial capital ({sol_received:.2f} SOL). Remaining {pos.remaining_tokens:,.0f} tokens "
                        f"are now a 100% RISK-FREE MOONBAG."
                    )
                    trade = self._record_trade(
                        mint=mint,
                        symbol=pos.symbol,
                        strategy="OLDWHALE_FREEROLLER",
                        action="FREE_ROLL_2X",
                        price_sol=current_price_sol,
                        tokens_transacted=sell_tokens,
                        sol_amount=sol_received,
                        realized_pnl_sol=sol_received - pos.initial_sol_invested,
                        pnl_pct=gain_pct,
                        reason=reason
                    )
                    executed_trades.append(trade)

        return executed_trades

    def handle_token_demoted(self, mint: str, new_risk_score: float, reason_detail: str) -> List[PaperTradeLog]:
        """Emergency liquidate any open paper positions when a token is demoted from Survivor to Rug."""
        executed_trades = []
        keys_to_dump = [k for k in self.positions.keys() if k.endswith(f":{mint}")]

        for key in keys_to_dump:
            pos = self.positions.get(key)
            if not pos or pos.status != "OPEN":
                continue

            strat = pos.strategy
            sell_tokens = pos.remaining_tokens
            price_sol = pos.current_price_sol
            sol_received = sell_tokens * price_sol
            pnl_sol = (pos.realized_sol_profit + sol_received) - pos.initial_sol_invested
            pnl_pct = pos.unrealized_pnl_pct

            self.cash_balances[strat] += sol_received
            pos.remaining_tokens = 0.0
            pos.status = "CLOSED"

            reason = (
                f"[EMERGENCY RUG DUMP] Token failed re-scan & was demoted to CONFIRMED_RUG "
                f"(Risk Score: {new_risk_score}/100 | {reason_detail}). Dumped 100% position immediately!"
            )

            trade = self._record_trade(
                mint=mint,
                symbol=pos.symbol,
                strategy=strat,
                action="EMERGENCY_RUG_EXIT",
                price_sol=price_sol,
                tokens_transacted=sell_tokens,
                sol_amount=sol_received,
                realized_pnl_sol=pnl_sol,
                pnl_pct=pnl_pct,
                reason=reason
            )
            executed_trades.append(trade)
            del self.positions[key]

        return executed_trades

    def _record_trade(
        self,
        mint: str,
        symbol: str,
        strategy: str,
        action: str,
        price_sol: float,
        tokens_transacted: float,
        sol_amount: float,
        realized_pnl_sol: float,
        pnl_pct: float,
        reason: str
    ) -> PaperTradeLog:
        """Create and store a trade log entry."""
        trade_id = f"ptrade_{int(time.time() * 1000)}_{len(self.trades_history) + 1}"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        trade = PaperTradeLog(
            trade_id=trade_id,
            timestamp=ts,
            mint=mint,
            symbol=symbol,
            strategy=strategy,
            action=action,
            price_sol=price_sol,
            tokens_transacted=tokens_transacted,
            sol_amount=sol_amount,
            realized_pnl_sol=realized_pnl_sol,
            pnl_pct=pnl_pct,
            reason=reason
        )
        self.trades_history.insert(0, trade)
        if len(self.trades_history) > 200:
            self.trades_history.pop()

        # Persist to DuckDB if available
        if self.storage and hasattr(self.storage, "_conn"):
            try:
                self.storage._conn.execute("""
                    INSERT INTO paper_trades (
                        trade_id, timestamp, mint, symbol, strategy, action,
                        price_sol, tokens_transacted, sol_amount, realized_pnl_sol,
                        pnl_pct, reason
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """, [
                    trade.trade_id,
                    datetime.now(timezone.utc),
                    trade.mint,
                    trade.symbol,
                    trade.strategy,
                    trade.action,
                    trade.price_sol,
                    trade.tokens_transacted,
                    trade.sol_amount,
                    trade.realized_pnl_sol,
                    trade.pnl_pct,
                    trade.reason
                ])
            except Exception as e:
                logger.debug(f"DuckDB paper_trades insert notice: {e}")

        logger.info(f"PAPER TRADE: [{strategy}] {action} on {symbol} ({mint[:8]}...) - {reason}")
        return trade

    def get_summary_stats(self) -> Dict[str, Any]:
        """Aggregate performance telemetry for both strategies."""
        stats = {}
        for strat in ["SEAL_SCALPER", "OLDWHALE_FREEROLLER"]:
            strat_trades = [t for t in self.trades_history if t.strategy == strat and t.action != "BUY"]
            closed_profitable = [t for t in strat_trades if t.realized_pnl_sol > 0]
            win_rate = (len(closed_profitable) / len(strat_trades) * 100.0) if strat_trades else 0.0
            realized_pnl = sum(t.realized_pnl_sol for t in strat_trades)

            strat_positions = [p.to_dict() for k, p in self.positions.items() if p.strategy == strat and p.status == "OPEN"]
            unrealized_pnl = sum(p.total_pnl_sol for k, p in self.positions.items() if p.strategy == strat and p.status == "OPEN")
            moonbags = len([p for k, p in self.positions.items() if p.strategy == strat and p.freeroll_executed and p.status == "OPEN"])

            cash = self.cash_balances[strat]
            portfolio_val = cash + sum(p.current_market_value_sol for k, p in self.positions.items() if p.strategy == strat and p.status == "OPEN")

            stats[strat] = {
                "initial_capital_sol": self.INITIAL_CAPITAL_PER_STRATEGY,
                "cash_sol": round(cash, 4),
                "portfolio_value_sol": round(portfolio_val, 4),
                "realized_pnl_sol": round(realized_pnl, 4),
                "unrealized_pnl_sol": round(unrealized_pnl, 4),
                "total_return_pct": round(((portfolio_val - self.INITIAL_CAPITAL_PER_STRATEGY) / self.INITIAL_CAPITAL_PER_STRATEGY) * 100.0, 2),
                "win_rate_pct": round(win_rate, 1),
                "total_trades_count": len([t for t in self.trades_history if t.strategy == strat]),
                "active_positions_count": len(strat_positions),
                "active_positions": strat_positions,
                "moonbags_retained": moonbags,
            }

        return {
            "strategies": stats,
            "total_portfolio_sol": round(stats["SEAL_SCALPER"]["portfolio_value_sol"] + stats["OLDWHALE_FREEROLLER"]["portfolio_value_sol"], 4),
            "total_realized_pnl_sol": round(stats["SEAL_SCALPER"]["realized_pnl_sol"] + stats["OLDWHALE_FREEROLLER"]["realized_pnl_sol"], 4),
            "recent_trades": [asdict(t) for t in self.trades_history[:40]],
            "active_positions_count": len(self.positions),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def reset_balances(self):
        """Reset virtual portfolios to starting capital."""
        self.cash_balances = {
            "SEAL_SCALPER": self.INITIAL_CAPITAL_PER_STRATEGY,
            "OLDWHALE_FREEROLLER": self.INITIAL_CAPITAL_PER_STRATEGY,
        }
        self.positions.clear()
        self.trades_history.clear()
        logger.info("Paper trading portfolios reset to initial state.")
