"""Research training and survival analysis evaluation runner."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.config import load_settings
from src.ingestion.storage import DuckDBStorage
from src.models.dataset_builder import DatasetBuilder
from src.models.survival_model import SurvivalAnalysisEngine
from src.models.classifier import RugPullClassifier

logging.basicConfig(level=logging.INFO)
console = Console()


def run_training_pipeline():
    settings = load_settings()
    storage = DuckDBStorage(settings.storage.database_path, read_only=True)

    console.print(Panel("[bold cyan]NEMO: STATISTICAL SURVIVAL & MACHINE LEARNING PIPELINE[/bold cyan]\n"
                        "[dim]Methodology aligned with arXiv:2608.20271 & Srifa et al. (2025)[/dim]", style="cyan"))

    builder = DatasetBuilder(storage)
    console.print("[yellow]Extracting 5-minute feature vectors and ground-truth survival labels from DuckDB...[/yellow]")
    df = builder.build_dataset_from_storage()

    if df.empty or len(df) < 5:
        console.print(f"[bold red]Insufficient tokens in database ({len(df)} found). Need at least 5 to run analysis.[/bold red]")
        console.print("[dim]Run 'python3 main.py' to stream and record live tokens first.[/dim]")
        storage.close()
        return

    console.print(f"[bold green]Successfully extracted {len(df)} token cohorts.[/bold green]")

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

    # Filter to available features
    feature_cols = [c for c in feature_cols if c in df.columns]

    # 1. Survival Analysis: Cox Proportional Hazards
    console.print("\n[bold magenta]1. Cox Proportional Hazards Model (Instantaneous Risk of Collapse)[/bold magenta]")
    survival_engine = SurvivalAnalysisEngine()
    durations = df["survival_minutes"].values
    events = df["is_rug_pull"].values

    hr_results = survival_engine.fit_cox_ph(df[feature_cols], durations, events)

    hr_table = Table(title="Cox Proportional Hazards: Risk Factor Multipliers", border_style="magenta")
    hr_table.add_column("Feature", style="cyan")
    hr_table.add_column("Hazard Ratio (HR)", justify="right", style="bold yellow")
    hr_table.add_column("95% Conf. Interval", justify="center")
    hr_table.add_column("p-value", justify="right")
    hr_table.add_column("Interpretation", style="green")

    for r in hr_results:
        interpretation = "Increases Rug Risk" if r.hazard_ratio > 1.05 else ("Decreases Rug Risk" if r.hazard_ratio < 0.95 else "Neutral")
        hr_table.add_row(
            r.feature_name,
            f"{r.hazard_ratio:.2f}x",
            f"[{r.ci_lower:.2f}, {r.ci_upper:.2f}]",
            f"{r.p_value:.3f}",
            interpretation
        )
    console.print(hr_table)

    # 2. Supervised Classification: Rolling Time-Series CV
    if len(df) >= 15 and df["is_rug_pull"].nunique() > 1:
        console.print("\n[bold blue]2. Rolling Forward Time-Series Cross-Validation (Gradient Boosting)[/bold blue]")
        classifier = RugPullClassifier(max_iter=50, learning_rate=0.08, max_depth=3)
        metrics_list, importances = classifier.train_with_rolling_cv(
            df,
            feature_cols=feature_cols,
            target_col="is_rug_pull",
            time_col="created_at",
            n_splits=min(3, max(1, len(df) // 10))
        )

        # Print Fold Metrics
        m_table = Table(title="Rolling Forward CV Evaluation (arXiv §IV-C)", border_style="blue")
        m_table.add_column("Fold", justify="center")
        m_table.add_column("Train / Test", justify="center")
        m_table.add_column("MCC", justify="right", style="bold green")
        m_table.add_column("Positive F1", justify="right", style="yellow")
        m_table.add_column("PR-AUC", justify="right", style="cyan")
        m_table.add_column("Brier Score", justify="right")

        for idx, m in enumerate(metrics_list):
            m_table.add_row(
                f"Fold {idx+1}",
                f"{m.train_samples} / {m.test_samples}",
                f"{m.mcc:.3f}",
                f"{m.f1_positive:.3f}",
                f"{m.pr_auc:.3f}",
                f"{m.brier_score:.3f}"
            )
        console.print(m_table)

        # Print Permutation Importance Ranking
        imp_table = Table(title="Predictive Feature Importance (Permutation Ranking)", border_style="cyan")
        imp_table.add_column("Rank", justify="center", width=5)
        imp_table.add_column("Feature", style="bold cyan")
        imp_table.add_column("Importance Gain", justify="right", style="yellow")

        for idx, imp in enumerate(importances[:6]):
            imp_table.add_row(str(idx + 1), imp.feature, f"{imp.importance_gain:.4f}")
        console.print(imp_table)

    storage.close()


if __name__ == "__main__":
    run_training_pipeline()
