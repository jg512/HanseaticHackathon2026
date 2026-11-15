"""Eine Ablage für PDFs: Freigaben gehen an den Zertifikatsserver, Dokumente nur im Server
findet die Suche über zertifikatsserver.ordner. Der Server ist hier ein Ersatz im Speicher."""

import hashlib
import io
import re

import httpx
import pytest
from conftest import xlsx
from test_suche import VACF, pdf

from app.db import db
from app.models import BomItem, Finding
from app.models.bom import BomEvent
from app.services import freigabe, suche, zertifikatsserver

SERVER = "http://zertifikatsserver:5100"
ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1-5", 4, "Zone 1"]]


def norm(text):
    return re.sub(r"[\W_]", "", (text or "").lower())


class Server:
    """Ersatz für den Zertifikatsserver: die Endpunkte, die das Backend benutzt."""

    def __init__(self):
        self.dokumente, self.teile, self.versionen, self.anfragen = [], [], [], []
        self.erreichbar = True
        self.upload_fehler = None  # (Code, Meldung) für POST …/dokumente

    def dokument(self, inhalt, hersteller="Festo", dateiname="neu.pdf", **mehr):
        d = {"id": len(self.dokumente) + 1, "sha256": hashlib.sha256(inhalt).hexdigest(),
             "hersteller": hersteller, "dateiname": dateiname, "ablage": "aktuell",
             "ausgemustert": False, "markierungsstand": "ohne", "gruende": [], "inhalt": inhalt}
        d.update(mehr)
        d["pdf_url"] = f"/api/dokumente/{d['id']}/pdf"
        self.dokumente.append(d)
        return d

    @staticmethod
    def _antwort(code, daten=None, inhalt=None, url=""):
        anfrage = httpx.Request("GET", url)
        if inhalt is not None:
            return httpx.Response(code, content=inhalt, request=anfrage)
        return httpx.Response(code, json=daten, request=anfrage)

    def get(self, url, params=None, timeout=None):
        return self.request("GET", url, params=params, timeout=timeout)

    def request(self, methode, url, params=None, json=None, data=None, files=None, timeout=None):
        if not self.erreichbar:
            raise httpx.ConnectError("weg")
        pfad = url.removeprefix(SERVER)
        self.anfragen.append((methode, pfad))
        params = params or {}
        if (methode, pfad) == ("GET", "/api/dokumente"):
            liste = [d for d in self.dokumente
                     if (not params.get("sha256") or d["sha256"] in params["sha256"].split(","))
                     and (not params.get("hersteller")
                          or norm(d["hersteller"]).startswith(norm(params["hersteller"])))
                     and (not params.get("ablage") or d["ablage"] == params["ablage"])
                     and (params.get("ausgemustert") != "nein" or not d["ausgemustert"])]
            return self._antwort(200, [{k: v for k, v in d.items() if k != "inhalt"}
                                       for d in liste])
        if m := re.fullmatch(r"/api/dokumente/(\d+)/pdf", pfad):
            return self._antwort(200, inhalt=self.dokumente[int(m[1]) - 1]["inhalt"])
        if (methode, pfad) == ("GET", "/api/teile"):
            return self._antwort(200, [dict(t, treffer="genau") for t in self.teile
                                       if norm(t["artikelnr"]) == norm(params["artikelnr"])])
        if (methode, pfad) == ("POST", "/api/teile"):
            t = {"id": len(self.teile) + 1, **json}
            self.teile.append(t)
            return self._antwort(201, t)
        if m := re.fullmatch(r"/api/teile/(\d+)/dokumente", pfad):
            if self.upload_fehler:
                return self._antwort(self.upload_fehler[0], {"error": self.upload_fehler[1]})
            teil = self.teile[int(m[1]) - 1]
            name, inhalt, _ = files["datei"]
            d = self.dokument(inhalt, teil["hersteller"], name)
            v = {"version": 1 + sum(x["teil_id"] == teil["id"] for x in self.versionen),
                 "teil_id": teil["id"], "dokument_id": d["id"], "hersteller": teil["hersteller"],
                 "artikelnr": teil["artikelnr"], **data}
            self.versionen.append(v)
            return self._antwort(201, {"verknuepfung": v})
        return self._antwort(404, {"error": "Nicht gefunden."})


@pytest.fixture
def server(client, tmp_path, monkeypatch):
    client.application.config.update(ATEX_DOCS=str(tmp_path), ZERTIFIKATSSERVER_URL=SERVER)
    s = Server()
    monkeypatch.setattr(zertifikatsserver.httpx, "get", s.get)
    monkeypatch.setattr(zertifikatsserver.httpx, "request", s.request)
    return s


