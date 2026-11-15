"""Bearbeiten in den Tabellen der Oberfläche (routes/bearbeiten.py): Vorgang umbenennen,
Position korrigieren, gelesene Werte eines Funds korrigieren."""

import pytest
from test_oberflaeche import art, artikel, gestartet, hochladen, leer, ordner, vorgang  # noqa: F401

from app.db import db
from app.models import Finding
from app.services.bearbeiten import FindingCorrection


def ereignisse(client, vid):
    return [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]


# Vorgang


def test_vorgang_umbenennen(client, ordner):
    vid = hochladen(client)
    r = client.patch(f"/api/vorgaenge/{vid}", json={"von": "Jan", "name": "  Linie 4  "})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["name"] == "Linie 4" and "artikel" not in r.get_json()
    assert vorgang(client, vid)["name"] == "Linie 4"
    assert "Jan hat den Vorgang von „Abfüllanlage Test“ in „Linie 4“ umbenannt." in ereignisse(client, vid)
    [kurz] = client.get("/api/vorgaenge").get_json()
    assert kurz["maschine"] == "Abfüllanlage TM-1" and kurz["datei"] == "liste.xlsx"


def test_vorgang_umbenennen_eindeutig(client, ordner):
    erster, zweiter = hochladen(client, name="Linie 4"), hochladen(client, name="Linie 5")
    r = client.patch(f"/api/vorgaenge/{zweiter}", json={"von": "Jan", "name": "linie 4"})
    assert r.status_code == 200
    assert (r.get_json()["name"], r.get_json()["gewuenscht"]) == ("linie 4 (2)", "linie 4")
    assert vorgang(client, erster)["name"] == "Linie 4"


@pytest.mark.parametrize("daten, code, text", [
    ({"name": "Neu"}, 400, "Ohne Ihren Namen"),
    ({"von": "Jan", "name": "  "}, 400, "braucht einen Namen"),
    ({"von": "Jan", "name": "Abfüllanlage Test"}, 400, "heißt schon so"),
    ({"von": "Jan", "name": "x" * 201}, 400, "zu lang"),
])
def test_vorgang_umbenennen_fehler(client, ordner, daten, code, text):
    vid = hochladen(client)
    r = client.patch(f"/api/vorgaenge/{vid}", json=daten)
    assert r.status_code == code and text in r.get_json()["fehler"]
    assert vorgang(client, vid)["name"] == "Abfüllanlage Test"
    assert client.patch("/api/vorgaenge/999", json={"von": "Jan", "name": "x"}).status_code == 404


def test_name_aus_dem_kopf(client, ordner):
    vid = hochladen(client)
    r = client.patch(f"/api/vorgaenge/{vid}", json={"name": "Kopf"}, headers={"X-Anwender": "Eva"})
    assert r.status_code == 200
    assert "Eva hat den Vorgang von „Abfüllanlage Test“ in „Kopf“ umbenannt." in ereignisse(client, vid)


# Position


