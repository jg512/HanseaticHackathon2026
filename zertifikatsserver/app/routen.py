"""HTTP-Endpunkte. JSON überall, Fehler als {"error": "…"} in Sätzen für Anwender. Neben
technischen Werten stehen die Texte für Anwender (Felder „…_text“, begriffe.py). Übersicht in
README.md."""

import re

from flask import Blueprint, abort, current_app, request, send_file
from sqlalchemy import text

from . import (ablage, auslesen, bearbeiten, begriffe, dateiformate, normen as normtabelle,
               quellen, regeln)
from .begriffe import text as wort
from .db import db
from .modelle import (DOC_TYPES, STAENDE, Aenderung, Angabe, Dokument, Hersteller, Importlauf,
                      Markierung, NormAusgabe, Teil, Verknuepfung, jetzt)

bp = Blueprint("api", __name__)
NAME_FEHLT = ("Ohne Ihren Namen kann das Dokumentenarchiv nichts ändern. Bitte tragen Sie Ihren "
              "Namen in den Einstellungen ein.")
GRUND_FEHLT = "Bitte schreiben Sie einen kurzen Grund dazu."
LISTE_FEHLT = ("Die Auswahl der Dokumente ist nicht angekommen. Bitte laden Sie die Seite neu und "
               "versuchen Sie es noch einmal.")
DATEI_FEHLT = "Es ist keine Datei angekommen. Bitte wählen Sie die Datei noch einmal aus."
ART_FEHLT = ("Die Dokumentart ließ sich nicht erkennen. Bitte wählen Sie aus, um welche Art von "
             "Dokument es sich handelt.")


def _iso(wert):
    return wert.isoformat() if wert else None


def _fehler(meldung, code=400):
    return {"error": meldung}, code


def _daten():
    daten = request.get_json(silent=True)
    return daten if isinstance(daten, dict) else {}


def _name(daten, feld="von"):
    # Wer handelt: der Angemeldete, den nginx nach der Prüfung im Kopf X-Anwender schickt
    # (URL-kodiert, web/nginx.conf); ohne Kopf (Aufrufe des Backends, Tests) „von“ im Körper
    if feld == "von" and request.headers.get("X-Anwender"):
        from urllib.parse import unquote
        return unquote(request.headers["X-Anwender"]).strip()[:200]
    wert = daten.get(feld)
    return wert.strip() if isinstance(wert, str) else ""


def _ids(daten, feld):
    werte = daten.get(feld) or []
    if not isinstance(werte, list) or not all(isinstance(x, int) for x in werte):
        return None
    return list(dict.fromkeys(werte))


# ---------- Darstellung als JSON ----------

def hersteller_json(h):
    return {"id": h.id, "name": h.name, "ordner": h.ordner}


def markierung_json(m):
    return {"id": m.id, "dokument_id": m.dokument_id, "regel": m.regel,
            "regel_titel": regeln.REGELN.get(m.regel, regeln.MANUELL), "stufe": m.stufe,
            "stufe_text": wort(begriffe.STUFE, m.stufe),
            "grund": m.grund, "seite": m.seite, "textstelle": m.textstelle, "status": m.status,
            "status_text": wort(begriffe.MARKIERUNGSSTAND, m.status),
            "erstellt_von": wort(begriffe.VON, m.erstellt_von), "erstellt_am": _iso(m.erstellt_am),
            "entschieden_von": m.entschieden_von, "entschieden_am": _iso(m.entschieden_am),
            "entscheidung_grund": m.entscheidung_grund}


def aenderung_json(a):
    """Eintrag im Protokoll; *_text in Worten (Feldnamen, Dokumentarten, Daten), von: System-
    namen wie „Import Altbestand“ in Worten für Anwender."""
    feld = a.feld
    if a.objekt == "norm_ausgabe":  # dort steht die Norm im Feld
        feld_text = f"Norm {a.feld}"
    else:
        feld_text = wort(begriffe.FELD, a.feld)
    return {"id": a.id, "objekt": a.objekt, "objekt_text": wort(begriffe.OBJEKT, a.objekt),
            "objekt_id": a.objekt_id, "feld": feld, "feld_text": feld_text,
            "alt": a.alt, "neu": a.neu, "alt_text": begriffe.wert_text(feld, a.alt),
            "neu_text": begriffe.wert_text(feld, a.neu),
            "von": wort(begriffe.VON, a.von), "am": _iso(a.am), "grund": a.grund}


def aenderungen(*paare):
    """Protokoll zu [(Objekt, [ids])], neueste zuerst."""
    bedingungen = [db.and_(Aenderung.objekt == o, Aenderung.objekt_id.in_(ids))
                   for o, ids in paare if ids]
    if not bedingungen:
        return []
    return [aenderung_json(a) for a in db.session.scalars(
        db.select(Aenderung).where(db.or_(*bedingungen)).order_by(Aenderung.id.desc()))]


