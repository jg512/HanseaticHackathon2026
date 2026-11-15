import io
from datetime import datetime, timezone

from conftest import xlsx
from test_export import bauteil, dokument, lies

from app.db import db
from app.models import BomItem, Finding
from app.services import status
from app.services.excel_export import KOPF, VERMERK_KOPF

ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-1", 4, "Zone 1"],
          [20, 99000020, "Sensor", "P+F", "NJ-1", 1, "Zone 1"],
          [30, 99000010, "Magnetspule", "Festo", "VACF-1", 4, "Zone 1"],   # Dublette von Pos. 10
          [280, 99000280, "Linearführung", None, None, 2, None]]
GRUND = "Hersteller existiert nicht mehr, Teil ist rein mechanisch."


def heute():
    return datetime.now(timezone.utc).strftime("%d.%m.%Y")


def vorgang(client):
    sensor = bauteil("99000020", name_de="Sensor induktiv")
    dokument(sensor, "eu_konformitaetserklaerung", "P+F/ke.pdf", ["de"])
    db.session.commit()
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "l.xlsx")}).get_json()
    return bom["id"], {i["pos"]: i["id"] for i in bom["items"]}


def stati(client, bom_id):
    return {i["pos"]: i["status"] for i in client.get(f"/api/boms/{bom_id}").get_json()["items"]}


def erledige(client, bom_id, item_id, **daten):
    return client.post(f"/api/boms/{bom_id}/items/{item_id}/resolve", json=daten)


def oeffne(client, bom_id, item_id, **daten):
    return client.post(f"/api/boms/{bom_id}/items/{item_id}/reopen", json=daten)


def test_position_von_hand_erledigen_und_zuruecknehmen(client):
    bom_id, pos = vorgang(client)
    assert stati(client, bom_id) == {"10": "offen", "20": "vorhanden", "30": "offen",
                                     "280": "klaerung"}

    for daten in ({}, {"by": "Eva"}, {"note": GRUND}, {"by": "  ", "note": GRUND},
                  {"by": "Eva", "note": "   "}, {"by": "Eva", "note": 5},
                  {"by": "Eva", "note": "x" * 2001}, {"by": "E" * 201, "note": GRUND}):
        r = erledige(client, bom_id, pos["280"], **daten)
        assert r.status_code == 400, daten
    assert stati(client, bom_id)["280"] == "klaerung"

    r = erledige(client, bom_id, pos["280"], by="  Eva ", note=f"  {GRUND}\n")
    assert r.status_code == 200
    item = r.get_json()
    assert (item["status"], item["resolved_by"], item["resolution_note"]) == (
        "erledigt", "Eva", GRUND)
    assert item["status_note"] == (f"Eva hat die Position am {heute()} von Hand erledigt. "
                                   f"Begründung: {GRUND}")
    assert item["resolved_at"].startswith(datetime.now(timezone.utc).date().isoformat())

    r = erledige(client, bom_id, pos["280"], by="Jan", note="nochmal")
    assert r.status_code == 409 and "Eva hat diese Position schon von Hand erledigt." in r.get_json()["error"]
    r = erledige(client, bom_id, pos["20"], by="Jan", note="x")
    assert r.status_code == 409 and "schon vollständig" in r.get_json()["error"]
    client.post(f"/api/boms/{bom_id}/items/{pos['10']}/ex-teil", json={"ex_teil": False, "by": "Jan"})
    assert erledige(client, bom_id, pos["10"], by="Jan", note="x").status_code == 409

    assert erledige(client, bom_id, 999, by="Eva", note=GRUND).status_code == 404
    assert erledige(client, 999, pos["280"], by="Eva", note=GRUND).status_code == 404
    assert oeffne(client, 999, pos["280"], by="Eva").status_code == 404

    assert oeffne(client, bom_id, pos["280"]).status_code == 400  # ohne Namen
    r = oeffne(client, bom_id, pos["280"], by="Jan")
    assert r.status_code == 200
    item = r.get_json()
    assert (item["status"], item["status_note"]) == (
        "klaerung", "Es fehlt die Hersteller-Artikelnummer. Ohne sie kann der Agent nicht suchen.")
    assert (item["resolved_by"], item["resolved_at"], item["resolution_note"]) == (None, None, None)
    assert oeffne(client, bom_id, pos["280"], by="Jan").status_code == 409
    assert oeffne(client, bom_id, pos["30"], by="Jan").status_code == 409  # nie erledigt


