"""Automatische Freigabe (services/freigabe.automatisch): Ein Nachweis aus dem eigenen Bestand,
an dem die Vorprüfung nichts zu beanstanden hat, braucht keinen Klick eines Menschen. Er ist dann
freigegeben und als automatisch markiert; alles andere wartet wie bisher auf einen Menschen."""

from types import SimpleNamespace

import pytest
from test_oberflaeche import ZEILEN, art, artikel, gestartet, hochladen, leer, ordner  # noqa: F401

from app.db import db
from app.models import Finding, PartDocument
from app.services import freigabe


@pytest.fixture
def automatisch(client):
    client.application.config["AUTOMATISCH_FREIGEBEN"] = True


def fund(pos):
    (f,) = [f for f in db.session.scalars(db.select(Finding)) if f.item.pos == str(pos)]
    return f


def test_lokaler_nachweis_ohne_befund_ist_automatisch_freigegeben(client, ordner, automatisch):
    vid = hochladen(client)
    gestartet(client, vid)

    f = fund(10)
    assert (f.source, f.check_result, f.status) == ("ordner", "ok", "freigegeben")
    assert f.auto_approved and f.decided_by == freigabe.AUTOMATISCH_VON
    # wie eine Freigabe von Hand: in den Stammdaten geprüft
    (zuordnung,) = db.session.scalars(db.select(PartDocument)).all()[:1]
    assert (zuordnung.status, zuordnung.verified_by) == ("geprueft", freigabe.AUTOMATISCH_VON)

    a = artikel(client, vid, 10)
    assert a["status"] == "geprueft" and a["automatisch"]
    assert a["freigabe"]["automatisch"] and a["freigabe"]["von"] == freigabe.AUTOMATISCH_VON
    assert [x["automatisch"] for x in a["funde"]] == [True]
    assert art(client, vid, 10)["automatisch"]

    # die Baumusterprüfbescheinigung ist auch frei; die Konformitätserklärung fehlt noch, also
    # geht die Anfrage an den Hersteller trotzdem raus
    assert fund(20).auto_approved and artikel(client, vid, 20)["status"] == "mail_entwurf"

    texte = [e["text"] for e in client.get(f"/api/vorgaenge/{vid}/ereignisse").get_json()]
    assert any(t.startswith("Position 10: Der Agent hat „Festo_Konformitätserklärung_"
                            "VACF-B-K1-1-5.pdf“ selbst freigegeben, weil es im eigenen Bestand "
                            "lag und die Vorprüfung nichts zu beanstanden hatte. Die Freigabe ist "
                            "als automatisch markiert.") for t in texte)


def test_mit_befund_wartet_der_fund_auf_einen_menschen(client, ordner, automatisch):
    # Kategorie 3G reicht für Zone 1 nicht: Warnung, also keine automatische Freigabe
    vid = hochladen(client, [[20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1,
                              "Zone 1"]])
    gestartet(client, vid)
    f = fund(20)
    assert (f.check_result, f.status, f.auto_approved) == ("warnung", "vorgeschlagen", False)
    a = artikel(client, vid, 20)
    assert a["status"] == "zu_pruefen" and not a["automatisch"] and a["freigabe"] is None

    # ein Mensch gibt frei: nicht als automatisch markiert
    r = client.post(f"/api/artikel/{a['id']}/freigeben",
                    json={"fund_id": f.id, "von": "Jan"})
    assert r.status_code == 200, r.get_json()
    a = artikel(client, vid, 20)
    assert a["freigabe"]["von"] == "Jan" and not a["freigabe"]["automatisch"]
    assert not art(client, vid, 20)["automatisch"]


def test_schalter_aus_laesst_alles_beim_menschen(client, ordner):
    vid = hochladen(client)
    gestartet(client, vid)
    assert fund(10).status == "vorgeschlagen" and not fund(10).auto_approved
    assert artikel(client, vid, 10)["status"] == "zu_pruefen"


@pytest.mark.parametrize("aenderung", [
    {"source": "internet"},                      # aus dem Netz: immer ein Mensch
    {"source": "manuell"}, {"source": "hersteller"},
    {"source": "suchdienst", "matched_term": None},  # nur ähnlich, Typ steht nicht drin
    {"check_result": "unklar"}, {"check_result": "warnung"},
    {"doc_type": "bedienungsanleitung"}, {"doc_type": "datenblatt"},
    {"status": "abgelehnt"},
    {"item": SimpleNamespace(profile_no=None)},
])
def test_nur_lokale_nachweise_ohne_befund(aenderung):
    basis = dict(status="vorgeschlagen", check_result="ok", doc_type="eu_konformitaetserklaerung",
                 source="ordner", matched_term="X-1", item=SimpleNamespace(profile_no="1"))
    assert freigabe.lokal_und_ohne_befund(SimpleNamespace(**basis))
    assert freigabe.lokal_und_ohne_befund(SimpleNamespace(**(basis | {"source": "suchdienst"})))
    assert not freigabe.lokal_und_ohne_befund(SimpleNamespace(**(basis | aenderung)))
