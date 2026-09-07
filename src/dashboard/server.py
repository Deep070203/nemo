"""FastAPI production dashboard server for real-time Pump.fun forensics."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
import re
import numpy as np
from typing import Set, Dict, Any, List, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from src.config import load_settings
from src.ingestion.storage import DuckDBStorage
from src.ingestion.models import TokenCreatedEvent, TokenTradeEvent
from src.ingestion.pumpdev_client import PumpDevWebSocketClient
from src.ingestion.rpc_client import SolanaRPCClient
from src.forensics.engine import ForensicsEngine, TokenForensicReport
from src.models.dataset_builder import DatasetBuilder
from src.models.survival_model import SurvivalAnalysisEngine
from src.models.classifier import RugPullClassifier

logger = logging.getLogger("nemo.dashboard_server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Nemo: Pump.fun Real-Time Forensic HUD", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


class ConnectionManager:
    """Manages active WebSocket connections to browser clients."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        if not self.active_connections:
            return
        payload = json.dumps(message, default=str)
        dead = set()
        for ws in self.active_connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self.active_connections.difference_update(dead)


ws_manager = ConnectionManager()

# Global state holders
settings = load_settings()
storage = DuckDBStorage(settings.storage.database_path)
rpc_client = SolanaRPCClient(settings.rpc)
forensics_engine = ForensicsEngine(settings, rpc_client, storage)
pumpdev_client = PumpDevWebSocketClient(settings.websocket)

# In-memory recent cache
recent_tokens: List[Dict[str, Any]] = []
recent_trades: List[Dict[str, Any]] = []
recent_reports: Dict[str, Dict[str, Any]] = {}


