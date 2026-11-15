"""Fehlende Spalten nachziehen: kleiner Ersatz für Migrationen (läuft in `flask init-db`).

create_all legt nur fehlende Tabellen an. Bekommt ein Modell eine neue Spalte, fehlt sie in jeder
bestehenden Datenbank. nachziehen vergleicht die Modelle mit den echten Tabellen (Inspector) und
ergänzt jede fehlende Spalte, die das gefahrlos erlaubt: leer erlaubt (nullable) oder mit Standard
in der Datenbank (server_default). Bestehende Zeilen bleiben, sie bekommen NULL bzw. den Standard.

Nie gelöscht, umbenannt oder im Typ geändert. Was sich nicht sicher ergänzen lässt (NOT NULL ohne
Standard in der Datenbank, eindeutige Spalten, Primärschlüssel), steht als Warnung mit Tabelle,
Spalte und Vorschlag da; das macht ein Mensch von Hand. PostgreSQL und SQLite.
"""

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn


def _ddl(spalte, dialekt):
    """„name TYP [NOT NULL] [DEFAULT …]“ wie in CREATE TABLE, ohne Fremdschlüssel."""
    return str(CreateColumn(spalte).compile(dialect=dialekt)).strip()


def _warum_nicht(spalte):
    """Grund, warum die Spalte nicht automatisch dazukommt, sonst None."""
    if spalte.primary_key:
        return "Primärschlüssel"
    if spalte.unique:
        return "eindeutig (UNIQUE), SQLite kann das nicht nachträglich"
    if not spalte.nullable and spalte.server_default is None:
        return "NOT NULL ohne Standard in der Datenbank (server_default), bestehende Zeilen hätten keinen Wert"
    return None


def nachziehen(engine, metadata, ausgabe=print):
    """Ergänzt fehlende Spalten bestehender Tabellen. Gibt (ergänzt, Warnungen) zurück, je als
    Liste von „tabelle.spalte“ bzw. Text; jede Zeile geht auch an ausgabe."""
    insp = inspect(engine)
    tabellen = set(insp.get_table_names())
    dialekt = engine.dialect
    ergaenzt, warnungen = [], []

    def warne(tabelle, spalte, grund, vorschlag=None):
        zeile = (f"WARNUNG Schema: Spalte {tabelle}.{spalte} fehlt in der Datenbank und wird "
                 f"nicht automatisch angelegt: {grund}."
                 + (f" Von Hand: {vorschlag}" if vorschlag else ""))
        warnungen.append(zeile)
        ausgabe(zeile)

    for tabelle in metadata.sorted_tables:
        if tabelle.name not in tabellen:
            continue  # neue Tabelle: legt create_all an
        vorhanden = {s["name"] for s in insp.get_columns(tabelle.name)}
        neu = []
        name = dialekt.identifier_preparer.format_table(tabelle)
        for spalte in tabelle.columns:
            if spalte.name in vorhanden:
                continue
            befehl = f"ALTER TABLE {name} ADD COLUMN {_ddl(spalte, dialekt)}"
            grund = _warum_nicht(spalte)
            if grund:
                warne(tabelle.name, spalte.name, grund, befehl + ";")
                continue
            try:
                with engine.begin() as verbindung:
                    verbindung.execute(text(befehl))
            except Exception as e:  # z. B. SQLite: Standard ist kein fester Wert
                warne(tabelle.name, spalte.name, f"die Datenbank lehnt ab ({e.__class__.__name__}: "
                      f"{str(e).splitlines()[0]})", befehl + ";")
                continue
            neu.append(spalte.name)
            ergaenzt.append(f"{tabelle.name}.{spalte.name}")
            hinweis = " (ohne Fremdschlüssel-Bedingung)" if spalte.foreign_keys else ""
            ausgabe(f"Schema: Spalte {tabelle.name}.{spalte.name} ergänzt{hinweis}: {befehl}")
        if not neu:
            continue
        # Indizes über die neuen Spalten (index=True im Modell) gleich mit anlegen
        indizes = {i["name"] for i in inspect(engine).get_indexes(tabelle.name)}
        for index in tabelle.indexes:
            if index.name not in indizes and any(s.name in neu for s in index.columns):
                index.create(engine)
                ausgabe(f"Schema: Index {index.name} auf {tabelle.name} angelegt")
    if not ergaenzt and not warnungen:
        ausgabe("Schema: alle Spalten vorhanden, nichts ergänzt.")
    return ergaenzt, warnungen
