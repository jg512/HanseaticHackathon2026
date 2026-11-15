import io
from datetime import datetime, timezone

from conftest import xlsx

ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-1", 4, "Zone 1"],
          [240, 99000240, "Drucktransmitter", "Siemens", "7MF4033", 1, "Zone 1"]]


def vorgang(client):
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "l.xlsx")}).get_json()
    return bom["id"], {i["pos"]: i["id"] for i in bom["items"]}


def frage(client, bom_id, **daten):
    return client.post(f"/api/boms/{bom_id}/questions", json=daten)


def test_frage_anlegen_kommentieren_klaeren_oeffnen(client):
    bom_id, pos = vorgang(client)
    anderer, pos2 = vorgang(client)

    for daten in ({}, {"by": "Eva"}, {"text": "Welche Zone?"}, {"by": "Eva", "text": "  "},
                  {"by": "Eva", "text": "x" * 2001}, {"by": "Eva", "text": "?", "item_id": "1"},
                  {"by": "Eva", "text": "?", "item_id": pos2["240"]}):
        assert frage(client, bom_id, **daten).status_code == 400, daten
    assert frage(client, 999, by="Eva", text="?").status_code == 404

    r = frage(client, bom_id, by=" Eva ", text=" Ist der Transmitter in Zone 1 oder 2? ",
              item_id=pos["240"])
    assert r.status_code == 201
    q = r.get_json()
    assert {k: q[k] for k in ("bom_id", "bom_item_id", "text", "created_by", "status", "answer",
                              "resolved_by", "resolved_at", "comments")} == {
        "bom_id": bom_id, "bom_item_id": pos["240"],
        "text": "Ist der Transmitter in Zone 1 oder 2?", "created_by": "Eva", "status": "offen",
        "answer": None, "resolved_by": None, "resolved_at": None, "comments": []}
    assert q["item"] == {"id": pos["240"], "pos": "240", "profile_no": "99000240",
                         "description": "Drucktransmitter"}
    assert q["created_at"]

    url = f"/api/questions/{q['id']}"
    assert client.post(f"{url}/comments", json={"by": "Jan"}).status_code == 400
    assert client.post("/api/questions/999/comments", json={"by": "Jan", "text": "x"}).status_code == 404
    r = client.post(f"{url}/comments", json={"by": "Jan", "text": "Laut Plan Zone 1."})
    assert r.status_code == 201
    assert [(c["created_by"], c["text"]) for c in r.get_json()["comments"]] == [
        ("Jan", "Laut Plan Zone 1.")]

    assert client.post(f"{url}/resolve", json={"by": "Eva"}).status_code == 400  # ohne Antwort
    r = client.post(f"{url}/resolve", json={"by": "Eva", "answer": "Zone 1, bestätigt vom Kunden."})
    assert r.status_code == 200
    q = r.get_json()
    assert (q["status"], q["answer"], q["resolved_by"]) == (
        "geklaert", "Zone 1, bestätigt vom Kunden.", "Eva")
    assert client.post(f"{url}/resolve", json={"by": "Jan", "answer": "x"}).status_code == 409
    assert client.get(f"/api/boms/{bom_id}").get_json()["open_question_count"] == 0
    # kein Einfluss auf den Stand
    assert [i["status"] for i in client.get(f"/api/boms/{bom_id}").get_json()["items"]] == [
        "offen", "offen"]

    assert client.post(f"{url}/reopen", json={}).status_code == 400
    r = client.post(f"{url}/reopen", json={"by": "Jan"})
    q = r.get_json()
    assert (q["status"], q["answer"], q["resolved_by"], q["resolved_at"]) == (
        "offen", None, None, None)
    heute = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    assert [(c["created_by"], c["text"]) for c in q["comments"]] == [
        ("Jan", "Laut Plan Zone 1."),
        ("Jan", f"Die Frage ist wieder offen. Die bisherige Antwort von Eva vom {heute} "
                "lautete: Zone 1, bestätigt vom Kunden.")]
    assert client.post(f"{url}/reopen", json={"by": "Jan"}).status_code == 409
    assert client.post("/api/questions/999/resolve", json={"by": "J", "answer": "x"}).status_code == 404


def test_liste_offene_zuerst(client):
    bom_id, pos = vorgang(client)
    ids = [frage(client, bom_id, by="Eva", text=f"Frage {n}").get_json()["id"] for n in (1, 2, 3)]
    client.post(f"/api/questions/{ids[0]}/resolve", json={"by": "Jan", "answer": "erledigt"})

    liste = client.get(f"/api/boms/{bom_id}/questions").get_json()
    assert [(q["text"], q["status"]) for q in liste] == [
        ("Frage 2", "offen"), ("Frage 3", "offen"), ("Frage 1", "geklaert")]
    assert liste[0]["item"] is None and liste[0]["bom_item_id"] is None
    bom = client.get(f"/api/boms/{bom_id}").get_json()
    assert bom["open_question_count"] == 2
    assert client.get("/api/boms").get_json()[0]["open_question_count"] == 2
    assert client.get("/api/boms/999/questions").status_code == 404
