"""Erlaubte Dateiformate: Endung UND erkannter Inhalt müssen zusammenpassen.

Das Archiv nimmt außer PDFs übliche Office-Dateien, Bilder, Zeichnungen und Archive an. Es legt
sie nur ab: nichts wird ausgeführt, entpackt oder gelesen. Nur aus PDFs liest es Angaben, nur auf
PDFs laufen die Prüfregeln. Ausgeliefert werden nur PDFs und gängige Bilder im Browser, alles
andere als Download (routen.datei_antwort).
"""

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO


@dataclass(frozen=True)
class Format:
    endung: str      # ohne Punkt, klein: „pdf“, „docx“
    familie: str     # pdf, office, bild, zeichnung, archiv, text
    mimetype: str
    name: str        # für Anwender: „Word-Datei“
    im_browser: bool  # inline zeigen (PDF, gängige Bilder), sonst nur herunterladen


_OOXML = "application/vnd.openxmlformats-officedocument."
FORMATE = {f.endung: f for f in (
    Format("pdf", "pdf", "application/pdf", "PDF", True),
    Format("docx", "office", _OOXML + "wordprocessingml.document", "Word-Datei", False),
    Format("xlsx", "office", _OOXML + "spreadsheetml.sheet", "Excel-Datei", False),
    Format("pptx", "office", _OOXML + "presentationml.presentation", "PowerPoint-Datei", False),
    Format("doc", "office", "application/msword", "Word-Datei", False),
    Format("xls", "office", "application/vnd.ms-excel", "Excel-Datei", False),
    Format("ppt", "office", "application/vnd.ms-powerpoint", "PowerPoint-Datei", False),
    Format("odt", "office", "application/vnd.oasis.opendocument.text", "OpenDocument-Text", False),
    Format("ods", "office", "application/vnd.oasis.opendocument.spreadsheet",
           "OpenDocument-Tabelle", False),
    Format("rtf", "office", "application/rtf", "RTF-Datei", False),
    Format("txt", "text", "text/plain", "Textdatei", False),
    Format("csv", "text", "text/csv", "CSV-Datei", False),
    Format("png", "bild", "image/png", "Bild", True),
    Format("jpg", "bild", "image/jpeg", "Bild", True),
    Format("jpeg", "bild", "image/jpeg", "Bild", True),
    Format("gif", "bild", "image/gif", "Bild", True),
    Format("tif", "bild", "image/tiff", "Bild", False),
    Format("tiff", "bild", "image/tiff", "Bild", False),
    Format("dwg", "zeichnung", "image/vnd.dwg", "CAD-Zeichnung", False),
    Format("dxf", "zeichnung", "image/vnd.dxf", "CAD-Zeichnung", False),
    Format("stp", "zeichnung", "model/step", "3D-Modell", False),
    Format("step", "zeichnung", "model/step", "3D-Modell", False),
    Format("zip", "archiv", "application/zip", "ZIP-Archiv", False),
    Format("7z", "archiv", "application/x-7z-compressed", "7-Zip-Archiv", False),
)}
FAMILIEN = {"pdf": "PDF", "office": "Office-Datei", "bild": "Bild", "zeichnung": "Zeichnung",
            "archiv": "Archiv (ZIP)", "text": "Textdatei"}
ERLAUBT_TEXT = ("Erlaubt sind PDF, Word, Excel, PowerPoint, OpenDocument, Text und CSV, Bilder "
                "(PNG, JPG, GIF, TIFF), Zeichnungen (DWG, DXF, STEP) und Archive (ZIP, 7z).")

# Inhalt an den ersten Bytes erkennen; ZIP-Container genauer über das Inhaltsverzeichnis
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_INHALT = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/", "odt": "content.xml",
               "ods": "content.xml"}


class Formatfehler(ValueError):
    """Datei nicht erlaubt; die Meldung geht so an den Anwender."""


def endung(name):
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", (name or "").strip())
    return m.group(1).lower() if m else ""


def von_endung(e):
    """Format zur Endung eines gespeicherten Dokuments; alte Zeilen ohne Endung sind PDFs."""
    return FORMATE.get((e or "pdf").lower(), FORMATE["pdf"])


