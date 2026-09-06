"""Asynchronous WebSocket client for pumpdev.io streaming API."""

import asyncio
import json
import logging
from typing import Callable, Coroutine, Any, Set, List, Optional
import websockets
from websockets.exceptions import ConnectionClosed

from src.config import WebSocketConfig
from src.ingestion.models import TokenCreatedEvent, TokenTradeEvent, AccountTradeEvent

from websockets.protocol import State

logger = logging.getLogger("nemo.pumpdev_client")


class PumpDevWebSocketClient:
    """Robust WebSocket client with auto-reconnection, watchdogs, and dynamic resubscription."""

    def __init__(self, config: Optional[WebSocketConfig] = None):
        self.config = config or WebSocketConfig()
        self._ws = None
        self._is_running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_msg_timestamp: float = 0.0

        # Subscriptions to maintain across reconnects
        self._sub_new_tokens = True
        self._tracked_tokens: Set[str] = set()
        self._tracked_accounts: Set[str] = set()

        # Listeners / callbacks
        self._on_token_created_callbacks: List[Callable[[TokenCreatedEvent], Coroutine[Any, Any, None]]] = []
        self._on_token_trade_callbacks: List[Callable[[TokenTradeEvent], Coroutine[Any, Any, None]]] = []
        self._on_account_trade_callbacks: List[Callable[[AccountTradeEvent], Coroutine[Any, Any, None]]] = []

    @property
    def is_connected(self) -> bool:
        """Check if active connection is in OPEN state."""
        return self._ws is not None and getattr(self._ws, "state", None) == State.OPEN

    def on_token_created(self, cb: Callable[[TokenCreatedEvent], Coroutine[Any, Any, None]]) -> None:
        """Register a callback for new token creation events."""
        self._on_token_created_callbacks.append(cb)

    def on_token_trade(self, cb: Callable[[TokenTradeEvent], Coroutine[Any, Any, None]]) -> None:
        """Register a callback for token trade events."""
        self._on_token_trade_callbacks.append(cb)

    def on_account_trade(self, cb: Callable[[AccountTradeEvent], Coroutine[Any, Any, None]]) -> None:
        """Register a callback for account trade events."""
        self._on_account_trade_callbacks.append(cb)

    async def start(self) -> None:
        """Start the WebSocket client and supervision loop."""
        if self._is_running:
            return
        self._is_running = True
        self._loop_task = asyncio.create_task(self._connection_supervisor())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        """Stop the client and close active connections."""
        self._is_running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._loop_task:
            self._loop_task.cancel()
        if self._ws:
            await self._ws.close()
        logger.info("PumpDev WebSocket client stopped.")

    async def track_token_trades(self, mints: List[str]) -> None:
        """Add mints to trade subscription stream."""
        new_mints = [m for m in mints if m not in self._tracked_tokens]
        if not new_mints:
            return

        for m in new_mints:
            self._tracked_tokens.add(m)

        # Enforce max limit by evicting oldest if necessary
        while len(self._tracked_tokens) > self.config.max_tracked_trade_tokens:
            self._tracked_tokens.pop()

        if self.is_connected:
            await self._send_subscribe_trades(list(self._tracked_tokens))

    async def _send_subscribe_trades(self, keys: List[str]) -> None:
        if not keys:
            return
        payload = {
            "method": "subscribeTokenTrade",
            "keys": keys[:100]  # API limits up to 100 per call
        }
        await self._ws.send(json.dumps(payload))

    async def _send_subscribe_new_tokens(self) -> None:
        payload = {"method": "subscribeNewToken"}
        await self._ws.send(json.dumps(payload))

    async def _connection_supervisor(self) -> None:
        """Supervises the connection and applies exponential backoff on disconnects."""
        delay = self.config.reconnect_min_delay
        while self._is_running:
            try:
                logger.info(f"Connecting to {self.config.url}...")
                async with websockets.connect(
                    self.config.url,
                    ping_interval=self.config.ping_interval,
                    ping_timeout=self.config.ping_timeout
                ) as ws:
                    self._ws = ws
                    self._last_msg_timestamp = asyncio.get_event_loop().time()
                    delay = self.config.reconnect_min_delay
                    logger.info("Connected to pumpdev.io WebSocket.")

                    # Re-subscribe to channels
                    if self._sub_new_tokens:
                        await self._send_subscribe_new_tokens()
                    if self._tracked_tokens:
                        await self._send_subscribe_trades(list(self._tracked_tokens))

                    # Process inbound messages
                    await self._message_reader(ws)

            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                logger.warning(f"WebSocket connection lost: {e}. Reconnecting in {delay:.1f}s...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in WebSocket loop: {e}", exc_info=True)

            if self._is_running:
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, self.config.reconnect_max_delay)

    async def _message_reader(self, ws: Any) -> None:
        """Read and dispatch incoming stream events."""
        async for raw_message in ws:
            self._last_msg_timestamp = asyncio.get_event_loop().time()
            try:
                data = json.loads(raw_message)
                await self._dispatch(data)
            except json.JSONDecodeError:
                logger.warning(f"Failed to decode JSON: {raw_message[:100]}")
            except Exception as e:
                logger.error(f"Error handling message: {e}", exc_info=True)

    async def _dispatch(self, data: Any) -> None:
        """Route parsed message to proper handler based on payload structure."""
        if not isinstance(data, dict):
            return

        msg_type = data.get("type")
        # Filter status control messages
        if msg_type in ("connected", "connectionStatus", "subscribed"):
            logger.debug(f"Control message: {data}")
            return

        tx_type = data.get("txType")
        if tx_type == "create":
            # Token creation event
            try:
                event = TokenCreatedEvent(**data)
                for cb in self._on_token_created_callbacks:
                    asyncio.create_task(cb(event))
            except Exception as e:
                logger.warning(f"Error parsing TokenCreatedEvent: {e}")

        elif tx_type in ("buy", "sell"):
            # Trade event
            try:
                event = TokenTradeEvent(**data)
                for cb in self._on_token_trade_callbacks:
                    asyncio.create_task(cb(event))
            except Exception as e:
                logger.warning(f"Error parsing TokenTradeEvent: {e}")

    async def _watchdog_loop(self) -> None:
        """Watchdog to force reconnection if no messages are received within timeout window."""
        while self._is_running:
            await asyncio.sleep(5.0)
            if self.is_connected and self._last_msg_timestamp > 0:
                elapsed = asyncio.get_event_loop().time() - self._last_msg_timestamp
                if elapsed > self.config.inactivity_timeout_seconds:
                    logger.warning(f"Inactivity timeout ({elapsed:.1f}s > {self.config.inactivity_timeout_seconds}s). Forcing reconnect.")
                    await self._ws.close()
