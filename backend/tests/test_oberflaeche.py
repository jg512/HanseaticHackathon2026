"""HTML-Oberfläche: Vertrag nach dem Entwurf aus dem Team auf unserem Datenmodell (routes/oberflaeche.py)."""

import io
import smtplib
import time

import pymupdf
import pytest
from conftest import kontakt, xlsx
from openpyxl import load_workbook

from app import create_app
from app.db import db
from app.models import Bom, BomItem, EmailDraft, Finding
from app.models.bom import PurchasingEmail
from app.services import oberflaeche

VTUG = ("EU-Baumusterprüfbescheinigung\nNummer: IBExU16ATEXB021 X\nProdukt: Ventilinsel\n"
        "Typ: VTUG-10-VI-EX2E und VTUG-14-VI-EX2E\nDie Kennzeichnung des Gerätes muss die "
        "folgenden Angaben enthalten:\nII 3G Ex ec IIC T4 Gc\nDie Ventilinseln werden mit der "
        "Steuerung verbunden und sind für den Einsatz in der Zone 2 bestimmt.")
VACF = ("EU-Konformitätserklärung\nMagnetspule VACF-B-K1-1-5\nII 2G Ex mb IIC T5 Gb\n"
        "Baumusterprüfbescheinigung IBExU16ATEX1146 X\nDer Hersteller erklärt, dass das Produkt "
        "mit den Anforderungen der Richtlinie übereinstimmt und für die Zone bestimmt ist.")

ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1-5", 4, "Zone 1"],
          [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
          [30, 99000030, "Schalter", "Rittal", "SZ-123", 1, "Zone 1"],
          [40, 99000040, "Klemme", "Festo", None, 1, "Zone 1"],
          [50, 99000050, "Linearführung", "Bosch", "LF-1", 1, "außerhalb Ex"]]


def pdf(pfad, *seiten):
    doc = pymupdf.open()
    for text in seiten:
        doc.new_page().insert_text((40, 60), text, fontsize=8)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    doc.save(pfad)
    return pfad


class FalscherSmtp:
    """Merkt sich die Nachrichten statt sie zu senden."""
    gesendet = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def send_message(self, nachricht):
        self.gesendet.append(nachricht)


@pytest.fixture(autouse=True)
def leer():
    """Protokoll und Lauf liegen im Prozess; jeder Test beginnt ohne."""
    oberflaeche._lauf.clear()
    yield


@pytest.fixture
def ordner(client, tmp_path):
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf", VTUG)
    pdf(tmp_path / "Festo" / "Festo_Konformit#U00e4tserkl#U00e4rung_VACF-B-K1-1-5.pdf", VACF)
    (tmp_path / "Bosch").mkdir()
    client.application.config.update(ATEX_DOCS=str(tmp_path), OBERFLAECHE_SUCHE_SYNCHRON=True)
    return tmp_path


@pytest.fixture
def smtp(monkeypatch):
    FalscherSmtp.gesendet = []
    monkeypatch.setattr(smtplib, "SMTP", FalscherSmtp)
    return FalscherSmtp


def hochladen(client, zeilen=ZEILEN, name="Abfüllanlage Test"):
    r = client.post("/api/vorgaenge", data={"datei": (io.BytesIO(xlsx(zeilen)), "liste.xlsx"),
                                            "name": name})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["id"]


def vorgang(client, vid):
    r = client.get(f"/api/vorgaenge/{vid}")
    assert r.status_code == 200
    return r.get_json()


def art(client, vid, pos):
    return next(a for a in vorgang(client, vid)["artikel"] if a["pos"] == str(pos))


def artikel(client, vid, pos):
    return client.get(f"/api/artikel/{art(client, vid, pos)['id']}").get_json()


def gestartet(client, vid):
    r = client.post(f"/api/vorgaenge/{vid}/start")
    assert r.status_code == 200, r.get_json()
    return vorgang(client, vid)


# Vorgänge


