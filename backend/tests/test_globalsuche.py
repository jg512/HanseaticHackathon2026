"""Suchfeld oben: GET /api/suche über Vorgänge, Positionen, Stammdaten, Hersteller und das
Dokumentenarchiv (services/globalsuche.py)."""

import httpx
import pytest
from test_auth import anmelden, app  # noqa: F401  (Fixture mit Anmeldung)
from test_oberflaeche import ZEILEN, hochladen, ordner  # noqa: F401

from app.db import db
from app.models import Document, Manufacturer, Part
from app.services import globalsuche


@pytest.fixture(autouse=True)
def archiv_vergessen(monkeypatch):
    """Die Liste des Archivs ist modulweit gemerkt: je Test frisch."""
    monkeypatch.setattr(globalsuche, "_archiv", {"zeit": 0.0, "liste": None, "weg_seit": 0.0})


def suche(client, q, **mehr):
    r = client.get("/api/suche", query_string={"q": q, **mehr})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def gruppe(ergebnis, art):
    return next((g for g in ergebnis["gruppen"] if g["art"] == art), None)


def test_norm_und_woerter():
    assert globalsuche.norm("VTUG-10-VI-EX2E") == "vtug10viex2e"
    assert globalsuche.woerter("  Festo   vtug-10 ") == ["festo", "vtug10"]
    assert globalsuche.woerter("- / .") == []


def test_vorgang_nach_name_maschine_und_datei(client, ordner):
    vid = hochladen(client, name="Linie Nord")
    for q in ("linie nord", "TM-1", "abfüllanlage", "liste.xlsx", "LISTE"):
        g = gruppe(suche(client, q), "vorgaenge")
        assert g and g["treffer"][0]["ziel"] == f"#/v/{vid}", q
    t = gruppe(suche(client, "Linie"), "vorgaenge")["treffer"][0]
    assert t["titel"] == "Linie Nord"
    assert "Maschine Abfüllanlage TM-1" in t["kontext"] and "Stückliste liste.xlsx" in t["kontext"]


def test_position_ohne_trennzeichen_und_ohne_gross_klein(client, ordner):
    vid = hochladen(client)
    e = suche(client, "vtug10")
    g = gruppe(e, "positionen")
    assert g["anzahl"] == 1
    [t] = g["treffer"]
    aid = next(i for i in client.get(f"/api/vorgaenge/{vid}").get_json()["artikel"]
               if i["pos"] == "20")["id"]
    assert t["ziel"] == f"#/v/{vid}/a/{aid}"
    assert t["titel"] == "Position 20: Ventilinsel"
    assert "VTUG-10-VI-EX2E" in t["kontext"] and "PRO.FILE 99000020" in t["kontext"]
    assert "Vorgang „Abfüllanlage Test“" in t["kontext"]
    # auch über PRO.FILE-Nr., Hersteller und Bezeichnung
    assert gruppe(suche(client, "99000030"), "positionen")["treffer"][0]["titel"] == \
        "Position 30: Schalter"
    assert gruppe(suche(client, "RITTAL"), "positionen")["anzahl"] == 1
    assert gruppe(suche(client, "linearführung"), "positionen")["anzahl"] == 1


def test_mehrere_woerter_muessen_alle_passen(client, ordner):
    hochladen(client)
    assert gruppe(suche(client, "festo ventilinsel"), "positionen")["anzahl"] == 1
    assert gruppe(suche(client, "festo"), "positionen")["anzahl"] == 3
    assert gruppe(suche(client, "rittal ventilinsel"), "positionen") is None


def test_grenze_je_gruppe_und_gesamtzahl(client, ordner):
    hochladen(client)
    hochladen(client, name="Zweiter")
    e = suche(client, "festo", je=2)
    g = gruppe(e, "positionen")
    assert g["anzahl"] == 6 and len(g["treffer"]) == 2
    assert e["anzahl"] == sum(x["anzahl"] for x in e["gruppen"])
    # der genaue Treffer zuerst: „VTUG-10-VI-EX2E“ ist gleich, nicht nur enthalten
    g = gruppe(suche(client, "vtug-10-vi-ex2e"), "positionen")
    assert all("VTUG-10-VI-EX2E" in t["kontext"] for t in g["treffer"])
    assert client.get("/api/suche?q=festo&je=x").status_code == 400


def test_zu_kurz_und_leer(client, ordner):
    hochladen(client)
    for q in ("", "a", " - "):
        e = suche(client, q)
        assert (e["zu_kurz"], e["gruppen"], e["anzahl"]) == (True, [], 0)
    e = suche(client, "gibtesnicht")
    assert (e["zu_kurz"], e["gruppen"], e["anzahl"]) == (False, [], 0)
    assert "dauer_ms" in e


