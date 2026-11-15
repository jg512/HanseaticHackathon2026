"""Ein Vorgang: eine hochgeladene Stückliste mit ihren Positionen und Meldungen."""

from datetime import datetime, timezone

from ..db import db


# Stand einer Position (M14)
ITEM_STATUS = (
    "offen",           # noch nicht gesucht
    "klaerung",        # Angaben fehlen oder unklar: ein Mensch muss entscheiden
    "vorschlag",       # Dokument gefunden, wartet auf Freigabe
    "nicht_gefunden",  # gesucht, nichts gefunden
    "angefordert",     # E-Mail an den Hersteller ist gesendet
    "eskaliert",       # Hersteller antwortet nicht: an den Einkauf eskaliert, bleibt offen
    "vorhanden",       # Pflichtdokumente liegen geprüft in der Datenbank
    "kein_ex_teil",    # Entscheidung eines Menschen: braucht keine Dokumente
    "erledigt",        # von Hand erledigt: ein Mensch nimmt die Lücke mit Begründung hin
)
FINDING_STATUS = ("vorgeschlagen", "freigegeben", "abgelehnt")
EMAIL_STATUS = ("entwurf", "freigegeben", "gesendet")
EMAIL_ART = ("anfrage", "erinnerung")


class Bom(db.Model):
    __tablename__ = "bom"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String)               # Name des Vorgangs aus dem Upload, sonst die Maschine
    # gesetzt, solange die Suche der Oberfläche läuft; nach einem Neustart setzt sie dort fort
    search_started_at = db.Column(db.DateTime)
    # Sprachen, die dieser Vorgang braucht („de,en“), beim Start des Agenten gewählt; leer: SPRACHEN
    sprachen = db.Column(db.String)
    machine = db.Column(db.String)
    ex_area = db.Column(db.String)            # Zeile „Ex-Bereich“, wie geschrieben
    required_category = db.Column(db.String)  # Zeile „Geforderte Kategorie“, wie geschrieben
    source_file_name = db.Column(db.String, nullable=False)
    source_file_hash = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))

    items = db.relationship("BomItem", back_populates="bom", order_by="BomItem.row_number",
                            cascade="all, delete-orphan")
    issues = db.relationship("BomIssue", order_by="BomIssue.row_number",
                             cascade="all, delete-orphan")
    questions = db.relationship("Question", back_populates="bom", order_by="Question.id",
                                cascade="all, delete-orphan")

    def to_dict(self, detail=False):
        d = {"id": self.id, "machine": self.machine, "ex_area": self.ex_area,
             "required_category": self.required_category,
             "source_file_name": self.source_file_name,
             "created_at": self.created_at.isoformat(),
             "item_count": len(self.items), "issue_count": len(self.issues),
             "open_issue_count": sum(not i.resolved_at for i in self.issues),
             "open_question_count": sum(q.status == "offen" for q in self.questions)}
        if detail:
            d["items"] = [i.to_dict() for i in self.items]
            d["issues"] = [i.to_dict() for i in self.issues]
        return d

    # Einstellungen des Vorgangs (services/modelle.py), leer = Standard der Installation:
    # Name eines Chat-Modells aus config/modelle.yaml und Web-Anfragen je Position (0 = keine)
    llm_modell = db.Column(db.String)
    internet_versuche = db.Column(db.Integer)


class BomItem(db.Model):
    __tablename__ = "bom_item"

    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.ForeignKey("bom.id"), nullable=False)
    row_number = db.Column(db.Integer, nullable=False)  # Zeile in der Excel-Datei
    pos = db.Column(db.String)
    profile_no = db.Column(db.String)
    description = db.Column(db.String)
    manufacturer = db.Column(db.String)
    manufacturer_article_no = db.Column(db.String)
    quantity = db.Column(db.String)
    location = db.Column(db.String)
    # Stand der Dokumente (M14), einer aus ITEM_STATUS. Gesetzt nur über services/status.py.
    status = db.Column(db.String, nullable=False, default="offen")
    status_note = db.Column(db.String)   # Grund, z. B. warum ein Mensch klären muss
    searched_at = db.Column(db.DateTime)
    searched_in = db.Column(db.String)   # z. B. „Datenbank, Ordner“ für den Bericht „gesucht in …“
    # Von Hand erledigt: ein Mensch nimmt die fehlenden Dokumente mit Begründung hin.
    # status.py leitet daraus den Stand „erledigt“ ab; Zurücknehmen leert die drei Felder.
    resolved_by = db.Column(db.String)
    resolved_at = db.Column(db.DateTime)
    resolution_note = db.Column(db.String)

    bom = db.relationship(Bom, back_populates="items")
    findings = db.relationship("Finding", back_populates="item", order_by="Finding.id",
                               cascade="all, delete-orphan")
    email_drafts = db.relationship("EmailDraft", back_populates="item", order_by="EmailDraft.id",
                                   cascade="all, delete-orphan")

    FIELDS = ("pos", "profile_no", "description", "manufacturer", "manufacturer_article_no",
              "quantity", "location", "status", "status_note", "searched_in",
              "resolved_by", "resolution_note")

    def to_dict(self):
        d = {f: getattr(self, f) for f in self.FIELDS}
        d.update(id=self.id, row_number=self.row_number,
                 resolved_at=self.resolved_at.isoformat() if self.resolved_at else None)
        return d

    # Ein Mensch bestätigt: die Position ist ein Ex-Teil, obwohl die Regeln es nicht sicher sagen
    # (services/ex_liste.py). Das Gegenteil ist der Stand „kein_ex_teil“.
    ex_confirmed_by = db.Column(db.String)
    ex_confirmed_at = db.Column(db.DateTime)


