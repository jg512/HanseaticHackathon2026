"""Stufe Suchdienst (RAG) in der Suche: der Dienst ist gefälscht, die Prüfung echt."""

import io

import httpx
import pytest
from conftest import xlsx
from test_suche import VTUG_DE, pdf

from app.db import db
from app.models import Bom, Finding
from app.services import internet, rag, suche, zertifikatsserver

ZEILEN = [
    [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
    [240, 99000240, "Klemmenkasten Ex e", "R. STAHL", "8146", 2, "Zone 1"],
]
STAHL = ("Ex-Datenblatt Klemmenkasten 8146\nR. STAHL Schaltgeräte GmbH\n"
         "II 2G Ex eb IIC T6 Gb\nPTB 99 ATEX 3101")
FESTO_ANDERS = ("EU-Konformitätserklärung\nSchwenkantrieb DSM-25-EX\nII 2G Ex h IIC T4 Gb\n"
                "Festo SE & Co. KG")


INTERNET = "Internet (example.com, 0 von 1 Versuchen; kein passendes Dokument)"
# was die Sachbearbeiterin unter „Gesucht in“ liest
MIT_ORDNER = "Gesucht hat der Agent in der Datenbank und im Ordner des Herstellers."
OHNE_ORDNER = ("Gesucht hat der Agent in der Datenbank. Einen Ordner für diesen Hersteller gibt "
               "es nicht.")
INTERNET_SATZ = ("Im Internet hat der Agent auf example.com gesucht und dabei keine Seite von "
                 "höchstens 1 abgerufen. Ein passendes Dokument war nicht dabei.")


class Dienst:
    """Gefälschter Suchdienst: antwortet mit den Textstellen in self.treffer."""

    def __init__(self):
        self.treffer, self.fragen, self.fehler = [], [], None

    def textstellen(self, frage, k=rag.TEXTSTELLEN):
        self.fragen.append(frage)
        if self.fehler:
            raise self.fehler
        return self.treffer

    def setze(self, *relpfade, seite=1, text="ähnliche Textstelle"):
        self.treffer = [{"name": p.rsplit("/", 1)[-1], "path": "/x/" + p, "relpath": p,
                         "page": seite, "text": text, "rang": i}
                        for i, p in enumerate(relpfade, start=1)]


@pytest.fixture
def ordner(client, tmp_path):
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf", VTUG_DE)
    pdf(tmp_path / "Festo" / "Festo_DSM_Konf.pdf", FESTO_ANDERS)
    pdf(tmp_path / "_internet" / "R_STAHL" / "datenblatt-8146.pdf", STAHL)
    pdf(tmp_path / "Rittal" / "Rittal_anderes_8146.pdf", STAHL.replace("R. STAHL", "Rittal"))
    pdf(tmp_path / "Festo" / "Festo_noch_eins.pdf", FESTO_ANDERS + "\nDSM-32-EX")
    client.application.config.update(ATEX_DOCS=str(tmp_path), SEARCH_URL="http://suchdienst:8001")
    return tmp_path


@pytest.fixture
def dienst(monkeypatch):
    d = Dienst()
    monkeypatch.setattr(rag, "textstellen", d.textstellen)
    return d


@pytest.fixture
def netz(monkeypatch):
    """Internet-Stufe gefälscht: merkt sich, für welche Position sie lief."""
    aufrufe = []

    def suche_im_netz(item, angabe, lauf, wurzel):
        aufrufe.append(item.pos)
        return [], {}, INTERNET
    monkeypatch.setattr(internet, "suche", suche_im_netz)
    return aufrufe


def vorgang(client, zeilen=ZEILEN):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "l.xlsx")})
    return db.session.get(Bom, r.get_json()["id"])


def position(bom, pos):
    return next(i for i in bom.items if i.pos == pos)


def suchen(bom, versuche=1, melde=None):
    suche.suche_vorgang(bom, versuche, melde)
    db.session.commit()


def test_ordner_hat_den_typ_dann_kein_suchdienst(client, ordner, dienst, netz):
    bom = vorgang(client, ZEILEN[:1])
    suchen(bom)
    item = position(bom, "20")
    assert dienst.fragen == [] and netz == []
    assert [f.source for f in item.findings] == ["ordner"]
    assert item.searched_in == MIT_ORDNER


