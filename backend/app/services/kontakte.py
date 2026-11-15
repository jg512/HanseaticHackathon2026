"""Herstellerkontakte: welche E-Mail-Adresse gehört zu welchem Hersteller (Wunsch aus dem Team).

Ein Kontakt je Hersteller (Tabelle hersteller_kontakt, models/kontakt.py), von Hand in der
Anwendung gepflegt oder aus einer Excel- bzw. CSV-Datei übernommen. Regeln:

- Zugeordnet wird über den normalisierten Herstellernamen (freigabe.normalisiere), wie bei der
  Freigabe: „Pepperl + Fuchs“ = „Pepperl+Fuchs“. Nichts wird geraten: ein Name, den weder die
  Stammdaten noch eine Stückliste kennen, bleibt „unbekannt“.
- Hersteller, die bisher nur in einer Stückliste stehen, erscheinen in der Liste ohne
  hersteller_id. Wer ihnen eine Adresse gibt, legt den Hersteller in den Stammdaten an
  (Eintrag „angelegt“ im Änderungsprotokoll, wie bei der Freigabe).
- Jede Änderung trägt einen Namen (by) und steht mit altem und neuem Wert im Änderungsprotokoll
  stammdaten_aenderung (table_name „hersteller_kontakt“). Leere E-Mail entfernt die Adresse.
- Versand: Standard EMAIL_NUR_TEST: jede E-Mail an einen Hersteller geht an MAIL_TEST_ADDRESS,
  der Entwurf nennt die Adresse des Herstellers nur als eigentlichen Empfänger. Ohne gespeicherte
  Adresse wird die Anfrage markiert (kontakt_fehlt) und nicht gesendet (409).
- Wird eine Adresse gespeichert oder geändert, ziehen die offenen Entwürfe an diesen Hersteller
  nach (Empfänger, Hinweis der Position).

Texte aus der Oberfläche oder aus Dateien sind fremde Eingaben: getrimmt, in der Länge begrenzt,
nur gespeichert. Das Speichern (commit) macht der Aufrufer.
"""

import csv
import io
import re
from datetime import datetime, timezone

from flask import current_app
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font
from sqlalchemy.orm import contains_eager

from ..db import db
from ..models import BomItem, EmailDraft, Manufacturer, StammdatenAenderung
from ..models.kontakt import SPRACHEN, HerstellerKontakt
from . import lesepuffer, status
from .erledigt import Fehler, text, von
from .freigabe import normalisiere

EMAIL = re.compile(r"^[^@\s,;<>()\[\]\"']+@[^@\s,;<>()\[\]\"']+\.[A-Za-z0-9-]{2,}$")
MAX_EMAIL = 254
MAX_WERT = 500
TABELLE = "hersteller_kontakt"
# Feld der API -> Spalte der Tabelle
FELDER = {"email": "email", "ansprechpartner": "contact_person", "sprache": "language",
          "notiz": "note"}
# Spalten der Import- und Exportdatei; Kopf ohne Leer- und Satzzeichen, klein
KOPF_IMPORT = {"hersteller": "hersteller", "manufacturer": "hersteller", "firma": "hersteller",
               "unternehmen": "hersteller", "email": "email", "emailadresse": "email",
               "mail": "email", "mailadresse": "email", "ansprechpartner": "ansprechpartner",
               "kontaktperson": "ansprechpartner", "sprache": "sprache", "language": "sprache",
               "notiz": "notiz", "bemerkung": "notiz", "note": "notiz"}
KOPF_EXPORT = ["Hersteller", "E-Mail", "Ansprechpartner", "Sprache", "Notiz", "Geändert von",
               "Geändert am", "Kontakt fehlt", "In den Stammdaten"]
BREITE_EXPORT = [28, 34, 24, 9, 40, 18, 18, 13, 16]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ZEILEN = 5000
NICHTS = "Sie haben nichts geändert. Die gespeicherten Angaben bleiben, wie sie sind."


def _jetzt():
    return datetime.now(timezone.utc)


def _protokoll(name, m, feld, alt, neu, note=None):
    db.session.add(StammdatenAenderung(
        table_name=TABELLE, row_key=str(m.id), field=feld, old_value=alt or None,
        new_value=neu or None, changed_by=name, note=note))


# --- Zuordnung Position -> Hersteller -> Adresse ---------------------------------------------

def hersteller_zum_namen(name):
    """Hersteller der Stammdaten zum Namen (normalisiert), sonst None."""
    norm = normalisiere(name)
    if not norm:
        return None
    return db.session.scalar(db.select(Manufacturer).filter_by(normalized_name=norm))


