"""Weitere Angaben aus einem Ex-Dokument lesen, jede mit Seite und Textstelle (Regel 2).

Nur Regeln im Code, kein Sprachmodell. Was nicht sicher erkennbar ist, fehlt oder steht mit
Hinweis da; ein falsches Ablaufdatum ist schlimmer als keins. Gelesen werden:

- Gültigkeit: „gültig bis“ / „valid until“ / „valable jusqu'au“ mit Datum direkt dahinter, oder
  „unbefristet“; sonst „keine Angabe“ (ATEX-Bescheinigungen nennen in der Regel kein Ablaufdatum).
- Ausstellungsdatum und Ausgabe/Revision („Ausgabe Nr. 9“, „Issue No. 2“, „Rev. C“).
- Richtlinien (2014/34/EU, 94/9/EG, UK SI 2016/1107, …) und Normen mit Ausgabe („EN IEC
  60079-0:2018“, mit Änderungen).
- Benannte Stellen und Prüfstellen mit vierstelliger Nummer, so wie der Text sie nennt.
- Hersteller (Name, Ort) laut Dokument, Ort/Datum und Name der Unterschrift.
- Aus der Ex-Kennzeichnung: Gruppe und Kategorie, Zündschutzart, Gas-/Staubgruppe,
  Temperaturklasse, EPL; dazu Umgebungstemperatur, besondere Bedingungen („X“), IECEx-Nummer.

Die Regeln für Datum, „gültig bis“, Richtlinien und Normen sind aus dem Zertifikatsserver
übernommen (zertifikatsserver/app/auslesen.py), damit beide Dienste dasselbe lesen; hier
erweitert um zweistellige Jahre direkt nach „gültig bis“, Datumsangaben, die zu „gültig bis“
gehören, zählen nicht als Ausstellungsdatum, und Normen außerhalb der Reihe 60079.

Ergebnis ist ein JSON-fähiges dict (Finding.angaben). Jede Angabe hat „seite“ (ab 1) und
„text“ (die Textstelle); „ocr“: True, wenn die Seite per OCR gelesen ist (am Seitenbild prüfen).
lies() ist ohne Datenbank und ohne PDF testbar; fuelle() und pruefe() arbeiten am Vorschlag.
"""

import re
from datetime import date, timedelta

from . import pruefung

VERSION = 1  # Stand der Regeln; „angaben-nachlesen --alle“ liest ältere Stände neu

# --- Datum (wie im Zertifikatsserver) ---------------------------------------------------------

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
# nur direkt nach „gültig bis“: auch „31.12.28“ und „31/12/2028“
DATUM_KURZ = re.compile(r"\b(?P<t>\d{1,2})[./][ \t]?(?P<m>\d{1,2})[./][ \t]?(?P<j>(?:19|20)?\d{2})\b")
# Daten, die zu einer zitierten Richtlinie gehören („Richtlinie 2014/34/EU vom 26. Februar 2014“)
RICHTLINIE_DAVOR = re.compile(r"(?:\b\d{2,4}\s*/\s*\d{1,3}\s*/\s*(?:EU|EG|EC|EWG|UE|CE)\b"
                              r"|council|rates|parlament\w*|parliament|conseil)"
                              r"[^.;]{0,60}?(?:\b(?:vom|of|du|dated|datée?)(?:\s+the)?|\()\s*$",
                              re.I)
# Zeitraum statt Ausstellung: „(ab 20.04.2016)“, „until 19.04.2016“, „bis …“
ZEITRAUM_DAVOR = re.compile(r"\b(?:bis|until|jusqu\S*|to|ab|from|fron|seit|since)\s*$", re.I)
DATUM_STICHWORT = re.compile(r"datum|date|ausstellung|ausgestellt|issued?|fait|délivr|signed"
                             r"|unterschrift|dated|ausgabe", re.I)
_BIS = (r"gültig\s+bis|gueltig\s+bis|gültigkeit\s+bis|ablaufdatum|läuft\s+ab\s+am"
        r"|valid\s+until|valid\s+till|valid\s+to|validity\s+(?:date|until)|expiry\s+date"
        r"|expires(?:\s+on)?|expiration\s+date|date\s+of\s+expiry"
        r"|valable\s+jusqu['’]?(?:au|à)?|validité\s+jusqu['’]?(?:au|à)?"
        r"|date\s+d['’]expiration|date\s+d['’]échéance|fin\s+de\s+validité")
GUELTIG_BIS = re.compile(_BIS, re.I)
# zwischen Stichwort und Datum nur Trenner, kleine Wörter oder weitere Stichwörter (mehrsprachig)
LUECKE = re.compile(rf"(?:\s+|[:./,()|\-–]+|\b(?:am|zum|den|le|au|on|the)\b|{_BIS})*", re.I)
UNBEFRISTET = re.compile(
    r"\bunbefristet\w*|zeitlich\s+unbegrenzt\s+gültig|ohne\s+Ablaufdatum|no\s+expiry\s+date"
    r"|does\s+not\s+expire|unlimited\s+validity|valid\s+indefinitely|validité\s+illimitée"
    r"|durée\s+de\s+validité\s+illimitée|sans\s+limite\s+de\s+validité|n['’]expire\s+pas", re.I)
JAHRE = (1990, 2100)  # außerhalb gilt ein Datum als verlesen


def _text(seite):
    """Weiche Trennzeichen raus (R. STAHL: „Umgebungs\xadtemperatur“), geschützte und
    schmale Leerzeichen als Leerzeichen („-60\xa0°C“)."""
    return re.sub(r"[\xa0\u2009\u202f]", " ", (seite or "").replace("\u00ad", ""))


def textstelle(text, start, ende, rand=40):
    return re.sub(r"\s+", " ", text[max(0, start - rand):ende + rand]).strip()


def _datum(m):
    try:
        if m.group("iso"):
            return date.fromisoformat(m.group("iso"))
        if m.group("t"):
            return date(int(m.group("j")), int(m.group("m")), int(m.group("t")))
        return date(int(m.group("j2")), MONATE[m.group("mn").lower()], int(m.group("t2")))
    except ValueError:
        return None


def _datum_kurz(m):
    j = m.group("j")
    try:
        return date(int(j) + (2000 if len(j) == 2 else 0), int(m.group("m")), int(m.group("t")))
    except ValueError:
        return None


def _je_seite(seiten, muster):
    for nr, text in enumerate(seiten, start=1):
        for m in muster.finditer(text):
            yield nr, text, m


def _angabe(wert, nr, text, start, ende, ocr, **mehr):
    a = {"wert": wert, "seite": nr, "text": textstelle(text, start, ende)}
    if nr in ocr:
        a["ocr"] = True
    a.update({k: v for k, v in mehr.items() if v is not None})
    return a


def _plausibel(tag):
    return tag is not None and JAHRE[0] <= tag.year <= JAHRE[1]


