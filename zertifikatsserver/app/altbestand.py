"""Import des alten Ordners <Hersteller>/<Datei> als „Altbestand“ (für Anwender „Bestand
einlesen“, der Bestand heißt „übernommener Bestand“).

Eingelesen werden PDFs und die anderen erlaubten Formate (dateiformate.py); andere Dateien stehen
mit Grund in den Hinweisen des Laufs. Quellennachweis jeder Datei: Art „altbestand“ und der
relative Pfad im Ordner, sonst nichts (kein Link, kein Abrufdatum: beides ist nicht bekannt).
Jede Datei wird einmal abgelegt (gleicher SHA-256 = eine Datei, weitere Pfade als altpfade).
Teile entstehen nur, wo die Typbezeichnung im Dateinamen erkennbar ist (auslesen.typen). Mehrere
Dokumente derselben Art zu einem Teil kommen in dieselbe Version: welches das neuere ist, lässt
sich aus dem Ordner nicht sicher sagen, deshalb steht dann ein Hinweis an der Verknüpfung.
Der Import ist wiederholbar: bekannte Dateien werden übersprungen.
"""

import hashlib
from pathlib import Path

from flask import current_app

from . import ablage, auslesen, dateiformate, regeln
from .config import paare
from .db import db
from .modelle import Importlauf, Verknuepfung, jetzt

VON = "Bestand einlesen"
MEHRDEUTIG = ("Im übernommenen Bestand lagen {n} Dokumente dieser Art zu diesem Teil. Welches "
              "neuer ist, ließ sich nicht erkennen, deshalb stehen alle in Version {v}.")


def dateien(quelle):
    """Dateien in Herstellerordnern; Ordner mit „_“ oder „.“ am Anfang sind Zwischenspeicher,
    Dateien mit „.“ am Anfang (.gitkeep) zählen nicht."""
    for pfad in sorted(quelle.rglob("*")):
        teile = pfad.relative_to(quelle).parts
        if (pfad.is_file() and len(teile) > 1 and not pfad.name.startswith(".")
                and not any(t[0] in "_." for t in teile[:-1])):
            yield pfad


def pdf_dateien(quelle):
    """Nur die PDFs (wie früher), z. B. für Zählungen."""
    return (p for p in dateien(quelle) if p.suffix.lower() == ".pdf")


