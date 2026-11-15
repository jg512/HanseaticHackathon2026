"""Wörter für Anwender: technischer Wert -> was in der Oberfläche steht.

Die API behält ihre technischen Werte und liefert die Texte daneben (Felder „…_text“) und gesammelt
unter GET /api/begriffe, damit jede Oberfläche dieselben Wörter zeigt. Für Anwender heißt der Dienst
„Dokumentenarchiv“. Tabelle „Begriffe“ in README.md.
"""

from datetime import date, datetime

DIENST = "Dokumentenarchiv"

DOC_TYPE = {
    "eu_baumusterpruefbescheinigung": "EU-Baumusterprüfbescheinigung",
    "eu_konformitaetserklaerung": "EU-Konformitätserklärung",
    "uk_baumusterpruefbescheinigung": "UK-Baumusterprüfbescheinigung",
    "uk_konformitaetserklaerung": "UK-Konformitätserklärung",
    "bedienungsanleitung": "Bedienungsanleitung",
    "datenblatt": "Datenblatt",
    "zeichnung": "Zeichnung",
    "sonstiges": "Sonstiges Dokument",
    "unbekannt": "Dokumentart noch offen",
}
# Gesamtstand eines Dokuments und Stand einer einzelnen Markierung
MARKIERUNGSSTAND = {
    "ohne": "Unauffällig",
    "verdacht": "Möglicherweise veraltet",
    "bestaetigt": "Veraltet, Ersatz wird gesucht",
    "verworfen": "Geprüft, gilt weiter",
}
STUFE = {"veraltet": "Spricht für veraltet", "unklar": "Lässt sich nicht beurteilen"}
# Stand einer Version (Verknüpfung Teil–Dokument)
VERSION = {
    "aktuell": "Aktuelle Version",
    "abgeloest": "Durch neuere Version ersetzt",
    "beendet": "Beendet, ohne Nachfolger",
}
ABLAGE = {"aktuell": "In Gebrauch", "archiv": "Im Archiv"}
AUSGEMUSTERT = "Im Archiv abgelegt (nicht mehr in Gebrauch)"
# Wie ein Dokument ins Archiv kam (Quellennachweis, Feld quelle_art)
QUELLE_ART = {
    "altbestand": "Übernommener Bestand",
    "internet": "Im Internet gefunden",
    "hersteller": "Vom Hersteller geschickt",
    "hochgeladen": "Von Hand hochgeladen",
    "atex_agent": "Bei einer Freigabe im ATEX-Agent übernommen",
}
# Wie eine Version entstand (Verknüpfung.quelle)
VERSION_QUELLE = {
    "altbestand": "Beim Einlesen des Bestands zugeordnet",
    "upload": "Hochgeladen",
    "manuell": "Von Hand zugeordnet",
    "atex_agent": "Bei einer Freigabe im ATEX-Agent übernommen",
}
OCR_STAND = {
    "offen": "Texterkennung steht noch aus",
    "fertig": "Text aus der Texterkennung",
    "kein_text": "Texterkennung fand keinen Text",
    "fehler": "Texterkennung gescheitert",
}
# Angaben aus dem Dokument (Angabe.art) und Herkunft einer Angabe (Angabe.quelle)
ANGABE = {
    "dokumentart": "Dokumentart", "richtlinie": "Richtlinie", "norm": "Normausgabe",
    "zertifikatsnummer": "Zertifikatsnummer", "ausstellungsdatum": "Ausstellungsdatum",
    "gueltig_bis": "Gültig bis", "sprache": "Sprache",
}
ANGABE_QUELLE = {"pdf": "Aus dem PDF gelesen", "ocr": "Aus der Texterkennung",
                 "manuell": "Von Hand eingetragen"}
# Änderungsprotokoll: Objekt und Feld
OBJEKT = {"teil": "Teil", "dokument": "Dokument", "verknuepfung": "Version",
          "regeln": "Prüfregeln", "norm_ausgabe": "Normausgabe"}
