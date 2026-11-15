"""Dokumentensuche für einen Vorgang starten (M11, M14)."""

from flask import Blueprint, abort, current_app, request

from ..db import db
from ..models import Bom
from ..services import email_entwurf, lesepuffer, sprachversionen, zertifikatsserver
from ..services.erledigt import Fehler
from ..services.internet import OBERGRENZE
from ..services.suche import SuchFehler, suche_vorgang
from ..services import modelle  # Sprachmodell und Websuche-Versuche je Vorgang

bp = Blueprint("suche", __name__, url_prefix="/api/boms")


@bp.get("/search-settings")
def einstellungen():
    """Standard und Obergrenze der Web-Anfragen je Position für die Oberfläche."""
    return {"internet_versuche": current_app.config["INTERNET_VERSUCHE_PRO_TEIL"],
            "internet_versuche_max": OBERGRENZE}


@bp.post("/<int:bom_id>/search")
def suchen(bom_id):
    """Sucht für alle Positionen die Ex-Dokumente und legt Vorschläge an.

    Anfrage (optional): {"internet_versuche": 0 bis 50}, Web-Anfragen je Position, 0 = ohne
    Internet. Antwort: der Vorgang wie bei GET /api/boms/<id>. Die Vorschläge hängen an den
    Positionen.
    """
    bom = db.session.get(Bom, bom_id) or abort(404)
    daten = request.get_json(silent=True)
    versuche = daten.get("internet_versuche") if isinstance(daten, dict) else None
    if versuche is not None and (type(versuche) is not int or not 0 <= versuche <= OBERGRENZE):
        return {"error": f"Bitte geben Sie bei den Versuchen im Internet eine ganze Zahl von 0 "
                         f"bis {OBERGRENZE} ein."}, 400
    versuche = modelle.fuer_lauf(bom, versuche)  # Set-Point: Modell + Versuche des Vorgangs
    try:
        suche_vorgang(bom, versuche)
    except SuchFehler as e:
        return {"error": str(e)}, 503
    db.session.commit()
    return bom.to_dict(detail=True)


@bp.get("/<int:bom_id>/sprachen")
def sprachen(bom_id):
    """Sprachversionen je Position (M8): Pflichtsprachen, je Dokumentart die vorhandenen
    Sprachen aus Vorschlägen und Freigaben und unter fehlende_sprachen die Lücken."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    with lesepuffer.nur_lesen():  # Bauteile und Funde gesammelt laden (services/lesepuffer.py)
        return sprachversionen.uebersicht(lesepuffer.lade(bom))


@bp.get("/<int:bom_id>/veraltet")
def veraltet(bom_id):
    """Positionen, zu denen ein im Zertifikatsserver bestätigt veraltetes Dokument gehört."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    try:
        return zertifikatsserver.ansicht(bom)
    except Fehler as e:
        return {"error": str(e)}, e.code


@bp.post("/<int:bom_id>/veraltet/suche")
def veraltet_neu_suchen(bom_id):
    """Neue Suche für diese Positionen. Anfrage: {"by", "item_ids"?, "internet_versuche"?}."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    daten = request.get_json(silent=True) or {}
    versuche = daten.get("internet_versuche")
    if versuche is not None and (type(versuche) is not int or not 0 <= versuche <= OBERGRENZE):
        return {"error": f"Bitte geben Sie bei den Versuchen im Internet eine ganze Zahl von 0 "
                         f"bis {OBERGRENZE} ein."}, 400
    versuche = modelle.fuer_lauf(bom, versuche)  # Set-Point: Modell + Versuche des Vorgangs
    try:
        items = zertifikatsserver.suche_neu(bom, daten, versuche)
    except Fehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    except SuchFehler as e:
        db.session.rollback()
        return {"error": str(e)}, 503
    email_entwurf.lege_entwuerfe_an(bom)  # ohne Ersatzfund: Anfrage an den Hersteller als Entwurf
    db.session.commit()
    return {"items": [i.to_dict() for i in items]}