def verknuepfung_json(v):
    d = v.dokument
    return {"id": v.id, "teil_id": v.teil_id, "hersteller": v.teil.hersteller.name,
            "artikelnr": v.teil.artikelnr, "doc_type": v.doc_type,
            "doc_type_text": wort(begriffe.DOC_TYPE, v.doc_type), "version": v.version,
            "aktuell": v.abgeloest_am is None, "stand": v.stand,
            "stand_text": wort(begriffe.VERSION, v.stand), "gueltig_ab": _iso(v.gueltig_ab),
            "abgeloest_am": _iso(v.abgeloest_am), "abgeloest_durch_id": v.abgeloest_durch_id,
            "beendet_von": v.beendet_von, "beendet_grund": v.beendet_grund,
            "von": wort(begriffe.VON, v.von), "quelle": v.quelle,
            "quelle_text": wort(begriffe.VERSION_QUELLE, v.quelle), "beleg": v.beleg,
            "hinweis": v.hinweis, "quellennachweis": quellen.version_nachweis(v),
            "dokument_id": d.id, "dateiname": d.dateiname, "ablage": d.ablage,
            "ablage_text": wort(begriffe.ABLAGE, d.ablage),
            "ausstellungsdatum": _iso(d.ausstellungsdatum),
            "markierungsstand": d.markierungsstand,
            "markierungsstand_text": wort(begriffe.MARKIERUNGSSTAND, d.markierungsstand),
            "dateiart": d.format.endung, "pdf_url": d.pdf_url, "datei_url": d.datei_url}


def teil_json(t, treffer=None, protokoll=False):
    alle = sorted(t.verknuepfungen, key=lambda v: (v.doc_type, -v.version, v.id))
    extra = ({"aenderungen": aenderungen(("teil", [t.id]),
                                         ("verknuepfung", [v.id for v in alle]))}
             if protokoll else {})
    return {**extra, "id": t.id, "hersteller": t.hersteller.name, "hersteller_id": t.hersteller_id,
            "artikelnr": t.artikelnr, "profile_no": t.profile_no, "bezeichnung": t.bezeichnung,
            "angelegt_von": t.angelegt_von, "angelegt_am": _iso(t.angelegt_am),
            **({"treffer": treffer} if treffer else {}),
            "aktuell": [verknuepfung_json(v) for v in alle if v.abgeloest_am is None],
            "historie": [verknuepfung_json(v) for v in alle]}


def dokument_kurz(d):
    offen = [m for m in d.markierungen if m.status != "verworfen"]
    fmt = d.format
    return {"id": d.id, "dateiname": d.dateiname, "hersteller": d.hersteller.name,
            "hersteller_id": d.hersteller_id, "doc_type": d.doc_type,
            "doc_type_text": wort(begriffe.DOC_TYPE, d.doc_type), "ablage": d.ablage,
            "ablage_text": begriffe.AUSGEMUSTERT if d.ausgemustert_am
            else wort(begriffe.ABLAGE, d.ablage),
            "herkunft": d.herkunft, "lesbar": d.lesbar, "seiten": d.seiten,
            # Dateiformat: nur aus PDFs liest das Archiv Angaben, nur dort laufen Prüfregeln
            "dateiart": fmt.endung, "dateiart_text": fmt.name, "familie": fmt.familie,
            "mimetype": fmt.mimetype, "im_browser": fmt.im_browser,
            "auslesbar": fmt.familie == "pdf",
            "auslesbar_grund": dateiformate.nicht_auslesbar_grund(fmt),
            "groesse": d.groesse,
            "ausstellungsdatum": _iso(d.ausstellungsdatum), "gueltig_bis": _iso(d.gueltig_bis),
            "sha256": d.sha256, "markierungsstand": d.markierungsstand,
            "markierungsstand_text": wort(begriffe.MARKIERUNGSSTAND, d.markierungsstand),
            "quelle_art": d.quelle_art, "quelle_art_text": wort(begriffe.QUELLE_ART, d.quelle_art),
            "regeln": sorted({m.regel or "manuell" for m in offen}),
            "gruende": [m.grund + (f" (Seite {m.seite})" if m.seite and "Seite" not in m.grund
                                   else "") for m in offen if m.grund],
            # neutrale Hinweise ohne Verdacht, z. B. Bescheinigung nach 94/9/EG gilt weiter
            "hinweise": regeln.hinweise(d),
            "teile": [{"teil_id": v.teil_id, "artikelnr": v.teil.artikelnr,
                       "doc_type": v.doc_type, "version": v.version,
                       "aktuell": v.abgeloest_am is None} for v in d.verknuepfungen],
            "ausgemustert": bool(d.ausgemustert_am), "ausgemustert_am": _iso(d.ausgemustert_am),
            "ausgemustert_von": d.ausgemustert_von, "ausgemustert_grund": d.ausgemustert_grund,
            "pdf_url": d.pdf_url, "datei_url": d.datei_url, "angelegt_am": _iso(d.angelegt_am),
            # Texterkennung: per_ocr = Text (teilweise) per OCR gelesen, Werte können falsch sein
            "per_ocr": d.per_ocr, "ocr_seiten": d.ocr_seiten or [], "ocr_stand": d.ocr_stand,
            "ocr_stand_text": wort(begriffe.OCR_STAND, d.ocr_stand),
            "ocr_hinweis": d.ocr_hinweis}


