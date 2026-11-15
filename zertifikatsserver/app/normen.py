"""Tabelle der aktuellen Normausgaben (norm_ausgabe): Vorgaben, Änderungen, Bestätigung.

Die Regel „norm_ausgabe“ (regeln.py) vergleicht die im PDF genannten Ausgaben mit dieser Tabelle.
Im Feld jahr steht das Jahr der IEC-Ausgabe, die früheste Jahreszahl einer Ausgabe: IEC
60079-0:2017 und EN IEC 60079-0:2018 sind dieselbe Ausgabe, mit 2017 gelten beide als aktuell.

Die Vorgaben unten sind die neueste Ausgabe, die das Team sicher kennt und für harmonisiert hält,
ohne Recherche im Internet. Gibt es eine neuere, schlägt die Regel zu selten an, nicht zu oft.
Jede Vorgabe ist „zu bestätigen“, bis ein benannter Mensch sie bestätigt oder ändert.
"""

import re

from flask import current_app

from .config import paare
from .db import db
from .modelle import Aenderung, NormAusgabe, jetzt

VORGABE_VON = "Vorgabe des Teams"
VORGABE_HINWEIS = ("Diese Ausgaben sind eine Vorgabe des Teams. Bitte prüfen Sie sie gegen die "
                   "aktuelle Liste der harmonisierten Normen zur Richtlinie 2014/34/EU und "
                   "bestätigen Sie jede Zeile.")
OBJEKT = "norm_ausgabe"  # im Änderungsprotokoll, objekt_id 0 (Schlüssel ist die Norm im Feld)
KONFIGURATION_HINWEIS = "Aus der Grundeinstellung des Dokumentenarchivs übernommen."
# frühere Texte des Systems, die der Start durch den aktuellen ersetzt
ALTE_KONFIGURATION_HINWEISE = ("Startwert aus AKTUELLE_NORMEN",
                               "Aus der Grundeinstellung des Zertifikatsservers übernommen.")

# Norm, Jahr der IEC-Ausgabe, Hinweis (steht in der Oberfläche in der Tabelle). Nur Normen, die
# die Auslesung in den 82 Dokumenten des Altbestands findet und deren Ausgabe das Team sicher
# kennt (Zählung im README).
VORGABEN = (
    ("60079-0", 2017, "Ausgabe 7 von 2017, in Europa als EN IEC 60079-0:2018 erschienen."),
    ("60079-1", 2014, "Ausgabe 7 von 2014, in Europa als EN 60079-1:2014 erschienen."),
    ("60079-7", 2015, "Ausgabe 5 von 2015, in Europa als EN 60079-7:2015 erschienen. Die "
                      "Änderung A1 von 2018 ist keine neue Ausgabe."),
    ("60079-11", 2011, "Ausgabe 6 von 2011, in Europa als EN 60079-11:2012 erschienen. Ausgabe 7 "
                       "von 2023 gibt es schon. Tragen Sie 2023 ein, sobald sie harmonisiert ist."),
    ("60079-15", 2010, "Ausgabe 4 von 2010, in Europa als EN 60079-15:2010 erschienen. Ausgabe 5 "
                       "von 2017 gibt es nach unserem Wissen schon. Tragen Sie 2017 ein, sobald "
                       "sie harmonisiert ist."),
    ("60079-18", 2014, "Ausgabe 4 von 2014, in Europa als EN 60079-18:2015 erschienen."),
    ("60079-26", 2014, "Ausgabe 3 von 2014, in Europa als EN 60079-26:2015 erschienen."),
    ("60079-28", 2015, "Ausgabe 2 von 2015, in Europa als EN 60079-28:2015 erschienen."),
    ("60079-31", 2013, "Ausgabe 2 von 2013, in Europa als EN 60079-31:2014 erschienen."),
)

NAME = re.compile(r"(?:(?:DIN|EN|IEC|ISO)[\s/]+)*60079\s*-\s*(\d{1,2})", re.I)


LISTE_FEHLT = ("Die Tabelle der Normen ist nicht vollständig angekommen. Bitte laden Sie die Seite "
               "neu und versuchen Sie es noch einmal.")


class Eingabefehler(ValueError):
    """Ungültige Tabelle in der Anfrage; die Meldung geht so an den Anwender."""


def name(text):
    """Einheitlicher Name der Norm wie in den Angaben: „EN IEC 60079-07“ -> „60079-7“; sonst None."""
    m = NAME.fullmatch((text or "").strip()) if isinstance(text, str) else None
    return f"60079-{int(m.group(1))}" if m else None


def jahr_gueltig(jahr):
    return isinstance(jahr, int) and not isinstance(jahr, bool) and 1900 <= jahr <= 2100


def _protokoll(norm, alt, neu, von, grund=None):
    db.session.add(Aenderung(objekt=OBJEKT, objekt_id=0, feld=norm, alt=alt, neu=neu, von=von,
                             grund=grund))


def _werte(n):
    return {"jahr": n.jahr, "hinweis": n.hinweis}


