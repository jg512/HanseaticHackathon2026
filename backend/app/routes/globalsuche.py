"""GET /api/suche?q=…&je=…: Suche über die ganze Anwendung (services/globalsuche.py).

Braucht wie alles eine Anmeldung (app/auth.py); nur lesend, also auch für die Rolle „leser“.
"""

import time

from flask import Blueprint, request

from ..services import globalsuche

bp = Blueprint("globalsuche", __name__)


@bp.get("/api/suche")
def suche():
    start = time.perf_counter()
    je = request.args.get("je", "")
    if je and not je.isdigit():
        return {"error": "Die Zahl der Treffer je Gruppe muss eine ganze Zahl sein."}, 400
    ergebnis = globalsuche.suche(request.args.get("q", ""), int(je) if je else None)
    ergebnis["dauer_ms"] = round((time.perf_counter() - start) * 1000)
    return ergebnis