def dokument_voll(d):
    teile = {v.teil_id: v.teil for v in d.verknuepfungen}
    return {**dokument_kurz(d), "sha256": d.sha256, "groesse": d.groesse,
            "seiten_ohne_text": d.seiten_ohne_text, "altpfade": d.altpfade,
            "angelegt_von": wort(begriffe.VON, d.angelegt_von),
            "geprueft_am": _iso(d.geprueft_am),
            "quellennachweis": quellen.nachweis(d),
            "angaben": [{"art": a.art, "art_text": wort(begriffe.ANGABE, a.art), "wert": a.wert,
                         "wert_text": wort(begriffe.DOC_TYPE, a.wert) if a.art == "dokumentart"
                         else a.wert, "seite": a.seite,
                         "textstelle": a.textstelle, "sicher": a.sicher, "hinweis": a.hinweis,
                         "quelle": a.quelle, "quelle_text": wort(begriffe.ANGABE_QUELLE, a.quelle)}
                        for a in d.angaben],
            "werte": bearbeiten.dokument_werte(d),
            "markierungen": [markierung_json(m) for m in d.markierungen],
            "teile_historie": [teil_json(t) for t in teile.values()],
            "aenderungen": aenderungen(("dokument", [d.id]),
                                       ("verknuepfung", [v.id for v in d.verknuepfungen]))}


# ---------- Allgemein ----------

@bp.get("/health")
def health():
    db.session.execute(text("SELECT 1"))
    return {"ok": True}


@bp.get("/api/hersteller")
def hersteller_liste():
    alle = db.session.scalars(db.select(Hersteller).order_by(Hersteller.name)).all()
    zahl = dict(db.session.execute(db.select(Dokument.hersteller_id, db.func.count(Dokument.id))
                                   .group_by(Dokument.hersteller_id)).all())
    return [dict(hersteller_json(h), dokumente=zahl.get(h.id, 0)) for h in alle]


@bp.get("/api/begriffe")
def begriffe_liste():
    """Alle Texte für Anwender je technischem Wert, Regeln mit ihren Dokumentarten, erlaubte
    Dateiformate. So zeigt jede Oberfläche dieselben Wörter."""
    return begriffe.alle()


@bp.get("/api/importe")
def importe():
    laeufe = db.session.scalars(db.select(Importlauf).order_by(Importlauf.id.desc())).all()
    return [{"id": x.id, "quelle": x.quelle, "gestartet": _iso(x.gestartet),
             "beendet": _iso(x.beendet), "zahlen": x.zahlen, "hinweise": x.hinweise}
            for x in laeufe]


# ---------- Teile und Versionen ----------

@bp.get("/api/teile")
def teile_suchen():
    """Suche nach ?hersteller=…&artikelnr=…&profile_no=…; ohne Angaben alle Teile.
    Artikelnr.: erst genau (ohne Leer- und Satzzeichen), sonst teilweise (eine enthält die andere)."""
    abfrage = db.select(Teil).order_by(Teil.id)
    if hersteller := request.args.get("hersteller", "").strip():
        ids = [h.id for h in ablage.hersteller_suchen(hersteller)]
        abfrage = abfrage.where(Teil.hersteller_id.in_(ids))
    if profile_no := request.args.get("profile_no", "").strip():
        abfrage = abfrage.where(Teil.profile_no == profile_no)
    teile = db.session.scalars(abfrage).all()
    artikelnr = request.args.get("artikelnr", "").strip()
    if not artikelnr:
        return [teil_json(t) for t in teile]
    n = auslesen.norm(artikelnr)
    genau = [t for t in teile if t.artikelnr_norm == n]
    if genau:
        return [teil_json(t, "genau") for t in genau]
    teilweise = [t for t in teile if len(t.artikelnr_norm) >= 3 and len(n) >= 3
                 and (t.artikelnr_norm in n or n in t.artikelnr_norm)]
    teilweise.sort(key=lambda t: -len(t.artikelnr_norm))
    return [teil_json(t, "teilweise") for t in teilweise]


@bp.post("/api/teile")
def teil_anlegen():
    """{"hersteller", "artikelnr", "von", "profile_no"?, "bezeichnung"?}"""
    daten = _daten()
    von, name, artikelnr = _name(daten), _name(daten, "hersteller"), _name(daten, "artikelnr")
    if not von:
        return _fehler(NAME_FEHLT)
    if not (name and artikelnr):
        return _fehler("Bitte geben Sie Hersteller und Hersteller-Artikelnummer an.")
    gefunden = ablage.hersteller_suchen(name)
    hersteller = gefunden[0] if len(gefunden) == 1 else ablage.hersteller_holen(name)
    try:
        teil, neu = ablage.teil_holen(hersteller, artikelnr, von, _name(daten, "profile_no"),
                                      _name(daten, "bezeichnung"))
    except ablage.AblageFehler as e:
        return _fehler(str(e))
    if not neu:
        db.session.rollback()
        return {"error": "Dieses Teil gibt es schon. Bitte öffnen Sie das vorhandene Teil.",
                "teil_id": teil.id}, 409
    ablage.protokoll("teil", teil.id, "angelegt", None,
                     f"{hersteller.name} {teil.artikelnr}", von, _name(daten, "grund"))
    db.session.commit()
    return teil_json(teil, protokoll=True), 201


@bp.get("/api/teile/<int:teil_id>")
def teil_detail(teil_id):
    """Teil mit aktueller Version je Dokumentart, allen Versionen und dem Änderungsprotokoll."""
    return teil_json(db.session.get(Teil, teil_id) or abort(404), protokoll=True)


