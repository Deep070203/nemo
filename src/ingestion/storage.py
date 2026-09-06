"""Persistent storage manager powered by DuckDB."""

import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any
import duckdb
from src.ingestion.models import TokenCreatedEvent, TokenTradeEvent


class DuckDBStorage:
    """High-throughput relational storage engine for Pump.fun research events."""

    def __init__(self, db_path: str = "data/nemo_research.duckdb", batch_size: int = 50, flush_interval: float = 5.0):
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = duckdb.connect(self.db_path)
        self._init_schema()

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

    def close(self) -> None:
        """Close connection."""
        self._conn.close()
