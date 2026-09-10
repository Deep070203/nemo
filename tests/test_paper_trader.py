"""Unit tests for the Automated Paper Trading Engine and Survivor Demotion."""

import pytest
from src.trading.paper_trader import PaperTradingEngine, PaperPosition, PaperTradeLog
from src.forensics.engine import TokenForensicReport
from src.microstructure.entropy import EntropyResult


from src.forensics.bundle_detector import Block0BundleAnalysis


def make_test_report(risk_score: float = 15.0, flags: list = None, dev_buy_ratio: float = 0.02, entropy: float = 2.4):
    """Helper to construct a mock forensic report for testing."""
    mint = "TestMint11111111111111111111111111111111111"
    return TokenForensicReport(
        mint=mint,
        composite_risk_score=int(risk_score),
        risk_tier="LOW" if risk_score <= 30 else ("MEDIUM" if risk_score <= 50 else "HIGH"),
        bundle_analysis=Block0BundleAnalysis(
            mint=mint,
            slot=100000,
            block0_supply_pct=dev_buy_ratio
        ),
        entropy_analysis=EntropyResult(
            mint=mint,
            total_trades_analyzed=50,
            shannon_entropy=entropy,
            sign_autocorrelation=0.05,
            is_uniform_distribution=False,
            most_common_size_pct=5.0,
            micro_trade_pct=10.0,
            circular_wallet_ratio=0.0,
            is_micro_cadence_ladder=False,
            is_circular_ring=False,
            is_wash_trading=False,
            risk_level="LOW"
        ),
        all_flags=flags or []
    )



def test_scalper_entry_and_take_profit():
    """Test Seal Scalper entry and progressive TP1 / TP2 exit ladder."""
    engine = PaperTradingEngine(storage=None)
    mint = "ScalpMint1111111111111111111111111111111111"
    initial_price = 0.00000005

    report = make_test_report(risk_score=20.0)

    # 1. Entry evaluation (Requires active breakout: progress >= 8%, trades >= 8)
    trades = engine.evaluate_token(
        mint=mint,
        symbol="SCALPT",
        name="Scalp Token",
        price_sol=initial_price,
        forensic_report=report,
        bonding_curve_progress_pct=18.0,
        recent_trade_count=12
    )
    assert len(trades) >= 1
    scalp_buy = [t for t in trades if t.strategy == "SEAL_SCALPER" and t.action == "BUY"][0]
    assert scalp_buy.sol_amount == 0.50
    assert engine.cash_balances["SEAL_SCALPER"] == 9.50
    assert f"SEAL_SCALPER:{mint}" in engine.positions

    # 2. Price rises +35% (TP1 Trigger: +30%)
    price_tp1 = initial_price * 1.35
    tp1_trades = engine.update_price(mint, price_tp1)
    assert len(tp1_trades) == 1
    tp1_trade = tp1_trades[0]
    assert tp1_trade.action == "PARTIAL_TP1"
    assert tp1_trade.realized_pnl_sol > 0
    assert "TP1 HIT" in tp1_trade.reason

    # Position should still be OPEN with 50% remaining tokens
    pos = engine.positions[f"SEAL_SCALPER:{mint}"]
    assert pos.status == "OPEN"
    assert pos.tp1_executed is True

    # 3. Price rises to +85% (TP2 Trigger: +80%)
    price_tp2 = initial_price * 1.85
    tp2_trades = engine.update_price(mint, price_tp2)
    assert len(tp2_trades) == 1
    tp2_trade = tp2_trades[0]
    assert tp2_trade.action == "FULL_TP2"
    assert tp2_trade.realized_pnl_sol > 0
    assert "TP2 HIT" in tp2_trade.reason

    # Position should now be CLOSED and removed
    assert f"SEAL_SCALPER:{mint}" not in engine.positions
    assert engine.cash_balances["SEAL_SCALPER"] > 10.0  # Overall profitable


