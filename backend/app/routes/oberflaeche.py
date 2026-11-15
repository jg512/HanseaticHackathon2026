"""Endpunkte für die HTML-Oberfläche (web/index.html), Vertrag nach dem Entwurf der Oberfläche
aus dem Team (beschrieben in docs/api.md).

Übersetzt nur; die Abbildung auf unser Datenmodell steht in services/oberflaeche.py. Fehler
hier als {"fehler": "…"} (Vertrag), der Rest der API bleibt bei {"error": "…"}.

Wer entscheidet (M3): der angemeldete Benutzer (app/auth.py). Nur ohne Anmeldungspflicht:
„von“ oder „by“ im JSON bzw. Formular, sonst der Kopf „X-Anwender“, sonst „Oberfläche“.
"""

import io
import json

from flask import Blueprint, current_app, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from ..auth import anwender
from ..db import db
from ..models import Bom, BomItem, EmailDraft
from ..models.stammdaten import DOC_TYPES
from ..services import excel_export, hochladen, oberflaeche
from ..services.excel_import import ImportFehler
from ..services.oberflaeche import Fehler

bp = Blueprint("oberflaeche", __name__)
_fortgesetzt = False  # einmal je Prozess: unterbrochene Suchen wieder aufnehmen


@bp.before_app_request
def unterbrochene_suche_fortsetzen():
    global _fortgesetzt
    if _fortgesetzt:
        return
    _fortgesetzt = True
    try:
        oberflaeche.setze_fort()
    except Exception:  # z. B. Tabellen fehlen noch: die Anfrage selbst soll trotzdem laufen
        db.session.rollback()
        current_app.logger.exception("Unterbrochene Suchen nicht fortgesetzt")


@bp.errorhandler(Fehler)
def _fehler(e):
    return {"fehler": str(e)}, e.code


@bp.errorhandler(HTTPException)
def _http_fehler(e):
    texte = {404: "Das gibt es nicht mehr. Bitte laden Sie die Seite neu.",
             413: "Die Datei ist zu groß. Bitte wählen Sie eine Datei bis 30 MB."}
    return {"fehler": texte.get(e.code, "Das hat nicht geklappt. Bitte laden Sie die Seite neu "
                                        "und versuchen Sie es noch einmal.")}, e.code


DOKUMENTART_FALSCH = "Diese Dokumentart kennt der Agent nicht. Bitte wählen Sie eine aus der Liste."


def _hole(modell, id_):
    obj = db.session.get(modell, id_)
    if obj is None:
        raise Fehler("Diesen Eintrag gibt es nicht mehr. Bitte laden Sie die Seite neu.", 404)
    return obj


def _json():
    daten = request.get_json(silent=True)
    return daten if isinstance(daten, dict) else {}


def _von(daten=None):
    """Name des Menschen, der entscheidet: der angemeldete Benutzer (app/auth.py); nur ohne
    Anmeldung (LOGIN_PFLICHT aus) aus der Anfrage, ohne Angabe „Oberfläche“."""
    if name := anwender():
        return name
    daten = daten if daten is not None else _json()
    for wert in (daten.get("von"), daten.get("by"), request.headers.get("X-Anwender")):
        if isinstance(wert, str) and wert.strip():
            return wert.strip()[:200]
    return oberflaeche.ANWENDER


# Vorgänge


@bp.get("/api/vorgaenge")
def vorgaenge():
    return oberflaeche.vorgaenge()


@bp.post("/api/vorgaenge")
def vorgang_neu():
    datei = request.files.get("datei")
    if datei is None or not datei.filename:
        raise Fehler("Es wurde keine Datei mitgeschickt. Bitte wählen Sie die Excel-Datei mit der "
                     "Stückliste aus.")
    if not datei.filename.lower().endswith((".xlsx", ".xlsm")):
        raise Fehler("Das ist keine Excel-Datei. Bitte laden Sie die Stückliste als Excel-Datei "
                     "hoch, zum Beispiel „Stueckliste.xlsx“.")
    try:
        bom = oberflaeche.lege_an(datei.read(), datei.filename,
                                  (request.form.get("name") or "").strip() or None)
    except ImportFehler as e:
        raise Fehler(f"Die Excel-Datei ließ sich nicht einlesen. {e}") from None
    return {"id": bom.id}


@bp.get("/api/vorgaenge/<int:bom_id>")
def vorgang(bom_id):
    return oberflaeche.vorgang(_hole(Bom, bom_id))


@bp.post("/api/vorgaenge/<int:bom_id>/start")
def start(bom_id):
    """Optional {"sprachen": ["de", "en"]}: die Sprachen, die dieser Vorgang braucht."""
    bom = _hole(Bom, bom_id)
    daten = request.get_json(silent=True) or {}
    if "sprachen" in daten:
        oberflaeche.sprachen_waehlen(bom, daten["sprachen"], anwender() or daten.get("von"))
    oberflaeche.starte(bom)
    return {"ok": True}


@bp.get("/api/vorgaenge/<int:bom_id>/ereignisse")
def ereignisse(bom_id):
    _hole(Bom, bom_id)
    return oberflaeche.ereignisse(bom_id, request.args.get("seit", 0, type=int))