def importiere(quelle, heute=None):
    """Liest den Ordner ein und gibt den Importlauf mit Zahlen und Hinweisen zurück.
    Das Speichern (commit) macht der Aufrufer."""
    quelle = Path(quelle)
    if not quelle.is_dir():
        raise ablage.AblageFehler(f"Ordner mit dem Altbestand fehlt: {quelle}")
    namen = dict(paare(current_app.config["HERSTELLER_NAMEN"]))
    lauf = Importlauf(quelle=str(quelle))
    db.session.add(lauf)
    hinweise = []
    z = dict(dateien=0, dokumente_neu=0, dubletten=0, schon_bekannt=0, nicht_lesbar=0,
             teilweise_ohne_text=0, mit_teil=0, ohne_teil=0, teile_neu=0, verknuepfungen=0,
             andere_dateien=0, abgelehnt=0)

    gruppen = {}  # sha256 -> [Pfad]
    for pfad in dateien(quelle):
        rel = pfad.relative_to(quelle).as_posix()
        if dateiformate.endung(pfad.name) not in dateiformate.FORMATE:
            z["abgelehnt"] += 1
            hinweise.append(f"{rel} wurde nicht übernommen, weil das Dokumentenarchiv Dateien "
                            "dieser Art nicht annimmt.")
            continue
        z["dateien"] += 1
        gruppen.setdefault(hashlib.sha256(pfad.read_bytes()).hexdigest(), []).append(pfad)

    neue = []
    for pfade in gruppen.values():
        rel = [p.relative_to(quelle).as_posix() for p in pfade]
        if len(pfade) > 1:
            z["dubletten"] += len(pfade) - 1
            hinweise.append(f"Dieselbe Datei lag mehrfach im Ordner ({', '.join(rel)}). Sie "
                            "wurde einmal übernommen.")
        ordner = pfade[0].relative_to(quelle).parts[0]
        hersteller = ablage.hersteller_holen(namen.get(ordner, ordner), ordner)
        try:
            dok, auswertung, neu = ablage.dokument_anlegen(
                pfade[0].read_bytes(), hersteller, auslesen.dateiname(pfade[0].name),
                "altbestand", VON, rel, quelle={"art": "altbestand", "dateiname": rel[0]})
        except ablage.AblageFehler as e:
            z["abgelehnt"] += 1
            hinweise.append(f"{rel[0]} wurde nicht übernommen. {e}")
            continue
        if not neu:
            z["schon_bekannt"] += 1
            continue
        z["dokumente_neu"] += 1
        neue.append(dok)
        if not dok.ist_pdf:
            z["andere_dateien"] += 1
        elif not dok.lesbar:
            z["nicht_lesbar"] += 1
            hinweise.append(f"{rel[0]} ist ein Scan ohne lesbaren Text. Die Texterkennung liest "
                            "ihn im Hintergrund, wenn sie eingeschaltet ist.")
        elif dok.seiten_ohne_text:
            z["teilweise_ohne_text"] += 1
        typen = auslesen.typen([p.name for p in pfade], auswertung.seiten if auswertung else [],
                               [hersteller.name, ordner])
        for typ, beleg, _ in typen:
            teil, teil_neu = ablage.teil_holen(hersteller, typ, VON)
            z["teile_neu"] += teil_neu
            ablage.verknuepfe(teil, dok, dok.doc_type, VON, "altbestand", beleg,
                              neue_version=False)
            z["verknuepfungen"] += 1
        z["mit_teil" if typen else "ohne_teil"] += 1

    z["mehrdeutig"] = _mehrdeutige_markieren()
    pruefung = regeln.pruefe(neue, heute)
    z["markierungen_neu"] = pruefung["neu"]
    z["markierungen_je_regel"] = pruefung["je_regel"]
    lauf.zahlen, lauf.hinweise, lauf.beendet = z, hinweise, jetzt()
    return lauf


def _mehrdeutige_markieren():
    """Hinweis an Versionen, in denen mehrere Dokumente gleicher Art aus dem Altbestand stehen;
    gibt die Anzahl solcher Versionen zurück."""
    gruppen = {}
    for v in db.session.scalars(db.select(Verknuepfung).filter_by(quelle="altbestand")):
        gruppen.setdefault((v.teil_id, v.doc_type, v.version), []).append(v)
    anzahl = 0
    for (_, _, version), liste in gruppen.items():
        if len(liste) > 1:
            anzahl += 1
            for v in liste:
                v.hinweis = MEHRDEUTIG.format(n=len(liste), v=version)
    return anzahl


def bericht(lauf):
    """Zahlen des Importlaufs als Text für die Kommandozeile."""
    z = lauf.zahlen
    zeilen = [
        f"Bestand eingelesen aus {lauf.quelle}",
        f"  Dateien: {z['dateien']}, davon Dubletten (gleicher Hash): {z['dubletten']}, "
        f"keine PDFs: {z.get('andere_dateien', 0)}, nicht angenommen: {z.get('abgelehnt', 0)}",
        f"  Dokumente neu abgelegt: {z['dokumente_neu']}, schon bekannt: {z['schon_bekannt']}",
        f"  Nicht lesbar (Scan ohne Textebene): {z['nicht_lesbar']}, "
        f"teilweise ohne Text: {z['teilweise_ohne_text']}",
        f"  Mit Teil verknüpft: {z['mit_teil']}, ohne Teil: {z['ohne_teil']} "
        f"(Teile neu: {z['teile_neu']}, Verknüpfungen: {z['verknuepfungen']}, "
        f"Versionen mit mehreren Dokumenten gleicher Art: {z['mehrdeutig']})",
        f"  Markierungen neu: {z['markierungen_neu']}",
    ]
    zeilen += [f"    {regeln.REGELN[r]}: {n}" for r, n in z["markierungen_je_regel"].items()]
    return "\n".join(zeilen)
