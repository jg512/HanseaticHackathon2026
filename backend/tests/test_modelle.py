"""Sprachmodelle aus der Datei (services/modelle.py), Test-Endpunkt, Einstellungen je Vorgang."""

import io
import logging
import os
from types import SimpleNamespace

import litellm
import pytest
from conftest import xlsx

from app import create_app, llm
from app.db import db
from app.models import Bom
from app.models.bom import BomEvent
from app.services import internet, modelle, oberflaeche, suche

GEHEIM = "sk-test-GEHEIM-0123456789abcdef"
ZWEI = f"""
modelle:
  - name: beispiel-chat
    art: chat
    modell: openai/beispiel-modell
    api_base: https://llm.example/v1
    api_key: "-"
    timeout: 60
  - name: claude
    modell: anthropic/claude-sonnet-5-5
    api_key: "{GEHEIM}"
    timeout: 30
  - name: beispiel-embedding
    art: embedding
    modell: openai/qwen3-embedding-4b
    api_base: https://llm.example/v1
    api_key: "-"
standard:
  websuche: beispiel-chat
  email: beispiel-chat
  rag_antwort: beispiel-chat
  rag_embedding: beispiel-embedding
"""


def antwort(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture
def datei(client, tmp_path):
    """Schreibt die Modell-Datei und stellt MODELLE_DATEI darauf."""
    pfad = tmp_path / "modelle.yaml"

    def schreibe(text):
        alt = pfad.stat().st_mtime_ns if pfad.exists() else 0
        pfad.write_text(text, encoding="utf-8")
        os.utime(pfad, ns=(alt + 10**9, alt + 10**9))  # neuer Zeitstempel auch im selben Tick
        client.application.config["MODELLE_DATEI"] = str(pfad)
        return pfad
    return schreibe


@pytest.fixture
def aufrufe(monkeypatch):
    liste = []
    monkeypatch.setattr(litellm, "completion", lambda **kw: liste.append(kw) or antwort("OK"))
    return liste


def vorgang(llm_modell=None, versuche=None):
    bom = Bom(source_file_name="x.xlsx", source_file_hash="h", machine="M",
              llm_modell=llm_modell, internet_versuche=versuche)
    db.session.add(bom)
    db.session.commit()
    return bom


def texte(bom_id):
    return [e.text for e in db.session.scalars(
        db.select(BomEvent).filter_by(bom_id=bom_id).order_by(BomEvent.id))]


# Datei lesen und prüfen


def test_datei_mit_mehreren_modellen(client, datei):
    datei(ZWEI)
    b = modelle.bestand()
    assert list(b.modelle) == ["beispiel-chat", "claude", "beispiel-embedding"]
    assert b.modelle["claude"].art == "chat" and b.modelle["claude"].anbieter == "anthropic"
    assert b.standard == {"websuche": "beispiel-chat", "email": "beispiel-chat",
                          "rag_antwort": "beispiel-chat", "rag_embedding": "beispiel-embedding"}
    assert GEHEIM not in repr(b.modelle["claude"])


def test_fehlender_standard_nimmt_das_erste_modell_der_art(client, datei):
    datei(ZWEI.split("standard:")[0] + "standard:\n  email: claude\n")
    assert modelle.bestand().standard == {"websuche": "beispiel-chat", "email": "claude",
                                          "rag_antwort": "beispiel-chat",
                                          "rag_embedding": "beispiel-embedding"}


M = "modelle:\n  - name: a\n    modell: openai/x\n    api_key: \"-\"\n"


@pytest.mark.parametrize("text, meldung", [
    ("- a\n- b\n", "oben müssen"),
    (M + "extra: 1\n", "unbekannter Eintrag „extra“"),
    ("modelle: []\n", "Liste der Modelle"),
    (M + "    farbe: rot\n", "unbekannter Eintrag „farbe“"),
    ("modelle:\n  - modell: openai/x\n    api_key: \"-\"\n", "„name“ fehlt"),
    (M + M.replace("modelle:\n", ""), "kommt zweimal vor"),
    (M + "    art: bild\n", "chat oder embedding"),
    ("modelle:\n  - name: a\n    modell: gemma\n    api_key: \"-\"\n", "anbieter/modell"),
    (M + "    api_key_env: X\n", "nicht beides"),
    ("modelle:\n  - name: a\n    modell: openai/x\n", "kein Zugang angegeben"),
    ("modelle:\n  - name: a\n    modell: openai/x\n    api_key_env: ATEX_FEHLT_SICHER\n",
     "ATEX_FEHLT_SICHER (api_key_env) ist nicht gesetzt"),
    (M + "    timeout: -1\n", "„timeout“"),
    (M + "standard:\n  websuche: b\n", "standard.websuche nennt „b“"),
    (M + "  - name: e\n    art: embedding\n    modell: openai/e\n    api_key: \"-\"\n"
     "standard:\n  websuche: e\n", "braucht ein Modell mit „art: chat“"),
    (M + "standard:\n  zeichnen: a\n", "unbekannte Aufgabe „standard.zeichnen“"),
    ("modelle:\n  - name: e\n    art: embedding\n    modell: openai/e\n    api_key: \"-\"\n",
     "kein Modell mit „art: chat“"),
    (f"modelle:\n  - name: a\n    api_key: \"{GEHEIM}\"\n  modell: [\n", "kein gültiges YAML"),
])
def test_fehler_in_der_datei(client, datei, text, meldung):
    pfad = datei(text)
    with pytest.raises(modelle.KonfigFehler) as e:
        modelle.bestand()
    assert meldung in str(e.value) and str(pfad) in str(e.value)
    assert GEHEIM not in str(e.value)
    # auch in der Schnittstelle, und kein Modell still als Ersatz
    r = client.get("/api/modelle")
    assert r.status_code == 503 and meldung in r.get_json()["detail"]
    assert r.get_json()["error"].startswith("Die Sprachmodelle sind falsch eingerichtet.")
    with pytest.raises(modelle.KonfigFehler):
        llm.chat([{"role": "user", "content": "Hi"}])


def test_schluessel_aus_umgebungsvariable(client, datei, monkeypatch):
    monkeypatch.setenv("ATEX_TEST_KEY", GEHEIM)
    datei("modelle:\n  - name: c\n    modell: anthropic/claude-haiku-5-5\n"
          "    api_key_env: ATEX_TEST_KEY\n")
    m = modelle.bestand().modelle["c"]
    assert m.api_key == GEHEIM and m.zugang == "env:ATEX_TEST_KEY"
    eintrag = client.get("/api/modelle").get_json()["modelle"][0]
    assert eintrag["zugang_hinterlegt"] and eintrag["zugang_aus"] == "env:ATEX_TEST_KEY"


def test_ohne_datei_ein_modell_aus_den_alten_variablen(client, aufrufe, tmp_path):
    client.application.config["MODELLE_DATEI"] = str(tmp_path / "fehlt.yaml")
    b = modelle.bestand()
    assert b.quelle == "umgebung" and list(b.modelle) == ["standard"]
    assert b.standard["websuche"] == "standard" and b.standard["rag_embedding"] is None
    llm.chat([{"role": "user", "content": "Hi"}])
    assert aufrufe[0]["model"] == "openai/gpt-4o-mini"
    d = client.get("/api/modelle").get_json()
    assert d["quelle"] == "umgebung" and d["modelle"][0]["standard_fuer"] == [
        "websuche", "email", "rag_antwort"]


def test_neues_modell_ohne_neustart(client, datei):
    datei(M)
    assert list(modelle.bestand().modelle) == ["a"]
    datei(M + "  - name: b\n    modell: openai/y\n    api_key: \"-\"\n")
    assert list(modelle.bestand().modelle) == ["a", "b"]
    datei("kaputt: [\n")
    with pytest.raises(modelle.KonfigFehler):
        modelle.bestand()
    datei(M)  # repariert: wieder nutzbar
    assert list(modelle.bestand().modelle) == ["a"]


def test_fehlerhafte_datei_beim_start_im_log(tmp_path, caplog):
    pfad = tmp_path / "modelle.yaml"
    pfad.write_text(M + "    farbe: rot\n")
    with caplog.at_level(logging.ERROR):
        create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "MODELLE_DATEI": str(pfad)})
    assert "unbekannter Eintrag „farbe“" in caplog.text


