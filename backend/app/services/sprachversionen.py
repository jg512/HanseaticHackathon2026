"""Sprachversionen je Position (M8): welche Sprachen je Dokumentart vorliegen, welche fehlen,
und ein zweiter Durchgang im Ordner, der fehlende Sprachversionen nachlädt.

Entscheidung des Teams: Die deutsche Fassung der EU-Konformitätserklärung ist
Pflicht, andere Sprachen sind willkommen, aber freiwillig.

- PFLICHTSPRACHE (Standard „de“) gilt für die Pflichtdokumente (status.PFLICHT, M20). Liegt die
  Konformitätserklärung geprüft nur in anderen Sprachen vor, ist die Position nicht vollständig
  (status.py), steht mit „Deutsche Fassung fehlt (Pflicht)“ in der Fehlliste, und die Anfrage an
  den Hersteller fragt gezielt nach der deutschen Fassung.
- SPRACHEN (Standard „de,en“, wie die Spalten (d) und (e) der Kundenliste) sind die Sprachen, die
  gesucht und gezeigt werden. Fehlt eine davon, oder fehlt bei Baumusterprüfbescheinigung und
  Anleitung (freiwillig) die deutsche Fassung, ist das nur ein Hinweis.

Grenzfälle: Ein zweisprachiges PDF, das Deutsch enthält, zählt als deutsche Fassung. Bei einem
Scan ohne lesbaren Text zählt das Sprachkürzel im Dateinamen („_de“, „_ger“, „_d_e“); ein Mensch
hat das Dokument bei der Freigabe gesehen. Ist die Sprache gar nicht bekannt, gilt die deutsche
Fassung als nicht belegt: ein Mensch trägt die Sprache in den Stammdaten ein oder erledigt die
Position von Hand mit Begründung.

Geprüft werden nur die Arten in ARTEN, und nur, wenn die Position schon ein Dokument dieser Art
hat: Fehlt die Art ganz, meldet das status.py (M20). Es zählen Vorschläge, Freigaben und die
geprüften Dokumente des Bauteils in der Datenbank; für die Vollständigkeit nur die geprüften
(gepruefte_sprachen).
"""

import re
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from flask import current_app

from . import lesepuffer, pdf_text, suche

# Arten mit Pflichtsprachen. UK-Dokumente gibt es nur auf Englisch, Sonstiges zählt nicht.
ARTEN = ("eu_konformitaetserklaerung", "eu_baumusterpruefbescheinigung", "bedienungsanleitung")
ZAEHLT = ("vorgeschlagen", "freigegeben")  # abgelehnte Vorschläge decken nichts ab
# Kürzel am Ende des Dateinamens, wie sie im Ordner vorkommen („_de_en_fr_etc“, „.engl“, „_d_e“)
DATEI_SPRACHE = {"de": "de", "d": "de", "ger": "de", "deu": "de", "deutsch": "de",
                 "german": "de", "en": "en", "e": "en", "eng": "en", "engl": "en",
                 "english": "en", "englisch": "en", "fr": "fr", "f": "fr", "fra": "fr",
                 "fre": "fr", "french": "fr", "franz": "fr"}
ENDUNG = re.compile(r"[_. ]+([A-Za-z]+)$")  # Bindestrich nicht: „…-E“ gehört oft zum Typ


def gewaehlt(item):
    """Sprachen, die für den Vorgang der Position beim Start des Agenten gewählt wurden
    (Bom.sprachen), in der gewählten Reihenfolge; leer, wenn nichts gewählt ist."""
    bom = item if hasattr(item, "items") else getattr(item, "bom", None)
    return [s for s in (getattr(bom, "sprachen", None) or "").split(",") if s]


def pflichtsprache(item=None):
    """Sprache, ohne die ein Pflichtdokument nicht zählt (PFLICHTSPRACHE, Standard „de“). Hat
    der Vorgang eigene Sprachen und ist sie nicht dabei, gilt die erste gewählte."""
    p = current_app.config.get("PFLICHTSPRACHE") or "de"
    wahl = gewaehlt(item) if item is not None else []
    return p if not wahl or p in wahl else wahl[0]


