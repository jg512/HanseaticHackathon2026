import io
from datetime import datetime

from openpyxl import load_workbook

from app.db import db
from app.models import (Bom, BomItem, Document, DocumentLanguage, Manufacturer, Part,
                        PartDocument)
from app.services.excel_export import KOPF

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def vorgang(*positionen):
    items = []
    for nr, (pos, profile_no, felder) in enumerate(positionen, start=1):
        werte = dict(row_number=nr, pos=pos, profile_no=profile_no, description=f"Teil {pos}",
                     manufacturer="Hersteller lt. Liste", manufacturer_article_no=f"TYP-{pos}",
                     location="Zone 1")
        werte.update(felder)
        items.append(BomItem(**werte))
    bom = Bom(machine="Abfüllanlage TM-1", source_file_name="x.xlsx", source_file_hash="h",
              items=items)
    db.session.add(bom)
    db.session.commit()
    return bom.id


def bauteil(profile_no, **felder):
    part = Part(profile_no=profile_no, changed_by="test", **felder)
    db.session.add(part)
    return part


def dokument(part, doc_type, pfad, sprachen, pd_status="geprueft",
             verified_at=datetime(2023, 8, 23, 14, 0)):
    doc = Document(doc_type=doc_type, file_path=pfad, changed_by="test",
                   languages=[DocumentLanguage(language=s) for s in sprachen])
    db.session.add(PartDocument(part=part, document=doc, status=pd_status,
                                verified_at=verified_at, changed_by="test"))


def lies(antwort):
    assert antwort.status_code == 200
    assert antwort.mimetype == XLSX
    wb = load_workbook(io.BytesIO(antwort.data))
    return wb, [list(z) for z in wb.active.iter_rows(values_only=True)]


def vollstaendiger_vorgang():
    sensor = bauteil("99000001", name_de="Sensor induktiv", name_en="Inductive sensor",
                     manufacturer=Manufacturer(name="Pepperl+Fuchs", normalized_name="pepperlfuchs"),
                     ex_marking_raw="II 1G Ex ia IIC T6 Ga",
                     manufacturer_url="https://example.com/sensor")
    dokument(sensor, "eu_baumusterpruefbescheinigung", "P+F/zert.pdf", ["de", "en"],
             verified_at=datetime(2023, 8, 21))
    dokument(sensor, "eu_konformitaetserklaerung", "P+F/ke_de.pdf", ["de"])
    dokument(sensor, "bedienungsanleitung", "P+F/manual_en.pdf", ["en"])
    dokument(sensor, "datenblatt", "P+F/datenblatt.pdf", ["de"])          # keine Spalte
    dokument(sensor, "eu_baumusterpruefbescheinigung", "P+F/zert_fr.pdf", ["fr"])  # keine Spalte
    dokument(sensor, "bedienungsanleitung", "P+F/manual_de.pdf", ["de"], pd_status="ki_geprueft",
             verified_at=datetime(2024, 1, 1))                              # nicht geprüft

    ventil = bauteil("99000002")  # ohne Namen und Hersteller: Angaben der Position
    dokument(ventil, "eu_konformitaetserklaerung", "Siemens/ke_multi.pdf", ["de", "en", "fr"],
             verified_at=datetime(2025, 3, 5))
    dokument(ventil, "uk_konformitaetserklaerung", "Siemens/uk_ke.pdf", ["en"])
    dokument(ventil, "uk_baumusterpruefbescheinigung", "Siemens/uk_zert.pdf", ["en"])
    dokument(ventil, "bedienungsanleitung", "Siemens/b_de.pdf", ["de"])
    dokument(ventil, "bedienungsanleitung", "Siemens/a_de.pdf", ["de"])
    dokument(ventil, "bedienungsanleitung", "Siemens/anleitung.pdf", ["de", "en", "fr"])
    return vorgang(("10", "99000001", {}),
                   ("20", "99000002", {"description": "Magnetventil", "manufacturer": "Siemens"}),
                   ("30", "99000001", {}),                                 # Dublette
                   ("40", "99000099", {"status": "kein_ex_teil"}))


