"""Unified Forensics Engine orchestrating on-chain bundle detection, graph forensics, and market microstructure."""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from src.config import Settings
from src.ingestion.rpc_client import SolanaRPCClient
from src.ingestion.storage import DuckDBStorage
from src.forensics.bundle_detector import Block0BundleDetector, Block0BundleAnalysis
from src.forensics.wallet_graph import WalletGraphForensics, WalletGraphAnalysis
from src.forensics.concentration import ConcentrationCalculator, ConcentrationMetrics
from src.microstructure.vpin import VPINCalculator, VPINResult
from src.microstructure.entropy import TradeEntropyDetector, EntropyResult
from src.microstructure.phash_matcher import PerceptualHashMatcher, ImageMatchResult
from src.microstructure.social_auditor import SocialAuditor, SocialAuditResult

logger = logging.getLogger("nemo.forensics_engine")


class TokenForensicReport(BaseModel):
    mint: str
    composite_risk_score: int  # 0 to 100
    risk_tier: str  # LOW, MEDIUM, HIGH, CRITICAL
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    bundle_analysis: Optional[Block0BundleAnalysis] = None
    graph_analysis: Optional[WalletGraphAnalysis] = None
    concentration_metrics: Optional[ConcentrationMetrics] = None
    vpin_analysis: Optional[VPINResult] = None
    entropy_analysis: Optional[EntropyResult] = None
    image_match: Optional[ImageMatchResult] = None
    social_audit: Optional[SocialAuditResult] = None
    all_flags: List[str] = Field(default_factory=list)


class ForensicsEngine:
    """Coordinates on-chain and microstructure forensic inspections to generate composite risk scores."""

    def __init__(self, settings: Settings, rpc_client: SolanaRPCClient, storage: Optional[DuckDBStorage] = None):
        self.settings = settings
        self.rpc_client = rpc_client
        self.storage = storage

        # Phase 2 On-Chain Modules
        self.bundle_detector = Block0BundleDetector(
            rpc_client=rpc_client,
            max_supply_pct_warning=settings.forensics_thresholds.max_block0_bought_supply_ratio
        )
        self.wallet_graph = WalletGraphForensics(
            rpc_client=rpc_client,
            max_cluster_share_warning=settings.forensics_thresholds.cluster_share_ratio_warning
        )
        self.concentration_calc = ConcentrationCalculator(
            hhi_warning_threshold=settings.forensics_thresholds.max_hhi_warning
        )

        # Phase 3 Microstructure & Content Modules
        self.vpin_calc = VPINCalculator()
        self.entropy_detector = TradeEntropyDetector()
        self.phash_matcher = PerceptualHashMatcher()
        self.social_auditor = SocialAuditor()

    async def audit_token(
        self,
        mint: str,
        creation_signature: Optional[str] = None,
        holder_balances: Optional[Dict[str, float]] = None,
        metadata_uri: Optional[str] = None,
        image_url: Optional[str] = None,
        token_name: str = "",
        trades: Optional[List[Dict[str, Any]]] = None
    ) -> TokenForensicReport:
        """Perform full multi-modal forensic inspection of a token."""
        flags: List[str] = []
        score = 0

        # 1. Block-0 Bundle Detection
        bundle_res: Optional[Block0BundleAnalysis] = None
        if creation_signature:
            bundle_res = await self.bundle_detector.analyze_token_launch(mint, creation_signature)
            flags.extend(bundle_res.flags)
            if bundle_res.is_jito_bundled:
                score += 35
            if bundle_res.block0_supply_pct > 15.0:
                score += 40
            elif bundle_res.block0_supply_pct > 8.0:
                score += 20
            if bundle_res.unique_block0_buyers > 3:
                score += 20

        # 2. Holder Concentration & Wealth Inequality
        conc_res: Optional[ConcentrationMetrics] = None
        graph_res: Optional[WalletGraphAnalysis] = None
        if holder_balances:
            balances_list = list(holder_balances.values())
            conc_res = self.concentration_calc.compute(mint, balances_list)
            flags.extend(conc_res.flags)
            if conc_res.distribution_tier == "MONOPOLY":
                score += 35
            elif conc_res.distribution_tier == "CONCENTRATED":
                score += 20

            # 3. Sybil Cluster Graph Forensics
            graph_res = await self.wallet_graph.analyze_wallet_cohort(mint, holder_balances)
            flags.extend(graph_res.flags)
            if graph_res.largest_cluster_share_pct > 20.0:
                score += 40
            elif graph_res.largest_cluster_share_pct > 10.0:
                score += 20
            if graph_res.largest_cluster_size >= 4:
                score += 15

        # 4. Microstructure: VPIN Order Flow Toxicity
        vpin_res: Optional[VPINResult] = None
        entropy_res: Optional[EntropyResult] = None
        if trades:
            vpin_res = self.vpin_calc.compute(mint, trades)
            flags.extend(vpin_res.flags)
            if vpin_res.toxicity_tier == "TOXIC":
                score += 30
            elif vpin_res.toxicity_tier == "MODERATE":
                score += 15

            # 5. Microstructure: Trade Entropy & Wash Trading
            entropy_res = self.entropy_detector.compute(mint, trades)
            flags.extend(entropy_res.flags)
            if entropy_res.is_wash_trading:
                score += 30

        # 6. Content Forensics: pHash Meme Image Matching
        img_res: Optional[ImageMatchResult] = None
        if image_url:
            p_hash = await self.phash_matcher.fetch_and_hash_image(image_url)
            if p_hash:
                img_res = self.phash_matcher.match_hash(mint, p_hash, token_name)
                flags.extend(img_res.flags)
                if img_res.is_clone:
                    score += 35

        # 7. Content Forensics: Social Link Audit
        social_res: Optional[SocialAuditResult] = None
        if metadata_uri:
            social_res = await self.social_auditor.audit_metadata(mint, metadata_uri)
            flags.extend(social_res.flags)
            if social_res.is_dummy_socials:
                score += 25
            elif not social_res.has_twitter and not social_res.has_telegram:
                score += 15

        # Clamp composite score between 0 and 100
        composite_score = min(100, max(0, score))

        if composite_score >= 75:
            tier = "CRITICAL"
        elif composite_score >= 50:
            tier = "HIGH"
        elif composite_score >= 25:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        report = TokenForensicReport(
            mint=mint,
            composite_risk_score=composite_score,
            risk_tier=tier,
            bundle_analysis=bundle_res,
            graph_analysis=graph_res,
            concentration_metrics=conc_res,
            vpin_analysis=vpin_res,
            entropy_analysis=entropy_res,
            image_match=img_res,
            social_audit=social_res,
            all_flags=list(set(flags))
        )

        # Persist flags to DuckDB if storage is configured
        if self.storage and report.all_flags:
            try:
                for f in report.all_flags:
                    flag_id = f"{mint}_{hash(f)}"
                    self.storage._conn.execute("""
                        INSERT OR REPLACE INTO forensic_flags VALUES ($1, $2, $3, $4, $5, $6);
                    """, [flag_id, mint, f, tier, str(report.composite_risk_score), datetime.now(timezone.utc)])
            except Exception as e:
                logger.warning(f"Could not persist forensic flag: {e}")

        return report
