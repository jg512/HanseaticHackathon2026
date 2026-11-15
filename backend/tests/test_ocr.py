"""Texterkennung für Scans, ohne Tesseract: die OCR-Schicht ist ersetzt."""

import io
import shutil
from pathlib import Path

import pytest
from conftest import xlsx
from test_suche import funde, pdf

from app.services import belege, pdf_text

# was die ersetzte Texterkennung je Datei und Seite „liest“
NJ50 = ("EG-Baumusterprüfbescheinigung\nPTB 00 ATEX 2048 X\nNäherungsschalter Typ NJ50-FP-E2-N\n"
        "Kennzeichnung: II 1G EEx ia IIC T6\nDie Schalter werden in der Zone 0 eingesetzt.")
KEL = "EG-Konformitätserklärung\nKlemmenkasten KEL 9401\nII 3G Ex e IIC T6\nPTB 02 ATEX 1082U"
OCR_TEXT = {"P+F_Ini_NJ50.pdf": {1: NJ50}, "Rittal_KEL.pdf": {2: KEL}}

ZEILEN = [
    [10, 99000010, "Induktiver Sensor", "Pepperl+Fuchs", "NJ50-FP-E2-N", 2, "Zone 1"],
    [20, 99000020, "Klemmenkasten", "Rittal", "KEL 9401", 1, "Zone 1"],
    [30, 99000030, "Klemmenkasten", "Rittal", "KEL 9402", 1, "Zone 2"],
]


@pytest.fixture
def ordner(client, tmp_path):
    docs = tmp_path / "docs"
    pdf(docs / "P+F" / "P+F_Ini_NJ50.pdf", "")  # Scan, eine Seite
    shutil.copy(docs / "P+F" / "P+F_Ini_NJ50.pdf", docs / "P+F" / "P+F_Ini_PTB00ATEX2048X.pdf")
    # Seite 1 mit Text, Seite 2 Scan
    pdf(docs / "Rittal" / "Rittal_KEL.pdf", "Betriebsanleitung KEL 9402\nII 2G Ex e IIC T6", "")
    client.application.config.update(ATEX_DOCS=str(docs), OCR=True, OCR_SPRACHEN="deu+eng",
                                     OCR_CACHE=str(tmp_path / "ocr"))
    return docs


@pytest.fixture
def tesseract(monkeypatch):
    """Tesseract „vorhanden“; gibt die Liste der gelesenen (Datei, Seite) zurück."""
    gelesen = []

    def ocr_seite(seite, ocr):
        name = Path(seite.parent.name).name
        gelesen.append((name, seite.number + 1))
        return OCR_TEXT[name][seite.number + 1]

    monkeypatch.setattr(pdf_text, "tesseract", lambda sprachen: ("/tessdata", None))
    monkeypatch.setattr(pdf_text, "_ocr_seite", ocr_seite)
    return gelesen


def ocr_lauf(client):
    r = client.application.test_cli_runner().invoke(args=["ocr"])
    return r.exit_code, r.output


def suche(client):
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    r = client.post(f"/api/boms/{bom.get_json()['id']}/search")
    return {i["pos"]: i["status"] for i in r.get_json()["items"]}


def test_ocr_lauf_liest_jede_datei_einmal(client, ordner, tesseract):
    code, ausgabe = ocr_lauf(client)
    assert code == 0
    # gleiche Datei unter zwei Namen nur einmal, Seiten mit Text gar nicht
    assert tesseract == [("P+F_Ini_NJ50.pdf", 1), ("Rittal_KEL.pdf", 2)]
    assert "OCR fertig: 2 Seiten aus 2 Dateien" in ausgabe

    scan = pdf_text.lies(ordner / "P+F" / "P+F_Ini_PTB00ATEX2048X.pdf")
    assert (scan.seiten, scan.ocr, scan.ungelesen) == ((NJ50,), (1,), ())
    gemischt = pdf_text.lies(ordner / "Rittal" / "Rittal_KEL.pdf")
    assert gemischt.seiten[1] == KEL and gemischt.ocr == (2,)
    # Schlüssel: SHA-256 der Datei, Sprachen, Auflösung
    assert sorted(p.name for p in Path(client.application.config["OCR_CACHE"]).iterdir()) == \
        sorted(f"{x.sha256}_deu+eng_300.json" for x in (scan, gemischt))

    # zweiter Lauf liest nichts neu, andere Sprachen schon
    assert ocr_lauf(client)[0] == 0 and len(tesseract) == 2
    client.application.config["OCR_SPRACHEN"] = "deu+eng+fra"
    assert pdf_text.lies(ordner / "P+F" / "P+F_Ini_NJ50.pdf").ungelesen == (1,)
    ocr_lauf(client)
    assert len(tesseract) == 4