def test_kandidat_mit_typ_wird_normaler_vorschlag_ohne_internet(client, ordner, dienst, netz):
    dienst.setze("_internet/R_STAHL/datenblatt-8146.pdf", seite=1)
    bom = vorgang(client, ZEILEN[1:])
    stufen = []
    suchen(bom, melde=stufen.append)
    item = position(bom, "240")
    assert dienst.fragen == ["R. STAHL 8146 Klemmenkasten Ex e"]
    (f,) = item.findings
    assert (f.source, f.file_path, f.matched_term, f.page) == (
        "suchdienst", "_internet/R_STAHL/datenblatt-8146.pdf", "8146", 1)
    assert f.check_result == "ok"  # Typ genau im Text, Kategorie 2G passt zu Zone 1
    assert f.check_message.endswith(" Die Ähnlichkeitssuche hat dieses Dokument gefunden.")
    assert f.belege is not None  # dieselbe Belegprüfung wie im Ordner
    assert item.status == "vorschlag"
    assert item.searched_in == (f"{OHNE_ORDNER} Die Ähnlichkeitssuche hat ein mögliches "
                                "Dokument gefunden.")
    assert netz == [] and stufen == ["Ähnlichkeitssuche"]


def test_kandidat_ohne_typ_ist_nur_unklar_und_internet_laeuft(client, ordner, dienst, netz):
    """QXZ-77 steht in keinem Dokument: ähnliche Datei aus dem Herstellerordner als Hinweis."""
    dienst.setze("Festo/Festo_DSM_Konf.pdf", seite=1, text="Schwenkantrieb  DSM-25-EX\n Zone 1")
    zeilen = [[30, 99000030, "Ventilinsel", "Festo", "QXZ-77-EX9", 1, "Zone 1"]]
    bom = vorgang(client, zeilen)
    stufen = []
    suchen(bom, melde=stufen.append)
    item = position(bom, "30")
    (f,) = item.findings
    assert (f.source, f.matched_term, f.page) == ("suchdienst", None, 1)
    assert f.check_result in ("unklar", "warnung")
    assert f.check_message.startswith(
        "Die Ähnlichkeitssuche schlägt dieses Dokument nur vor, weil Seite 1 ähnlich klingt wie "
        "die Position. Der Typ steht nicht darin. Bitte prüfen Sie, ob es trotzdem passt. ")
    assert f.snippet == "Schwenkantrieb DSM-25-EX Zone 1"
    assert item.searched_in == (f"{MIT_ORDNER} Die Ähnlichkeitssuche hat ein mögliches Dokument "
                                f"gefunden. {INTERNET_SATZ}")
    assert netz == ["30"] and stufen == ["Ähnlichkeitssuche", "Internet"]


def test_ohne_typ_aus_fremdem_ordner_kein_vorschlag(client, ordner, dienst, netz):
    """R. STAHL hat keinen Ordner: eine ähnliche Datei ohne Typ ist kein Vorschlag."""
    dienst.setze("Festo/Festo_DSM_Konf.pdf")
    zeilen = [[250, 99000250, "Sensor", "R. STAHL", "MS-NAMUR-12", 2, "Zone 2"]]
    bom = vorgang(client, zeilen)
    suchen(bom, versuche=0)
    item = position(bom, "250")
    assert item.findings == []
    assert item.status == "nicht_gefunden"
    assert item.searched_in == (f"{OHNE_ORDNER} Die Ähnlichkeitssuche hat ein ähnliches Dokument "
                                "angesehen, es passt aber nicht zur Position. "
                                f"{suche.INTERNET_AUS}")


def test_hoechstens_drei_vorschlaege_aus_fuenf_kandidaten(client, ordner, dienst, netz):
    pfade = [f"_internet/R_STAHL/d{i}.pdf" for i in range(7)]
    for i, p in enumerate(pfade):
        pdf(ordner / p, STAHL + f"\nVariante {i}")
    dienst.setze(*pfade)
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom)
    item = position(bom, "240")
    assert [f.file_path for f in item.findings] == pfade[:suche.RAG_VORSCHLAEGE]
    assert item.searched_in.endswith("Die Ähnlichkeitssuche hat drei mögliche Dokumente gefunden.")


