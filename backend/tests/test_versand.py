"""Postausgang (services/versand.py): Sicherheit, Anmeldung, Fehler, Einstellungen, Test-E-Mail.

Keine echte E-Mail: smtplib.SMTP und smtplib.SMTP_SSL sind durch eine Attrappe ersetzt.
"""

import smtplib
import socket
import ssl

import pytest

from app import create_app
from app.services import versand
from app.services.versand import KonfigFehler, pruefe_konfiguration

from test_auth import anmelden, app  # noqa: F401  (Fixture mit Anmeldung)
from test_emails import anlegen, kontakt, vorgang


class Attrappe:
    """Merkt sich Verbindung, STARTTLS, Anmeldung und Nachrichten; wirft auf Wunsch."""
    protokoll = []
    fehler = {}  # Schritt -> Ausnahme

    def __init__(self, host, port, timeout=None, context=None):
        self.art = "ssl" if context is not None else "smtp"
        self.protokoll.append(("verbinden", self.art, host, port))
        self._wirf("verbinden")

    def _wirf(self, schritt):
        if schritt in self.fehler:
            raise self.fehler[schritt]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        self.protokoll.append(("starttls",))
        self._wirf("starttls")

    def login(self, benutzer, passwort):
        self.protokoll.append(("login", benutzer, passwort))
        self._wirf("login")

    def send_message(self, nachricht):
        self._wirf("senden")
        self.protokoll.append(("senden", nachricht["To"], nachricht))


@pytest.fixture
def postausgang(monkeypatch):
    Attrappe.protokoll, Attrappe.fehler = [], {}
    monkeypatch.setattr(smtplib, "SMTP", Attrappe)
    monkeypatch.setattr(smtplib, "SMTP_SSL", Attrappe)
    return Attrappe


def stelle(client, **werte):
    client.application.config.update({"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587",
                                      "SMTP_BENUTZER": "", "SMTP_PASSWORT": "",
                                      "SMTP_SICHERHEIT": "keine", **werte})


@pytest.mark.parametrize("sicherheit, erwartet", [
    ("keine", [("verbinden", "smtp", "smtp.example.com", 587), "senden"]),
    ("starttls", [("verbinden", "smtp", "smtp.example.com", 587), ("starttls",), "senden"]),
    ("ssl", [("verbinden", "ssl", "smtp.example.com", 587), "senden"]),
])
def test_drei_arten_der_sicherheit(client, postausgang, sicherheit, erwartet):
    stelle(client, SMTP_SICHERHEIT=sicherheit)
    with client.application.app_context():
        versand.sende("a@example.com", "Betreff", "Text")
    schritte = [p if p[0] != "senden" else "senden" for p in postausgang.protokoll]
    assert schritte == erwartet


def test_anmeldung_absender_antwort_an(client, postausgang):
    stelle(client, SMTP_SICHERHEIT="starttls", SMTP_BENUTZER="agent@example.com",
           SMTP_PASSWORT="geheim", MAIL_FROM="agent@example.com", MAIL_FROM_NAME="ATEX-Agent",
           MAIL_ANTWORT_AN="einkauf@example.com")
    with client.application.app_context():
        versand.sende("a@example.com", "Betreff", "Text")
    assert [p[0] for p in postausgang.protokoll] == ["verbinden", "starttls", "login", "senden"]
    assert postausgang.protokoll[2] == ("login", "agent@example.com", "geheim")
    n = postausgang.protokoll[3][2]
    assert (n["From"], n["Reply-To"]) == ("ATEX-Agent <agent@example.com>", "einkauf@example.com")


@pytest.mark.parametrize("schritt, ausnahme, teil", [
    ("login", smtplib.SMTPAuthenticationError(535, b"bad credentials geheim"),
     "die Anmeldung am Postfach des Agenten nicht geklappt hat"),
    ("starttls", ssl.SSLError("certificate verify failed"),
     "keine sichere Verbindung zum Postausgang zustande kam"),
    ("senden", smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no such user")}),
     "die Empfängeradresse abgelehnt hat"),
    ("verbinden", socket.timeout("timed out"), "nicht rechtzeitig geantwortet hat"),
    ("verbinden", ConnectionRefusedError("refused"), "gerade nicht erreichbar ist"),
])
def test_fehler_als_satz_ohne_passwort_im_log(client, postausgang, caplog, schritt, ausnahme,
                                               teil):
    stelle(client, SMTP_SICHERHEIT="starttls", SMTP_BENUTZER="agent", SMTP_PASSWORT="geheim")
    postausgang.fehler = {schritt: ausnahme}
    with client.application.app_context(), pytest.raises(versand.SendeFehler) as e:
        versand.sende("a@example.com", "Betreff", "Text")
    assert teil in str(e.value)
    assert "geheim" not in caplog.text and "geheim" not in str(e.value)
    assert "smtp.example.com" in caplog.text  # der technische Grund steht im Log
    assert not any(p[0] == "senden" for p in postausgang.protokoll)


