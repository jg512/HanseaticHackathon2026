"""Vorgänge: Stückliste hochladen, auflisten, ansehen, EX-Bauteil-Liste."""

import hashlib

from flask import Blueprint, abort, request

from ..db import db
from ..models import Bom, BomIssue, BomItem
from ..services import erledigt, ex_liste, excel_export, lesepuffer, status
from ..services.excel_import import ImportFehler, lese_stueckliste

bp = Blueprint("boms", __name__, url_prefix="/api/boms")


@bp.post("")
def upload():
    datei = request.files.get("file")
    if datei is None or not datei.filename:
        return {"error": "Es wurde keine Datei mitgeschickt. Bitte wählen Sie die Excel-Datei "
                         "mit der Stückliste aus."}, 400
    inhalt = datei.read()
    try:
        ergebnis = lese_stueckliste(inhalt)
    except ImportFehler as e:
        return {"error": str(e)}, 400

    hash_ = hashlib.sha256(inhalt).hexdigest()
    frueher = db.session.scalar(db.select(Bom).filter_by(source_file_hash=hash_)
                                .order_by(Bom.created_at))
    bom = Bom(source_file_name=datei.filename, source_file_hash=hash_, **ergebnis.meta)
    bom.items = [BomItem(**i) for i in ergebnis.items]
    bom.issues = [BomIssue(**i) for i in ergebnis.issues]
    if frueher:  # erlaubt, aber nicht still: vielleicht ein versehentlich doppelter Upload
        bom.issues.append(BomIssue(
            code="datei_doppelt", severity="info",
            message=f"Dieselbe Datei wurde schon als Vorgang {frueher.id} hochgeladen "
                    f"({frueher.created_at:%d.%m.%Y}, {frueher.source_file_name})."))
    db.session.add(bom)
    db.session.flush()
    status.aktualisiere_alle(bom)
    db.session.commit()
    return bom.to_dict(detail=True), 201


@bp.get("")
def liste():
    # Stammdaten gelten für alle Vorgänge: eine Freigabe in einem ändert den Stand in anderen.
    # Der Stand wird deshalb frisch abgeleitet, nur lesend (services/lesepuffer.py).
    with lesepuffer.nur_lesen():
        boms = lesepuffer.vorgaenge(Bom.created_at.desc(), meldungen=True)
        for b in boms:
            status.aktualisiere_alle(b)
        return [b.to_dict() for b in boms]


@bp.get("/<int:bom_id>")
def detail(bom_id):
    bom = db.session.get(Bom, bom_id) or abort(404)
    with lesepuffer.nur_lesen():  # Stand frisch, nur lesend (services/lesepuffer.py)
        status.aktualisiere_alle(lesepuffer.lade(bom))
        return bom.to_dict(detail=True)


@bp.post("/<int:bom_id>/items/<int:item_id>/ex-teil")
def ex_teil(bom_id, item_id):
    """Ein Mensch entscheidet, ob die Position ein Ex-Teil ist (M3, E1).

    Anfrage: {"ex_teil": false | true | null, "by": "Name"}. false nimmt die Position aus der
    Suche, true bestätigt sie als Ex-Teil, null nimmt die Entscheidung zurück.
    """
    item = db.session.get(BomItem, item_id)
    if item is None or item.bom_id != bom_id:
        abort(404)
    try:
        erledigt.entscheide_ex_teil(item, request.get_json(silent=True) or {})
    except erledigt.Fehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    db.session.commit()
    return item.to_dict()


@bp.get("/<int:bom_id>/ex-liste")
def ex_bauteil_liste(bom_id):
    """EX-Bauteil-Liste (E1): jede Position mit Vorschlag, Begründung, Quelle, Entscheidung."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    with lesepuffer.nur_lesen():  # Stand frisch, nur lesend (services/lesepuffer.py)
        status.aktualisiere_alle(lesepuffer.lade(bom))
        return ex_liste.liste(bom) | {"file_name": excel_export.dateiname(bom, "EX-Bauteil-Liste")}
