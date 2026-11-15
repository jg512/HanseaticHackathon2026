"""Herstellerkontakte: E-Mail je Hersteller, Markierung ohne Adresse, Import und Export."""

import csv
import io
import smtplib
from datetime import datetime

import pytest
from conftest import kontakt
from openpyxl import Workbook, load_workbook

from app.db import db
from app.models import (Bom, BomItem, EmailDraft, HerstellerKontakt, Manufacturer,
                        StammdatenAenderung)
from app.services import email_entwurf, status


class FalscherSmtp:
    gesendet = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def send_message(self, nachricht):
        self.gesendet.append(nachricht)


@pytest.fixture
def smtp(monkeypatch):
    FalscherSmtp.gesendet = []
    monkeypatch.setattr(smtplib, "SMTP", FalscherSmtp)
    return FalscherSmtp


def vorgang():
    """Zwei Positionen „nicht gefunden“ (Festo, Muster Sensorik), dazu Festo in den Stammdaten."""
    gesucht = dict(searched_at=datetime(2026, 11, 13), searched_in="Datenbank, Ordner",
                   location="Zone 1")
    bom = Bom(machine="Abfüllanlage TM-1", source_file_name="x.xlsx", source_file_hash="h",
              items=[BomItem(row_number=1, pos="10", profile_no="99000010", manufacturer="Festo",
                             manufacturer_article_no="VACF-1", **gesucht),
                     BomItem(row_number=2, pos="250", profile_no="99000250",
                             manufacturer="Muster Sensorik GmbH",
                             manufacturer_article_no="MS-NAMUR-12", **gesucht)])
    db.session.add(bom)
    db.session.add(Manufacturer(name="Festo", normalized_name="festo"))
    db.session.flush()
    status.aktualisiere_alle(bom)
    db.session.commit()
    return bom


def festo():
    return db.session.scalar(db.select(Manufacturer).filter_by(normalized_name="festo"))


def zeilen(client, **filter_):
    return {z["hersteller"]: z for z in client.get("/api/kontakte", query_string=filter_)
            .get_json()}


def test_liste_mit_herstellern_aus_der_stueckliste(client):
    vorgang()
    z = zeilen(client)
    assert set(z) == {"Festo", "Muster Sensorik GmbH"}
    assert z["Festo"]["hersteller_id"] == festo().id and z["Festo"]["in_stammdaten"]
    assert z["Muster Sensorik GmbH"]["hersteller_id"] is None
    assert not z["Muster Sensorik GmbH"]["in_stammdaten"]
    assert {k for k in z["Festo"]} >= {"hersteller_id", "hersteller", "email", "ansprechpartner",
                                       "sprache", "notiz", "geaendert_von", "geaendert_am",
                                       "fehlt", "offene_entwuerfe"}
    assert all(r["fehlt"] and r["email"] is None for r in z.values())

    kontakt("Festo", "anfragen@festo.example")
    assert set(zeilen(client, fehlt=1)) == {"Muster Sensorik GmbH"}
    assert set(zeilen(client, fehlt=0)) == {"Festo"}
    assert set(zeilen(client, q="FESTO.ex")) == {"Festo"}
    assert set(zeilen(client, q="muster")) == {"Muster Sensorik GmbH"}


