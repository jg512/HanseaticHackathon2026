import io

import httpx
import pymupdf
import pytest
from conftest import xlsx
from test_suche import VTUG_DE, pdf

from app import llm
from app.db import db
from app.models import Finding
from app.services import internet
from app.services import suche as suche_dienst
from app.services.pruefung import lies_typangabe
from app.services.suche import herstellerordner

MIT_ORDNER = "Gesucht hat der Agent in der Datenbank und im Ordner des Herstellers."
OHNE_ORDNER = ("Gesucht hat der Agent in der Datenbank. Einen Ordner für diesen Hersteller gibt "
               "es nicht.")

ZEILEN = [
    [20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
    [240, 99000240, "Klemmenkasten Ex e", "R. STAHL", "8146", 2, "Zone 1"],
    [250, 99000250, "Induktiver Sensor", "Muster Sensorik GmbH", "MS-NAMUR-12", 2, "Zone 2"],
]
START = "https://r-stahl.com/de/global/home/"
DOC = ("EU-Konformitätserklärung\nKlemmenkasten Typ 8146/1\nTyp 8146\n"
       "II 2G Ex eb IIC T6 Gb\nPTB 01 ATEX 1105")


def pdf_bytes(text):
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), text, fontsize=8)
    return doc.tobytes()


class Netz:
    """Ersetzt das Internet: seiten {URL: (Status, Inhalt)}, Inhalt ist schon Markdown."""

    def __init__(self, seiten):
        self.seiten, self.anfragen = seiten, []

    def __call__(self, request):
        url = str(request.url)
        self.anfragen.append(url)
        antwort = self.seiten.get(url, (404, ""))
        if isinstance(antwort, Exception):
            raise antwort
        if isinstance(antwort, httpx.Response):
            return antwort
        status, inhalt = antwort
        if isinstance(inhalt, bytes):
            return httpx.Response(status, content=inhalt, headers={"content-type": "application/pdf"})
        return httpx.Response(status, text=inhalt, headers={"content-type": "text/html"})


@pytest.fixture
def netz(client, tmp_path, monkeypatch):
    """Ordner nur mit Festo, Internet ersetzt, html2markdown ersetzt (Inhalt ist Markdown)."""
    pdf(tmp_path / "Festo" / "Festo_IBExU16ATEXB021X_de.pdf", VTUG_DE)
    client.application.config.update(ATEX_DOCS=str(tmp_path), INTERNET_VERSUCHE_PRO_TEIL=10)
    n = Netz({"https://r-stahl.com/robots.txt": (200, "User-agent: *\nDisallow: /intern/\n")})
    monkeypatch.setattr(internet, "neuer_client", lambda: httpx.Client(
        transport=httpx.MockTransport(n), headers={"User-Agent": internet.USER_AGENT}))
    monkeypatch.setattr(internet, "markdown", lambda html: html.decode())
    return n


def suche(client, **daten):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    r = client.post(f"/api/boms/{r.get_json()['id']}/search", json=daten)
    assert r.status_code == 200
    return {i["pos"]: i for i in r.get_json()["items"]}


def test_markdown_links():
    md = ("[Konformitätserklärung\\_8146 \\*neu\\*](docs/a%20%281%29.pdf) "
          "[![Bild](i.png) Download \\[EN\\]](/x?a=1&b=2) [Mail](mailto:a@b.de) ![Logo](/l.png) "
          "[Treffer](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fr-stahl.com%2Fa.pdf&rut=1) "
          "[Anker](#oben) [Titel](<https://r-stahl.com/b c> \"Titel\")")
    assert internet.links(md, "https://r-stahl.com/de/seite/") == [
        ("Konformitätserklärung_8146 *neu*", "https://r-stahl.com/de/seite/docs/a%20%281%29.pdf"),
        ("Bild Download [EN]", "https://r-stahl.com/x?a=1&b=2"),
        ("Treffer", "https://r-stahl.com/a.pdf"),
        ("Anker", "https://r-stahl.com/de/seite/"),
        ("Titel", "https://r-stahl.com/b c")]


