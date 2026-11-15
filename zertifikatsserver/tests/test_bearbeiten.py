import pytest

from app import altbestand
from app.db import db


@pytest.fixture
def c(app, alt, client):
    altbestand.importiere(alt)
    db.session.commit()
    return client


def dok(c, dateiname):
    i = next(d["id"] for d in c.get("/api/dokumente").get_json() if d["dateiname"] == dateiname)
    return c.get(f"/api/dokumente/{i}").get_json()


def teil(c, artikelnr):
    i = c.get(f"/api/teile?artikelnr={artikelnr}").get_json()[0]["id"]
    return c.get(f"/api/teile/{i}").get_json()


ALT = "festo_dsm_konf_714757.pdf"
NEU = "Festo_Schwenkantrieb DSM_Konformitätserklärung.pdf"


def test_dokument_korrigieren_mit_protokoll_und_regeln(c):
    d = dok(c, ALT)
    assert {m["regel"] for m in d["markierungen"]} == {"richtlinie_94_9", "norm_ausgabe", "alter"}
    url = f"/api/dokumente/{d['id']}"
    assert c.patch(url, json={"richtlinien": ["2014/34/EU"]}).status_code == 400  # ohne Name
    r = c.patch(url, json={"von": "Anna", "richtlinien": ["94/9/XY"]})
    assert r.status_code == 400 and "Richtlinien" in r.get_json()["error"]
    assert c.patch(url, json={"von": "Anna", "ausstellungsdatum": "12.05.2005"}).status_code == 400

    r = c.patch(url, json={"von": "Anna", "grund": "Regel hat falsch gelesen",
                           "richtlinien": ["2014/34/EU"], "normen": ["60079-0:2018"],
                           "ausstellungsdatum": "2023-01-10", "zertifikatsnummern": ["PTB 1"],
                           "sprachen": ["DE", "en"], "seiten": {"ausstellungsdatum": 1}})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert sorted(d["geaendert"]) == ["ausstellungsdatum", "normen", "richtlinien", "sprachen",
                                      "zertifikatsnummern"]
    assert d["werte"]["sprachen"] == ["de", "en"] and d["ausstellungsdatum"] == "2023-01-10"
    datum = next(a for a in d["angaben"] if a["art"] == "ausstellungsdatum")
    assert (datum["quelle"], datum["seite"]) == ("manuell", 1) and "Anna" in datum["hinweis"]
    # Regeln neu gerechnet: alle drei Verdachte fallen weg
    assert d["markierungen"] == []
    eintrag = next(a for a in d["aenderungen"] if a["feld"] == "richtlinien")
    assert (eintrag["alt"], eintrag["neu"], eintrag["von"]) == (["94/9/EG"], ["2014/34/EU"], "Anna")
    assert eintrag["grund"] == "Regel hat falsch gelesen"
    # nichts geändert: kein Protokoll
    r = c.patch(url, json={"von": "Anna", "richtlinien": ["2014/34/EU"]}).get_json()
    assert r["geaendert"] == []


def test_dokumentart_aendern(c):
    d = dok(c, "Festo_Allgemein.pdf")
    r = c.patch(f"/api/dokumente/{d['id']}", json={"von": "Ben", "doc_type": "datenblatt"})
    assert r.get_json()["doc_type"] == "datenblatt"
    assert c.patch(f"/api/dokumente/{d['id']}",
                   json={"von": "Ben", "doc_type": "quatsch"}).status_code == 400


def test_teil_bearbeiten(c):
    t = teil(c, "NJ50")
    url = f"/api/teile/{t['id']}"
    r = c.patch(url, json={"von": "Anna", "artikelnr": "NJ50-FP-N", "profile_no": "99000150",
                           "grund": "laut Stückliste"})
    assert r.status_code == 200
    t = r.get_json()
    assert (t["artikelnr"], t["profile_no"]) == ("NJ50-FP-N", "99000150")
    assert {(a["feld"], a["alt"], a["neu"]) for a in t["aenderungen"]} == {
        ("artikelnr", "NJ50", "NJ50-FP-N"), ("profile_no", None, "99000150")}
    # Hersteller wechseln: neuer Name legt den Hersteller an
    r = c.patch(url, json={"von": "Anna", "hersteller": "Pepperl+Fuchs SE"}).get_json()
    assert r["hersteller"] == "Pepperl+Fuchs"  # „Pepperl+Fuchs SE“ beginnt mit dem bekannten
    # gleiche Typbezeichnung wie ein anderes Teil desselben Herstellers: abgelehnt
    neu = c.post("/api/teile", json={"hersteller": "P+F", "artikelnr": "NJ40", "von": "A"})
    assert neu.status_code == 201 and neu.get_json()["aenderungen"][0]["feld"] == "angelegt"
    r = c.patch(f"/api/teile/{neu.get_json()['id']}", json={"von": "A", "artikelnr": "nj50 fp n"})
    assert r.status_code == 409
    assert c.patch(url, json={"von": "A", "artikelnr": " "}).status_code == 400


