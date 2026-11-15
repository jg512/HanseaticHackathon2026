"""Neue Spalten in vorhandene Datenbanken nachtragen, ohne Migrationen und ohne SQL von Hand.

create_all legt nur fehlende Tabellen an, keine fehlenden Spalten. Beim Start vergleicht
spalten_nachziehen die Modelle mit der Datenbank und ergänzt fehlende Spalten, die leer sein
dürfen (ALTER TABLE … ADD COLUMN). Nie wird etwas gelöscht oder umgebaut; eine fehlende Spalte,
die nicht leer sein darf, meldet der Start nur, sie muss jemand von Hand anlegen.
"""

from flask import current_app
from sqlalchemy import inspect

from .db import db


def spalten_nachziehen():
    """Fehlende nullbare Spalten anlegen. Gibt [„tabelle.spalte“] der neuen Spalten zurück."""
    engine = db.engine
    pruefer = inspect(engine)
    vorbereiter = engine.dialect.identifier_preparer
    neu = []
    for tabelle in db.metadata.sorted_tables:
        if not pruefer.has_table(tabelle.name):
            continue  # legt create_all an
        vorhanden = {s["name"] for s in pruefer.get_columns(tabelle.name)}
        for spalte in tabelle.columns:
            if spalte.name in vorhanden:
                continue
            if not spalte.nullable or spalte.primary_key:
                current_app.logger.warning(
                    "Spalte %s.%s fehlt in der Datenbank und darf nicht leer sein: bitte von "
                    "Hand anlegen.", tabelle.name, spalte.name)
                continue
            typ = spalte.type.compile(dialect=engine.dialect)
            with engine.begin() as verbindung:
                verbindung.exec_driver_sql(
                    f"ALTER TABLE {vorbereiter.format_table(tabelle)} "
                    f"ADD COLUMN {vorbereiter.format_column(spalte)} {typ}")
            neu.append(f"{tabelle.name}.{spalte.name}")
    return neu
