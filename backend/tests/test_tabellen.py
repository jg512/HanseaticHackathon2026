"""Tabellen aus PDFs und Belege je Angabe eines Vorschlags (services/belege.py)."""

import io
import os
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from conftest import xlsx

from app.services import belege, pdf_text

ECHT = Path(os.environ.get("ATEX_DOCS", "/data/ex_dokumente"))
KOPF = ["Typ", "Kennzeichnung", "Zertifikat"]
VTUG = [KOPF, ["VTUG-10-VI-EX2E", "II 3G Ex ec IIC T4 Gc", "IBExU16ATEXB021 X"],
        ["VTUG-14-VI-EX2E", "II 2G Ex h IIC T4 Gb", "IBExU16ATEX1146 X"]]


def tabellen_pdf(pfad, zeilen, davor="EU-Konformitätserklärung Ventilinsel", scan=False):
    """PDF mit Text oben und einer Tabelle mit Linien; scan: zweite Seite ohne Textebene."""
    doc = pymupdf.open()
    seite = doc.new_page()
    seite.insert_text((40, 40), davor, fontsize=9)
    x, h, oben = [40, 200, 380, 560], 20, 60
    for i, zeile in enumerate(zeilen):
        for j, text in enumerate(zeile):
            seite.insert_text((x[j] + 3, oben + i * h + 14), text, fontsize=8)
    for i in range(len(zeilen) + 1):
        seite.draw_line((x[0], oben + i * h), (x[-1], oben + i * h))
    for xx in x:
        seite.draw_line((xx, oben), (xx, oben + len(zeilen) * h))
    if scan:
        doc.new_page().draw_rect(pymupdf.Rect(40, 40, 200, 200), fill=(0.5, 0.5, 0.5))
    pfad.parent.mkdir(parents=True, exist_ok=True)
    doc.save(pfad)
    return pfad


def vorschlag(datei, artikel, ort, kennz=None, nummern=None, seite=1):
    """Position und Vorschlag ohne Datenbank, so wie belege.pruefe sie liest."""
    item = SimpleNamespace(manufacturer_article_no=artikel, location=ort)
    finding = SimpleNamespace(file_path=datei, page=seite, ex_marking_raw=kennz,
                              certificate_no=nummern)
    return item, finding


def kurz(liste):
    """(wert, angabe, belegt, seite, tabelle, zeile) je Beleg."""
    return [(b["wert"], b["angabe"], b["belegt"], b["seite"], b["tabelle"], b["zeile"])
            for b in liste]


def test_tabellen_je_seite(tmp_path):
    pfad = tabellen_pdf(tmp_path / "vtug.pdf", VTUG, scan=True)
    (t,) = pdf_text.tabellen(pfad, 1)
    assert (t.seite, t.nr) == (1, 1)
    assert t.zeilen == tuple(tuple(z) for z in VTUG)
    assert pdf_text.tabellen(pfad, 2) is None  # Scan: keine Tabellen, nichts geraten
    assert pdf_text.tabellen(pfad, 3) is None  # gibt es nicht

    # Text ohne Linien: keine Tabelle
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "Typ VTUG-10-VI-EX2E\nII 3G Ex ec IIC T4 Gc")
    doc.save(tmp_path / "ohne.pdf")
    assert pdf_text.tabellen(tmp_path / "ohne.pdf", 1) == ()


