"""Suche über die ganze Anwendung (Suchfeld oben in der Oberfläche, GET /api/suche).

Gruppen: Vorgänge (Name, Maschine, Datei), Positionen (Bezeichnung, Hersteller,
Hersteller-Artikelnr., PRO.FILE-Nr., Pos.), Bauteile und Dokumente der Stammdaten, Hersteller
(mit Kontakt) und, wenn eingestellt, die Dokumente im Dokumentenarchiv (Zertifikatsserver).

Verglichen wird ohne Rücksicht auf Groß- und Kleinschreibung und auch ohne Leer- und
Satzzeichen: „VTUG10“ findet „VTUG-10-VI-EX2E“. Mehrere Wörter müssen alle passen, gleich in
welchem Feld. Je Gruppe höchstens `je` Treffer, die besten zuerst (ganzer Wert gleich vor Anfang
gleich vor irgendwo enthalten), dazu die Zahl aller Treffer.

Gelesen wird nur das Nötige (Spalten, keine Objekte mit Beziehungen); die Liste des Archivs
bleibt kurz im Speicher, ein Archiv, das nicht antwortet, hält die Suche nicht auf.
"""

import re
import threading
import time
from pathlib import PurePosixPath

import httpx
from flask import current_app

from ..db import db
from ..models import Bom, BomItem, Document, Manufacturer, Part
from ..models.stammdaten import DocumentLanguage  # noqa: F401  (Tabelle bekannt machen)
from .excel_export import ART_TEXT
from .pdf_text import lesbar  # „#U00e4“ in Dateinamen als „ä“

MIN_ZEICHEN = 2
JE_GRUPPE = 6
JE_GRUPPE_MAX = 50
ARCHIV_WARTEZEIT = 2.5   # Sekunden; die Suche tippt mit, sie darf nicht hängen
ARCHIV_MERKEN = 60       # Sekunden, so lange gilt die Liste des Archivs
ARCHIV_WEG_MERKEN = 30   # Sekunden, so lange wird ein Archiv, das nicht antwortet, nicht gefragt

TITEL = {"vorgaenge": "Vorgänge", "positionen": "Positionen", "bauteile": "Bauteile",
         "dokumente": "Dokumente", "hersteller": "Hersteller", "archiv": "Dokumentenarchiv"}
DOKUMENTART = {**ART_TEXT, "datenblatt": "Datenblatt", "sonstiges": "Sonstiges"}

_archiv = {"zeit": 0.0, "liste": None, "weg_seit": 0.0}
_archiv_sperre = threading.Lock()


def norm(text):
    """Nur Buchstaben und Ziffern, klein: „VTUG-10-VI-EX2E“ -> „vtug10viex2e“."""
    return re.sub(r"[^0-9a-zäöüß]", "", (text or "").casefold())


def woerter(q):
    """Suchwörter, normalisiert; Wörter ohne Buchstaben und Ziffern fallen weg."""
    return [w for w in (norm(t) for t in re.split(r"\s+", q or "")) if w]


def _punkte(wort, felder):
    """Bester Treffer des Worts in den Feldern: 3 gleich, 2 Anfang, 1 enthalten, 0 nicht."""
    beste = 0
    for f in felder:
        n = norm(f)
        if not n or wort not in n:
            continue
        beste = max(beste, 3 if n == wort else 2 if n.startswith(wort) else 1)
        if beste == 3:
            break
    return beste


def _bewerte(ws, felder):
    """Summe der Punkte, wenn jedes Wort in einem Feld steht, sonst 0."""
    summe = 0
    for w in ws:
        p = _punkte(w, felder)
        if not p:
            return 0
        summe += p
    return summe


def _gruppe(art, kandidaten, je):
    """kandidaten: [(punkte, reihenfolge, treffer)] -> Gruppe mit den besten `je`."""
    kandidaten.sort(key=lambda k: (-k[0], k[1]))
    return {"art": art, "titel": TITEL[art], "anzahl": len(kandidaten),
            "treffer": [t for _, _, t in kandidaten[:je]], "hinweis": None}


