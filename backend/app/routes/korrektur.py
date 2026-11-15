"""Korrekturschleife (E5): Mängel ansehen, Positionen korrigieren, korrigierte neu suchen.

Die Mängelliste an den Einkauf liegt bei den E-Mails (routes/emails.py).
"""

from flask import Blueprint, abort, request

from ..db import db
from ..models import Bom, BomItem
from ..services import korrektur, lesepuffer, status
from ..services import modelle  # Sprachmodell und Websuche-Versuche je Vorgang
from ..services.excel_import import ImportFehler
from ..services.internet import OBERGRENZE
from ..services.suche import SuchFehler

bp = Blueprint("korrektur", __name__, url_prefix="/api/boms")


@bp.get("/<int:bom_id>/korrektur")
def ansicht(bom_id):
    """Mängel, Mängellisten, bisherige Korrekturen und die neu zu suchenden Positionen."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    # Stand frisch wie in der Vorgangsansicht, nur lesend (services/lesepuffer.py): die
    # Ansichten speichern ihn nicht mehr, eine Freigabe anderswo soll hier trotzdem gelten
    with lesepuffer.nur_lesen():
        status.aktualisiere_alle(lesepuffer.lade(bom))
        return korrektur.ansicht(bom)


@bp.post("/<int:bom_id>/items/<int:item_id>/korrektur")
def korrigieren(bom_id, item_id):
    """Anfrage: {"by": "Name", "note": optional, und Felder aus korrektur.FELDER}.

    Antwort: die Position mit neu abgeleitetem Stand und unter „corrections“ ihre Korrekturen.
    """
    item = db.session.get(BomItem, item_id)
    if item is None or item.bom_id != bom_id:
        abort(404)
    try:
        korrektur.korrigiere(item, request.get_json(silent=True) or {})
    except korrektur.Fehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    db.session.commit()
    return item.to_dict() | {"corrections": [k.to_dict() for k in item.corrections]}


@bp.post("/<int:bom_id>/korrektur/suche")
def neu_suchen(bom_id):
    """Sucht nur korrigierte Positionen neu.

    Anfrage (optional): {"item_ids": [...], "internet_versuche": 0 bis 50}. Ohne item_ids alle
    korrigierten, die seit der Korrektur nicht gesucht wurden. Antwort: die gesuchten Positionen.
    """
    bom = db.session.get(Bom, bom_id) or abort(404)
    daten = request.get_json(silent=True)
    daten = daten if isinstance(daten, dict) else {}
    ids, versuche = daten.get("item_ids"), daten.get("internet_versuche")
    if ids is not None and not (isinstance(ids, list) and all(type(i) is int for i in ids)):
        return {"error": "Die Auswahl der Positionen ist ungültig. Bitte laden Sie die Seite neu "
                         "und versuchen Sie es noch einmal."}, 400
    if versuche is not None and (type(versuche) is not int or not 0 <= versuche <= OBERGRENZE):
        return {"error": f"Bitte geben Sie bei den Versuchen im Internet eine ganze Zahl von 0 "
                         f"bis {OBERGRENZE} ein."}, 400
    versuche = modelle.fuer_lauf(bom, versuche)  # Set-Point: Modell + Versuche des Vorgangs
    try:
        items = korrektur.suche_korrigierte(bom, ids, versuche)
    except korrektur.Fehler as e:
        return {"error": str(e)}, e.code
    except SuchFehler as e:
        db.session.rollback()
        return {"error": str(e)}, 503
    db.session.commit()
    return [i.to_dict() for i in items]


@bp.post("/<int:bom_id>/korrektur/excel")
def excel(bom_id):
    """Korrigierte Stückliste: Datei im Feld „file“, Name im Feld „by“ (multipart/form-data).

    Antwort: {"changed": Positionen, "unknown_pos", "missing_pos"}.
    """
    bom = db.session.get(Bom, bom_id) or abort(404)
    datei = request.files.get("file")
    if datei is None or not datei.filename:
        return {"error": "Es wurde keine Datei mitgeschickt. Bitte wählen Sie die korrigierte "
                         "Stückliste aus."}, 400
    try:
        ergebnis = korrektur.korrigiere_aus_excel(bom, datei.read(), datei.filename,
                                                  request.form.to_dict())
    except ImportFehler as e:
        db.session.rollback()
        return {"error": str(e)}, 400
    except korrektur.Fehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    db.session.commit()
    return ergebnis