def test_kundenliste_im_festen_schema(client):
    bom_id = vollstaendiger_vorgang()
    r = client.get(f"/api/boms/{bom_id}/export")
    assert f"filename=Ex-Dokumente_Abfullanlage_TM-1_Vorgang-{bom_id}.xlsx" in \
        r.headers["Content-Disposition"]
    wb, zeilen = lies(r)

    assert wb.sheetnames == ["Ex-Dokumente"]
    assert zeilen[0] == KOPF
    assert zeilen[0][:3] == ["Gerät", "Device", "Artikel-Nr. / Item number"]
    assert zeilen[0][16] == "DL-Link" and len(zeilen[0]) == 17
    assert len(zeilen) == 3  # Dublette = eine Zeile, kein_ex_teil fehlt

    assert zeilen[1] == [
        "Sensor induktiv", "Inductive sensor", "99000001", "Pepperl+Fuchs", "II 1G Ex ia IIC T6 Ga",
        "zert.pdf", "zert.pdf", None, "ke_de.pdf", None, None, None,
        None, "manual_en.pdf", None, datetime(2023, 8, 23), None]  # Webseite ist kein DL-Link
    assert zeilen[2] == [
        "Magnetventil", None, "99000002", "Siemens", None,
        None, None, "uk_zert.pdf", "ke_multi.pdf", "ke_multi.pdf", "ke_multi.pdf", "uk_ke.pdf",
        "a_de.pdf\nanleitung.pdf\nb_de.pdf", "anleitung.pdf", "anleitung.pdf",
        datetime(2025, 3, 5), None]

    ws = wb.active
    assert ws["P2"].number_format == "DD.MM.YYYY"
    assert ws["A1"].font.b and ws.freeze_panes == "A2"
    assert ws["M3"].alignment.wrap_text


def test_ausgabe_ist_wiederholbar(client):
    bom_id = vollstaendiger_vorgang()
    assert lies(client.get(f"/api/boms/{bom_id}/export"))[1] == \
        lies(client.get(f"/api/boms/{bom_id}/export"))[1]


def test_keine_ausgabe_mit_offenen_positionen(client):
    teil = bauteil("99000001")
    dokument(teil, "eu_konformitaetserklaerung", "x/ke.pdf", ["de"])
    entwurf = bauteil("99000002")
    dokument(entwurf, "eu_konformitaetserklaerung", "x/ke2.pdf", ["de"], pd_status="ki_geprueft")
    bom_id = vorgang(("10", "99000001", {}), ("20", "99000002", {}),
                     ("30", "99000003", {"location": ""}))

    r = client.get(f"/api/boms/{bom_id}/export")
    assert r.status_code == 409
    antwort = r.get_json()
    assert "Noch 2 Positionen sind offen." in antwort["error"]
    assert [(o["pos"], o["status"]) for o in antwort["open"]] == [("20", "offen"),
                                                                  ("30", "klaerung")]


def test_fehlliste(client):
    bom_id = vorgang(("10", "99000001", {"searched_in": "Datenbank, Ordner"}),
                     ("20", "", {}), ("30", "99000003", {"status": "kein_ex_teil"}))
    stand = client.get(f"/api/boms/{bom_id}/missing").get_json()
    assert (stand["total"], stand["done"], stand["complete"]) == (3, 1, False)
    assert stand["missing_file_name"] == f"Fehlliste_Abfüllanlage_TM-1_Vorgang-{bom_id}.xlsx"
    erste = stand["open"][0]
    assert {k: erste[k] for k in ("pos", "profile_no", "description", "manufacturer",
                                  "manufacturer_article_no", "location", "status",
                                  "status_note", "searched_in", "missing_doc_types")} == {
        "pos": "10", "profile_no": "99000001", "description": "Teil 10",
        "manufacturer": "Hersteller lt. Liste", "manufacturer_article_no": "TYP-10",
        "location": "Zone 1", "status": "offen", "status_note": None,
        "searched_in": "Datenbank, Ordner", "missing_doc_types": ["eu_konformitaetserklaerung"]}

    wb, zeilen = lies(client.get(f"/api/boms/{bom_id}/missing.xlsx"))
    assert wb.sheetnames == ["Fehlliste"]
    assert zeilen[0][0] == "Pos." and zeilen[0][-1] == "Fehlende Dokumente"
    assert zeilen[1] == ["10", "99000001", "Teil 10", "Hersteller lt. Liste", "TYP-10", "Zone 1",
                         "Offen", None, "Datenbank, Ordner", "EU-Konformitätserklärung"]
    assert zeilen[2][:2] == ["20", None] and zeilen[2][6:8] == [
        "Klärung nötig", "Es fehlt die PRO.FILE-Nummer. Ohne sie kann der Agent nicht suchen."]
    assert len(zeilen) == 3


def test_fehlliste_eines_vollstaendigen_vorgangs(client):
    bom_id = vollstaendiger_vorgang()
    stand = client.get(f"/api/boms/{bom_id}/missing").get_json()
    assert (stand["total"], stand["done"], stand["complete"], stand["open"]) == (4, 4, True, [])
    assert lies(client.get(f"/api/boms/{bom_id}/missing.xlsx"))[1] == [
        ["Pos.", "PRO.FILE-Nr.", "Bezeichnung", "Hersteller", "Typ (Hersteller-Artikelnr.)",
         "Einbauort", "Status", "Hinweis", "Gesucht in", "Fehlende Dokumente"]]


def test_unbekannter_vorgang(client):
    for pfad in ("export", "missing", "missing.xlsx"):
        assert client.get(f"/api/boms/99/{pfad}").status_code == 404