def gueltig_bis(seiten, ocr=()):
    """{"status": "datum" | "unbefristet" | "keine_angabe", ...}. Das Datum muss direkt hinter
    dem Stichwort stehen, nur durch Trenner getrennt; „bis 19.04.2016“ allein zählt nicht."""
    for nr, text, m in _je_seite(seiten, GUELTIG_BIS):
        luecke = LUECKE.match(text, m.end())
        pos = luecke.end() if luecke else m.end()
        d = DATUM.match(text, pos) or DATUM_KURZ.match(text, pos)
        if not d:
            continue
        tag = _datum(d) if d.re is DATUM else _datum_kurz(d)
        if not _plausibel(tag):
            continue
        a = _angabe(tag.isoformat(), nr, text, m.start(), d.end(), ocr, status="datum")
        a["_pos"] = (nr, d.start())
        return a
    for nr, text, m in _je_seite(seiten, UNBEFRISTET):
        return _angabe(None, nr, text, m.start(), m.end(), ocr, status="unbefristet")
    return {"status": "keine_angabe", "wert": None}


_ORT = r"[A-ZÄÖÜ][a-zäöüß]+(?:[ -][A-ZÄÖÜ]?[a-zäöüß]+){0,3}"  # „Verneuil-en-Halatte“
ORT_KOMMA = re.compile(rf"(?m)^[ \t]*{_ORT}[ \t]*[,;][ \t]*"
                       r"(?:(?:den|le|am|on)[ \t]+)?$")


def ausstellungsdatum(seiten, ocr=(), ausser=None):
    """Spätestes Datum mit Stichwort davor („Datum“, „Date of issue“, „issued on“) oder als
    „Ort, Datum“ am Zeilenanfang („Freiberg, 2020-05-19“); sonst None.

    Wie im Zertifikatsserver, aber strenger: ohne Stichwort kein Datum (dort: spätestes Datum als
    „unsicher“ – auf den echten PDFs war das oft ein Druckdatum oder das einer Richtlinie), ohne
    das Datum hinter „gültig bis“ (ausser: (Seite, Stelle)) und ohne „ab/bis/until …“."""
    funde = []
    for nr, text, m in _je_seite(seiten, DATUM):
        tag = _datum(m)
        davor = text[max(0, m.start() - 100):m.start()]
        if tag is None or not _plausibel(tag) or RICHTLINIE_DAVOR.search(davor) \
                or (nr, m.start()) == ausser or ZEITRAUM_DAVOR.search(davor):
            continue
        zeile = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if DATUM_STICHWORT.search(text[max(0, m.start() - 80):m.start()]) \
                or ORT_KOMMA.match(zeile):
            funde.append((tag, nr, text, m))
    if not funde:
        return None
    tag, nr, text, m = max(funde, key=lambda f: (f[0], -f[1]))
    return _angabe(tag.isoformat(), nr, text, m.start(), m.end(), ocr)


ZEILE_NUR_DATUM = re.compile(r"(?m)^[ \t]*((?:19|20)\d{2}-\d{2}-\d{2})[ \t]*$")


def kopfdatum(seiten, ocr=()):
    """Stand des Dokuments aus Kopf oder Fuß: ein Datum, das allein in einer Zeile steht und auf
    jeder von mindestens zwei Seiten dasselbe ist (Festo: unter der Dokumentnummer,
    „5629141 ⏎ 2021-10-13“). Mehrere verschiedene solche Daten oder eine Seite ohne: None.

    Kein Ausstellungsdatum im strengen Sinn (ausstellungsdatum, es kann ein Druckdatum sein) und
    deshalb nicht angezeigt: Es dient nur dazu, zwei Fassungen desselben Dokuments zu ordnen
    (suche.aeltere_fassungen)."""
    je_seite = [{m.group(1) for m in ZEILE_NUR_DATUM.finditer(_text(s))} for s in seiten]
    if len(je_seite) < 2 or any(len(d) != 1 for d in je_seite) or len(set.union(*je_seite)) != 1:
        return None
    text = _text(seiten[0])
    m = ZEILE_NUR_DATUM.search(text)
    tag = date.fromisoformat(m.group(1)) if m else None
    if not _plausibel(tag):
        return None
    return _angabe(tag.isoformat(), 1, text, m.start(1), m.end(1), ocr)


# --- Ausgabe / Revision -----------------------------------------------------------------------

AUSGABE = re.compile(
    r"(?i:\bAusgabe(?:[ \t]*(?:Nr\.?|Nummer))?|\bIssue(?:[ \t]*(?:No\.?|Number|N°))?"
    r"|\bIndice(?:[ \t]*/[ \t]*Issue)?(?:[ \t]*N°)?)[ \t]*[:.]?[ \t]*(?:N°[ \t]*)?(?P<nr>\d{1,3})\b"
    r"|(?:\bRev\.|\bRevision|\bRévision|\bREV\.?)[ \t]*[:.]?[ \t]*(?P<rev>\d{1,3}|[A-Z])\b")


def _nummer_davor(text, start, zertifikat):
    """Steht die Nummer der Bescheinigung kurz vor start („Nummer: KEMA 04ATEX2242⏎Ausgabe 9“)?"""
    davor = re.sub(r"\s+", "", text[max(0, start - 120):start])
    return re.sub(r"\s+", "", zertifikat) in davor


def ausgabe(seiten, ocr=(), zertifikate=()):
    """Ausgabe der Bescheinigung: die direkt hinter ihrer Nummer (zertifikate[0]), sonst die
    höchste genannte. Eine PDF kann mehrere Bescheinigungen enthalten (Siemens: DEKRA 19ATEX0094 X
    Ausgabe 3 und KEMA 08ATEX0002 X Ausgabe 7); die Übersicht am Ende nennt auch frühere
    („Ausgabe Nr. 1 – Erstbescheinigung“), „gegenüber der Ausgabe 0“ ebenso.
    Beleg: erste Stelle dieser Ausgabe."""
    funde = []
    for nr, text, m in _je_seite(seiten, AUSGABE):
        wert = m.group("nr") or m.group("rev")
        if m.group("rev") and m.group("rev").isdigit() and len(m.group("rev")) > 2:
            continue
        rang = (1, int(wert)) if wert.isdigit() else (0, ord(wert))
        funde.append((rang, -nr, -m.start(), wert, nr, text, m))
    eigene = [f for f in funde if zertifikate and _nummer_davor(f[5], f[6].start(), zertifikate[0])]
    funde = eigene or funde
    if not funde:
        return None
    rang = max(f[0] for f in funde)
    _, _, _, wert, nr, text, m = max(f for f in funde if f[0] == rang)
    return _angabe(str(int(wert)) if wert.isdigit() else wert, nr, text, m.start(), m.end(), ocr)


# --- Richtlinien und Normen -------------------------------------------------------------------

RICHTLINIE = re.compile(r"\b(?P<j>\d{2,4}|2[O0]14)\s*[/l|]\s*(?P<n>\d{1,3})\s*[/l|]\s*"
                        r"(?P<s>EU|EG|EC|EWG|EEC|UE|CE)\b")
UKSI = re.compile(r"\b(?:UK\s*)?S\.?\s?I\.?\s*2016\s*(?:[:/]|No\.?)\s*1107\b", re.I)
ATEX_RICHTLINIEN = ("94/9/EG", "2014/34/EU")