def test_domain_regel():
    assert internet.erlaubt("https://r-stahl.com/a.pdf", "r-stahl.com")
    assert internet.erlaubt("http://files.r-stahl.com/a.pdf", "r-stahl.com")
    assert not internet.erlaubt("https://evil-r-stahl.com/a.pdf", "r-stahl.com")
    assert not internet.erlaubt("https://r-stahl.com.evil.de/a.pdf", "r-stahl.com")
    assert not internet.erlaubt("ftp://r-stahl.com/a.pdf", "r-stahl.com")
    assert internet.seite_des_herstellers("R. STAHL")[0] == "r-stahl.com"
    assert internet.seite_des_herstellers("Pepperl+Fuchs GmbH")[0] == "pepperl-fuchs.com"
    assert internet.seite_des_herstellers("Muster Sensorik GmbH") is None
    assert internet.seite_des_herstellers(None) is None


def test_bewertung_der_links():
    angabe = lies_typangabe("8146")
    assert internet.bewerte("EU-Konformitätserklärung 8146", "https://r-stahl.com/k.pdf", angabe) == 8
    assert internet.bewerte("Downloads", "https://r-stahl.com/downloads/", angabe) == 1
    assert internet.bewerte("Datenblatt", "https://r-stahl.com/x.pdf", angabe) == 0
    assert internet.bewerte("ATEX-Zertifikate", "https://r-stahl.com/login/", angabe) == 0
    assert internet.bewerte("Über uns", "https://r-stahl.com/firma/", angabe) == 0


def test_fund_wird_vorschlag_mit_quelle(client, netz, tmp_path):
    netz.seiten.update({
        START: (200, "[Über uns](/firma/) [Downloads](/de/downloads/) "
                     "[Zertifikat 8146](https://fremd.example/8146-atex.pdf)"),
        "https://r-stahl.com/de/downloads/": (200, "[EU-Konformitätserklärung 8146](/dl/8146_DoC.pdf) "
                                                    "[Intern ATEX 8146](/intern/8146.pdf)"),
        "https://r-stahl.com/dl/8146_DoC.pdf": (200, pdf_bytes(DOC)),
    })
    items = suche(client)

    # Ordner hat Pos. 20 gefunden: kein Internet. Unbekannter Hersteller: übersprungen.
    assert items["20"]["searched_in"] == MIT_ORDNER
    assert items["250"]["searched_in"] == (
        f"{OHNE_ORDNER} Im Internet hat der Agent nicht gesucht, weil die Webseite des "
        "Herstellers nicht bekannt ist.")
    assert items["250"]["status"] == "nicht_gefunden"
    # fremde Domain, robots.txt-Sperre und „Über uns“ werden nicht abgerufen
    assert netz.anfragen == ["https://r-stahl.com/robots.txt", START,
                             "https://r-stahl.com/de/downloads/", "https://r-stahl.com/dl/8146_DoC.pdf"]
    assert items["240"]["searched_in"] == (
        f"{OHNE_ORDNER} Im Internet hat der Agent auf r-stahl.com gesucht und dabei 4 Seiten von "
        "höchstens 10 abgerufen. Dort hat er ein passendes Dokument gefunden.")
    assert items["240"]["status"] == "vorschlag"

    (f,) = db.session.scalars(db.select(Finding).filter_by(source="internet"))
    assert (f.url, f.file_path) == ("https://r-stahl.com/dl/8146_DoC.pdf", "_internet/R_STAHL/8146_DoC.pdf")
    assert (f.doc_type, f.page, f.matched_term, f.check_result) == (
        "eu_konformitaetserklaerung", 1, "8146", "ok")
    assert (f.ex_marking_raw, f.certificate_no) == ("II 2G Ex eb IIC T6 Gb", "PTB 01 ATEX 1105")
    assert f.check_message.endswith(
        "Das Dokument stammt von der Webseite des Herstellers (https://r-stahl.com/dl/8146_DoC.pdf).")
    assert (tmp_path / "_internet" / "R_STAHL" / "8146_DoC.pdf").read_bytes().startswith(b"%PDF")
    # Datei und Seitenbild gehen wie bei Ordner-Funden
    assert client.get(f"/api/findings/{f.id}/page.png").status_code == 200
    # der Zwischenspeicher ist kein Herstellerordner
    assert herstellerordner(tmp_path, "Internet GmbH") is None