async def on_new_token(event: TokenCreatedEvent):
    """Handle new token creation and broadcast to browser UI."""
    await storage.insert_token(event)
    data = event.model_dump()
    data["initial_buy_supply_pct"] = event.initial_buy_supply_pct
    data["price_sol"] = event.price_sol

    recent_tokens.insert(0, data)
    if len(recent_tokens) > 50:
        recent_tokens.pop()

    # Track trades for this token
    await pumpdev_client.track_token_trades([event.mint])

    # Broadcast creation event to browser
    await ws_manager.broadcast({
        "type": "token_created",
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    # Trigger asynchronous audit
    asyncio.create_task(run_background_audit(event))


async def run_background_audit(event: TokenCreatedEvent):
    """Run forensic audit without blocking and broadcast report when ready."""
    try:
        report = await forensics_engine.audit_token(
            mint=event.mint,
            creation_signature=event.signature,
            metadata_uri=event.uri,
            token_name=event.name or ""
        )
        rep_dict = report.model_dump()
        recent_reports[event.mint] = rep_dict

        await ws_manager.broadcast({
            "type": "audit_completed",
            "data": rep_dict,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.debug(f"Audit error on {event.mint}: {e}")


async def on_new_trade(event: TokenTradeEvent):
    """Handle trade and broadcast to browser UI."""
    await storage.insert_trade(event)
    data = event.model_dump()
    data["price_sol"] = event.price_sol
    data["bonding_curve_progress_pct"] = event.bonding_curve_progress_pct

    recent_trades.insert(0, data)
    if len(recent_trades) > 50:
        recent_trades.pop()

    await ws_manager.broadcast({
        "type": "trade_executed",
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@app.on_event("startup")
async def startup_event():
    """Start ingestion client and DuckDB worker."""
    await storage.start()
    pumpdev_client.on_token_created(on_new_token)
    pumpdev_client.on_token_trade(on_new_trade)
    await pumpdev_client.start()
    logger.info("Nemo Production Dashboard server started.")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources."""
    await pumpdev_client.stop()
    await storage.stop()
    await rpc_client.close()
    storage.close()


@app.get("/api/stats")
async def get_stats():
    """Global statistics on database and risk tiers."""
    tok_count = storage.get_token_count()
    tr_count = storage.get_trade_count()

    flag_counts = storage._conn.execute("""
        SELECT severity, COUNT(*) FROM forensic_flags GROUP BY severity;
    """).fetchall()

    severity_map = {row[0]: row[1] for row in flag_counts}

    return {
        "total_tokens": tok_count,
        "total_trades": tr_count,
        "critical_rugs": severity_map.get("CRITICAL", 0),
        "high_risk": severity_map.get("HIGH", 0),
        "medium_risk": severity_map.get("MEDIUM", 0),
        "active_ws_clients": len(ws_manager.active_connections)
    }


@app.get("/api/tokens")
async def get_tokens(limit: int = 50):
    """Fetch recent tokens with audit reports if available."""
    tokens = storage.get_recent_tokens(limit)
    for t in tokens:
        mint = t["mint"]
        if mint in recent_reports:
            t["audit"] = recent_reports[mint]
        else:
            # Query flags from db if cached
            flags = storage._conn.execute("""
                SELECT flag_type, severity FROM forensic_flags WHERE mint = $1;
            """, [mint]).fetchall()
            t["flags"] = [f[0] for f in flags]
            t["max_severity"] = flags[0][1] if flags else "LOW"
    return tokens


@app.get("/api/token/{mint}")
async def get_token_detail(mint: str):
    """Fetch complete detail, recent trades, and forensic audit for a token."""
    tok = storage._conn.execute("SELECT * FROM tokens WHERE mint = $1", [mint]).df()
    if tok.empty:
        raise HTTPException(status_code=404, detail="Token not found")

    trades = storage._conn.execute("""
        SELECT * FROM trades WHERE mint = $1 ORDER BY timestamp DESC LIMIT 100;
    """, [mint]).df()

    flags = storage._conn.execute("""
        SELECT flag_type, severity, details, created_at FROM forensic_flags WHERE mint = $1;
    """, [mint]).df()

    tok_data = tok.to_dict(orient="records")[0]
    audit_data = recent_reports.get(mint)

    return {
        "token": tok_data,
        "trades": trades.to_dict(orient="records"),
        "flags": flags.to_dict(orient="records"),
        "audit": audit_data
    }


@app.post("/api/audit/{mint}")
async def trigger_audit(mint: str):
    """Trigger on-demand forensic audit for a token."""
    tok = storage._conn.execute("SELECT * FROM tokens WHERE mint = $1", [mint]).df()
    if tok.empty:
        raise HTTPException(status_code=404, detail="Token not found")

    row = tok.iloc[0]
    report = await forensics_engine.audit_token(
        mint=mint,
        creation_signature=row.get("signature"),
        metadata_uri=row.get("uri"),
        token_name=row.get("name", "")
    )
    rep_dict = report.model_dump()
    recent_reports[mint] = rep_dict
    return rep_dict


@app.get("/api/survival")
async def get_survival_analysis():
    """Return Kaplan-Meier curve points and Cox hazard multipliers for dataset."""
    builder = DatasetBuilder(storage)
    df = builder.build_dataset_from_storage()
    if df.empty or len(df) < 5:
        return {"status": "insufficient_data", "count": len(df)}

    engine = SurvivalAnalysisEngine()
    km = engine.fit_kaplan_meier(df["survival_minutes"].values, df["is_rug_pull"].values)

    feature_cols = [c for c in ["obs_trade_count", "total_sol_vol", "buy_vol_ratio", "vpin_score", "shannon_entropy", "dev_buy_supply_pct", "is_jito_mev"] if c in df.columns]
    hr_results = engine.fit_cox_ph(df[feature_cols], df["survival_minutes"].values, df["is_rug_pull"].values)

    return {
        "status": "success",
        "cohort_count": len(df),
        "kaplan_meier": km.model_dump(),
        "hazard_ratios": [r.model_dump() for r in hr_results]
    }


@app.post("/api/inspect")
async def inspect_coin(payload: Dict[str, Any]):
    """Inspect and audit any address: EVM cross-chain diagnostic or Solana ML & forensic audit."""
    address = str(payload.get("address", "")).strip()
    if not address:
        raise HTTPException(status_code=400, detail="Address is required")

    # 1. EVM Hex Address Check (e.g. 0x...)
    if re.match(r"^0x[a-fA-F0-9]{40}$", address):
        identified_asset = "Unknown EVM Asset"
        network_name = "Robinhood Chain / Arbitrum Nitro EVM"
        addr_lower = address.lower()
        if "39dbed3a2bd333467115de45665cc57f813c4571" in addr_lower:
            identified_asset = "Pons (PONS) Token"
            network_name = "Robinhood Chain (Arbitrum Nitro Stack)"
        elif "afa57c4c5a72d36530c8e816ad6e9a5947941536" in addr_lower:
            identified_asset = "EVM Token"
            network_name = "Robinhood Chain / EVM L2"

        return {
            "status": "evm_diagnostic",
            "is_evm": True,
            "address": address,
            "address_type": "EVM Hex Contract Address (42 characters, '0x' prefix)",
            "detected_network": network_name,
            "identified_asset": identified_asset,
            "message": "⚠️ NETWORK MISMATCH DETECTED: This is an EVM hex contract address. The Nemo Screener is configured for Solana Mainnet & Pump.fun bonding curve protocols (Base58 addresses).",
            "explanation": "Solana RPC nodes reject EVM hex addresses as invalid Base58 encoding. To audit EVM tokens, an EVM JSON-RPC provider (such as Arbitrum or Robinhood Chain RPC) and Uniswap V2/V3 pair tracking are required.",
            "solana_compatible": False
        }

    # 2. Solana Base58 Address Check
    if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid address format '{address}'. Please provide a 32-44 character Solana Base58 mint or 42-char EVM 0x address."
        )

    mint = address

    # Query local DuckDB
    tok_df = storage._conn.execute("SELECT * FROM tokens WHERE mint = ?", [mint]).df()
    trades_df = storage._conn.execute("SELECT * FROM trades WHERE mint || '' = ? ORDER BY timestamp ASC", [mint]).df()

    creation_sig = None
    metadata_uri = None
    token_name = mint[:8] + "..."
    token_symbol = "SOL-SPL"
    creator = "Unknown / External"
    dev_buy = 0.0
    dev_sol = 0.0
    on_chain_found = False

    if not tok_df.empty:
        r = tok_df.iloc[0]
        token_name = r.get("name") or token_name
        token_symbol = r.get("symbol") or token_symbol
        creator = r.get("creator") or creator
        creation_sig = r.get("signature")
        metadata_uri = r.get("uri")
        dev_buy = float(r.get("initial_buy") or 0.0)
        dev_sol = float(r.get("sol_amount") or 0.0)
        on_chain_found = True
    else:
        # Check on-chain via Solana RPC
        try:
            acc_info = await rpc_client.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
            if acc_info and acc_info.get("value"):
                on_chain_found = True
                sigs = await rpc_client.call("getSignaturesForAddress", [mint, {"limit": 10}])
                if sigs and len(sigs) > 0:
                    creation_sig = sigs[-1].get("signature")
        except Exception as e:
            logger.warning(f"On-chain lookup error for {mint}: {e}")

    # Run full multi-modal forensic inspection
    report = await forensics_engine.audit_token(
        mint=mint,
        creation_signature=creation_sig,
        metadata_uri=metadata_uri,
        token_name=token_name,
        trades=trades_df.to_dict(orient="records") if not trades_df.empty else None
    )
    recent_reports[mint] = report.model_dump()

    # Extract or build predictor vector
    builder = DatasetBuilder(storage)
    df_all = builder.build_dataset_from_storage()

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

    token_feat = df_all[df_all["mint"] == mint] if not df_all.empty else None
    if token_feat is not None and not token_feat.empty:
        feat_dict = token_feat.iloc[0].to_dict()
    else:
        feat_dict = {
            "obs_trade_count": len(trades_df),
            "total_sol_vol": float(trades_df["sol_amount"].sum()) if not trades_df.empty else 0.0,
            "buy_vol_ratio": 0.5,
            "vpin_score": report.vpin_analysis.vpin_score if report.vpin_analysis else 0.0,
            "shannon_entropy": report.entropy_analysis.shannon_entropy if report.entropy_analysis else 0.0,
            "dev_buy_supply_pct": (dev_buy / 1_000_000_000.0) * 100.0,
            "is_jito_mev": int(any("JITO" in f for f in report.all_flags)),
            "is_block0_cornered": int(any("BLOCK0_SUPPLY_CORNERED" in f for f in report.all_flags)),
        }

    # Run Survival Model (Cox PH)
    survival_info = None
    if not df_all.empty and len(df_all) >= 5:
        survival_engine = SurvivalAnalysisEngine()
        durations = df_all["survival_minutes"].values
        events = df_all["is_rug_pull"].values
        hr_results = survival_engine.fit_cox_ph(df_all[feature_cols], durations, events)

        total_log_hazard = 0.0
        for r in hr_results:
            f_val = feat_dict.get(r.feature_name, 0.0)
            mean_val = float(df_all[r.feature_name].mean())
            total_log_hazard += r.coef_beta * (f_val - mean_val)

        hazard_multiplier = float(np.exp(np.clip(total_log_hazard, -10, 10)))
        survival_info = {
            "hazard_multiplier": round(hazard_multiplier, 2),
            "is_elevated": hazard_multiplier > 1.5,
            "cohort_size": len(df_all),
            "interpretation": f"Collapses at {hazard_multiplier:.2f}x standard cohort rate" if hazard_multiplier > 1.5 else "Stable hazard rate within normal lifespan distribution"
        }

    # Run ML Supervised Classifier
    ml_info = None
    if not df_all.empty and len(df_all) >= 15:
        classifier = RugPullClassifier(max_iter=50, learning_rate=0.08, max_depth=3)
        classifier.train_with_rolling_cv(
            df_all,
            feature_cols=feature_cols,
            target_col="is_rug_pull",
            time_col="created_at",
            n_splits=min(2, max(1, len(df_all) // 50))
        )
        pred = classifier.predict(feat_dict, mint=mint)
        ml_info = {
            "rug_probability": round(pred.rug_probability * 100.0, 2),
            "predicted_label": pred.predicted_label,
            "verdict": "RUG / COLLAPSE (1)" if pred.predicted_label == 1 else "VIABLE / SURVIVING (0)",
            "risk_tier": pred.risk_tier
        }

    return {
        "status": "success",
        "is_evm": False,
        "mint": mint,
        "token": {
            "mint": mint,
            "name": token_name,
            "symbol": token_symbol,
            "creator": creator,
            "initial_buy": dev_buy,
            "sol_amount": dev_sol,
            "in_database": not tok_df.empty,
            "on_chain_found": on_chain_found
        },
        "features": {k: round(float(v), 4) if isinstance(v, (float, np.floating)) else v for k, v in feat_dict.items() if k in feature_cols},
        "forensics": report.model_dump(),
        "survival": survival_info,
        "classifier": ml_info,
        "trades_count": len(trades_df)
    }


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time streaming channel for browser client."""
    await ws_manager.connect(websocket)
    try:
        # Send initial snapshot of recent tokens and trades
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "tokens": recent_tokens[:20],
            "trades": recent_trades[:20],
            "reports": list(recent_reports.values())[:20]
        }, default=str))

        while True:
            # Keep connection open and receive client pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.debug(f"Client disconnected: {e}")
        ws_manager.disconnect(websocket)


# Mount static assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index():
    """Serve index.html at root."""
    index_file = STATIC_DIR / "index.html"
    return FileResponse(str(index_file))
