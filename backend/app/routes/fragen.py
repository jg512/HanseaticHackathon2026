"""Offene Fragen je Vorgang: anlegen, kommentieren, klären, wieder öffnen."""

from flask import Blueprint, abort, request

from ..db import db
from ..models import Bom, Question
from ..services import fragen
from ..services.erledigt import Fehler

bp = Blueprint("fragen", __name__, url_prefix="/api")


def _ausfuehren(schritt, objekt, code=200):
    """Führt schritt(objekt, Anfrage) aus; Antwort ist die Frage, die schritt zurückgibt oder ändert."""
    try:
        frage = schritt(objekt, request.get_json(silent=True) or {}) or objekt
    except Fehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    db.session.commit()
    return frage.to_dict(), code


def _frage(question_id):
    return db.session.get(Question, question_id) or abort(404)


@bp.get("/boms/<int:bom_id>/questions")
def liste(bom_id):
    """Alle Fragen des Vorgangs mit Kommentaren, offene zuerst."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    return [q.to_dict() for q in fragen.liste(bom)]


@bp.post("/boms/<int:bom_id>/questions")
def anlegen(bom_id):
    """Anfrage: {"by": "Name", "text": "Frage", "item_id": optional}. 201: die Frage."""
    return _ausfuehren(fragen.lege_an, db.session.get(Bom, bom_id) or abort(404), 201)


@bp.post("/questions/<int:question_id>/comments")
def kommentieren(question_id):
    """Anfrage: {"by": "Name", "text": "Kommentar"}. 201: die Frage mit allen Kommentaren."""
    return _ausfuehren(fragen.kommentiere, _frage(question_id), 201)


@bp.post("/questions/<int:question_id>/resolve")
def klaeren(question_id):
    """Anfrage: {"by": "Name", "answer": "Antwort"}. Antwort: die Frage."""
    return _ausfuehren(fragen.klaere, _frage(question_id))


@bp.post("/questions/<int:question_id>/reopen")
def oeffnen(question_id):
    """Anfrage: {"by": "Name"}. Antwort: die Frage."""
    return _ausfuehren(fragen.oeffne, _frage(question_id))
