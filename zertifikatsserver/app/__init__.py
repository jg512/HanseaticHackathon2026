"""Zertifikatsserver (für Anwender „Dokumentenarchiv“): eigene Flask-App mit eigener Datenbank
und eigener Ablage der Dateien."""

import click
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from .config import Config
from .db import db

# Fehler ohne eigene Meldung, in Sätzen für Anwender
FEHLER = {
    400: "Die Anfrage war unvollständig. Bitte laden Sie die Seite neu und versuchen Sie es noch "
         "einmal.",
    404: "Das gesuchte Dokument oder Teil gibt es im Dokumentenarchiv nicht. Bitte laden Sie die "
         "Seite neu.",
    405: "Diese Aktion ist hier nicht möglich. Bitte laden Sie die Seite neu.",
    413: "Die Datei ist zu groß. Das Dokumentenarchiv nimmt Dateien bis {mb} MB an.",
    500: "Im Dokumentenarchiv ist ein Fehler aufgetreten. Bitte versuchen Sie es später noch "
         "einmal.",
}


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    db.init_app(app)
    from . import modelle  # noqa: F401  Tabellen registrieren
    from .routen import bp
    app.register_blueprint(bp)

    @app.errorhandler(HTTPException)
    def http_fehler(e):
        """Auch Fehler als JSON, nie als HTML-Seite."""
        mb = (app.config.get("MAX_CONTENT_LENGTH") or 0) // (1024 * 1024)
        return {"error": FEHLER.get(e.code, FEHLER[400]).format(mb=mb)}, e.code

    @app.errorhandler(500)
    def server_fehler(e):
        return {"error": FEHLER[500]}, 500

    @app.cli.command("init-db")
    def init_db():
        """Legt fehlende Tabellen und Spalten an und trägt fehlende Normausgaben ein. Kamen
        Normen dazu, rechnet es die Regeln für alle Dokumente neu."""
        from . import regeln
        from .modelle import Dokument
        neu = anlegen()
        if neu["spalten"]:
            click.echo(f"Spalten nachgetragen: {', '.join(neu['spalten'])}")
        if neu["quellen"]:
            click.echo(f"Quellennachweis aus vorhandenen Angaben nachgetragen: {neu['quellen']} "
                       "Dokumente")
        if neu["normen"]:
            click.echo(f"Normausgaben eingetragen (zu bestätigen): {', '.join(neu['normen'])}")
            if db.session.scalar(db.select(db.func.count(Dokument.id))):
                zahlen = regeln.pruefe_alle()
                db.session.commit()
                click.echo(f"Regeln neu geprüft: {zahlen}")

    @app.cli.command("import-altbestand")
    @click.option("--nur-wenn-leer", is_flag=True, help="Nur, wenn noch kein Dokument da ist.")
    def import_altbestand(nur_wenn_leer):
        """Liest den alten Ordner (ALTBESTAND) ein und gibt den Bericht aus."""
        from . import ablage, altbestand
        from .modelle import Dokument
        if nur_wenn_leer and db.session.scalar(db.select(db.func.count(Dokument.id))):
            click.echo("Altbestand schon eingelesen, Import übersprungen.")
            return
        try:
            lauf = altbestand.importiere(app.config["ALTBESTAND"])
        except ablage.AblageFehler as e:
            raise click.ClickException(str(e))
        db.session.commit()
        click.echo(altbestand.bericht(lauf))

    @app.cli.command("regeln-pruefen")
    def regeln_pruefen():
        """Rechnet die Regeln für alle Dokumente neu."""
        from . import regeln
        zahlen = regeln.pruefe_alle()
        db.session.commit()
        click.echo(zahlen)

    @app.cli.command("ocr")
    @click.option("--dauerhaft", is_flag=True,
                  help="Weiterlaufen und alle OCR_INTERVALL Sekunden nach neuen Scans sehen.")
    def ocr_befehl(dauerhaft):
        """Liest Seiten ohne Textebene per Texterkennung und wertet die Dokumente neu aus."""
        from . import ocr
        ocr.lauf(dauerhaft=dauerhaft)

    @app.cli.command("neu-auslesen")
    def neu_auslesen():
        """Liest alle Dokumente mit der aktuellen Auslesung neu (Angaben von Hand bleiben) und
        rechnet für geänderte die Regeln neu."""
        from . import ocr
        anzahl = ocr.neu_auslesen(click.echo)
        db.session.commit()
        click.echo(f"Neu ausgelesen, geändert: {anzahl} Dokumente.")

    return app


def anlegen():
    """Tabellen anlegen, fehlende Spalten nachtragen (schema.py), fehlende Normausgaben aus
    AKTUELLE_NORMEN und den Vorgaben eintragen; vorhandene Zeilen bleiben. Dokumente ohne
    Quellennachweis (aus der Zeit davor) bekommen ihn aus dem schon Gespeicherten. Gibt zurück,
    was neu ist: {"spalten": [...], "normen": [...], "quellen": Anzahl}."""
    from flask import current_app

    from . import normen, quellen, schema
    db.create_all()
    spalten = schema.spalten_nachziehen()
    normen.alte_zeilen_einordnen()
    neu = normen.vorgaben_eintragen(current_app.config["AKTUELLE_NORMEN"])
    db.session.commit()
    try:
        nachgetragen = quellen.nachtragen()
        db.session.commit()
    except SQLAlchemyError:  # z. B. eine Pflichtspalte fehlt (siehe Log von schema.py)
        db.session.rollback()
        current_app.logger.exception("Quellennachweis nicht nachgetragen")
        nachgetragen = 0
    return {"spalten": spalten, "normen": neu, "quellen": nachgetragen}
