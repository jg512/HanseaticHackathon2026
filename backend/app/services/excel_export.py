"""Excel-Ausgabe (M7, M9, M12) und Liste, was fehlt (S4).

Kundenliste: festes Schema der Vorgabe (Spec 001 Abschnitt 6), 17 Spalten, eine Zeile je Bauteil,
nur geprüfte Zuordnungen. Ob ausgegeben werden darf, entscheidet die Route über status.py.
Fehlliste: die offenen Positionen mit Grund, immer abrufbar.
EX-Bauteil-Liste (E1): jede Position mit Einordnung, Regel, Begründung und Quelle, immer abrufbar.
Von Hand erledigte Positionen stehen in beiden Mappen zusätzlich auf dem Blatt „Vermerke“, damit
die bewusste Lücke sichtbar bleibt; Blatt 1 behält sein Schema.
"""

import io
import re
from datetime import date
from pathlib import PurePosixPath

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from ..db import db
from ..models import Part
from . import ex_liste, pdf_text, status

KOPF = ["Gerät", "Device", "Artikel-Nr. / Item number", "Hersteller / producer",
        "Explosionsschutz / Explosion protection", "Baumusterprüfbescheinigung (d)",
        "EU type-examination Certificate (e)", "UK type-examination Certificate (e)",
        "EU-Konformitätserklärung (d)", "EU Declaration of Conformity (e)",
        "UE Déclaration de Conformité (f)", "UK Declaration of Conformity (e)",
        "Bedienungsanleitung (d)", "Manual (e)", "Mode d'emploi (f)",
        "zuletzt geändert/ausgegeben am", "DL-Link"]
BREITE = [28, 28, 14, 20, 30] + [26] * 10 + [14, 40]

# Dokumentart × Sprache -> Spalte (ab 1). Was hier fehlt, hat keine Spalte und erscheint nicht.
DOK_SPALTE = {
    ("eu_baumusterpruefbescheinigung", "de"): 6,
    ("eu_baumusterpruefbescheinigung", "en"): 7,
    ("uk_baumusterpruefbescheinigung", "en"): 8,
    ("eu_konformitaetserklaerung", "de"): 9,
    ("eu_konformitaetserklaerung", "en"): 10,
    ("eu_konformitaetserklaerung", "fr"): 11,
    ("uk_konformitaetserklaerung", "en"): 12,
    ("bedienungsanleitung", "de"): 13,
    ("bedienungsanleitung", "en"): 14,
    ("bedienungsanleitung", "fr"): 15,
}

FEHL_KOPF = ["Pos.", "PRO.FILE-Nr.", "Bezeichnung", "Hersteller", "Typ (Hersteller-Artikelnr.)",
             "Einbauort", "Status", "Hinweis", "Gesucht in", "Fehlende Dokumente"]
FEHL_BREITE = [8, 14, 30, 18, 28, 16, 16, 40, 20, 30]
STATUS_TEXT = {"offen": "Offen", "klaerung": "Klärung nötig", "vorschlag": "Vorschlag prüfen",
               "nicht_gefunden": "Nicht gefunden", "angefordert": "Angefordert",
               "eskaliert": "An den Einkauf eskaliert",
               "vorhanden": "Vorhanden", "kein_ex_teil": "Kein Ex-Teil",
               "erledigt": "Von Hand erledigt"}
VERMERK_KOPF = ["Pos.", "PRO.FILE-Nr.", "Bezeichnung", "Hersteller", "Typ (Hersteller-Artikelnr.)",
                "Begründung", "Erledigt von", "Erledigt am", "Fehlende Pflichtdokumente"]
VERMERK_BREITE = [8, 14, 30, 18, 28, 50, 18, 12, 30]
ART_TEXT = {"eu_baumusterpruefbescheinigung": "EU-Baumusterprüfbescheinigung",
            "eu_konformitaetserklaerung": "EU-Konformitätserklärung",
            "uk_baumusterpruefbescheinigung": "UK-Baumusterprüfbescheinigung",
            "uk_konformitaetserklaerung": "UK-Konformitätserklärung",
            "bedienungsanleitung": "Bedienungsanleitung"}

