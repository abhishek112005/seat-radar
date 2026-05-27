"""
Self-healing CSS selector recovery using sentence-transformers + BeautifulSoup.

When the seat scraper returns empty results, SelfHealingSelector re-analyses the
raw page HTML semantically to locate seat-like elements and saves the recovered
selector patterns to scraper/selectors.json for future runs.

No external API calls — runs fully locally with the all-MiniLM-L6-v2 model.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_SELECTORS_PATH = Path("scraper/selectors.json")

# Phrases that describe available seats — used as the embedding target
_SEAT_PHRASES = [
    "available seat",
    "select seat",
    "book this seat",
    "seat number",
    "open seat",
    "choose seat",
]

# Phrases for section headings
_SECTION_PHRASES = [
    "gold section",
    "platinum section",
    "premium seating area",
    "director choice",
    "balcony section",
    "seating category",
]


class SelfHealingSelector:
    """
    Uses sentence-transformers to locate seat/section elements semantically.

    Usage:
        healer = SelfHealingSelector()
        elements = healer.find_seats(page_html)   # list of dicts with selector, text, etc.
        healer.save("available_seat", elements[0]["selector"])
    """

    def __init__(self, selectors_path: str = str(_SELECTORS_PATH)) -> None:
        self._path = Path(selectors_path)
        self._saved: Dict[str, str] = self._load()
        self._model = None  # lazy — only loaded when actually needed

    # ── public ────────────────────────────────────────────────────────────────

    def find_seats(self, html: str, top_k: int = 20) -> List[Dict]:
        """Return up to *top_k* seat-like elements ranked by semantic similarity."""
        return self._find(html, _SEAT_PHRASES, "seat_layout", top_k)

    def find_sections(self, html: str, top_k: int = 10) -> List[Dict]:
        """Return up to *top_k* section-heading elements."""
        return self._find(html, _SECTION_PHRASES, "section_label", top_k)

    def get(self, context: str) -> Optional[str]:
        """Return the last saved CSS selector for *context*, or None."""
        return self._saved.get(context)

    def save(self, context: str, selector: str) -> None:
        """Persist a healed selector for *context*."""
        self._saved[context] = selector
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._saved, fh, indent=2)
        logger.info("Self-healing saved selector [%s] = %s", context, selector)

    # ── internals ─────────────────────────────────────────────────────────────

    def _find(
        self, html: str, target_phrases: List[str], context: str, top_k: int
    ) -> List[Dict]:
        candidates = self._extract_candidates(html)
        if not candidates:
            return []

        self._ensure_model()

        texts = [f"{c['text']} {c['class_str']} {c['aria']}" for c in candidates]
        all_texts = texts + target_phrases
        try:
            all_emb = self._model.encode(all_texts, show_progress_bar=False)
        except Exception as exc:
            logger.warning("sentence-transformers encode failed: %s", exc)
            return []

        cand_emb = all_emb[: len(candidates)]
        target_emb = np.mean(all_emb[len(candidates) :], axis=0)

        # Cosine similarity
        cand_norm = cand_emb / (np.linalg.norm(cand_emb, axis=1, keepdims=True) + 1e-9)
        t_norm = target_emb / (np.linalg.norm(target_emb) + 1e-9)
        sims = cand_norm @ t_norm

        threshold = 0.25
        ranked = sorted(
            [(float(s), c) for s, c in zip(sims, candidates) if float(s) >= threshold],
            key=lambda x: x[0],
            reverse=True,
        )[:top_k]

        results = [{**c, "similarity": s} for s, c in ranked]

        if results:
            best = results[0]["selector"]
            self.save(context, best)

        return results

    def _extract_candidates(self, html: str) -> List[Dict]:
        soup = BeautifulSoup(html, "lxml")
        candidates = []
        seen_selectors: set = set()

        for tag in soup.find_all(["div", "span", "button", "td", "li", "a"]):
            text = tag.get_text(strip=True)
            if not text or len(text) > 30:
                continue

            selector = self._make_selector(tag)
            if selector in seen_selectors:
                continue
            seen_selectors.add(selector)

            classes = tag.get("class") or []
            candidates.append(
                {
                    "text": text,
                    "class_str": " ".join(classes),
                    "aria": tag.get("aria-label", ""),
                    "role": tag.get("role", ""),
                    "selector": selector,
                    "tag": tag.name,
                }
            )

        return candidates

    def _make_selector(self, tag: Tag) -> str:
        classes = tag.get("class") or []
        if classes:
            return f"{tag.name}.{'.'.join(classes[:2])}"
        tag_id = tag.get("id", "")
        if tag_id:
            return f"#{tag_id}"
        role = tag.get("role", "")
        if role:
            return f'{tag.name}[role="{role}"]'
        return tag.name

    def _ensure_model(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformers model (first time may take ~10s)")
            self._model = SentenceTransformer("all-MiniLM-L6-v2")

    def _load(self) -> Dict[str, str]:
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
        return {}
