"""Regeln für den Verdacht „veraltet“. Rechnet nur der Code, jede mit Grund und Seite.

Eine Regel setzt höchstens den Stand „verdacht“. Bestätigen oder verwerfen kann nur ein Mensch;
diese Entscheidung bleibt auch beim nächsten Lauf der Regeln stehen.

Jede Regel gilt nur für die Dokumentarten in GILT_FUER (eine Bedienungsanleitung oder ein
Datenblatt bekommt keinen Verdacht wegen 94/9/EG oder einer alten Normausgabe). Für Dateien, die
kein PDF sind, läuft keine Regel: das Archiv liest aus ihnen nichts.
"""

from datetime import date

from flask import current_app

from .db import db
from .modelle import Dokument, Markierung, NormAusgabe, jetzt

# Name jeder Prüfregel für Anwender (Kennung bleibt in der API)
REGELN = {
    "richtlinie_94_9": "Nennt nur die alte Richtlinie 94/9/EG",
    "norm_ausgabe": "Nennt nur eine ältere Ausgabe einer Norm",
    "alter": "Ausstellungsdatum liegt lange zurück",
    "gueltig_bis": "Gültigkeit abgelaufen",
    "kein_datum": "Kein Ausstellungsdatum gefunden",
}
MANUELL = "Von Hand als veraltet gemeldet"  # Titel einer Markierung ohne Regel
ABLOESUNG_94_9 = "20.04.2016"
# Dokumentarten, die eine Behörde oder Prüfstelle ausstellt: nach Art. 41 der Richtlinie
# 2014/34/EU gelten Bescheinigungen nach 94/9/EG weiter, sie sind kein Verdacht.
BESCHEINIGUNGEN = ("eu_baumusterpruefbescheinigung", "uk_baumusterpruefbescheinigung")
ERKLAERUNGEN = ("eu_konformitaetserklaerung", "uk_konformitaetserklaerung")
# Art noch nicht erkannt oder „sonstiges“: kann eine Bescheinigung oder Erklärung sein, die die
# Auslesung nicht erkannt hat; die Regeln laufen, ein Mensch entscheidet.
OFFEN = ("sonstiges", "unbekannt")
# Für welche Dokumentarten jede Regel gilt (README, Tabelle „Prüfregeln“). Anleitungen,
# Datenblätter und Zeichnungen: nur „gueltig_bis“, denn wer selbst ein Ende nennt, ist danach alt.
GILT_FUER = {
    # Bescheinigungen bekommen statt eines Verdachts nur einen neutralen Hinweis (hinweise)
    "richtlinie_94_9": ERKLAERUNGEN + OFFEN,
    "norm_ausgabe": BESCHEINIGUNGEN + ERKLAERUNGEN + OFFEN,
    "alter": BESCHEINIGUNGEN + ERKLAERUNGEN + OFFEN,
    "kein_datum": BESCHEINIGUNGEN + ERKLAERUNGEN + OFFEN,
    "gueltig_bis": BESCHEINIGUNGEN + ERKLAERUNGEN + OFFEN
                   + ("bedienungsanleitung", "datenblatt", "zeichnung"),
}


def gilt(regel, dok):
    return _ist_pdf(dok) and dok.doc_type in GILT_FUER[regel]


def _ist_pdf(dok):
    return getattr(dok, "ist_pdf", True)  # Prüfobjekte ohne Dateiformat zählen als PDF
HINWEIS_94_9 = ("Die Bescheinigung wurde nach der alten Richtlinie 94/9/EG ausgestellt{seite}. Sie "
                "gilt nach Artikel 41 der Richtlinie 2014/34/EU weiter.")