def richtlinien(seiten, ocr=()):
    """Genannte Richtlinien, je eine mit der ersten Fundstelle; „art“: atex | uk | sonstige.
    „94/9/EC“ heißt hier „94/9/EG“, „2014/34/UE“ „2014/34/EU“."""
    funde = {}
    for nr, text, m in _je_seite(seiten, RICHTLINIE):
        j = m.group("j").replace("O", "0")
        if (j in ("94", "1994") and m.group("n") == "9") or (j == "2014" and m.group("n") == "34"):
            wert = "94/9/EG" if j.endswith("94") else "2014/34/EU"
        elif "l" in m.group(0) or "|" in m.group(0):
            continue  # die OCR-Nachsicht nur für die beiden ATEX-Richtlinien
        else:
            wert = f"{j}/{m.group('n')}/{'EU' if m.group('s') in ('EU', 'UE') else 'EG'}"
        funde.setdefault(wert, _angabe(wert, nr, text, m.start(), m.end(), ocr,
                                       art="atex" if wert in ATEX_RICHTLINIEN else "sonstige"))
    for nr, text, m in _je_seite(seiten, UKSI):
        funde.setdefault("UK SI 2016/1107", _angabe("UK SI 2016/1107", nr, text, m.start(),
                                                    m.end(), ocr, art="uk"))
    rang = {"atex": 0, "uk": 1, "sonstige": 2}
    return sorted(funde.values(), key=lambda a: rang[a["art"]])


# „EN 60079-0:2012“, „EN IEC 60079-0 : 2018“, „EN 60079-7:2015+A1:2018“, „IEC 60079-0 : 2007-10“,
# „EN IEC 60079-0/AC:2020-02“, „EN 60079-31 ......  2014“ (Siemens), „EN 60079-1⏎:⏎2014“ (INERIS)
_JAHR = r"(?:19|20)\d{2}(?:-\d{2})?(?!\d)"
_ZUSATZ = rf"(?:[ \t]*[+/,][ \t]*A(?:C|\d{{1,2}}(?!\d))(?:[ \t]*:?[ \t]*{_JAHR})?)"
NORM = re.compile(
    r"(?<![\w/])(?P<name>(?:DIN[ \t]+)?(?:BS[ \t]+)?(?:EN|IEC|ISO)(?:[ \t]+(?:IEC|ISO))?)[ \t]*"
    r"(?P<nr>\d{3,5}(?:-\d{1,3}){0,2})(?P<vorn>(?:[ \t]*/[ \t]*A[C\d]{1,2})?)"
    rf"(?:\s*[:/]\s*|[ \t]*\.{{2,}}[ \t]*|[ \t]+)(?P<jahr>{_JAHR})(?P<zusatz>{_ZUSATZ}*)")
EX_NORM = re.compile(r"^(?:60079|80079|1127|13463|5001[4-9]|5002\d|5003\d|61241|50281|50284"
                     r"|50303|14986|13237)\b")


def _norm_wert(m):
    """„EN IEC 60079-0 : 2018 + A11“ -> „EN IEC 60079-0:2018+A11“."""
    name = re.sub(r"[ \t]+", " ", m.group("name"))
    vorn = re.sub(r"[ \t]+", "", m.group("vorn"))
    zusatz = re.sub(r"^,", "+", re.sub(r"[ \t]+", "", m.group("zusatz")))
    zusatz = re.sub(r"(A(?:C|\d{1,2}))((?:19|20)\d{2})", r"\1:\2", zusatz)  # „AC2020“
    return f"{name} {m.group('nr')}{vorn}:{m.group('jahr')}{zusatz}"


def normen(seiten, ocr=()):
    """Normen mit Ausgabe, je Norm die erste Fundstelle; „ex“: True für Ex-Normen (60079, 80079,
    EN 1127, EN 13463, …). Ex-Normen zuerst, sonst in der Reihenfolge des Dokuments."""
    funde = {}
    for nr, text, m in _je_seite(seiten, NORM):
        wert = _norm_wert(m)
        funde.setdefault(wert, _angabe(wert, nr, text, m.start(), m.end(), ocr,
                                       ex=bool(EX_NORM.match(m.group("nr")))))
    return sorted(funde.values(), key=lambda a: not a["ex"])


# --- Benannte Stellen ---------------------------------------------------------------------------

STELLEN = (  # (Name, Muster im Text); die Nummer kommt immer aus dem Text
    ("Physikalisch-Technische Bundesanstalt (PTB)",
     r"Physikalisch[\s-]+Technische[n]?\s+Bundesanstalt|\bPTB\b"),
    ("DEKRA Certification B.V.", r"DEKRA\s+Certification\s+B\.?\s?V\.?"),
    ("DEKRA Testing and Certification GmbH", r"DEKRA\s+Testing\s+and\s+Certification\s+GmbH"),
    ("DEKRA EXAM GmbH", r"DEKRA\s+EXAM\s+GmbH"),
    ("IBExU Institut für Sicherheitstechnik GmbH", r"\bI\s?BExU\b|\b113ExU\b"),
    ("INERIS", r"\bINERIS\b|\bIneris\b"),
    ("TÜV Rheinland", r"T[ÜU]V\s+Rheinland"), ("TÜV NORD", r"T[ÜU]V\s+NORD|T[ÜU]V\s+Nord"),
    ("TÜV SÜD", r"T[ÜU]V\s+S[ÜU]D|T[ÜU]V\s+Süd"),
    ("Intertek", r"\bINTERTEK\b|\bIntertek\b"), ("SGS Fimko", r"SGS\s+FIMKO|SGS\s+Fimko"),
    ("Baseefa", r"\bBaseefa\b"), ("CML", r"\bCML\b"), ("LCIE", r"\bLCIE\b"), ("CESI", r"\bCESI\b"),
    ("Sira", r"\bSira\b|\bSIRA\b"), ("UL International DEMKO", r"\bDEMKO\b"),
    ("Nemko", r"\bNemko\b|\bNEMKO\b"), ("FM Approvals", r"FM\s+Approvals"),
    ("Eurofins", r"\bEurofins\b"), ("Bureau Veritas", r"Bureau\s+Veritas"),
)
STELLE_NAME = re.compile("|".join(f"(?P<s{i}>{m})" for i, (_, m) in enumerate(STELLEN)))
STELLE_STICHWORT = re.compile(
    r"benannte[n]?\s+Stelle|notifizierte[n]?\s+Stelle|notified\s+body|organisme\s+notifi[ée]"
    r"|Kennnummer|Typprüflabor\w*|Type\s+Test\s+Laboratory|Certificate\s+issued\s+by"
    r"|Zertifizierungsstelle", re.I)
STELLE_NR = re.compile(r"(?:\b(?:Nr|No|n°|N°|number|numéro|Nummer|Kennnummer)\.?[ \t]*:?[ \t]*"
                       r"|\([ \t]*|\.{3,}[ \t]*)(?P<nr>[012]\d{3})(?!\d|[.,/]\d)", re.I)
ROLLE_QM = re.compile(r"QM|QS|QA[\s-]|quality|Qualität|Überwachung|surveillance|production", re.I)
ROLLE_PRUEF = re.compile(r"bescheinigt|certifies|certifie|Baumusterprüf|type[\s-]examination"
                         r"|Typprüflabor|Type\s+Test|issued\s+by", re.I)
# Kürzel vor der Nummer der Bescheinigung -> Prüfstelle, wenn der Text keine nennt
KUERZEL = re.compile(r"^(?:IECEx\s+)?([A-ZÄÖÜ][A-Za-zÄÖÜ]{1,7})\b")