def test_aendern_mit_protokoll_und_pruefung(client):
    vorgang()
    m = festo()
    url = f"/api/kontakte/{m.id}"
    assert client.put(url, json={"email": "a@festo.example"}).status_code == 400  # ohne Namen
    for falsch in ("keine-adresse", "a@b", "a b@festo.example", "a@festo.example, b@festo.example"):
        r = client.put(url, json={"email": falsch, "by": "Eva"})
        assert r.status_code == 400 and "keine gültige E-Mail-Adresse" in r.get_json()["error"]
    r = client.put(url, json={"email": "a@festo.example", "sprache": "fr", "by": "Eva"})
    assert r.status_code == 400 and "Deutsch (de) oder Englisch (en)" in r.get_json()["error"]
    assert client.put(url, json={"email": "a@festo.example", "x": 1, "by": "Eva"}).status_code == 400
    assert client.put("/api/kontakte/999", json={"email": "", "by": "Eva"}).status_code == 404

    r = client.put(url, json={"email": " ex@festo.example ", "ansprechpartner": "Frau Muster",
                              "sprache": "DE", "notiz": "Vertrieb Ex", "by": "Eva"})
    assert r.status_code == 200
    k = r.get_json()
    assert (k["email"], k["ansprechpartner"], k["sprache"], k["notiz"], k["geaendert_von"],
            k["fehlt"], k["entwuerfe_aktualisiert"]) == (
        "ex@festo.example", "Frau Muster", "de", "Vertrieb Ex", "Eva", False, 0)
    r = client.put(url, json={"email": "ex@festo.example", "by": "Eva"})
    assert r.status_code == 400 and "nichts geändert" in r.get_json()["error"]

    # leere E-Mail entfernt die Adresse, der Rest bleibt
    k = client.put(url, json={"email": "", "by": "Jan"}).get_json()
    assert (k["email"], k["fehlt"], k["ansprechpartner"]) == (None, True, "Frau Muster")
    spur = db.session.scalars(db.select(StammdatenAenderung).filter_by(
        table_name="hersteller_kontakt").order_by(StammdatenAenderung.id)).all()
    assert [(a.row_key, a.field, a.old_value, a.new_value, a.changed_by) for a in spur] == [
        (str(m.id), "email", None, "ex@festo.example", "Eva"),
        (str(m.id), "ansprechpartner", None, "Frau Muster", "Eva"),
        (str(m.id), "sprache", None, "de", "Eva"),
        (str(m.id), "notiz", None, "Vertrieb Ex", "Eva"),
        (str(m.id), "email", "ex@festo.example", None, "Jan")]
    # im allgemeinen Änderungsprotokoll der Stammdaten sichtbar
    tabellen = {a["table_name"] for a in client.get("/api/stammdaten/changes").get_json()}
    assert "hersteller_kontakt" in tabellen


def test_anlegen_fuer_hersteller_nur_aus_der_stueckliste(client):
    vorgang()
    r = client.post("/api/kontakte", json={"hersteller": "Muster Sensorik GmbH",
                                           "email": "ex@muster.example", "by": "Eva"})
    assert r.status_code == 201
    k = r.get_json()
    assert k["hersteller_id"] and k["in_stammdaten"] and k["entwuerfe_aktualisiert"] == 0
    angelegt = db.session.scalar(db.select(StammdatenAenderung).filter_by(
        table_name="manufacturer", field="angelegt"))
    assert (angelegt.new_value, angelegt.changed_by) == ("Muster Sensorik GmbH", "Eva")
    # zweites Mal: der Hersteller ist bekannt, 200
    r = client.post("/api/kontakte", json={"hersteller": "muster sensorik gmbh",
                                           "email": "neu@muster.example", "by": "Eva"})
    assert r.status_code == 200 and r.get_json()["hersteller"] == "Muster Sensorik GmbH"
    assert client.post("/api/kontakte", json={"hersteller": "--", "email": "a@b.example",
                                              "by": "Eva"}).status_code == 400


def test_stammdaten_hersteller_tragen_die_adresse(client):
    vorgang()
    [festo_] = client.get("/api/stammdaten/manufacturers").get_json()
    assert (festo_["name"], festo_["email"], festo_["kontakt_fehlt"]) == ("Festo", None, True)
    kontakt("Festo", "anfragen@festo.example")
    [festo_] = client.get("/api/stammdaten/manufacturers").get_json()
    assert (festo_["name"], festo_["email"], festo_["kontakt_fehlt"]) == (
        "Festo", "anfragen@festo.example", False)


def test_entwurf_ohne_adresse_markiert_und_nicht_gesendet(client, smtp):
    bom = vorgang()
    entwuerfe = client.post(f"/api/boms/{bom.id}/emails").get_json()
    e = entwuerfe[0]
    assert (e["empfaenger_hersteller"], e["kontakt_fehlt"], e["versand_an"]) == (None, True, None)
    assert e["to_address"] == "test@example.com"  # Pflichtfeld, vorerst die Test-Adresse
    item = db.session.get(BomItem, e["bom_item_id"])
    assert "Für den Hersteller Festo ist noch keine E-Mail-Adresse hinterlegt." in item.status_note

    client.post(f"/api/emails/{e['id']}/approve", json={"by": "Jan"})
    r = client.post(f"/api/emails/{e['id']}/send")
    assert r.status_code == 409 and "Herstellerkontakten" in r.get_json()["error"]
    assert smtp.gesendet == []

    # Adresse nachgetragen: der offene Entwurf zieht nach, der Hinweis verschwindet
    r = client.put(f"/api/kontakte/{festo().id}", json={"email": "ex@festo.example", "by": "Eva"})
    assert r.get_json()["entwuerfe_aktualisiert"] == 1
    assert zeilen(client)["Festo"]["offene_entwuerfe"] == 1
    item = db.session.get(BomItem, e["bom_item_id"])
    assert item.status_note is None
    e = client.get(f"/api/boms/{bom.id}/emails").get_json()[0]
    assert (e["empfaenger_hersteller"], e["kontakt_fehlt"], e["versand_an"]) == (
        "ex@festo.example", False, "test@example.com")
    r = client.post(f"/api/emails/{e['id']}/send")
    assert r.status_code == 200
    assert [n["To"] for n in smtp.gesendet] == ["test@example.com"]  # nie an den Hersteller
    assert r.get_json()["versand_an"] == "test@example.com"


