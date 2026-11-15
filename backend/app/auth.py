"""Anmeldung an EINER Stelle: Sitzung prüfen, Rechte prüfen, Namen setzen.

- Jede Anfrage außer OFFEN braucht eine Anmeldung (LOGIN_PFLICHT, Standard an); sonst 401.
- Rolle „leser“ darf nur lesen (GET, HEAD, OPTIONS); schreiben gibt 403. Abmelden und das eigene
  Passwort ändern darf jeder.
- Wer handelt, steht in der Sitzung, nicht in der Anfrage: für angemeldete Benutzer ersetzt
  AnfrageMitAnwender „by“ (immer) und „von“ (wenn mitgeschickt) im JSON und im Formular sowie den
  Kopf „X-Anwender“ durch den Anzeigenamen. So schreibt jede Route, auch künftige, den richtigen
  Namen, ohne selbst etwas zu tun; anwender() liefert ihn direkt.
- Sitzung: signiertes Cookie von Flask (HttpOnly, SameSite=Lax), gültig SITZUNG_STUNDEN ab der
  letzten Anfrage. Neues Passwort oder Deaktivierung beenden ältere Sitzungen (sitzungs_stand).
"""

import time
from datetime import timedelta

import click
from flask import current_app, g, has_app_context, request, session
from flask.wrappers import Request
from werkzeug.datastructures import ImmutableMultiDict, MultiDict

from .db import db

# Ohne Anmeldung erreichbar: Lebenszeichen für Docker, die Anmeldung selbst, Abmelden (harmlos)
OFFEN = {"/health", "/api/auth/login", "/api/auth/logout"}
LESEND = {"GET", "HEAD", "OPTIONS"}
ENTWICKLUNGS_SCHLUESSEL = "entwicklung-nicht-geheim"  # auch der Standard in docker-compose.yml
NICHT_ANGEMELDET = "Sie sind nicht angemeldet. Bitte melden Sie sich an."
NUR_LESEN = ("Mit Ihrem Zugang können Sie nur ansehen, nichts ändern. Wenn Sie mehr dürfen "
             "sollen, wenden Sie sich an die Administration.")


def anwender():
    """Anzeigename des angemeldeten Benutzers oder None (ohne Anmeldung, LOGIN_PFLICHT aus)."""
    return g.get("anwender") if has_app_context() else None


def _fehler(text, code):
    return {"fehler": text, "error": text}, code


class AnfrageMitAnwender(Request):
    """Request, der den Namen aus der Sitzung in JSON und Formular einsetzt (siehe oben)."""

    def _name_fuer_schreiben(self):
        return None if self.method in LESEND else anwender()

    def get_json(self, force=False, silent=False, cache=True):
        daten = super().get_json(force=force, silent=silent, cache=cache)
        name = self._name_fuer_schreiben()
        if name is None:
            return daten
        if daten is None:  # ohne Körper (silent): nur der Name
            return {"by": name}
        if isinstance(daten, dict):
            daten["by"] = name  # der Cache liefert dasselbe Objekt, also überall derselbe Name
            if "von" in daten:
                daten["von"] = name
        return daten

    def _load_form_data(self):
        super()._load_form_data()
        name = self._name_fuer_schreiben()
        if name is None:
            return
        form = MultiDict(self.__dict__["form"])
        form["by"] = name
        if "von" in form:
            form["von"] = name
        self.__dict__["form"] = ImmutableMultiDict(form)


def _sitzung_laden():
    """Benutzer aus der Sitzung oder None; abgelaufene oder ungültige Sitzungen werden geleert."""
    from .models.benutzer import Benutzer
    uid = session.get("uid")
    if uid is None:
        return None
    b = db.session.get(Benutzer, uid)
    abgelaufen = time.time() - session.get("seit", 0) > current_app.permanent_session_lifetime \
        .total_seconds()
    if b is None or not b.aktiv or session.get("stand") != b.sitzungs_stand or abgelaufen:
        session.clear()
        return None
    # Wer arbeitet, bleibt angemeldet: die Frist zählt ab der letzten Anfrage (höchstens einmal
    # je Minute neu gesetzt, damit nicht jede Antwort ein neues Cookie trägt)
    if time.time() - session.get("seit", 0) > 60:
        session["seit"] = int(time.time())
    return b


def sitzung_starten(b):
    session.clear()
    session.permanent = True
    session.update(uid=b.id, stand=b.sitzungs_stand, seit=int(time.time()))


def _pruefen():
    g.benutzer = _sitzung_laden()
    if g.benutzer is not None:
        g.anwender = g.benutzer.name
        request.environ["HTTP_X_ANWENDER"] = g.benutzer.name
    if request.path in OFFEN:
        return None
    if g.benutzer is None:
        if current_app.config["LOGIN_PFLICHT"]:
            return _fehler(NICHT_ANGEMELDET, 401)
        return None
    if request.method not in LESEND:
        # Schutz vor fremden Seiten (CSRF), zusätzlich zu SameSite=Lax: Browser melden die Herkunft
        if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            return _fehler("Diese Änderung kam nicht aus dem ATEX-Agenten und wurde abgelehnt. "
                            "Bitte arbeiten Sie direkt im ATEX-Agenten.", 403)
        if g.benutzer.rolle == "leser" and not request.path.startswith("/api/auth/"):
            return _fehler(NUR_LESEN, 403)
    return None


