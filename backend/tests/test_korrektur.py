import io
from datetime import datetime

import pytest
from conftest import xlsx
from test_emails import smtp  # noqa: F401  Fixture: SMTP ersetzt
from test_suche import VACF, pdf

from app.db import db
from app.models import BomIssue, BomItem, EmailDraft, Finding
from app.models.bom import ItemCorrection
from app.services import korrektur

ZEILEN = [
    [10, 99000010, "Magnetspule", "Festo", "QZ-4711", 4, "Zone 1"],
    [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
    [280, 99000280, "Linearführung", None, None, 2, None],
    [290, None, "Klemmenkasten", "R. STAHL", "8146", 1, "Zone 1"],
    [300, 99000300, "Drucksensor", "Festo", "SPAN-P10R", 1, None],
]


@pytest.fixture
def ordner(client, tmp_path):
    pdf(tmp_path / "Festo" / "Festo_Konformitaetserklaerung_VACF-B-K1-1-5.pdf", VACF)
    client.application.config["ATEX_DOCS"] = str(tmp_path)
    return tmp_path


def lade_hoch(client, zeilen=ZEILEN):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "liste.xlsx")})
    assert r.status_code == 201
    return r.get_json()


def position(bom, pos):
    return next(i for i in bom["items"] if i["pos"] == pos)


def korrigiere(client, bom, pos, **daten):
    return client.post(f"/api/boms/{bom['id']}/items/{position(bom, pos)['id']}/korrektur",
                       json=daten)


def test_maengel_aus_klaerung_und_meldungen(client):
    bom = lade_hoch(client)
    maengel = client.get(f"/api/boms/{bom['id']}/korrektur").get_json()["defects"]
    assert [(m["pos"], m["status"], m["missing"]) for m in maengel] == [
        ("280", "klaerung", ["Der Hersteller fehlt.", "Die Hersteller-Artikelnummer fehlt.",
                             "Der Einbauort fehlt. Deshalb ist die Zone unbekannt."]),
        ("290", "klaerung", ["Die PRO.FILE-Nr. fehlt."]),
        ("300", "klaerung", ["Der Einbauort fehlt. Deshalb ist die Zone unbekannt."])]
    assert maengel[1]["profile_no"] is None and maengel[1]["manufacturer"] == "R. STAHL"

    # kein Ex-Teil: entschieden, der Einkauf muss nichts tun
    client.post(f"/api/boms/{bom['id']}/items/{position(bom, '300')['id']}/ex-teil",
                json={"ex_teil": False, "by": "Eva"})
    maengel = client.get(f"/api/boms/{bom['id']}/korrektur").get_json()["defects"]
    assert [m["pos"] for m in maengel] == ["280", "290"]
    assert client.get("/api/boms/99/korrektur").status_code == 404


def test_maengelliste_entwerfen_freigeben_senden(client, smtp):  # noqa: F811
    client.application.config.update(EINKAUF_ADRESSE="einkauf@beispiel-maschinenbau.test",
                                     SMTP_HOST="mail", SMTP_PORT=1025, EMAIL_NUR_TEST=False)
    bom = lade_hoch(client)
    r = client.post(f"/api/boms/{bom['id']}/maengelliste")
    assert r.status_code == 201
    liste = r.get_json()
    assert (liste["status"], liste["to_address"]) == ("entwurf", "einkauf@beispiel-maschinenbau.test")
    assert liste["subject"] == "Mängelliste Stückliste Abfüllanlage TM-1: 3 Positionen"
    assert ("- Position 280, PRO.FILE-Nr. 99000280, Linearführung, Hersteller –\n"
            "  Der Hersteller fehlt. Die Hersteller-Artikelnummer fehlt. Der Einbauort fehlt. "
            "Deshalb ist die Zone unbekannt.") in liste["body"]
    assert ("- Position 290, PRO.FILE-Nr. –, Klemmenkasten, R. STAHL 8146\n"
            "  Die PRO.FILE-Nr. fehlt.") in liste["body"]
    assert "„liste.xlsx“ für Abfüllanlage TM-1" in liste["body"]

    # ein offener Entwurf wird neu aufgebaut, nicht verdoppelt
    nr = liste["id"]
    client.put(f"/api/maengellisten/{nr}", json={"subject": "alt", "body": "alt"})
    r = client.post(f"/api/boms/{bom['id']}/maengelliste")
    assert (r.status_code, r.get_json()["id"], r.get_json()["subject"][:10]) == (
        200, nr, "Mängellist")

    assert client.post(f"/api/maengellisten/{nr}/send").status_code == 409  # erst freigeben
    r = client.put(f"/api/maengellisten/{nr}", json={"subject": "Mängel TM-1", "body": "Bitte ä"})
    assert r.get_json()["body"] == "Bitte ä"
    assert client.put(f"/api/maengellisten/{nr}", json={"subject": ""}).status_code == 400
    assert client.post(f"/api/maengellisten/{nr}/approve", json={}).status_code == 400
    r = client.post(f"/api/maengellisten/{nr}/approve", json={"by": "Jan"})
    assert (r.get_json()["status"], r.get_json()["approved_by"]) == ("freigegeben", "Jan")
    # freigegeben, aber nicht gesendet: kein neuer Entwurf daneben
    assert client.post(f"/api/boms/{bom['id']}/maengelliste").status_code == 409

    r = client.post(f"/api/maengellisten/{nr}/send")
    assert r.status_code == 200
    assert (r.get_json()["status"], bool(r.get_json()["sent_at"])) == ("gesendet", True)
    [(ziel, nachricht)] = smtp.gesendet
    assert ziel == ("mail", 1025)
    assert (nachricht["To"], nachricht["Subject"]) == ("einkauf@beispiel-maschinenbau.test", "Mängel TM-1")
    assert client.post(f"/api/maengellisten/{nr}/send").status_code == 409  # nicht zweimal

    # nach dem Versand entsteht ein neuer Entwurf; der gesendete bleibt
    r = client.post(f"/api/boms/{bom['id']}/maengelliste")
    assert r.status_code == 201 and r.get_json()["id"] != nr
    listen = client.get(f"/api/boms/{bom['id']}/maengelliste").get_json()
    assert [m["status"] for m in listen] == ["entwurf", "gesendet"]
    assert client.put("/api/maengellisten/999", json={}).status_code == 404