class BomIssue(db.Model):
    """Meldung aus der Eingangsprüfung, z. B. fehlende Artikelnummer (M1)."""
    __tablename__ = "bom_issue"

    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.ForeignKey("bom.id"), nullable=False)
    row_number = db.Column(db.Integer)
    pos = db.Column(db.String)
    code = db.Column(db.String, nullable=False)
    severity = db.Column(db.String, nullable=False)  # error | warning | info
    message = db.Column(db.String, nullable=False)
    # Abgehakt von einem Menschen, mit optionalem Kommentar. Ohne Einfluss auf Stand und Ausgabe.
    resolved_by = db.Column(db.String)
    resolved_at = db.Column(db.DateTime)
    comment = db.Column(db.String)

    def to_dict(self):
        return {"id": self.id, "row_number": self.row_number, "pos": self.pos, "code": self.code,
                "severity": self.severity, "message": self.message,
                "resolved_by": self.resolved_by,
                "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
                "comment": self.comment}


class Finding(db.Model):
    """Vorschlag: ein für eine Position gefundenes Dokument, bis ein Mensch entscheidet (M6, M19).

    Erst die Freigabe überträgt es in die Stammdaten (Document, PartDocument).
    """
    __tablename__ = "finding"

    id = db.Column(db.Integer, primary_key=True)
    bom_item_id = db.Column(db.ForeignKey("bom_item.id"), nullable=False)
    # ordner | suchdienst | internet | manuell (von Hand eingefügt) | hersteller (Antwort)
    source = db.Column(db.String, nullable=False)
    doc_type = db.Column(db.String, nullable=False)  # einer aus stammdaten.DOC_TYPES
    file_path = db.Column(db.String)                 # relativ zu ATEX_DOCS, z. B. „Festo/x.pdf“
    url = db.Column(db.String)
    file_hash = db.Column(db.String)                 # SHA-256 der Datei
    languages = db.Column(db.String)                 # z. B. „de,en“
    # Beleg (Regel: jede Angabe mit Datei und Seite)
    page = db.Column(db.Integer)                     # Seite der Fundstelle, ab 1
    matched_term = db.Column(db.String)              # der gefundene Bezeichner
    snippet = db.Column(db.String)                   # Text um die Fundstelle
    # aus dem Dokument gelesen, wie geschrieben
    ex_marking_raw = db.Column(db.String)
    certificate_no = db.Column(db.String)
    # Ergebnis der Prüfregeln (services/pruefung.py)
    check_result = db.Column(db.String)              # ok | warnung | unklar
    check_message = db.Column(db.String)
    status = db.Column(db.String, nullable=False, default="vorgeschlagen")
    decided_by = db.Column(db.String)
    decided_at = db.Column(db.DateTime)

    item = db.relationship(BomItem, back_populates="findings")

    FIELDS = ("id", "bom_item_id", "source", "doc_type", "file_path", "url", "file_hash",
              "languages", "page", "matched_term", "snippet", "ex_marking_raw",
              "certificate_no", "check_result", "check_message", "status", "decided_by")

    def to_dict(self):
        d = {f: getattr(self, f) for f in self.FIELDS}
        d["decided_at"] = self.decided_at.isoformat() if self.decided_at else None
        return d

    # Je Angabe des Vorschlags, ob sie im Dokument steht (services/belege.py): Liste aus
    # {wert, angabe, belegt, seite, tabelle, zeile, hinweis}. None: noch nicht geprüft.
    belege = db.Column(db.JSON)
    FIELDS += ("belege",)

    # Weitere Angaben aus dem Dokument, je mit Seite und Textstelle (services/angaben.py):
    # Gültigkeit, Ausstellung, Richtlinien, Normen, Stellen, Hersteller … None: noch nicht gelesen.
    angaben = db.Column(db.JSON)
    FIELDS += ("angaben",)

    # Freigegeben vom Agenten selbst, ohne Klick eines Menschen (services/freigabe.automatisch)
    auto_approved = db.Column(db.Boolean, default=False)
    FIELDS += ("auto_approved",)


