"""Text je Seite aus PDFs lesen, im Prozess zwischengespeichert.

Schlüssel des Zwischenspeichers ist Pfad, Änderungszeit und Größe: Eine geänderte Datei wird neu
gelesen.

Scans: Seiten ohne Textebene liest die Texterkennung (Tesseract über PyMuPDF). Das dauert Sekunden
je Seite, deshalb nie während der Suche, sondern vorab mit `flask --app wsgi ocr` (lies_scans).
Das Ergebnis liegt je Datei in OCR_CACHE; Schlüssel sind SHA-256 der Datei, Sprachen und
Auflösung. lies nimmt nur, was dort liegt, und nennt die Seiten, die noch fehlen. Mit OCR=0 oder
ohne Tesseract bleiben Scans leer wie ohne OCR. OCR-Text wird wie jeder PDF-Text nur durchsucht
und angezeigt.

Tabellen: tabellen liest sie je Seite mit PyMuPDF (find_tables), nur auf Seiten mit Textebene.
Scans haben keine: OCR liefert nur Text, die Zellen wären geraten.
"""

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import pymupdf
from flask import current_app, has_app_context

DPI = 300  # Auflösung für die Texterkennung
_speicher = {}


@dataclass(frozen=True)
class PdfText:
    seiten: tuple   # Text je Seite, Seite 1 an Stelle 0
    sha256: str
    ocr: tuple = ()        # Seiten (ab 1), deren Text per OCR gelesen ist
    ungelesen: tuple = ()  # Seiten ohne Textebene, die OCR noch nicht gelesen hat

    @property
    def lesbar(self):
        return any(s.strip() for s in self.seiten)


@dataclass(frozen=True)
class Ocr:
    cache: Path
    sprachen: str   # z. B. „deu+eng+fra“
    tessdata: str   # Ordner mit den Sprachdaten von Tesseract

    def datei(self, sha256):
        return self.cache / f"{sha256}_{self.sprachen}_{DPI}.json"


def lies(pfad: Path) -> PdfText:
    st = pfad.stat()
    schluessel = (str(pfad), st.st_mtime_ns, st.st_size)
    if schluessel not in _speicher:
        inhalt = pfad.read_bytes()
        try:
            with pymupdf.open(stream=inhalt, filetype="pdf") as doc:
                seiten = tuple(seite.get_text() for seite in doc)
        except (pymupdf.FileDataError, RuntimeError, ValueError):
            seiten = ()  # kaputte Datei: wie ein Scan ohne Text behandeln
        _speicher[schluessel] = PdfText(seiten, hashlib.sha256(inhalt).hexdigest())
    pdf = _speicher[schluessel]
    leer = tuple(nr for nr, s in enumerate(pdf.seiten, start=1) if not s.strip())
    ocr = einstellung() if leer else None
    if not ocr:
        return pdf
    gelesen = _aus_cache(ocr, pdf.sha256)
    if gelesen is None:
        return replace(pdf, ungelesen=leer)
    seiten = tuple(gelesen.get(str(nr), "") if nr in leer else s
                   for nr, s in enumerate(pdf.seiten, start=1))
    return replace(pdf, seiten=seiten, ocr=leer)


def einstellung():
    """OCR-Einstellung aus der Konfiguration; None, wenn OCR aus ist oder Tesseract fehlt."""
    if not has_app_context() or not current_app.config.get("OCR"):
        return None
    sprachen = current_app.config["OCR_SPRACHEN"]
    tessdata, fehler = tesseract(sprachen)
    if fehler:
        return None
    return Ocr(Path(current_app.config["OCR_CACHE"]), sprachen, tessdata)