def test_ohne_typ_hoechstens_zwei(client, ordner, dienst, netz):
    dienst.setze("Festo/Festo_DSM_Konf.pdf", "Festo/Festo_noch_eins.pdf",
                 "Festo/Festo_IBExU16ATEXB021X_de.pdf")
    zeilen = [[30, 99000030, "Ventilinsel", "Festo", "QXZ-77-EX9", 1, "Zone 1"]]
    bom = vorgang(client, zeilen)
    suchen(bom, versuche=0)
    funde = position(bom, "30").findings
    assert len(funde) == suche.RAG_OHNE_TREFFER
    assert all(f.check_result != "ok" and f.matched_term is None for f in funde)


def test_abgelehnte_datei_kommt_nicht_wieder(client, ordner, dienst, netz):
    dienst.setze("_internet/R_STAHL/datenblatt-8146.pdf")
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom)
    item = position(bom, "240")
    item.findings[0].status = "abgelehnt"
    db.session.commit()
    suchen(bom)
    assert len(item.findings) == 1
    assert item.searched_in == (f"{OHNE_ORDNER} Die Ähnlichkeitssuche hat nur Dokumente gefunden, "
                                "die für diese Position schon vorgeschlagen oder abgelehnt sind. "
                                f"{INTERNET_SATZ}")
    assert netz == ["240"]  # die abgelehnte zählt nicht: weiter ins Internet


def test_mit_archiv_keine_aehnlichkeitssuche(client, ordner, dienst, netz, monkeypatch):
    """Mit Dokumentenarchiv geht es nach Datenbank und Archiv gleich ins Internet."""
    client.application.config["ZERTIFIKATSSERVER_URL"] = "http://zert:5100"
    monkeypatch.setattr(zertifikatsserver, "auszug", lambda wurzel, hersteller: (None, True))
    dienst.setze("_internet/R_STAHL/datenblatt-8146.pdf")
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom)
    item = position(bom, "240")
    assert item.findings == [] and dienst.fragen == []
    assert "Ähnlichkeitssuche" not in item.searched_in
    assert netz == ["240"]


def test_dienst_nicht_erreichbar_suche_geht_weiter(client, ordner, monkeypatch, netz):
    def kaputt(*a, **k):
        raise httpx.ConnectError("weg")
    monkeypatch.setattr(rag.httpx, "post", kaputt)
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom)
    item = position(bom, "240")
    assert item.findings == []
    assert item.searched_in == (f"{OHNE_ORDNER} Die Ähnlichkeitssuche war gerade nicht "
                                f"verfügbar. {INTERNET_SATZ}")
    assert netz == ["240"]


def test_index_wird_noch_gebaut(client, ordner, monkeypatch, netz):
    antwort = httpx.Response(503, json={"detail": "Index wird gebaut (94 Dateien)"},
                             request=httpx.Request("POST", "http://suchdienst:8001/api/search"))
    monkeypatch.setattr(rag.httpx, "post", lambda *a, **k: antwort)
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom, versuche=0)
    assert position(bom, "240").searched_in == (
        f"{OHNE_ORDNER} Die Ähnlichkeitssuche bereitet die Dokumente noch vor und konnte noch "
        f"nicht suchen. {suche.INTERNET_AUS}")


def test_antwort_des_dienstes_wird_gelesen(client, ordner, monkeypatch):
    """Echter Weg durch rag.textstellen/kandidaten: nur PDFs unter ATEX_DOCS, eine je Datei."""
    gesendet = []

    def post(url, json, timeout):
        gesendet.append((url, json))
        return httpx.Response(200, request=httpx.Request("POST", url), json=[
            {"relpath": "_internet/R_STAHL/datenblatt-8146.pdf", "page": 2, "text": "a"},
            {"relpath": "_internet/R_STAHL/datenblatt-8146.pdf", "page": 3, "text": "b"},
            {"relpath": "../../etc/passwd", "page": 1, "text": "c"},
            {"relpath": "Festo/fehlt.pdf", "page": 1, "text": "d"},
            {"relpath": "Festo/tabelle.xlsx", "page": "Blatt1", "text": "e"},
            {"relpath": None, "page": 1, "text": "f"}])
    monkeypatch.setattr(rag.httpx, "post", post)
    bom = vorgang(client, ZEILEN[1:])
    kand = rag.kandidaten(position(bom, "240"), ordner)
    assert [(k.pfad.name, k.seite, k.text, k.rang) for k in kand] == [
        ("datenblatt-8146.pdf", 2, "a", 1)]
    assert gesendet == [("http://suchdienst:8001/api/search",
                         {"q": "R. STAHL 8146 Klemmenkasten Ex e", "k": rag.TEXTSTELLEN})]


