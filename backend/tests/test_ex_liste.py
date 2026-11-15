import io

import pytest
from conftest import xlsx
from openpyxl import load_workbook

from app.db import db
from app.models import Bom, BomItem
from app.services import ex_liste

# Die Fallen der Test-Stückliste (data/README.md), dazu Zeilen für die übrigen Regeln
ZEILEN = [[10, 99000010, "Magnetspule für Ventil", "Festo", "VACF-B-K1-1A-1-EX4-M", 4, "Zone 1"],
          [140, 99000140, "Trennschaltverstärker", "Pepperl+Fuchs", "KCD2-SR-Ex1.LB", 2,
           "Schaltschrank außerhalb Ex"],
          [150, 99000150, "Induktiver Sensor", "Pepperl+Fuchs", "NJ50-…-N", 2, "Zone 1"],
          [270, 99000270, "Zahnriemen", "Breco", "AT10", 1, None],
          [280, 99000280, "Linearführung", None, None, 2, None]]


def position(location, description="Teil", typ="TYP-1",
             ex_area="Innenraum Zone 1, Umgebung Zone 2",
             required_category="Zone 1: mindestens 2G · Zone 2: mindestens 3G"):
    i = BomItem(row_number=9, pos="10", profile_no="99000010", description=description,
                manufacturer="Festo", manufacturer_article_no=typ, location=location)
    db.session.add(Bom(source_file_name="x.xlsx", source_file_hash="h", ex_area=ex_area,
                       required_category=required_category, items=[i]))
    db.session.flush()
    return i


@pytest.mark.parametrize("ort, vorschlag, regel, zone, kategorie", [
    ("Zone 1", "ex_teil", "zone_im_einbauort", "Zone 1", "2G"),
    ("Zone 2", "ex_teil", "zone_im_einbauort", "Zone 2", "3G"),
    ("Innenraum", "ex_teil", "bereich_aus_kopfzeile", "Zone 1", "2G"),
    ("Ex-Bereich Halle 2", "ex_teil", "ex_bereich_im_einbauort", None, None),
    ("Schaltschrank außerhalb Ex", "pruefen", "ausserhalb_ex", None, None),
    ("außerhalb Zone 1", "pruefen", "widerspruch", "Zone 1", "2G"),
    ("Halle 3", "pruefen", "einbauort_unklar", None, None),
    ("", "pruefen", "einbauort_fehlt", None, None),
])
def test_regeln(client, ort, vorschlag, regel, zone, kategorie):
    d = ex_liste.einordnen(position(ort))
    assert (d["vorschlag"], d["regel"], d["zone"], d["kategorie"]) == (vorschlag, regel, zone,
                                                                         kategorie)
    assert d["grund"] and d["quelle"].startswith("Zeile 9, Einbauort")


def test_kopfangaben(client):
    d = ex_liste.einordnen(position("Zone 21", ex_area="", required_category=""))
    assert (d["zone"], d["kategorie"]) == ("Zone 21", "2D")  # ohne Kopfangabe: ATEX-Mindestwert
    assert "nach ATEX" in d["kategorie_quelle"]
    assert "fehlt die Angabe „Ex-Bereich“" in d["hinweis"]
    d = ex_liste.einordnen(position("Zone 0"))
    assert d["hinweis"] == ("Zone 0 steht nicht im Kopf der Stückliste unter „Ex-Bereich“, dort "
                            "stehen nur Zone 1 und Zone 2.")
    assert ex_liste.einordnen(position("Innenraum"))["quelle"].endswith(
        "Kopfangabe „Ex-Bereich“: „Innenraum Zone 1, Umgebung Zone 2“")


