import json
from scraper import parse_sv, parse_generic

# Reconstructed body.innerText as the SV page renders it (incl. nav/footer noise
# above and below) -- taken verbatim from the DevTools screenshot, Stopover Di 23.06.
sample = """SV
Schloss, Schiff oder doch lieber Restaurant?
Mehr erfahren
Mittagsmenü
Diese Woche
Mo 22.06.
Di 23.06.
Mi 24.06.
Do 25.06.
Fr 26.06.
Chefs Choice
Gebackene Pouletbrust
Kräuterbutter, Schupfnudeln, Kabis Randen Gemüse, Rote Zwiebeln, Ingwer
EXT  CHF 20.00
INT  CHF 18.00
Home
Ghackets und Hörnli
Rindsgehacktem, Apfelmus, Reibkäse |Tagessalat oder Tagessuppe
EXT  CHF 16.50
INT  CHF 14.80
Veggie
Süsskartoffel Curry
Kichererbsen, Blattspinat, Linsen Quinoa Basmatireis |Tagessuppe oder Tagessalat
EXT  CHF 16.00
INT  CHF 13.90
Impressum
Datenschutzerklärung"""

items = parse_sv(sample)
print(json.dumps(items, ensure_ascii=False, indent=2))
print(f"\n--> {len(items)} items parsed")

# sanity assertions
assert len(items) == 3, "expected 3 items"
assert items[0]["category"] == "Chefs Choice"
assert items[0]["name"] == "Gebackene Pouletbrust"
assert items[0]["price_ext"] == 20.00 and items[0]["price_int"] == 18.00
assert items[1]["name"] == "Ghackets und Hörnli" and items[1]["price_int"] == 14.80
assert items[2]["category"] == "Veggie" and items[2]["price_ext"] == 16.00
assert "Kichererbsen" in items[2]["description"]
print("ALL ASSERTIONS PASSED")
