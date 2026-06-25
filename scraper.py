#!/usr/bin/env python3
"""
Menu scraper for six lunch restaurants around Zurich Airport / The Circle.

Renders each source in a real (headless) Chromium browser via Playwright so that
JavaScript-only pages (sv-gastronomie.ch -> Firestore/qnips, leonsloft.ch) are
fully loaded, extracts *today's* menu, and writes everything to menus.json.

The output always contains a `raw_text` field per restaurant, so even if the
structured parser misses something the day's menu is never lost.

Run locally:   python scraper.py
In CI:         see .github/workflows/menus.yml
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

TZ = ZoneInfo("Europe/Zurich")
WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
               "Samstag", "Sonntag"]
WEEKDAYS_ABBR = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# --- restaurant configuration -------------------------------------------------
# type "sv"      -> sv-gastronomie.ch single-page app (day tabs + EXT/INT prices)
# type "generic" -> server-rendered or simple JS page; best-effort CHF parsing
RESTAURANTS = [
    {
        "id": "stopover",
        "name": "Stopover",
        "url": "https://sv-gastronomie.ch/menu/Flughafen%20AG,%20Stopover,%20Z%C3%BCrich/Mittagsmen%C3%BC",
        "type": "sv",
    },
    {
        "id": "chreis14",
        "name": "Chreis 14",
        "url": "https://sv-gastronomie.ch/menu/Chreis%2014,%20Z%C3%BCrich/Mittagsmen%C3%BC",
        "type": "sv",
    },
    {
        "id": "air",
        "name": "AIR",
        "url": "https://air-zrh.ch/",
        "type": "generic",
    },
    {
        "id": "babel",
        "name": "Babel",
        "url": "https://www.hyattrestaurants.com/de/zurich-airport/restaurant/babel-restaurant-zurich-the-circle/menu/dfdaf9ad-21b9-407d-b99c-3f957ef14476",
        "type": "generic",
    },
    {
        "id": "zoom",
        "name": "ZOOM",
        "url": "https://www.hyattrestaurants.com/de/zurich-airport/restaurant/zoom-restaurant-the-circle-zurich-airport/menu/de7208f9-a9c9-4c4b-96df-82777daffb54",
        "type": "generic",
    },
    {
        "id": "leons_loft",
        "name": "Leon's Loft",
        "url": "https://www.leonsloft.ch/lunch-dine/",
        "type": "generic",
    },
]

# --- parsing helpers ----------------------------------------------------------
SV_PRICE_RE = re.compile(r'(EXT|INT)\s*CHF\s*(\d+[.,]\d{2})', re.IGNORECASE)
GENERIC_PRICE_RE = re.compile(r'CHF\s*(\d+[.,]\d{2})', re.IGNORECASE)
DAY_TAB_RE = re.compile(r'^(Mo|Di|Mi|Do|Fr|Sa|So)\.?\s*\d{1,2}\.\d{1,2}\.?$')
NOISE_LINES = {
    "mittagsmenü", "diese woche", "nächste woche", "letzte woche",
    "mehr erfahren", "speiseplan", "menu", "menü",
}


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def clean_lines(text: str) -> list[str]:
    """Split rendered text into trimmed, de-noised lines."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low in NOISE_LINES:
            continue
        if DAY_TAB_RE.match(line):          # weekday selector chips
            continue
        out.append(line)
    return out


def _looks_like_header(line: str) -> bool:
    """Category headers are short, comma/pipe/price free and have no digits."""
    if len(line) > 32:
        return False
    if any(c in line for c in (",", "|")):
        return False
    if "chf" in line.lower():
        return False
    if re.search(r"\d", line):
        return False
    return True


def parse_sv(text: str) -> list[dict]:
    """
    Price-anchored parser for sv-gastronomie.ch.

    The repeating unit on the page is:
        [Category]            (only when it changes)
        Dish name
        Description
        EXT  CHF xx.xx
        INT  CHF yy.yy
    We accumulate non-price lines, and on each EXT/INT pair we close an item
    using the last 1-3 buffered lines. Upstream navigation/footer text is
    discarded automatically because only the lines right before a price pair
    are consumed.
    """
    items: list[dict] = []
    buffer: list[str] = []
    current_category = ""
    pending_ext = None

    for line in clean_lines(text):
        m = SV_PRICE_RE.search(line)
        if m:
            kind = m.group(1).upper()
            value = _to_float(m.group(2))
            if kind == "EXT":
                pending_ext = value
                continue
            # kind == INT -> close the current item
            block = [b for b in buffer if not SV_PRICE_RE.search(b)]
            category = current_category
            name, description = "", ""
            if len(block) >= 3 and _looks_like_header(block[-3]):
                category = block[-3].strip()
                current_category = category
                name = block[-2].strip()
                description = block[-1].strip()
            elif len(block) >= 2:
                name = block[-2].strip()
                description = block[-1].strip()
            elif len(block) == 1:
                name = block[-1].strip()
            if name:
                items.append({
                    "category": category,
                    "name": name,
                    "description": description,
                    "price_ext": pending_ext,
                    "price_int": value,
                })
            buffer = []
            pending_ext = None
            continue
        buffer.append(line)

    return items