def empfaenger(item):
    """Empfänger einer Anfrage zur Position.

    empfaenger_hersteller: gespeicherte Adresse oder None; kontakt_fehlt: keine Adresse;
    versand_an: wohin die E-Mail wirklich geht (EMAIL_NUR_TEST: die Test-Adresse), None ohne
    Adresse, denn dann wird nicht gesendet.
    """
    m, k = lesepuffer.hersteller_kontakt(item.manufacturer)  # in GET-Ansichten vorgeladen
    adresse = k.email if k else None
    cfg = current_app.config
    versand = None
    if adresse:
        versand = cfg["MAIL_TEST_ADDRESS"] if cfg.get("EMAIL_NUR_TEST", True) else adresse
    return {"hersteller_id": m.id if m else None, "hersteller": item.manufacturer,
            "empfaenger_hersteller": adresse, "kontakt_fehlt": not adresse,
            "versand_an": versand, "nur_test": bool(cfg.get("EMAIL_NUR_TEST", True)),
            "sprache": k.language if k else None}


def fehlt_text(item):
    """Text für Position, Entwurf und 409: wo die Adresse einzutragen ist."""
    return (f"Für den Hersteller {item.manufacturer} ist noch keine E-Mail-Adresse hinterlegt. "
            "Bitte tragen Sie die Adresse in den Stammdaten bei den Herstellerkontakten ein, "
            "dann können Sie die Anfrage senden.")


def kontakt_hinweis(item):
    """Hinweis zur Position, wenn ein ungesendeter Entwurf an einen Hersteller ohne Adresse
    wartet; sonst None. status.aktualisiere hängt ihn an status_note."""
    if not item.manufacturer or not any(e.status != "gesendet" for e in item.email_drafts):
        return None
    return fehlt_text(item) if empfaenger(item)["kontakt_fehlt"] else None


def _offene_entwuerfe():
    """{normalisierter Herstellername: [ungesendete Entwürfe]}."""
    entwuerfe = db.session.scalars(db.select(EmailDraft).join(BomItem)
                                   .where(EmailDraft.status != "gesendet")
                                   .options(contains_eager(EmailDraft.item)))  # nicht je Entwurf
    gruppen = {}
    for e in entwuerfe:
        gruppen.setdefault(normalisiere(e.item.manufacturer), []).append(e)
    return gruppen


def nachziehen(m):
    """Offene Entwürfe an diesen Hersteller übernehmen die neue Adresse; der Stand der
    Positionen (Hinweis „Adresse fehlt“) wird neu abgeleitet. Gibt die Zahl der Entwürfe zurück."""
    db.session.flush()
    entwuerfe = _offene_entwuerfe().get(m.normalized_name, [])
    test = current_app.config["MAIL_TEST_ADDRESS"]
    for e in entwuerfe:
        e.to_address = empfaenger(e.item)["versand_an"] or test
    for item in {e.item for e in entwuerfe}:
        status.aktualisiere(item)
    return len(entwuerfe)


# --- Ansichten -------------------------------------------------------------------------------

def _zeile(name, norm, m=None, k=None, offen=0):
    return {"hersteller_id": m.id if m else None, "hersteller": m.name if m else name,
            "normalized_name": norm, "email": k.email if k else None,
            "ansprechpartner": k.contact_person if k else None,
            "sprache": k.language if k else None, "notiz": k.note if k else None,
            "geaendert_von": k.changed_by if k else None,
            "geaendert_am": k.changed_at.isoformat() if k else None,
            "fehlt": not (k and k.email), "in_stammdaten": m is not None,
            "offene_entwuerfe": offen}


def kontakt_dict(m):
    k = db.session.get(HerstellerKontakt, m.id)
    offen = len(_offene_entwuerfe().get(m.normalized_name, []))
    return _zeile(m.name, m.normalized_name, m, k, offen)