EX_KOPF = ["Pos.", "PRO.FILE-Nr.", "Bezeichnung", "Hersteller", "Typ (Hersteller-Artikelnr.)",
           "Menge", "Einbauort", "Zone", "Geforderte Kategorie", "Einordnung",
           "Vorschlag der Regel", "Begründung", "Quelle", "Hinweis", "Entscheidung"]
EX_BREITE = [8, 14, 30, 18, 28, 8, 22, 9, 12, 18, 30, 50, 40, 36, 36]

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def dateiname(bom, art):
    """z. B. „Ex-Dokumente_Abfüllanlage_TM-1_Vorgang-3.xlsx“, art: „Ex-Dokumente“, „Fehlliste“ …"""
    maschine = re.sub(r"[^\w.-]+", "_", bom.machine or "").strip("_")
    return "_".join(t for t in (art, maschine, f"Vorgang-{bom.id}") if t) + ".xlsx"


def _zeile(part, item):
    from . import zertifikatsserver  # nur für die Zelle „DL-Link“
    zeile = [part.name_de or item.description, part.name_en, part.profile_no,
             part.manufacturer.name if part.manufacturer else item.manufacturer,
             part.ex_marking_raw] + [None] * 10 + [None, None]
    zellen = {}  # Spalte -> Dateinamen
    belegt = {}  # (Spalte, Dateiname) -> Dokument, für „DL-Link“
    gepruefte = [pd for pd in part.documents if pd.status == "geprueft"]
    for pd in gepruefte:
        doc = pd.document
        # „#U00e4“ im Namen als „ä“: die Kundenliste liest ein Mensch
        name = pdf_text.lesbar(PurePosixPath(doc.file_path).name) if doc.file_path else doc.url
        for sprache in doc.languages:
            spalte = DOK_SPALTE.get((doc.doc_type, sprache.language))
            if spalte:
                zellen.setdefault(spalte, set()).add(name)
                belegt[(spalte, name)] = doc
    for spalte, namen in zellen.items():
        zeile[spalte - 1] = "\n".join(sorted(namen))
    # „DL-Link“: je Dokument der Dokumentspalten ein Link, eine Zeile je Link, in der Reihenfolge
    # der Spalten und Namen; Link des Herstellers, sonst Dokumentenarchiv, sonst keiner. Die
    # allgemeine Webseite des Herstellers (part.manufacturer_url) ist kein Download-Link.
    zeile[16] = zertifikatsserver.download_links(belegt[k] for k in sorted(belegt))
    daten = [pd.verified_at.date() for pd in gepruefte if pd.verified_at]
    zeile[15] = max(daten) if daten else part.legacy_last_modified
    return zeile


def _zeile_ohne_bauteil(item):
    """Von Hand erledigt, ohne Bauteil in der Datenbank: Angaben der Position, Dokumente leer."""
    return [item.description, None, item.profile_no, item.manufacturer] + [None] * 13


def kunden_zeilen(bom):
    """Eine Zeile je Bauteil in der Reihenfolge der Stückliste, ohne „kein_ex_teil“.

    Positionen ohne PRO.FILE-Nr. (nur von Hand erledigt möglich) stehen je für sich.
    """
    zeilen, gesehen = [], set()
    for item in bom.items:
        if item.status == "kein_ex_teil" or (item.profile_no and item.profile_no in gesehen):
            continue
        gesehen.add(item.profile_no)
        part = db.session.scalar(db.select(Part).filter_by(profile_no=item.profile_no)) \
            if item.profile_no else None
        zeilen.append(_zeile(part, item) if part else _zeile_ohne_bauteil(item))
    return zeilen


def offene_zeile(item):
    """Eine offene oder von Hand erledigte Position für Oberfläche, 409-Antwort und Fehlliste.

    missing_languages: Pflichtdokumente, die geprüft vorliegen, aber nicht in der Pflichtsprache
    (Standard de); sie stehen auch in missing_documents („… (deutsche Fassung)“)."""
    arten = status.fehlende_arten(item)
    sprachen = status.fehlende_sprachen(item)
    d = item.to_dict()
    d["missing_doc_types"] = arten
    d["missing_languages"] = sprachen
    d["missing_documents"] = ", ".join(
        [ART_TEXT.get(a, a) for a in arten]
        + [f"{ART_TEXT.get(a, a)} ({FASSUNG.get(s[0], s[0])} Fassung)"
           for a, s in sprachen.items()])
    return d


