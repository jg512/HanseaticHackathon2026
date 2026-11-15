"""Angaben eines Vorschlags gegen die Quelle prüfen, Unbelegtes markieren (Regeln 2 und 3).

Prozessschritt „Werte und Tabellen gegen Quelle prüfen“. Je Angabe, die das System zu einem
Dokument macht, sucht der Code die Stelle im PDF: Typ der Position, Ex-Kennzeichnung,
Zertifikatsnummer, Kategorie gegen den Einbauort. Jede Angabe ist danach „belegt“ mit Seite, und
wenn sie in einer Tabellenzelle steht, mit Tabelle und Zeile; oder „unbelegt“ mit Grund. Stehen
zwei Angaben in derselben Tabellenzeile (Kennzeichnung und Zertifikat), sagt der Hinweis das,
ebenso, wenn eine Angabe in derselben Tabelle wie der Typ, aber in der Zeile eines anderen steht.
Ein Vorschlag mit unbelegter Angabe ist nie „ok“.

Die Prüfung läuft für jeden neuen Vorschlag mit Datei, bevor er gespeichert wird (before_flush).
So gilt sie für Funde aus Ordner, Internet und Upload gleich, ohne dass die Suche sie aufruft.
Im selben Schritt liest services/angaben.py die weiteren Angaben (Gültigkeit, Normen, …).
Tabellen gibt es nur auf Seiten mit Textebene (pdf_text.tabellen); bei Scans steht im Hinweis,
dass keine Tabelle ausgewertet ist.
"""

from flask import current_app, has_app_context
from sqlalchemy import event
from sqlalchemy.orm import Session

from ..models import BomItem, Finding
from . import angaben, freigabe, pdf_text, pruefung

NAME = {"typ": "Typ", "kennzeichnung": "Kennzeichnung", "zertifikat": "Zertifikatsnummer",
        "kategorie": "Kategorie"}
# Tabellen je Angabe auf höchstens so vielen Seiten suchen: find_tables braucht bis 1 s je Seite
TABELLENSEITEN = 2
SCAN = ("Diese Seite ist eingescannt, ihr Text ist nicht sicher gelesen. Bitte prüfen Sie den "
        "Wert am Seitenbild. Tabellen auf eingescannten Seiten wertet der Agent nicht aus.")
ART = {"muster": "Die Stückliste nennt nur ein Typmuster.",
       "reihe": "Die Stückliste nennt nur eine Baureihe.",
       "beschreibung": "Die Stückliste nennt keinen Typ, nur ein Stichwort."}


def _beleg(wert, angabe, belegt, ort=None, hinweis=None):
    seite, tabelle, zeile = ort or (None, None, None)
    return {"wert": wert, "angabe": angabe, "belegt": belegt, "seite": seite,
            "tabelle": tabelle, "zeile": zeile, "hinweis": hinweis}


def _liste(text):
    return [t.strip() for t in (text or "").split(";") if t.strip()]


def _ort(pfad, pdf, passt, zuerst=None, seiten_max=TABELLENSEITEN):
    """(Seite, Tabelle, Zeile) der Angabe, Tabelle und Zeile None außerhalb von Tabellen.

    Gesucht wird auf den Seiten, deren Text passt, Seite zuerst vorn. Eine Tabellenzelle geht
    vor: Steht die Angabe in keiner, zählt die erste Seite. None, wenn sie nirgends steht.
    """
    nummern = [nr for nr, text in enumerate(pdf.seiten, start=1) if passt(text)]
    if not nummern:
        return None
    if zuerst in nummern:
        nummern.insert(0, nummern.pop(nummern.index(zuerst)))
    for nr in nummern[:seiten_max]:
        for t in pdf_text.tabellen(pfad, nr) or ():
            for z, zeile in enumerate(t.zeilen, start=1):
                if any(passt(zelle) for zelle in zeile):
                    return nr, t.nr, z
    return nummern[0], None, None


def _scan(pdf, ort):
    return SCAN if ort and ort[0] in pdf.ocr else None