def liste(q=None, fehlt=None):
    """Alle Hersteller der Stammdaten und die, die bisher nur in einer Stückliste stehen.

    q: Teil von Name, Adresse oder Ansprechpartner (ohne Groß/klein); fehlt: True nur ohne
    Adresse, False nur mit Adresse, None alle. Sortiert nach Name.
    """
    offen = {n: len(e) for n, e in _offene_entwuerfe().items()}
    kontakte = {k.manufacturer_id: k for k in db.session.scalars(db.select(HerstellerKontakt))}
    zeilen, bekannt = [], set()
    for m in db.session.scalars(db.select(Manufacturer)):
        bekannt.add(m.normalized_name)
        zeilen.append(_zeile(m.name, m.normalized_name, m, kontakte.get(m.id),
                             offen.get(m.normalized_name, 0)))
    namen = db.session.scalars(db.select(BomItem.manufacturer).distinct()
                               .where(BomItem.manufacturer.is_not(None)))
    for name in sorted(n.strip() for n in namen if n and n.strip()):
        norm = normalisiere(name)
        if norm and norm not in bekannt:
            bekannt.add(norm)
            zeilen.append(_zeile(name, norm, offen=offen.get(norm, 0)))
    if fehlt is not None:
        zeilen = [z for z in zeilen if z["fehlt"] == fehlt]
    if q and q.strip():
        such = q.strip().lower()
        zeilen = [z for z in zeilen if any(such in (z[f] or "").lower()
                                           for f in ("hersteller", "email", "ansprechpartner"))]
    return sorted(zeilen, key=lambda z: (z["hersteller"].lower(), z["normalized_name"]))


def anzahl_fehlend():
    """Zahl der Hersteller in der Liste ohne Adresse."""
    return len(liste(fehlt=True))


# --- Ändern ----------------------------------------------------------------------------------

def _email(wert):
    """Geprüfte Adresse oder None (leer = Adresse entfernen); Fehler 400 sonst."""
    if wert is not None and not isinstance(wert, str):
        raise Fehler("Die E-Mail-Adresse ist kein Text. Bitte geben Sie sie noch einmal ein.", 400)
    wert = (wert or "").strip()
    if not wert:
        return None
    if len(wert) > MAX_EMAIL or not EMAIL.match(wert):
        raise Fehler(f"„{wert[:80]}“ ist keine gültige E-Mail-Adresse. Bitte geben Sie genau "
                     "eine Adresse ein, zum Beispiel vorname.nachname@firma.de.", 400)
    return wert


def _sprache(wert):
    if wert is not None and not isinstance(wert, str):
        raise Fehler("Die Sprache ist kein Text. Bitte wählen Sie Deutsch oder Englisch.", 400)
    wert = (wert or "").strip().lower()
    if wert and wert not in SPRACHEN:
        raise Fehler("Als Sprache der Anfrage gehen nur Deutsch (de) oder Englisch (en). Bitte "
                     "wählen Sie eine davon oder lassen Sie das Feld leer.", 400)
    return wert or None


def _neue_werte(daten):
    """Geprüfte Werte der Felder, die in daten stehen."""
    neu = {}
    if "email" in daten:
        neu["email"] = _email(daten["email"])
    if "ansprechpartner" in daten:
        neu["ansprechpartner"] = text(daten, "ansprechpartner", "Ansprechpartner", pflicht=False,
                                      laenge=MAX_WERT)
    if "sprache" in daten:
        neu["sprache"] = _sprache(daten["sprache"])
    if "notiz" in daten:
        neu["notiz"] = text(daten, "notiz", "Notiz", pflicht=False, laenge=MAX_WERT)
    return neu


def _uebernimm(m, neu, name, note=None):
    """Werte übernehmen, je Feld mit Eintrag im Protokoll. Gibt die Zahl der Änderungen zurück."""
    k = db.session.get(HerstellerKontakt, m.id)
    if k is None:
        if not any(neu.values()):
            return 0
        k = HerstellerKontakt(manufacturer_id=m.id, changed_by=name)
        db.session.add(k)
    n = 0
    for feld, wert in neu.items():
        spalte = FELDER[feld]
        if wert != getattr(k, spalte):
            _protokoll(name, m, feld, getattr(k, spalte), wert, note)
            setattr(k, spalte, wert)
            n += 1
    if n:
        k.changed_by, k.changed_at = name, _jetzt()
    return n


def setze(m, daten):
    """PUT /api/kontakte/<id>: {"email", "ansprechpartner"?, "sprache"?, "notiz"?, "by"}.

    Fehlende Felder bleiben, leere werden geleert; leere E-Mail entfernt die Adresse.
    """
    if not isinstance(daten, dict):
        daten = {}
    name = von(daten)
    if unbekannt := sorted(set(daten) - set(FELDER) - {"by"}):
        raise Fehler(f"Diese Angabe kann hier nicht gespeichert werden: {', '.join(unbekannt)}. "
                     "Bitte senden Sie nur E-Mail, Ansprechpartner, Sprache und Notiz.", 400)
    if not _uebernimm(m, _neue_werte(daten), name):
        raise Fehler(NICHTS, 400)
    return kontakt_dict(m) | {"entwuerfe_aktualisiert": nachziehen(m)}


