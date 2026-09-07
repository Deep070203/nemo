"""Unit tests for the Cohort Auditor, DexScreener batching, and Active Learning."""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from src.ingestion.storage import DuckDBStorage
from src.forensics.cohort_auditor import CohortAuditor, DexScreenerClient
from src.ingestion.models import TokenCreatedEvent


@pytest.fixture
def temp_storage(tmp_path):
    db_file = str(tmp_path / "test_cohort.duckdb")
    storage = DuckDBStorage(db_file)
    yield storage
    storage.close()


def test_storage_cohort_tables_and_methods(temp_storage):
    """Test DuckDB table creation and basic upsert/query functionality."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(hours=12)

    # 1. Insert a token created 12 hours ago
    token = TokenCreatedEvent(
        signature="test_sig_1",
        mint="TokenMint1111111111111111111111111111111111",
        traderPublicKey="Creator111111111111111111111111111111111",
        name="Test Old Token",
        symbol="OLD",
        initialBuy=50_000_000,
        solAmount=2.0,
        created_at=old_time
    )
    # Synchronously execute insert via internal lock or direct SQL
    temp_storage._conn.execute("""
        INSERT INTO tokens (mint, name, symbol, creator, signature, initial_buy, sol_amount, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
    """, [token.mint, token.name, token.symbol, token.traderPublicKey, token.signature, token.initialBuy, token.solAmount, old_time])

    # Check due for T1 audit
    due_tokens = temp_storage.get_tokens_due_for_t1_audit(hours_threshold=10.0)
    assert len(due_tokens) == 1
    assert due_tokens[0]["mint"] == token.mint

    # 2. Upsert audit record
    temp_storage.upsert_token_audit({
        "mint": token.mint,
        "name": token.name,
        "symbol": token.symbol,
        "status": "CONFIRMED_RUG",
        "stage1_audited_at": now,
        "initial_risk_score": 85,
        "initial_risk_tier": "HIGH",
        "current_price_usd": 0.000001,
        "current_mcap_usd": 2800.0,
        "volume_24h": 50.0,
        "price_change_24h": -94.5,
        "audit_notes": "Collapsed bonding curve",
        "created_at": old_time,
        "updated_at": now
    })

    # Token should no longer be due for T1 audit
    assert len(temp_storage.get_tokens_due_for_t1_audit(hours_threshold=10.0)) == 0

    # Query rugs bucket
    rugs = temp_storage.get_audit_bucket("rugs")
    assert len(rugs) == 1
    assert rugs[0]["status"] == "CONFIRMED_RUG"
    assert rugs[0]["current_mcap_usd"] == 2800.0

    # 3. Test Human Verdict update
    temp_storage.record_human_audit(
        mint=token.mint,
        verdict="SLOW_RUG",
        notes="Dev slowly liquidated via multiple sub-wallets"
    )

    updated = temp_storage.get_audit_bucket("rugs")[0]
    assert updated["human_verdict"] == "SLOW_RUG"
    assert "Dev slowly liquidated" in updated["human_notes"]

    # 4. Summary stats
    stats = temp_storage.get_cohort_summary_stats()
    assert stats["total_audits"] == 1
    assert stats["confirmed_rugs"] == 1
    assert stats["human_reviewed_count"] == 1


def test_cohort_auditor_batch_t1_logic(temp_storage):
    """Test stage 1 classification into CONFIRMED_RUG vs SURVIVING_CANDIDATE."""
    async def _run():
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=14)

        # Insert 2 tokens: one that dies, one that thrives
        temp_storage._conn.execute("""
            INSERT INTO tokens (mint, name, symbol, creator, signature, initial_buy, sol_amount, created_at)
            VALUES 
            ('DeadMint1111111111111111111111111111111111', 'Dead Token', 'DEAD', 'dev1', 'sig1', 1000, 1.0, $1),
            ('AliveMint111111111111111111111111111111111', 'Alive Token', 'ALIVE', 'dev2', 'sig2', 1000, 1.0, $1);
        """, [old_time])

        auditor = CohortAuditor(temp_storage)

        # Mock DexScreener response
        mock_market_data = {
            "DeadMint1111111111111111111111111111111111": {
                "mint": "DeadMint1111111111111111111111111111111111",
                "name": "Dead Token",
                "symbol": "DEAD",
                "price_usd": 0.000002,
                "market_cap_usd": 2500.0,
                "volume_24h": 45.0,
                "price_change_24h": -95.0,
                "liquidity_usd": 100.0,
                "dex_id": "pumpfun",
                "is_graduated": False
            },
            "AliveMint111111111111111111111111111111111": {
                "mint": "AliveMint111111111111111111111111111111111",
                "name": "Alive Token",
                "symbol": "ALIVE",
                "price_usd": 0.00015,
                "market_cap_usd": 150_000.0,
                "volume_24h": 85_000.0,
                "price_change_24h": 120.0,
                "liquidity_usd": 35_000.0,
                "dex_id": "raydium",
                "is_graduated": True
            }
        }

        with patch.object(auditor.dex_client, "fetch_tokens_batch", new=AsyncMock(return_value=mock_market_data)):
            res = await auditor.audit_batch_t1(hours_threshold=10.0)

        assert res["processed"] == 2
        assert res["new_rugs"] == 1
        assert res["new_survivors"] == 1

        rugs = temp_storage.get_audit_bucket("rugs")
        survivors = temp_storage.get_audit_bucket("survivors")

        assert len(rugs) == 1
        assert rugs[0]["symbol"] == "DEAD"
        assert rugs[0]["status"] == "CONFIRMED_RUG"

        assert len(survivors) == 1
        assert survivors[0]["symbol"] == "ALIVE"
        assert survivors[0]["status"] == "SURVIVING_CANDIDATE"
        assert survivors[0]["is_graduated"] is True

    asyncio.run(_run())


def test_cohort_resurrection_cto_detection(temp_storage):
    """Test price refresh detecting when a dead token resurrects into a CTO."""
    async def _run():
        now = datetime.now(timezone.utc)
        mint = "ZombieMint11111111111111111111111111111111"

        # Insert a confirmed rug
        temp_storage.upsert_token_audit({
            "mint": mint,
            "name": "Zombie Coin",
            "symbol": "ZOMBIE",
            "status": "CONFIRMED_RUG",
            "initial_risk_score": 90,
            "initial_risk_tier": "CRITICAL",
            "current_price_usd": 0.000001,
            "current_mcap_usd": 2000.0,
            "volume_24h": 10.0,
            "price_change_24h": -98.0,
            "created_at": now - timedelta(days=2),
            "updated_at": now - timedelta(days=2)
        })

        auditor = CohortAuditor(temp_storage)

        # Mock DexScreener showing massive spike in mcap & vol
        mock_refresh_data = {
            mint: {
                "mint": mint,
                "name": "Zombie Coin",
                "symbol": "ZOMBIE",
                "price_usd": 0.00008,
                "market_cap_usd": 80_000.0,
                "volume_24h": 25_000.0,
                "price_change_24h": 3900.0,
                "liquidity_usd": 15_000.0,
                "dex_id": "raydium",
                "is_graduated": True
            }
        }

        with patch.object(auditor.dex_client, "fetch_tokens_batch", new=AsyncMock(return_value=mock_refresh_data)):
            res = await auditor.refresh_bucket_prices(bucket="rugs")

        assert res["updated"] == 1
        assert res["revived_count"] == 1
        assert res["revived_tokens"][0]["symbol"] == "ZOMBIE"

        # Token should now be classified as CTO
        survivors = temp_storage.get_audit_bucket("survivors")
        assert any(s["mint"] == mint and s["status"] == "CTO" for s in survivors)

    asyncio.run(_run())
