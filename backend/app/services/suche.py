"""Dokumentensuche je Position (M11, M14).

Reihenfolge je Bauteil:
1. Datenbank: status.aktualisiere erkennt Positionen, deren Pflichtdokumente geprüft in der
   Datenbank liegen („vorhanden“). Sie werden nicht gesucht, ohne KI fertig.
2. Dokumentenarchiv (mit ZERTIFIKATSSERVER_URL): in den aktuellen Dokumenten des Herstellers im
   Archiv (zertifikatsserver.auszug) die Hersteller-Typbezeichnung im PDF-Text und im
   Dateinamen suchen. Jeder Fund wird ein Vorschlag (Finding) mit Datei, Seite und Ergebnis der
   Prüfregeln (services/pruefung.py). Den Herstellerordner und den Suchdienst lässt der Agent
   dann aus: nach dem Archiv kommt gleich das Internet.
   Ohne Archiv steht der Herstellerordner unter ATEX_DOCS für das Archiv (Entwicklung, Tests);
   kennt der Ordner den Hersteller nicht, gibt es keinen Treffer. Die Ordner einer Position
   liefert ordner_fuer.
3. Suchdienst (RAG, services/rag.py, nur mit SEARCH_URL und ohne Archiv): wenn der Ordner
   keinen Vorschlag hat, der zählt, auch ohne Herstellerordner. Die Treffer der Ähnlichkeitssuche sind nur Kandidaten:
   Jede Kandidaten-Datei geht durch dieselbe Prüfung wie eine Datei im Ordner. Steht der Typ
   darin, wird sie ein normaler Vorschlag (Quelle „suchdienst“). Steht er in keiner, werden
   höchstens RAG_OHNE_TREFFER Dateien aus den Ordnern der Position Vorschläge, immer „unklar“,
   mit der ähnlichen Textstelle und dem Grund. Ist der Dienst aus oder baut noch, geht es weiter.
4. Internet: nur wenn Ordner und Suchdienst nichts ergaben, das zählt (ein Vorschlag des
   Suchdienstes ohne Typ-Treffer zählt nicht), auf der Webseite des Herstellers
   (services/internet.py), mit einer Grenze an Web-Anfragen je Position.
5. Sprachversionen (M8): Fehlt einem Fund aus dem Ordner eine Pflichtsprache, sucht ein zweiter
   Durchgang im Herstellerordner seine anderen Sprachversionen (services/sprachversionen.py).

Ohne Treffer steht die Position auf „nicht_gefunden“, searched_in sagt, wo gesucht wurde und
warum die Suche endete.
"""

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import current_app

from ..db import db
from ..models import Finding
from . import (freigabe, internet, pdf_text, pruefung, rag, sprachversionen, status,
               zertifikatsserver)

# Diese Positionen werden nicht gesucht (Status nach status.aktualisiere)
NICHT_SUCHEN = ("klaerung", "kein_ex_teil", "vorhanden", "erledigt")
# Hersteller in der Stückliste -> Ordnername, beide ohne Leer- und Satzzeichen
HERSTELLER = {"pepperlfuchs": "pf"}
# Suchdienst: so viele Kandidaten-Dateien je Position prüfen, so viele Vorschläge höchstens,
# davon ohne Typ-Treffer („unklar“) höchstens so viele
RAG_DATEIEN = 5
RAG_VORSCHLAEGE = 3
RAG_OHNE_TREFFER = 2
# Texte für den Anwender (ganze Sätze, ohne Technikwörter)
RAG_HINWEIS = "Die Ähnlichkeitssuche schlägt dieses Dokument nur vor"
RAG_GEFUNDEN = "Die Ähnlichkeitssuche hat dieses Dokument gefunden."

# Dokumentart: das früheste Stichwort im Kopf des Dokuments zählt, denn eine
# Konformitätserklärung nennt weiter unten oft die Baumusterprüfbescheinigung und umgekehrt.
# Die Mehrzahl zählt nicht: Ein Datenblatt führt „Konformitätserklärungen ATEX (EUK)“ unter
# seinen Zulassungen auf und ist deshalb keine Erklärung.
ARTEN = (
    (re.compile(r"konformitätserklärung(?!en)|konformitaetserklaerung(?!en)"
                r"|declaration of conformity"
                r"|déclaration (?:ue |ce )?de conformité"), "konformitaetserklaerung"),
    (re.compile(r"baumusterprüf|type[ -]?examination"), "baumusterpruefbescheinigung"),
    (re.compile(r"betriebsanleitung|bedienungsanleitung|instruction manual|operating instructions"
                r"|manuel d'instructions|notice d'instructions"), "bedienungsanleitung"),
)
# Produktinformation statt Nachweis: Datenblatt, Produktübersicht, Katalog, Prospekt
DATENBLATT = re.compile(r"datenblatt|data[ _-]?sheet|fiche[ _-]technique|product[ _-]?overview"
                        r"|produkt(?:ue|ü)bersicht|katalog|catalog|brosch(?:ue|ü)re|brochure"
                        r"|prospekt|flyer", re.I)
