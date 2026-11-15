"""Antwortzeiten: Die Ansichten, die die Oberfläche laufend abfragt, brauchen eine feste Zahl von
SQL-Anfragen, egal wie viele Vorgänge und Positionen es gibt, und schreiben nichts
(services/lesepuffer.py). Gezählt wird mit dem SQLAlchemy-Ereignis before_cursor_execute; die
Zeit wird bewusst nicht gemessen (hängt vom Rechner ab).

Vorher brauchte GET /api/vorgaenge bei 9 Vorgängen mit je 30 Positionen 3682 Anfragen, davon
126 schreibende. Wächst die Zahl hier wieder mit den Daten, ist ein N+1 zurück.
"""

import io
from contextlib import contextmanager

import pytest
from conftest import kontakt, xlsx
from sqlalchemy import event
from test_oberflaeche import ZEILEN, artikel, gestartet, hochladen, ordner  # noqa: F401

from app.db import db
from app.models import (BomItem, Document, DocumentLanguage, EmailDraft, Finding, Manufacturer,
                        Part, PartDocument)
from app.services.freigabe import normalisiere

HERSTELLER = ("Festo", "Rittal", "Bosch", "Pepperl+Fuchs")
SPIELRAUM = 2  # so viele Anfragen mehr dürfen es bei mehr Daten sein


@contextmanager
def gezaehlt():
    anfragen = []

    def merken(conn, cursor, statement, *args):
        anfragen.append(statement.split(None, 1)[0].upper())

    event.listen(db.engine, "before_cursor_execute", merken)
    try:
        yield anfragen
    finally:
        event.remove(db.engine, "before_cursor_execute", merken)


def zahl(client, pfad):
    """(Anfragen, schreibende Anfragen) für einen GET, nach einem Abruf zum Aufwärmen."""
    assert client.get(pfad).status_code == 200, pfad
    with gezaehlt() as anfragen:
        r = client.get(pfad)
    assert r.status_code == 200, (pfad, r.get_json())
    return len(anfragen), sum(a in ("INSERT", "UPDATE", "DELETE") for a in anfragen)


def vorgang_mit_daten(client, n, start):
    """Vorgang mit n Positionen; je Position ein Fund, jede dritte mit E-Mail-Entwurf, jede
    zweite mit geprüfter Konformitätserklärung (jede vierte nur englisch) in den Stammdaten."""
    zeilen = [[10 * i, 99000000 + start + i, "Magnetspule", HERSTELLER[i % len(HERSTELLER)],
               None if i % 5 == 4 else f"T-{start + i}", 1, "Zone 1"] for i in range(n)]
    r = client.post("/api/vorgaenge", data={"datei": (io.BytesIO(xlsx(zeilen)), "liste.xlsx")})
    vid = r.get_json()["id"]
    for k, item in enumerate(db.session.scalars(db.select(BomItem).filter_by(bom_id=vid))):
        db.session.add(Finding(
            bom_item_id=item.id, source="ordner", doc_type="eu_baumusterpruefbescheinigung",
            file_path=f"Festo/x{item.id}.pdf", file_hash=f"f{item.id}", languages="de",
            ex_marking_raw="II 2G Ex mb IIC T5 Gb", status="vorgeschlagen", check_result="ok",
            belege=[{"wert": "typ", "angabe": item.manufacturer_article_no, "belegt": True,
                     "seite": 1}], angaben={}))
        if k % 3 == 0:
            db.session.add(EmailDraft(bom_item_id=item.id, to_address="test@example.com",
                                      subject="Anfrage", body="Bitte senden Sie …"))
        if k % 2 == 0 and item.profile_no:
            m = db.session.scalar(db.select(Manufacturer).filter_by(
                normalized_name=normalisiere(item.manufacturer)))
            if m is None:
                m = Manufacturer(name=item.manufacturer,
                                 normalized_name=normalisiere(item.manufacturer))
                db.session.add(m)
            part = Part(profile_no=item.profile_no, manufacturer=m, changed_by="test")
            doc = Document(doc_type="eu_konformitaetserklaerung", file_path=f"d{item.id}.pdf",
                           file_hash=f"d{item.id}", changed_by="test",
                           languages=[DocumentLanguage(language="en" if k % 4 == 0 else "de")])
            db.session.add(PartDocument(part=part, document=doc, status="geprueft",
                                        verified_by="test", changed_by="test"))
    db.session.commit()
    return vid