@bp.route("/api/teile/<int:teil_id>", methods=["PATCH", "PUT"])
def teil_aendern(teil_id):
    """{"von", "grund"?, "hersteller"?, "artikelnr"?, "profile_no"?, "bezeichnung"?}"""
    teil = db.session.get(Teil, teil_id) or abort(404)
    daten = _daten()
    von = _name(daten)
    if not von:
        return _fehler(NAME_FEHLT)
    try:
        geaendert = bearbeiten.aendere_teil(teil, daten, von, _name(daten, "grund"))
    except bearbeiten.Eingabefehler as e:
        db.session.rollback()
        return _fehler(str(e))
    except ablage.AblageFehler as e:
        db.session.rollback()
        return _fehler(str(e), 409)
    db.session.commit()
    return dict(teil_json(teil, protokoll=True), geaendert=[f for f, _, _ in geaendert])


def _art_setzen(dok, doc_type, von):
    """Dokumentart beim Hochladen oder Zuordnen gewählt: Steht sie am Dokument noch offen
    („unbekannt“, z. B. Word-Datei oder Scan), gilt die Wahl auch für das Dokument."""
    if dok.doc_type != "unbekannt" or doc_type == "unbekannt":
        return
    dok.angaben[:] = [a for a in dok.angaben if a.art != "dokumentart"]
    dok.angaben.append(Angabe(art="dokumentart", wert=doc_type, seite=None, sicher=True,
                              hinweis=f"beim Zuordnen gewählt von {von}", quelle="manuell"))
    ablage.protokoll("dokument", dok.id, "doc_type", "unbekannt", doc_type, von,
                     "Beim Zuordnen gewählt.")
    dok.doc_type = doc_type


def _neue_version(teil, dok, doc_type, von, quelle, hinweis, nachweis=None):
    """Gemeinsamer Teil von Upload und Verknüpfung: prüfen, verknüpfen, Antwort bauen.
    nachweis: Quellennachweis dieser Version, gespeichert nur, wo er vom Dokument abweicht."""
    doc_type = doc_type or dok.doc_type
    if doc_type not in DOC_TYPES or doc_type == "unbekannt":
        raise ablage.AblageFehler(ART_FEHLT)
    _art_setzen(dok, doc_type, von)
    vorher = [v.id for v in ablage.aktuelle(teil, doc_type)]
    abweichend = quellen.unterschied(dok, nachweis or {})
    neu = ablage.verknuepfe(teil, dok, doc_type, von, quelle, hinweis=hinweis,
                            quellennachweis=quellen.fuer_json(abweichend) if abweichend else None)
    return {"verknuepfung": verknuepfung_json(neu), "abgeloest": vorher,
            "dokument": dokument_kurz(dok), "teil": teil_json(teil)}


QUELLE_VERSION = {"upload": "upload", "atex_agent": "atex_agent", "manuell": "manuell"}


def _datei_aus_formular():
    """(Inhalt, Dateiname, Quellennachweis, von) aus multipart; Fehlerantwort als Ausnahme."""
    form, datei = request.form, request.files.get("datei")
    von = _name(form)  # angemeldet: X-Anwender von nginx, sonst Feld „von“
    if not von:
        raise _Antwort(_fehler(NAME_FEHLT))
    if datei is None:
        raise _Antwort(_fehler(DATEI_FEHLT))
    try:
        nachweis = quellen.pruefe(form, "quelle_")
    except quellen.Eingabefehler as e:
        raise _Antwort(_fehler(str(e))) from None
    name = auslesen.dateiname(datei.filename or "")
    # älteres Backend: Herkunft nur im Hinweis-Satz; daraus, was sicher drinsteht
    if "art" not in nachweis:
        nachweis.update(quellen.aus_altem_hinweis(form.get("hinweis")) or {})
    # ohne eigene Angabe: hochgeladen von dieser Person, ursprünglicher Dateiname
    nachweis.setdefault("art", "hochgeladen")
    nachweis.setdefault("dateiname", name or None)
    nachweis.setdefault("von", von)
    return datei.read(), name, nachweis, von


class _Antwort(Exception):
    def __init__(self, antwort):
        super().__init__()
        self.antwort = antwort


def _anlegen_oder_fehler(inhalt, hersteller, name, von, nachweis):
    """Dokument anlegen; Formatfehler als 400 mit dem Satz aus dateiformate.py."""
    try:
        return ablage.dokument_anlegen(inhalt, hersteller, name, "upload", von, quelle=nachweis)
    except ablage.AblageFehler as e:
        db.session.rollback()
        raise _Antwort(_fehler(str(e))) from None


@bp.post("/api/teile/<int:teil_id>/dokumente")
def version_hochladen(teil_id):
    """Neue Version hochladen. multipart: datei (PDF oder ein Format aus dateiformate.py), von,
    doc_type (bei PDF sonst erkannt, bei anderen Dateien Pflicht), hinweis, quelle (Weg der
    Version: upload, atex_agent), Quellennachweis quelle_art, quelle_link, quelle_seite,
    quelle_abgerufen_am, quelle_dateiname, quelle_hinweis."""
    teil = db.session.get(Teil, teil_id) or abort(404)
    form = request.form
    try:
        inhalt, name, nachweis, von = _datei_aus_formular()
        dok, _, dok_neu = _anlegen_oder_fehler(inhalt, teil.hersteller, name, von, nachweis)
    except _Antwort as a:
        return a.antwort
    hinweis = form.get("hinweis") or None
    if hinweis and "quelle_art" in form and hinweis.startswith(quellen.PUSH_ANFANG):
        # das Backend schickt die Herkunft strukturiert und den Satz nur für ältere Archive:
        # an der Version bleibt „Im ATEX-Agent freigegeben von X.“
        hinweis = hinweis.split(";", 1)[0].rstrip(".") + "."
    try:
        antwort = _neue_version(teil, dok, form.get("doc_type"), von,
                                QUELLE_VERSION.get(form.get("quelle"), "upload"),
                                hinweis, nachweis)
    except ablage.AblageFehler as e:
        pfad = ablage.datei(dok)
        db.session.rollback()
        if dok_neu:
            pfad.unlink(missing_ok=True)  # nichts verknüpft, Datei wieder weg
        return _fehler(str(e), 409)
    if dok_neu:
        regeln.pruefe([dok])
    db.session.commit()
    antwort["dokument"] = dokument_kurz(dok)
    return antwort, 201


