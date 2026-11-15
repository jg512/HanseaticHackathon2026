import io

import pytest

from app import FEHLER, altbestand
from app.db import db

from conftest import NEU_2014, pdf


@pytest.fixture
def importiert(app, alt, client):
    altbestand.importiere(alt)
    db.session.commit()
    return client


def dok_id(client, dateiname):
    return next(d["id"] for d in client.get("/api/dokumente").get_json()
                if d["dateiname"] == dateiname)


def test_fehler_als_json(client):
    for pfad in ("/gibt-es-nicht", "/api/dokumente/99", "/api/dokumente/99/pdf", "/api/teile/9"):
        r = client.get(pfad)
        assert r.status_code == 404 and r.get_json() == {"error": FEHLER[404]}
    r = client.delete("/api/dokumente")
    assert r.status_code == 405 and "error" in r.get_json()


def test_health(client):
    assert client.get("/health").get_json() == {"ok": True}


def test_suche_nach_hersteller_und_artikelnr(importiert):
    # Name aus der Stückliste, Ordnername und genaue Typbezeichnung
    r = importiert.get("/api/teile?hersteller=Pepperl%2BFuchs&artikelnr=NJ50").get_json()
    assert [(t["artikelnr"], t["treffer"]) for t in r] == [("NJ50", "genau")]
    assert importiert.get("/api/teile?hersteller=P%2BF&artikelnr=NJ50").get_json()[0]["id"] == \
        r[0]["id"]
    # Typ aus der Stückliste mit Zusätzen: teilweise
    r = importiert.get("/api/teile?hersteller=Festo&artikelnr=DSM-…-EX").get_json()
    assert [(t["artikelnr"], t["treffer"]) for t in r] == [("DSM", "teilweise")]
    teil = r[0]
    assert len(teil["aktuell"]) == 2 and all(v["version"] == 1 for v in teil["aktuell"])
    assert all(v["pdf_url"].startswith("/api/dokumente/") for v in teil["historie"])
    assert importiert.get("/api/teile?hersteller=Siemens&artikelnr=DSM").get_json() == []


def test_dokument_detail_und_pdf(importiert):
    i = dok_id(importiert, "festo_dsm_konf_714757.pdf")
    d = importiert.get(f"/api/dokumente/{i}").get_json()
    assert d["markierungsstand"] == "verdacht"
    assert {a["art"] for a in d["angaben"]} >= {"richtlinie", "norm", "ausstellungsdatum"}
    assert all(m["seite"] == 1 and m["grund"] for m in d["markierungen"])
    assert d["teile_historie"][0]["artikelnr"] == "DSM"
    r = importiert.get(d["pdf_url"])
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")


def test_filter(importiert):
    alle = importiert.get("/api/dokumente").get_json()
    assert len(alle) == 4
    assert {d["dateiname"] for d in importiert.get(
        "/api/dokumente?regel=richtlinie_94_9").get_json()} == {"festo_dsm_konf_714757.pdf"}
    assert len(importiert.get("/api/dokumente?lesbar=nein").get_json()) == 1
    assert len(importiert.get("/api/dokumente?verknuepft=nein").get_json()) == 1
    assert len(importiert.get("/api/dokumente?hersteller=P%2BF").get_json()) == 1
    assert len(importiert.get("/api/dokumente?markierung=ohne").get_json()) == 1
    # so fragt das Backend: nach dem SHA-256 seiner Funde, mit den Gründen der Markierung
    zwei = [d["sha256"] for d in alle[:2]]
    treffer = importiert.get("/api/dokumente?sha256=" + ",".join(zwei)).get_json()
    assert sorted(d["sha256"] for d in treffer) == sorted(zwei)
    alt = next(d for d in alle if d["dateiname"] == "festo_dsm_konf_714757.pdf")
    assert alt["gruende"] and all(isinstance(g, str) for g in alt["gruende"])