def test_erneute_suche_ohne_dublette(client, netz):
    netz.seiten.update({START: (200, "[EU-Konformitätserklärung 8146](/dl/8146_DoC.pdf)"),
                        "https://r-stahl.com/dl/8146_DoC.pdf": (200, pdf_bytes(DOC))})
    suche(client)
    items = {i["pos"]: i for i in client.post("/api/boms/1/search").get_json()["items"]}
    assert len(db.session.scalars(db.select(Finding).filter_by(source="internet")).all()) == 1
    assert items["240"]["status"] == "vorschlag"


def test_pdf_ohne_typ_oder_kein_pdf_ist_kein_vorschlag(client, netz, tmp_path):
    netz.seiten.update({
        START: (200, "[Konformitätserklärung](/a.pdf) [ATEX-Zertifikat](/b.pdf)"),
        "https://r-stahl.com/a.pdf": (200, pdf_bytes("EU-Konformitätserklärung Typ 8150")),
        "https://r-stahl.com/b.pdf": (200, "<html>kein PDF</html>".encode()),
    })
    items = suche(client)
    assert items["240"]["status"] == "nicht_gefunden"
    assert items["240"]["searched_in"].endswith(
        "dabei 4 Seiten von höchstens 10 abgerufen. Ein passendes Dokument war nicht dabei.")
    assert db.session.scalars(db.select(Finding).filter_by(source="internet")).all() == []
    assert list((tmp_path / "_internet" / "R_STAHL").iterdir()) == []  # nichts liegen gelassen


def test_groessengrenze(client, netz, monkeypatch):
    monkeypatch.setattr(internet, "MAX_BYTES", 1000)
    netz.seiten.update({START: (200, "[Konformitätserklärung 8146](/gross.pdf)"),
                        "https://r-stahl.com/gross.pdf": (200, pdf_bytes(DOC) + b" " * 2000)})
    items = suche(client)
    assert items["240"]["status"] == "nicht_gefunden"
    # der Rohtext („Datei größer als 30 MB“) steht im Log, für den Menschen ein Satz
    assert items["240"]["searched_in"].endswith("Eine Seite ließ sich nicht abrufen.")


def test_grenze_stoppt_genau(client, netz):
    # jede Seite verweist auf zwei weitere Download-Seiten: ohne Grenze endlos
    for i in range(1, 100):
        netz.seiten[f"https://r-stahl.com/d{i}/"] = (
            200, f"[Downloads](/d{2 * i}/) [Downloads](/d{2 * i + 1}/)")
    netz.seiten[START] = (200, "[Downloads](/d1/)")
    items = suche(client, internet_versuche=7)
    assert len(netz.anfragen) == 7
    assert items["240"]["searched_in"] == (
        f"{OHNE_ORDNER} Im Internet hat der Agent auf r-stahl.com gesucht und dabei 7 Seiten von "
        "höchstens 7 abgerufen. Danach war die eingestellte Zahl an Versuchen erreicht.")


def test_wert_aus_der_anfrage_schlaegt_den_standard(client, netz):
    netz.seiten[START] = (200, "[Downloads](/d/)")
    netz.seiten["https://r-stahl.com/d/"] = (200, "[Downloads](/e/)")
    items = suche(client, internet_versuche=2)
    assert netz.anfragen == ["https://r-stahl.com/robots.txt", START]
    assert ("2 Seiten von höchstens 2 abgerufen. Danach war die eingestellte Zahl an Versuchen "
            "erreicht.") in items["240"]["searched_in"]