@bp.post("/api/dokumente")
def dokument_hochladen():
    """Datei ohne Teil ablegen (z. B. Datenblatt, Zeichnung, Archiv), später zuordnen.
    multipart: datei, von, hersteller, doc_type?, Quellennachweis wie beim Hochladen zum Teil.
    Gleiche Datei schon da: 200 mit dem vorhandenen Dokument (bekannt: true)."""
    hersteller_name = (request.form.get("hersteller") or "").strip()
    doc_type = (request.form.get("doc_type") or "").strip()
    try:
        inhalt, name, nachweis, von = _datei_aus_formular()
        if not hersteller_name:
            return _fehler("Bitte geben Sie den Hersteller an.")
        if doc_type and doc_type not in DOC_TYPES:
            return _fehler(ART_FEHLT)
        gefunden = ablage.hersteller_suchen(hersteller_name)
        hersteller = gefunden[0] if len(gefunden) == 1 else ablage.hersteller_holen(hersteller_name)
        dok, _, dok_neu = _anlegen_oder_fehler(inhalt, hersteller, name, von, nachweis)
    except _Antwort as a:
        return a.antwort
    if dok_neu:
        if doc_type:
            _art_setzen(dok, doc_type, von)
        ablage.protokoll("dokument", dok.id, "angelegt", None, dok.dateiname, von,
                         request.form.get("hinweis") or None)
        db.session.flush()
        regeln.pruefe([dok])
    db.session.commit()
    return dict(dokument_voll(dok), bekannt=not dok_neu), 201 if dok_neu else 200


@bp.post("/api/teile/<int:teil_id>/verknuepfungen")
def version_verknuepfen(teil_id):
    """Vorhandenes Dokument als neue Version: {"dokument_id", "von", "doc_type"?, "hinweis"?,
    "quellennachweis"?: {art, link, seite, abgerufen_am, dateiname, hinweis}}."""
    teil = db.session.get(Teil, teil_id) or abort(404)
    daten = _daten()
    von = _name(daten)
    if not von:
        return _fehler(NAME_FEHLT)
    if not isinstance(daten.get("dokument_id"), int):
        return _fehler(LISTE_FEHLT)
    dok = db.session.get(Dokument, daten["dokument_id"]) or abort(404)
    roh = daten.get("quellennachweis")
    try:
        nachweis = quellen.pruefe(roh) if isinstance(roh, dict) else None
        antwort = _neue_version(teil, dok, daten.get("doc_type"), von, "manuell",
                                _name(daten, "hinweis") or None, nachweis)
    except quellen.Eingabefehler as e:
        db.session.rollback()
        return _fehler(str(e))
    except ablage.AblageFehler as e:
        db.session.rollback()
        return _fehler(str(e), 409)
    db.session.commit()
    return antwort, 201


@bp.post("/api/verknuepfungen/<int:v_id>/beenden")
def verknuepfung_beenden(v_id):
    """Version ohne Nachfolger beenden: {"von", "grund"}."""
    v = db.session.get(Verknuepfung, v_id) or abort(404)
    return _aktion(v, ablage.beende, grund_pflicht=True) or verknuepfung_json(v)


@bp.post("/api/verknuepfungen/<int:v_id>/wiederherstellen")
def verknuepfung_wiederherstellen(v_id):
    """Beendete oder abgelöste Version wieder aktuell: {"von", "grund"?}."""
    v = db.session.get(Verknuepfung, v_id) or abort(404)
    return _aktion(v, ablage.verknuepfung_wiederherstellen) or verknuepfung_json(v)


@bp.post("/api/verknuepfungen/<int:v_id>/umhaengen")
def verknuepfung_umhaengen(v_id):
    """Dokument einem anderen Teil zuordnen, dort als neue Version: {"teil_id", "von", "grund"?}."""
    v = db.session.get(Verknuepfung, v_id) or abort(404)
    daten = _daten()
    if not isinstance(daten.get("teil_id"), int):
        return _fehler("Bitte wählen Sie das Teil aus, zu dem das Dokument gehören soll.")
    ziel = db.session.get(Teil, daten["teil_id"]) or abort(404)
    neu = []
    fehler = _aktion(v, lambda v_, von, grund: neu.append(ablage.umhaengen(v_, ziel, von, grund)))
    return fehler or ({"alt": verknuepfung_json(v), "neu": verknuepfung_json(neu[0])}, 201)