def test_maengelliste_standard_ist_die_test_adresse_und_smtp_fehler(client, smtp):  # noqa: F811
    bom = lade_hoch(client)
    nr = client.post(f"/api/boms/{bom['id']}/maengelliste").get_json()["id"]
    client.post(f"/api/maengellisten/{nr}/approve", json={"by": "Jan"})
    smtp.fehler = ConnectionRefusedError("Verbindung abgelehnt")
    r = client.post(f"/api/maengellisten/{nr}/send")
    assert r.status_code == 502 and r.get_json()["error"].startswith(
        "Die E-Mail wurde nicht gesendet, weil der Postausgang gerade nicht erreichbar ist.")
    smtp.fehler = None
    r = client.post(f"/api/maengellisten/{nr}/send")
    assert r.get_json()["to_address"] == "test@example.com"
    assert smtp.gesendet[0][1]["To"] == "test@example.com"


def test_ohne_maengel_keine_maengelliste(client):
    bom = lade_hoch(client, ZEILEN[:2])
    r = client.post(f"/api/boms/{bom['id']}/maengelliste")
    assert r.status_code == 409 and r.get_json()["error"].startswith("Keine Mängel")


def test_korrektur_mit_namen_und_spur(client):
    bom = lade_hoch(client)
    assert korrigiere(client, bom, "280", manufacturer="Rexroth").status_code == 400  # ohne by
    assert korrigiere(client, bom, "280", by="Eva").status_code == 400  # nichts geändert
    assert korrigiere(client, bom, "280", by="Eva", manufacturer="").status_code == 400
    assert korrigiere(client, bom, "280", by="Eva", location=7).status_code == 400
    r = korrigiere(client, bom, "290", by="Eva", manufacturer="R. STAHL")
    assert r.status_code == 400  # gleicher Wert ist keine Änderung
    andere = lade_hoch(client)
    r = client.post(f"/api/boms/{andere['id']}/items/{position(bom, '280')['id']}/korrektur",
                    json={"by": "Eva", "location": "Zone 1"})
    assert r.status_code == 404

    r = korrigiere(client, bom, "280", by="Eva", note="laut Einkauf, Mail vom 14.11.",
                   manufacturer=" Bosch Rexroth ", manufacturer_article_no="R1651-EX")
    assert r.status_code == 200
    item = r.get_json()
    assert (item["manufacturer"], item["manufacturer_article_no"]) == ("Bosch Rexroth", "R1651-EX")
    assert (item["status"], item["status_note"]) == (
        "klaerung", "In der Stückliste steht kein Einbauort. Bitte entscheiden Sie, ob die "
                    "Position ein Ex-Teil ist.")
    assert [(k["field"], k["old_value"], k["new_value"], k["changed_by"], k["note"])
            for k in item["corrections"]] == [
        ("manufacturer", None, "Bosch Rexroth", "Eva", "laut Einkauf, Mail vom 14.11."),
        ("manufacturer_article_no", None, "R1651-EX", "Eva", "laut Einkauf, Mail vom 14.11.")]

    # die passenden Meldungen sind im Namen der Person abgehakt, die andere bleibt offen
    meldungen = {m.code: m for m in db.session.scalars(
        db.select(BomIssue).filter_by(bom_id=bom["id"], pos="280"))}
    hersteller = meldungen["hersteller_fehlt"]
    assert (hersteller.resolved_by, hersteller.comment) == ("Eva",
                                                           "Hersteller ist korrigiert auf „Bosch Rexroth“.")
    assert meldungen["herstellerartikelnr_fehlt"].resolved_at
    assert meldungen["einbauort_fehlt"].resolved_at is None

    r = korrigiere(client, bom, "280", by="Jan", location="Zone 1")
    assert r.get_json()["status"] == "offen"
    r = korrigiere(client, bom, "280", by="Jan", location="Zone 2")
    assert [(k["old_value"], k["new_value"], k["changed_by"])
            for k in r.get_json()["corrections"][2:]] == [(None, "Zone 1", "Jan"),
                                                           ("Zone 1", "Zone 2", "Jan")]
    ansicht = client.get(f"/api/boms/{bom['id']}/korrektur").get_json()
    assert [m["pos"] for m in ansicht["defects"]] == ["290", "300"]
    assert [(k["pos"], k["field"]) for k in ansicht["corrections"]][-1] == ("280", "location")
    assert ansicht["to_search"] == [position(bom, "280")["id"]]