# Schlüssel bleiben auf dem Server


def test_liste_ohne_schluessel(client, datei):
    datei(ZWEI)
    r = client.get("/api/modelle")
    assert GEHEIM not in r.get_data(as_text=True)
    d = r.get_json()
    claude = next(m for m in d["modelle"] if m["name"] == "claude")
    assert claude == {"name": "claude", "art": "chat", "modell": "anthropic/claude-sonnet-5-5",
                      "anbieter": "anthropic", "api_base": None, "zugang_hinterlegt": True,
                      "ohne_schluessel": False, "zugang_text": "Schlüssel hinterlegt",
                      "zugang_aus": "Datei", "timeout": 30,
                      "standard_fuer": []}
    gemma = d["modelle"][0]
    assert gemma["ohne_schluessel"] and gemma["standard_fuer"] == ["websuche", "email",
                                                                   "rag_antwort"]
    assert d["sprachmodell_genutzt"] == {"websuche": False, "email": False}
    assert d["hinweise"][0].startswith("Die Websuche nutzt derzeit kein Sprachmodell.")


def test_fehler_des_anbieters_ohne_schluessel(client, datei, monkeypatch, caplog):
    datei(ZWEI)

    def lehnt_ab(**kw):
        raise RuntimeError(f"AuthenticationError: invalid x-api-key {kw['api_key']}")
    monkeypatch.setattr(litellm, "completion", lehnt_ab)
    with pytest.raises(llm.LLMFehler) as e:
        llm.chat([{"role": "user", "content": "Hi"}], modell="claude")
    assert GEHEIM not in str(e.value) and "claude" in str(e.value)
    r = client.post("/api/modelle/claude/test")
    assert r.status_code == 200 and GEHEIM not in r.get_data(as_text=True)
    d = r.get_json()
    assert d["erreichbar"] is False and "AuthenticationError" in d["detail"]
    assert d["fehler"].startswith("Der Anbieter hat den Zugang abgelehnt.")
    assert GEHEIM not in caplog.text


