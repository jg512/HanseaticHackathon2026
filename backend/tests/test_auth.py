"""Anmeldung, Rollen, Benutzerverwaltung und der Name aus der Sitzung (app/auth.py).

Hier ist LOGIN_PFLICHT an; die übrigen Tests laufen ohne Anmeldung (conftest.py).
"""

import io
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import pytest
from conftest import xlsx
from test_findings import ZEILEN, pdf, vorschlag

from app import auth, create_app
from app.db import db
from app.models import BomItem, EmailDraft, Finding, StammdatenAenderung
from app.models.benutzer import Benutzer
from app.models.bom import ItemCorrection
from app.services import benutzer as dienst

NAME = "Petra Prüfer"  # Umlaut mit Absicht: der Name geht URL-kodiert durch nginx
FREMD = "Mallory"      # was ein Client als Namen mitschickt
PW = {"admin": "admin-geheim-1", "petra": "petra-geheim-1", "lea": "lea-geheim-1",
      "alt": "alt-geheim-1"}


@pytest.fixture
def app(tmp_path):
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True,
                      "INTERNET_VERSUCHE_PRO_TEIL": 0, "LOGIN_PFLICHT": True,
                      "LOGIN_VERZOEGERUNG": 0, "ATEX_DOCS": str(tmp_path / "ex_dokumente")})
    with app.app_context():
        db.create_all()
        for login, name, rolle in (("admin", "Ada Admin", "admin"), ("petra", NAME, "pruefer"),
                                   ("lea", "Lea Leser", "leser"), ("alt", "Alt Konto", "pruefer")):
            dienst.lege_an({"login": login, "name": name, "rolle": rolle, "passwort": PW[login]})
        db.session.get(Benutzer, 4).aktiv = False
        db.session.commit()
        yield app


def anmelden(app, login, passwort=None):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"login": login, "passwort": passwort or PW[login]})
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture
def petra(app):
    return anmelden(app, "petra")


@pytest.fixture
def admin(app):
    return anmelden(app, "admin")


# --- Anmelden, Abmelden, Sitzung ---------------------------------------------------------------

def test_login_richtig_setzt_sicheres_cookie(app):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"login": " Petra ", "passwort": PW["petra"]})
    assert r.status_code == 200
    assert r.get_json() == {"id": 2, "login": "petra", "name": NAME, "rolle": "pruefer",
                            "rolle_text": "Prüfung & Freigabe"}
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Expires=" in cookie
    assert c.get("/api/auth/ich").get_json()["name"] == NAME
    assert db.session.get(Benutzer, 2).letzte_anmeldung is not None


def test_login_falsch_immer_derselbe_text(app):
    c = app.test_client()
    antworten = [c.post("/api/auth/login", json=j) for j in (
        {"login": "petra", "passwort": "falsch"}, {"login": "niemand", "passwort": "x"},
        {"login": "alt", "passwort": PW["alt"]},  # deaktiviert, Passwort richtig
        {}, {"login": 5, "passwort": None})]
    assert {r.status_code for r in antworten} == {401}
    assert {r.get_json()["fehler"] for r in antworten} == {dienst.FALSCH}
    assert {r.get_json()["error"] for r in antworten} == {dienst.FALSCH}
    assert c.get("/api/auth/ich").status_code == 401


def test_sperre_nach_fehlversuchen(app, admin):
    c = app.test_client()
    for _ in range(app.config["LOGIN_MAX_FEHLVERSUCHE"]):
        assert c.post("/api/auth/login", json={"login": "petra", "passwort": "x"}).status_code == 401
    r = c.post("/api/auth/login", json={"login": "petra", "passwort": PW["petra"]})
    assert r.status_code == 429 and "falschen Versuchen" in r.get_json()["fehler"]
    liste = admin.get("/api/benutzer").get_json()
    assert next(b for b in liste if b["login"] == "petra")["gesperrt"] is True
    # Sperre abgelaufen: wieder möglich
    db.session.get(Benutzer, 2).gesperrt_bis = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.session.commit()
    assert c.post("/api/auth/login", json={"login": "petra", "passwort": PW["petra"]}).status_code == 200


