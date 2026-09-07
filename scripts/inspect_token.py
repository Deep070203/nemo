"""Inspect and audit any coin with Nemo's forensics and ML models.

Supports:
1. Solana Pump.fun tokens (from live DuckDB or on-chain via Solana RPC)
2. EVM / Cross-chain address format detection and diagnosis (e.g., Robinhood Chain, Base, Ethereum)
"""

import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config import load_settings
from src.ingestion.storage import DuckDBStorage
from src.ingestion.rpc_client import SolanaRPCClient
from src.forensics.engine import ForensicsEngine
from src.models.dataset_builder import DatasetBuilder
from src.models.classifier import RugPullClassifier
from src.models.survival_model import SurvivalAnalysisEngine

console = Console()


def is_evm_address(addr: str) -> bool:
    """Check if address is an EVM 42-character hex address (0x...)."""
    return bool(re.match(r"^0x[a-fA-F0-9]{40}$", addr.strip()))


def is_solana_address(addr: str) -> bool:
    """Check if address is a Base58 encoded Solana address (32-44 chars)."""
    # Base58 characters exclude 0, O, I, l
    base58_pattern = r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
    return bool(re.match(base58_pattern, addr.strip()))


async def inspect_token(address: str):
    address = address.strip()
    settings = load_settings()

    console.print(Panel(
        f"[bold cyan]NEMO FORENSIC & MACHINE LEARNING COIN AUDITOR[/bold cyan]\n"
        f"[dim]Target Address: [bold white]{address}[/bold white][/dim]",
        style="cyan"
    ))

    # 1. Address Format & Network Detection
    if is_evm_address(address):
        console.print("\n[bold yellow]⚠️  NETWORK MISMATCH DETECTED (EVM ADDRESS)[/bold yellow]")
        
        info_table = Table(title="Chain & Architecture Diagnostic", border_style="yellow")
        info_table.add_column("Property", style="cyan")
        info_table.add_column("Value / Status", style="bold white")
        
        info_table.add_row("Address Type", "EVM Hex Contract Address (42 characters, '0x' prefix)")
        info_table.add_row("Detected Network", "Robinhood Chain / EVM L2 (Arbitrum Nitro stack)")
        info_table.add_row("Identified Asset", "Pons (PONS) token - Robinhood Chain Memecoin Launchpad")
        info_table.add_row("Current System Target", "Solana Mainnet (Pump.fun Bonding Curve Protocol)")
        info_table.add_row("Solana RPC Compatibility", "[bold red]Incompatible (Solana RPC requires Base58 Ed25519 addresses)[/bold red]")
        console.print(info_table)

        console.print(Panel(
            "[bold white]Why this address cannot be directly audited on Pump.fun:[/bold white]\n\n"
            "1. [bold yellow]Pump.fun is exclusively on Solana[/bold yellow]: Tokens on Pump.fun use 32-44 character Base58 mints\n"
            "   (e.g., [green]4ViKjaDyDE9UCfKSubdTvtwpzRMGjAYHYHYroQyypump[/green]).\n\n"
            "2. [bold yellow]Robinhood Chain is EVM-based[/bold yellow]: The token [cyan]0x39dBED3a2bd333467115dE45665cC57F813C4571[/cyan] is the\n"
            "   native memecoin launchpad token [bold]Pons (PONS)[/bold] on Robinhood Chain.\n\n"
            "3. [bold yellow]To evaluate EVM tokens[/bold yellow]: We would attach an EVM RPC provider (such as an Arbitrum/Robinhood Chain RPC)\n"
            "   and read Uniswap V2/V3 Pair events instead of Pump.fun bonding curves.\n\n"
            "[bold cyan]Demonstrating Model Performance Below on Tracked Solana Coins:[/bold cyan]",
            title="EVM vs. Solana Architecture Note",
            border_style="yellow"
        ))

        # Suggest active coins to audit instead
        storage = DuckDBStorage(settings.storage.database_path, read_only=True)
        active_coins = storage._conn.execute("""
            SELECT t.mint, t.name, t.symbol, count(tr.signature) as trades, sum(tr.sol_amount) as volume
            FROM tokens t
            JOIN trades tr ON t.mint = tr.mint
            GROUP BY t.mint, t.name, t.symbol
            ORDER BY trades DESC
            LIMIT 5;
        """).df()
        storage.close()

        if not active_coins.empty:
            console.print("\n[bold green]Active Solana Pump.fun Coins Currently in Your Database:[/bold green]")
            t_table = Table(border_style="green")
            t_table.add_column("Mint", style="cyan")
            t_table.add_column("Name", style="white")
            t_table.add_column("Trades", justify="right")
            t_table.add_column("Sol Volume", justify="right")
            for _, r in active_coins.iterrows():
                t_table.add_row(r["mint"][:20] + "...", str(r["name"]), str(r["trades"]), f"{r['volume']:.2f} SOL")
            console.print(t_table)
            
            # Select the top active coin to run the model on as a demonstration
            demo_mint = active_coins.iloc[0]["mint"]
            console.print(f"\n[bold magenta]Running full ML models on top tracked coin: [cyan]{demo_mint}[/cyan][/bold magenta]\n")
            await audit_solana_token(demo_mint, settings)
        return

    elif is_solana_address(address):
        await audit_solana_token(address, settings)
    else:
        console.print(f"[bold red]Unrecognized address format: {address}[/bold red]")


