"""Anschluss an den Zertifikatsserver (zertifikatsserver/, für Anwender „Dokumentenarchiv“):
Markierungen „veraltet“ je Datei, Ablage freigegebener Dokumente mit Quellennachweis, Links.

Der Server kennt den Altbestand und markiert Zertifikate, die womöglich veraltet sind. Gefragt
wird über den SHA-256 der Datei, denselben Schlüssel, den Funde und Stammdaten tragen:

- „bestaetigt“: Ein Mensch hat bestätigt, dass das Dokument veraltet ist. Die Ordnersuche schlägt
  es nicht mehr vor und es zählt nicht als Fund, damit die Suche weitergeht (Internet, sonst
  E-Mail an den Hersteller).
- „verdacht“: Der Vorschlag bleibt, die Vorprüfung nennt den Grund und ist nie „ok“.

Ohne ZERTIFIKATSSERVER_URL oder wenn der Server nicht antwortet, läuft die Suche wie ohne ihn;
„gesucht in“ sagt das.

Eine Ablage für PDFs (unten, „Ablage“): Gibt ein Mensch einen Vorschlag frei, dessen Datei der
Server nicht kennt, bekommt der Server sie als neue Version des Teils (nach_freigabe, abgleich).
Umgekehrt holt ordner die aktuellen PDFs, die nur der Server hat, in einen Zwischenspeicher unter
ATEX_DOCS, damit die Ordnersuche sie findet (andere Dateiarten des Archivs nicht).

Quellennachweis (unten): Bei der Übertragung gehen Art, ursprünglicher Download-Link, Abrufzeit,
ursprünglicher Dateiname und wer freigegeben hat als eigene Felder mit (quelle_*). Die Webseite,
auf der die Websuche den Link fand, hält die Websuche (internet.py) nicht fest; sie bleibt
unbekannt. quellennachweis(finding) liefert denselben Block für die Prüfseite, download_links die
Zelle „DL-Link“ der Kundenliste.
"""

import hashlib
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit

import httpx
from flask import after_this_request, current_app, has_request_context

from ..db import db
from ..models import Document, Finding, Part, PartDocument
from ..models.bom import BomEvent
from . import freigabe, pdf_text, stammdaten, status
from .erledigt import Fehler, von

WARTEZEIT = 10  # Sekunden je Anfrage
NAME = "Dokumentenarchiv"  # so heißt der Zertifikatsserver in Texten für Anwender
NICHT_ERREICHBAR = "Das Dokumentenarchiv war nicht erreichbar."


class NichtErreichbar(Exception):
    """Der Zertifikatsserver ist eingestellt, antwortet aber nicht brauchbar. Der Text ist ein
    Satz für Anwender; Einzelheiten stehen im Log."""


def _url():
    return (current_app.config.get("ZERTIFIKATSSERVER_URL") or "").rstrip("/")


def veraltet_an():
    """Zählen die Markierungen „veraltet“ des Archivs (VERALTET_PRUEFUNG)? Standard aus."""
    return bool(current_app.config.get("VERALTET_PRUEFUNG"))


def an():
    """Ist ein Dokumentenarchiv eingestellt? Dann sucht die lokale Stufe nur dort (auszug)."""
    return bool(_url())


def _holen(pfad, **params):
    try:
        r = httpx.get(_url() + pfad, params=params, timeout=WARTEZEIT)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        current_app.logger.warning("Zertifikatsserver GET %s: %r", pfad, e)
        raise NichtErreichbar(NICHT_ERREICHBAR) from e


def markierungen(hashes):
    """{sha256: Dokument des Servers} für die Hashes, die der Server kennt."""
    hashes = sorted({h for h in hashes if h})
    if not hashes:
        return {}
    return {d["sha256"]: d for d in _holen("/api/dokumente", sha256=",".join(hashes))}


