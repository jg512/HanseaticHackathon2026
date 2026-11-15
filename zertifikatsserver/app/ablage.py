"""Ablage der Dateien und Versionen je Teil.

Dateien (PDF und die Formate aus dateiformate.py) liegen unter ABLAGE in aktuell/<Hersteller>/
oder archiv/<Hersteller>/. Ein Dokument geht ins Archiv, sobald keine aktuelle Version mehr darauf
zeigt. Gelöscht wird nie; die Links /api/dokumente/<id>/datei (und das ältere …/pdf) bleiben
gültig, auch nach dem Verschieben.
"""

import hashlib
import re
import shutil
from pathlib import Path

from flask import current_app

from . import auslesen, dateiformate
from .db import db
from .modelle import Aenderung, Angabe, Dokument, Hersteller, Teil, Verknuepfung, jetzt

AUSGEMUSTERT = "Dokument ausgemustert"  # alter Anfang von beendet_grund, wenn das Dokument ging
AUSGEMUSTERT_TEXT = "Das Dokument wurde ins Archiv gelegt"  # neuer Anfang, für Anwender


class AblageFehler(Exception):
    """Fachlicher Fehler, die Meldung geht so an den Anwender."""


def wurzel():
    return Path(current_app.config["ABLAGE"])


def ordnername(text):
    """Sicherer Ordner- oder Dateiname: keine Pfadtrenner, kein Punkt am Anfang."""
    return re.sub(r"[^\w+\-.,() äöüÄÖÜß\[\]]", "_", text).lstrip(".") or "_"


def hersteller_holen(name, ordner=None):
    n = auslesen.norm(name)
    h = db.session.scalar(db.select(Hersteller).filter_by(normalisiert=n))
    if h is None:
        h = Hersteller(name=name, normalisiert=n, ordner=ordner)
        db.session.add(h)
        db.session.flush()
    elif ordner and not h.ordner:
        h.ordner = ordner
    return h


def hersteller_suchen(text):
    """Hersteller zum Namen aus der Stückliste: gleich nach Name oder Ordnername, sonst einer
    beginnt mit dem anderen („Siemens AG“ und „Siemens“)."""
    n = auslesen.norm(text)
    if not n:
        return []
    alle = db.session.scalars(db.select(Hersteller).order_by(Hersteller.name)).all()
    gleich = [h for h in alle if n in (h.normalisiert, auslesen.norm(h.ordner))]
    if gleich:
        return gleich
    return [h for h in alle if len(n) >= 3 and len(h.normalisiert) >= 3
            and (n.startswith(h.normalisiert) or h.normalisiert.startswith(n))]


def _freier_pfad(bereich, hersteller, name, sha256, endung="pdf"):
    """Pfad in der Ablage; der Dateiname behält seine Endung (fehlt sie, kommt sie dazu)."""
    ordner = Path(bereich) / ordnername(hersteller.name)
    (wurzel() / ordner).mkdir(parents=True, exist_ok=True)
    name = ordnername(name)
    if not name.lower().endswith("." + endung):
        name += "." + endung
    stamm = name[:-(len(endung) + 1)]
    ziel = ordner / name
    if (wurzel() / ziel).exists():  # gleicher Name, anderer Inhalt: Hash anhängen
        ziel = ordner / f"{stamm}_{sha256[:8]}.{endung}"
    return ziel