def vorgang(client):
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "l.xlsx")}).get_json()
    return bom["id"], bom["items"][0]["id"]


def hochladen(client, bom_id, item_id, inhalt, quelle="manuell", name="Eva"):
    daten = {"file": (io.BytesIO(inhalt), "antwort.pdf"), "by": name, "source": quelle}
    if quelle == "manuell":
        daten["doc_type"] = "eu_konformitaetserklaerung"
    return client.post(f"/api/boms/{bom_id}/items/{item_id}/documents", data=daten)


def protokoll():
    return [e.text for e in db.session.scalars(db.select(BomEvent).order_by(BomEvent.id))]


@pytest.fixture
def neue_pdf(tmp_path_factory):
    """Inhalt einer PDF, die nicht unter ATEX_DOCS liegt."""
    ordner = tmp_path_factory.mktemp("quelle")
    return pdf(ordner / "neu.pdf", VACF + "\nAusgabe 2026").read_bytes()


# ---------- Freigabe -> Zertifikatsserver ----------

def test_manuell_eingefuegt_geht_als_neue_version_an_den_server(client, server, neue_pdf):
    bom_id, item_id = vorgang(client)
    r = hochladen(client, bom_id, item_id, neue_pdf)
    assert r.status_code == 201 and r.get_json()["status"] == "freigegeben"
    assert server.teile == [{"id": 1, "hersteller": "Festo", "artikelnr": "VACF-B-K1-1-5",
                             "von": "Eva", "profile_no": "99000010", "bezeichnung": "Magnetspule",
                             "grund": "Freigabe im ATEX-Agent"}]
    v = server.versionen[0]
    assert (v["von"], v["doc_type"], v["version"]) == ("Eva", "eu_konformitaetserklaerung", 1)
    assert v["hinweis"] == "Im ATEX-Agent freigegeben von Eva; Quelle: manuell hochgeladen"
    assert server.dokumente[0]["sha256"] == hashlib.sha256(neue_pdf).hexdigest()
    assert any("im Dokumentenarchiv, als Version 1 der eu_konformitaetserklaerung beim Teil "
               "Festo VACF-B-K1-1-5. Das Teil ist dort neu angelegt." in t for t in protokoll())


def test_antwort_des_herstellers_erst_nach_freigabe(client, server, neue_pdf):
    bom_id, item_id = vorgang(client)
    server.teile.append({"id": 1, "hersteller": "Festo", "artikelnr": "VACF-B-K1-1-5"})
    f = hochladen(client, bom_id, item_id, neue_pdf, quelle="hersteller").get_json()
    assert f["status"] == "vorgeschlagen" and server.versionen == []
    r = client.post(f"/api/findings/{f['id']}/decision", json={"decision": "freigeben", "by": "Jan"})
    assert r.status_code == 200
    v = server.versionen[0]
    assert (v["teil_id"], v["von"]) == (1, "Jan")  # vorhandenes Teil, keins neu angelegt
    assert v["hinweis"].endswith("Quelle: vom Hersteller") and len(server.teile) == 1


def test_bekannte_datei_wird_nicht_nochmal_geschickt(client, server, neue_pdf):
    server.dokument(neue_pdf)
    bom_id, item_id = vorgang(client)
    assert hochladen(client, bom_id, item_id, neue_pdf).status_code == 201
    assert server.versionen == [] and ("POST", "/api/teile") not in server.anfragen
    assert not any("Dokumentenarchiv" in t for t in protokoll())


def test_server_weg_freigabe_gilt_und_wird_nachgeholt(client, server, neue_pdf):
    server.erreichbar = False
    bom_id, item_id = vorgang(client)
    r = hochladen(client, bom_id, item_id, neue_pdf)
    assert r.status_code == 201 and r.get_json()["status"] == "freigegeben"
    assert db.session.scalar(db.select(Finding)).status == "freigegeben"
    assert any("ist noch nicht im Dokumentenarchiv. Das Dokumentenarchiv war nicht erreichbar. "
               "Die Freigabe gilt trotzdem" in t for t in protokoll())

    server.erreichbar = True
    cli = client.application.test_cli_runner()
    ergebnis = cli.invoke(args=["zert-abgleich"])
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "1 übertragen, 0 schon vorhanden, 0 nicht übertragen" in ergebnis.output
    assert len(server.versionen) == 1 and server.versionen[0]["von"] == "Eva"
    assert any("Die Datei ist damit nachgereicht" in t for t in protokoll())

    zweimal = cli.invoke(args=["zert-abgleich"])  # gleicher Hash: nichts mehr zu tun
    assert "0 übertragen, 1 schon vorhanden" in zweimal.output and len(server.versionen) == 1