def test_kaputter_zwischenspeicher_wird_ignoriert(client, ordner, tesseract):
    ocr_lauf(client)
    scan = ordner / "P+F" / "P+F_Ini_NJ50.pdf"
    datei = next(Path(client.application.config["OCR_CACHE"]).glob(
        f"{pdf_text.lies(scan).sha256}_*"))
    datei.write_text('{"sha256": "abgeschnitten', encoding="utf-8")
    assert (pdf_text.lies(scan).seiten, pdf_text.lies(scan).ungelesen) == (("",), (1,))
    ocr_lauf(client)  # der nächste Lauf ersetzt sie
    assert tesseract.count(("P+F_Ini_NJ50.pdf", 1)) == 2
    assert pdf_text.lies(scan).seiten == (NJ50,)


def test_ocr_fund_ist_nie_ok(client, ordner, tesseract):
    ocr_lauf(client)
    assert suche(client) == {"10": "vorschlag", "20": "vorschlag", "30": "vorschlag"}

    # genauer Typ und passende Kategorie, aber aus OCR-Text: unklar, mit Seite und Wortlaut
    (nj50,) = funde("10")
    assert (nj50.page, nj50.matched_term, nj50.doc_type) == (
        1, "NJ50-FP-E2-N", "eu_baumusterpruefbescheinigung")
    assert (nj50.ex_marking_raw, nj50.certificate_no) == ("II 1G EEx ia IIC T6",
                                                          "PTB 00 ATEX 2048 X")
    assert nj50.check_result == "unklar"
    assert nj50.check_message == (
        "Seite 1 ist eingescannt, der Text ist nicht sicher gelesen. Bitte prüfen Sie die "
        "Angaben am Seitenbild. Der Typ „NJ50-FP-E2-N“ steht genau so im Dokument, auf Seite 1. "
        "Die Kategorie 1G passt zu Zone 1. Dieselbe Datei liegt auch unter "
        "P+F_Ini_PTB00ATEX2048X.pdf.")
    assert "Näherungsschalter Typ NJ50-FP-E2-N" in nj50.snippet
    # belegt, aber nur aus OCR-Text: der Hinweis sagt es, Tabellen gibt es auf Scans nicht
    assert [(b["wert"], b["belegt"], b["seite"], b["tabelle"]) for b in nj50.belege] == [
        ("typ", True, 1, None), ("kennzeichnung", True, 1, None), ("zertifikat", True, 1, None),
        ("kategorie", True, 1, None)]
    assert [b["hinweis"] for b in nj50.belege[:3]] == [belege.SCAN] * 3

    # Widerspruch aus OCR-Text bleibt eine Warnung
    (kel,) = funde("20")
    assert (kel.page, kel.check_result) == (2, "warnung")
    assert kel.check_message.startswith("Seite 2 ist eingescannt, der Text ist nicht sicher")
    assert kel.check_message.endswith(
        "Die Kategorie 3G reicht für Zone 1 nicht aus. Verlangt ist 1G oder 2G.")

    # Typ und Kennzeichnung auf der Textseite: ok, die OCR-Seite wird nur genannt
    (kel2,) = funde("30")
    assert (kel2.page, kel2.check_result) == (1, "ok")
    assert [(b["wert"], b["angabe"], b["seite"], b["hinweis"]) for b in kel2.belege[:2]] == [
        ("typ", "KEL 9402", 1, None), ("kennzeichnung", "II 2G Ex e IIC T6", 1, None)]
    assert kel2.check_message.endswith(
        "Seite 2 ist eingescannt, der Text dort ist nicht sicher gelesen.")


