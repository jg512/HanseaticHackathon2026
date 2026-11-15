"""Stammdaten ansehen und von Hand pflegen (M18): Bauteile, Dokumente, Zuordnungen, Hersteller.

Jede schreibende Anfrage braucht „by“ (Name); jede Änderung steht im Änderungsprotokoll.
Fachlogik in services/stammdaten.py.
"""

from flask import Blueprint, current_app, request, send_file

from ..db import db
from ..models import Document, Manufacturer, Part, PartDocument
from ..models.stammdaten import DOC_TYPES, LANGUAGES, STATUS
from ..services import freigabe, pdf_text
from ..services import stammdaten as sd
from ..services.erledigt import Fehler

BAUTEIL_WEG = "Dieses Bauteil gibt es nicht mehr. Bitte laden Sie die Liste neu."
DOKUMENT_WEG = "Dieses Dokument gibt es nicht mehr. Bitte laden Sie die Liste neu."
HERSTELLER_WEG = "Diesen Hersteller gibt es nicht mehr. Bitte laden Sie die Liste neu."


bp = Blueprint("stammdaten", __name__, url_prefix="/api/stammdaten")


def _daten():
    daten = request.get_json(silent=True)
    return daten if isinstance(daten, dict) else {}


def _ausfuehren(schritt, *objekte):
    """Führt den Schritt aus und speichert; (None, Fehlerantwort) oder (Ergebnis, None)."""
    try:
        ergebnis = schritt(*objekte, _daten())
    except Fehler as e:
        db.session.rollback()
        return None, ({"error": str(e)}, e.code)
    db.session.commit()
    return ergebnis, None


def _fehlt(text):
    return {"error": text}, 404


def _ja_nein(name):
    """Query-Parameter „1“/„true“ oder „0“/„false“; sonst None (kein Filter)."""
    wert = (request.args.get(name) or "").lower()
    return True if wert in ("1", "true", "ja") else False if wert in ("0", "false", "nein") else None


def _zuordnung(part_id, doc_id):
    if db.session.get(Part, part_id) is None:
        return None, _fehlt(BAUTEIL_WEG)
    pd = db.session.get(PartDocument, (part_id, doc_id))
    return (pd, None) if pd else (None, _fehlt("Das Dokument ist dem Bauteil nicht zugeordnet."))


@bp.get("/werte")
def werte():
    """Feste Werte für Auswahllisten: Dokumentarten, Sprachen, Prüfstatus."""
    return {"doc_types": list(DOC_TYPES), "languages": list(LANGUAGES), "status": list(STATUS)}


# --- Bauteile ----------------------------------------------------------------------------------

@bp.get("/parts")
def bauteile():
    """Bauteile. Filter: q (Suche), manufacturer_id, status, review_needed (1/0)."""
    status_ = request.args.get("status") or None
    if status_ and status_ not in STATUS:
        return {"error": f"Unbekannter Status: {status_}"}, 400
    hersteller_id = request.args.get("manufacturer_id")
    if hersteller_id is not None and not hersteller_id.isdigit():
        return {"error": "manufacturer_id: ganze Zahl."}, 400
    teile = sd.bauteile(request.args.get("q"), int(hersteller_id) if hersteller_id else None,
                        status_, _ja_nein("review_needed"))
    return [sd.bauteil_dict(p) for p in teile]


@bp.post("/parts")
def bauteil_anlegen():
    """Anfrage: {"by", "profile_no", optional name_de, name_en, manufacturer, ...}."""
    part, fehler = _ausfuehren(sd.lege_bauteil_an)
    return fehler or (sd.bauteil_dict(part, detail=True), 201)


@bp.get("/parts/<int:part_id>")
def bauteil(part_id):
    part = db.session.get(Part, part_id)
    return sd.bauteil_dict(part, detail=True) if part else _fehlt(BAUTEIL_WEG)


@bp.patch("/parts/<int:part_id>")
def bauteil_aendern(part_id):
    """Anfrage: {"by", "note" optional, und Felder aus sd.BAUTEIL_FELDER, manufacturer,
    review_needed}. Antwort: das Bauteil mit Dokumenten und Änderungen."""
    part = db.session.get(Part, part_id)
    if part is None:
        return _fehlt(BAUTEIL_WEG)
    _, fehler = _ausfuehren(sd.aendere_bauteil, part)
    return fehler or sd.bauteil_dict(part, detail=True)


@bp.post("/parts/<int:part_id>/documents")
def zuordnen(part_id):
    """Anfrage: {"by", "document_id", "status" optional (Standard ungeprueft), "note"}."""
    part = db.session.get(Part, part_id)
    if part is None:
        return _fehlt(BAUTEIL_WEG)
    n, fehler = _ausfuehren(sd.ordne_zu, part)
    return fehler or (sd.bauteil_dict(part, detail=True) | {"items_updated": n}, 201)


