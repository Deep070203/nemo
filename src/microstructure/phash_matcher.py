"""Perceptual image hashing (pHash) module for meme copycat detection."""

import io
import logging
from typing import Dict, List, Optional, Tuple
import aiohttp
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

logger = logging.getLogger("nemo.phash_matcher")


class ImageMatchResult(BaseModel):
    mint: str
    image_hash: str
    is_clone: bool
    matched_mint: Optional[str] = None
    hamming_distance: int = 64
    risk_level: str = "LOW"
    flags: List[str] = Field(default_factory=list)


class PerceptualHashMatcher:
    """Computes perceptual hashes of token logos to detect recycled memes and copycat scams."""

    def __init__(self, max_hamming_clone_distance: int = 4):
        self.max_hamming_clone_distance = max_hamming_clone_distance
        # Map: image_hash (hex string) -> (mint, name)
        self._hash_registry: Dict[str, Tuple[str, str]] = {}

    def compute_phash_from_image(self, image: Image.Image) -> str:
        """Compute a 64-bit Difference Hash (dHash) for an image.

        1. Resize to 9x8 grayscale
        2. Compare adjacent pixels in each row
        3. Convert 64 boolean comparisons to a 16-character hex string
        """
        # Convert to grayscale and resize to 9 width x 8 height
        resized = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = np.array(resized, dtype=np.int32)

        # Compare adjacent pixels: row[x] > row[x+1]
        diff = pixels[:, 1:] > pixels[:, :-1]
        flat_bits = diff.flatten()

        # Convert 64 bits to an integer, then to hex
        hash_int = 0
        for bit in flat_bits:
            hash_int = (hash_int << 1) | int(bit)

        return f"{hash_int:016x}"

    @staticmethod
    def hamming_distance(hex1: str, hex2: str) -> int:
        """Compute bitwise Hamming distance between two 16-char hex hashes."""
        try:
            val1 = int(hex1, 16)
            val2 = int(hex2, 16)
            return bin(val1 ^ val2).count("1")
        except ValueError:
            return 64

    async def fetch_and_hash_image(self, image_url: str) -> Optional[str]:
        """Fetch image over HTTP/IPFS and calculate its perceptual hash."""
        # Convert ipfs:// to public HTTP gateway if needed
        url = image_url
        if url.startswith("ipfs://"):
            cid = url.replace("ipfs://", "")
            url = f"https://ipfs.io/ipfs/{cid}"

        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    data = await response.read()
                    img = Image.open(io.BytesIO(data))
                    return self.compute_phash_from_image(img)
        except Exception as e:
            logger.debug(f"Could not fetch/hash image from {url}: {e}")
            return None

    def match_hash(self, mint: str, image_hash: str, token_name: str = "") -> ImageMatchResult:
        """Search registered hashes to identify image clones."""
        best_match_mint = None
        best_match_name = None
        min_dist = 64

        for existing_hash, (ex_mint, ex_name) in self._hash_registry.items():
            if ex_mint == mint:
                continue
            dist = self.hamming_distance(image_hash, existing_hash)
            if dist < min_dist:
                min_dist = dist
                best_match_mint = ex_mint
                best_match_name = ex_name

        # Register current token
        self._hash_registry[image_hash] = (mint, token_name)

        is_clone = (min_dist <= self.max_hamming_clone_distance)
        flags: List[str] = []
        risk = "LOW"

        if is_clone:
            risk = "CRITICAL"
            flags.append(
                f"REUSED_MEME_IMAGE_CLONE (Dist: {min_dist} to '{best_match_name or 'token'}' [{best_match_mint[:6]}...])"
            )

        return ImageMatchResult(
            mint=mint,
            image_hash=image_hash,
            is_clone=is_clone,
            matched_mint=best_match_mint,
            hamming_distance=min_dist,
            risk_level=risk,
            flags=flags
        )
