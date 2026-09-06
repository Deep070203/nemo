"""FastAPI production dashboard server for real-time Pump.fun forensics."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
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