def dokument_anlegen(inhalt, hersteller, name, herkunft, von, altpfade=(), quelle=None):
    """(Dokument, Auswertung, neu). Gleicher Hash: das vorhandene Dokument, nichts neu abgelegt.

    Erlaubt sind die Formate aus dateiformate.py (sonst AblageFehler mit Satz für Anwender). Nur
    aus PDFs liest die Auslesung Angaben; andere Dateien liegen nur da, ohne Angaben und ohne
    Prüfregeln (Auswertung None), Dokumentart „unbekannt“, bis jemand sie setzt.
    Seiten ohne Textebene liest die Texterkennung später im Hintergrund (Stand „offen“); liegt
    ihr Ergebnis schon im Zwischenspeicher (z. B. nach neuem Import), gilt es gleich.
    quelle: Quellennachweis {art, link, seite, abgerufen_am, von, dateiname, hinweis}."""
    from . import ocr, quellen
    try:
        fmt = dateiformate.pruefe(name, inhalt, current_app.config.get("MAX_CONTENT_LENGTH"))
    except dateiformate.Formatfehler as e:
        raise AblageFehler(str(e)) from None
    if fmt.familie != "pdf":
        return _andere_datei_anlegen(inhalt, hersteller, name, herkunft, von, altpfade, quelle,
                                     fmt)
    a = auslesen.auswerten(inhalt)
    dok = db.session.scalar(db.select(Dokument).filter_by(sha256=a.sha256))
    if dok is not None:
        dok.altpfade = list(dict.fromkeys([*dok.altpfade, *altpfade]))
        return dok, a, False
    ocr_stand = None
    if a.seiten_ohne_text:
        texte = ocr.aus_cache(a.sha256)
        if texte is not None:
            a = auslesen.auswerten(inhalt, texte)
        ocr_stand = ocr.stand_nach(a) if texte is not None else "offen"
    ziel = _freier_pfad("aktuell", hersteller, name, a.sha256)
    (wurzel() / ziel).write_bytes(inhalt)
    dok = Dokument(sha256=a.sha256, hersteller=hersteller, dateiname=name, pfad=ziel.as_posix(),
                   groesse=len(inhalt), seiten=len(a.seiten), lesbar=a.lesbar,
                   seiten_ohne_text=a.seiten_ohne_text, herkunft=herkunft,
                   altpfade=list(altpfade), doc_type=a.doc_type,
                   ausstellungsdatum=a.ausstellungsdatum, gueltig_bis=a.gueltig_bis,
                   angelegt_von=von, angaben=[Angabe(**x) for x in a.angaben],
                   ocr_stand=ocr_stand, ocr_seiten=a.ocr_seiten or None,
                   ocr_hinweis=ocr.hinweis_nach(a) if ocr_stand not in (None, "offen") else None,
                   dateiart="pdf")
    quellen.setze(dok, {"von": von, **(quelle or {})})
    db.session.add(dok)
    db.session.flush()
    return dok, a, True


def _andere_datei_anlegen(inhalt, hersteller, name, herkunft, von, altpfade, quelle, fmt):
    """Datei, aus der das Archiv nichts liest: nur ablegen, Angaben trägt ein Mensch ein."""
    from . import quellen
    sha256 = hashlib.sha256(inhalt).hexdigest()
    dok = db.session.scalar(db.select(Dokument).filter_by(sha256=sha256))
    if dok is not None:
        dok.altpfade = list(dict.fromkeys([*dok.altpfade, *altpfade]))
        return dok, None, False
    ziel = _freier_pfad("aktuell", hersteller, name, sha256, fmt.endung)
    (wurzel() / ziel).write_bytes(inhalt)
    dok = Dokument(sha256=sha256, hersteller=hersteller, dateiname=name, pfad=ziel.as_posix(),
                   groesse=len(inhalt), seiten=0, lesbar=False, seiten_ohne_text=[],
                   herkunft=herkunft, altpfade=list(altpfade), doc_type="unbekannt",
                   angelegt_von=von, dateiart=fmt.endung)
    quellen.setze(dok, {"von": von, **(quelle or {})})
    db.session.add(dok)
    db.session.flush()
    return dok, None, True


def datei(dok):
    return wurzel() / dok.pfad


def protokoll(objekt, objekt_id, feld, alt, neu, von, grund=None):
    db.session.add(Aenderung(objekt=objekt, objekt_id=objekt_id, feld=feld, alt=alt, neu=neu,
                             von=von, grund=grund or None))


def ins_aktuell(dok):
    """Zurück nach aktuell/<Hersteller>/, z. B. nach dem Wiederherstellen."""
    if dok.ablage == "aktuell":
        return
    ziel = _freier_pfad("aktuell", dok.hersteller, Path(dok.pfad).name, dok.sha256,
                         dok.format.endung)
    shutil.move(wurzel() / dok.pfad, wurzel() / ziel)
    dok.pfad = ziel.as_posix()


