"""Weitere Angaben aus dem Dokument (services/angaben.py): Gültigkeit, Ausgabe, Richtlinien,
Normen, Stellen, Hersteller, Kennzeichnung; Vorprüfung, Prüfseite, Freigabe, /api/fristen."""

import io
from datetime import date, timedelta

import pymupdf
import pytest
from conftest import xlsx

from app.db import db
from app.models import BomItem, Document, Finding
from app.services import angaben, oberflaeche, pdf_text, pruefung

HEUTE = date(2026, 11, 13)


def pdf_datei(pfad, *seiten):
    doc = pymupdf.open()
    for text in seiten:
        doc.new_page().insert_text((40, 60), text, fontsize=8)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    doc.save(pfad)
    return pfad


@pytest.fixture
def lies_pdf(tmp_path):
    """Baut eine kleine PDF aus Seitentexten und liest die Angaben wie im Betrieb."""
    zahl = [0]

    def lies(*seiten, **kw):
        zahl[0] += 1
        pdf = pdf_text.lies(pdf_datei(tmp_path / f"d{zahl[0]}.pdf", *seiten))
        return angaben.lies(pdf.seiten, pdf.ocr, **kw)
    return lies


# --- Gültigkeit --------------------------------------------------------------------------------

@pytest.mark.parametrize("text, wert", [
    ("EU-Baumusterprüfbescheinigung\nGültig bis: 31.12.2027", "2027-12-31"),
    ("Certificate\nValid until 15 March 2028", "2028-03-15"),
    ("Certificat\nValable jusqu'au 01/02/2029", "2029-02-01"),
    ("Zertifikat\ngültig bis 31.12.28", "2028-12-31"),  # zweistelliges Jahr nur hier
    ("Gültig bis / Valid until / Valable jusqu'au:\n30.06.2027", "2027-06-30"),
    ("Expiry date: 2030-01-15", "2030-01-15"),
])
def test_gueltig_bis_drei_sprachen(lies_pdf, text, wert):
    a = lies_pdf(text)["gueltig_bis"]
    assert (a["status"], a["wert"], a["seite"]) == ("datum", wert, 1)
    assert wert[:4] in a["text"] or wert[2:4] in a["text"]


@pytest.mark.parametrize("text", [
    "Richtlinie 94/9/EG (bis 19.04.2016)\nDirective 2014/34/EU (until 19.04.2016)",
    "EN 60079-0:2018\nEN 60079-11:2012",                      # Jahre der Normen
    "Diese Bescheinigung ist gültig bis zur Änderung der Normen.\nDatum: 01.02.2020",
    "Gultig bis 31.13.2027",                                    # OCR: Monat 13
    "gültig bis 31.12.2O27",                                    # OCR: O statt 0
    "valid until 01.01.2207",                                   # unplausibles Jahr
    "Certificates without signature and official stamp shall not be valid.",
])
def test_kein_ablaufdatum(lies_pdf, text):
    assert lies_pdf(text)["gueltig_bis"] == {"status": "keine_angabe", "wert": None}


def test_unbefristet(lies_pdf):
    a = lies_pdf("Diese Bescheinigung ist unbefristet gültig.")["gueltig_bis"]
    assert (a["status"], a["wert"], a["seite"]) == ("unbefristet", None, 1)
    assert "unbefristet" in a["text"]


def test_ausstellung_und_gueltigkeit_getrennt(lies_pdf):
    a = lies_pdf("Ausstellungsdatum: 01.02.2020\nGültig bis: 01.02.2025",
                 "Richtlinie 2014/34/EU vom 26. Februar 2014")
    assert a["gueltig_bis"]["wert"] == "2025-02-01"
    assert a["ausstellungsdatum"]["wert"] == "2020-02-01"  # nicht das spätere „gültig bis“