@bp.get("/api/aenderungen")
def aenderungen_liste():
    """Änderungsprotokoll, ?objekt=teil|dokument|verknuepfung&objekt_id=…; sonst die letzten 200."""
    abfrage = db.select(Aenderung).order_by(Aenderung.id.desc()).limit(200)
    if request.args.get("objekt"):
        abfrage = abfrage.where(Aenderung.objekt == request.args["objekt"])
    if request.args.get("objekt_id", "").isdigit():
        abfrage = abfrage.where(Aenderung.objekt_id == int(request.args["objekt_id"]))
    return [aenderung_json(a) for a in db.session.scalars(abfrage)]


# ---------- Dokumente ----------

@bp.get("/api/dokumente")
def dokumente():
    """Filter: hersteller, markierung (ohne, verdacht, bestaetigt, verworfen), regel, ablage
    (aktuell, archiv), doc_type, verknuepft, lesbar, ausgemustert, auslesbar (je ja, nein),
    dateiart (Endung, mit Komma), familie (pdf, office, bild, zeichnung, archiv, text),
    quelle_art, sha256 (mit Komma; so fragt das Backend nach den Markierungen seiner Funde)."""
    a = request.args
    abfrage = db.select(Dokument).order_by(Dokument.id)
    if a.get("sha256"):
        abfrage = abfrage.where(Dokument.sha256.in_(a["sha256"].split(",")))
    if a.get("hersteller"):
        abfrage = abfrage.where(Dokument.hersteller_id.in_(
            [h.id for h in ablage.hersteller_suchen(a["hersteller"])]))
    if a.get("doc_type"):
        abfrage = abfrage.where(Dokument.doc_type == a["doc_type"])
    if a.get("lesbar") in ("ja", "nein"):
        abfrage = abfrage.where(Dokument.lesbar == (a["lesbar"] == "ja"))
    liste = db.session.scalars(abfrage).all()
    if a.get("markierung"):
        liste = [d for d in liste if d.markierungsstand == a["markierung"]]
    if a.get("regel"):
        liste = [d for d in liste if any((m.regel or "manuell") == a["regel"]
                                         and m.status != "verworfen" for m in d.markierungen)]
    if a.get("ablage"):
        liste = [d for d in liste if d.ablage == a["ablage"]]
    if a.get("ausgemustert") in ("ja", "nein"):
        liste = [d for d in liste if bool(d.ausgemustert_am) == (a["ausgemustert"] == "ja")]
    if a.get("verknuepft") in ("ja", "nein"):
        liste = [d for d in liste if bool(d.verknuepfungen) == (a["verknuepft"] == "ja")]
    if a.get("dateiart"):
        liste = [d for d in liste if d.format.endung in a["dateiart"].lower().split(",")]
    if a.get("familie"):
        liste = [d for d in liste if d.format.familie == a["familie"]]
    if a.get("auslesbar") in ("ja", "nein"):
        liste = [d for d in liste if d.ist_pdf == (a["auslesbar"] == "ja")]
    if a.get("quelle_art"):
        liste = [d for d in liste if d.quelle_art == a["quelle_art"]]
    return [dokument_kurz(d) for d in liste]


@bp.get("/api/dokumente/<int:dok_id>")
def dokument(dok_id):
    return dokument_voll(db.session.get(Dokument, dok_id) or abort(404))


@bp.patch("/api/dokumente/<int:dok_id>")
def dokument_aendern(dok_id):
    """Angaben korrigieren: {"von", "grund"?, "doc_type"?, "ausstellungsdatum"?, "gueltig_bis"?,
    "zertifikatsnummern"?, "sprachen"?, "richtlinien"?, "normen"?, "seiten"?: {Feld: Seite}},
    dazu der Quellennachweis: quelle_art, quelle_link, quelle_seite, quelle_abgerufen_am,
    quelle_von, quelle_dateiname, quelle_hinweis. Danach rechnen die Regeln neu (nur PDF)."""
    dok = db.session.get(Dokument, dok_id) or abort(404)
    daten = _daten()
    von = _name(daten)
    if not von:
        return _fehler(NAME_FEHLT)
    try:
        geaendert = bearbeiten.aendere_dokument(dok, daten, von, _name(daten, "grund"))
    except bearbeiten.Eingabefehler as e:
        db.session.rollback()
        return _fehler(str(e))
    db.session.commit()
    return dict(dokument_voll(dok), geaendert=[f for f, _, _ in geaendert])


def _aktion(objekt, aufruf, grund_pflicht=False):
    """Gemeinsamer Ablauf für Ausmustern, Wiederherstellen, Beenden, Umhängen: Name prüfen,
    aufrufen, fachliche Fehler als 409. Gibt None zurück oder die Fehlerantwort."""
    daten = _daten()
    von, grund = _name(daten), _name(daten, "grund")
    if not von:
        return _fehler(NAME_FEHLT)
    if grund_pflicht and not grund:
        return _fehler(GRUND_FEHLT)
    try:
        aufruf(objekt, von, grund or None)
    except ablage.AblageFehler as e:
        db.session.rollback()
        return _fehler(str(e), 409)
    db.session.commit()
    return None


@bp.post("/api/dokumente/<int:dok_id>/ausmustern")
def dokument_ausmustern(dok_id):
    """„Löschen“ ohne Verlust: {"von", "grund"}. Datei ins Archiv, Link bleibt."""
    dok = db.session.get(Dokument, dok_id) or abort(404)
    return _aktion(dok, ablage.ausmustern, grund_pflicht=True) or dokument_voll(dok)


