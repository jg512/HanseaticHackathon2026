"""Angaben von Hand korrigieren: Teil und Dokument. Jede Änderung mit Name ins Protokoll.

Eine korrigierte Angabe ersetzt die aus dem PDF gelesenen derselben Art und steht als „manuell“
da, mit dem Namen im Hinweis und, wenn angegeben, der Seite. Danach rechnen die Regeln für das
Dokument neu. Bei Dateien, die kein PDF sind, sind alle Angaben von Hand (ohne Seite), Regeln
laufen für sie nicht. Der Quellennachweis (Felder quelle_*) ist ebenfalls korrigierbar (quellen.py).
"""

import re
from datetime import date

from . import ablage, auslesen, begriffe, quellen, regeln
from .db import db
from .modelle import DOC_TYPES, Angabe, Teil

# Feld in der Anfrage -> Art der Angabe; Listenfelder dürfen mehrere Werte haben
LISTEN = {"zertifikatsnummern": "zertifikatsnummer", "sprachen": "sprache",
          "richtlinien": "richtlinie", "normen": "norm"}
DATEN = {"ausstellungsdatum": "ausstellungsdatum", "gueltig_bis": "gueltig_bis"}
FELDER = ("doc_type", *DATEN, *LISTEN)
RICHTLINIEN = ("94/9/EG", "2014/34/EU")
NORM_WERT = re.compile(r"60079-\d{1,2}:(?:19|20)\d{2}")


class Eingabefehler(ValueError):
    """Ungültiger Wert in der Anfrage; die Meldung geht so an den Anwender."""


def dokument_werte(dok):
    """Aktuelle Werte der änderbaren Felder, wie in einer Anfrage."""
    werte = {"doc_type": dok.doc_type, "ausstellungsdatum": _iso(dok.ausstellungsdatum),
             "gueltig_bis": _iso(dok.gueltig_bis)}
    for feld, art in LISTEN.items():
        werte[feld] = [a.wert for a in dok.angaben if a.art == art]
    return werte


def _iso(tag):
    return tag.isoformat() if tag else None


def _pruefe(feld, wert):
    """Wert in einheitlicher Form, sonst Eingabefehler."""
    if feld == "doc_type":
        if wert not in DOC_TYPES:
            raise Eingabefehler("Diese Dokumentart kennt das Dokumentenarchiv nicht. Bitte "
                                "wählen Sie eine aus der Liste.")
        return wert
    name = begriffe.text(begriffe.FELD, feld)
    if feld in DATEN:
        if wert in (None, ""):
            return None
        try:
            return date.fromisoformat(str(wert)).isoformat()
        except ValueError:
            raise Eingabefehler(f"Das Datum bei „{name}“ ließ sich nicht lesen. Bitte geben Sie "
                                "es vollständig ein, zum Beispiel 2026-11-13.") from None
    if not isinstance(wert, list) or not all(isinstance(x, str) for x in wert):
        raise Eingabefehler(f"Die Eingabe bei „{name}“ ist nicht angekommen. Bitte laden Sie die "
                            "Seite neu und versuchen Sie es noch einmal.")
    werte = list(dict.fromkeys(x.strip() for x in wert if x.strip()))
    if feld == "sprachen":
        werte = [x.lower() for x in werte]
        if not all(re.fullmatch(r"[a-z]{2}", x) for x in werte):
            raise Eingabefehler("Bitte geben Sie die Sprachen als Kürzel aus zwei Buchstaben an, "
                                "zum Beispiel de, en oder fr.")
    if feld == "richtlinien" and not all(x in RICHTLINIEN for x in werte):
        raise Eingabefehler(f"Als Richtlinien sind nur {' und '.join(RICHTLINIEN)} möglich. "
                            "Bitte wählen Sie eine davon.")
    if feld == "normen" and not all(NORM_WERT.fullmatch(x) for x in werte):
        raise Eingabefehler("Bitte schreiben Sie jede Normausgabe wie 60079-0:2018, mehrere mit "
                            "Komma getrennt.")
    return werte


