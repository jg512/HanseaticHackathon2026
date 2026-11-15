from conftest import xlsx

ZEILEN = [[20, 99000020, "Ventilinsel", "Festo", "VTUG-10-VI-EX2E", 1, "Zone 2"],
          [140, 99000140, "Trennschaltverstärker", "Pepperl+Fuchs", "KCD2-SR-Ex1.LB", 2, "Zone 1"],
          [250, 99000250, "Induktiver Sensor", "Muster Sensorik GmbH", "MS-NAMUR-12", 2, "Zone 2"],
          [280, 99000280, "Linearführung", None, None, 2, None]]


def test_links_je_position(client):
    import io
    bom = client.post("/api/boms", data={"file": (io.BytesIO(xlsx(ZEILEN)), "l.xlsx")}).get_json()
    links = client.get(f"/api/boms/{bom['id']}/links").get_json()
    festo, puf, muster, ohne = (links[str(i["id"])] for i in bom["items"])

    assert festo == [
        {"label": "Suche auf festo.com",
         "url": "https://www.festo.com/de/de/search/?text=VTUG-10-VI-EX2E"},
        {"label": "Websuche",
         "url": "https://duckduckgo.com/?q=site%3Afesto.com%20VTUG-10-VI-EX2E%20ATEX%20Konformit%C3%A4tserkl%C3%A4rung"}]
    assert puf[0]["url"] == "https://www.pepperl-fuchs.com/de-de/search-overview-gp27127?q=KCD2-SR-Ex1.LB"
    # unbekannter Hersteller: nur die Websuche, mit dem Namen statt einer Domain
    assert [l["label"] for l in muster] == ["Websuche"]
    assert muster[0]["url"].startswith("https://duckduckgo.com/?q=Muster%20Sensorik%20GmbH%20MS-NAMUR-12%20")
    assert ohne == []  # ohne Typ kein Link


def test_links_unbekannter_vorgang(client):
    assert client.get("/api/boms/99/links").status_code == 404