def parse_generic(text: str) -> list[dict]:
    """
    Best-effort parser for server-rendered pages (AIR, Babel, ZOOM, Leon's).
    Heuristic: a line containing CHF is a price; the nearest preceding
    non-price, non-trivial line is the dish name, the line before that the
    description. Tune per-site if needed -- raw_text is always kept as backup.
    """
    items: list[dict] = []
    lines = clean_lines(text)
    for i, line in enumerate(lines):
        pm = GENERIC_PRICE_RE.search(line)
        if not pm:
            continue
        price = _to_float(pm.group(1))
        # name = the price line stripped of the price, or the previous line
        name = GENERIC_PRICE_RE.sub("", line).strip(" -–·\t")
        description = ""
        if not name and i > 0:
            name = lines[i - 1].strip()
            if i > 1:
                description = lines[i - 2].strip()
        if name:
            items.append({
                "category": "",
                "name": name,
                "description": description,
                "price": price,
            })
    return items


# --- scraping -----------------------------------------------------------------
def wait_for_menu(page, timeout_ms: int = 25000) -> None:
    """Wait until menu content (a CHF price) is present. Firestore long-polls,
    so we never wait for networkidle on SV pages."""
    try:
        page.get_by_text(re.compile(r"CHF", re.I)).first.wait_for(timeout=timeout_ms)
    except PWTimeout:
        page.wait_for_timeout(3000)  # fall back to a short fixed settle


def select_today_sv(page, today: dt.date) -> None:
    """Click the weekday chip for today so the SV app shows the right day."""
    ddmm = f"{today.day:02d}.{today.month:02d}"
    abbr = WEEKDAYS_ABBR[today.weekday()]
    for needle in (f"{abbr} {ddmm}", ddmm, f"{abbr}.{today.day:02d}"):
        try:
            page.get_by_text(re.compile(re.escape(needle))).first.click(timeout=2500)
            page.wait_for_timeout(800)
            return
        except Exception:
            continue
    # default view already shows today -> nothing to do


def scrape_one(context, r: dict, today: dt.date) -> dict:
    result = {
        "id": r["id"], "name": r["name"], "url": r["url"],
        "status": "ok", "error": None, "items": [], "raw_text": "",
    }
    page = context.new_page()
    try:
        page.goto(r["url"], wait_until="domcontentloaded", timeout=45000)
        wait_for_menu(page)
        if r["type"] == "sv":
            select_today_sv(page, today)
            wait_for_menu(page, timeout_ms=8000)
        raw = page.evaluate("() => document.body.innerText") or ""
        result["raw_text"] = raw.strip()
        if r["type"] == "sv":
            result["items"] = parse_sv(raw)
        else:
            result["items"] = parse_generic(raw)
        if not result["items"] and not result["raw_text"]:
            result["status"] = "empty"
    except Exception as exc:  # noqa: BLE001 - we record every failure per site
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        page.close()
    return result


def main() -> int:
    now = dt.datetime.now(TZ)
    today = now.date()
    output = {
        "generated_at": now.isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "weekday": WEEKDAYS_DE[today.weekday()],
        "timezone": "Europe/Zurich",
        "restaurants": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="de-CH",
            timezone_id="Europe/Zurich",
            user_agent=UA,
            viewport={"width": 1440, "height": 1000},
        )
        context.set_default_timeout(45000)
        for r in RESTAURANTS:
            print(f"-> scraping {r['name']} ...", file=sys.stderr)
            output["restaurants"].append(scrape_one(context, r, today))
        browser.close()

    with open("menus.json", "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    ok = sum(1 for x in output["restaurants"] if x["status"] == "ok")
    print(f"done: {ok}/{len(RESTAURANTS)} restaurants ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