def test_admin_hebt_sperre_mit_neuem_passwort_auf(app, admin):
    c = app.test_client()
    for _ in range(app.config["LOGIN_MAX_FEHLVERSUCHE"]):
        c.post("/api/auth/login", json={"login": "petra", "passwort": "x"})
    assert admin.patch("/api/benutzer/2", json={"passwort": "ganz-neu-123"}).status_code == 200
    assert c.post("/api/auth/login", json={"login": "petra", "passwort": "ganz-neu-123"}).status_code == 200


def test_logout(petra):
    assert petra.get("/api/boms").status_code == 200
    assert petra.post("/api/auth/logout").get_json() == {"ok": True}
    assert petra.get("/api/auth/ich").status_code == 401
    assert petra.get("/api/boms").status_code == 401
    assert petra.post("/api/auth/logout").status_code == 200  # auch ohne Sitzung harmlos


def test_sitzung_laeuft_ab(app, petra):
    with petra.session_transaction() as s:
        s["seit"] = int(time.time() - app.config["SITZUNG_STUNDEN"] * 3600 - 5)
    r = petra.get("/api/auth/ich")
    assert r.status_code == 401 and r.get_json()["fehler"] == auth.NICHT_ANGEMELDET
    with petra.session_transaction() as s:
        assert "uid" not in s  # geleert


def test_wer_arbeitet_bleibt_angemeldet(app, petra):
    """Die Frist zählt ab der letzten Anfrage, nicht ab der Anmeldung."""
    fast = int(time.time() - app.config["SITZUNG_STUNDEN"] * 3600 + 300)  # 5 Minuten vor Schluss
    with petra.session_transaction() as s:
        s["seit"] = fast
    assert petra.get("/api/auth/ich").status_code == 200
    with petra.session_transaction() as s:
        assert s["seit"] > fast + 3600  # verlängert


def test_gefaelschtes_cookie_gilt_nicht(app):
    c = app.test_client()
    c.set_cookie("atex_sitzung", "eyJ1aWQiOjF9.falsch.signiert")
    assert c.get("/api/boms").status_code == 401


def test_deaktivieren_beendet_sitzung(app, admin, petra):
    assert admin.patch("/api/benutzer/2", json={"aktiv": False}).get_json()["aktiv"] is False
    assert petra.get("/api/boms").status_code == 401
    r = app.test_client().post("/api/auth/login", json={"login": "petra", "passwort": PW["petra"]})
    assert r.status_code == 401
    assert admin.patch("/api/benutzer/2", json={"aktiv": True}).status_code == 200
    anmelden(app, "petra")


# --- Geschützte und offene Pfade ---------------------------------------------------------------

def _alle_regeln(app):
    for regel in app.url_map.iter_rules():
        if regel.endpoint == "static":
            continue
        werte = {a: (1 if "int" in type(regel._converters[a]).__name__.lower() else "x.pdf")
                 for a in regel.arguments}
        url = regel.rule
        for a, w in werte.items():
            url = re.sub(rf"<[^>]*{a}>", str(w), url)
        for methode in regel.methods - {"HEAD", "OPTIONS"}:
            yield methode, url


def test_401_auf_jedem_geschuetzten_pfad(app):
    c = app.test_client()
    geprueft = 0
    for methode, url in _alle_regeln(app):
        if url in ("/health", "/api/auth/login", "/api/auth/logout"):
            continue
        r = c.open(url, method=methode, json={"by": FREMD})
        assert r.status_code == 401, (methode, url)
        assert r.get_json() == {"fehler": auth.NICHT_ANGEMELDET, "error": auth.NICHT_ANGEMELDET}
        geprueft += 1
    assert geprueft > 60
    # Pfade ohne Route ebenfalls erst nach der Anmeldung (verrät nichts über die API)
    assert c.get("/files/irgendwas.pdf").status_code == 401
    assert c.get("/api/gibt-es-nicht").status_code == 401


def test_offen_bleiben_nur_health_und_anmeldung(app):
    c = app.test_client()
    assert c.get("/health").get_json() == {"ok": True}
    assert c.post("/api/auth/login", json={}).status_code == 401  # erreichbar, Text der Anmeldung
    assert c.post("/api/auth/logout").status_code == 200


