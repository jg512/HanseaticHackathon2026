"""Quellennachweis je Dokument: woher die Datei kam, strukturiert statt als Freitext.

Felder (Spalten quelle_* am Dokument, gleiche Schlüssel in Anfragen ohne „quelle_“):
art (QUELLE_ARTEN), link (ursprünglicher Download-Link), seite (Webseite, auf der der Link stand;
leer = unbekannt), abgerufen_am, von (wer die Datei übernommen hat), dateiname (ursprünglicher
Name, beim Bestand der relative Pfad im Ordner), hinweis. Dazu aus dem Dokument selbst SHA-256 und
der stabile Link auf die gespeicherte Datei.

Kommt dieselbe Datei später auf anderem Weg noch einmal, bleibt der Nachweis des Dokuments (der
erste Weg) und die neue Version bekommt ihren eigenen (Verknuepfung.quellennachweis). Ein Mensch
kann den Nachweis korrigieren, jede Änderung steht im Protokoll.
"""

from datetime import date, datetime
from urllib.parse import urlsplit

from . import ablage, auslesen, begriffe
from .db import db
from .modelle import QUELLE_ARTEN

FELDER = ("art", "link", "seite", "abgerufen_am", "von", "dateiname", "hinweis")
SPALTE = {f: f"quelle_{f}" for f in FELDER}
VON_SYSTEM = "Dokumentenarchiv"


class Eingabefehler(ValueError):
    """Ungültiger Quellennachweis; die Meldung geht so an den Anwender."""


def _link(wert, was):
    wert = (wert or "").strip()
    if not wert:
        return None
    teile = urlsplit(wert)
    if teile.scheme not in ("http", "https") or not teile.netloc:
        raise Eingabefehler(f"Der {was} muss eine Webadresse sein, die mit http:// oder https:// "
                            "beginnt. Bitte prüfen Sie die Eingabe.")
    return wert


def _zeitpunkt(wert):
    if wert in (None, ""):
        return None
    if isinstance(wert, datetime):
        return wert
    try:
        text = str(wert).strip().replace("Z", "")
        return (datetime.fromisoformat(text) if "T" in text or " " in text
                else datetime.combine(date.fromisoformat(text), datetime.min.time()))
    except ValueError:
        raise Eingabefehler("Das Abrufdatum ließ sich nicht lesen. Bitte geben Sie es als Datum "
                            "ein, zum Beispiel 2026-11-13.") from None


def pruefe(daten, praefix=""):
    """Quellennachweis aus einer Anfrage, nur die angegebenen Felder, geprüft und einheitlich.

    praefix: „quelle_“ für Formularfelder und PATCH (quelle_link …), leer für ein eigenes Objekt
    {art, link, …}. Gibt {Feld: Wert} zurück; Eingabefehler bei ungültigen Werten."""
    ergebnis = {}
    for feld in FELDER:
        schluessel = praefix + feld
        if schluessel not in daten:
            continue
        wert = daten[schluessel]
        if wert is not None and not isinstance(wert, str):
            if not (feld == "abgerufen_am" and isinstance(wert, datetime)):
                raise Eingabefehler("Eine Angabe zur Herkunft ist kein Text. Bitte laden Sie die "
                                    "Seite neu und versuchen Sie es noch einmal.")
        if feld == "art":
            wert = (wert or "").strip() or None
            if wert and wert not in QUELLE_ARTEN:
                raise Eingabefehler("Diese Herkunft kennt das Dokumentenarchiv nicht. Bitte "
                                    "wählen Sie eine aus der Liste.")
        elif feld == "link":
            wert = _link(wert, "Download-Link")
        elif feld == "seite":
            wert = _link(wert, "Link auf die Webseite")
        elif feld == "abgerufen_am":
            wert = _zeitpunkt(wert)
        else:
            wert = (wert or "").strip() or None
        ergebnis[feld] = wert
    return ergebnis


def setze(dok, quelle):
    """Quellennachweis eines neuen Dokuments setzen (ohne Protokoll: das Anlegen steht dort)."""
    for feld, wert in quelle.items():
        setattr(dok, SPALTE[feld], wert)


def werte(dok):
    return {feld: getattr(dok, SPALTE[feld]) for feld in FELDER}


def unterschied(dok, quelle):
    """Die Felder aus quelle, die vom Nachweis des Dokuments abweichen (leere zählen nicht)."""
    bisher = werte(dok)
    return {f: w for f, w in quelle.items() if w not in (None, "") and w != bisher[f]}


def aendere(dok, daten, von, grund=None):
    """Quellennachweis von Hand korrigieren (Felder quelle_*). Gibt [(Feld, alt, neu)] zurück."""
    neu = pruefe(daten, "quelle_")
    aenderungen = []
    for feld, wert in neu.items():
        alt = getattr(dok, SPALTE[feld])
        if alt == wert:
            continue
        setattr(dok, SPALTE[feld], wert)
        alt_j, neu_j = (x.isoformat() if isinstance(x, datetime) else x for x in (alt, wert))
        ablage.protokoll("dokument", dok.id, SPALTE[feld], alt_j, neu_j, von, grund)
        aenderungen.append((SPALTE[feld], alt_j, neu_j))
    return aenderungen


