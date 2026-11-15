"""Tabellen des Zertifikatsservers. Eigene Datenbank, unabhängig vom Backend.

Ein Teil (Hersteller + Hersteller-Artikelnr.) hat je Dokumentart Versionen 1, 2, …; jede Version
zeigt auf ein oder mehrere Dokumente (z. B. dieselbe Bescheinigung auf Deutsch und Englisch).
Eine neue Version löst die alte ab, die alte bleibt mit ihrem Link erhalten.
Gelöscht wird nichts: Dokumente und Verknüpfungen werden ausgemustert bzw. beendet und lassen sich
wiederherstellen. Jede Änderung von Hand steht mit Name und altem und neuem Wert in aenderung.
"""

from datetime import datetime, timezone

from . import begriffe, dateiformate
from .db import db

# Wie im Backend (stammdaten.DOC_TYPES), dazu „zeichnung“ (nur im Archiv, keine Spalte in der
# Kundenliste) und „unbekannt“ für Scans ohne Textebene und Dateien, deren Art noch niemand gesetzt hat
DOC_TYPES = ("eu_baumusterpruefbescheinigung", "eu_konformitaetserklaerung",
             "uk_baumusterpruefbescheinigung", "uk_konformitaetserklaerung",
             "bedienungsanleitung", "datenblatt", "zeichnung", "sonstiges", "unbekannt")
# Namen der Dokumentarten in Texten für Anwender (wie in der Oberfläche, begriffe.py)
DOC_TYPE_NAMEN = begriffe.DOC_TYPE
# Herkunft eines Dokuments (Quellennachweis)
QUELLE_ARTEN = tuple(begriffe.QUELLE_ART)
# Stand einer Markierung: Regel oder Mensch meldet Verdacht, ein Mensch entscheidet
STAENDE = ("verdacht", "bestaetigt", "verworfen")
# Stufe: „veraltet“ = Beleg spricht dafür, „unklar“ = nicht beurteilbar (z. B. kein Datum)
STUFEN = ("veraltet", "unklar")


def jetzt():
    return datetime.now(timezone.utc).replace(tzinfo=None)  # UTC, ohne Zeitzone gespeichert


class Hersteller(db.Model):
    __tablename__ = "hersteller"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    normalisiert = db.Column(db.String, nullable=False, unique=True)  # ohne Leer- und Satzzeichen
    ordner = db.Column(db.String)  # Ordnername im Altbestand, z. B. „P+F“


class Teil(db.Model):
    __tablename__ = "teil"
    __table_args__ = (db.UniqueConstraint("hersteller_id", "artikelnr_norm"),)

    id = db.Column(db.Integer, primary_key=True)
    hersteller_id = db.Column(db.ForeignKey("hersteller.id"), nullable=False)
    artikelnr = db.Column(db.String, nullable=False)  # Typbezeichnung, wie geschrieben
    artikelnr_norm = db.Column(db.String, nullable=False)  # für Vergleiche
    profile_no = db.Column(db.String)  # PRO.FILE-Nr. (eigene Artikelnummer), wenn bekannt
    bezeichnung = db.Column(db.String)
    angelegt_von = db.Column(db.String, nullable=False)
    angelegt_am = db.Column(db.DateTime, nullable=False, default=jetzt)

    hersteller = db.relationship(Hersteller)
    verknuepfungen = db.relationship("Verknuepfung", back_populates="teil",
                                     order_by=lambda: [Verknuepfung.doc_type,
                                                       Verknuepfung.version, Verknuepfung.id])