def test_dublette_wird_einzeln_entschieden(client):
    bom_id, pos = vorgang(client)
    assert erledige(client, bom_id, pos["10"], by="Eva", note=GRUND).status_code == 200
    assert stati(client, bom_id) == {"10": "erledigt", "20": "vorhanden", "30": "offen",
                                     "280": "klaerung"}
    stand = client.get(f"/api/boms/{bom_id}/missing").get_json()
    assert [o["pos"] for o in stand["open"]] == ["30", "280"]
    assert client.get(f"/api/boms/{bom_id}/export").status_code == 409


def test_erledigt_bleibt_stehen_bis_zurueckgenommen(client):
    bom_id, pos = vorgang(client)
    erledige(client, bom_id, pos["10"], by="Eva", note=GRUND)
    item = db.session.get(BomItem, pos["10"])
    item.findings.append(Finding(source="ordner", doc_type="eu_konformitaetserklaerung"))
    db.session.flush()
    assert status.aktualisiere(item) == "erledigt"  # offener Vorschlag ändert den Stand nicht
    assert item not in status.offene_positionen(item.bom)
    db.session.commit()
    assert oeffne(client, bom_id, pos["10"], by="Eva").get_json()["status"] == "vorschlag"
    assert [f.status for f in item.findings] == ["vorgeschlagen"]


def test_ausgabe_mit_von_hand_erledigten_positionen(client):
    bom_id, pos = vorgang(client)
    for p in ("10", "30", "280"):
        assert erledige(client, bom_id, pos[p], by="Eva", note=f"{GRUND} ({p})").status_code == 200

    stand = client.get(f"/api/boms/{bom_id}/missing").get_json()
    assert (stand["total"], stand["done"], stand["complete"], stand["open"]) == (4, 4, True, [])
    assert [(o["pos"], o["status"], o["resolved_by"], o["resolution_note"], o["missing_documents"])
            for o in stand["resolved"]] == [
        ("10", "erledigt", "Eva", f"{GRUND} (10)", "EU-Konformitätserklärung"),
        ("30", "erledigt", "Eva", f"{GRUND} (30)", "EU-Konformitätserklärung"),
        ("280", "erledigt", "Eva", f"{GRUND} (280)", "EU-Konformitätserklärung")]
    assert stand["resolved"][0]["missing_doc_types"] == ["eu_konformitaetserklaerung"]

    tag = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0,
                                             tzinfo=None)
    vermerke = [
        VERMERK_KOPF,
        ["10", "99000010", "Magnetspule", "Festo", "VACF-1", f"{GRUND} (10)", "Eva", tag,
         "EU-Konformitätserklärung"],
        ["30", "99000010", "Magnetspule", "Festo", "VACF-1", f"{GRUND} (30)", "Eva", tag,
         "EU-Konformitätserklärung"],
        ["280", "99000280", "Linearführung", None, None, f"{GRUND} (280)", "Eva", tag,
         "EU-Konformitätserklärung"]]

    wb, zeilen = lies(client.get(f"/api/boms/{bom_id}/export"))
    assert wb.sheetnames == ["Ex-Dokumente", "Vermerke"]
    assert zeilen[0] == KOPF and len(zeilen[0]) == 17
    # ohne Bauteil in der Datenbank: Angaben der Position, alle Dokumentzellen leer; Dublette einmal
    assert zeilen[1:] == [
        ["Magnetspule", None, "99000010", "Festo"] + [None] * 13,
        ["Sensor induktiv", None, "99000020", "P+F", None, None, None, None, "ke.pdf"]
        + [None] * 6 + [datetime(2023, 8, 23), None],
        ["Linearführung", None, "99000280", None] + [None] * 13]
    assert [list(z) for z in wb["Vermerke"].iter_rows(values_only=True)] == vermerke
    assert wb["Vermerke"]["H2"].number_format == "DD.MM.YYYY"
    assert wb["Vermerke"]["A1"].font.b

    wb, zeilen = lies(client.get(f"/api/boms/{bom_id}/missing.xlsx"))
    assert wb.sheetnames == ["Fehlliste", "Vermerke"]
    assert len(zeilen) == 1  # nichts mehr offen
    assert [list(z) for z in wb["Vermerke"].iter_rows(values_only=True)] == vermerke


