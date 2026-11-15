"""Pflichtsprache (Entscheidung des Teams zu M8): die deutsche Fassung der
EU-Konformitätserklärung ist Pflicht, andere Sprachen und andere Dokumentarten sind freiwillig.
Dazu der Zwischenstand (Entscheidung des Teams)."""

import hashlib
import io

import pytest
from conftest import xlsx
from openpyxl import load_workbook
from test_suche import pdf

from app.db import db
from app.models import BomItem, Document, DocumentLanguage, Finding, Part, PartDocument
from app.services import email_entwurf

KONF = [[10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1-5", 4, "Zone 1"]]
DEUTSCH_FEHLT = "Die deutsche Fassung der EU-Konformitätserklärung fehlt noch, sie ist Pflicht."


def geprueft(doc_type="eu_konformitaetserklaerung", sprachen=("en",), datei="Festo/k.pdf",
             profile_no="99000010"):
    part = db.session.scalar(db.select(Part).filter_by(profile_no=profile_no)) \
        or Part(profile_no=profile_no, changed_by="test")
    doc = Document(doc_type=doc_type, file_path=datei, changed_by="test",
                   languages=[DocumentLanguage(language=s) for s in sprachen])
    db.session.add(PartDocument(part=part, document=doc, status="geprueft", changed_by="test"))
    db.session.commit()
    return doc


def neuer_vorgang(client, zeilen=KONF):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "liste.xlsx")})
    return r.get_json()


def funde(pos):
    return [f for f in db.session.scalars(db.select(Finding).order_by(Finding.id))
            if f.item.pos == pos]


def test_nur_englische_erklaerung_ist_nicht_vollstaendig(client):
    doc = geprueft(sprachen=("en",))
    bom = neuer_vorgang(client)
    [item] = bom["items"]
    assert item["status"] == "offen"  # nicht „vorhanden“: wird gesucht
    assert item["status_note"] == (f"{DEUTSCH_FEHLT} Die EU-Konformitätserklärung "
                                   "liegt geprüft nur auf Englisch vor.")
    # Fehlliste, /missing, Kundenliste gesperrt (M12)
    [offen] = client.get(f"/api/boms/{bom['id']}/missing").get_json()["open"]
    assert offen["missing_doc_types"] == []
    assert offen["missing_languages"] == {"eu_konformitaetserklaerung": ["de"]}
    assert offen["missing_documents"] == "EU-Konformitätserklärung (deutsche Fassung)"
    assert client.get(f"/api/boms/{bom['id']}/export").status_code == 409
    zeile = list(load_workbook(io.BytesIO(client.get(
        f"/api/boms/{bom['id']}/missing.xlsx").data)).active.iter_rows(values_only=True))[1]
    assert zeile[7].startswith(DEUTSCH_FEHLT)
    assert zeile[9] == "EU-Konformitätserklärung (deutsche Fassung)"
    # Übersicht der Sprachen
    antwort = client.get(f"/api/boms/{bom['id']}/sprachen").get_json()
    assert (antwort["sprachen"], antwort["pflichtsprache"]) == (["de", "en"], "de")
    [i] = antwort["items"]
    assert i["pflicht_fehlt"] == {"eu_konformitaetserklaerung": ["de"]}
    assert i["fehlende_sprachen"] == {}  # Englisch ist da, Deutsch steht unter pflicht_fehlt

    # Die Anfrage an den Hersteller fragt gezielt nach der deutschen Fassung
    text = email_entwurf.angaben(db.session.get(BomItem, item["id"]))
    assert email_entwurf.PFLICHT_FASSUNG in text
    assert "EU-Konformitätserklärung (zwingend erforderlich)" not in text

    # Sprache in den Stammdaten ergänzt: vollständig
    r = client.patch(f"/api/stammdaten/documents/{doc.id}",
                     json={"languages": ["de", "en"], "by": "Eva"})
    assert r.status_code == 200 and r.get_json()["items_updated"] == 1
    assert db.session.get(BomItem, item["id"]).status == "vorhanden"
    assert client.get(f"/api/boms/{bom['id']}/export").status_code == 200


def test_von_hand_erledigen_schliesst_die_luecke(client):
    geprueft(sprachen=("en",))
    bom = neuer_vorgang(client)
    item_id = bom["items"][0]["id"]
    r = client.post(f"/api/boms/{bom['id']}/items/{item_id}/resolve",
                    json={"by": "Eva", "note": "Der Kunde braucht nur Englisch"})
    assert r.status_code == 200 and r.get_json()["status"] == "erledigt"
    assert client.get(f"/api/boms/{bom['id']}/export").status_code == 200