def _stelle_name(text, start, ende):
    """Nächster bekannter Name um die Stelle: (Name, Position) oder (None, None)."""
    besten = None
    for m in STELLE_NAME.finditer(text, max(0, start - 160), min(len(text), ende + 130)):
        abstand = min(abs(m.start() - start), abs(m.start() - ende))
        if besten is None or abstand < besten[0]:
            i = int(next(k for k, v in m.groupdict().items() if v)[1:])
            besten = (abstand, STELLEN[i][0], m.start())
    return (besten[1], besten[2]) if besten else (None, None)


def stellen(seiten, ocr=(), zertifikate=()):
    """Benannte Stellen und Prüfstellen: [{"name", "nummer", "rolle", seite, text}].

    rolle: „pruefung“ (stellt die Bescheinigung aus), „qm“ (überwacht die Fertigung) oder
    „genannt“. Die Nummer nur, wenn sie hinter dem Stichwort steht („benannte Stelle Nr. 0344“,
    „INERIS (0080)“). Ohne Fund im Text: Kürzel aus der Nummer der Bescheinigung (z. B. „PTB“).
    """
    funde = {}
    for nr, text, m in _je_seite(seiten, STELLE_STICHWORT):
        name, pos = _stelle_name(text, m.start(), m.end())
        n = STELLE_NR.search(text, m.end(), min(len(text), m.end() + 130))
        if not name and not n:
            continue
        rolle = "qm" if ROLLE_QM.search(text, max(0, m.start() - 70), m.end() + 70) \
            else "pruefung" if ROLLE_PRUEF.search(text, max(0, m.start() - 70), m.end() + 250) \
            else "genannt"
        nummer = n.group("nr") if n else None
        start = min(m.start(), pos if pos is not None else m.start())
        ende = n.end() if n else m.end()
        # gleiche Stelle: gleicher Name oder gleiche Nummer (OCR verliest oft den Namen)
        alt = next((k for k, a in funde.items()
                    if (name and a.get("name") == name) or (nummer and a.get("nummer") == nummer)),
                   None)
        neu = _angabe(None, nr, text, start, ende, ocr, name=name, nummer=nummer, rolle=rolle)
        neu.pop("wert")
        if alt is None:
            funde[len(funde)] = neu
        elif (not funde[alt].get("nummer") and nummer) or (not funde[alt].get("name") and name):
            for feld in ("name", "nummer"):
                neu[feld] = neu.get(feld) or funde[alt].get(feld)
            funde[alt] = neu
    if not funde:
        for z in zertifikate or ():
            k = KUERZEL.match(z)
            if k and k.group(1).upper() not in ("ATEX", "UKEX"):
                for nr, text, m in _je_seite(seiten, re.compile(re.escape(z))):
                    funde[0] = _angabe(None, nr, text, m.start(), m.end(), ocr, name=k.group(1),
                                       rolle="pruefung",
                                       hinweis="Der Name steht nicht im Text. Er ist aus der "
                                               "Nummer des Zertifikats abgeleitet.")
                    funde[0].pop("wert")
                    break
                break
    return list(funde.values())


# --- Hersteller und Unterschrift --------------------------------------------------------------

_HERSTELLER = r"Hersteller|Manufacturer|Fabricant|Name\s+des\s+Herstellers"
_ANTRAG = r"Applicant(?:\s+Company)?|Antragsteller|Demandeur|Issued\s+to"
LABEL = re.compile(rf"(?im)^[ \t]*(?:\(?\d{{1,2}}\)?[ \t.]*)?(?P<label>(?:{_HERSTELLER}|{_ANTRAG})"
                   rf"(?:[ \t]*/[ \t]*(?:{_HERSTELLER}|{_ANTRAG}))*)[ \t]*(?::[ \t]*(?P<rest>[^\n]*))?$")
NUR_LABEL = re.compile(rf"(?i)^(?:\(?\d{{1,2}}\)?|{_HERSTELLER}|{_ANTRAG}|Address|Anschrift"
                       r"|Adresse|Listed\s+Company)[ \t:;]*$")
ANSCHRIFT = re.compile(r"(?i)^[ \t]*(?:\(?\d{1,2}\)?[ \t.]*)?(?:Anschrift|Address|Adresse)"
                       r"(?:[ \t]*/[ \t]*(?:Anschrift|Address|Adresse))*[ \t]*:?[ \t]*$")
PLZ = re.compile(r"\b(?:[A-Z]{1,2}-)?\d{4,5}\s+[A-ZÄÖÜ][\w.\- ]+")
WIR = re.compile(r"\b(?:We|Wir|Nous)[ \t]*,[ \t]*(?:the[ \t]+)?(?P<n>[^,\n]{2,60}?)[ \t]*,?\s+"
                 r"(?:declare|erklären|déclarons|hereby)", re.I)
DIE_ERKLAERT = re.compile(r"\bDie\s+(?P<n>[A-Z][^\n]{1,50}?)\s+erklärt\b")
RECHTSFORM = re.compile(
    r"(?m)^[ \t]*(?P<n>[A-Z][\w.+&\- ]{1,50}?\b(?:GmbH(?:[ \t]*&[ \t]*Co\.[ \t]*KG)?"
    r"|AG(?:[ \t]*&[ \t]*Co\.[ \t]*KG)?|SE(?:[ \t]*&[ \t]*Co\.[ \t]*KG)?|S\.A\.S?\.?|S\.p\.A\.|Ltd\.?"
    r"|Limited|Inc\.?|LLC))[ \t]*[∙|•·][ \t]*(?P<ort>[^\n]+)")


def _zeilen_nach(text, pos, anzahl=4):
    """Die nächsten nicht leeren Zeilen ab pos: [(Start, Zeile)]."""
    ergebnis = []
    for m in re.finditer(r"[^\n]+", text[pos:]):
        z = m.group().strip()
        if z:
            ergebnis.append((pos + m.start(), z))
        if len(ergebnis) >= anzahl:
            break
    return ergebnis


def _ist_name(z):
    return (len(z) <= 80 and re.search(r"[A-Za-zÄÖÜäöü]{2}", z) and not NUR_LABEL.match(z)
            and not re.match(r"(?i)same as|siehe|see ", z) and not z.endswith((":", ";")))


def _ort_nach(text, pos):
    """Ort nach dem Namen: Zeile mit Postleitzahl in den nächsten Zeilen, auch nach „Anschrift:“."""
    for start, z in _zeilen_nach(text, pos, 5):
        if LABEL.match(z) or STELLE_STICHWORT.search(z):
            break
        if PLZ.search(z):
            return z[:80]
    return None


