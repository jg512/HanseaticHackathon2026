import smtplib
import time
from datetime import datetime

import pytest

from app import llm
from app.db import db
from app.models import Bom, BomItem
from app.services import email_entwurf, status
from conftest import kontakt

TEST_ADRESSE = "test@example.com"


def vorgang():
    """Vorgang mit vier Positionen; die Suche wird hier von Hand nachgestellt."""
    def pos(nr, **felder):
        werte = dict(row_number=nr, pos=str(nr * 10), profile_no=f"990000{nr}0",
                     description="Magnetspule für Ventil", manufacturer="Festo",
                     manufacturer_article_no=f"VACF-{nr}-EX4-M", location="Zone 1")
        werte.update(felder)
        return BomItem(**werte)

    gesucht = dict(searched_at=datetime(2026, 11, 13), searched_in="Datenbank, Ordner")
    bom = Bom(machine="Abfüllanlage TM-1", ex_area="Innenraum Zone 1",
              source_file_name="x.xlsx", source_file_hash="h",
              items=[pos(1, **gesucht),                                    # nicht gefunden
                     pos(2, manufacturer="Pepperl+Fuchs", location="Zone 2", **gesucht),
                     pos(3),                                               # noch nicht gesucht
                     pos(4, manufacturer="", **gesucht)])                  # Hersteller fehlt
    db.session.add(bom)
    db.session.flush()
    status.aktualisiere_alle(bom)
    db.session.commit()
    return bom


class FalscherSmtp:
    """Ersetzt smtplib.SMTP: merkt sich die Nachrichten statt sie zu senden."""
    gesendet = []
    fehler = None

    def __init__(self, host, port, timeout=None):
        self.ziel = (host, port)

    def __enter__(self):
        if self.fehler:
            raise self.fehler
        return self

    def __exit__(self, *args):
        return False

    def send_message(self, nachricht):
        self.gesendet.append((self.ziel, nachricht))


@pytest.fixture
def smtp(monkeypatch):
    FalscherSmtp.gesendet, FalscherSmtp.fehler = [], None
    monkeypatch.setattr(smtplib, "SMTP", FalscherSmtp)
    return FalscherSmtp


def anlegen(client, bom):
    r = client.post(f"/api/boms/{bom.id}/emails")
    assert r.status_code == 200
    return r.get_json()


def test_entwuerfe_nur_fuer_nicht_gefunden_und_ohne_dubletten(client):
    bom = vorgang()
    assert [i.status for i in bom.items] == ["nicht_gefunden", "nicht_gefunden", "offen",
                                             "nicht_gefunden"]
    entwuerfe = anlegen(client, bom)
    # Pos. 30 ist nicht gesucht, Pos. 40 hat keinen Hersteller: an wen sollte die Anfrage gehen?
    assert [(e["pos"], e["manufacturer"], e["searched_in"]) for e in entwuerfe] == [
        ("10", "Festo", "Datenbank, Ordner"), ("20", "Pepperl+Fuchs", "Datenbank, Ordner")]
    assert {(e["status"], e["to_address"]) for e in entwuerfe} == {("entwurf", TEST_ADRESSE)}
    assert entwuerfe[0]["subject"] == "Anfrage Ex-Dokumente: Festo VACF-1-EX4-M"

    assert anlegen(client, bom) == entwuerfe  # zweiter Aufruf: keine Dubletten
    assert client.get(f"/api/boms/{bom.id}/emails").get_json() == entwuerfe
    assert client.post("/api/boms/99/emails").status_code == 404
    assert client.get("/api/boms/99/emails").status_code == 404