@pytest.mark.parametrize("sprachen, datei, vollstaendig", [
    (("de", "en"), "Festo/k.pdf", True),          # zweisprachig mit Deutsch zählt
    ((), "Festo/Festo_Konf_VACF_de.pdf", True),    # Scan: Kürzel im Dateinamen zählt
    ((), "Festo/Festo_Konf_VACF_d_e.pdf", True),
    ((), "Festo/Festo_Konf_VACF.pdf", False),      # Sprache unbekannt: nicht belegt
    (("fr",), "Festo/k.pdf", False),
])
def test_grenzfaelle_der_pflichtsprache(client, sprachen, datei, vollstaendig):
    geprueft(sprachen=sprachen, datei=datei)
    item = neuer_vorgang(client)["items"][0]
    assert (item["status"] == "vorhanden") is vollstaendig
    if not sprachen and not vollstaendig:
        assert "ist die Sprache nicht bekannt" in item["status_note"]


def test_pflichtsprache_einstellbar(client):
    client.application.config["PFLICHTSPRACHE"] = "en"
    geprueft(sprachen=("en",))
    assert neuer_vorgang(client)["items"][0]["status"] == "vorhanden"


def test_freiwillige_arten_halten_nie_auf(client):
    geprueft(sprachen=("de",))
    geprueft(doc_type="bedienungsanleitung", sprachen=("en",), datei="Festo/anleitung_en.pdf")
    bom = neuer_vorgang(client)
    assert bom["items"][0]["status"] == "vorhanden"
    [i] = client.get(f"/api/boms/{bom['id']}/sprachen").get_json()["items"]
    assert i["pflicht_fehlt"] == {}
    # Englisch fehlt bei der Erklärung, Deutsch bei der Anleitung: beides nur Hinweise
    assert i["fehlende_sprachen"] == {"eu_konformitaetserklaerung": ["en"],
                                      "bedienungsanleitung": ["de"]}
    a = client.get(f"/api/artikel/{i['id']}").get_json()
    assert a["deutsch_fehlt"] is False
    assert ("Die Bedienungsanleitung fehlt noch auf Deutsch. Das ist freiwillig und hält die "
            "Ausgabe nicht auf.") in a["hinweise"]


def test_oberflaeche_zeigt_deutsch_fehlt(client):
    geprueft(sprachen=("en",))
    bom = neuer_vorgang(client)
    v = client.get(f"/api/vorgaenge/{bom['id']}").get_json()
    assert v["deutsch_fehlt"] == 1 and v["artikel"][0]["deutsch_fehlt"] is True
    a = client.get(f"/api/artikel/{bom['items'][0]['id']}").get_json()
    assert a["notiz"].startswith(DEUTSCH_FEHLT)
    # Kategorie 2G ohne Baumusterprüfbescheinigung: nur ein Hinweis
    assert ("Für Geräte der Kategorie 2G gibt es meist eine EU-Baumusterprüfbescheinigung. Sie "
            "fehlt hier noch, hält die Ausgabe aber nicht auf.") in a["hinweise"]


KONF_EN = ("EU Declaration of Conformity\nSolenoid coil VACF-B-K1-1-5\nII 2G Ex mb IIC T5 Gb\n"
           "Certificate IBExU16ATEX1146 X\nThe manufacturer declares that the product is in "
           "conformity with the directive and that this declaration is valid for the product "
           "and the marking of the product.")


def test_zweiter_durchgang_findet_die_deutsche_fassung_zur_gepruefen_englischen(client, tmp_path):
    """Bezug ist die geprüfte englische Erklärung aus den Stammdaten, kein Fund der Suche."""
    en = pdf(tmp_path / "Festo" / "Festo_Konf_VACF_en.pdf", KONF_EN)
    pdf(tmp_path / "Festo" / "Festo_Konf_VACF_de.pdf", "")  # Scan, Sprache nur im Dateinamen
    client.application.config["ATEX_DOCS"] = str(tmp_path)
    doc = geprueft(sprachen=("en",), datei="Festo/Festo_Konf_VACF_en.pdf")
    doc.file_hash = hashlib.sha256(en.read_bytes()).hexdigest()
    db.session.commit()
    bom = neuer_vorgang(client)
    client.post(f"/api/boms/{bom['id']}/search")
    vorschlaege = [f for f in funde("10") if f.status == "vorgeschlagen"]
    de = next(f for f in vorschlaege if f.file_path.endswith("_de.pdf"))
    assert (de.doc_type, de.languages) == ("eu_konformitaetserklaerung", "de")
    assert de.check_message.startswith("Das ist vermutlich die deutsche Fassung von "
                                       "Festo_Konf_VACF_en.pdf")
    # die schon geprüfte englische Datei wird nicht als Sprachversion vorgeschlagen
    assert not any("Fassung von" in (f.check_message or "") and f.file_path.endswith("_en.pdf")
                   for f in vorschlaege)


