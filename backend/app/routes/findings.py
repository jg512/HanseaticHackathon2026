"""Vorschläge ansehen, freigeben, ablehnen (M6, M3); Dokumente von Hand hochladen (M18, E6)."""

import io
from pathlib import Path

from flask import Blueprint, abort, current_app, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from ..db import db
from ..models import Bom, BomItem, Finding
from ..models.stammdaten import DOC_TYPES
from ..services import belege, freigabe, hochladen, pdf_text

bp = Blueprint("findings", __name__, url_prefix="/api")


@bp.get("/boms/<int:bom_id>/findings")
def liste(bom_id):
    """Positionen des Vorgangs mit Vorschlägen: Angaben der Position plus „findings“.

    Offene Vorschläge von vor der Belegprüfung (belege leer) werden dabei einmal geprüft.
    """
    bom = db.session.get(Bom, bom_id) or abort(404)
    alt = [(i, f) for i in bom.items for f in i.findings
           if f.status == "vorgeschlagen" and f.belege is None and f.file_path]
    for i, f in alt:
        belege.pruefe_vorschlag(f, i, current_app.config["ATEX_DOCS"])
    if alt:
        db.session.commit()
    return [dict(i.to_dict(), findings=[f.to_dict() for f in i.findings])
            for i in bom.items if i.findings]


DOKUMENTART_FALSCH = "Diese Dokumentart kennt der Agent nicht. Bitte wählen Sie eine aus der Liste."
KEINE_DATEI = "Es wurde keine Datei mitgeschickt. Bitte wählen Sie eine Datei aus."


@bp.post("/findings/<int:finding_id>/decision")
def entscheidung(finding_id):
    """Anfrage: {"decision": "freigeben" | "ablehnen", "by": "Name", "doc_type": optional}."""
    finding = db.session.get(Finding, finding_id) or abort(404)
    daten = request.get_json(silent=True)
    daten = daten if isinstance(daten, dict) else {}
    von = daten.get("by")
    if daten.get("decision") not in freigabe.ENTSCHEIDUNGEN or not isinstance(von, str) or not von.strip():
        return {"error": "Bitte geben Sie den Fund frei oder lehnen Sie ihn ab. Ohne Anmeldung "
                         "wird nichts gespeichert."}, 400
    doc_type = daten.get("doc_type") if daten["decision"] == "freigeben" else None
    if doc_type is not None and doc_type not in DOC_TYPES:
        return {"error": DOKUMENTART_FALSCH}, 400
    try:
        item_status = freigabe.entscheide(finding, daten["decision"], von.strip(),
                                          current_app.config["ATEX_DOCS"], doc_type)
    except freigabe.FreigabeFehler as e:
        db.session.rollback()
        return {"error": str(e)}, 409
    db.session.commit()
    return dict(finding.to_dict(), item_status=item_status)


@bp.post("/boms/<int:bom_id>/items/<int:item_id>/documents")
def dokument_hochladen(bom_id, item_id):
    """PDF zu einer Position. multipart: file, by (Name), source (manuell | hersteller),
    doc_type (bei „manuell“ Pflicht, bei „hersteller“ optional)."""
    item = db.session.get(BomItem, item_id)
    if item is None or item.bom_id != bom_id:
        abort(404)
    request.max_content_length = hochladen.MAX_BYTES + 1024 * 1024  # Platz für die Formularfelder
    try:
        form, datei = request.form, request.files.get("file")
    except RequestEntityTooLarge:
        return {"error": hochladen.ZU_GROSS}, 413
    von, quelle, doc_type = (form.get("by") or "").strip(), form.get("source"), form.get("doc_type")
    if not von:
        return {"error": "Ohne Anmeldung wird nichts gespeichert. Bitte melden Sie sich an."}, 400
    if quelle not in hochladen.QUELLEN:
        return {"error": "Bitte geben Sie an, ob das Dokument vom Hersteller kommt oder von "
                         "Ihnen."}, 400
    if quelle == "manuell" and not doc_type:
        return {"error": "Bitte wählen Sie aus, was für ein Dokument das ist."}, 400
    if doc_type and doc_type not in DOC_TYPES:
        return {"error": DOKUMENTART_FALSCH}, 400
    if datei is None or not datei.filename:
        return {"error": KEINE_DATEI}, 400
    try:
        finding = hochladen.lade_hoch(item, datei.read(), datei.filename, quelle, von,
                                      doc_type or None, Path(current_app.config["ATEX_DOCS"]))
    except hochladen.UploadFehler as e:
        db.session.rollback()
        return {"error": str(e)}, e.code
    except freigabe.FreigabeFehler as e:
        db.session.rollback()
        return {"error": str(e)}, 409
    db.session.commit()
    return dict(finding.to_dict(), item_status=item.status), 201


@bp.get("/findings/<int:finding_id>/file")
def datei(finding_id):
    """Die PDF des Vorschlags. Mit ?markiert=1 sind Fundstelle und Belege hervorgehoben."""
    finding = db.session.get(Finding, finding_id) or abort(404)
    pfad = freigabe.datei(finding, current_app.config["ATEX_DOCS"]) or abort(404)
    name = pdf_text.lesbar(pfad.name)  # Name beim Speichern, „ä“ statt „#U00e4“
    if request.args.get("markiert"):
        try:
            daten = freigabe.markierte_pdf(pfad, freigabe.stellen(finding))
        except RuntimeError:  # kaputtes oder verschlüsseltes PDF: dann ohne Markierung
            return send_file(pfad, mimetype="application/pdf", download_name=name)
        return send_file(io.BytesIO(daten), mimetype="application/pdf", download_name=name)
    return send_file(pfad, mimetype="application/pdf", download_name=name)


@bp.get("/findings/<int:finding_id>/page.png")
def seite(finding_id):
    """Seite der Fundstelle als Bild, der gefundene Begriff markiert, wenn er als Text dasteht.

    Optional ?seite=<Nr>&begriff=<Text>: eine andere Seite, z. B. die eines Belegs.
    """
    finding = db.session.get(Finding, finding_id) or abort(404)
    pfad = freigabe.datei(finding, current_app.config["ATEX_DOCS"]) or abort(404)
    nr = request.args.get("seite", type=int)
    begriff = request.args.get("begriff") if nr else finding.matched_term
    try:
        png = freigabe.seite_als_png(pfad, nr or finding.page, begriff)
    except RuntimeError:  # kaputtes oder verschlüsseltes PDF
        abort(404)
    return send_file(io.BytesIO(png), mimetype="image/png") if png else abort(404)