def sprachen_liste(item=None):
    """Sprachen, die gesucht und gezeigt werden: die Pflichtsprache zuerst, dann die für den
    Vorgang gewählten, sonst SPRACHEN."""
    wahl = gewaehlt(item) if item is not None else []
    return list(dict.fromkeys([pflichtsprache(item), *(wahl or current_app.config["SPRACHEN"])]))


def passt_zur_wahl(item, finding):
    """Ist das Dokument in einer der gewählten Sprachen? Ohne Wahl und bei unbekannter Sprache
    (Scan) ja: dann entscheidet ein Mensch."""
    wahl = gewaehlt(item)
    da = sprachen_von(finding.languages, finding.file_path)
    return not wahl or not da or bool(da & set(wahl))


def pflichtsprachen():
    """Älterer Name, gilt weiter: die gesuchten und gezeigten Sprachen."""
    return sprachen_liste()


def _pflicht_arten():
    from .status import PFLICHT  # status importiert dieses Modul
    return PFLICHT


def dateiname_teilen(pfad):
    """„Rittal_PTB02ATEX1082U_KEL_94XX_de.pdf“ -> („rittalptb02atex1082ukel94xx“, ["de"]):
    Grundname ohne Sprachkürzel (klein, nur Buchstaben und Ziffern) und die Kürzel."""
    rest, kuerzel = pdf_text.dateiname(Path(pfad)), []
    while (m := ENDUNG.search(rest)) and (m[1].lower() in DATEI_SPRACHE or m[1].lower() == "etc"):
        if m[1].lower() != "etc":
            kuerzel.insert(0, DATEI_SPRACHE[m[1].lower()])
        rest = rest[:m.start()]
    return re.sub(r"[^a-z0-9äöüß]", "", rest.lower()), list(dict.fromkeys(kuerzel))


def sprachen_von(erkannt, pfad):
    """Sprachen eines Dokuments: aus dem Text erkannt („de,en“), sonst aus dem Dateinamen."""
    if erkannt:
        return {s.strip() for s in erkannt.split(",") if s.strip()}
    return set(dateiname_teilen(pfad)[1]) if pfad else set()


def _geordnet(sprachen, pflicht):
    return sorted(sprachen, key=lambda s: (pflicht.index(s) if s in pflicht else len(pflicht), s))


def _gepruefte_dokumente(profile_no):
    part = lesepuffer.teil(profile_no)
    return [pd.document for pd in part.documents if pd.status == "geprueft"] if part else []


def _doc_sprachen(doc):
    erkannt = ",".join(s.language for s in doc.languages)
    return sprachen_von(erkannt, doc.file_path)


def gepruefte_sprachen(profile_no):
    """{Dokumentart: {Sprachen}} nur aus den geprüften Dokumenten des Bauteils in der Datenbank.
    Eine Art mit leerer Menge: Dokument da, Sprache unbekannt."""
    arten = {}
    for doc in _gepruefte_dokumente(profile_no):
        arten.setdefault(doc.doc_type, set()).update(_doc_sprachen(doc))
    return arten


def abdeckung(item):
    """{Dokumentart: {Sprachen}} aus Vorschlägen, Freigaben und den geprüften Dokumenten des
    Bauteils in der Datenbank, nur Arten aus ARTEN."""
    arten = {}
    for f in item.findings:
        if f.status in ZAEHLT and f.doc_type in ARTEN:
            arten.setdefault(f.doc_type, set()).update(sprachen_von(f.languages, f.file_path))
    for art, sprachen in gepruefte_sprachen(item.profile_no).items():
        if art in ARTEN:
            arten.setdefault(art, set()).update(sprachen)
    return arten


def pflicht_fehlt(item, da=None):
    """{Pflichtdokumentart: [Pflichtsprache]}, wo das Pflichtdokument vorliegt (Vorschlag,
    Freigabe oder geprüft), aber nicht in der Pflichtsprache. Für die Übersicht."""
    if item.status == "kein_ex_teil":
        return {}
    da = abdeckung(item) if da is None else da
    p = pflichtsprache(item)
    return {art: [p] for art in _pflicht_arten() if art in da and p not in da[art]}


