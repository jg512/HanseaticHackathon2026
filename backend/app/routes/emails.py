"""E-Mail-Entwürfe ansehen, freigeben, senden (M16, M17); Erinnerung und Eskalation (E7, E8);
Mängelliste an den Einkauf (E5)."""

from flask import Blueprint, abort, g, request

from ..db import db
from ..models import Bom, BomItem, EmailDraft
from ..models.bom import PurchasingEmail
from ..services import email_entwurf, erinnerung, korrektur, versand

bp = Blueprint("emails", __name__, url_prefix="/api")


def _entwuerfe(bom_id):
    db.session.get(Bom, bom_id) or abort(404)
    entwuerfe = db.session.scalars(
        db.select(EmailDraft).join(BomItem).filter(BomItem.bom_id == bom_id)
        .order_by(BomItem.row_number, EmailDraft.id))
    return [erinnerung.ansicht(e) for e in entwuerfe]


STAND = {"entwurf": "noch ein Entwurf", "freigegeben": "freigegeben, aber nicht gesendet",
         "gesendet": "schon gesendet"}


def _entwurf(email_id, erwartet, modell=EmailDraft):
    """Entwurf laden; (Entwurf, None) oder (None, Fehlerantwort), wenn der Status nicht passt."""
    entwurf = db.session.get(modell, email_id) or abort(404)
    if entwurf.status != erwartet:
        soll, ist = STAND.get(erwartet, erwartet), STAND.get(entwurf.status, entwurf.status)
        return None, ({"error": f"Das geht nur bei einer E-Mail, die {soll} ist. Diese ist "
                                f"{ist}. Bitte laden Sie die Seite neu."}, 409)
    return entwurf, None


def _neuer_text(entwurf):
    """Betreff und Text aus der Anfrage übernehmen; None oder Fehlerantwort."""
    daten = request.get_json(silent=True) or {}
    betreff, text = daten.get("subject"), daten.get("body")
    if not (isinstance(betreff, str) and betreff.strip() and isinstance(text, str) and text.strip()):
        return {"error": "Betreff und Text dürfen nicht leer sein. Bitte tragen Sie beides "
                         "ein."}, 400
    entwurf.subject, entwurf.body = betreff.strip(), text
    return None


def _freigabe(entwurf):
    """Freigabe mit dem Namen aus der Anfrage (M3); None oder Fehlerantwort."""
    name = str((request.get_json(silent=True) or {}).get("by") or "").strip()
    if not name:
        return {"error": "Ohne Anmeldung wird nichts gespeichert. Bitte melden Sie sich an."}, 400
    entwurf.status, entwurf.approved_by = "freigegeben", name
    return None


@bp.get("/boms/<int:bom_id>/emails")
def liste(bom_id):
    return _entwuerfe(bom_id)