@pytest.fixture
def daten(client):
    kontakt("Festo", "info@festo.example")  # Rittal und Bosch ohne Adresse
    return client


def ansichten(client, vid, item_id):
    """{Ansicht: (Anfragen, schreibende)} für die Listen und die Ansichten eines Vorgangs."""
    pfade = ["/api/vorgaenge", "/api/boms", "/api/kontakte", "/api/vorgaenge/<id>",
             "/api/boms/<id>/ex-liste", "/api/vorgaenge/<id>/mails", "/api/boms/<id>",
             "/api/boms/<id>/sprachen", "/api/boms/<id>/korrektur", "/api/artikel/<pos>"]
    return {p: zahl(client, p.replace("<id>", str(vid)).replace("<pos>", str(item_id)))
            for p in pfade}


def erste_position(vid):
    return db.session.scalars(db.select(BomItem.id).filter_by(bom_id=vid)
                              .order_by(BomItem.row_number)).first()


def test_ansichten_mit_fester_zahl_von_anfragen(daten):
    client = daten
    klein = vorgang_mit_daten(client, 4, 0)
    vorher = ansichten(client, klein, erste_position(klein))

    for k in range(3):
        gross = vorgang_mit_daten(client, 16, 100 * (k + 1))
    nachher = ansichten(client, gross, erste_position(gross))

    for pfad, (anzahl, schreibend) in nachher.items():
        assert schreibend == 0, f"{pfad} schreibt beim Lesen"
        assert anzahl <= vorher[pfad][0] + SPIELRAUM, (
            f"{pfad}: {vorher[pfad][0]} Anfragen bei 1 Vorgang mit 4 Positionen, {anzahl} bei "
            f"4 Vorgängen mit bis zu 16 Positionen")
        assert anzahl <= 20, f"{pfad}: {anzahl} Anfragen"


def test_ansicht_rechnet_den_stand_frisch_und_speichert_ihn_nicht(daten):
    """Die Freigabe ändert den Stand anderer Vorgänge über die Stammdaten; die Ansicht zeigt ihn
    frisch, auch wenn der gespeicherte Stand veraltet ist, und lässt die Datenbank, wie sie ist."""
    client = daten
    vid = vorgang_mit_daten(client, 4, 0)
    item = db.session.get(BomItem, erste_position(vid))
    db.session.execute(db.update(BomItem).where(BomItem.id == item.id).values(status="offen"))
    db.session.commit()
    a = next(a for a in client.get(f"/api/vorgaenge/{vid}").get_json()["artikel"]
             if a["id"] == item.id)
    assert a["status"] == "zu_pruefen"  # frisch abgeleitet: ein Fund wartet
    assert client.get(f"/api/artikel/{item.id}").get_json()["status"] == "zu_pruefen"
    db.session.expire_all()
    assert db.session.get(BomItem, item.id).status == "offen"  # nichts gespeichert


def test_freigabe_speichert_den_stand_auch_in_anderen_vorgaengen(client, ordner):  # noqa: F811
    """Die Ansichten speichern nicht mehr; deshalb leitet die Freigabe den gespeicherten Stand
    derselben PRO.FILE-Nr. auch in anderen Vorgängen neu ab."""
    vid = hochladen(client)
    anderer = hochladen(client, ZEILEN[:1], name="Anderer")
    gestartet(client, vid)
    a = artikel(client, vid, 10)
    assert client.post(f"/api/artikel/{a['id']}/freigeben",
                       json={"fund_id": a["funde"][0]["id"], "von": "Jan"}).status_code == 200
    db.session.expire_all()
    dort = db.session.scalar(db.select(BomItem).filter_by(bom_id=anderer))
    assert dort.status == "vorhanden"  # ohne dass jemand den anderen Vorgang angesehen hat