def _hersteller_anlegen(name, wer, note):
    norm = normalisiere(name)
    m = Manufacturer(name=name, normalized_name=norm)
    db.session.add(m)
    db.session.flush()
    db.session.add(StammdatenAenderung(table_name="manufacturer", row_key=str(m.id),
                                       field="angelegt", new_value=name, changed_by=wer,
                                       note=note))
    return m


def lege_an(daten):
    """POST /api/kontakte: {"hersteller", "email", …, "by"} für einen Hersteller, der vielleicht
    nur in einer Stückliste steht. Legt ihn dann in den Stammdaten an. Gibt (dict, neu) zurück."""
    if not isinstance(daten, dict):
        daten = {}
    name = von(daten)
    if unbekannt := sorted(set(daten) - set(FELDER) - {"by", "hersteller"}):
        raise Fehler(f"Diese Angabe kann hier nicht gespeichert werden: {', '.join(unbekannt)}. "
                     "Bitte senden Sie nur Hersteller, E-Mail, Ansprechpartner, Sprache und "
                     "Notiz.", 400)
    hersteller = text(daten, "hersteller", "Hersteller", laenge=MAX_WERT)
    if not normalisiere(hersteller):
        raise Fehler("Der Name des Herstellers ist leer. Bitte geben Sie ihn mit Buchstaben oder "
                     "Ziffern ein.", 400)
    neu_werte = _neue_werte(daten)  # erst prüfen, dann anlegen
    m = hersteller_zum_namen(hersteller)
    angelegt = m is None
    if angelegt:
        m = _hersteller_anlegen(hersteller, name, "Herstellerkontakt")
    if not _uebernimm(m, neu_werte, name) and not angelegt:
        raise Fehler(NICHTS, 400)
    return kontakt_dict(m) | {"entwuerfe_aktualisiert": nachziehen(m)}, angelegt


# --- Import und Export -----------------------------------------------------------------------

def _kopf(zelle):
    return KOPF_IMPORT.get(re.sub(r"[\W_]", "", str(zelle or "").lower()))


def _zeilen_xlsx(inhalt):
    try:
        wb = load_workbook(io.BytesIO(inhalt), read_only=True, data_only=True)
    except Exception as e:  # kaputte oder falsche Datei
        current_app.logger.warning("Kontaktdatei nicht lesbar: %s", e)
        raise Fehler("Die Excel-Datei ließ sich nicht öffnen. Bitte speichern Sie sie noch einmal "
                     "als .xlsx und laden Sie sie erneut hoch.", 400) from None
    return [list(z) for z in wb.worksheets[0].iter_rows(values_only=True)]


def _zeilen_csv(inhalt):
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            text_ = inhalt.decode(kodierung)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialekt = csv.Sniffer().sniff(text_[:4096], delimiters=";,\t")
    except csv.Error:
        dialekt = csv.excel
        dialekt.delimiter = ";"
    return [list(z) for z in csv.reader(io.StringIO(text_), dialekt)]


def _tabelle(inhalt, dateiname):
    """(Spalten {feld: index}, [(zeilennummer, zeile)]) ab der Kopfzeile mit „Hersteller“."""
    endung = (dateiname or "").lower().rsplit(".", 1)[-1]
    if endung in ("xlsx", "xlsm"):
        zeilen = _zeilen_xlsx(inhalt)
    elif endung == "csv":
        zeilen = _zeilen_csv(inhalt)
    else:
        raise Fehler("Diese Datei kann nicht gelesen werden. Bitte laden Sie eine Excel-Datei "
                     "(.xlsx) oder eine CSV-Datei hoch.", 400)
    for nr, zeile in enumerate(zeilen[:20]):
        spalten = {}
        for i, zelle in enumerate(zeile):
            feld = _kopf(zelle)
            if feld and feld not in spalten:
                spalten[feld] = i
        if "hersteller" in spalten:
            if "email" not in spalten:
                raise Fehler("In der Datei fehlt die Spalte „E-Mail“. Bitte ergänzen Sie sie in "
                             "der Kopfzeile neben „Hersteller“.", 400)
            rest = [(nr + 1 + i + 1, z) for i, z in enumerate(zeilen[nr + 1:])]
            if len(rest) > MAX_ZEILEN:
                raise Fehler(f"Die Datei hat mehr als {MAX_ZEILEN} Zeilen. Bitte teilen Sie sie "
                             "auf mehrere Dateien auf.", 400)
            return spalten, rest
    raise Fehler("In der Datei steht keine Kopfzeile mit „Hersteller“ und „E-Mail“. Bitte "
                 "schreiben Sie diese Überschriften über die Spalten.", 400)


