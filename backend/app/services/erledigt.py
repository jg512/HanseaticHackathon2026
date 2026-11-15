"""Ein Mensch erledigt von Hand: offene Positionen und Meldungen der Eingangsprüfung.

Eine von Hand erledigte Position hält die Kundenliste nicht mehr auf, obwohl Pflichtdokumente
fehlen können (bewusste Abweichung von M20). Den Stand leitet status.aktualisiere aus
resolved_at ab. Jede Position wird einzeln entschieden, auch bei gleicher PRO.FILE-Nr.
Meldungen abhaken ändert weder Stand noch Vollständigkeit. Ob eine Position überhaupt ein Ex-Teil
ist, entscheidet ebenfalls ein Mensch (entscheide_ex_teil, EX-Bauteil-Liste E1).

Texte aus der Oberfläche sind fremde Eingaben: getrimmt, in der Länge begrenzt, nur gespeichert.
Das Speichern (commit) macht der Aufrufer.
"""

from datetime import datetime, timezone

from flask import current_app

from . import status

MAX_NAME = 200
MAX_TEXT = 2000
# Wer schon vollständig oder kein Ex-Teil ist, braucht keine Ausnahme
NICHT_ERLEDIGBAR = ("vorhanden", "kein_ex_teil")


class Fehler(Exception):
    """Eingabe oder Zustand passt nicht; der Text geht an den Menschen, code ist der HTTP-Status."""

    def __init__(self, text, code):
        super().__init__(text)
        self.code = code


OHNE_NAME = "Ohne Anmeldung wird nichts gespeichert. Bitte melden Sie sich an."


def text(daten, feld, name, pflicht=True, laenge=MAX_TEXT):
    """Getrimmter Text aus der Anfrage oder None; Fehler 400 bei fehlender Pflicht oder Überlänge."""
    wert = daten.get(feld) if isinstance(daten, dict) else None
    if wert is not None and not isinstance(wert, str):
        raise Fehler(f"„{name}“ ist kein Text. Bitte geben Sie es noch einmal ein.", 400)
    wert = (wert or "").strip()
    if pflicht and not wert:
        raise Fehler(OHNE_NAME if feld == "by" else f"Bitte füllen Sie „{name}“ aus. Ohne diese "
                     "Angabe wird nichts gespeichert.", 400)
    if len(wert) > laenge:
        raise Fehler(f"„{name}“ ist zu lang. Bitte kürzen Sie es auf höchstens {laenge} "
                     "Zeichen.", 400)
    return wert or None


def von(daten):
    return text(daten, "by", "Name", laenge=MAX_NAME)


def _jetzt():
    return datetime.now(timezone.utc)


def erledige_position(item, daten):
    """Anfrage {"by", "note"}: Position von Hand erledigen."""
    name, begruendung = von(daten), text(daten, "note", "Begründung")
    status.aktualisiere(item)
    if item.resolved_at:
        raise Fehler(f"{item.resolved_by} hat diese Position schon von Hand erledigt.", 409)
    if item.status in NICHT_ERLEDIGBAR:
        raise Fehler("Diese Position ist schon vollständig oder kein Ex-Teil. Sie braucht keine "
                     "Ausnahme.", 409)
    item.resolved_by, item.resolved_at, item.resolution_note = name, _jetzt(), begruendung
    status.aktualisiere(item)


def oeffne_position(item, daten):
    """Anfrage {"by"}: von Hand erledigt zurücknehmen, der Stand folgt wieder den Daten."""
    name = von(daten)
    if not item.resolved_at:
        raise Fehler("Diese Position ist nicht von Hand erledigt. Es gibt nichts "
                     "zurückzunehmen.", 409)
    current_app.logger.info("Position %s wieder offen, von %s (war erledigt von %s: %s)",
                            item.id, name, item.resolved_by, item.resolution_note)
    item.resolved_by = item.resolved_at = item.resolution_note = None
    status.aktualisiere(item)


def hake_ab(issue, daten):
    """Anfrage {"by", "comment"?}: Meldung abhaken."""
    name, kommentar = von(daten), text(daten, "comment", "Kommentar", pflicht=False)
    if issue.resolved_at:
        raise Fehler(f"{issue.resolved_by} hat diese Meldung schon abgehakt.", 409)
    issue.resolved_by, issue.resolved_at, issue.comment = name, _jetzt(), kommentar


def oeffne_meldung(issue, daten):
    """Anfrage {"by"}: Haken zurücknehmen."""
    name = von(daten)
    if not issue.resolved_at:
        raise Fehler("Diese Meldung ist nicht abgehakt. Es gibt keinen Haken zum "
                     "Zurücknehmen.", 409)
    current_app.logger.info("Meldung %s wieder offen, von %s (war abgehakt von %s)",
                            issue.id, name, issue.resolved_by)
    issue.resolved_by = issue.resolved_at = issue.comment = None


def entscheide_ex_teil(item, daten):
    """Anfrage {"ex_teil": true | false | null, "by"}: Ex-Teil oder nicht, die letzte gilt.

    false: Stand „kein_ex_teil“, die Position braucht keine Dokumente.
    true: Ex-Teil bestätigt, auch wenn die Regeln unsicher sind; gesucht wird wie sonst.
    null: Entscheidung zurücknehmen, es gilt wieder der Vorschlag der Regeln.
    """
    wert = daten.get("ex_teil", "") if isinstance(daten, dict) else ""
    if not (wert is None or isinstance(wert, bool)):
        raise Fehler("Bitte entscheiden Sie, ob die Position ein Ex-Teil ist oder nicht.", 400)
    name = von(daten)
    current_app.logger.info("Position %s: Ex-Teil %s, von %s", item.id, wert, name)
    item.ex_confirmed_by = item.ex_confirmed_at = None
    if wert is False:
        item.status, item.status_note = "kein_ex_teil", (f"{name} hat entschieden, dass die "
                                                          "Position kein Ex-Teil ist.")
        return
    if wert:
        item.ex_confirmed_by, item.ex_confirmed_at = name, _jetzt()
    item.status = "offen"  # „kein_ex_teil“ verlassen, der Stand folgt wieder den Daten
    status.aktualisiere(item)
