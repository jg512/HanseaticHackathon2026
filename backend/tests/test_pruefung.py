import pytest

from app.services.pruefung import (bewerte, finde_typ, kategorie_namen, kategorien,
                                   kennzeichnungen, lies_typangabe, pruefe_typ, pruefe_zone,
                                   steht_im_text, zone)


@pytest.mark.parametrize("text, art, begriff", [
    ("VTUG-10-VI-EX2E", "typ", "VTUG-10-VI-EX2E"),
    ("ET 200iSP IM 152-1", "typ", "ET 200iSP IM 152-1"),
    ("DSNU-…-EX", "muster", "DSNU"),
    ("VPPM-6…-EX", "muster", "VPPM-6"),
    ("SFAB-...-EX2", "muster", "SFAB"),
    ("KEL 94XX", "muster", "KEL 94"),
    ("78E-Serie, Ex-Ausführung", "reihe", "78E"),
    ("Kameragehäuse Ex", "beschreibung", "Kameragehäuse"),
    ("Kabeldose M12 Ex", "beschreibung", "Kabeldose M12"),
    ("AE, Ex-Ausführung Zone 2", "beschreibung", "AE"),
])
def test_typangabe_einordnen(text, art, begriff):
    angabe = lies_typangabe(text)
    assert (angabe.art, angabe.begriff) == (art, begriff)


def finde(artikel, *seiten, dateiname=""):
    return finde_typ(lies_typangabe(artikel), seiten, dateiname)


def test_genauer_typ_ohne_ruecksicht_auf_schreibweise():
    t = finde("SMT-8F-I-8,2V-K5,0-OE-EX", "Deckblatt", "Näherungsschalter\nsmt 8f-i-8.2v-k5.0-oe-ex\n")
    assert (t.genau, t.seite, t.wie_geschrieben) == (True, 2, "smt 8f-i-8.2v-k5.0-oe-ex")
    assert "Näherungsschalter" in t.textstelle
    # in der Tabelle umgebrochen
    t = finde("VACF-B-K1-1A-1-EX4-M", "Type\nVACF-B-K1-1A-1-\nEX4-M\nVACF-B-K1-1A-5-\nEX4-M")
    assert (t.genau, t.wie_geschrieben) == (True, "VACF-B-K1-1A-1-EX4-M")
    assert pruefe_typ(lies_typangabe("VACF-B-K1-1A-1-EX4-M"), t) == (
        "ok", "Der Typ „VACF-B-K1-1A-1-EX4-M“ steht genau so im Dokument, auf Seite 1.")


def test_anderer_typ_derselben_reihe_ist_kein_treffer():
    # Falle aus der Stückliste: Pos. 10 gegen ein Dokument zu „VACF-B-K1-1-5“
    angabe = lies_typangabe("VACF-B-K1-1A-1-EX4-M")
    t = finde_typ(angabe, ["Konformitätserklärung Magnetspule VACF-B-K1-1-5 II 2G"])
    assert (t.genau, t.wie_geschrieben) == (False, "VACF-B-K1-1-5")
    ergebnis, meldung = pruefe_typ(angabe, t)
    assert ergebnis == "unklar"
    assert meldung.startswith("Im Dokument steht nur „VACF-B-K1-1-5“, nicht genau der Typ")


@pytest.mark.parametrize("text, wie", [
    ("KCD2-SR-EX1.LB.SP Switch amplifier", "KCD2-SR-EX1.LB.SP"),  # längerer Typ
    ("ET 200iSP, IM 152-1 PN", "ET 200iSP, IM 152-1 PN"),       # Variante mit Zusatz
    ("KCD2-SOT-EX1.LB 214240", "KCD2-SOT-EX1.LB"),              # nur die Reihe
])
def test_teiltreffer(text, wie):
    artikel = "ET 200iSP IM 152-1" if "200iSP" in text else "KCD2-SR-Ex1.LB"
    t = finde(artikel, text)
    assert (t.genau, t.wie_geschrieben) == (False, wie)


def test_keine_treffer_mitten_im_wort_oder_zu_kurz():
    assert finde("DSM-…-EX", "Type ADSM-12 und DSMX-1") is None
    assert finde("NJ…", "Inductive sensor NJ50-FP-N", dateiname="P+F_Ini") is None  # nur „NJ“
    assert finde("8146", "Seite 18146") is None
    assert finde("MS-NAMUR-12", "Inductive sensor NJ50") is None