def fehlende(item, pflicht=None):
    """{Dokumentart: [fehlende Sprachen]}, nur die freiwilligen Lücken (ohne pflicht_fehlt):
    Sprachen aus SPRACHEN, bei Baumusterprüfbescheinigung und Anleitung auch Deutsch."""
    sprachen = pflicht or sprachen_liste(item)
    if item.status == "kein_ex_teil":
        return {}
    p, pflicht_arten = pflichtsprache(item), _pflicht_arten()
    return {art: luecke for art, da in abdeckung(item).items()
            if (luecke := [s for s in sprachen if s not in da
                           and not (art in pflicht_arten and s == p)])}


def luecken(item):
    """Alle Lücken, die der zweite Durchgang schließen soll: {Art: [Sprachen]}, die
    Pflichtsprache jeweils zuerst."""
    if item.status == "kein_ex_teil":
        return {}
    sprachen = sprachen_liste(item)
    return {art: luecke for art, da in abdeckung(item).items()
            if (luecke := [s for s in sprachen if s not in da])}


def uebersicht(bom):
    """Für die API: gezeigte Sprachen, Pflichtsprache und je Position vorhandene Sprachen,
    fehlende Pflichtsprache (pflicht_fehlt) und freiwillige Lücken (fehlende_sprachen)."""
    sprachen = sprachen_liste(bom)
    items = []
    for item in bom.items:
        da = {} if item.status == "kein_ex_teil" else abdeckung(item)
        items.append({"id": item.id, "pos": item.pos, "status": item.status,
                      "sprachen": {a: _geordnet(s, sprachen) for a, s in da.items()},
                      "pflicht_fehlt": pflicht_fehlt(item, da),
                      "fehlende_sprachen": fehlende(item, sprachen)})
    return {"sprachen": sprachen, "pflichtsprache": pflichtsprache(bom), "items": items}


def _nummern(zertifikat, pfad):
    """Eigene Zertifikatsnummer (die erste im Text) und die im Dateinamen:
    {ohne Leerzeichen, groß: wie geschrieben}. Spätere Nummern im Text verweisen oft nur."""
    nummern = [zertifikat.split(";")[0].strip()] if zertifikat else []
    nummern += suche.ZERTIFIKAT.findall(pdf_text.dateiname(Path(pfad)).replace("_", " "))
    return {re.sub(r"\s+", "", n).upper(): n for n in reversed(nummern)}


def gegenstueck(bezug, pfad, pdf):
    """Warum die Datei eine andere Sprachversion des Vorschlags bezug ist, sonst None:
    gleicher Grundname mit Sprachkürzel oder gleiche eigene Zertifikatsnummer."""
    grund, kuerzel = dateiname_teilen(pfad)
    if kuerzel and grund == dateiname_teilen(bezug.file_path)[0]:
        return "der Dateiname ist bis auf das Sprachkürzel gleich"
    eigene = _nummern(bezug.certificate_no, bezug.file_path)
    gleich = sorted(eigene.keys() & _nummern(suche.zertifikatsnummern(pdf.seiten), pfad).keys())
    return f"beide nennen die Zertifikatsnummer {eigene[gleich[0]]}" if gleich else None


def _db_bezug(item):
    """Geprüfte Dokumente des Bauteils mit Datei als Bezug für den zweiten Durchgang, damit
    auch eine nur englisch geprüfte Konformitätserklärung ihre deutsche Fassung findet."""
    return [SimpleNamespace(doc_type=d.doc_type, file_path=d.file_path,
                            certificate_no=d.certificate_no, file_hash=d.file_hash)
            for d in _gepruefte_dokumente(item.profile_no) if d.file_path]


