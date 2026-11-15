"""Texterkennung (OCR) für Seiten ohne Textebene, im Hintergrund nach Import und Hochladen.

Eigene Tesseract-Installation im Image (wie im Backend: deu+eng+fra, 300 dpi über PyMuPDF), kein
Zugriff auf den Zwischenspeicher des Backends: der Server bleibt für sich lauffähig und liest
auch Scans, die nur hier hochgeladen werden. Begründung im README.

Ablauf: Import und Hochladen legen ein Dokument mit Seiten ohne Text mit Stand „offen“ an und
warten nie auf OCR. `flask --app wsgi ocr --dauerhaft` (startet der Container im Hintergrund)
liest offene Dokumente, legt den Text je Datei (SHA-256) im Zwischenspeicher ab und schickt das
Dokument durch dieselbe Auslesung und dieselben Regeln. Angaben von OCR-Seiten tragen quelle „ocr“,
von Hand gesetzte Angaben bleiben. Findet OCR nichts oder scheitert sie, bleibt das Dokument „nicht
lesbar“, der Grund steht in ocr_hinweis.
"""

import json
import os
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf
from flask import current_app

from . import ablage, auslesen, regeln
from .db import db
from .modelle import DOC_TYPE_NAMEN, Angabe, Dokument

DPI = 300  # wie im Backend
VON = "Texterkennung"
NEU_GELESEN = "Angaben neu gelesen"  # „von“ im Protokoll nach neu-auslesen
OFFEN = (None, "offen")  # Stände, die der Lauf bearbeitet (None: Dokument von vor der OCR)
# Texte für Anwender (ocr_hinweis); Einzelheiten zu Fehlern stehen nur im Log
VON_HAND = "Bitte tragen Sie die Angaben von Hand ein."
AUS = "Die Texterkennung ist ausgeschaltet. " + VON_HAND
NICHT_EINGERICHTET = "Die Texterkennung ist im Dokumentenarchiv nicht eingerichtet. " + VON_HAND
GESCHEITERT = "Die Texterkennung ist bei diesem Scan gescheitert. " + VON_HAND


@dataclass(frozen=True)
class Einstellung:
    cache: Path
    sprachen: str   # z. B. „deu+eng+fra“
    tessdata: str   # Ordner mit den Sprachdaten von Tesseract

    def datei(self, sha256):
        return datei(self.cache, sha256, self.sprachen)


def cache_ordner():
    return Path(current_app.config.get("OCR_CACHE") or Path(current_app.config["ABLAGE"]) / ".ocr")


def datei(cache, sha256, sprachen):
    return Path(cache) / f"{sha256}_{sprachen}_{DPI}.json"


@lru_cache
def tesseract(sprachen):
    """(Sprachdaten-Ordner, None), sonst (None, Grund), wenn Tesseract oder eine Sprache fehlt."""
    try:
        ordner = pymupdf.get_tessdata()
    except RuntimeError:
        return None, "Tesseract ist im Image nicht installiert, Scans bleiben ohne OCR."
    fehlt = [s for s in sprachen.split("+") if not Path(ordner, f"{s}.traineddata").is_file()]
    if fehlt:
        return None, f"Tesseract-Sprachdaten fehlen ({', '.join(fehlt)}), Scans bleiben ohne OCR."
    return ordner, None


def einstellung():
    """(Einstellung, None) oder (None, Satz für Anwender), wenn OCR aus ist oder Tesseract fehlt;
    der technische Grund geht ins Log."""
    if not current_app.config.get("OCR"):
        return None, AUS
    sprachen = current_app.config["OCR_SPRACHEN"]
    tessdata, fehler = tesseract(sprachen)
    if fehler:
        current_app.logger.warning(fehler)
        return None, NICHT_EINGERICHTET
    return Einstellung(cache_ordner(), sprachen, tessdata), None


def aus_cache(sha256):
    """{„Seite“: Text} aus dem Zwischenspeicher, None ohne passende, heile Datei."""
    pfad = datei(cache_ordner(), sha256, current_app.config["OCR_SPRACHEN"])
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        seiten = daten["seiten"]
        if daten["sha256"] != sha256 or daten["dpi"] != DPI \
                or not all(isinstance(t, str) for t in seiten.values()):
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return seiten