FELD = {
    "angelegt": "Angelegt", "doc_type": "Dokumentart", "ausstellungsdatum": "Ausstellungsdatum",
    "gueltig_bis": "Gültig bis", "zertifikatsnummern": "Zertifikatsnummern",
    "sprachen": "Sprachen", "richtlinien": "Richtlinien", "normen": "Normausgaben",
    "hersteller": "Hersteller", "artikelnr": "Hersteller-Artikelnummer",
    "profile_no": "PRO.FILE-Nummer", "bezeichnung": "Bezeichnung",
    "ausgemustert": "Archiv", "stand": "Stand der Version",
    "ocr": "Texterkennung", "auslesung": "Angaben neu gelesen",
    "pruefen": "Prüfregeln angewendet",
    "quelle_art": "Herkunft", "quelle_link": "Download-Link", "quelle_seite": "Gefunden auf Seite",
    "quelle_abgerufen_am": "Abgerufen am", "quelle_von": "Übernommen von",
    "quelle_dateiname": "Ursprünglicher Dateiname", "quelle_hinweis": "Hinweis zur Herkunft",
    "quellennachweis": "Herkunft",
}
# Werte, die im Protokoll stehen können (Stand einer Version, Ausmustern)
WERT = {**VERSION, "ja": "im Archiv", "wiederhergestellt": "wieder in Gebrauch",
        "bestätigt": "bestätigt"}
# Systemnamen in „von“, auch alte Einträge („Import Altbestand“)
VON = {"Import Altbestand": "Bestand einlesen", "Bestand einlesen": "Bestand einlesen",
       "Regel": "Prüfregel", "Texterkennung": "Texterkennung",
       "Auslesung neu": "Angaben neu gelesen", "Konfiguration": "Grundeinstellung",
       "Vorgabe des Teams": "Vorgabe des Teams", "nicht angegeben": "ohne Namen"}
BEREICHE = {"altbestand": "Übernommener Bestand", "import": "Bestand einlesen",
            "regeln": "Prüfregeln", "versionen": "Versionen", "teile": "Teile",
            "neue_suche": "Ersatz wird gesucht"}


def text(tafel, wert):
    """Text zum Wert, unbekannte Werte unverändert."""
    return tafel.get(wert, wert) if wert is not None else None


def datum(wert):
    """ISO-Datum oder datetime -> „13.11.2026“ bzw. „13.11.2026, 14:05 Uhr“."""
    if isinstance(wert, datetime):
        return f"{wert:%d.%m.%Y, %H:%M} Uhr"
    if isinstance(wert, date):
        return f"{wert:%d.%m.%Y}"
    if isinstance(wert, str) and len(wert) == 10 and wert[4] == "-" and wert[7] == "-":
        try:
            return f"{date.fromisoformat(wert):%d.%m.%Y}"
        except ValueError:
            return wert
    return wert


def wert_text(feld, wert):
    """Ein Wert aus dem Änderungsprotokoll in Worten."""
    if wert is None or wert == [] or wert == "":
        return "leer"
    if feld == "doc_type":
        return text(DOC_TYPE, wert)
    if feld == "quelle_art":
        return text(QUELLE_ART, wert)
    if isinstance(wert, list):
        return ", ".join(str(datum(x)) for x in wert)
    if isinstance(wert, dict):
        if "jahr" in wert:  # Normtabelle
            return f"Ausgabe {wert['jahr']}" + (f" ({wert['hinweis']})" if wert.get("hinweis")
                                                else "")
        if "neu" in wert and "entfallen" in wert:  # Lauf der Prüfregeln
            return f"{wert['neu']} neue Hinweise, {wert['entfallen']} entfallen"
        if "dokument_ids" in wert:
            ids = wert["dokument_ids"]
            return "alle Dokumente" if ids == "alle" else f"{len(ids)} Dokumente"
        return ", ".join(f"{text(FELD, k)}: {wert_text(k, v)}" for k, v in wert.items())
    if isinstance(wert, str):
        return str(datum(text(WERT, wert)))
    return str(wert)


def alle():
    """Alles für GET /api/begriffe."""
    from . import dateiformate, regeln
    return {"dienst": DIENST, "doc_type": DOC_TYPE, "markierungsstand": MARKIERUNGSSTAND,
            "stufe": STUFE, "version": VERSION, "ablage": ABLAGE, "ausgemustert": AUSGEMUSTERT,
            "quelle_art": QUELLE_ART, "version_quelle": VERSION_QUELLE, "ocr_stand": OCR_STAND,
            "angabe": ANGABE, "angabe_quelle": ANGABE_QUELLE, "objekt": OBJEKT, "feld": FELD,
            "bereiche": BEREICHE, "regeln": regeln.REGELN,
            "regeln_gilt_fuer": {r: [text(DOC_TYPE, a) for a in arten]
                                 for r, arten in regeln.GILT_FUER.items()},
            "dateiformate": dateiformate.liste(), "dateiformate_text": dateiformate.ERLAUBT_TEXT,
            "familien": dateiformate.FAMILIEN}