MODELLE = """
modelle:
  - name: beispiel-chat
    art: chat
    modell: openai/beispiel-modell
    api_base: https://llm.example/v1
    api_key: "-"
  - name: beispiel-embedding
    art: embedding
    modell: openai/beispiel-embedding
    api_base: https://llm.example/v1
    api_key: "-"
standard:
  rag_antwort: beispiel-chat
  rag_embedding: beispiel-embedding
"""


def test_anfrage_nennt_das_modell_aus_der_modelldatei(client, ordner, monkeypatch, tmp_path):
    """Mit Modelldatei (die auch der Suchdienst liest) schickt die Anfrage den Modellnamen für
    „rag_antwort“; kennt der Dienst ihn nicht (400), geht es ohne Modell weiter."""
    pfad = tmp_path / "modelle.yaml"
    pfad.write_text(MODELLE, encoding="utf-8")
    client.application.config["MODELLE_DATEI"] = str(pfad)
    gesendet, antworten = [], [400, 200]

    def post(url, json, timeout):
        gesendet.append(dict(json))
        code = antworten.pop(0) if antworten else 200
        return httpx.Response(code, request=httpx.Request("POST", url),
                              json={"detail": "Modell unbekannt"} if code == 400 else [])
    monkeypatch.setattr(rag.httpx, "post", post)
    bom = vorgang(client, ZEILEN[1:])
    assert rag.textstellen("frage") == []
    assert gesendet == [{"q": "frage", "k": rag.TEXTSTELLEN, "modell": "beispiel-chat"},
                        {"q": "frage", "k": rag.TEXTSTELLEN}]
    # Modell des Vorgangs im Lauf: modelle.setze_lauf_modell
    from app.services import modelle
    gesendet.clear()
    modelle.setze_lauf_modell("beispiel-chat")
    rag.kandidaten(position(bom, "240"), ordner)
    assert gesendet[0]["modell"] == "beispiel-chat" and len(gesendet) == 1


def test_ohne_modelldatei_kein_modellname(client, ordner, monkeypatch):
    """Ohne Datei hieße das Modell „standard“, das kennt der Suchdienst nicht: nicht mitschicken."""
    gesendet = []

    def post(url, json, timeout):
        gesendet.append(dict(json))
        return httpx.Response(200, request=httpx.Request("POST", url), json=[])
    monkeypatch.setattr(rag.httpx, "post", post)
    rag.textstellen("frage")
    assert gesendet == [{"q": "frage", "k": rag.TEXTSTELLEN}]


def test_ohne_search_url_ist_die_stufe_aus(client, ordner, dienst, netz):
    client.application.config["SEARCH_URL"] = ""
    dienst.setze("_internet/R_STAHL/datenblatt-8146.pdf")
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom)
    assert dienst.fragen == []
    assert position(bom, "240").findings == []
    assert netz == ["240"]


def test_mehrere_ordner_je_position(client, ordner, dienst, netz, monkeypatch):
    """ordner_fuer liefert eine Liste von Ordnern: Funde aus allen, ohne Dubletten."""
    monkeypatch.setattr(suche, "ordner_fuer", lambda wurzel, item: (
        [wurzel / "_internet" / "R_STAHL", wurzel / "Rittal"], "Gesucht in zwei Ordnern."))
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom)
    item = position(bom, "240")
    assert sorted(f.file_path for f in item.findings) == [
        "Rittal/Rittal_anderes_8146.pdf", "_internet/R_STAHL/datenblatt-8146.pdf"]
    assert {f.source for f in item.findings} == {"ordner"}
    assert dienst.fragen == []


def test_quelle_in_der_oberflaeche(client, ordner, dienst, netz):
    from app.services import oberflaeche
    dienst.setze("_internet/R_STAHL/datenblatt-8146.pdf")
    bom = vorgang(client, ZEILEN[1:])
    suchen(bom)
    f = db.session.scalar(db.select(Finding))
    assert oberflaeche.fund(f)["quelle"] == "Ähnlichkeitssuche"
