"""Versand jeder E-Mail des Agenten über SMTP: Anfragen und Erinnerungen an Hersteller,
Mängelliste an den Einkauf, Test-E-Mail der Administration.

Das Postfach kommt aus der Umgebung (.env):

    SMTP_HOST, SMTP_PORT          Postausgangsserver (Standard in Compose: Mailpit „mail“:1025)
    SMTP_BENUTZER, SMTP_PASSWORT  Anmeldung; beide leer = ohne Anmeldung (Mailpit)
    SMTP_SICHERHEIT               keine | starttls | ssl; anderes ist ein Fehler beim Start
    MAIL_FROM, MAIL_FROM_NAME     Absender (Adresse, Anzeigename optional)
    MAIL_ANTWORT_AN               Reply-To, optional

Wohin eine E-Mail an einen Hersteller geht, entscheidet kontakte.empfaenger (EMAIL_NUR_TEST:
an MAIL_TEST_ADDRESS). Das Passwort steht nie im Log und nie in einer Antwort. Fehler: im Log
mit technischem Grund, für den Menschen ein Satz, was nicht geklappt hat und wen er fragt.
"""

import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from flask import current_app

SICHERHEITEN = ("keine", "starttls", "ssl")
WARTEZEIT = 20  # Sekunden für Verbindung und Antwort des Servers
# Hosts von Mailpit (Compose-Dienst „mail“): dort kommt keine E-Mail nach draußen
TESTPOSTFACH_HOSTS = ("mail", "mailpit", "localhost", "127.0.0.1")
ADMIN = "Bitte geben Sie der Administration Bescheid."


class SendeFehler(Exception):
    """Die E-Mail ist nicht gesendet. Der Text ist ein Satz für Anwender."""
    code = 502


class KonfigFehler(RuntimeError):
    """Die Einstellungen für den Versand sind falsch (beim Start der Anwendung)."""


def pruefe_konfiguration(cfg):
    """Beim Start: SMTP_SICHERHEIT bekannt, Port eine Zahl, Benutzer und Passwort nur zusammen."""
    sicherheit = str(cfg.get("SMTP_SICHERHEIT") or "keine").strip().lower()
    if sicherheit not in SICHERHEITEN:
        raise KonfigFehler(f"SMTP_SICHERHEIT={cfg.get('SMTP_SICHERHEIT')!r} ist unbekannt. "
                           f"Erlaubt sind: {', '.join(SICHERHEITEN)}.")
    try:
        int(cfg.get("SMTP_PORT"))
    except (TypeError, ValueError):
        raise KonfigFehler(f"SMTP_PORT={cfg.get('SMTP_PORT')!r} ist keine Zahl.") from None
    if bool(cfg.get("SMTP_BENUTZER")) != bool(cfg.get("SMTP_PASSWORT")):
        raise KonfigFehler("SMTP_BENUTZER und SMTP_PASSWORT gehören zusammen: beide setzen "
                           "(Anmeldung am Postfach) oder beide leer lassen (ohne Anmeldung).")
    cfg["SMTP_SICHERHEIT"] = sicherheit


def _sicherheit(cfg):
    return str(cfg.get("SMTP_SICHERHEIT") or "keine").strip().lower()


def ist_testpostfach(cfg=None):
    """Mailpit ohne Anmeldung: nichts verlässt die Umgebung."""
    cfg = cfg or current_app.config
    return (not cfg.get("SMTP_BENUTZER")
            and str(cfg.get("SMTP_HOST") or "").lower() in TESTPOSTFACH_HOSTS)


def absender(cfg=None):
    cfg = cfg or current_app.config
    adresse = cfg.get("MAIL_FROM") or "atex-agent@example.com"
    name = (cfg.get("MAIL_FROM_NAME") or "").strip()
    return formataddr((name, adresse)) if name else adresse


def einstellungen():
    """Für die Oberfläche, ohne Geheimnisse: wohin E-Mails in dieser Umgebung gehen."""
    cfg = current_app.config
    test = cfg["MAIL_TEST_ADDRESS"]
    nur_test = cfg.get("EMAIL_NUR_TEST", True)
    if ist_testpostfach(cfg):
        versand = "testpostfach"
        satz = ("In dieser Umgebung landet jede E-Mail im Test-Postfach. Nichts geht an "
                "Hersteller oder an den Einkauf.")
    elif nur_test:
        versand = "echt_an_testadresse"
        satz = (f"E-Mails an Hersteller gehen in dieser Umgebung an die Testadresse {test}, "
                "nicht an den Hersteller.")
    else:
        versand = "echt"
        satz = ("E-Mails an Hersteller gehen wirklich an die Adresse, die bei den "
                "Herstellerkontakten hinterlegt ist.")
    return {"versand": versand, "absender": absender(cfg), "testadresse": test,
            "antwort_an": cfg.get("MAIL_ANTWORT_AN") or None, "nur_test": bool(nur_test),
            "einkauf": cfg.get("EINKAUF_ADRESSE") or test, "satz": satz}