@bp.post("/boms/<int:bom_id>/emails")
def anlegen(bom_id):
    """Entwürfe für alle Positionen „nicht gefunden“ ohne Entwurf; gibt alle Entwürfe zurück."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    email_entwurf.lege_entwuerfe_an(bom)
    db.session.commit()
    return _entwuerfe(bom_id)


@bp.put("/emails/<int:email_id>")
def bearbeiten(email_id):
    """Anfrage: {"subject": "...", "body": "..."}"""
    entwurf, fehler = _entwurf(email_id, "entwurf")
    if fehler or (fehler := _neuer_text(entwurf)):
        return fehler
    db.session.commit()
    return erinnerung.ansicht(entwurf)


@bp.post("/emails/<int:email_id>/approve")
def freigeben(email_id):
    """Ein Mensch gibt den Entwurf frei (M3). Anfrage: {"by": "Name"}"""
    entwurf, fehler = _entwurf(email_id, "entwurf")
    if fehler or (fehler := _freigabe(entwurf)):
        return fehler
    db.session.commit()
    return erinnerung.ansicht(entwurf)


@bp.post("/emails/<int:email_id>/send")
def senden(email_id):
    """Versand an MAIL_TEST_ADDRESS, nur nach Freigabe."""
    entwurf, fehler = _entwurf(email_id, "freigegeben")
    if fehler:
        return fehler
    try:
        email_entwurf.sende(entwurf)
    except email_entwurf.SendeFehler as e:  # 409 ohne Herstelleradresse (KontaktFehlt)
        db.session.rollback()
        return {"error": str(e)}, e.code
    db.session.commit()
    return erinnerung.ansicht(entwurf)


@bp.post("/emails/<int:email_id>/reminder")
def erinnern(email_id):
    """Neuer Entwurf „erinnerung“ zur Anfrage, nur wenn die Erinnerung fällig ist (sonst 409)."""
    entwurf = db.session.get(EmailDraft, email_id) or abort(404)
    try:
        neu = erinnerung.lege_erinnerung_an(entwurf)
    except erinnerung.SchrittFehler as e:
        return {"error": str(e)}, 409
    db.session.commit()
    return erinnerung.ansicht(neu), 201


@bp.post("/emails/<int:email_id>/escalate")
def eskalieren(email_id):
    """Ein Mensch eskaliert an den Einkauf, nur wenn es fällig ist. Anfrage: {"by": "Name"}"""
    entwurf = db.session.get(EmailDraft, email_id) or abort(404)
    name = str((request.get_json(silent=True) or {}).get("by") or "").strip()
    if not name:
        return {"error": "Ohne Anmeldung wird nichts gespeichert. Bitte melden Sie sich an."}, 400
    try:
        erinnerung.eskaliere(entwurf, name)
    except erinnerung.SchrittFehler as e:
        return {"error": str(e)}, 409
    db.session.commit()
    return erinnerung.ansicht(entwurf)


# Mängelliste an den Einkauf (E5): ein Entwurf je Vorgang, gleicher Weg wie die Anfragen


@bp.get("/boms/<int:bom_id>/maengelliste")
def maengellisten(bom_id):
    """Alle Mängellisten des Vorgangs, die neueste zuerst."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    return [m.to_dict() for m in reversed(bom.purchasing_emails)]


@bp.post("/boms/<int:bom_id>/maengelliste")
def maengelliste_anlegen(bom_id):
    """Entwurf mit den aktuellen Mängeln; ein offener Entwurf wird neu aufgebaut (200)."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    try:
        liste, neu = korrektur.lege_maengelliste_an(bom)
    except korrektur.Fehler as e:
        return {"error": str(e)}, e.code
    db.session.commit()
    return liste.to_dict(), 201 if neu else 200


@bp.put("/maengellisten/<int:liste_id>")
def maengelliste_bearbeiten(liste_id):
    """Anfrage: {"subject": "...", "body": "..."}"""
    liste, fehler = _entwurf(liste_id, "entwurf", PurchasingEmail)
    if fehler or (fehler := _neuer_text(liste)):
        return fehler
    db.session.commit()
    return liste.to_dict()


@bp.post("/maengellisten/<int:liste_id>/approve")
def maengelliste_freigeben(liste_id):
    """Ein Mensch gibt die Mängelliste frei (M3). Anfrage: {"by": "Name"}"""
    liste, fehler = _entwurf(liste_id, "entwurf", PurchasingEmail)
    if fehler or (fehler := _freigabe(liste)):
        return fehler
    db.session.commit()
    return liste.to_dict()


@bp.post("/maengellisten/<int:liste_id>/send")
def maengelliste_senden(liste_id):
    """Versand an EINKAUF_ADRESSE (Standard: MAIL_TEST_ADDRESS), nur nach Freigabe."""
    liste, fehler = _entwurf(liste_id, "freigegeben", PurchasingEmail)
    if fehler:
        return fehler
    try:
        korrektur.sende_maengelliste(liste)
    except email_entwurf.SendeFehler as e:
        db.session.rollback()
        return {"error": str(e)}, 502
    db.session.commit()
    return liste.to_dict()


# Postausgang (services/versand.py): wohin E-Mails gehen, Test-E-Mail für die Administration


@bp.get("/mail/einstellungen")
def mail_einstellungen():
    """{versand: testpostfach | echt_an_testadresse | echt, absender, testadresse, satz, …},
    ohne Zugangsdaten."""
    return versand.einstellungen()


@bp.post("/mail/test")
def mail_test():
    """Nur Admins: eine Test-E-Mail an MAIL_TEST_ADDRESS; 502 mit dem Grund als Satz."""
    benutzer = getattr(g, "benutzer", None)
    if benutzer is not None and benutzer.rolle != "admin":
        return {"error": "Die Test-E-Mail darf nur die Administration senden."}, 403
    try:
        return versand.test_email()
    except versand.SendeFehler as e:
        return {"error": str(e)}, e.code