class Dokument(db.Model):
    """Eine Datei in der Ablage (PDF, Office, Bild, Zeichnung, Archiv). Gleicher Hash = gleiches
    Dokument (nur einmal abgelegt). Angaben und Prüfregeln nur für PDFs."""
    __tablename__ = "dokument"

    id = db.Column(db.Integer, primary_key=True)
    sha256 = db.Column(db.String, nullable=False, unique=True)
    hersteller_id = db.Column(db.ForeignKey("hersteller.id"), nullable=False)
    dateiname = db.Column(db.String, nullable=False)  # Originalname, lesbar („ä“ statt „#U00e4“)
    pfad = db.Column(db.String, nullable=False)  # relativ zur Ablage: aktuell/… oder archiv/…
    groesse = db.Column(db.Integer, nullable=False)
    seiten = db.Column(db.Integer, nullable=False, default=0)
    lesbar = db.Column(db.Boolean, nullable=False)  # False: Scan ohne Textebene, nicht gelesen
    seiten_ohne_text = db.Column(db.JSON, nullable=False, default=list)
    herkunft = db.Column(db.String, nullable=False)  # „altbestand“, „upload“ (genauer: quelle_art)
    altpfade = db.Column(db.JSON, nullable=False, default=list)  # Pfade im Altbestand
    doc_type = db.Column(db.String, nullable=False)
    # Zusammenfassung der Angaben für Filter; Beleg je Angabe in der Tabelle angabe
    ausstellungsdatum = db.Column(db.Date)
    gueltig_bis = db.Column(db.Date)
    angelegt_von = db.Column(db.String, nullable=False)
    angelegt_am = db.Column(db.DateTime, nullable=False, default=jetzt)
    geprueft_am = db.Column(db.DateTime)  # letzter Lauf der Regeln
    # „Löschen“: nur ausmustern, Datei geht ins Archiv, Link bleibt, wiederherstellbar
    ausgemustert_am = db.Column(db.DateTime)
    ausgemustert_von = db.Column(db.String)
    ausgemustert_grund = db.Column(db.String)
    # Texterkennung (ocr.py) für Seiten ohne Textebene. Stand: leer = nichts zu tun (oder vor
    # der OCR angelegt), „offen“, „fertig“, „kein_text“, „fehler“; Hinweis = Grund in Worten.
    # Neue Spalten sind nullbar: der Start trägt sie in alte Datenbanken nach (schema.py).
    ocr_stand = db.Column(db.String)
    ocr_seiten = db.Column(db.JSON)  # Seiten (ab 1), deren Text per OCR gelesen ist
    ocr_hinweis = db.Column(db.String)
    # Dateiformat als Endung („pdf“, „docx“, „zip“, dateiformate.FORMATE); leer = alte Zeile = PDF
    dateiart = db.Column(db.String)
    # Quellennachweis: woher die Datei kam. Art aus QUELLE_ARTEN; Link = ursprünglicher
    # Download-Link; Seite = Webseite, auf der der Link stand (leer = unbekannt); abgerufen am;
    # übernommen von wem; ursprünglicher Dateiname (beim Bestand der relative Pfad im Ordner).
    quelle_art = db.Column(db.String)
    quelle_link = db.Column(db.String)
    quelle_seite = db.Column(db.String)
    quelle_abgerufen_am = db.Column(db.DateTime)
    quelle_von = db.Column(db.String)
    quelle_dateiname = db.Column(db.String)
    quelle_hinweis = db.Column(db.String)

    hersteller = db.relationship(Hersteller)
    angaben = db.relationship("Angabe", cascade="all, delete-orphan", order_by="Angabe.id")
    markierungen = db.relationship("Markierung", back_populates="dokument",
                                   cascade="all, delete-orphan", order_by="Markierung.id")
    verknuepfungen = db.relationship("Verknuepfung", back_populates="dokument")

    @property
    def ablage(self):
        return self.pfad.split("/", 1)[0]

    @property
    def pdf_url(self):
        """Alter Link, bleibt gültig; liefert jede Dateiart (wie datei_url)."""
        return f"/api/dokumente/{self.id}/pdf"

    @property
    def datei_url(self):
        """Stabiler Link auf die Datei, auch nach Ablösen und Ausmustern."""
        return f"/api/dokumente/{self.id}/datei"

    @property
    def format(self):
        return dateiformate.von_endung(self.dateiart)

    @property
    def ist_pdf(self):
        return self.format.familie == "pdf"

    @property
    def per_ocr(self):
        return bool(self.ocr_seiten)

    @property
    def markierungsstand(self):
        """Gesamtstand: bestätigt vor Verdacht vor verworfen; ohne Markierung „ohne“."""
        staende = {m.status for m in self.markierungen}
        return next((s for s in ("bestaetigt", "verdacht", "verworfen") if s in staende), "ohne")


class Angabe(db.Model):
    """Eine aus dem PDF gelesene Angabe mit Beleg: Seite und Textstelle."""
    __tablename__ = "angabe"

    id = db.Column(db.Integer, primary_key=True)
    dokument_id = db.Column(db.ForeignKey("dokument.id"), nullable=False, index=True)
    # dokumentart, richtlinie, norm, zertifikatsnummer, ausstellungsdatum, gueltig_bis, sprache
    art = db.Column(db.String, nullable=False)
    wert = db.Column(db.String, nullable=False)  # einheitlich, z. B. „94/9/EG“, „60079-0:2012“
    seite = db.Column(db.Integer)  # ab 1; leer nur bei Angaben aus dem Dateinamen
    textstelle = db.Column(db.String)  # wie im PDF geschrieben, mit etwas Umgebung
    sicher = db.Column(db.Boolean, nullable=False, default=True)
    hinweis = db.Column(db.String)  # warum unsicher, oder wer die Angabe von Hand gesetzt hat
    # „pdf“, „ocr“ (Seite per Texterkennung gelesen, Wert kann falsch sein) oder „manuell“
    quelle = db.Column(db.String, nullable=False, default="pdf")