@bp.post("/api/vorgaenge/<int:bom_id>/maengel/senden")
def maengel_senden(bom_id):
    return oberflaeche.sende_maengelliste(_hole(Bom, bom_id), _von())


@bp.get("/api/vorgaenge/<int:bom_id>/mails")
def mails(bom_id):
    return oberflaeche.mails(_hole(Bom, bom_id))


@bp.get("/api/vorgaenge/<int:bom_id>/export")
def export(bom_id):
    # ?zwischenstand=1: unvollständige Mappe, deutlich markiert, nicht für den Kunden
    zwischen = (request.args.get("zwischenstand") or "").lower() in ("1", "true", "ja")
    inhalt, name = oberflaeche.export(_hole(Bom, bom_id), zwischen)
    return send_file(io.BytesIO(inhalt), mimetype=excel_export.XLSX, as_attachment=True,
                     download_name=name)


# Artikel


@bp.get("/api/artikel/<int:item_id>")
def artikel(item_id):
    """Ohne sortierte Schlüssel: die Felder eines Funds kommen in der Reihenfolge der Prüfung."""
    daten = oberflaeche.artikel(_hole(BomItem, item_id))
    return current_app.response_class(json.dumps(daten, ensure_ascii=False),
                                      mimetype="application/json")


@bp.post("/api/artikel/<int:item_id>/korrigieren")
def korrigieren(item_id):
    daten = _json()
    oberflaeche.korrigiere(_hole(BomItem, item_id), daten, _von(daten))
    return {"ok": True}


@bp.post("/api/artikel/<int:item_id>/freigeben")
def freigeben(item_id):
    item, daten = _hole(BomItem, item_id), _json()
    fund_id = daten.get("fund_id")
    if type(fund_id) is not int:
        raise Fehler("Bitte wählen Sie zuerst den Fund aus, den Sie freigeben möchten.")
    oberflaeche.freigeben(item, fund_id, _von(daten), daten.get("doc_type") or None)
    return {"ok": True}


@bp.post("/api/artikel/<int:item_id>/ablehnen")
def ablehnen(item_id):
    daten = _json()
    grund = str(daten.get("grund") or "").strip()[:500] or "Fund passt nicht zum Artikel"
    fund_id = daten.get("fund_id")
    oberflaeche.ablehnen(_hole(BomItem, item_id), grund, _von(daten),
                         fund_id if type(fund_id) is int else None)
    return {"ok": True}


@bp.post("/api/artikel/<int:item_id>/zertifikat")
def zertifikat(item_id):
    item = _hole(BomItem, item_id)
    request.max_content_length = hochladen.MAX_BYTES + 1024 * 1024
    try:
        form, datei = request.form, request.files.get("datei")
    except RequestEntityTooLarge:
        raise Fehler(hochladen.ZU_GROSS, 413) from None
    if datei is None or not datei.filename.lower().endswith(".pdf"):
        raise Fehler(hochladen.KEIN_PDF)
    herkunft = form.get("herkunft") or "manuell"
    if herkunft not in hochladen.QUELLEN:
        raise Fehler("Bitte geben Sie an, ob das Dokument vom Hersteller kommt oder von Ihnen.")
    doc_type = form.get("doc_type") or None
    if doc_type and doc_type not in DOC_TYPES:
        raise Fehler(DOKUMENTART_FALSCH)
    finding = oberflaeche.zertifikat(item, datei.read(), datei.filename, herkunft,
                                     _von(form.to_dict()), doc_type)
    return {"ok": True, "fund_id": finding.id}


# E-Mails


@bp.put("/api/mails/<int:mail_id>")
def mail_aendern(mail_id):
    entwurf = _hole(EmailDraft, mail_id)
    oberflaeche.bearbeite_mail(entwurf, _json())
    return oberflaeche.mail(entwurf)


@bp.post("/api/mails/<int:mail_id>/senden")
def mail_senden(mail_id):
    entwurf = _hole(EmailDraft, mail_id)
    oberflaeche.sende_mail(entwurf, _von())
    return oberflaeche.mail(entwurf)


@bp.post("/api/mails/<int:mail_id>/erinnern")
def mail_erinnern(mail_id):
    entwurf = _hole(EmailDraft, mail_id)
    oberflaeche.erinnere(entwurf, _von())
    return oberflaeche.mail(entwurf.original or entwurf)


# Dateien


@bp.get("/files/<path:pfad>")
def datei(pfad):
    """PDF aus ATEX_DOCS für die Vorschau; ?markiert=<Fund-id> mit Fundstelle und Belegen."""
    ziel, markiert = oberflaeche.datei(pfad, request.args.get("markiert", type=int))
    name = oberflaeche.lesbar(ziel.name)  # Name beim Speichern, „ä“ statt „#U00e4“
    if markiert is not None:
        return send_file(io.BytesIO(markiert), mimetype="application/pdf", download_name=name)
    return send_file(ziel, mimetype="application/pdf", download_name=name)


@bp.after_request
def _nicht_zwischenspeichern(antwort):
    """Die Oberfläche fragt laufend nach dem Stand: nichts zwischenspeichern."""
    if antwort.mimetype == "application/json":
        antwort.headers["Cache-Control"] = "no-store"
    return antwort
