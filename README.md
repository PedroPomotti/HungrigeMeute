# Lunch menu scraper — Zurich Airport / The Circle

Renders six lunch sources in a headless browser once per weekday, extracts
**today's** menu, and writes `menus.json` to this repo. Because the file lives on
`raw.githubusercontent.com`, it can be read fully automatically by any downstream
consumer (including Claude) with no screenshots and no manual steps.

## Restaurants & sources

| id           | name        | source                          | how it's read |
|--------------|-------------|---------------------------------|---------------|
| `stopover`   | Stopover    | sv-gastronomie.ch (Firestore/qnips) | JS render + structured `EXT/INT` parser |
| `chreis14`   | Chreis 14   | sv-gastronomie.ch (Firestore/qnips) | JS render + structured `EXT/INT` parser |
| `air`        | AIR         | air-zrh.ch                      | render + best-effort + `raw_text` |
| `babel`      | Babel       | hyattrestaurants.com            | render + best-effort + `raw_text` |
| `zoom`       | ZOOM        | hyattrestaurants.com            | render + best-effort + `raw_text` |
| `leons_loft` | Leon's Loft | leonsloft.ch                    | render + best-effort + `raw_text` |

The two SV restaurants get a **fully structured** parse (category, dish,
description, EXT + INT price) — validated against real page data. The other four
are server-rendered; they get a best-effort parse **plus** the complete rendered
menu text in `raw_text`, which is the reliable source for formatting.

## Setup (one time)

1. Create a new GitHub repo and copy these files into it:
   ```
   scraper.py
   requirements.txt
   test_parser.py
   .github/workflows/menus.yml
   ```
2. Push to the `main` branch.
3. In the repo: **Settings → Actions → General → Workflow permissions →**
   select **Read and write permissions** (lets the action commit `menus.json`).
4. Go to the **Actions** tab → select **Scrape lunch menus** → **Run workflow**
   to trigger the first run manually. After that it runs automatically on the
   cron schedule (weekdays, 05:30 UTC).

Your menu file will then live at:
```
https://raw.githubusercontent.com/<user>/<repo>/main/menus.json
```
Give me that URL and I'll read it every day on request.

## Output format

```json
{
  "generated_at": "2026-06-23T07:30:05+02:00",
  "date": "2026-06-23",
  "weekday": "Dienstag",
  "timezone": "Europe/Zurich",
  "restaurants": [
    {
      "id": "stopover",
      "name": "Stopover",
      "url": "https://sv-gastronomie.ch/...",
      "status": "ok",
      "error": null,
      "items": [
        { "category": "Chefs Choice", "name": "Gebackene Pouletbrust",
          "description": "Kräuterbutter, Schupfnudeln, ...",
          "price_ext": 20.0, "price_int": 18.0 }
      ],
      "raw_text": "full rendered menu text ..."
    }
  ]
}
```

`status` is `ok`, `empty`, or `error`; on `error` the `error` field holds the
reason and other restaurants are unaffected (each is scraped independently).

## Run locally

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python scraper.py        # writes menus.json
python test_parser.py    # validates the SV parser
```

## Tuning notes

- **SV parser** (`parse_sv`) is the robust one and needs no tuning.
- **Generic parser** (`parse_generic`) is intentionally simple. If you want
  fully structured output for AIR/Babel/ZOOM/Leon's too, inspect each page's
  DOM and give the menu container a CSS selector — but the `raw_text` field
  already makes the daily menu usable as-is.
- **Leon's Loft** disallows simple bots via robots.txt; a real browser render
  works. Use it for your own personal/non-commercial menu lookups.
- **Day selection**: the scraper sets locale `de-CH` and timezone
  `Europe/Zurich`, computes the Swiss date, and clicks today's chip on the SV
  pages so the correct day is captured even around midnight / in CI (UTC).