def aendere_dokument(dok, daten, von, grund=None):
    """Felder aus daten übernehmen; seiten: {Feld: Seite} als Beleg. Gibt die Änderungen zurück
    [(Feld, alt, neu)] und rechnet danach die Regeln für das Dokument neu."""
    seiten = daten.get("seiten") or {}
    if not isinstance(seiten, dict):
        raise Eingabefehler("Die Seitenangabe ist nicht angekommen. Bitte laden Sie die Seite "
                            "neu und versuchen Sie es noch einmal.")
    vorher = dokument_werte(dok)
    aenderungen = []
    for feld in FELDER:
        if feld not in daten:
            continue
        neu = _pruefe(feld, daten[feld])
        if neu == vorher[feld]:
            continue
        seite = seiten.get(feld)
        if seite is not None and (not dok.ist_pdf or not isinstance(seite, int)
                                  or not 1 <= seite <= max(dok.seiten, 1)):
            raise Eingabefehler(f"Das Dokument hat {dok.seiten} Seiten. Bitte geben Sie eine "
                                f"Seite zwischen 1 und {max(dok.seiten, 1)} an."
                                if dok.ist_pdf else
                                "Zu Dateien, die kein PDF sind, lässt sich keine Seite angeben.")
        art = "dokumentart" if feld == "doc_type" else DATEN.get(feld) or LISTEN[feld]
        dok.angaben[:] = [a for a in dok.angaben if a.art != art]
        hinweis = f"von Hand gesetzt von {von}" + (f": {grund}" if grund else "")
        for wert in (neu if isinstance(neu, list) else [neu] if neu else []):
            dok.angaben.append(Angabe(art=art, wert=wert, seite=seite, sicher=True,
                                      hinweis=hinweis, quelle="manuell"))
        if feld == "doc_type":
            dok.doc_type = neu
        elif feld in DATEN:
            setattr(dok, feld, date.fromisoformat(neu) if neu else None)
        ablage.protokoll("dokument", dok.id, feld, vorher[feld], neu, von, grund)
        aenderungen.append((feld, vorher[feld], neu))
    try:
        aenderungen += quellen.aendere(dok, daten, von, grund)
    except quellen.Eingabefehler as e:
        raise Eingabefehler(str(e)) from None
    if aenderungen:
        db.session.flush()
        regeln.pruefe([dok])
    return aenderungen


def aendere_teil(teil, daten, von, grund=None):
    """Hersteller, Artikelnr., PRO.FILE-Nr., Bezeichnung. Gibt [(Feld, alt, neu)] zurück."""
    aenderungen = []
    if "hersteller" in daten:
        name = (daten["hersteller"] or "").strip() if isinstance(daten["hersteller"], str) else ""
        if not name:
            raise Eingabefehler("Bitte geben Sie den Hersteller an.")
        gefunden = ablage.hersteller_suchen(name)
        hersteller = gefunden[0] if len(gefunden) == 1 else ablage.hersteller_holen(name)
        if hersteller.id != teil.hersteller_id:
            aenderungen.append(("hersteller", teil.hersteller.name, hersteller.name))
            teil.hersteller = hersteller
    if "artikelnr" in daten:
        artikelnr = daten["artikelnr"].strip() if isinstance(daten["artikelnr"], str) else ""
        if not auslesen.norm(artikelnr):
            raise Eingabefehler("Bitte geben Sie die Hersteller-Artikelnummer an.")
        if artikelnr != teil.artikelnr:
            aenderungen.append(("artikelnr", teil.artikelnr, artikelnr))
            teil.artikelnr, teil.artikelnr_norm = artikelnr, auslesen.norm(artikelnr)
    for feld in ("profile_no", "bezeichnung"):
        if feld in daten:
            wert = daten[feld].strip() if isinstance(daten[feld], str) else None
            if (wert or None) != getattr(teil, feld):
                aenderungen.append((feld, getattr(teil, feld), wert or None))
                setattr(teil, feld, wert or None)
    with db.session.no_autoflush:  # erst prüfen, dann schreiben
        doppelt = db.session.scalar(db.select(Teil).where(
            Teil.hersteller_id == teil.hersteller.id, Teil.artikelnr_norm == teil.artikelnr_norm,
            Teil.id != teil.id))
    if doppelt:
        raise ablage.AblageFehler(f"Das Teil {doppelt.artikelnr} gibt es bei diesem Hersteller "
                                  "schon. Bitte öffnen Sie das vorhandene Teil.")
    for feld, alt, neu in aenderungen:
        ablage.protokoll("teil", teil.id, feld, alt, neu, von, grund)
    return aenderungen