def test_null_schaltet_ab(client, netz):
    items = suche(client, internet_versuche=0)
    assert netz.anfragen == []
    assert items["240"]["searched_in"] == f"{OHNE_ORDNER} {suche_dienst.INTERNET_AUS}"


@pytest.mark.parametrize("wert", [-1, 51, "5", 2.5, True])
def test_ungueltige_versuche(client, wert):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    r = client.post(f"/api/boms/{r.get_json()['id']}/search", json={"internet_versuche": wert})
    assert r.status_code == 400
    assert "0 bis 50" in r.get_json()["error"]


def test_einstellungen(client):
    assert client.get("/api/boms/search-settings").get_json() == {
        "internet_versuche": 0, "internet_versuche_max": 50}


def test_gesperrter_host_wird_im_lauf_nicht_mehr_gefragt(client, netz):
    netz.seiten[START] = (403, "Access Denied")
    zeilen = ZEILEN + [[245, 99000245, "Klemmenkasten", "R. STAHL", "8125", 1, "Zone 1"]]
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "liste.xlsx")})
    items = {i["pos"]: i for i in client.post(f"/api/boms/{r.get_json()['id']}/search").get_json()["items"]}
    assert netz.anfragen == ["https://r-stahl.com/robots.txt", START]
    assert items["240"]["searched_in"] == (
        f"{OHNE_ORDNER} Im Internet hat der Agent auf r-stahl.com gesucht und dabei 2 Seiten von "
        "höchstens 10 abgerufen. Die Webseite r-stahl.com lässt keine automatischen Abrufe zu.")
    assert items["245"]["searched_in"].endswith(
        "dabei keine Seite von höchstens 10 abgerufen. Die Webseite r-stahl.com lässt keine "
        "automatischen Abrufe zu.")


def test_pruefseite_mensch_oder_bot(client, netz):
    netz.seiten[START] = (200, "Please enable JavaScript and cookies to continue")
    items = suche(client)
    assert items["240"]["searched_in"].endswith(
        "Die Webseite r-stahl.com verlangt eine Bestätigung, dass ein Mensch sie aufruft.")


def test_netzfehler_bricht_die_suche_nicht_ab(client, netz):
    netz.seiten["https://r-stahl.com/robots.txt"] = httpx.ConnectError("keine Verbindung")
    items = suche(client)
    assert items["240"]["status"] == "nicht_gefunden"
    assert items["240"]["searched_in"].endswith("Die Webseite r-stahl.com war nicht erreichbar.")
    assert items["20"]["status"] == "vorschlag"


def test_weiterleitung_nur_beim_hersteller(client, netz):
    netz.seiten[START] = (200, "[Konformitätserklärung 8146](/dl/umleitung.pdf)")
    netz.seiten["https://r-stahl.com/dl/umleitung.pdf"] = httpx.Response(
        302, headers={"location": "https://fremd.example/8146.pdf"})
    items = suche(client)
    assert netz.anfragen[-1] == "https://r-stahl.com/dl/umleitung.pdf"  # dem Ziel nicht gefolgt
    assert items["240"]["status"] == "nicht_gefunden"


def test_sprachmodell_waehlt_nur_aus_der_liste(client, netz, monkeypatch):
    client.application.config["INTERNET_LLM"] = True
    netz.seiten[START] = (200, "[Downloads](/a/) [Zertifikate ATEX](/b/)")
    netz.seiten["https://r-stahl.com/b/"] = (200, "")
    fragen = []
    monkeypatch.setattr(llm, "chat", lambda nachrichten, max_tokens: fragen.append(nachrichten)
                        or "https://r-stahl.com/b/\nhttps://boese.example/x.pdf")
    suche(client)
    # erst die Wahl des Modells, dann der Rest der Liste als Reserve, weil nichts gefunden ist;
    # die erfundene Adresse nie
    assert netz.anfragen == ["https://r-stahl.com/robots.txt", START, "https://r-stahl.com/b/",
                             "https://r-stahl.com/a/"]
    assert "https://r-stahl.com/a/" in fragen[0][1]["content"]  # die Liste ging ans Modell