def hersteller(seiten, ocr=(), doc_type=None):
    """Hersteller laut Dokument: {"wert": Name, "ort", seite, text, "quelle"}; sonst None.

    Zuerst „Hersteller:“ / „Manufacturer:“ / „Fabricant:“ (Wert in derselben oder der nächsten
    Zeile), dann „Applicant:“, dann „Wir, X erklären“ / „We, X declare“. Bei Erklärungen und
    Anleitungen zuletzt eine Kopf- oder Fußzeile „Firma GmbH ∙ Straße ∙ Ort“.
    """
    kandidaten = []  # (Rang, Seite, Start, Name, Text, Ende)
    for nr, text, m in _je_seite(seiten, LABEL):
        rang = 0 if re.match(rf"(?i){_HERSTELLER}", m.group("label")) else 1
        rest = (m.group("rest") or "").strip()
        if rest and _ist_name(rest):
            kandidaten.append((rang, nr, m.start(), rest, text, m.end()))
            continue
        for start, z in _zeilen_nach(text, m.end(), 3):
            if _ist_name(z):
                kandidaten.append((rang, nr, m.start(), z, text, start + len(z)))
                break
            if not NUR_LABEL.match(z):
                break
    for muster in (WIR, DIE_ERKLAERT):
        for nr, text, m in _je_seite(seiten, muster):
            kandidaten.append((2, nr, m.start(), m.group("n").strip(), text, m.end()))
    if not kandidaten and doc_type not in ("eu_baumusterpruefbescheinigung",
                                           "uk_baumusterpruefbescheinigung"):
        for nr, text, m in _je_seite(seiten[:2], RECHTSFORM):
            if not STELLE_NAME.search(m.group("n")):
                ort = next((t.strip() for t in re.split(r"[∙|•·]", m.group("ort"))
                            if PLZ.search(t.strip())), None)
                a = _angabe(m.group("n").strip(), nr, text, m.start(), m.end(), ocr,
                            ort=ort, hinweis="Der Hersteller steht nur in der Kopf- oder "
                                             "Fußzeile.")
                return a
    if not kandidaten:
        return None
    rang, nr, start, name, text, ende = min(kandidaten, key=lambda k: k[:3])
    name = re.sub(r"\s+", " ", name).strip(" ,;")
    ort = None
    if (m := re.search(r"\s+(?:at|in|aus|à)\s+(?=(?:[A-Z]{1,2}-)?\d{4,5}\s)", name)):
        name, ort = name[:m.start()], name[m.end():]  # „We, X GmbH at 68301 Mannheim declare“
    return _angabe(name, nr, text, start, ende, ocr, ort=ort or _ort_nach(text, ende))


ORT_DATUM = re.compile(rf"(?m)^[ \t]*(?P<ort>{_ORT})"
                       rf"[ \t]*,[ \t]*(?:(?:den|le|am|on)[ \t]+)?(?P<datum>{DATUM.pattern})", re.I)
UNTERZEICHNER = re.compile(r"(?m)^[ \t]*(?P<z>(?:ppa\.|i\.[ \t]?V\.|i\.[ \t]?A\.|p\.[ \t]?p\.)[ \t]*"
                           r"[A-ZÄÖÜ][\w.\-]+(?:[ \t]+[A-ZÄÖÜ][\w.\-]+){0,3})[ \t]*$")


def unterschrift(seiten, ocr=()):
    """Ort und Datum der Unterschrift („Mannheim, 2021-11-22“) und Unterzeichner mit „ppa.“
    oder „i. V.“, wenn sie so dastehen; sonst None. Nur was billig sicher lesbar ist."""
    for nr, text, m in _je_seite(seiten, ORT_DATUM):
        d = DATUM.match(text, m.start("datum"))
        tag = _datum(d) if d else None
        if not _plausibel(tag) or m.group("ort").lower() in MONATE:
            continue
        namen = [u.group("z") for u in UNTERZEICHNER.finditer(text, m.end(), m.end() + 400)]
        return _angabe(None, nr, text, m.start(), m.end(), ocr, ort=m.group("ort"),
                       datum=tag.isoformat(), unterzeichner=namen[:2] or None)
    return None


# --- Kennzeichnung ------------------------------------------------------------------------------

GRUPPE = re.compile(r"^\(?(?:II[ABC](?:\+H2)?|III[ABC]|I|II|III)\)?$")
EPL = re.compile(r"^\(?[GDM][abc]\)?$")
TEMP = re.compile(r"^(?:T[1-6]|T\d{2,3}(?:°C)?|\d{2,3}°C|°C|/)$")
SCHUTZ = re.compile(r"^(?:ia|ib|ic|ma|mb|mc|e|eb|ec|d|db|dc|p|px|pxb|py|pyb|pz|pzc|q|qb|o|ob|n|nA|"
                    r"nC|nR|nL|h|t|ta|tb|tc|op|is|pr|sh|s|sa|sb|sc|m|i)$")
EX_ALLEIN = re.compile(r"(?m)\bE?Ex[ \t]+(?:\[?(?:ia|ib|ic|ma|mb|mc|e|eb|ec|d|db|dc|p|pxb|pyb|pzc|"
                       r"q|o|nA|nC|nR|h|t|ta|tb|tc|op[ \t]?is)\]?[ \t]+)+"
                       r"(?:I|II[ABC]?(?:\+H2)?|III[ \t]?[ABC])\b[^\n]{0,30}")


def zerlege(kennzeichnung):
    """„II 2 G (1) G Ex eb [ia Ga/ib] IIC T4 Gb“ -> {"geraet": "II 2 G (1) G",
    "zuendschutz": "Ex eb [ia Ga/ib]", "gruppe": "IIC", "temperatur": "T4", "epl": "Gb"}.
    Felder, die nicht dastehen, fehlen."""
    text = re.sub(r"\s+", " ", kennzeichnung or "").strip()
    text = re.sub(r"\bIII ([ABC])\b", r"III\1", text)
    text = re.sub(r"(\d) °C", r"\1°C", text)
    text = re.sub(r"\bT (\d{2,3}°C)", r"T\1", text)
    d = {}
    k = pruefung.KENNZEICHNUNG.match(text)
    rest = text
    if k:
        d["geraet"] = k.group().strip()
        rest = text[k.end():]
    teile = re.findall(r"\[[^\]]*\]|\S+", rest)
    schutz, gruppen, temp, epl, i = [], [], [], [], 0
    while i < len(teile) and not re.match(r"^E?Ex$", teile[i]):
        i += 1
    if i < len(teile):
        schutz.append(teile[i])
        i += 1
        while i < len(teile) and (SCHUTZ.match(teile[i]) or teile[i].startswith("[")):
            schutz.append(teile[i])
            i += 1
    for t in teile[i:]:
        if GRUPPE.match(t) and not temp and not epl:
            gruppen.append(t.strip("()"))
        elif TEMP.match(t) and not epl:
            temp.append(t)
        elif EPL.match(t):
            epl.append(t.strip("()"))
        elif re.match(r"^IP\d{2}", t):
            d["ip"] = t
        else:
            break
    if len(schutz) > 1:
        d["zuendschutz"] = " ".join(schutz)
    if gruppen:
        d["gruppe"] = " ".join(gruppen)
    temperatur = " ".join(temp).replace(" / ", "/").strip(" /")
    if temperatur:
        d["temperatur"] = temperatur
    if epl:
        d["epl"] = " ".join(epl)
    return d


