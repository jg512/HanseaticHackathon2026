"""Andere Dateien als PDF, Quellennachweis je Dokument, Texte für Anwender."""

import hashlib
import io
import zipfile

import pytest

from app import altbestand, anlegen
from app.db import db
from app.modelle import Dokument

from conftest import ALT_94, pdf


def office(art="word"):
    """Kleine Office-Datei (OOXML ist ein ZIP mit word/, xl/ oder ppt/)."""
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(f"{art}/document.xml", "<w:document/>")
    return puffer.getvalue()


def archiv():
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("zeichnung.dxf", "0\nSECTION\n")
    return puffer.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


@pytest.fixture
def teil(client):
    return client.post("/api/teile", json={"hersteller": "Festo", "artikelnr": "DSM-25",
                                           "von": "Anna"}).get_json()


def hochladen(client, teil_id, inhalt, name, **felder):
    daten = {"datei": (io.BytesIO(inhalt), name), "von": "Anna", **felder}
    return client.post(f"/api/teile/{teil_id}/dokumente", data=daten,
                       content_type="multipart/form-data")


def test_word_datei_als_datenblatt_ohne_auslesung(client, teil, app):
    r = hochladen(client, teil["id"], office(), "Datenblatt DSM.docx", doc_type="datenblatt")
    assert r.status_code == 201, r.get_json()
    d = client.get(f"/api/dokumente/{r.get_json()['dokument']['id']}").get_json()
    assert (d["dateiart"], d["familie"], d["auslesbar"]) == ("docx", "office", False)
    assert "Word-Datei" in d["auslesbar_grund"] and "von Hand" in d["auslesbar_grund"]
    assert d["doc_type"] == "datenblatt" and d["doc_type_text"] == "Datenblatt"
    assert d["markierungen"] == [] and d["angaben"][0]["quelle"] == "manuell"
    assert d["dateiname"] == "Datenblatt DSM.docx"
    # in der Ablage mit Endung .docx, nicht .pdf
    assert db.session.get(Dokument, d["id"]).pfad.endswith(".docx")
    # Download: richtiger Typ, nur als Anhang, Browser rät nicht
    r = client.get(d["datei_url"])
    assert r.mimetype.endswith("wordprocessingml.document")
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get(d["pdf_url"]).data == r.data  # alter Link liefert dieselbe Datei


def test_andere_datei_braucht_dokumentart(client, teil, app, tmp_path):
    r = hochladen(client, teil["id"], office(), "x.docx")
    assert r.status_code == 409 and "Dokumentart" in r.get_json()["error"]
    assert client.get("/api/dokumente").get_json() == []
    assert not list((tmp_path / "ablage").rglob("*.docx"))  # Datei wieder weg


def test_mehrere_arten_nebeneinander_aktuell(client, teil):
    anleitung = pdf("Betriebsanleitung\nSchwenkantrieb DSM\nRichtlinie 94/9/EG\nDatum: 12.05.2005")
    erklaerung = pdf(ALT_94)
    assert hochladen(client, teil["id"], anleitung, "anleitung.pdf").status_code == 201
    assert hochladen(client, teil["id"], erklaerung, "ke.pdf").status_code == 201
    assert hochladen(client, teil["id"], office("xl"), "daten.xlsx",
                     doc_type="datenblatt").status_code == 201
    assert hochladen(client, teil["id"], archiv(), "cad.zip", doc_type="zeichnung").status_code \
        == 201
    t = client.get(f"/api/teile/{teil['id']}").get_json()
    assert sorted(v["doc_type"] for v in t["aktuell"]) == [
        "bedienungsanleitung", "datenblatt", "eu_konformitaetserklaerung", "zeichnung"]
    assert all(v["version"] == 1 and v["stand_text"] == "Aktuelle Version" for v in t["aktuell"])
    # Prüfregeln je Dokumentart: die alte Anleitung bekommt keinen 94/9- oder Alters-Verdacht,
    # die Erklärung schon
    docs = {d["doc_type"]: d for d in client.get("/api/dokumente").get_json()}
    assert docs["bedienungsanleitung"]["regeln"] == []
    assert "richtlinie_94_9" in docs["eu_konformitaetserklaerung"]["regeln"]
    # Filter nach Dateiart, Familie, Auslesbarkeit
    assert {d["dateiart"] for d in client.get("/api/dokumente?auslesbar=nein").get_json()} == \
        {"xlsx", "zip"}
    assert len(client.get("/api/dokumente?dateiart=pdf").get_json()) == 2
    assert [d["dateiart"] for d in client.get("/api/dokumente?familie=archiv").get_json()] == ["zip"]