def aktuelle_normen():
    """{Norm: (Jahr, Anmerkung)} aus der Tabelle norm_ausgabe. Die Anmerkung ist ein Satz für den
    Grund: noch unbestätigt, oder der Hinweis, den ein Mensch zur Zeile geschrieben hat."""
    ergebnis = {}
    for n in db.session.scalars(db.select(NormAusgabe)):
        if n.zu_bestaetigen:
            anmerkung = "Die aktuelle Ausgabe ist eine Vorgabe, die noch niemand bestätigt hat."
        elif n.quelle == "manuell" and n.hinweis:
            anmerkung = f"Anmerkung zur Norm: {n.hinweis.rstrip('.')}."
        else:
            anmerkung = None
        ergebnis[n.norm] = (n.jahr, anmerkung)
    return ergebnis


def _ocr(a):
    """Zusatz zum Grund, wenn die Angabe von einer Seite stammt, die die Texterkennung las."""
    return (" Der Wert stammt aus der Texterkennung eines Scans, bitte prüfen Sie ihn im PDF."
            if a is not None and a.quelle == "ocr" else "")


def _seite(a):
    return f" (Seite {a.seite})" if a is not None and a.seite else ""


def _vor_jahren(heute, jahre):
    return date(heute.year - jahre, heute.month, min(heute.day, 28))


def _nur_94_9(dok):
    richtlinien = {a.wert: a for a in dok.angaben if a.art == "richtlinie"}
    if "94/9/EG" in richtlinien and "2014/34/EU" not in richtlinien:
        return richtlinien["94/9/EG"]
    return None


def hinweise(dok):
    """Neutrale Hinweise ohne Verdacht, z. B. Bescheinigung nach 94/9/EG (gilt weiter)."""
    if not _ist_pdf(dok):
        return []
    a = _nur_94_9(dok)
    if a is not None and dok.doc_type in BESCHEINIGUNGEN:
        return [HINWEIS_94_9.format(seite=_seite(a))]
    return []