def test_entwurf_bleibt_ungesendet_bei_fehler(client, postausgang):
    stelle(client, SMTP_BENUTZER="agent", SMTP_PASSWORT="falsch")
    bom = vorgang()
    kontakt("Festo", "anfragen@festo.example")
    nr = anlegen(client, bom)[0]["id"]
    client.post(f"/api/emails/{nr}/approve", json={"by": "Jan"})
    postausgang.fehler = {"login": smtplib.SMTPAuthenticationError(535, b"nein")}
    r = client.post(f"/api/emails/{nr}/send")
    assert r.status_code == 502 and "Administration" in r.get_json()["error"]
    e = client.get(f"/api/boms/{bom.id}/emails").get_json()[0]
    assert (e["status"], e["sent_at"]) == ("freigegeben", None)


@pytest.mark.parametrize("nur_test, empfaenger", [(True, "test@example.com"),
                                                  (False, "anfragen@festo.example")])
def test_weg_der_anfrage_nach_email_nur_test(client, postausgang, nur_test, empfaenger):
    stelle(client, EMAIL_NUR_TEST=nur_test, MAIL_TEST_ADDRESS="test@example.com")
    bom = vorgang()
    kontakt("Festo", "anfragen@festo.example")
    nr = anlegen(client, bom)[0]["id"]
    client.post(f"/api/emails/{nr}/approve", json={"by": "Jan"})
    assert client.post(f"/api/emails/{nr}/send").status_code == 200
    [gesendet] = [p for p in postausgang.protokoll if p[0] == "senden"]
    assert gesendet[1] == empfaenger


def test_unbekannte_sicherheit_haelt_den_start_an():
    with pytest.raises(KonfigFehler, match="SMTP_SICHERHEIT"):
        create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://",
                    "SMTP_SICHERHEIT": "tls"})
    with pytest.raises(KonfigFehler, match="gehören zusammen"):
        pruefe_konfiguration({"SMTP_SICHERHEIT": "ssl", "SMTP_PORT": "465",
                              "SMTP_BENUTZER": "agent", "SMTP_PASSWORT": ""})
    cfg = {"SMTP_SICHERHEIT": " STARTTLS ", "SMTP_PORT": "587"}
    pruefe_konfiguration(cfg)
    assert cfg["SMTP_SICHERHEIT"] == "starttls"


def test_einstellungen_ohne_geheimnisse(client):
    client.application.config.update(SMTP_HOST="mail", SMTP_BENUTZER="", SMTP_PASSWORT="",
                                     EMAIL_NUR_TEST=True)
    e = client.get("/api/mail/einstellungen").get_json()
    assert e["versand"] == "testpostfach" and "Test-Postfach" in e["satz"]
    stelle(client, SMTP_BENUTZER="agent", SMTP_PASSWORT="geheim", EMAIL_NUR_TEST=True,
           MAIL_TEST_ADDRESS="pruefung@beispiel-maschinenbau.test")
    e = client.get("/api/mail/einstellungen").get_json()
    assert e["versand"] == "echt_an_testadresse" and "pruefung@beispiel-maschinenbau.test" in e["satz"]
    assert "geheim" not in str(e)
    client.application.config.update(EMAIL_NUR_TEST=False)
    assert client.get("/api/mail/einstellungen").get_json()["versand"] == "echt"


def test_test_email(client, postausgang):
    stelle(client, MAIL_TEST_ADDRESS="pruefung@beispiel-maschinenbau.test")
    r = client.post("/api/mail/test")
    assert r.status_code == 200 and r.get_json()["an"] == "pruefung@beispiel-maschinenbau.test"
    assert [p[1] for p in postausgang.protokoll if p[0] == "senden"] == ["pruefung@beispiel-maschinenbau.test"]
    postausgang.fehler = {"verbinden": ConnectionRefusedError("weg")}
    r = client.post("/api/mail/test")
    assert r.status_code == 502 and "nicht gesendet" in r.get_json()["error"]


def test_test_email_und_modelltest_nur_fuer_admins(app, postausgang):  # noqa: F811
    petra = anmelden(app, "petra")  # Rolle „pruefer“
    r = petra.post("/api/mail/test", json={})
    assert r.status_code == 403 and "Administration" in r.get_json()["error"]
    assert petra.post("/api/modelle/beispiel-chat/test").status_code == 403
    assert petra.get("/api/mail/einstellungen").status_code == 200
    assert not [p for p in postausgang.protokoll if p[0] == "senden"]
    admin = anmelden(app, "admin")
    assert admin.post("/api/mail/test", json={}).status_code == 200
