"""Angaben aus einem PDF lesen, jede mit Seite und Textstelle.

Nur Regeln im Code, kein Sprachmodell. Was nicht sicher erkennbar ist, steht als unsicher da oder
fehlt. Scans ohne Textebene sind „nicht lesbar“, bis die Texterkennung (ocr.py) im Hintergrund
ihren Text liefert; dann läuft dieselbe Auslesung, Angaben von solchen Seiten tragen quelle „ocr“.
Die Muster für Dokumentart, Sprachen und Zertifikatsnummern stammen aus backend/app/services/suche.py.
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import pymupdf

# Dokumentart: das früheste Stichwort im Kopf des Dokuments zählt (wie im Backend)
ARTEN = (
    (re.compile(r"konformitätserklärung|konformitaetserklaerung|declaration of conformity"
                r"|déclaration (?:ue |ce )?de conformité"), "konformitaetserklaerung"),
    (re.compile(r"baumusterprüf|type[ -]?examination"), "baumusterpruefbescheinigung"),
    (re.compile(r"betriebsanleitung|bedienungsanleitung|instruction manual|operating instructions"
                r"|manuel d'instructions|notice d'instructions"), "bedienungsanleitung"),
)
UK_KOPF = re.compile(r"\buksi\b")
KOPF = 3000  # Zeichen

SPRACHE = {  # häufige kurze Wörter je Sprache
    "de": {"und", "der", "die", "das", "für", "mit", "nicht", "wird", "werden", "ist", "den",
           "dem"},
    "en": {"the", "and", "of", "with", "for", "is", "are", "this", "be", "shall", "to"},
    "fr": {"les", "et", "pour", "avec", "est", "sont", "du", "une", "aux", "dans", "être"},
}
ZERTIFIKAT = re.compile(r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöü]{1,7}[ \t]?\d{2}[ \t]?(?:ATEX|UKEX)"
                        r"[ \t]?[A-Z]?[ \t]?\d{3,5}(?:[ \t]?[XU])?\b")
IECEX = re.compile(r"\bIECEx[ \t][A-Z]{2,6}[ \t]\d{2}\.\d{4}[XU]?\b")

RICHTLINIE = re.compile(r"\b(?:94\s*/\s*9\s*/\s*(?:EG|EC|CE|EWG)|2014\s*/\s*34\s*/\s*(?:EU|UE))\b")
# „EN 60079-0:2012“, „EN IEC 60079-0:2018“, „IEC 60079-11:2011“, „EN 60079-0:2012+A11:2013“
# (zählt als 2012: Änderungen sind keine neue Ausgabe). Auch über Zeilenumbrüche hinweg, wie in
# Tabellen: „EN IEC\n60079-11:2024-12“, „EN 60079-1\n:\n2014“. Ohne Jahr („EN 60079-14“) keine
# Angabe: eine undatierte Nennung sagt nichts über die Ausgabe.
NORM = re.compile(r"\b(?:EN|IEC|DIN\s+EN)(?:\s+IEC)?\s*60079\s*-\s*(\d{1,2})"
                  r"\s*[:/]\s*((?:19|20)\d{2})\b")
OCR_HINWEIS = "Aus der Texterkennung eines Scans, bitte im PDF prüfen."

MONATE = {
    "januar": 1, "january": 1, "janvier": 1, "jan": 1, "februar": 2, "february": 2,
    "février": 2, "fevrier": 2, "feb": 2, "märz": 3, "maerz": 3, "march": 3, "mars": 3, "mar": 3,
    "april": 4, "avril": 4, "apr": 4, "mai": 5, "may": 5, "juni": 6, "june": 6, "juin": 6,
    "jun": 6, "juli": 7, "july": 7, "juillet": 7, "jul": 7, "august": 8, "août": 8, "aout": 8,
    "aug": 8, "september": 9, "septembre": 9, "sep": 9, "sept": 9, "oktober": 10, "october": 10,
    "octobre": 10, "oct": 10, "okt": 10, "november": 11, "novembre": 11, "nov": 11,
    "dezember": 12, "december": 12, "décembre": 12, "decembre": 12, "dec": 12, "dez": 12,
}
_MONAT = "|".join(sorted(MONATE, key=len, reverse=True))
DATUM = re.compile(
    r"\b(?P<iso>(?:19|20)\d{2}-\d{2}-\d{2})\b"
    r"|\b(?P<t>\d{1,2})\.[ \t]?(?P<m>\d{1,2})\.[ \t]?(?P<j>(?:19|20)\d{2})\b"
    rf"|\b(?P<t2>\d{{1,2}})(?:st|nd|rd|th|er)?\.?\s+(?P<mn>{_MONAT})\.?\s+(?P<j2>(?:19|20)\d{{2}})\b",
    re.I)
# Daten, die zu einer zitierten Richtlinie gehören („Richtlinie 2014/34/EU vom 26. Februar 2014“)
RICHTLINIE_DAVOR = re.compile(r"(?:\b\d{2,4}\s*/\s*\d{1,3}\s*/\s*(?:EU|EG|EC|EWG|UE|CE)\b"
                              r"|council|rates|parlament\w*|parliament|conseil)"
                              r"[^.;]{0,60}?\b(?:vom|of|du|dated|datée?)\s*$", re.I)
DATUM_STICHWORT = re.compile(r"datum|date|ausstellung|ausgestellt|issued?|fait|délivr|signed"
                             r"|unterschrift|dated|ausgabe", re.I)
GUELTIG_BIS = re.compile(r"gültig bis|gueltig bis|valid until|valid to|expiry date|expires"
                         r"|expiration date|date of expiry|valable jusqu", re.I)


@dataclass
class Auswertung:
    sha256: str
    seiten: list  # Text je Seite, Seite 1 an Stelle 0; Seiten ohne Textebene mit OCR-Text
    doc_type: str
    angaben: list = field(default_factory=list)  # dicts wie modelle.Angabe
    ausstellungsdatum: date | None = None
    gueltig_bis: date | None = None
    seiten_ohne_text: list = field(default_factory=list)  # ohne Textebene im PDF, ab 1
    ocr_seiten: list = field(default_factory=list)  # davon per OCR gelesen

    @property
    def lesbar(self):
        return any(s.strip() for s in self.seiten)


def dateiname(name):
    """Dateiname mit „#U00e4“ aus dem Altbestand als „ä“."""
    return re.sub(r"#U([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), name)


def norm(text):
    """Für Vergleiche: klein, ohne Leer- und Satzzeichen; „…“ wie „...“."""
    return re.sub(r"[^a-z0-9äöüß]", "", (text or "").lower())


def lies_seiten(inhalt):
    """Text je Seite; kaputte Datei wie ein Scan ohne Text. Nullbytes fallen weg (PostgreSQL
    nimmt sie in Texten nicht an)."""
    try:
        with pymupdf.open(stream=inhalt, filetype="pdf") as doc:
            return [seite.get_text().replace("\x00", "") for seite in doc]
    except (pymupdf.FileDataError, RuntimeError, ValueError):
        return []


def textstelle(text, start, ende, rand=40):
    stelle = text[max(0, start - rand):ende + rand]
    return re.sub(r"\s+", " ", stelle).strip()


def auswerten(inhalt, ocr_texte=None):
    """Angaben aus dem PDF. ocr_texte: {Seite: Text} der Texterkennung für Seiten ohne
    Textebene; Angaben von diesen Seiten stehen mit quelle „ocr“ und Hinweis da."""
    seiten = lies_seiten(inhalt)
    ohne = [nr for nr, s in enumerate(seiten, start=1) if not s.strip()]
    ocr_seiten = []
    for nr in ohne:
        text = (ocr_texte or {}).get(str(nr)) or (ocr_texte or {}).get(nr) or ""
        if text.strip():
            seiten[nr - 1] = text.replace("\x00", "")
            ocr_seiten.append(nr)
    a = Auswertung(hashlib.sha256(inhalt).hexdigest(), seiten, "unbekannt",
                   seiten_ohne_text=ohne, ocr_seiten=ocr_seiten)
    if not a.lesbar:
        return a  # nicht lesbar: nichts raten
    a.doc_type, art = dokumentart(seiten)
    a.angaben.append(art)
    a.angaben += richtlinien(seiten) + normen(seiten) + zertifikatsnummern(seiten)
    a.angaben += sprachen(seiten)
    datum = ausstellungsdatum(seiten)
    if datum:
        a.ausstellungsdatum = date.fromisoformat(datum["wert"])
        a.angaben.append(datum)
    bis = gueltig_bis(seiten)
    if bis:
        a.gueltig_bis = date.fromisoformat(bis["wert"])
        a.angaben.append(bis)
    for x in a.angaben:
        if x["seite"] in ocr_seiten:
            x["quelle"] = "ocr"
            x["hinweis"] = " ".join(filter(None, (x["hinweis"] and x["hinweis"].rstrip(".") + ".",
                                                  OCR_HINWEIS)))
    return a


def _angabe(art, wert, seite=None, stelle=None, sicher=True, hinweis=None):
    return {"art": art, "wert": wert, "seite": seite, "textstelle": stelle, "sicher": sicher,
            "hinweis": hinweis, "quelle": "pdf"}


def dokumentart(seiten):
    """(doc_type, Angabe). Das früheste Stichwort im Kopf zählt; ohne Treffer „sonstiges“."""
    kopf = re.sub(r"\s+", " ", " ".join(seiten)[:KOPF].lower()).replace(" -", "-")
    funde = [(m.start(), m.end(), art) for muster, art in ARTEN if (m := muster.search(kopf))]
    if not funde:
        return "sonstiges", _angabe("dokumentart", "sonstiges", 1, None, False,
                                    "kein Stichwort für die Dokumentart im Kopf gefunden")
    start, ende, art = min(funde)
    stelle = textstelle(kopf, start, ende)
    seite = _seite_von(seiten, kopf[start:ende])
    if art != "bedienungsanleitung":
        davor = kopf[max(0, start - 15):start]  # „EU and UK Declaration“ zählt als EU
        uk = not re.search(r"\b(?:eu|eg|ce)\b", davor) and (re.search(r"\buk\b", davor)
                                                             or UK_KOPF.search(kopf))
        art = f"{'uk' if uk else 'eu'}_{art}"
    return art, _angabe("dokumentart", art, seite, stelle)


def _seite_von(seiten, wort):
    """Erste Seite, auf der das Wort steht (ohne Rücksicht auf Groß- und Kleinschreibung)."""
    muster = re.compile(r"\s+".join(map(re.escape, wort.split())), re.I)
    return next((nr for nr, s in enumerate(seiten, start=1) if muster.search(s)), 1)


def _je_seite(seiten, muster):
    for nr, text in enumerate(seiten, start=1):
        for m in muster.finditer(text):
            yield nr, text, m


def richtlinien(seiten):
    """Genannte Richtlinien, je Richtlinie die erste Fundstelle: „94/9/EG“, „2014/34/EU“."""
    funde = {}
    for nr, text, m in _je_seite(seiten, RICHTLINIE):
        wert = "94/9/EG" if m.group(0).startswith("94") else "2014/34/EU"
        funde.setdefault(wert, _angabe("richtlinie", wert, nr, textstelle(text, m.start(), m.end())))
    return list(funde.values())


def normen(seiten):
    """Genannte Ausgaben der Normenreihe 60079, je Ausgabe die erste Fundstelle."""
    funde = {}
    for nr, text, m in _je_seite(seiten, NORM):
        wert = f"60079-{int(m.group(1))}:{m.group(2)}"
        funde.setdefault(wert, _angabe("norm", wert, nr, textstelle(text, m.start(), m.end())))
    return list(funde.values())


def zertifikatsnummern(seiten):
    """ATEX- und UKEX-Nummern wie geschrieben; ohne solche die IECEx-Nummer."""
    funde = {}
    for muster in (ZERTIFIKAT, IECEX):
        for nr, text, m in _je_seite(seiten, muster):
            wert = re.sub(r"\s+", " ", m.group(0))
            funde.setdefault(norm(wert), _angabe("zertifikatsnummer", wert, nr,
                                                 textstelle(text, m.start(), m.end())))
        if funde:
            break
    return list(funde.values())[:5]


def _woerter(text):
    woerter = Counter(re.findall(r"[a-zäöüßéèàçê]+", text.lower()))
    return {s: sum(woerter[w] for w in liste) for s, liste in SPRACHE.items()}


def sprachen(seiten):
    """Sprachen wie im Backend (Kennung „(DE)“ oder häufige Wörter), Beleg: die Seite mit den
    meisten Wörtern dieser Sprache."""
    text = " ".join(seiten)
    anzahl = _woerter(text)
    kennung = {k.lower() for k in re.findall(r"\((DE|EN|FR)\)", text)}
    je_seite = [_woerter(s) for s in seiten]
    ergebnis = []
    for s in SPRACHE:
        if s in kennung or anzahl[s] >= 5 and anzahl[s] * 5 >= max(anzahl.values()):
            seite = max(range(len(seiten)), key=lambda i: je_seite[i][s]) + 1
            ergebnis.append(_angabe("sprache", s, seite, None,
                                    hinweis=f"{anzahl[s]} typische Wörter im Dokument"))
    return ergebnis


def _datum(m):
    try:
        if m.group("iso"):
            return date.fromisoformat(m.group("iso"))
        if m.group("t"):
            return date(int(m.group("j")), int(m.group("m")), int(m.group("t")))
        return date(int(m.group("j2")), MONATE[m.group("mn").lower()], int(m.group("t2")))
    except ValueError:
        return None


def daten(seiten):
    """Alle Datumsangaben außer denen zitierter Richtlinien: [(Datum, Seite, Text, Treffer)]."""
    funde = []
    for nr, text, m in _je_seite(seiten, DATUM):
        tag = _datum(m)
        if tag is None or RICHTLINIE_DAVOR.search(text[max(0, m.start() - 100):m.start()]):
            continue
        funde.append((tag, nr, text, m))
    return funde


def ausstellungsdatum(seiten):
    """Spätestes Datum mit Stichwort davor („Datum“, „Date of issue“, „Ort und Datum“); ohne
    Stichwort das späteste Datum überhaupt, dann als unsicher. Keins: None."""
    funde = daten(seiten)
    mit_stichwort = [f for f in funde
                     if DATUM_STICHWORT.search(f[2][max(0, f[3].start() - 80):f[3].start()])]
    kandidaten, sicher = (mit_stichwort, True) if mit_stichwort else (funde, False)
    if not kandidaten:
        return None
    tag, nr, text, m = max(kandidaten, key=lambda f: (f[0], -f[1]))
    return _angabe("ausstellungsdatum", tag.isoformat(), nr, textstelle(text, m.start(), m.end()),
                   sicher, None if sicher else
                   "kein Stichwort wie „Datum“ davor: spätestes Datum im Dokument genommen")


def gueltig_bis(seiten):
    """Datum direkt nach „gültig bis“, „valid until“, „expiry date“; sonst None."""
    for nr, text, m in _je_seite(seiten, GUELTIG_BIS):
        d = DATUM.search(text, m.end(), m.end() + 60)
        if d and (tag := _datum(d)):
            return _angabe("gueltig_bis", tag.isoformat(), nr, textstelle(text, m.start(), d.end()))
    return None


# Typbezeichnung im Dateinamen: Stichwörter, Prüfstellen und Zeichen, die kein Typ sind
KEIN_TYP = {"ATEX", "IECEX", "UKEX", "KEMA", "DEKRA", "PTB", "BVS", "INERIS", "ITS", "IBEXU",
            "TÜV", "TUEV", "EGBAUM", "ENG", "GER", "FRA", "ENGL", "PDF", "ZONE", "RAL", "EMV",
            "ROHS", "CSA", "IECEX-ZERTIFIKAT", "UL", "SIMATIC", "SIMATEC"}
KUERZEL_OHNE_TYP = {"EU", "UK", "EG", "CE", "DE", "EN", "FR", "UL"}  # „EU_5629123“ ist kein Typ
DOKUMENT_WORT = re.compile(r"[-.](?:konf\w*|erkl\w*|beschein\w*|baumuster\w*|anleitung"
                           r"|engl?|ger|fra)$", re.I)


def ist_typ(wort):
    """Sieht nach Typbezeichnung aus: mindestens 3 Zeichen, mehr Groß- als Kleinbuchstaben,
    zwei Großbuchstaben oder Großbuchstabe und Ziffer; keine Zertifikatsnummer."""
    gross = sum(c.isupper() for c in wort)
    klein = sum(c.islower() for c in wort)
    return (len(norm(wort)) >= 3 and gross >= klein and gross >= 1
            and (gross >= 2 or any(c.isdigit() for c in wort))
            and wort.upper() not in KEIN_TYP and not re.search(r"ATEX|UKEX|IECEx", wort, re.I)
            and not re.fullmatch(r"RAL\d+", wort))


def _woerter_im_namen(name):
    """Teile des Dateinamens (ohne Endung), getrennt an „_“ und Leerzeichen; ein kurzes Kürzel
    vor einem Teil mit Ziffer am Anfang gehört dazu: „KEL_94XX“ -> „KEL 94XX“."""
    stamm = re.sub(r"\.pdf$", "", dateiname(name), flags=re.I)
    stamm = re.sub(r"\[[^\]]*\]", " ", stamm)
    teile = [DOKUMENT_WORT.sub("", t).strip("-.,") for t in re.split(r"[_\s]+", stamm)]
    teile = [t for t in teile if t]
    woerter = []
    for t in teile:
        if (woerter and re.fullmatch(r"[A-Z]{2,3}", woerter[-1]) and t[:1].isdigit()
                and woerter[-1] not in KUERZEL_OHNE_TYP):
            woerter[-1] += " " + t
        else:
            woerter.append(t)
    return woerter


def typen(namen, seiten, hersteller_woerter=()):
    """Typbezeichnungen aus den Dateinamen, bestätigt im Text wo möglich.

    Ergebnis: [(Typ, Beleg, Seite)]. Ein Wort im Dateinamen, das wie ein Typ aussieht, zählt; steht
    es im Text, ist die Seite der Beleg, sonst „nur Dateiname“. Ein klein geschriebenes Wort
    („festo_dsm_konf“) zählt nur, wenn es im Text als Typ geschrieben steht („DSM“).
    """
    weg = {norm(w) for w in hersteller_woerter}
    ergebnis = {}
    for name in namen:
        for wort in _woerter_im_namen(name):
            if norm(wort) in weg or norm(wort) in {norm(k) for k in KEIN_TYP}:
                continue
            fund = _im_text(wort, seiten)
            if ist_typ(wort):
                typ = wort
            elif fund and ist_typ(fund[1]):
                typ = fund[1]
            else:
                continue
            if norm(typ) in ergebnis:
                continue
            if fund:
                ergebnis[norm(typ)] = (typ, f"Seite {fund[0]}: „{fund[2]}“", fund[0])
            else:
                ergebnis[norm(typ)] = (typ, f"nur Dateiname: {dateiname(name)}", None)
    return list(ergebnis.values())


def _im_text(wort, seiten):
    """(Seite, wie geschrieben, Textstelle) des Worts als eigenes Wort im Text, sonst None.
    Leerzeichen und Bindestriche im Wort dürfen im Text fehlen oder anders sein."""
    teile = [re.escape(t) for t in re.split(r"[\s\-]+", wort) if t]
    muster = re.compile(r"(?<![\w-])" + r"[\s\-]?".join(teile) + r"(?![\w])", re.I)
    for nr, text in enumerate(seiten, start=1):
        if m := muster.search(text):
            return nr, m.group(0), textstelle(text, m.start(), m.end(), 30)
    return None
