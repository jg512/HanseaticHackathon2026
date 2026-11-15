"""Herstellerkontakte: E-Mail-Adresse je Hersteller ansehen, ändern, importieren, exportieren.

Jede schreibende Anfrage braucht „by“ (Name); jede Änderung steht im Änderungsprotokoll.
Fachlogik in services/kontakte.py. Fehler als {"error": "…"} wie die Stammdaten.
"""

import io

from flask import Blueprint, request, send_file

from ..db import db
from ..models import Manufacturer
from ..services import kontakte
from ..services.erledigt import Fehler

bp = Blueprint("kontakte", __name__, url_prefix="/api/kontakte")


def _daten():
    daten = request.get_json(silent=True)
    return daten if isinstance(daten, dict) else {}


def _ja_nein(name):
    wert = (request.args.get(name) or "").lower()
    return True if wert in ("1", "true", "ja") else False if wert in ("0", "false", "nein") else None


def _ausfuehren(schritt, *args):
    try:
        ergebnis = schritt(*args)
    except Fehler as e:
        db.session.rollback()
        return None, ({"error": str(e)}, e.code)
    db.session.commit()
    return ergebnis, None


@bp.get("")
def liste():
    """Filter: fehlt=1 (nur ohne Adresse) bzw. fehlt=0, q (Name, Adresse, Ansprechpartner)."""
    return kontakte.liste(request.args.get("q"), _ja_nein("fehlt"))


@bp.post("")
def anlegen():
    """{"hersteller", "email", "ansprechpartner"?, "sprache"?, "notiz"?, "by"}; 201, wenn der
    Hersteller dabei in den Stammdaten angelegt wurde."""
    ergebnis, fehler = _ausfuehren(kontakte.lege_an, _daten())
    if fehler:
        return fehler
    d, neu = ergebnis
    return d, 201 if neu else 200


@bp.put("/<int:hersteller_id>")
def setzen(hersteller_id):
    """{"email", "ansprechpartner"?, "sprache"?, "notiz"?, "by"}; leere E-Mail entfernt sie."""
    m = db.session.get(Manufacturer, hersteller_id)
    if m is None:
        return {"error": "Diesen Hersteller gibt es nicht mehr. Bitte laden Sie die Liste neu."}, 404
    ergebnis, fehler = _ausfuehren(kontakte.setze, m, _daten())
    return fehler or ergebnis


@bp.post("/import")
def importieren():
    """multipart: datei (oder file) als .xlsx oder .csv, by (Name)."""
    datei = request.files.get("datei") or request.files.get("file")
    if datei is None or not datei.filename:
        return {"error": "Es wurde keine Datei mitgeschickt. Bitte wählen Sie eine Excel- oder "
                         "CSV-Datei aus."}, 400
    daten = request.form.to_dict()
    daten.setdefault("by", request.headers.get("X-Anwender", ""))
    ergebnis, fehler = _ausfuehren(kontakte.importiere, datei.read(), datei.filename, daten)
    return fehler or ergebnis


@bp.get("/export.xlsx")
def export():
    return send_file(io.BytesIO(kontakte.export()), mimetype=kontakte.XLSX, as_attachment=True,
                     download_name="Herstellerkontakte.xlsx")