@pytest.mark.parametrize("text, wert", [
    ("Datum der Bescheinigung: 17. Februar 2020", "2020-02-17"),
    ("Date of certification: 3 March 2026", "2026-03-03"),
    ("Freiberg, 2020-05-19\nIBExU", "2020-05-19"),
    ("Verneuil-en-Halatte, 2021-10-18", "2021-10-18"),
    ("Issue No. 2 (2012-07-11)\nIssue No. 1 (2010-06-10)", "2012-07-11"),
])
def test_ausstellungsdatum(lies_pdf, text, wert):
    assert lies_pdf(text)["ausstellungsdatum"]["wert"] == wert


@pytest.mark.parametrize("text", [
    "2026-11-09\nR. STAHL Schaltgeräte GmbH",             # Druckdatum ohne Stichwort
    "Directive 94/9/EC of the 23rd March 1994",            # Datum der Richtlinie
    "Richtlinie 2014/34/EU (20. April 2016) bestanden",
    "Amtsblatt der EU L96, S. 357-374 (ab 20.04.2016)",
])
def test_kein_ausstellungsdatum(lies_pdf, text):
    assert lies_pdf(text)["ausstellungsdatum"] is None


# --- Ausgabe, Richtlinien, Normen ----------------------------------------------------------------

@pytest.mark.parametrize("text, wert", [
    ("Bescheinigung Nummer: KEMA 04ATEX2242\nAusgabe Nr.: 9\n(20) Bescheinigungsübersicht\n"
     "Ausgabe Nr. 1 - Erstbescheinigung\nAusgabe Nr. 2 - Redaktionelle Änderung", "9"),
    ("Änderungen gegenüber der Ausgabe 0 dieser Bescheinigung:\nAusgabe 1", "1"),
    ("Type Examination Certificate Number: DEKRA 21UKEX0009X\nIssue Number: 21", "21"),
    ("INERIS 14ATEX0035X\nINDICE / ISSUE : 02", "2"),
    ("Dokument DOC-1\nRev. C", "C"),
])
def test_ausgabe(lies_pdf, text, wert):
    assert lies_pdf(text)["ausgabe"]["wert"] == wert


def test_ausgabe_der_eigenen_bescheinigung(lies_pdf):
    """Eine PDF mit zwei Bescheinigungen: die Ausgabe hinter der eigenen Nummer zählt."""
    a = lies_pdf("Certificate Number: DEKRA 19ATEX0094 X\nIssue Number: 3",
                 "Certificate Number: KEMA 08ATEX0002 X\nIssue Number: 7",
                 zertifikate=["DEKRA 19ATEX0094 X", "KEMA 08ATEX0002 X"])
    assert (a["ausgabe"]["wert"], a["ausgabe"]["seite"]) == ("3", 1)


@pytest.mark.parametrize("text", ["Certification file n 16-1069IR rev.0 of 2014.08.13",
                                  "Ausgabedatum\nDate of issue", "Issue a new certificate"])
def test_keine_ausgabe(lies_pdf, text):
    assert lies_pdf(text)["ausgabe"] is None


def test_richtlinien(lies_pdf):
    a = lies_pdf("EMC 2014/30/EU\nATEX Directive 94/9/EC\nATEX 2014/34/UE\n"
                 "Regulations 2016, UKSI 2016:1107 (as amended)")
    assert [(r["wert"], r["art"]) for r in a["richtlinien"]] == [
        ("94/9/EG", "atex"), ("2014/34/EU", "atex"), ("UK SI 2016/1107", "uk"),
        ("2014/30/EU", "sonstige")]
    # OCR: „2O14l34lEU“ ist die ATEX-Richtlinie, sonst keine Nachsicht
    assert [r["wert"] for r in lies_pdf("Richtlinie 2O14l34lEU des Rates")["richtlinien"]] == [
        "2014/34/EU"]


