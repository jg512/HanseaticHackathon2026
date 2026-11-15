import io
import shutil

import pymupdf
import pytest
from conftest import xlsx

from app.db import db
from app.models import Finding
from app.services.suche import dokumentart, herstellerordner, sprachen, zertifikatsnummern

VTUG_DE = ("EU-Baumusterprüfbescheinigung\nNummer: IBExU16ATEXB021 X\n"
           "Produkt: Ventilinsel\nTyp: VTUG-10-VI-EX2E und VTUG-14-VI-EX2E\n"
           "Die Kennzeichnung des Gerätes muss die folgenden Angaben enthalten:\n"
           "II 3G Ex ec IIC T4 Gc\nDie Ventilinseln werden mit der Steuerung verbunden und sind "
           "für den Einsatz in der Zone 2 bestimmt. Das ist nicht für Zone 1.")
VTUG_EN = ("EU-Type Examination Certificate\nNumber: IBExU16ATEXB021 X\n"
           "Type: VTUG-10-VI-EX2E and VTUG-14-VI-EX2E\nThe marking of the product shall include:\n"
           "II 3G Ex ec IIC T4 Gc\nThe valve terminals are used for the control of pneumatic "
           "systems and shall be installed in zone 2. This is the schedule of the certificate.")
VACF = ("EU-Konformitätserklärung\nMagnetspule VACF-B-K1-1-5\nII 2G Ex mb IIC T5 Gb\n"
        "Baumusterprüfbescheinigung IBExU16ATEX1146 X\n"
        # deutscher Satz: die Sprache wird erkannt, die deutsche Fassung ist Pflicht (M8)
        "Der Hersteller erklärt, dass das Produkt mit den Vorschriften der Richtlinie "
        "übereinstimmt und die Anforderungen für den Betrieb erfüllt werden, die in der Anlage "
        "stehen.")


def pdf(pfad, *seiten):
    """Legt ein PDF an; eine leere Seite steht für einen Scan ohne Textebene."""
    doc = pymupdf.open()
    for text in seiten:
        seite = doc.new_page()
        if text:
            seite.insert_text((40, 60), text, fontsize=8)
        else:
            seite.draw_rect(pymupdf.Rect(40, 40, 200, 200), fill=(0.5, 0.5, 0.5))
    pfad.parent.mkdir(parents=True, exist_ok=True)
    doc.save(pfad)
    return pfad