def nachladen(item, ordner, wurzel):
    """Zweiter Durchgang im Herstellerordner: Fehlt einer Dokumentart eine Sprache, werden
    die Sprachversionen der schon gefundenen Dokumente dieser Art neue Vorschläge.

    Die Pflichtsprache zuerst: Dateien, die sie enthalten, werden vor den anderen geprüft.
    Bezug sind Vorschläge und Freigaben aus dem Ordner, solche der Ähnlichkeitssuche mit
    Typ-Treffer und die geprüften Dokumente des Bauteils.
    Ausgewertet wird wie jede Datei im Ordner (suche.auswerten); ohne Typ-Treffer bleibt das
    Ergebnis „unklar“. Ein schon vorgeschlagener Scan („sonstiges“, ohne Sprache) wird als
    Sprachversion gedeutet statt doppelt vorgeschlagen. Ist eine Lücke geschlossen, wird für sie
    nichts mehr vorgeschlagen. Gibt die neuen Vorschläge zurück.
    """
    fehlt = luecken(item)
    # Funde aus dem Ordner und der Ähnlichkeitssuche, diese nur mit Typ-Treffer (sonst ist schon
    # das Dokument selbst unsicher und seine Sprachversion erst recht)
    bezug = [f for f in item.findings if f.status in ZAEHLT and f.doc_type in fehlt and f.file_path
             and (f.source == "ordner" or (f.source == "suchdienst" and f.matched_term))]
    db_bezug = [d for d in _db_bezug(item) if d.doc_type in fehlt]
    bezug += db_bezug
    if not bezug:
        return []
    # auch abgelehnte: nicht neu vorschlagen; geprüfte Dokumente auch nicht
    bekannt = {f.file_hash: f for f in reversed(item.findings)}
    schon_geprueft = {d.file_hash for d in db_bezug if d.file_hash}
    p = pflichtsprache(item)
    kandidaten = []
    for pfad in sorted(p_ for p_ in ordner.rglob("*") if p_.suffix.lower() == ".pdf"):
        pdf = pdf_text.lies(pfad)
        da = bekannt.get(pdf.sha256)
        if pdf.sha256 in schon_geprueft or da and not (
                da.status == "vorgeschlagen" and da.doc_type == "sonstiges" and not da.languages):
            continue
        sprachen = sprachen_von(suche.sprachen(pdf.seiten), pfad)
        kandidaten.append((p not in sprachen, pfad, pdf, sprachen))
    neu = []
    for _, pfad, pdf, sprachen in sorted(kandidaten, key=lambda k: k[0]):  # stabil: Pflicht zuerst
        da = bekannt.get(pdf.sha256)
        art = suche.dokumentart(pdf.seiten)  # Scan: „sonstiges“
        for f in bezug:
            luecke = [s for s in fehlt.get(f.doc_type, []) if s in sprachen]
            if not luecke or art not in (f.doc_type, "sonstiges"):
                continue
            if not (wie := gegenstueck(f, pfad, pdf)):
                continue
            if da:
                _als_sprachversion(da, f, wie, luecke, sprachen)
            else:
                neu.append(_als_sprachversion(suche.auswerten(item, pfad, wurzel), f, wie,
                                              luecke, sprachen))
                bekannt[pdf.sha256] = neu[-1]
            fehlt[f.doc_type] = [s for s in fehlt[f.doc_type] if s not in sprachen]
            break
    return neu


FASSUNG = {"de": "deutsche", "en": "englische", "fr": "französische"}


def _fassungen(sprachen):
    namen = [FASSUNG.get(s, s) for s in sprachen]
    return " und ".join(namen) + (" Fassung" if len(namen) == 1 else " Fassungen")


def _als_sprachversion(finding, bezug, wie, luecke, sprachen):
    name = PurePosixPath(bezug.file_path).name
    hinweis = (f"Das ist vermutlich die {_fassungen(luecke)} von {name}, denn {wie}.")
    if finding.doc_type == "sonstiges":  # Scan ohne lesbaren Kopf
        finding.doc_type = bezug.doc_type
        hinweis += " Die Dokumentart haben wir von dort übernommen."
    if not finding.languages:
        finding.languages = ",".join(_geordnet(sprachen, sprachen_liste()))
        hinweis += " Die Sprache steht nur im Dateinamen."
    finding.check_message = f"{hinweis} {finding.check_message}"
    return finding