def test_normen(lies_pdf):
    a = lies_pdf("EN IEC 60079-0 : 2018\nEN 60079-7 : 2015 + A1 : 2018\nEN 60079-1\n:\n2014\n"
                 "EN 60079-31 ......     2014\nEN IEC 60079-0:2018+AC2020\n"
                 "EN ISO 80079-36:2016\nEN 61000-6-2:2005\nnach EN 60079-11 gebaut")
    assert [(n["wert"], n["ex"]) for n in a["normen"]] == [
        ("EN IEC 60079-0:2018", True), ("EN 60079-7:2015+A1:2018", True),
        ("EN 60079-1:2014", True), ("EN 60079-31:2014", True),
        ("EN IEC 60079-0:2018+AC:2020", True), ("EN ISO 80079-36:2016", True),
        ("EN 61000-6-2:2005", False)]  # ohne Ausgabe („EN 60079-11“) zählt nicht
    assert all(n["seite"] == 1 and n["text"] for n in a["normen"])


# --- Stellen, Hersteller, Unterschrift -----------------------------------------------------------

def test_benannte_stelle_pruefung_und_qm(lies_pdf):
    a = lies_pdf("(8) DEKRA Certification B.V., Notified Body number 0344 in accordance with\n"
                 "Article 17 of Directive 2014/34/EU, certifies that this product",
                 "Postfach 5185, 6802 ED Arnhem")
    [s] = a["stellen"]
    assert (s["name"], s["nummer"], s["rolle"], s["seite"]) == (
        "DEKRA Certification B.V.", "0344", "pruefung", 1)
    a = lies_pdf("ANNEX ATEX\nNotified Body QM-System / Notifizierte Stelle des QM-Systems\n"
                 "Physikalisch Technische Bundesanstalt (0102)\nBundesallee 100")
    [s] = a["stellen"]
    assert (s["name"], s["nummer"], s["rolle"]) == (
        "Physikalisch-Technische Bundesanstalt (PTB)", "0102", "qm")
    a = lies_pdf("L'Ineris, organisme notifié et identifié sous le numéro 0080, conformément")
    assert [(s["name"], s["nummer"]) for s in a["stellen"]] == [("INERIS", "0080")]


def test_stelle_aus_der_nummer(lies_pdf):
    a = lies_pdf("ATEX Bescheinigung Gas\nPTB 01 ATEX 1016", zertifikate=["PTB 01 ATEX 1016"])
    [s] = a["stellen"]
    assert (s["name"], s.get("nummer"), s["hinweis"]) == (
        "PTB", None, "Der Name steht nicht im Text. Er ist aus der Nummer des Zertifikats "
                     "abgeleitet.")


def test_hersteller_mit_ort_und_vergleich(lies_pdf):
    text = "(5)\nHersteller:\nSiemens AG\n(6)\nAnschrift:\n76181 Karlsruhe, Deutschland"
    h = lies_pdf(text, stueckliste="Siemens")["hersteller"]
    assert (h["wert"], h["ort"], h["passt"], h["seite"]) == (
        "Siemens AG", "76181 Karlsruhe, Deutschland", True, 1)
    h = lies_pdf(text, stueckliste="Festo")["hersteller"]
    assert (h["passt"], h["stueckliste"]) == (False, "Festo")
    # OCR „Pepperi“ statt „Pepperl“, Ort im Satz
    h = lies_pdf("We, Pepperi+Fuchs GmbH at 68301 Mannheim hereby declare under our",
                 stueckliste="Pepperl+Fuchs")["hersteller"]
    assert (h["wert"], h["ort"], h["passt"]) == ("Pepperi+Fuchs GmbH", "68301 Mannheim", True)


def test_hersteller_aus_fusszeile_nur_bei_erklaerungen(lies_pdf):
    fuss = "Festo SE & Co. KG | Ruiter Strasse 82 | 73734 Esslingen | Germany"
    h = lies_pdf(f"EU-Konformitätserklärung\n{fuss}", doc_type="eu_konformitaetserklaerung",
                 stueckliste="Festo")["hersteller"]
    assert (h["wert"], h["ort"], h["passt"], h["hinweis"]) == (
        "Festo SE & Co. KG", "73734 Esslingen", True,
        "Der Hersteller steht nur in der Kopf- oder Fußzeile.")
    assert lies_pdf(f"DEKRA Certification B.V. | Meander 1051 | 6825 MJ Arnhem",
                    doc_type="eu_baumusterpruefbescheinigung")["hersteller"] is None


