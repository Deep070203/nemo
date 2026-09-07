"""Cohort-based delayed batch audit and active learning engine for Nemo.

Performs:
1. Stage 1 delayed audit (6–12h) to route tokens into Confirmed Rugs vs Surviving Candidates.
2. Batch price & volume refreshes via DexScreener API (30 tokens/request).
3. Stage 2 re-audit (3–10d) tracking slow rugs, Raydium migrations, and CTO takeovers.
4. Model learning metrics: Confusion matrix, Precision/Recall, and rule attribution.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import aiohttp

from src.ingestion.storage import DuckDBStorage
from src.ingestion.rpc_client import SolanaRPCClient

logger = logging.getLogger("nemo.cohort_auditor")


class DexScreenerClient:
    """Async multi-token market data retriever using DexScreener's public API."""

    BASE_URL = "https://api.dexscreener.com/tokens/v1/solana"

    def __init__(self, timeout_seconds: float = 12.0):
        self.timeout_seconds = timeout_seconds

    async def fetch_tokens_batch(self, mints: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch market stats for up to 30 Solana mint addresses in a single HTTP request."""
        if not mints:
            return {}

        # DexScreener max 30 addresses per comma-separated URL
        results: Dict[str, Dict[str, Any]] = {}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        chunk_size = 30
        for i in range(0, len(mints), chunk_size):
            chunk = mints[i : i + chunk_size]
            url = f"{self.BASE_URL}/{','.join(chunk)}"

            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            pairs = await response.json()
                            if isinstance(pairs, list):
                                for p in pairs:
                                    base_addr = p.get("baseToken", {}).get("address")
                                    if not base_addr:
                                        continue

                                    # Prioritize Raydium pair if multiple exist
                                    dex_id = p.get("dexId", "").lower()
                                    is_raydium = "raydium" in dex_id or "orca" in dex_id
                                    price_usd = float(p.get("priceUsd") or 0.0)
                                    mcap = float(p.get("marketCap") or p.get("fdv") or 0.0)
                                    vol24 = float(p.get("volume", {}).get("h24") or 0.0)
                                    change24 = float(p.get("priceChange", {}).get("h24") or 0.0)
                                    liq_usd = float(p.get("liquidity", {}).get("usd") or 0.0)

                                    current = results.get(base_addr)
                                    # If not in results, or if this pair is Raydium / has higher volume, store it
                                    if not current or is_raydium or vol24 > current.get("volume_24h", 0):
                                        results[base_addr] = {
                                            "mint": base_addr,
                                            "name": p.get("baseToken", {}).get("name", ""),
                                            "symbol": p.get("baseToken", {}).get("symbol", ""),
                                            "price_usd": price_usd,
                                            "market_cap_usd": mcap,
                                            "volume_24h": vol24,
                                            "price_change_24h": change24,
                                            "liquidity_usd": liq_usd,
                                            "dex_id": dex_id,
                                            "is_graduated": is_raydium,
                                            "pair_address": p.get("pairAddress", "")
                                        }
                        else:
                            logger.warning(f"DexScreener HTTP {response.status} for chunk of {len(chunk)} mints")
            except Exception as e:
                logger.warning(f"DexScreener fetch error for batch: {e}")

            # Modest delay between chunks to be courteous
            if i + chunk_size < len(mints):
                await asyncio.sleep(0.2)

        return results


class CohortAuditor:
    """Orchestrates multi-stage delayed auditing and active learning."""

    def __init__(self, storage: DuckDBStorage, rpc_client: Optional[SolanaRPCClient] = None):
        self.storage = storage
        self.rpc_client = rpc_client
        self.dex_client = DexScreenerClient()

    async def audit_batch_t1(self, hours_threshold: float = 10.0, limit: int = 60) -> Dict[str, Any]:
        """Audit tokens that reached the Stage 1 age threshold (e.g. 10 hours)."""
        pending_tokens = self.storage.get_tokens_due_for_t1_audit(hours_threshold=hours_threshold, limit=limit)
        if not pending_tokens:
            return {
                "processed": 0,
                "new_rugs": 0,
                "new_survivors": 0,
                "message": f"No tokens older than {hours_threshold}h pending Stage 1 audit."
            }

        mints = [t["mint"] for t in pending_tokens]
        market_data = await self.dex_client.fetch_tokens_batch(mints)

        new_rugs = 0
        new_survivors = 0
        now = datetime.now(timezone.utc)

        for t in pending_tokens:
            mint = t["mint"]
            m_info = market_data.get(mint)

            # Query any initial forensic risk scores in DuckDB
            flag_rows = self.storage._conn.execute("""
                SELECT severity, flag_type FROM forensic_flags WHERE mint = $1;
            """, [mint]).fetchall()

            has_critical = any(r[0] == "CRITICAL" for r in flag_rows)
            has_high = any(r[0] == "HIGH" for r in flag_rows)
            initial_tier = "CRITICAL" if has_critical else ("HIGH" if has_high else "LOW")
            initial_score = 90 if has_critical else (70 if has_high else 25)

            # Decision Logic for Stage 1 (10 Hours)
            status = "CONFIRMED_RUG"
            audit_notes = []
            current_price = 0.0
            current_mcap = 0.0
            vol24 = 0.0
            change24 = 0.0
            is_graduated = False

            if m_info:
                current_price = m_info["price_usd"]
                current_mcap = m_info["market_cap_usd"]
                vol24 = m_info["volume_24h"]
                change24 = m_info["price_change_24h"]
                is_graduated = m_info["is_graduated"]

                if is_graduated:
                    status = "SURVIVING_CANDIDATE"
                    audit_notes.append("Graduated to Raydium AMM")
                elif current_mcap >= 15000.0 or vol24 >= 500.0:
                    status = "SURVIVING_CANDIDATE"
                    audit_notes.append(f"Active market: Mcap ${current_mcap:,.0f}, Vol ${vol24:,.0f}")
                elif change24 <= -92.0 or current_mcap < 3500.0:
                    status = "CONFIRMED_RUG"
                    audit_notes.append(f"Severe collapse: Mcap ${current_mcap:,.0f}, 24h change {change24:.1f}%")
                else:
                    # Borderline: check trading activity
                    if vol24 < 150.0:
                        status = "CONFIRMED_RUG"
                        audit_notes.append("Abandoned curve (24h volume < $150)")
                    else:
                        status = "SURVIVING_CANDIDATE"
                        audit_notes.append("Low volume but maintains price floor")
            else:
                # No pairs indexed on DexScreener after 10 hours: almost certainly abandoned or dead curve
                status = "CONFIRMED_RUG"
                audit_notes.append("No active pool or liquidity found on DexScreener after 10h")

            if status == "CONFIRMED_RUG":
                new_rugs += 1
            else:
                new_survivors += 1

            audit_record = {
                "mint": mint,
                "name": t.get("name") or (m_info.get("name") if m_info else ""),
                "symbol": t.get("symbol") or (m_info.get("symbol") if m_info else ""),
                "status": status,
                "stage1_audited_at": now,
                "stage2_audited_at": None,
                "initial_risk_score": initial_score,
                "initial_risk_tier": initial_tier,
                "current_price_usd": current_price,
                "current_mcap_usd": current_mcap,
                "peak_mcap_usd": current_mcap,
                "volume_24h": vol24,
                "price_change_24h": change24,
                "is_graduated": is_graduated,
                "dev_balance_pct": 0.0,
                "audit_notes": "; ".join(audit_notes),
                "human_verdict": None,
                "human_notes": None,
                "created_at": t.get("created_at") or now,
                "updated_at": now
            }

            self.storage.upsert_token_audit(audit_record)

        return {
            "processed": len(pending_tokens),
            "new_rugs": new_rugs,
            "new_survivors": new_survivors,
            "timestamp": now.isoformat()
        }

    async def refresh_bucket_prices(self, bucket: str = "rugs", limit: int = 60) -> Dict[str, Any]:
        """Batch reload current prices & volume for all tokens in a bucket."""
        records = self.storage.get_audit_bucket(bucket=bucket, limit=limit)
        if not records:
            return {"updated": 0, "revived_count": 0, "revived_tokens": [], "message": f"Bucket '{bucket}' is empty."}

        mints = [r["mint"] for r in records]
        market_data = await self.dex_client.fetch_tokens_batch(mints)

        now = datetime.now(timezone.utc)
        updated = 0
        revived_tokens = []

        for r in records:
            mint = r["mint"]
            m_info = market_data.get(mint)
            if not m_info:
                continue

            current_mcap = m_info["market_cap_usd"]
            vol24 = m_info["volume_24h"]
            price_usd = m_info["price_usd"]
            change24 = m_info["price_change_24h"]
            is_graduated = m_info["is_graduated"]

            # Resurrection / CTO Detection on "dead" rugs
            was_rug = r["status"] in ("CONFIRMED_RUG", "SLOW_RUG")
            is_revived = was_rug and (current_mcap >= 30000.0 or vol24 >= 8000.0 or is_graduated)

            new_status = r["status"]
            notes = r.get("audit_notes") or ""

            if is_revived:
                new_status = "CTO"
                revived_tokens.append({
                    "mint": mint,
                    "symbol": r.get("symbol"),
                    "mcap": current_mcap,
                    "volume_24h": vol24
                })
                notes += f" | ⚡ REVIVED / CTO DETECTED: Mcap surged to ${current_mcap:,.0f} with ${vol24:,.0f} volume!"

            r["current_price_usd"] = price_usd
            r["current_mcap_usd"] = current_mcap
            r["volume_24h"] = vol24
            r["price_change_24h"] = change24
            r["is_graduated"] = is_graduated
            r["status"] = new_status
            r["audit_notes"] = notes
            r["updated_at"] = now
            if current_mcap > (r.get("peak_mcap_usd") or 0.0):
                r["peak_mcap_usd"] = current_mcap

            self.storage.upsert_token_audit(r)
            updated += 1

        return {
            "updated": updated,
            "revived_count": len(revived_tokens),
            "revived_tokens": revived_tokens,
            "timestamp": now.isoformat()
        }

    def get_detailed_learning_metrics(self) -> Dict[str, Any]:
        """Compute advanced confusion matrix, false alarm breakdown, and rule precision."""
        summary = self.storage.get_cohort_summary_stats()

        # Query all audited tokens with their forensic flags to calculate individual rule accuracy
        try:
            flag_stats_df = self.storage._conn.execute("""
                SELECT 
                    f.flag_type,
                    a.status,
                    COUNT(*) as occurrences
                FROM forensic_flags f
                JOIN token_audits a ON f.mint = a.mint
                WHERE a.status != 'NEW'
                GROUP BY f.flag_type, a.status;
            """).df()

            rule_attribution = []
            if not flag_stats_df.empty:
                grouped = flag_stats_df.groupby("flag_type")
                for flag, grp in grouped:
                    rug_count = grp[grp["status"].isin(["CONFIRMED_RUG", "SLOW_RUG"])]["occurrences"].sum()
                    survivor_count = grp[grp["status"].isin(["SURVIVING_CANDIDATE", "GRADUATED", "CTO"])]["occurrences"].sum()
                    total = rug_count + survivor_count
                    precision = float(round((rug_count / total) * 100.0, 1)) if total > 0 else 0.0

                    rule_attribution.append({
                        "flag": flag,
                        "total_triggers": int(total),
                        "actual_rugs": int(rug_count),
                        "false_alarms": int(survivor_count),
                        "precision_pct": precision
                    })

                rule_attribution.sort(key=lambda x: x["precision_pct"], reverse=True)

            summary["rule_attribution"] = rule_attribution

            # Query recent False Positives (predicted rug, but survived - potential CTOs)
            fp_rows = self.storage._conn.execute("""
                SELECT mint, symbol, current_mcap_usd, volume_24h, audit_notes, human_notes
                FROM token_audits
                WHERE initial_risk_tier IN ('HIGH', 'CRITICAL')
                  AND status IN ('SURVIVING_CANDIDATE', 'GRADUATED', 'CTO')
                ORDER BY current_mcap_usd DESC
                LIMIT 10;
            """).df().to_dict(orient="records")
            summary["top_false_positives"] = fp_rows

            # Query recent False Negatives (predicted clean, but rugged - missed soft rugs)
            fn_rows = self.storage._conn.execute("""
                SELECT mint, symbol, current_mcap_usd, volume_24h, audit_notes, human_notes
                FROM token_audits
                WHERE initial_risk_tier = 'LOW'
                  AND status IN ('CONFIRMED_RUG', 'SLOW_RUG')
                ORDER BY updated_at DESC
                LIMIT 10;
            """).df().to_dict(orient="records")
            summary["top_false_negatives"] = fn_rows

        except Exception as e:
            logger.debug(f"Learning metrics calculation error: {e}")
            summary["rule_attribution"] = []
            summary["top_false_positives"] = []
            summary["top_false_negatives"] = []

        return summary