def einrichten(app):
    """Hängt Anmeldung, Rechte und Befehle an die App. Vor den Blueprints aufrufen."""
    from .models import benutzer as _  # noqa: F401  Tabelle registrieren
    from .routes import auth as auth_routen

    app.request_class = AnfrageMitAnwender
    app.permanent_session_lifetime = timedelta(hours=app.config["SITZUNG_STUNDEN"])
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = ENTWICKLUNGS_SCHLUESSEL
    if app.config["SECRET_KEY"] == ENTWICKLUNGS_SCHLUESSEL and not app.testing:
        app.logger.warning(
            "SECRET_KEY fehlt oder ist der Entwicklungswert: Sitzungen sind fälschbar. "
            "Für jeden Betrieb außer der lokalen Entwicklung SECRET_KEY setzen (.env.example).")
    if not app.config["LOGIN_PFLICHT"] and not app.testing:
        app.logger.warning("LOGIN_PFLICHT=0: die Anwendung ist ohne Anmeldung offen.")
    app.before_request(_pruefen)
    app.register_blueprint(auth_routen.bp)
    if not app.testing:
        _warnung_beim_start(app)

    @app.cli.command("benutzer-anlegen")
    @click.option("--login", prompt="Anmeldename")
    @click.option("--name", prompt="Anzeigename (steht im Protokoll)")
    @click.option("--rolle", type=click.Choice(["admin", "pruefer", "leser"]), default="pruefer",
                  show_default=True, prompt="Rolle")
    @click.option("--passwort", prompt=True, hide_input=True, confirmation_prompt=True,
                  envvar="NEUES_PASSWORT", help="sonst Abfrage; auch aus NEUES_PASSWORT")
    def benutzer_anlegen(login, name, rolle, passwort):
        """Legt ein Benutzerkonto an."""
        from .services import benutzer as dienst
        from .services.erledigt import Fehler
        try:
            b = dienst.lege_an({"login": login, "name": name, "rolle": rolle,
                                "passwort": passwort}, von="Kommandozeile")
        except Fehler as e:
            raise click.ClickException(str(e)) from None
        db.session.commit()
        click.echo(f"Angelegt: {b.login} ({b.name}, {b.rolle})")


def erster_admin():
    """Für init-db: Admin aus ADMIN_BENUTZER, ADMIN_NAME, ADMIN_PASSWORT anlegen, wenn es diesen
    Anmeldenamen noch nicht gibt (auch bei vorhandenen Daten). Ein vorhandenes Konto bleibt, wie
    es ist. Das Passwort steht nie im Protokoll."""
    import os

    from .services import benutzer as dienst
    from .services.erledigt import Fehler
    login, passwort = os.environ.get("ADMIN_BENUTZER", ""), os.environ.get("ADMIN_PASSWORT", "")
    name = os.environ.get("ADMIN_NAME", "") or "Administrator"
    if not (login and passwort):
        if not dienst.anzahl():
            click.echo("Noch kein Benutzerkonto: ADMIN_BENUTZER und ADMIN_PASSWORT setzen oder "
                       "„flask --app wsgi benutzer-anlegen“ ausführen.", err=True)
        return
    try:
        neu = dienst.start_admin(login, name, passwort)
    except Fehler as e:
        db.session.rollback()
        click.echo(f"Admin „{login}“ nicht angelegt: {e}", err=True)
        return
    db.session.commit()
    if neu:
        click.echo(f"Admin angelegt: {login.strip().lower()}")
    if _entwicklungs_admin_aktiv():
        click.echo(ENTWICKLUNGS_ADMIN_WARNUNG, err=True)


# Entwicklungs-Zugang (docker-compose.yml, .env.example): Anmeldename und Passwort gleich
ENTWICKLUNGS_ADMIN = "admin"
ENTWICKLUNGS_ADMIN_WARNUNG = (
    "WARNUNG: Der Entwicklungs-Zugang „admin“ hat noch sein Standardpasswort. Für jeden Betrieb "
    "außer der lokalen Entwicklung das Passwort ändern (Einstellungen) und ADMIN_PASSWORT setzen.")


def _entwicklungs_admin_aktiv():
    """True, solange ein aktives Konto „admin“ das Standardpasswort hat."""
    from .services import benutzer as dienst
    b = dienst.finde(ENTWICKLUNGS_ADMIN)
    return bool(b and b.aktiv and dienst.hat_passwort(b, ENTWICKLUNGS_ADMIN))


def _warnung_beim_start(app):
    """Beim Start (gunicorn lädt wsgi.py): Standardpasswort noch aktiv? Ohne Tabellen still."""
    try:
        with app.app_context():
            if _entwicklungs_admin_aktiv():
                app.logger.warning(ENTWICKLUNGS_ADMIN_WARNUNG)
            db.session.remove()
    except Exception:  # noqa: BLE001  Datenbank noch nicht bereit: init-db warnt ebenfalls
        pass