def test_nach_einem_fund_der_modellwahl_bleibt_die_reserve_liegen(client, netz, monkeypatch):
    client.application.config["INTERNET_LLM"] = True
    netz.seiten[START] = (200, "[Downloads](/a/) [Zertifikat 8146 ATEX](/dl/8146_DoC.pdf)")
    netz.seiten["https://r-stahl.com/dl/8146_DoC.pdf"] = (200, pdf_bytes(DOC))
    monkeypatch.setattr(llm, "chat", lambda nachrichten, max_tokens:
                        "https://r-stahl.com/dl/8146_DoC.pdf")
    items = suche(client)
    assert items["240"]["status"] == "vorschlag"
    assert "https://r-stahl.com/a/" not in netz.anfragen


def test_datenblatt_aus_dem_internet_ist_kein_fund(client, netz):
    """Ein Datenblatt nennt den Typ und führt „Konformitätserklärungen“ nur als Zulassung auf."""
    blatt = ("R. STAHL Schaltgeräte GmbH\nKlemmenkasten Ex e 8146/1041\nZulassungen ATEX (PTB)\n"
             "Konformitätserklärungen ATEX (EUK)\nII 2G Ex eb IIC T6 Gb")
    netz.seiten[START] = (200, "[Ex-Einzeldatenblatt 8146 ATEX](/dl/ex-einzeldatenblatt-8146.pdf)")
    netz.seiten["https://r-stahl.com/dl/ex-einzeldatenblatt-8146.pdf"] = (200, pdf_bytes(blatt))
    items = suche(client)
    assert items["240"]["status"] == "nicht_gefunden"
    assert db.session.scalars(db.select(Finding).filter_by(source="internet")).all() == []
    assert ("Gefunden hat er dort nur Datenblätter. Die sind kein Nachweis und deshalb nicht "
            "vorgeschlagen.") in items["240"]["searched_in"]


def test_mehrzahl_konformitaetserklaerungen_ist_keine_erklaerung():
    from app.services.suche import dokumentart
    blatt = ["Klemmenkasten 8146\nZulassungen\nKonformitätserklärungen ATEX (EUK)"]
    assert dokumentart(blatt) == "sonstiges"
    assert dokumentart(blatt, "ex-einzeldatenblatt-8146.pdf") == "datenblatt"
    assert dokumentart(["Datenblatt\nKlemmenkasten 8146"]) == "datenblatt"
    assert dokumentart(["Declaration of conformity 8146"],
                       "ex-product-overview-monitor-8146-5-en-gb.pdf") == "datenblatt"
    assert dokumentart(["EU-Konformitätserklärung\nTyp 8146"]) == "eu_konformitaetserklaerung"
    assert dokumentart(["EU-Konformitätserklärung\nTyp 8146"], "8146_DoC.pdf") \
        == "eu_konformitaetserklaerung"


def test_frueher_vorgeschlagenes_datenblatt_faellt_bei_der_naechsten_suche_weg(client, netz):
    from app.models import BomItem
    netz.seiten[START] = (200, "")
    suche(client)
    item = db.session.scalar(db.select(BomItem).filter_by(pos="240"))
    item.findings.append(Finding(source="internet", status="vorgeschlagen", check_result="ok",
                                 doc_type="eu_konformitaetserklaerung", file_hash="a" * 64,
                                 file_path="_internet/R_STAHL/ex-einzeldatenblatt-8146.pdf"))
    db.session.commit()
    r = client.post(f"/api/boms/{item.bom_id}/search", json={})
    assert r.status_code == 200
    assert db.session.scalars(db.select(Finding).filter_by(source="internet")).all() == []