UK_KOPF = re.compile(r"\buksi\b")  # UK-Verordnung „UKSI 2016:1107“
KOPF = 3000  # Zeichen
# Vorschlag ist vorläufig, die nächste Suche prüft neu; UNGELESEN_ALT steht in älteren Vorschlägen
UNGELESEN = "eingescannt und noch nicht gelesen"
UNGELESEN_ALT = "Scan noch nicht per OCR gelesen"

SPRACHE = {  # häufige kurze Wörter je Sprache, ohne solche, die es auch im Spanischen gibt
    "de": {"und", "der", "die", "das", "für", "mit", "nicht", "wird", "werden", "ist", "den",
           "dem"},
    "en": {"the", "and", "of", "with", "for", "is", "are", "this", "be", "shall", "to"},
    "fr": {"les", "et", "pour", "avec", "est", "sont", "du", "une", "aux", "dans", "être"},
}
# „PTB 00 ATEX 2048 X“, „KEMA 04ATEX1244“, „ITS15ATEX18372X“, „BVS 18 ATEX E 049 X“
ZERTIFIKAT = re.compile(r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöü]{1,7}[ \t]?\d{2}[ \t]?(?:ATEX|UKEX)"
                        r"[ \t]?[A-Z]?[ \t]?\d{3,5}(?:[ \t]?[XU])?\b")
IECEX = re.compile(r"\bIECEx[ \t][A-Z]{2,6}[ \t]\d{2}\.\d{4}[XU]?\b")


class SuchFehler(Exception):
    """Die Suche kann nicht laufen, z. B. weil der Ordner fehlt."""


def ordner_fuer(wurzel, item):
    """(Ordner, Satz für „gesucht in“) der lokalen Stufe: mit Dokumentenarchiv nur dessen
    aktuelle Dokumente des Herstellers (zertifikatsserver.auszug), sonst der Herstellerordner."""
    if zertifikatsserver.an():
        o, erreichbar = zertifikatsserver.auszug(wurzel, item.manufacturer)
        return ([o] if o else []), archiv_satz(o, erreichbar)
    ordner = [o for o in [herstellerordner(wurzel, item.manufacturer)] if o]
    return ordner, ordner_satz(wurzel, ordner)


def herstellerordner(wurzel, hersteller):
    """Unterordner zum Hersteller der Position, sonst None (dann kein Treffer)."""
    def norm(s):
        return re.sub(r"[^a-z0-9äöüß]", "", (s or "").lower())
    name = HERSTELLER.get(norm(hersteller), norm(hersteller))
    if not name:
        return None
    # „_internet“ ist der Zwischenspeicher der Internet-Stufe, kein Herstellerordner
    for ordner in sorted(p for p in wurzel.iterdir() if p.is_dir() and p.name[0] != "_"):
        if norm(ordner.name) and name.startswith(norm(ordner.name)):
            return ordner
    return None


def dokumentart(seiten, dateiname=""):
    """Einer aus stammdaten.DOC_TYPES, aus dem Kopf des Textes; ohne Treffer „sonstiges“.
    Nennt sich die Datei selbst Datenblatt („ex-einzeldatenblatt-…“), ist sie eines, auch wenn
    ihr Text andere Dokumente erwähnt."""
    if DATENBLATT.search(dateiname or ""):
        return "datenblatt"
    kopf = re.sub(r"\s+", " ", " ".join(seiten)[:KOPF].lower()).replace(" -", "-")
    funde = [(m.start(), art) for muster, art in ARTEN if (m := muster.search(kopf))]
    if not funde:
        return "datenblatt" if DATENBLATT.search(kopf[:300]) else "sonstiges"
    start, art = min(funde)
    if art == "bedienungsanleitung":
        return art
    davor = kopf[max(0, start - 15):start]  # „EU and UK Declaration“ zählt als EU
    uk = not re.search(r"\b(?:eu|eg|ce)\b", davor) and (re.search(r"\buk\b", davor)
                                                         or UK_KOPF.search(kopf))
    return f"{'uk' if uk else 'eu'}_{art}"