def _archiv_wenn_frei(dok):
    if not any(x.abgeloest_am is None for x in dok.verknuepfungen):
        ins_archiv(dok)


def ins_archiv(dok):
    """Datei nach archiv/<Hersteller>/ verschieben; der Link bleibt."""
    if dok.ablage == "archiv":
        return
    ziel = _freier_pfad("archiv", dok.hersteller, Path(dok.pfad).name, dok.sha256,
                         dok.format.endung)
    shutil.move(wurzel() / dok.pfad, wurzel() / ziel)
    dok.pfad = ziel.as_posix()


def teil_holen(hersteller, artikelnr, von, profile_no=None, bezeichnung=None):
    """Teil zu Hersteller und Typbezeichnung, neu angelegt, wenn es fehlt: (Teil, neu)."""
    n = auslesen.norm(artikelnr)
    if not n:
        raise AblageFehler("Bitte geben Sie die Hersteller-Artikelnummer an.")
    teil = db.session.scalar(db.select(Teil).filter_by(hersteller_id=hersteller.id,
                                                       artikelnr_norm=n))
    if teil is not None:
        return teil, False
    teil = Teil(hersteller=hersteller, artikelnr=artikelnr.strip(), artikelnr_norm=n,
                profile_no=profile_no or None, bezeichnung=bezeichnung or None, angelegt_von=von)
    db.session.add(teil)
    db.session.flush()
    return teil, True


def aktuelle(teil, doc_type):
    return [v for v in teil.verknuepfungen if v.doc_type == doc_type and v.abgeloest_am is None]


def _andere_version_aktuell(v):
    """Ist für Teil und Art eine andere Version aktuell? Fassungen derselben Version (z. B.
    Sprachen) stören nicht."""
    return any(x.version != v.version for x in aktuelle(v.teil, v.doc_type))


def verknuepfe(teil, dok, doc_type, von, quelle, beleg=None, hinweis=None, neue_version=True,
               quellennachweis=None):
    """Dokument als Version zum Teil. neue_version: löst die aktuelle Version derselben Art ab
    (deren Dokumente gehen ins Archiv, wenn sonst nichts Aktuelles darauf zeigt). Sonst (Import)
    kommt das Dokument zur aktuellen Version dazu, z. B. als weitere Sprachfassung."""
    if dok.ausgemustert_am:
        raise AblageFehler("Das Dokument liegt im Archiv und ist nicht mehr in Gebrauch. Bitte "
                           "nehmen Sie es zuerst wieder in Gebrauch.")
    vorher = aktuelle(teil, doc_type)
    if any(v.dokument_id == dok.id for v in vorher):
        raise AblageFehler("Dieses Dokument ist schon die aktuelle Version für das Teil.")
    if any(v.dokument_id == dok.id and v.doc_type == doc_type for v in teil.verknuepfungen):
        raise AblageFehler("Dieses Dokument war schon einmal eine Version für das Teil. Sie "
                           "können diese Version unter „Versionen“ wieder aktuell machen.")
    hoechste = max((v.version for v in teil.verknuepfungen if v.doc_type == doc_type), default=0)
    version = hoechste + 1 if neue_version or not vorher else vorher[0].version
    neu = Verknuepfung(teil=teil, dokument=dok, doc_type=doc_type, version=version, von=von,
                       quelle=quelle, beleg=beleg, hinweis=hinweis,
                       quellennachweis=quellennachweis or None)
    db.session.add(neu)
    db.session.flush()
    ins_aktuell(dok)
    if neue_version:
        for v in vorher:
            v.abgeloest_am = jetzt()
            v.abgeloest_durch = neu
        db.session.flush()
        for v in vorher:
            _archiv_wenn_frei(v.dokument)
    if quelle != "altbestand":
        protokoll("verknuepfung", neu.id, "angelegt", None,
                  f"Version {version} von {dok.dateiname} zu {teil.artikelnr}", von, hinweis)
    return neu