def nachricht(an, betreff, text, cfg=None):
    cfg = cfg or current_app.config
    n = EmailMessage()
    n["From"] = absender(cfg)
    n["To"] = an
    if cfg.get("MAIL_ANTWORT_AN"):
        n["Reply-To"] = cfg["MAIL_ANTWORT_AN"]
    n["Subject"] = betreff
    n["Date"] = formatdate(localtime=True)
    n["Message-ID"] = make_msgid(domain="atex-agent.local")
    n.set_content(text)
    return n


def _verbinden(cfg):
    host, port = cfg["SMTP_HOST"], int(cfg["SMTP_PORT"])
    if _sicherheit(cfg) == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=WARTEZEIT,
                                context=ssl.create_default_context())
    return smtplib.SMTP(host, port, timeout=WARTEZEIT)


def _fehlertext(e):
    """(Satz für Anwender, Grund für das Log) zu einer Ausnahme beim Versand."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return ("Die E-Mail wurde nicht gesendet, weil die Anmeldung am Postfach des Agenten "
                f"nicht geklappt hat. {ADMIN}", "Anmeldung abgelehnt")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return ("Die E-Mail wurde nicht gesendet, weil der Postausgang die Empfängeradresse "
                "abgelehnt hat. Bitte prüfen Sie die Adresse. Stimmt sie, geben Sie der "
                "Administration Bescheid.", "Empfänger abgelehnt")
    if isinstance(e, smtplib.SMTPSenderRefused):
        return ("Die E-Mail wurde nicht gesendet, weil der Postausgang den Absender abgelehnt "
                f"hat. {ADMIN}", "Absender abgelehnt")
    if isinstance(e, (ssl.SSLError, smtplib.SMTPNotSupportedError)):
        return ("Die E-Mail wurde nicht gesendet, weil keine sichere Verbindung zum Postausgang "
                f"zustande kam. {ADMIN}", "TLS fehlgeschlagen")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return ("Die E-Mail wurde nicht gesendet, weil der Postausgang nicht rechtzeitig "
                "geantwortet hat. Bitte versuchen Sie es in ein paar Minuten noch einmal. Bleibt "
                "der Fehler, geben Sie der Administration Bescheid.", "Zeitüberschreitung")
    return ("Die E-Mail wurde nicht gesendet, weil der Postausgang gerade nicht erreichbar ist. "
            "Bitte versuchen Sie es in ein paar Minuten noch einmal. Bleibt der Fehler, geben Sie "
            "der Administration Bescheid.", "nicht erreichbar oder abgelehnt")


def sende(an, betreff, text):
    """Eine E-Mail an `an`. SendeFehler, wenn sie nicht angenommen wurde; dann ist nichts
    gesendet und der Aufrufer markiert nichts als gesendet."""
    cfg = current_app.config
    n = nachricht(an, betreff, text, cfg)
    try:
        with _verbinden(cfg) as smtp:
            if _sicherheit(cfg) == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if cfg.get("SMTP_BENUTZER"):
                smtp.login(cfg["SMTP_BENUTZER"], cfg.get("SMTP_PASSWORT") or "")
            smtp.send_message(n)
    except (smtplib.SMTPException, OSError) as e:
        satz, grund = _fehlertext(e)
        # nie das Passwort: nur Host, Port, Modus, Benutzername, Art und Text der Ausnahme
        current_app.logger.error(
            "Versand an %s über %s:%s (%s, Benutzer %s) fehlgeschlagen, %s: %s: %s", an,
            cfg["SMTP_HOST"], cfg["SMTP_PORT"], _sicherheit(cfg), cfg.get("SMTP_BENUTZER") or "-",
            grund, type(e).__name__, str(e).replace(cfg.get("SMTP_PASSWORT") or "\0", "***"))
        raise SendeFehler(satz) from e
    current_app.logger.info("E-Mail an %s gesendet (%s)", an, betreff)


def test_email():
    """Admin: eine E-Mail an MAIL_TEST_ADDRESS, um den Versand zu prüfen."""
    an = current_app.config["MAIL_TEST_ADDRESS"]
    e = einstellungen()
    sende(an, "Test-E-Mail des ATEX-Agenten",
          "Diese E-Mail hat die Administration im ATEX-Agenten verschickt, um den Versand zu "
          f"prüfen.\n\nAbsender: {e['absender']}\n{e['satz']}\n")
    return {"ok": True, "an": an,
            "text": f"Die Test-E-Mail ist an {an} gegangen. Bitte sehen Sie im Postfach nach, "
                    "ob sie angekommen ist."}