def sprachen(seiten):
    """„de,en,fr“: Sprachen mit Kennung „(DE)“ im Text oder deren häufige Wörter oft genug
    vorkommen (mindestens 5-mal und ein Fünftel der häufigsten Sprache)."""
    text = " ".join(seiten)
    woerter = Counter(re.findall(r"[a-zäöüßéèàçê]+", text.lower()))
    anzahl = {s: sum(woerter[w] for w in liste) for s, liste in SPRACHE.items()}
    kennung = {k.lower() for k in re.findall(r"\((DE|EN|FR)\)", text)}
    return ",".join(s for s in SPRACHE if s in kennung
                    or anzahl[s] >= 5 and anzahl[s] * 5 >= max(anzahl.values())) or None


def zertifikatsnummern(seiten):
    """ATEX-Nummern wie geschrieben, z. B. „PTB 00 ATEX 2048 X“; ohne solche die IECEx-Nummer."""
    text = "\n".join(seiten)
    nummern = [re.sub(r"\s+", " ", n) for n in ZERTIFIKAT.findall(text) or IECEX.findall(text)]
    return "; ".join(list(dict.fromkeys(nummern))[:3]) or None


def suche_vorgang(bom, internet_versuche=None, melde=None):
    """Sucht für alle Positionen des Vorgangs. Das Speichern (commit) macht der Aufrufer.

    internet_versuche: Web-Anfragen je Position (0 = keine Internet-Suche), sonst der Standard
    INTERNET_VERSUCHE_PRO_TEIL; nie mehr als internet.OBERGRENZE.
    melde: wird mit der Stufe aufgerufen („Ähnlichkeitssuche“, „Internet“), für die Anzeige.
    """
    wurzel = Path(current_app.config["ATEX_DOCS"])
    if not wurzel.is_dir():
        raise SuchFehler(f"Ordner mit den Ex-Dokumenten fehlt: {wurzel}")
    if internet_versuche is None:
        internet_versuche = current_app.config["INTERNET_VERSUCHE_PRO_TEIL"]
    melde = melde or (lambda stufe: None)
    lauf = internet.Lauf(max(0, min(internet_versuche, internet.OBERGRENZE)))
    for item in bom.items:
        if status.aktualisiere(item) in NICHT_SUCHEN:
            continue
        ordner, satz = ordner_fuer(wurzel, item)
        saetze = [satz]
        # Funde, die nur in nicht gewählten Sprachen vorliegen; auch offene Vorschläge von
        # früher, als andere Sprachen gewählt waren (Entscheidungen eines Menschen bleiben)
        alt = [f for f in item.findings if f.status == "vorgeschlagen"
               and not sprachversionen.passt_zur_wahl(item, f)]
        # ebenso Datenblätter aus dem Internet, die früher als Fund galten (internet._pdf)
        alt += [f for f in item.findings if f.status == "vorgeschlagen" and f.source == "internet"
                and f not in alt and DATENBLATT.search(PurePosixPath(f.file_path or "").name)]
        for f in alt:
            item.findings.remove(f)
        andere_sprache = 0  # die Suche findet sie wieder und zählt sie dann
        neue, n = _nur_gewaehlte(item, vorschlaege(item, ordner, wurzel))
        andere_sprache += n
        # Dokumentenarchiv: bestätigt veraltete Dateien fallen weg, Verdacht steht in der Prüfung
        neue, veraltet, vermerk = zertifikatsserver.pruefe(item, neue)
        item.findings.extend(neue)
        if vermerk:
            saetze.append(vermerk)
        # Suchdienst nur ohne Archiv und wenn der Ordner keinen Vorschlag hat, der noch zählt
        if rag.an() and not zertifikatsserver.an() and not _zaehlt(item, veraltet):
            melde("Ähnlichkeitssuche")
            neue, ergebnis, geprueft = rag_vorschlaege(item, ordner, wurzel)
            neue, n = _nur_gewaehlte(item, neue)
            andere_sprache += n
            gefunden = len(neue)
            neue, alt, _ = zertifikatsserver.pruefe(item, neue)
            veraltet |= alt
            item.findings.extend(neue)
            saetze.append(rag_satz(ergebnis, gefunden, gefunden - len(neue), geprueft) + ".")
        # Internet nur, wenn Ordner und Suchdienst keinen Vorschlag haben, der noch zählt
        if not _zaehlt(item, veraltet):
            if not lauf.versuche:
                saetze.append(INTERNET_AUS)
            else:
                melde("Internet")
                angabe = pruefung.lies_typangabe(item.manufacturer_article_no)
                funde, urls, vermerk = internet.suche(item, angabe, lauf, wurzel)
                neu_internet, n = _nur_gewaehlte(item, _neue(item, angabe, funde, wurzel, urls))
                andere_sprache += n
                item.findings.extend(neu_internet)
                saetze.append(internet_satz(vermerk, len(neu_internet)))
        # nach dem Internet, damit dessen Entscheidung unberührt bleibt; dazu die Ordner der
        # Funde der Ähnlichkeitssuche, die außerhalb des Herstellerordners liegen
        for o in ordner + _rag_ordner(item, wurzel, ordner):
            item.findings.extend(sprachversionen.nachladen(item, o, wurzel))
        # Von mehreren Fassungen derselben Erklärung bleibt nur die neueste ein Vorschlag
        aeltere = aeltere_fassungen(item, wurzel)
        for f in aeltere:
            item.findings.remove(f)
        if aeltere:
            saetze.append(FASSUNG_SATZ[len(aeltere) == 1].format(n=len(aeltere)))
        if andere_sprache:
            saetze.append(SPRACHE_SATZ[andere_sprache == 1].format(
                n=andere_sprache, sprachen=_sprachnamen(sprachversionen.gewaehlt(item))))
        # Lokal gefunden und ohne Befund: der Agent gibt selbst frei, markiert als automatisch
        if current_app.config.get("AUTOMATISCH_FREIGEBEN", True):
            freigabe.automatisch(item, wurzel)
        item.searched_at = datetime.now(timezone.utc)
        item.searched_in = " ".join(s for s in saetze if s)
        status.aktualisiere(item)


