"""Ein Mensch lädt ein PDF zu einer Position hoch (M18, E6).

- „manuell“: Zertifikat manuell einfügen. Der Mensch sagt, was es ist, und das ist schon die
  Entscheidung: Der Vorschlag wird sofort über services/freigabe.py freigegeben und landet in den
  Stammdaten. Die Vorprüfung läuft trotzdem mit und bleibt am Vorschlag stehen.
- „hersteller“: Antwort des Herstellers auf die Anfrage. Wird ein normaler Vorschlag und wartet
  auf die Prüfung durch einen Menschen.

Ausgewertet wird wie bei einem Fund im Ordner (suche.auswerten). Ohne Typ-Treffer wird nichts
verworfen, sondern „unklar“ mit Begründung vorgelegt. Die Datei liegt unter
<ATEX_DOCS>/_upload/<Hersteller>/; die Ordnersuche überspringt Ordner mit „_“.
"""

import hashlib
import re

from flask import current_app

from . import freigabe, internet, status, suche

QUELLEN = ("manuell", "hersteller")
ORDNER = "_upload"
MAX_BYTES = internet.MAX_BYTES  # wie bei der Websuche: 30 MB
# Texte für Anwender, auch von den Routen benutzt
ZU_GROSS = "Die Datei ist größer als 30 MB. Bitte wählen Sie eine kleinere Datei."
KEIN_PDF = "Das ist keine PDF-Datei. Bitte wählen Sie das Dokument als PDF aus."
ORDNER_WEG = ("Der Ordner mit den Ex-Dokumenten ist gerade nicht erreichbar, deshalb lässt sich "
              "die Datei nicht ablegen. Bitte melden Sie das der Administration.")


class UploadFehler(Exception):
    """Datei wird nicht angenommen; Text für den Menschen und HTTP-Code."""

    def __init__(self, text, code):
        super().__init__(text)
        self.code = code


def sicherer_name(name):
    """Dateiname ohne Pfad, nur Buchstaben, Ziffern, „_“, „+“, „-“, mit Endung „.pdf“."""
    name = re.split(r"[/\\]", name or "")[-1]
    stamm = re.sub(r"(?i)\.pdf$", "", name)
    stamm = re.sub(r"[^\w+-]+", "_", stamm).strip("_")[:100]
    return f"{stamm or 'dokument'}.pdf"


def ablegen(inhalt, sha256, item, wurzel, dateiname):
    """Legt die Datei unter _upload/<Hersteller>/ ab. (Pfad, neu angelegt).

    Gleiche Datei (gleicher Hash) wird nicht doppelt abgelegt; ein belegter Name mit anderem
    Inhalt bekommt den Anfang des Hashes angehängt.
    """
    ordner = (wurzel / ORDNER
              / (re.sub(r"[^\w+-]+", "_", item.manufacturer or "").strip("_") or "unbekannt"))
    if ordner.is_dir():
        for p in sorted(ordner.glob("*.pdf")):
            if hashlib.sha256(p.read_bytes()).hexdigest() == sha256:
                return p, False
    pfad = ordner / sicherer_name(dateiname)
    if pfad.exists():
        pfad = pfad.with_name(f"{pfad.stem}_{sha256[:8]}.pdf")
    ordner.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(pfad.name + ".tmp")
    tmp.write_bytes(inhalt)
    tmp.replace(pfad)
    return pfad, True


def lade_hoch(item, inhalt, dateiname, quelle, von, doc_type, wurzel):
    """Legt die Datei ab und den Vorschlag an; bei „manuell“ gleich freigegeben.

    doc_type: Angabe des Menschen (bei „manuell“ Pflicht), sonst erkennt sie der Code.
    Gibt den Vorschlag zurück. Das Speichern (commit) macht der Aufrufer.
    """
    if not item.profile_no:
        raise UploadFehler("Der Position fehlt die PRO.FILE-Nr., deshalb lässt sich das Dokument "
                           "keinem Bauteil zuordnen. Bitte tragen Sie zuerst die PRO.FILE-Nr. "
                           "ein.", 409)
    if len(inhalt) > MAX_BYTES:
        raise UploadFehler(ZU_GROSS, 413)
    if not inhalt.startswith(b"%PDF"):
        raise UploadFehler(KEIN_PDF, 400)
    sha256 = hashlib.sha256(inhalt).hexdigest()
    for f in item.findings:
        if f.file_hash == sha256 and f.status != "abgelehnt":
            stand = {"vorgeschlagen": "wartet dort auf Ihre Prüfung",
                     "freigegeben": "ist dort schon freigegeben"}.get(f.status, "liegt dort schon")
            raise UploadFehler(f"Dieses Dokument gibt es bei der Position schon. Es {stand}.",
                               409)
    if not wurzel.is_dir():
        current_app.logger.error("Ordner der Ex-Dokumente fehlt: %s", wurzel)
        raise UploadFehler(ORDNER_WEG, 503)
    try:
        pfad, neu = ablegen(inhalt, sha256, item, wurzel, dateiname)
    except OSError as e:
        current_app.logger.error("Ablage %s/ nicht beschreibbar: %s", ORDNER, e)
        raise UploadFehler(ORDNER_WEG, 503) from None

    try:
        finding = suche.auswerten(item, pfad, wurzel)
        finding.source = quelle
        if doc_type:
            finding.doc_type = doc_type
        item.findings.append(finding)
        if quelle == "manuell":
            freigabe.entscheide(finding, "freigeben", von, wurzel, doc_type)
        else:
            status.aktualisiere(item)
    except Exception:
        if neu:  # kein halber Zustand: ohne Vorschlag bleibt die Datei nicht liegen
            pfad.unlink(missing_ok=True)
        raise
    return finding
