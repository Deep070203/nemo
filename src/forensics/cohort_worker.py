"""Background worker task for automated cohort-based delayed auditing."""

import asyncio
import logging
from typing import Optional
from src.forensics.cohort_auditor import CohortAuditor
from src.ingestion.storage import DuckDBStorage
from src.ingestion.rpc_client import SolanaRPCClient

logger = logging.getLogger("nemo.cohort_worker")


class CohortAuditWorker:
    """Periodically runs delayed Stage 1 batch audits and checks for resurrected tokens."""

    def __init__(
        self,
        storage: DuckDBStorage,
        rpc_client: Optional[SolanaRPCClient] = None,
        interval_seconds: float = 600.0,  # 10 minutes
        hours_threshold: float = 10.0
    ):
        self.storage = storage
        self.auditor = CohortAuditor(storage, rpc_client)
        self.interval_seconds = interval_seconds
        self.hours_threshold = hours_threshold
        self._is_running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the periodic background audit worker."""
        if not self._is_running:
            self._is_running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info(f"CohortAuditWorker started (Audit Horizon: {self.hours_threshold}h, Interval: {self.interval_seconds}s)")

    async def stop(self):
        """Stop worker."""
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("CohortAuditWorker stopped.")

    async def _run_loop(self):
        while self._is_running:
            try:
                logger.info(f"Running automated cohort batch audit for tokens > {self.hours_threshold}h old...")
                res = await self.auditor.audit_batch_t1(hours_threshold=self.hours_threshold, limit=60)
                if res.get("processed", 0) > 0:
                    logger.info(
                        f"Batch audit completed: {res['processed']} checked | "
                        f"💀 {res['new_rugs']} Rugs | 🛡️ {res['new_survivors']} Survivors"
                    )
            except Exception as e:
                logger.error(f"CohortAuditWorker error: {e}", exc_info=True)

            await asyncio.sleep(self.interval_seconds)

    async def trigger_now(self) -> dict:
        """Trigger an immediate audit on demand."""
        return await self.auditor.audit_batch_t1(hours_threshold=self.hours_threshold, limit=60)
