from datetime import datetime

from app.db import db
from app.models import (Bom, BomItem, Document, DocumentLanguage, EmailDraft, Finding, Part,
                        PartDocument)
from app.services import status


def item(**felder):
    werte = dict(row_number=1, pos="10", profile_no="99000010", manufacturer="Festo",
                 manufacturer_article_no="VACF-1", location="Zone 1")
    werte.update(felder)
    i = BomItem(**werte)
    db.session.add(Bom(source_file_name="x.xlsx", source_file_hash="h", items=[i]))
    db.session.flush()
    return i


def lege_dokument_an(profile_no, doc_type, pd_status, sprachen=("de",)):
    part = db.session.scalar(db.select(Part).filter_by(profile_no=profile_no)) \
        or Part(profile_no=profile_no, changed_by="test")
    doc = Document(doc_type=doc_type, file_path="Festo/x.pdf", changed_by="test",
                   languages=[DocumentLanguage(language=s) for s in sprachen])
    db.session.add(PartDocument(part=part, document=doc, status=pd_status, changed_by="test"))
    db.session.flush()


def test_stand_folgt_den_daten(client):
    i = item()
    assert status.aktualisiere(i) == "offen"

    i.searched_at = datetime(2026, 11, 13)
    assert status.aktualisiere(i) == "nicht_gefunden"

    i.email_drafts.append(EmailDraft(to_address="t@example.com", subject="s", body="b",
                                     status="gesendet"))
    assert status.aktualisiere(i) == "angefordert"

    i.findings.append(Finding(source="ordner", doc_type="eu_konformitaetserklaerung"))
    db.session.flush()
    assert status.aktualisiere(i) == "vorschlag"


def test_eskaliert_bis_ein_dokument_kommt(client):
    i = item(searched_at=datetime(2026, 11, 13))
    i.email_drafts.append(EmailDraft(to_address="t@example.com", subject="s", body="b",
                                     status="gesendet", escalated_by="Eva",
                                     escalated_at=datetime(2026, 11, 16, 9, 0)))
    assert status.aktualisiere(i) == "eskaliert"
    assert i.status_note == "Eva hat die Anfrage am 16.11.2026 an den Einkauf eskaliert."
    assert status.offene_positionen(i.bom) == [i]  # hält die Kundenliste auf

    i.findings.append(Finding(source="hersteller", doc_type="eu_konformitaetserklaerung"))
    db.session.flush()
    assert (status.aktualisiere(i), i.status_note) == ("vorschlag", None)
    lege_dokument_an("99000010", "eu_konformitaetserklaerung", "geprueft")
    assert status.aktualisiere(i) == "vorhanden"


def test_vorhanden_nur_mit_gepruefter_konformitaetserklaerung(client):
    i = item()
    lege_dokument_an("99000010", "eu_konformitaetserklaerung", "ki_geprueft")
    assert status.aktualisiere(i) == "offen"
    assert status.fehlende_arten(i) == ["eu_konformitaetserklaerung"]

    i.bom.items.append(BomItem(row_number=2, pos="20", profile_no="99000020",
                               manufacturer_article_no="X", location="Zone 2"))
    lege_dokument_an("99000020", "bedienungsanleitung", "geprueft")
    lege_dokument_an("99000030", "eu_konformitaetserklaerung", "geprueft")
    j = item(profile_no="99000030", location="")
    status.aktualisiere_alle(i.bom)
    assert [x.status for x in i.bom.items] == ["offen", "offen"]
    assert status.aktualisiere(j) == "vorhanden"  # geprüft in der Datenbank schlägt fehlenden Einbauort
    assert status.offene_positionen(j.bom) == []


def test_klaerung_und_menschliche_entscheidung(client):
    assert status.aktualisiere(item(location="")) == "klaerung"
    assert status.aktualisiere(item(profile_no="")) == "klaerung"
    i = item(manufacturer_article_no="", status="kein_ex_teil")
    assert status.aktualisiere(i) == "kein_ex_teil"
    assert status.offene_positionen(i.bom) == []


def test_ex_teil_nach_der_ex_bauteil_liste(client):
    """Unsicherer Einbauort: klaerung, bis ein Mensch bestätigt (E1)."""
    i = item(location="Halle 3")
    assert status.aktualisiere(i) == "klaerung"
    assert "Halle 3" in i.status_note
    i.ex_confirmed_by, i.ex_confirmed_at = "Eva", datetime(2026, 11, 13)
    assert (status.aktualisiere(i), i.status_note) == ("offen", None)
    j = item(location="", ex_confirmed_by="Eva", ex_confirmed_at=datetime(2026, 11, 13))
    assert status.aktualisiere(j) == "offen"  # bestätigt: der fehlende Einbauort hält nicht auf

    # außerhalb, aber zugehöriges Betriebsmittel (Pos. 140): Ex-Teil, ohne offene Entscheidung
    k = item(location="Schaltschrank außerhalb Ex", manufacturer_article_no="KCD2-SR-Ex1.LB",
             searched_at=datetime(2026, 11, 13))
    assert (status.aktualisiere(k), k.status_note) == ("nicht_gefunden", None)
    # außerhalb mit nur vagem Hinweis: wird gesucht, die Entscheidung steht im Hinweis
    v = item(location="Schaltschrank außerhalb Ex", manufacturer_article_no="PS-24 ATEX",
             searched_at=datetime(2026, 11, 13))
    assert status.aktualisiere(v) == "nicht_gefunden"
    assert "Bitte entscheiden Sie" in v.status_note
    assert status.aktualisiere(item(location="außerhalb Ex", manufacturer_article_no="PS-24")) \
        == "klaerung"


def test_teilweise_vorhanden_nennt_was_fehlt(client):
    # Baumusterprüfbescheinigung freigegeben, Konformitätserklärung fehlt: nicht stumm „nicht gefunden“
    i = item(searched_at=datetime(2026, 11, 13))
    lege_dokument_an("99000010", "eu_baumusterpruefbescheinigung", "geprueft")
    assert status.aktualisiere(i) == "nicht_gefunden"
    assert i.status_note == ("Geprüft liegt schon die EU-Baumusterprüfbescheinigung vor. "
                             "Es fehlt noch die EU-Konformitätserklärung.")