def test_korrektur_verwirft_die_alte_suche(client):
    bom = lade_hoch(client)
    item = db.session.get(BomItem, position(bom, "10")["id"])
    item.searched_at, item.searched_in = datetime(2026, 11, 13), "Datenbank, Ordner"
    item.findings = [Finding(source="ordner", doc_type="sonstiges", status="vorgeschlagen"),
                     Finding(source="ordner", doc_type="sonstiges", status="abgelehnt",
                             decided_by="Jan")]
    item.email_drafts = [EmailDraft(to_address="t", subject="alt", body="alt")]
    db.session.commit()

    r = korrigiere(client, bom, "10", by="Eva", manufacturer_article_no="VACF-B-K1-1-5")
    assert (r.get_json()["status"], r.get_json()["searched_in"]) == ("offen", None)
    db.session.expire_all()
    item = db.session.get(BomItem, item.id)
    assert [f.status for f in item.findings] == ["abgelehnt"]  # Entscheidungen bleiben
    assert item.email_drafts == []                            # Entwurf mit altem Typ fällt weg
    assert db.session.scalar(db.select(db.func.count(ItemCorrection.id))) == 1


def test_nur_korrigierte_positionen_werden_neu_gesucht(client, ordner):
    bom = lade_hoch(client)
    r = client.post(f"/api/boms/{bom['id']}/search")
    vorher = {i["pos"]: i for i in r.get_json()["items"]}
    assert (vorher["10"]["status"], vorher["20"]["status"]) == ("nicht_gefunden", "nicht_gefunden")
    suche = f"/api/boms/{bom['id']}/korrektur/suche"
    assert client.post(suche).status_code == 409  # nichts korrigiert
    r = client.post(suche, json={"item_ids": [vorher["20"]["id"]]})
    assert r.status_code == 409 and "nur korrigierte Positionen" in r.get_json()["error"]
    assert client.post(suche, json={"item_ids": "10"}).status_code == 400
    assert client.post(suche, json={"internet_versuche": 99}).status_code == 400

    korrigiere(client, bom, "10", by="Eva", manufacturer_article_no="VACF-B-K1-1-5")
    korrigiere(client, bom, "280", by="Eva", manufacturer="Festo")  # weiter in Klärung
    gesucht_20 = db.session.get(BomItem, vorher["20"]["id"]).searched_at

    r = client.post(suche, json={"internet_versuche": 0})
    assert r.status_code == 200
    assert [(i["pos"], i["status"]) for i in r.get_json()] == [("10", "vorschlag")]
    (fund,) = db.session.get(BomItem, vorher["10"]["id"]).findings
    assert fund.matched_term == "VACF-B-K1-1-5"
    assert db.session.get(BomItem, vorher["20"]["id"]).searched_at == gesucht_20  # nicht erneut
    assert client.post(suche).status_code == 409  # schon gesucht

    # ausdrücklich gewählt: eine korrigierte Position darf noch einmal gesucht werden
    r = client.post(suche, json={"item_ids": [vorher["10"]["id"]]})
    assert [i["status"] for i in r.get_json()] == ["vorschlag"]
    assert len(db.session.get(BomItem, vorher["10"]["id"]).findings) == 1  # ohne Dublette