def test_ausserhalb_mit_ex_kennung_wird_gesucht(client):
    """Außerhalb mit nur vagem Ex-Hinweis: gesucht, aber die Entscheidung bleibt offen."""
    vage = ex_liste.einordnen(position("außerhalb Ex", description="Netzteil", typ="PS-24 Ex"))
    ohne = ex_liste.einordnen(position("außerhalb Ex", description="Netzteil", typ="PS-24"))
    assert (vage["vorschlag"], vage["ex_kennung"], vage["suchen"]) == ("pruefen", True, True)
    assert (ohne["vorschlag"], ohne["ex_kennung"], ohne["suchen"]) == ("pruefen", False, False)
    for hinweis in ("ATEX", "NAMUR", "II 2G"):
        d = ex_liste.einordnen(position("außerhalb Ex", description=f"Netzteil {hinweis}",
                                        typ="PS-24"))
        assert (d["regel"], d["ex_kennung"], d["suchen"]) == ("ausserhalb_ex", True, True)


@pytest.mark.parametrize("beschreibung, typ, woran", [
    ("Trennschaltverstärker", "KCD2-SR-Ex1.LB", "Bezeichnung „Trennschaltverstärker“"),
    ("Baustein", "KFD2-SOT2-Ex2", "Typ KFD2-SOT2-Ex2"),
    ("Speisetrenner", "X1", "Bezeichnung „Speisetrenner“"),
    ("Barriere II (1)G [Ex ia Ga] IIC", "Z-1", "Kennzeichnung in Klammern"),
])
def test_zugehoeriges_betriebsmittel_ist_ex_teil(client, beschreibung, typ, woran):
    """Pos. 140: außerhalb des Ex-Bereichs, aber zugehöriges Betriebsmittel -> Ex-Teil."""
    d = ex_liste.einordnen(position("Schaltschrank außerhalb Ex", description=beschreibung,
                                    typ=typ))
    assert (d["vorschlag"], d["regel"], d["suchen"]) == (
        "ex_teil", "zugehoeriges_betriebsmittel", True)
    assert woran in d["grund"] and "braucht sie Ex-Dokumente" in d["grund"]
    assert "Klammern" in d["hinweis"]


def test_kennzeichnung_aus_dem_fund(client):
    """Die Kennzeichnung eines gefundenen Dokuments zählt auch (nicht, wenn abgelehnt)."""
    from app.models import Finding
    i = position("außerhalb Ex", description="Modul", typ="M-1")
    i.findings.append(Finding(source="ordner", doc_type="eu_konformitaetserklaerung",
                              ex_marking_raw="II (1)G [Ex ia Ga] IIC", status="abgelehnt"))
    assert ex_liste.einordnen(i)["vorschlag"] == "pruefen"
    i.findings[0].status = "vorgeschlagen"
    d = ex_liste.einordnen(i)
    assert (d["vorschlag"], d["regel"]) == ("ex_teil", "zugehoeriges_betriebsmittel")
    assert "II (1)G [Ex ia Ga] IIC" in d["grund"]


def upload(client, zeilen=ZEILEN):
    r = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(zeilen)), "liste.xlsx")})
    return r.get_json()


def test_ex_liste_nach_upload(client):
    bom = upload(client)
    liste = client.get(f"/api/boms/{bom['id']}/ex-liste").get_json()
    assert [liste[k] for k in ("total", "ex_teil", "klaerung", "kein_ex_teil")] == [5, 3, 2, 0]
    assert liste["file_name"] == f"EX-Bauteil-Liste_Abfüllanlage_TM-1_Vorgang-{bom['id']}.xlsx"
    zeilen = {z["pos"]: z for z in liste["items"]}
    assert {p: (z["einordnung"], z["status"]) for p, z in zeilen.items()} == {
        "10": ("ex_teil", "offen"), "140": ("ex_teil", "offen"), "150": ("ex_teil", "offen"),
        "270": ("klaerung", "klaerung"), "280": ("klaerung", "klaerung")}
    assert zeilen["140"]["regel_text"] == ("Zugehöriges Betriebsmittel außerhalb des "
                                           "Ex-Bereichs")
    assert zeilen["10"]["quelle"] == "Zeile 7, Einbauort: „Zone 1“"
    # Pos. 140 ist Ex-Teil ohne offene Entscheidung, Pos. 270 braucht eine
    items = {i["pos"]: i for i in bom["items"]}
    assert items["140"]["status_note"] is None
    assert items["270"]["status_note"] == ("In der Stückliste steht kein Einbauort. Bitte "
                                           "entscheiden Sie, ob die Position ein Ex-Teil ist.")
    assert client.get("/api/boms/99/ex-liste").status_code == 404


