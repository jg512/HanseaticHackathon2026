"""Ein Mensch entscheidet über einen Vorschlag (M3, M6, M19).

Erst die Freigabe überträgt den Vorschlag in die Stammdaten: Hersteller, Bauteil, Dokument und
die geprüfte Zuordnung. Das Speichern (commit) macht der Aufrufer, damit alles in einer
Transaktion landet.
"""

import hashlib
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pymupdf

from ..db import db
from ..models import BomItem, Document, DocumentLanguage, Manufacturer, Part, PartDocument
from . import angaben, pruefung, status

ENTSCHEIDUNGEN = ("freigeben", "ablehnen")
# Automatische Freigabe (automatisch): wer dann als „freigegeben von“ dasteht, und welche
# Dokumente der Agent selbst freigeben darf: Nachweise, keine Anleitungen oder Datenblätter
AUTOMATISCH_VON = "ATEX-Agent (automatisch)"
AUTOMATISCH_ARTEN = ("eu_konformitaetserklaerung", "eu_baumusterpruefbescheinigung",
                     "uk_konformitaetserklaerung", "uk_baumusterpruefbescheinigung")


class FreigabeFehler(Exception):
    """Der Vorschlag lässt sich nicht entscheiden; der Text geht an den Menschen."""


def normalisiere(name):
    """„Pepperl + Fuchs GmbH“ -> „pepperlfuchsgmbh“: klein, ohne Leer- und Satzzeichen."""
    return re.sub(r"[\W_]", "", (name or "").lower())


def datei(finding, basis):
    """Pfad der Datei des Vorschlags, nur innerhalb von basis (ATEX_DOCS), sonst None."""
    if not finding.file_path:
        return None
    basis = Path(basis).resolve()
    pfad = (basis / finding.file_path).resolve()
    if not pfad.is_relative_to(basis) or not pfad.is_file():
        return None
    return pfad


def fundstellen(page, begriff):
    """Wo begriff auf der Seite steht: je Fundstelle eine Liste von Rechtecken (eins je Zeile).

    Zuerst die Suche von PyMuPDF (genau so geschrieben, in einer Zeile). Dazu kommen Stellen, die
    sie nicht findet, die Vorprüfung aber schon: über Zeilen umbrochen („VACF-B-K1-1A-1-⏎EX4-M“,
    Tabellenzellen) oder mit anderen Leer- und Bindestrichen. Verglichen wird wie in
    pruefung._stellen, nur Buchstaben und Ziffern; kurze Werte (bis 6 Zeichen) nur als ganzes Wort.
    """
    gefunden = [[r] for r in page.search_for(begriff)] if begriff else []
    nadel = pruefung._normiert(begriff or "")[0]
    if not nadel:
        return gefunden
    # Zeichen der Seite mit Rahmen und Zeile; zwischen den Zeilen ein Umbruch ohne Rahmen
    text, rahmen = [], []
    for nr_block, block in enumerate(page.get_text("rawdict")["blocks"]):
        for nr_zeile, zeile in enumerate(block.get("lines", ())):
            for span in zeile["spans"]:
                for z in span["chars"]:
                    text.append(z["c"])
                    rahmen.append((pymupdf.Rect(z["bbox"]), (nr_block, nr_zeile)))
            text.append("\n")
            rahmen.append(None)
    text = "".join(text)
    for start, ende in pruefung._stellen(begriff, text):
        if len(nadel) <= 6 and text[ende:ende + 1].isalnum():
            continue
        zeilen = {}
        for r in rahmen[start:ende]:
            if r is not None:
                zeilen[r[1]] = zeilen.get(r[1], pymupdf.Rect(r[0])) | r[0]
        rechtecke = list(zeilen.values())
        if rechtecke and not any(r.intersects(g) for r in rechtecke for f in gefunden for g in f):
            gefunden.append(rechtecke)
    return gefunden


def seite_als_png(pfad, seite, begriff=None, zoom=1.5):
    """Rendert eine Seite (ab 1) als PNG und markiert den Begriff, wenn er als Text darauf steht.

    Gibt None zurück, wenn es die Seite nicht gibt. Scans ohne Textebene kommen ohne Markierung.
    """
    with pymupdf.open(pfad) as pdf:
        if not seite or not 1 <= seite <= pdf.page_count:
            return None
        page = pdf[seite - 1]
        for stelle in fundstellen(page, begriff):
            page.add_highlight_annot(stelle)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