@pytest.mark.parametrize("dokument, stueckliste, passt", [
    ("Siemens Aktiengesellschaft", "Siemens", True), ("PEPPERL+FUCHS SE", "P+F", True),
    ("Rittal-Werk, Rudolf Loh GmbH & Co. KG", "Rittal", True),
    ("R. STAHL Schaltgeräte GmbH", "Festo", False), ("Festo AG & Co. KG", None, None)])
def test_hersteller_passt(dokument, stueckliste, passt):
    assert pruefung.hersteller_passt(dokument, stueckliste) is passt


def test_unterschrift(lies_pdf):
    u = lies_pdf("Signatures / Unterschriften\nMannheim, 2021-11-22\n\nppa. Michael Kessler\n"
                 "i.V. Udo Koerner\nExecutive Vice President")["unterschrift"]
    assert (u["ort"], u["datum"], u["unterzeichner"]) == (
        "Mannheim", "2021-11-22", ["ppa. Michael Kessler", "i.V. Udo Koerner"])


# --- Kennzeichnung, Umgebungstemperatur, Bedingungen, IECEx --------------------------------------

@pytest.mark.parametrize("kennz, teile", [
    ("II 2 G (1) G Ex eb [ia Ga/ib] IIC T4 Gb",
     {"geraet": "II 2 G (1) G", "zuendschutz": "Ex eb [ia Ga/ib]", "gruppe": "IIC",
      "temperatur": "T4", "epl": "Gb"}),
    ("II 2D Ex tb IIIC T80 °C Db",
     {"geraet": "II 2D", "zuendschutz": "Ex tb", "gruppe": "IIIC", "temperatur": "T80°C",
      "epl": "Db"}),
    ("II 2 G Ex eb IIC T6 / T5 Gb",
     {"geraet": "II 2 G", "zuendschutz": "Ex eb", "gruppe": "IIC", "temperatur": "T6/T5",
      "epl": "Gb"}),
    ("Ex tb III C Db IP66", {"zuendschutz": "Ex tb", "gruppe": "IIIC", "epl": "Db",
                             "ip": "IP66"}),
    ("II 2G", {"geraet": "II 2G"}),
])
def test_kennzeichnung_zerlegen(kennz, teile):
    assert angaben.zerlege(kennz) == teile


def test_kennzeichnung_ohne_ii_wie_bei_iecex(lies_pdf):
    a = lies_pdf("Marking:\nEx e IIC Gb\nEx tb IIIC Db IP66\nCertificate No.:\nIECEx PTB 09.0035U")
    assert [(k["zuendschutz"], k["gruppe"], k["epl"]) for k in a["kennzeichnungen"]] == [
        ("Ex e", "IIC", "Gb"), ("Ex tb", "IIIC", "Db")]
    assert [i["wert"] for i in a["iecex"]] == ["IECEx PTB 09.0035U"]


@pytest.mark.parametrize("seiten, werte", [
    (["II 2G Ex h IIC T4 Gb\n-20°C ≤ Ta ≤ +70°C"], ["-20 °C … +70 °C"]),
    (["Marking\nTamb -30 °C to +80 °C"], ["-30 °C … +80 °C"]),
    (["Umgebungstemperaturbereich: -20 °C bis +70 °C (waagerecht),\n-20 °C bis +50 °C"],
     ["-20 °C … +70 °C", "-20 °C … +50 °C"]),
    (["Umgebungs\u00adtemperatur\n-60\u00a0°C ... +40\u00a0°C (T6)\n-76\u00a0°F ... +104\u00a0°F"],
     ["-60 °C … +40 °C"]),
    (["Umgebungstemperaturbereich:....\"30 °C bis +80 °C"], []),  # OCR hat das Minus verlesen
    (["Ex tb IIIC T80 °C Db\nOberflächentemperatur 20 °C bis 80 °C"], []),  # ohne „Ta“
])
def test_umgebungstemperatur(seiten, werte):
    assert [t["wert"] for t in angaben.lies(seiten)["umgebungstemperatur"]] == werte


