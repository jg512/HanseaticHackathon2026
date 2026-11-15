import io
from datetime import datetime

import pymupdf
import pytest
from conftest import xlsx

from app.db import db
from app.models import BomItem, Document, EmailDraft, Finding, Part, PartDocument
from app.services import hochladen, status

ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1A-1-EX4-M", 4, "Zone 1"],
          [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
          [30, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1A-1-EX4-M", 4, "Zone 1"],
          [40, None, "Klemme", "Festo", "X-1", 1, "Zone 1"]]

DEUTSCH = ("Der Hersteller erklärt, dass das Produkt mit den Anforderungen der Richtlinie "
           "übereinstimmt und für den Einsatz in der Zone bestimmt ist. Die Normen werden "
           "eingehalten, das Gerät ist mit der Kennzeichnung versehen und wird nicht verändert.")
VACF_KONF = ("EU-Konformitätserklärung\nMagnetspule VACF-B-K1-1A-1-EX4-M\n"
             "II 2G Ex mb IIC T5 Gb\nBaumusterprüfbescheinigung IBExU16ATEX1146 X\n" + DEUTSCH)
VTUG_KONF = ("EU-Konformitätserklärung\nVentilinsel VTUG-10-VI-EX2E\nII 3G Ex ec IIC T4 Gc\n"
             + DEUTSCH)


def pdf_bytes(*seiten):
    doc = pymupdf.open()
    for text in seiten:
        doc.new_page().insert_text((40, 60), text, fontsize=8)
    return doc.tobytes()


@pytest.fixture
def docs(client, tmp_path):
    ordner = tmp_path / "ex_dokumente"
    (ordner / "Festo").mkdir(parents=True)
    client.application.config["ATEX_DOCS"] = str(ordner)
    return ordner


@pytest.fixture
def bom(client, docs):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    return r.get_json()


def item_id(bom, pos):
    return next(i["id"] for i in bom["items"] if i["pos"] == pos)


def hoch(client, bom, pos, inhalt, name="dokument.pdf", **felder):
    daten = {"by": "Jan", "source": "manuell", "doc_type": "eu_konformitaetserklaerung"}
    daten.update(felder)
    daten = {k: v for k, v in daten.items() if v is not None}
    if inhalt is not None:
        daten["file"] = (io.BytesIO(inhalt), name)
    return client.post(f"/api/boms/{bom['id']}/items/{item_id(bom, pos)}/documents", data=daten)


def stati(client, bom):
    return {i["pos"]: i["status"] for i in client.get(f"/api/boms/{bom['id']}").get_json()["items"]}


def anfrage_gesendet(item_id_):
    """Position wie nach Suche ohne Fund und gesendeter Anfrage: „angefordert“."""
    item = db.session.get(BomItem, item_id_)
    item.searched_at = datetime(2026, 11, 13)
    item.email_drafts.append(EmailDraft(to_address="test@example.com", subject="s", body="b",
                                        status="gesendet", sent_at=datetime(2024, 3, 4, 8, 0)))
    status.aktualisiere(item)
    db.session.commit()


def test_manuell_eingefuegt_geht_direkt_in_die_stammdaten(client, bom, docs):
    inhalt = pdf_bytes(VACF_KONF)
    r = hoch(client, bom, "10", inhalt, name="../../Konf VACF (neu).pdf", by=" Jan ")
    assert r.status_code == 201
    f = r.get_json()
    assert (f["source"], f["status"], f["decided_by"], f["item_status"]) == (
        "manuell", "freigegeben", "Jan", "vorhanden")
    assert f["decided_at"]
    assert f["file_path"] == "_upload/Festo/Konf_VACF_neu.pdf"
    assert (docs / "_upload" / "Festo" / "Konf_VACF_neu.pdf").read_bytes() == inhalt
    # Vorprüfung läuft mit und bleibt stehen
    assert (f["doc_type"], f["page"], f["matched_term"], f["check_result"]) == (
        "eu_konformitaetserklaerung", 1, "VACF-B-K1-1A-1-EX4-M", "ok")
    assert f["check_message"] == ("Der Typ „VACF-B-K1-1A-1-EX4-M“ steht genau so im Dokument, "
                                  "auf Seite 1. Die Kategorie 2G passt zu Zone 1.")
    assert (f["languages"], f["certificate_no"], f["ex_marking_raw"]) == (
        "de", "IBExU16ATEX1146 X", "II 2G Ex mb IIC T5 Gb")

    doc = db.session.scalars(db.select(Document)).one()
    assert (doc.doc_type, doc.file_path, doc.certificate_no, doc.changed_by, doc.file_exists) == (
        "eu_konformitaetserklaerung", "_upload/Festo/Konf_VACF_neu.pdf", "IBExU16ATEX1146 X",
        "Jan", True)
    assert [s.language for s in doc.languages] == ["de"]
    pd = db.session.scalars(db.select(PartDocument)).one()
    assert (pd.part.profile_no, pd.status, pd.verified_by) == ("99000010", "geprueft", "Jan")
    # Pos. 30 hat dieselbe PRO.FILE-Nr. und zieht mit
    assert stati(client, bom) == {"10": "vorhanden", "20": "offen", "30": "vorhanden",
                                  "40": "klaerung"}

    # Datei- und Seitenbild-Endpunkte funktionieren mit dem Pfad unter _upload/
    r = client.get(f"/api/findings/{f['id']}/file")
    assert (r.status_code, r.data) == (200, inhalt)
    r.close()
    assert client.get(f"/api/findings/{f['id']}/page.png").data.startswith(b"\x89PNG")


def test_manuell_mit_unklarer_vorpruefung(client, bom):
    r = hoch(client, bom, "20", pdf_bytes("EU-Baumusterprüfbescheinigung\nVentil ABC-1\nII 2G"),
             doc_type="eu_baumusterpruefbescheinigung")
    f = r.get_json()
    assert (r.status_code, f["status"], f["doc_type"], f["check_result"], f["page"]) == (
        201, "freigegeben", "eu_baumusterpruefbescheinigung", "unklar", None)
    assert f["check_message"] == ("Der Typ „VTUG-10-VI-EX2E“ steht weder im Dokument noch im "
                                  "Dateinamen. Die Kategorie 2G passt zu Zone 2.")
    # in den Stammdaten, aber die Pflicht-Konformitätserklärung fehlt noch
    assert db.session.scalars(db.select(PartDocument)).one().status == "geprueft"
    assert f["item_status"] == "offen"


def test_herstellerantwort_wird_vorschlag_und_wartet_auf_freigabe(client, bom):
    pos20 = item_id(bom, "20")
    anfrage_gesendet(pos20)
    assert stati(client, bom)["20"] == "angefordert"

    r = hoch(client, bom, "20", pdf_bytes(VTUG_KONF), name="Antwort Festo.pdf",
             source="hersteller", doc_type=None)
    assert r.status_code == 201
    f = r.get_json()
    assert (f["source"], f["status"], f["decided_by"], f["item_status"]) == (
        "hersteller", "vorgeschlagen", None, "vorschlag")
    assert (f["doc_type"], f["page"], f["check_result"], f["file_path"]) == (
        "eu_konformitaetserklaerung", 1, "ok", "_upload/Festo/Antwort_Festo.pdf")
    assert "Ventilinsel VTUG-10-VI-EX2E" in f["snippet"]
    assert db.session.scalars(db.select(Part)).all() == []  # noch nichts in den Stammdaten
    # erscheint auf der Prüfseite
    liste = client.get(f"/api/boms/{bom['id']}/findings").get_json()
    assert [(p["pos"], [x["id"] for x in p["findings"]]) for p in liste] == [("20", [f["id"]])]

    r = client.post(f"/api/findings/{f['id']}/decision", json={"decision": "freigeben", "by": "Eva"})
    assert (r.status_code, r.get_json()["item_status"]) == (200, "vorhanden")
    assert db.session.scalars(db.select(PartDocument)).one().verified_by == "Eva"


def test_abgelehnte_herstellerantwort_faellt_zurueck(client, bom):
    pos20 = item_id(bom, "20")
    anfrage_gesendet(pos20)
    # Ohne Typ, Kategorie 3G in Zone 1 (Pos. 10): nicht verworfen, sondern „warnung“ vorgelegt
    r = hoch(client, bom, "10", pdf_bytes("Declaration of conformity\nII 3G Ex ec IIC T4 Gc"),
             source="hersteller", doc_type="eu_konformitaetserklaerung")
    f = r.get_json()
    assert (f["status"], f["check_result"]) == ("vorgeschlagen", "warnung")
    assert f["check_message"].startswith("Der Typ „VACF-B-K1-1A-1-EX4-M“ steht weder im Dokument")

    inhalt = pdf_bytes(VTUG_KONF)
    f20 = hoch(client, bom, "20", inhalt, source="hersteller").get_json()
    r = client.post(f"/api/findings/{f20['id']}/decision", json={"decision": "ablehnen", "by": "Eva"})
    assert r.get_json()["item_status"] == "angefordert"
    # die Erinnerung ist wieder fällig (Anfrage vom 04.03.2024)
    [mail] = client.get(f"/api/boms/{bom['id']}/emails").get_json()
    assert mail["next_step"] == "erinnern"

    # dieselbe Datei nach der Ablehnung bewusst erneut hochgeladen: neuer Vorschlag
    r = hoch(client, bom, "20", inhalt, source="hersteller")
    assert (r.status_code, r.get_json()["item_status"]) == (201, "vorschlag")


def test_fehler_beim_hochladen(client, bom, monkeypatch):
    inhalt = pdf_bytes(VACF_KONF)
    for felder, text in (({"by": "  "}, "Anmeldung"), ({"source": "mail"}, "vom Hersteller"),
                         ({"source": None}, "vom Hersteller"),
                         ({"doc_type": None}, "was für ein Dokument"),
                         ({"doc_type": "zertifikat"}, "kennt der Agent nicht"),
                         ({"source": "hersteller", "doc_type": "x"}, "kennt der Agent nicht")):
        r = hoch(client, bom, "10", inhalt, **felder)
        assert r.status_code == 400 and text in r.get_json()["error"]
    assert hoch(client, bom, "10", None).status_code == 400  # keine Datei
    r = hoch(client, bom, "10", b"Hallo, das ist kein PDF")
    assert (r.status_code, r.get_json()["error"]) == (400, hochladen.KEIN_PDF)
    r = hoch(client, bom, "40", inhalt)
    assert r.status_code == 409 and "PRO.FILE" in r.get_json()["error"]

    url = f"/api/boms/{bom['id']}/items"
    assert client.post(f"{url}/999/documents", data={"by": "Jan"}).status_code == 404
    assert client.post(f"/api/boms/999/items/{item_id(bom, '10')}/documents").status_code == 404

    monkeypatch.setattr(hochladen, "MAX_BYTES", 100)
    r = hoch(client, bom, "10", inhalt)
    assert (r.status_code, r.get_json()["error"]) == (413, hochladen.ZU_GROSS)
    monkeypatch.setattr(hochladen, "MAX_BYTES", 0)  # Anfrage selbst über der Grenze (1 MB Puffer)
    r = hoch(client, bom, "10", b"%PDF" + b"0" * (2 * 1024 * 1024))
    assert r.status_code == 413 and r.get_json()["error"]

    assert db.session.scalars(db.select(Finding)).all() == []


def test_ablage_nicht_beschreibbar(client, bom, docs, tmp_path):
    (docs / "_upload").write_text("eine Datei, kein Ordner")
    r = hoch(client, bom, "10", pdf_bytes(VACF_KONF))
    assert r.status_code == 503 and "nicht erreichbar" in r.get_json()["error"]
    assert db.session.scalars(db.select(Finding)).all() == []
    assert db.session.scalars(db.select(Document)).all() == []

    client.application.config["ATEX_DOCS"] = str(tmp_path / "fehlt")
    assert hoch(client, bom, "10", pdf_bytes(VACF_KONF)).status_code == 503


def test_gleiche_datei_nicht_doppelt(client, bom, docs):
    inhalt = pdf_bytes(VACF_KONF)
    assert hoch(client, bom, "10", inhalt, source="hersteller").status_code == 201
    r = hoch(client, bom, "10", inhalt, name="andere.pdf", source="hersteller")
    assert r.status_code == 409 and "gibt es bei der Position schon" in r.get_json()["error"]
    # gleiche Datei zu einer anderen Position: ein Vorschlag mehr, aber nur eine Datei
    f = hoch(client, bom, "20", inhalt, name="andere.pdf", source="hersteller").get_json()
    assert f["file_path"] == "_upload/Festo/dokument.pdf"
    # gleicher Name, anderer Inhalt: Zusatz am Namen
    anders = pdf_bytes(VTUG_KONF)
    f = hoch(client, bom, "20", anders, source="hersteller").get_json()
    assert f["file_path"] == f"_upload/Festo/dokument_{f['file_hash'][:8]}.pdf"
    assert sorted(p.name for p in (docs / "_upload" / "Festo").iterdir()) == [
        "dokument.pdf", f"dokument_{f['file_hash'][:8]}.pdf"]


@pytest.mark.parametrize("name, sicher", [
    ("../../etc/passwd.pdf", "passwd.pdf"),
    ("/tmp/x/Zertifikat.PDF", "Zertifikat.pdf"),
    ("C:\\Users\\jan\\Konf ä (1).pdf", "Konf_ä_1.pdf"),
    ("a<b>|c?*.pdf", "a_b_c.pdf"),
    ("..", "dokument.pdf"),
    ("", "dokument.pdf"),
    (".pdf", "dokument.pdf"),
])
def test_sicherer_name(name, sicher):
    assert hochladen.sicherer_name(name) == sicher


def test_kein_pfad_aus_dem_ordner_heraus(client, docs, tmp_path):
    zeilen = [[10, 99000010, "Magnetspule", "../../..", "VACF-B-K1-1A-1-EX4-M", 4, "Zone 1"]]
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "l.xlsx")}).get_json()
    f = hoch(client, bom, "10", pdf_bytes(VACF_KONF), name="/etc/../../böse.pdf").get_json()
    assert f["file_path"] == "_upload/unbekannt/böse.pdf"
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.pdf")] == [
        "ex_dokumente/_upload/unbekannt/böse.pdf"]