def test_neu_suchen_ohne_ordner_ergibt_503(client, tmp_path):
    client.application.config["ATEX_DOCS"] = str(tmp_path / "fehlt")
    bom = lade_hoch(client)
    korrigiere(client, bom, "300", by="Eva", location="Zone 1")
    assert client.post(f"/api/boms/{bom['id']}/korrektur/suche").status_code == 503


def test_korrigierte_stueckliste_hochladen(client):
    bom = lade_hoch(client)
    neu = [list(z) for z in ZEILEN]
    neu[2][3:5] = ["Bosch Rexroth", "R1651-EX"]  # Pos. 280: Hersteller und Typ ergänzt
    neu[3][1] = 99000290                         # Pos. 290: PRO.FILE-Nr. ergänzt
    neu[4][6] = None                             # leer bleibt unberührt
    neu[1][2] = "Andere Bezeichnung"             # Pos. 20: Bezeichnung korrigiert
    del neu[0]                                   # Pos. 10 fehlt in der Datei
    neu.append([999, 99000999, "Neu", "Festo", "X", 1, "Zone 1"])
    url = f"/api/boms/{bom['id']}/korrektur/excel"

    datei = (io.BytesIO(xlsx(neu)), "liste_korrigiert.xlsx")
    assert client.post(url, data={"file": datei}).status_code == 400  # ohne Namen
    datei = (io.BytesIO(b"kein excel"), "x.xlsx")
    assert client.post(url, data={"file": datei, "by": "Eva"}).status_code == 400

    r = client.post(url, data={"file": (io.BytesIO(xlsx(neu)), "liste_korrigiert.xlsx"),
                               "by": "Eva"})
    assert r.status_code == 200
    ergebnis = r.get_json()
    assert [(i["pos"], i["status"]) for i in ergebnis["changed"]] == [("20", "offen"),
                                                                       ("280", "klaerung"),
                                                                       ("290", "offen")]
    assert (ergebnis["unknown_pos"], ergebnis["missing_pos"]) == (["999"], ["10"])
    detail = client.get(f"/api/boms/{bom['id']}").get_json()
    assert len(detail["items"]) == 5  # keine neue Position
    assert position(detail, "20")["description"] == "Andere Bezeichnung"
    korrekturen = client.get(f"/api/boms/{bom['id']}/korrektur").get_json()["corrections"]
    assert [(k["pos"], k["field"], k["new_value"], k["changed_by"]) for k in korrekturen] == [
        ("20", "description", "Andere Bezeichnung", "Eva"),
        ("280", "manufacturer", "Bosch Rexroth", "Eva"),
        ("280", "manufacturer_article_no", "R1651-EX", "Eva"),
        ("290", "profile_no", "99000290", "Eva")]
    assert {k["note"] for k in korrekturen} == {"Korrigierte Stückliste liste_korrigiert.xlsx"}

    fremd = [[1, 1, "x", "y", "z", 1, "Zone 1"]]
    r = client.post(url, data={"file": (io.BytesIO(xlsx(fremd)), "f.xlsx"), "by": "Eva"})
    assert r.status_code == 400 and "Keine Position der Datei" in r.get_json()["error"]


def test_maengel_text_ohne_maschine():
    class Vorgang:
        machine, source_file_name = None, "x.xlsx"
    liste = [{"pos": "1", "profile_no": None, "description": None, "manufacturer": None,
              "manufacturer_article_no": None, "missing": ["Die PRO.FILE-Nr. fehlt."]}]
    text = korrektur.text_der_liste(Vorgang, liste)
    assert "„x.xlsx“ fehlen Angaben" in text
    assert "- Position 1, PRO.FILE-Nr. –, Hersteller –\n  Die PRO.FILE-Nr. fehlt." in text
    assert korrektur.betreff(Vorgang, liste) == "Mängelliste Stückliste x.xlsx: 1 Position"


def test_maengelliste_mit_nur_test_geht_an_die_testadresse(client, smtp):  # noqa: F811
    client.application.config.update(EINKAUF_ADRESSE="einkauf@beispiel-maschinenbau.test",
                                     EMAIL_NUR_TEST=True)
    bom = lade_hoch(client)
    liste = client.post(f"/api/boms/{bom['id']}/maengelliste").get_json()
    assert liste["to_address"] == "einkauf@beispiel-maschinenbau.test"  # gedacht für den Einkauf
    client.post(f"/api/maengellisten/{liste['id']}/approve", json={"by": "Jan"})
    r = client.post(f"/api/maengellisten/{liste['id']}/send")
    assert r.get_json()["to_address"] == "test@example.com"
    assert smtp.gesendet[0][1]["To"] == "test@example.com"