def test_bereinige_entfernt_typische_muster(client, datei):
    datei(ZWEI)
    text = modelle.bereinige(f"key {GEHEIM}, Bearer abcdefghijk, api_key=geheim123 rest")
    assert GEHEIM not in text and "abcdefghijk" not in text and "geheim123" not in text
    assert text.endswith("rest")


# Verbindung testen


def test_verbindung_testen(client, datei, aufrufe, monkeypatch):
    datei(ZWEI)
    d = client.post("/api/modelle/beispiel-chat/test").get_json()
    assert d["erreichbar"] and d["antwort"] == "OK" and isinstance(d["dauer_ms"], int)
    assert aufrufe[0]["model"] == "openai/beispiel-modell" and aufrufe[0]["api_key"] == "-"
    assert aufrufe[0]["api_base"] == "https://llm.example/v1"

    monkeypatch.setattr(litellm, "embedding", lambda **kw: SimpleNamespace(
        data=[{"embedding": [0.1] * 8}]))
    d = client.post("/api/modelle/beispiel-embedding/test").get_json()
    assert d["erreichbar"] and d["antwort"] == "Vektor mit 8 Werten"
    assert client.post("/api/modelle/gibts-nicht/test").status_code == 404


# Einstellungen je Vorgang


def test_einstellungen_lesen_und_aendern(client, datei):
    datei(ZWEI)
    client.application.config.update(INTERNET_LLM=True, EMAIL_MIT_LLM=True,
                                     INTERNET_VERSUCHE_PRO_TEIL=10)
    bom = vorgang()
    d = client.get(f"/api/vorgaenge/{bom.id}/einstellungen").get_json()
    assert d["modell"] is None and d["standard"] == {"websuche": "beispiel-chat",
                                                     "email": "beispiel-chat"}
    assert [m["name"] for m in d["modell_auswahl"]] == ["beispiel-chat", "claude"]
    assert d["modell_wirksam"] == {"websuche": "beispiel-chat", "email": "beispiel-chat"}
    assert (d["internet_versuche"], d["internet_versuche_standard"],
            d["internet_versuche_max"], d["internet_versuche_wirksam"]) == (None, 10, 50, 10)
    assert d["hinweise"] == [] and not d["laeuft"]

    r = client.put(f"/api/vorgaenge/{bom.id}/einstellungen",
                   json={"von": "Jan", "modell": "claude", "internet_versuche": 3})
    assert r.status_code == 200
    d = r.get_json()
    assert d["modell"] == "claude" and d["modell_wirksam"]["email"] == "claude"
    assert d["internet_versuche"] == 3 and d["internet_versuche_wirksam"] == 3
    assert texte(bom.id) == [
        "Jan hat für diesen Vorgang das Sprachmodell „claude“ gewählt.",
        "Jan hat die Websuche für diesen Vorgang auf höchstens 3 Versuche je Position gestellt."]
    # 0 = keine Websuche: dann fragt sie auch kein Modell
    d = client.put(f"/api/vorgaenge/{bom.id}/einstellungen",
                   json={"by": "Eva", "internet_versuche": 0}).get_json()
    assert d["internet_versuche_wirksam"] == 0 and d["modell_wirksam"]["websuche"] is None
    # leer = wieder Standard
    client.put(f"/api/vorgaenge/{bom.id}/einstellungen",
               json={"von": "Jan", "modell": None, "internet_versuche": None})
    assert texte(bom.id)[-3:] == [
        "Eva hat die Websuche für diesen Vorgang abgeschaltet.",
        "Jan hat für diesen Vorgang wieder das Standard-Sprachmodell „beispiel-chat“ eingestellt.",
        "Jan hat für die Websuche dieses Vorgangs wieder den Standard eingestellt, das heißt "
        "10 Versuche je Position."]
    db.session.refresh(bom)
    assert bom.llm_modell is None and bom.internet_versuche is None