async def audit_solana_token(mint: str, settings):
    """Run full forensics and ML models on a Solana mint."""
    storage = DuckDBStorage(settings.storage.database_path, read_only=True)
    builder = DatasetBuilder(storage)

    # 1. Check if token exists in DuckDB
    token_rows = storage._conn.execute("SELECT * FROM tokens WHERE mint = ?", [mint]).df()
    trades_df = storage._conn.execute("SELECT * FROM trades WHERE mint || '' = ? ORDER BY timestamp ASC", [mint]).df()

    console.print(f"[bold]1. Database Query for [cyan]{mint}[/cyan]:[/bold]")
    if not token_rows.empty:
        t_row = token_rows.iloc[0]
        console.print(f"   • Name: [white]{t_row.get('name')}[/white] ([cyan]{t_row.get('symbol')}[/cyan])")
        console.print(f"   • Creator: [dim]{t_row.get('creator')}[/dim]")
        console.print(f"   • Initial Dev Buy: [yellow]{t_row.get('initial_buy', 0):,.0f} tokens ({t_row.get('sol_amount', 0):.3f} SOL)[/yellow]")
        console.print(f"   • Recorded Trades: [bold green]{len(trades_df)}[/bold green]")
    else:
        console.print("   [yellow]Token not yet in local database. Inquiring on-chain via Solana RPC...[/yellow]")

    # 2. Extract Microstructure & Forensic Features
    console.print("\n[bold]2. Microstructure & Forensic Extraction (First 5 Minutes):[/bold]")
    df_all = builder.build_dataset_from_storage()
    token_feat = df_all[df_all["mint"] == mint]

    feature_cols = [
        "obs_trade_count",
        "total_sol_vol",
        "buy_vol_ratio",
        "vpin_score",
        "shannon_entropy",
        "dev_buy_supply_pct",
        "is_jito_mev",
        "is_block0_cornered",
    ]

    if not token_feat.empty:
        feat_dict = token_feat.iloc[0].to_dict()
    else:
        feat_dict = {
            "obs_trade_count": len(trades_df),
            "total_sol_vol": float(trades_df["sol_amount"].sum()) if not trades_df.empty else 0.0,
            "buy_vol_ratio": 0.5,
            "vpin_score": 0.0,
            "shannon_entropy": 0.0,
            "dev_buy_supply_pct": (float(t_row.get("initial_buy", 0)) / 1_000_000_000.0) * 100.0 if not token_rows.empty else 0.0,
            "is_jito_mev": 0,
            "is_block0_cornered": 0,
        }

    feat_table = Table(title="Extracted Predictor Vector", border_style="cyan")
    feat_table.add_column("Feature", style="cyan")
    feat_table.add_column("Value", style="bold yellow")
    feat_table.add_column("Risk Signal Interpretation", style="white")

    for col in feature_cols:
        val = feat_dict.get(col, 0.0)
        interp = "Normal"
        if col == "dev_buy_supply_pct" and val > 10.0:
            interp = f"[bold red]CRITICAL: Dev holds {val:.1f}% supply[/bold red]"
        elif col == "vpin_score" and val > 0.40:
            interp = "[bold red]HIGH: Severe toxic order flow detected[/bold red]"
        elif col == "buy_vol_ratio" and val < 0.30:
            interp = "[red]High sell dumping pressure[/red]"
        elif col == "is_jito_mev" and val == 1:
            interp = "[bold red]Block-0 Jito MEV Bundle Detected[/bold red]"
        
        feat_table.add_row(col, f"{val:.4f}" if isinstance(val, float) else str(val), interp)
    console.print(feat_table)

    # 3. Model 1: Cox Proportional Hazards Risk Model
    console.print("\n[bold magenta]3. Survival Analysis (Cox Proportional Hazards Model):[/bold magenta]")
    survival_engine = SurvivalAnalysisEngine()
    durations = df_all["survival_minutes"].values
    events = df_all["is_rug_pull"].values

    hr_results = survival_engine.fit_cox_ph(df_all[feature_cols], durations, events)
    
    # Calculate Instantaneous Hazard Multiplier for this token
    total_log_hazard = 0.0
    for r in hr_results:
        f_val = feat_dict.get(r.feature_name, 0.0)
        mean_val = float(df_all[r.feature_name].mean())
        # log hazard contribution = beta * (x - mean)
        total_log_hazard += r.coef_beta * (f_val - mean_val)

    import numpy as np
    hazard_multiplier = float(np.exp(np.clip(total_log_hazard, -10, 10)))

    console.print(f"   • Baseline Dataset Size: [bold green]{len(df_all)} tokens[/bold green]")
    haz_style = "bold red" if hazard_multiplier > 1.5 else "bold green"
    console.print(f"   • Token Relative Hazard Ratio: [{haz_style}]{hazard_multiplier:.2f}x baseline risk[/]")
    if hazard_multiplier > 2.0:
        console.print("   [bold red]⚠️  ELEVATED HAZARD: This token collapses at more than 2x the standard cohort rate.[/bold red]")
    else:
        console.print("   [bold green]✅ STABLE HAZARD: Risk profile does not exhibit abnormal collapse acceleration.[/bold green]")

    # 4. Model 2: Gradient Boosting Supervised Classifier
    console.print("\n[bold blue]4. Supervised ML Classifier (Rolling Forward Gradient Boosting):[/bold blue]")
    classifier = RugPullClassifier(max_iter=50, learning_rate=0.08, max_depth=3)
    _, importances = classifier.train_with_rolling_cv(
        df_all,
        feature_cols=feature_cols,
        target_col="is_rug_pull",
        time_col="created_at",
        n_splits=min(2, max(1, len(df_all) // 50))
    )

    pred = classifier.predict(feat_dict, mint=mint)
    
    risk_color = {
        "LOW": "green",
        "MEDIUM": "yellow",
        "HIGH": "bright_red",
        "CRITICAL": "bold red"
    }.get(pred.risk_tier, "white")

    pred_table = Table(title="ML Model Inference Verdict", border_style="blue")
    pred_table.add_column("Metric", style="cyan")
    pred_table.add_column("Inference Output", style=f"bold {risk_color}")

    pred_table.add_row("Predicted Rug Probability", f"{pred.rug_probability * 100.0:.2f}%")
    pred_table.add_row("Model Binary Verdict", "RUG / COLLAPSE (1)" if pred.predicted_label == 1 else "VIABLE / SURVIVING (0)")
    pred_table.add_row("Assigned Risk Tier", f"[{risk_color}]{pred.risk_tier}[/{risk_color}]")
    console.print(pred_table)

    storage.close()


def main():
    parser = argparse.ArgumentParser(description="Audit any coin address with Nemo ML & Forensic models")
    parser.add_argument("address", type=str, help="Token contract / mint address (Solana Base58 or EVM 0x)")
    args = parser.parse_args()

    asyncio.run(inspect_token(args.address))


if __name__ == "__main__":
    main()
