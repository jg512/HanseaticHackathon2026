from pathlib import Path

from app import altbestand
from app.db import db
from app.modelle import Dokument, Hersteller, Teil

from conftest import NEU_2014, pdf


def test_import_zaehlt_und_legt_ab(app, alt):
    lauf = altbestand.importiere(alt)
    db.session.commit()
    z = lauf.zahlen
    assert (z["dateien"], z["dubletten"], z["dokumente_neu"]) == (5, 1, 4)
    assert (z["nicht_lesbar"], z["mit_teil"], z["ohne_teil"]) == (1, 3, 1)
    assert (z["teile_neu"], z["verknuepfungen"], z["mehrdeutig"]) == (2, 3, 1)
    assert z["markierungen_je_regel"] == {"richtlinie_94_9": 1, "norm_ausgabe": 1, "alter": 1,
                                          "gueltig_bis": 0, "kein_datum": 2}
    assert any("festo_kopie_dsm.pdf" in h for h in lauf.hinweise)
    assert "Bestand eingelesen aus" in altbestand.bericht(lauf)

    # Ordnername „P+F“ wird zum Herstellernamen, Ablage unter aktuell/<Hersteller>/
    pf = db.session.scalar(db.select(Hersteller).filter_by(ordner="P+F"))
    assert pf.name == "Pepperl+Fuchs"
    scan = db.session.scalar(db.select(Dokument).filter_by(hersteller_id=pf.id))
    assert scan.pfad == "aktuell/Pepperl+Fuchs/P+F_Ini_NJ50_PTB00ATEX2032X.pdf"
    assert not scan.lesbar and scan.doc_type == "unbekannt"
    assert (Path(app.config["ABLAGE"]) / scan.pfad).is_file()

    alt_doc = db.session.scalar(db.select(Dokument).filter_by(dateiname="festo_dsm_konf_714757.pdf"))
    assert sorted(alt_doc.altpfade) == ["Festo/festo_dsm_konf_714757.pdf",
                                        "Festo/festo_kopie_dsm.pdf"]
    assert {m.regel for m in alt_doc.markierungen} == {"richtlinie_94_9", "norm_ausgabe", "alter"}
    assert all(m.status == "verdacht" and m.seite == 1 for m in alt_doc.markierungen)

    # zwei Erklärungen zum Teil DSM: beide Version 1, mit Hinweis statt geratener Reihenfolge
    dsm = db.session.scalar(db.select(Teil).filter_by(artikelnr="DSM"))
    assert [v.version for v in dsm.verknuepfungen] == [1, 1]
    assert all("ließ sich nicht erkennen" in v.hinweis for v in dsm.verknuepfungen)


def test_import_ist_wiederholbar(app, alt):
    altbestand.importiere(alt)
    db.session.commit()
    (alt / "Festo" / "Festo_Neu_DGC_Konf.pdf").write_bytes(pdf(NEU_2014.replace("DSM", "DGC")))
    z = altbestand.importiere(alt).zahlen
    db.session.commit()
    assert (z["dateien"], z["schon_bekannt"], z["dokumente_neu"]) == (6, 4, 1)
    assert db.session.scalar(db.select(db.func.count(Dokument.id))) == 5


def test_befehl_nur_wenn_leer(app, alt):
    runner = app.test_cli_runner()
    ergebnis = runner.invoke(args=["import-altbestand", "--nur-wenn-leer"])
    assert "Dateien: 5" in ergebnis.output, ergebnis.output
    ergebnis = runner.invoke(args=["import-altbestand", "--nur-wenn-leer"])
    assert "übersprungen" in ergebnis.output


def test_fehlender_ordner(app, tmp_path):
    runner = app.test_cli_runner()
    ergebnis = runner.invoke(args=["import-altbestand"])
    assert ergebnis.exit_code != 0
