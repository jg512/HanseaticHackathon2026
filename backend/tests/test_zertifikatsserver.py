import hashlib
import io

import httpx
import pytest
from conftest import xlsx
from test_suche import VACF, pdf

from app.db import db
from app.models import Finding, PartDocument
from app.services import zertifikatsserver

ZEILEN = [[10, 99000010, "Magnetspule", "Festo", "VACF-B-K1-1-5", 4, "Zone 1"]]
SERVER = "http://zertifikatsserver:5100"


class FalscherServer:
    """Ersetzt httpx.get: Markierungen je SHA-256 wie GET /api/dokumente und /api/neue-suche."""

    def __init__(self):
        self.stand = {}  # sha256 -> markierungsstand
        self.erreichbar = True
        self.liste = []  # sha256 der Dokumente des Herstellers (zertifikatsserver.auszug)

    def _dok(self, sha):
        return {"sha256": sha, "dateiname": "alt.pdf", "pdf_url": f"{SERVER}/api/dokumente/1/pdf",
                "markierungsstand": self.stand.get(sha, "ohne"),
                "gruende": ["nennt 94/9/EG (S. 1)"]}

    def get(self, url, params=None, timeout=None):
        if not self.erreichbar:
            raise httpx.ConnectError("weg")
        if url.endswith("/api/dokumente") and "sha256" not in (params or {}):
            # Liste je Hersteller (zertifikatsserver.auszug); die Dateien liegen auch in ATEX_DOCS
            daten = [self._dok(h) | {"hersteller": "Festo", "ablage": "aktuell",
                                     "dateiname": "KE VACF.pdf"} for h in self.liste]
        elif url.endswith("/api/dokumente"):
            daten = [self._dok(h) for h in params["sha256"].split(",") if h in self.stand]
        else:
            daten = [{"dokument": self._dok(h),
                      "gruende": [{"grund": "nennt 94/9/EG", "status": "bestaetigt"}]}
                     for h, s in self.stand.items() if s == "bestaetigt"]
        return httpx.Response(200, json=daten, request=httpx.Request("GET", url))


@pytest.fixture
def server(client, tmp_path, monkeypatch):
    datei = pdf(tmp_path / "Festo" / "Festo_Konformitaetserklaerung_VACF-B-K1-1-5.pdf", VACF)
    client.application.config.update(ATEX_DOCS=str(tmp_path), ZERTIFIKATSSERVER_URL=SERVER)
    falsch = FalscherServer()
    falsch.sha = hashlib.sha256(datei.read_bytes()).hexdigest()
    falsch.liste = [falsch.sha]
    monkeypatch.setattr(zertifikatsserver.httpx, "get", falsch.get)
    return falsch


def vorgang(client):
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "l.xlsx")}).get_json()
    return bom["id"], bom["items"][0]["id"]


def suche(client, bom_id):
    return client.post(f"/api/boms/{bom_id}/search", json={"internet_versuche": 0}).get_json()


def test_mit_archiv_sucht_der_agent_nur_dort(client, server, tmp_path):
    # liegt nur im Herstellerordner, nicht im Archiv: wird nicht gefunden
    pdf(tmp_path / "Festo" / "nur_im_ordner_VACF-B-K1-1-5.pdf", VACF + "\nNur im Ordner.")
    bom_id, _ = vorgang(client)
    item = suche(client, bom_id)["items"][0]
    assert item["status"] == "vorschlag"
    assert item["searched_in"].startswith(
        "Gesucht hat der Agent in der Datenbank und im Dokumentenarchiv.")
    (f,) = db.session.scalars(db.select(Finding)).all()
    assert f.file_path == "_upload/zertifikatsserver/Festo/KE_VACF.pdf"


def test_archiv_ohne_dokumente_des_herstellers(client, server):
    server.liste = []
    bom_id, _ = vorgang(client)
    item = suche(client, bom_id)["items"][0]
    assert item["status"] == "nicht_gefunden"
    assert item["searched_in"].startswith(
        "Gesucht hat der Agent in der Datenbank und im Dokumentenarchiv. Dort gibt es keine "
        "aktuellen Dokumente dieses Herstellers.")


def test_verdacht_bleibt_vorschlag_mit_grund(client, server):
    server.stand[server.sha] = "verdacht"
    bom_id, _ = vorgang(client)
    assert suche(client, bom_id)["items"][0]["status"] == "vorschlag"
    f = db.session.scalar(db.select(Finding))
    assert (" Im Dokumentenarchiv ist dieses Dokument als vermutlich veraltet markiert. Als Grund "
            "steht dort „nennt 94/9/EG (S. 1)“.") in f.check_message
    assert "Zertifikatsserver" not in f.check_message
    assert f.check_result != "ok"