def test_vorlage_enthaelt_die_fakten(client):
    text = anlegen(client, vorgang())[1]["body"]
    for teil in ("- Hersteller: Pepperl+Fuchs", "- Typbezeichnung: VACF-2-EX4-M",
                 "- Bezeichnung: Magnetspule für Ventil", "- Unsere Artikelnummer: 99000020",
                 "- Maschine: Abfüllanlage TM-1", "- Ex-Bereich: Innenraum Zone 1",
                 "- Einbauort: Zone 2", "EU-Konformitätserklärung (zwingend erforderlich)",
                 "EU-Baumusterprüfbescheinigung, falls vorhanden",
                 "Betriebsanleitung, falls vorhanden",
                 "Die EU-Konformitätserklärung benötigen wir unbedingt in deutscher Sprache.",
                 "Mit freundlichen Grüßen\nATEX Agent"):
        assert teil in text
    # Bericht und interne Begriffe gehören nicht in die E-Mail an den Hersteller
    assert "gesucht" not in text.lower() and "PRO.FILE" not in text


VORLAGE = "Sehr geehrte Damen und Herren,\n\nfür die Dokumentation einer Abfüllanlage"


def test_modell_formuliert_nur_die_einleitung(client, monkeypatch):
    client.application.config["EMAIL_MIT_LLM"] = True

    def modell(messages):
        assert "Typbezeichnung: VACF-" in messages[1]["content"]
        if "VACF-1-EX4-M" in messages[1]["content"]:
            return "Guten Tag,\n\nwir bitten um die Ex-Dokumente zu Festo VACF-1-EX4-M.\n"
        return "Guten Tag,\n\nbitte senden Sie uns die Dokumente von Pepperl+Fuchs."

    monkeypatch.setattr(llm, "chat", modell)
    a, b = anlegen(client, vorgang())
    # Einleitung vom Modell, die Fakten darunter setzt der Code
    assert a["body"].startswith("Guten Tag,\n\nwir bitten um die Ex-Dokumente zu Festo "
                                "VACF-1-EX4-M.\n\nBauteil:\n- Hersteller: Festo\n")
    assert "- Unsere Artikelnummer: 99000010" in a["body"]
    assert "EU-Konformitätserklärung (zwingend erforderlich)" in a["body"]
    assert b["body"].startswith(VORLAGE)  # Typ fehlt im Modelltext: Vorlage


@pytest.mark.parametrize("antwort", [
    "Sehr geehrte [Ansprechpartner], Festo VACF-1-EX4-M",       # Platzhalter
    "Guten Tag, Festo VACF-1-EX4-M.\n\nMit freundlichen Grüßen\nMax Muster",  # erfundener Name
    "Festo VACF-1-EX4-M " * 60,                                  # zu lang
    None])
def test_zweifelhafter_modelltext_wird_verworfen(client, monkeypatch, antwort):
    client.application.config["EMAIL_MIT_LLM"] = True
    monkeypatch.setattr(llm, "chat", lambda messages: antwort)
    assert anlegen(client, vorgang())[0]["body"].startswith(VORLAGE)


def test_modellausfall_und_zeitueberschreitung_ergeben_die_vorlage(client, monkeypatch):
    client.application.config["EMAIL_MIT_LLM"] = True

    def modell(messages):
        if "VACF-1-EX4-M" in messages[1]["content"]:
            raise ConnectionError("Anbieter nicht erreichbar")
        time.sleep(1)
        return "Guten Tag, Pepperl+Fuchs VACF-2-EX4-M."  # wäre brauchbar, kommt aber zu spät

    monkeypatch.setattr(llm, "chat", modell)
    monkeypatch.setattr(email_entwurf, "MODELL_WARTEZEIT", 0.2)
    entwuerfe = anlegen(client, vorgang())
    assert [e["body"].startswith(VORLAGE) for e in entwuerfe] == [True, True]


def test_ohne_schalter_wird_das_modell_nicht_gefragt(client, monkeypatch):
    def modell(messages):
        raise AssertionError("Modell darf nicht gefragt werden")

    monkeypatch.setattr(llm, "chat", modell)
    assert len(anlegen(client, vorgang())) == 2