def kennzeichnungen(seiten, ocr=(), kennz=None):
    """Zerlegte Kennzeichnungen: die des Vorschlags (kennz), sonst die im Text; ohne „II 2G“
    die allein stehenden „Ex e IIC Gb“ (IECEx). Je Kennzeichnung Seite und Textstelle."""
    liste = list(kennz or [])
    if not liste:
        liste = list(dict.fromkeys(k for s in seiten for k in pruefung.kennzeichnungen(s)))[:3]
    if not liste:
        liste = list(dict.fromkeys(re.sub(r"\s+", " ", m.group()).strip()
                                   for s in seiten for m in EX_ALLEIN.finditer(s)))[:3]
    ergebnis = []
    for k in liste:
        teile = zerlege(k)
        if not teile:
            continue
        nr = next((nr for nr, s in enumerate(seiten, start=1) if pruefung.steht_im_text(k, s)),
                  None)
        a = {"wert": k, **teile, "seite": nr, "text": k}
        if nr in ocr:
            a["ocr"] = True
        ergebnis.append(a)
    return ergebnis


TA_STICHWORT = re.compile(r"\bT[ \t]?a(?:mb)?\b|\bTu\b|Umgebungs\W?temperatur\w*|ambient\s+temperature"
                          r"(?:\s+range)?|température\s+ambiante|Ambient", re.I)
_GRAD = r"[ \t]*°?[ \t]*C?"
TA_BEREICH = re.compile(
    rf"(?P<min>[-–−+]?[ \t]?\d{{1,3}}){_GRAD}[ \t]*(?:≤|<=|<|\.{{2,3}}|…|bis|to|à|up\s+to|–|-)"
    rf"[ \t]*(?:(?:T[ \t]?a(?:mb)?|Tu)[ \t]*(?:≤|<=|<)[ \t]*)?(?P<max>\+?[ \t]?\d{{1,3}})[ \t]*°[ \t]*C")
TA_NUR_MAX = re.compile(r"\b(?:T[ \t]?a(?:mb)?|Tu)[ \t]*(?:≤|<=)[ \t]*(?P<max>\+?[ \t]?\d{1,3})[ \t]*°[ \t]*C")


def _zahl(s):
    return int(re.sub(r"[\s+]", "", s).replace("–", "-").replace("−", "-"))


def umgebungstemperatur(seiten, ocr=()):
    """Bereiche der Umgebungstemperatur in °C mit Stichwort („Ta“, „Tamb“, „Umgebungstemperatur“,
    „ambient temperature“) davor oder darin: [{"wert": "-20 °C … +70 °C", "min", "max", …}]."""
    funde = {}
    for nr, text, m in _je_seite(seiten, TA_BEREICH):
        davor = text[max(0, m.start() - 70):m.start()]
        if not (TA_STICHWORT.search(davor) or TA_STICHWORT.search(m.group())):
            continue
        lo, hi = _zahl(m.group("min")), _zahl(m.group("max"))
        vorzeichen = re.match(r"[-–−+]", m.group("min").strip())
        if not (-80 <= lo < hi <= 300) or (
                not vorzeichen and lo != 0 and m.start() and not text[m.start() - 1].isspace()):
            continue  # „"30 °C bis +80 °C“: OCR hat das Minus verlesen
        wert = f"{lo:+d} °C … {hi:+d} °C".replace("+0 ", "0 ")
        funde.setdefault(wert, _angabe(wert, nr, text, m.start(), m.end(), ocr, min=lo, max=hi))
    if not funde:
        for nr, text, m in _je_seite(seiten, TA_NUR_MAX):
            hi = _zahl(m.group("max"))
            wert = f"bis {hi:+d} °C"
            funde.setdefault(wert, _angabe(wert, nr, text, m.start(), m.end(), ocr, max=hi))
    return list(funde.values())[:5]


BEDINGUNGEN = re.compile(
    r"(?im)^[ \t]*(?:\(?1[78]\)?\.?[ \t]*)?(?P<k>Besondere\s+Bedingungen(?:\s+für\s+die\s+(?:sichere\s+)?"
    r"(?:Verwendung|Anwendung))?|Special\s+Conditions(?:\s+(?:for\s+(?:safe\s+)?use|of\s+use))?"
    r"|Specific\s+Conditions\s+of\s+Use|Conditions\s+(?:spéciales|particulières)"
    r"(?:\s+(?:pour\s+une\s+utilisation\s+sûre|d['’]utilisation))?|Schedule\s+of\s+Limitations"
    r"|Conditions\s+of\s+(?:Certification|Manufacture))(?P<rest>[^\n]*)$")
KEINE = re.compile(r"(?i)^(?:keine|none|aucune|néant|n/?a|not\s+applicable|entfällt)\b")


def besondere_bedingungen(seiten, ocr=(), zertifikate=(), doc_type=None):
    """Besondere Bedingungen: „x“ True, wenn eine Bescheinigungsnummer auf „X“ endet; Stelle der
    Überschrift („(17) Besondere Bedingungen“) mit Seite, „keine“ True, wenn dort „Keine.“ steht.
    None, wenn weder X noch Überschrift."""
    x = any(re.search(r"X$", re.sub(r"\s+", "", z)) for z in zertifikate or ())
    for nr, text, m in _je_seite(seiten, BEDINGUNGEN):
        rest = m.group("rest").strip()
        if rest and not re.match(r"^[:.]*\s*(?:/.{0,70})?$", rest) and not KEINE.match(
                rest.strip(":. ")):
            continue  # Fließtext wie „… Specific Conditions of Use specified in the schedule“
        folgt = [z for _, z in _zeilen_nach(text, m.end(), 2)]
        keine = bool(KEINE.match(rest.strip(":. "))) or bool(folgt and KEINE.match(folgt[0]))
        return _angabe(None, nr, text, m.start(), m.end() + 60, ocr, x=x, keine=keine)
    if x and doc_type and "baumuster" in doc_type:
        return {"wert": None, "x": True, "keine": False, "seite": None, "text": None,
                "hinweis": "Das „X“ an der Nummer zeigt, dass besondere Bedingungen gelten. Die "
                           "Überschrift dazu ließ sich im Text nicht lesen."}
    if x:  # z. B. eine Erklärung, die die X-Bescheinigung nennt: Beleg ist die Nummer
        hinweis = ("Das „X“ an der Nummer zeigt, dass besondere Bedingungen gelten. Sie stehen "
                   "im Zertifikat, nicht in diesem Dokument.")
        for z in zertifikate:
            if not re.search(r"X$", re.sub(r"\s+", "", z)):
                continue
            muster = re.compile(r"\s*".join(map(re.escape, re.sub(r"\s+", "", z))))
            for nr, text, m in _je_seite(seiten, muster):
                return _angabe(None, nr, text, m.start(), m.end(), ocr, x=True, keine=False,
                               nummer=z, hinweis=hinweis)
        return {"wert": None, "x": True, "keine": False, "seite": None, "text": None,
                "hinweis": hinweis}
    return None


IECEX = re.compile(r"\bIECEx[ \t]+[A-Z]{2,6}[ \t]+\d{2}\.\d{4}[ \t]?[XU]?\b")


def iecex(seiten, ocr=()):
    """IECEx-Nummern wie geschrieben, z. B. „IECEx PTB 09.0035U“, je eine mit erster Stelle."""
    funde = {}
    for nr, text, m in _je_seite(seiten, IECEX):
        wert = re.sub(r"\s+", " ", m.group()).replace(" X", "X").replace(" U", "U")
        funde.setdefault(wert, _angabe(wert, nr, text, m.start(), m.end(), ocr))
    return list(funde.values())[:3]


# --- alles zusammen -----------------------------------------------------------------------------