def test_stammdaten_und_hersteller(client, ordner):
    hochladen(client)
    m = Manufacturer(name="Pepperl+Fuchs", normalized_name="pepperlfuchs")
    db.session.add(m)
    db.session.flush()
    db.session.add(Part(profile_no="99000140", name_de="Trennschaltverstärker", manufacturer=m,
                        manufacturer_article_no="KCD2-SR-Ex1.LB", changed_by="t"))
    db.session.add(Document(doc_type="eu_konformitaetserklaerung",
                            file_path="P+F/P+F_Konf_KCD2_de.pdf",
                            certificate_no="BASEEFA 13 ATEX 0080", changed_by="t"))
    db.session.commit()
    b = gruppe(suche(client, "kcd2srex1lb"), "bauteile")
    assert b["treffer"][0]["titel"] == "Bauteil 99000140: Trennschaltverstärker"
    assert b["treffer"][0]["ziel"].startswith("#/stammdaten/teil/")
    d = gruppe(suche(client, "baseefa13atex0080"), "dokumente")
    assert d["treffer"][0]["titel"] == "P+F_Konf_KCD2_de.pdf"
    assert "EU-Konformitätserklärung" in d["treffer"][0]["kontext"]
    assert d["treffer"][0]["ziel"].startswith("#/stammdaten/dokument/")
    # Hersteller: aus den Stammdaten und die, die nur in einer Stückliste stehen
    h = gruppe(suche(client, "pepperl"), "hersteller")
    assert h["treffer"][0]["kontext"] == "Noch keine E-Mail-Adresse hinterlegt"
    h = gruppe(suche(client, "rittal"), "hersteller")
    assert h["treffer"][0]["ziel"] == "#/stammdaten/hersteller"
    assert "bisher nur in einer Stückliste" in h["treffer"][0]["kontext"]


def test_ohne_archiv_keine_gruppe(client, ordner):
    hochladen(client)
    assert client.application.config["ZERTIFIKATSSERVER_URL"] == ""
    assert gruppe(suche(client, "festo"), "archiv") is None


def test_archiv_weg_die_anderen_gruppen_bleiben(client, ordner, monkeypatch):
    hochladen(client)
    client.application.config["ZERTIFIKATSSERVER_URL"] = "http://zert:5100"
    aufrufe = []

    def weg(*a, **k):
        aufrufe.append(a)
        raise httpx.ConnectError("weg")
    monkeypatch.setattr(globalsuche.httpx, "get", weg)
    e = suche(client, "festo")
    a = gruppe(e, "archiv")
    assert a["anzahl"] == 0 and a["treffer"] == []
    assert a["hinweis"].startswith("Das Dokumentenarchiv ist gerade nicht erreichbar.")
    assert gruppe(e, "positionen")["anzahl"] == 3
    suche(client, "festo")  # ein Archiv, das nicht antwortet, wird kurz nicht mehr gefragt
    assert len(aufrufe) == 1


def test_archiv_treffer(client, ordner, monkeypatch):
    client.application.config["ZERTIFIKATSSERVER_URL"] = "http://zert:5100"
    liste = [{"id": 7, "dateiname": "Festo_VTUG_de.pdf", "hersteller": "Festo",
              "doc_type": "eu_konformitaetserklaerung", "ablage": "aktuell",
              "ausgemustert": False, "teile": [{"artikelnr": "VTUG-10-VI-EX2E"}]},
             {"id": 8, "dateiname": "Rittal_alt.pdf", "hersteller": "Rittal",
              "doc_type": "sonstiges", "ablage": "archiv", "ablage_text": "Im Archiv",
              "ausgemustert": False, "teile": []}]
    aufrufe = []

    def get(url, timeout):
        aufrufe.append(url)
        return httpx.Response(200, json=liste, request=httpx.Request("GET", url))
    monkeypatch.setattr(globalsuche.httpx, "get", get)
    a = gruppe(suche(client, "vtug10"), "archiv")
    assert a["anzahl"] == 1 and a["treffer"][0]["ziel"] == "#/zertifikate/dok/7"
    assert a["treffer"][0]["titel"] == "Festo_VTUG_de.pdf"
    r = gruppe(suche(client, "rittal"), "archiv")["treffer"][0]
    assert r["kontext"] == "Rittal · Sonstiges · Im Archiv"
    assert aufrufe == ["http://zert:5100/api/dokumente"]  # die Liste bleibt kurz gemerkt


def test_braucht_anmeldung_und_leser_darf_suchen(app):  # noqa: F811
    assert app.test_client().get("/api/suche?q=festo").status_code == 401
    lea = anmelden(app, "lea")
    r = lea.get("/api/suche?q=festo")
    assert r.status_code == 200 and r.get_json()["zu_kurz"] is False