def test_ohne_login_pflicht_offen_wie_bisher(app):
    app.config["LOGIN_PFLICHT"] = False
    c = app.test_client()
    assert c.get("/api/boms").status_code == 200
    assert c.get("/api/auth/ich").get_json()["login"] is None
    assert c.get("/api/benutzer").status_code == 401  # Verwaltung braucht trotzdem einen Admin


# --- Rollen ------------------------------------------------------------------------------------

def test_leser_darf_nur_lesen(app):
    lea = anmelden(app, "lea")
    assert lea.get("/api/boms").status_code == 200
    assert lea.get("/api/vorgaenge").status_code == 200
    for methode, url in (("POST", "/api/boms"), ("POST", "/api/vorgaenge"),
                         ("POST", "/api/findings/1/decision"), ("PATCH", "/api/stammdaten/parts/1"),
                         ("DELETE", "/api/stammdaten/documents/1"), ("PUT", "/api/mails/1")):
        r = lea.open(url, method=methode, json={})
        assert r.status_code == 403, url
        assert r.get_json()["fehler"] == auth.NUR_LESEN == r.get_json()["error"]
    assert lea.get("/api/benutzer").status_code == 403
    # eigenes Passwort und Abmelden gehen
    assert lea.post("/api/auth/passwort", json={"alt": PW["lea"], "neu": "lea-neu-12345"}).status_code == 200
    assert lea.post("/api/auth/logout").status_code == 200


def test_pruefer_darf_keine_konten_verwalten(petra):
    assert petra.get("/api/benutzer").status_code == 403
    assert petra.post("/api/benutzer", json={"login": "x"}).status_code == 403
    r = petra.patch("/api/benutzer/2", json={"rolle": "admin"})
    assert r.status_code == 403 and r.get_json()["fehler"] == "Das darf nur die Administration."


