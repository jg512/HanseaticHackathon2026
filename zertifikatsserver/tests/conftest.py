import pymupdf
import pytest

from app import anlegen, create_app


def pdf(*seiten):
    """PDF mit einer Seite je Text; leerer Text = Seite ohne Textebene (wie ein Scan)."""
    doc = pymupdf.open()
    for text in seiten:
        seite = doc.new_page()
        if text:
            seite.insert_textbox(pymupdf.Rect(40, 40, 560, 800), text, fontsize=9)
    inhalt = doc.tobytes()
    doc.close()
    return inhalt


ALT_94 = ("EG-Konformitätserklärung\nFesto AG\nProdukt: Schwenkantrieb DSM-...-EX\n"
          "Richtlinie 94/9/EG des Europäischen Parlaments und des Rates vom 23. März 1994\n"
          "Normen: EN 60079-0:2009\nEsslingen, Datum: 12.05.2005")
NEU_2014 = ("EU-Konformitätserklärung\nFesto SE\nProdukt: Schwenkantrieb DSM-...-EX\n"
            "Richtlinie 2014/34/EU des Europäischen Parlaments und des Rates vom 26. Februar 2014"
            "\nNormen: EN IEC 60079-0:2018\nEsslingen, Datum: 2022-08-09")


@pytest.fixture
def app(tmp_path):
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True,
                      "ABLAGE": str(tmp_path / "ablage"), "ALTBESTAND": str(tmp_path / "alt"),
                      "VERALTET_NACH_JAHREN": 10, "AKTUELLE_NORMEN": "60079-0:2017"})
    with app.app_context():
        anlegen()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def alt(tmp_path):
    """Kleiner Altbestand: Festo mit alter und neuer Erklärung, Dublette, P+F-Scan, Cache."""
    wurzel = tmp_path / "alt"
    for ordner in ("Festo", "P+F", "_internet/X"):
        (wurzel / ordner).mkdir(parents=True)
    alt_94 = pdf(ALT_94)
    (wurzel / "Festo" / "festo_dsm_konf_714757.pdf").write_bytes(alt_94)
    (wurzel / "Festo" / "Festo_Schwenkantrieb DSM_Konformit#U00e4tserkl#U00e4rung.pdf") \
        .write_bytes(pdf(NEU_2014, "Seite 2 ohne Belang"))
    (wurzel / "Festo" / "festo_kopie_dsm.pdf").write_bytes(alt_94)  # Dublette
    (wurzel / "Festo" / "Festo_Allgemein.pdf").write_bytes(pdf("Datenblatt ohne Typ"))
    (wurzel / "P+F" / "P+F_Ini_NJ50_PTB00ATEX2032X.pdf").write_bytes(pdf("", ""))
    (wurzel / "_internet" / "X" / "cache.pdf").write_bytes(pdf("Zwischenspeicher"))
    (wurzel / "lose.pdf").write_bytes(pdf("ohne Herstellerordner"))
    return wurzel
