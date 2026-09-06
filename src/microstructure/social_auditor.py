"""Social link verification and metadata auditor module."""

import logging
from typing import Dict, Any, Optional, List
import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger("nemo.social_auditor")

DUMMY_SOCIAL_DOMAINS = {
    "twitter.com", "x.com", "t.me", "telegram.org", "pump.fun", "example.com"
}


class SocialAuditResult(BaseModel):
    mint: str
    metadata_uri: Optional[str] = None
    has_twitter: bool = False
    has_telegram: bool = False
    has_website: bool = False
    twitter_url: Optional[str] = None
    telegram_url: Optional[str] = None
    website_url: Optional[str] = None
    is_dummy_socials: bool = False
    social_completeness_score: int = 0  # 0 to 100
    risk_level: str = "LOW"
    flags: List[str] = Field(default_factory=list)


class SocialAuditor:
    """Audits token metadata JSON and verifies social links."""

    async def audit_metadata(self, mint: str, metadata_uri: Optional[str]) -> SocialAuditResult:
        """Fetch metadata JSON and evaluate attached social links."""
        if not metadata_uri:
            return SocialAuditResult(
                mint=mint,
                metadata_uri=None,
                risk_level="HIGH",
                flags=["NO_METADATA_URI"]
            )

        metadata = await self._fetch_metadata_json(metadata_uri)
        if not metadata:
            return SocialAuditResult(
                mint=mint,
                metadata_uri=metadata_uri,
                risk_level="MEDIUM",
                flags=["METADATA_FETCH_FAILED"]
            )

        twitter = metadata.get("twitter") or metadata.get("extensions", {}).get("twitter")
        telegram = metadata.get("telegram") or metadata.get("extensions", {}).get("telegram")
        website = metadata.get("website") or metadata.get("extensions", {}).get("website")

        flags: List[str] = []
        is_dummy = False
        score = 0

        if twitter:
            score += 40
            if self._is_dummy_link(twitter):
                is_dummy = True
                flags.append("DUMMY_TWITTER_LINK")
        else:
            flags.append("MISSING_TWITTER")

        if telegram:
            score += 35
            if self._is_dummy_link(telegram):
                is_dummy = True
                flags.append("DUMMY_TELEGRAM_LINK")
        else:
            flags.append("MISSING_TELEGRAM")

        if website:
            score += 25
            if self._is_dummy_link(website):
                is_dummy = True
                flags.append("DUMMY_WEBSITE_LINK")

        if is_dummy:
            risk = "HIGH"
            score = max(0, score - 50)
        elif score >= 75:
            risk = "LOW"
        elif score >= 35:
            risk = "MEDIUM"
        else:
            risk = "HIGH"

        return SocialAuditResult(
            mint=mint,
            metadata_uri=metadata_uri,
            has_twitter=bool(twitter),
            has_telegram=bool(telegram),
            has_website=bool(website),
            twitter_url=twitter,
            telegram_url=telegram,
            website_url=website,
            is_dummy_socials=is_dummy,
            social_completeness_score=score,
            risk_level=risk,
            flags=flags
        )

    def _is_dummy_link(self, url: str) -> bool:
        """Check if URL points to generic landing page or empty placeholder."""
        cleaned = url.strip().lower().rstrip("/")
        # Exact root domain matches like https://x.com or https://t.me
        for d in DUMMY_SOCIAL_DOMAINS:
            if cleaned in (f"https://{d}", f"http://{d}", f"https://www.{d}", f"http://www.{d}"):
                return True
        return len(cleaned.split("/")) <= 3  # No handle or subpath

    async def _fetch_metadata_json(self, uri: str) -> Optional[Dict[str, Any]]:
        url = uri
        if url.startswith("ipfs://"):
            cid = url.replace("ipfs://", "")
            url = f"https://ipfs.io/ipfs/{cid}"

        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
        except Exception as e:
            logger.debug(f"Could not parse metadata JSON from {url}: {e}")
        return None