def test_hochladen_liste_und_detail(client, ordner):
    vid = hochladen(client)
    v = vorgang(client, vid)
    assert v["name"] == "Abfüllanlage Test" and v["anzahl"] == 5
    assert v["schritt"] == "maengel"  # Pos. 40 ohne Artikelnummer, Pos. 50 Ex-Frage offen
    assert {a["pos"]: a["status"] for a in v["artikel"]} == {
        "10": "wartet", "20": "wartet", "30": "wartet", "40": "info_fehlt", "50": "info_fehlt"}
    assert v["zaehler"] == {"wartet": 3, "info_fehlt": 2}
    assert not v["vollstaendig"] and v["offen"] == 5
    a = v["artikel"][0]
    assert set(a) == {"id", "pos", "profile_no", "bezeichnung", "hersteller", "artikelnr", "menge",
                      "einbauort", "status", "suche_in", "mail_id", "anzahl_funde", "freigegeben",
                      "automatisch", "deutsch_fehlt", "kontakt_fehlt"}
    assert (v["deutsch_fehlt"], v["kontakte_fehlen"]) == (0, 0)
    assert (a["bezeichnung"], a["hersteller"], a["artikelnr"]) == ("Magnetspule", "Festo",
                                                                    "VACF-B-K1-1-5")
    [kurz] = client.get("/api/vorgaenge").get_json()
    assert kurz["id"] == vid and "artikel" not in kurz and kurz["erstellt"]
    [e] = client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()
    assert e["n"] == 1 and e["text"] == ("Die Stückliste mit 5 Positionen ist eingelesen. Bei 2 "
                                         "Positionen fehlt eine Angabe oder ist unklar.")


def test_name_ist_immer_eindeutig(client, ordner):
    # ohne Angabe: Maschine mit der Nummer des Vorgangs
    erster, zweiter = hochladen(client, name=""), hochladen(client, name="")
    assert vorgang(client, erster)["name"] == f"Abfüllanlage TM-1 · Nr. {erster}"
    assert vorgang(client, zweiter)["name"] == f"Abfüllanlage TM-1 · Nr. {zweiter}"
    # derselbe Wunschname zweimal: der zweite bekommt eine Nummer, Groß/Klein zählt nicht
    a, b, c = (hochladen(client, name=n) for n in ("Auftrag 7", "auftrag 7", "Auftrag 7"))
    assert [vorgang(client, v)["name"] for v in (a, b, c)] == ["Auftrag 7", "auftrag 7 (2)",
                                                               "Auftrag 7 (3)"]
    namen = [v["name"] for v in client.get("/api/vorgaenge").get_json()]
    assert len(namen) == len(set(n.casefold() for n in namen)) == 5


KEIN_PDF = "Das ist keine PDF-Datei. Bitte wählen Sie das Dokument als PDF aus."


@pytest.mark.parametrize("daten, text", [
    ({}, "keine Datei mitgeschickt"),
    ({"datei": (io.BytesIO(b"x"), "liste.csv")}, "Excel-Datei"),
    ({"datei": (io.BytesIO(b"kaputt"), "liste.xlsx")}, "ließ sich nicht einlesen"),
])
def test_hochladen_fehler(client, daten, text):
    r = client.post("/api/vorgaenge", data=daten)
    assert r.status_code == 400 and text in r.get_json()["fehler"]


@pytest.mark.parametrize("methode, pfad", [
    ("get", "/api/vorgaenge/99"), ("post", "/api/vorgaenge/99/start"),
    ("get", "/api/vorgaenge/99/ereignisse"), ("get", "/api/vorgaenge/99/mails"),
    ("get", "/api/vorgaenge/99/export"), ("post", "/api/vorgaenge/99/maengel/senden"),
    ("get", "/api/artikel/99"), ("post", "/api/artikel/99/freigeben"),
    ("post", "/api/mails/99/senden"), ("post", "/api/mails/99/erinnern"),
    ("get", "/files/gibt/es/nicht.pdf"),
])
def test_unbekannt_ist_404_mit_fehler(client, methode, pfad):
    r = getattr(client, methode)(pfad, json={})
    weg = "Diesen Eintrag gibt es nicht mehr. Bitte laden Sie die Seite neu."
    assert r.status_code == 404 and r.get_json() == {"fehler": weg} | (
        {"fehler": "Diese Datei liegt nicht im Ordner der Ex-Dokumente."}
        if pfad.startswith("/files") else {})


# Suche und Protokoll


