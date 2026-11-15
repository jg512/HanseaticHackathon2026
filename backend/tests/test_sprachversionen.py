import io

import pytest
from conftest import xlsx
from test_suche import VTUG_DE, pdf

from app.db import db
from app.models import Finding
from app.services.sprachversionen import dateiname_teilen

# Englische Fassung ohne den Typ im Text: die normale Ordnersuche findet sie nicht
VTUG_EN_OHNE_TYP = ("EU-Type Examination Certificate\nNumber: IBExU16ATEXB021 X\n"
                    "Product: valve terminal\nThe marking of the product shall include:\n"
                    "II 3G Ex ec IIC T4 Gc\nThe valve terminals are used for the control of "
                    "pneumatic systems and shall be installed in zone 2. This is the schedule.")
# andere Dokumentart mit derselben Nummer: keine Sprachversion der Bescheinigung
ERKLAERUNG_EN = ("EU Declaration of Conformity\nThe product is in conformity with the directive "
                 "and the certificate IBExU16ATEXB021 X of the notified body. This is the "
                 "declaration of the manufacturer and it shall be kept with the product.")
KEL_DE = ("EG-Baumusterprüfbescheinigung\nPTB 02 ATEX 1082 U\nKlemmenkasten KEL 94XX\n"
          "II 2G Ex e IIC Gb\nDie Klemmenkästen werden mit der Anlage verbunden und sind für den "
          "Einsatz in der Zone 1 bestimmt. Das Gerät ist nicht für Zone 0, die Prüfung ist mit "
          "dem Bericht und der Anlage abgeschlossen.")

ZEILEN = [
    [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
    [220, 99000220, "Klemmenkasten", "Rittal", "KEL 94XX", 1, "Zone 1"],
]


@pytest.fixture
def ordner(client, tmp_path):
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf", VTUG_DE)
    pdf(tmp_path / "Rittal" / "Rittal_PTB02ATEX1082U_KEL_94XX_de.pdf", KEL_DE)
    client.application.config["ATEX_DOCS"] = str(tmp_path)
    return tmp_path


def suche(client):
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    bom_id = bom.get_json()["id"]
    client.post(f"/api/boms/{bom_id}/search")
    return bom_id


def funde(pos):
    return [f for f in db.session.scalars(db.select(Finding)) if f.item.pos == pos]


def sprachen(client, bom_id):
    antwort = client.get(f"/api/boms/{bom_id}/sprachen").get_json()
    return antwort["sprachen"], {i["pos"]: i for i in antwort["items"]}


def test_fehlende_sprache_wird_gemeldet(client, ordner):
    bom_id = suche(client)
    pflicht, items = sprachen(client, bom_id)
    assert pflicht == ["de", "en"]
    assert items["20"]["sprachen"] == {"eu_baumusterpruefbescheinigung": ["de"]}
    assert items["20"]["fehlende_sprachen"] == {"eu_baumusterpruefbescheinigung": ["en"]}
    assert [f.file_path for f in funde("20")] == ["Festo/Festo_IBExU16ATEXB021X_de.pdf"]


def test_zweiter_durchgang_gleicher_dateiname(client, ordner):
    pdf(ordner / "Festo" / "Festo_IBExU16ATEXB021X_en.pdf", VTUG_EN_OHNE_TYP)
    pdf(ordner / "Festo" / "Festo_Erklaerung_VTUG_en.pdf", ERKLAERUNG_EN)
    bom_id = suche(client)

    de, en = funde("20")  # die Erklärung trägt dieselbe Nummer, ist aber eine andere Art
    assert en.file_path == "Festo/Festo_IBExU16ATEXB021X_en.pdf"
    assert (en.source, en.doc_type, en.languages, en.status, en.check_result) == (
        "ordner", "eu_baumusterpruefbescheinigung", "en", "vorgeschlagen", "unklar")
    assert en.check_message.startswith(
        "Das ist vermutlich die englische Fassung von Festo_IBExU16ATEXB021X_de.pdf, denn der "
        "Dateiname ist bis auf das Sprachkürzel gleich. ")
    _, items = sprachen(client, bom_id)
    assert items["20"]["sprachen"] == {"eu_baumusterpruefbescheinigung": ["de", "en"]}
    assert items["20"]["fehlende_sprachen"] == {}

    # erneute Suche: keine Dubletten
    client.post(f"/api/boms/{bom_id}/search")
    assert len(funde("20")) == 2


def test_zweiter_durchgang_gleiche_zertifikatsnummer(client, ordner):
    pdf(ordner / "Festo" / "Ventilinsel_Zertifikat.pdf", VTUG_EN_OHNE_TYP)
    suche(client)
    _, en = funde("20")
    assert en.file_path == "Festo/Ventilinsel_Zertifikat.pdf"
    assert "denn beide nennen die Zertifikatsnummer IBExU16ATEXB021 X." in en.check_message


def test_scan_als_sprachversion(client, ordner):
    # Scan ohne Text: die Ordnersuche schlägt ihn über den Dateinamen als „sonstiges“ ohne
    # Sprache vor; der zweite Durchgang deutet ihn als englische Fassung der Bescheinigung
    pdf(ordner / "Rittal" / "Rittal_PTB02ATEX1082U_KEL_94XX_en.pdf", "")
    bom_id = suche(client)
    de, en = funde("220")
    assert de.languages == "de"
    assert (en.file_path, en.doc_type, en.languages) == (
        "Rittal/Rittal_PTB02ATEX1082U_KEL_94XX_en.pdf", "eu_baumusterpruefbescheinigung", "en")
    assert ("Die Dokumentart haben wir von dort übernommen. Die Sprache steht nur im Dateinamen."
            in en.check_message)
    assert en.check_result == "unklar"
    assert sprachen(client, bom_id)[1]["220"]["fehlende_sprachen"] == {}


def test_neuer_scan_als_sprachversion(client, ordner):
    pdf(ordner / "Festo" / "Festo_IBExU16ATEXB021X_en.pdf", "")  # ohne Typ im Dateinamen
    suche(client)
    _, en = funde("20")
    assert (en.doc_type, en.languages, en.page, en.check_result) == (
        "eu_baumusterpruefbescheinigung", "en", None, "unklar")


def test_abgelehnt_zaehlt_nicht_und_kommt_nicht_wieder(client, ordner):
    pdf(ordner / "Festo" / "Festo_IBExU16ATEXB021X_en.pdf", VTUG_EN_OHNE_TYP)
    bom_id = suche(client)
    en = funde("20")[1]
    en.status = "abgelehnt"
    db.session.commit()
    client.post(f"/api/boms/{bom_id}/search")
    assert [f.status for f in funde("20")] == ["vorgeschlagen", "abgelehnt"]
    assert sprachen(client, bom_id)[1]["20"]["fehlende_sprachen"] == {
        "eu_baumusterpruefbescheinigung": ["en"]}


def test_freigabe_deckt_ab_auch_aus_der_datenbank(client, ordner):
    bom_id = suche(client)
    de = funde("20")[0]
    r = client.post(f"/api/findings/{de.id}/decision", json={"decision": "freigeben", "by": "jan"})
    assert r.status_code == 200
    # Die Datenbank allein: ein neuer Vorgang mit derselben PRO.FILE-Nr., noch ohne Suche
    neu = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "neu.xlsx")})
    item = sprachen(client, neu.get_json()["id"])[1]["20"]
    assert item["sprachen"] == {"eu_baumusterpruefbescheinigung": ["de"]}
    assert item["fehlende_sprachen"] == {"eu_baumusterpruefbescheinigung": ["en"]}