def _vorgangsname(name, machine, datei, bom_id):
    """Wie oberflaeche.name, aus den Spalten."""
    return name or f"{machine or PurePosixPath(datei or '').stem} · Nr. {bom_id}"


def _mit(*teile):
    return " · ".join(t for t in teile if t)


def _vorgaenge(ws, je):
    zeilen = db.session.execute(db.select(
        Bom.id, Bom.name, Bom.machine, Bom.source_file_name, Bom.created_at)).all()
    kandidaten = []
    for bid, name, machine, datei, erstellt in zeilen:
        anzeige = _vorgangsname(name, machine, datei, bid)
        p = _bewerte(ws, [anzeige, machine, datei])
        if p:
            kandidaten.append((p, -bid, {
                "titel": anzeige,
                "kontext": _mit(machine and f"Maschine {machine}", datei and f"Stückliste {datei}",
                                erstellt and f"hochgeladen am {erstellt:%d.%m.%Y}"),
                "ziel": f"#/v/{bid}"}))
    return _gruppe("vorgaenge", kandidaten, je)


def _positionen(ws, je):
    zeilen = db.session.execute(db.select(
        BomItem.id, BomItem.bom_id, BomItem.pos, BomItem.description, BomItem.manufacturer,
        BomItem.manufacturer_article_no, BomItem.profile_no,
        Bom.name, Bom.machine, Bom.source_file_name).join(Bom, Bom.id == BomItem.bom_id)).all()
    kandidaten = []
    for iid, bid, pos, bez, herst, typ, pro, name, machine, datei in zeilen:
        p = _bewerte(ws, [bez, herst, typ, pro, pos and f"pos {pos}"])
        if p:
            vorgang = _vorgangsname(name, machine, datei, bid)
            kandidaten.append((p, (-bid, iid), {
                "titel": f"Position {pos or '?'}: {bez or 'ohne Bezeichnung'}",
                "kontext": _mit(herst, typ, pro and f"PRO.FILE {pro}", f"Vorgang „{vorgang}“"),
                "ziel": f"#/v/{bid}/a/{iid}"}))
    return _gruppe("positionen", kandidaten, je)


def _bauteile(ws, je):
    zeilen = db.session.execute(db.select(
        Part.id, Part.profile_no, Part.name_de, Part.manufacturer_article_no, Manufacturer.name)
        .outerjoin(Manufacturer, Manufacturer.id == Part.manufacturer_id)).all()
    kandidaten = []
    for pid, pro, bez, typ, herst in zeilen:
        p = _bewerte(ws, [pro, bez, typ, herst])
        if p:
            kandidaten.append((p, pro or "", {
                "titel": f"Bauteil {pro}" + (f": {bez}" if bez else ""),
                "kontext": _mit(herst, typ),
                "ziel": f"#/stammdaten/teil/{pid}"}))
    return _gruppe("bauteile", kandidaten, je)


def _dokumente(ws, je):
    zeilen = db.session.execute(db.select(
        Document.id, Document.doc_type, Document.file_path, Document.url,
        Document.certificate_no)).all()
    kandidaten = []
    for did, art, pfad, url, nummer in zeilen:
        datei = lesbar(PurePosixPath(pfad).name) if pfad else (url or "")
        p = _bewerte(ws, [datei, nummer])
        if p:
            kandidaten.append((p, -did, {
                "titel": datei or f"Dokument {did}",
                "kontext": _mit(DOKUMENTART.get(art, art), nummer and f"Zertifikat {nummer}"),
                "ziel": f"#/stammdaten/dokument/{did}"}))
    return _gruppe("dokumente", kandidaten, je)