def test_start_sucht_und_protokolliert(client, ordner):
    vid = hochladen(client)
    v = gestartet(client, vid)
    stati = {a["pos"]: a["status"] for a in v["artikel"]}
    assert stati == {"10": "zu_pruefen", "20": "zu_pruefen", "30": "mail_entwurf",
                     "40": "info_fehlt", "50": "info_fehlt"}
    assert v["schritt"] == "pruefung"
    # nichts gefunden: der Entwurf an den Hersteller liegt schon da
    a30 = art(client, vid, 30)
    assert a30["mail_id"] and client.get(f"/api/vorgaenge/{vid}/mails").get_json()[0]["status"] \
        == "entwurf"

    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    assert texte[1].startswith("Der Agent startet mit 5 Positionen.")
    assert "Position 10: Der Agent sucht im Ordner „Festo“ des Herstellers." in texte
    assert ("Position 10: Ein Fund wartet auf Ihre Prüfung. Gesucht hat der Agent in der "
            "Datenbank und im Ordner des Herstellers.") in texte
    assert any(t.startswith("Position 30: Der Agent hat nichts gefunden.")
               and t.endswith("Ein E-Mail-Entwurf an den Hersteller liegt bereit.") for t in texte)
    assert texte[-1].startswith("Der Agent ist fertig. Von den 5 Positionen sind ")
    # ganze Sätze: keine Semikolon-Ketten, keine Rohwerte des Stands
    assert not any(";" in t or "zu_pruefen" in t or "Pos. " in t for t in texte)
    neu = client.get(f"/api/vorgaenge/{vid}/ereignisse?seit={len(texte) - 1}").get_json()
    assert [e["n"] for e in neu] == [len(texte)]
    assert all(e["artikel_id"] for e in client.get(
        f"/api/vorgaenge/{vid}/ereignisse").get_json() if e["text"].startswith("Position "))