def test_fremde_seite_darf_nicht_schreiben(petra):
    r = petra.post("/api/boms", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert petra.get("/api/boms", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


# --- Benutzerverwaltung ------------------------------------------------------------------------

def test_konto_anlegen_und_rolle_aendern(app, admin):
    r = admin.post("/api/benutzer", json={"login": "Neu.Er", "name": "Nora Neu", "rolle": "leser",
                                          "passwort": "nora-geheim-1"})
    assert r.status_code == 201
    neu = r.get_json()
    assert (neu["login"], neu["rolle"], neu["angelegt_von"]) == ("neu.er", "leser", "Ada Admin")
    assert "passwort" not in str(neu) and "hash" not in str(neu)
    b = db.session.get(Benutzer, neu["id"])
    assert b.passwort_hash != "nora-geheim-1" and b.passwort_hash.startswith(("scrypt:", "pbkdf2:"))
    nora = anmelden(app, "neu.er", "nora-geheim-1")
    assert nora.post("/api/boms").status_code == 403
    assert admin.patch(f"/api/benutzer/{neu['id']}", json={"rolle": "pruefer"}).status_code == 200
    assert nora.post("/api/boms").status_code == 400  # jetzt erlaubt, nur ohne Datei
    assert admin.patch(f"/api/benutzer/{neu['id']}", json={"name": "Nora Neumann"}).get_json()["name"] == "Nora Neumann"
    assert nora.get("/api/auth/ich").get_json()["name"] == "Nora Neumann"


@pytest.mark.parametrize("daten, code, text", [
    ({"login": "PETRA", "name": "Andere", "passwort": "lang-genug-1"}, 409, "gibt es schon"),
    ({"login": "neu", "name": "petra prüfer", "passwort": "lang-genug-1"}, 409, "anderer Zugang"),
    ({"login": "neu", "name": "Neu", "passwort": "kurz"}, 400, "mindestens 8"),
    ({"login": "a b", "name": "Neu", "passwort": "lang-genug-1"}, 400, "Anmeldename"),
    ({"login": "neu", "name": "Neu", "rolle": "chef", "passwort": "lang-genug-1"}, 400, "Rolle"),
    ({"login": "neu", "passwort": "lang-genug-1"}, 400, "Es fehlt der Name"),
])
def test_konto_anlegen_fehler(admin, daten, code, text):
    r = admin.post("/api/benutzer", json=daten)
    assert r.status_code == code and text in r.get_json()["fehler"]


def test_letzter_admin_bleibt(app, admin):
    for daten in ({"rolle": "pruefer"}, {"aktiv": False}):
        r = admin.patch("/api/benutzer/1", json=daten)
        assert r.status_code == 409 and "einzige aktive Zugang" in r.get_json()["fehler"]
    assert db.session.get(Benutzer, 1).rolle == "admin"
    # mit einem zweiten Admin geht es
    assert admin.patch("/api/benutzer/2", json={"rolle": "admin"}).status_code == 200
    assert admin.patch("/api/benutzer/1", json={"rolle": "pruefer"}).status_code == 200
    assert admin.get("/api/benutzer").status_code == 403  # sofort ohne Admin-Rechte
    petra = anmelden(app, "petra")
    r = petra.patch("/api/benutzer/2", json={"aktiv": False})
    assert r.status_code == 409


def test_kein_loeschen(admin):
    assert admin.delete("/api/benutzer/2").status_code == 405


def test_passwort_aendern(app, petra):
    zweite = anmelden(app, "petra")
    r = petra.post("/api/auth/passwort", json={"alt": "falsch", "neu": "petra-neu-123"})
    assert r.status_code == 400 and "bisherige" in r.get_json()["fehler"]
    assert petra.post("/api/auth/passwort", json={"alt": PW["petra"], "neu": "kurz"}).status_code == 400
    assert petra.post("/api/auth/passwort", json={"alt": PW["petra"], "neu": "petra-neu-123"}).status_code == 200
    assert petra.get("/api/auth/ich").status_code == 200  # diese Sitzung bleibt
    assert zweite.get("/api/auth/ich").status_code == 401  # andere enden
    c = app.test_client()
    assert c.post("/api/auth/login", json={"login": "petra", "passwort": PW["petra"]}).status_code == 401
    anmelden(app, "petra", "petra-neu-123")


# --- Prüfung für nginx (/zert/) ----------------------------------------------------------------

def test_pruefe_fuer_nginx(app, petra):
    assert app.test_client().get("/api/auth/pruefe").status_code == 401
    r = petra.get("/api/auth/pruefe", headers={"X-Original-Method": "POST"})
    assert r.status_code == 204 and unquote(r.headers["X-Anwender-Name"]) == NAME
    assert r.headers["X-Anwender-Name"] == "Petra%20Pr%C3%BCfer"
    lea = anmelden(app, "lea")
    assert lea.get("/api/auth/pruefe", headers={"X-Original-Method": "GET"}).status_code == 204
    assert lea.get("/api/auth/pruefe", headers={"X-Original-Method": "DELETE"}).status_code == 403


# --- Der Name kommt aus der Sitzung ------------------------------------------------------------

@pytest.fixture
def bom(app, petra):
    ordner = app.config["ATEX_DOCS"]
    pdf(__import__("pathlib").Path(ordner) / "P+F" / "78E_Konf.pdf",
        ["Deckblatt", "EU-Konformitaetserklaerung 78E Drehgeber"])
    r = petra.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "liste.xlsx")})
    assert r.status_code == 201
    return r.get_json()


KOPF = {"X-Anwender": FREMD}


def _pdf_bytes():
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Datenblatt 78E")
    return doc.tobytes()


def test_name_findings_entscheidung(petra, bom):
    fid = vorschlag(bom["items"][0]["id"])
    r = petra.post(f"/api/findings/{fid}/decision", headers=KOPF,
                   json={"decision": "freigeben", "by": FREMD})
    assert r.status_code == 200 and r.get_json()["decided_by"] == NAME
    assert db.session.get(Finding, fid).decided_by == NAME


def test_name_dokument_hochladen_formular(petra, bom):
    item = bom["items"][0]["id"]
    r = petra.post(f"/api/boms/{bom['id']}/items/{item}/documents", headers=KOPF, data={
        "file": (io.BytesIO(_pdf_bytes()), "datenblatt.pdf"), "by": FREMD, "source": "manuell",
        "doc_type": "datenblatt"})
    assert r.status_code == 201 and r.get_json()["decided_by"] == NAME