def test_markieren_bestaetigen_verwerfen(importiert):
    alt = dok_id(importiert, "festo_dsm_konf_714757.pdf")
    neu = dok_id(importiert, "Festo_Schwenkantrieb DSM_Konformitätserklärung.pdf")
    # ohne Namen oder Grund nichts
    assert importiert.post("/api/markierungen", json={"dokument_ids": [neu]}).status_code == 400
    assert importiert.post("/api/markierungen", json={
        "dokument_ids": [neu, 999], "von": "Anna", "grund": "x"}).status_code == 404
    r = importiert.post("/api/markierungen", json={
        "dokument_ids": [neu], "von": "Anna", "grund": "Hersteller hat neue Ausgabe"})
    assert r.status_code == 201 and r.get_json()["markierungen"][0]["erstellt_von"] == "Anna"

    r = importiert.post("/api/markierungen/bestaetigen",
                        json={"dokument_ids": [alt, neu], "von": "Anna"}).get_json()
    assert r["geaendert"] == 4 and r["ohne_markierung"] == []
    liste = importiert.get("/api/neue-suche").get_json()
    assert {e["dokument"]["id"] for e in liste} == {alt, neu}
    eintrag = next(e for e in liste if e["dokument"]["id"] == alt)
    assert {g["regel"] for g in eintrag["gruende"]} == {"richtlinie_94_9", "norm_ausgabe", "alter"}
    assert eintrag["teile"][0]["artikelnr"] == "DSM"

    # Verwerfen braucht einen Grund
    assert importiert.post("/api/markierungen/verwerfen",
                           json={"dokument_ids": [neu], "von": "Ben"}).status_code == 400
    r = importiert.post("/api/markierungen/verwerfen", json={
        "dokument_ids": [neu], "von": "Ben", "grund": "Ausgabe 2022 ist aktuell"}).get_json()
    assert r["geaendert"] == 1 and r["markierungen"][0]["entschieden_von"] == "Ben"
    assert {e["dokument"]["id"] for e in importiert.get("/api/neue-suche").get_json()} == {alt}

    # neu rechnen ändert keine Entscheidung
    importiert.post("/api/regeln/pruefen", json={})
    d = importiert.get(f"/api/dokumente/{alt}").get_json()
    assert {m["status"] for m in d["markierungen"]} == {"bestaetigt"}


def test_einzelne_markierung_entscheiden(importiert):
    alt = dok_id(importiert, "festo_dsm_konf_714757.pdf")
    m = importiert.get(f"/api/dokumente/{alt}").get_json()["markierungen"][0]
    r = importiert.post("/api/markierungen/verwerfen", json={
        "markierung_ids": [m["id"]], "von": "Ben", "grund": "geprüft"}).get_json()
    assert r["geaendert"] == 1
    d = importiert.get(f"/api/dokumente/{alt}").get_json()
    assert sorted(x["status"] for x in d["markierungen"]) == ["verdacht", "verdacht", "verworfen"]
    assert d["markierungsstand"] == "verdacht"


