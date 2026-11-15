"""Stammdaten nach der Vorgabe „ATEX-Datenbank: Tabellen und Spalten“ (Spec 001 v2).

Die Spaltennamen der Vorgabe stehen in docs/datenmodell.md neben den Namen hier.
"""

from ..db import db

# Prüfstatus: nur ein Mensch setzt „geprueft“ (M3)
STATUS = ("ungeprueft", "ki_geprueft", "geprueft")
SOURCES = ("legacy_excel", "eplan_addin", "data_portal", "manual")
DOC_TYPES = ("eu_baumusterpruefbescheinigung", "eu_konformitaetserklaerung",
             "uk_baumusterpruefbescheinigung", "uk_konformitaetserklaerung",
             "bedienungsanleitung", "datenblatt", "sonstiges")
LANGUAGES = ("de", "en", "fr")


class Manufacturer(db.Model):
    __tablename__ = "manufacturer"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)  # wie zuerst gesehen
    # ohne Leer- und Satzzeichen, für Vergleiche
    normalized_name = db.Column(db.String, nullable=False, unique=True)


class Part(db.Model):
    __tablename__ = "part"

    id = db.Column(db.Integer, primary_key=True)
    profile_no = db.Column(db.String, nullable=False, unique=True)  # PRO.FILE-Nummer
    name_de = db.Column(db.String)
    name_en = db.Column(db.String)
    manufacturer_id = db.Column(db.ForeignKey("manufacturer.id"))
    manufacturer_article_no = db.Column(db.String)
    ex_marking_raw = db.Column(db.String)  # z. B. „II 2G Ex ia IIC T4 Gb“, wie geschrieben
    manufacturer_url = db.Column(db.String)
    legacy_last_modified = db.Column(db.Date)
    status = db.Column(db.String, nullable=False, default="ungeprueft")
    review_needed = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String, nullable=False, default="manual")
    changed_by = db.Column(db.String, nullable=False)

    manufacturer = db.relationship(Manufacturer)
    documents = db.relationship("PartDocument", back_populates="part")


class Document(db.Model):
    __tablename__ = "document"

    id = db.Column(db.Integer, primary_key=True)
    doc_type = db.Column(db.String, nullable=False)
    file_path = db.Column(db.String)  # eins von beiden: Dateipfad oder URL
    url = db.Column(db.String)
    file_hash = db.Column(db.String, unique=True)  # SHA-256: gleiche Datei = gleiches Dokument
    file_exists = db.Column(db.Boolean, nullable=False, default=True)
    checked_at = db.Column(db.DateTime)
    certificate_no = db.Column(db.String)
    valid_until = db.Column(db.Date)
    changed_by = db.Column(db.String, nullable=False)

    languages = db.relationship("DocumentLanguage", cascade="all, delete-orphan")


class DocumentLanguage(db.Model):
    __tablename__ = "document_language"

    document_id = db.Column(db.ForeignKey("document.id"), primary_key=True)
    language = db.Column(db.String, primary_key=True)


class PartDocument(db.Model):
    """Welches Dokument zu welchem Bauteil gehört (n:m), mit Prüfstatus."""
    __tablename__ = "part_document"

    part_id = db.Column(db.ForeignKey("part.id"), primary_key=True)
    document_id = db.Column(db.ForeignKey("document.id"), primary_key=True)
    source = db.Column(db.String, nullable=False, default="manual")
    status = db.Column(db.String, nullable=False, default="ungeprueft")
    verified_by = db.Column(db.String)
    verified_at = db.Column(db.DateTime)
    changed_by = db.Column(db.String, nullable=False)

    part = db.relationship(Part, back_populates="documents")
    document = db.relationship(Document)


# Änderungen von Hand (M18); unten angehängt, damit der Teil nach der Vorgabe oben unverändert bleibt
from datetime import datetime, timezone  # noqa: E402


class StammdatenAenderung(db.Model):
    """Spur jeder Änderung an den Stammdaten von Hand: wer, was, wann, alter und neuer Wert.

    Eigene Tabelle des Teams, nicht Teil der Vorgabe. part_id und document_id sind bewusst keine
    Fremdschlüssel: die Spur bleibt, auch wenn eine Zuordnung gelöst oder ein Dokument gelöscht ist.
    """
    __tablename__ = "stammdaten_aenderung"

    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String, nullable=False)  # part | document | part_document | manufacturer
    row_key = db.Column(db.String, nullable=False)     # id, bei part_document „part_id/document_id“
    part_id = db.Column(db.Integer, index=True)
    document_id = db.Column(db.Integer, index=True)
    field = db.Column(db.String, nullable=False)  # Spalte, oder „angelegt“, „zuordnung“, „geloescht“
    old_value = db.Column(db.String)
    new_value = db.Column(db.String)
    changed_by = db.Column(db.String, nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    note = db.Column(db.String)

    FIELDS = ("id", "table_name", "row_key", "part_id", "document_id", "field", "old_value",
              "new_value", "changed_by", "note")

    def to_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS} | {
            "changed_at": self.changed_at.isoformat()}
