"""Korrekturschleife (E5): Mängelliste an den Einkauf, Korrektur, erneute Suche.

Fehlen Angaben in der Stückliste, stellt der Code die Mängel je Position zusammen. Daraus wird
ein Entwurf an den Einkauf; ein Mensch prüft, gibt frei und sendet, wie bei den E-Mails an die
Hersteller. Die Antwort des Einkaufs trägt ein Mensch je Position ein oder lädt die korrigierte
Stückliste hoch. Jede Änderung wird mit Name, Zeit, altem und neuem Wert gespeichert. Danach
werden nur die korrigierten Positionen neu gesucht, nicht der ganze Vorgang.

Das Speichern (commit) macht der Aufrufer.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from flask import current_app
from sqlalchemy.orm import selectinload

from ..db import db
from ..models.bom import BomItem, ItemCorrection, PurchasingEmail
from . import email_entwurf, status, suche
from .erledigt import Fehler, text, von
from .excel_import import lese_stueckliste

# Felder, die ein Mensch korrigieren darf, mit Namen für Meldungen und E-Mail
FELDER = {"description": "Bezeichnung", "manufacturer": "Hersteller",
          "manufacturer_article_no": "Hersteller-Artikelnr.", "profile_no": "PRO.FILE-Nr.",
          "location": "Einbauort", "quantity": "Menge"}
# Nach diesen Feldern richtet sich die Suche nicht: Vorschläge und Entwürfe bleiben stehen
OHNE_NEUE_SUCHE = {"description", "quantity"}
# Meldung der Eingangsprüfung -> Feld, dessen Korrektur sie erledigt
MELDUNG_FELD = {"profile_no_fehlt": "profile_no", "hersteller_fehlt": "manufacturer",
                "herstellerartikelnr_fehlt": "manufacturer_article_no",
                "einbauort_fehlt": "location"}
# Hat ein Mensch die Position schon entschieden, braucht der Einkauf nichts zu tun
NICHT_MELDEN = ("kein_ex_teil", "erledigt")
MAX_WERT = 200


def _jetzt():
    return datetime.now(timezone.utc)


def einkauf_adresse():
    cfg = current_app.config
    return cfg.get("EINKAUF_ADRESSE") or cfg["MAIL_TEST_ADDRESS"]


def maengel(bom):
    """Positionen mit fehlenden oder unklaren Angaben: Stand „klaerung“ oder offene Meldung.

    Je Position die Angaben und unter „missing“ die Texte, was fehlt.
    """
    offen = {}
    for issue in bom.issues:
        if not issue.resolved_at:
            offen.setdefault(issue.row_number, []).append(issue.message)
    liste = []
    for item in bom.items:
        if item.status in NICHT_MELDEN:
            continue
        texte = list(offen.get(item.row_number, []))
        if item.status == "klaerung" and not texte:
            texte.append(item.status_note or "Angaben unklar.")
        if texte:
            d = {f: getattr(item, f) or None for f in ("pos", "description", *FELDER)}
            d.update(bom_item_id=item.id, row_number=item.row_number, status=item.status,
                     missing=texte)
            liste.append(d)
    return liste


def _positionen(n):
    return "1 Position" if n == 1 else f"{n} Positionen"


def betreff(bom, liste):
    name = bom.machine or bom.source_file_name
    return f"Mängelliste Stückliste {name}: {_positionen(len(liste))}"


def text_der_liste(bom, liste):
    """Fester Text der Mängelliste. Alle Angaben setzt der Code ein, kein Sprachmodell."""
    def zeile(m):
        angaben = [f"Position {m['pos'] or '–'}", f"PRO.FILE-Nr. {m['profile_no'] or '–'}"]
        angaben += [m["description"]] if m["description"] else []
        angaben += [f"{m['manufacturer'] or 'Hersteller –'} {m['manufacturer_article_no'] or ''}"
                    .strip()]
        return f"- {', '.join(angaben)}\n  {' '.join(m['missing'])}"

    maschine = f" für {bom.machine}" if bom.machine else ""
    return (f"Guten Tag,\n\n"
            f"in der Stückliste „{bom.source_file_name}“{maschine} fehlen Angaben oder sind "
            "unklar. Ohne sie können wir die Ex-Dokumente nicht suchen. Bitte ergänzen oder "
            f"korrigieren Sie {'diese' if len(liste) > 1 else 'die'} {_positionen(len(liste))}:\n\n"
            + "\n".join(zeile(m) for m in liste) +
            "\n\nBitte antworten Sie mit den richtigen Angaben je Position oder senden Sie die "
            "korrigierte Stückliste. Wir tragen die Korrektur ein und suchen nur diese "
            "Positionen neu.\n\n"
            "Vielen Dank.\n\n"
            "Mit freundlichen Grüßen\n"
            "Dokumentation Ex-Bauteile\n")


def lege_maengelliste_an(bom):
    """Entwurf an den Einkauf mit den aktuellen Mängeln. Gibt (Entwurf, neu angelegt) zurück.

    Ein noch nicht freigegebener Entwurf wird neu aufgebaut (Änderungen am Text gehen verloren),
    es gibt also höchstens einen offenen. Fehler 409 ohne Mängel oder bei freigegebener,
    noch nicht gesendeter Liste.
    """
    liste = maengel(bom)
    if not liste:
        raise Fehler("Keine Mängel. Keiner Position fehlt eine Angabe, deshalb braucht es "
                     "keine Mängelliste.", 409)
    offen = next((m for m in bom.purchasing_emails if m.status != "gesendet"), None)
    if offen and offen.status == "freigegeben":
        raise Fehler(f"Die Mängelliste {offen.id} ist schon freigegeben, aber noch nicht "
                     "gesendet. Bitte senden Sie sie zuerst.", 409)
    werte = dict(to_address=einkauf_adresse(), subject=betreff(bom, liste),
                 body=text_der_liste(bom, liste))
    if offen:
        for feld, wert in werte.items():
            setattr(offen, feld, wert)
        return offen, False
    neu = PurchasingEmail(**werte)
    bom.purchasing_emails.append(neu)
    return neu, True


def sende_maengelliste(liste):
    """Schickt eine freigegebene Mängelliste an EINKAUF_ADRESSE (sonst die Test-Adresse). Wie bei
    den Herstellern gilt EMAIL_NUR_TEST: dann geht sie an MAIL_TEST_ADDRESS."""
    cfg = current_app.config
    adresse = cfg["MAIL_TEST_ADDRESS"] if cfg.get("EMAIL_NUR_TEST", True) else einkauf_adresse()
    email_entwurf.versende(adresse, liste.subject, liste.body)
    liste.to_address, liste.status, liste.sent_at = adresse, "gesendet", _jetzt()


def korrigiere(item, daten):
    """Anfrage {"by", "note"?, und mindestens eines der FELDER}. Gibt die neuen Korrekturen zurück.

    Gespeichert wird nur, was sich ändert; leer machen geht nicht. Passende Meldungen der
    Eingangsprüfung hakt der Code im Namen der Person ab. Die alte Suche gilt danach nicht mehr:
    unentschiedene Vorschläge und nicht freigegebene Entwürfe an den Hersteller fallen weg, die
    Position wartet auf die erneute Suche (suche_korrigierte).
    """
    name, notiz = von(daten), text(daten, "note", "Notiz", pflicht=False)
    neu = {}
    for feld, titel in FELDER.items():
        if feld in daten:
            wert = text(daten, feld, titel, laenge=MAX_WERT)
            if wert != (getattr(item, feld) or None):
                neu[feld] = wert
    if not neu:
        raise Fehler("Diese Werte stehen schon so in der Position. Es gab nichts zu ändern.",
                     400)
    jetzt = _jetzt()
    korrekturen = [ItemCorrection(field=feld, old_value=getattr(item, feld) or None,
                                  new_value=wert, changed_by=name, changed_at=jetzt, note=notiz)
                   for feld, wert in neu.items()]
    item.corrections.extend(korrekturen)
    for feld, wert in neu.items():
        setattr(item, feld, wert)
    for issue in item.bom.issues:
        if (issue.row_number == item.row_number and not issue.resolved_at
                and MELDUNG_FELD.get(issue.code) in neu):
            feld = MELDUNG_FELD[issue.code]
            issue.resolved_by, issue.resolved_at = name, jetzt
            issue.comment = f"{FELDER[feld]} ist korrigiert auf „{neu[feld]}“."
    if set(neu) - OHNE_NEUE_SUCHE:
        for f in [f for f in item.findings if f.status == "vorgeschlagen"]:
            item.findings.remove(f)
        for e in [e for e in item.email_drafts if e.status == "entwurf"]:
            item.email_drafts.remove(e)
        item.searched_at = item.searched_in = None
    status.aktualisiere(item)
    return korrekturen


def neu_zu_suchen(bom):
    """Korrigierte Positionen, die seit der Korrektur nicht gesucht wurden und suchbar sind."""
    return [i for i in bom.items if i.corrections and not i.searched_at
            and i.status not in suche.NICHT_SUCHEN]


def suche_korrigierte(bom, item_ids=None, internet_versuche=None):
    """Sucht nur korrigierte Positionen neu (services/suche.py). Gibt die gesuchten zurück.

    Ohne item_ids alle aus neu_zu_suchen. Mit item_ids nur korrigierte Positionen des Vorgangs,
    sonst Fehler 409. SuchFehler (Ordner fehlt) geht an den Aufrufer.
    """
    if item_ids is None:
        items = neu_zu_suchen(bom)
    else:
        items = [i for i in bom.items if i.id in set(item_ids)]
        falsch = set(item_ids) - {i.id for i in items if i.corrections}
        if falsch:
            raise Fehler("Neu suchen lassen sich hier nur korrigierte Positionen dieses "
                         "Vorgangs. Bitte laden Sie die Seite neu.", 409)
    if not items:
        raise Fehler("Keine korrigierte Position wartet auf die Suche. Korrigieren Sie zuerst "
                     "eine Position.", 409)
    # suche_vorgang braucht vom Vorgang nur die Positionen
    suche.suche_vorgang(SimpleNamespace(items=items), internet_versuche)
    return items


def korrigiere_aus_excel(bom, inhalt, dateiname, daten):
    """Korrigierte Stückliste desselben Vorgangs: Positionen über die Pos. zuordnen.

    Übernommen werden nur geänderte, nicht leere Werte der FELDER, je Position als Korrektur
    mit Namen und Dateiname. Neue Positionen und weitere Spalten bleiben unberührt. ImportFehler
    bei unlesbarer Datei, Fehler 400, wenn keine Pos. zum Vorgang passt.
    """
    name = von(daten)
    zeilen = {z["pos"]: z for z in lese_stueckliste(inhalt).items if z["pos"]}
    positionen = {i.pos for i in bom.items}
    if not set(zeilen) & positionen:
        raise Fehler("Keine Position der Datei passt zu diesem Vorgang. Bitte prüfen Sie, ob "
                     "Sie die richtige Stückliste gewählt haben.", 400)
    geaendert = []
    for item in bom.items:
        zeile = zeilen.get(item.pos)
        if zeile is None:
            continue
        werte = {f: zeile[f] for f in FELDER if zeile[f] and zeile[f] != (getattr(item, f) or "")}
        if werte:
            korrigiere(item, {"by": name, "note": f"Korrigierte Stückliste {dateiname}", **werte})
            geaendert.append(item)
    return {"changed": [i.to_dict() for i in geaendert],
            "unknown_pos": sorted(set(zeilen) - positionen),
            "missing_pos": sorted(p for p in positionen - set(zeilen) if p)}


def ansicht(bom):
    """Alles für die Seite Korrektur: Mängel, Mängellisten, Korrekturen, neu zu suchen."""
    # Korrekturen aller Positionen in einer Anfrage statt einer je Position
    db.session.scalars(db.select(BomItem).filter_by(bom_id=bom.id)
                       .options(selectinload(BomItem.corrections))).all()
    return {"defects": maengel(bom),
            "purchasing_emails": [m.to_dict() for m in reversed(bom.purchasing_emails)],
            "corrections": [k.to_dict() for i in bom.items for k in i.corrections],
            "to_search": [i.id for i in neu_zu_suchen(bom)]}
