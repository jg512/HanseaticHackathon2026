"""Von Hand erledigen: offene Positionen und Meldungen der Eingangsprüfung, und zurücknehmen."""

from flask import Blueprint, abort, request

from ..db import db
from ..models import BomIssue, BomItem
from ..services import erledigt

bp = Blueprint("erledigt", __name__, url_prefix="/api/boms")


def _ausfuehren(schritt, objekt):
    try:
        schritt(objekt, request.get_json(silent=True) or {})
    except erledigt.Fehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    db.session.commit()
    return objekt.to_dict()


def _position(bom_id, item_id):
    item = db.session.get(BomItem, item_id)
    if item is None or item.bom_id != bom_id:
        abort(404)
    return item


def _meldung(bom_id, issue_id):
    issue = db.session.get(BomIssue, issue_id)
    if issue is None or issue.bom_id != bom_id:
        abort(404)
    return issue


@bp.post("/<int:bom_id>/items/<int:item_id>/resolve")
def position_erledigen(bom_id, item_id):
    """Anfrage: {"by": "Name", "note": "Begründung"}. Antwort: die Position."""
    return _ausfuehren(erledigt.erledige_position, _position(bom_id, item_id))


@bp.post("/<int:bom_id>/items/<int:item_id>/reopen")
def position_oeffnen(bom_id, item_id):
    """Anfrage: {"by": "Name"}. Antwort: die Position mit neu abgeleitetem Stand."""
    return _ausfuehren(erledigt.oeffne_position, _position(bom_id, item_id))


@bp.post("/<int:bom_id>/issues/<int:issue_id>/resolve")
def meldung_abhaken(bom_id, issue_id):
    """Anfrage: {"by": "Name", "comment": optional}. Antwort: die Meldung."""
    return _ausfuehren(erledigt.hake_ab, _meldung(bom_id, issue_id))


@bp.post("/<int:bom_id>/issues/<int:issue_id>/reopen")
def meldung_oeffnen(bom_id, issue_id):
    """Anfrage: {"by": "Name"}. Antwort: die Meldung."""
    return _ausfuehren(erledigt.oeffne_meldung, _meldung(bom_id, issue_id))