def _hersteller(ws, je):
    from . import kontakte
    kandidaten = []
    for z in kontakte.liste():
        p = _bewerte(ws, [z["hersteller"], z["email"], z["ansprechpartner"]])
        if p:
            kontext = (z["email"] if z["email"] else "Noch keine E-Mail-Adresse hinterlegt")
            if not z["in_stammdaten"]:
                kontext += " · bisher nur in einer Stückliste"
            kandidaten.append((p, z["hersteller"].casefold(), {
                "titel": z["hersteller"], "kontext": kontext, "ziel": "#/stammdaten/hersteller"}))
    return _gruppe("hersteller", kandidaten, je)


def _archiv_liste():
    """Alle Dokumente des Archivs (kurz gemerkt) oder None, wenn es nicht antwortet."""
    from . import zertifikatsserver
    basis = zertifikatsserver._url()
    if not basis:
        return "aus"
    jetzt = time.monotonic()
    with _archiv_sperre:
        if _archiv["liste"] is not None and jetzt - _archiv["zeit"] < ARCHIV_MERKEN:
            return _archiv["liste"]
        if _archiv["weg_seit"] and jetzt - _archiv["weg_seit"] < ARCHIV_WEG_MERKEN:
            return None
    try:
        r = httpx.get(basis + "/api/dokumente", timeout=ARCHIV_WARTEZEIT)
        r.raise_for_status()
        liste = r.json()
        if not isinstance(liste, list):
            raise ValueError("keine Liste")
    except (httpx.HTTPError, ValueError) as e:
        current_app.logger.warning("Suche: Dokumentenarchiv antwortet nicht (%s)", e)
        with _archiv_sperre:
            _archiv["weg_seit"] = time.monotonic()
        return None
    with _archiv_sperre:
        _archiv.update(zeit=time.monotonic(), liste=liste, weg_seit=0.0)
    return liste


def _archiv_gruppe(ws, je):
    liste = _archiv_liste()
    if liste == "aus":
        return None
    if liste is None:
        g = _gruppe("archiv", [], je)
        g["hinweis"] = ("Das Dokumentenarchiv ist gerade nicht erreichbar. Seine Dokumente "
                        "fehlen deshalb in den Treffern.")
        return g
    kandidaten = []
    for d in liste:
        typen = [t.get("artikelnr") for t in d.get("teile") or []]
        p = _bewerte(ws, [d.get("dateiname"), d.get("hersteller"), *typen])
        if p:
            # „aktuell“ ist der Normalfall; sonst sagt das Archiv selbst, wo das Dokument liegt
            stand = (d.get("ablage_text") or d.get("ablage")) \
                if d.get("ausgemustert") or d.get("ablage") not in (None, "aktuell") else None
            kandidaten.append((p, -(d.get("id") or 0), {
                "titel": lesbar(d.get("dateiname")) or f"Dokument {d.get('id')}",
                "kontext": _mit(d.get("hersteller"), DOKUMENTART.get(d.get("doc_type"),
                                                                     d.get("doc_type")),
                                ", ".join(t for t in typen if t), stand),
                "ziel": f"#/zertifikate/dok/{d.get('id')}"}))
    return _gruppe("archiv", kandidaten, je)


def suche(q, je=JE_GRUPPE):
    """{"q", "anzahl", "gruppen": [{art, titel, anzahl, treffer: [{titel, kontext, ziel}],
    hinweis}], "zu_kurz"}; Gruppen ohne Treffer fehlen (außer mit Hinweis)."""
    q = (q or "").strip()
    ws = woerter(q)
    je = max(1, min(int(je or JE_GRUPPE), JE_GRUPPE_MAX))
    if sum(len(w) for w in ws) < MIN_ZEICHEN:
        return {"q": q, "anzahl": 0, "gruppen": [], "zu_kurz": True}
    gruppen = [_vorgaenge(ws, je), _positionen(ws, je), _bauteile(ws, je), _dokumente(ws, je),
               _hersteller(ws, je), _archiv_gruppe(ws, je)]
    gruppen = [g for g in gruppen if g and (g["anzahl"] or g["hinweis"])]
    return {"q": q, "anzahl": sum(g["anzahl"] for g in gruppen), "gruppen": gruppen,
            "zu_kurz": False}
