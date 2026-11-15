"""Benutzerkonten: anlegen, anmelden, ändern, Passwort. Routen in routes/auth.py.

Regeln: Anmeldename eindeutig ohne Rücksicht auf Groß- und Kleinschreibung, ebenso der
Anzeigename (er steht im Protokoll, zwei gleiche wären nicht zu unterscheiden). Der letzte
aktive Admin bleibt Admin und aktiv. Nichts wird gelöscht.
"""

import re
from datetime import datetime, timedelta, timezone

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import db
from ..models.benutzer import ROLLEN, Benutzer, _utc
from .erledigt import Fehler

LOGIN_MUSTER = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
PASSWORT_MIN = 8
UNGUELTIGER_LOGIN = ("Dieser Anmeldename geht nicht. Bitte verwenden Sie 2 bis 40 "
                     "Kleinbuchstaben, Ziffern, Punkte oder Striche.")
FALSCH = "Anmeldename oder Passwort stimmen nicht. Bitte prüfen Sie Ihre Eingabe."
# Vergleichswert, damit ein unbekannter Name gleich lange dauert wie ein falsches Passwort
_ATTRAPPE = generate_password_hash("attrappe-zum-zeitausgleich")


def _jetzt():
    return datetime.now(timezone.utc)


def _text(daten, feld, name, laenge=200):
    """name im Nominativ mit Artikel, z. B. „der Anmeldename“."""
    wert = daten.get(feld)
    if wert is not None and not isinstance(wert, str):
        wert = ""
    wert = (wert or "").strip()
    if not wert:
        raise Fehler(f"Es fehlt {name}. Bitte tragen Sie ihn ein.", 400)
    if len(wert) > laenge:
        raise Fehler(f"{name[0].upper()}{name[1:]} ist zu lang. Bitte kürzen Sie ihn auf "
                     f"höchstens {laenge} Zeichen.", 400)
    return wert


def _passwort(wert):
    if not isinstance(wert, str) or len(wert) < PASSWORT_MIN:
        raise Fehler(f"Das Passwort ist zu kurz. Bitte wählen Sie eines mit mindestens "
                     f"{PASSWORT_MIN} Zeichen.", 400)
    if len(wert) > 200:
        raise Fehler("Das Passwort ist zu lang. Bitte wählen Sie eines mit höchstens 200 Zeichen.",
                     400)
    return generate_password_hash(wert)


def _rolle(wert):
    if wert not in ROLLEN:
        raise Fehler("Diese Rolle gibt es nicht. Bitte wählen Sie Administration, Prüfung & "
                     "Freigabe oder Nur lesen.", 400)
    return wert


def _name_frei(name, ausser=None):
    andere = db.session.scalars(db.select(Benutzer).where(
        db.func.lower(Benutzer.name) == name.lower())).all()
    if any(b.id != ausser for b in andere):
        raise Fehler(f"Den Namen „{name}“ hat schon ein anderer Zugang. Bitte ergänzen Sie ihn, "
                     "zum Beispiel um die Abteilung.", 409)


def finde(login):
    login = (login or "").strip().lower()
    return db.session.scalar(db.select(Benutzer).filter_by(login=login)) if login else None


def anzahl():
    return db.session.scalar(db.select(db.func.count(Benutzer.id)))


def liste():
    return db.session.scalars(db.select(Benutzer).order_by(Benutzer.aktiv.desc(),
                                                           db.func.lower(Benutzer.name))).all()


def lege_an(daten, von=None):
    """{"login", "name", "rolle", "passwort"}. Fehler 400/409."""
    login = _text(daten, "login", "der Anmeldename", 40).lower()
    if not LOGIN_MUSTER.match(login):
        raise Fehler(UNGUELTIGER_LOGIN, 400)
    if finde(login):
        raise Fehler(f"Den Anmeldenamen „{login}“ gibt es schon. Bitte wählen Sie einen anderen.",
                     409)
    name = _text(daten, "name", "der Name")
    _name_frei(name)
    b = Benutzer(login=login, name=name, rolle=_rolle(daten.get("rolle") or "pruefer"),
                 passwort_hash=_passwort(daten.get("passwort")), angelegt_von=von)
    db.session.add(b)
    db.session.flush()
    return b


def _aktive_admins(ausser):
    return db.session.scalar(db.select(db.func.count(Benutzer.id)).where(
        Benutzer.rolle == "admin", Benutzer.aktiv.is_(True), Benutzer.id != ausser))