def test_fehler_des_servers_haelt_die_freigabe_nicht_auf(client, server, neue_pdf):
    server.upload_fehler = (409, "Dokumentart nicht erkannt")
    bom_id, item_id = vorgang(client)
    r = hochladen(client, bom_id, item_id, neue_pdf)
    assert r.status_code == 201 and r.get_json()["status"] == "freigegeben"
    assert any("Das Dokumentenarchiv hat die Datei nicht angenommen. Dokumentart nicht erkannt"
               in t
               for t in protokoll())
    ergebnis = client.application.test_cli_runner().invoke(args=["zert-abgleich"])
    assert ergebnis.exit_code == 1 and "1 nicht übertragen" in ergebnis.output


def test_naechste_freigabe_holt_fehlende_nach(client, server, neue_pdf, tmp_path):
    server.erreichbar = False
    bom_id, item_id = vorgang(client)
    hochladen(client, bom_id, item_id, neue_pdf)
    server.erreichbar = True
    zweite = pdf(tmp_path / "quelle" / "b.pdf", VACF + "\nBetriebsanleitung").read_bytes()
    hochladen(client, bom_id, item_id, zweite, name="Jan")
    assert sorted(v["von"] for v in server.versionen) == ["Eva", "Jan"]


def test_ohne_server_url_keine_anfrage(client, server, neue_pdf):
    client.application.config["ZERTIFIKATSSERVER_URL"] = ""
    bom_id, item_id = vorgang(client)
    assert hochladen(client, bom_id, item_id, neue_pdf).status_code == 201
    assert server.anfragen == []
    assert zertifikatsserver.auszug(client.application.config["ATEX_DOCS"], "Festo") == (None, True)


def test_abgelehnt_geht_nicht_an_den_server(client, server, neue_pdf):
    bom_id, item_id = vorgang(client)
    f = hochladen(client, bom_id, item_id, neue_pdf, quelle="hersteller").get_json()
    client.post(f"/api/findings/{f['id']}/decision", json={"decision": "ablehnen", "by": "Jan"})
    assert server.versionen == []


# ---------- Dokumente des Herstellers im Archiv -> Suche ----------

def auszug(wurzel, hersteller):
    ordner, erreichbar = zertifikatsserver.auszug(wurzel, hersteller)
    assert erreichbar
    return ordner


def test_auszug_holt_alle_aktuellen_dokumente_des_herstellers(client, server, tmp_path, neue_pdf):
    alt = pdf(tmp_path / "Festo" / "alt.pdf", VACF).read_bytes()
    server.dokument(alt, dateiname="alt.pdf")  # liegt auch in ATEX_DOCS: kopiert, nicht geladen
    server.dokument(neue_pdf, dateiname="Festo Erklärung 2026.pdf")
    server.dokument(b"%PDF-veraltet", dateiname="v.pdf", markierungsstand="bestaetigt")
    server.dokument(b"%PDF-weg", dateiname="w.pdf", ausgemustert=True, ablage="archiv")
    server.dokument(b"%PDF-siemens", hersteller="Siemens", dateiname="s.pdf")

    vorher = len(server.anfragen)
    ordner = auszug(tmp_path, "FESTO")
    assert ordner == tmp_path / "_upload" / "zertifikatsserver" / "Festo"
    assert sorted(p.name for p in ordner.iterdir()) == ["Festo_Erklärung_2026.pdf", "alt.pdf"]
    assert (ordner / "Festo_Erklärung_2026.pdf").read_bytes() == neue_pdf
    assert (ordner / "alt.pdf").read_bytes() == alt
    assert len(server.anfragen) == vorher + 2  # die Liste und die eine PDF, die es nur dort gibt

    # Zweiter Aufruf lädt nichts neu
    vorher = len(server.anfragen)
    assert auszug(tmp_path, "Festo") == ordner
    assert len(server.anfragen) == vorher + 1  # nur die Liste

    # Die Suche findet die Dateien dort wie in jedem Herstellerordner, Vorschau innerhalb ATEX_DOCS
    bom_id, item_id = vorgang(client)
    item = db.session.get(BomItem, item_id)
    funde = sorted(suche.vorschlaege(item, ordner, tmp_path), key=lambda f: f.file_path)
    assert [f.file_path for f in funde] == [
        "_upload/zertifikatsserver/Festo/Festo_Erklärung_2026.pdf",
        "_upload/zertifikatsserver/Festo/alt.pdf"]
    assert freigabe.datei(funde[0], tmp_path) == ordner / "Festo_Erklärung_2026.pdf"


