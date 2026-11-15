import io
import smtplib
from datetime import datetime, timedelta, timezone

import pytest
from conftest import kontakt
from openpyxl import load_workbook

from app.db import db
from app.models import Bom, BomItem, EmailDraft, Finding
from app.services import erinnerung, status

ANFRAGE_AM = datetime(2024, 3, 4, 8, 0)  # lange vorbei: Frist abgelaufen


class FalscherSmtp:
    gesendet = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def send_message(self, nachricht):
        self.gesendet.append(nachricht)


@pytest.fixture
def smtp(monkeypatch):
    FalscherSmtp.gesendet = []
    monkeypatch.setattr(smtplib, "SMTP", FalscherSmtp)
    return FalscherSmtp


@pytest.fixture
def bom(client):
    """Ein Vorgang mit einer Position ohne Fund (Suche von Hand nachgestellt)."""
    item = BomItem(row_number=9, pos="10", profile_no="99000010", description="Magnetspule",
                   manufacturer="Festo", manufacturer_article_no="VACF-1-EX4-M",
                   location="Zone 1", searched_at=datetime(2024, 3, 1),
                   searched_in="Datenbank, Ordner")
    bom = Bom(machine="Abfüllanlage TM-1", source_file_name="x.xlsx", source_file_hash="h",
              items=[item])
    db.session.add(bom)
    db.session.flush()
    status.aktualisiere(item)
    db.session.commit()
    kontakt("Festo", "anfragen@festo.example")  # ohne Adresse wird nicht gesendet
    return bom


def senden(client, nr):
    assert client.post(f"/api/emails/{nr}/approve", json={"by": "Jan"}).status_code == 200
    r = client.post(f"/api/emails/{nr}/send")
    assert r.status_code == 200
    return r.get_json()


def mails(client, bom):
    return {e["id"]: e for e in client.get(f"/api/boms/{bom.id}/emails").get_json()}


def zuruecksetzen(nr, sent_at):
    """„48 Stunden später“: Versandzeit in die Vergangenheit legen."""
    db.session.get(EmailDraft, nr).sent_at = sent_at
    db.session.commit()


