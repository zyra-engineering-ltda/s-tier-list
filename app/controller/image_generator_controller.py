# services/image_generator.py
from __future__ import annotations

import hashlib
import os
import re
import textwrap
from io import BytesIO
from typing import Dict, Tuple, Optional

import requests
from PIL import Image, ImageDraw, ImageFont


class ImageGeneratorController:
    """
    Generate tier-list collages from book covers with a per-namespace disk cache.

    Pass a cache namespace (e.g., user ID/email/tenant) to segregate files:
        svc = ImageGeneratorService(cache_root="var/cover_cache")
        png_bytes = svc.generate_collage(ranks, urls, titles, cache_namespace=user_id)
    """

    def __init__(self, cache_root: Optional[str] = None):
        # Base cache root (shared across the app)
        self.cache_root = cache_root or os.path.join(os.path.dirname(__file__), "cover_cache")
        os.makedirs(self.cache_root, exist_ok=True)

    # ── Utilities to parse form data ──────────────────────────────────────────
    @staticmethod
    def extract_ranks(form) -> Dict[str, str]:
        """Accepts keys like ranks[BOOK_ID] or ranks.BOOK_ID"""
        ranks: Dict[str, str] = {}
        b = re.compile(r"^ranks\[(?P<id>.+?)\]$")
        d = re.compile(r"^ranks\.(?P<id>.+)$")
        for k, v in form.items():
            m = b.match(k) or d.match(k)
            if m and v:
                ranks[m.group("id")] = str(v)
        return ranks

    @staticmethod
    def extract_meta(form) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Pick up {{ book_id }}-url and {{ book_id }}-title"""
        urls, titles = {}, {}
        for k, v in form.items():
            if k.endswith("-url"):
                book_id = k[:-4]
                urls[book_id] = str(v)
            elif k.endswith("-title"):
                book_id = k[:-6]
                titles[book_id] = str(v)
        return urls, titles

    # ── Cache helpers (per-namespace) ────────────────────────────────────────
    def _ns_dir(self, namespace: Optional[str]) -> str:
        """
        Return/create a directory for this namespace. If namespace is None, use 'shared'.
        We hash to keep it filesystem-safe and short.
        """
        ns = "shared" if not namespace else hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
        ns_dir = os.path.join(self.cache_root, ns)
        os.makedirs(ns_dir, exist_ok=True)
        return ns_dir

    def _cache_path(self, url: str, namespace: Optional[str]) -> str:
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self._ns_dir(namespace), f"{h}.jpg")

    # ── IO helpers ───────────────────────────────────────────────────────────
    def _open_rgb_copy_from_path(self, path: str) -> Optional[Image.Image]:
        """Open an image from disk as an RGB *copy* (detaches file handle)."""
        try:
            with Image.open(path) as im:
                return im.convert("RGB").copy()
        except Exception:
            return None

    # ── Network/cache fetch ──────────────────────────────────────────────────
    def fetch_cover(self, url: Optional[str], namespace: Optional[str], timeout: int = 8) -> Optional[Image.Image]:
        """
        Return a PIL.Image (RGB). Uses per-namespace disk cache.
        Falls back to None if fetch fails.
        """
        if not url:
            return None

        path = self._cache_path(url, namespace)
        if os.path.exists(path):
            im = self._open_rgb_copy_from_path(path)
            if im is not None:
                return im

        # Not cached or failed to open: fetch and cache
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            # Save raw bytes to cache path
            with open(path, "wb") as f:
                f.write(r.content)
            # Open from bytes (no lingering file handle)
            with Image.open(BytesIO(r.content)) as im:
                return im.convert("RGB").copy()
        except Exception:
            return None

    # ── Collage generator ────────────────────────────────────────────────────
    def generate_collage(
        self,
        ranks: Dict[str, str],
        urls: Dict[str, str],
        titles: Dict[str, str],
        cache_namespace: Optional[str] = None,
    ) -> BytesIO:
        """
        ranks:  {book_id: tier_key}
        urls:   {book_id: cover_url}
        titles: {book_id: title}
        cache_namespace: a per-user/tenant ID for isolated disk cache
        """
        # Order and visual config
        TIERS = [
            ("S",   "Glorious Pantheon of Literary Deities", (234, 149, 148)),
            ("A",   "Champions of the Grand Narrative",      (68,  68,  68 )),
            ("B",   "Worthy Contenders for the Hero’s Feast",(232, 213, 141)),
            ("C",   "Respectable Denizens of the Mid-Levels",(243, 236, 168)),
            ("D",   "Shaky Survivors of Chapter 3",          (214, 236, 163)),
            ("F",   "Fodder for the Slush Pile Golems",      (214, 236, 163)),
            ("DNF", "Vanquished by the Reader’s Apathy",     (230, 230, 230)),
            ("ITP", "Cast Screaming Into the Pit",           (210, 210, 210)),
        ]

        # Bucket by tier
        buckets = {key: [] for key, *_ in TIERS}
        for book_id, tier in ranks.items():
            t = (tier or "").upper()
            buckets.get(t if t in buckets else "F").append(book_id)

        # Layout constants
        CANVAS_W = 1920
        MARGIN   = 24
        ROW_GAP  = 16
        LABEL_W  = 220
        TILE_H   = 180
        TILE_GAP = 10

        # Fonts
        try:
            font_label = ImageFont.truetype("arial.ttf", 36)
            font_small = ImageFont.truetype("arial.ttf", 18)
        except Exception:
            font_label = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # Helper to estimate row height based on how many tiles fit
        def row_height(n_tiles: int) -> int:
            if n_tiles == 0:
                return TILE_H
            usable_w = CANVAS_W - 2 * MARGIN - LABEL_W
            approx_tile_w = int(TILE_H * 0.66) + TILE_GAP  # rough average aspect ratio
            per_row = max(1, usable_w // approx_tile_w)
            rows = (n_tiles + per_row - 1) // per_row
            return rows * (TILE_H + TILE_GAP) - TILE_GAP

        total_h = 2 * MARGIN + sum(row_height(len(buckets[tier])) + ROW_GAP for tier, *_ in TIERS) - ROW_GAP
        img = Image.new("RGB", (CANVAS_W, total_h), (20, 20, 20))
        draw = ImageDraw.Draw(img)

        y = MARGIN
        for tier_key, label, color in TIERS:
            # Label background block
            draw.rectangle([MARGIN, y, MARGIN + LABEL_W, y + TILE_H], fill=color)

            # Label text
            tx = MARGIN + 12
            ty = y + (TILE_H - 24) // 2
            draw.text((tx, ty), label, fill=(0, 0, 0), font=font_label)

            # Grid positions
            x0 = MARGIN + LABEL_W + TILE_GAP
            x, row_y = x0, y
            usable_w = CANVAS_W - MARGIN - x0
            per_row = max(1, usable_w // (int(TILE_H * 0.66) + TILE_GAP))

            count = 0
            for book_id in buckets[tier_key]:
                cover = self.fetch_cover(urls.get(book_id), namespace=cache_namespace)
                if cover is None:
                    # Fallback: draw a block with wrapped title
                    w_guess = int(TILE_H * 0.66)
                    block = Image.new("RGB", (w_guess, TILE_H), (40, 40, 40))
                    d2 = ImageDraw.Draw(block)
                    title = titles.get(book_id, book_id)
                    lines = textwrap.wrap(title or "", width=16)
                    ty2 = 8
                    for ln in lines[:6]:
                        d2.text((6, ty2), ln, fill=(230, 230, 230), font=font_small)
                        ty2 += 20
                    tile = block
                else:
                    # Scale to fixed height, keep aspect ratio
                    w, h = cover.size
                    scale = TILE_H / float(h)
                    tile = cover.resize((max(1, int(w * scale)), TILE_H))

                # Wrap to next row when needed
                if count and (x + tile.size[0] > CANVAS_W - MARGIN):
                    x = x0
                    row_y += TILE_H + TILE_GAP

                img.paste(tile, (x, row_y))
                x += tile.size[0] + TILE_GAP
                count += 1

            # Advance to next tier row
            y += row_height(len(buckets[tier_key])) + ROW_GAP

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