def _fehlt(pdf):
    hinweis = "Das steht auf keiner lesbaren Seite des Dokuments."
    if pdf.ungelesen:
        seiten = pdf_text.seitenangabe(pdf.ungelesen)
        hinweis += (f" {seiten} {'sind' if seiten.startswith('Seiten') else 'ist'} eingescannt "
                    "und noch nicht gelesen.")
    return hinweis


def _typ(artikel, pfad, pdf, seite):
    """Typ der Position: belegt nur, wenn genau dieser Typ im Text steht."""
    if not artikel:
        return _beleg("typ", None, False, hinweis="Die Stückliste nennt keinen Typ.")
    angabe = pruefung.lies_typangabe(artikel)
    if angabe.art == "typ":
        ort = _ort(pfad, pdf, lambda t: pruefung.steht_im_text(artikel, t, angabe), seite)
        if ort:
            return _beleg("typ", artikel, True, ort, _scan(pdf, ort))
    grund = ART.get(angabe.art, "Genau dieser Typ steht nicht im Dokument.")
    treffer = pruefung.finde_typ(angabe, pdf.seiten, pdf_text.dateiname(pfad))
    if treffer is None:
        return _beleg("typ", artikel, False, hinweis=f"{grund} {_fehlt(pdf)}")
    if not treffer.im_text:
        return _beleg("typ", artikel, False,
                      hinweis=f"{grund} „{treffer.wie_geschrieben}“ steht nur im Dateinamen.")
    # die ähnliche Stelle zeigen, als Hilfe für den Menschen, nicht als Beleg
    ort = _ort(pfad, pdf, lambda t: pruefung.steht_im_text(treffer.wie_geschrieben, t),
               treffer.seite, seiten_max=1)
    return _beleg("typ", artikel, False, ort,
                  f"{grund} Im Dokument steht nur „{treffer.wie_geschrieben}“.")


def _gefunden(wert, angabe, pfad, pdf):
    ort = _ort(pfad, pdf, lambda t: pruefung.steht_im_text(angabe, t))
    return _beleg(wert, angabe, ort is not None, ort, _scan(pdf, ort) if ort else _fehlt(pdf))


def _kategorie(einbauort, kennz):
    """Kategorie aus den belegten Kennzeichnungen gegen die Zone des Einbauorts."""
    belegt = [b for b in kennz if b["belegt"] and pruefung.kategorien(b["angabe"])]
    if not belegt:
        grund = "Im Text steht keine lesbare Ex-Kennzeichnung." if not kennz else \
            "Keine Kennzeichnung mit lesbarer Kategorie ist im Dokument belegt."
        return _beleg("kategorie", None, False, hinweis=grund)
    namen = pruefung.kategorie_namen([b["angabe"] for b in belegt])
    erste = belegt[0]
    ort = erste["seite"], erste["tabelle"], erste["zeile"]
    if pruefung.zone(einbauort) is None:
        return _beleg("kategorie", namen, False, ort,
                      f"Der Einbauort „{einbauort or ''}“ nennt keine Zone. Deshalb ist der "
                      "Vergleich nicht belegt.")
    return _beleg("kategorie", namen, True, ort,
                  f"Abgeleitet aus der Kennzeichnung „{erste['angabe']}“ und dem Einbauort "
                  f"„{einbauort}“.")


def _dazu(b, text):
    b["hinweis"] = f"{b['hinweis']} {text}" if b["hinweis"] else text