def test_scan_noch_nicht_gelesen(client, ordner, tesseract):
    suche(client)  # ohne OCR-Lauf
    assert tesseract == []  # die Suche wartet nicht auf OCR
    nj50 = funde("10")[0]
    assert (nj50.page, nj50.check_result) == (None, "unklar")
    assert nj50.check_message == (
        "Im Dokument steht nur „NJ50“, nicht genau der Typ „NJ50-FP-E2-N“. Ob das Dokument dazu "
        "gehört, ist nicht sicher. Im Dokument ist kein Text lesbar, geprüft ist nur der "
        "Dateiname. Im Text steht keine lesbare Ex-Kennzeichnung. Deshalb lässt sich die "
        "Kategorie nicht prüfen. Seite 1 ist eingescannt und noch nicht gelesen.")

    # nach dem OCR-Lauf ersetzt die nächste Suche den vorläufigen Vorschlag
    ocr_lauf(client)
    client.post(f"/api/boms/{nj50.item.bom_id}/search")
    (nj50,) = funde("10")
    assert (nj50.page, nj50.check_result) == (1, "unklar")
    assert nj50.check_message.startswith("Seite 1 ist eingescannt, der Text ist nicht sicher")


@pytest.mark.parametrize("ocr_an", [True, False])
def test_ohne_tesseract_wie_bisher(client, ordner, monkeypatch, ocr_an):
    monkeypatch.setattr(pdf_text, "tesseract",
                        lambda sprachen: (None, "Tesseract ist nicht installiert, Scans werden "
                                                "ohne OCR gelesen."))
    client.application.config["OCR"] = ocr_an
    code, ausgabe = ocr_lauf(client)
    assert (code, ausgabe.strip()) == (
        (1, "Tesseract ist nicht installiert, Scans werden ohne OCR gelesen.") if ocr_an
        else (0, "OCR ist ausgeschaltet (OCR=0)."))

    scan = pdf_text.lies(ordner / "P+F" / "P+F_Ini_NJ50.pdf")
    assert (scan.seiten, scan.ocr, scan.ungelesen) == (("",), (), ())
    suche(client)
    assert funde("10")[0].check_message == (
        "Im Dokument steht nur „NJ50“, nicht genau der Typ „NJ50-FP-E2-N“. Ob das Dokument dazu "
        "gehört, ist nicht sicher. Im Dokument ist kein Text lesbar, geprüft ist nur der "
        "Dateiname. Im Text steht keine lesbare Ex-Kennzeichnung. Deshalb lässt sich die "
        "Kategorie nicht prüfen.")
    assert not Path(client.application.config["OCR_CACHE"]).exists()


def test_seitenangabe():
    assert pdf_text.seitenangabe([4]) == "Seite 4"
    assert pdf_text.seitenangabe([1, 2, 3, 5, 7, 8]) == "Seiten 1–3, 5, 7–8"


def test_ocr_ohne_treffer_stuerzt_nicht_ab_und_bleibt_unklar():
    """Hochgeladenes Dokument ohne Typ-Treffer (treffer=None): OCR-Seite mit Kennzeichnung zählt als Beleg."""
    from types import SimpleNamespace

    from app.services import suche

    pdf = SimpleNamespace(ocr={1: True}, ungelesen=[], seiten=["Kennzeichnung II 2G Ex ia IIC T4 Gb"])
    ergebnis, meldung = suche._mit_ocr(pdf, None, ["II 2G Ex ia IIC T4 Gb"], "ok", "Kategorie 2G passt zu Zone 1.")
    assert ergebnis == "unklar"
    assert meldung.startswith("Seite 1 ist eingescannt, der Text ist nicht sicher gelesen. "
                              "Bitte prüfen Sie die Angaben am Seitenbild.")