def test_ausmustern_und_wiederherstellen(c):
    d = dok(c, NEU)
    url = f"/api/dokumente/{d['id']}"
    assert c.post(url + "/ausmustern", json={"von": "Anna"}).status_code == 400  # ohne Grund
    r = c.post(url + "/ausmustern", json={"von": "Anna", "grund": "falsches Gerät"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ausgemustert"] and d["ablage"] == "archiv"
    assert c.get(d["pdf_url"]).status_code == 200  # Link geht weiter
    t = teil(c, "DSM")
    assert all(v["dokument_id"] != d["id"] for v in t["aktuell"])
    beendet = next(v for v in t["historie"] if v["dokument_id"] == d["id"])
    assert beendet["stand"] == "beendet" and "Archiv" in beendet["beendet_grund"]
    assert c.post(url + "/ausmustern", json={"von": "A", "grund": "x"}).status_code == 409
    assert len(c.get("/api/dokumente?ausgemustert=ja").get_json()) == 1
    # ein ausgemustertes Dokument wird nicht neu verknüpft
    r = c.post(f"/api/teile/{t['id']}/verknuepfungen", json={"dokument_id": d["id"], "von": "A"})
    assert r.status_code == 409

    r = c.post(url + "/wiederherstellen", json={"von": "Ben", "grund": "doch richtig"})
    d = r.get_json()
    assert not d["ausgemustert"] and d["ablage"] == "aktuell"
    assert any(v["dokument_id"] == d["id"] for v in teil(c, "DSM")["aktuell"])
    assert [(a["feld"], a["von"]) for a in d["aenderungen"]][:2] == [
        ("ausgemustert", "Ben"), ("ausgemustert", "Anna")]


def test_version_beenden_wiederherstellen_umhaengen(c):
    t = teil(c, "DSM")
    v_alt, v_neu = sorted(t["aktuell"], key=lambda v: v["dateiname"] != ALT)
    url = f"/api/verknuepfungen/{v_alt['id']}"
    r = c.post(url + "/beenden", json={"von": "Anna", "grund": "nur alte Fassung"})
    assert r.status_code == 200 and r.get_json()["stand"] == "beendet"
    assert dok(c, ALT)["ablage"] == "archiv"
    assert c.post(url + "/beenden", json={"von": "A", "grund": "x"}).status_code == 409

    # umhängen der anderen Fassung auf ein neues Teil
    ziel = c.post("/api/teile", json={"hersteller": "Festo", "artikelnr": "DSM-25-270-P-A-B",
                                      "von": "Anna"}).get_json()
    r = c.post(f"/api/verknuepfungen/{v_neu['id']}/umhaengen",
               json={"teil_id": ziel["id"], "von": "Anna", "grund": "genauerer Typ"})
    assert r.status_code == 201, r.get_json()
    antwort = r.get_json()
    assert antwort["alt"]["stand"] == "beendet" and "Umgehängt" in antwort["alt"]["beendet_grund"]
    assert antwort["neu"]["teil_id"] == ziel["id"] and antwort["neu"]["version"] == 1
    assert dok(c, NEU)["ablage"] == "aktuell"

    # die beendete Fassung lässt sich wiederherstellen
    r = c.post(url + "/wiederherstellen", json={"von": "Ben", "grund": "zurück"})
    assert r.status_code == 200 and r.get_json()["stand"] == "aktuell"
    assert dok(c, ALT)["ablage"] == "aktuell"
    protokoll = c.get(f"/api/aenderungen?objekt=verknuepfung&objekt_id={v_alt['id']}").get_json()
    assert [(a["alt"], a["neu"]) for a in protokoll] == [("beendet", "aktuell"),
                                                         ("aktuell", "beendet")]

    # nach einer neuen Version 2 bleibt die beendete Version 1 beendet
    c.post(url + "/beenden", json={"von": "Anna", "grund": "wieder weg"})
    r = c.post(f"/api/teile/{t['id']}/verknuepfungen",
               json={"dokument_id": dok(c, "Festo_Allgemein.pdf")["id"], "von": "Anna",
                     "doc_type": "eu_konformitaetserklaerung"})
    assert r.get_json()["verknuepfung"]["version"] == 2
    r = c.post(url + "/wiederherstellen", json={"von": "Anna"})
    assert r.status_code == 409 and "andere aktuelle Version" in r.get_json()["error"]
    assert c.post("/api/verknuepfungen/999/beenden", json={"von": "A", "grund": "x"}) \
        .status_code == 404