def test_mensch_entscheidet(client):
    bom = upload(client)
    items = {i["pos"]: i["id"] for i in bom["items"]}

    def entscheide(pos, wert, by="Eva"):
        return client.post(f"/api/boms/{bom['id']}/items/{items[pos]}/ex-teil",
                           json={"ex_teil": wert, "by": by})

    def zeile(pos):
        liste = client.get(f"/api/boms/{bom['id']}/ex-liste").get_json()
        return next(z for z in liste["items"] if z["pos"] == pos)

    # Zahnriemen ohne Einbauort: kein Ex-Teil
    r = entscheide("270", False).get_json()
    assert (r["status"], r["status_note"]) == ("kein_ex_teil", "Eva hat entschieden, dass die Position kein Ex-Teil ist.")
    assert (zeile("270")["einordnung"], zeile("270")["entschieden"]) == (
        "kein_ex_teil", "Eva hat entschieden, dass die Position kein Ex-Teil ist.")

    # Zahnriemen doch Ex-Teil: wird gesucht wie jedes andere
    r = entscheide("270", True)
    assert (r.get_json()["status"], r.get_json()["status_note"]) == ("offen", None)
    assert zeile("270")["einordnung"] == "ex_teil"
    assert zeile("270")["entschieden"].startswith("Eva hat am ")
    assert zeile("270")["entschieden"].endswith(" bestätigt, dass die Position ein Ex-Teil ist.")

    # Zurücknehmen: es gilt wieder der Vorschlag der Regeln
    assert entscheide("270", None).get_json()["status"] == "klaerung"
    assert (zeile("270")["einordnung"], zeile("270")["entschieden"]) == ("klaerung", None)

    # Pos. 140 bestätigt: Hinweis zum Stand verschwindet
    assert entscheide("140", True).get_json()["status_note"] is None
    # ohne Nummern hilft auch die Bestätigung nicht
    assert entscheide("280", True).get_json()["status"] == "klaerung"

    assert entscheide("140", "ja").status_code == 400
    assert entscheide("140", 1).status_code == 400
    assert entscheide("140", False, by=" ").status_code == 400
    assert client.post(f"/api/boms/{bom['id']}/items/{items['140']}/ex-teil",
                       json={"by": "Eva"}).status_code == 400


def test_excel_ex_bauteil_liste(client):
    bom = upload(client)
    client.post(f"/api/boms/{bom['id']}/items/{bom['items'][3]['id']}/ex-teil",
                json={"ex_teil": False, "by": "Eva"})  # Pos. 270
    r = client.get(f"/api/boms/{bom['id']}/ex-liste.xlsx")
    assert r.status_code == 200
    assert "EX-Bauteil-Liste_" in r.headers["Content-Disposition"]
    wb = load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ["EX-Bauteil-Liste", "Nicht in der Liste"]
    ex, rest = ([list(z) for z in ws.iter_rows(values_only=True)] for ws in wb.worksheets)
    assert ex[0][:3] == ["Pos.", "PRO.FILE-Nr.", "Bezeichnung"]
    assert [z[0] for z in ex[1:]] == ["10", "140", "150"]
    assert ex[1][7:12] == ["Zone 1", "2G", "Ex-Teil", "Einbauort nennt eine Zone: Ex-Teil",
                           "Der Einbauort liegt in Zone 1."]
    assert [(z[0], z[9]) for z in rest[1:]] == [("270", "kein Ex-Teil"),
                                                ("280", "Entscheidung nötig")]
    assert rest[1][14] == "Eva hat entschieden, dass die Position kein Ex-Teil ist."
    assert client.get("/api/boms/99/ex-liste.xlsx").status_code == 404