@bp.post("/api/dokumente/<int:dok_id>/wiederherstellen")
def dokument_wiederherstellen(dok_id):
    """Ausmustern rückgängig: {"von", "grund"?}."""
    dok = db.session.get(Dokument, dok_id) or abort(404)
    return _aktion(dok, ablage.dokument_wiederherstellen) or dokument_voll(dok)


def datei_antwort(dok):
    """Die Datei mit passendem Typ. PDFs und gängige Bilder zeigt der Browser an (?download=1
    zum Speichern), alles andere kommt nur als Download: keine HTML- oder SVG-Darstellung aus
    gespeicherten Dateien. nosniff: der Browser rät keinen anderen Typ."""
    pfad = ablage.datei(dok)
    if not pfad.is_file():
        current_app.logger.error("Datei fehlt in der Ablage: %s (Dokument %s)", dok.pfad, dok.id)
        return _fehler("Die Datei fehlt im Dokumentenarchiv. Bitte melden Sie das der "
                       "Person, die das Archiv betreut.", 404)
    fmt = dok.format
    name = dok.dateiname
    if not name.lower().endswith("." + fmt.endung):
        name += "." + fmt.endung
    antwort = send_file(pfad, mimetype=fmt.mimetype, download_name=name,
                        as_attachment=not fmt.im_browser or request.args.get("download") == "1")
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    return antwort


@bp.get("/api/dokumente/<int:dok_id>/datei")
@bp.get("/api/dokumente/<int:dok_id>/pdf")
def dokument_datei(dok_id):
    """Die Datei, stabil auch im Archiv. …/pdf ist der ältere Name desselben Links."""
    return datei_antwort(db.session.get(Dokument, dok_id) or abort(404))