def test_muster_mit_kurzem_anfang_braucht_den_schluss_als_eigenes_glied():
    """„NJ…-N“: „NJ“ allein ist zu kurz, zusammen mit dem Schluss „-N“ zählt die Bezeichnung."""
    a = lies_typangabe("NJ…-N")
    assert (a.art, a.begriff, a.schluss) == ("muster", "NJ", "N")
    assert lies_typangabe("DSNU-…-EX").schluss == ""  # langer Anfang: wie bisher
    for text, wie in (("Types NJ0,8-5GM-N... and others", "NJ0,8-5GM-N"),
                      ("Inductive sensor NJ50-FP-N", "NJ50-FP-N"),
                      ("sensor NJ 8-18GM-N", "NJ 8-18GM-N")):
        t = finde("NJ…-N", text)
        assert t is not None and t.wie_geschrieben == wie and not t.genau
    for text in ("NJ10-30GK-SN...", "NJ15S+U3+N", "Typ KNJ5-8-N", "NJ und -N", "nj5-8-n"):
        assert finde("NJ…-N", text) is None
    ergebnis, meldung = pruefe_typ(a, finde("NJ…-N", "Inductive sensor NJ50-FP-N"))
    assert ergebnis == "unklar" and "im Dokument steht „NJ50-FP-N“" in meldung


def test_muster_darf_direkt_weitergehen():
    t = finde("KEL 94XX", "Operating Instructions\nKEL 9401 - KEL 9416")
    assert t.wie_geschrieben == "KEL 9401"
    ergebnis, meldung = pruefe_typ(lies_typangabe("KEL 94XX"), t)
    assert ergebnis == "unklar"
    assert meldung.startswith("Die Stückliste nennt nur ein Typmuster („KEL 94XX“)")


def test_nur_dateiname():
    angabe = lies_typangabe("OP170B")
    t = finde_typ(angabe, ["", ""], "OP170B_04ATEX1297_de")
    assert (t.im_text, t.seite, t.wie_geschrieben) == (False, None, "OP170B")
    assert pruefe_typ(angabe, t, text_lesbar=False) == (
        "unklar", "Der Typ „OP170B“ steht nur im Dateinamen. Im Dokument ist kein Text lesbar, "
                  "geprüft ist nur der Dateiname.")
    angabe = lies_typangabe("Kameragehäuse Ex")
    t = finde_typ(angabe, ["Camera housing"], "P+F_Kameragehäuse_INERIS14ATEX0035X_en")
    assert pruefe_typ(angabe, t)[1].endswith("Im lesbaren Text des Dokuments steht er nicht.")


def test_kennzeichnungen_wie_geschrieben():
    text = "Kennzeichnung:\nII 2G Ex ia IIC T4 Gb\nII 2 G (1) G\nEx eb [ia Ga/ib] IIC T4 Gb\nIIC T4"
    assert kennzeichnungen(text) == ["II 2G Ex ia IIC T4 Gb", "II 2 G (1) G Ex eb [ia Ga/ib] IIC T4 Gb"]
    assert kennzeichnungen("Annex II of the directive, Gruppe IIC, Kategorie II 2 Gas") == []


@pytest.mark.parametrize("kennz, erwartet", [
    ("II 2G Ex h IIC T4 Gb", {(2, "G", False)}),
    ("II 2 G", {(2, "G", False)}),
    ("II 3G Ex ec IIC T4 Gc", {(3, "G", False)}),
    ("II 2GD c T4", {(2, "G", False), (2, "D", False)}),
    ("II 1/2G Ex ia/db", {(2, "G", False)}),
    ("II (1)G [Ex ia Ga] IIC", {(1, "G", True)}),
    ("II 2 G (1) D", {(2, "G", False), (1, "D", True)}),
    ("IIC T4", set()),
])
def test_kategorien(kennz, erwartet):
    assert kategorien(kennz) == erwartet


@pytest.mark.parametrize("ort, erwartet", [
    ("Zone 1", ("G", 1)), ("Zone 2", ("G", 2)), ("zone 0", ("G", 0)), ("Zone 21", ("D", 1)),
    ("Schaltschrank außerhalb Ex", "ausserhalb"), ("Halle 3", None), (None, None),
    # dieselben Wörter wie die EX-Bauteil-Liste (ex_liste.AUSSERHALB)
    ("sicherer Bereich", "ausserhalb"),
    ("Control cabinet, safe area", "ausserhalb"), ("non-hazardous area", "ausserhalb"),
    ("Nonhazardous", "ausserhalb"), ("Zone 1, nicht Ex-Bereich", ("G", 1)),
])
def test_zone(ort, erwartet):
    assert zone(ort) == erwartet


