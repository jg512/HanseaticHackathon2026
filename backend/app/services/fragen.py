"""Offene Fragen je Vorgang: anlegen, kommentieren, klären, wieder öffnen.

Ohne Einfluss auf Stand und Vollständigkeit. Eingaben prüft erledigt.text wie überall.
Das Speichern (commit) macht der Aufrufer.
"""

from datetime import datetime, timezone

from ..db import db
from ..models import BomItem, Question, QuestionComment
from .erledigt import Fehler, text, von


def liste(bom):
    """Offene zuerst, darin die älteste zuerst."""
    return sorted(bom.questions, key=lambda q: (q.status != "offen", q.id))


def lege_an(bom, daten):
    """Anfrage {"by", "text", "item_id"?}."""
    name, frage = von(daten), text(daten, "text", "Frage")
    item_id = daten.get("item_id")
    item = None
    if item_id is not None:
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            raise Fehler("Diese Position kennt der Agent nicht. Bitte laden Sie die Seite neu.",
                         400)
        item = db.session.get(BomItem, item_id)
        if item is None or item.bom_id != bom.id:
            raise Fehler("Diese Position gehört nicht zu diesem Vorgang. Bitte laden Sie die "
                         "Seite neu.", 400)
    q = Question(text=frage, created_by=name, item=item)
    bom.questions.append(q)
    return q


def kommentiere(frage, daten):
    """Anfrage {"by", "text"}; auch an geklärten Fragen möglich."""
    frage.comments.append(QuestionComment(text=text(daten, "text", "Kommentar"),
                                          created_by=von(daten)))


def klaere(frage, daten):
    """Anfrage {"by", "answer"}: Antwort ist Pflicht."""
    name, antwort = von(daten), text(daten, "answer", "Antwort")
    if frage.status == "geklaert":
        raise Fehler(f"{frage.resolved_by} hat diese Frage schon geklärt.", 409)
    frage.status, frage.answer = "geklaert", antwort
    frage.resolved_by, frage.resolved_at = name, datetime.now(timezone.utc)


def oeffne(frage, daten):
    """Anfrage {"by"}: wieder offen; die bisherige Antwort bleibt als Kommentar erhalten."""
    name = von(daten)
    if frage.status != "geklaert":
        raise Fehler("Diese Frage ist noch offen. Es gibt nichts wieder zu öffnen.", 409)
    frage.comments.append(QuestionComment(
        created_by=name, text=f"Die Frage ist wieder offen. Die bisherige Antwort von "
                              f"{frage.resolved_by} vom {frage.resolved_at:%d.%m.%Y} lautete: "
                              f"{frage.answer}"))
    frage.status = "offen"
    frage.answer = frage.resolved_by = frage.resolved_at = None
