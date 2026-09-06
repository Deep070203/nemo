"""Block-0 Bundle and Jito MEV sniper detection module."""

import logging
from typing import Dict, Any, List, Set, Optional
from pydantic import BaseModel, Field

from src.ingestion.rpc_client import SolanaRPCClient

logger = logging.getLogger("nemo.bundle_detector")

# Known official Jito tip payment receiver accounts
JITO_TIP_ACCOUNTS: Set[str] = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
}

# Pump.fun Program ID
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class Block0Transaction(BaseModel):
    signature: str
    signer: str
    is_create: bool = False
    tokens_bought: float = 0.0
    sol_spent: float = 0.0
    jito_tip_lamports: int = 0
    jito_tip_account: Optional[str] = None


class Block0BundleAnalysis(BaseModel):
    mint: str
    slot: int
    total_block_transactions: int = 0
    token_related_transactions: int = 0
    unique_block0_buyers: int = 0
    total_tokens_bought_block0: float = 0.0
    total_sol_spent_block0: float = 0.0
    block0_supply_pct: float = 0.0
    is_jito_bundled: bool = False
    total_jito_tip_lamports: int = 0
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    flags: List[str] = Field(default_factory=list)


class Block0BundleDetector:
    """Detects same-block bundled launches, Jito MEV tips, and supply cornering."""

    def __init__(self, rpc_client: SolanaRPCClient, max_supply_pct_warning: float = 0.15):
        self.rpc_client = rpc_client
        self.max_supply_pct_warning = max_supply_pct_warning
        self.total_supply = 1_000_000_000.0

    async def analyze_token_launch(self, mint: str, creation_signature: str) -> Block0BundleAnalysis:
        """Analyze the creation transaction and its parent block for bundled snipes."""
        # 1. Fetch creation transaction to find slot
        tx_info = await self.rpc_client.get_transaction(creation_signature)
        if not tx_info or "slot" not in tx_info:
            logger.warning(f"Could not retrieve creation tx info for {mint}")
            return Block0BundleAnalysis(
                mint=mint,
                slot=0,
                risk_level="UNKNOWN",
                flags=["CREATION_TX_UNAVAILABLE"]
            )

        slot = tx_info["slot"]
        return await self.analyze_slot(mint, slot, creation_signature)

    async def analyze_slot(self, mint: str, slot: int, creation_signature: Optional[str] = None) -> Block0BundleAnalysis:
        """Inspect all transactions in the creation slot for token interactions and Jito tips."""
        block_data = await self.rpc_client.get_block(slot, detail="full")
        if not block_data or "transactions" not in block_data:
            logger.warning(f"Could not retrieve block data for slot {slot}")
            return Block0BundleAnalysis(
                mint=mint,
                slot=slot,
                risk_level="UNKNOWN",
                flags=["BLOCK_DATA_UNAVAILABLE"]
            )

        transactions = block_data["transactions"]
        token_txs: List[Block0Transaction] = []
        unique_buyers: Set[str] = set()
        total_tokens_bought = 0.0
        total_sol_spent = 0.0
        is_jito = False
        total_tips = 0

        for tx in transactions:
            parsed_tx = self._parse_block_transaction(tx, mint)
            if parsed_tx:
                token_txs.append(parsed_tx)
                if parsed_tx.tokens_bought > 0:
                    unique_buyers.add(parsed_tx.signer)
                    total_tokens_bought += parsed_tx.tokens_bought
                    total_sol_spent += parsed_tx.sol_spent
                if parsed_tx.jito_tip_lamports > 0:
                    is_jito = True
                    total_tips += parsed_tx.jito_tip_lamports

        # Calculate percentage of total supply bought in slot 0
        supply_pct = (total_tokens_bought / self.total_supply) * 100.0

        # Assess risk levels and flags
        flags = []
        risk_level = "LOW"

        if is_jito:
            flags.append(f"JITO_MEV_TIP_DETECTED ({total_tips / 1e9:.3f} SOL)")
            risk_level = "HIGH"

        if supply_pct >= (self.max_supply_pct_warning * 100.0):
            flags.append(f"BLOCK0_SUPPLY_CORNERED ({supply_pct:.1f}% > {self.max_supply_pct_warning*100:.0f}%)")
            risk_level = "CRITICAL"

        if len(token_txs) > 1 and unique_buyers and len(unique_buyers) > 3:
            flags.append(f"MULTI_WALLET_SAME_BLOCK_SNIPE ({len(unique_buyers)} buyers in slot {slot})")
            if risk_level != "CRITICAL":
                risk_level = "HIGH"

        return Block0BundleAnalysis(
            mint=mint,
            slot=slot,
            total_block_transactions=len(transactions),
            token_related_transactions=len(token_txs),
            unique_block0_buyers=len(unique_buyers),
            total_tokens_bought_block0=total_tokens_bought,
            total_sol_spent_block0=total_sol_spent,
            block0_supply_pct=supply_pct,
            is_jito_bundled=is_jito,
            total_jito_tip_lamports=total_tips,
            risk_level=risk_level,
            flags=flags
        )

    def _parse_block_transaction(self, tx: Dict[str, Any], mint: str) -> Optional[Block0Transaction]:
        """Extract mint interaction and Jito tip information from a parsed transaction."""
        meta = tx.get("meta")
        transaction = tx.get("transaction", {})
        if not meta or not transaction:
            return None

        # Check if transaction errored
        if meta.get("err") is not None:
            return None

        account_keys = transaction.get("message", {}).get("accountKeys", [])
        # Account keys can be list of dicts or list of strings
        account_pubkeys = [
            acc.get("pubkey") if isinstance(acc, dict) else str(acc)
            for acc in account_keys
        ]

        # Must involve the mint or Pump program
        if mint not in account_pubkeys and PUMP_PROGRAM_ID not in account_pubkeys:
            return None

        signature = transaction.get("signatures", [""])[0]
        signer = account_pubkeys[0] if account_pubkeys else "Unknown"

        # Check for Jito MEV tips inside instructions or post-balance differences
        tip_lamports = 0
        tip_account = None

        # Inspect pre and post balances for transfers to Jito tip accounts
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])

        for idx, acc in enumerate(account_pubkeys):
            if acc in JITO_TIP_ACCOUNTS and idx < len(pre_balances) and idx < len(post_balances):
                delta = post_balances[idx] - pre_balances[idx]
                if delta > 0:
                    tip_lamports += delta
                    tip_account = acc

        # Check token balance changes to calculate tokens bought
        tokens_bought = 0.0
        pre_token_balances = meta.get("preTokenBalances", [])
        post_token_balances = meta.get("postTokenBalances", [])

        post_balances_by_owner: Dict[str, float] = {}
        for pt in post_token_balances:
            if pt.get("mint") == mint:
                owner = pt.get("owner", "")
                ui_amount = pt.get("uiTokenAmount", {}).get("uiAmount") or 0.0
                post_balances_by_owner[owner] = ui_amount

        pre_balances_by_owner: Dict[str, float] = {}
        for pt in pre_token_balances:
            if pt.get("mint") == mint:
                owner = pt.get("owner", "")
                ui_amount = pt.get("uiTokenAmount", {}).get("uiAmount") or 0.0
                pre_balances_by_owner[owner] = ui_amount

        for owner, post_bal in post_balances_by_owner.items():
            pre_bal = pre_balances_by_owner.get(owner, 0.0)
            diff = post_bal - pre_bal
            if diff > 0 and owner not in (PUMP_PROGRAM_ID, mint):
                tokens_bought += diff

        # SOL spent estimation (fee payer balance delta minus fees)
        sol_spent = 0.0
        if len(pre_balances) > 0 and len(post_balances) > 0:
            sol_delta = (pre_balances[0] - post_balances[0]) / 1e9
            if sol_delta > 0:
                sol_spent = max(0.0, sol_delta)

        return Block0Transaction(
            signature=signature,
            signer=signer,
            is_create=(mint in account_pubkeys and PUMP_PROGRAM_ID in account_pubkeys),
            tokens_bought=tokens_bought,
            sol_spent=sol_spent,
            jito_tip_lamports=tip_lamports,
            jito_tip_account=tip_account
        )
