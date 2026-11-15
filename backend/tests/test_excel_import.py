import pytest
from conftest import KOPF, xlsx

from app.services.excel_import import ImportFehler, lese_stueckliste

GUT = [10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1A-1-EX4-M", 4, "Zone 1"]


def codes(ergebnis):
    return [(i["pos"], i["code"]) for i in ergebnis.issues]


def test_liest_meta_und_positionen():
    e = lese_stueckliste(xlsx([GUT]))
    assert e.meta == {"machine": "Abfüllanlage TM-1", "ex_area": "Innenraum Zone 1",
                      "required_category": "Zone 1: mindestens 2G"}
    assert e.items == [{"pos": "10", "profile_no": "99000010", "description": "Magnetspule",
                        "manufacturer": "Festo", "manufacturer_article_no": "VACF-B-K1-1A-1-EX4-M",
                        "quantity": "4", "location": "Zone 1", "row_number": 7}]
    assert e.issues == []


def test_meldet_fehlende_angaben():
    e = lese_stueckliste(xlsx([[280, 99000280, "Linearführung", None, None, 2, None],
                               [290, None, "Ohne Nummer", "Festo", "X-1", 1, "Zone 1"]]))
    assert codes(e) == [("280", "hersteller_fehlt"), ("280", "herstellerartikelnr_fehlt"),
                        ("280", "einbauort_fehlt"), ("290", "profile_no_fehlt")]
    assert [i["severity"] for i in e.issues] == ["warning", "warning", "info", "error"]


def test_meldet_doppelte_nummer():
    doppelt = [290] + GUT[1:]
    e = lese_stueckliste(xlsx([GUT, doppelt]))
    assert codes(e) == [("290", "profile_no_doppelt")]
    assert "Position 10" in e.issues[0]["message"]


def test_lehnt_falsches_schema_ab():
    with pytest.raises(ImportFehler, match="fehlt die Spalte „Einbauort“"):
        lese_stueckliste(xlsx([GUT[:-1]], kopf=KOPF[:-1]))
    with pytest.raises(ImportFehler, match="Kopfzeile"):
        lese_stueckliste(xlsx([GUT], kopf=["a", "b"]))
    with pytest.raises(ImportFehler, match="keine Positionen"):
        lese_stueckliste(xlsx([]))
    with pytest.raises(ImportFehler, match="keine lesbare Excel-Datei"):
        lese_stueckliste(b"kein excel")