def lies(seiten, ocr=(), doc_type=None, kennz=None, zertifikate=None, stueckliste=None):
    """Alle Angaben eines Dokuments. seiten: Text je Seite; ocr: Seiten (ab 1) aus OCR;
    kennz und zertifikate: Kennzeichnungen und Nummern des Vorschlags; stueckliste: Hersteller
    der Position, für den Vergleich mit dem Hersteller im Dokument."""
    seiten = [_text(s) for s in seiten]
    ocr = tuple(ocr or ())
    zertifikate = list(zertifikate or [])
    bis = gueltig_bis(seiten, ocr)
    pos = bis.pop("_pos", None)
    herst = hersteller(seiten, ocr, doc_type)
    if herst is not None:
        herst["stueckliste"] = stueckliste or None
        herst["passt"] = pruefung.hersteller_passt(herst["wert"], stueckliste,
                                                   _einstellung("HERSTELLER_AEHNLICHKEIT",
                                                                pruefung.AEHNLICHKEIT))
    return {
        "version": VERSION,
        "gueltig_bis": bis,
        "ausstellungsdatum": ausstellungsdatum(seiten, ocr, pos),
        "ausgabe": ausgabe(seiten, ocr, zertifikate),
        "richtlinien": richtlinien(seiten, ocr),
        "normen": normen(seiten, ocr),
        "stellen": stellen(seiten, ocr, zertifikate),
        "hersteller": herst,
        "unterschrift": unterschrift(seiten, ocr),
        "kennzeichnungen": kennzeichnungen(seiten, ocr, kennz),
        "umgebungstemperatur": umgebungstemperatur(seiten, ocr),
        "besondere_bedingungen": besondere_bedingungen(seiten, ocr, zertifikate, doc_type),
        "iecex": iecex(seiten, ocr),
    }


def _einstellung(name, standard):
    """Wert aus der Konfiguration, ohne App (Tests der reinen Regeln) der Standard."""
    from flask import current_app, has_app_context
    return current_app.config.get(name, standard) if has_app_context() else standard


def _liste(text):
    return [t.strip() for t in (text or "").split(";") if t.strip()]


def fuelle(finding, item, basis):
    """Setzt finding.angaben aus der Datei, wenn sie noch fehlen. False ohne Datei."""
    from . import freigabe, pdf_text  # erst hier: freigabe importiert die Modelle

    if finding.angaben is not None:
        return True
    pfad = freigabe.datei(finding, basis)
    if pfad is None:
        return False
    pdf = pdf_text.lies(pfad)
    finding.angaben = lies(pdf.seiten, pdf.ocr, finding.doc_type, _liste(finding.ex_marking_raw),
                           _liste(finding.certificate_no), item.manufacturer if item else None)
    return True


def pruefe(finding, heute=None, warn_tage=None):
    """Vorprüfung der Angaben einmal je Vorschlag (services/pruefung.py): passt Ergebnis und
    Meldung an und merkt die Meldungen in angaben["vorpruefung"]. Bleibt ein Rat: entscheiden
    tut ein Mensch."""
    a = finding.angaben
    if not a or "vorpruefung" in a:
        return
    if warn_tage is None:
        warn_tage = _einstellung("ABLAUF_WARNUNG_TAGE", 180)
    meldungen = pruefung.pruefe_angaben(a, heute or date.today(), warn_tage, finding.doc_type)
    finding.check_result, finding.check_message = pruefung.bewerte_angaben(
        finding.check_result, finding.check_message, meldungen)
    finding.angaben = dict(a, vorpruefung=[{"stufe": s, "text": t} for s, t in meldungen])


# --- Anzeige ----------------------------------------------------------------------------------

UNSICHER = " Dieser Text stammt aus einem eingescannten Blatt und ist nicht sicher gelesen."


def _tt(iso):
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _und(teile):
    """[„a“, „b“, „c“] -> „a, b und c“."""
    teile = [t for t in teile if t]
    return ", ".join(teile[:-1]) + " und " + teile[-1] if len(teile) > 1 else "".join(teile)


def _beleg(a, *weitere):
    """{seite, text} für die Oberfläche; None ohne Seite (dann „unbelegt“). Weitere Stellen auf
    anderen Seiten stehen mit „Seite n:“ im Text; Text aus OCR sagt, dass er unsicher ist."""
    if not a or not a.get("seite") or not a.get("text"):
        return None
    text = a["text"]
    for w in weitere:
        if w and w.get("text") and w.get("text") not in text:
            text += f" … Seite {w['seite']}: {w['text']}" if w.get("seite") != a["seite"] \
                else f" … {w['text']}"
    if a.get("ocr") or any(w and w.get("ocr") for w in weitere):
        text += UNSICHER
    return {"seite": a["seite"], "text": text}


def _kennzeichnung(k):
    teile = [k.get("zuendschutz"), k.get("gruppe") and f"Gruppe {k['gruppe']}",
             k.get("temperatur") and f"Temperatur {k['temperatur']}",
             k.get("epl") and f"Schutzniveau {k['epl']}"]
    return ", ".join(t for t in teile if t)


def _stelle(s):
    name = s.get("name") or "Name nicht lesbar"
    if s.get("nummer"):
        name += f", Nummer {s['nummer']}"
    if s.get("rolle") == "qm":
        name += ", überwacht die Fertigung"
    return name