def _wert(zeile, spalten, feld):
    i = spalten.get(feld)
    if i is None or i >= len(zeile) or zeile[i] is None:
        return None
    return str(zeile[i]).strip() or None


def importiere(inhalt, dateiname, daten):
    """Übernimmt Adressen aus einer Datei mit den Spalten Hersteller, E-Mail, Ansprechpartner,
    Sprache (dazu optional Notiz). Zugeordnet über den normalisierten Herstellernamen.

    Leere Zellen ändern nichts (der Import leert nie). Ein Name, den nur eine Stückliste kennt,
    legt den Hersteller in den Stammdaten an. Gibt den Bericht je Zeile zurück.
    """
    name = von(daten)
    spalten, zeilen = _tabelle(inhalt, dateiname)
    stueckliste = {normalisiere(n): n for n in db.session.scalars(
        db.select(BomItem.manufacturer).distinct().where(BomItem.manufacturer.is_not(None)))}
    bericht = {"datei": dateiname, "uebernommen": [], "unveraendert": [], "unbekannt": [],
               "ungueltig": [], "ohne_email": [], "angelegt": [], "entwuerfe_aktualisiert": 0}
    gesehen, geaendert = {}, []
    for nr, zeile in zeilen:
        werte = {f: _wert(zeile, spalten, f) for f in ("hersteller", *FELDER)}
        if not any(werte.values()):
            continue
        hersteller = werte.pop("hersteller")
        eintrag = {"zeile": nr, "hersteller": hersteller}
        norm = normalisiere(hersteller)
        if not norm:
            bericht["ungueltig"].append(eintrag | {"grund": "In dieser Zeile steht kein Hersteller."})
            continue
        if norm in gesehen:
            bericht["ungueltig"].append(eintrag | {
                "grund": f"Der Hersteller steht schon in Zeile {gesehen[norm]}. Diese Zeile "
                         "wurde nicht übernommen."})
            continue
        gesehen[norm] = nr
        if not werte["email"]:
            bericht["ohne_email"].append(eintrag)
            continue
        try:
            neu = _neue_werte({f: w for f, w in werte.items() if w is not None})
        except Fehler as e:
            bericht["ungueltig"].append(eintrag | {"grund": str(e)})
            continue
        m = hersteller_zum_namen(hersteller)
        if m is None and norm in stueckliste:
            m = _hersteller_anlegen(stueckliste[norm], name, f"Import {dateiname}, Zeile {nr}")
            bericht["angelegt"].append(m.name)
        if m is None:
            bericht["unbekannt"].append(eintrag | {
                "grund": "Diesen Hersteller kennen wir weder aus den Stammdaten noch aus einer "
                         "Stückliste. Bitte prüfen Sie die Schreibweise."})
            continue
        n = _uebernimm(m, neu, name, f"Import {dateiname}, Zeile {nr}")
        eintrag |= {"hersteller_id": m.id, "email": neu["email"]}
        bericht["uebernommen" if n else "unveraendert"].append(eintrag)
        if n:
            geaendert.append(m)
    bericht["entwuerfe_aktualisiert"] = sum(nachziehen(m) for m in geaendert)
    return bericht


def export():
    """Excel mit allen Zeilen der Liste, im Aufbau der Importdatei (wieder einlesbar)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Herstellerkontakte"
    ws.append(KOPF_EXPORT)
    for z in liste():
        ws.append([z["hersteller"], z["email"], z["ansprechpartner"], z["sprache"], z["notiz"],
                   z["geaendert_von"], z["geaendert_am"][:16].replace("T", " ")
                   if z["geaendert_am"] else None, "ja" if z["fehlt"] else "nein",
                   "ja" if z["in_stammdaten"] else "nein, bisher nur in einer Stückliste"])
    for zelle in ws[1]:
        zelle.font = Font(bold=True)
    for reihe in ws.iter_rows(min_row=2):
        for zelle in reihe:
            zelle.alignment = Alignment(vertical="top")
    for nr, breite in enumerate(BREITE_EXPORT, start=1):
        ws.column_dimensions[ws.cell(1, nr).column_letter].width = breite
    ws.freeze_panes = "A2"
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()