def pruefe(item, neue):
    """Neue Ordner-Funde einer Position gegen die Markierungen des Servers.

    Gibt (behalten, veraltet, vermerk) zurück: die Funde, die Vorschläge werden; die SHA-256 der
    bestätigt veralteten Dokumente unter allen Funden der Position (zählen nicht als Fund);
    einen Satz für „gesucht in“ oder None.
    """
    if not _url() or not veraltet_an():
        return neue, set(), None
    try:
        docs = markierungen([f.file_hash for f in item.findings] + [f.file_hash for f in neue])
    except NichtErreichbar:
        return neue, set(), ("Das Dokumentenarchiv war gerade nicht erreichbar. Deshalb ist nicht "
                             "geprüft, ob ein gefundenes Dokument dort als veraltet markiert ist.")
    veraltet = {h for h, d in docs.items() if d["markierungsstand"] == "bestaetigt"}
    behalten = []
    for f in neue:
        d = docs.get(f.file_hash)
        if f.file_hash in veraltet:
            continue
        if d and d["markierungsstand"] == "verdacht":
            f.check_message = f"{f.check_message or ''} {verdacht_satz(d['gruende'])}".strip()
            if f.check_result == "ok":
                f.check_result = "unklar"
        behalten.append(f)
    n = len(neue) - len(behalten)
    vermerk = ((f"Ein Dokument aus dem Ordner ist im {NAME} als veraltet bestätigt und wird "
                "deshalb nicht vorgeschlagen." if n == 1 else
                f"{n} Dokumente aus dem Ordner sind im {NAME} als veraltet bestätigt und werden "
                "deshalb nicht vorgeschlagen.") if n else None)
    return behalten, veraltet, vermerk


def verdacht_satz(gruende):
    """Satz zur Markierung „verdacht“ für die Prüfmeldung; die Gründe stehen so im Archiv."""
    gruende = [g.strip().rstrip(".") for g in gruende or [] if g and g.strip()]
    satz = f"Im {NAME} ist dieses Dokument als vermutlich veraltet markiert."
    if not gruende:
        return satz + " Einen Grund nennt das Archiv nicht."
    zitiert = [f"„{g}“" for g in gruende]
    if len(zitiert) == 1:
        return f"{satz} Als Grund steht dort {zitiert[0]}."
    return f"{satz} Als Gründe stehen dort {', '.join(zitiert[:-1])} und {zitiert[-1]}."


def _bestaetigt():
    """{sha256: Eintrag aus GET /api/neue-suche} für alle bestätigt veralteten Dokumente."""
    if not _url():
        raise Fehler("Das Dokumentenarchiv ist nicht angeschlossen. Bitte wenden Sie sich an die "
                     "Person, die den ATEX-Agent betreut.", 503)
    try:
        return {e["dokument"]["sha256"]: e for e in _holen("/api/neue-suche")}
    except NichtErreichbar as e:
        raise Fehler(f"{e} Bitte versuchen Sie es in ein paar Minuten noch einmal.", 503) from e


def _hashes(item):
    """SHA-256 der Dokumente, die für die Position noch zählen: offene Vorschläge und
    Stammdaten-Zuordnungen, die nicht (mehr) auf „ungeprueft“ stehen."""
    eigene = {f.file_hash for f in item.findings if f.file_hash and f.status == "vorgeschlagen"}
    if item.profile_no:
        eigene |= set(db.session.scalars(
            db.select(Document.file_hash).join(PartDocument).join(Part)
            .where(Part.profile_no == item.profile_no, Document.file_hash.is_not(None),
                   PartDocument.status != "ungeprueft")))
    return eigene


def betroffene(bom):
    """Positionen des Vorgangs, zu denen ein bestätigt veraltetes Dokument gehört."""
    alt = _bestaetigt()
    ergebnis = []
    for item in bom.items:
        treffer = [alt[h] for h in sorted(_hashes(item) & alt.keys())]
        if treffer and item.status not in ("kein_ex_teil", "erledigt"):
            ergebnis.append((item, treffer))
    return ergebnis


def ansicht(bom):
    return {"items": [dict(item.to_dict(), veraltet=[
        {"dateiname": e["dokument"]["dateiname"],
         "pdf_url": e["dokument"].get("datei_url") or e["dokument"]["pdf_url"],
         "gruende": [m["grund"] for m in e["gruende"]]} for e in treffer])
        for item, treffer in betroffene(bom)]}