def test_position_ohne_nummer_steht_einzeln_in_der_kundenliste(client):
    zeilen = [[10, None, "Schild", "Acme", None, 1, None], [20, None, "Kabel", None, None, 1, None]]
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "l.xlsx")}).get_json()
    for i in bom["items"]:
        erledige(client, bom["id"], i["id"], by="Eva", note="ohne Nummer, geprüft")
    _, kunden = lies(client.get(f"/api/boms/{bom['id']}/export"))
    assert kunden[1:] == [["Schild", None, None, "Acme"] + [None] * 13,
                          ["Kabel", None, None, None] + [None] * 13]


def test_meldung_abhaken_und_zuruecknehmen(client):
    bom_id, pos = vorgang(client)
    bom = client.get(f"/api/boms/{bom_id}").get_json()
    assert (bom["issue_count"], bom["open_issue_count"]) == (4, 4)
    meldung = next(m for m in bom["issues"] if m["code"] == "hersteller_fehlt")
    assert (meldung["resolved_by"], meldung["resolved_at"], meldung["comment"]) == (None, None, None)
    url = f"/api/boms/{bom_id}/issues/{meldung['id']}"

    assert client.post(f"{url}/resolve", json={"comment": "x"}).status_code == 400
    assert client.post(f"{url}/resolve", json={"by": "Eva", "comment": "x" * 2001}).status_code == 400
    assert client.post(f"/api/boms/{bom_id}/issues/999/resolve", json={"by": "Eva"}).status_code == 404
    assert client.post(f"/api/boms/999/issues/{meldung['id']}/resolve",
                       json={"by": "Eva"}).status_code == 404

    r = client.post(f"{url}/resolve", json={"by": " Eva ", "comment": " Hersteller ist Bosch Rexroth. "})
    assert r.status_code == 200
    m = r.get_json()
    assert (m["id"], m["code"], m["resolved_by"], m["comment"]) == (
        meldung["id"], "hersteller_fehlt", "Eva", "Hersteller ist Bosch Rexroth.")
    assert m["resolved_at"]
    assert client.post(f"{url}/resolve", json={"by": "Jan"}).status_code == 409

    bom = client.get(f"/api/boms/{bom_id}").get_json()
    assert (bom["issue_count"], bom["open_issue_count"]) == (4, 3)
    assert stati(client, bom_id)["280"] == "klaerung"  # kein Einfluss auf den Stand
    assert client.get("/api/boms").get_json()[0]["open_issue_count"] == 3

    ohne = next(m for m in bom["issues"] if m["code"] == "einbauort_fehlt")
    r = client.post(f"/api/boms/{bom_id}/issues/{ohne['id']}/resolve", json={"by": "Jan"})
    assert (r.get_json()["resolved_by"], r.get_json()["comment"]) == ("Jan", None)

    assert client.post(f"{url}/reopen", json={}).status_code == 400
    r = client.post(f"{url}/reopen", json={"by": "Jan"})
    assert (r.get_json()["resolved_by"], r.get_json()["resolved_at"], r.get_json()["comment"]) == (
        None, None, None)
    assert client.post(f"{url}/reopen", json={"by": "Jan"}).status_code == 409
    assert client.get(f"/api/boms/{bom_id}").get_json()["open_issue_count"] == 3