def test_name_dokument_hochladen_ohne_namen_im_formular(petra, bom):
    item = bom["items"][0]["id"]
    r = petra.post(f"/api/boms/{bom['id']}/items/{item}/documents", data={
        "file": (io.BytesIO(_pdf_bytes()), "datenblatt.pdf"), "source": "manuell",
        "doc_type": "datenblatt"})
    assert r.status_code == 201 and r.get_json()["decided_by"] == NAME


def test_name_emails_freigabe_und_eskalation(petra, bom):
    item = bom["items"][0]["id"]
    for _ in range(2):
        db.session.add(EmailDraft(bom_item_id=item, to_address="t@example.com", subject="s",
                                  body="b"))
    db.session.commit()
    r = petra.post("/api/emails/1/approve", headers=KOPF, json={"by": FREMD})
    assert r.status_code == 200 and db.session.get(EmailDraft, 1).approved_by == NAME
    r = petra.post("/api/emails/2/approve")  # ganz ohne Körper: der Name aus der Sitzung
    assert r.status_code == 200 and db.session.get(EmailDraft, 2).approved_by == NAME


def test_name_erledigt_und_ex_teil(petra, bom):
    b, item = bom["id"], bom["items"][1]["id"]
    r = petra.post(f"/api/boms/{b}/items/{item}/resolve", json={"by": FREMD, "note": "Grund"})
    assert r.status_code == 200 and r.get_json()["resolved_by"] == NAME
    r = petra.post(f"/api/boms/{b}/items/{item}/ex-teil", json={"ex_teil": True, "by": FREMD})
    assert r.status_code == 200 and db.session.get(BomItem, item).ex_confirmed_by == NAME
    r = petra.post(f"/api/boms/{b}/items/{item}/ex-teil", json={"ex_teil": False, "by": FREMD})
    assert r.status_code == 200 and db.session.get(BomItem, item).status_note == \
        f"{NAME} hat entschieden, dass die Position kein Ex-Teil ist."


def test_name_korrektur_json_und_excel(petra, bom):
    b, item = bom["id"], bom["items"][1]["id"]
    r = petra.post(f"/api/boms/{b}/items/{item}/korrektur",
                   json={"by": FREMD, "location": "Zone 2"})
    assert r.status_code == 200 and r.get_json()["corrections"][-1]["changed_by"] == NAME
    neu = [list(z) for z in ZEILEN]
    neu[0][2] = "Andere Bezeichnung"
    r = petra.post(f"/api/boms/{b}/korrektur/excel",
                   data={"file": (io.BytesIO(xlsx(neu)), "k.xlsx"), "by": FREMD})
    assert r.status_code == 200
    namen = {k.changed_by for k in db.session.scalars(db.select(ItemCorrection))}
    assert namen == {NAME}


def test_name_stammdaten(petra, bom):
    r = petra.post("/api/stammdaten/parts", json={"by": FREMD, "profile_no": "12345678"})
    assert r.status_code == 201, r.get_json()
    namen = {a.changed_by for a in db.session.scalars(db.select(StammdatenAenderung))}
    assert namen == {NAME}


def test_name_fragen(petra, bom):
    r = petra.post(f"/api/boms/{bom['id']}/questions", json={"by": FREMD, "text": "Zone?"})
    assert r.status_code == 201 and r.get_json()["created_by"] == NAME
    r = petra.post(f"/api/questions/{r.get_json()['id']}/comments",
                   json={"by": FREMD, "text": "Kommentar"})
    assert r.get_json()["comments"][-1]["created_by"] == NAME