def test_freeroller_entry_and_2x_breakeven():
    """Test OldWhale Conviction Free-Roller 2x Break-Even rule and moonbag retention."""
    engine = PaperTradingEngine(storage=None)
    mint = "WhaleMint1111111111111111111111111111111111"
    initial_price = 0.00000010

    report = make_test_report(risk_score=15.0, dev_buy_ratio=0.03, flags=[])

    # 1. Entry evaluation (Requires proven survivor: progress >= 15%, trades >= 12)
    trades = engine.evaluate_token(
        mint=mint,
        symbol="WHALE",
        name="Whale Cult",
        price_sol=initial_price,
        forensic_report=report,
        bonding_curve_progress_pct=22.0,
        recent_trade_count=16
    )
    assert len(trades) >= 1
    whale_buy = [t for t in trades if t.strategy == "OLDWHALE_FREEROLLER" and t.action == "BUY"][0]
    assert whale_buy.sol_amount == 1.00
    assert engine.cash_balances["OLDWHALE_FREEROLLER"] == 9.00

    pos = engine.positions[f"OLDWHALE_FREEROLLER:{mint}"]
    initial_tokens = pos.initial_tokens

    # 2. Price surges 2x (+100%)
    price_2x = initial_price * 2.00
    ex_trades = engine.update_price(mint, price_2x)
    free_roll_trade = [t for t in ex_trades if t.strategy == "OLDWHALE_FREEROLLER"][0]
    assert free_roll_trade.action == "FREE_ROLL_2X"
    assert "2X FREE-ROLL HIT" in free_roll_trade.reason
    assert "RISK-FREE MOONBAG" in free_roll_trade.reason

    # Initial 1.0 SOL capital is fully retrieved back to cash!
    assert round(engine.cash_balances["OLDWHALE_FREEROLLER"], 2) >= 10.00

    # Half the tokens remain held as a moonbag!
    pos_after = engine.positions[f"OLDWHALE_FREEROLLER:{mint}"]
    assert pos_after.status == "OPEN"
    assert pos_after.freeroll_executed is True
    assert round(pos_after.remaining_tokens, 0) == round(initial_tokens * 0.5, 0)


def test_survivor_demotion_and_emergency_exit():
    """Test that when a coin is demoted to rug on re-scan, an emergency market dump occurs."""
    engine = PaperTradingEngine(storage=None)
    mint = "DemoteMint111111111111111111111111111111111"
    initial_price = 0.00000008

    report = make_test_report(risk_score=18.0)
    engine.evaluate_token(
        mint=mint,
        symbol="DEMOTE",
        name="Demote Test",
        price_sol=initial_price,
        forensic_report=report,
        bonding_curve_progress_pct=15.0,
        recent_trade_count=10
    )
    assert f"SEAL_SCALPER:{mint}" in engine.positions

    # Trigger re-scan demotion
    dump_trades = engine.handle_token_demoted(
        mint=mint,
        new_risk_score=78.0,
        reason_detail="Micro-cadence wash ladder & dev dump detected on re-scan"
    )

    assert len(dump_trades) >= 1
    dump_trade = dump_trades[0]
    assert dump_trade.action == "EMERGENCY_RUG_EXIT"
    assert "EMERGENCY RUG DUMP" in dump_trade.reason
    assert "78.0/100" in dump_trade.reason

    # Position is fully liquidated and removed
    assert f"SEAL_SCALPER:{mint}" not in engine.positions


def test_block0_creation_rejected():
    """Test that tokens at block 0 or with <8% progress / <8 trades are strictly rejected."""
    engine = PaperTradingEngine(storage=None)
    mint = "Block0Mint1111111111111111111111111111111111"
    report = make_test_report(risk_score=10.0, flags=[])

    # 1. Block-0 token creation event
    trades_created = engine.evaluate_token(
        mint=mint,
        symbol="FRESH",
        name="Freshly Created",
        price_sol=0.00000003,
        forensic_report=report,
        is_token_creation=True
    )
    assert len(trades_created) == 0
    assert len(engine.positions) == 0

    # 2. Token with minimal trades (progress 2%, 2 trades)
    trades_early = engine.evaluate_token(
        mint=mint,
        symbol="FRESH",
        name="Freshly Created",
        price_sol=0.00000003,
        forensic_report=report,
        bonding_curve_progress_pct=2.0,
        recent_trade_count=2,
        is_token_creation=False
    )
    assert len(trades_early) == 0
    assert len(engine.positions) == 0


def test_anti_rug_rejection():
    """Test that scam syndicates and circular wash rings are immediately rejected with 0 trades."""
    engine = PaperTradingEngine(storage=None)
    mint = "ScamMint11111111111111111111111111111111111"

    # High risk score + circular ring
    scam_report = make_test_report(
        risk_score=75.0,
        flags=["CIRCULAR_PING_PONG_RING", "WASH_LADDER_SYNDICATE"]
    )

    trades = engine.evaluate_token(
        mint=mint,
        symbol="SCAM",
        name="Scam Token",
        price_sol=0.00000005,
        forensic_report=scam_report,
        bonding_curve_progress_pct=20.0,
        recent_trade_count=20
    )

    assert len(trades) == 0
    assert len(engine.positions) == 0
    assert engine.cash_balances["SEAL_SCALPER"] == 10.0
    assert engine.cash_balances["OLDWHALE_FREEROLLER"] == 10.0