ZEILEN = [
    [10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1A-1-EX4-M", 4, "Zone 1"],
    [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
    [30, 99000030, "Ventilinsel in Zone 1", "Festo", "VTUG-14-VI-EX2E", 1, "Zone 1"],
    [140, 99000140, "Trennschaltverstärker", "Pepperl+Fuchs", "KCD2-SR-Ex1.LB", 2,
     "Schaltschrank außerhalb Ex"],
    [150, 99000150, "Induktiver Sensor", "Pepperl+Fuchs", "NJ50-…-N", 2, "Zone 1"],
    [240, 99000240, "Klemmenkasten", "R. STAHL", "8146", 2, "Zone 1"],
    [280, 99000280, "Linearführung", None, None, 2, None],
]


@pytest.fixture
def ordner(client, tmp_path):
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf", VTUG_DE)
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_en.pdf", VTUG_EN)
    pdf(tmp_path / "Festo" / "Festo_Konformit#U00e4tserkl#U00e4rung_VACF-B-K1-1-5.pdf", VACF)
    pdf(tmp_path / "P+F" / "P+F_Konformitaetserklaerung_KCD2.pdf",
        "EU-Declaration of conformity\nKCD2-SR-EX1.LB\n214240\nKCD2-SR-EX1.LB.SP\n"
        "Marking\nII (1) G\nII 3 G\nBaseefa 13 ATEX 0080")
    pdf(tmp_path / "P+F" / "P+F_Ini_NJ50_KonformErkl.pdf", "")
    client.application.config["ATEX_DOCS"] = str(tmp_path)
    return tmp_path


def lade_hoch(client):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    return r.get_json()


def funde(pos):
    return [f for f in db.session.scalars(db.select(Finding)) if f.item.pos == pos]


def test_suche_legt_vorschlaege_an(client, ordner):
    bom = lade_hoch(client)
    r = client.post(f"/api/boms/{bom['id']}/search")
    assert r.status_code == 200
    items = {i["pos"]: i for i in r.get_json()["items"]}
    assert {p: i["status"] for p, i in items.items()} == {
        "10": "vorschlag", "20": "vorschlag", "30": "vorschlag", "140": "vorschlag",
        "150": "vorschlag", "240": "nicht_gefunden", "280": "klaerung"}
    assert items["240"]["searched_in"] == (
        "Gesucht hat der Agent in der Datenbank. Einen Ordner für diesen Hersteller gibt es "
        "nicht. Im Internet hat der Agent nicht gesucht, weil die Websuche ausgeschaltet ist.")
    assert items["20"]["searched_in"] == ("Gesucht hat der Agent in der Datenbank und im Ordner "
                                          "des Herstellers.")
    assert items["280"]["searched_in"] is None  # ohne Angaben keine Suche

    # genauer Typ und passende Kategorie: ok, beide Sprachversionen als eigene Vorschläge
    f = funde("20")
    assert [x.file_path for x in f] == ["Festo/Festo_IBExU16ATEXB021X_de.pdf",
                                        "Festo/Festo_IBExU16ATEXB021X_en.pdf"]
    assert [x.languages for x in f] == ["de", "en"]
    de = f[0]
    assert (de.source, de.doc_type, de.page, de.matched_term, de.check_result) == (
        "ordner", "eu_baumusterpruefbescheinigung", 1, "VTUG-10-VI-EX2E", "ok")
    assert (de.ex_marking_raw, de.certificate_no) == ("II 3G Ex ec IIC T4 Gc", "IBExU16ATEXB021 X")
    assert de.check_message == ("Der Typ „VTUG-10-VI-EX2E“ steht genau so im Dokument, auf "
                                "Seite 1. Die Kategorie 3G passt zu Zone 2.")
    assert "Typ: VTUG-10-VI-EX2E" in de.snippet
    assert len(de.file_hash) == 64 and de.status == "vorgeschlagen"

    # Falle: anderer Typ derselben Reihe, Dateiname mit „#U00e4“
    (vacf,) = funde("10")
    assert vacf.check_result == "unklar"
    assert vacf.file_path == "Festo/Festo_Konformit#U00e4tserkl#U00e4rung_VACF-B-K1-1-5.pdf"
    assert vacf.check_message.startswith("Im Dokument steht nur „VACF-B-K1-1-5“")

    # genauer Typ, aber Kategorie 3G in Zone 1: Widerspruch
    w = funde("30")[0]
    assert w.check_result == "warnung" and w.check_message.endswith(
        "Die Kategorie 3G reicht für Zone 1 nicht aus. Verlangt ist 1G oder 2G.")

    # zugehöriges Betriebsmittel außerhalb des Ex-Bereichs
    (kcd2,) = funde("140")
    assert (kcd2.check_result, kcd2.doc_type, kcd2.page) == ("ok", "eu_konformitaetserklaerung", 1)

    # Scan ohne Text: nur über den Dateinamen, unklar
    (scan,) = funde("150")
    assert (scan.page, scan.languages, scan.check_result) == (None, None, "unklar")
    assert "Im Dokument ist kein Text lesbar, geprüft ist nur der Dateiname." in scan.check_message


def test_erneute_suche_ohne_dubletten_und_ohne_abgelehnte(client, ordner):
    bom = lade_hoch(client)
    client.post(f"/api/boms/{bom['id']}/search")
    vorher = len(db.session.scalars(db.select(Finding)).all())
    abgelehnt = funde("10")[0]
    abgelehnt.status = "abgelehnt"
    db.session.commit()

    r = client.post(f"/api/boms/{bom['id']}/search")
    assert len(db.session.scalars(db.select(Finding)).all()) == vorher
    assert [f.status for f in funde("10")] == ["abgelehnt"]
    assert {i["pos"]: i["status"] for i in r.get_json()["items"]}["10"] == "nicht_gefunden"


def test_gleiche_datei_zweimal_ist_ein_vorschlag(client, ordner):
    quelle = ordner / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf"
    shutil.copy(quelle, ordner / "Festo" / "Festo_Ventilinsel_Kopie.pdf")
    bom = lade_hoch(client)
    client.post(f"/api/boms/{bom['id']}/search")
    assert [f.file_path for f in funde("20")] == ["Festo/Festo_IBExU16ATEXB021X_de.pdf",
                                                  "Festo/Festo_IBExU16ATEXB021X_en.pdf"]
    assert funde("20")[0].check_message.endswith(
        "Dieselbe Datei liegt auch unter Festo_Ventilinsel_Kopie.pdf.")


def test_kein_ex_teil_wird_nicht_gesucht(client, ordner):
    bom = lade_hoch(client)
    item = next(i for i in bom["items"] if i["pos"] == "20")
    client.post(f"/api/boms/{bom['id']}/items/{item['id']}/ex-teil", json={"ex_teil": False, "by": "jan"})
    items = {i["pos"]: i for i in client.post(f"/api/boms/{bom['id']}/search").get_json()["items"]}
    assert (items["20"]["status"], items["20"]["searched_in"]) == ("kein_ex_teil", None)
    assert funde("20") == []


def test_fehler(client, tmp_path):
    assert client.post("/api/boms/99/search").status_code == 404
    bom = lade_hoch(client)
    client.application.config["ATEX_DOCS"] = str(tmp_path / "fehlt")
    r = client.post(f"/api/boms/{bom['id']}/search")
    assert r.status_code == 503
    assert "fehlt" in r.get_json()["error"]


def test_herstellerordner(tmp_path):
    for name in ("Festo", "P+F", "Siemens"):
        (tmp_path / name).mkdir()
    assert herstellerordner(tmp_path, "Pepperl+Fuchs").name == "P+F"
    assert herstellerordner(tmp_path, "Siemens AG").name == "Siemens"
    assert herstellerordner(tmp_path, "festo").name == "Festo"
    assert herstellerordner(tmp_path, "R. STAHL") is None
    assert herstellerordner(tmp_path, None) is None
    # Ablagen der Internet-Stufe und der hochgeladenen Dateien sind keine Herstellerordner
    for name in ("_upload", "_internet"):
        (tmp_path / name / "Festo").mkdir(parents=True)
    assert herstellerordner(tmp_path, "Upload GmbH") is None
    assert herstellerordner(tmp_path, "Internet AG") is None
    assert herstellerordner(tmp_path, "Festo").name == "Festo"


@pytest.mark.parametrize("kopf, art", [
    ("EU-Konformitätserklärung\nDie Baumusterprüfbescheinigung PTB 00 ATEX 2048 X", "eu_konformitaetserklaerung"),
    ("(1) EU -TYPE EXAMINATION CERTIFICATE\nsee declaration of conformity", "eu_baumusterpruefbescheinigung"),
    ("UK Declaration of conformity", "uk_konformitaetserklaerung"),
    ("EU and UK Declaration of Conformity (EN)", "eu_konformitaetserklaerung"),
    ("CERTIFICATE (1) Type Examination\nRegulations 2016, UKSI 2016:1107", "uk_baumusterpruefbescheinigung"),
    ("Betriebsanleitung\nKabeldose\nKonformitätserklärung siehe Anhang", "bedienungsanleitung"),
    ("Manuel d'instructions", "bedienungsanleitung"),
    ("Herstellererklärung über den Einsatz", "sonstiges"),
])
def test_dokumentart(kopf, art):
    assert dokumentart([kopf]) == art


def test_sprachen_und_zertifikat():
    assert sprachen([VTUG_DE, VTUG_EN]) == "de,en"
    assert sprachen(["(DE) Text (EN) text (FR) texte"]) == "de,en,fr"
    assert sprachen([""]) is None
    assert zertifikatsnummern(["PTB 00 ATEX 2048 X und KEMA 04ATEX1244, PTB 00 ATEX 2048 X"]) == \
        "PTB 00 ATEX 2048 X; KEMA 04ATEX1244"
    assert zertifikatsnummern(["Certificate No.: IECEx PTB 09.0035U"]) == "IECEx PTB 09.0035U"
    assert zertifikatsnummern(["113ExU16ATEX1146 X"]) is None  # verlesen: lieber nichts
