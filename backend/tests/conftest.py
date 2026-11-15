import io

import pytest
from openpyxl import Workbook

from app import create_app
from app.config import Config
from app.db import db

# Fachtests ohne Anmeldung (gilt auch für eigene create_app-Aufrufe); tests/test_auth.py schaltet
# sie je App wieder ein
Config.LOGIN_PFLICHT = False
# Die Fachtests prüfen die Freigabe durch einen Menschen; die automatische Freigabe lokaler Funde
# schaltet tests/test_automatisch.py je App ein
Config.AUTOMATISCH_FREIGEBEN = False
# Ebenso aus im Betrieb, an in den Fachtests: EX-Bauteil-Liste und Markierungen „veraltet“
Config.EX_LISTE = True
Config.VERALTET_PRUEFUNG = True

KOPF = ["Pos.", "PRO.FILE-Nr. (erfunden)", "Bezeichnung", "Hersteller",
        "Hersteller-Artikelnr.", "Menge", "Einbauort"]


def xlsx(zeilen, kopf=KOPF):
    """Baut eine Stückliste im Aufbau, den data/README.md beschreibt."""
    wb = Workbook()
    ws = wb.active
    ws.append(["Test-Stückliste"])
    ws.append([])
    ws.append(["Maschine", "Abfüllanlage TM-1"])
    ws.append(["Ex-Bereich", "Innenraum Zone 1"])
    ws.append(["Geforderte Kategorie", "Zone 1: mindestens 2G"])
    ws.append(kopf)
    for z in zeilen:
        ws.append(z)
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


@pytest.fixture
def client():
    # ohne Internet: Tests, die es brauchen, setzen die Versuche und ersetzen HTTP
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True,
                      "INTERNET_VERSUCHE_PRO_TEIL": 0})
    with app.app_context():
        db.create_all()
        yield app.test_client()


def kontakt(hersteller, email, sprache=None):
    """Herstellerkontakt direkt in der Datenbank: ohne Adresse wird keine Anfrage gesendet."""
    from app.models import HerstellerKontakt, Manufacturer
    from app.services.freigabe import normalisiere
    m = db.session.scalar(db.select(Manufacturer).filter_by(
        normalized_name=normalisiere(hersteller)))
    if m is None:
        m = Manufacturer(name=hersteller, normalized_name=normalisiere(hersteller))
        db.session.add(m)
        db.session.flush()
    db.session.merge(HerstellerKontakt(manufacturer_id=m.id, email=email, language=sprache,
                                       changed_by="test"))
    db.session.commit()
    return m