def test_position_korrigieren_mit_menge_und_notiz(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    assert a["menge"] == "4" and a["status"] == "zu_pruefen"
    r = client.patch(f"/api/artikel/{a['id']}",
                     json={"von": "Jan", "note": "laut Einkauf", "quantity": "6",
                           "description": "Magnetspule groß"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert (d["menge"], d["bezeichnung"]) == ("6", "Magnetspule groß")
    assert {(k["field"], k["old_value"], k["new_value"], k["changed_by"], k["note"])
            for k in d["korrekturen"]} == {("quantity", "4", "6", "Jan", "laut Einkauf"),
                                           ("description", "Magnetspule", "Magnetspule groß",
                                            "Jan", "laut Einkauf")}
    # Menge und Bezeichnung stoßen keine neue Suche an: der Vorschlag bleibt
    assert artikel(client, vid, 10)["status"] == "zu_pruefen"
    assert any(t.startswith("Position 10: Jan hat ") and "Menge von „4“ auf „6“" in t
               for t in ereignisse(client, vid))


def test_position_hersteller_verwirft_vorschlaege(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    assert a["funde"]
    r = client.patch(f"/api/artikel/{a['id']}", json={"von": "Jan", "manufacturer": "Festo AG"})
    assert r.status_code == 200
    a = artikel(client, vid, 10)
    assert a["hersteller"] == "Festo AG" and not a["funde"] and a["status"] == "wartet"


@pytest.mark.parametrize("daten, text", [
    ({"quantity": "6"}, "Ohne Ihren Namen"),
    ({"von": "Jan", "location": " "}, "Einbauort darf nicht leer sein"),
    ({"von": "Jan", "quantity": "4"}, "stehen schon so in der Stückliste"),
    ({"von": "Jan", "status": "geprueft"}, "lässt sich hier nicht ändern"),
])
def test_position_fehler(client, ordner, daten, text):
    vid = hochladen(client)
    a = art(client, vid, 10)
    r = client.patch(f"/api/artikel/{a['id']}", json=daten)
    assert r.status_code == 400 and text in r.get_json()["fehler"], r.get_json()
    assert art(client, vid, 10)["menge"] == "4"


# Fund


def test_fund_werte_korrigieren(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    fid = a["funde"][0]["id"]
    angaben = client.get(f"/api/artikel/{a['id']}/fundangaben").get_json()[str(fid)]
    assert angaben["offen"] and [f["schluessel"] for f in angaben["felder"]] == [
        "doc_type", "kennzeichnung", "zertifikat", "sprachen", "gueltig_bis", "ausstellungsdatum",
        "ausgabe", "richtlinien", "normen", "stellen", "umgebungstemperatur", "iecex"]
    felder = {f["schluessel"]: f for f in angaben["felder"]}
    assert felder["doc_type"]["art"] == "auswahl" and "datenblatt" in felder["doc_type"]["optionen"]
    alt = felder["kennzeichnung"]["wert"]

    r = client.patch(f"/api/funde/{fid}", json={"von": "Jan", "note": "Seite 2 falsch gelesen",
                                                "kennzeichnung": "II 2G Ex mb IIC T4 Gb",
                                                "sprachen": "DE; en", "doc_type": "datenblatt"})
    assert r.status_code == 200, r.get_json()
    felder = {f["schluessel"]: f for f in r.get_json()["felder"]}
    assert felder["kennzeichnung"]["wert"] == "II 2G Ex mb IIC T4 Gb"
    assert felder["kennzeichnung"]["gelesen"] == alt
    assert felder["kennzeichnung"]["korrektur"]["von"] == "Jan"
    assert felder["kennzeichnung"]["korrektur"]["notiz"] == "Seite 2 falsch gelesen"
    assert felder["sprachen"]["wert"] == "de,en" and felder["zertifikat"]["korrektur"] is None
    f = db.session.get(Finding, fid)
    assert (f.ex_marking_raw, f.doc_type) == ("II 2G Ex mb IIC T4 Gb", "datenblatt")
    assert db.session.query(FindingCorrection).filter_by(finding_id=fid).count() == 3
    assert any(t.startswith("Position 10: Jan hat im Fund „") and "Dokumentart von „" in t
               and "auf „Datenblatt“" in t for t in ereignisse(client, vid))

    # die Freigabe übernimmt die korrigierten Werte
    r = client.post(f"/api/artikel/{a['id']}/freigeben", json={"fund_id": fid, "von": "Jan"})
    assert r.status_code == 200
    assert db.session.get(Finding, fid).doc_type == "datenblatt"
    # danach ist nichts mehr korrigierbar
    r = client.patch(f"/api/funde/{fid}", json={"von": "Jan", "zertifikat": "X"})
    assert r.status_code == 409 and "schon freigegeben" in r.get_json()["fehler"]
    assert not client.get(f"/api/artikel/{a['id']}/fundangaben").get_json()[str(fid)]["offen"]


def test_gueltig_bis_korrigiert_warnt_und_kommt_in_die_stammdaten(client, ordner):
    from app.models import Document
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    fid = a["funde"][0]["id"]
    vorher = db.session.get(Finding, fid).check_result
    # abgelaufen, als 31.12.2020 eingetippt: Warnung in der Vorprüfung
    r = client.patch(f"/api/funde/{fid}", json={"von": "Jan", "gueltig_bis": "31.12.2020"})
    assert r.status_code == 200, r.get_json()
    felder = {f["schluessel"]: f for f in r.get_json()["felder"]}
    assert felder["gueltig_bis"]["wert"] == "2020-12-31" and felder["gueltig_bis"]["art"] == "datum"
    f = db.session.get(Finding, fid)
    assert f.check_result == "warnung"
    assert "Das Zertifikat ist seit dem 31.12.2020 abgelaufen, so ist es von Hand eingetragen." \
        in f.check_message
    assert f.angaben["gueltig_bis"]["von_hand"] is True
    # wieder in die Zukunft: die Warnung verschwindet, das alte Ergebnis gilt wieder
    client.patch(f"/api/funde/{fid}", json={"von": "Jan", "gueltig_bis": "2099-01-31"})
    f = db.session.get(Finding, fid)
    assert f.check_result == vorher and "abgelaufen" not in f.check_message
    # Normen und Benannte Stelle als Liste
    r = client.patch(f"/api/funde/{fid}", json={"von": "Jan", "normen": "EN IEC 60079-0; EN 60079-7",
                                                "stellen": "IBExU (0637)"})
    felder = {f["schluessel"]: f for f in r.get_json()["felder"]}
    assert felder["normen"]["wert"] == "EN IEC 60079-0; EN 60079-7"
    assert db.session.get(Finding, fid).angaben["stellen"][0]["nummer"] == "0637"
    # die Freigabe trägt das korrigierte Datum in die Stammdaten ein
    assert client.post(f"/api/artikel/{a['id']}/freigeben",
                       json={"fund_id": fid, "von": "Jan"}).status_code == 200
    doc = db.session.scalar(db.select(Document).filter_by(file_hash=f.file_hash))
    assert doc.valid_until.isoformat() == "2099-01-31"


@pytest.mark.parametrize("daten, text", [
    ({"kennzeichnung": "x"}, "Ohne Ihren Namen"),
    ({"von": "Jan", "doc_type": "quatsch"}, "passt nicht zu Dokumentart"),
    ({"von": "Jan", "doc_type": ""}, "darf nicht leer"),
    ({"von": "Jan", "sprachen": "deutsch"}, "kein Sprachkürzel"),
    ({"von": "Jan", "seite": "3"}, "lässt sich hier nicht ändern"),
    ({"von": "Jan"}, "stehen schon so im Fund"),
])
def test_fund_fehler(client, ordner, daten, text):
    vid = hochladen(client)
    gestartet(client, vid)
    fid = artikel(client, vid, 10)["funde"][0]["id"]
    vorher = db.session.get(Finding, fid).doc_type
    r = client.patch(f"/api/funde/{fid}", json=daten)
    assert r.status_code == 400 and text in r.get_json()["fehler"], r.get_json()
    db.session.expire_all()
    assert db.session.get(Finding, fid).doc_type == vorher
    assert client.patch("/api/funde/9999", json={"von": "Jan"}).status_code == 404


def test_fund_leeren_erlaubt(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    fid = artikel(client, vid, 10)["funde"][0]["id"]
    r = client.patch(f"/api/funde/{fid}", json={"von": "Jan", "zertifikat": ""})
    assert r.status_code == 200
    assert db.session.get(Finding, fid).certificate_no is None
