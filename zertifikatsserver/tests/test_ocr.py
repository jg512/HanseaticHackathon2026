import hashlib
import io
import json

import pymupdf
import pytest

from app import altbestand, anlegen, ocr
from app.db import db
from app.modelle import Dokument

from conftest import ALT_94, pdf

SCAN = "P+F_Ini_NJ50_PTB00ATEX2032X.pdf"  # zwei Seiten ohne Textebene im kleinen Altbestand


@pytest.fixture
def importiert(app, alt, client):
    altbestand.importiere(alt)
    db.session.commit()
    return client


@pytest.fixture
def tesseract(app, monkeypatch):
    """Tesseract vortäuschen: texte[Seite] ist das Ergebnis der Erkennung."""
    texte = {}
    e = ocr.Einstellung(ocr.cache_ordner(), app.config["OCR_SPRACHEN"], "/tessdata")
    monkeypatch.setattr(ocr, "einstellung", lambda: (e, None))
    monkeypatch.setattr(ocr, "ocr_seite", lambda seite, e: texte.get(seite.number + 1, ""))
    return texte


def scan(client):
    i = next(d["id"] for d in client.get("/api/dokumente").get_json() if d["dateiname"] == SCAN)
    return client.get(f"/api/dokumente/{i}").get_json()


def test_scan_wartet_auf_ocr_und_import_blockiert_nicht(importiert):
    d = scan(importiert)
    assert (d["lesbar"], d["ocr_stand"], d["per_ocr"]) == (False, "offen", False)
    assert [m["regel"] for m in d["markierungen"]] == ["kein_datum"]


def test_ocr_liest_scan_mit_denselben_regeln(importiert, tesseract):
    tesseract[1] = ALT_94
    assert ocr.runde(lambda _: None) == 1
    d = scan(importiert)
    assert (d["lesbar"], d["ocr_stand"], d["per_ocr"], d["ocr_seiten"]) == \
        (True, "fertig", True, [1])
    assert d["ocr_hinweis"] == (
        "Der Text auf Seite 1 stammt aus der Texterkennung eines Scans. Einzelne Werte können "
        "falsch sein, bitte prüfen Sie sie im PDF. Auf Seite 2 wurde kein Text erkannt.")
    assert d["doc_type"] == "eu_konformitaetserklaerung" and d["ausstellungsdatum"] == "2005-05-12"
    assert all(a["quelle"] == "ocr" and "Texterkennung" in a["hinweis"] for a in d["angaben"])
    regeln = {m["regel"]: m for m in d["markierungen"]}
    assert set(regeln) == {"richtlinie_94_9", "norm_ausgabe", "alter"}
    assert all("Texterkennung" in m["grund"] for m in regeln.values())
    assert regeln["richtlinie_94_9"]["grund"].startswith("Die Erklärung nennt nur die alte")
    # Version im Import mit Art „unbekannt“: bekommt die erkannte Art
    assert [(v["doc_type"], v["version"]) for v in d["teile_historie"][0]["historie"]] == \
        [("eu_konformitaetserklaerung", 1)]
    assert any(a["feld"] == "ocr" and a["von"] == ocr.VON for a in d["aenderungen"])
    # Zwischenspeicher je Datei; zweiter Lauf hat nichts mehr zu tun
    assert ocr.aus_cache(d["sha256"]) == {"1": ALT_94, "2": ""}
    assert ocr.runde(lambda _: None) == 0


def test_ocr_ohne_text_bleibt_nicht_lesbar_mit_grund(importiert, tesseract):
    ocr.runde(lambda _: None)
    d = scan(importiert)
    assert (d["lesbar"], d["ocr_stand"]) == (False, "kein_text")
    assert d["ocr_hinweis"] == ("Die Texterkennung hat in diesem Scan keinen Text gefunden. "
                                "Bitte tragen Sie die Angaben von Hand ein.")
    assert d["ocr_hinweis"] in d["markierungen"][0]["grund"]


def test_ocr_behaelt_angaben_von_hand(importiert, tesseract):
    i = scan(importiert)["id"]
    importiert.patch(f"/api/dokumente/{i}", json={"von": "Anna", "ausstellungsdatum": "2024-01-02"})
    tesseract[1] = ALT_94
    ocr.runde(lambda _: None)
    d = scan(importiert)
    assert d["ausstellungsdatum"] == "2024-01-02" and d["doc_type"] == "eu_konformitaetserklaerung"
    assert [a["quelle"] for a in d["angaben"] if a["art"] == "ausstellungsdatum"] == ["manuell"]
    assert "alter" not in {m["regel"] for m in d["markierungen"]}


def test_ocr_aus_oder_gescheitert(importiert, app, monkeypatch, tesseract):
    app.config["OCR"] = False
    monkeypatch.undo()  # echte einstellung(): OCR=0
    meldungen = []
    assert ocr.runde(meldungen.append) == 0
    d = scan(importiert)
    assert (d["ocr_stand"], d["ocr_hinweis"]) == ("offen", ocr.AUS)
    assert len(meldungen) == 1 and ocr.runde(meldungen.append) == 0 and len(meldungen) == 1

    app.config["OCR"] = True
    e = ocr.Einstellung(ocr.cache_ordner(), app.config["OCR_SPRACHEN"], "/tessdata")
    monkeypatch.setattr(ocr, "einstellung", lambda: (e, None))

    def kaputt(seite, e):
        raise RuntimeError("Tesseract abgestürzt")
    monkeypatch.setattr(ocr, "ocr_seite", kaputt)
    ocr.lauf(ausgabe=meldungen.append)  # bricht nicht ab
    d = scan(importiert)
    assert (d["lesbar"], d["ocr_stand"]) == (False, "fehler")
    assert d["ocr_hinweis"] == ocr.GESCHEITERT  # Einzelheiten nur im Log
    assert any("Tesseract abgestürzt" in m for m in meldungen)


