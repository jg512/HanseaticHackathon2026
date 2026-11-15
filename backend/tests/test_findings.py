import io
import os
from pathlib import Path

import pymupdf
import pytest
from conftest import xlsx

from app.db import db
from app.models import (BomItem, Document, Finding, Manufacturer, Part, PartDocument)
from app.services import freigabe, pruefung, status

ECHT = Path(os.environ.get("ATEX_DOCS", "/data/ex_dokumente"))

ZEILEN = [[110, 99000110, "Absolutwert-Drehgeber", "Pepperl+Fuchs", "78E-Serie, Ex-Ausführung", 1, "Zone 1"],
          [150, 99000150, "Induktiver Sensor", "pepperl + fuchs", "NJ50-…-N", 2, "Zone 1"],
          [290, 99000110, "Absolutwert-Drehgeber", "Pepperl+Fuchs", "78E-Serie, Ex-Ausführung", 1, "Zone 1"]]


def pdf(pfad, seiten):
    """Kleines PDF mit einem Text je Seite."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for text in seiten:
        doc.new_page().insert_text((72, 100), text)
    doc.save(pfad)
    return pfad


@pytest.fixture
def docs(client, tmp_path):
    ordner = tmp_path / "ex_dokumente"
    pdf(ordner / "P+F" / "78E_Konf.pdf", ["Deckblatt", "EU-Konformitaetserklaerung 78E Drehgeber"])
    client.application.config["ATEX_DOCS"] = str(ordner)
    return ordner


@pytest.fixture
def bom(client, docs):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    return r.get_json()


def vorschlag(item_id, **felder):
    """Legt einen Vorschlag an, wie es die Suche tut."""
    werte = dict(bom_item_id=item_id, source="ordner", doc_type="eu_konformitaetserklaerung",
                 file_path="P+F/78E_Konf.pdf", file_hash="hash-78e", languages="de, en",
                 page=2, matched_term="78E", snippet="EU-Konformitaetserklaerung 78E Drehgeber",
                 ex_marking_raw="II 2G Ex ia IIC T4 Gb", certificate_no="ITS15ATEX18372X",
                 check_result="ok", check_message="Typ 78E steht im Dokument.")
    werte.update(felder)
    f = Finding(**werte)
    db.session.add(f)
    db.session.flush()
    status.aktualisiere(db.session.get(BomItem, item_id))
    db.session.commit()
    return f.id


def entscheide(client, finding_id, **daten):
    return client.post(f"/api/findings/{finding_id}/decision", json=daten)


def stati(client, bom):
    return [i["status"] for i in client.get(f"/api/boms/{bom['id']}").get_json()["items"]]


def anzahl(modell):
    return db.session.scalar(db.select(db.func.count()).select_from(modell))


def test_liste_zeigt_positionen_mit_vorschlaegen(client, bom):
    pos110 = bom["items"][0]["id"]
    fid = vorschlag(pos110)
    liste = client.get(f"/api/boms/{bom['id']}/findings").get_json()
    assert [(p["pos"], p["status"]) for p in liste] == [("110", "vorschlag")]
    (f,) = liste[0]["findings"]
    # Kennzeichnung und Zertifikat stehen nicht im PDF, der Typ nur als Reihe: nicht „ok“
    assert (f["id"], f["status"], f["page"], f["check_result"]) == (fid, "vorgeschlagen", 2, "unklar")
    assert f["check_message"] == (
        "Typ 78E steht im Dokument. Im Dokument nicht belegt sind Typ „78E-Serie, "
        "Ex-Ausführung“, Kennzeichnung „II 2G Ex ia IIC T4 Gb“, Zertifikatsnummer "
        "„ITS15ATEX18372X“ und Kategorie.")
    assert [(b["wert"], b["belegt"], b["seite"]) for b in f["belege"]] == [
        ("typ", False, 2), ("kennzeichnung", False, None), ("zertifikat", False, None),
        ("kategorie", False, None)]
    assert client.get("/api/boms/99/findings").status_code == 404


def test_alter_vorschlag_wird_beim_ansehen_geprueft(client, bom):
    fid = vorschlag(bom["items"][0]["id"])
    f = db.session.get(Finding, fid)
    f.belege, f.check_result = None, "ok"  # wie vor der Belegprüfung gespeichert
    db.session.commit()
    (g,) = client.get(f"/api/boms/{bom['id']}/findings").get_json()[0]["findings"]
    assert (g["check_result"], len(g["belege"])) == ("unklar", 4)
    assert db.session.get(Finding, fid).belege == g["belege"]


def test_freigabe_legt_stammdaten_an(client, bom):
    fid = vorschlag(bom["items"][0]["id"])
    assert stati(client, bom) == ["vorschlag", "offen", "offen"]

    r = entscheide(client, fid, decision="freigeben", by=" Jan ")
    assert r.status_code == 200
    antwort = r.get_json()
    assert (antwort["status"], antwort["decided_by"], antwort["item_status"]) == ("freigegeben", "Jan", "vorhanden")
    assert antwort["decided_at"]

    m = db.session.scalars(db.select(Manufacturer)).one()
    assert (m.name, m.normalized_name) == ("Pepperl+Fuchs", "pepperlfuchs")
    part = db.session.scalars(db.select(Part)).one()
    assert (part.profile_no, part.name_de, part.manufacturer, part.manufacturer_article_no,
            part.ex_marking_raw, part.source, part.changed_by, part.status) == \
        ("99000110", "Absolutwert-Drehgeber", m, "78E-Serie, Ex-Ausführung",
         "II 2G Ex ia IIC T4 Gb", "manual", "Jan", "ungeprueft")
    doc = db.session.scalars(db.select(Document)).one()
    assert (doc.doc_type, doc.file_path, doc.file_hash, doc.certificate_no, doc.file_exists,
            doc.changed_by) == ("eu_konformitaetserklaerung", "P+F/78E_Konf.pdf", "hash-78e",
                                "ITS15ATEX18372X", True, "Jan")
    assert doc.checked_at is not None
    assert sorted(s.language for s in doc.languages) == ["de", "en"]
    pd = db.session.scalars(db.select(PartDocument)).one()
    assert (pd.part, pd.document, pd.status, pd.verified_by, pd.source, pd.changed_by) == \
        (part, doc, "geprueft", "Jan", "manual", "Jan")
    assert pd.verified_at is not None

    # Pos. 290 hat dieselbe PRO.FILE-Nr. und ist damit auch vollständig
    assert stati(client, bom) == ["vorhanden", "offen", "vorhanden"]


def test_weiteres_dokument_fuer_dasselbe_bauteil(client, bom):
    pos110, pos150, pos290 = (i["id"] for i in bom["items"])
    entscheide(client, vorschlag(pos110), decision="freigeben", by="Jan")
    r = entscheide(client, vorschlag(pos290, doc_type="bedienungsanleitung", file_hash="hash-anl",
                                     ex_marking_raw="anders"), decision="freigeben", by="Eva")
    assert r.status_code == 200
    entscheide(client, vorschlag(pos150, file_hash="hash-nj50"), decision="freigeben", by="Eva")

    assert anzahl(Manufacturer) == 1  # „pepperl + fuchs“ ist derselbe Hersteller
    assert anzahl(Part) == 2
    part = db.session.scalar(db.select(Part).filter_by(profile_no="99000110"))
    assert part.ex_marking_raw == "II 2G Ex ia IIC T4 Gb"  # zuerst gesehener Wert bleibt
    assert sorted(pd.document.doc_type for pd in part.documents) == \
        ["bedienungsanleitung", "eu_konformitaetserklaerung"]


def test_gleiche_datei_fuer_zwei_bauteile(client, bom):
    pos110, pos150, _ = (i["id"] for i in bom["items"])
    entscheide(client, vorschlag(pos110), decision="freigeben", by="Jan")
    entscheide(client, vorschlag(pos150, languages="fr"), decision="freigeben", by="Jan")

    doc = db.session.scalars(db.select(Document)).one()
    assert sorted(pd.part.profile_no for pd in db.session.scalars(db.select(PartDocument))) == \
        ["99000110", "99000150"]
    assert sorted(s.language for s in doc.languages) == ["de", "en", "fr"]
    assert stati(client, bom) == ["vorhanden", "vorhanden", "vorhanden"]


def test_mensch_korrigiert_dokumentart(client, bom):
    fid = vorschlag(bom["items"][0]["id"], doc_type="sonstiges")
    r = entscheide(client, fid, decision="freigeben", by="Jan", doc_type="eu_baumusterpruefbescheinigung")
    assert (r.get_json()["doc_type"], r.get_json()["item_status"]) == ("eu_baumusterpruefbescheinigung", "offen")
    assert db.session.scalars(db.select(Document)).one().doc_type == "eu_baumusterpruefbescheinigung"


def test_ablehnen(client, bom):
    fid = vorschlag(bom["items"][0]["id"])
    r = entscheide(client, fid, decision="ablehnen", by="Jan", doc_type="egal")
    assert r.status_code == 200
    assert (r.get_json()["status"], r.get_json()["decided_by"], r.get_json()["item_status"]) == \
        ("abgelehnt", "Jan", "offen")
    assert (anzahl(Part), anzahl(Document), anzahl(Manufacturer)) == (0, 0, 0)


def test_fehlerhafte_entscheidungen(client, bom):
    fid = vorschlag(bom["items"][0]["id"])
    assert entscheide(client, 999, decision="ablehnen", by="Jan").status_code == 404
    for daten in ({"decision": "freigeben"}, {"decision": "freigeben", "by": "  "},
                  {"decision": "vielleicht", "by": "Jan"},
                  {"decision": "freigeben", "by": "Jan", "doc_type": "zertifikat"}):
        r = entscheide(client, fid, **daten)
        assert r.status_code == 400 and r.get_json()["error"]
    assert client.post(f"/api/findings/{fid}/decision", json=["freigeben"]).status_code == 400
    assert anzahl(Part) == 0

    assert entscheide(client, fid, decision="ablehnen", by="Jan").status_code == 200
    r = entscheide(client, fid, decision="freigeben", by="Eva")
    assert r.status_code == 409
    assert "abgelehnt" in r.get_json()["error"]
    assert anzahl(Part) == 0


def test_datei_nur_aus_atex_docs(client, bom, docs):
    pos110 = bom["items"][0]["id"]
    r = client.get(f"/api/findings/{vorschlag(pos110)}/file")
    assert (r.status_code, r.mimetype) == (200, "application/pdf")
    assert r.data == (docs / "P+F" / "78E_Konf.pdf").read_bytes()
    r.close()

    pdf(docs.parent / "geheim.pdf", ["geheim"])
    for pfad in ("../geheim.pdf", str(docs.parent / "geheim.pdf"), "P+F/fehlt.pdf", None):
        fid = vorschlag(pos110, file_path=pfad)
        assert client.get(f"/api/findings/{fid}/file").status_code == 404
        assert client.get(f"/api/findings/{fid}/page.png").status_code == 404


def test_seite_als_bild(client, bom, docs):
    pos110 = bom["items"][0]["id"]
    r = client.get(f"/api/findings/{vorschlag(pos110)}/page.png")
    assert (r.status_code, r.mimetype) == (200, "image/png")
    assert r.data.startswith(b"\x89PNG")
    # Begriff nicht als Text auf der Seite: Seite ohne Markierung
    assert client.get(f"/api/findings/{vorschlag(pos110, matched_term='XYZ')}/page.png").data.startswith(b"\x89PNG")
    assert client.get(f"/api/findings/{vorschlag(pos110, page=3)}/page.png").status_code == 404
    # andere Seite, z. B. die eines Belegs
    fid = vorschlag(pos110)
    r = client.get(f"/api/findings/{fid}/page.png?seite=1&begriff=Deckblatt")
    assert (r.status_code, r.data.startswith(b"\x89PNG")) == (200, True)
    assert r.data != client.get(f"/api/findings/{fid}/page.png").data
    assert client.get(f"/api/findings/{fid}/page.png?seite=5").status_code == 404

    pfad = docs / "P+F" / "78E_Konf.pdf"
    assert freigabe.seite_als_png(pfad, 2, "78E") != freigabe.seite_als_png(pfad, 2, "XYZ")  # markiert
    assert freigabe.seite_als_png(pfad, 2, "XYZ") == freigabe.seite_als_png(pfad, 2)


def test_ganze_pdf_mit_markierungen(client, bom, docs):
    pos110 = bom["items"][0]["id"]
    fid = vorschlag(pos110, belege=[
        {"wert": "typ", "angabe": "78E", "belegt": True, "seite": 2},
        {"wert": "zertifikat", "angabe": "Drehgeber", "belegt": False, "seite": 2,
         "hinweis": "Nur ähnlich."},
        {"wert": "kategorie", "angabe": "2G", "belegt": True, "seite": 2}])
    r = client.get(f"/api/findings/{fid}/file?markiert=1")
    assert (r.status_code, r.mimetype) == (200, "application/pdf")
    with pymupdf.open(stream=r.data, filetype="pdf") as pdf:
        assert pdf.page_count == 2  # die ganze Datei, nicht nur die Seite
        erste, zweite = pdf[0], pdf[1]
        assert list(erste.annots()) == []
        notizen = sorted(a.info["content"] for a in zweite.annots())
    # Fundstelle und Beleg zum Typ, der unbelegte Wert orange mit Hinweis; Kategorie nicht
    assert notizen == ["Fundstelle: 78E", "Typ belegt: 78E",
                       "Zertifikatsnummer nicht belegt. Nur ähnlich."]
    assert client.get(f"/api/findings/{fid}/file").data == (docs / "P+F" / "78E_Konf.pdf").read_bytes()


def test_typ_ueber_zwei_zeilen_wird_markiert(client, bom, docs):
    """Steht der Typ umbrochen in einer Tabellenzelle, findet ihn die Vorprüfung; die markierte
    PDF muss ihn auf derselben Seite zeigen."""
    pfad = docs / "Festo" / "Spule.pdf"
    pfad.parent.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Deckblatt")
    doc.new_page().insert_text((72, 100), "Typ\nABC-D-K1-1A-1-\nEX4-M\nABC-D-K1-1A-5-\nEX4-M")
    doc.save(pfad)

    angabe = pruefung.lies_typangabe("ABC-D-K1-1A-1-EX4-M")
    with pymupdf.open(pfad) as pdf:
        treffer = pruefung.finde_typ(angabe, [s.get_text() for s in pdf])
        assert (treffer.genau, treffer.seite) == (True, 2)
        assert pdf[1].search_for(treffer.wie_geschrieben) == []  # PyMuPDF allein findet ihn nicht
        (stelle,) = freigabe.fundstellen(pdf[1], treffer.wie_geschrieben)
        assert len(stelle) == 2  # eine Markierung über zwei Zeilen
        # andere Schreibweise wie in der Vorprüfung, aber nicht die Nachbarzeile „1A-5“
        assert len(freigabe.fundstellen(pdf[1], "abc d k1 1a 1 ex4 m")) == 1
        assert freigabe.fundstellen(pdf[0], treffer.wie_geschrieben) == []

    pos110 = bom["items"][0]["id"]
    fid = vorschlag(pos110, file_path="Festo/Spule.pdf", page=2,
                    matched_term=treffer.wie_geschrieben, belege=[])
    r = client.get(f"/api/findings/{fid}/file?markiert=1")
    with pymupdf.open(stream=r.data, filetype="pdf") as pdf:
        assert [len(list(s.annots())) for s in pdf] == [0, 1]
    assert freigabe.seite_als_png(pfad, 2, treffer.wie_geschrieben) != freigabe.seite_als_png(pfad, 2)


@pytest.mark.skipif(not (ECHT / "Festo").is_dir(), reason="echte Ex-Dokumente fehlen")
def test_echte_pdf_markiert_typ_auf_der_seite_der_vorpruefung():
    """Festo-Bescheinigung: Der Typ steht nur umbrochen in der Tabelle auf Seite 2."""
    pfad = ECHT / "Festo" / "Festo_IBExU16ATEX1146X_de_en.pdf"
    angabe = pruefung.lies_typangabe("VACF-B-K1-1A-1-EX4-M")
    with pymupdf.open(pfad) as pdf:
        treffer = pruefung.finde_typ(angabe, [s.get_text() for s in pdf])
    assert (treffer.genau, treffer.seite) == (True, 2)
    daten = freigabe.markierte_pdf(pfad, [(treffer.seite, treffer.wie_geschrieben, freigabe.GELB,
                                           "Fundstelle")])
    with pymupdf.open(stream=daten, filetype="pdf") as pdf:
        assert len(list(pdf[treffer.seite - 1].annots())) == 1