def test_erinnerung_dann_eskalation(client, bom, smtp):
    [anfrage] = client.post(f"/api/boms/{bom.id}/emails").get_json()
    assert (anfrage["art"], anfrage["next_step"], anfrage["due_at"]) == ("anfrage", None, None)
    nr = anfrage["id"]
    gesendet = senden(client, nr)
    # vor der Frist: warten, fällig 48 Stunden nach dem Versand
    assert (gesendet["next_step"], gesendet["reminders_sent"], gesendet["item_status"]) == (
        "warten", 0, "angefordert")
    sent_at = datetime.fromisoformat(gesendet["sent_at"]).replace(tzinfo=None)
    assert datetime.fromisoformat(gesendet["due_at"]) == sent_at + timedelta(hours=48)
    r = client.post(f"/api/emails/{nr}/reminder")
    assert r.status_code == 409 and "noch bis" in r.get_json()["error"]

    # nach der Frist: Erinnerung fällig, noch keine Eskalation
    zuruecksetzen(nr, ANFRAGE_AM)
    e = mails(client, bom)[nr]
    assert (e["next_step"], e["due_at"]) == ("erinnern", "2024-03-06T08:00:00")
    assert e["step_note"] == ("Der Hersteller hat bis 06.03.2024, 09:00 Uhr nicht geantwortet. "
                              "Eine Erinnerung ist fällig.")
    assert client.post(f"/api/emails/{nr}/escalate", json={"by": "Eva"}).status_code == 409

    r = client.post(f"/api/emails/{nr}/reminder")
    assert r.status_code == 201
    neu = r.get_json()
    assert (neu["art"], neu["original_id"], neu["status"], neu["to_address"], neu["next_step"]) == (
        "erinnerung", nr, "entwurf", "test@example.com", None)
    assert neu["subject"] == "Erinnerung: Anfrage Ex-Dokumente: Festo VACF-1-EX4-M"
    assert neu["body"].startswith(
        "Sehr geehrte Damen und Herren,\n\nam 04.03.2024 haben wir Sie um die Ex-Dokumente zu "
        "Ihrem Produkt VACF-1-EX4-M (Festo) gebeten.")
    for teil in ("- Typbezeichnung: VACF-1-EX4-M", "- Unsere Artikelnummer: 99000010",
                 "- Einbauort: Zone 1", "EU-Konformitätserklärung (zwingend erforderlich)"):
        assert teil in neu["body"]
    # solange die Erinnerung Entwurf ist, keine zweite
    e = mails(client, bom)[nr]
    assert (e["next_step"], e["step_note"]) == (
        "warten", "Eine Erinnerung liegt schon als Entwurf bereit. Bitte prüfen Sie sie und "
                  "senden Sie sie.")
    assert client.post(f"/api/emails/{nr}/reminder").status_code == 409

    # Erinnerung durchläuft die vorhandene Freigabe und den Versand
    senden(client, neu["id"])
    assert [m["Subject"] for m in smtp.gesendet] == [
        "Anfrage Ex-Dokumente: Festo VACF-1-EX4-M",
        "Erinnerung: Anfrage Ex-Dokumente: Festo VACF-1-EX4-M"]
    alle = mails(client, bom)
    assert (alle[nr]["next_step"], alle[nr]["step_note"][:30]) == (
        "erledigt", "Es geht weiter mit der Erinner")
    assert (alle[neu["id"]]["next_step"], alle[neu["id"]]["reminders_sent"]) == ("warten", 1)

    # Frist erneut um: Eskalation, keine zweite Erinnerung (ERINNERUNGEN_MAX = 1)
    zuruecksetzen(neu["id"], datetime(2024, 3, 7, 9, 30))
    e = mails(client, bom)[neu["id"]]
    assert (e["next_step"], e["due_at"]) == ("eskalieren", "2024-03-09T09:30:00")
    assert e["step_note"] == ("Der Hersteller hat bis 09.03.2024, 10:30 Uhr nicht geantwortet, "
                              "auch nicht auf eine Erinnerung. Jetzt ist die Eskalation an den "
                              "Einkauf fällig.")
    assert client.post(f"/api/emails/{neu['id']}/reminder").status_code == 409
    assert client.post(f"/api/emails/{nr}/escalate", json={"by": "Eva"}).status_code == 409
    assert client.post(f"/api/emails/{neu['id']}/escalate", json={}).status_code == 400

    r = client.post(f"/api/emails/{neu['id']}/escalate", json={"by": " Eva "})
    assert r.status_code == 200
    e = r.get_json()
    heute = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    assert (e["escalated_by"], e["item_status"], e["next_step"]) == ("Eva", "eskaliert", "erledigt")
    assert e["escalated_at"]
    assert e["step_note"] == f"Eva hat die Anfrage am {heute} an den Einkauf eskaliert."
    assert client.post(f"/api/emails/{neu['id']}/escalate", json={"by": "Eva"}).status_code == 409
    assert len(smtp.gesendet) == 2  # keine E-Mail an den Einkauf

    # eskaliert bleibt offen: in /missing und in der Fehlliste, keine Kundenliste
    stand = client.get(f"/api/boms/{bom.id}/missing").get_json()
    assert stand["complete"] is False
    assert [(o["pos"], o["status"], o["status_note"]) for o in stand["open"]] == [
        ("10", "eskaliert", f"Eva hat die Anfrage am {heute} an den Einkauf eskaliert.")]
    wb = load_workbook(io.BytesIO(client.get(f"/api/boms/{bom.id}/missing.xlsx").data))
    zeile = list(wb.active.iter_rows(values_only=True))[1]
    assert zeile[6:8] == ("An den Einkauf eskaliert",
                          f"Eva hat die Anfrage am {heute} an den Einkauf eskaliert.")
    assert client.get(f"/api/boms/{bom.id}/export").status_code == 409

    # später kommt doch ein Dokument: es geht normal weiter
    item = db.session.get(BomItem, bom.items[0].id)
    item.findings.append(Finding(source="hersteller", doc_type="eu_konformitaetserklaerung"))
    db.session.flush()
    assert status.aktualisiere(item) == "vorschlag"
    db.session.commit()
    assert mails(client, bom)[neu["id"]]["next_step"] == "erledigt"
    item.findings[0].status = "abgelehnt"
    assert status.aktualisiere(item) == "eskaliert"  # abgelehnt: zurück auf den alten Stand


def test_frist_aus_der_konfiguration(client, bom, smtp):
    client.application.config.update(ERINNERUNG_NACH_STUNDEN=0)
    [anfrage] = client.post(f"/api/boms/{bom.id}/emails").get_json()
    assert senden(client, anfrage["id"])["next_step"] == "erinnern"  # sofort fällig (Demo)

    client.application.config.update(ERINNERUNGEN_MAX=0)  # ohne Erinnerung gleich eskalieren
    assert mails(client, bom)[anfrage["id"]]["next_step"] == "eskalieren"


def test_frist_genau(client, bom):
    item = bom.items[0]
    mail = EmailDraft(to_address="t@example.com", subject="s", body="b", status="gesendet",
                      sent_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc))
    item.email_drafts.append(mail)
    db.session.flush()
    frist = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    assert erinnerung.naechster_schritt(mail, frist - timedelta(minutes=1))["next_step"] == "warten"
    assert erinnerung.naechster_schritt(mail, frist)["next_step"] == "erinnern"
    # ein eingegangenes Dokument: nichts mehr fällig
    item.status = "vorhanden"
    assert erinnerung.naechster_schritt(mail, frist) == {
        "next_step": "erledigt", "due_at": None, "reminders_sent": 0,
        "step_note": "Die Position braucht kein Dokument mehr."}


def test_unbekannte_email(client):
    assert client.post("/api/emails/999/reminder").status_code == 404
    assert client.post("/api/emails/999/escalate", json={"by": "Eva"}).status_code == 404


def test_nur_gesendete_emails(client, bom):
    [anfrage] = client.post(f"/api/boms/{bom.id}/emails").get_json()
    r = client.post(f"/api/emails/{anfrage['id']}/reminder")
    assert r.status_code == 409 and "nicht gesendet" in r.get_json()["error"]