def test_bestaetigt_veraltet_wird_nicht_vorgeschlagen(client, server):
    server.stand[server.sha] = "bestaetigt"
    bom_id, _ = vorgang(client)
    item = suche(client, bom_id)["items"][0]
    assert item["status"] == "nicht_gefunden"
    assert "Dort gibt es keine aktuellen Dokumente dieses Herstellers." in item["searched_in"]
    assert db.session.scalar(db.select(Finding)) is None


def test_server_weg_dann_nur_datenbank(client, server):
    # ohne Archiv sucht der Agent nicht im Ordner, auch wenn die Datei dort liegt
    server.erreichbar = False
    bom_id, _ = vorgang(client)
    item = suche(client, bom_id)["items"][0]
    assert item["status"] == "nicht_gefunden"
    assert item["searched_in"].startswith(
        "Gesucht hat der Agent in der Datenbank. Das Dokumentenarchiv war gerade nicht "
        "erreichbar, deshalb hat er dort nicht gesucht.")


def test_neue_suche_fuer_bestaetigt_veraltete(client, server):
    bom_id, item_id = vorgang(client)
    suche(client, bom_id)
    f = db.session.scalar(db.select(Finding))
    r = client.post(f"/api/findings/{f.id}/decision",
                    json={"decision": "freigeben", "by": "Eva"})
    assert r.get_json()["item_status"] == "vorhanden"
    assert client.get(f"/api/boms/{bom_id}/veraltet").get_json() == {"items": []}

    server.stand[server.sha] = "bestaetigt"  # später im Server als veraltet bestätigt
    betroffen = client.get(f"/api/boms/{bom_id}/veraltet").get_json()["items"]
    assert [(i["id"], i["veraltet"][0]["dateiname"]) for i in betroffen] == [(item_id, "alt.pdf")]

    assert client.post(f"/api/boms/{bom_id}/veraltet/suche", json={}).status_code == 400
    r = client.post(f"/api/boms/{bom_id}/veraltet/suche",
                    json={"by": "Eva", "internet_versuche": 0})
    assert r.status_code == 200
    item = r.get_json()["items"][0]
    # Stammdaten-Zuordnung zurück auf ungeprüft, alte Datei zählt nicht mehr: E-Mail-Weg offen
    assert item["status"] == "nicht_gefunden"
    assert len(client.get(f"/api/boms/{bom_id}/emails").get_json()) == 1  # Entwurf liegt bereit
    pd = db.session.scalar(db.select(PartDocument))
    assert (pd.status, pd.verified_by) == ("ungeprueft", None)
    zweimal = client.post(f"/api/boms/{bom_id}/veraltet/suche", json={"by": "Eva"})
    assert zweimal.status_code == 409


def test_ohne_server_eingestellt(client):
    client.application.config["ZERTIFIKATSSERVER_URL"] = ""
    bom_id, _ = vorgang(client)
    r = client.get(f"/api/boms/{bom_id}/veraltet")
    assert r.status_code == 503 and "nicht angeschlossen" in r.get_json()["error"]


@pytest.fixture(autouse=True)
def kein_netz(monkeypatch):
    """Übertragen nach der Freigabe (tests/test_ablage.py) geht hier ins Leere, nie ins Netz."""
    def weg(*args, **kwargs):
        raise httpx.ConnectError("kein Server im Test")
    monkeypatch.setattr(zertifikatsserver.httpx, "request", weg)


def test_archivfund_ohne_befund_ist_automatisch_frei(client, server):
    client.application.config["AUTOMATISCH_FREIGEBEN"] = True
    bom_id, item_id = vorgang(client)
    assert suche(client, bom_id)["items"][0]["status"] == "vorhanden"
    f = db.session.scalar(db.select(Finding))
    assert (f.status, f.auto_approved, f.check_result) == ("freigegeben", True, "ok")
    (fund,) = client.get(f"/api/artikel/{item_id}").get_json()["funde"]
    assert fund["quelle"] == "Dokumentenarchiv" and fund["automatisch"]


def test_ohne_veraltet_pruefung_zaehlen_die_markierungen_nicht(client, server):
    client.application.config["VERALTET_PRUEFUNG"] = False
    server.stand[server.sha] = "bestaetigt"
    bom_id, _ = vorgang(client)
    assert suche(client, bom_id)["items"][0]["status"] == "vorschlag"
    f = db.session.scalar(db.select(Finding))
    assert f.check_result == "ok" and "veraltet" not in f.check_message