def test_name_oberflaeche(app, petra, bom):
    item = bom["items"][0]["id"]
    # korrigieren mit „von“ im JSON und im Kopf
    r = petra.post(f"/api/artikel/{item}/korrigieren", headers=KOPF,
                   json={"einbauort": "Zone 21", "von": FREMD, "by": FREMD})
    assert r.status_code == 200
    assert db.session.scalars(db.select(ItemCorrection)).all()[-1].changed_by == NAME
    # ablehnen und freigeben
    f1, f2 = vorschlag(item), vorschlag(item, file_hash="anders")
    assert petra.post(f"/api/artikel/{item}/ablehnen",
                      json={"fund_id": f1, "von": FREMD}).status_code == 200
    assert db.session.get(Finding, f1).decided_by == NAME
    assert petra.post(f"/api/artikel/{item}/freigeben", headers=KOPF,
                      json={"fund_id": f2}).status_code == 200
    assert db.session.get(Finding, f2).decided_by == NAME
    # Zertifikat als Formular mit „von“
    item2 = bom["items"][1]["id"]
    r = petra.post(f"/api/artikel/{item2}/zertifikat", data={
        "datei": (io.BytesIO(_pdf_bytes()), "z.pdf"), "von": FREMD, "doc_type": "datenblatt"})
    assert r.status_code == 200, r.get_json()
    assert db.session.get(Finding, r.get_json()["fund_id"]).decided_by == NAME


# --- Kommandozeile -----------------------------------------------------------------------------

def test_init_db_legt_admin_an_und_laesst_vorhandene_konten(monkeypatch):
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True})
    monkeypatch.setenv("ADMIN_BENUTZER", "Chefin")
    monkeypatch.setenv("ADMIN_NAME", "Clara Chefin")
    monkeypatch.setenv("ADMIN_PASSWORT", "chefin-geheim-1")
    runner = app.test_cli_runner()
    with app.app_context():
        r = runner.invoke(args=["init-db"])
        assert r.exit_code == 0 and "chefin" in r.output and "chefin-geheim-1" not in r.output
        b = dienst.finde("chefin")
        assert (b.name, b.rolle, b.aktiv) == ("Clara Chefin", "admin", True)
        # noch einmal mit anderem Passwort: nichts zurückgesetzt
        monkeypatch.setenv("ADMIN_PASSWORT", "anderes-passwort")
        runner.invoke(args=["init-db"])
        assert dienst.anzahl() == 1 and dienst.hat_passwort(b, "chefin-geheim-1")
        r = runner.invoke(args=["benutzer-anlegen", "--login", "max", "--name", "Max M",
                                "--rolle", "leser"], input="max-geheim-12\nmax-geheim-12\n")
        assert r.exit_code == 0, r.output
        assert dienst.finde("max").rolle == "leser"


def test_entwicklungs_admin_auch_in_vorhandener_datenbank(monkeypatch):
    """Wie nach dem Merge: Daten und Konten da, aber noch kein „admin“. Kurzes Passwort erlaubt,
    Warnung ohne das Passwort."""
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True})
    monkeypatch.setenv("ADMIN_BENUTZER", "admin")
    monkeypatch.setenv("ADMIN_NAME", "Administrator")
    monkeypatch.setenv("ADMIN_PASSWORT", "admin")
    runner = app.test_cli_runner()
    with app.app_context():
        db.create_all()
        dienst.lege_an({"login": "petra", "name": NAME, "passwort": PW["petra"]})
        db.session.commit()
        r = runner.invoke(args=["init-db"])
        assert r.exit_code == 0 and "Admin angelegt: admin" in r.output
        assert "Standardpasswort" in r.output and "ADMIN_PASSWORT=" not in r.output
        b = dienst.finde("admin")
        assert (b.rolle, b.name, dienst.anzahl()) == ("admin", "Administrator", 2)
        c = app.test_client()
        app.config["LOGIN_PFLICHT"] = True
        app.config["LOGIN_VERZOEGERUNG"] = 0
        assert c.post("/api/auth/login", json={"login": "admin", "passwort": "admin"}).status_code == 200
        # Passwort geändert: bleibt, keine Warnung mehr
        assert c.post("/api/auth/passwort", json={"alt": "admin", "neu": "neues-admin-pw"}).status_code == 200
        r = runner.invoke(args=["init-db"])
        assert "angelegt" not in r.output and "Standardpasswort" not in r.output
        assert dienst.hat_passwort(dienst.finde("admin"), "neues-admin-pw")


def test_init_db_ohne_admin_variablen_gibt_hinweis(monkeypatch):
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True})
    monkeypatch.delenv("ADMIN_PASSWORT", raising=False)
    with app.app_context():
        r = app.test_cli_runner().invoke(args=["init-db"])
        assert r.exit_code == 0 and "benutzer-anlegen" in r.output
        assert dienst.anzahl() == 0
