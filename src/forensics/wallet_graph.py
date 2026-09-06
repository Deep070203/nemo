"""Graph-theoretic wallet ancestry and Sybil cluster detection module."""

import logging
from typing import Dict, List, Set, Optional, Tuple, Any
import networkx as nx
from pydantic import BaseModel, Field

from src.ingestion.rpc_client import SolanaRPCClient

logger = logging.getLogger("nemo.wallet_graph")

# Known Central Exchange (CEX) Hot Wallets / Aggregators
KNOWN_CEX_WALLETS: Dict[str, str] = {
    "5tzFkiKscMRHK5ZXPBHk2fZByJPMoCNEQuUMTXMz219V": "Binance 1",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance 2",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm": "Coinbase",
    "AC5RDfQFmDS1deWZos921qbhirGLTgRyrYZdmZ9XRDCQ": "Bybit",
    "FWznbcNXWQuHTawe9RxvQ2LdJF8nMkoSeqE urllib": "KuCoin",
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ": "OKX",
    "BM4qPteP3pNmE68t2Bw4U2C5h6t9zYw2H2D9U5Wb3aYx": "FixedFloat (Mixer/Bridge)",
}


class SybilCluster(BaseModel):
    cluster_id: int
    root_funder: str
    funder_label: Optional[str] = None
    wallets: List[str]
    total_tokens_held: float = 0.0
    cluster_share_ratio_pct: float = 0.0


class WalletGraphAnalysis(BaseModel):
    mint: str
    total_analyzed_wallets: int
    total_sybil_clusters: int
    largest_cluster_size: int
    largest_cluster_share_pct: float
    clusters: List[SybilCluster] = Field(default_factory=list)
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    flags: List[str] = Field(default_factory=list)


class WalletGraphForensics:
    """Builds funding DAGs and detects Sybil clusters among token buyers."""

    def __init__(self, rpc_client: SolanaRPCClient, max_cluster_share_warning: float = 0.20):
        self.rpc_client = rpc_client
        self.max_cluster_share_warning = max_cluster_share_warning
        self.total_supply = 1_000_000_000.0

    async def analyze_wallet_cohort(
        self,
        mint: str,
        wallet_balances: Dict[str, float],
        max_hops: int = 2
    ) -> WalletGraphAnalysis:
        """Trace funding sources for a cohort of early buyer wallets and identify Sybil rings."""
        graph = nx.DiGraph()
        wallet_funder_map: Dict[str, str] = {}

        # 1. Trace funding transactions for each wallet
        for wallet in wallet_balances.keys():
            funder = await self._find_funding_parent(wallet)
            if funder:
                wallet_funder_map[wallet] = funder
                graph.add_edge(funder, wallet, weight=1.0)
            else:
                wallet_funder_map[wallet] = wallet
                graph.add_node(wallet)

        # 2. Partition into Connected Components
        clusters: List[SybilCluster] = []
        cluster_id = 1

        # Group wallets sharing the exact same root funder
        funder_groups: Dict[str, List[str]] = {}
        for wallet, funder in wallet_funder_map.items():
            funder_groups.setdefault(funder, []).append(wallet)

        for funder, members in funder_groups.items():
            total_held = sum(wallet_balances.get(w, 0.0) for w in members)
            share_pct = (total_held / self.total_supply) * 100.0
            label = KNOWN_CEX_WALLETS.get(funder)

            clusters.append(SybilCluster(
                cluster_id=cluster_id,
                root_funder=funder,
                funder_label=label,
                wallets=members,
                total_tokens_held=total_held,
                cluster_share_ratio_pct=share_pct
            ))
            cluster_id += 1

        # Sort clusters by size and tokens held
        clusters.sort(key=lambda c: (len(c.wallets), c.cluster_share_ratio_pct), reverse=True)

        largest_cluster = clusters[0] if clusters else None
        largest_size = len(largest_cluster.wallets) if largest_cluster else 0
        largest_share = largest_cluster.cluster_share_ratio_pct if largest_cluster else 0.0

        # Assess risk
        flags = []
        risk_level = "LOW"

        if largest_share >= (self.max_cluster_share_warning * 100.0):
            flags.append(
                f"SYBIL_CABAL_SUPPLY_DOMINANCE ({largest_share:.1f}% held by single funder cluster)"
            )
            risk_level = "CRITICAL"

        if largest_size >= 4 and largest_cluster and largest_cluster.root_funder not in KNOWN_CEX_WALLETS:
            flags.append(
                f"PRIVATE_WALLET_SYBIL_RING ({largest_size} wallets funded by {largest_cluster.root_funder[:8]}...)"
            )
            if risk_level != "CRITICAL":
                risk_level = "HIGH"

        return WalletGraphAnalysis(
            mint=mint,
            total_analyzed_wallets=len(wallet_balances),
            total_sybil_clusters=len(clusters),
            largest_cluster_size=largest_size,
            largest_cluster_share_pct=largest_share,
            clusters=clusters,
            risk_level=risk_level,
            flags=flags
        )

    async def _find_funding_parent(self, wallet_address: str) -> Optional[str]:
        """Find the root wallet that originally funded this address with SOL."""
        sigs = await self.rpc_client.get_signatures_for_address(wallet_address, limit=10)
        if not sigs:
            return None

        # Sort by slot ascending to examine earliest known transaction
        oldest_sigs = sorted(sigs, key=lambda s: s.get("slot", 0))
        for sig_meta in oldest_sigs[:3]:
            sig = sig_meta.get("signature")
            if not sig:
                continue

            tx = await self.rpc_client.get_transaction(sig)
            if not tx or not tx.get("meta") or not tx.get("transaction"):
                continue

            meta = tx["meta"]
            transaction = tx["transaction"]
            account_keys = [
                acc.get("pubkey") if isinstance(acc, dict) else str(acc)
                for acc in transaction.get("message", {}).get("accountKeys", [])
            ]

            pre_bal = meta.get("preBalances", [])
            post_bal = meta.get("postBalances", [])

            # Look for an incoming SOL transfer
            for idx, acc in enumerate(account_keys):
                if acc == wallet_address and idx < len(pre_bal) and idx < len(post_bal):
                    if post_bal[idx] > pre_bal[idx]:
                        # Fee payer / signer is typically the funder
                        funder = account_keys[0] if account_keys and account_keys[0] != wallet_address else None
                        if funder:
                            return funder

        return None