# --- „Gesucht in“: ganze Sätze für die Sachbearbeiterin -----------------------------------------
# Die Rohtexte von internet.py (Telegrammstil, Fehlernamen; die Datei bleibt unverändert) werden
# hier übersetzt, Einzelheiten stehen im Log. zertifikatsserver.pruefe liefert schon ganze Sätze.

# [mehrere, eins]: Funde, die es nur in nicht gewählten Sprachen gibt (Bom.sprachen)
SPRACHE_SATZ = ("{n} gefundene Dokumente liegen nur in anderen Sprachen vor als den gewählten "
                "({sprachen}) und sind deshalb nicht vorgeschlagen.",
                "Ein gefundenes Dokument liegt nur in einer anderen Sprache vor als den gewählten "
                "({sprachen}) und ist deshalb nicht vorgeschlagen.")


# [mehrere, eine]: ältere Fassungen einer Konformitätserklärung, zu der eine neuere vorliegt
FASSUNG_SATZ = ("{n} ältere Fassungen der Konformitätserklärung hat der Agent weggelassen, weil "
                "eine neuere vorliegt.",
                "Eine ältere Fassung der Konformitätserklärung hat der Agent weggelassen, weil "
                "eine neuere vorliegt.")


def aeltere_fassungen(item, wurzel):
    """Offene Vorschläge der Position, die eine ältere Fassung einer anderen
    Konformitätserklärung derselben Position sind (Vorschlag oder Freigabe). Zwei Regeln, beide
    nur, wenn die neuere jede Sprache der älteren enthält:

    - derselbe Typ im Dokument, und die andere Erklärung ist später ausgestellt;
    - diese Erklärung nennt nur die alte Richtlinie 94/9/EG, die andere 2014/34/EU.

    Vorher speichern (flush): erst dann sind die Angaben aus dem Dokument gelesen
    (services/belege.py). Ohne lesbares Datum und ohne Richtlinie bleibt jede Fassung stehen."""
    db.session.flush()
    erkl = [f for f in item.findings if f.status in ("vorgeschlagen", "freigegeben")
            and f.doc_type in pruefung.ERKLAERUNG]
    if len(erkl) < 2:
        return []
    from . import angaben as gelesen

    def datum(f):
        a = (f.angaben or {}).get("ausstellungsdatum") or {}
        if a.get("wert"):
            return a["wert"]
        pfad = freigabe.datei(f, wurzel)  # ohne Ausstellungsdatum: der Stand aus Kopf oder Fuß
        kopf = gelesen.kopfdatum(pdf_text.lies(pfad).seiten) if pfad else None
        return kopf and kopf["wert"]

    def richtlinien(f):
        return {r["wert"] for r in (f.angaben or {}).get("richtlinien") or []}

    def deckt_sprachen(neu, alt):
        a, n = (sprachversionen.sprachen_von(x.languages, x.file_path) for x in (alt, neu))
        return not a or not n or a <= n

    def typ(f):
        return pruefung._normiert(f.matched_term or "")[0]

    def aelter_als(f, g):
        if g is f or g.doc_type != f.doc_type or not deckt_sprachen(g, f):
            return False
        df, dg = datum(f), datum(g)
        if typ(f) and typ(f) == typ(g) and df and dg and df < dg:
            return True
        rf = richtlinien(f)
        return "94/9/EG" in rf and "2014/34/EU" not in rf and "2014/34/EU" in richtlinien(g)

    return [f for f in erkl if f.status == "vorgeschlagen" and any(aelter_als(f, g) for g in erkl)]