@pytest.mark.parametrize("ort, kennz, ergebnis, meldung", [
    ("Zone 1", ["II 2G Ex ia IIC T4 Gb"], "ok", "Die Kategorie 2G passt zu Zone 1."),
    ("Zone 1", ["II 1 GD EEx ia IIC T6"], "ok", "Die Kategorie 1G passt zu Zone 1."),
    ("Zone 2", ["II 3G Ex ec IIC T4 Gc"], "ok", "Die Kategorie 3G passt zu Zone 2."),
    ("Zone 1", ["II 3G Ex ec IIC T4 Gc"], "warnung",
     "Die Kategorie 3G reicht für Zone 1 nicht aus. Verlangt ist 1G oder 2G."),
    ("Zone 1", ["II 2G Ex h", "II 3G Ex ec"], "unklar",
     "Das Dokument nennt mehrere Kategorien (2G, 3G), zu Zone 1 passt nur 2G. "
     "Welche zum Typ gehört, ist nicht sicher."),
    ("Schaltschrank außerhalb Ex", ["II (1) G", "II 3 G"], "ok",
     "Das Gerät ist ein zugehöriges Betriebsmittel, das Dokument nennt die Kategorie (1)G, 3G. "
     "Es darf außerhalb des Ex-Bereichs eingebaut werden, das passt."),
    # ohne Kategorie in Klammern: keine Frage an den Menschen, ein klarer Prüfauftrag
    ("Schaltschrank außerhalb Ex", ["II 2G Ex d"], "unklar",
     "Der Einbauort liegt außerhalb des Ex-Bereichs, das Dokument nennt aber die Kategorie 2G. "
     "Bitte prüfen Sie den Einbauort und das Dokument."),
    ("sicherer Bereich", ["II 2G Ex d"], "unklar",
     "Der Einbauort liegt außerhalb des Ex-Bereichs, das Dokument nennt aber die Kategorie 2G. "
     "Bitte prüfen Sie den Einbauort und das Dokument."),
    # Kategorie in Klammern wird nie als passend für eine Zone gezählt
    ("Zone 1", ["II (1) G [Ex ia Ga] IIC"], "warnung",
     "Das Dokument nennt nur die Kategorie (1)G in Klammern. Ein solches zugehöriges "
     "Betriebsmittel muss außerhalb des Ex-Bereichs stehen, laut Stückliste sitzt es aber in "
     "Zone 1."),
    ("Zone 1", [], "unklar", "Im Text steht keine lesbare Ex-Kennzeichnung. Deshalb lässt sich "
                             "die Kategorie nicht prüfen."),
    ("Halle 3", ["II 2G"], "unklar", "Der Einbauort „Halle 3“ nennt keine Zone. Deshalb lässt sich "
                                     "die Kategorie nicht prüfen."),
])
def test_zone_gegen_kategorie(ort, kennz, ergebnis, meldung):
    assert pruefe_zone(ort, kennz) == (ergebnis, meldung)


def test_bewerte():
    ok, unklar, warnung = ("ok", "T."), ("unklar", "U."), ("warnung", "W.")
    assert bewerte(ok, ok) == ("ok", "T. T.")
    assert bewerte(ok, unklar) == ("unklar", "T. U.")
    assert bewerte(unklar, ok) == ("unklar", "U. T.")
    assert bewerte(unklar, warnung) == ("warnung", "U. W.")


@pytest.mark.parametrize("wert, text, erwartet", [
    ("II 2G Ex ia IIC T4 Gb", "Kennzeichnung:\nII 2 G Ex ia\nIIC T4 Gb", True),  # umgebrochen
    ("IBExU16ATEX1146 X", "Nr. IBExU 16 ATEX 1146 X", True),
    ("II 2G", "II 2GD c T4", False),        # kurz: nicht mitten in einer Kennzeichnung
    ("II 2G", "II 2GD c T4 und II 2G", True),
    ("PTB 99 ATEX 9999", "PTB 00 ATEX 2048 X", False),
    ("", "irgendwas", False),
])
def test_steht_im_text(wert, text, erwartet):
    assert steht_im_text(wert, text) is erwartet


def test_steht_im_text_typ_genau():
    angabe = lies_typangabe("KCD2-SR-Ex1.LB")
    assert steht_im_text(angabe.text, "KCD2-SR-EX1.LB | 214240", angabe)
    assert not steht_im_text(angabe.text, "KCD2-SR-EX1.LB.SP | 240650", angabe)  # geht weiter


def test_kategorie_namen():
    assert kategorie_namen(["II 2G Ex h", "II (1) D", "IIC T4"]) == "(1)D, 2G"
    assert kategorie_namen([]) == ""


def test_ausserhalb_wie_in_der_ex_liste():
    from app.services import ex_liste, pruefung
    assert pruefung.AUSSERHALB.pattern == ex_liste.AUSSERHALB.pattern
    assert pruefung.AUSSERHALB.flags == ex_liste.AUSSERHALB.flags