def alte_zeilen_einordnen():
    """Zeilen von vor den Vorgaben (ohne quelle): aus AKTUELLE_NORMEN = zu bestätigen, von einem
    Menschen per PUT gesetzt = von ihm bestätigt."""
    for n in db.session.scalars(db.select(NormAusgabe).where(
            NormAusgabe.hinweis.in_(ALTE_KONFIGURATION_HINWEISE))):
        n.hinweis = KONFIGURATION_HINWEIS  # alter Text des Systems, kein Eintrag eines Menschen
    for n in db.session.scalars(db.select(NormAusgabe).where(NormAusgabe.quelle.is_(None))):
        if n.geaendert_von == "Konfiguration":
            n.quelle = "konfiguration"
        else:
            n.quelle = "manuell"
            n.bestaetigt_von, n.bestaetigt_am = n.geaendert_von, n.geaendert_am


def vorgaben_eintragen(aktuelle_normen=""):
    """Fehlende Normen eintragen: erst AKTUELLE_NORMEN, dann die Vorgaben. Vorhandene Zeilen
    bleiben, wie sie sind. Gibt die neu eingetragenen Normen zurück."""
    vorhanden = set(db.session.scalars(db.select(NormAusgabe.norm)))
    zeilen = []
    for text, jahr in paare(aktuelle_normen):
        norm = name(text)
        if not norm or not jahr.isdigit() or not jahr_gueltig(int(jahr)):
            current_app.logger.warning("AKTUELLE_NORMEN: „%s:%s“ ist keine Norm mit Jahr, "
                                       "übersprungen.", text, jahr)
            continue
        zeilen.append((norm, int(jahr), KONFIGURATION_HINWEIS, "konfiguration", "Konfiguration"))
    zeilen += [(norm, jahr, hinweis, "vorgabe", VORGABE_VON) for norm, jahr, hinweis in VORGABEN]
    neu = []
    for norm, jahr, hinweis, quelle, von in zeilen:
        if norm in vorhanden:
            continue
        vorhanden.add(norm)
        db.session.add(NormAusgabe(norm=norm, jahr=jahr, hinweis=hinweis, quelle=quelle,
                                   geaendert_von=von))
        _protokoll(norm, None, {"jahr": jahr, "hinweis": hinweis, "quelle": quelle}, von,
                   "Beim Start eingetragen, weil die Norm noch in der Tabelle fehlte.")
        neu.append(norm)
    return neu


def pruefe_liste(liste):
    """[{norm, jahr, hinweis?}] -> {Norm: (Jahr, Hinweis)}; sonst Eingabefehler."""
    if not isinstance(liste, list):
        raise Eingabefehler(LISTE_FEHLT)
    ergebnis = {}
    for x in liste:
        norm = name(x.get("norm")) if isinstance(x, dict) else None
        if not norm or not jahr_gueltig(x.get("jahr")):
            raise Eingabefehler("Eine Zeile der Tabelle ist nicht vollständig. Bitte schreiben "
                                "Sie die Norm wie 60079-0 und das Jahr vierstellig wie 2017.")
        if norm in ergebnis:
            raise Eingabefehler(f"Die Norm {norm} steht zweimal in der Tabelle. Bitte entfernen "
                                "Sie eine der beiden Zeilen.")
        hinweis = x.get("hinweis")
        ergebnis[norm] = (x["jahr"], (hinweis.strip() or None) if isinstance(hinweis, str) else None)
    return ergebnis


def tabelle_setzen(liste, von):
    """Tabelle auf die Liste bringen. Nur Zeilen, die sich wirklich ändern, bekommen Name und
    Zeit; wer eine Zeile ändert oder neu einträgt, hat sie damit bestätigt. Gibt die geänderten
    Normen zurück; jede Änderung steht im Protokoll."""
    soll = pruefe_liste(liste)
    ist = {n.norm: n for n in db.session.scalars(db.select(NormAusgabe))}
    geaendert = []
    for norm, n in ist.items():
        if norm not in soll:
            _protokoll(norm, _werte(n), None, von)
            db.session.delete(n)
            geaendert.append(norm)
    for norm, (jahr, hinweis) in soll.items():
        n = ist.get(norm)
        if n is not None and (n.jahr, n.hinweis) == (jahr, hinweis):
            continue
        alt = _werte(n) if n is not None else None
        if n is None:
            n = NormAusgabe(norm=norm)
            db.session.add(n)
        n.jahr, n.hinweis, n.quelle = jahr, hinweis, "manuell"
        n.geaendert_von, n.geaendert_am = von, jetzt()
        n.bestaetigt_von, n.bestaetigt_am = von, n.geaendert_am
        _protokoll(norm, alt, _werte(n), von)
        geaendert.append(norm)
    db.session.flush()
    return sorted(geaendert)


def bestaetigen(namen, von):
    """Zeilen unverändert bestätigen. Gibt die Normen zurück, die bisher unbestätigt waren."""
    if not isinstance(namen, list) or not namen:
        raise Eingabefehler(LISTE_FEHLT)
    zeilen = []
    for text in namen:
        n = db.session.get(NormAusgabe, name(text) or "")
        if n is None:
            raise Eingabefehler(f"Die Norm {text} steht nicht in der Tabelle. Bitte laden Sie "
                                "die Seite neu.")
        zeilen.append(n)
    neu = []
    for n in zeilen:
        if n.zu_bestaetigen:
            n.bestaetigt_von, n.bestaetigt_am = von, jetzt()
            _protokoll(n.norm, None, "bestätigt", von, f"Ausgabe {n.jahr} bestätigt.")
            neu.append(n.norm)
    return neu