@pytest.mark.parametrize("inhalt,name,teil_text", [
    (b"MZ\x90\x00", "setup.exe", "Endung .exe"),
    (b"<html><script>alert(1)</script></html>", "seite.html", "Endung .html"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "bild.svg", "Endung .svg"),
    (b"%PDF-1.4 kein Word", "falsch.docx", "passt nicht zur Endung .docx"),
    (b"\x00\x01binaer", "notiz.txt", "passt nicht zur Endung .txt"),
    (b"irgendwas", "ohne_endung", "keine Endung"),
    (b"", "leer.pdf", "leer"),
])
def test_nicht_erlaubte_dateien(client, teil, inhalt, name, teil_text):
    r = hochladen(client, teil["id"], inhalt, name, doc_type="sonstiges")
    assert r.status_code == 400 and teil_text in r.get_json()["error"], r.get_json()
    assert client.get("/api/dokumente").get_json() == []


def test_bild_im_browser_zip_als_anhang(client, teil):
    r = hochladen(client, teil["id"], PNG, "Typenschild.png", doc_type="sonstiges")
    d = r.get_json()["dokument"]
    antwort = client.get(d["datei_url"])
    assert antwort.mimetype == "image/png" and "attachment" not in antwort.headers.get(
        "Content-Disposition", "")
    assert client.get(d["datei_url"] + "?download=1").headers["Content-Disposition"] \
        .startswith("attachment")
    z = hochladen(client, teil["id"], archiv(), "paket.zip", doc_type="zeichnung") \
        .get_json()["dokument"]
    antwort = client.get(z["datei_url"])
    assert antwort.mimetype == "application/zip"
    assert antwort.headers["Content-Disposition"].startswith("attachment")


def test_quellennachweis_beim_hochladen_und_aendern(client, teil):
    inhalt = pdf(ALT_94)
    r = hochladen(client, teil["id"], inhalt, "festo_ke.pdf", quelle="atex_agent",
                  quelle_art="internet", quelle_link="https://www.festo.com/media/ke.pdf",
                  quelle_abgerufen_am="2026-11-12T09:30:00", quelle_dateiname="ke.pdf")
    assert r.status_code == 201, r.get_json()
    v = r.get_json()["verknuepfung"]
    assert (v["quelle"], v["quelle_text"]) == ("atex_agent",
                                               "Bei einer Freigabe im ATEX-Agent übernommen")
    d = client.get(f"/api/dokumente/{v['dokument_id']}").get_json()
    q = d["quellennachweis"]
    assert (q["art"], q["art_text"]) == ("internet", "Im Internet gefunden")
    assert q["link"] == "https://www.festo.com/media/ke.pdf" and q["seite_gefunden_auf"] is None
    assert "nicht bekannt" in q["text"]  # Webseite unbekannt: gesagt, nicht verschwiegen
    assert (q["abgerufen_am"], q["uebernommen_von"], q["dateiname"]) == (
        "2026-11-12T09:30:00", "Anna", "ke.pdf")
    assert q["sha256"] == hashlib.sha256(inhalt).hexdigest()
    assert q["archiv_link"] == f"/api/dokumente/{d['id']}/datei"
    assert client.get(f"/api/dateien/{q['sha256']}").data == inhalt
    assert client.get("/api/dateien/123").status_code == 404

    # Mensch trägt die Webseite nach: mit Protokoll
    r = client.patch(f"/api/dokumente/{d['id']}", json={
        "von": "Ben", "grund": "Seite gefunden",
        "quelle_seite": "https://www.festo.com/de/de/a/8000000/"})
    assert r.status_code == 200 and r.get_json()["geaendert"] == ["quelle_seite"]
    d = r.get_json()
    assert d["quellennachweis"]["seite_gefunden_auf"].endswith("/8000000/")
    eintrag = d["aenderungen"][0]
    assert (eintrag["feld_text"], eintrag["von"], eintrag["alt_text"]) == (
        "Gefunden auf Seite", "Ben", "leer")
    # nur Webadressen, nichts zum Ausführen
    r = client.patch(f"/api/dokumente/{d['id']}", json={"von": "Ben",
                                                        "quelle_link": "javascript:alert(1)"})
    assert r.status_code == 400 and "http" in r.get_json()["error"]
    r = hochladen(client, teil["id"], pdf("anderes"), "y.pdf", doc_type="datenblatt",
                  quelle_art="erfunden")
    assert r.status_code == 400


