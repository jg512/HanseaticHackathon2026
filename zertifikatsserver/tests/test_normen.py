from datetime import date

from app import anlegen, auslesen, normen, regeln
from app.db import db
from app.modelle import Aenderung, Angabe, NormAusgabe

from conftest import pdf


def werte(*seiten):
    return sorted(x["wert"] for x in auslesen.auswerten(pdf(*seiten)).angaben
                  if x["art"] == "norm")


def test_schreibweisen_der_normen():
    # Änderungen zählen nicht als Ausgabe, undatierte Nennungen gar nicht
    assert werte("EN 60079-0:2012 + A11:2013, EN 60079-0:2011/A11:2013") == \
        ["60079-0:2011", "60079-0:2012"]
    assert werte("EN IEC 60079-0:2018-07 und IEC 60079-0:2017-12") == \
        ["60079-0:2017", "60079-0:2018"]
    assert werte("EN IEC 60079-7:2015+A1:2018, EN 60079-14, IEC/EN 60079-11.") == \
        ["60079-7:2015"]
    # Zeilenumbrüche mitten in der Nennung, wie in Tabellen der Hersteller
    assert werte("EN IEC\n60079-11:2024-12\nEN 60079-1 \n: \n2014") == \
        ["60079-11:2024", "60079-1:2014"]


def test_name_der_norm():
    assert normen.name("EN IEC 60079-07") == "60079-7"
    assert normen.name(" IEC 60079-0 ") == "60079-0"
    assert normen.name("60079") is None and normen.name("x") is None and normen.name(3) is None


def test_vorgaben_beim_ersten_start(app):
    tabelle = {n.norm: n for n in db.session.scalars(db.select(NormAusgabe))}
    assert set(tabelle) == {n for n, _, _ in normen.VORGABEN}
    # AKTUELLE_NORMEN (hier 60079-0:2017) geht vor der Vorgabe
    assert tabelle["60079-0"].quelle == "konfiguration"
    assert tabelle["60079-11"].quelle == "vorgabe" and tabelle["60079-11"].jahr == 2011
    assert all(n.zu_bestaetigen for n in tabelle.values())


def test_vorhandene_zeilen_bleiben(app):
    db.session.execute(db.delete(NormAusgabe))
    db.session.add(NormAusgabe(norm="60079-0", jahr=2011, geaendert_von="Anna",
                               hinweis="von Hand"))
    db.session.add(NormAusgabe(norm="60079-11", jahr=2023, geaendert_von="Konfiguration"))
    db.session.commit()
    neu = anlegen()["normen"]
    assert "60079-0" not in neu and "60079-11" not in neu and "60079-7" in neu
    n0, n11 = db.session.get(NormAusgabe, "60079-0"), db.session.get(NormAusgabe, "60079-11")
    assert (n0.jahr, n0.hinweis, n0.quelle, n0.bestaetigt_von) == (2011, "von Hand", "manuell",
                                                                    "Anna")
    assert (n11.jahr, n11.quelle, n11.zu_bestaetigen) == (2023, "konfiguration", True)
    # zweiter Start: nichts mehr neu, Protokoll nennt die eingetragenen
    assert anlegen() == {"spalten": [], "normen": [], "quellen": 0}
    assert db.session.scalar(db.select(db.func.count(Aenderung.id)).where(
        Aenderung.objekt == "norm_ausgabe", Aenderung.feld == "60079-7")) == 2  # Start, nach Löschen


def test_iec_jahr_gilt_auch_fuer_en_ausgabe(app):
    class Dok:
        lesbar, ocr_seiten, ocr_hinweis, ausstellungsdatum, gueltig_bis = True, None, None, \
            date(2020, 1, 1), None
        doc_type = "eu_konformitaetserklaerung"

    def norm_befund(*werte_):
        Dok.angaben = [Angabe(art="norm", wert=w, seite=1, quelle="pdf") for w in werte_]
        return [b for b in regeln.befunde(Dok, date(2026, 1, 1), 10, regeln.aktuelle_normen())
                if b["regel"] == "norm_ausgabe"]

    assert norm_befund("60079-0:2017") == [] and norm_befund("60079-0:2018") == []
    assert norm_befund("60079-0:2012", "60079-0:2018") == []
    # mehrere alte Normen: eine Markierung, alle im Grund
    b = norm_befund("60079-0:2012", "60079-7:2007", "60079-11:2012")
    assert len(b) == 1 and b[0]["grund"] == (
        "Das Dokument nennt die Norm 60079-0 nur in der Ausgabe 2012 (Seite 1). Aktuell ist die "
        "Ausgabe 2017. Das Dokument nennt die Norm 60079-7 nur in der Ausgabe 2007 (Seite 1). "
        "Aktuell ist die Ausgabe 2015. Die aktuelle Ausgabe ist eine Vorgabe, die noch niemand "
        "bestätigt hat.")  # 60079-11:2012 = Ausgabe von 2011, aktuell laut Vorgabe