def test_fund_mit_feldern_belegen_und_vorschau(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    assert a["status"] == "zu_pruefen" and a["anzahl_funde"] == 1 and a["freigabe"] is None
    [f] = a["funde"]
    assert f["quelle"] == "Ordner des Herstellers" and f["seite"] == 1
    # lesbar zum Anzeigen; Pfad und Link behalten den Namen auf der Platte
    assert f["datei"] == "Festo_Konformitätserklärung_VACF-B-K1-1-5.pdf"
    assert f["pfad"] == "Festo/Festo_Konformit#U00e4tserkl#U00e4rung_VACF-B-K1-1-5.pdf"
    assert f["url"] == f"/files/Festo/Festo_Konformit%23U00e4tserkl%23U00e4rung_VACF-B-K1-1-5.pdf" \
                       f"?markiert={f['id']}"
    felder = f["felder"]
    grund = ["Dokumenttyp", "Typ", "Kennzeichnung", "Zertifikatsnummer", "Kategorie", "Sprachen"]
    assert [k for k in felder if k in grund] == grund
    # dazu die Angaben aus dem Dokument (services/angaben.py, test_angaben.py)
    assert list(felder)[-1] == "Sprachen" and "Zündschutzart" in felder
    assert felder["Kennzeichnung"]["beleg"]["text"] == "II 2G Ex mb IIC T5 Gb"
    assert felder["Dokumenttyp"] == {"wert": "EU-Konformitätserklärung", "beleg": None,
                                     "abgeleitet": True}
    assert felder["Typ"]["wert"] == "VACF-B-K1-1-5"
    typ = felder["Typ"]["beleg"]
    assert typ["seite"] == 1 and "VACF-B-K1-1-5" in typ["text"]
    assert felder["Kennzeichnung"]["wert"] == "II 2G Ex mb IIC T5 Gb"
    assert felder["Kennzeichnung"]["beleg"]["seite"] == 1
    assert felder["Zertifikatsnummer"]["wert"] == "IBExU16ATEX1146 X"
    # vom Agenten erkannt, nie wörtlich belegt: die Oberfläche zeigt es neutral, nicht rot
    assert felder["Sprachen"]["beleg"] is None and felder["Sprachen"]["abgeleitet"] is True
    abgeleitet = [k for k, x in felder.items() if x.get("abgeleitet")]
    assert abgeleitet == ["Dokumenttyp", "Sprachen"]

    r = client.get(f["url"])  # markierte PDF wie im iframe der Oberfläche
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    with pymupdf.open(stream=r.data) as doc:
        assert list(doc[0].annots())
    r = client.get(f["url"].split("?")[0])
    assert r.status_code == 200 and r.data == (ordner / f["pfad"]).read_bytes()
    # gespeichert wird die Datei unter dem lesbaren Namen
    assert "Konformit%C3%A4tserkl%C3%A4rung" in r.headers["Content-Disposition"]
    r = client.get(f"/api/findings/{f['id']}/file")
    assert r.status_code == 200 and "Konformit%C3%A4tserkl%C3%A4rung" in r.headers["Content-Disposition"]


def test_dateinamen_lesbar_auch_in_alten_saetzen(client, ordner):
    """„#U00e4“ aus den gelieferten Dateinamen: im Protokoll und in der Vorprüfung lesbar, auch
    in Sätzen, die schon so gespeichert sind; die Datenbank bleibt, wie sie ist."""
    from app.models.bom import BomEvent
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    alt = "Position 10: Jan hat „Festo_Konformit#U00e4tserkl#U00e4rung_VACF-B-K1-1-5.pdf“ freigegeben."
    db.session.add(BomEvent(bom_id=vid, bom_item_id=a["id"], text=alt))
    finding = db.session.get(Finding, a["funde"][0]["id"])
    finding.check_message += " Dieselbe Datei liegt auch unter Erkl#U00e4rung.pdf."
    db.session.commit()

    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    assert ("Position 10: Jan hat „Festo_Konformitätserklärung_VACF-B-K1-1-5.pdf“ freigegeben."
            in texte)
    assert not any("#U" in t for t in texte)
    assert artikel(client, vid, 10)["funde"][0]["pruefung"]["meldung"].endswith(
        "Dieselbe Datei liegt auch unter Erklärung.pdf.")
    assert db.session.scalar(db.select(BomEvent.text).filter_by(text=alt)) == alt


def test_lesbar():
    from app.services.pdf_text import lesbar
    assert lesbar("Konformit#U00e4tserkl#U00e4rung f#U00FCr Zone 2.pdf") == \
        "Konformitätserklärung für Zone 2.pdf"
    assert lesbar("Smiley #L0001f600") == "Smiley \U0001f600"
    # kein Zeichen oder kein Code: bleibt stehen
    assert lesbar("#Ud800 #U12 Nr. #1 #Uxyz1") == "#Ud800 #U12 Nr. #1 #Uxyz1"
    assert lesbar(None) is None and lesbar("") == ""


def test_files_bleibt_im_ordner(client, ordner, tmp_path):
    (tmp_path.parent / "geheim.pdf").write_bytes(b"%PDF-1.4")
    for pfad in ("/files/../geheim.pdf", "/files/%2E%2E/geheim.pdf", "/files/Festo"):
        assert client.get(pfad).status_code == 404


def test_start_waehrend_der_suche_ist_409_und_zeigt_suche(client, ordner):
    vid = hochladen(client)
    aid = art(client, vid, 20)["id"]
    oberflaeche._lauf[vid] = {"item_id": aid, "suche_in": "Ordner des Herstellers"}
    r = client.post(f"/api/vorgaenge/{vid}/start")
    assert r.status_code == 409 and r.get_json() == {
        "fehler": "Der Agent sucht in diesem Vorgang schon. Bitte warten Sie, bis er fertig ist."}
    v = vorgang(client, vid)
    a = next(a for a in v["artikel"] if a["id"] == aid)
    assert (a["status"], a["suche_in"]) == ("suche", "Ordner des Herstellers")
    assert v["schritt"] == "suche" and v["zaehler"]["suche"] == 1
    assert client.get(f"/api/artikel/{aid}").get_json()["status"] == "suche"


def test_start_ohne_ordner_ist_503(client, ordner, tmp_path):
    vid = hochladen(client)
    client.application.config["ATEX_DOCS"] = str(tmp_path / "fehlt")
    r = client.post(f"/api/vorgaenge/{vid}/start")
    assert r.status_code == 503 and r.get_json()["fehler"].startswith(
        "Der Ordner mit den Ex-Dokumenten ist gerade nicht erreichbar")
    assert str(tmp_path) not in r.get_json()["fehler"]  # kein Pfad für den Menschen
    assert not oberflaeche.laeuft(vid)


def test_suche_im_hintergrund(tmp_path):
    """Echter Thread mit eigener Sitzung, Datenbank als Datei wie im Betrieb."""
    pdf(tmp_path / "docs" / "Festo" / "Festo_VACF.pdf", VACF)
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x.db'}",
                      "TESTING": True, "INTERNET_VERSUCHE_PRO_TEIL": 0,
                      "ATEX_DOCS": str(tmp_path / "docs")})
    with app.app_context():
        db.create_all()
    client = app.test_client()
    vid = hochladen(client, ZEILEN[:1])
    assert client.post(f"/api/vorgaenge/{vid}/start").status_code == 200
    ende = time.monotonic() + 30
    while oberflaeche.laeuft(vid) and time.monotonic() < ende:
        time.sleep(0.05)
    assert not oberflaeche.laeuft(vid)
    assert art(client, vid, 10)["status"] == "zu_pruefen"
    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    assert texte[-1] == ("Der Agent ist fertig. Die einzige Position ist mit Fund zum Prüfen. "
                         "Bitte prüfen Sie jetzt die Funde und geben Sie sie frei.")
    with app.app_context():
        db.engine.dispose()


# Entscheidungen