def befunde(dok, heute, jahre, normen):
    """Ergebnis aller Regeln für ein Dokument: [{regel, stufe, grund, seite, textstelle}]. Nur
    die Regeln, die für seine Dokumentart gelten (GILT_FUER); für andere Dateien als PDF keine."""
    if not _ist_pdf(dok):
        return []
    angaben = {}
    for a in dok.angaben:
        angaben.setdefault(a.art, []).append(a)
    ergebnis = []

    a = _nur_94_9(dok)
    if a is not None and gilt("richtlinie_94_9", dok):
        wer = "Die Erklärung" if dok.doc_type in ERKLAERUNGEN else "Das Dokument"
        folge = ("Eine Konformitätserklärung muss heute die Richtlinie 2014/34/EU nennen."
                 if dok.doc_type in ERKLAERUNGEN else
                 "Vermutlich gibt es eine neuere Fassung.")
        ergebnis.append(dict(regel="richtlinie_94_9", stufe="veraltet", seite=a.seite,
                             textstelle=a.textstelle,
                             grund=f"{wer} nennt nur die alte Richtlinie 94/9/EG{_seite(a)}. "
                                   f"Diese gilt seit dem {ABLOESUNG_94_9} nicht mehr. {folge}"
                                   + _ocr(a)))

    # Je Norm alle datierten Nennungen; Verdacht, wenn keine so neu ist wie die Tabelle sagt.
    # Mehrere betroffene Normen ergeben eine Markierung mit allen im Grund (Seite: die erste).
    zitiert = {}
    for a in angaben.get("norm", []):
        name, jahr = a.wert.split(":")
        zitiert.setdefault(name, []).append((int(jahr), a))
    alt, anmerkungen = [], []
    for name, (aktuell, anmerkung) in sorted(normen.items(), key=lambda x: normzahl(x[0])):
        ausgaben = sorted(zitiert.get(name, []), key=lambda x: x[0])
        if ausgaben and all(jahr < aktuell for jahr, _ in ausgaben):
            jahre_ = sorted({j for j, _ in ausgaben})
            welche = " und ".join(str(j) for j in jahre_)
            alt.append((ausgaben[-1][1],
                        f"Das Dokument nennt die Norm {name} nur in der Ausgabe {welche}"
                        f"{_seite(ausgaben[-1][1])}. Aktuell ist die Ausgabe {aktuell}."))
            if anmerkung and anmerkung not in anmerkungen:
                anmerkungen.append(anmerkung)
    if alt and gilt("norm_ausgabe", dok):
        a = alt[0][0]
        ergebnis.append(dict(regel="norm_ausgabe", stufe="veraltet", seite=a.seite,
                             textstelle=a.textstelle,
                             grund=" ".join([t for _, t in alt] + anmerkungen) + _ocr(a)))

    datum = next(iter(angaben.get("ausstellungsdatum", [])), None)
    if (dok.ausstellungsdatum and dok.ausstellungsdatum < _vor_jahren(heute, jahre)
            and gilt("alter", dok)):
        unsicher = (" Das Datum ließ sich nicht sicher lesen." if datum and not datum.sicher
                    else "")
        ergebnis.append(dict(regel="alter", stufe="veraltet", seite=datum and datum.seite,
                             textstelle=datum and datum.textstelle,
                             grund=f"Das Dokument wurde am {dok.ausstellungsdatum:%d.%m.%Y} "
                                   f"ausgestellt{_seite(datum)} und ist damit älter als "
                                   f"{jahre} Jahre." + unsicher + _ocr(datum)))
    elif not dok.ausstellungsdatum and gilt("kein_datum", dok):
        grund ="Im Dokument steht kein Ausstellungsdatum, deshalb lässt sich sein Alter nicht " \
                "beurteilen."
        if not dok.lesbar:
            grund += " " + (dok.ocr_hinweis or "Das PDF ist ein Scan ohne lesbaren Text.")
        elif dok.ocr_seiten:
            grund += " Ein Teil des Texts stammt aus der Texterkennung eines Scans."
        ergebnis.append(dict(regel="kein_datum", stufe="unklar", seite=None, textstelle=None,
                             grund=grund))

    if dok.gueltig_bis and dok.gueltig_bis < heute and gilt("gueltig_bis", dok):
        a = next(iter(angaben.get("gueltig_bis", [])), None)
        ergebnis.append(dict(regel="gueltig_bis", stufe="veraltet", seite=a and a.seite,
                             textstelle=a and a.textstelle,
                             grund=f"Das Dokument war nur bis zum {dok.gueltig_bis:%d.%m.%Y} "
                                   f"gültig{_seite(a)}." + _ocr(a)))
    return ergebnis


def normzahl(name):
    """„60079-11“ -> (60079, 11), damit 60079-2 vor 60079-11 kommt."""
    return tuple(int(t) for t in name.split("-") if t.isdigit())


def pruefe(dokumente, heute=None):
    """Regeln für die Dokumente neu rechnen. Neue Befunde werden Verdacht; ein Verdacht, dessen
    Regel nicht mehr zutrifft, fällt weg; entschiedene Markierungen bleiben. Zählt je Regel."""
    heute = heute or date.today()
    jahre = current_app.config["VERALTET_NACH_JAHREN"]
    normen = aktuelle_normen()
    zahlen = {"neu": 0, "entfallen": 0, "je_regel": {r: 0 for r in REGELN}}
    for dok in dokumente:
        alt = {m.regel: m for m in dok.markierungen if m.regel}
        neu = {b["regel"]: b for b in befunde(dok, heute, jahre, normen)}
        for regel, b in neu.items():
            zahlen["je_regel"][regel] += 1
            m = alt.get(regel)
            if m is None:
                dok.markierungen.append(Markierung(erstellt_von="Regel", status="verdacht", **b))
                zahlen["neu"] += 1
            else:
                for feld in ("stufe", "grund", "seite", "textstelle"):
                    setattr(m, feld, b[feld])
        for regel, m in alt.items():
            if regel not in neu and m.status == "verdacht":
                dok.markierungen.remove(m)
                zahlen["entfallen"] += 1
        dok.geprueft_am = jetzt()
    return zahlen


def pruefe_alle(heute=None):
    return pruefe(db.session.scalars(db.select(Dokument)).all(), heute)