def test_veraltet_oder_ausgemustert_kommt_nicht_zurueck(client, server, tmp_path, neue_pdf):
    d = server.dokument(neue_pdf, dateiname="neu.pdf")
    ordner = auszug(tmp_path, "Festo")
    assert (ordner / "neu.pdf").is_file()

    d["markierungsstand"] = "bestaetigt"  # im Server als veraltet bestätigt
    assert auszug(tmp_path, "Festo") is None
    assert not (ordner / "neu.pdf").exists()
    weg = tmp_path / "_upload" / "zertifikatsserver" / "_zurueckgezogen" / "Festo" / "neu.pdf"
    assert weg.read_bytes() == neue_pdf  # nicht gelöscht, nur aus der Suche genommen

    d["markierungsstand"], d["ausgemustert"] = "ohne", True
    assert auszug(tmp_path, "Festo") is None


def test_auszug_server_weg_oder_unbekannt(client, server, tmp_path, neue_pdf):
    server.dokument(neue_pdf)
    assert zertifikatsserver.auszug(tmp_path, "Phoenix") == (None, True)
    assert zertifikatsserver.auszug(tmp_path, "") == (None, True)
    server.erreichbar = False
    assert zertifikatsserver.auszug(tmp_path, "Festo") == (None, False)


def test_auszug_falscher_hash_wird_nicht_abgelegt(client, server, tmp_path, neue_pdf):
    d = server.dokument(neue_pdf)
    d["sha256"] = "0" * 64
    assert auszug(tmp_path, "Festo") is None


# ---------- Quellennachweis und Download-Links (Dokumentenarchiv) ----------

@pytest.fixture
def ohne_linkspeicher():
    zertifikatsserver._link_speicher.clear()
    zertifikatsserver._gesperrt_bis[0] = 0.0
    yield
    zertifikatsserver._link_speicher.clear()
    zertifikatsserver._gesperrt_bis[0] = 0.0


def internet_fund(tmp_path, item_id, inhalt):
    """Fund der Websuche wie suche._finding: Datei unter _internet/, url = Link auf die PDF."""
    datei = tmp_path / "_internet" / "Festo" / "ke_vacf.pdf"
    datei.parent.mkdir(parents=True)
    datei.write_bytes(inhalt)
    f = Finding(bom_item_id=item_id, source="internet", doc_type="eu_konformitaetserklaerung",
                url="https://www.festo.com/media/KE%20VACF.pdf",
                file_path="_internet/Festo/ke_vacf.pdf",
                file_hash=hashlib.sha256(inhalt).hexdigest(), languages="de",
                check_result="ok", status="vorgeschlagen")
    db.session.add(f)
    db.session.commit()
    return f


def test_freigabe_schickt_quellennachweis_strukturiert(client, server, tmp_path, neue_pdf):
    bom_id, item_id = vorgang(client)
    f = internet_fund(tmp_path, item_id, neue_pdf)
    r = client.post(f"/api/findings/{f.id}/decision", json={"decision": "freigeben", "by": "Jan"})
    assert r.status_code == 200
    v = server.versionen[0]
    assert (v["quelle"], v["quelle_art"], v["quelle_link"], v["quelle_dateiname"]) == (
        "atex_agent", "internet", "https://www.festo.com/media/KE%20VACF.pdf", "KE VACF.pdf")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", v["quelle_abgerufen_am"])
    assert "quelle_seite" not in v  # die Websuche hält die Seite nicht fest: nichts erfunden
    assert v["hinweis"].startswith("Im ATEX-Agent freigegeben von Jan")  # für ältere Archive


def test_manuell_und_hersteller_als_art(client, server, neue_pdf):
    bom_id, item_id = vorgang(client)
    hochladen(client, bom_id, item_id, neue_pdf)
    v = server.versionen[0]
    assert (v["quelle_art"], v["quelle_dateiname"]) == ("hochgeladen", "antwort.pdf")
    assert "quelle_link" not in v