def _sprachnamen(kuerzel):
    return ", ".join(status.SPRACHNAME.get(s, s) for s in kuerzel)


def _nur_gewaehlte(item, neue):
    """(behalten, Zahl der weggelassenen): Hat der Vorgang eigene Sprachen, zählen nur Funde in
    einer davon; Funde ohne erkennbare Sprache bleiben."""
    behalten = [f for f in neue if sprachversionen.passt_zur_wahl(item, f)]
    return behalten, len(neue) - len(behalten)


INTERNET_AUS = "Im Internet hat der Agent nicht gesucht, weil die Websuche ausgeschaltet ist."
ARCHIV_ORDNER = "_upload/zertifikatsserver"


def archiv_satz(ordner, erreichbar):
    """Erster Satz, wenn die lokale Stufe das Dokumentenarchiv ist."""
    if not erreichbar:
        return ("Gesucht hat der Agent in der Datenbank. Das Dokumentenarchiv war gerade nicht "
                "erreichbar, deshalb hat er dort nicht gesucht.")
    if not ordner:
        return ("Gesucht hat der Agent in der Datenbank und im Dokumentenarchiv. Dort gibt es "
                "keine aktuellen Dokumente dieses Herstellers.")
    return "Gesucht hat der Agent in der Datenbank und im Dokumentenarchiv."


def ordner_satz(wurzel, ordner):
    """Erster Satz: wo der Agent gesucht hat (Datenbank, Ordner des Herstellers, Archiv)."""
    archiv = any(ARCHIV_ORDNER in o.as_posix() for o in ordner)
    eigen = [o for o in ordner if ARCHIV_ORDNER not in o.as_posix()]
    if eigen and archiv:
        return ("Gesucht hat der Agent in der Datenbank, im Ordner des Herstellers und in den "
                "Dokumenten, die nur das Dokumentenarchiv hat.")
    if eigen:
        return "Gesucht hat der Agent in der Datenbank und im Ordner des Herstellers."
    if archiv:
        return ("Gesucht hat der Agent in der Datenbank und in den Dokumenten des Herstellers im "
                "Dokumentenarchiv. Einen eigenen Ordner für diesen Hersteller gibt es nicht.")
    return ("Gesucht hat der Agent in der Datenbank. Einen Ordner für diesen Hersteller gibt es "
            "nicht.")


def _seite(grund):
    """Grund aus internet.py als Satz; None, wenn er nichts Neues sagt."""
    if grund == "Grenze erreicht":
        return "Danach war die eingestellte Zahl an Versuchen erreicht."
    if grund == "kein passendes Dokument":
        return "Ein passendes Dokument war nicht dabei."
    if m := re.match(r"(\S+) sperrt automatische Abrufe", grund):
        return f"Die Webseite {m[1]} lässt keine automatischen Abrufe zu."
    if m := re.match(r"(\S+) verlangt eine Prüfung", grund):
        return f"Die Webseite {m[1]} verlangt eine Bestätigung, dass ein Mensch sie aufruft."
    if m := re.match(r"(\S+) nicht erreichbar", grund):
        return f"Die Webseite {m[1]} war nicht erreichbar."
    if grund == "nur Datenblätter":
        return ("Gefunden hat er dort nur Datenblätter. Die sind kein Nachweis und deshalb nicht "
                "vorgeschlagen.")
    if "html2markdown" in grund or "Browser" in grund or "Zwischenspeicher" in grund:
        return "Wegen eines technischen Problems konnte die Websuche nicht weitersuchen."
    return None


