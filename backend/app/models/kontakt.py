"""E-Mail-Kontakt je Hersteller (eigene Tabelle des Teams, nicht Teil der Vorgabe).

Ein Kontakt je Hersteller aus der Tabelle manufacturer, deren Spalten unverändert bleiben. Fehlt
die Adresse, wird jede Anfrage an den Hersteller markiert und nicht gesendet
(services/kontakte.py). Jede Änderung steht im Änderungsprotokoll stammdaten_aenderung
(table_name „hersteller_kontakt“, row_key = manufacturer_id).
"""

from datetime import datetime, timezone

from ..db import db
from .stammdaten import Manufacturer

SPRACHEN = ("de", "en")  # Sprache der Anfrage an den Hersteller


class HerstellerKontakt(db.Model):
    __tablename__ = "hersteller_kontakt"

    manufacturer_id = db.Column(db.ForeignKey("manufacturer.id"), primary_key=True)
    email = db.Column(db.String)           # leer: Adresse fehlt, Anfragen werden markiert
    contact_person = db.Column(db.String)  # Ansprechpartner, optional
    language = db.Column(db.String)        # Sprache der Anfrage: de | en, optional
    note = db.Column(db.String)
    changed_by = db.Column(db.String, nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))

    manufacturer = db.relationship(Manufacturer)