def test_ohne_nur_test_geht_die_mail_an_den_hersteller(client, smtp):
    client.application.config["EMAIL_NUR_TEST"] = False
    bom = vorgang()
    kontakt("Festo", "ex@festo.example")
    e = client.post(f"/api/boms/{bom.id}/emails").get_json()[0]
    assert (e["to_address"], e["versand_an"]) == ("ex@festo.example", "ex@festo.example")
    client.post(f"/api/emails/{e['id']}/approve", json={"by": "Jan"})
    assert client.post(f"/api/emails/{e['id']}/send").status_code == 200
    assert [n["To"] for n in smtp.gesendet] == ["ex@festo.example"]


def test_kontakt_spaeter_gesetzt_ohne_nur_test_aendert_den_empfaenger(client):
    client.application.config["EMAIL_NUR_TEST"] = False
    bom = vorgang()
    client.post(f"/api/boms/{bom.id}/emails")
    entwurf = db.session.scalar(db.select(EmailDraft).join(BomItem).filter(BomItem.pos == "10"))
    assert entwurf.to_address == "test@example.com"
    client.put(f"/api/kontakte/{festo().id}", json={"email": "ex@festo.example", "by": "Eva"})
    assert db.session.get(EmailDraft, entwurf.id).to_address == "ex@festo.example"


def test_sende_ohne_adresse_ist_kontaktfehlt(client):
    bom = vorgang()
    client.post(f"/api/boms/{bom.id}/emails")
    entwurf = db.session.scalar(db.select(EmailDraft))
    with pytest.raises(email_entwurf.KontaktFehlt) as fehler:
        email_entwurf.sende(entwurf)
    assert fehler.value.code == 409


# Import und Export


def xlsx_datei(zeilen_):
    wb = Workbook()
    for z in zeilen_:
        wb.active.append(z)
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


def importiere(client, inhalt, name="kontakte.xlsx", by="Eva"):
    return client.post("/api/kontakte/import", data={"datei": (io.BytesIO(inhalt), name),
                                                     "by": by})


def test_import_mit_bericht(client):
    vorgang()
    kontakt("Festo", "alt@festo.example")
    inhalt = xlsx_datei([
        ["Herstellerkontakte (Beispiel)"],
        [],
        ["Hersteller", "E-Mail", "Ansprechpartner", "Sprache"],
        ["FESTO", "neu@festo.example", "Herr Beispiel", "de"],     # geändert
        ["Muster Sensorik GmbH", "ex@muster.example", None, "en"],  # nur Stückliste: angelegt
        ["Unbekannt AG", "a@unbekannt.example", None, None],         # nicht geraten
        ["Festo", "doppelt@festo.example", None, None],              # doppelt
        ["Rittal", "kaputt", None, None],                            # ungültig (vor dem Abgleich)
        ["Breco", None, "Frau X", None],                             # ohne E-Mail: nichts
        [None, None, None, None],                                    # leer: übergangen
        [None, "ohne@name.example", None, None],                     # ohne Hersteller
    ])
    r = importiere(client, inhalt)
    assert r.status_code == 200, r.get_json()
    b = r.get_json()
    assert [(e["zeile"], e["hersteller"]) for e in b["uebernommen"]] == [
        (4, "FESTO"), (5, "Muster Sensorik GmbH")]
    assert [(e["zeile"], e["hersteller"]) for e in b["unbekannt"]] == [(6, "Unbekannt AG")]
    assert [(e["zeile"], e["hersteller"]) for e in b["ungueltig"]] == [
        (7, "Festo"), (8, "Rittal"), (11, None)]
    assert "schon in Zeile 4" in b["ungueltig"][0]["grund"]
    assert [e["zeile"] for e in b["ohne_email"]] == [9]
    assert b["angelegt"] == ["Muster Sensorik GmbH"]
    z = zeilen(client)
    assert (z["Festo"]["email"], z["Festo"]["ansprechpartner"], z["Festo"]["geaendert_von"]) == (
        "neu@festo.example", "Herr Beispiel", "Eva")
    assert (z["Muster Sensorik GmbH"]["email"], z["Muster Sensorik GmbH"]["sprache"]) == (
        "ex@muster.example", "en")
    assert "Unbekannt AG" not in z
    notiz = db.session.scalar(db.select(StammdatenAenderung).filter_by(
        table_name="hersteller_kontakt", new_value="neu@festo.example")).note
    assert notiz == "Import kontakte.xlsx, Zeile 4"

    # derselbe Import noch einmal: nichts mehr zu ändern
    b = importiere(client, inhalt).get_json()
    assert b["uebernommen"] == [] and len(b["unveraendert"]) == 2


