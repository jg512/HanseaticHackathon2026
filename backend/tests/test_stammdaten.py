import io

import pytest
from conftest import xlsx
from test_findings import pdf, vorschlag

from app.db import db
from app.models import Document, PartDocument, StammdatenAenderung

ZEILEN = [[110, 99000110, "Absolutwert-Drehgeber", "Pepperl+Fuchs", "78E-Serie, Ex-Ausführung", 1, "Zone 1"],
          [150, 99000150, "Induktiver Sensor", "Festo", "NJ50-N", 2, "Zone 1"],
          [290, 99000110, "Absolutwert-Drehgeber", "Pepperl+Fuchs", "78E-Serie, Ex-Ausführung", 1, "Zone 1"]]
S = "/api/stammdaten"


@pytest.fixture
def docs(client, tmp_path):
    ordner = tmp_path / "ex_dokumente"
    pdf(ordner / "P+F" / "78E_Konf.pdf", ["EU-Konformitaetserklaerung 78E Drehgeber"])
    pdf(ordner / "P+F" / "78E_Anleitung.pdf", ["Betriebsanleitung 78E"])
    client.application.config["ATEX_DOCS"] = str(ordner)
    return ordner


@pytest.fixture
def bom(client, docs):
    """Vorgang, in dem Pos. 110 über eine Freigabe vollständig ist: Bauteil, Dokument, geprüft."""
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")}).get_json()
    fid = vorschlag(bom["items"][0]["id"])
    r = client.post(f"/api/findings/{fid}/decision", json={"decision": "freigeben", "by": "Anna"})
    assert r.status_code == 200 and r.get_json()["item_status"] == "vorhanden"
    return bom


@pytest.fixture
def part(client, bom):
    return client.get(f"{S}/parts").get_json()[0]


def stati(client, bom):
    return [i["status"] for i in client.get(f"/api/boms/{bom['id']}").get_json()["items"]]


def zweites_dokument():
    """Ein Dokument ohne Bauteil: erst zuordnen, dann lösen."""
    doc = Document(doc_type="bedienungsanleitung", file_path="P+F/78E_Anleitung.pdf",
                   file_hash="hash-anl", changed_by="Import")
    db.session.add(doc)
    db.session.commit()
    return doc.id


def test_liste_suche_und_detail(client, part):
    assert part["profile_no"] == "99000110" and part["manufacturer"] == "Pepperl+Fuchs"
    assert (part["document_count"], part["checked_count"], part["missing_doc_types"]) == (1, 1, [])
    assert [p["profile_no"] for p in client.get(f"{S}/parts?q=drehgeber").get_json()] == ["99000110"]
    assert [p["id"] for p in client.get(f"{S}/parts?q=pepperl").get_json()] == [part["id"]]
    assert client.get(f"{S}/parts?q=%25").get_json() == []  # % ist ein Zeichen, kein Platzhalter
    assert client.get(f"{S}/parts?review_needed=1").get_json() == []
    assert len(client.get(f"{S}/parts?status=ungeprueft").get_json()) == 1
    assert client.get(f"{S}/parts?status=quatsch").status_code == 400
    m_id = part["manufacturer_id"]
    assert len(client.get(f"{S}/parts?manufacturer_id={m_id}").get_json()) == 1

    d = client.get(f"{S}/parts/{part['id']}").get_json()
    [doc] = d["documents"]
    assert doc["doc_type"] == "eu_konformitaetserklaerung" and doc["languages"] == ["de", "en"]
    assert (doc["link_status"], doc["verified_by"], doc["certificate_no"]) == \
        ("geprueft", "Anna", "ITS15ATEX18372X")
    assert [(i["pos"], i["status"]) for i in d["bom_items"]] == [("110", "vorhanden"),
                                                                  ("290", "vorhanden")]
    assert d["changes"] == []
    r = client.get(f"{S}/parts/999")
    assert r.status_code == 404 and r.get_json()["error"] == ("Dieses Bauteil gibt es nicht mehr. Bitte laden Sie die "
                                                           "Liste neu.")
    assert client.get(f"{S}/werte").get_json()["languages"] == ["de", "en", "fr"]