def _iso(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else x


def satz(art, link, seite, dateiname):
    """Ein Satz für Anwender, was über die Herkunft bekannt ist und was nicht. Der Dateiname
    steht lesbar darin („ä“ statt „#U00e4“), gespeichert bleibt er, wie er kam."""
    dateiname = auslesen.dateiname(dateiname) if dateiname else dateiname
    if art == "altbestand":
        return (f"Die Datei stammt aus dem übernommenen Bestand, dort lag sie unter {dateiname}."
                if dateiname else "Die Datei stammt aus dem übernommenen Bestand.")
    if art == "internet":
        if link and seite:
            return "Die Datei wurde im Internet gefunden. Download-Link und Webseite stehen darunter."
        if link:
            return ("Die Datei wurde im Internet gefunden. Die Webseite, auf der der Link stand, "
                    "ist nicht bekannt.")
        return "Die Datei wurde im Internet gefunden, der Download-Link ist nicht bekannt."
    if art == "hersteller":
        return "Der Hersteller hat die Datei geschickt."
    if art == "hochgeladen":
        return "Die Datei wurde von Hand hochgeladen."
    if art == "atex_agent":
        return "Die Datei wurde bei einer Freigabe im ATEX-Agent übernommen."
    return "Woher die Datei stammt, ist nicht vermerkt."


def nachweis(dok, basis_url=""):
    """Quellennachweis des Dokuments für die API."""
    w = werte(dok)
    return {"art": w["art"], "art_text": begriffe.text(begriffe.QUELLE_ART, w["art"]),
            "link": w["link"], "seite_gefunden_auf": w["seite"],
            "abgerufen_am": _iso(w["abgerufen_am"]), "uebernommen_von": w["von"],
            "uebernommen_am": _iso(dok.angelegt_am), "dateiname": w["dateiname"],
            "hinweis": w["hinweis"], "sha256": dok.sha256,
            "archiv_link": basis_url + dok.datei_url,
            "text": satz(w["art"], w["link"], w["seite"], w["dateiname"])}


def version_nachweis(v):
    """Eigener Quellennachweis einer Version (nur wo er abweicht), sonst None."""
    q = v.quellennachweis
    if not q:
        return None
    return {**q, "art_text": begriffe.text(begriffe.QUELLE_ART, q.get("art")),
            "text": satz(q.get("art"), q.get("link"), q.get("seite"), q.get("dateiname"))}


def fuer_json(quelle):
    """Nachweis für eine JSON-Spalte (Verknuepfung.quellennachweis)."""
    return {f: _iso(w) for f, w in quelle.items() if w not in (None, "")} or None


# ---------- alte Datenbanken: Nachweis aus vorhandenen Angaben nachtragen ----------

PUSH_ANFANG = "Im ATEX-Agent freigegeben von "


def aus_altem_hinweis(hinweis):
    """Älteres Backend schickte die Herkunft nur als Satz („Im ATEX-Agent freigegeben von X;
    Quelle: <Link oder Weg>“). Daraus {art, link?}; sonst None."""
    if not (hinweis or "").startswith(PUSH_ANFANG):
        return None
    weg = hinweis.split("Quelle: ", 1)[1].strip() if "Quelle: " in hinweis else ""
    if weg.startswith(("http://", "https://")):
        return {"art": "internet", "link": weg}
    return {"art": "atex_agent"}


def nachtragen():
    """Dokumente ohne quelle_art (angelegt vor dem Quellennachweis) bekommen ihn aus dem, was
    schon gespeichert ist: Bestand -> altbestand mit Pfad; Freigabe aus dem ATEX-Agent (Hinweis an
    der Version „Im ATEX-Agent freigegeben von X; Quelle: <Link oder Weg>“) -> atex_agent mit Link;
    sonst hochgeladen. Nichts wird erfunden. Gibt die Anzahl zurück."""
    from .modelle import Dokument
    anzahl = 0
    for dok in db.session.scalars(db.select(Dokument).where(Dokument.quelle_art.is_(None))):
        quelle = {"von": dok.angelegt_von}
        if dok.herkunft == "altbestand":
            quelle.update(art="altbestand", dateiname=(dok.altpfade or [None])[0])
        else:
            quelle.update(art="hochgeladen", dateiname=dok.dateiname)
            alt = next((q for v in dok.verknuepfungen if (q := aus_altem_hinweis(v.hinweis))),
                       None)
            quelle.update(alt or {})
        setze(dok, quelle)
        ablage.protokoll("dokument", dok.id, "quellennachweis", None,
                         {"art": quelle["art"], "link": quelle.get("link")}, VON_SYSTEM,
                         "Aus den schon gespeicherten Angaben nachgetragen.")
        anzahl += 1
    return anzahl