def test_freigeben_geprueft_und_in_db_im_naechsten_vorgang(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    r = client.post(f"/api/artikel/{a['id']}/freigeben",
                    json={"fund_id": a["funde"][0]["id"], "von": "Jan"})
    assert r.status_code == 200
    a = artikel(client, vid, 10)
    assert a["status"] == "geprueft" and a["freigegeben"]
    assert a["freigabe"]["fund_id"] == a["funde"][0]["id"] and a["freigabe"]["von"] == "Jan"
    assert db.session.get(Finding, a["funde"][0]["id"]).decided_by == "Jan"

    zweiter = hochladen(client, ZEILEN[:1], name="Zweiter")
    b = artikel(client, zweiter, 10)
    assert b["status"] == "in_db" and b["freigabe"]["von"] == "Jan"
    assert [f["quelle"] for f in b["funde"]] == ["Datenbank"]
    assert b["funde"][0]["felder"]["Typ"]["beleg"]  # Belege des freigegebenen Vorschlags
    v = gestartet(client, zweiter)
    assert v["vollstaendig"] and v["zaehler"] == {"in_db": 1}
    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{zweiter}/ereignisse").get_json()]
    assert ("Position 10: Die Dokumente liegen schon geprüft in der Datenbank. Der Agent musste "
            "nicht suchen.") in texte


def test_freigeben_ohne_namen_und_fehler(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 20)
    assert client.post(f"/api/artikel/{a['id']}/freigeben", json={}).status_code == 400
    r = client.post(f"/api/artikel/{a['id']}/freigeben", json={"fund_id": 12345})
    assert r.status_code == 400 and r.get_json() == {
        "fehler": "Diesen Fund gibt es bei der Position nicht mehr. Bitte laden Sie die Seite neu."}
    fid = a["funde"][0]["id"]
    assert client.post(f"/api/artikel/{a['id']}/freigeben", json={"fund_id": fid},
                       headers={"X-Anwender": "Kopf"}).status_code == 200
    assert db.session.get(Finding, fid).decided_by == "Kopf"
    # nur eine Baumusterprüfbescheinigung: die Konformitätserklärung fehlt noch (M20)
    a = artikel(client, vid, 20)
    assert a["status"] == "mail_entwurf" and a["freigegeben"]
    assert a["freigabe"]["von"] == "Kopf" and "Es fehlt noch die EU-Konformitätserklärung." in a["notiz"]
    assert "Baumusterprüfbescheinigung" not in a["mail"]["text"].split("folgende Dokumente")[1]
    r = client.post(f"/api/artikel/{a['id']}/freigeben", json={"fund_id": fid})
    assert r.status_code == 409 and "schon freigegeben" in r.get_json()["fehler"]


def test_ablehnen_gibt_entwurf_mit_grund(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 20)
    r = client.post(f"/api/artikel/{a['id']}/ablehnen",
                    json={"grund": "Zertifikat gilt nicht für diesen Typ"})
    assert r.status_code == 200
    a = artikel(client, vid, 20)
    assert a["status"] == "mail_entwurf" and a["funde"] == [] and a["mail"]["status"] == "entwurf"
    assert a["mail"]["grund"] == "Zertifikat gilt nicht für diesen Typ"
    assert ("Grund der Anfrage: Zertifikat gilt nicht für diesen Typ\n\nBauteil:"
            in a["mail"]["text"])
    # Festo hat noch keine Adresse: der Grund bleibt, der Hinweis steht dahinter
    assert a["notiz"].startswith("Zertifikat gilt nicht für diesen Typ. Für den Hersteller Festo "
                                 "ist noch keine E-Mail-Adresse hinterlegt.")
    assert a["kontakt_fehlt"] and a["mail"]["kontakt_fehlt"]
    f = db.session.scalar(db.select(Finding).filter_by(bom_item_id=a["id"]))
    assert (f.status, f.decided_by) == ("abgelehnt", "Oberfläche")
    r = client.post(f"/api/artikel/{a['id']}/ablehnen", json={"grund": "x"})
    assert r.status_code == 409


def test_korrigieren(client, ordner):
    vid = hochladen(client)
    a = art(client, vid, 40)
    assert a["status"] == "info_fehlt"
    r = client.post(f"/api/artikel/{a['id']}/korrigieren",
                    json={"artikelnr": "VACF-B-K1-1-5", "hersteller": "", "von": "Jan"})
    assert r.status_code == 200
    a = artikel(client, vid, 40)
    assert a["status"] == "wartet" and a["artikelnr"] == "VACF-B-K1-1-5"
    assert db.session.get(BomItem, a["id"]).corrections[0].changed_by == "Jan"
    r = client.post(f"/api/artikel/{a['id']}/korrigieren", json={"artikelnr": "VACF-B-K1-1-5"})
    assert r.status_code == 400 and "Es gab nichts zu ändern" in r.get_json()["fehler"]
    # die Bezeichnung ist korrigierbar und stößt keine neue Suche an
    gestartet(client, vid)
    funde = len(artikel(client, vid, 10)["funde"])
    a10 = artikel(client, vid, 10)
    r = client.post(f"/api/artikel/{a10['id']}/korrigieren", json={"bezeichnung": "Neu", "von": "Jan"})
    assert r.status_code == 200
    nachher = artikel(client, vid, 10)
    assert nachher["bezeichnung"] == "Neu" and len(nachher["funde"]) == funde > 0
    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    assert ("Position 40: Jan hat Hersteller-Artikelnr. geändert. Die Position steht jetzt auf "
            "„wartet“.") in texte


def test_kein_ex_und_erledigt(client, ordner):
    vid = hochladen(client)
    a50, a30 = art(client, vid, 50), art(client, vid, 30)
    client.post(f"/api/boms/{vid}/items/{a50['id']}/ex-teil", json={"ex_teil": False, "by": "Jan"})
    assert art(client, vid, 50)["status"] == "kein_ex"
    client.post(f"/api/boms/{vid}/items/{a30['id']}/resolve",
                json={"by": "Jan", "note": "Kunde verzichtet"})
    a = artikel(client, vid, 30)
    assert a["status"] == "geprueft" and a["freigabe"]["von"] == "Jan"
    assert "Kunde verzichtet" in a["notiz"]


def test_zertifikat_einfuegen(client, ordner):
    vid = hochladen(client)
    a = art(client, vid, 30)
    inhalt = (ordner / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf").read_bytes()
    r = client.post(f"/api/artikel/{a['id']}/zertifikat",
                    data={"datei": (io.BytesIO(inhalt), "antwort.pdf"), "herkunft": "hersteller"})
    assert r.status_code == 200, r.get_json()
    a = artikel(client, vid, 30)
    assert a["status"] == "zu_pruefen" and a["funde"][0]["quelle"] == "Antwort des Herstellers"
    assert a["funde"][0]["pfad"] == "_upload/Rittal/antwort.pdf"
    assert client.get(a["funde"][0]["url"]).status_code == 200

    b = art(client, vid, 20)  # manuell ohne Dokumentart: in die Prüfung, nicht gleich frei
    r = client.post(f"/api/artikel/{b['id']}/zertifikat",
                    data={"datei": (io.BytesIO(inhalt), "zert.pdf"), "herkunft": "manuell"})
    b = artikel(client, vid, 20)
    assert b["status"] == "zu_pruefen" and b["funde"][0]["quelle"] == "von Hand eingefügt"

    c = art(client, vid, 10)  # manuell mit Dokumentart: freigegeben (M18)
    vacf = (ordner / "Festo" / "Festo_Konformit#U00e4tserkl#U00e4rung_VACF-B-K1-1-5.pdf")
    r = client.post(f"/api/artikel/{c['id']}/zertifikat",
                    data={"datei": (io.BytesIO(vacf.read_bytes()), "k.pdf"), "herkunft": "manuell",
                          "doc_type": "eu_konformitaetserklaerung", "von": "Jan"})
    assert r.status_code == 200
    c = artikel(client, vid, 10)
    assert c["status"] == "geprueft" and c["freigabe"]["von"] == "Jan"

    r = client.post(f"/api/artikel/{c['id']}/zertifikat",
                    data={"datei": (io.BytesIO(b"x"), "k.txt")})
    assert r.status_code == 400 and r.get_json() == {"fehler": KEIN_PDF}
    r = client.post(f"/api/artikel/{a['id']}/zertifikat",
                    data={"datei": (io.BytesIO(b"kein pdf"), "k.pdf"), "herkunft": "hersteller"})
    assert r.status_code == 400 and r.get_json() == {"fehler": KEIN_PDF}


# E-Mails


def test_mail_bearbeiten_senden_erinnern_eskalieren(client, ordner, smtp):
    vid = hochladen(client)
    gestartet(client, vid)
    [m] = client.get(f"/api/vorgaenge/{vid}/mails").get_json()
    assert set(m) >= {"id", "vorgang_id", "artikel_id", "status", "an", "betreff", "text",
                      "grund", "gesendet", "erinnert"}
    assert m["status"] == "entwurf" and m["an"] == "test@example.com"
    assert m["grund"] == "Der Agent hat kein passendes Dokument gefunden."
    # Rittal hat noch keine Adresse: markiert, Senden wird abgelehnt
    assert (m["empfaenger_hersteller"], m["kontakt_fehlt"], m["versand_an"]) == (None, True, None)
    assert vorgang(client, vid)["kontakte_fehlen"] == 1
    r = client.post(f"/api/mails/{m['id']}/senden", json={"von": "Jan"})
    assert r.status_code == 409 and "Herstellerkontakten" in r.get_json()["fehler"]
    assert smtp.gesendet == [] and db.session.get(EmailDraft, m["id"]).status == "entwurf"
    r = client.post("/api/kontakte", json={"hersteller": "Rittal", "email": "ex@rittal.example",
                                           "by": "Jan"})
    assert r.status_code == 201 and r.get_json()["entwuerfe_aktualisiert"] == 1
    [m] = client.get(f"/api/vorgaenge/{vid}/mails").get_json()
    assert (m["empfaenger_hersteller"], m["kontakt_fehlt"], m["versand_an"]) == (
        "ex@rittal.example", False, "test@example.com")
    assert vorgang(client, vid)["kontakte_fehlen"] == 0

    r = client.post(f"/api/mails/{m['id']}/erinnern")
    assert r.status_code == 409  # erst senden
    r = client.put(f"/api/mails/{m['id']}", json={"an": "info@rittal.example", "betreff": "x",
                                                   "text": "y"})
    assert r.status_code == 400 and "Herstellerkontakten" in r.get_json()["fehler"]
    r = client.put(f"/api/mails/{m['id']}", json={"an": m["an"], "betreff": "Anfrage SZ-123",
                                                   "text": "Bitte senden."})
    assert r.status_code == 200 and r.get_json()["betreff"] == "Anfrage SZ-123"

    r = client.post(f"/api/mails/{m['id']}/senden", json={"von": "Jan"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "gesendet" and r.get_json()["gesendet"]
    assert len(smtp.gesendet) == 1 and smtp.gesendet[0]["To"] == "test@example.com"
    assert db.session.get(EmailDraft, m["id"]).approved_by == "Jan"
    assert art(client, vid, 30)["status"] == "angefordert"
    assert client.put(f"/api/mails/{m['id']}", json={"text": "z"}).status_code == 409
    assert client.post(f"/api/mails/{m['id']}/senden").status_code == 409

    r = client.post(f"/api/mails/{m['id']}/erinnern")  # Demo: 48 h vorbei
    assert r.status_code == 200
    assert r.get_json()["status"] == "erinnert" and r.get_json()["erinnert"]
    assert len(smtp.gesendet) == 2 and smtp.gesendet[1]["Subject"].startswith("Erinnerung:")
    assert art(client, vid, 30)["status"] == "erinnert"
    assert len(client.get(f"/api/vorgaenge/{vid}/mails").get_json()) == 1  # zusammengefasst

    r = client.post(f"/api/mails/{m['id']}/erinnern", json={"von": "Jan"})
    assert r.status_code == 200 and r.get_json()["status"] == "eskaliert"
    assert art(client, vid, 30)["status"] == "eskaliert"
    assert client.post(f"/api/mails/{m['id']}/erinnern").status_code == 409
    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    assert ("Position 30: Der Hersteller hat auch auf die Erinnerung nicht geantwortet. Jan hat "
            "die Anfrage an den Einkauf eskaliert.") in texte
    assert ("Position 30: Jan hat die E-Mail an den Hersteller freigegeben und gesendet. Gedacht "
            "ist sie für ex@rittal.example, in dieser Umgebung ging sie an das Test-Postfach "
            "test@example.com.") in texte


def test_erinnern_ohne_demo_wartet_die_frist_ab(client, ordner, smtp):
    client.application.config["OBERFLAECHE_DEMO_48H"] = False
    vid = hochladen(client)
    gestartet(client, vid)
    [m] = client.get(f"/api/vorgaenge/{vid}/mails").get_json()
    kontakt("Rittal", "ex@rittal.example")
    client.post(f"/api/mails/{m['id']}/senden")
    r = client.post(f"/api/mails/{m['id']}/erinnern")
    assert r.status_code == 409 and "noch bis" in r.get_json()["fehler"]
    assert client.application.config["ERINNERUNG_NACH_STUNDEN"] == 48


def test_maengel_senden(client, ordner, smtp):
    vid = hochladen(client)
    r = client.post(f"/api/vorgaenge/{vid}/maengel/senden", json={"von": "Jan"})
    assert r.status_code == 200 and r.get_json()["anzahl"] == 2
    liste = db.session.get(PurchasingEmail, r.get_json()["id"])
    assert (liste.status, liste.approved_by) == ("gesendet", "Jan")
    assert len(smtp.gesendet) == 1 and "Mängelliste" in smtp.gesendet[0]["Subject"]


def test_maengel_senden_ohne_maengel_ist_409(client, ordner, smtp):
    vid = hochladen(client, ZEILEN[:1])
    r = client.post(f"/api/vorgaenge/{vid}/maengel/senden")
    assert r.status_code == 409 and "Keine Mängel" in r.get_json()["fehler"]


# Ausgabe


def test_export_erst_wenn_vollstaendig(client, ordner):
    vid = hochladen(client, ZEILEN[:1])
    r = client.get(f"/api/vorgaenge/{vid}/export")
    assert r.status_code == 409 and r.get_json()["fehler"].startswith(
        "Noch 1 Position ist offen, deshalb gibt es die Kundenliste noch nicht.")
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    client.post(f"/api/artikel/{a['id']}/freigeben", json={"fund_id": a["funde"][0]["id"]})
    v = vorgang(client, vid)
    assert v["vollstaendig"] and v["offen"] == 0 and v["zaehler"] == {"geprueft": 1}
    r = client.get(f"/api/vorgaenge/{vid}/export")
    assert r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
    ws = load_workbook(io.BytesIO(r.data)).active
    assert any("99000010" in str(z) for z in ws.iter_rows(values_only=True))
    zellen = [str(z) for zeile in ws.iter_rows(values_only=True) for z in zeile if z]
    assert "Festo_Konformitätserklärung_VACF-B-K1-1-5.pdf" in zellen
    assert not any("#U" in z for z in zellen)
    # das Protokoll der Freigabe nennt die Datei lesbar
    assert any("„Festo_Konformitätserklärung_VACF-B-K1-1-5.pdf“ freigegeben" in e["text"]
               for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json())


def test_name_und_protokoll_ueberstehen_einen_neustart(client, ordner):
    vid = hochladen(client, name="Auftrag 4711")
    gestartet(client, vid)
    vorher = client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()
    oberflaeche._lauf.clear()  # nach einem Neustart ist der Arbeitsspeicher leer
    db.session.expire_all()
    assert vorgang(client, vid)["name"] == "Auftrag 4711"
    assert db.session.get(Bom, vid).name == "Auftrag 4711"
    assert client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json() == vorher and len(vorher) > 2
    assert [e["n"] for e in vorher] == list(range(1, len(vorher) + 1))
    rest = client.get(f"/api/vorgaenge/{vid}/ereignisse?seit=2").get_json()
    assert rest == vorher[2:]


def test_freigeben_mit_korrigierter_dokumentart(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    a = artikel(client, vid, 20)
    fund = a["funde"][0]
    assert fund["doc_type"] == "eu_baumusterpruefbescheinigung"
    r = client.post(f"/api/artikel/{a['id']}/freigeben",
                    json={"fund_id": fund["id"], "von": "Jan", "doc_type": "quatsch"})
    assert r.status_code == 400 and "Dokumentart" in r.get_json()["fehler"]
    r = client.post(f"/api/artikel/{a['id']}/freigeben",
                    json={"fund_id": fund["id"], "von": "Jan",
                          "doc_type": "eu_konformitaetserklaerung"})
    assert r.status_code == 200
    assert db.session.get(Finding, fund["id"]).doc_type == "eu_konformitaetserklaerung"
    assert artikel(client, vid, 20)["status"] == "geprueft"


def test_unterbrochene_suche_laeuft_nach_neustart_weiter(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    assert db.session.get(Bom, vid).search_started_at is None  # fertig: nichts offen
    assert oberflaeche.setze_fort() == []

    # Neustart mitten im Lauf: der Lauf steht noch offen, Pos. 10 ist schon gesucht, Pos. 20 nicht
    bom = db.session.get(Bom, vid)
    p10, p20 = (next(i for i in bom.items if i.pos == p) for p in ("10", "20"))
    bom.search_started_at = p10.searched_at.replace(tzinfo=None)
    p20.searched_at = None
    for f in list(p20.findings):
        db.session.delete(f)
    db.session.commit()
    oberflaeche._lauf.clear()
    vorher = len(client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json())

    assert oberflaeche.setze_fort() == [vid]
    neu = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse?seit={vorher}").get_json()]
    assert neu[0] == ("Der ATEX-Agent wurde neu gestartet. Er setzt die unterbrochene Suche jetzt "
                      "fort.")
    assert any(t.startswith("Position 20: Der Agent sucht") for t in neu)
    assert not any(t.startswith("Position 10: Der Agent sucht") for t in neu)
    db.session.expire_all()
    assert db.session.get(Bom, vid).search_started_at is None
    assert artikel(client, vid, 20)["funde"]