def test_neue_version_loest_ab_und_archiviert(importiert, app):
    teil = importiert.get("/api/teile?hersteller=Festo&artikelnr=DSM").get_json()[0]
    alte_urls = [v["pdf_url"] for v in teil["aktuell"]]
    alt = dok_id(importiert, "festo_dsm_konf_714757.pdf")
    importiert.post("/api/markierungen/bestaetigen", json={"dokument_ids": [alt], "von": "Anna"})

    datei = pdf(NEU_2014.replace("2022-08-09", "2025-01-15"))
    r = importiert.post(f"/api/teile/{teil['id']}/dokumente", data={
        "datei": (io.BytesIO(datei), "DSM_KE_2025.pdf"), "von": "Anna"},
        content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    antwort = r.get_json()
    assert antwort["verknuepfung"]["version"] == 2
    assert antwort["verknuepfung"]["doc_type"] == "eu_konformitaetserklaerung"
    assert sorted(antwort["abgeloest"]) == sorted(v["id"] for v in teil["aktuell"])

    teil = importiert.get(f"/api/teile/{teil['id']}").get_json()
    assert [v["version"] for v in teil["aktuell"]] == [2]
    assert [v["version"] for v in teil["historie"]] == [2, 1, 1]
    assert all(v["abgeloest_durch_id"] == antwort["verknuepfung"]["id"]
               for v in teil["historie"] if v["version"] == 1)
    # alte Dateien liegen im Archiv, ihre Links gehen weiter
    for url in alte_urls:
        d = importiert.get(url.removesuffix("/pdf")).get_json()
        assert d["ablage"] == "archiv"
        assert importiert.get(url).status_code == 200
    # das abgelöste Dokument braucht keine neue Suche mehr
    assert importiert.get("/api/neue-suche").get_json() == []

    # dieselbe Datei noch einmal: schon aktuell
    r = importiert.post(f"/api/teile/{teil['id']}/dokumente", data={
        "datei": (io.BytesIO(datei), "nochmal.pdf"), "von": "Anna"},
        content_type="multipart/form-data")
    assert r.status_code == 409


def test_upload_pruefungen(importiert):
    teil = importiert.get("/api/teile?artikelnr=NJ50").get_json()[0]
    url = f"/api/teile/{teil['id']}/dokumente"
    r = importiert.post(url, data={"datei": (io.BytesIO(b"kein pdf"), "x.pdf"), "von": "A"},
                        content_type="multipart/form-data")
    assert r.status_code == 400 and "passt nicht zur Endung .pdf" in r.get_json()["error"]
    r = importiert.post(url, data={"datei": (io.BytesIO(pdf("")), "scan.pdf"), "von": "A"},
                        content_type="multipart/form-data")
    assert r.status_code == 409 and "Dokumentart" in r.get_json()["error"]
    r = importiert.post(url, data={"datei": (io.BytesIO(pdf("")), "scan.pdf"), "von": "A",
                                   "doc_type": "eu_baumusterpruefbescheinigung"},
                        content_type="multipart/form-data")
    assert r.status_code == 201
    assert r.get_json()["verknuepfung"]["version"] == 1  # andere Art als der Scan im Altbestand


def test_teil_anlegen_und_vorhandenes_dokument_verknuepfen(importiert):
    r = importiert.post("/api/teile", json={"hersteller": "Festo", "artikelnr": "DSM",
                                            "von": "Anna"})
    assert r.status_code == 409
    r = importiert.post("/api/teile", json={"hersteller": "Festo", "artikelnr": "DFM-…-EX",
                                            "profile_no": "99000999", "von": "Anna"})
    assert r.status_code == 201
    teil = r.get_json()
    ohne = importiert.get("/api/dokumente?verknuepft=nein").get_json()[0]
    r = importiert.post(f"/api/teile/{teil['id']}/verknuepfungen", json={
        "dokument_id": ohne["id"], "von": "Anna", "doc_type": "datenblatt"})
    assert r.status_code == 201 and r.get_json()["verknuepfung"]["quelle"] == "manuell"
    assert importiert.get("/api/teile?profile_no=99000999").get_json()[0]["aktuell"][0][
        "dokument_id"] == ohne["id"]
    r = importiert.put(f"/api/teile/{teil['id']}", json={"bezeichnung": "Linearantrieb",
                                                          "von": "Anna"})
    assert r.get_json()["bezeichnung"] == "Linearantrieb"


def test_normen_aendern_rechnet_neu(importiert):
    neu = dok_id(importiert, "Festo_Schwenkantrieb DSM_Konformitätserklärung.pdf")
    assert importiert.put("/api/normen", json={"normen": [{"norm": "x", "jahr": 1}],
                                               "von": "A"}).status_code == 400
    r = importiert.put("/api/normen", json={
        "normen": [{"norm": "60079-0", "jahr": 2025, "hinweis": "Test"}], "von": "Anna"})
    assert r.status_code == 200 and r.get_json()["normen"][0]["jahr"] == 2025
    d = importiert.get(f"/api/dokumente/{neu}").get_json()
    assert [m["regel"] for m in d["markierungen"]] == ["norm_ausgabe"]
    assert "Test" in d["markierungen"][0]["grund"]
    regeln = importiert.get("/api/regeln").get_json()
    assert regeln["veraltet_nach_jahren"] == 10 and regeln["normen"][0]["norm"] == "60079-0"
    # zurück: der offene Verdacht fällt wieder weg
    importiert.put("/api/normen", json={"normen": [{"norm": "60079-0", "jahr": 2017}],
                                        "von": "Anna"})
    assert importiert.get(f"/api/dokumente/{neu}").get_json()["markierungen"] == []


def test_importe_und_hersteller(importiert):
    laeufe = importiert.get("/api/importe").get_json()
    assert laeufe[0]["zahlen"]["dokumente_neu"] == 4
    hersteller = {h["name"]: h["dokumente"] for h in importiert.get("/api/hersteller").get_json()}
    assert hersteller == {"Festo": 3, "Pepperl+Fuchs": 1}
