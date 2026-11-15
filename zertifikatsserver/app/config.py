"""Konfiguration, alles über Umgebungsvariablen (siehe ../.env.example)."""

import os


class Config:
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///zertifikate.db")
    # größte Datei beim Hochladen und im Bestand (MB); nginx lässt bis 64 MB durch
    MAX_CONTENT_LENGTH = int(os.environ.get("DATEI_MAX_MB", 50)) * 1024 * 1024

    # Eigene Ablage der PDFs: aktuell/<Hersteller>/ und archiv/<Hersteller>/
    ABLAGE = os.environ.get("ABLAGE", "/ablage")
    # Alter Ordner <Hersteller>/*.pdf, nur lesend; Unterordner mit „_“ sind Zwischenspeicher
    ALTBESTAND = os.environ.get("ALTBESTAND", "/altbestand")
    # Ordnername im Altbestand -> Name des Herstellers, „Ordner=Name“ mit Komma getrennt
    HERSTELLER_NAMEN = os.environ.get("HERSTELLER_NAMEN", "P+F=Pepperl+Fuchs")

    # Regel „alt“: Ausstellungsdatum älter als so viele Jahre
    VERALTET_NACH_JAHREN = int(os.environ.get("VERALTET_NACH_JAHREN", 10))
    # Aktuelle Ausgaben der Normen, „Norm:Jahr“ mit Komma getrennt, zusätzlich zu den Vorgaben
    # des Teams (normen.VORGABEN) und vor ihnen. Nur Startwert für Normen ohne Zeile in der
    # Tabelle norm_ausgabe; vorhandene Zeilen überschreibt der Start nie (PUT /api/normen).
    AKTUELLE_NORMEN = os.environ.get("AKTUELLE_NORMEN", "")

    # Texterkennung für Scans (ocr.py): „0“ = aus. Läuft im Hintergrund, nie im Import.
    OCR = os.environ.get("OCR", "1") != "0"
    OCR_SPRACHEN = os.environ.get("OCR_SPRACHEN", "deu+eng+fra")
    # Ergebnisse je Datei (SHA-256), leer = Ordner .ocr in der Ablage
    OCR_CACHE = os.environ.get("OCR_CACHE", "")
    # Wie oft der Hintergrundlauf nach neuen Scans sieht (Sekunden)
    OCR_INTERVALL = int(os.environ.get("OCR_INTERVALL", 60))


def paare(text):
    """„a=b, c=d“ oder „a:b, c:d“ -> [(a, b), (c, d)]; leere Teile fallen weg."""
    ergebnis = []
    for teil in (text or "").split(","):
        for trenner in ("=", ":"):
            if trenner in teil:
                links, rechts = teil.split(trenner, 1)
                if links.strip() and rechts.strip():
                    ergebnis.append((links.strip(), rechts.strip()))
                break
    return ergebnis