def test_pruefseite_zeigt_herkunft_des_fundes(client, server, tmp_path, neue_pdf):
    bom_id, item_id = vorgang(client)
    f = internet_fund(tmp_path, item_id, neue_pdf)
    a = client.get(f"/api/artikel/{item_id}").get_json()
    fund = next(x for x in a["funde"] if x["id"] == f.id)
    q = fund["quellennachweis"]
    assert (q["art"], q["link"], q["seite_gefunden_auf"], q["seite_bekannt"]) == (
        "internet", "https://www.festo.com/media/KE%20VACF.pdf", None, False)
    assert q["datei"] == "KE VACF.pdf" and q["abgerufen_am"]
    assert q["archiv_link"] == f"/zert/api/dateien/{f.file_hash}"
    assert "hält nicht fest, auf welcher Webseite" in q["text"]
    assert isinstance(fund["quelle"], str)  # der Text des Vertrags bleibt, wie index.html ihn zeigt
    client.application.config["ZERTIFIKATSSERVER_URL"] = ""
    fund = next(x for x in client.get(f"/api/artikel/{item_id}").get_json()["funde"]
                if x["id"] == f.id)
    assert fund["quellennachweis"]["archiv_link"] is None  # ohne Archiv kein Link dorthin


def test_auszug_holt_nur_pdfs_aus_dem_archiv(client, server, tmp_path, neue_pdf):
    server.dokument(b"PK\x03\x04word", dateiname="Datenblatt.docx", dateiart="docx")
    assert auszug(tmp_path, "Festo") is None
    server.dokument(neue_pdf, dateiname="ke.pdf", dateiart="pdf")
    ordner = auszug(tmp_path, "Festo")
    assert [p.name for p in ordner.iterdir()] == ["ke.pdf"]


def test_dl_link_hersteller_vor_archiv_in_spaltenfolge(client, server, ohne_linkspeicher):
    from test_export import bauteil, dokument as stammdokument, vorgang as export_vorgang

    from app.models import Bom
    from app.services import excel_export
    client.application.config["OEFFENTLICHE_ADRESSE"] = "http://atex.example:8000/"
    im_archiv = server.dokument(b"%PDF-im-archiv", dateiname="ke de.pdf")
    im_archiv["datei_url"] = f"/api/dokumente/{im_archiv['id']}/datei"
    teil = bauteil("99000001", manufacturer_url="https://www.festo.com/")  # nur die Webseite
    stammdokument(teil, "eu_baumusterpruefbescheinigung", "Festo/zert.pdf", ["de", "en"])
    stammdokument(teil, "eu_konformitaetserklaerung", "Festo/ke.pdf", ["de"])
    stammdokument(teil, "bedienungsanleitung", "Festo/ba.pdf", ["en"])
    docs = {pd.document.file_path: pd.document for pd in teil.documents}
    docs["Festo/zert.pdf"].url = "https://www.festo.com/media/zert.pdf"
    docs["Festo/ke.pdf"].file_hash = im_archiv["sha256"]
    docs["Festo/ba.pdf"].file_hash = "f" * 64
    bom_id = export_vorgang(("10", "99000001", {}))
    zeile = excel_export.kunden_zeilen(db.session.get(Bom, bom_id))[0]
    assert len(zeile) == 17
    assert zeile[16] == ("https://www.festo.com/media/zert.pdf\n"
                         f"http://atex.example:8000/zert/api/dokumente/{im_archiv['id']}/datei")

    # Archiv weg: nur der Link des Herstellers, kein geratener Link ins Archiv
    zertifikatsserver._link_speicher.clear()
    server.erreichbar = False
    zeile = excel_export.kunden_zeilen(db.session.get(Bom, bom_id))[0]
    assert zeile[16] == "https://www.festo.com/media/zert.pdf"
    anfragen = len(server.anfragen)
    excel_export.kunden_zeilen(db.session.get(Bom, bom_id))  # fragt eine Minute nicht nach
    assert len(server.anfragen) == anfragen


def test_dl_link_leer_ohne_bekannten_link(client, ohne_linkspeicher):
    from test_export import bauteil, dokument as stammdokument, vorgang as export_vorgang

    from app.models import Bom
    from app.services import excel_export
    client.application.config["ZERTIFIKATSSERVER_URL"] = ""
    teil = bauteil("99000001", manufacturer_url="https://www.festo.com/")
    stammdokument(teil, "eu_konformitaetserklaerung", "Festo/ke.pdf", ["de"])
    bom_id = export_vorgang(("10", "99000001", {}))
    assert excel_export.kunden_zeilen(db.session.get(Bom, bom_id))[0][16] is None
