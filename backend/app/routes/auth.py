"""Anmeldung (/api/auth/…) und Benutzerverwaltung für Admins (/api/benutzer).

Fehler mit beiden Schlüsseln {"fehler", "error"}: die Oberfläche liest den einen, ältere Routen
den anderen. Prüfung von Sitzung und Rolle für alle anderen Routen steht in app/auth.py.
"""

import time
from urllib.parse import quote

from flask import Blueprint, current_app, g, request, session

from .. import auth
from ..db import db
from ..models.benutzer import Benutzer
from ..services import benutzer as dienst
from ..services.erledigt import Fehler

bp = Blueprint("auth", __name__)


@bp.errorhandler(Fehler)
def _fehler(e):
    return {"fehler": str(e), "error": str(e)}, e.code


def _daten():
    daten = request.get_json(silent=True)
    return daten if isinstance(daten, dict) else {}


def _ich(b):
    return {"id": b.id, "login": b.login, "name": b.name, "rolle": b.rolle,
            "rolle_text": b.to_dict()["rolle_text"]}


@bp.post("/api/auth/login")
def login():
    """{"login", "passwort"} -> wer angemeldet ist. 401 mit immer gleichem Text, 429 gesperrt."""
    daten = _daten()
    try:
        b = dienst.melde_an(daten.get("login"), daten.get("passwort"))
    except Fehler:
        time.sleep(current_app.config["LOGIN_VERZOEGERUNG"])  # bremst Durchprobieren
        raise
    db.session.commit()
    auth.sitzung_starten(b)
    current_app.logger.info("Anmeldung: %s", b.login)
    return _ich(b)


@bp.post("/api/auth/logout")
def logout():
    session.clear()
    return {"ok": True}


@bp.get("/api/auth/ich")
def ich():
    """Wer angemeldet ist; ohne Anmeldung 401 (app/auth.py). Ohne LOGIN_PFLICHT und ohne
    Anmeldung: {"login": null, "rolle": null} (Entwicklung)."""
    if g.benutzer is None:
        if current_app.config["LOGIN_PFLICHT"]:
            return {"fehler": auth.NICHT_ANGEMELDET, "error": auth.NICHT_ANGEMELDET}, 401
        return {"id": None, "login": None, "name": None, "rolle": None, "rolle_text": None}
    return _ich(g.benutzer)


@bp.post("/api/auth/passwort")
def passwort():
    """{"alt", "neu"}: eigenes Passwort ändern. Andere Sitzungen dieses Kontos enden."""
    if g.benutzer is None:
        return {"fehler": auth.NICHT_ANGEMELDET, "error": auth.NICHT_ANGEMELDET}, 401
    daten = _daten()
    dienst.aendere_passwort(g.benutzer, daten.get("alt"), daten.get("neu"))
    db.session.commit()
    auth.sitzung_starten(g.benutzer)
    return {"ok": True}


@bp.get("/api/auth/pruefe")
def pruefe():
    """Für nginx (auth_request vor /zert/): 204 mit dem Namen im Kopf X-Anwender-Name
    (URL-kodiert), 401 ohne Anmeldung (schon in app/auth.py), 403, wenn ein Leser schreiben will.
    Die Methode der eigentlichen Anfrage schickt nginx im Kopf X-Original-Method."""
    methode = request.headers.get("X-Original-Method", "GET").upper()
    if g.benutzer is None:
        return "", 204  # nur ohne LOGIN_PFLICHT
    if g.benutzer.rolle == "leser" and methode not in auth.LESEND:
        return {"fehler": auth.NUR_LESEN, "error": auth.NUR_LESEN}, 403
    return "", 204, {"X-Anwender-Name": quote(g.benutzer.name, safe="")}


# --- Benutzerverwaltung, nur Admins -----------------------------------------------------------

def _nur_admin():
    if g.benutzer is None:
        raise Fehler(auth.NICHT_ANGEMELDET, 401)
    if g.benutzer.rolle != "admin":
        raise Fehler("Das darf nur die Administration.", 403)


@bp.get("/api/benutzer")
def benutzer_liste():
    _nur_admin()
    return [b.to_dict() for b in dienst.liste()]


@bp.post("/api/benutzer")
def benutzer_anlegen():
    """{"login", "name", "rolle", "passwort"} -> 201 das Konto (ohne Passwort)."""
    _nur_admin()
    b = dienst.lege_an(_daten(), von=g.benutzer.name)
    db.session.commit()
    current_app.logger.info("Konto angelegt: %s (%s) von %s", b.login, b.rolle, g.benutzer.login)
    return b.to_dict(), 201


@bp.patch("/api/benutzer/<int:benutzer_id>")
def benutzer_aendern(benutzer_id):
    """{"name"?, "rolle"?, "aktiv"?, "passwort"?} (Passwort zurücksetzen). 409: letzter Admin."""
    _nur_admin()
    b = db.session.get(Benutzer, benutzer_id)
    if b is None:
        raise Fehler("Diesen Zugang gibt es nicht. Bitte laden Sie die Seite neu.", 404)
    daten = _daten()
    daten.pop("by", None)  # vom Namen-Einsetzen (app/auth.py), gehört nicht zum Konto
    try:
        dienst.aendere(b, daten)
    except Fehler:
        db.session.rollback()
        raise
    db.session.commit()
    if b.id == g.benutzer.id and b.aktiv:
        auth.sitzung_starten(b)  # eigenes Passwort zurückgesetzt: angemeldet bleiben
    current_app.logger.info("Konto geändert: %s (%s) von %s", b.login,
                            ", ".join(sorted(daten)), g.benutzer.login)
    return b.to_dict()
