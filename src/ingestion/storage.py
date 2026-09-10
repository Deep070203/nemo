"""Persistent storage manager powered by DuckDB."""

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any
import duckdb
from src.ingestion.models import TokenCreatedEvent, TokenTradeEvent


class DuckDBStorage:
    """High-throughput relational storage engine for Pump.fun research events."""

    def __init__(self, db_path: str = "data/nemo_research.duckdb", batch_size: int = 50, flush_interval: float = 5.0, read_only: bool = False):
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.read_only = read_only
        self._is_snapshot = False

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        try:
            self._conn = duckdb.connect(self.db_path, read_only=read_only)
            if not read_only:
                self._init_schema()
        except duckdb.IOException as e:
            if read_only and "Conflicting lock" in str(e):
                import shutil, time
                snapshot_path = f"/tmp/nemo_snapshot_{int(time.time())}.duckdb"
                shutil.copy2(self.db_path, snapshot_path)
                wal_path = f"{self.db_path}.wal"
                if os.path.exists(wal_path):
                    shutil.copy2(wal_path, f"{snapshot_path}.wal")
                self._conn = duckdb.connect(snapshot_path, read_only=True)
                self._is_snapshot = True
                self._snapshot_path = snapshot_path
            else:
                raise

        # Buffers for asynchronous batch insertion
        self._token_buffer: List[Dict[str, Any]] = []
        self._trade_buffer: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._is_running = False

    def _init_schema(self) -> None:
        """Initialize DuckDB tables with indexes for fast analytical slicing."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                mint VARCHAR PRIMARY KEY,
                name VARCHAR,
                symbol VARCHAR,
                uri VARCHAR,
                creator VARCHAR,
                signature VARCHAR,
                initial_buy DOUBLE,
                sol_amount DOUBLE,
                bonding_curve_key VARCHAR,
                v_tokens DOUBLE,
                v_sol DOUBLE,
                market_cap_sol DOUBLE,
                created_at TIMESTAMP
            );
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                signature VARCHAR PRIMARY KEY,
                mint VARCHAR,
                trader VARCHAR,
                tx_type VARCHAR,
                token_amount DOUBLE,
                sol_amount DOUBLE,
                v_tokens DOUBLE,
                v_sol DOUBLE,
                market_cap_sol DOUBLE,
                price_sol DOUBLE,
                slot BIGINT,
                timestamp TIMESTAMP
            );
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_mint ON trades(mint);
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_trader ON trades(trader);
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS forensic_flags (
                id VARCHAR PRIMARY KEY,
                mint VARCHAR,
                flag_type VARCHAR,
                severity VARCHAR,
                details VARCHAR,
                created_at TIMESTAMP
            );
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS token_audits (
                mint VARCHAR PRIMARY KEY,
                name VARCHAR,
                symbol VARCHAR,
                status VARCHAR DEFAULT 'NEW',
                stage1_audited_at TIMESTAMP,
                stage2_audited_at TIMESTAMP,
                initial_risk_score INTEGER DEFAULT 0,
                initial_risk_tier VARCHAR DEFAULT 'LOW',
                current_price_usd DOUBLE DEFAULT 0.0,
                current_mcap_usd DOUBLE DEFAULT 0.0,
                peak_mcap_usd DOUBLE DEFAULT 0.0,
                volume_24h DOUBLE DEFAULT 0.0,
                price_change_24h DOUBLE DEFAULT 0.0,
                is_graduated BOOLEAN DEFAULT FALSE,
                dev_balance_pct DOUBLE DEFAULT 0.0,
                audit_notes VARCHAR,
                human_verdict VARCHAR,
                human_notes VARCHAR,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            );
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audits_status ON token_audits(status);
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audits_created ON token_audits(created_at);
        """)

    async def start(self) -> None:
        """Start the periodic flush worker."""
        if not self._is_running:
            self._is_running = True
            self._flush_task = asyncio.create_task(self._periodic_flush())

    async def stop(self) -> None:
        """Stop worker and flush remaining buffered records."""
        self._is_running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()

    async def insert_token(self, token: TokenCreatedEvent) -> None:
        """Queue a newly created token for persistence."""
        record = {
            "mint": token.mint,
            "name": token.name,
            "symbol": token.symbol,
            "uri": token.uri,
            "creator": token.traderPublicKey,
            "signature": token.signature,
            "initial_buy": token.initialBuy,
            "sol_amount": token.solAmount,
            "bonding_curve_key": token.bondingCurveKey,
            "v_tokens": token.vTokensInBondingCurve,
            "v_sol": token.vSolInBondingCurve,
            "market_cap_sol": token.marketCapSol,
            "created_at": token.created_at,
        }
        async with self._lock:
            self._token_buffer.append(record)
            if len(self._token_buffer) >= self.batch_size:
                await self._flush_tokens_locked()

    async def insert_trade(self, trade: TokenTradeEvent) -> None:
        """Queue a trade event for persistence."""
        record = {
            "signature": trade.signature,
            "mint": trade.mint,
            "trader": trade.traderPublicKey,
            "tx_type": trade.txType,
            "token_amount": trade.tokenAmount,
            "sol_amount": trade.solAmount or 0.0,
            "v_tokens": trade.vTokensInBondingCurve,
            "v_sol": trade.vSolInBondingCurve,
            "market_cap_sol": trade.marketCapSol,
            "price_sol": trade.price_sol,
            "slot": trade.slot,
            "timestamp": trade.timestamp,
        }
        async with self._lock:
            self._trade_buffer.append(record)
            if len(self._trade_buffer) >= self.batch_size:
                await self._flush_trades_locked()

    async def _flush_tokens_locked(self) -> None:
        if not self._token_buffer:
            return
        tokens_to_insert = self._token_buffer[:]
        self._token_buffer.clear()

        # Batch insert using parameterized VALUES or executemany
        self._conn.executemany("""
            INSERT OR REPLACE INTO tokens VALUES (
                $mint, $name, $symbol, $uri, $creator, $signature,
                $initial_buy, $sol_amount, $bonding_curve_key,
                $v_tokens, $v_sol, $market_cap_sol, $created_at
            )
        """, tokens_to_insert)

    async def _flush_trades_locked(self) -> None:
        if not self._trade_buffer:
            return
        trades_to_insert = self._trade_buffer[:]
        self._trade_buffer.clear()

        self._conn.executemany("""
            INSERT OR REPLACE INTO trades VALUES (
                $signature, $mint, $trader, $tx_type, $token_amount,
                $sol_amount, $v_tokens, $v_sol, $market_cap_sol,
                $price_sol, $slot, $timestamp
            )
        """, trades_to_insert)

    async def flush(self) -> None:
        """Flush all pending records."""
        async with self._lock:
            await self._flush_tokens_locked()
            await self._flush_trades_locked()

    async def _periodic_flush(self) -> None:
        while self._is_running:
            await asyncio.sleep(self.flush_interval)
            await self.flush()

    def get_token_count(self) -> int:
        """Total tokens recorded."""
        res = self._conn.execute("SELECT COUNT(*) FROM tokens;").fetchone()
        return res[0] if res else 0

    def get_trade_count(self) -> int:
        """Total trades recorded."""
        res = self._conn.execute("SELECT COUNT(*) FROM trades;").fetchone()
        return res[0] if res else 0

    def get_recent_tokens(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch the most recently created tokens."""
        df = self._conn.execute(f"""
            SELECT * FROM tokens ORDER BY created_at DESC LIMIT {limit};
        """).df()
        return df.to_dict(orient="records")

    def export_to_parquet(self, output_dir: str = "data/exports") -> None:
        """Export DuckDB tables to Parquet files for research & ML modeling."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        self._conn.execute(f"COPY tokens TO '{out_path / 'tokens.parquet'}' (FORMAT PARQUET);")
        self._conn.execute(f"COPY trades TO '{out_path / 'trades.parquet'}' (FORMAT PARQUET);")

    def upsert_token_audit(self, audit: Dict[str, Any]) -> None:
        """Upsert a token audit outcome record into DuckDB."""
        mint = audit.get("mint")
        if not mint:
            return

        cols = [
            "mint", "name", "symbol", "status", "stage1_audited_at", "stage2_audited_at",
            "initial_risk_score", "initial_risk_tier", "current_price_usd", "current_mcap_usd",
            "peak_mcap_usd", "volume_24h", "price_change_24h", "is_graduated", "dev_balance_pct",
            "audit_notes", "human_verdict", "human_notes", "created_at", "updated_at"
        ]

        # Ensure defaults
        row = {c: audit.get(c) for c in cols}
        row["status"] = row["status"] or "NEW"
        row["initial_risk_score"] = int(row["initial_risk_score"] or 0)
        row["initial_risk_tier"] = row["initial_risk_tier"] or "LOW"
        row["current_price_usd"] = float(row["current_price_usd"] or 0.0)
        row["current_mcap_usd"] = float(row["current_mcap_usd"] or 0.0)
        row["peak_mcap_usd"] = float(row["peak_mcap_usd"] or 0.0)
        row["volume_24h"] = float(row["volume_24h"] or 0.0)
        row["price_change_24h"] = float(row["price_change_24h"] or 0.0)
        row["is_graduated"] = bool(row["is_graduated"] or False)
        row["dev_balance_pct"] = float(row["dev_balance_pct"] or 0.0)

        self._conn.execute("""
            INSERT OR REPLACE INTO token_audits (
                mint, name, symbol, status, stage1_audited_at, stage2_audited_at,
                initial_risk_score, initial_risk_tier, current_price_usd, current_mcap_usd,
                peak_mcap_usd, volume_24h, price_change_24h, is_graduated, dev_balance_pct,
                audit_notes, human_verdict, human_notes, created_at, updated_at
            ) VALUES (
                $mint, $name, $symbol, $status, $stage1_audited_at, $stage2_audited_at,
                $initial_risk_score, $initial_risk_tier, $current_price_usd, $current_mcap_usd,
                $peak_mcap_usd, $volume_24h, $price_change_24h, $is_graduated, $dev_balance_pct,
                $audit_notes, $human_verdict, $human_notes, $created_at, $updated_at
            );
        """, row)

    def get_tokens_due_for_t1_audit(self, hours_threshold: float = 10.0, limit: int = 60) -> List[Dict[str, Any]]:
        """Find tokens created >= hours_threshold ago that need stage 1 rug audit."""
        # DuckDB query joining tokens and token_audits
        query = f"""
            SELECT 
                t.mint, t.name, t.symbol, t.creator, t.initial_buy, t.sol_amount, 
                t.created_at,
                COALESCE(a.status, 'NEW') AS audit_status,
                a.stage1_audited_at
            FROM tokens t
            LEFT JOIN token_audits a ON t.mint = a.mint
            WHERE (a.status IS NULL OR a.status = 'NEW' OR a.stage1_audited_at IS NULL)
              AND t.created_at <= (CURRENT_TIMESTAMP - INTERVAL '{hours_threshold} hours')
            ORDER BY t.created_at ASC
            LIMIT {limit};
        """
        try:
            df = self._conn.execute(query).df()
            return df.to_dict(orient="records")
        except Exception:
            return []

    def get_tokens_due_for_t2_audit(self, days_threshold: float = 3.0, limit: int = 60) -> List[Dict[str, Any]]:
        """Find tokens surviving stage 1 that are >= days_threshold old for long-term re-audit."""
        query = f"""
            SELECT 
                a.*,
                t.creator,
                t.initial_buy
            FROM token_audits a
            JOIN tokens t ON a.mint = t.mint
            WHERE a.status = 'SURVIVING_CANDIDATE'
              AND a.stage2_audited_at IS NULL
              AND a.created_at <= (CURRENT_TIMESTAMP - INTERVAL '{days_threshold} days')
            ORDER BY a.created_at ASC
            LIMIT {limit};
        """
        try:
            df = self._conn.execute(query).df()
            return df.to_dict(orient="records")
        except Exception:
            return []

    def get_audit_bucket(self, bucket: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Fetch tokens classified into specific research buckets."""
        bucket = bucket.lower()
        where_clause = "1=1"
        if bucket == "rugs":
            where_clause = "status IN ('CONFIRMED_RUG', 'SLOW_RUG')"
        elif bucket == "survivors":
            where_clause = "status IN ('SURVIVING_CANDIDATE', 'GRADUATED', 'CTO')"
        elif bucket == "reaudit":
            where_clause = "status = 'SURVIVING_CANDIDATE' AND (human_verdict IS NULL OR human_verdict = '')"
        elif bucket == "all":
            where_clause = "1=1"
        else:
            where_clause = f"status = '{bucket.upper()}'"

        query = f"""
            SELECT * FROM token_audits
            WHERE {where_clause}
            ORDER BY updated_at DESC NULLS LAST, created_at DESC
            LIMIT {limit} OFFSET {offset};
        """
        try:
            df = self._conn.execute(query).df()
            return df.to_dict(orient="records")
        except Exception:
            return []

    def record_human_audit(self, mint: str, verdict: str, notes: str = "") -> bool:
        """Record manual verification/feedback for active learning."""
        now = datetime.now(timezone.utc)
        status_map = {
            "CONFIRMED_RUG": "CONFIRMED_RUG",
            "SLOW_RUG": "SLOW_RUG",
            "GRADUATED": "GRADUATED",
            "CTO": "CTO",
            "SURVIVOR": "SURVIVING_CANDIDATE"
        }
        new_status = status_map.get(verdict.upper(), verdict.upper())

        res = self._conn.execute("""
            UPDATE token_audits
            SET human_verdict = $1,
                human_notes = $2,
                status = $3,
                stage2_audited_at = COALESCE(stage2_audited_at, $4),
                updated_at = $4
            WHERE mint = $5;
        """, [verdict.upper(), notes, new_status, now, mint])
        return True

    def get_cohort_summary_stats(self) -> Dict[str, Any]:
        """Aggregate counts and accuracy metrics for the cohort dashboard."""
        try:
            counts = self._conn.execute("""
                SELECT status, COUNT(*) FROM token_audits GROUP BY status;
            """).fetchall()
            status_counts = {row[0]: row[1] for row in counts}

            # Human audited count
            human_reviewed = self._conn.execute("""
                SELECT COUNT(*) FROM token_audits WHERE human_verdict IS NOT NULL AND human_verdict != '';
            """).fetchone()[0]

            # Tokens eligible for T1 audit
            due_t1 = len(self.get_tokens_due_for_t1_audit(hours_threshold=10.0, limit=500))
            # Tokens eligible for T2 audit
            due_t2 = len(self.get_tokens_due_for_t2_audit(days_threshold=3.0, limit=500))

            total_audited = sum(status_counts.values())

            # Calculate preliminary precision/accuracy
            # A true positive is initial_risk_tier in ('HIGH', 'CRITICAL') and status in ('CONFIRMED_RUG', 'SLOW_RUG')
            eval_rows = self._conn.execute("""
                SELECT 
                    initial_risk_tier,
                    status,
                    human_verdict
                FROM token_audits
                WHERE status != 'NEW';
            """).fetchall()

            tp, fp, tn, fn = 0, 0, 0, 0
            for row in eval_rows:
                pred_rug = row[0] in ('HIGH', 'CRITICAL')
                actual_rug = row[1] in ('CONFIRMED_RUG', 'SLOW_RUG')
                if pred_rug and actual_rug:
                    tp += 1
                elif pred_rug and not actual_rug:
                    fp += 1
                elif not pred_rug and not actual_rug:
                    tn += 1
                elif not pred_rug and actual_rug:
                    fn += 1

            total_eval = tp + fp + tn + fn
            accuracy = round(((tp + tn) / total_eval) * 100.0, 1) if total_eval > 0 else 0.0
            precision = round((tp / (tp + fp)) * 100.0, 1) if (tp + fp) > 0 else 0.0
            recall = round((tp / (tp + fn)) * 100.0, 1) if (tp + fn) > 0 else 0.0

            return {
                "total_audits": total_audited,
                "confirmed_rugs": status_counts.get("CONFIRMED_RUG", 0) + status_counts.get("SLOW_RUG", 0),
                "surviving_candidates": status_counts.get("SURVIVING_CANDIDATE", 0),
                "graduated_runners": status_counts.get("GRADUATED", 0),
                "cto_takeovers": status_counts.get("CTO", 0),
                "pending_t1_audit": due_t1,
                "pending_t2_reaudit": due_t2,
                "human_reviewed_count": human_reviewed,
                "learning_metrics": {
                    "total_evaluated": total_eval,
                    "accuracy_pct": accuracy,
                    "precision_pct": precision,
                    "recall_pct": recall,
                    "true_positives": tp,
                    "false_positives_cto": fp,
                    "false_negatives_missed": fn,
                    "true_negatives": tn
                }
            }
        except Exception as e:
            return {
                "total_audits": 0,
                "confirmed_rugs": 0,
                "surviving_candidates": 0,
                "graduated_runners": 0,
                "cto_takeovers": 0,
                "pending_t1_audit": 0,
                "pending_t2_reaudit": 0,
                "human_reviewed_count": 0,
                "learning_metrics": {
                    "total_evaluated": 0,
                    "accuracy_pct": 0.0,
                    "precision_pct": 0.0,
                    "recall_pct": 0.0,
                    "true_positives": 0,
                    "false_positives_cto": 0,
                    "false_negatives_missed": 0,
                    "true_negatives": 0
                }
            }

    def get_cohort_matrix_tokens(self, matrix_type: str = "tp", limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Query tokens belonging to a specific confusion matrix quadrant.
        
        Quadrants:
        - 'tp' (True Positives): Predicted Rug & Confirmed Rugged
        - 'fp' (False Positives): Predicted Rug, but Survived (CTOs)
        - 'fn' (False Negatives): Predicted Clean, but Slow-Rugged (Missed)
        - 'tn' (True Negatives): Predicted Clean & Confirmed Survived
        """
        matrix_type = matrix_type.lower()
        if matrix_type == "tp":
            where_clause = "initial_risk_tier IN ('HIGH', 'CRITICAL') AND status IN ('CONFIRMED_RUG', 'SLOW_RUG')"
            order_by = "updated_at DESC"
        elif matrix_type == "fp":
            where_clause = "initial_risk_tier IN ('HIGH', 'CRITICAL') AND status IN ('SURVIVING_CANDIDATE', 'GRADUATED', 'CTO')"
            order_by = "current_mcap_usd DESC"
        elif matrix_type == "fn":
            where_clause = "initial_risk_tier = 'LOW' AND status IN ('CONFIRMED_RUG', 'SLOW_RUG')"
            order_by = "updated_at DESC"
        elif matrix_type == "tn":
            where_clause = "initial_risk_tier = 'LOW' AND status IN ('SURVIVING_CANDIDATE', 'GRADUATED', 'CTO')"
            order_by = "current_mcap_usd DESC"
        else:
            return []

        query = f"""
            SELECT 
                mint, symbol, name, status,
                initial_risk_score, initial_risk_tier,
                current_price_usd, current_mcap_usd, volume_24h, price_change_24h,
                audit_notes, human_notes, human_verdict, updated_at
            FROM token_audits
            WHERE {where_clause}
            ORDER BY {order_by}
            LIMIT $1 OFFSET $2;
        """
        try:
            return self._conn.execute(query, [limit, offset]).df().to_dict(orient="records")
        except Exception as e:
            logger.debug(f"get_cohort_matrix_tokens error for {matrix_type}: {e}")
            return []

    def close(self) -> None:
        """Close connection."""
        self._conn.close()