def felder(a, heute=None):
    """Zeilen für die Prüfseite, gruppiert: (kopf, rest), je [(Titel, {wert, beleg})].

    kopf (Gültigkeit, Ausstellung) steht oben, rest nach den Belegen. Nur Angaben, die im
    Dokument stehen; „gültig bis“ ohne Angabe fehlt (ATEX-Bescheinigungen nennen meist keins).
    Die Texte liest eine Sachbearbeiterin: kurz, ohne Fachwörter der Software.
    """
    heute = heute or date.today()
    kopf, rest = [], []
    if not a:
        return kopf, rest
    bis = a.get("gueltig_bis") or {}
    if bis.get("status") == "datum":
        tage = (date.fromisoformat(bis["wert"]) - heute).days
        stand = (f"seit {-tage} Tagen abgelaufen" if tage < -1 else "seit gestern abgelaufen"
                 if tage == -1 else "läuft heute ab" if tage == 0 else f"noch {tage} Tage gültig"
                 if tage > 1 else "noch 1 Tag gültig")
        kopf.append(("Gültig bis", {"wert": f"{_tt(bis['wert'])}, {stand}", "beleg": _beleg(bis)}))
    elif bis.get("status") == "unbefristet":
        kopf.append(("Gültig bis", {"wert": "Unbefristet laut Dokument", "beleg": _beleg(bis)}))
    aus, nr = a.get("ausstellungsdatum"), a.get("ausgabe")
    if aus or nr:
        wert = ", ".join(t for t in ((aus and f"Am {_tt(aus['wert'])}"),
                                     nr and f"Ausgabe {nr['wert']}") if t)
        kopf.append(("Ausgestellt", {"wert": wert, "beleg": _beleg(aus or nr, aus and nr)}))

    kz = [k for k in a.get("kennzeichnungen") or [] if k.get("zuendschutz") or k.get("gruppe")]
    if kz:
        wert = _kennzeichnung(kz[0])
        if len(kz) > 1:
            wert += (f". Dazu {len(kz) - 1} weitere Kennzeichnung"
                     f"{'en' if len(kz) > 2 else ''} im Dokument")
        rest.append(("Zündschutzart", {"wert": wert, "beleg": _beleg(kz[0])}))
    ta = a.get("umgebungstemperatur") or []
    if ta:
        werte = [t["wert"].replace(" … ", " bis ") for t in ta]
        rest.append(("Umgebungstemperatur", {"wert": " oder ".join(werte),
                                             "beleg": _beleg(ta[0])}))
    bb = a.get("besondere_bedingungen")
    if bb:
        if bb.get("keine"):
            wert = "Keine"
        elif bb.get("nummer"):
            wert = f"Ja, sie stehen im Zertifikat {bb['nummer']}"
        elif bb.get("seite"):
            wert = f"Ja, sie stehen auf Seite {bb['seite']}"
        else:
            wert = "Ja, die Stelle im Text ließ sich aber nicht lesen"
        rest.append(("Besondere Bedingungen", {"wert": wert, "beleg": _beleg(bb)}))
    if a.get("iecex"):
        rest.append(("IECEx-Nummer", {"wert": _und([i["wert"] for i in a["iecex"]]),
                                      "beleg": _beleg(a["iecex"][0])}))
    stellen_ = a.get("stellen") or []
    if stellen_:
        rest.append(("Benannte Stelle", {"wert": _und([_stelle(s) for s in stellen_]),
                                         "beleg": _beleg(stellen_[0])}))
    h = a.get("hersteller")
    if h:
        wert = h["wert"] + (f", {h['ort']}" if h.get("ort") else "")
        if h.get("passt") is True:
            wert += ". Passt zur Stückliste"
        elif h.get("passt") is False:
            wert += f". In der Stückliste steht aber „{h.get('stueckliste')}“"
        rest.append(("Hersteller laut Dokument", {"wert": wert, "beleg": _beleg(h)}))
    if a.get("richtlinien"):
        rest.append(("Richtlinien", {"wert": _und([r["wert"] for r in a["richtlinien"]]),
                                     "beleg": _beleg(a["richtlinien"][0])}))
    normen_ = a.get("normen") or []
    if normen_:
        ex = [n["wert"] for n in normen_ if n.get("ex")]
        andere = len(normen_) - len(ex)
        if ex and andere:
            wert = ", ".join(ex) + f" und {andere} weitere Norm{'en' if andere > 1 else ''}"
        else:
            wert = _und(ex or [n["wert"] for n in normen_])
        rest.append(("Genannte Normen", {"wert": wert, "beleg": _beleg(normen_[0])}))
    u = a.get("unterschrift")
    if u:
        wert = f"{u['ort']}, {_tt(u['datum'])}"
        if u.get("unterzeichner"):
            wert += f", unterschrieben von {_und(u['unterzeichner'])}"
        rest.append(("Unterschrift", {"wert": wert, "beleg": _beleg(u)}))
    return kopf, rest


# --- Fristen und Nachlesen ----------------------------------------------------------------------

def _eintrag(quelle, bis, heute, **mehr):
    return {"quelle": quelle, "gueltig_bis": bis.isoformat(), "abgelaufen": bis < heute,
            "tage": (bis - heute).days, **mehr}


def fristen(heute, tage):
    """Zertifikate, die bis heute + tage ablaufen oder schon abgelaufen sind, nach Datum.

    quelle „stammdaten“: Document.valid_until (mit den Bauteilen dazu); „vorschlag“: offener
    Vorschlag mit „gültig bis“ aus dem Dokument; „freigegeben“: freigegebener Vorschlag, dessen
    Dokument in den Stammdaten noch kein valid_until hat (z. B. vor dieser Funktion freigegeben).
    """
    from ..db import db
    from ..models import Document, Finding, PartDocument

    grenze = heute + timedelta(days=tage)
    eintraege, mit_datum = [], set()
    for doc in db.session.scalars(db.select(Document).where(Document.valid_until.is_not(None))):
        mit_datum.add(doc.file_hash)
        if doc.valid_until > grenze:
            continue
        teile = [{"profile_no": pd.part.profile_no, "artikel": pd.part.manufacturer_article_no,
                  "hersteller": pd.part.manufacturer.name if pd.part.manufacturer else None,
                  "status": pd.status}
                 for pd in db.session.scalars(db.select(PartDocument).filter_by(document_id=doc.id))]
        eintraege.append(_eintrag("stammdaten", doc.valid_until, heute, dokument_id=doc.id,
                                  doc_type=doc.doc_type, zertifikat=doc.certificate_no,
                                  datei=doc.file_path or doc.url, teile=teile))
    for f in db.session.scalars(db.select(Finding).where(
            Finding.angaben.is_not(None), Finding.status.in_(("vorgeschlagen", "freigegeben")))):
        b = (f.angaben or {}).get("gueltig_bis") or {}
        if b.get("status") != "datum" or not b.get("wert"):
            continue
        bis = date.fromisoformat(b["wert"])
        if bis > grenze or (f.status == "freigegeben" and f.file_hash in mit_datum):
            continue
        i = f.item
        eintraege.append(_eintrag(
            "vorschlag" if f.status == "vorgeschlagen" else "freigegeben", bis, heute,
            finding_id=f.id, bom_id=i.bom_id, position=i.pos, doc_type=f.doc_type,
            zertifikat=f.certificate_no, datei=f.file_path or f.url,
            teile=[{"profile_no": i.profile_no, "artikel": i.manufacturer_article_no,
                    "hersteller": i.manufacturer, "status": f.status}],
            beleg={"seite": b.get("seite"), "text": b.get("text"), "ocr": bool(b.get("ocr"))}))
    eintraege.sort(key=lambda e: (e["gueltig_bis"], e["quelle"]))
    return eintraege


def nachlesen(basis, alle=False):
    """Angaben für Vorschläge nachlesen, deren Datei noch da ist. Ohne alle nur fehlende
    (wiederholbar: ein zweiter Lauf liest nichts mehr), mit alle auch solche mit älterem
    Regelstand (VERSION). Offene Vorschläge bekommen die Vorprüfung einmal, entschiedene nur die
    Werte; Stammdaten bleiben unberührt. Speichern (commit) macht der Aufrufer.
    Gibt {"gelesen", "ohne_datei", "schon_da"} zurück."""
    from ..db import db
    from ..models import Finding

    zahl = {"gelesen": 0, "ohne_datei": 0, "schon_da": 0}
    for f in db.session.scalars(db.select(Finding).where(Finding.file_path.is_not(None))
                                .order_by(Finding.id)):
        if f.angaben is not None and not (alle and f.angaben.get("version") != VERSION):
            zahl["schon_da"] += 1
            continue
        vorher = f.angaben
        f.angaben = None
        if not fuelle(f, f.item, basis):
            f.angaben = vorher
            zahl["ohne_datei"] += 1
            continue
        if vorher and "vorpruefung" in vorher:  # die Meldung nicht doppelt anhängen
            f.angaben = dict(f.angaben, vorpruefung=vorher["vorpruefung"])
        elif f.status == "vorgeschlagen":
            pruefe(f)
        zahl["gelesen"] += 1
    return zahl