def internet_satz(vermerk, gefunden=0):
    """Ergebnis von internet.suche als Sätze."""
    current_app.logger.info("Websuche: %s", vermerk)
    if vermerk.startswith("Internet übersprungen"):
        return ("Im Internet hat der Agent nicht gesucht, weil die Webseite des Herstellers "
                "nicht bekannt ist.")
    m = re.match(r"Internet \((?P<domain>[^,]+), (?P<a>\d+) von (?P<b>\d+) Versuchen"
                 r"(?:; (?P<gruende>.*))?\)$", vermerk)
    if not m:
        return "Im Internet hat der Agent auf der Webseite des Herstellers gesucht."
    a, b = int(m["a"]), int(m["b"])
    saetze = [f"Im Internet hat der Agent auf {m['domain']} gesucht und dabei "
              f"{'keine Seite' if a == 0 else 'eine Seite' if a == 1 else f'{a} Seiten'} von "
              f"höchstens {b} abgerufen."]
    if gefunden:
        saetze.append("Dort hat er ein passendes Dokument gefunden." if gefunden == 1 else
                      f"Dort hat er {gefunden} passende Dokumente gefunden.")
    gruende = (m["gruende"] or "").split("; ") if m["gruende"] else []
    texte = [t for g in gruende if (t := _seite(g))]
    andere = sum(1 for g in gruende if not _seite(g))
    if andere:
        texte.append("Eine Seite ließ sich nicht abrufen." if andere == 1 else
                     f"{andere} Seiten ließen sich nicht abrufen.")
    saetze += list(dict.fromkeys(texte))
    return " ".join(saetze)


def _rag_ordner(item, wurzel, ordner):
    """Ordner der Funde der Ähnlichkeitssuche (mit Typ-Treffer), die in keinem der Ordner der
    Position liegen: dort sucht der zweite Durchgang ihre anderen Sprachfassungen."""
    neu = []
    for f in item.findings:
        if f.source != "suchdienst" or not f.matched_term or not f.file_path:
            continue
        o = (wurzel / f.file_path).parent
        if o.is_dir() and not any(o == x or o.is_relative_to(x) for x in ordner + neu):
            neu.append(o)
    return neu


def _zaehlt(item, veraltet):
    """Hat die Position einen Vorschlag aus Ordner oder Suchdienst, der zählt? Nicht abgelehnt,
    nicht als veraltet bestätigt; vom Suchdienst nur mit Typ-Treffer."""
    return any(f.source in ("ordner", "suchdienst") and f.status != "abgelehnt"
               and f.file_hash not in veraltet and (f.source == "ordner" or f.matched_term)
               for f in item.findings)


def vorschlaege(item, ordner, wurzel):
    """Neue Vorschläge für die Position aus den Ordnern (Liste oder ein Ordner), ohne Dubletten."""
    angabe = pruefung.lies_typangabe(item.manufacturer_article_no)
    funde = []  # (pfad, pdf, treffer)
    for o in [ordner] if isinstance(ordner, Path) else ordner:
        for pfad in sorted(p for p in o.rglob("*") if p.suffix.lower() == ".pdf"):
            pdf = pdf_text.lies(pfad)
            treffer = pruefung.finde_typ(angabe, pdf.seiten, pdf_text.dateiname(pfad))
            if treffer:
                funde.append((pfad, pdf, treffer))
    return _neue(item, angabe, funde, wurzel)


def rag_vorschlaege(item, ordner, wurzel):
    """Stufe Suchdienst: (neue Vorschläge, Ergebnis, Zahl der geprüften Dateien). Ergebnis ist
    einer aus RAG_SAETZE.

    Die besten RAG_DATEIEN Kandidaten-Dateien werden wie Dateien im Ordner geprüft. Mit
    Typ-Treffer: Vorschläge wie aus dem Ordner (gleiche Auswahl, Prüfung, Belege). Ohne: nur
    Dateien aus den Ordnern der Position, höchstens RAG_OHNE_TREFFER, immer „unklar“.
    """
    try:
        kandidaten = rag.kandidaten(item, wurzel)
    except rag.IndexFehlt:
        return [], "baut", 0
    except rag.NichtErreichbar:
        return [], "weg", 0
    angabe = pruefung.lies_typangabe(item.manufacturer_article_no)
    bekannt = {f.file_hash for f in item.findings}  # auch abgelehnte: nicht neu vorschlagen
    mit, ohne, rang = [], [], {}
    for k in kandidaten[:RAG_DATEIEN]:
        pdf = pdf_text.lies(k.pfad)
        if pdf.sha256 in bekannt or pdf.sha256 in rang:
            continue
        rang[pdf.sha256] = k
        treffer = pruefung.finde_typ(angabe, pdf.seiten, pdf_text.dateiname(k.pfad))
        (mit if treffer else ohne).append((k.pfad, pdf, treffer))
    if mit:
        neu = _neue(item, angabe, mit, wurzel, quelle="suchdienst")[:RAG_VORSCHLAEGE]
        for f in neu:
            f.check_message = f"{f.check_message} {RAG_GEFUNDEN}"
    else:
        eigene = [(p, pdf) for p, pdf, _ in ohne
                  if any(p.is_relative_to(o) for o in ordner)][:RAG_OHNE_TREFFER]
        neu = [_ohne_treffer(item, angabe, pdf, rang[pdf.sha256], wurzel) for _, pdf in eigene]
    if neu:
        return neu, "gefunden", len(rang)
    if not kandidaten:
        return neu, "nichts", 0
    return neu, ("keins" if rang else "bekannt"), len(rang)


