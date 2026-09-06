"""Unit and integration tests for Phase 2 on-chain forensics modules."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.forensics.concentration import ConcentrationCalculator
from src.forensics.bundle_detector import Block0BundleDetector, Block0BundleAnalysis, JITO_TIP_ACCOUNTS
from src.forensics.wallet_graph import WalletGraphForensics
from src.forensics.engine import ForensicsEngine
from src.config import Settings


def test_concentration_monopoly():
    calc = ConcentrationCalculator(total_supply=1_000_000_000.0)
    # One whale holds 800M, and 100 small wallets hold 1M each
    balances = [800_000_000.0] + [1_000_000.0] * 100
    metrics = calc.compute("mint_monopoly", balances)

    assert metrics.distribution_tier == "MONOPOLY"
    assert metrics.risk_level == "CRITICAL"
    assert metrics.hhi_score > 2500.0
    assert metrics.gini_coefficient > 0.8
    assert any("EXTREME_SUPPLY_CONCENTRATION" in f for f in metrics.flags)


def test_concentration_decentralized():
    calc = ConcentrationCalculator(total_supply=1_000_000_000.0)
    # 200 wallets holding 0.5% each
    balances = [5_000_000.0] * 200
    metrics = calc.compute("mint_decentralized", balances)

    assert metrics.distribution_tier == "DECENTRALIZED"
    assert metrics.risk_level == "LOW"
    assert metrics.hhi_score < 800.0
    assert metrics.gini_coefficient < 0.2


def test_block0_jito_tip_detection():
    mock_rpc = MagicMock()
    detector = Block0BundleDetector(mock_rpc)

    mint_addr = "TestMint1111111111111111111111111111111111111"
    tip_acc = list(JITO_TIP_ACCOUNTS)[0]
    # Simulated Solana block transaction with a Jito tip and mint in accountKeys
    tx = {
        "meta": {
            "err": None,
            "preBalances": [10_000_000_000, 0, 0],
            "postBalances": [9_950_000_000, 50_000_000, 0],  # 50,000,000 lamports tipped
            "preTokenBalances": [],
            "postTokenBalances": [
                {
                    "mint": mint_addr,
                    "owner": "BuyerWallet1111111111111111111111111111111111",
                    "uiTokenAmount": {"uiAmount": 200_000_000.0}
                }
            ]
        },
        "transaction": {
            "signatures": ["sig123"],
            "message": {
                "accountKeys": [
                    {"pubkey": "BuyerWallet1111111111111111111111111111111111"},
                    {"pubkey": tip_acc},
                    {"pubkey": mint_addr}
                ]
            }
        }
    }

    parsed = detector._parse_block_transaction(tx, mint_addr)
    assert parsed is not None
    assert parsed.jito_tip_lamports == 50_000_000
    assert parsed.jito_tip_account == tip_acc
    assert parsed.tokens_bought == 200_000_000.0


def test_wallet_graph_sybil_clustering():
    mock_rpc = MagicMock()
    forensics = WalletGraphForensics(mock_rpc)

    # Mock finding funding parents: 4 wallets funded by root wallet 'CabalBoss111'
    async def mock_find_funder(w):
        if w in ["Sniper1", "Sniper2", "Sniper3", "Sniper4"]:
            return "CabalBoss111"
        return "IndependentWallet999"

    forensics._find_funding_parent = mock_find_funder

    wallet_balances = {
        "Sniper1": 80_000_000.0,
        "Sniper2": 80_000_000.0,
        "Sniper3": 70_000_000.0,
        "Sniper4": 70_000_000.0,
        "IndependentWallet999": 20_000_000.0,
    }

    loop = asyncio.new_event_loop()
    analysis = loop.run_until_complete(forensics.analyze_wallet_cohort("TestMint", wallet_balances))
    loop.close()

    assert analysis.total_analyzed_wallets == 5
    assert analysis.largest_cluster_size == 4
    # Total tokens in Cabal cluster: 300,000,000 / 1,000,000,000 = 30%
    assert analysis.largest_cluster_share_pct == 30.0
    assert analysis.risk_level == "CRITICAL"
    assert any("SYBIL_CABAL_SUPPLY_DOMINANCE" in f for f in analysis.flags)


def test_forensics_engine_audit():
    mock_rpc = MagicMock()
    mock_rpc.get_signatures_for_address = AsyncMock(return_value=[])
    mock_rpc.get_transaction = AsyncMock(return_value=None)

    settings = Settings()
    engine = ForensicsEngine(settings, mock_rpc)

    # Mock bundle analysis response
    mock_bundle_analysis = Block0BundleAnalysis(
        mint="TestMint123",
        slot=123456,
        flags=["JITO_MEV_TIP_DETECTED (0.050 SOL)", "BLOCK0_SUPPLY_CORNERED (25.0%)"],
        is_jito_bundled=True,
        block0_supply_pct=25.0,
        unique_block0_buyers=4,
        risk_level="CRITICAL"
    )
    engine.bundle_detector.analyze_token_launch = AsyncMock(return_value=mock_bundle_analysis)

    loop = asyncio.new_event_loop()
    report = loop.run_until_complete(
        engine.audit_token(
            mint="TestMint123",
            creation_signature="sigCreate123",
            holder_balances={"walletA": 600_000_000.0, "walletB": 200_000_000.0}
        )
    )
    loop.close()

    assert report.composite_risk_score >= 75
    assert report.risk_tier == "CRITICAL"
    assert len(report.all_flags) >= 2
    print("\nForensic Engine Audit passed with flags:", report.all_flags)