def test_besondere_bedingungen(lies_pdf):
    b = lies_pdf("(16) Prüfbericht\n(17) Besondere Bedingungen\nKeine.\n(18) Anforderungen",
                 zertifikate=["KEMA 04ATEX2242"])["besondere_bedingungen"]
    assert (b["x"], b["keine"], b["seite"]) == (False, True, 1)
    b = lies_pdf("(10) If the sign X is placed after the certificate number, it indicates that\n"
                 "the product is subject to the Specific Conditions of Use specified in the schedule",
                 "(17) Specific conditions of use\n1. The fuse must be installed.",
                 zertifikate=["DEKRA 21ATEX0047 X"],
                 doc_type="eu_baumusterpruefbescheinigung")["besondere_bedingungen"]
    assert (b["x"], b["keine"], b["seite"]) == (True, False, 2)
    # Erklärung, die eine X-Bescheinigung nennt: Beleg ist die Nummer
    b = lies_pdf("EU-Konformitätserklärung\nBaumusterprüfbescheinigung IBExU16ATEX1146 X",
                 zertifikate=["IBExU16ATEX1146 X"])["besondere_bedingungen"]
    assert (b["x"], b["nummer"], b["seite"]) == (True, "IBExU16ATEX1146 X", 1)
    assert lies_pdf("Kein Zertifikat")["besondere_bedingungen"] is None


def test_ocr_seiten_sind_markiert():
    a = angaben.lies(["", "Gültig bis: 31.12.2027"], ocr=(2,))
    assert a["gueltig_bis"]["ocr"] is True
    meldungen = pruefung.pruefe_angaben(a, date(2028, 1, 5))
    assert meldungen == [("warnung", "Das Zertifikat ist seit dem 31.12.2027 abgelaufen, so "
                                     "steht es auf Seite 2. Das Datum stammt aus einem "
                                     "eingescannten Blatt und ist nicht sicher gelesen. Bitte "
                                     "prüfen Sie es im Dokument.")]
    assert angaben.felder(a, date(2028, 1, 5))[0][0][1]["beleg"]["text"].endswith(
        "nicht sicher gelesen.")


# --- Vorprüfung ----------------------------------------------------------------------------------

def _a(bis=None, richtlinien=(), hersteller=None):
    return {"gueltig_bis": {"status": "datum", "wert": bis, "seite": 1} if bis
            else {"status": "keine_angabe", "wert": None},
            "richtlinien": [{"wert": r, "seite": 2} for r in richtlinien],
            "hersteller": hersteller}


def test_vorpruefung_gueltigkeit():
    assert pruefung.pruefe_angaben(_a("2026-01-31"), HEUTE) == [
        ("warnung", "Das Zertifikat ist seit dem 31.01.2026 abgelaufen, so steht es auf Seite 1.")]
    assert pruefung.pruefe_angaben(_a("2027-03-01"), HEUTE) == [
        ("hinweis", "Das Zertifikat läuft am 01.03.2027 ab, also in 108 Tagen. So steht es auf "
                    "Seite 1.")]
    assert pruefung.pruefe_angaben(_a("2027-03-01"), HEUTE, warn_tage=90) == []  # Schwelle