ZAHLWORT = {1: "ein", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf"}
RAG_SAETZE = {
    "baut": "Die Ähnlichkeitssuche bereitet die Dokumente noch vor und konnte noch nicht suchen",
    "weg": "Die Ähnlichkeitssuche war gerade nicht verfügbar",
    "nichts": "Die Ähnlichkeitssuche hat kein ähnliches Dokument gefunden",
    "bekannt": ("Die Ähnlichkeitssuche hat nur Dokumente gefunden, die für diese Position schon "
                "vorgeschlagen oder abgelehnt sind"),
}


def rag_satz(ergebnis, gefunden=0, veraltet=0, geprueft=0):
    """Satz für „gesucht in“, wie man ihn einer Kollegin sagt (ohne Punkt am Ende)."""
    if ergebnis in RAG_SAETZE:
        return RAG_SAETZE[ergebnis]
    if ergebnis == "keins":
        if geprueft == 1:
            return ("Die Ähnlichkeitssuche hat ein ähnliches Dokument angesehen, es passt aber "
                    "nicht zur Position")
        return (f"Die Ähnlichkeitssuche hat {ZAHLWORT.get(geprueft, geprueft)} ähnliche "
                f"Dokumente angesehen, keines passt zur Position")
    satz = ("Die Ähnlichkeitssuche hat ein mögliches Dokument gefunden" if gefunden == 1 else
            f"Die Ähnlichkeitssuche hat {ZAHLWORT.get(gefunden, gefunden)} mögliche Dokumente "
            f"gefunden")
    if veraltet:
        satz += (". Im Dokumentenarchiv ist es als veraltet bestätigt, deshalb wird es nicht "
                 "vorgeschlagen" if gefunden == 1 else
                 f". {ZAHLWORT.get(veraltet, veraltet).capitalize()} davon "
                 f"{'ist' if veraltet == 1 else 'sind'} im Dokumentenarchiv als veraltet "
                 f"bestätigt und {'wird' if veraltet == 1 else 'werden'} nicht vorgeschlagen")
    return satz


def _ohne_treffer(item, angabe, pdf, kandidat, wurzel):
    """Vorschlag aus einer Kandidaten-Datei, in der der Typ nicht steht: immer „unklar“, mit der
    ähnlichen Textstelle als Fundstelle und dem Grund."""
    pfad = kandidat.pfad.relative_to(wurzel).as_posix()
    f = _finding(item, angabe, pdf, None, [pfad], quelle="suchdienst")
    stelle = re.sub(r"\s+", " ", kandidat.text).strip()
    seite = f"Seite {kandidat.seite}" if kandidat.seite else "eine Stelle darin"
    f.page, f.snippet = kandidat.seite, stelle[:400] or None
    f.check_message = (f"{RAG_HINWEIS}, weil {seite} ähnlich klingt wie die Position. Der Typ "
                       f"steht nicht darin. Bitte prüfen Sie, ob es trotzdem passt. "
                       f"{f.check_message}")
    if f.check_result == "ok":  # ohne Typ-Treffer nie „ok“ (pruefe_typ sorgt schon dafür)
        f.check_result = "unklar"
    return f


def auswerten(item, pfad, wurzel):
    """Vorschlag aus einer einzelnen Datei, z. B. von Hand hochgeladen (services/hochladen.py):
    dieselbe Auswertung wie im Ordner, aber auch ohne Typ-Treffer („unklar“ mit Begründung)."""
    angabe = pruefung.lies_typangabe(item.manufacturer_article_no)
    pdf = pdf_text.lies(pfad)
    treffer = pruefung.finde_typ(angabe, pdf.seiten, pdf_text.dateiname(pfad))
    return _finding(item, angabe, pdf, treffer, [pfad.relative_to(wurzel).as_posix()])


def _neue(item, angabe, funde, wurzel, urls=None, quelle=None):
    """Findings aus den Funden [(pfad, pdf, treffer)]; urls: Quelle je Pfad (Internet)."""
    urls = urls or {}
    # Steht der genaue Typ in einem Text, sind Teil- und Dateinamen-Treffer nur Rauschen. Sonst
    # nur die Dokumente mit der längsten Übereinstimmung („ET 200iSP IM 152-1“ vor „ET 200iSP“).
    genau = [f for f in funde if angabe.art == "typ" and f[2].genau and f[2].im_text]
    laengste = max((f[2].laenge for f in funde), default=0)
    funde = genau or [f for f in funde if f[2].laenge == laengste]

    for f in [f for f in item.findings if f.status == "vorgeschlagen"
              and (UNGELESEN in (f.check_message or "")
                   or UNGELESEN_ALT in (f.check_message or ""))]:
        item.findings.remove(f)  # unentschieden und vorläufig: neu prüfen, OCR ist evtl. fertig
    bekannt = {f.file_hash for f in item.findings}  # auch abgelehnte: nicht neu vorschlagen
    pfade = {}
    for pfad, pdf, _ in funde:
        pfade.setdefault(pdf.sha256, []).append(pfad.relative_to(wurzel).as_posix())
    neu = []
    for pfad, pdf, treffer in funde:
        if pdf.sha256 in bekannt:
            continue
        bekannt.add(pdf.sha256)
        neu.append(_finding(item, angabe, pdf, treffer, pfade[pdf.sha256], urls.get(pfad),
                            quelle))
    return neu


def _mit_ocr(pdf, treffer, kennz, ergebnis, meldung):
    """Scans: Stammen Fundstelle oder Kennzeichnung aus OCR-Text, ist nichts sicher (nie „ok“),
    denn OCR verliest Zeichen. Per OCR gelesene und noch nicht gelesene Seiten werden genannt."""
    if pdf.ungelesen:
        meldung += f" {_seiten_satz(pdf.ungelesen)} {UNGELESEN}."
    if not pdf.ocr:
        return ergebnis, meldung
    belege = {nr for nr in pdf.ocr if (treffer is not None and nr == treffer.seite)
              or set(kennz) & set(pruefung.kennzeichnungen(pdf.seiten[nr - 1]))}
    if not belege:
        return ergebnis, (f"{meldung} {_seiten_satz(pdf.ocr)} eingescannt, der Text dort ist nicht "
                          "sicher gelesen.")
    return ("warnung" if ergebnis == "warnung" else "unklar",
            f"{_seiten_satz(pdf.ocr)} eingescannt, der Text ist nicht sicher gelesen. Bitte "
            f"prüfen Sie die Angaben am Seitenbild. {meldung}")


def _seiten_satz(nummern):
    """„Seite 2 ist“, „Seiten 1–3 sind“ (ohne das Verb am Ende bei „ist eingescannt …“)."""
    seiten = pdf_text.seitenangabe(nummern)
    return f"{seiten} {'sind' if seiten.startswith('Seiten') else 'ist'}"


def _finding(item, angabe, pdf, treffer, pfade, url=None, quelle=None):
    seiten = pdf.seiten
    # Kennzeichnung bevorzugt von der Seite des Treffers, sonst aus dem ganzen Dokument
    kennz = pruefung.kennzeichnungen(seiten[treffer.seite - 1]) if treffer and treffer.seite else []
    kennz = kennz or [k for s in seiten for k in pruefung.kennzeichnungen(s)]
    kennz = list(dict.fromkeys(kennz))
    ergebnis, meldung = pruefung.bewerte(
        pruefung.pruefe_typ(angabe, treffer, pdf.lesbar),
        pruefung.pruefe_zone(item.location, kennz))
    ergebnis, meldung = _mit_ocr(pdf, treffer, kennz, ergebnis, meldung)
    if len(pfade) > 1:
        meldung += (" Dieselbe Datei liegt auch unter "
                    + ", ".join(PurePosixPath(p).name for p in pfade[1:]) + ".")
    if url:
        meldung += f" Das Dokument stammt von der Webseite des Herstellers ({url})."
    return Finding(
        source=quelle or ("internet" if url else "ordner"), url=url, status="vorgeschlagen",
        doc_type=dokumentart(seiten, PurePosixPath(pfade[0]).name), file_path=pfade[0],
        file_hash=pdf.sha256,
        languages=sprachen(seiten),
        page=treffer and treffer.seite, matched_term=treffer and treffer.wie_geschrieben,
        snippet=(treffer and (treffer.textstelle if treffer.im_text
                              else f"Treffer im Dateinamen: {pfade[0]}")),
        ex_marking_raw="; ".join(kennz[:3]) or None, certificate_no=zertifikatsnummern(seiten),
        check_result=ergebnis, check_message=meldung)
