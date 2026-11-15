from app import auslesen

from conftest import ALT_94, NEU_2014, pdf


def angaben(a, art):
    return [x for x in a.angaben if x["art"] == art]


def test_alte_erklaerung_mit_seiten():
    a = auslesen.auswerten(pdf("Deckblatt", ALT_94))
    assert a.lesbar and a.doc_type == "eu_konformitaetserklaerung"
    assert [(x["wert"], x["seite"]) for x in angaben(a, "richtlinie")] == [("94/9/EG", 2)]
    assert [(x["wert"], x["seite"]) for x in angaben(a, "norm")] == [("60079-0:2009", 2)]
    # das Datum der Richtlinie („vom 23. März 1994“) zählt nicht
    datum = angaben(a, "ausstellungsdatum")[0]
    assert (datum["wert"], datum["seite"], datum["sicher"]) == ("2005-05-12", 2, True)
    assert "12.05.2005" in datum["textstelle"]


def test_neue_erklaerung():
    a = auslesen.auswerten(pdf(NEU_2014))
    assert [x["wert"] for x in angaben(a, "richtlinie")] == ["2014/34/EU"]
    assert [x["wert"] for x in angaben(a, "norm")] == ["60079-0:2018"]
    assert str(a.ausstellungsdatum) == "2022-08-09"


def test_sprachen_mit_seite():
    a = auslesen.auswerten(pdf("Die Erklärung ist für das Gerät und wird mit der Anleitung "
                               "geliefert, nicht ohne den Hersteller.",
                               "The declaration is for the device and shall be used with the "
                               "manual; this is valid for all types."))
    assert [(x["wert"], x["seite"]) for x in angaben(a, "sprache")] == [("de", 1), ("en", 2)]


def test_spaetestes_datum_mit_stichwort():
    a = auslesen.auswerten(pdf("EC-Type Examination Certificate\nKEMA 04ATEX1244\n"
                               "Issue 3 of 17 February 2020, previous 1 March 2010"))
    assert a.doc_type == "eu_baumusterpruefbescheinigung"
    datum = angaben(a, "ausstellungsdatum")[0]
    assert (datum["wert"], datum["sicher"]) == ("2020-02-17", True)
    assert [x["wert"] for x in angaben(a, "zertifikatsnummer")] == ["KEMA 04ATEX1244"]


def test_datum_ohne_stichwort_ist_unsicher():
    a = auslesen.auswerten(pdf("Zertifikat\nPTB 00 ATEX 2048 X\n\n\n2010-03-01 und 2020-02-17"))
    datum = angaben(a, "ausstellungsdatum")[0]
    assert (datum["wert"], datum["sicher"]) == ("2020-02-17", False)
    assert "spätestes Datum" in datum["hinweis"]


def test_gueltig_bis():
    a = auslesen.auswerten(pdf("Certificate\nDate of issue: 2019-04-29\nValid until: 2021-04-28"))
    assert str(a.gueltig_bis) == "2021-04-28"
    assert angaben(a, "gueltig_bis")[0]["seite"] == 1


def test_scan_wird_nicht_geraten():
    a = auslesen.auswerten(pdf("", ""))
    assert not a.lesbar and a.doc_type == "unbekannt" and a.angaben == []
    assert a.seiten_ohne_text == [1, 2]


def test_kaputte_datei():
    a = auslesen.auswerten(b"%PDF-1.4 kaputt")
    assert not a.lesbar and a.seiten == []


def typen(name, *seiten, hersteller=("Festo",)):
    return [(t, s) for t, _, s in auslesen.typen([name], list(seiten), hersteller)]


def test_typ_aus_dateinamen():
    assert typen("Festo_SMT-8F-I-8,2V-K5,0-OE-EX_Konf.pdf") == [("SMT-8F-I-8,2V-K5,0-OE-EX", None)]
    assert [t for t, _ in typen(
        "Festo_MFH_MOFH_JMFH_JMFHD_Konformit#U00e4tserkl#U00e4rung_de_en_fr.pdf")] == \
        ["MFH", "MOFH", "JMFH", "JMFHD"]
    assert typen("Rittal_PTB02ATEX1082U_KEL_94XX_de.pdf", hersteller=("Rittal",)) == \
        [("KEL 94XX", None)]
    assert typen("Festo_Druckaufbauventil_MS-EX4-KonfErkl.pdf") == [("MS-EX4", None)]


def test_keine_typen_aus_zertifikatsnummer_und_woertern():
    assert typen("Festo_IBExU16ATEX1146X_de_en.pdf", "IBExU 16 ATEX 1146 X") == []
    assert typen("festo_spule_konform_atex.pdf", "Spule für ATEX, Festo") == []


def test_kuerzel_vor_dokumentnummer_ist_kein_typ():
    # „EU_5629123“ ist Kürzel und Dokumentnummer, „KEL_94XX“ dagegen ein Typ
    assert typen("Festo_dnc_Konf_EU_5629123.pdf", "Normzylinder DNC") == [("DNC", 1)]


def test_datum_der_richtlinie_mit_dated_zaehlt_nicht():
    a = auslesen.auswerten(pdf("Directive 2014/34/EU of the European Parliament and of the "
                               "Council, dated 26 February 2014, certifies"))
    assert a.ausstellungsdatum is None


def test_klein_geschriebener_typ_nur_mit_beleg_im_text():
    assert typen("festo_dsm_konf_714757.pdf", "Deckblatt", "Schwenkantrieb DSM-...-EX") == \
        [("DSM", 2)]
    assert typen("festo_dsm_konf_714757.pdf", "nichts") == []