@bp.get("/api/dateien/<sha256>")
def datei_nach_hash(sha256):
    """Die Datei zu ihrem SHA-256: ein stabiler Link, den Backend und Kundenliste ohne
    Nachfrage bilden können."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        abort(404)
    return datei_antwort(db.session.scalar(db.select(Dokument).filter_by(sha256=sha256))
                         or abort(404))


# ---------- Markierungen ----------

def _dokumente_oder_404(ids):
    gefunden = {d.id: d for d in db.session.scalars(db.select(Dokument).where(Dokument.id.in_(ids)))}
    fehlen = [i for i in ids if i not in gefunden]
    if fehlen:
        abort(404)
    return [gefunden[i] for i in ids]


@bp.post("/api/markierungen")
def markieren():
    """Von Hand als veraltet markieren, für eine Auswahl:
    {"dokument_ids": [..], "von", "grund", "bestaetigen"?: true = gleich „neue Suche nötig“}."""
    daten = _daten()
    ids, von, grund = _ids(daten, "dokument_ids"), _name(daten), _name(daten, "grund")
    if not von:
        return _fehler(NAME_FEHLT)
    if not ids:
        return _fehler(LISTE_FEHLT)
    if not grund:
        return _fehler(GRUND_FEHLT)
    bestaetigen = daten.get("bestaetigen") is True
    neu = []
    for dok in _dokumente_oder_404(ids):
        m = Markierung(regel=None, stufe="veraltet", grund=grund, erstellt_von=von,
                       status="bestaetigt" if bestaetigen else "verdacht")
        if bestaetigen:
            m.entschieden_von, m.entschieden_am, m.entscheidung_grund = von, jetzt(), grund
        dok.markierungen.append(m)
        neu.append(m)
    db.session.commit()
    return {"markierungen": [markierung_json(m) for m in neu]}, 201


def _entscheiden(ziel):
    """Markierungen auf „bestaetigt“ oder „verworfen“ setzen. Je Dokument: Bestätigen nimmt jeden
    offenen Verdacht, Verwerfen auch bestätigte. Je Markierung: genau diese. Verwerfen braucht
    einen Grund."""
    daten = _daten()
    von, grund = _name(daten), _name(daten, "grund")
    dok_ids, m_ids = _ids(daten, "dokument_ids"), _ids(daten, "markierung_ids")
    if not von:
        return _fehler(NAME_FEHLT)
    if not (dok_ids or m_ids):
        return _fehler(LISTE_FEHLT)
    if ziel == "verworfen" and not grund:
        return _fehler(GRUND_FEHLT)
    markierungen, ohne = [], []
    for dok in _dokumente_oder_404(dok_ids or []):
        offen = [m for m in dok.markierungen
                 if m.status == "verdacht" or (ziel == "verworfen" and m.status == "bestaetigt")]
        markierungen += offen
        if not offen:
            ohne.append(dok.id)
    for m_id in m_ids or []:
        m = db.session.get(Markierung, m_id) or abort(404)
        if m.status != ziel and m not in markierungen:
            markierungen.append(m)
    for m in markierungen:
        m.status, m.entschieden_von, m.entschieden_am = ziel, von, jetzt()
        m.entscheidung_grund = grund or None
    db.session.commit()
    return {"geaendert": len(markierungen), "ohne_markierung": ohne,
            "markierungen": [markierung_json(m) for m in markierungen]}


@bp.post("/api/markierungen/bestaetigen")
def bestaetigen():
    """Verdacht bestätigen: das Dokument braucht eine neue Suche (GET /api/neue-suche)."""
    return _entscheiden("bestaetigt")


@bp.post("/api/markierungen/verwerfen")
def verwerfen():
    """Verdacht verwerfen: das Dokument gilt weiter, mit Name und Grund."""
    return _entscheiden("verworfen")


@bp.get("/api/neue-suche")
def neue_suche():
    """Dokumente mit bestätigter Markierung, die noch aktuell sind: dafür ist eine neue Suche
    nötig. Erledigt, sobald eine neue Version das Dokument ablöst (es geht dann ins Archiv)."""
    liste = db.session.scalars(db.select(Dokument).order_by(Dokument.id)).all()
    ergebnis = []
    for d in liste:
        bestaetigt = [m for m in d.markierungen if m.status == "bestaetigt"]
        if not bestaetigt or d.ablage != "aktuell":
            continue
        ergebnis.append({
            "dokument": dokument_kurz(d),
            "gruende": [markierung_json(m) for m in bestaetigt],
            "teile": [{"teil_id": v.teil_id, "hersteller": v.teil.hersteller.name,
                       "artikelnr": v.teil.artikelnr, "profile_no": v.teil.profile_no,
                       "doc_type": v.doc_type, "version": v.version}
                      for v in d.verknuepfungen if v.abgeloest_am is None]})
    return ergebnis


# ---------- Regeln und Normen ----------

@bp.get("/api/regeln")
def regeln_liste():
    return {"regeln": regeln.REGELN, "stufen": begriffe.STUFE,
            "gilt_fuer": {r: list(arten) for r, arten in regeln.GILT_FUER.items()},
            "gilt_fuer_text": {r: [wort(begriffe.DOC_TYPE, x) for x in arten]
                               for r, arten in regeln.GILT_FUER.items()},
            "staende": list(STAENDE), "staende_text": begriffe.MARKIERUNGSSTAND,
            "abloesung_94_9": regeln.ABLOESUNG_94_9,
            "veraltet_nach_jahren": current_app.config["VERALTET_NACH_JAHREN"],
            "normen": _normen_liste()}


@bp.post("/api/regeln/pruefen")
def regeln_pruefen():
    """Regeln neu rechnen: {"dokument_ids"?: [..], "von"?}, ohne Liste für alle Dokumente.
    Steht mit Name (ohne: „nicht angegeben“) und Ergebnis im Änderungsprotokoll."""
    daten = _daten()
    ids = _ids(daten, "dokument_ids")
    if ids is None:
        return _fehler(LISTE_FEHLT)
    zahlen = regeln.pruefe(_dokumente_oder_404(ids)) if ids else regeln.pruefe_alle()
    ablage.protokoll("regeln", 0, "pruefen", {"dokument_ids": ids or "alle"},
                     {"neu": zahlen["neu"], "entfallen": zahlen["entfallen"]},
                     _name(daten) or "nicht angegeben")
    db.session.commit()
    return zahlen


def _norm_json(n):
    return {"norm": n.norm, "jahr": n.jahr, "hinweis": n.hinweis,
            "geaendert_von": n.geaendert_von, "geaendert_am": _iso(n.geaendert_am),
            "quelle": n.quelle, "zu_bestaetigen": n.zu_bestaetigen,
            "bestaetigt_von": n.bestaetigt_von, "bestaetigt_am": _iso(n.bestaetigt_am),
            "pruefhinweis": normtabelle.VORGABE_HINWEIS if n.zu_bestaetigen else None}


def _normen_liste():
    """Nach Teilnummer sortiert: 60079-7 vor 60079-11."""
    return [_norm_json(n) for n in sorted(db.session.scalars(db.select(NormAusgabe)),
                                          key=lambda n: regeln.normzahl(n.norm))]


@bp.get("/api/normen")
def normen():
    return _normen_liste()


@bp.put("/api/normen")
def normen_setzen():
    """Tabelle der aktuellen Ausgaben auf die Liste bringen und alle Regeln neu rechnen:
    {"normen": [{"norm": "60079-0", "jahr": 2017, "hinweis"?}], "von"}. Fehlende Zeilen fallen
    weg; Name und Zeit bekommen nur Zeilen, die sich ändern (sie gelten damit als bestätigt).
    „EN IEC 60079-0“ wird zu „60079-0“; jahr ist das Jahr der IEC-Ausgabe."""
    daten = _daten()
    von = _name(daten)
    if not von:
        return _fehler(NAME_FEHLT)
    try:
        geaendert = normtabelle.tabelle_setzen(daten.get("normen"), von)
    except normtabelle.Eingabefehler as e:
        db.session.rollback()
        return _fehler(str(e))
    zahlen = regeln.pruefe_alle()
    db.session.commit()
    return {"normen": _normen_liste(), "pruefung": zahlen, "geaendert": geaendert}


@bp.post("/api/normen/bestaetigen")
def normen_bestaetigen():
    """Vorgaben unverändert bestätigen: {"normen": ["60079-0", …], "von"}. Rechnet die Regeln
    neu (der Grund nennt dann keine unbestätigte Vorgabe mehr)."""
    daten = _daten()
    von = _name(daten)
    if not von:
        return _fehler(NAME_FEHLT)
    try:
        bestaetigt = normtabelle.bestaetigen(daten.get("normen"), von)
    except normtabelle.Eingabefehler as e:
        db.session.rollback()
        return _fehler(str(e))
    zahlen = regeln.pruefe_alle() if bestaetigt else None
    db.session.commit()
    return {"normen": _normen_liste(), "bestaetigt": bestaetigt, "pruefung": zahlen}
