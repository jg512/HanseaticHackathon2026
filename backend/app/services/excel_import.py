"""Stückliste (Excel) einlesen und gegen das feste Eingangsschema prüfen (M4, M9, M1).

Erwartet wird der Aufbau, den data/README.md beschreibt: oben Angaben zur Maschine
(„Maschine“, „Ex-Bereich“, „Geforderte Kategorie“), dann eine Kopfzeile, dann die Positionen.
Hier wird nichts korrigiert oder ergänzt: Auffälligkeiten werden gemeldet, nicht geraten.
"""

import io
import re
import zipfile
from dataclasses import dataclass, field

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

# Kopfzeile (normalisiert) -> Feldname. Alle Spalten sind Pflicht.
SPALTEN = {
    "pos": "pos",
    "profilenr": "profile_no",
    "bezeichnung": "description",
    "hersteller": "manufacturer",
    "herstellerartikelnr": "manufacturer_article_no",
    "menge": "quantity",
    "einbauort": "location",
}
# Feldname -> Spaltenkopf für Meldungen, wie in der Stückliste (data/README.md)
KOPF_TEXT = {"pos": "Pos.", "profile_no": "PRO.FILE-Nr.", "description": "Bezeichnung",
             "manufacturer": "Hersteller", "manufacturer_article_no": "Hersteller-Artikelnr.",
             "quantity": "Menge", "location": "Einbauort"}
META = {
    "maschine": "machine",
    "exbereich": "ex_area",
    "gefordertekategorie": "required_category",
}


class ImportFehler(Exception):
    """Die Datei passt nicht zum Eingangsschema und wird nicht verarbeitet."""


@dataclass
class Ergebnis:
    meta: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    issues: list = field(default_factory=list)


def _norm(text):
    """„PRO.FILE-Nr. (erfunden)“ -> „profilenr“."""
    text = re.sub(r"\(.*?\)", "", text or "")
    return re.sub(r"[^a-zäöüß0-9]", "", text.lower())


def _zelle(wert):
    if wert is None:
        return ""
    if isinstance(wert, float) and wert.is_integer():
        wert = int(wert)
    return str(wert).strip()


def lese_stueckliste(inhalt: bytes) -> Ergebnis:
    try:
        wb = load_workbook(io.BytesIO(inhalt), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError, ValueError) as e:
        raise ImportFehler("Die Datei ist keine lesbare Excel-Datei. Bitte speichern Sie die "
                           "Stückliste als .xlsx und laden Sie sie noch einmal hoch.") from e
    zeilen = [[_zelle(c) for c in z] for z in wb.worksheets[0].iter_rows(values_only=True)]

    ergebnis = Ergebnis()
    kopf = None
    for nr, zeile in enumerate(zeilen, start=1):
        namen = [_norm(c) for c in zeile]
        if "pos" in namen and "bezeichnung" in namen:
            kopf = nr
            spalte = {SPALTEN[n]: i for i, n in enumerate(namen) if n in SPALTEN}
            break
        if zeile and namen[0] in META and len(zeile) > 1:
            ergebnis.meta[META[namen[0]]] = zeile[1]
    if kopf is None:
        raise ImportFehler("In der Datei fehlt die Kopfzeile mit den Spalten „Pos.“ und "
                           "„Bezeichnung“. Bitte prüfen Sie, ob es die richtige Stückliste ist.")
    fehlend = [n for n in SPALTEN.values() if n not in spalte]
    if fehlend:
        namen = [f"„{KOPF_TEXT[n]}“" for n in fehlend]
        liste = namen[0] if len(namen) == 1 else ", ".join(namen[:-1]) + " und " + namen[-1]
        was = "fehlt die Spalte" if len(namen) == 1 else "fehlen die Spalten"
        raise ImportFehler(f"In der Stückliste {was} {liste}. Bitte ergänzen Sie sie und laden "
                           "Sie die Datei noch einmal hoch.")

    gesehen = {}  # profile_no -> pos der ersten Zeile
    for nr, zeile in enumerate(zeilen[kopf:], start=kopf + 1):
        if not any(zeile):
            continue
        item = {name: zeile[i] if i < len(zeile) else "" for name, i in spalte.items()}
        item["row_number"] = nr
        ergebnis.items.append(item)

        def melde(code, severity, message):
            ergebnis.issues.append({"row_number": nr, "pos": item["pos"], "code": code,
                                    "severity": severity, "message": message})

        if not item["profile_no"]:
            melde("profile_no_fehlt", "error", "Die PRO.FILE-Nr. fehlt.")
        elif item["profile_no"] in gesehen:
            melde("profile_no_doppelt", "warning",
                  f"Die PRO.FILE-Nr. {item['profile_no']} steht schon in Position "
                  f"{gesehen[item['profile_no']]}.")
        else:
            gesehen[item["profile_no"]] = item["pos"]
        if not item["manufacturer"]:
            melde("hersteller_fehlt", "warning", "Der Hersteller fehlt.")
        if not item["manufacturer_article_no"]:
            melde("herstellerartikelnr_fehlt", "warning", "Die Hersteller-Artikelnummer fehlt.")
        if not item["location"]:
            melde("einbauort_fehlt", "info", "Der Einbauort fehlt. Deshalb ist die Zone unbekannt.")

    if not ergebnis.items:
        raise ImportFehler("Die Stückliste enthält keine Positionen. Bitte prüfen Sie die Datei.")
    return ergebnis