class Verknuepfung(db.Model):
    """Version eines Dokuments zu einem Teil, je Dokumentart. Aktuell: abgeloest_am ist leer.
    Abgelöst: abgeloest_durch zeigt auf die neue Version. Beendet: von Hand, ohne Nachfolger."""
    __tablename__ = "verknuepfung"
    __table_args__ = (db.UniqueConstraint("teil_id", "dokument_id", "doc_type"),)

    id = db.Column(db.Integer, primary_key=True)
    teil_id = db.Column(db.ForeignKey("teil.id"), nullable=False, index=True)
    dokument_id = db.Column(db.ForeignKey("dokument.id"), nullable=False, index=True)
    doc_type = db.Column(db.String, nullable=False)
    version = db.Column(db.Integer, nullable=False)
    gueltig_ab = db.Column(db.DateTime, nullable=False, default=jetzt)
    abgeloest_am = db.Column(db.DateTime)
    abgeloest_durch_id = db.Column(db.ForeignKey("verknuepfung.id"))
    von = db.Column(db.String, nullable=False)
    quelle = db.Column(db.String, nullable=False)  # altbestand, upload, manuell, atex_agent
    beleg = db.Column(db.String)  # woran das Teil erkannt wurde, z. B. „Seite 1: DSM-…“
    hinweis = db.Column(db.String)
    beendet_von = db.Column(db.String)
    beendet_grund = db.Column(db.String)
    # Quellennachweis dieser Version, nur wenn er von dem des Dokuments abweicht (z. B. dieselbe
    # Datei kam später noch einmal auf anderem Weg): {art, link, seite, abgerufen_am, von, dateiname}
    quellennachweis = db.Column(db.JSON)

    teil = db.relationship(Teil, back_populates="verknuepfungen")
    dokument = db.relationship(Dokument, back_populates="verknuepfungen")
    abgeloest_durch = db.relationship("Verknuepfung", remote_side=[id])

    @property
    def stand(self):
        if self.abgeloest_am is None:
            return "aktuell"
        return "abgeloest" if self.abgeloest_durch_id else "beendet"


class Markierung(db.Model):
    """Verdacht „veraltet“: aus einer Regel oder von einem Menschen. Löscht und versteckt nichts."""
    __tablename__ = "markierung"

    id = db.Column(db.Integer, primary_key=True)
    dokument_id = db.Column(db.ForeignKey("dokument.id"), nullable=False, index=True)
    regel = db.Column(db.String)  # Kennung der Regel, leer bei manueller Markierung
    stufe = db.Column(db.String, nullable=False, default="veraltet")
    grund = db.Column(db.String, nullable=False)
    seite = db.Column(db.Integer)
    textstelle = db.Column(db.String)
    status = db.Column(db.String, nullable=False, default="verdacht")
    erstellt_von = db.Column(db.String, nullable=False)  # „Regel“ oder Name
    erstellt_am = db.Column(db.DateTime, nullable=False, default=jetzt)
    entschieden_von = db.Column(db.String)
    entschieden_am = db.Column(db.DateTime)
    entscheidung_grund = db.Column(db.String)

    dokument = db.relationship(Dokument, back_populates="markierungen")


class NormAusgabe(db.Model):
    """Aktuelle Ausgabe einer Norm. Ältere Ausgaben im PDF sind ein Verdacht „veraltet“.

    Zeilen aus den Vorgaben des Teams oder aus AKTUELLE_NORMEN gelten als „zu bestätigen“, bis
    ein benannter Mensch sie bestätigt oder ändert (bestaetigt_von, bestaetigt_am)."""
    __tablename__ = "norm_ausgabe"

    norm = db.Column(db.String, primary_key=True)  # z. B. „60079-0“
    jahr = db.Column(db.Integer, nullable=False)  # Jahr der IEC-Ausgabe (früheste Jahreszahl)
    hinweis = db.Column(db.String)
    geaendert_von = db.Column(db.String, nullable=False)
    geaendert_am = db.Column(db.DateTime, nullable=False, default=jetzt)
    quelle = db.Column(db.String)  # „vorgabe“, „konfiguration“, „manuell“; leer = alte Zeile
    bestaetigt_von = db.Column(db.String)
    bestaetigt_am = db.Column(db.DateTime)

    @property
    def zu_bestaetigen(self):
        return not self.bestaetigt_von


class Importlauf(db.Model):
    __tablename__ = "importlauf"

    id = db.Column(db.Integer, primary_key=True)
    quelle = db.Column(db.String, nullable=False)
    gestartet = db.Column(db.DateTime, nullable=False, default=jetzt)
    beendet = db.Column(db.DateTime)
    zahlen = db.Column(db.JSON, nullable=False, default=dict)
    hinweise = db.Column(db.JSON, nullable=False, default=list)


class Aenderung(db.Model):
    """Protokoll jeder Änderung von Hand: wer, wann, was, alt -> neu."""
    __tablename__ = "aenderung"

    id = db.Column(db.Integer, primary_key=True)
    objekt = db.Column(db.String, nullable=False)  # teil, dokument, verknuepfung
    objekt_id = db.Column(db.Integer, nullable=False, index=True)
    feld = db.Column(db.String, nullable=False)
    alt = db.Column(db.JSON)
    neu = db.Column(db.JSON)
    von = db.Column(db.String, nullable=False)
    am = db.Column(db.DateTime, nullable=False, default=jetzt)
    grund = db.Column(db.String)
