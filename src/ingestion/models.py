"""Data models for Pump.fun WebSocket events and storage entities."""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class TokenCreatedEvent(BaseModel):
    """Fired when a new token is created on the Pump.fun bonding curve."""
    signature: str
    mint: str
    traderPublicKey: str
    txType: str = "create"
    name: Optional[str] = None
    symbol: Optional[str] = None
    uri: Optional[str] = None
    initialBuy: float = 0.0
    initialQuoteAmount: Optional[float] = None
    solAmount: float = 0.0
    quoteAmountRaw: Optional[int] = None
    bondingCurveKey: Optional[str] = None
    vTokensInBondingCurve: Optional[float] = None
    vSolInBondingCurve: Optional[float] = None
    marketCapSol: Optional[float] = None
    tokenProgram: Optional[str] = None
    quoteMint: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def price_sol(self) -> float:
        """Derive price in SOL from virtual reserves."""
        if self.vSolInBondingCurve and self.vTokensInBondingCurve and self.vTokensInBondingCurve > 0:
            return self.vSolInBondingCurve / self.vTokensInBondingCurve
        return 0.0

    @property
    def initial_buy_supply_pct(self) -> float:
        """Estimate dev initial buy percentage of total 1 billion supply."""
        total_supply = 1_000_000_000.0
        return (self.initialBuy / total_supply) * 100.0


class TokenTradeEvent(BaseModel):
    """Fired on each buy/sell trade on an active bonding curve."""
    signature: str
    mint: str
    traderPublicKey: str
    txType: str  # 'buy' or 'sell'
    tokenAmount: float
    solAmount: Optional[float] = None
    quoteAmountRaw: Optional[int] = None
    bondingCurveKey: Optional[str] = None
    vTokensInBondingCurve: Optional[float] = None
    vSolInBondingCurve: Optional[float] = None
    marketCapSol: Optional[float] = None
    slot: Optional[int] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def price_sol(self) -> float:
        """Derive price in SOL from virtual reserves."""
        if self.vSolInBondingCurve and self.vTokensInBondingCurve and self.vTokensInBondingCurve > 0:
            return self.vSolInBondingCurve / self.vTokensInBondingCurve
        return 0.0

    @property
    def bonding_curve_progress_pct(self) -> float:
        """Estimate completion percentage toward graduation (~85 SOL curve cap)."""
        if not self.vSolInBondingCurve:
            return 0.0
        # Pump.fun starts around 30 virtual SOL and graduates around 115 virtual SOL (85 real SOL accumulated)
        # Real sol accumulated = (vSol - 30_000_000_000) / 1e9
        v_sol_lamports = self.vSolInBondingCurve
        real_sol = max(0.0, (v_sol_lamports - 30_000_000_000.0) / 1e9)
        return min(100.0, (real_sol / 85.0) * 100.0)


class AccountTradeEvent(BaseModel):
    """Fired on trades executed by tracked insider or whale wallets."""
    signature: str
    mint: str
    traderPublicKey: str
    txType: str
    tokenAmount: float
    solAmount: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
