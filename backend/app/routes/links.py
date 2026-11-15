"""Suchlinks je Position für den Browser des Anwenders."""

from flask import Blueprint, abort

from ..db import db
from ..models import Bom
from ..services import suchlinks

bp = Blueprint("links", __name__, url_prefix="/api/boms")


@bp.get("/<int:bom_id>/links")
def links(bom_id):
    """Antwort: {"<item_id>": [{"label", "url"}, ...]} für alle Positionen des Vorgangs."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    return {str(i.id): suchlinks.fuer(i) for i in bom.items}
