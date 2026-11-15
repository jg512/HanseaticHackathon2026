"""Suchlinks, die der Anwender im eigenen Browser öffnet.

Das Backend ruft diese Adressen nicht ab: Die Hersteller sperren automatische Abrufe. Ein Mensch
öffnet den Link, lädt das Dokument von der Herstellerseite und fügt es zur Position ein; dann
läuft es durch dieselbe Vorprüfung wie jeder andere Fund.
"""

from urllib.parse import quote

from flask import current_app

from . import internet

# Suche auf der Herstellerseite, je Hersteller-Domain. Jede Adresse ist im Browser geprüft (13.11.2026).
HERSTELLERSUCHE = {
    "festo.com": "https://www.festo.com/de/de/search/?text={q}",
    "pepperl-fuchs.com": "https://www.pepperl-fuchs.com/de-de/search-overview-gp27127?q={q}",
    "siemens.com": "https://support.industry.siemens.com/cs/search?search={q}&type=Certificate&lc=de-DE",
    "rittal.com": "https://www.rittal.com/de-de/Suche?q={q}",
    "r-stahl.com": "https://r-stahl.com/de/global/suchergebnisse/?q={q}",
}


def fuer(item):
    """Links zur Position als Liste aus {"label", "url"}; leer, wenn der Typ fehlt."""
    typ = (item.manufacturer_article_no or "").strip()
    if not typ:
        return []
    links = []
    seite = internet.seite_des_herstellers(item.manufacturer)
    if seite and seite[0] in HERSTELLERSUCHE:
        links.append({"label": f"Suche auf {seite[0]}",
                      "url": HERSTELLERSUCHE[seite[0]].format(q=quote(typ, safe=""))})
    frage = " ".join(filter(None, [f"site:{seite[0]}" if seite else item.manufacturer, typ,
                                   "ATEX Konformitätserklärung"]))
    links.append({"label": "Websuche",
                  "url": current_app.config["WEBSUCHE_URL"].format(q=quote(frage, safe=""))})
    return links