def test_alter_hinweis_des_backends_wird_strukturiert(client, teil):
    """Ein Backend ohne die neuen Felder schickt die Herkunft nur als Satz."""
    r = hochladen(client, teil["id"], pdf(ALT_94), "ke.pdf", quelle="upload",
                  hinweis="Im ATEX-Agent freigegeben von Eva; Quelle: https://x.example/ke.pdf")
    q = client.get(f"/api/dokumente/{r.get_json()['dokument']['id']}").get_json()[
        "quellennachweis"]
    assert (q["art"], q["link"], q["uebernommen_von"]) == ("internet", "https://x.example/ke.pdf",
                                                            "Anna")


def test_neues_backend_strukturiert_hinweis_kurz(client, teil):
    """Mit quelle_art kommt der alte Satz nur noch gekürzt an die Version."""
    r = hochladen(client, teil["id"], pdf(ALT_94), "ke.pdf", quelle="atex_agent",
                  quelle_art="hersteller",
                  hinweis="Im ATEX-Agent freigegeben von Eva; Quelle: vom Hersteller")
    v = r.get_json()["verknuepfung"]
    assert v["hinweis"] == "Im ATEX-Agent freigegeben von Eva."
    assert client.get(f"/api/dokumente/{v['dokument_id']}").get_json()["quellennachweis"][
        "art"] == "hersteller"


def test_gleiche_datei_auf_anderem_weg_eigener_nachweis_der_version(client, teil):
    inhalt = pdf(ALT_94)
    hochladen(client, teil["id"], inhalt, "ke.pdf")
    zweites = client.post("/api/teile", json={"hersteller": "Festo", "artikelnr": "DSM-32",
                                              "von": "Anna"}).get_json()
    r = hochladen(client, zweites["id"], inhalt, "ke_vom_hersteller.pdf",
                  quelle_art="hersteller", quelle_dateiname="ke_vom_hersteller.pdf")
    v = r.get_json()["verknuepfung"]
    assert v["quellennachweis"]["art"] == "hersteller"
    assert v["quellennachweis"]["text"] == "Der Hersteller hat die Datei geschickt."
    d = client.get(f"/api/dokumente/{v['dokument_id']}").get_json()
    assert d["quellennachweis"]["art"] == "hochgeladen"  # der erste Weg bleibt am Dokument


