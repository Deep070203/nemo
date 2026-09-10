"""Unit and integration tests for Phase 3 market microstructure and content forensics."""

import pytest
import numpy as np
from PIL import Image

from src.microstructure.vpin import VPINCalculator
from src.microstructure.entropy import TradeEntropyDetector
from src.microstructure.phash_matcher import PerceptualHashMatcher
from src.microstructure.social_auditor import SocialAuditor


def test_vpin_toxic_flow():
    calc = VPINCalculator(num_buckets=10, default_bucket_volume_sol=1.0)
    # Heavy toxic selling: 95% sells, 5% buys
    trades = [{"tx_type": "sell", "sol_amount": 0.95}, {"tx_type": "buy", "sol_amount": 0.05}] * 20
    res = calc.compute("mint_toxic", trades)

    assert res.vpin_score >= 0.75
    assert res.toxicity_tier == "TOXIC"
    assert res.risk_level == "CRITICAL"
    assert any("HIGH_ORDER_FLOW_TOXICITY" in f for f in res.flags)


def test_vpin_balanced_flow():
    calc = VPINCalculator(num_buckets=10, default_bucket_volume_sol=1.0)
    # Balanced 50/50 buy/sell order flow
    trades = [{"tx_type": "buy", "sol_amount": 0.5}, {"tx_type": "sell", "sol_amount": 0.5}] * 20
    res = calc.compute("mint_balanced", trades)

    assert res.vpin_score <= 0.20
    assert res.toxicity_tier == "BALANCED"
    assert res.risk_level == "LOW"


def test_entropy_bot_wash_trading():
    detector = TradeEntropyDetector(min_trades_required=10, low_entropy_threshold=2.0)
    # Synthetic bot churn: repeating identical 0.05 SOL trades with alternating signs
    trades = []
    for i in range(30):
        trades.append({
            "tx_type": "buy" if i % 2 == 0 else "sell",
            "sol_amount": 0.05
        })

    res = detector.compute("mint_wash", trades)

    assert res.is_wash_trading is True
    assert res.shannon_entropy < 1.0
    assert res.most_common_size_pct == 100.0
    assert res.sign_autocorrelation < -0.80  # Strict alternating pattern
    assert any("REPETITIVE_ORDER_SIZES" in f for f in res.flags)
    assert any("ALTERNATING_BOT_CHURN" in f for f in res.flags)


def test_entropy_organic_trading():
    detector = TradeEntropyDetector(min_trades_required=10, low_entropy_threshold=2.0)
    # Realistic log-normal distributed varied trade sizes
    np.random.seed(42)
    sizes = np.random.lognormal(mean=0.0, sigma=1.0, size=40)
    trades = [{"tx_type": "buy" if np.random.rand() > 0.4 else "sell", "sol_amount": float(s)} for s in sizes]

    res = detector.compute("mint_organic", trades)

    assert res.is_wash_trading is False
    assert res.shannon_entropy >= 2.5
    assert res.most_common_size_pct < 30.0
    assert res.risk_level == "LOW"


def test_phash_meme_cloning():
    matcher = PerceptualHashMatcher(max_hamming_clone_distance=4)

    # Generate Image 1: Simple gradient
    img1 = Image.new("RGB", (100, 100), color=(128, 128, 128))
    for x in range(100):
        for y in range(100):
            img1.putpixel((x, y), (x * 2, y * 2, 100))

    # Generate Image 2: Clone of Image 1 with minor noise
    img2 = img1.copy()
    img2.putpixel((10, 10), (255, 255, 255))
    img2.putpixel((20, 20), (0, 0, 0))

    # Generate Image 3: Completely different checkerboard
    img3 = Image.new("RGB", (100, 100), color=(0, 0, 0))
    for x in range(0, 100, 10):
        for y in range(0, 100, 10):
            if (x // 10 + y // 10) % 2 == 0:
                for dx in range(10):
                    for dy in range(10):
                        img3.putpixel((x + dx, y + dy), (255, 255, 255))

    hash1 = matcher.compute_phash_from_image(img1)
    hash2 = matcher.compute_phash_from_image(img2)
    hash3 = matcher.compute_phash_from_image(img3)

    # Register token 1
    matcher.match_hash("mint_original", hash1, "Original Doge")

    # Match token 2 (should be detected as clone)
    res2 = matcher.match_hash("mint_clone", hash2, "Fake Doge")
    assert res2.is_clone is True
    assert res2.hamming_distance <= 2
    assert res2.matched_mint == "mint_original"
    assert any("REUSED_MEME_IMAGE_CLONE" in f for f in res2.flags)

    # Match token 3 (distinct image)
    res3 = matcher.match_hash("mint_distinct", hash3, "New Token")
    assert res3.is_clone is False
    assert res3.hamming_distance > 10


def test_social_auditor_dummy_detection():
    auditor = SocialAuditor()
    assert auditor._is_dummy_link("https://x.com") is True
    assert auditor._is_dummy_link("https://twitter.com/") is True
    assert auditor._is_dummy_link("https://t.me") is True
    assert auditor._is_dummy_link("https://x.com/real_project_official") is False


def test_micro_cadence_wash_ladder():
    detector = TradeEntropyDetector()
    # 30 trades where 80% are 0.0003 SOL micro-buys (simulating the syndicate ladder bot)
    trades = []
    for i in range(30):
        trades.append({
            "tx_type": "buy",
            "sol_amount": 0.0003 if i % 5 != 0 else 0.5,
            "user": f"wallet_{i}"
        })

    res = detector.compute("mint_micro_ladder", trades)

    assert res.is_micro_cadence_ladder is True
    assert res.is_wash_trading is True
    assert res.risk_level == "CRITICAL"
    assert res.micro_trade_pct >= 70.0
    assert any("MICRO_CADENCE_WASH_LADDER" in f for f in res.flags)


def test_circular_ping_pong_ring():
    detector = TradeEntropyDetector()
    # 40 trades cycling across only 5 wallets (simulating the BHns... circular ring bot)
    closed_ring = [f"ring_wallet_{w}" for w in range(5)]
    trades = []
    for i in range(40):
        trades.append({
            "tx_type": "buy",
            "sol_amount": 0.005,
            "user": closed_ring[i % len(closed_ring)]
        })

    res = detector.compute("mint_circular_ring", trades)

    assert res.is_circular_ring is True
    assert res.is_wash_trading is True
    assert res.risk_level == "CRITICAL"
    assert res.circular_wallet_ratio >= 5.0
    assert any("CIRCULAR_PING_PONG_RING" in f for f in res.flags)