def test_vorpruefung_richtlinie_je_dokumentart():
    """Nur 94/9/EG: in einer Erklärung veraltet (Warnung); eine Bescheinigung gilt nach der
    Übergangsregel (Art. 41) weiter (Hinweis); unbekannte Art: Hinweis."""
    alt = _a(richtlinien=["94/9/EG"])
    assert pruefung.pruefe_angaben(_a(richtlinien=["94/9/EG", "2014/34/EU"]), HEUTE,
                                   doc_type="eu_konformitaetserklaerung") == []
    [(stufe, text)] = pruefung.pruefe_angaben(alt, HEUTE, doc_type="eu_konformitaetserklaerung")
    assert stufe == "warnung"
    assert text == ("Die Konformitätserklärung nennt auf Seite 2 nur die alte Richtlinie 94/9/EG. "
                    "Seit dem 20.04.2016 muss sie die Richtlinie 2014/34/EU nennen. Bitte fordern "
                    "Sie beim Hersteller eine aktuelle Erklärung an.")
    [(stufe, text)] = pruefung.pruefe_angaben(alt, HEUTE,
                                              doc_type="eu_baumusterpruefbescheinigung")
    assert stufe == "hinweis" and "gilt nach der Übergangsregel" in text
    for art in (None, "sonstiges", "bedienungsanleitung"):
        [(stufe, text)] = pruefung.pruefe_angaben(alt, HEUTE, doc_type=art)
        assert stufe == "hinweis" and text.startswith("Das Dokument nennt auf Seite 2 nur")


def test_vorpruefung_hersteller():
    h = {"wert": "R. STAHL GmbH", "seite": 1, "passt": False, "stueckliste": "Festo"}
    assert pruefung.pruefe_angaben(_a(hersteller=h), HEUTE) == [
        ("hinweis", "Als Hersteller steht im Dokument „R. STAHL GmbH“ auf Seite 1, in der "
                    "Stückliste aber „Festo“. Bitte prüfen Sie, ob das Dokument zu diesem Bauteil "
                    "gehört.")]
    # die Schwelle für OCR-Fehler im Namen ist einstellbar
    assert pruefung.hersteller_passt("Pepperi+Fuchs GmbH", "Pepperl+Fuchs") is True
    assert pruefung.hersteller_passt("Pepperi+Fuchs GmbH", "Pepperl+Fuchs", 0.95) is False


def test_bewertung_bleibt_ein_rat():
    assert pruefung.bewerte_angaben("ok", "Typ passt.", []) == ("ok", "Typ passt.")
    assert pruefung.bewerte_angaben("ok", "Typ passt.", [("hinweis", "Läuft ab.")]) == (
        "ok", "Typ passt. Läuft ab.")
    assert pruefung.bewerte_angaben("unklar", "", [("warnung", "Abgelaufen.")]) == (
        "warnung", "Abgelaufen.")


def test_prueffelder_gruppiert():
    a = angaben.lies(["Datum der Bescheinigung: 17. Februar 2020\nAusgabe Nr.: 9\n"
                      "Gültig bis: 01.03.2027\nDEKRA Certification B.V., Notified Body number "
                      "0344\nHersteller:\nSiemens AG\nEN IEC 60079-0:2018\nEN 61000-6-2:2005\n"
                      "2014/34/EU\nII 2 G Ex ib IIC T4 Gb"], kennz=["II 2 G Ex ib IIC T4 Gb"],
                     stueckliste="Siemens")
    kopf, rest = angaben.felder(a, HEUTE)
    assert [t for t, _ in kopf] == ["Gültig bis", "Ausgestellt"]
    assert kopf[0][1]["wert"] == "01.03.2027, noch 108 Tage gültig"
    assert kopf[1][1]["wert"] == "Am 17.02.2020, Ausgabe 9"
    assert kopf[1][1]["beleg"]["seite"] == 1
    zeilen = dict(rest)
    assert zeilen["Zündschutzart"]["wert"] == "Ex ib, Gruppe IIC, Temperatur T4, Schutzniveau Gb"
    assert zeilen["Benannte Stelle"]["wert"] == "DEKRA Certification B.V., Nummer 0344"
    assert zeilen["Hersteller laut Dokument"]["wert"] == "Siemens AG. Passt zur Stückliste"
    assert zeilen["Genannte Normen"]["wert"] == "EN IEC 60079-0:2018 und 1 weitere Norm"
    assert zeilen["Richtlinien"]["wert"] == "2014/34/EU"
    assert all(z["beleg"] and z["beleg"]["seite"] == 1 for z in zeilen.values())
    assert angaben.felder(None) == ([], [])
    # Kennzeichnung des Vorschlags, die so nicht im Text steht: unbelegt
    a = angaben.lies(["II 2G Ex ia IIC T6"], kennz=["II 2G Ex ib IIC T4 Gb"])
    zeilen = dict(angaben.felder(a, HEUTE)[1])
    assert zeilen["Zündschutzart"]["beleg"] is None


