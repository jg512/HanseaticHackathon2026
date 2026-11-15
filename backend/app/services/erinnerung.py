"""Erinnerung und Eskalation, wenn der Hersteller nicht antwortet (E7, E8).

Nichts geschieht von selbst: naechster_schritt zeigt nur, was fällig ist. Eine Erinnerung ist ein
neuer Entwurf und läuft durch dieselbe Freigabe und denselben Versand wie die Anfrage. Die
Eskalation löst ein Mensch aus; sie schickt keine E-Mail, der Einkauf arbeitet mit der Fehlliste.

Frist ERINNERUNG_NACH_STUNDEN ab der zuletzt gesendeten E-Mail, ERINNERUNGEN_MAX Erinnerungen
vor der Eskalation. Kommt ein Dokument oder ein Vorschlag herein, ist nichts mehr fällig.
"""

from datetime import datetime, timedelta, timezone

from flask import current_app

from ..models import EmailDraft
from . import email_entwurf, status

class SchrittFehler(Exception):
    """Der verlangte Schritt ist gerade nicht dran; der Text sagt, warum."""


def _utc(zeit):
    """Zeitpunkt aus der Datenbank (ohne Zone, gespeichert in UTC) mit Zone."""
    return zeit if zeit is None or zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


def _ortszeit(zeit):
    """UTC -> Ortszeit (ZEITZONE, Standard Europe/Berlin) für Texte an Anwender."""
    from zoneinfo import ZoneInfo
    return _utc(zeit).astimezone(ZoneInfo(current_app.config.get("ZEITZONE", "Europe/Berlin")))


def _zeit(zeit):
    """„06.03.2024, 09:00 Uhr“ in Ortszeit."""
    return f"{_ortszeit(zeit):%d.%m.%Y, %H:%M} Uhr"


def _iso(zeit):
    """Wie sent_at in der Antwort: UTC ohne Zonenangabe."""
    return zeit.astimezone(timezone.utc).replace(tzinfo=None).isoformat() if zeit else None


def _kette(entwurf):
    """Die erste Anfrage und alle Erinnerungen dazu."""
    erste = entwurf.original or entwurf
    return erste, [erste] + [e for e in erste.item.email_drafts if e.original is erste]


def naechster_schritt(entwurf, jetzt=None):
    """{"next_step", "due_at", "reminders_sent", "step_note"} für eine gesendete E-Mail.

    next_step: warten | erinnern | eskalieren | erledigt; None für ungesendete Entwürfe.
    """
    if entwurf.status != "gesendet":
        return {"next_step": None, "due_at": None, "reminders_sent": None, "step_note": None}
    jetzt = jetzt or datetime.now(timezone.utc)
    item = entwurf.item
    _, kette = _kette(entwurf)
    gesendet = sorted((e for e in kette if e.status == "gesendet"), key=lambda e: _utc(e.sent_at))
    erinnert = sum(e.art == "erinnerung" for e in gesendet)
    letzte = gesendet[-1]
    frist = _utc(letzte.sent_at) + timedelta(
        hours=current_app.config["ERINNERUNG_NACH_STUNDEN"])
    eskaliert = next((e for e in kette if e.escalated_at), None)
    entwuerfe = [e for e in kette if e.status != "gesendet"]

    def schritt(name, notiz, faellig=frist):
        return {"next_step": name, "due_at": _iso(faellig), "reminders_sent": erinnert,
                "step_note": notiz}

    if entwurf is not letzte:
        return schritt("erledigt", f"Es geht weiter mit der Erinnerung vom "
                                   f"{_ortszeit(letzte.sent_at):%d.%m.%Y}.", None)
    if item.status == "vorschlag":
        return schritt("erledigt", "Die Antwort liegt als Fund vor. Bitte prüfen Sie ihn beim "
                                   "Artikel.", None)
    if item.status in status.ERLEDIGT:
        return schritt("erledigt", "Die Position braucht kein Dokument mehr.", None)
    if eskaliert:
        return schritt("erledigt", item.status_note or "Die Anfrage ist an den Einkauf "
                                                   "eskaliert.", None)
    if entwuerfe:
        return schritt("warten", "Eine Erinnerung liegt schon als Entwurf bereit. Bitte prüfen "
                                 "Sie sie und senden Sie sie.")
    if jetzt < frist:
        return schritt("warten", f"Der Hersteller hat noch bis {_zeit(frist)} Zeit zu "
                                 "antworten. Bitte warten Sie bis dahin.")
    if erinnert < current_app.config["ERINNERUNGEN_MAX"]:
        return schritt("erinnern", f"Der Hersteller hat bis {_zeit(frist)} nicht geantwortet. "
                                   "Eine Erinnerung ist fällig.")
    mal = "eine Erinnerung" if erinnert == 1 else f"{erinnert} Erinnerungen"
    return schritt("eskalieren", f"Der Hersteller hat bis {_zeit(frist)} nicht geantwortet, auch "
                                 f"nicht auf {mal}. Jetzt ist die Eskalation an den Einkauf "
                                 "fällig.")


def ansicht(entwurf):
    """E-Mail mit den Angaben der Position und dem fälligen Schritt."""
    return dict(email_entwurf.mit_position(entwurf), **naechster_schritt(entwurf))


def _pruefe(entwurf, erwartet):
    s = naechster_schritt(entwurf)
    if s["next_step"] != erwartet:
        grund = s["step_note"] or "Die E-Mail ist noch nicht gesendet."
        raise SchrittFehler(f"Das geht gerade nicht. {grund}")


def erinnerungstext(item, erste):
    """Feste Vorlage: Bezug auf die Anfrage und dieselben Fakten wie dort."""
    return ("Sehr geehrte Damen und Herren,\n\n"
            f"am {_utc(erste.sent_at):%d.%m.%Y} haben wir Sie um die Ex-Dokumente zu Ihrem Produkt "
            f"{item.manufacturer_article_no} ({item.manufacturer}) gebeten. Bisher liegen sie uns "
            "nicht vor. Wir erinnern freundlich an unsere Anfrage.\n\n"
            f"{email_entwurf.angaben(item)}")


def lege_erinnerung_an(entwurf):
    """Neuer Entwurf „erinnerung“ zur Anfrage, nur wenn er fällig ist. Commit macht der Aufrufer."""
    _pruefe(entwurf, "erinnern")
    erste, _ = _kette(entwurf)
    item = erste.item
    neu = EmailDraft(art="erinnerung", original=erste,
                     to_address=current_app.config["MAIL_TEST_ADDRESS"],
                     subject=f"Erinnerung: {erste.subject}", body=erinnerungstext(item, erste))
    item.email_drafts.append(neu)
    return neu


def eskaliere(entwurf, von):
    """Ein Mensch eskaliert an den Einkauf, nur wenn es fällig ist. Commit macht der Aufrufer."""
    _pruefe(entwurf, "eskalieren")
    entwurf.escalated_by, entwurf.escalated_at = von, datetime.now(timezone.utc)
    status.aktualisiere(entwurf.item)
