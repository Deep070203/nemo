"""Main entry point for Nemo: Pump.fun Ingestion & Research Pipeline."""

import asyncio
import signal
import logging
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout

from src.config import load_settings
from src.ingestion.models import TokenCreatedEvent, TokenTradeEvent
from src.ingestion.pumpdev_client import PumpDevWebSocketClient
from src.ingestion.storage import DuckDBStorage
from src.ingestion.rpc_client import SolanaRPCClient
from src.forensics.engine import ForensicsEngine, TokenForensicReport

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nemo.main")
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

console = Console()


class NemoResearchService:
    def __init__(self):
        self.settings = load_settings()
        self.storage = DuckDBStorage(
            db_path=self.settings.storage.database_path,
            batch_size=self.settings.storage.batch_size,
            flush_interval=self.settings.storage.flush_interval_seconds
        )
        self.rpc_client = SolanaRPCClient(self.settings.rpc)
        self.ws_client = PumpDevWebSocketClient(self.settings.websocket)
        self.forensics = ForensicsEngine(self.settings, self.rpc_client, self.storage)

        # In-memory recent events for UI display
        self.recent_tokens = []
        self.recent_trades = []
        self.recent_reports = []
        self.is_running = False

    async def handle_new_token(self, event: TokenCreatedEvent):
        """Handler for newly launched tokens on Pump.fun."""
        # 1. Persist to DuckDB
        await self.storage.insert_token(event)

        # 2. Add to UI stream
        self.recent_tokens.append(event)
        if len(self.recent_tokens) > 15:
            self.recent_tokens.pop(0)

        # 3. Dynamically subscribe to trades for the last N tokens
        await self.ws_client.track_token_trades([event.mint])

        logger.info(
            f"⚡ NEW TOKEN: {event.name or 'Unknown'} ({event.symbol or '?'}) | "
            f"Mint: {event.mint[:6]}...{event.mint[-4:]} | "
            f"Dev Buy: {event.initial_buy_supply_pct:.2f}% | "
            f"SOL: {event.solAmount:.2f}"
        )

        # 4. Trigger asynchronous forensic audit
        asyncio.create_task(self._audit_token_task(event))

    async def _audit_token_task(self, event: TokenCreatedEvent):
        """Execute on-chain forensic inspection without blocking live stream."""
        try:
            report = await self.forensics.audit_token(
                mint=event.mint,
                creation_signature=event.signature,
                metadata_uri=event.uri,
                token_name=event.name or ""
            )
            self.recent_reports.append(report)
            if len(self.recent_reports) > 15:
                self.recent_reports.pop(0)

            tier_style = {
                "LOW": "green",
                "MEDIUM": "yellow",
                "HIGH": "bold orange3",
                "CRITICAL": "bold red"
            }.get(report.risk_tier, "white")

            flag_msg = f" | Flags: {', '.join(report.all_flags[:2])}" if report.all_flags else " | Clean launch"
            logger.info(
                f"🔍 AUDIT [{report.risk_tier}] {event.symbol or event.mint[:6]} "
                f"(Risk Score: {report.composite_risk_score}/100){flag_msg}"
            )
        except Exception as e:
            logger.debug(f"Audit error for {event.mint}: {e}")

    async def handle_trade(self, event: TokenTradeEvent):
        """Handler for real-time trades on active bonding curves."""
        # 1. Persist to DuckDB
        await self.storage.insert_trade(event)

        # 2. Add to UI stream
        self.recent_trades.append(event)
        if len(self.recent_trades) > 15:
            self.recent_trades.pop(0)

        direction_emoji = "🟢 BUY " if event.txType == "buy" else "🔴 SELL"
        logger.debug(
            f"{direction_emoji}: {event.mint[:6]}... | "
            f"SOL: {event.solAmount or 0.0:.3f} | "
            f"Curve: {event.bonding_curve_progress_pct:.1f}%"
        )

    def render_dashboard(self) -> Layout:
        """Create a real-time terminal HUD using Rich layouts."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3)
        )

        # Header panel
        tokens_count = self.storage.get_token_count()
        trades_count = self.storage.get_trade_count()
        header_text = (
            f"[bold cyan]NEMO RESEARCH PIPELINE[/bold cyan] | "
            f"DB: [green]{self.settings.storage.database_path}[/green] | "
            f"Tokens Logged: [bold yellow]{tokens_count}[/bold yellow] | "
            f"Trades Logged: [bold yellow]{trades_count}[/bold yellow]"
        )
        layout["header"].update(Panel(header_text, style="cyan"))

        # Body split into 3 panels: Tokens, Trades, and Forensics Audits
        layout["body"].split_row(
            Layout(name="tokens", ratio=1),
            Layout(name="trades", ratio=1),
            Layout(name="audits", ratio=1)
        )

        # Tokens table
        tok_table = Table(title="Recent Token Launches", expand=True, border_style="blue")
        tok_table.add_column("Symbol", style="bold magenta", width=8)
        tok_table.add_column("Mint", style="cyan", width=12)
        tok_table.add_column("Dev Buy %", justify="right", style="yellow")
        tok_table.add_column("SOL", justify="right", style="green")

        for t in reversed(self.recent_tokens[-8:]):
            dev_buy = f"{t.initial_buy_supply_pct:.1f}%"
            dev_style = "bold red" if t.initial_buy_supply_pct > 10.0 else "yellow"
            tok_table.add_row(
                (t.symbol or "N/A")[:8],
                f"{t.mint[:5]}..{t.mint[-4:]}",
                f"[{dev_style}]{dev_buy}[/{dev_style}]",
                f"{t.solAmount:.2f}"
            )
        layout["tokens"].update(tok_table)

        # Trades table
        trade_table = Table(title="Live Trade Tape", expand=True, border_style="green")
        trade_table.add_column("Type", width=6)
        trade_table.add_column("Mint", style="cyan", width=12)
        trade_table.add_column("SOL", justify="right")
        trade_table.add_column("Curve %", justify="right", style="magenta")

        for tr in reversed(self.recent_trades[-8:]):
            tx_style = "bold green" if tr.txType == "buy" else "bold red"
            trade_table.add_row(
                f"[{tx_style}]{tr.txType.upper()}[/{tx_style}]",
                f"{tr.mint[:5]}..{tr.mint[-4:]}",
                f"{tr.solAmount or 0.0:.3f}",
                f"{tr.bonding_curve_progress_pct:.1f}%"
            )
        layout["trades"].update(trade_table)

        # Forensics & Audits table
        audit_table = Table(title="Forensics & Risk Audits", expand=True, border_style="red")
        audit_table.add_column("Mint", style="cyan", width=10)
        audit_table.add_column("Tier", justify="center", width=8)
        audit_table.add_column("Score", justify="right", width=6)
        audit_table.add_column("Top Flag", style="yellow", overflow="ellipsis")

        for rep in reversed(self.recent_reports[-8:]):
            tier_style = {
                "LOW": "bold green",
                "MEDIUM": "bold yellow",
                "HIGH": "bold orange3",
                "CRITICAL": "bold red"
            }.get(rep.risk_tier, "white")

            top_flag = rep.all_flags[0] if rep.all_flags else "Clean"
            audit_table.add_row(
                f"{rep.mint[:4]}..{rep.mint[-3:]}",
                f"[{tier_style}]{rep.risk_tier}[/{tier_style}]",
                f"{rep.composite_risk_score}/100",
                top_flag[:28]
            )
        layout["audits"].update(audit_table)

        # Footer
        footer_text = "[dim]Press Ctrl+C to stop service and safely flush database.[/dim]"
        layout["footer"].update(Panel(footer_text, style="dim"))

        return layout

    async def run(self):
        """Main service lifecycle."""
        self.is_running = True
        await self.storage.start()

        # Wire up listeners
        self.ws_client.on_token_created(self.handle_new_token)
        self.ws_client.on_token_trade(self.handle_trade)

        # Start streaming
        await self.ws_client.start()
        console.print("[bold green]Nemo Research Ingestion Service Started successfully.[/bold green]")

        try:
            while self.is_running:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful termination and database flush."""
        console.print("\n[yellow]Shutting down service and flushing DuckDB buffers...[/yellow]")
        await self.ws_client.stop()
        await self.storage.stop()
        await self.rpc_client.close()
        self.storage.close()
        console.print("[bold green]All buffers safely persisted. Nemo stopped.[/bold green]")


async def main():
    service = NemoResearchService()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        service.is_running = False
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Fallback for systems that don't support signal handlers in threads
            pass

    runner_task = asyncio.create_task(service.run())
    await stop_event.wait()
    runner_task.cancel()
    try:
        await runner_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