class EmailDraft(db.Model):
    """E-Mail an einen Hersteller für eine Position ohne Dokument (M16, M17)."""
    __tablename__ = "email_draft"

    id = db.Column(db.Integer, primary_key=True)
    bom_item_id = db.Column(db.ForeignKey("bom_item.id"), nullable=False)
    to_address = db.Column(db.String, nullable=False)  # im Hackathon immer MAIL_TEST_ADDRESS
    subject = db.Column(db.String, nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String, nullable=False, default="entwurf")
    approved_by = db.Column(db.String)
    sent_at = db.Column(db.DateTime)
    art = db.Column(db.String, nullable=False, default="anfrage")  # einer aus EMAIL_ART
    original_id = db.Column(db.ForeignKey("email_draft.id"))   # Erinnerung: die erste Anfrage
    # Eskalation an den Einkauf (E8), von einem Menschen ausgelöst; status.py liest das
    escalated_by = db.Column(db.String)
    escalated_at = db.Column(db.DateTime)

    item = db.relationship(BomItem, back_populates="email_drafts")
    original = db.relationship("EmailDraft", remote_side=[id])

    FIELDS = ("id", "bom_item_id", "art", "original_id", "to_address", "subject", "body",
              "status", "approved_by", "escalated_by")

    def to_dict(self):
        d = {f: getattr(self, f) for f in self.FIELDS}
        for f in ("sent_at", "escalated_at"):
            d[f] = getattr(self, f).isoformat() if getattr(self, f) else None
        return d


class ItemCorrection(db.Model):
    """Korrektur einer Angabe der Stückliste durch einen Menschen (E5): wer, was, wann."""
    __tablename__ = "item_correction"

    id = db.Column(db.Integer, primary_key=True)
    bom_item_id = db.Column(db.ForeignKey("bom_item.id"), nullable=False)
    field = db.Column(db.String, nullable=False)  # einer aus services/korrektur.FELDER
    old_value = db.Column(db.String)
    new_value = db.Column(db.String)
    changed_by = db.Column(db.String, nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    note = db.Column(db.String)                   # z. B. „laut Einkauf“ oder der Dateiname

    item = db.relationship(BomItem, backref=db.backref(
        "corrections", order_by="ItemCorrection.id", cascade="all, delete-orphan"))

    def to_dict(self):
        return {"id": self.id, "bom_item_id": self.bom_item_id, "pos": self.item.pos,
                "field": self.field, "old_value": self.old_value, "new_value": self.new_value,
                "changed_by": self.changed_by, "changed_at": self.changed_at.isoformat(),
                "note": self.note}


class PurchasingEmail(db.Model):
    """Mängelliste an den Einkauf (E5): fehlende Angaben eines Vorgangs, ein Mensch gibt frei."""
    __tablename__ = "purchasing_email"

    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.ForeignKey("bom.id"), nullable=False)
    to_address = db.Column(db.String, nullable=False)  # EINKAUF_ADRESSE
    subject = db.Column(db.String, nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String, nullable=False, default="entwurf")  # einer aus EMAIL_STATUS
    approved_by = db.Column(db.String)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    sent_at = db.Column(db.DateTime)

    bom = db.relationship(Bom, backref=db.backref(
        "purchasing_emails", order_by="PurchasingEmail.id", cascade="all, delete-orphan"))

    FIELDS = ("id", "bom_id", "to_address", "subject", "body", "status", "approved_by")

    def to_dict(self):
        d = {f: getattr(self, f) for f in self.FIELDS}
        for f in ("created_at", "sent_at"):
            d[f] = getattr(self, f).isoformat() if getattr(self, f) else None
        return d


class BomEvent(db.Model):
    """Protokoll eines Vorgangs für die Oberfläche: was eingelesen, gesucht, entschieden wurde."""
    __tablename__ = "bom_event"

    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.ForeignKey("bom.id", ondelete="CASCADE"), nullable=False, index=True)
    bom_item_id = db.Column(db.Integer)  # Position, zu der der Eintrag gehört, wenn es eine gibt
    text = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