def test_bearbeiten_freigeben_senden(client, smtp):
    bom = vorgang()
    kontakt("Festo", "anfragen@festo.example")
    client.application.config.update(MAIL_TEST_ADDRESS="demo@beispiel-maschinenbau.test",
                                     SMTP_HOST="mail", SMTP_PORT=1025)
    nr = anlegen(client, bom)[0]["id"]
    assert client.post(f"/api/emails/{nr}/send").status_code == 409  # erst freigeben

    r = client.put(f"/api/emails/{nr}", json={"subject": "Anfrage Festo", "body": "Text mit Ä"})
    assert (r.get_json()["subject"], r.get_json()["body"]) == ("Anfrage Festo", "Text mit Ä")
    assert client.put(f"/api/emails/{nr}", json={"subject": ""}).status_code == 400

    assert client.post(f"/api/emails/{nr}/approve", json={}).status_code == 400  # ohne Namen
    r = client.post(f"/api/emails/{nr}/approve", json={"by": "Jan"})
    assert (r.get_json()["status"], r.get_json()["approved_by"]) == ("freigegeben", "Jan")
    assert client.put(f"/api/emails/{nr}", json={"subject": "x", "body": "y"}).status_code == 409
    assert client.post(f"/api/emails/{nr}/approve", json={"by": "Jan"}).status_code == 409

    r = client.post(f"/api/emails/{nr}/send")
    assert r.status_code == 200
    e = r.get_json()
    assert (e["status"], e["to_address"], e["item_status"]) == (
        "gesendet", "demo@beispiel-maschinenbau.test", "angefordert")
    assert (e["empfaenger_hersteller"], e["kontakt_fehlt"], e["versand_an"]) == (
        "anfragen@festo.example", False, "demo@beispiel-maschinenbau.test")
    assert e["sent_at"]

    [(ziel, nachricht)] = smtp.gesendet
    assert ziel == ("mail", 1025)
    assert (nachricht["To"], nachricht["Subject"]) == ("demo@beispiel-maschinenbau.test", "Anfrage Festo")
    assert nachricht.get_content().strip() == "Text mit Ä"
    assert "festo" not in nachricht["To"].lower()

    assert client.post(f"/api/emails/{nr}/send").status_code == 409  # nicht zweimal
    pos = client.get(f"/api/boms/{bom.id}").get_json()["items"][0]
    assert pos["status"] == "angefordert"
    assert client.put("/api/emails/999", json={}).status_code == 404


def test_smtp_fehler_ergibt_502(client, smtp):
    bom = vorgang()
    kontakt("Festo", "anfragen@festo.example")
    nr = anlegen(client, bom)[0]["id"]
    client.post(f"/api/emails/{nr}/approve", json={"by": "Jan"})
    smtp.fehler = ConnectionRefusedError("Verbindung abgelehnt")

    r = client.post(f"/api/emails/{nr}/send")
    assert r.status_code == 502
    # für den Menschen ein ganzer Satz, die Einzelheiten stehen im Log
    assert r.get_json()["error"] == (
        "Die E-Mail wurde nicht gesendet, weil der Postausgang gerade nicht erreichbar ist. "
        "Bitte versuchen Sie es in ein paar Minuten noch einmal. Bleibt der Fehler, geben Sie "
        "der Administration Bescheid.")
    e = client.get(f"/api/boms/{bom.id}/emails").get_json()[0]
    assert (e["status"], e["sent_at"], e["item_status"]) == ("freigegeben", None, "nicht_gefunden")
    assert smtp.gesendet == []


def test_anfrage_fragt_nicht_nach_gepruefter_bescheinigung(client):
    from app.models import Document, Part, PartDocument
    part = Part(profile_no="99000010", changed_by="test")
    doc = Document(doc_type="eu_baumusterpruefbescheinigung", file_path="Festo/x.pdf",
                   changed_by="test")
    db.session.add(PartDocument(part=part, document=doc, status="geprueft", changed_by="test"))
    db.session.commit()
    text = anlegen(client, vorgang())[0]["body"]
    assert "EU-Konformitätserklärung (zwingend erforderlich)" in text
    assert "Baumusterprüfbescheinigung" not in text