# Farben der Markierungen in der ganzen PDF: Fundstelle gelb, Beleg grün, nur ähnlich orange
GELB, GRUEN, ORANGE = (1, 0.88, 0.2), (0.55, 0.88, 0.55), (1, 0.66, 0.3)
WERT_NAME = {"typ": "Typ", "kennzeichnung": "Ex-Kennzeichnung", "zertifikat": "Zertifikatsnummer"}


def stellen(finding):
    """Was in der PDF eines Vorschlags markiert wird: [(Seite oder None, Begriff, Farbe, Notiz)].

    Die Fundstelle des Typs und die Belege aus services/belege.py, je auf ihrer Seite. Ohne
    Belege (ältere Vorschläge) Kennzeichnung und Zertifikatsnummer auf allen Seiten.
    """
    ergebnis = []
    if finding.matched_term:
        ergebnis.append((finding.page, finding.matched_term, GELB,
                         f"Fundstelle: {finding.matched_term}"))
    for b in finding.belege or []:
        name = WERT_NAME.get(b.get("wert"))
        if not name or not b.get("angabe") or not b.get("seite"):
            continue  # Kategorie ist abgeleitet und steht so nicht im Text
        if b.get("belegt"):
            ergebnis.append((b["seite"], b["angabe"], GRUEN, f"{name} belegt: {b['angabe']}"))
        else:
            ergebnis.append((b["seite"], b["angabe"], ORANGE,
                             f"{name} nicht belegt. {b.get('hinweis') or ''}".strip()))
    if finding.belege is None:
        for wert, name in ((finding.ex_marking_raw, "Ex-Kennzeichnung"),
                           (finding.certificate_no, "Zertifikatsnummer")):
            for teil in (wert or "").split("; "):
                if teil:
                    ergebnis.append((None, teil, GRUEN, f"{name}: {teil}"))
    return ergebnis


def markierte_pdf(pfad, markierungen):
    """Die ganze PDF mit Hervorhebungen [(Seite oder None = alle, Begriff, Farbe, Notiz)].

    Steht ein Begriff nicht als Text da (Scan ohne Textebene), bleibt die Seite unmarkiert; über
    Zeilen umbrochene Begriffe findet fundstellen wie die Vorprüfung.
    """
    with pymupdf.open(pfad) as pdf:
        for seite, begriff, farbe, notiz in markierungen:
            seiten = [seite - 1] if seite and 1 <= seite <= pdf.page_count else range(pdf.page_count)
            for nr in seiten:
                page = pdf[nr]  # festhalten: Markierungen hängen an diesem Objekt
                for stelle in fundstellen(page, begriff):
                    annot = page.add_highlight_annot(stelle)
                    annot.set_colors(stroke=farbe)
                    annot.set_info(content=notiz, title="ATEX-Agent")
                    annot.update()
        return pdf.tobytes()


def entscheide(finding, entscheidung, von, basis, doc_type=None):
    """Setzt die Entscheidung eines Menschen („freigeben“ oder „ablehnen“) und aktualisiert den
    Stand der Positionen. doc_type: Korrektur der Dokumentart bei der Freigabe, sonst None."""
    if finding.status != "vorgeschlagen":
        raise FreigabeFehler(f"Vorschlag ist schon {finding.status} ({finding.decided_by}).")
    jetzt = datetime.now(timezone.utc)
    if entscheidung == "freigeben":
        _uebernimm(finding, von, basis, doc_type, jetzt)
        finding.status = "freigegeben"
    else:
        finding.status = "abgelehnt"
    finding.decided_by, finding.decided_at = von, jetzt
    if entscheidung == "freigeben":  # Datei an den Zertifikatsserver; hält die Freigabe nie auf
        from . import zertifikatsserver  # importiert dieses Modul
        zertifikatsserver.nach_freigabe(finding, basis)

    item = finding.item
    # Dubletten mit derselben PRO.FILE-Nr., auch in anderen Vorgängen: die Stammdaten gelten
    # für alle, und die Ansichten (GET) speichern den Stand nicht mehr (services/lesepuffer.py)
    gleiche = db.session.scalars(db.select(BomItem).filter_by(
        profile_no=item.profile_no)) if item.profile_no else [item]
    for i in gleiche:
        status.aktualisiere(i)
    return item.status


def lokal_und_ohne_befund(finding):
    """Darf der Agent den Vorschlag selbst freigeben? Nur ein Nachweis aus einer lokalen Quelle
    (Herstellerordner, Dokumentenarchiv; Ähnlichkeitssuche nur mit Typ-Treffer), nie aus dem
    Internet, mit Vorprüfung „ok“ und einer Position mit PRO.FILE-Nr."""
    return (finding.status == "vorgeschlagen" and finding.check_result == "ok"
            and finding.doc_type in AUTOMATISCH_ARTEN and bool(finding.item.profile_no)
            and (finding.source == "ordner"
                 or (finding.source == "suchdienst" and bool(finding.matched_term))))