@bp.patch("/parts/<int:part_id>/documents/<int:doc_id>")
def pruefstatus(part_id, doc_id):
    """Anfrage: {"by", "status", "note" optional}. „geprueft“ setzt verified_by und verified_at."""
    pd, fehlt = _zuordnung(part_id, doc_id)
    if fehlt:
        return fehlt
    part = pd.part
    n, fehler = _ausfuehren(sd.setze_pruefstatus, pd)
    return fehler or sd.bauteil_dict(part, detail=True) | {"items_updated": n}


@bp.delete("/parts/<int:part_id>/documents/<int:doc_id>")
def loesen(part_id, doc_id):
    """Zuordnung lösen. Anfrage: {"by", "note" optional}. Das Dokument bleibt."""
    pd, fehlt = _zuordnung(part_id, doc_id)
    if fehlt:
        return fehlt
    part = pd.part
    n, fehler = _ausfuehren(sd.loese, pd)
    return fehler or sd.bauteil_dict(part, detail=True) | {"items_updated": n}


# --- Dokumente ---------------------------------------------------------------------------------

@bp.get("/documents")
def dokumente():
    """Dokumente. Filter: q (Datei, URL, Zertifikats-Nr.), doc_type, unlinked=1 (ohne Bauteil)."""
    doc_type = request.args.get("doc_type") or None
    if doc_type and doc_type not in DOC_TYPES:
        return {"error": f"Unbekannte Dokumentart: {doc_type}"}, 400
    return [sd.dokument_dict(d) for d in sd.dokumente(request.args.get("q"), doc_type,
                                                      bool(_ja_nein("unlinked")))]


@bp.get("/documents/<int:doc_id>")
def dokument(doc_id):
    doc = db.session.get(Document, doc_id)
    if doc is None:
        return _fehlt(DOKUMENT_WEG)
    return sd.dokument_dict(doc) | {
        "changes": [a.to_dict() for a in sd.aenderungen(document_id=doc.id)]}


@bp.patch("/documents/<int:doc_id>")
def dokument_aendern(doc_id):
    """Anfrage: {"by", "note" optional, doc_type, certificate_no, valid_until (JJJJ-MM-TT oder
    leer), languages (Liste)}. Antwort: das Dokument und items_updated."""
    doc = db.session.get(Document, doc_id)
    if doc is None:
        return _fehlt(DOKUMENT_WEG)
    n, fehler = _ausfuehren(sd.aendere_dokument, doc)
    return fehler or sd.dokument_dict(doc) | {
        "changes": [a.to_dict() for a in sd.aenderungen(document_id=doc.id)],
        "items_updated": n}


@bp.delete("/documents/<int:doc_id>")
def dokument_loeschen(doc_id):
    """Nur ohne Zuordnung (sonst 409). Anfrage: {"by", "note" optional}. Die Datei bleibt."""
    doc = db.session.get(Document, doc_id)
    if doc is None:
        return _fehlt(DOKUMENT_WEG)
    _, fehler = _ausfuehren(sd.loesche_dokument, doc)
    return fehler or {"deleted": doc_id}


@bp.get("/documents/<int:doc_id>/file")
def datei(doc_id):
    """Die PDF-Datei des Dokuments, nur innerhalb von ATEX_DOCS (wie bei den Vorschlägen)."""
    doc = db.session.get(Document, doc_id)
    if doc is None:
        return _fehlt(DOKUMENT_WEG)
    pfad = freigabe.datei(doc, current_app.config["ATEX_DOCS"])
    if pfad is None:
        return _fehlt("Datei nicht vorhanden.")
    return send_file(pfad, mimetype="application/pdf", download_name=pdf_text.lesbar(pfad.name))


# --- Hersteller und Protokoll ------------------------------------------------------------------

@bp.get("/manufacturers")
def hersteller():
    return sd.hersteller()


@bp.patch("/manufacturers/<int:manufacturer_id>")
def hersteller_aendern(manufacturer_id):
    """Anfrage: {"by", "name", "note" optional}. 409, wenn es den Namen schon gibt."""
    m = db.session.get(Manufacturer, manufacturer_id)
    if m is None:
        return _fehlt(HERSTELLER_WEG)
    _, fehler = _ausfuehren(sd.aendere_hersteller, m)
    return fehler or sd.hersteller_dict(m)


@bp.get("/changes")
def aenderungen():
    """Änderungsprotokoll, das neueste zuerst. Filter: table, limit (höchstens 500)."""
    limit = request.args.get("limit", "200")
    if not limit.isdigit():
        return {"error": "limit: ganze Zahl."}, 400
    return [a.to_dict() for a in sd.aenderungen(request.args.get("table") or None,
                                                limit=int(limit))]