def stand(bom):
    offen = [offene_zeile(i) for i in status.offene_positionen(bom)]
    erledigt = [offene_zeile(i) for i in bom.items if i.status == "erledigt"]
    return {"bom_id": bom.id, "machine": bom.machine, "total": len(bom.items),
            "done": len(bom.items) - len(offen), "complete": not offen, "open": offen,
            "resolved": erledigt,
            "export_file_name": dateiname(bom, "Ex-Dokumente"),
            "missing_file_name": dateiname(bom, "Fehlliste")}


def _blatt(ws, titel, kopf, breiten, zeilen):
    ws.title = titel
    ws.append(kopf)
    for z in zeilen:
        ws.append(z)
    umbruch = Alignment(wrap_text=True, vertical="top")
    for reihe in ws.iter_rows():
        for zelle in reihe:
            zelle.alignment = umbruch
            if isinstance(zelle.value, date):
                zelle.number_format = "DD.MM.YYYY"
    for zelle in ws[1]:
        zelle.font = Font(bold=True)
    for nr, breite in enumerate(breiten, start=1):
        ws.column_dimensions[ws.cell(1, nr).column_letter].width = breite
    ws.freeze_panes = "A2"


def _mappe(titel, kopf, breiten, zeilen, bom):
    """Mappe mit einem Blatt, dazu „Vermerke“, wenn es von Hand erledigte Positionen gibt."""
    wb = Workbook()
    _blatt(wb.active, titel, kopf, breiten, zeilen)
    vermerke = vermerk_zeilen(bom)
    if vermerke:
        _blatt(wb.create_sheet(), "Vermerke", VERMERK_KOPF, VERMERK_BREITE, vermerke)
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


def vermerk_zeilen(bom):
    """Von Hand erledigte Positionen mit Begründung, wer, wann und was fehlt."""
    return [[i.pos, i.profile_no, i.description, i.manufacturer, i.manufacturer_article_no,
             i.resolution_note, i.resolved_by, i.resolved_at.date(),
             offene_zeile(i)["missing_documents"] or None]
            for i in bom.items if i.status == "erledigt"]


def kundenliste(bom):
    return _mappe("Ex-Dokumente", KOPF, BREITE, kunden_zeilen(bom), bom)


def fehlliste(bom):
    zeilen = [[o["pos"], o["profile_no"], o["description"], o["manufacturer"],
               o["manufacturer_article_no"], o["location"], STATUS_TEXT.get(o["status"], o["status"]),
               o["status_note"], o["searched_in"], o["missing_documents"]]
              for o in stand(bom)["open"]]
    return _mappe("Fehlliste", FEHL_KOPF, FEHL_BREITE, zeilen, bom)


def _ex_zeile(z):
    vorschlag = ex_liste.EINORDNUNG_TEXT["ex_teil" if z["vorschlag"] == ex_liste.EX_TEIL
                                         else "klaerung"]
    return [z["pos"], z["profile_no"], z["description"], z["manufacturer"],
            z["manufacturer_article_no"], z["quantity"], z["location"], z["zone"], z["kategorie"],
            z["einordnung_text"], f"{z['regel_text']}: {vorschlag}", z["grund"], z["quelle"],
            z["hinweis"], z["entschieden"]]


def ex_bauteil_liste(bom):
    """Blatt „EX-Bauteil-Liste“ mit den Ex-Teilen, Blatt „Nicht in der Liste“ mit dem Rest.

    Nichts fällt still heraus: offene Entscheidungen und „kein Ex-Teil“ stehen mit Grund daneben.
    """
    zeilen = ex_liste.liste(bom)["items"]
    wb = Workbook()
    _blatt(wb.active, "EX-Bauteil-Liste", EX_KOPF, EX_BREITE,
           [_ex_zeile(z) for z in zeilen if z["einordnung"] == "ex_teil"])
    _blatt(wb.create_sheet(), "Nicht in der Liste", EX_KOPF, EX_BREITE,
           [_ex_zeile(z) for z in zeilen if z["einordnung"] != "ex_teil"])
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


