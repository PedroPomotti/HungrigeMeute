#!/usr/bin/env python3
"""
Menu scraper for six lunch restaurants around Zurich Airport / The Circle.

Renders each source in a real (headless) Chromium browser via Playwright so that
JavaScript-only pages (sv-gastronomie.ch -> Firestore/qnips, leonsloft.ch) are
fully loaded, extracts *today's* menu, and writes everything to menus.json.

Each restaurant entry always includes the full rendered `raw_text`, so even if a
structured parser misses something the day's menu is never lost.

Per-site parsers (validated against real page data):
  stopover, chreis14 -> parse_sv     (SV "EXT/INT CHF"; INT optional)
  air                -> parse_air     (bare-number prices, daily specials)
  babel              -> parse_hyatt   (full lunch card; "NUM" + "CHF")
  zoom               -> parse_zoom    (weekly-vegan + today's weekday hit)
  leons_loft         -> raw_text only (dishes live in an image/PDF, not in HTML)
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

TZ = ZoneInfo("Europe/Zurich")
WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
               "Samstag", "Sonntag"]
WEEKDAYS_DE_UPPER = [w.upper() for w in WEEKDAYS_DE]
WEEKDAYS_ABBR = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

RESTAURANTS = [
    {"id": "stopover",   "name": "Stopover",    "type": "sv",
     "url": "https://sv-gastronomie.ch/menu/Flughafen%20AG,%20Stopover,%20Z%C3%BCrich/Mittagsmen%C3%BC"},
    {"id": "chreis14",   "name": "Chreis 14",   "type": "sv",
     "url": "https://sv-gastronomie.ch/menu/Chreis%2014,%20Z%C3%BCrich/Mittagsmen%C3%BC"},
    {"id": "air",        "name": "AIR",         "type": "air",
     "url": "https://air-zrh.ch/"},
    {"id": "babel",      "name": "Babel",       "type": "hyatt",
     "url": "https://www.hyattrestaurants.com/de/zurich-airport/restaurant/babel-restaurant-zurich-the-circle/menu/dfdaf9ad-21b9-407d-b99c-3f957ef14476"},
    {"id": "zoom",       "name": "ZOOM",        "type": "zoom",
     "url": "https://www.hyattrestaurants.com/de/zurich-airport/restaurant/zoom-restaurant-the-circle-zurich-airport/menu/de7208f9-a9c9-4c4b-96df-82777daffb54"},
    {"id": "leons_loft", "name": "Leon's Loft", "type": "leons",
     "url": "https://www.leonsloft.ch/lunch-dine/"},
]

# ----------------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------------
SV_PRICE_RE = re.compile(r'(EXT|INT)\s*CHF\s*(\d+[.,]\d{2})', re.I)
BARE_PRICE = re.compile(r'^\d{1,3}\.\d{2}$')
SV_NOISE = {"mittagsmenü", "diese woche", "nächste woche", "letzte woche",
            "menüplan", "sv restaurant", "standorte", "informationen",
            "account_circle", "de", "filter", "catering", "menu", "menü",
            "standort", "clear", "mehr erfahren"}
DAY_CHIP = re.compile(r'^(mo|di|mi|do|fr|sa|so)\.?$', re.I)
DATE_CHIP = re.compile(r'^\d{1,2}\.\d{1,2}\.?$')


def _f(s: str) -> float:
    return float(s.replace(",", "."))


# ----------------------------------------------------------------------------
# SV (Stopover, Chreis 14)
# ----------------------------------------------------------------------------
def _clean_sv(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s in ("1", "2", "3"):
            continue
        if s.lower() in SV_NOISE:
            continue
        if DAY_CHIP.match(s) or DATE_CHIP.match(s):
            continue
        out.append(s)
    return out


def _is_header(l: str) -> bool:
    return (len(l) <= 32 and "," not in l and "|" not in l
            and "chf" not in l.lower() and not re.search(r"\d", l))


def parse_sv(text: str) -> list[dict]:
    """SV price-anchored parser. Handles EXT+INT (Stopover) and EXT-only (Chreis 14)."""
    lines = _clean_sv(text)
    items: list[dict] = []
    buf: list[str] = []
    cat = ""
    i = 0
    while i < len(lines):
        m = SV_PRICE_RE.search(lines[i])
        if m and m.group(1).upper() == "EXT":
            ext = _f(m.group(2))
            intval = None
            if i + 1 < len(lines):
                m2 = SV_PRICE_RE.search(lines[i + 1])
                if m2 and m2.group(1).upper() == "INT":
                    intval = _f(m2.group(2))
                    i += 1
            block = [b for b in buf if not SV_PRICE_RE.search(b)]
            c, name, desc = cat, "", ""
            if len(block) >= 3 and _is_header(block[-3]):
                c = block[-3]; cat = c; name = block[-2]; desc = block[-1]
            elif len(block) >= 2:
                name, desc = block[-2], block[-1]
            elif len(block) == 1:
                name = block[-1]
            if name:
                items.append({"category": c, "name": name, "description": desc,
                              "price_ext": ext, "price_int": intval})
            buf = []
        elif not (m and m.group(1).upper() == "INT"):
            buf.append(lines[i])
        i += 1
    return items


# ----------------------------------------------------------------------------
# AIR -- bare-number prices; daily specials sit under fixed headers
# ----------------------------------------------------------------------------
AIR_DAILY_HEADERS = ["PIZZA OF THE WEEK", "Daily Menu", "Daily Grill",
                     "Daily Wok", "Daily Vegi"]


def parse_air(text: str) -> list[dict]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    items, seen = [], set()
    for i, l in enumerate(lines):
        if l in AIR_DAILY_HEADERS:
            name = lines[i + 1] if i + 1 < len(lines) else ""
            price = None
            for j in (i + 2, i + 3):
                if j < len(lines) and BARE_PRICE.match(lines[j]):
                    price = _f(lines[j]); break
            key = (l, name)
            if name and key not in seen:
                seen.add(key)
                items.append({"category": l, "name": name,
                              "description": "", "price": price})
    return items


# ----------------------------------------------------------------------------
# Hyatt generic (Babel) -- pattern: NUM line then 'CHF' line
# ----------------------------------------------------------------------------
def parse_hyatt(text: str) -> list[dict]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    items, i = [], 0
    while i < len(lines):
        if BARE_PRICE.match(lines[i]) and i + 1 < len(lines) and lines[i + 1].upper() == "CHF":
            price = _f(lines[i])
            texts, k = [], i - 1
            while k >= 0 and len(texts) < 2 and not (BARE_PRICE.match(lines[k]) or lines[k].upper() == "CHF"):
                texts.append(lines[k]); k -= 1
            texts.reverse()
            name = texts[0] if texts else ""
            desc = texts[1] if len(texts) >= 2 else ""
            if name:
                items.append({"category": "", "name": name,
                              "description": desc, "price": price})
            i += 2
            continue
        i += 1
    return items


# ----------------------------------------------------------------------------
# ZOOM -- weekly-vegan + today's weekday hit (with optional "+ salad" price)
# ----------------------------------------------------------------------------
def _zoom_block(seg: list[str], category: str) -> dict | None:
    name = seg[0] if seg else ""
    desc = seg[1] if len(seg) >= 2 else ""
    prices = []
    salad_price = None
    for j, s in enumerate(seg):
        if BARE_PRICE.match(s):
            val = _f(s)
            prev = seg[j - 1].lower() if j > 0 else ""
            if "salat" in prev or "salad" in prev:
                salad_price = val
            else:
                prices.append(val)
    if not name:
        return None
    return {"category": category, "name": name, "description": desc,
            "price": prices[0] if prices else None,
            "price_with_salad": salad_price}


def parse_zoom(text: str, today: dt.date | None = None) -> list[dict]:
    today = today or dt.datetime.now(TZ).date()
    wd = WEEKDAYS_DE_UPPER[today.weekday()]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    items = []
    for i, l in enumerate(lines):
        up = l.upper()
        if up.startswith("WÖCHENTLICH"):
            blk = _zoom_block(lines[i + 1:i + 8], "Wöchentlich vegan")
            if blk:
                items.append(blk)
        elif up.startswith(wd):
            blk = _zoom_block(lines[i + 1:i + 8], f"Tageshit {l}")
            if blk:
                items.append(blk)
    return items


# ----------------------------------------------------------------------------
# Leon's Loft -- weekly menu lives in a PDF linked from the site (DE + EN)
# ----------------------------------------------------------------------------
LEONS_SECTIONS = {"weekly-lunch", "salad & bowls", "soup", "sommerlich leicht",
                  "burger", "more nice bites", "dessert", "season & more",
                  "vegetarisch", "fleisch", "fisch", "meat", "pasta", "classic"}
LEONS_NOISE_RE = re.compile(r'lunch\s*[&-]?\s*dine|the circle|mail@|leonsloft|'
                            r'alle preise|add on|translated with|'
                            r'woche\s*\d|week\s*\d', re.I)
LEONS_PRICE_TAIL = re.compile(
    r'^(.*?)\s+(\d{1,3}(?:[.,]\d{1,2})?(?:\s*\|\s*\d{1,3}(?:[.,]\d{1,2})?)?)\s*$')
PDF_LINK_JS = """() => {
  const a = [...document.querySelectorAll('a')].map(x => x.href)
    .find(h => /Lunch[_-]?Dine.*_DE\\.pdf/i.test(h));
  return a || null;
}"""


def extract_pdf_text(data: bytes) -> str:
    import pdfplumber  # imported lazily so non-PDF runs don't need it
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out)


def parse_leons(text: str) -> list[dict]:
    items, cat = [], ""
    for ln in text.splitlines():
        s = ln.strip()
        if not s or LEONS_NOISE_RE.search(s):
            continue
        if s.lower() in LEONS_SECTIONS:
            cat = s
            continue
        m = LEONS_PRICE_TAIL.match(s)
        if m and len(m.group(1)) > 2 and not m.group(1).lower() in LEONS_SECTIONS:
            items.append({"category": cat, "name": m.group(1).strip(),
                          "description": "", "price_text": m.group(2).strip()})
    return items


PARSERS = {
    "sv": lambda t, today: parse_sv(t),
    "air": lambda t, today: parse_air(t),
    "hyatt": lambda t, today: parse_hyatt(t),
    "zoom": lambda t, today: parse_zoom(t, today),
    "leons": lambda t, today: parse_leons(t),
}


# ----------------------------------------------------------------------------
# scraping
# ----------------------------------------------------------------------------
def wait_for_menu(page, timeout_ms: int = 25000) -> None:
    try:
        page.get_by_text(re.compile(r"CHF|\d{2}\.\d{2}")).first.wait_for(timeout=timeout_ms)
    except PWTimeout:
        page.wait_for_timeout(3000)


def select_today_sv(page, today: dt.date) -> None:
    ddmm = f"{today.day:02d}.{today.month:02d}"
    abbr = WEEKDAYS_ABBR[today.weekday()]
    for needle in (f"{abbr} {ddmm}", f"{abbr}.", ddmm):
        try:
            page.get_by_text(re.compile(re.escape(needle))).first.click(timeout=2500)
            page.wait_for_timeout(800)
            return
        except Exception:
            continue


def scrape_one(context, r: dict, today: dt.date) -> dict:
    res = {"id": r["id"], "name": r["name"], "url": r["url"],
           "status": "ok", "error": None, "items": [], "raw_text": ""}
    page = context.new_page()
    try:
        if r["type"] == "leons":
            return scrape_leons(context, page, r, res)
        page.goto(r["url"], wait_until="domcontentloaded", timeout=45000)
        wait_for_menu(page)
        if r["type"] == "sv":
            select_today_sv(page, today)
            wait_for_menu(page, timeout_ms=8000)
        raw = (page.evaluate("() => document.body.innerText") or "").strip()
        res["raw_text"] = raw
        res["items"] = PARSERS.get(r["type"], lambda t, d: [])(raw, today)
        if not res["items"] and not raw:
            res["status"] = "empty"
        elif not res["items"]:
            res["status"] = "raw_only"
    except Exception as exc:  # noqa: BLE001
        res["status"] = "error"
        res["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        page.close()
    return res


def scrape_leons(context, page, r: dict, res: dict) -> dict:
    """Find the current week's German PDF on the site and extract its text.
    A real browser request bypasses the robots block that stops simple fetchers;
    use only for personal, non-commercial menu lookups."""
    try:
        # the weekly PDF is linked from the front page; lunch-dine page is a backup
        href = None
        for url in ("https://www.leonsloft.ch/", r["url"]):
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            href = page.evaluate(PDF_LINK_JS)
            if href:
                break
        if not href:
            res["status"] = "error"
            res["error"] = "DE weekly PDF link not found on site"
            return res
        res["pdf_url"] = href
        resp = context.request.get(href, timeout=45000)
        if not resp.ok:
            res["status"] = "error"
            res["error"] = f"PDF download HTTP {resp.status}"
            return res
        text = extract_pdf_text(resp.body())
        res["raw_text"] = text.strip()
        res["items"] = parse_leons(text)
        if not res["items"] and not res["raw_text"]:
            res["status"] = "empty"
        elif not res["items"]:
            res["status"] = "raw_only"
    except Exception as exc:  # noqa: BLE001
        res["status"] = "error"
        res["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        page.close()
    return res


def main() -> int:
    now = dt.datetime.now(TZ)
    today = now.date()
    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "weekday": WEEKDAYS_DE[today.weekday()],
        "timezone": "Europe/Zurich",
        "restaurants": [],
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="de-CH", timezone_id="Europe/Zurich",
            user_agent=UA, viewport={"width": 1440, "height": 1000})
        context.set_default_timeout(45000)
        for r in RESTAURANTS:
            print(f"-> scraping {r['name']} ...", file=sys.stderr)
            out["restaurants"].append(scrape_one(context, r, today))
        browser.close()
    with open("menus.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    ok = sum(1 for x in out["restaurants"] if x["status"] in ("ok",))
    print(f"done: {ok}/{len(RESTAURANTS)} with structured items", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