def suche_neu(bom, daten, internet_versuche=None):
    """Ein Mensch stößt die neue Suche für die betroffenen Positionen an. {"by", "item_ids"?}

    Je Position: Stammdaten-Zuordnungen zu den veralteten Dateien gehen auf „ungeprueft“ (mit
    Protokoll), unentschiedene Vorschläge dieser Dateien fallen weg, dann sucht die Suche diese
    Positionen neu; dort zählen die veralteten Dateien nicht mehr als Fund.
    """
    from . import suche  # suche importiert dieses Modul
    name = von(daten)
    auswahl = daten.get("item_ids")
    paare = [(i, t) for i, t in betroffene(bom) if auswahl is None or i.id in auswahl]
    if not paare:
        raise Fehler("Keine Position stützt sich auf ein Dokument, das als veraltet bestätigt "
                     "ist. Es gibt nichts neu zu suchen.", 409)
    for item, treffer in paare:
        alt = {e["dokument"]["sha256"] for e in treffer}
        grund = "Im Dokumentenarchiv als veraltet bestätigt: " + ", ".join(
            e["dokument"]["dateiname"] for e in treffer)
        zuordnungen = db.session.scalars(
            db.select(PartDocument).join(Part).join(Document)
            .where(Part.profile_no == item.profile_no, Document.file_hash.in_(alt),
                   PartDocument.status != "ungeprueft")).all() if item.profile_no else []
        for pd in zuordnungen:
            stammdaten.setze_pruefstatus(pd, {"by": name, "status": "ungeprueft", "note": grund})
        for f in [f for f in item.findings if f.file_hash in alt and f.status == "vorgeschlagen"]:
            item.findings.remove(f)
            db.session.delete(f)
        item.searched_at = None
        status.aktualisiere(item)
    items = [i for i, _ in paare]
    db.session.flush()
    # suche_vorgang braucht vom Vorgang nur die Positionen
    suche.suche_vorgang(SimpleNamespace(items=items), internet_versuche)
    return items


# ---------- Ablage: freigegebene Dokumente zum Server, Dokumente nur im Server lesen ----------

ZWISCHENSPEICHER = ("_upload", "zertifikatsserver")  # unter ATEX_DOCS, je Hersteller ein Ordner
ZURUECKGEZOGEN = "_zurueckgezogen"  # dort landen Dateien, die nicht mehr vorgeschlagen werden
NACHHOLEN_JE_FREIGABE = 5  # so viele fehlende Dateien überträgt eine Freigabe nebenbei
HERKUNFT = {"manuell": "manuell hochgeladen", "hersteller": "vom Hersteller",
            "ordner": "Ordner ex_dokumente", "suchdienst": "Suchdienst"}

_sperre = threading.Lock()
_hash_speicher = {}  # (Pfad, Änderungszeit, Größe) -> SHA-256


class Abgelehnt(Exception):
    """Der Server antwortet, lehnt aber ab (4xx/5xx); die Meldung des Servers steht im Text."""


def _anfrage(methode, pfad, **kw):
    try:
        return httpx.request(methode, _url() + pfad, timeout=WARTEZEIT, **kw)
    except httpx.HTTPError as e:
        current_app.logger.warning("Zertifikatsserver %s %s: %r", methode, pfad, e)
        raise NichtErreichbar(NICHT_ERREICHBAR) from e


def _json_oder_none(r):
    try:
        return r.json()
    except ValueError:
        return None


def _antwort(r):
    """JSON der Antwort; Fehler des Servers als Abgelehnt mit dessen Meldung."""
    daten = _json_oder_none(r)
    if r.status_code >= 400:
        meldung = daten.get("error") if isinstance(daten, dict) else None
        current_app.logger.warning("Zertifikatsserver lehnt ab (%s): %s", r.status_code,
                                   meldung or r.reason_phrase)
        raise Abgelehnt("Das Dokumentenarchiv hat die Datei nicht angenommen. "
                        + (meldung or "Einen Grund hat es nicht genannt."))
    if daten is None:
        current_app.logger.warning("Zertifikatsserver: Antwort ist kein JSON (%s)", r.status_code)
        raise NichtErreichbar(NICHT_ERREICHBAR)
    return daten


