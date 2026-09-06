"""Asynchronous Solana JSON-RPC client with failover and rate limiting."""

import asyncio
import logging
from typing import Dict, Any, Optional, List
import aiohttp
from src.config import RPCConfig

logger = logging.getLogger("nemo.rpc_client")


class SolanaRPCClient:
    """Async Solana RPC client with rate limiting, exponential retries, and multi-endpoint failover."""

    def __init__(self, config: Optional[RPCConfig] = None):
        self.config = config or RPCConfig()
        self._endpoints = [self.config.endpoint] + [
            ep for ep in self.config.backup_endpoints if ep != self.config.endpoint
        ]
        self._current_ep_idx = 0
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._min_interval = 1.0 / max(1, self.config.rate_limit_rps)
        self._last_call_time = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    @property
    def current_endpoint(self) -> str:
        return self._endpoints[self._current_ep_idx]

    def _rotate_endpoint(self) -> None:
        self._current_ep_idx = (self._current_ep_idx + 1) % len(self._endpoints)
        logger.warning(f"Rotated to backup RPC endpoint: {self.current_endpoint}")

    async def call(self, method: str, params: Optional[List[Any]] = None) -> Optional[Any]:
        """Execute JSON-RPC call with rate limiting, retries, and failover."""
        params = params or []
        session = await self._get_session()

        for attempt in range(self.config.max_retries):
            # Rate limiting check
            async with self._lock:
                now = asyncio.get_event_loop().time()
                elapsed = now - self._last_call_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
                self._last_call_time = asyncio.get_event_loop().time()
                self._request_id += 1
                req_id = self._request_id

            payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }

            try:
                async with session.post(self.current_endpoint, json=payload) as response:
                    if response.status == 429:
                        logger.warning(f"RPC rate limit (429) hit on {self.current_endpoint}. Retrying...")
                        await asyncio.sleep(1.0 * (attempt + 1))
                        self._rotate_endpoint()
                        continue

                    if response.status != 200:
                        logger.warning(f"RPC error status {response.status} on {method}. Retrying...")
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue

                    data = await response.json()
                    if "error" in data:
                        err = data["error"]
                        logger.warning(f"RPC returned error for {method}: {err}")
                        return None

                    return data.get("result")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"RPC request exception on {self.current_endpoint} ({method}): {e}")
                self._rotate_endpoint()
                await asyncio.sleep(0.5 * (attempt + 1))

        logger.error(f"RPC call {method} failed after {self.config.max_retries} attempts.")
        return None

    async def get_transaction(self, signature: str) -> Optional[Dict[str, Any]]:
        """Fetch confirmed transaction details including slot and instruction trace."""
        params = [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0
            }
        ]
        return await self.call("getTransaction", params)

    async def get_block(self, slot: int, detail: str = "full") -> Optional[Dict[str, Any]]:
        """Fetch block contents with version 0 transaction support."""
        params = [
            slot,
            {
                "encoding": "jsonParsed",
                "transactionDetails": detail,
                "rewards": False,
                "maxSupportedTransactionVersion": 0
            }
        ]
        return await self.call("getBlock", params)

    async def get_signatures_for_address(self, address: str, limit: int = 20) -> Optional[List[Dict[str, Any]]]:
        """Fetch transaction signatures associated with a wallet."""
        params = [
            address,
            {"limit": limit}
        ]
        return await self.call("getSignaturesForAddress", params)

    async def get_token_largest_accounts(self, mint: str) -> Optional[Dict[str, Any]]:
        """Fetch the 20 largest token accounts for a given mint."""
        params = [mint]
        return await self.call("getTokenLargestAccounts", params)

    async def close(self) -> None:
        """Close underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