@lru_cache
def tesseract(sprachen):
    """(Sprachdaten-Ordner, None), sonst (None, Meldung), wenn Tesseract oder eine Sprache fehlt."""
    try:
        ordner = pymupdf.get_tessdata()
    except RuntimeError:
        fehler = "Tesseract ist nicht installiert, Scans werden ohne OCR gelesen."
    else:
        fehlt = [s for s in sprachen.split("+") if not Path(ordner, f"{s}.traineddata").is_file()]
        if not fehlt:
            return ordner, None
        fehler = f"Tesseract-Sprachdaten fehlen ({', '.join(fehlt)}), Scans ohne OCR gelesen."
    if has_app_context():
        current_app.logger.warning(fehler)
    return None, fehler


def _aus_cache(ocr, sha256):
    """{„Seite“: Text} aus dem Zwischenspeicher, None ohne passende, heile Datei."""
    datei = ocr.datei(sha256)
    try:
        schluessel = ("ocr", str(datei), datei.stat().st_mtime_ns)
    except OSError:
        return None
    if schluessel not in _speicher:
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
            seiten = daten["seiten"]
            if ((daten["sha256"], daten["sprachen"], daten["dpi"]) != (sha256, ocr.sprachen, DPI)
                    or not all(isinstance(t, str) for t in seiten.values())):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            seiten = None  # kaputt oder fremd: wie nicht gelesen, der nächste Lauf ersetzt sie
        _speicher[schluessel] = seiten
    return _speicher[schluessel]


def _ocr_seite(seite, ocr):
    tp = seite.get_textpage_ocr(language=ocr.sprachen, dpi=DPI, full=True, tessdata=ocr.tessdata)
    return seite.get_text(textpage=tp)


def _schreibe(ocr, daten):
    """Atomar: erst eine Zwischendatei, dann umbenennen. Gleichzeitige Läufe stören sich nicht."""
    ocr.cache.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ocr.cache, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.chmod(tmp, 0o644)  # mkstemp legt 0600 an; lesen darf auch ein anderer Nutzer
        os.replace(tmp, ocr.datei(daten["sha256"]))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _zeile(text):
    print(text, flush=True)  # sofort im Log, auch im Hintergrund


def lies_scans(wurzel: Path, ausgabe=_zeile):
    """Liest alle Seiten ohne Textebene unter wurzel per OCR in den Zwischenspeicher.

    Gleiche Dateien unter mehreren Namen nur einmal, schon gelesene gar nicht. Rückgabe: 0 ok,
    1 wenn OCR nicht laufen kann oder eine Datei scheiterte.
    """
    if not current_app.config.get("OCR"):
        ausgabe("OCR ist ausgeschaltet (OCR=0).")
        return 0
    ocr = einstellung()
    if not ocr:
        ausgabe(tesseract(current_app.config["OCR_SPRACHEN"])[1])
        return 1
    erledigt, seiten_gesamt, start, fehler = set(), 0, time.monotonic(), 0
    for pfad in sorted(p for p in wurzel.rglob("*") if p.suffix.lower() == ".pdf"):
        pdf = lies(pfad)
        if not pdf.ungelesen or pdf.sha256 in erledigt:
            continue
        erledigt.add(pdf.sha256)
        t = time.monotonic()
        try:
            with pymupdf.open(pfad) as doc:
                texte = {str(nr): _ocr_seite(doc[nr - 1], ocr) for nr in pdf.ungelesen}
        except (RuntimeError, ValueError) as e:
            ausgabe(f"{pfad.relative_to(wurzel)}: OCR gescheitert ({e})")
            fehler += 1
            continue
        try:
            _schreibe(ocr, {"datei": pfad.name, "sha256": pdf.sha256, "sprachen": ocr.sprachen,
                            "dpi": DPI, "seiten": texte})
        except OSError as e:
            ausgabe(f"Zwischenspeicher {ocr.cache} nicht beschreibbar ({e}).")
            return 1
        seiten_gesamt += len(texte)
        ausgabe(f"{pfad.relative_to(wurzel)}: {seitenangabe(pdf.ungelesen)} per OCR gelesen "
                f"({time.monotonic() - t:.0f} s)")
    dauer = time.monotonic() - start
    groesse = sum(p.stat().st_size for p in ocr.cache.glob("*.json")) if ocr.cache.is_dir() else 0
    je_seite = f" ({dauer / seiten_gesamt:.1f} s je Seite)" if seiten_gesamt else ""
    ausgabe(f"OCR fertig: {seiten_gesamt} Seiten aus {len(erledigt) - fehler} Dateien in "
            f"{dauer:.0f} s{je_seite}, Zwischenspeicher {groesse / 1e6:.1f} MB in {ocr.cache}.")
    return 1 if fehler else 0