@pytest.mark.parametrize("daten, code, meldung", [
    ({"modell": "gpt-9"}, 400, "„gpt-9“ ist nicht eingerichtet. Bitte wählen Sie eines aus "
                               "der Liste: beispiel-chat, claude."),
    ({"modell": "beispiel-embedding"}, 400, "kann keine Texte schreiben"),
    ({"modell": 5}, 400, "kennt der Agent nicht"),
    ({"internet_versuche": -1}, 400, "ganze Zahl von 0 bis 50"),
    ({"internet_versuche": 51}, 400, "ganze Zahl von 0 bis 50"),
    ({"internet_versuche": "3"}, 400, "ganze Zahl von 0 bis 50"),
    ({"internet_versuche": True}, 400, "ganze Zahl von 0 bis 50"),
    ({}, 400, "nichts zu speichern"),
])
def test_einstellungen_pruefen_die_eingabe(client, datei, daten, code, meldung):
    datei(ZWEI)
    bom = vorgang()
    r = client.put(f"/api/vorgaenge/{bom.id}/einstellungen", json={"von": "Jan", **daten})
    assert r.status_code == code and meldung in r.get_json()["error"]
    assert texte(bom.id) == []


def test_einstellungen_brauchen_namen_und_ruhe(client, datei):
    datei(ZWEI)
    bom = vorgang()
    r = client.put(f"/api/vorgaenge/{bom.id}/einstellungen", json={"modell": "claude"})
    assert r.status_code == 400 and "Ohne Anmeldung wird nichts gespeichert" in r.get_json()["error"]
    oberflaeche._lauf[bom.id] = {"item_id": None, "suche_in": None}
    try:
        r = client.put(f"/api/vorgaenge/{bom.id}/einstellungen",
                       json={"von": "Jan", "modell": "claude"})
        assert r.status_code == 409
        assert client.get(f"/api/vorgaenge/{bom.id}/einstellungen").get_json()["laeuft"]
    finally:
        oberflaeche._lauf.pop(bom.id, None)
    assert client.get("/api/vorgaenge/999/einstellungen").status_code == 404


def test_ohne_schalter_sagt_es_ehrlich_dass_kein_modell_wirkt(client, datei):
    datei(ZWEI)
    bom = vorgang("claude")
    d = client.get(f"/api/vorgaenge/{bom.id}/einstellungen").get_json()
    assert d["sprachmodell_genutzt"] == {"websuche": False, "email": False}
    assert d["modell_wirksam"] == {"websuche": None, "email": None}
    assert any("Websuche nutzt derzeit kein Sprachmodell" in h for h in d["hinweise"])
    assert modelle.vermerk(bom, 10) == ". Der Agent arbeitet ohne Sprachmodell."


def test_entferntes_modell_wird_gemeldet_und_nicht_ersetzt(client, datei, aufrufe):
    datei(ZWEI)
    client.application.config.update(INTERNET_LLM=True)
    bom = vorgang("claude")
    datei(ZWEI.replace("name: claude", "name: claude-alt"))
    d = client.get(f"/api/vorgaenge/{bom.id}/einstellungen").get_json()
    assert d["modell_fehler"].startswith("Das gewählte Sprachmodell „claude“ steht nicht mehr "
                                         "zur Verfügung.")
    modelle.fuer_lauf(bom)
    with pytest.raises(modelle.KonfigFehler):
        llm.chat([{"role": "user", "content": "Hi"}])
    assert aufrufe == []
    assert "„claude“ steht nicht mehr zur Verfügung" in modelle.vermerk(bom, 10)


# Die Wahl erreicht den Aufruf


