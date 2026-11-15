"""Excel-Ausgabe, Liste der fehlenden Dokumente und EX-Bauteil-Liste (M7, M12, E1)."""

import io

from flask import Blueprint, abort, request, send_file

from ..db import db
from ..models import Bom
from ..services import excel_export, status

bp = Blueprint("export", __name__, url_prefix="/api/boms")


def _vorgang(bom_id):
    """Vorgang mit frisch gerechnetem Stand der Positionen."""
    bom = db.session.get(Bom, bom_id) or abort(404)
    status.aktualisiere_alle(bom)
    db.session.commit()
    return bom


def _datei(inhalt, name):
    return send_file(io.BytesIO(inhalt), mimetype=excel_export.XLSX, as_attachment=True,
                     download_name=name)


@bp.get("/<int:bom_id>/export")
def export(bom_id):
    """Kundenliste im festen Schema, nur wenn keine Position mehr offen ist (M12).

    ?zwischenstand=1: jederzeit, als unvollständig markiert, mit der Liste, was fehlt."""
    bom = _vorgang(bom_id)
    if (request.args.get("zwischenstand") or "").lower() in ("1", "true", "ja"):
        return _datei(excel_export.zwischenstand(bom),
                      excel_export.dateiname(bom, "Zwischenstand_Ex-Dokumente"))
    offen = excel_export.stand(bom)["open"]
    if offen:
        n = "1 Position ist" if len(offen) == 1 else f"{len(offen)} Positionen sind"
        return {"error": f"Die Kundenliste gibt es erst, wenn alle Positionen erledigt sind. "
                         f"Noch {n} offen.", "open": offen}, 409
    return _datei(excel_export.kundenliste(bom), excel_export.dateiname(bom, "Ex-Dokumente"))


@bp.get("/<int:bom_id>/missing")
def missing(bom_id):
    return excel_export.stand(_vorgang(bom_id))


@bp.get("/<int:bom_id>/missing.xlsx")
def missing_xlsx(bom_id):
    bom = _vorgang(bom_id)
    return _datei(excel_export.fehlliste(bom), excel_export.dateiname(bom, "Fehlliste"))


@bp.get("/<int:bom_id>/ex-liste.xlsx")
def ex_liste_xlsx(bom_id):
    """EX-Bauteil-Liste (E1) als Excel, immer abrufbar."""
    bom = _vorgang(bom_id)
    return _datei(excel_export.ex_bauteil_liste(bom),
                  excel_export.dateiname(bom, "EX-Bauteil-Liste"))