KEL_FR = ("Déclaration UE de conformité\nBoîte à bornes KEL 94XX\nPTB 02 ATEX 1082 U\n"
          "Le fabricant déclare que les produits sont conformes aux exigences de la directive et "
          "les produits sont pour une utilisation dans les zones avec les marquages.")
KEL_EN = ("EU Declaration of Conformity\nPTB 02 ATEX 1082 U\nThe manufacturer declares that the "
          "product is in conformity with the directive and that this is the declaration of the "
          "product with the marking.")
KEL_DE = ("EU-Konformitätserklärung\nPTB 02 ATEX 1082 U\nDer Hersteller erklärt, dass das "
          "Produkt mit der Richtlinie übereinstimmt und die Anforderungen für den Betrieb "
          "erfüllt werden, die in der Anlage stehen.")


def test_zweiter_durchgang_versucht_die_pflichtsprache_zuerst(client, tmp_path):
    pdf(tmp_path / "Rittal" / "Rittal_KEL_94XX_Konformite_fr.pdf", KEL_FR)
    pdf(tmp_path / "Rittal" / "Aaa_Erklaerung.pdf", KEL_EN)       # alphabetisch zuerst
    pdf(tmp_path / "Rittal" / "Zzz_Erklaerung.pdf", KEL_DE)
    client.application.config["ATEX_DOCS"] = str(tmp_path)
    bom = neuer_vorgang(client, [[220, 99000220, "Klemmenkasten", "Rittal", "KEL 94XX", 1,
                                  "Zone 1"]])
    client.post(f"/api/boms/{bom['id']}/search")
    assert [(f.file_path, f.languages) for f in funde("220")] == [
        ("Rittal/Rittal_KEL_94XX_Konformite_fr.pdf", "fr"),
        ("Rittal/Zzz_Erklaerung.pdf", "de"),   # Pflichtsprache vor der alphabetischen Reihe
        ("Rittal/Aaa_Erklaerung.pdf", "en")]
    [i] = client.get(f"/api/boms/{bom['id']}/sprachen").get_json()["items"]
    assert i["pflicht_fehlt"] == {} and i["fehlende_sprachen"] == {}


# Zwischenstand (Entscheidung des Teams)


def test_zwischenstand_jederzeit_und_deutlich_markiert(client):
    geprueft(sprachen=("en",))
    bom = neuer_vorgang(client, KONF + [[20, 99000020, "Ventilinsel", "Festo", "VTUG", 1,
                                         "Zone 2"]])
    for url in (f"/api/boms/{bom['id']}/export?zwischenstand=1",
                f"/api/vorgaenge/{bom['id']}/export?zwischenstand=1"):
        r = client.get(url)
        assert r.status_code == 200
        assert "Zwischenstand_Ex-Dokumente_" in r.headers["Content-Disposition"]
        wb = load_workbook(io.BytesIO(r.data))
        assert wb.sheetnames == ["Zwischenstand", "Ex-Dokumente (Zwischenstand)"]
        blatt = [list(z) for z in wb["Zwischenstand"].iter_rows(values_only=True)]
        assert blatt[0][0] == "ZWISCHENSTAND – unvollständig, nicht zur Weitergabe an den Kunden"
        assert blatt[1][0].startswith("Diese Mappe ist ein Zwischenstand vom ")
        zahlen = {z[0]: z[1] for z in blatt[3:9]}
        assert (zahlen["Positionen gesamt"], zahlen["Davon noch offen"],
                zahlen["Davon ohne deutsche Fassung der EU-Konformitätserklärung"]) == (2, 2, 1)
        kopf = next(n for n, z in enumerate(blatt) if z[0] == "Pos.")
        assert [z[0] for z in blatt[kopf + 1:]] == ["10", "20"]
        assert blatt[kopf + 1][9] == "EU-Konformitätserklärung (deutsche Fassung)"
        # die Kundenliste dahinter behält ihre 17 Spalten
        assert len(next(wb["Ex-Dokumente (Zwischenstand)"].iter_rows(values_only=True))) == 17
    # die echte Kundenliste bleibt gesperrt
    assert client.get(f"/api/vorgaenge/{bom['id']}/export").status_code == 409
    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{bom['id']}/ereignisse").get_json()]
    assert ("Der Zwischenstand ist als Excel-Datei ausgegeben, 2 Positionen sind noch offen. Er "
            "ist nicht für den Kunden bestimmt.") in texte