def _tabellenzeilen(belege):
    """Hinweise aus der Lage in Tabellen: was in derselben Zeile steht, und was in derselben
    Tabelle wie der Typ, aber in einer anderen Zeile (gehört dann evtl. zu einem anderen Typ).
    Die Kategorie ist aus der Kennzeichnung abgeleitet und zählt hier nicht."""
    in_tabelle = [b for b in belege if b["belegt"] and b["tabelle"] and b["wert"] != "kategorie"]
    typ = next((b for b in in_tabelle if b["wert"] == "typ"), None)
    for b in in_tabelle:
        andere = [f"{NAME[a['wert']]} „{a['angabe']}“" for a in in_tabelle
                  if a["wert"] != b["wert"]
                  and (a["seite"], a["tabelle"], a["zeile"]) == (b["seite"], b["tabelle"], b["zeile"])]
        if andere:
            _dazu(b, f"In derselben Tabellenzeile steht {_und(andere)}.")
        if typ and b is not typ and (b["seite"], b["tabelle"]) == (typ["seite"], typ["tabelle"]) \
                and b["zeile"] != typ["zeile"]:
            _dazu(b, f"Das steht in Zeile {b['zeile']}, der Typ aber in Zeile {typ['zeile']} "
                     "derselben Tabelle. Es gehört womöglich zu einem anderen Typ.")


def _und(teile):
    return teile[0] if len(teile) == 1 else ", ".join(teile[:-1]) + " und " + teile[-1]


def pruefe(item, finding, basis):
    """Belege je Angabe des Vorschlags: Typ, Kennzeichnungen, Zertifikatsnummern, Kategorie."""
    artikel = (item.manufacturer_article_no or "").strip() or None
    kennz, nummern = _liste(finding.ex_marking_raw), _liste(finding.certificate_no)
    pfad = freigabe.datei(finding, basis)
    if pfad is None:
        fehlt = ("Die Datei fehlt. Deshalb lässt sich der Wert nicht im Dokument "
                 "nachprüfen.")
        return ([_beleg("typ", artikel, False, hinweis=fehlt)]
                + [_beleg("kennzeichnung", k, False, hinweis=fehlt) for k in kennz]
                + [_beleg("zertifikat", n, False, hinweis=fehlt) for n in nummern]
                + [_beleg("kategorie", pruefung.kategorie_namen(kennz) or None, False,
                          hinweis=fehlt)])
    pdf = pdf_text.lies(pfad)
    belege = [_typ(artikel, pfad, pdf, finding.page)]
    belege += [_gefunden("kennzeichnung", k, pfad, pdf) for k in kennz]
    belege += [_gefunden("zertifikat", n, pfad, pdf) for n in nummern]
    belege.append(_kategorie(item.location, belege[1:1 + len(kennz)]))
    _tabellenzeilen(belege)
    return belege


def bewerte(ergebnis, meldung, belege):
    """Mit unbelegter Angabe wird aus „ok“ „unklar“, mit Grund; warnung und unklar bleiben."""
    offen = [b for b in belege if not b["belegt"]]
    if ergebnis != "ok" or not offen:
        return ergebnis, meldung
    namen = _und([NAME[b["wert"]] + (f" „{b['angabe']}“" if b["angabe"] else "")
                  for b in offen])
    verb = "ist" if len(offen) == 1 else "sind"
    return "unklar", f"{meldung or ''} Im Dokument nicht belegt {verb} {namen}.".strip()


def pruefe_vorschlag(finding, item, basis):
    """Setzt finding.belege und passt das Prüfergebnis an. Dazu die weiteren Angaben aus dem
    Dokument (services/angaben.py: Gültigkeit, Richtlinien, Hersteller …) mit ihrer Vorprüfung,
    nach den Belegen, damit eine Warnung dort („abgelaufen“) nicht überschrieben wird."""
    finding.belege = pruefe(item, finding, basis)
    finding.check_result, finding.check_message = bewerte(
        finding.check_result, finding.check_message, finding.belege)
    if angaben.fuelle(finding, item, basis):
        angaben.pruefe(finding)


@event.listens_for(Session, "before_flush")
def _neue_vorschlaege(session, flush_context, instances):
    """Jeden neuen Vorschlag mit Datei prüfen, bevor er gespeichert wird."""
    if not has_app_context():
        return
    for f in [o for o in session.new if isinstance(o, Finding)]:
        if f.belege is None and f.file_path:
            item = f.item or (f.bom_item_id and session.get(BomItem, f.bom_item_id))
            if item is not None:
                pruefe_vorschlag(f, item, current_app.config["ATEX_DOCS"])
