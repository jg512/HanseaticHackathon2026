from flask import Flask
from werkzeug.exceptions import HTTPException

from .config import Config
from .db import db


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    from .services import versand  # falsche Einstellungen des Postausgangs: nicht starten
    versand.pruefe_konfiguration(app.config)

    db.init_app(app)
    from . import models  # noqa: F401  Tabellen registrieren
    from . import auth  # Anmeldung und Rechte (app/auth.py), vor allen anderen Routen
    auth.einrichten(app)

    from .routes import boms, emails, erledigt, export, findings, fragen, health, links, suche
    for modul in (health, boms, suche, findings, emails, export, links, erledigt, fragen):
        app.register_blueprint(modul.bp)
    from .routes import korrektur
    app.register_blueprint(korrektur.bp)
    from .routes import stammdaten
    app.register_blueprint(stammdaten.bp)
    # HTML-Oberfläche, Vertrag nach dem Entwurf aus dem Team (docs/api.md)
    from .routes import oberflaeche
    app.register_blueprint(oberflaeche.bp)
    from .routes import fristen  # GET /api/fristen, Befehl „angaben-nachlesen“
    app.register_blueprint(fristen.bp)
    from .routes import modelle  # Sprachmodelle aus config/modelle.yaml, Einstellungen je Vorgang
    app.register_blueprint(modelle.bp)
    _pruefe_modelle(app)
    from .routes import bearbeiten  # Bearbeiten in den Tabellen der Oberfläche
    app.register_blueprint(bearbeiten.bp)
    from .routes import kontakte  # Herstellerkontakte (E-Mail je Hersteller)
    app.register_blueprint(kontakte.bp)
    from .routes import globalsuche  # Suchfeld oben: GET /api/suche über die ganze Anwendung
    app.register_blueprint(globalsuche.bp)

    @app.errorhandler(HTTPException)
    def fehler_als_json(e):
        """Auch 404 und 405 als JSON, wie in docs/api.md versprochen."""
        texte = {404: "Nicht gefunden.", 405: "Methode nicht erlaubt."}
        return {"error": texte.get(e.code, e.name)}, e.code

    @app.cli.command("init-db")
    def init_db():
        """Legt alle Tabellen an, die noch fehlen, ergänzt fehlende Spalten (services/schema.py)
        und legt den Admin-Zugang an, wenn es ihn noch nicht gibt (app/auth.py)."""
        db.create_all()
        from .services import schema
        schema.nachziehen(db.engine, db.metadata)
        auth.erster_admin()

    @app.cli.command("ocr")
    def ocr():
        """Liest Scans in ATEX_DOCS per OCR in den Zwischenspeicher OCR_CACHE."""
        from pathlib import Path

        from .services import pdf_text
        raise SystemExit(pdf_text.lies_scans(Path(app.config["ATEX_DOCS"])))

    @app.cli.command("zert-abgleich")
    def zert_abgleich():
        """Freigegebene Dokumente, die der Zertifikatsserver noch nicht hat, dorthin übertragen."""
        from .services import zertifikatsserver
        if not app.config.get("ZERTIFIKATSSERVER_URL"):
            raise SystemExit("Kein Zertifikatsserver eingestellt (ZERTIFIKATSSERVER_URL).")
        ergebnis = zertifikatsserver.abgleich(ausgabe=print)
        db.session.commit()
        print(f"Zertifikatsserver: {ergebnis['uebertragen']} übertragen, {ergebnis['bekannt']} "
              f"schon vorhanden, {ergebnis['fehler']} nicht übertragen.")
        raise SystemExit(1 if ergebnis["fehler"] else 0)

    return app


def _pruefe_modelle(app):
    """Beim Start: eine fehlerhafte Modell-Datei sofort im Log melden (ohne Schlüssel)."""
    from .services import modelle
    with app.app_context():
        try:
            b = modelle.bestand()
        except modelle.KonfigFehler as e:
            app.logger.error("Sprachmodelle nicht nutzbar: %s", e)
            return
        if b.quelle == "umgebung":
            app.logger.info("Keine Modell-Datei (%s): Sprachmodell aus LLM_MODEL",
                            b.datei or "MODELLE_DATEI leer")
