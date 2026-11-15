"""Bearbeiten in den Tabellen der Oberfläche (web/tabelle.js): Vorgang umbenennen, Position
korrigieren (auch Menge), gelesene Werte eines Funds korrigieren.

Die Oberfläche speichert erst nach der Bestätigung eines Menschen; jede Anfrage braucht seinen
Namen („von“ oder „by“). Fehler wie im Vertrag der Oberfläche als {"fehler": "…"}.
"""

from flask import Blueprint, request

from ..db import db
from ..models import Bom, BomItem, Finding
from ..services import bearbeiten, status
from ..services.oberflaeche import artikel_kurz, vorgang

bp = Blueprint("bearbeiten", __name__)


@bp.errorhandler(bearbeiten.Fehler)
def _fehler(e):
    db.session.rollback()
    return {"fehler": str(e)}, e.code


def _hole(modell, id_):
    obj = db.session.get(modell, id_)
    if obj is None:
        raise bearbeiten.Fehler("Diesen Eintrag gibt es nicht mehr. Bitte laden Sie die Seite neu.", 404)
    return obj


def _json():
    """JSON der Anfrage; ohne „von“/„by“ gilt der Kopf „X-Anwender“ wie in routes/oberflaeche.py."""
    daten = request.get_json(silent=True)
    daten = daten if isinstance(daten, dict) else {}
    if not (daten.get("von") or daten.get("by")) and request.headers.get("X-Anwender"):
        daten["von"] = request.headers["X-Anwender"]
    return daten


@bp.patch("/api/vorgaenge/<int:bom_id>")
def vorgang_aendern(bom_id):
    """{"von", "name"}: Vorgang umbenennen. Antwort: der Vorgang ohne Artikel; „name“ ist der
    gespeicherte Name (bei Gleichstand mit „ (2)“ …), „gewuenscht“ der geschickte."""
    bom, daten = _hole(Bom, bom_id), _json()
    bearbeiten.benenne_um(bom, daten)
    db.session.commit()
    return vorgang(bom, kurz=True) | {"gewuenscht": str(daten.get("name") or "").strip()}


@bp.patch("/api/artikel/<int:item_id>")
def artikel_aendern(item_id):
    """{"von", "note"?, Felder aus korrektur.FELDER}: Position korrigieren.

    Antwort: der Artikel wie in der Liste des Vorgangs und unter „korrekturen“ die neuen.
    """
    item = _hole(BomItem, item_id)
    neu = bearbeiten.korrigiere_position(item, _json())
    db.session.commit()
    return artikel_kurz(item) | {"korrekturen": [k.to_dict() for k in neu]}


@bp.get("/api/artikel/<int:item_id>/fundangaben")
def fundangaben(item_id):
    """Korrigierbare Werte je Fund dieser Position: {"<fund_id>": {offen, status, felder}}."""
    item = _hole(BomItem, item_id)
    return {str(f.id): bearbeiten.fund_angaben(f) for f in item.findings}


@bp.patch("/api/funde/<int:finding_id>")
def fund_aendern(finding_id):
    """{"von", "note"?, Werte aus bearbeiten.FUND_FELDER}: gelesene Werte korrigieren, nur
    solange der Fund unentschieden ist (sonst 409). Antwort: die Werte wie bei fundangaben."""
    finding = _hole(Finding, finding_id)
    bearbeiten.korrigiere_fund(finding, _json())
    # z. B. Kennzeichnung in Klammern: ändert die Ex-Einordnung; die Ansichten speichern den
    # Stand nicht mehr (services/lesepuffer.py), deshalb hier
    status.aktualisiere(finding.item)
    db.session.commit()
    return bearbeiten.fund_angaben(finding)