def test_pflichtsprachen_einstellbar(client, ordner):
    client.application.config["SPRACHEN"] = ["de"]
    bom_id = suche(client)
    pflicht, items = sprachen(client, bom_id)
    assert pflicht == ["de"]
    assert items["20"]["fehlende_sprachen"] == {}
    client.application.config["SPRACHEN"] = ["de", "en", "fr"]
    assert sprachen(client, bom_id)[1]["220"]["fehlende_sprachen"] == {
        "eu_baumusterpruefbescheinigung": ["en", "fr"]}


def test_kein_ex_teil_und_unbekannt(client, ordner):
    assert client.get("/api/boms/99/sprachen").status_code == 404
    bom_id = suche(client)
    item = next(i for i in sprachen(client, bom_id)[1].values() if i["pos"] == "20")
    client.post(f"/api/boms/{bom_id}/items/{item['id']}/ex-teil",
                json={"ex_teil": False, "by": "jan"})
    assert sprachen(client, bom_id)[1]["20"] == {
        "id": item["id"], "pos": "20", "status": "kein_ex_teil", "sprachen": {},
        "pflicht_fehlt": {}, "fehlende_sprachen": {}}


@pytest.mark.parametrize("name, grund, kuerzel", [
    ("Rittal_PTB02ATEX1082U_KEL_94XX_de.pdf", "rittalptb02atex1082ukel94xx", ["de"]),
    ("Festo_DSNU_Konformit#U00e4tserkl#U00e4rung_de_en_fr_etc.pdf",
     "festodsnukonformitätserklärung", ["de", "en", "fr"]),
    ("P+F_Betriebsanleitung_Kabeldose_ger.pdf", "pfbetriebsanleitungkabeldose", ["de"]),
    ("KEMA_EGBaum_KEMA_04ATEX1243_Issue6_152-1AA0.engl.pdf",
     "kemaegbaumkema04atex1243issue61521aa0", ["en"]),
    ("Siemens_ET200S_V1_10_2015_d_e.pdf", "siemenset200sv1102015", ["de", "en"]),
    ("1517000_Schutzart_EN.pdf", "1517000schutzart", ["en"]),
    ("festo_schalter_smt-8f_konf_714770_x.pdf", "festoschaltersmt8fkonf714770x", []),
    ("Festo_SMT-8F-I-8,2V-K5,0-OE-EX_Konf.pdf", "festosmt8fi82vk50oeexkonf", []),
    ("Typ_VXY-E.pdf", "typvxye", []),  # „-E“ gehört zum Typ, nicht zur Sprache
])
def test_dateiname_teilen(name, grund, kuerzel):
    assert dateiname_teilen(name) == (grund, kuerzel)