# --- Zwischenstand (Entscheidung des Teams) -------------------------------------------------
# Die Kundenliste bleibt gesperrt, bis alles vollständig ist (M12). Für „Paket raus plus Liste, was
# fehlt“ gibt es jederzeit den Zwischenstand: dieselbe Mappe, vorn ein Blatt, das sie deutlich als
# unvollständig markiert, mit Datum, Zählung und der Liste, was fehlt.

FASSUNG = {"de": "deutsche", "en": "englische", "fr": "französische"}
ZWISCHENSTAND_TITEL = "ZWISCHENSTAND – unvollständig, nicht zur Weitergabe an den Kunden"


def _zwischenstand_kopf(ws, bom, st, jetzt):
    offen = len(st["open"])
    zeilen = [
        [ZWISCHENSTAND_TITEL],
        [f"Diese Mappe ist ein Zwischenstand vom {jetzt:%d.%m.%Y, %H:%M} Uhr. Es fehlen noch "
         "Dokumente, deshalb ist sie nicht für den Kunden bestimmt."],
        [],
        ["Maschine", bom.machine],
        ["Vorgang", bom.id],
        ["Positionen gesamt", st["total"]],
        ["Davon erledigt", st["done"]],
        ["Davon noch offen", offen],
        ["Davon ohne deutsche Fassung der EU-Konformitätserklärung",
         sum(bool(o["missing_languages"]) for o in st["open"])],
        [],
        ["Was noch fehlt"],
    ]
    for z in zeilen:
        ws.append(z)
    ws["A1"].font = Font(bold=True, size=14, color="C00000")
    ws["A2"].font = Font(italic=True)
    ws.cell(len(zeilen), 1).font = Font(bold=True)
    return len(zeilen)


def zwischenstand(bom, jetzt=None):
    """Mappe „Zwischenstand“: Blatt 1 markiert sie als unvollständig, mit Datum, Zählung und den
    offenen Positionen (Spalten der Fehlliste); danach die Kundenliste mit dem, was schon
    geprüft vorliegt (Spalten unverändert) und, falls vorhanden, „Vermerke“."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from flask import current_app
    if jetzt is None:
        jetzt = datetime.now(ZoneInfo(current_app.config.get("ZEITZONE", "Europe/Berlin")))
    st = stand(bom)
    wb = Workbook()
    ws = wb.active
    ws.title = "Zwischenstand"
    kopf = _zwischenstand_kopf(ws, bom, st, jetzt)
    ws.append(FEHL_KOPF)
    for zelle in ws[kopf + 1]:
        zelle.font = Font(bold=True)
    for o in st["open"]:
        ws.append([o["pos"], o["profile_no"], o["description"], o["manufacturer"],
                   o["manufacturer_article_no"], o["location"],
                   STATUS_TEXT.get(o["status"], o["status"]), o["status_note"], o["searched_in"],
                   o["missing_documents"]])
    if not st["open"]:
        ws.append(["Es fehlt nichts mehr. Sie können die Kundenliste herunterladen."])
    umbruch = Alignment(wrap_text=True, vertical="top")
    for reihe in ws.iter_rows(min_row=kopf + 1):
        for zelle in reihe:
            zelle.alignment = umbruch
    for nr, breite in enumerate(FEHL_BREITE, start=1):
        ws.column_dimensions[ws.cell(1, nr).column_letter].width = max(breite, 14)
    ws.column_dimensions["A"].width = 30

    _blatt(wb.create_sheet(), "Ex-Dokumente (Zwischenstand)", KOPF, BREITE, kunden_zeilen(bom))
    vermerke = vermerk_zeilen(bom)
    if vermerke:
        _blatt(wb.create_sheet(), "Vermerke", VERMERK_KOPF, VERMERK_BREITE, vermerke)
    wb.properties.title = ZWISCHENSTAND_TITEL
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()
