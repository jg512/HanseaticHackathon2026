"""Schalter und Auswahl der schlanken Oberfläche: Sprachen je Vorgang (beim Start gewählt),
EX-Bauteil-Liste aus (EX_LISTE), Markierungen „veraltet“ aus (VERALTET_PRUEFUNG)."""

import pytest
from test_oberflaeche import artikel, gestartet, hochladen, leer, vorgang  # noqa: F401
from test_suche import VACF, VTUG_DE, VTUG_EN, pdf

from app.db import db
from app.models import Bom, Finding

ZEILE = [[20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"]]


@pytest.fixture
def ordner(client, tmp_path):
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf", VTUG_DE)
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_en.pdf", VTUG_EN)
    pdf(tmp_path / "Festo" / "Festo_KE_VACF-B-K1-1-5.pdf", VACF)
    client.application.config.update(ATEX_DOCS=str(tmp_path), OBERFLAECHE_SUCHE_SYNCHRON=True)
    return tmp_path


def start(client, vid, **daten):
    return client.post(f"/api/vorgaenge/{vid}/start", json=daten)


def test_nur_gewaehlte_sprachen_werden_gesucht(client, ordner):
    vid = hochladen(client, ZEILE)
    assert vorgang(client, vid)["sprachen"] == {
        "moeglich": [{"kuerzel": "de", "name": "Deutsch"}, {"kuerzel": "en", "name": "Englisch"},
                     {"kuerzel": "fr", "name": "Französisch"}],
        "gewaehlt": ["de", "en"]}
    assert start(client, vid, sprachen=["de"]).status_code == 200
    assert db.session.get(Bom, vid).sprachen == "de"
    assert [f.languages for f in db.session.scalars(db.select(Finding))] == ["de"]
    a = artikel(client, vid, 20)
    assert ("Ein gefundenes Dokument liegt nur in einer anderen Sprache vor als den gewählten "
            "(Deutsch) und ist deshalb nicht vorgeschlagen.") in a["gesucht_in"]
    assert vorgang(client, vid)["sprachen"]["gewaehlt"] == ["de"]
    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    assert any("die Sprachen für diesen Vorgang festgelegt: Deutsch." in t for t in texte)


def test_ohne_wahl_wie_bisher_alle_sprachen(client, ordner):
    vid = hochladen(client, ZEILE)
    assert start(client, vid).status_code == 200
    assert db.session.get(Bom, vid).sprachen is None
    assert sorted(f.languages for f in db.session.scalars(db.select(Finding))) == ["de", "en"]


def test_nur_englisch_dann_ist_englisch_die_pflichtsprache(client, ordner):
    vid = hochladen(client, ZEILE)
    assert start(client, vid, sprachen=["en"]).status_code == 200
    assert [f.languages for f in db.session.scalars(db.select(Finding))] == ["en"]
    assert not artikel(client, vid, 20)["deutsch_fehlt"]


@pytest.mark.parametrize("sprachen", [[], ["xx"], "de"])
def test_sprachwahl_wird_geprueft(client, ordner, sprachen):
    vid = hochladen(client, ZEILE)
    r = start(client, vid, sprachen=sprachen)
    assert r.status_code == 400 and r.get_json()["fehler"]
    assert db.session.get(Bom, vid).sprachen is None


def test_ohne_ex_liste_wartet_niemand_auf_die_ex_entscheidung(client, ordner):
    zeile = [[50, 99000050, "Linearführung", "Bosch", "LF-1", 1, "außerhalb Ex"]]
    vid = hochladen(client, zeile)
    assert vorgang(client, vid)["artikel"][0]["status"] == "info_fehlt"  # mit EX_LISTE
    client.application.config["EX_LISTE"] = False
    assert vorgang(client, vid)["artikel"][0]["status"] != "info_fehlt"


def test_offene_vorschlaege_in_anderer_sprache_fallen_bei_neuer_wahl_weg(client, ordner):
    vid = hochladen(client, ZEILE)
    start(client, vid)
    assert len(db.session.scalars(db.select(Finding)).all()) == 2
    start(client, vid, sprachen=["de"])
    assert [f.languages for f in db.session.scalars(db.select(Finding))] == ["de"]


# --- ältere Fassungen derselben Konformitätserklärung (suche.aeltere_fassungen) ---------------

def erklaerung(nummer, datum, richtlinie="2014/34/EU", typ="DSM-a-b-EX3-CS", sprache_de=True):
    kopf = f"1 ( 2 )\n{nummer}\n{datum}\n" if datum else ""
    text = ("EU-Konformitätserklärung\nDer Hersteller erklärt, dass das Produkt mit den "
            "Vorschriften der Richtlinie übereinstimmt und die Anforderungen für den Betrieb "
            "erfüllt werden, die in der Anlage stehen.\n" if sprache_de else
            "EU Declaration of conformity\nThe manufacturer declares that the product is in "
            "conformity with the directive and shall be used for the intended purpose of this "
            "equipment.\n")
    seite = f"{kopf}{text}Schwenkantrieb {typ}\n{richtlinie}\nII 2G Ex h IIC T4 Gb X"
    return [seite, f"{kopf}Anlage zur Erklärung {nummer}\n{typ}"]


DSM = [[50, 99000050, "Schwenkantrieb", "Festo", "DSM-…-EX", 1, "Zone 2"]]


def dsm_ordner(client, tmp_path, *dateien):
    for name, seiten in dateien:
        pdf(tmp_path / "Festo" / name, *seiten)
    client.application.config.update(ATEX_DOCS=str(tmp_path), OBERFLAECHE_SUCHE_SYNCHRON=True)


def dateien(vid):
    return sorted(f.file_path.split("/")[-1] for f in db.session.scalars(db.select(Finding)))


def test_von_zwei_fassungen_bleibt_die_neuere(client, tmp_path):
    dsm_ordner(client, tmp_path, ("dsm_2016.pdf", erklaerung("4637814", "2016-04-20")),
               ("dsm_2021.pdf", erklaerung("5629141", "2021-10-13")))
    vid = hochladen(client, DSM)
    start(client, vid)
    assert dateien(vid) == ["dsm_2021.pdf"]
    a = artikel(client, vid, 50)
    assert ("Eine ältere Fassung der Konformitätserklärung hat der Agent weggelassen, weil eine "
            "neuere vorliegt.") in a["gesucht_in"]
    start(client, vid)  # noch einmal suchen: die ältere kommt nicht zurück
    assert dateien(vid) == ["dsm_2021.pdf"]


def test_erklaerung_nach_alter_richtlinie_faellt_neben_einer_neuen_weg(client, tmp_path):
    dsm_ordner(client, tmp_path, ("dsm_alt.pdf", erklaerung("714757", None, "94/9/EG", "DSM")),
               ("dsm_2021.pdf", erklaerung("5629141", "2021-10-13")))
    vid = hochladen(client, DSM)
    start(client, vid)
    assert dateien(vid) == ["dsm_2021.pdf"]


def test_ohne_neuere_fassung_bleibt_auch_die_alte_stehen(client, tmp_path):
    # einzige Erklärung, nur alte Richtlinie: bleibt als Fund mit Warnung
    dsm_ordner(client, tmp_path, ("dsm_alt.pdf", erklaerung("714757", None, "94/9/EG", "DSM")))
    vid = hochladen(client, DSM)
    start(client, vid)
    assert dateien(vid) == ["dsm_alt.pdf"]


def test_andere_sprache_oder_anderer_typ_ist_keine_aeltere_fassung(client, tmp_path):
    dsm_ordner(client, tmp_path,
               ("dsm_de_2016.pdf", erklaerung("1", "2016-04-20")),
               ("dsm_en_2021.pdf", erklaerung("2", "2021-10-13", sprache_de=False)),
               ("dsm_b_2019.pdf", erklaerung("3", "2019-01-01", typ="DSM-x-y-EX9-ZZ")))
    vid = hochladen(client, DSM)
    start(client, vid)
    assert dateien(vid) == ["dsm_b_2019.pdf", "dsm_de_2016.pdf", "dsm_en_2021.pdf"]