def test_import_csv_und_fehler(client):
    vorgang()
    puffer = io.StringIO()
    csv.writer(puffer, delimiter=";").writerows([["Hersteller", "E-Mail", "Sprache"],
                                                 ["Festo", "csv@festo.example", "de"]])
    r = importiere(client, puffer.getvalue().encode("cp1252"), name="k.csv")
    assert r.status_code == 200 and len(r.get_json()["uebernommen"]) == 1
    assert zeilen(client)["Festo"]["email"] == "csv@festo.example"

    assert importiere(client, b"x", by="").status_code == 400  # ohne Namen
    r = importiere(client, b"x", name="k.txt")
    assert r.status_code == 400 and "Excel-Datei (.xlsx) oder eine CSV-Datei" in r.get_json()["error"]
    r = importiere(client, b"kein excel", name="k.xlsx")
    assert r.status_code == 400 and "ließ sich nicht öffnen" in r.get_json()["error"]
    r = importiere(client, xlsx_datei([["Name", "Adresse"]]))
    assert r.status_code == 400 and "keine Kopfzeile" in r.get_json()["error"]
    r = importiere(client, xlsx_datei([["Hersteller", "Telefon"]]))
    assert r.status_code == 400 and "fehlt die Spalte „E-Mail“" in r.get_json()["error"]
    assert client.post("/api/kontakte/import", data={"by": "Eva"}).status_code == 400


def test_export_ist_wieder_einlesbar(client):
    vorgang()
    kontakt("Festo", "anfragen@festo.example", sprache="de")
    r = client.get("/api/kontakte/export.xlsx")
    assert r.status_code == 200 and "Herstellerkontakte.xlsx" in r.headers["Content-Disposition"]
    zeilen_ = [list(z) for z in load_workbook(io.BytesIO(r.data)).active.iter_rows(values_only=True)]
    assert zeilen_[0][:4] == ["Hersteller", "E-Mail", "Ansprechpartner", "Sprache"]
    assert [(z[0], z[1], z[3], z[7]) for z in zeilen_[1:]] == [
        ("Festo", "anfragen@festo.example", "de", "nein"),
        ("Muster Sensorik GmbH", None, None, "ja")]
    b = importiere(client, r.data, name="Herstellerkontakte.xlsx").get_json()
    assert len(b["unveraendert"]) == 1 and len(b["ohne_email"]) == 1 and not b["ungueltig"]


def test_beispieldatei_passt_zur_test_stueckliste(client):
    """data/herstellerkontakte_beispiel.xlsx: nur erfundene Adressen unter .example."""
    from pathlib import Path
    pfad = Path(__file__).resolve().parents[2] / "data" / "herstellerkontakte_beispiel.xlsx"
    if not pfad.is_file():
        pytest.skip("Beispieldatei liegt nicht im Container")
    adressen = [z[1] for z in load_workbook(pfad).active.iter_rows(min_row=2, values_only=True)
                if z[1]]
    assert adressen and all(a.endswith(".example") for a in adressen)
    db.session.add(Bom(source_file_name="t.xlsx", source_file_hash="h", items=[
        BomItem(row_number=i, manufacturer=n) for i, n in enumerate(
            ["Festo", "Pepperl+Fuchs", "Siemens", "Rittal", "R. STAHL", "Breco"])]))
    db.session.commit()
    b = importiere(client, pfad.read_bytes(), name=pfad.name).get_json()
    assert not b["unbekannt"] and not b["ungueltig"]
    assert db.session.scalar(db.select(db.func.count()).select_from(HerstellerKontakt)) == len(
        b["uebernommen"])
