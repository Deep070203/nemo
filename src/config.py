"""Configuration loader and schema definition."""

from pathlib import Path
from typing import List
import yaml
from pydantic import BaseModel, Field


class WebSocketConfig(BaseModel):
    url: str = "wss://pumpdev.io/ws"
    ping_interval: int = 20
    ping_timeout: int = 20
    inactivity_timeout_seconds: int = 35
    reconnect_min_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    max_tracked_trade_tokens: int = 100


class RPCConfig(BaseModel):
    endpoint: str = "https://api.mainnet-beta.solana.com"
    backup_endpoints: List[str] = Field(default_factory=lambda: ["https://solana-rpc.publicnode.com"])
    timeout_seconds: int = 15
    rate_limit_rps: int = 5
    max_retries: int = 3


class StorageConfig(BaseModel):
    database_path: str = "data/nemo_research.duckdb"
    batch_size: int = 50
    flush_interval_seconds: float = 5.0


class ForensicsThresholds(BaseModel):
    max_block0_bought_supply_ratio: float = 0.15
    max_dev_initial_buy_ratio: float = 0.10
    min_entropy_threshold: float = 2.0
    max_hhi_warning: float = 2500.0
    cluster_share_ratio_warning: float = 0.20


class Settings(BaseModel):
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)
    rpc: RPCConfig = Field(default_factory=RPCConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    forensics_thresholds: ForensicsThresholds = Field(default_factory=ForensicsThresholds)


def load_settings(config_path: str = "config/settings.yaml") -> Settings:
    path = Path(config_path)
    if not path.exists():
        return Settings()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return Settings(**data)
