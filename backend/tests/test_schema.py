"""Fehlende Spalten nachziehen (services/schema.py, flask init-db).

Läuft gegen SQLite in einer Datei; mit SCHEMA_TEST_DATABASE_URL (leere PostgreSQL-Datenbank,
z. B. postgresql+psycopg://…) zusätzlich gegen PostgreSQL.
"""

import os
from datetime import date, datetime

import pytest
from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, ForeignKey, Integer, MetaData,
                        String, Table, create_engine, inspect, select)

from app import create_app, models  # noqa: F401  Tabellen registrieren
from app.db import db
from app.services import schema

# Spalten, die zuletzt neu waren und von Hand per ALTER TABLE nachgezogen werden mussten
NEU = {("bom", "name"), ("bom", "search_started_at"), ("bom_item", "ex_confirmed_by"),
       ("bom_item", "ex_confirmed_at"), ("finding", "belege")}

URLS = ["sqlite"] + ([os.environ["SCHEMA_TEST_DATABASE_URL"]]
                     if os.environ.get("SCHEMA_TEST_DATABASE_URL") else [])


@pytest.fixture(params=URLS)
def engine(request, tmp_path):
    url = f"sqlite:///{tmp_path / 'alt.db'}" if request.param == "sqlite" else request.param
    e = create_engine(url)
    alles = MetaData()
    alles.reflect(e)
    alles.drop_all(e)  # PostgreSQL: von einem früheren Lauf
    yield e
    alles = MetaData()
    alles.reflect(e)
    alles.drop_all(e)
    e.dispose()


def alte_modelle(fehlen):
    """Kopie der Modelle ohne die Spalten in fehlen: so sah die Datenbank früher aus."""
    alt = MetaData()
    for t in db.metadata.sorted_tables:
        Table(t.name, alt, *[c._copy() for c in t.columns if (t.name, c.name) not in fehlen])
    return alt


def wert(spalte):
    typ = type(spalte.type)
    return {Integer: 1, Boolean: False, DateTime: datetime(2026, 1, 1), Date: date(2026, 1, 1),
            JSON: {}}.get(typ, "x")


def zeile(tabelle, **werte):
    """Pflichtfelder mit einem passenden Wert, dazu werte."""
    return {**{c.name: wert(c) for c in tabelle.columns
               if not c.nullable and not c.primary_key and c.default is None
               and c.server_default is None}, **werte}


def test_neue_spalten_kommen_dazu_zeilen_bleiben(engine):
    alt = alte_modelle(NEU)
    alt.create_all(engine)
    with engine.begin() as v:
        v.execute(alt.tables["bom"].insert(), [zeile(alt.tables["bom"], id=1, machine="TM-1"),
                                               zeile(alt.tables["bom"], id=2, machine="TM-2")])
        v.execute(alt.tables["bom_item"].insert(), zeile(alt.tables["bom_item"], id=1, bom_id=1))

    db.metadata.create_all(engine)  # wie init-db: legt nur fehlende Tabellen an
    zeilen = []
    ergaenzt, warnungen = schema.nachziehen(engine, db.metadata, zeilen.append)

    assert sorted(ergaenzt) == sorted(f"{t}.{s}" for t, s in NEU)
    assert warnungen == []
    assert any(z.startswith("Schema: Spalte finding.belege ergänzt: ALTER TABLE finding ADD "
                            "COLUMN belege") for z in zeilen)
    insp = inspect(engine)
    for t, s in NEU:
        assert s in {c["name"] for c in insp.get_columns(t)}
    bom = db.metadata.tables["bom"]
    with engine.connect() as v:
        assert v.execute(select(bom.c.id, bom.c.machine, bom.c.name).order_by(bom.c.id)).all() \
            == [(1, "TM-1", None), (2, "TM-2", None)]

    zeilen.clear()
    assert schema.nachziehen(engine, db.metadata, zeilen.append) == ([], [])
    assert zeilen == ["Schema: alle Spalten vorhanden, nichts ergänzt."]


def test_unsicheres_nur_als_warnung(engine):
    alt = MetaData()
    Table("teil", alt, Column("id", Integer, primary_key=True), Column("name", String))
    alt.create_all(engine)
    with engine.begin() as v:
        v.execute(alt.tables["teil"].insert(), [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])

    neu = MetaData()
    Table("hersteller", neu, Column("id", Integer, primary_key=True))
    Table("teil", neu, Column("id", Integer, primary_key=True), Column("name", String),
          Column("notiz", String),
          Column("anzahl", Integer, nullable=False, server_default="0"),
          Column("hersteller_id", ForeignKey("hersteller.id"), index=True),
          Column("pflicht", String, nullable=False, default="x"),  # Standard nur in Python
          Column("code", String, unique=True))
    neu.create_all(engine)
    zeilen = []
    ergaenzt, warnungen = schema.nachziehen(engine, neu, zeilen.append)

    assert ergaenzt == ["teil.notiz", "teil.anzahl", "teil.hersteller_id"]
    assert len(warnungen) == 2
    assert "teil.pflicht" in warnungen[0] and "NOT NULL ohne Standard" in warnungen[0]
    assert "Von Hand: ALTER TABLE teil ADD COLUMN pflicht VARCHAR NOT NULL;" in warnungen[0]
    assert "teil.code" in warnungen[1] and "UNIQUE" in warnungen[1]
    assert "ohne Fremdschlüssel-Bedingung" in next(z for z in zeilen if "hersteller_id" in z)
    insp = inspect(engine)
    spalten = {c["name"] for c in insp.get_columns("teil")}
    assert {"notiz", "anzahl", "hersteller_id"} <= spalten and not {"pflicht", "code"} & spalten
    assert "ix_teil_hersteller_id" in {i["name"] for i in insp.get_indexes("teil")}
    with engine.connect() as v:
        assert v.execute(select(neu.tables["teil"].c.name, neu.tables["teil"].c.anzahl)
                         .order_by(neu.tables["teil"].c.id)).all() == [("a", 0), ("b", 0)]

    # Zweiter Lauf: nichts mehr zu ergänzen, die Warnungen bleiben, bis ein Mensch handelt
    ergaenzt, warnungen = schema.nachziehen(engine, neu, zeilen.append)
    assert ergaenzt == [] and len(warnungen) == 2


def test_init_db_zieht_spalten_nach(tmp_path):
    url = f"sqlite:///{tmp_path / 'atex.db'}"
    e = create_engine(url)
    alt = alte_modelle(NEU)
    alt.create_all(e)
    with e.begin() as v:
        v.execute(alt.tables["bom"].insert(), zeile(alt.tables["bom"], id=1))
    e.dispose()

    app = create_app({"SQLALCHEMY_DATABASE_URI": url, "TESTING": True})
    cli = app.test_cli_runner()
    erster = cli.invoke(args=["init-db"])
    assert erster.exit_code == 0, erster.output
    assert "Schema: Spalte bom.name ergänzt" in erster.output
    assert "Schema: Spalte finding.belege ergänzt" in erster.output
    zweiter = cli.invoke(args=["init-db"])
    assert "nichts ergänzt" in zweiter.output
    with app.app_context():
        assert db.session.execute(db.text("SELECT id, name FROM bom")).all() == [(1, None)]
        db.engine.dispose()
