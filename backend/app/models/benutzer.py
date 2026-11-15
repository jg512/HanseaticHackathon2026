"""Benutzerkonten für die Anmeldung (C1): Rolle, aktiv, Sperre nach Fehlversuchen.

Das Passwort liegt nur als Hash vor (werkzeug.security) und verlässt nie das Backend. Konten
werden nicht gelöscht, nur deaktiviert: ihre Namen stehen im Protokoll.
"""

from datetime import datetime, timezone

from ..db import db

# Rolle -> Bezeichnung für die Oberfläche
ROLLEN = {"admin": "Administration", "pruefer": "Prüfung & Freigabe", "leser": "Nur lesen"}


def _jetzt():
    return datetime.now(timezone.utc)


def _iso(zeit):
    return zeit.isoformat() if zeit else None


class Benutzer(db.Model):
    __tablename__ = "benutzer"

    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(40), nullable=False, unique=True)  # immer klein geschrieben
    name = db.Column(db.String(200), nullable=False)  # Anzeigename, steht im Protokoll
    passwort_hash = db.Column(db.String, nullable=False)
    rolle = db.Column(db.String(20), nullable=False, default="pruefer")  # einer aus ROLLEN
    aktiv = db.Column(db.Boolean, nullable=False, default=True)
    angelegt_am = db.Column(db.DateTime, nullable=False, default=_jetzt)
    angelegt_von = db.Column(db.String(200))
    letzte_anmeldung = db.Column(db.DateTime)
    fehlversuche = db.Column(db.Integer, nullable=False, default=0)
    gesperrt_bis = db.Column(db.DateTime)
    # Steigt bei neuem Passwort und Deaktivierung: ältere Sitzungen gelten dann nicht mehr
    sitzungs_stand = db.Column(db.Integer, nullable=False, default=0)

    def to_dict(self):
        """Ohne Passwort-Hash."""
        return {"id": self.id, "login": self.login, "name": self.name, "rolle": self.rolle,
                "rolle_text": ROLLEN.get(self.rolle, self.rolle), "aktiv": self.aktiv,
                "angelegt_am": _iso(self.angelegt_am), "angelegt_von": self.angelegt_von,
                "letzte_anmeldung": _iso(self.letzte_anmeldung),
                "gesperrt": bool(self.gesperrt_bis and _utc(self.gesperrt_bis) > _jetzt())}


def _utc(zeit):
    """SQLite liefert Zeiten ohne Zone zurück; gespeichert wird immer UTC."""
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)