def test_wahl_des_vorgangs_erreicht_die_websuche(client, datei, aufrufe):
    datei(ZWEI)
    bom = vorgang("claude")
    kandidaten = [internet.Kandidat((0,), "https://festo.com/a.pdf", "Konformität")]
    item = SimpleNamespace(manufacturer="Festo", manufacturer_article_no="X", description="Y")
    internet.waehle_mit_llm(item, kandidaten)
    assert aufrufe[-1]["model"] == "openai/beispiel-modell"  # ohne Lauf: Standard der Websuche
    assert modelle.fuer_lauf(bom) == 0  # conftest: INTERNET_VERSUCHE_PRO_TEIL 0
    internet.waehle_mit_llm(item, kandidaten)
    assert aufrufe[-1]["model"] == "anthropic/claude-sonnet-5-5"
    assert aufrufe[-1]["api_key"] == GEHEIM and aufrufe[-1]["timeout"] == 30
    assert modelle.name_fuer("rag_antwort") == "claude"
    assert modelle.name_fuer("rag_embedding") == "beispiel-embedding"


ZEILEN = [[30, 99000030, "Schalter", "Rittal", "SZ-123", 1, "Zone 1"]]


def test_lauf_der_oberflaeche_nutzt_modell_und_versuche_des_vorgangs(client, datei, aufrufe,
                                                                      monkeypatch, tmp_path):
    datei(ZWEI)
    (tmp_path / "docs").mkdir()
    client.application.config.update(ATEX_DOCS=str(tmp_path / "docs"), EMAIL_MIT_LLM=True,
                                     INTERNET_LLM=True, OBERFLAECHE_SUCHE_SYNCHRON=True,
                                     INTERNET_VERSUCHE_PRO_TEIL=10)
    monkeypatch.setattr(litellm, "completion", lambda **kw: aufrufe.append(kw) or antwort(
        "Sehr geehrte Damen und Herren, wir bitten um die Ex-Dokumente zu SZ-123 von Rittal."))
    erhalten = []
    echt = suche.suche_vorgang

    def suche_ohne_internet(bom, versuche=None, **weiteres):
        erhalten.append(versuche)
        return echt(bom, 0, **weiteres)
    monkeypatch.setattr(suche, "suche_vorgang", suche_ohne_internet)

    r = client.post("/api/vorgaenge", data={"datei": (io.BytesIO(xlsx(ZEILEN)), "s.xlsx")},
                    content_type="multipart/form-data")
    vid = r.get_json()["id"]
    client.put(f"/api/vorgaenge/{vid}/einstellungen",
               json={"von": "Jan", "modell": "claude", "internet_versuche": 3})
    assert client.post(f"/api/vorgaenge/{vid}/start").status_code == 200

    assert erhalten == [3]
    assert aufrufe and {a["model"] for a in aufrufe} == {"anthropic/claude-sonnet-5-5"}
    t = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    start = next(x for x in t if x.startswith("Der Agent startet"))
    assert start == ("Der Agent startet mit 1 Position. Im Internet ruft er je Position höchstens "
                     "3 Seiten ab. Der Agent nutzt das Sprachmodell „claude“, das für diesen "
                     "Vorgang gewählt ist.")
    assert ("Position 30: Die Einleitung des E-Mail-Entwurfs hat das Sprachmodell „claude“ "
            "geschrieben.") in t


def test_andere_suchwege_nehmen_die_versuche_des_vorgangs(client, datei, monkeypatch, tmp_path):
    datei(ZWEI)
    (tmp_path / "docs").mkdir()
    client.application.config.update(ATEX_DOCS=str(tmp_path / "docs"),
                                     INTERNET_VERSUCHE_PRO_TEIL=10)
    from app.routes import korrektur as korrektur_route
    from app.routes import suche as suche_route
    erhalten = []
    monkeypatch.setattr(suche_route, "suche_vorgang",
                        lambda bom, v=None: erhalten.append(("alt", v, modelle.lauf_modell())))
    monkeypatch.setattr(korrektur_route.korrektur, "suche_korrigierte",
                        lambda bom, ids, v=None: erhalten.append(("korrektur", v)) or [])
    bom = vorgang("claude", 4)
    client.post(f"/api/boms/{bom.id}/search")
    client.post(f"/api/boms/{bom.id}/search", json={"internet_versuche": 7})  # Angabe gewinnt
    client.post(f"/api/boms/{bom.id}/korrektur/suche")
    assert erhalten == [("alt", 4, "claude"), ("alt", 7, "claude"), ("korrektur", 4)]