def test_put_aendert_nur_geaenderte_zeilen(client):
    vorher = {n["norm"]: n for n in client.get("/api/normen").get_json()}
    liste = [{"norm": n["norm"], "jahr": n["jahr"], "hinweis": n["hinweis"]}
             for n in vorher.values()]
    liste[1]["jahr"] = 2099  # 60079-1
    liste.append({"norm": "EN IEC 60079-2", "jahr": 2014})
    liste = [x for x in liste if x["norm"] != "60079-28"]
    r = client.put("/api/normen", json={"normen": liste, "von": "Bea"}).get_json()
    assert r["geaendert"] == ["60079-1", "60079-2", "60079-28"]
    nachher = {n["norm"]: n for n in r["normen"]}
    assert nachher["60079-0"]["geaendert_von"] == vorher["60079-0"]["geaendert_von"]
    assert nachher["60079-0"]["zu_bestaetigen"] and nachher["60079-0"]["pruefhinweis"]
    assert (nachher["60079-1"]["geaendert_von"], nachher["60079-1"]["bestaetigt_von"]) == \
        ("Bea", "Bea")
    assert nachher["60079-2"]["quelle"] == "manuell" and "60079-28" not in nachher
    # sortiert nach Teilnummer
    assert [n["norm"] for n in r["normen"]][:4] == ["60079-0", "60079-1", "60079-2", "60079-7"]
    protokoll = client.get("/api/aenderungen?objekt=norm_ausgabe").get_json()
    assert {(a["feld"], a["von"]) for a in protokoll if a["von"] == "Bea"} == \
        {("60079-1", "Bea"), ("60079-2", "Bea"), ("60079-28", "Bea")}


def test_put_lehnt_doppelte_und_falsche_ab(client):
    for liste in ([{"norm": "60079-0", "jahr": 2017}, {"norm": "EN 60079-00", "jahr": 2018}],
                  [{"norm": "60079-0", "jahr": True}], [{"norm": "60079-0", "jahr": 17}],
                  [{"norm": "61241-0", "jahr": 2006}], "60079-0"):
        r = client.put("/api/normen", json={"normen": liste, "von": "Bea"})
        assert r.status_code == 400 and "error" in r.get_json()
    assert len(client.get("/api/normen").get_json()) == len(normen.VORGABEN)


def test_bestaetigen(client):
    r = client.post("/api/normen/bestaetigen", json={"normen": ["60079-0", "EN 60079-7"],
                                                     "von": "Carl"})
    assert r.status_code == 200 and r.get_json()["bestaetigt"] == ["60079-0", "60079-7"]
    n = {x["norm"]: x for x in r.get_json()["normen"]}
    assert (n["60079-0"]["bestaetigt_von"], n["60079-0"]["zu_bestaetigen"]) == ("Carl", False)
    assert n["60079-0"]["pruefhinweis"] is None and n["60079-1"]["zu_bestaetigen"]
    # nochmals: nichts mehr zu bestätigen; unbekannte Norm und fehlender Name: Fehler
    assert client.post("/api/normen/bestaetigen", json={"normen": ["60079-0"], "von": "Dora"}) \
        .get_json()["bestaetigt"] == []
    assert client.post("/api/normen/bestaetigen", json={"normen": ["60079-99"], "von": "D"}) \
        .status_code == 400
    assert client.post("/api/normen/bestaetigen", json={"normen": ["60079-0"]}).status_code == 400


def test_regeln_pruefen_mit_name_im_protokoll(client):
    assert client.post("/api/regeln/pruefen", json={"von": "Eva"}).status_code == 200
    eintrag = client.get("/api/aenderungen?objekt=regeln").get_json()[0]
    assert (eintrag["von"], eintrag["feld"], eintrag["alt"]) == \
        ("Eva", "pruefen", {"dokument_ids": "alle"})


def test_94_9_je_dokumentart(app):
    """Erklärung nur mit 94/9/EG: Verdacht. Baumusterprüfbescheinigung nach 94/9/EG: gilt nach
    Art. 41 der Richtlinie 2014/34/EU weiter, nur ein neutraler Hinweis."""
    class Dok:
        lesbar, ocr_seiten, ocr_hinweis, ausstellungsdatum, gueltig_bis = True, None, None, \
            date(2020, 1, 1), None
        angaben = [Angabe(art="richtlinie", wert="94/9/EG", seite=2, quelle="pdf")]

    def regeln_fuer(doc_type):
        Dok.doc_type = doc_type
        return [b["regel"] for b in regeln.befunde(Dok, date(2026, 1, 1), 10, {})]

    assert regeln_fuer("eu_konformitaetserklaerung") == ["richtlinie_94_9"]
    assert regeln_fuer("bedienungsanleitung") == []  # Regel gilt nicht für Anleitungen
    assert regeln_fuer("sonstiges") == ["richtlinie_94_9"]
    assert regeln_fuer("eu_baumusterpruefbescheinigung") == []
    assert regeln.hinweise(Dok) == [
        "Die Bescheinigung wurde nach der alten Richtlinie 94/9/EG ausgestellt (Seite 2). Sie gilt "
        "nach Artikel 41 der Richtlinie 2014/34/EU weiter."]
    Dok.doc_type = "eu_konformitaetserklaerung"
    assert regeln.hinweise(Dok) == []