def test_hochgeladener_scan_nimmt_vorhandenes_ergebnis(importiert, app):
    teil = importiert.post("/api/teile", json={"hersteller": "Festo", "artikelnr": "VZBA",
                                                "von": "Anna"}).get_json()
    inhalt = pdf("")
    sha = hashlib.sha256(inhalt).hexdigest()
    pfad = ocr.datei(ocr.cache_ordner(), sha, app.config["OCR_SPRACHEN"])
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps({"sha256": sha, "dpi": ocr.DPI, "seiten": {"1": ALT_94}}))
    r = importiert.post(f"/api/teile/{teil['id']}/dokumente", data={
        "datei": (io.BytesIO(inhalt), "scan.pdf"), "von": "Anna"},
        content_type="multipart/form-data")
    assert r.status_code == 201
    d = r.get_json()["dokument"]
    assert (d["lesbar"], d["per_ocr"], d["ocr_stand"], d["doc_type"]) == \
        (True, True, "fertig", "eu_konformitaetserklaerung")


def test_spalten_werden_nachgetragen(importiert, app):
    """Alte Datenbank ohne die neuen Spalten: der Start trägt sie nach, Daten bleiben."""
    anzahl = db.session.scalar(db.select(db.func.count(Dokument.id)))
    db.session.commit()
    with db.engine.begin() as v:
        for tabelle, spalte in (("dokument", "ocr_stand"), ("dokument", "ocr_seiten"),
                                ("dokument", "ocr_hinweis"), ("norm_ausgabe", "quelle"),
                                ("norm_ausgabe", "bestaetigt_von")):
            v.exec_driver_sql(f"ALTER TABLE {tabelle} DROP COLUMN {spalte}")
    db.session.expire_all()
    neu = anlegen()
    assert sorted(neu["spalten"]) == ["dokument.ocr_hinweis", "dokument.ocr_seiten",
                                      "dokument.ocr_stand", "norm_ausgabe.bestaetigt_von",
                                      "norm_ausgabe.quelle"]
    assert db.session.scalar(db.select(db.func.count(Dokument.id))) == anzahl
    # alte Dokumente (Stand leer) mit Seiten ohne Text bearbeitet der OCR-Lauf
    assert [d.dateiname for d in ocr.offene()] == [SCAN]
    assert anlegen()["spalten"] == []


def test_pflichtspalte_wird_nicht_angelegt(app, caplog):
    with db.engine.begin() as v:
        v.exec_driver_sql("ALTER TABLE dokument DROP COLUMN herkunft")
    assert anlegen()["spalten"] == []
    assert "dokument.herkunft fehlt" in caplog.text


def test_echte_texterkennung(app, tmp_path):
    """Nur mit Tesseract im Image (Test-Image zs-test-ocr), sonst übersprungen."""
    tessdata, fehler = ocr.tesseract("eng")
    if fehler:
        pytest.skip(fehler)
    text = pdf("EU Declaration of Conformity\nDirective 2014/34/EU\nDate: 2022-08-09")
    with pymupdf.open(stream=text, filetype="pdf") as quelle:
        bild = quelle[0].get_pixmap(dpi=200)
    scan_pdf = pymupdf.open()
    seite = scan_pdf.new_page()
    seite.insert_image(seite.rect, pixmap=bild)
    e = ocr.Einstellung(tmp_path, "eng", tessdata)
    gelesen = ocr.seiten_lesen(scan_pdf.tobytes(), [1], e)["1"]
    assert "Declaration" in gelesen and "2014/34/EU" in gelesen


def test_neu_auslesen(importiert, tesseract):
    """Nach einer besseren Auslesung im Code: alte Angaben werden neu gelesen, von Hand gesetzte
    bleiben, der OCR-Text kommt aus dem Zwischenspeicher."""
    tesseract[1] = ALT_94
    ocr.runde(lambda _: None)
    i = next(d["id"] for d in importiert.get("/api/dokumente").get_json()
             if d["dateiname"] == "festo_dsm_konf_714757.pdf")
    importiert.patch(f"/api/dokumente/{i}", json={"von": "Anna", "sprachen": ["de", "fr"]})
    dok = db.session.get(Dokument, i)
    dok.angaben[:] = [a for a in dok.angaben if a.art != "norm"]  # wie von einer alten Auslesung
    db.session.commit()
    assert ocr.neu_auslesen(lambda _: None) == 1
    d = importiert.get(f"/api/dokumente/{i}").get_json()
    assert [a["wert"] for a in d["angaben"] if a["art"] == "norm"] == ["60079-0:2009"]
    assert sorted(a["wert"] for a in d["angaben"] if a["art"] == "sprache") == ["de", "fr"]
    assert scan(importiert)["per_ocr"]  # Scan behält den OCR-Text
    assert ocr.neu_auslesen(lambda _: None) == 0