def test_belege_aus_tabelle(tmp_path):
    tabellen_pdf(tmp_path / "Festo" / "vtug.pdf", VTUG)
    item, f = vorschlag("Festo/vtug.pdf", "VTUG-10-VI-EX2E", "Zone 2",
                        "II 3G Ex ec IIC T4 Gc; II 2G Ex h IIC T4 Gb", "IBExU16ATEXB021 X")
    liste = belege.pruefe(item, f, tmp_path)
    assert kurz(liste) == [
        ("typ", "VTUG-10-VI-EX2E", True, 1, 1, 2),
        ("kennzeichnung", "II 3G Ex ec IIC T4 Gc", True, 1, 1, 2),
        ("kennzeichnung", "II 2G Ex h IIC T4 Gb", True, 1, 1, 3),
        ("zertifikat", "IBExU16ATEXB021 X", True, 1, 1, 2),
        ("kategorie", "2G, 3G", True, 1, 1, 2)]
    typ, eigene, fremde, nummer, kategorie = liste
    assert typ["hinweis"] == ("In derselben Tabellenzeile steht Kennzeichnung „II 3G Ex ec IIC "
                              "T4 Gc“ und Zertifikatsnummer „IBExU16ATEXB021 X“.")
    assert fremde["hinweis"] == ("Das steht in Zeile 3, der Typ aber in Zeile 2 derselben "
                                 "Tabelle. Es gehört womöglich zu einem anderen Typ.")
    assert "Typ „VTUG-10-VI-EX2E“" in nummer["hinweis"]
    assert kategorie["hinweis"] == ("Abgeleitet aus der Kennzeichnung „II 3G Ex ec IIC T4 Gc“ und "
                                    "dem Einbauort „Zone 2“.")
    assert belege.bewerte("ok", "Typ steht da.", liste) == ("ok", "Typ steht da.")


def test_unbelegtes_wird_markiert(tmp_path):
    tabellen_pdf(tmp_path / "Festo" / "vtug.pdf", VTUG, scan=True)
    # anderer Typ derselben Reihe, erfundene Nummer, Einbauort ohne Zone
    item, f = vorschlag("Festo/vtug.pdf", "VTUG-12-VI-EX2E", "Halle 3",
                        "II 3G Ex ec IIC T4 Gc", "PTB 99 ATEX 9999")
    typ, kennz, nummer, kategorie = belege.pruefe(item, f, tmp_path)
    # die ähnliche Stelle wird gezeigt, ist aber kein Beleg
    assert (typ["belegt"], typ["seite"], typ["tabelle"], typ["zeile"], typ["hinweis"]) == (
        False, 1, 1, 2, "Genau dieser Typ steht nicht im Dokument. Im Dokument steht nur "
                        "„VTUG-10-VI-EX2E“.")
    assert kennz["belegt"] is True
    assert (nummer["belegt"], nummer["hinweis"]) == (
        False, "Das steht auf keiner lesbaren Seite des Dokuments.")
    assert (kategorie["belegt"], kategorie["angabe"], kategorie["hinweis"]) == (
        False, "3G", "Der Einbauort „Halle 3“ nennt keine Zone. Deshalb ist der Vergleich nicht "
                     "belegt.")

    ergebnis, meldung = belege.bewerte("ok", "Alles passt.", [typ, kennz, nummer, kategorie])
    assert (ergebnis, meldung) == (
        "unklar", "Alles passt. Im Dokument nicht belegt sind Typ „VTUG-12-VI-EX2E“, "
                  "Zertifikatsnummer „PTB 99 ATEX 9999“ und Kategorie „3G“.")
    assert belege.bewerte("ok", "A.", [nummer]) == (
        "unklar", "A. Im Dokument nicht belegt ist Zertifikatsnummer „PTB 99 ATEX 9999“.")
    assert belege.bewerte("warnung", "W.", [nummer]) == ("warnung", "W.")

    # Datei fehlt: nichts ist belegt
    item, f = vorschlag("Festo/weg.pdf", None, "Zone 1", "II 2G", None)
    assert kurz(belege.pruefe(item, f, tmp_path)) == [
        ("typ", None, False, None, None, None), ("kennzeichnung", "II 2G", False, None, None, None),
        ("kategorie", "2G", False, None, None, None)]


