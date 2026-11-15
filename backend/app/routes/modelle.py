"""Sprachmodelle ansehen und testen, Einstellungen eines Vorgangs (Modell, Websuche).

Keine Antwort enthält einen Schlüssel, nur ob einer hinterlegt ist. Eingerichtet werden Modelle
nur in der Datei (MODELLE_DATEI), nie über die Oberfläche.
"""

from flask import Blueprint, abort, g, request

from .. import llm
from ..db import db
from ..models import Bom
from ..services import modelle, oberflaeche

bp = Blueprint("modelle", __name__)


@bp.get("/api/modelle")
def liste():
    """Eingerichtete Modelle ohne Schlüssel, Standard je Aufgabe und welche Aufgabe eins nutzt."""
    nutzung = modelle.genutzt()
    try:
        b = modelle.bestand()
    except modelle.KonfigFehler as e:  # Einzelheiten für den Administrator in „detail“
        return {"error": modelle.KAPUTT, "detail": str(e), "datei": modelle._datei(),
                "sprachmodell_genutzt": nutzung}, 503
    return {"quelle": b.quelle, "datei": b.datei, "standard": b.standard,
            "aufgaben": modelle.AUFGABE_TEXT, "sprachmodell_genutzt": nutzung,
            "hinweise": modelle.hinweise(nutzung),
            "modelle": [m.oeffentlich(b.standard_fuer(m.name)) for m in b.modelle.values()]}


@bp.post("/api/modelle/<name>/test")
def teste(name):
    """Kleine Anfrage an das Modell: erreichbar ja/nein, Dauer, Fehlertext ohne Schlüssel.
    Nur Admins (ohne LOGIN_PFLICHT, also ohne Benutzer, wie bisher offen)."""
    benutzer = getattr(g, "benutzer", None)
    if benutzer is not None and benutzer.rolle != "admin":
        return {"error": "Die Verbindung testen darf nur die Administration."}, 403
    try:
        return llm.teste(name)
    except modelle.KonfigFehler as e:
        return {"error": modelle.KAPUTT, "detail": str(e)}, 503
    except KeyError:
        return {"error": f"Das Sprachmodell „{name}“ ist nicht eingerichtet. Bitte laden Sie "
                         "die Seite neu."}, 404


def _von(daten):
    for wert in (daten.get("von"), daten.get("by"), request.headers.get("X-Anwender")):
        if isinstance(wert, str) and wert.strip():
            return wert.strip()[:200]
    return None


@bp.get("/api/vorgaenge/<int:bom_id>/einstellungen")
def einstellungen(bom_id):
    """Sprachmodell und Websuche-Versuche des Vorgangs, Standard, Grenze und was wirkt."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    return modelle.einstellungen(bom, oberflaeche.laeuft(bom_id))


@bp.put("/api/vorgaenge/<int:bom_id>/einstellungen")
def aendere(bom_id):
    """Anfrage: {"von", "modell"?, "internet_versuche"?}; null oder "" = Standard."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    daten = request.get_json(silent=True)
    daten = daten if isinstance(daten, dict) else {}
    if oberflaeche.laeuft(bom_id):
        return {"error": "Der Agent sucht gerade. Bitte ändern Sie die Einstellungen, wenn er "
                         "fertig ist."}, 409
    try:
        modelle.aendere(bom, daten, _von(daten))
    except modelle.EingabeFehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    return modelle.einstellungen(bom)