# --- im Betrieb: Upload, Prüfseite, Freigabe, Fristen, Nachlesen ---------------------------------

ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1-5", 4, "Zone 1"],
          [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"]]
ABGELAUFEN = ("EU-Baumusterprüfbescheinigung\nNummer: IBExU16ATEX1146 X\nMagnetspule VACF-B-K1-1-5\n"
              "II 2G Ex mb IIC T5 Gb\nHersteller:\nFesto SE & Co. KG\nRichtlinie 2014/34/EU\n"
              "Datum: 01.02.2015\nGültig bis: 31.01.2025")


def _bald(tage):
    return (date.today() + timedelta(days=tage)).strftime("%d.%m.%Y")


@pytest.fixture
def vorgang(client, tmp_path):
    client.application.config.update(ATEX_DOCS=str(tmp_path), OCR=False)
    (tmp_path / "Festo").mkdir()
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    return r.get_json()


def _hoch(client, bom, pos, text, **felder):
    item = next(i["id"] for i in bom["items"] if i["pos"] == pos)
    inhalt = pymupdf.open()
    inhalt.new_page().insert_text((40, 60), text, fontsize=8)
    daten = {"by": "Jan", "source": "hersteller",
             "file": (io.BytesIO(inhalt.tobytes()), "antwort.pdf"), **felder}
    return client.post(f"/api/boms/{bom['id']}/items/{item}/documents", data=daten)


def test_upload_abgelaufen_warnt_und_zeigt_felder(client, vorgang):
    r = _hoch(client, vorgang, "10", ABGELAUFEN)
    assert r.status_code == 201
    f = r.get_json()
    assert f["check_result"] == "warnung"
    meldung = "Das Zertifikat ist seit dem 31.01.2025 abgelaufen, so steht es auf Seite 1."
    assert meldung in f["check_message"]
    a = f["angaben"]
    assert (a["gueltig_bis"]["wert"], a["ausstellungsdatum"]["wert"]) == ("2025-01-31", "2015-02-01")
    assert a["vorpruefung"] == [{"stufe": "warnung", "text": meldung}]
    felder = oberflaeche.felder(db.session.get(Finding, f["id"]))
    assert list(felder)[:3] == ["Dokumenttyp", "Gültig bis", "Ausgestellt"]
    assert felder["Gültig bis"]["wert"].startswith("31.01.2025, seit ")
    assert felder["Gültig bis"]["wert"].endswith(" Tagen abgelaufen")
    assert felder["Gültig bis"]["beleg"]["seite"] == 1
    assert felder["Hersteller laut Dokument"]["wert"] == "Festo SE & Co. KG. Passt zur Stückliste"
    assert list(felder)[-1] == "Sprachen"


def test_freigabe_uebernimmt_gueltig_bis_und_fristen(client, vorgang):
    bald = _bald(30)
    f = _hoch(client, vorgang, "10", ABGELAUFEN.replace("31.01.2025", bald)).get_json()
    assert f["check_result"] != "warnung"
    assert (f"Das Zertifikat läuft am {bald} ab, also in 30 Tagen. So steht es auf Seite 1."
            in f["check_message"])

    r = client.get("/api/fristen?tage=60").get_json()
    assert (r["tage"], r["anzahl"], r["abgelaufen"]) == (60, 1, 0)
    [e] = r["eintraege"]
    assert (e["quelle"], e["finding_id"], e["tage"], e["abgelaufen"]) == (
        "vorschlag", f["id"], 30, False)
    assert e["teile"][0]["profile_no"] == "99000010" and e["beleg"]["seite"] == 1
    assert client.get("/api/fristen?tage=10").get_json()["anzahl"] == 0

    r = client.post(f"/api/findings/{f['id']}/decision", json={"decision": "freigeben", "by": "Jan"})
    assert r.status_code == 200
    doc = db.session.scalars(db.select(Document)).one()
    assert doc.valid_until == date.today() + timedelta(days=30)
    [e] = client.get("/api/fristen?tage=60").get_json()["eintraege"]
    assert (e["quelle"], e["dokument_id"], e["teile"][0]["status"]) == (
        "stammdaten", doc.id, "geprueft")


def test_manuell_eingefuegt_gueltig_bis_in_stammdaten(client, vorgang):
    r = _hoch(client, vorgang, "10", ABGELAUFEN, source="manuell",
              doc_type="eu_baumusterpruefbescheinigung")
    f = r.get_json()
    assert (r.status_code, f["status"]) == (201, "freigegeben")
    assert db.session.scalars(db.select(Document)).one().valid_until == date(2025, 1, 31)
    # die Vorprüfung läuft trotzdem mit und bleibt stehen
    assert db.session.get(Finding, f["id"]).check_result == "warnung"
    [e] = client.get("/api/fristen").get_json()["eintraege"]
    assert (e["quelle"], e["abgelaufen"]) == ("stammdaten", True)


def test_fristen_parameter(client, vorgang):
    assert client.get("/api/fristen?tage=abc").status_code == 400
    assert client.get("/api/fristen?tage=-1").status_code == 400
    r = client.get("/api/fristen").get_json()
    assert (r["tage"], r["anzahl"], r["eintraege"]) == (180, 0, [])


def test_ordnersuche_liest_angaben(client, vorgang, tmp_path):
    pdf_datei(tmp_path / "Festo" / "Konf_VACF.pdf",
              "EU-Konformitätserklärung\nMagnetspule VACF-B-K1-1-5\nII 2G Ex mb IIC T5 Gb\n"
              "Richtlinie 94/9/EG\nEN 60079-18:2015")
    r = client.post(f"/api/boms/{vorgang['id']}/search")
    assert r.status_code == 200
    f = db.session.scalars(db.select(Finding)).one()
    assert [n["wert"] for n in f.angaben["normen"]] == ["EN 60079-18:2015"]
    assert f.check_result == "warnung"  # Erklärung nur nach 94/9/EG
    assert "nur die alte Richtlinie 94/9/EG" in f.check_message


def test_angaben_nachlesen(client, vorgang, tmp_path):
    pdf_datei(tmp_path / "Festo" / "alt.pdf", "Gültig bis: 31.12.2030\nEN 60079-0:2018")
    pos = db.session.scalars(db.select(BomItem).filter_by(pos="10")).one()
    f = Finding(source="ordner", doc_type="sonstiges", file_path="Festo/alt.pdf",
                status="vorgeschlagen", belege=[], check_result="ok", check_message="Alt.")
    weg = Finding(source="ordner", doc_type="sonstiges", file_path="Festo/fehlt.pdf",
                  status="vorgeschlagen", belege=[])
    pos.findings.extend([f, weg])
    db.session.commit()
    assert f.angaben is None  # vor dieser Funktion angelegt (belege gesetzt: kein Nachlesen)

    lauf = client.application.test_cli_runner()
    r = lauf.invoke(args=["angaben-nachlesen"])
    assert r.exit_code == 0, r.output
    assert "Angaben gelesen: 1, Datei fehlt: 1, schon vorhanden: 0." in r.output
    db.session.expire_all()
    f = db.session.get(Finding, f.id)
    assert f.angaben["gueltig_bis"]["wert"] == "2030-12-31"
    assert f.check_message == "Alt."  # weit genug weg: keine Meldung
    r = lauf.invoke(args=["angaben-nachlesen"])  # wiederholbar
    assert "Angaben gelesen: 0, Datei fehlt: 1, schon vorhanden: 1." in r.output
