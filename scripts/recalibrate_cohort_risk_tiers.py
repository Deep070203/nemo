"""Recalibrate historical cohort risk tiers in DuckDB based on early trade momentum.

Eliminates the false-negative artifact where zero-volume, abandoned pump.fun tokens
were mistakenly labeled as 'LOW' risk simply because they didn't trigger a Jito MEV flag.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nemo.recalibrate")


def recalibrate(db_path: str = "data/nemo_research.duckdb"):
    logger.info(f"Connecting to {db_path}...")
    conn = duckdb.connect(db_path)

    # 1. Inspect pre-migration confusion metrics
    pre_stats = conn.execute("""
        SELECT initial_risk_tier, status, COUNT(*) 
        FROM token_audits 
        GROUP BY 1, 2;
    """).fetchall()
    logger.info(f"Pre-migration distribution: {pre_stats}")

    # Inspect Claude before recalibration
    claude_pre = conn.execute("""
        SELECT mint, symbol, initial_risk_tier, status, current_mcap_usd, volume_24h, price_change_24h
        FROM token_audits WHERE mint = '57ifgrPyhc7MH8bRa7YXQLvSU6hLiYDUbqRz2W3Wpump';
    """).df().to_dict(orient="records")
    logger.info(f"Claude Ai before recalibration: {claude_pre}")

    # 2. Query all tokens with forensic flags, trades, and creation dev snipe
    query = """
        SELECT 
            a.mint,
            COALESCE(MAX(CASE WHEN f.severity = 'CRITICAL' THEN 1 ELSE 0 END), 0) as has_critical,
            COALESCE(MAX(CASE WHEN f.severity = 'HIGH' THEN 1 ELSE 0 END), 0) as has_high,
            COALESCE(COUNT(tr.mint), 0) as trade_count,
            COALESCE(SUM(tr.sol_amount), 0.0) as total_sol_vol,
            COALESCE(COUNT(DISTINCT tr.trader), 0) as unique_traders,
            COALESCE(MAX(tok.initial_buy), 0.0) as initial_buy,
            COALESCE(MAX(tok.sol_amount), 0.0) as sol_amount,
            COALESCE(MAX(a.current_mcap_usd), 0.0) as current_mcap_usd,
            COALESCE(MAX(a.volume_24h), 0.0) as volume_24h,
            COALESCE(MAX(a.price_change_24h), 0.0) as price_change_24h,
            COALESCE(MAX(CASE WHEN a.is_graduated THEN 1 ELSE 0 END), 0) as is_graduated
        FROM token_audits a
        LEFT JOIN forensic_flags f ON a.mint = f.mint
        LEFT JOIN trades tr ON a.mint = tr.mint
        LEFT JOIN tokens tok ON a.mint = tok.mint
        GROUP BY a.mint;
    """
    records = conn.execute(query).fetchall()
    logger.info(f"Evaluating {len(records)} tokens for Bayesian recalibration and strict status classification...")

    updated_to_critical = 0
    updated_to_high = 0
    updated_to_medium = 0
    kept_low = 0
    new_rugs = 0
    new_survivors = 0

    updates = []
    for r in records:
        mint = r[0]
        has_crit = bool(r[1])
        has_hi = bool(r[2])
        trades = r[3]
        vol_sol = float(r[4])
        unique_traders = r[5]
        init_buy = float(r[6])
        sol_spent = float(r[7])
        mcap = float(r[8])
        vol24 = float(r[9])
        change24 = float(r[10])
        is_grad = bool(r[11])

        # Bayesian Risk Scoring (Inverted Prior)
        # Check dev snipe (>10% total supply = 100M tokens or >=5.0 SOL)
        dev_supply_pct = (init_buy / 1_000_000_000.0) * 100.0
        if has_crit or dev_supply_pct >= 10.0 or sol_spent >= 5.0:
            tier = "CRITICAL"
            score = 95
            updated_to_critical += 1
        elif has_hi:
            tier = "HIGH"
            score = 80
            updated_to_high += 1
        elif trades < 5 or vol_sol < 2.0 or unique_traders < 4:
            # Zero-volume or dormant launch: Abandonment Hazard
            tier = "HIGH"
            score = 75
            updated_to_high += 1
        elif trades < 10 or vol_sol < 5.0 or unique_traders < 8:
            tier = "MEDIUM"
            score = 45
            updated_to_medium += 1
        else:
            # Demonstrated genuine organic liquidity & buyer dispersion
            tier = "LOW"
            score = 20
            kept_low += 1

        # Strict Outcome Classification:
        if is_grad:
            status = "GRADUATED" if mcap >= 50000.0 else "SURVIVING_CANDIDATE"
            notes = f"Graduated to Raydium AMM (Mcap ${mcap:,.0f})"
            new_survivors += 1
        elif change24 <= -70.0 or mcap < 7500.0:
            status = "CONFIRMED_RUG"
            notes = f"Severe collapse: Mcap ${mcap:,.0f}, 24h change {change24:.1f}%"
            new_rugs += 1
        elif mcap >= 25000.0 and change24 > -50.0 and vol24 >= 3000.0:
            status = "SURVIVING_CANDIDATE"
            notes = f"Active runner: Mcap ${mcap:,.0f}, Vol ${vol24:,.0f}"
            new_survivors += 1
        elif mcap >= 15000.0 and change24 > -40.0 and vol24 >= 5000.0:
            status = "SURVIVING_CANDIDATE"
            notes = f"Healthy bonding curve: Mcap ${mcap:,.0f}, Vol ${vol24:,.0f}"
            new_survivors += 1
        else:
            status = "CONFIRMED_RUG"
            notes = f"Stalled below escape velocity: Mcap ${mcap:,.0f}, Vol ${vol24:,.0f}"
            new_rugs += 1

        updates.append({
            "mint": mint,
            "initial_risk_tier": tier,
            "initial_risk_score": score,
            "status": status,
            "audit_notes": notes
        })

    import pandas as pd
    updates_df = pd.DataFrame(updates)
    conn.register("updates_df", updates_df)
    
    # Atomic swap: create new table with explicit PRIMARY KEY and updated values
    conn.execute("""
        CREATE TABLE token_audits_new (
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
    conn.execute("""
        INSERT INTO token_audits_new
        SELECT 
            a.mint,
            a.name,
            a.symbol,
            u.status,
            a.stage1_audited_at,
            a.stage2_audited_at,
            u.initial_risk_score,
            u.initial_risk_tier,
            a.current_price_usd,
            a.current_mcap_usd,
            a.peak_mcap_usd,
            a.volume_24h,
            a.price_change_24h,
            a.is_graduated,
            a.dev_balance_pct,
            u.audit_notes,
            a.human_verdict,
            a.human_notes,
            a.created_at,
            CURRENT_TIMESTAMP as updated_at
        FROM token_audits a
        JOIN updates_df u ON a.mint = u.mint;
    """)
    conn.execute("DROP TABLE token_audits;")
    conn.execute("ALTER TABLE token_audits_new RENAME TO token_audits;")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audits_status ON token_audits(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audits_created ON token_audits(created_at);")
    conn.commit()

    # 3. Inspect post-migration stats
    post_stats = conn.execute("""
        SELECT initial_risk_tier, status, COUNT(*) 
        FROM token_audits 
        GROUP BY 1, 2 
        ORDER BY 3 DESC;
    """).fetchall()

    logger.info("Recalibration complete!")
    logger.info(f"Updated CRITICAL: {updated_to_critical} | HIGH: {updated_to_high} | MEDIUM: {updated_to_medium} | LOW: {kept_low}")
    logger.info(f"New Rugs: {new_rugs} | New Survivors: {new_survivors}")
    logger.info(f"Post-migration distribution: {post_stats}")

    claude_post = conn.execute("""
        SELECT mint, symbol, initial_risk_tier, status, current_mcap_usd, volume_24h, price_change_24h, audit_notes
        FROM token_audits WHERE mint = '57ifgrPyhc7MH8bRa7YXQLvSU6hLiYDUbqRz2W3Wpump';
    """).df().to_dict(orient="records")
    logger.info(f"Claude Ai AFTER recalibration: {claude_post}")

    # Compute new confusion matrix
    eval_rows = conn.execute("""
        SELECT initial_risk_tier, status 
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

    total = tp + fp + tn + fn
    accuracy = round(((tp + tn) / total) * 100.0, 1) if total > 0 else 0
    precision = round((tp / (tp + fp)) * 100.0, 1) if (tp + fp) > 0 else 0
    recall = round((tp / (tp + fn)) * 100.0, 1) if (tp + fn) > 0 else 0

    logger.info(f"NEW CONFUSION MATRIX:")
    logger.info(f"  TP (Predicted Rug & Rugged):        {tp}")
    logger.info(f"  FP (Predicted Rug, But Survived):   {fp}")
    logger.info(f"  FN (Predicted Clean, But Collapsed): {fn}")
    logger.info(f"  TN (Predicted Clean & Survived):     {tn}")
    logger.info(f"  ACCURACY:  {accuracy}%")
    logger.info(f"  PRECISION: {precision}%")
    logger.info(f"  RECALL:    {recall}%")

    conn.close()


if __name__ == "__main__":
    recalibrate()