@dataclass(frozen=True)
class Tabelle:
    seite: int     # ab 1
    nr: int        # ab 1, je Seite in der Reihenfolge von find_tables
    zeilen: tuple  # je Zeile die Zellen als Text, leere Zellen ""


def tabellen(pfad: Path, nr):
    """Tabellen der Seite nr (ab 1), im Prozess zwischengespeichert.

    None, wenn die Seite keine Textebene hat (Scan, Text aus OCR oder noch gar nicht gelesen):
    dort gibt es nichts zu erkennen. Scheitert die Erkennung, gilt die Seite als ohne Tabellen.
    """
    pdf = lies(pfad)
    if nr in pdf.ocr or nr in pdf.ungelesen or not 1 <= nr <= len(pdf.seiten) \
            or not pdf.seiten[nr - 1].strip():
        return None
    schluessel = ("tabellen", pdf.sha256, nr)
    if schluessel not in _speicher:
        try:
            with pymupdf.open(pfad) as doc:
                seite = doc[nr - 1]
                # find_tables sucht Linien; ohne Linien keine Tabelle, die teure Suche entfällt
                linien = sum(len(d["items"]) for d in seite.get_drawings())
                gefunden = seite.find_tables().tables if linien >= 4 else []
                _speicher[schluessel] = tuple(
                    Tabelle(nr, i, tuple(tuple(z or "" for z in zeile) for zeile in t.extract()))
                    for i, t in enumerate(gefunden, start=1))
        except Exception as e:  # noqa: BLE001  Erkennung ist Heuristik, eine Seite darf scheitern
            if has_app_context():
                current_app.logger.warning("Tabellen in %s, Seite %s: %s", pfad.name, nr, e)
            _speicher[schluessel] = ()
    return _speicher[schluessel]


def seitenangabe(nummern):
    """(1, 2, 3, 5) -> „Seiten 1–3, 5“, (4,) -> „Seite 4“."""
    teile, nummern = [], sorted(nummern)
    for nr in nummern:
        if teile and nr == teile[-1][1] + 1:
            teile[-1][1] = nr
        else:
            teile.append([nr, nr])
    text = ", ".join(f"{a}–{b}" if b > a else str(a) for a, b in teile)
    return f"{'Seiten' if len(nummern) > 1 else 'Seite'} {text}"


def dateiname(pfad: Path) -> str:
    """Dateiname ohne Endung, „#U00e4“ aus dem Ordner als „ä“ gelesen."""
    return lesbar(pfad.stem)


# „#U00e4“ (ein Zeichen) und „#L0001f600“ (außerhalb der ersten 65536) stehen in vielen Namen
# der gelieferten Dateien für ein Zeichen, das beim Packen nicht ging.
_ZEICHENCODE = re.compile(r"#U([0-9a-fA-F]{4})|#L([0-9a-fA-F]{8})")


def lesbar(text):
    """Text mit Dateinamen so, wie ein Mensch ihn lesen soll: „Konformit#U00e4tserkl#U00e4rung“
    -> „Konformitätserklärung“. Nur zum Anzeigen; Pfade auf der Platte und in der Datenbank
    bleiben, wie sie sind, sonst findet niemand die Datei wieder."""
    if not text or "#" not in text:
        return text
    def zeichen(m):
        code = int(m.group(1) or m.group(2), 16)
        if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:  # kein Zeichen: so lassen
            return m.group(0)
        return chr(code)
    return _ZEICHENCODE.sub(zeichen, text)
