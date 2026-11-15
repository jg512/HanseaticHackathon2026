"""Fristen: Zertifikate, die bald ablaufen oder abgelaufen sind (Angaben aus services/angaben.py).

Dazu der Befehl `flask --app wsgi angaben-nachlesen`, der die Angaben für ältere Vorschläge
nachträgt. Er läuft nur, wenn ihn jemand aufruft.
"""

from datetime import date

import click
from flask import Blueprint, current_app, request

from ..db import db
from ..services import angaben

bp = Blueprint("fristen", __name__, url_prefix="/api", cli_group=None)
TAGE_MAX = 3650


@bp.get("/fristen")
def fristen():
    """?tage=180 (Standard ABLAUF_WARNUNG_TAGE): alles, was bis heute + tage abläuft, auch schon
    Abgelaufenes, aus Stammdaten und Vorschlägen."""
    tage = request.args.get("tage", current_app.config["ABLAUF_WARNUNG_TAGE"])
    try:
        tage = int(tage)
    except (TypeError, ValueError):
        return {"error": "Die Anzahl der Tage ist keine ganze Zahl. Bitte geben Sie zum "
                         "Beispiel 180 ein."}, 400
    if not 0 <= tage <= TAGE_MAX:
        return {"error": f"So viele Tage sind nicht möglich. Bitte geben Sie zwischen 0 und "
                         f"{TAGE_MAX} Tagen an."}, 400
    heute = date.today()
    eintraege = angaben.fristen(heute, tage)
    return {"stichtag": heute.isoformat(), "tage": tage, "anzahl": len(eintraege),
            "abgelaufen": sum(e["abgelaufen"] for e in eintraege), "eintraege": eintraege}


@bp.cli.command("angaben-nachlesen")
@click.option("--alle", is_flag=True, help="Auch Vorschläge mit älterem Regelstand neu lesen.")
def angaben_nachlesen(alle):
    """Liest die Angaben (Gültigkeit, Normen, …) für Vorschläge nach, deren Datei noch da ist."""
    zahl = angaben.nachlesen(current_app.config["ATEX_DOCS"], alle)
    db.session.commit()
    click.echo(f"Angaben gelesen: {zahl['gelesen']}, Datei fehlt: {zahl['ohne_datei']}, "
               f"schon vorhanden: {zahl['schon_da']}.")