def _ist_text(inhalt):
    probe = inhalt[:8192]
    if b"\x00" in probe:
        return False
    for kodierung in ("utf-8", "cp1252"):
        try:
            probe.decode(kodierung)
            return True
        except UnicodeDecodeError:
            # ein abgeschnittenes Mehrbytezeichen am Ende der Probe zählt nicht
            try:
                probe[:-3].decode(kodierung)
                return True
            except UnicodeDecodeError:
                continue
    return False


def _zip_namen(inhalt):
    try:
        with zipfile.ZipFile(BytesIO(inhalt)) as z:  # liest nur das Inhaltsverzeichnis
            return z.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return None


def inhalt_passt(e, inhalt):
    """Passt der Inhalt zur Endung? Nur die ersten Bytes und bei ZIP das Inhaltsverzeichnis."""
    kopf = inhalt[:16]
    familie = FORMATE[e].familie
    if e == "pdf":
        return b"%PDF" in inhalt[:1024]
    if e in _ZIP_INHALT or e == "zip":
        if not kopf.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            return False
        if e == "zip":
            return True
        namen = _zip_namen(inhalt)
        return namen is not None and any(n.startswith(_ZIP_INHALT[e]) for n in namen)
    if e in ("doc", "xls", "ppt"):
        return kopf.startswith(_OLE)
    if e == "rtf":
        return kopf.startswith(b"{\\rtf")
    if e == "png":
        return kopf.startswith(b"\x89PNG\r\n\x1a\n")
    if e in ("jpg", "jpeg"):
        return kopf.startswith(b"\xff\xd8\xff")
    if e == "gif":
        return kopf.startswith((b"GIF87a", b"GIF89a"))
    if e in ("tif", "tiff"):
        return kopf.startswith((b"II*\x00", b"MM\x00*"))
    if e == "dwg":
        return kopf.startswith(b"AC10")
    if e == "7z":
        return kopf.startswith(b"7z\xbc\xaf\x27\x1c")
    if e in ("stp", "step"):
        return inhalt[:200].lstrip().startswith(b"ISO-10303-21")
    if familie == "text" or e == "dxf":
        return _ist_text(inhalt)
    return False


def pruefe(name, inhalt, hoechstens=None):
    """Format der Datei, sonst Formatfehler mit einem Satz für Anwender."""
    if not inhalt:
        raise Formatfehler("Die Datei ist leer. Bitte wählen Sie die richtige Datei aus.")
    if hoechstens and len(inhalt) > hoechstens:
        raise Formatfehler(f"Die Datei ist zu groß. Das Dokumentenarchiv nimmt Dateien bis "
                           f"{hoechstens // (1024 * 1024)} MB an.")
    e = endung(name)
    if not e:
        raise Formatfehler("Die Datei hat keine Endung wie .pdf oder .docx. Bitte benennen Sie "
                           "sie passend und laden Sie sie noch einmal hoch.")
    if e not in FORMATE:
        raise Formatfehler(f"Dateien mit der Endung .{e} nimmt das Dokumentenarchiv nicht an. "
                           + ERLAUBT_TEXT)
    if not inhalt_passt(e, inhalt):
        raise Formatfehler(f"Der Inhalt der Datei passt nicht zur Endung .{e}. Bitte prüfen "
                           "Sie, ob die Datei beschädigt oder falsch benannt ist.")
    return FORMATE[e]


def nicht_auslesbar_grund(f):
    """Satz für Anwender, warum das Archiv aus dieser Datei keine Angaben liest; PDF: None."""
    if f.familie == "pdf":
        return None
    if f.familie == "archiv":
        return ("Ein Archiv wird nur abgelegt und nie geöffnet. Bitte tragen Sie die Angaben "
                "von Hand ein.")
    return (f"Aus einer Datei vom Typ {f.name} liest das Dokumentenarchiv keine Angaben, und es "
            "prüft sie nicht auf veraltete Inhalte. Bitte tragen Sie die Angaben von Hand ein.")


def liste():
    """Für die Oberfläche: Endungen, Familien, was im Browser angezeigt wird."""
    return [{"endung": f.endung, "familie": f.familie, "name": f.name, "mimetype": f.mimetype,
             "im_browser": f.im_browser, "auslesbar": f.familie == "pdf"}
            for f in FORMATE.values()]