def test_datei_ohne_teil_ablegen(client):
    r = client.post("/api/dokumente", data={
        "datei": (io.BytesIO(office()), "Montage.docx"), "von": "Anna", "hersteller": "Festo",
        "doc_type": "bedienungsanleitung", "quelle_art": "hersteller"},
        content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    assert (d["doc_type"], d["teile"], d["bekannt"]) == ("bedienungsanleitung", [], False)
    assert d["quellennachweis"]["art"] == "hersteller"
    assert d["aenderungen"][0]["feld"] in ("angelegt", "doc_type")
    nochmal = client.post("/api/dokumente", data={
        "datei": (io.BytesIO(office()), "kopie.docx"), "von": "Ben", "hersteller": "Festo"},
        content_type="multipart/form-data")
    assert nochmal.status_code == 200 and nochmal.get_json()["bekannt"] is True
    assert client.post("/api/dokumente", data={"datei": (io.BytesIO(office()), "x.docx"),
                                               "von": "Ben"},
                       content_type="multipart/form-data").status_code == 400


def test_bestand_mit_pfad_und_anderen_dateien(app, alt, client):
    (alt / "Festo" / "Masszeichnung DSM.dxf").write_bytes(b"0\nSECTION\n2\nHEADER\n")
    (alt / "Festo" / "virus.exe").write_bytes(b"MZ")
    lauf = altbestand.importiere(alt)
    db.session.commit()
    assert lauf.zahlen["andere_dateien"] == 1 and lauf.zahlen["abgelehnt"] == 1
    assert any("virus.exe wurde nicht übernommen" in h for h in lauf.hinweise)
    docs = {d["dateiname"]: d for d in client.get("/api/dokumente").get_json()}
    q = client.get(f"/api/dokumente/{docs['festo_dsm_konf_714757.pdf']['id']}").get_json()[
        "quellennachweis"]
    assert (q["art"], q["art_text"], q["link"], q["abgerufen_am"]) == (
        "altbestand", "Übernommener Bestand", None, None)
    assert q["dateiname"] in ("Festo/festo_dsm_konf_714757.pdf", "Festo/festo_kopie_dsm.pdf")
    zeichnung = docs["Masszeichnung DSM.dxf"]
    assert (zeichnung["dateiart"], zeichnung["auslesbar"], zeichnung["regeln"]) == (
        "dxf", False, [])
    assert {d["quelle_art"] for d in client.get(
        "/api/dokumente?quelle_art=altbestand").get_json()} == {"altbestand"}
    # „#U00e4“ im Ordner: der Satz nennt den Pfad lesbar, gespeichert bleibt er wie im Ordner
    konf = docs["Festo_Schwenkantrieb DSM_Konformitätserklärung.pdf"]
    q = client.get(f"/api/dokumente/{konf['id']}").get_json()["quellennachweis"]
    assert q["dateiname"] == "Festo/Festo_Schwenkantrieb DSM_Konformit#U00e4tserkl#U00e4rung.pdf"
    assert q["text"] == ("Die Datei stammt aus dem übernommenen Bestand, dort lag sie unter "
                         "Festo/Festo_Schwenkantrieb DSM_Konformitätserklärung.pdf.")


def test_alte_datenbank_bekommt_nachweis_aus_vorhandenem(app, client, teil):
    r = hochladen(client, teil["id"], pdf(ALT_94), "ke.pdf",
                  hinweis="Im ATEX-Agent freigegeben von Eva; Quelle: manuell hochgeladen")
    dok = db.session.get(Dokument, r.get_json()["dokument"]["id"])
    for spalte in ("quelle_art", "quelle_link", "quelle_von", "quelle_dateiname"):
        setattr(dok, spalte, None)  # wie vor dem Quellennachweis
    db.session.commit()
    assert anlegen()["quellen"] == 1
    d = client.get(f"/api/dokumente/{dok.id}").get_json()
    assert d["quellennachweis"]["art"] == "atex_agent"
    assert d["aenderungen"][0]["feld_text"] == "Herkunft"


def test_begriffe_und_texte(client, teil):
    b = client.get("/api/begriffe").get_json()
    assert b["dienst"] == "Dokumentenarchiv"
    assert b["markierungsstand"]["verdacht"] == "Möglicherweise veraltet"
    assert b["markierungsstand"]["bestaetigt"] == "Veraltet, Ersatz wird gesucht"
    assert b["markierungsstand"]["verworfen"] == "Geprüft, gilt weiter"
    assert "Bedienungsanleitung" not in b["regeln_gilt_fuer"]["richtlinie_94_9"]
    assert "Bedienungsanleitung" in b["regeln_gilt_fuer"]["gueltig_bis"]
    assert any(f["endung"] == "docx" for f in b["dateiformate"])
    r = client.get("/api/regeln").get_json()
    assert "bedienungsanleitung" not in r["gilt_fuer"]["norm_ausgabe"]
    # Protokoll in Worten: Dokumentart als Name, nicht als Kennung
    d = hochladen(client, teil["id"], pdf("Datenblatt"), "db.pdf").get_json()["dokument"]
    r = client.patch(f"/api/dokumente/{d['id']}", json={"von": "Anna", "doc_type": "datenblatt"})
    e = r.get_json()["aenderungen"][0]
    assert (e["feld_text"], e["alt_text"], e["neu_text"]) == (
        "Dokumentart", "Sonstiges Dokument", "Datenblatt")
    # Fehler ohne Namen in einem Satz für Anwender
    r = client.patch(f"/api/dokumente/{d['id']}", json={"doc_type": "datenblatt"})
    assert r.status_code == 400 and "Namen" in r.get_json()["error"]
    # Markierungen mit Text zum Stand
    m = client.post("/api/markierungen", json={"dokument_ids": [d["id"]], "von": "Anna",
                                               "grund": "neue Ausgabe"}).get_json()
    assert m["markierungen"][0]["status_text"] == "Möglicherweise veraltet"
    assert m["markierungen"][0]["regel_titel"] == "Von Hand als veraltet gemeldet"
