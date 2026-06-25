"""Self-contained tests for the per-site parsers (no network needed).
Samples are trimmed from real rendered page / PDF text."""
import datetime as dt

from scraper import parse_sv, parse_air, parse_hyatt, parse_zoom, parse_leons

STOPOVER = """Mittagsmenü
Diese Woche
Mo 22.06.
Chefs Choice
Rind Fajita
Peperoni, Guacamole, Cheddarkäse
EXT  CHF 20.00
INT  CHF 18.00
Veggie
Gelbes Linsen Dal
Kokosmilch, Lauch, Karotten
EXT  CHF 16.00
INT  CHF 13.90
Impressum"""

CHREIS14 = """SV RESTAURANT
Menüplan
Mo.
22.06.
Pure & Whole
Poutine
Pulled Pork, BBQ Sauce, Cole Slaw, rote Zwiebeln, Pommes Frites
EXT  CHF 18.80
Heart & Soul
Black Pepper Chicken
Kräuterbutter, Eierknöpfli, Grillgemüse
EXT  CHF 21.20
Filter"""

AIR = """Stand: 25.06.2026 08:04
PIZZA OF THE WEEK
Quattro Formaggi
22.90
Daily Menu
Pouletbrust mit Pfeffersauce
21.90
Daily Vegi
Fitness-Bowl mit Quinoa, Avocado und Himbeeren
20.90"""

BABEL = """SALATE
Fattoush Salat
Tomaten, Gurke, Radieschen, Sumach-Gewürz
19.00
CHF"""

ZOOM = """WÖCHENTLICH VEGAN
planted caccitore
paprika, tomaten, kapern, oliven
25.00
CHF
mit Salat
29.00
CHF
DONNERSTAG, 25.06.
schweinsfilet medaillons
risotto, grüner spargel, parmesan
25.00
CHF
mit Salat
29.00
CHF"""

LEONS = """Lunch & Dine Woche 26
Weekly-Lunch
Burrata-Ravioli 26
Salad & Bowls
Caesar Salad 19
Soup
Tagessuppe 12.5
Alle Preise in CHF inkl. MwSt."""

LEONS_TRICKY = """More nice Bites
Tagliata 39
Leon's Tatar
klein | gross 29 | 35
Dessert
Panna Cotta mit
Aprikosen-Kompott 10.5
Soup
Small Green Salad 9.5"""


def test_sv_ext_int():
    it = parse_sv(STOPOVER)
    assert len(it) == 2, it
    assert it[0]["name"] == "Rind Fajita"
    assert it[0]["price_ext"] == 20.0 and it[0]["price_int"] == 18.0
    assert it[1]["category"] == "Veggie" and it[1]["price_int"] == 13.9


def test_sv_ext_only():
    it = parse_sv(CHREIS14)
    assert len(it) == 2, it
    assert it[0]["category"] == "Pure & Whole" and it[0]["name"] == "Poutine"
    assert it[0]["price_ext"] == 18.8 and it[0]["price_int"] is None
    assert it[1]["name"] == "Black Pepper Chicken"


def test_air():
    it = parse_air(AIR)
    assert len(it) == 3, it
    assert it[0]["name"] == "Quattro Formaggi" and it[0]["price"] == 22.9
    assert it[2]["category"] == "Daily Vegi"


def test_hyatt():
    it = parse_hyatt(BABEL)
    assert len(it) == 1 and it[0]["name"] == "Fattoush Salat"
    assert it[0]["price"] == 19.0


def test_zoom_today():
    it = parse_zoom(ZOOM, dt.date(2026, 6, 25))
    assert len(it) == 2, it
    assert it[1]["name"] == "schweinsfilet medaillons"
    assert it[1]["price"] == 25.0 and it[1]["price_with_salad"] == 29.0


def test_leons():
    it = parse_leons(LEONS)
    names = [x["name"] for x in it]
    assert "Lunch & Dine Woche" not in names
    assert it[0]["category"] == "Weekly-Lunch" and it[0]["name"] == "Burrata-Ravioli"
    assert any(x["name"] == "Tagessuppe" and x["price_text"] == "12.5" for x in it)


def test_leons_size_and_continuation():
    it = parse_leons(LEONS_TRICKY)
    names = [x["name"] for x in it]
    assert "Leon's Tatar klein | gross" in names
    assert "Panna Cotta mit Aprikosen-Kompott" in names
    assert "Small Green Salad" in names
    assert not any(n.startswith("mit Brot") for n in names)


if __name__ == "__main__":
    for fn in (test_sv_ext_int, test_sv_ext_only, test_air, test_hyatt,
               test_zoom_today, test_leons, test_leons_size_and_continuation):
        fn()
        print(f"{fn.__name__} OK")
    print("ALL TESTS PASSED")
