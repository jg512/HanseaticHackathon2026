import io

from conftest import xlsx

ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1A-1-EX4-M", 4, "Zone 1"],
          [280, 99000280, "Linearführung", None, None, 2, None]]


def upload(client, inhalt, name="liste.xlsx"):
    return client.post("/api/boms", data={"file": (io.BytesIO(inhalt), name)})


def test_upload_speichert_vorgang(client):
    r = upload(client, xlsx(ZEILEN))
    assert r.status_code == 201
    bom = r.get_json()
    assert bom["machine"] == "Abfüllanlage TM-1"
    assert [i["pos"] for i in bom["items"]] == ["10", "280"]
    assert [i["status"] for i in bom["items"]] == ["offen", "klaerung"]
    assert bom["items"][1]["status_note"] == ("Es fehlt die Hersteller-Artikelnummer. Ohne sie kann der "
                                                 "Agent nicht suchen.")
    assert bom["issue_count"] == 3

    assert client.get(f"/api/boms/{bom['id']}").get_json() == bom
    assert [b["id"] for b in client.get("/api/boms").get_json()] == [bom["id"]]


def test_upload_lehnt_fremde_datei_ab(client):
    r = upload(client, b"kein excel")
    assert r.status_code == 400
    assert "Excel" in r.get_json()["error"]
    assert client.get("/api/boms").get_json() == []


def test_unbekannter_vorgang(client):
    assert client.get("/api/boms/99").status_code == 404


def test_health(client):
    assert client.get("/health").get_json() == {"ok": True}


def test_mensch_entscheidet_kein_ex_teil(client):
    bom = upload(client, xlsx(ZEILEN)).get_json()
    url = f"/api/boms/{bom['id']}/items/{bom['items'][1]['id']}/ex-teil"

    assert client.post(url, json={"ex_teil": False}).status_code == 400  # ohne Namen
    r = client.post(url, json={"ex_teil": False, "by": "jan"})
    assert (r.get_json()["status"], r.get_json()["status_note"]) == ("kein_ex_teil", "jan hat entschieden, dass die Position kein Ex-Teil ist.")
    assert client.post(url, json={"ex_teil": True, "by": "jan"}).get_json()["status"] == "klaerung"
    assert client.post(f"/api/boms/{bom['id']}/items/999/ex-teil", json={}).status_code == 404


def test_404_als_json(client):
    for pfad in ("/api/boms/99", "/api/boms/abc", "/api/findings/999999/file"):
        r = client.get(pfad)
        assert r.status_code == 404 and r.get_json() == {"error": "Nicht gefunden."}


def test_pflichtspalten_mit_kopf_der_datei(client):
    r = upload(client, xlsx([[10, "Magnetspule", "Festo"]],
                            kopf=["Pos.", "Bezeichnung", "Hersteller"]))
    assert r.status_code == 400
    assert r.get_json()["error"] == (
        "In der Stückliste fehlen die Spalten „PRO.FILE-Nr.“, „Hersteller-Artikelnr.“, „Menge“ "
        "und „Einbauort“. Bitte ergänzen Sie sie und laden Sie die Datei noch einmal hoch.")


def test_doppelte_datei_wird_gemeldet(client):
    inhalt = xlsx(ZEILEN)
    erster = upload(client, inhalt).get_json()
    zweiter = upload(client, inhalt).get_json()
    meldung = [m for m in zweiter["issues"] if m["code"] == "datei_doppelt"]
    assert len(meldung) == 1 and f"Vorgang {erster['id']}" in meldung[0]["message"]
    assert not [m for m in erster["issues"] if m["code"] == "datei_doppelt"]