def automatisch(item, basis):
    """Gibt die Vorschläge der Position frei, die lokal_und_ohne_befund sind, und markiert sie
    (auto_approved). Gibt die freigegebenen zurück. Das Speichern macht der Aufrufer.

    Vorher speichern (flush): erst dann laufen Belege und Angaben-Vorprüfung
    (services/belege.py), die aus „ok“ noch „unklar“ oder „warnung“ machen können."""
    db.session.flush()
    frei = []
    for f in [f for f in item.findings if lokal_und_ohne_befund(f)]:
        if datei(f, basis) is None:
            continue
        entscheide(f, "freigeben", AUTOMATISCH_VON, basis)
        f.auto_approved = True
        frei.append(f)
    return frei


def _uebernimm(finding, von, basis, doc_type, jetzt):
    """Hersteller, Bauteil, Dokument und geprüfte Zuordnung finden oder anlegen."""
    item = finding.item
    if not item.profile_no:
        raise FreigabeFehler("Position ohne PRO.FILE-Nr.: das Dokument lässt sich keinem Bauteil zuordnen.")
    if doc_type:
        finding.doc_type = doc_type  # Korrektur des Menschen

    part = db.session.scalar(db.select(Part).filter_by(profile_no=item.profile_no))
    if part is None:
        part = Part(profile_no=item.profile_no, name_de=item.description,
                    manufacturer=_hersteller(item.manufacturer),
                    manufacturer_article_no=item.manufacturer_article_no or None,
                    source="manual", changed_by=von)
        db.session.add(part)
    if finding.ex_marking_raw and not part.ex_marking_raw:
        part.ex_marking_raw, part.changed_by = finding.ex_marking_raw, von

    pfad = datei(finding, basis)
    file_hash = finding.file_hash or (_sha256(pfad) if pfad else None)
    doc = None
    if file_hash:
        doc = db.session.scalar(db.select(Document).filter_by(file_hash=file_hash))
    elif finding.url:
        doc = db.session.scalar(db.select(Document).filter_by(url=finding.url))
    if doc is None:
        doc = Document(doc_type=finding.doc_type, file_path=finding.file_path, url=finding.url,
                       file_hash=file_hash, changed_by=von)
        db.session.add(doc)
    elif doc_type:  # bestehendes Dokument: Art nur auf ausdrücklichen Wunsch ändern
        doc.doc_type = doc_type
    doc.certificate_no = doc.certificate_no or finding.certificate_no
    _angaben_uebernehmen(finding, doc, basis)
    doc.file_exists, doc.checked_at, doc.changed_by = pfad is not None, jetzt, von
    vorhanden = {s.language for s in doc.languages}
    for sprache in (finding.languages or "").split(","):
        sprache = sprache.strip().lower()
        if sprache and sprache not in vorhanden:
            doc.languages.append(DocumentLanguage(language=sprache))
            vorhanden.add(sprache)

    db.session.flush()
    zuordnung = db.session.get(PartDocument, (part.id, doc.id))
    if zuordnung is None:
        zuordnung = PartDocument(part=part, document=doc, source="manual", changed_by=von)
        db.session.add(zuordnung)
    zuordnung.status, zuordnung.verified_by, zuordnung.verified_at = "geprueft", von, jetzt
    zuordnung.changed_by = von
    db.session.flush()


def _angaben_uebernehmen(finding, doc, basis):
    """Angaben aus dem Dokument (services/angaben.py) in die Stammdaten, soweit es Spalten gibt:
    „gültig bis“ -> Document.valid_until, nur wenn dort noch nichts steht."""
    angaben.fuelle(finding, finding.item, basis)  # von Hand eingefügt: noch nicht gelesen
    bis = (finding.angaben or {}).get("gueltig_bis") or {}
    if bis.get("status") == "datum" and bis.get("wert") and doc.valid_until is None:
        doc.valid_until = date.fromisoformat(bis["wert"])


def _hersteller(name):
    if not normalisiere(name):
        return None
    m = db.session.scalar(db.select(Manufacturer).filter_by(normalized_name=normalisiere(name)))
    if m is None:
        m = Manufacturer(name=name.strip(), normalized_name=normalisiere(name))
        db.session.add(m)
    return m


def _sha256(pfad):
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
