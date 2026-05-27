"""
BookMyShow movie search → theatre listing → seat-map URL resolver.

Flow:
  1. Navigate to BMS city page, search for movie name
  2. Extract Event Code (ET00XXXXXX) from search results
  3. Navigate to listings page for that event + city
  4. Parse all venue + timing cards → extract Session IDs
  5. Build seat-map URLs for each timing slot
  6. Return JSON: { event_code, movie_title, theatres: [{name, timings: [{time, session_id, seat_map_url}]}] }
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# BMS city codes for supported cities
CITY_CODES: Dict[str, str] = {
    "hyderabad": "hyd",
    "mumbai": "mum",
    "delhi": "ncr",
    "bangalore": "bang",
    "chennai": "che",
    "kolkata": "kol",
    "pune": "pun",
    "ahmedabad": "ahm",
}

# Regex to find Event Code anywhere in a URL or text
_EC_RE = re.compile(r"(ET\d{8})", re.I)

# Seat-map URL template
# https://in.bookmyshow.com/movies/{city}/seat-layout/{event}/{venue}/{session}/{date}
_SEAT_MAP_RE = re.compile(
    r"/movies/([a-z]+)/seat-layout/(ET\d{8})/([A-Z0-9]+)/(\d+)/(\d{8})",
    re.I,
)


class BMSSearch:
    """
    Resolves a movie name + city into a list of theatre+timing seat-map URLs.
    Uses a shared BookMyShowScraper context (already initialised by PlaywrightRunner).
    """

    def __init__(self, context) -> None:
        """
        Args:
            context: a Playwright BrowserContext (from BookMyShowScraper.context)
        """
        self._context = context

    # ── public API ────────────────────────────────────────────────────────────

    async def search(self, movie_name: str, city: str) -> Dict[str, Any]:
        """Return theatre/timing data for *movie_name* in *city*.

        Returns:
            {
              "event_code": "ET00XXXXXX",
              "movie_title": "...",
              "theatres": [
                {
                  "name": "PVR",
                  "venue_code": "PVRA",
                  "timings": [
                    {"time": "10:00 AM", "session_id": "595",
                     "date": "20260412",
                     "seat_map_url": "https://..."}
                  ]
                }
              ]
            }
        """
        city_code = CITY_CODES.get(city.lower().strip(), "hyd")
        page = await self._context.new_page()
        page.set_default_timeout(25000)
        try:
            event_code, movie_title = await self._find_event_code(page, movie_name, city_code)
            if not event_code:
                return {"error": f"Movie '{movie_name}' not found on BMS", "theatres": []}

            theatres = await self._get_listings(page, event_code, city_code)
            return {
                "event_code": event_code,
                "movie_title": movie_title or movie_name,
                "theatres": theatres,
            }
        except Exception as exc:
            logger.error("BMSSearch.search failed: %s", exc)
            return {"error": str(exc), "theatres": []}
        finally:
            await page.close()

    # ── internal steps ────────────────────────────────────────────────────────

    async def _find_event_code(
        self, page: Page, movie_name: str, city_code: str
    ) -> tuple[str, str]:
        """Try multiple strategies to find the BMS Event Code for the movie."""

        # Strategy A: city landing page + search bar
        try:
            await page.goto(
                f"https://in.bookmyshow.com/{city_code}",
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(2)

            for sel in [
                'input[placeholder*="Search"]',
                'input[placeholder*="search"]',
                '[data-testid="search-input"]',
                ".search-input input",
                "#search",
            ]:
                locator = page.locator(sel)
                if await locator.count():
                    await locator.first.fill(movie_name)
                    await asyncio.sleep(2)
                    break

            html = await page.content()
            ec, title = self._extract_event_code(html)
            if ec:
                return ec, title
        except Exception as exc:
            logger.debug("Strategy A failed: %s", exc)

        # Strategy B: BMS search results page
        try:
            query = movie_name.replace(" ", "+")
            await page.goto(
                f"https://in.bookmyshow.com/search?q={query}&searchCategory=MOVIES",
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(2)
            html = await page.content()
            ec, title = self._extract_event_code(html)
            if ec:
                return ec, title
        except Exception as exc:
            logger.debug("Strategy B failed: %s", exc)

        return "", ""

    def _extract_event_code(self, html: str) -> tuple[str, str]:
        """Scan raw HTML for an ET event code in href attributes."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("a", href=True):
            m = _EC_RE.search(tag["href"])
            if m:
                title = tag.get_text(strip=True)
                return m.group(1).upper(), title
        # Fallback: plain text scan
        m = _EC_RE.search(html)
        return (m.group(1).upper(), "") if m else ("", "")

    async def _get_listings(
        self, page: Page, event_code: str, city_code: str
    ) -> List[Dict[str, Any]]:
        """Navigate to the listings page and extract theatre + timing cards."""
        url = (
            f"https://in.bookmyshow.com/movies/{city_code}"
            f"/book-tickets/{event_code}/movies"
        )
        try:
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await asyncio.sleep(3)
        except Exception as exc:
            logger.warning("Listings page load failed: %s", exc)
            return []

        html = await page.content()
        return self._parse_listings(html, event_code, city_code)

    def _parse_listings(
        self, html: str, event_code: str, city_code: str
    ) -> List[Dict[str, Any]]:
        """Extract seat-map links from the listings HTML."""
        soup = BeautifulSoup(html, "lxml")
        theatre_map: Dict[str, Dict[str, Any]] = {}

        pattern = re.compile(
            rf"/movies/{re.escape(city_code)}/seat-layout/"
            rf"{re.escape(event_code)}/([A-Z0-9]+)/(\d+)/(\d{{8}})",
            re.I,
        )

        for link in soup.find_all("a", href=True):
            m = pattern.search(link["href"])
            if not m:
                continue

            venue_code, session_id, date = m.group(1), m.group(2), m.group(3)
            seat_map_url = (
                f"https://in.bookmyshow.com/movies/{city_code}/seat-layout/"
                f"{event_code}/{venue_code}/{session_id}/{date}"
            )

            venue_name = self._find_venue_name(link, soup) or venue_code
            time_text = self._find_time_text(link) or session_id

            if venue_name not in theatre_map:
                theatre_map[venue_name] = {
                    "name": venue_name,
                    "venue_code": venue_code,
                    "timings": [],
                }
            # Deduplicate by session_id
            existing_ids = {t["session_id"] for t in theatre_map[venue_name]["timings"]}
            if session_id not in existing_ids:
                theatre_map[venue_name]["timings"].append(
                    {
                        "time": time_text,
                        "session_id": session_id,
                        "date": date,
                        "seat_map_url": seat_map_url,
                    }
                )

        return list(theatre_map.values())

    # ── HTML helpers ──────────────────────────────────────────────────────────

    def _find_venue_name(self, link, soup) -> str:
        """Walk up the DOM tree looking for a heading that names the venue."""
        el = link.parent
        for _ in range(6):
            if el is None or el.name in ("html", "body"):
                break
            for tag in ("h2", "h3", "h4", "strong", "b", "span"):
                heading = el.find(tag)
                if heading:
                    text = heading.get_text(strip=True)
                    if 3 < len(text) < 120 and not re.match(r"^\d", text):
                        return text
            el = el.parent
        return ""

    def _find_time_text(self, link) -> str:
        """Extract the human-readable show time from a timing link."""
        text = link.get_text(strip=True)
        # Prefer text that looks like a time  e.g. "10:30 AM"
        if re.search(r"\d{1,2}:\d{2}", text):
            return text
        # Check aria-label / title attributes
        for attr in ("aria-label", "title", "data-time"):
            val = link.get(attr, "")
            if val and re.search(r"\d{1,2}:\d{2}", val):
                return val
        return text