def test_pruefstatus_nur_mit_namen_und_stand_folgt(client, bom, part):
    pfad = f"{S}/parts/{part['id']}/documents/{part_doc(client, part)}"
    assert client.patch(pfad, json={"status": "ungeprueft"}).status_code == 400
    assert client.patch(pfad, json={"status": "fertig", "by": "Ben"}).status_code == 400
    assert client.patch(pfad, json={"status": "geprueft", "by": "Ben"}).status_code == 400  # schon

    r = client.patch(pfad, json={"status": "ungeprueft", "by": "Ben", "note": "Datum unleserlich"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["items_updated"] == 2 and d["missing_doc_types"] == ["eu_konformitaetserklaerung"]
    assert (d["documents"][0]["link_status"], d["documents"][0]["verified_by"]) == ("ungeprueft", None)
    assert stati(client, bom)[0] != "vorhanden"
    [c] = d["changes"]
    assert (c["table_name"], c["field"], c["old_value"], c["new_value"], c["changed_by"], c["note"]) \
        == ("part_document", "status", "geprueft", "ungeprueft", "Ben", "Datum unleserlich")

    d = client.patch(pfad, json={"status": "geprueft", "by": "Carla"}).get_json()
    assert (d["documents"][0]["verified_by"], d["documents"][0]["link_changed_by"]) == ("Carla", "Carla")
    assert d["documents"][0]["verified_at"]
    assert stati(client, bom) == ["vorhanden", "offen", "vorhanden"]
    assert client.patch(f"{S}/parts/{part['id']}/documents/999",
                        json={"status": "geprueft", "by": "Ben"}).status_code == 404


def part_doc(client, part):
    return client.get(f"{S}/parts/{part['id']}").get_json()["documents"][0]["id"]


def test_loesen_zuordnen_und_loeschen(client, bom, part, docs):
    doc_id = part_doc(client, part)
    pfad = f"{S}/parts/{part['id']}/documents/{doc_id}"
    assert client.delete(f"{S}/documents/{doc_id}", json={"by": "Ben"}).status_code == 409
    assert client.delete(pfad, json={}).status_code == 400

    r = client.delete(pfad, json={"by": "Ben", "note": "falsches Bauteil"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["documents"] == [] and d["items_updated"] == 2
    assert db.session.get(PartDocument, (part["id"], doc_id)) is None
    assert stati(client, bom)[0] != "vorhanden"
    [c] = d["changes"]  # die Spur bleibt
    assert (c["field"], c["new_value"], c["row_key"]) == ("zuordnung", None, f"{part['id']}/{doc_id}")
    assert "geprüft von Anna" in c["old_value"]
    assert client.delete(pfad, json={"by": "Ben"}).status_code == 404

    ohne = client.get(f"{S}/documents?unlinked=1").get_json()
    assert [x["id"] for x in ohne] == [doc_id] and ohne[0]["parts"] == []

    # wieder zuordnen, geprüft durch einen Menschen
    neu = f"{S}/parts/{part['id']}/documents"
    assert client.post(neu, json={"by": "Ben", "document_id": 999}).status_code == 404
    assert client.post(neu, json={"by": "Ben", "document_id": "1"}).status_code == 400
    r = client.post(neu, json={"by": "Ben", "document_id": doc_id, "status": "geprueft"})
    assert r.status_code == 201
    assert r.get_json()["documents"][0]["verified_by"] == "Ben"
    assert stati(client, bom) == ["vorhanden", "offen", "vorhanden"]
    assert client.post(neu, json={"by": "Ben", "document_id": doc_id}).status_code == 409

    # Dokument ohne Bauteil löschen; die Datei bleibt liegen
    anl = zweites_dokument()
    r = client.delete(f"{S}/documents/{anl}", json={"by": "Ben", "note": "doppelt"})
    assert r.status_code == 200 and r.get_json() == {"deleted": anl}
    assert db.session.get(Document, anl) is None
    assert (docs / "P+F" / "78E_Anleitung.pdf").is_file()
    [spur] = client.get(f"{S}/changes?table=document").get_json()
    assert (spur["field"], spur["document_id"], spur["changed_by"]) == ("geloescht", anl, "Ben")
    assert "P+F/78E_Anleitung.pdf" in spur["old_value"]
    assert client.delete(f"{S}/documents/{anl}", json={"by": "Ben"}).status_code == 404


def test_bauteil_aendern(client, part):
    pfad = f"{S}/parts/{part['id']}"
    r = client.patch(pfad, json={"by": "Ben", "name_en": "Absolute encoder", "manufacturer": "Neu AG",
                                 "review_needed": True, "note": "laut Datenblatt"})
    assert r.status_code == 200
    d = r.get_json()
    assert (d["name_en"], d["manufacturer"], d["review_needed"], d["changed_by"]) == \
        ("Absolute encoder", "Neu AG", True, "Ben")
    assert {(c["field"], c["old_value"], c["new_value"]) for c in d["changes"]} == {
        ("name_en", None, "Absolute encoder"), ("manufacturer", "Pepperl+Fuchs", "Neu AG"),
        ("review_needed", "nein", "ja")}
    assert [p["id"] for p in client.get(f"{S}/parts?review_needed=1").get_json()] == [part["id"]]
    namen = {m["name"]: m["part_count"] for m in client.get(f"{S}/manufacturers").get_json()}
    assert namen == {"Neu AG": 1, "Pepperl+Fuchs": 0}

    # Leeren ist erlaubt, ein gleicher Hersteller in anderer Schreibweise wird wiedergefunden
    d = client.patch(pfad, json={"by": "Ben", "name_en": "", "manufacturer": "pepperl + fuchs"}).get_json()
    assert (d["name_en"], d["manufacturer"]) == (None, "Pepperl+Fuchs")

    for falsch in ({"name_de": "x"}, {"by": "Ben"}, {"by": "Ben", "name_en": None},
                   {"by": "Ben", "profile_no": "1"}, {"by": "Ben", "review_needed": "ja"},
                   {"by": "Ben", "manufacturer_url": "javascript:alert(1)"},
                   {"by": "Ben", "name_de": 5}):
        r = client.patch(pfad, json=falsch)
        assert r.status_code == 400, falsch
        assert r.get_json()["error"]
    assert client.patch(f"{S}/parts/999", json={"by": "Ben", "name_de": "x"}).status_code == 404


def test_bauteil_anlegen(client, bom):
    r = client.post(f"{S}/parts", json={"by": "Ben", "profile_no": "99000150",
                                        "name_de": "Induktiver Sensor", "manufacturer": "Festo",
                                        "manufacturer_article_no": "NJ50-N"})
    assert r.status_code == 201
    d = r.get_json()
    assert (d["source"], d["status"], d["changed_by"], d["manufacturer"]) == \
        ("manual", "ungeprueft", "Ben", "Festo")
    assert d["missing_doc_types"] == ["eu_konformitaetserklaerung"]
    assert [i["pos"] for i in d["bom_items"]] == ["150"]
    assert {c["field"] for c in d["changes"]} == {"angelegt", "name_de",
                                                  "manufacturer_article_no", "manufacturer"}
    assert client.post(f"{S}/parts", json={"by": "Ben", "profile_no": "99000150"}).status_code == 409
    assert client.post(f"{S}/parts", json={"by": "Ben"}).status_code == 400
    assert client.post(f"{S}/parts", json={"profile_no": "1"}).status_code == 400


def test_dokument_aendern(client, bom, part):
    doc_id = part_doc(client, part)
    pfad = f"{S}/documents/{doc_id}"
    r = client.patch(pfad, json={"by": "Ben", "doc_type": "datenblatt", "valid_until": "2030-06-30",
                                 "languages": ["DE", "fr"], "certificate_no": ""})
    assert r.status_code == 200
    d = r.get_json()
    assert (d["doc_type"], d["valid_until"], d["languages"], d["certificate_no"]) == \
        ("datenblatt", "2030-06-30", ["de", "fr"], None)
    assert d["items_updated"] == 2 and d["changed_by"] == "Ben"
    assert stati(client, bom)[0] != "vorhanden"  # Pflichtdokument fehlt jetzt
    assert {(c["field"], c["old_value"], c["new_value"]) for c in d["changes"]} == {
        ("doc_type", "eu_konformitaetserklaerung", "datenblatt"),
        ("valid_until", None, "2030-06-30"), ("languages", "de,en", "de,fr"),
        ("certificate_no", "ITS15ATEX18372X", None)}
    # Änderungen am Dokument stehen auch beim Bauteil
    assert len(client.get(f"{S}/parts/{part['id']}").get_json()["changes"]) == 4

    d = client.patch(pfad, json={"by": "Ben", "doc_type": "eu_konformitaetserklaerung",
                                 "valid_until": ""}).get_json()
    assert d["valid_until"] is None
    assert stati(client, bom) == ["vorhanden", "offen", "vorhanden"]
    assert client.get(f"{S}/documents?doc_type=eu_konformitaetserklaerung").get_json()[0]["parts"][0]["profile_no"] == "99000110"
    assert len(client.get(f"{S}/documents?q=78E_Konf").get_json()) == 1

    for falsch in ({"by": "Ben", "doc_type": "zertifikat"}, {"by": "Ben", "valid_until": "30.13.2030"},
                   {"by": "Ben", "languages": ["es"]}, {"by": "Ben", "languages": "de"},
                   {"by": "Ben", "doc_type": "eu_konformitaetserklaerung"}, {"doc_type": "datenblatt"}):
        assert client.patch(pfad, json=falsch).status_code == 400, falsch
    assert client.get(f"{S}/documents?doc_type=x").status_code == 400


def test_hersteller_umbenennen(client, part):
    client.post(f"{S}/parts", json={"by": "Ben", "profile_no": "1", "manufacturer": "Festo"})
    m = {m["name"]: m["id"] for m in client.get(f"{S}/manufacturers").get_json()}
    pfad = f"{S}/manufacturers/{m['Pepperl+Fuchs']}"
    r = client.patch(pfad, json={"by": "Ben", "name": "Pepperl+Fuchs SE"})
    assert r.status_code == 200 and r.get_json()["normalized_name"] == "pepperlfuchsse"
    assert client.get(f"{S}/parts/{part['id']}").get_json()["manufacturer"] == "Pepperl+Fuchs SE"
    assert client.patch(pfad, json={"by": "Ben", "name": "FESTO"}).status_code == 409
    assert client.patch(pfad, json={"by": "Ben", "name": "Pepperl+Fuchs SE"}).status_code == 400
    assert client.patch(pfad, json={"by": "Ben", "name": "--"}).status_code == 400
    assert client.patch(f"{S}/manufacturers/999", json={"by": "Ben", "name": "x"}).status_code == 404
    spur = client.get(f"{S}/changes?table=manufacturer").get_json()
    assert [(c["field"], c["old_value"], c["new_value"]) for c in spur][0] == \
        ("name", "Pepperl+Fuchs", "Pepperl+Fuchs SE")


def test_pdf_nur_im_dokumentenordner(client, part):
    doc_id = part_doc(client, part)
    r = client.get(f"{S}/documents/{doc_id}/file")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:4] == b"%PDF"
    doc = db.session.get(Document, doc_id)
    doc.file_path = "../../etc/passwd"
    db.session.commit()
    r = client.get(f"{S}/documents/{doc_id}/file")
    assert r.status_code == 404 and r.get_json()["error"] == "Datei nicht vorhanden."
    assert client.get(f"{S}/documents/999/file").status_code == 404
    assert db.session.scalar(db.select(db.func.count()).select_from(StammdatenAenderung)) == 0