def _datei_hash(pfad):
    st = pfad.stat()
    schluessel = (str(pfad), st.st_mtime_ns, st.st_size)
    if schluessel not in _hash_speicher:
        h = hashlib.sha256()
        with open(pfad, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _hash_speicher[schluessel] = h.hexdigest()
    return _hash_speicher[schluessel]


def _quelle(finding):
    return finding.url or HERKUNFT.get(finding.source, finding.source)


# ---------- Quellennachweis ----------

# Weg des Fundes im ATEX-Agent -> Art der Herkunft im Archiv (zertifikatsserver/app/begriffe.py)
QUELLE_ART = {"internet": "internet", "hersteller": "hersteller", "manuell": "hochgeladen",
              "ordner": "altbestand", "suchdienst": "atex_agent"}
QUELLE_ART_TEXT = {"internet": "Im Internet gefunden", "hersteller": "Vom Hersteller geschickt",
                   "hochgeladen": "Von Hand hochgeladen", "altbestand": "Übernommener Bestand",
                   "atex_agent": "Bei einer Freigabe im ATEX-Agent übernommen"}


def _webadresse(url):
    return url if (url or "").startswith(("http://", "https://")) else None


def _iso(zeit):
    return zeit.replace(microsecond=0).isoformat() if zeit else None


def _abgerufen(finding, pfad):
    """Wann die Datei kam: Funde aus dem Internet, vom Hersteller und von Hand legt das Backend
    beim Eingang ab, die Änderungszeit der Datei ist dieser Zeitpunkt (UTC). Ordner: None."""
    if finding.source not in ("internet", "hersteller", "manuell") or pfad is None:
        return None
    try:
        return datetime.fromtimestamp(pfad.stat().st_mtime, timezone.utc).replace(tzinfo=None)
    except OSError:
        return None


def _ursprungsname(finding):
    """Ursprünglicher Dateiname: beim Internet der letzte Teil des Links, sonst der Name im
    Ordner (beim Ordner der Pfad relativ zu ATEX_DOCS, wie im Archiv beim Bestand)."""
    link = _webadresse(finding.url)
    if link:
        return unquote(urlsplit(link).path.rsplit("/", 1)[-1]) or None
    if not finding.file_path:
        return None
    return finding.file_path if finding.source == "ordner" else PurePosixPath(finding.file_path).name


def quelle_felder(finding, pfad=None):
    """Quellennachweis eines Fundes als Formularfelder für POST /api/teile/<id>/dokumente."""
    felder = {"quelle_art": QUELLE_ART.get(finding.source, "atex_agent"),
              "quelle_link": _webadresse(finding.url),
              "quelle_abgerufen_am": _iso(_abgerufen(finding, pfad)),
              "quelle_dateiname": _ursprungsname(finding)}
    return {k: v for k, v in felder.items() if v}


def oeffentlich(pfad_im_archiv):
    """Link auf das Archiv, den auch erreicht, wer die Excel-Datei öffnet: OEFFENTLICHE_ADRESSE
    (Adresse der Oberfläche) + /zert + Pfad."""
    basis = (current_app.config.get("OEFFENTLICHE_ADRESSE") or "").rstrip("/")
    return f"{basis}/zert{pfad_im_archiv}"


def quellennachweis(finding):
    """Herkunft eines Fundes für die Prüfseite (oberflaeche.fund): art, link (ursprünglicher
    Download-Link), seite_gefunden_auf (die Websuche hält sie nicht fest: None, seite_bekannt
    False), abgerufen_am, datei, archiv_link (Link auf die Datei im Archiv über ihren SHA-256,
    relativ zur Oberfläche; ob das Archiv sie hat, prüft die Oberfläche selbst), text."""
    pfad = freigabe.datei(finding, current_app.config["ATEX_DOCS"])
    art = QUELLE_ART.get(finding.source, "atex_agent")
    link = _webadresse(finding.url)
    # Satz zur Art (art_text): was dazu bekannt ist und was nicht; None, wenn nichts zu sagen ist
    if art == "internet":
        text = ("Die Suche hält nicht fest, auf welcher Webseite der Link stand." if link
                else "Der Download-Link ist nicht bekannt.")
    elif art == "altbestand":
        text = "Die Datei lag im Ordner mit den vorhandenen Dokumenten."
    else:
        text = None
    return {"art": art, "art_text": QUELLE_ART_TEXT[art], "link": link,
            "seite_gefunden_auf": None, "seite_bekannt": False if art == "internet" else None,
            "abgerufen_am": _iso(_abgerufen(finding, pfad)),
            "datei": pdf_text.lesbar(_ursprungsname(finding)), "sha256": finding.file_hash,
            "archiv_link": (f"/zert/api/dateien/{finding.file_hash}"
                            if _url() and finding.file_hash else None),
            "text": text}


# ---------- Kundenliste: Download-Links ----------

_link_speicher = {}  # sha256 -> (Zeit, Pfad der Datei im Archiv oder None)
_gesperrt_bis = [0.0]  # nach einem Fehler fragt die Ausgabe eine Minute lang nicht nach
SPEICHERZEIT = 60


def _archiv_pfade(hashes):
    """{sha256: Pfad der Datei im Archiv} für die Hashes, die das Archiv kennt. Ohne Archiv, wenn
    es nicht antwortet oder kürzlich nicht antwortete: {} (kein Link statt eines falschen)."""
    jetzt = time.monotonic()
    offen = [h for h in hashes if h and (h not in _link_speicher
                                         or jetzt - _link_speicher[h][0] > SPEICHERZEIT)]
    if offen and _url() and jetzt >= _gesperrt_bis[0]:
        try:
            for i in range(0, len(offen), 40):
                teil = offen[i:i + 40]
                gefunden = markierungen(teil)
                for h in teil:
                    d = gefunden.get(h)
                    _link_speicher[h] = (jetzt, (d.get("datei_url") or d.get("pdf_url"))
                                         if d and not d.get("ausgemustert") else None)
        except NichtErreichbar:
            _gesperrt_bis[0] = jetzt + SPEICHERZEIT
            current_app.logger.warning("Kundenliste: Dokumentenarchiv nicht erreichbar, "
                                       "DL-Link nur mit Links der Hersteller.")
    return {h: _link_speicher[h][1] for h in hashes
            if h in _link_speicher and _link_speicher[h][1]}


def download_links(dokumente):
    """Zelle „DL-Link“ der Kundenliste: je Dokument (in der gegebenen Reihenfolge, doppelte
    einmal) ein Link, eine Zeile je Link. Der ursprüngliche Download-Link des Herstellers, wo er
    bekannt ist (Document.url); sonst der stabile Link im Dokumentenarchiv (OEFFENTLICHE_ADRESSE),
    wenn das Archiv die Datei hat; sonst kein Link. Leer: None."""
    dokumente = list(dict.fromkeys(dokumente))
    pfade = _archiv_pfade([d.file_hash for d in dokumente if not _webadresse(d.url)])
    links = []
    for d in dokumente:
        link = _webadresse(d.url) or (oeffentlich(quote(pfade[d.file_hash], safe="/"))
                                      if d.file_hash in pfade else None)
        if link and link not in links:
            links.append(link)
    return "\n".join(links) or None


def _teil(item, name):
    """Teil im Server zu Hersteller und Hersteller-Artikelnr. der Position, sonst neu: (id, neu)."""
    liste = _antwort(_anfrage("GET", "/api/teile", params={
        "hersteller": item.manufacturer, "artikelnr": item.manufacturer_article_no}))
    genau = [t for t in liste if t.get("treffer") == "genau"]
    if genau:
        return genau[0]["id"], False
    r = _anfrage("POST", "/api/teile", json={
        "hersteller": item.manufacturer, "artikelnr": item.manufacturer_article_no, "von": name,
        "profile_no": item.profile_no or "", "bezeichnung": item.description or "",
        "grund": "Freigabe im ATEX-Agent"})
    daten = _json_oder_none(r)
    if r.status_code == 409 and isinstance(daten, dict) and daten.get("teil_id"):
        return daten["teil_id"], False  # gibt es schon, z. B. gleichzeitig angelegt
    return _antwort(r)["id"], True


def uebertrage(finding, basis):
    """Datei eines freigegebenen Vorschlags als neue Version an den Server.

    Gibt (Stand, Text) zurück; Stand „uebertragen“, „bekannt“ (der Server hat die Datei schon,
    gleicher SHA-256) oder „fehler“. Wirft nie: die Freigabe gilt in jedem Fall.
    """
    item = finding.item
    pfad = freigabe.datei(finding, basis)
    if pfad is None:
        current_app.logger.warning("Datei fehlt unter ATEX_DOCS: %s", finding.file_path)
        return "fehler", "Die Datei ist im ATEX-Agent nicht mehr vorhanden"
    try:
        sha = _datei_hash(pfad)
        if markierungen([sha]):
            return "bekannt", f"{pfad.name} liegt schon im Dokumentenarchiv"
        if not ((item.manufacturer or "").strip() and (item.manufacturer_article_no or "").strip()):
            return "fehler", ("Ohne Hersteller und Hersteller-Artikelnummer lässt sich das "
                              "Dokument im Dokumentenarchiv keinem Teil zuordnen")
        name = finding.decided_by or "ATEX-Agent"
        teil_id, neu = _teil(item, name)
        # Satz für ältere Archive ohne Quellennachweis; neue lesen die Felder quelle_*
        hinweis = f"Im ATEX-Agent freigegeben von {name}; Quelle: {_quelle(finding)}"
        r = _anfrage("POST", f"/api/teile/{teil_id}/dokumente",
                     data={"von": name, "doc_type": finding.doc_type, "quelle": "atex_agent",
                           "hinweis": hinweis, **quelle_felder(finding, pfad)},
                     files={"datei": (pfad.name, pfad.read_bytes(), "application/pdf")})
        v = _antwort(r)["verknuepfung"]
    except (NichtErreichbar, Abgelehnt, OSError) as e:
        return "fehler", str(e).rstrip(".")
    except Exception as e:  # unerwartete Antwort: nie die Freigabe aufhalten
        current_app.logger.exception("Zertifikatsserver: unerwartete Antwort")
        return "fehler", "Das Dokumentenarchiv hat unerwartet geantwortet"
    art = v.get("doc_type_text") or v["doc_type"]
    return "uebertragen", (f"{pfad.name} liegt jetzt im Dokumentenarchiv, als Version "
                           f"{v['version']} der {art} beim Teil {v['hersteller']} {v['artikelnr']}"
                           f"{'. Das Teil ist dort neu angelegt' if neu else ''}")


def _vermerk(finding, text, fehler=False):
    """Eintrag ins Protokoll des Vorgangs (bom_event) und ins Log; speichern macht der Aufrufer."""
    item = finding.item
    zeile = f"Position {item.pos or item.row_number}: {text.rstrip('.')}."
    (current_app.logger.warning if fehler else current_app.logger.info)(zeile)
    db.session.add(BomEvent(bom_id=item.bom_id, bom_item_id=item.id, text=zeile))


def _uebertrage_mit_vermerk(finding, basis, nachgeholt=False):
    """Übertragen und im Protokoll des Vorgangs vermerken: Erfolg immer, Fehler nur bei der
    Freigabe selbst (beim Nachholen nicht bei jedem Versuch erneut)."""
    stand, text = uebertrage(finding, basis)
    if stand == "uebertragen":
        _vermerk(finding, text + (". Die Datei ist damit nachgereicht" if nachgeholt else ""))
    elif stand == "fehler" and not nachgeholt:
        current_app.logger.warning("Nachholen mit „flask --app wsgi zert-abgleich“ oder bei der "
                                   "nächsten Freigabe.")
        _vermerk(finding, f"{Path(finding.file_path or '').name} ist noch nicht im "
                          f"Dokumentenarchiv. {text.rstrip('.')}. Die Freigabe gilt trotzdem, "
                          "das Dokument wird bei der nächsten Freigabe nachgereicht",
                 fehler=True)
    return stand, text


def nach_freigabe(finding, basis):
    """Haken aus freigabe.entscheide. In einer Anfrage erst nach deren Speichern (die Freigabe
    gilt dann sicher), mit eigenem Speichern des Vermerks; sonst sofort, der Aufrufer speichert.
    Läuft nur mit ZERTIFIKATSSERVER_URL. Ist der Server erreichbar, holt die Freigabe nebenbei
    bis zu NACHHOLEN_JE_FREIGABE früher gescheiterte Übertragungen nach."""
    if not _url():
        return
    if not has_request_context():
        _uebertrage_mit_vermerk(finding, basis)
        return
    finding_id = finding.id

    @after_this_request
    def _nach_dem_speichern(antwort):
        try:
            db.session.rollback()  # nur was die Anfrage gespeichert hat, zählt
            f = db.session.get(Finding, finding_id)
            if f is not None and f.status == "freigegeben":
                stand, _ = _uebertrage_mit_vermerk(f, basis)
                if stand != "fehler":
                    abgleich(grenze=NACHHOLEN_JE_FREIGABE, basis=basis)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Zertifikatsserver: Übertragung nach der Freigabe")
        return antwort


def abgleich(grenze=None, ausgabe=None, basis=None):
    """Freigegebene Vorschläge, deren Datei der Server nicht kennt, übertragen. Wiederholbar:
    je SHA-256 einmal, der Server kennt danach den Hash. grenze: höchstens so viele Versuche.
    ausgabe: Zeile je Datei (Befehl zert-abgleich). Erfolge stehen im Protokoll des Vorgangs.
    Gibt {"uebertragen", "bekannt", "fehler"} zurück; speichern macht der Aufrufer."""
    basis = basis or current_app.config["ATEX_DOCS"]
    ausgabe = ausgabe or (lambda zeile: None)
    zahlen = {"uebertragen": 0, "bekannt": 0, "fehler": 0}
    funde = db.session.scalars(
        db.select(Finding).where(Finding.status == "freigegeben", Finding.file_path.is_not(None))
        .order_by(Finding.decided_at.desc(), Finding.id.desc())).all()
    je_hash = {}  # je Datei der zuletzt freigegebene Vorschlag
    for f in funde:
        pfad = freigabe.datei(f, basis)
        h = f.file_hash or (_datei_hash(pfad) if pfad else None)
        if h:
            je_hash.setdefault(h, f)
        else:
            zahlen["fehler"] += 1
            ausgabe(f"Vorschlag {f.id}: Datei fehlt ({f.file_path})")
    hashes = list(je_hash)
    try:
        bekannt = set()
        for i in range(0, len(hashes), 40):  # Adresse nicht zu lang
            bekannt |= markierungen(hashes[i:i + 40]).keys()
    except NichtErreichbar as e:
        ausgabe(str(e))
        zahlen["fehler"] += len(hashes)
        return zahlen
    zahlen["bekannt"] = len(bekannt)
    versuche = 0
    for h, f in je_hash.items():
        if h in bekannt:
            continue
        if grenze is not None and versuche >= grenze:
            break
        versuche += 1
        stand, text = _uebertrage_mit_vermerk(f, basis, nachgeholt=True)
        zahlen["fehler" if stand == "fehler" else stand] += 1
        ausgabe(f"Vorschlag {f.id} ({f.file_path}): {text}")
    return zahlen


def _ordnername(name):
    return re.sub(r"[^\w+\-]+", "_", name or "").strip("_") or "unbekannt"


def _dateiname(name):
    stamm = re.sub(r"(?i)\.pdf$", "", Path(name or "").name)
    return f"{re.sub(r'[^\w+\-.]+', '_', stamm).strip('_.')[:120] or 'dokument'}.pdf"


def _frei(pfad, sha):
    """Freier Dateiname: belegt, dann mit dem Anfang des Hashes."""
    return pfad.with_name(f"{pfad.stem}_{sha[:8]}.pdf") if pfad.exists() else pfad


def auszug(wurzel, hersteller):
    """(Ordner, erreichbar): Ordner mit allen aktuellen PDFs des Herstellers im Archiv, sonst None.

    Das ist die lokale Stufe der Suche, wenn ein Archiv eingestellt ist: Der Agent sucht dann nur
    in diesen Dokumenten, nicht im Herstellerordner. Die Dateien liegen als Zwischenspeicher unter
    <ATEX_DOCS>/_upload/zertifikatsserver/<Hersteller>/ (innerhalb von ATEX_DOCS: Pfadprüfung und
    Vorschau /files/… gelten wie für jeden Fund). Nur was im Archiv aktuell ist (Ablage „aktuell“,
    nicht ausgemustert, nicht als veraltet bestätigt). Liegt dieselbe Datei (gleicher SHA-256)
    schon unter ATEX_DOCS, wird sie kopiert statt geladen. Was nicht mehr aktuell ist, wandert nach
    _upload/zertifikatsserver/_zurueckgezogen/<Hersteller>/ und wird nicht mehr gefunden.
    erreichbar ist False, wenn das Archiv nicht antwortet; ohne ZERTIFIKATSSERVER_URL (None, True).
    """
    if not _url() or not (hersteller or "").strip():
        return None, True
    wurzel = Path(wurzel)
    try:
        dokumente = _holen("/api/dokumente", hersteller=hersteller, ablage="aktuell",
                           ausgemustert="nein", dateiart="pdf")
    except NichtErreichbar as e:
        current_app.logger.warning(f"{e} Dokumente des Herstellers {hersteller} nicht gelesen.")
        return None, False
    gruppen = {}
    for d in dokumente:
        gruppen.setdefault(d["hersteller"], []).append(d)
    if len(gruppen) > 1:  # „Siemens“ passt auf mehrere Hersteller: nur ein gleichnamiger zählt
        gleich = [n for n in gruppen
                  if freigabe.normalisiere(n) == freigabe.normalisiere(hersteller)]
        if len(gleich) != 1:
            current_app.logger.warning(f"Zertifikatsserver: Hersteller „{hersteller}“ mehrdeutig "
                                       f"({', '.join(sorted(gruppen))}), nicht gelesen.")
            return None, True
        gruppen = {gleich[0]: gruppen[gleich[0]]}
    if not gruppen:
        return None, True
    name, liste = next(iter(gruppen.items()))
    speicher = wurzel.joinpath(*ZWISCHENSPEICHER)
    ziel = speicher / _ordnername(name)
    with _sperre:
        lokal = {}  # SHA-256 -> Datei unter ATEX_DOCS außerhalb des Zwischenspeichers
        for p in sorted(wurzel.rglob("*")):
            if p.suffix.lower() == ".pdf" and p.is_file() and not p.is_relative_to(speicher):
                lokal.setdefault(_datei_hash(p), p)
        # nur PDFs (ältere Archive kennen dateiart nicht: dort ist alles PDF)
        gewollt = {d["sha256"]: d for d in liste
                   if d.get("ablage") == "aktuell" and not d.get("ausgemustert")
                   and d.get("dateiart", "pdf") == "pdf"
                   and not (veraltet_an() and d.get("markierungsstand") == "bestaetigt")}
        da = set()
        for p in sorted(ziel.glob("*.pdf")) if ziel.is_dir() else []:
            sha = _datei_hash(p)
            if sha in gewollt and sha not in da:
                da.add(sha)
                continue
            weg = _frei(speicher / ZURUECKGEZOGEN / ziel.name / p.name, sha)
            weg.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(p, weg)
            current_app.logger.warning(f"Zertifikatsserver: {p.relative_to(wurzel)} nicht mehr "
                                       f"aktuell im Server (oder jetzt in ATEX_DOCS), verschoben "
                                       f"nach {weg.relative_to(wurzel)}")
        for sha, d in gewollt.items():
            if sha in da:
                continue
            if sha in lokal:
                ziel.mkdir(parents=True, exist_ok=True)
                shutil.copy2(lokal[sha], _frei(ziel / _dateiname(d["dateiname"]), sha))
            else:
                _laden(d, ziel)
        return (ziel if ziel.is_dir() and any(ziel.glob("*.pdf")) else None), True


def _laden(d, ziel):
    """PDF eines Server-Dokuments in den Zwischenspeicher, geprüft über den SHA-256."""
    try:
        r = _anfrage("GET", d.get("datei_url") or d["pdf_url"])
        if r.status_code != 200:
            raise Abgelehnt(f"Zertifikatsserver: PDF {d['pdf_url']} nicht geliefert ({r.status_code})")
        if hashlib.sha256(r.content).hexdigest() != d["sha256"]:
            raise Abgelehnt(f"Zertifikatsserver: PDF {d['pdf_url']} hat einen anderen SHA-256")
        ziel.mkdir(parents=True, exist_ok=True)
        pfad = _frei(ziel / _dateiname(d["dateiname"]), d["sha256"])
        tmp = pfad.with_name(pfad.name + ".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(pfad)
    except (NichtErreichbar, Abgelehnt, OSError) as e:
        current_app.logger.warning(f"{e}; {d.get('dateiname')} nicht geladen.")