def beende(v, von, grund):
    """Version von Hand beenden, ohne Nachfolger. Das Dokument geht ins Archiv, wenn nichts
    Aktuelles mehr darauf zeigt; der Link bleibt."""
    if v.abgeloest_am is not None:
        raise AblageFehler("Diese Version gilt schon nicht mehr. Sie wurde beendet oder durch "
                           "eine neuere ersetzt.")
    v.abgeloest_am, v.beendet_von, v.beendet_grund = jetzt(), von, grund
    db.session.flush()
    _archiv_wenn_frei(v.dokument)
    protokoll("verknuepfung", v.id, "stand", "aktuell", "beendet", von, grund)


def verknuepfung_wiederherstellen(v, von, grund=None):
    """Beendete oder abgelöste Version wieder aktuell machen, wenn für Teil und Dokumentart
    gerade keine andere Version aktuell ist."""
    if v.abgeloest_am is None:
        raise AblageFehler("Diese Version ist schon aktuell.")
    if v.dokument.ausgemustert_am:
        raise AblageFehler("Das Dokument liegt im Archiv und ist nicht mehr in Gebrauch. Bitte "
                           "nehmen Sie zuerst das Dokument wieder in Gebrauch.")
    if _andere_version_aktuell(v):
        raise AblageFehler("Das Teil hat für diese Dokumentart schon eine andere aktuelle "
                           "Version. Bitte beenden Sie zuerst diese Version.")
    alt = v.stand
    v.abgeloest_am = v.abgeloest_durch_id = v.beendet_von = v.beendet_grund = None
    ins_aktuell(v.dokument)
    protokoll("verknuepfung", v.id, "stand", alt, "aktuell", von, grund)


def umhaengen(v, ziel, von, grund):
    """Dokument dieser Version einem anderen Teil zuordnen: dort neue Version, hier beendet."""
    if v.abgeloest_am is not None:
        raise AblageFehler("Nur eine aktuelle Version lässt sich einem anderen Teil zuordnen.")
    if ziel.id == v.teil_id:
        raise AblageFehler("Das Dokument gehört schon zu diesem Teil. Bitte wählen Sie ein "
                           "anderes Teil.")
    neu = verknuepfe(ziel, v.dokument, v.doc_type, von, "manuell",
                     hinweis=f"Umgehängt, vorher beim Teil {v.teil.artikelnr}.")
    beende(v, von, f"Umgehängt zum Teil {ziel.artikelnr}."
                   + (f" {grund}" if grund else ""))
    return neu


def ausmustern(dok, von, grund):
    """„Löschen“: Dokument ausmustern. Aktuelle Versionen enden, die Datei geht ins Archiv,
    der Link bleibt, alles lässt sich wiederherstellen."""
    if dok.ausgemustert_am:
        raise AblageFehler("Das Dokument liegt schon im Archiv und ist nicht mehr in Gebrauch.")
    dok.ausgemustert_am, dok.ausgemustert_von, dok.ausgemustert_grund = jetzt(), von, grund
    for v in dok.verknuepfungen:
        if v.abgeloest_am is None:
            v.abgeloest_am, v.beendet_von = dok.ausgemustert_am, von
            v.beendet_grund = f"{AUSGEMUSTERT_TEXT}. {grund}"
    ins_archiv(dok)
    protokoll("dokument", dok.id, "ausgemustert", None, grund, von, grund)


def dokument_wiederherstellen(dok, von, grund=None):
    """Ausmustern rückgängig. Versionen, die dabei endeten, werden wieder aktuell, wenn das Teil
    inzwischen keine andere Version dieser Art aktuell hat. Gibt diese Versionen zurück."""
    if not dok.ausgemustert_am:
        raise AblageFehler("Das Dokument ist schon in Gebrauch.")
    am = dok.ausgemustert_am
    dok.ausgemustert_am = dok.ausgemustert_von = dok.ausgemustert_grund = None
    zurueck = []
    for v in dok.verknuepfungen:
        if (v.abgeloest_am == am
                and (v.beendet_grund or "").startswith((AUSGEMUSTERT, AUSGEMUSTERT_TEXT))
                and not _andere_version_aktuell(v)):
            v.abgeloest_am = v.beendet_von = v.beendet_grund = None
            zurueck.append(v)
    if zurueck or not dok.verknuepfungen:
        ins_aktuell(dok)
    protokoll("dokument", dok.id, "ausgemustert", "ja", "wiederhergestellt", von, grund)
    return zurueck