def _schreibe(pfad, daten):
    """Atomar: erst eine Zwischendatei, dann umbenennen."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=pfad.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.replace(tmp, pfad)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ocr_seite(seite, e):
    """Text einer Seite per Tesseract (Tests ersetzen diese Funktion)."""
    tp = seite.get_textpage_ocr(language=e.sprachen, dpi=DPI, full=True, tessdata=e.tessdata)
    return seite.get_text(textpage=tp)


def seiten_lesen(inhalt, nummern, e):
    """{„Seite“: Text} für die Seiten (ab 1) per OCR."""
    with pymupdf.open(stream=inhalt, filetype="pdf") as doc:
        return {str(nr): ocr_seite(doc[nr - 1], e).replace("\x00", "") for nr in nummern}


def stand_nach(a):
    return "fertig" if a.ocr_seiten else "kein_text"


def hinweis_nach(a):
    """Ergebnis in ganzen Sätzen für Anwender."""
    rest = [nr for nr in a.seiten_ohne_text if nr not in a.ocr_seiten]
    if not a.ocr_seiten:
        return "Die Texterkennung hat in diesem Scan keinen Text gefunden. " + VON_HAND
    text = (f"Der Text auf {seitenangabe(a.ocr_seiten)} stammt aus der Texterkennung eines Scans. "
            "Einzelne Werte können falsch sein, bitte prüfen Sie sie im PDF.")
    return text + (f" Auf {seitenangabe(rest)} wurde kein Text erkannt." if rest else "")


def seitenangabe(nummern):
    """[1, 2, 3, 5] -> „Seiten 1–3, 5“, [4] -> „Seite 4“."""
    teile = []
    for nr in sorted(nummern):
        if teile and nr == teile[-1][1] + 1:
            teile[-1][1] = nr
        else:
            teile.append([nr, nr])
    text = ", ".join(f"{a}–{b}" if b > a else str(a) for a, b in teile)
    return f"{'Seiten' if len(nummern) > 1 else 'Seite'} {text}"


def offene():
    """Dokumente mit Seiten ohne Textebene, die OCR noch nicht bearbeitet hat."""
    return [d for d in db.session.scalars(db.select(Dokument).order_by(Dokument.id))
            if d.ist_pdf and d.seiten_ohne_text and d.ocr_stand in OFFEN]


def _kennung(angaben):
    return sorted((x["art"], x["wert"], x["seite"] or 0, x["quelle"]) for x in angaben)


def _zusammenfassung(dok, anzahl):
    """Kurz fürs Änderungsprotokoll, z. B. „EU-Konformitätserklärung, 7 Angaben“."""
    if not dok.lesbar:
        return "Scan ohne lesbaren Text"
    return f"{DOC_TYPE_NAMEN.get(dok.doc_type, dok.doc_type)}, {anzahl} Angaben"


def uebernehmen(dok, a, von=VON, ocr_gelesen=True):
    """Neue Auswertung ins Dokument: Angaben aus dem PDF ersetzen, von Hand gesetzte Angaben
    (und die Felder dazu) bleiben. ocr_gelesen: a enthält das Ergebnis der Texterkennung, der
    OCR-Stand wird gesetzt. Gibt zurück, ob sich etwas geändert hat (dann im Protokoll)."""
    manuell = {x.art for x in dok.angaben if x.quelle == "manuell"}
    neu = [x for x in a.angaben if x["art"] not in manuell]
    bisher = [{"art": x.art, "wert": x.wert, "seite": x.seite, "quelle": x.quelle}
              for x in dok.angaben if x.quelle != "manuell"]
    if not ocr_gelesen and _kennung(neu) == _kennung(bisher) and dok.lesbar == a.lesbar:
        return False
    alter_typ = dok.doc_type
    vorher = _zusammenfassung(dok, len(bisher))
    dok.angaben[:] = [x for x in dok.angaben if x.quelle == "manuell"] + [Angabe(**x) for x in neu]
    if "dokumentart" not in manuell:
        dok.doc_type = a.doc_type
    if "ausstellungsdatum" not in manuell:
        dok.ausstellungsdatum = a.ausstellungsdatum
    if "gueltig_bis" not in manuell:
        dok.gueltig_bis = a.gueltig_bis
    dok.lesbar = a.lesbar
    if ocr_gelesen:
        dok.ocr_seiten = a.ocr_seiten or None
        dok.ocr_stand, dok.ocr_hinweis = stand_nach(a), hinweis_nach(a)
    ablage.protokoll("dokument", dok.id, "ocr" if ocr_gelesen else "auslesung", vorher,
                     _zusammenfassung(dok, len(neu)), von,
                     dok.ocr_hinweis if ocr_gelesen else "Das Dokument wurde neu ausgelesen.")
    if alter_typ == "unbekannt" and dok.doc_type != "unbekannt":
        _versionen_einordnen(dok)
    return True


def _versionen_einordnen(dok):
    """Versionen der Art „unbekannt“ (Scan im Import) bekommen die erkannte Art, wenn das Teil
    keine anderen Versionen dieser Art hat (oder nur Fassungen derselben Import-Version);
    sonst nur einen Hinweis, einordnen muss ein Mensch."""
    for v in dok.verknuepfungen:
        if v.doc_type != "unbekannt":
            continue
        andere = [x for x in v.teil.verknuepfungen if x.doc_type == dok.doc_type and x is not v]
        gleiche_version = all(x.quelle == "altbestand" and x.version == v.version
                              and x.dokument_id != dok.id for x in andere)
        if v.quelle == "altbestand" and gleiche_version:
            ablage.protokoll("verknuepfung", v.id, "doc_type", "unbekannt", dok.doc_type, VON,
                             "Die Texterkennung hat die Dokumentart erkannt.")
            v.doc_type = dok.doc_type
        else:
            art = DOC_TYPE_NAMEN.get(dok.doc_type, dok.doc_type)
            v.hinweis = " ".join(filter(None, (
                v.hinweis, f"Die Texterkennung hat das Dokument als {art} erkannt. Das Teil hat "
                           "schon Versionen dieser Art, bitte ordnen Sie es von Hand ein.")))


def lies_dokument(dok_id, e):
    """OCR für ein Dokument, Auswertung übernehmen, Regeln rechnen, speichern. Gibt den Stand
    zurück. Die langsame Erkennung läuft ohne offene Transaktion."""
    dok = db.session.get(Dokument, dok_id)
    pfad, sha256, nummern = ablage.datei(dok), dok.sha256, list(dok.seiten_ohne_text)
    db.session.rollback()
    inhalt = pfad.read_bytes()
    texte = aus_cache(sha256)
    if texte is None:
        texte = seiten_lesen(inhalt, nummern, e)
        _schreibe(e.datei(sha256), {"datei": pfad.name, "sha256": sha256,
                                    "sprachen": e.sprachen, "dpi": DPI, "seiten": texte})
    a = auslesen.auswerten(inhalt, texte)
    dok = db.session.get(Dokument, dok_id)
    if dok.ocr_stand not in OFFEN:  # inzwischen anders erledigt
        db.session.rollback()
        return dok.ocr_stand
    uebernehmen(dok, a)
    db.session.flush()
    regeln.pruefe([dok])
    db.session.commit()
    return dok.ocr_stand


def neu_auslesen(ausgabe=print):
    """Alle Dokumente mit der aktuellen Auslesung neu lesen (z. B. nach einer verbesserten Regel
    im Code), mit OCR-Text aus dem Zwischenspeicher; von Hand gesetzte Angaben bleiben. Rechnet
    danach die Regeln für die geänderten Dokumente. Gibt die Anzahl geänderter zurück."""
    geaendert = []
    for dok in db.session.scalars(db.select(Dokument).order_by(Dokument.id)).all():
        if not dok.ist_pdf:
            continue  # aus anderen Dateien liest das Archiv nichts
        try:
            inhalt = ablage.datei(dok).read_bytes()
        except OSError as fehler:
            ausgabe(f"{dok.dateiname}: Datei fehlt ({fehler}), übersprungen.")
            continue
        texte = aus_cache(dok.sha256) if dok.seiten_ohne_text else None
        if dok.seiten_ohne_text and texte is None and dok.ocr_stand not in OFFEN + ("fehler",):
            dok.ocr_stand = "offen"  # Ergebnis der OCR fehlt: der Hintergrundlauf liest neu
        if uebernehmen(dok, auslesen.auswerten(inhalt, texte), NEU_GELESEN, ocr_gelesen=False):
            geaendert.append(dok)
            ausgabe(f"{dok.dateiname}: Angaben geändert.")
    db.session.flush()
    regeln.pruefe(geaendert)
    return len(geaendert)


def runde(ausgabe=print):
    """Ein Durchgang über alle offenen Dokumente. Gibt die Anzahl bearbeiteter zurück."""
    liste = offene()
    if not liste:
        return 0
    e, grund = einstellung()
    if e is None:
        geaendert = [d for d in liste if d.ocr_hinweis != grund]
        for d in geaendert:
            d.ocr_stand, d.ocr_hinweis = "offen", grund
        db.session.commit()
        if geaendert:
            ausgabe(f"OCR wartet ({grund}): {len(liste)} Dokumente.")
        return 0
    ids = [(d.id, d.dateiname) for d in liste]
    db.session.rollback()
    for dok_id, name in ids:
        t = time.monotonic()
        try:
            stand = lies_dokument(dok_id, e)
        except Exception as fehler:  # noqa: BLE001  eine Datei darf scheitern, der Rest läuft
            db.session.rollback()
            ausgabe(f"OCR {name}: gescheitert ({fehler!r})")
            dok = db.session.get(Dokument, dok_id)
            dok.ocr_stand, dok.ocr_hinweis = "fehler", GESCHEITERT
            regeln.pruefe([dok])
            db.session.commit()
            stand = "fehler"
        ausgabe(f"OCR {name}: {stand} ({time.monotonic() - t:.0f} s)")
    return len(ids)


def _zeile(text):
    print(text, flush=True)  # sofort im Log, auch im Hintergrund


def lauf(dauerhaft=False, ausgabe=_zeile):
    """Offene Dokumente lesen; dauerhaft: danach alle OCR_INTERVALL Sekunden wieder nachsehen.
    Fehler (z. B. Datenbank kurz weg) beenden den Lauf nicht."""
    while True:
        try:
            anzahl = runde(ausgabe)
            if anzahl:
                ausgabe(f"OCR-Durchgang fertig: {anzahl} Dokumente.")
        except Exception as fehler:  # noqa: BLE001
            db.session.rollback()
            ausgabe(f"OCR-Durchgang gescheitert: {fehler}")
        if not dauerhaft:
            return
        time.sleep(current_app.config["OCR_INTERVALL"])