def aendere(b, daten):
    """Admin ändert ein Konto: name, rolle, aktiv, passwort (zurücksetzen)."""
    unbekannt = sorted(set(daten) - {"name", "rolle", "aktiv", "passwort"})
    if unbekannt:
        raise Fehler(f"Unbekanntes Feld: {', '.join(unbekannt)}.", 400)
    if "name" in daten:
        name = _text(daten, "name", "der Name")
        _name_frei(name, ausser=b.id)
        b.name = name
    rolle = _rolle(daten["rolle"]) if "rolle" in daten else b.rolle
    aktiv = daten.get("aktiv", b.aktiv)
    if not isinstance(aktiv, bool):
        raise Fehler("aktiv: true oder false.", 400)
    bleibt_admin = rolle == "admin" and aktiv
    if b.rolle == "admin" and b.aktiv and not bleibt_admin and not _aktive_admins(b.id):
        raise Fehler("Das ist der einzige aktive Zugang mit der Rolle Administration. Geben Sie "
                     "zuerst einem anderen Zugang diese Rolle.", 409)
    if b.aktiv and not aktiv:
        b.sitzungs_stand += 1  # abmelden
    if aktiv and not b.aktiv:
        b.fehlversuche, b.gesperrt_bis = 0, None
    b.rolle, b.aktiv = rolle, aktiv
    if "passwort" in daten:
        b.passwort_hash = _passwort(daten["passwort"])
        b.sitzungs_stand += 1
        b.fehlversuche, b.gesperrt_bis = 0, None
    return b


def aendere_passwort(b, alt, neu):
    """Eigenes Passwort; das alte muss stimmen. Andere Sitzungen enden."""
    if not isinstance(alt, str) or not check_password_hash(b.passwort_hash, alt):
        raise Fehler("Das bisherige Passwort stimmt nicht. Bitte geben Sie es noch einmal ein.", 400)
    if alt == neu:
        raise Fehler("Das neue Passwort ist dasselbe wie das bisherige. Bitte wählen Sie ein "
                     "anderes.", 400)
    b.passwort_hash = _passwort(neu)
    b.sitzungs_stand += 1


def melde_an(login, passwort):
    """Benutzer bei Erfolg; sonst Fehler 401 (immer derselbe Text) oder 429 (gesperrt)."""
    cfg = current_app.config
    b = finde(login) if isinstance(login, str) else None
    passwort = passwort if isinstance(passwort, str) else ""
    if b is None:
        check_password_hash(_ATTRAPPE, passwort)
        raise Fehler(FALSCH, 401)
    jetzt = _jetzt()
    if b.gesperrt_bis and _utc(b.gesperrt_bis) > jetzt:
        minuten = max(1, round((_utc(b.gesperrt_bis) - jetzt).total_seconds() / 60))
        dauer = "eine Minute" if minuten == 1 else f"{minuten} Minuten"
        raise Fehler(f"Nach zu vielen falschen Versuchen ist Ihr Zugang für {dauer} gesperrt. "
                     "Bitte versuchen Sie es danach noch einmal oder wenden Sie sich an die "
                     "Administration.", 429)
    if not check_password_hash(b.passwort_hash, passwort):
        b.fehlversuche += 1
        if b.fehlversuche >= cfg["LOGIN_MAX_FEHLVERSUCHE"]:
            b.gesperrt_bis = jetzt + timedelta(minutes=cfg["LOGIN_SPERRE_MINUTEN"])
            b.fehlversuche = 0
        db.session.commit()
        raise Fehler(FALSCH, 401)
    if not b.aktiv:
        raise Fehler(FALSCH, 401)
    b.fehlversuche, b.gesperrt_bis, b.letzte_anmeldung = 0, None, jetzt
    return b


def start_admin(login, name, passwort):
    """Legt den Admin aus der Umgebung an, wenn es diesen Anmeldenamen noch nicht gibt (auch in
    einer Datenbank mit anderen Konten). Ein vorhandenes Konto bleibt unverändert, sein Passwort
    wird nie zurückgesetzt. Die Mindestlänge gilt hier nicht: der Entwicklungs-Zugang hat ein
    kurzes Passwort (Warnung beim Start, siehe app/auth.py). True, wenn angelegt."""
    login = (login or "").strip().lower()
    if not LOGIN_MUSTER.match(login):
        raise Fehler(UNGUELTIGER_LOGIN, 400)
    if finde(login):
        return False
    if not isinstance(passwort, str) or not passwort:
        raise Fehler("Kein Passwort für den Start-Admin.", 400)
    name = (name or "").strip()[:200] or "Administrator"
    _name_frei(name)
    db.session.add(Benutzer(login=login, name=name, rolle="admin", angelegt_von="init-db",
                            passwort_hash=generate_password_hash(passwort)))
    db.session.flush()
    return True


def hat_passwort(b, passwort):
    return b is not None and check_password_hash(b.passwort_hash, passwort)