def test_suche_legt_belege_an(client, tmp_path):
    """Der neue Vorschlag wird vor dem Speichern geprüft, die API gibt die Belege aus."""
    tabellen_pdf(tmp_path / "Festo" / "Festo_VTUG_Konf.pdf", VTUG)
    client.application.config["ATEX_DOCS"] = str(tmp_path)
    zeilen = [[20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"]]
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "liste.xlsx")})
    bom_id = bom.get_json()["id"]
    client.post(f"/api/boms/{bom_id}/search")
    (f,) = client.get(f"/api/boms/{bom_id}/findings").get_json()[0]["findings"]
    assert f["check_result"] == "ok"
    assert [(b["wert"], b["belegt"], b["seite"], b["tabelle"], b["zeile"]) for b in f["belege"]] == [
        ("typ", True, 1, 1, 2), ("kennzeichnung", True, 1, 1, 2), ("kennzeichnung", True, 1, 1, 3),
        ("zertifikat", True, 1, 1, 2), ("zertifikat", True, 1, 1, 3), ("kategorie", True, 1, 1, 2)]


@pytest.mark.skipif(not (ECHT / "Siemens").is_dir(), reason="echte Ex-Dokumente fehlen")
def test_echte_konformitaetserklaerung_mit_tabellen():
    """Siemens ET 200SP: Kennzeichnung und Zertifikat stehen je Zeile in einer Tabelle."""
    datei = "Siemens/Siemens_ET200SP_Konformit#U00e4tserkl#U00e4rung_de_en.pdf"
    tabellen = pdf_text.tabellen(ECHT / datei, 3)
    zeilen = [z for t in tabellen for z in t.zeilen]
    assert ("1", "DEKRA 20ATEX0002X", "II 3 G Ex ec IIC T4 Gc", "1") in zeilen

    item, f = vorschlag(datei, "ET 200SP", "Zone 2", "II 3 G Ex ec IIC T4 Gc",
                        "DEKRA 20ATEX0002X; DEKRA 21UKEX0009X")
    liste = belege.pruefe(item, f, ECHT)
    assert kurz(liste) == [
        ("typ", "ET 200SP", True, 1, None, None),
        ("kennzeichnung", "II 3 G Ex ec IIC T4 Gc", True, 3, 2, 2),
        ("zertifikat", "DEKRA 20ATEX0002X", True, 3, 2, 2),
        ("zertifikat", "DEKRA 21UKEX0009X", True, 3, 2, 3),
        ("kategorie", "3G", True, 3, 2, 2)]
    assert liste[1]["hinweis"] == ("In derselben Tabellenzeile steht Zertifikatsnummer "
                                   "„DEKRA 20ATEX0002X“.")


@pytest.mark.skipif(not (ECHT / "P+F").is_dir(), reason="echte Ex-Dokumente fehlen")
def test_echter_typ_fehlt_in_der_produkttabelle():
    """P+F KCD2: der Typ der Stückliste steht nicht in der Produkttabelle, nur ein Nachbar."""
    datei = "P+F/P+F_Konformit#U00e4tserkl#U00e4rung_KCD2_de_en.pdf"
    item, f = vorschlag(datei, "KCD2-SR-Ex1.LB", "Schaltschrank außerhalb Ex",
                        "II (1) G; II 3 G", "Baseefa 13 ATEX 0080; CML 19ATEX4410X")
    typ, k1, k2, n1, n2, kategorie = belege.pruefe(item, f, ECHT)
    assert (typ["belegt"], typ["seite"], typ["tabelle"], typ["zeile"]) == (False, 1, 2, 4)
    assert typ["hinweis"].endswith("Im Dokument steht nur „KCD2-SOT-EX1.LB“.")
    # Kennzeichnung und Zertifikat je in einer Zeile der Tabelle „Marking / Certificate“
    assert [(b["angabe"], b["tabelle"], b["zeile"]) for b in (k1, n1, k2, n2)] == [
        ("II (1) G", 3, 3), ("Baseefa 13 ATEX 0080", 3, 3), ("II 3 G", 3, 4),
        ("CML 19ATEX4410X", 3, 4)]
    assert n2["hinweis"] == "In derselben Tabellenzeile steht Kennzeichnung „II 3 G“."
    assert (kategorie["angabe"], kategorie["belegt"]) == ("(1)G, 3G", True)
