"""Adapter für die HTML-Oberfläche des Teams (web/index.html; Vertrag nach dem Entwurf der
Oberfläche aus dem Team, beschrieben in docs/api.md).

Kein zweites Backend: Jeder Endpunkt liest und schreibt unser Datenmodell und ruft die
vorhandenen Dienste (excel_import, suche, freigabe, hochladen, email_entwurf, erinnerung,
korrektur, excel_export, status). Hier steht nur die Übersetzung in die Felder des Vertrags.

Begriffe: Vorgang = Bom, Artikel = BomItem, Fund = Finding, Mail = EmailDraft (Anfrage an den
Hersteller), Mängelliste = PurchasingEmail.

Stand eines Artikels, unser Stand -> Vertrag:

    kein_ex_teil    -> kein_ex
    offen           -> wartet
    klaerung        -> info_fehlt     (Angaben fehlen oder Ex-Frage offen; Grund in „notiz“)
    vorschlag       -> zu_pruefen
    vorhanden       -> geprueft, wenn in diesem Vorgang ein Mensch einen Fund dazu freigegeben
                       hat, sonst in_db (schon früher geprüft, aus den Stammdaten)
    nicht_gefunden  -> mail_entwurf   (nach der Suche wird der Entwurf angelegt)
    angefordert     -> angefordert, oder erinnert, wenn eine Erinnerung gesendet ist
    eskaliert       -> eskaliert
    erledigt        -> geprueft       (von Hand erledigt; Begründung in „notiz“)
    dazu „suche“, solange die Hintergrundsuche gerade diese Position bearbeitet; „suche_in“
    sagt Datenbank, Ordner des Herstellers, Ähnlichkeitssuche (Suchdienst, wenn der Ordner
    nichts hat) oder Internet (kein Ordner, Internet an).
    Ob der Ordner-Lauf danach noch ins Internet geht, sagt erst das Ergebnis im Protokoll.

Texte im Protokoll und in Fehlern liest die Sachbearbeiterin: ganze Sätze mit „Sie“, ohne
Technikwörter; der Zertifikatsserver heißt für sie „Dokumentenarchiv“.

Das Protokoll je Vorgang („ereignisse“) steht in der Tabelle bom_event, der Name aus dem Upload
in bom.name (ohne Namen gilt die Maschine aus der Excel-Datei oder der Dateiname). Welche Position
gerade gesucht wird, liegt im Arbeitsspeicher (gunicorn mit einem Prozess); dass ein Lauf offen
ist, steht in bom.search_started_at: Nach einem Neustart mitten in der Suche setzt setze_fort
den Lauf für die noch nicht gesuchten Positionen fort.
"""

import hashlib
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import current_app

from ..db import db
from ..models import Bom, BomIssue, BomItem, Finding
from ..models.bom import BomEvent
from ..models.stammdaten import DOC_TYPES, LANGUAGES
from . import (email_entwurf, erinnerung, excel_export, freigabe, hochladen, korrektur,
               lesepuffer, status, suche)
from .pdf_text import lesbar  # „#U00e4“ in Dateinamen als „ä“, nur zum Anzeigen
from . import modelle  # Sprachmodell und Websuche-Versuche je Vorgang (services/modelle.py)
from .excel_import import lese_stueckliste

STATUS = {"kein_ex_teil": "kein_ex", "offen": "wartet", "klaerung": "info_fehlt",
          "vorschlag": "zu_pruefen", "nicht_gefunden": "mail_entwurf",
          "angefordert": "angefordert", "eskaliert": "eskaliert", "erledigt": "geprueft"}
QUELLE = {"ordner": "Ordner des Herstellers", "suchdienst": "Ähnlichkeitssuche",
          "internet": "Internet", "manuell": "von Hand eingefügt",
          "hersteller": "Antwort des Herstellers"}
# Stand eines Artikels in Worten, für das Protokoll
STAND_TEXT = {"kein_ex": "kein Ex-Teil", "wartet": "wartet", "info_fehlt": "Angabe fehlt",
              "suche": "wird gesucht", "zu_pruefen": "Fund prüfen", "in_db": "aus der Datenbank",
              "geprueft": "freigegeben", "mail_entwurf": "E-Mail-Entwurf",
              "angefordert": "angefordert", "erinnert": "erinnert", "eskaliert": "eskaliert"}
# „Der Agent ist fertig. Von den 30 Positionen sind 5 …“
FERTIG_TEXT = {"zu_pruefen": "mit Fund zum Prüfen", "mail_entwurf": "mit E-Mail-Entwurf",
               "geprueft": "freigegeben", "in_db": "schon geprüft in der Datenbank",
               "info_fehlt": "mit fehlender Angabe", "kein_ex": "kein Ex-Teil",
               "wartet": "noch nicht gesucht", "angefordert": "beim Hersteller angefragt",
               "erinnert": "beim Hersteller angefragt", "eskaliert": "an den Einkauf eskaliert"}
# Felder der Korrektur: Vertrag -> unser Feld (services/korrektur.FELDER)
KORREKTUR = {"bezeichnung": "description", "hersteller": "manufacturer", "artikelnr": "manufacturer_article_no",
             "einbauort": "location", "profile_no": "profile_no"}
FELDNAME = {"typ": "Typ", "kennzeichnung": "Kennzeichnung", "zertifikat": "Zertifikatsnummer",
            "kategorie": "Kategorie"}
GRUND = "Grund der Anfrage: "
ANWENDER = "Oberfläche"  # wenn die Oberfläche keinen Namen schickt

_sperre = threading.Lock()
_lauf = {}       # bom_id -> {"item_id", "suche_in"}, solange die Suche läuft


class Fehler(Exception):
    """Text für den Anwender und HTTP-Code."""

    def __init__(self, text, code=400):
        super().__init__(text)
        self.code = code


# Protokoll und Lauf


def _zeit(zeit):
    """Uhrzeit eines Protokolleintrags (gespeichert in UTC) in der Zeitzone der Anzeige."""
    zone = ZoneInfo(current_app.config["ZEITZONE"])
    return zeit.replace(tzinfo=timezone.utc).astimezone(zone).strftime("%d.%m. %H:%M:%S")


def _datum(zeit):
    """Datum und Uhrzeit (gespeichert in UTC) in der Zeitzone der Anzeige, „13.11.2026 14:05“."""
    if not zeit:
        return None
    zone = ZoneInfo(current_app.config["ZEITZONE"])
    utc = zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)
    return utc.astimezone(zone).strftime("%d.%m.%Y %H:%M")


def protokolliere(bom_id, text, artikel_id=None):
    """Eintrag ins Protokoll des Vorgangs (Tabelle bom_event), sofort gespeichert."""
    db.session.add(BomEvent(bom_id=bom_id, bom_item_id=artikel_id, text=text))
    db.session.commit()


def ereignisse(bom_id, seit=0):
    """Einträge ab dem seit-ten, fortlaufend nummeriert: [{n, zeit, text, artikel_id}]. Dateinamen
    im Text lesbar, auch in Einträgen, die vor dieser Umwandlung gespeichert sind."""
    liste = db.session.scalars(db.select(BomEvent).filter_by(bom_id=bom_id)
                               .order_by(BomEvent.id).offset(max(seit, 0))).all()
    return [{"n": max(seit, 0) + i, "zeit": _zeit(e.created_at), "text": lesbar(e.text),
             "artikel_id": e.bom_item_id} for i, e in enumerate(liste, start=1)]


def laeuft(bom_id):
    with _sperre:
        return bom_id in _lauf


def _setze_lauf(bom_id, item_id=None, suche_in=None):
    with _sperre:
        _lauf[bom_id] = {"item_id": item_id, "suche_in": suche_in}


def _lauf_von(bom_id):
    with _sperre:
        return dict(_lauf.get(bom_id) or {})


# Ansichten


def name(bom):
    """Name des Vorgangs. Ohne eigenen Namen: Maschine (oder Dateiname) mit der Nummer des
    Vorgangs, damit auch dann jeder Vorgang eindeutig heißt."""
    if bom.name:
        return bom.name
    return f"{bom.machine or PurePosixPath(bom.source_file_name).stem} · Nr. {bom.id}"


def eindeutiger_name(wunsch, ohne_id=None):
    """Der gewünschte Name, bei Gleichstand mit einem anderen Vorgang mit „ (2)“, „ (3)“ …
    Leer bleibt leer (dann gilt der Name aus name()). ohne_id: der Vorgang, der umbenannt wird."""
    wunsch = (wunsch or "").strip()
    if not wunsch:
        return None
    vergeben = {name(b).casefold() for b in db.session.scalars(db.select(Bom)) if b.id != ohne_id}
    kandidat, n = wunsch, 1
    while kandidat.casefold() in vergeben:
        n += 1
        kandidat = f"{wunsch} ({n})"
    return kandidat


def _pos(item):
    return f"Position {item.pos or item.row_number}"


def _und(teile):
    teile = [t for t in teile if t]
    return ", ".join(teile[:-1]) + " und " + teile[-1] if len(teile) > 1 else "".join(teile)


def _freigegeben_hier(item):
    """Freigegebene Funde dieser Position oder ihrer Dubletten (gleiche PRO.FILE-Nr.) im Vorgang."""
    gleiche = [i for i in item.bom.items if i is item
               or (item.profile_no and i.profile_no == item.profile_no)]
    return [f for i in gleiche for f in i.findings if f.status == "freigegeben"]


def _nur_automatisch(item):
    """Freigegeben, und zwar nur vom Agenten selbst: so kennzeichnet die Liste die Position."""
    hier = _freigegeben_hier(item)
    return bool(hier) and all(f.auto_approved for f in hier)


def _erinnert(item):
    return any(e.art == "erinnerung" and e.status == "gesendet" for e in item.email_drafts)


def artikel_status(item, lauf=None):
    if lauf and lauf.get("item_id") == item.id:
        return "suche"
    if item.status == "vorhanden":
        return "geprueft" if _freigegeben_hier(item) else "in_db"
    if item.status == "angefordert" and _erinnert(item):
        return "erinnert"
    return STATUS.get(item.status, item.status)


def _mails(item):
    """Mails der Position, wie die Oberfläche sie zeigt: Anfragen mit ihren gesendeten
    Erinnerungen zusammengefasst, dazu noch nicht gesendete Erinnerungen als eigener Entwurf."""
    return [e for e in item.email_drafts if e.art == "anfrage" or e.status != "gesendet"]


def _anzahl_funde(item):
    if item.status == "vorhanden" and not _freigegeben_hier(item):
        return len(_db_zuordnungen(item))
    return sum(f.status in ("vorgeschlagen", "freigegeben") for f in item.findings)


def artikel_kurz(item, lauf=None):
    mails = _mails(item)
    s = artikel_status(item, lauf)
    return {"id": item.id, "pos": item.pos, "profile_no": item.profile_no,
            "bezeichnung": item.description, "hersteller": item.manufacturer,
            "artikelnr": item.manufacturer_article_no, "menge": item.quantity,
            "einbauort": item.location, "status": s,
            "suche_in": lauf.get("suche_in") if s == "suche" else None,
            "mail_id": mails[-1].id if mails else None, "anzahl_funde": _anzahl_funde(item),
            "freigegeben": (item.status in ("vorhanden", "erledigt")
                            or bool(_freigegeben_hier(item))),
            # alle Freigaben hier hat der Agent selbst gemacht (freigabe.automatisch)
            "automatisch": _nur_automatisch(item),
            # Pflichtsprache (M8): Konformitätserklärung geprüft, aber nicht auf Deutsch
            "deutsch_fehlt": (item.status not in ("kein_ex_teil", "erledigt")
                              and bool(status.pflichtsprache_fehlt(item))),
            "kontakt_fehlt": any(m.status != "gesendet" for m in mails)
                             and email_entwurf.empfaenger(mails[-1])["kontakt_fehlt"]}


def _offen(bom):
    return status.offene_positionen(bom)


def schritt(bom, lauf):
    if lauf:
        return "suche"
    if any(i.searched_at for i in bom.items):
        return "pruefung"
    if any(i.status == "klaerung" for i in bom.items):
        return "maengel"
    return "eingelesen"


def vorgang(bom, kurz=False):
    """Vorgang im Vertrag; den Stand frisch ableiten, außer die Suche läuft gerade. Nur lesend
    (lesepuffer.nur_lesen): der frische Stand steht in der Antwort, gespeichert wird nichts."""
    with lesepuffer.nur_lesen():
        return _vorgang(lesepuffer.lade(bom), kurz)


def _vorgang(bom, kurz):
    lauf = _lauf_von(bom.id)
    if not lauf:
        status.aktualisiere_alle(bom)
    artikel = [artikel_kurz(i, lauf) for i in bom.items]
    zaehler = {}
    for a in artikel:
        zaehler[a["status"]] = zaehler.get(a["status"], 0) + 1
    offen = _offen(bom)
    d = {"id": bom.id, "name": name(bom), "erstellt": _datum(bom.created_at),
         "schritt": schritt(bom, lauf), "anzahl": len(artikel), "zaehler": zaehler,
         "vollstaendig": not offen, "offen": len(offen),
         "sprachen": sprachen_von(bom),
         "deutsch_fehlt": sum(a["deutsch_fehlt"] for a in artikel),
         "kontakte_fehlen": sum(email_entwurf.empfaenger(e)["kontakt_fehlt"]
                                for i in bom.items for e in _mails(i) if e.status != "gesendet")}
    # für Filter und Suche der Projektliste: Maschine und Dateiname aus dem Upload
    d.update(maschine=bom.machine, datei=bom.source_file_name)
    if not kurz:
        d["artikel"] = artikel
    return d


def vorgaenge():
    """Alle Vorgänge ohne Positionen, neueste zuerst; wie vorgang() nur lesend."""
    with lesepuffer.nur_lesen():
        return [_vorgang(b, kurz=True) for b in lesepuffer.vorgaenge()]


def _beleg(b, finding):
    """{seite, text} für eine belegte Angabe aus services/belege.py, sonst None."""
    if not b.get("belegt"):
        return None
    if b["wert"] == "typ" and b.get("seite") == finding.page and finding.snippet:
        text = finding.snippet
    else:
        text = b.get("hinweis") or b.get("angabe")
    return {"seite": b.get("seite"), "text": text}


def felder(finding):
    """{Name: {wert, beleg}} aus dem Vorschlag; beleg None heißt unbelegt (rot).

    Dokumenttyp und Sprachen erkennt der Agent aus dem ganzen Dokument, sie stehen nie wörtlich
    darin: Sie tragen ``abgeleitet: True``, die Oberfläche zeigt sie neutral und zählt sie nicht
    als unbelegt."""
    f = {"Dokumenttyp": {"wert": excel_export.ART_TEXT.get(finding.doc_type, finding.doc_type),
                         "beleg": None, "abgeleitet": True}}
    # Angaben aus dem Dokument (services/angaben.py): Gültigkeit oben, der Rest nach den Belegen
    from . import angaben  # hier, damit die Importzeile oben unberührt bleibt
    kopf, rest = angaben.felder(finding.angaben)
    f.update(kopf)
    if finding.belege is not None:
        zahl = {}
        for b in finding.belege:
            titel = FELDNAME.get(b["wert"], b["wert"])
            zahl[titel] = zahl.get(titel, 0) + 1
            if zahl[titel] > 1:
                titel = f"{titel} {zahl[titel]}"
            f[titel] = {"wert": b.get("angabe"), "beleg": _beleg(b, finding)}
    else:  # ohne Belegprüfung (z. B. nur URL): nur die Fundstelle ist belegt
        f["Typ"] = {"wert": finding.matched_term or finding.item.manufacturer_article_no,
                    "beleg": {"seite": finding.page, "text": finding.snippet}
                    if finding.page and finding.snippet else None}
        f["Kennzeichnung"] = {"wert": finding.ex_marking_raw, "beleg": None}
        f["Zertifikatsnummer"] = {"wert": finding.certificate_no, "beleg": None}
    f.update(rest)
    f["Sprachen"] = {"wert": finding.languages, "beleg": None, "abgeleitet": True}
    return f


def _datei_url(pfad, finding_id=None):
    """Adresse für die Vorschau: /files/<pfad>, mit ?markiert=<id> die markierte PDF."""
    url = "/files/" + quote(pfad, safe="/")
    return f"{url}?markiert={finding_id}" if finding_id else url


def fund(finding, quelle=None):
    from . import zertifikatsserver  # hier statt oben: nur diese Funktion braucht es
    basis = current_app.config["ATEX_DOCS"]
    da = freigabe.datei(finding, basis) is not None
    return {"id": finding.id,
            "datei": lesbar(PurePosixPath(finding.file_path).name) if finding.file_path
            else finding.url,
            "pfad": finding.file_path,
            "url": _datei_url(finding.file_path, finding.id) if da else finding.url,
            "quelle": quelle or ("Dokumentenarchiv" if (finding.file_path or "").startswith(
                suche.ARCHIV_ORDNER) else QUELLE.get(finding.source, finding.source)),
            "seite": finding.page, "felder": felder(finding),
            # zusätzlich zum Vertrag: Prüfergebnis und Stand des Vorschlags
            "pruefung": {"ergebnis": finding.check_result,
                         "meldung": lesbar(finding.check_message)},
            "status": finding.status, "url_quelle": finding.url, "doc_type": finding.doc_type,
            # vom Agenten selbst freigegeben (freigabe.automatisch), kein Mensch hat geklickt
            "automatisch": bool(finding.auto_approved),
            # Herkunft des Fundes (M6), Block aus zertifikatsserver.quellennachweis; „quelle“
            # oben bleibt der Text des Vertrags, den index.html zeigt
            "quellennachweis": zertifikatsserver.quellennachweis(finding)}


def _db_zuordnungen(item):
    part = lesepuffer.teil(item.profile_no)
    return [pd for pd in part.documents if pd.status == "geprueft"] if part else []


def _db_funde(item):
    """Geprüfte Dokumente aus den Stammdaten; mit dem freigegebenen Vorschlag dazu, wenn es ihn
    gibt (dann mit dessen Belegen), sonst nur die Angaben des Dokuments (id negativ)."""
    funde = []
    for pd in _db_zuordnungen(item):
        doc = pd.document
        vorschlag = db.session.scalar(db.select(Finding).filter_by(
            file_hash=doc.file_hash, status="freigegeben").order_by(Finding.id)) \
            if doc.file_hash else None
        if vorschlag is not None:
            funde.append(fund(vorschlag, "Datenbank"))
            continue
        pfad = doc.file_path
        da = pfad and (Path(current_app.config["ATEX_DOCS"]) / pfad).is_file()
        funde.append({"id": -doc.id, "datei": lesbar(PurePosixPath(pfad).name) if pfad else doc.url,
                      "pfad": pfad, "url": _datei_url(pfad) if da else doc.url,
                      "quelle": "Datenbank", "seite": None,
                      "felder": {"Dokumenttyp": {"wert": excel_export.ART_TEXT.get(
                          doc.doc_type, doc.doc_type), "beleg": None},
                                 "Zertifikatsnummer": {"wert": doc.certificate_no, "beleg": None},
                                 "Sprachen": {"wert": ",".join(s.language for s in doc.languages)
                                              or None, "beleg": None}},
                      "pruefung": None, "status": "freigegeben", "url_quelle": doc.url})
    return funde


def _funde(item):
    if item.status == "vorhanden" and not _freigegeben_hier(item):
        return _db_funde(item)
    offen = [f for f in item.findings if f.status == "vorgeschlagen"]
    frei = [f for f in item.findings if f.status == "freigegeben"]
    return [fund(f) for f in offen + frei]


def _freigabe(item):
    """Wer freigegeben hat: ein Fund in diesem Vorgang (auch wenn noch ein Pflichtdokument
    fehlt), von Hand erledigt, sonst geprüft aus den Stammdaten (in_db)."""
    hier = _freigegeben_hier(item)
    if hier:
        f = hier[-1]
        return {"fund_id": f.id, "von": f.decided_by, "zeit": _datum(f.decided_at),
                "automatisch": bool(f.auto_approved)}
    if item.status == "erledigt":
        return {"fund_id": None, "von": item.resolved_by, "zeit": _datum(item.resolved_at)}
    if item.status != "vorhanden":
        return None
    zuordnungen = _db_zuordnungen(item)
    if not zuordnungen:
        return None
    pd = zuordnungen[0]
    funde = _db_funde(item)
    return {"fund_id": funde[0]["id"] if funde else None, "von": pd.verified_by or "Datenbank",
            "zeit": _datum(pd.verified_at)}


def artikel(item):
    """Position mit Funden, Freigabe, Notiz, E-Mail; wie vorgang() nur lesend."""
    with lesepuffer.nur_lesen():
        return _artikel(item)


def _artikel(item):
    lauf = _lauf_von(item.bom_id)
    lesepuffer.lade(item.bom)  # Dubletten im Vorgang (_freigegeben_hier) mit ihren Funden
    lesepuffer.lade_funde(item)
    if not lauf:
        status.aktualisiere(item)
    mails = _mails(item)
    return artikel_kurz(item, lauf) | {
        "funde": _funde(item), "freigabe": _freigabe(item),
        "notiz": _notiz(item, mails),
        "mail": mail(mails[-1]) if mails else None,
        "gesucht_in": item.searched_in,
        "hinweise": _hinweise(item)}


def _notiz(item, mails):
    """Hinweis zum Stand, sonst der Grund der Anfrage; der Hinweis „Adresse fehlt“
    (services/kontakte.py) steht dahinter und verdrängt den Grund nicht."""
    from . import kontakte
    kontakt = kontakte.kontakt_hinweis(item)
    basis = item.status_note
    if kontakt and basis and basis.endswith(kontakt):
        basis = basis[:-len(kontakt)].strip() or None
    if not basis and mails:
        basis = _grund(mails[-1])
    if basis and kontakt and not basis.endswith((".", "!", "?")):
        basis += "."
    return " ".join(t for t in (basis, kontakt) if t) or None


def _hinweise(item):
    """Hinweise, die nichts aufhalten: fehlende freiwillige Sprachfassungen, erwartete
    Baumusterprüfbescheinigung bei Kategorie 1 und 2."""
    from . import sprachversionen
    hinweise = []
    for art, sprachen in sprachversionen.fehlende(item).items():
        namen = " und ".join(status.SPRACHNAME.get(s, s) for s in sprachen)
        hinweise.append(f"Die {excel_export.ART_TEXT.get(art, art)} fehlt noch auf {namen}. "
                        "Das ist freiwillig und hält die Ausgabe nicht auf.")
    if hinweis := status.baumuster_hinweis(item):
        hinweise.append(hinweis)
    return hinweise


def _grund(entwurf):
    m = re.search(rf"^{GRUND}(.+)$", entwurf.body or "", re.M)
    if m:
        return m.group(1).strip()
    if entwurf.art == "erinnerung":
        return "Erinnerung an die erste Anfrage, weil der Hersteller nicht geantwortet hat."
    item = entwurf.item
    fehlt = status.fehlende_arten(item) if item.profile_no else []
    if fehlt and (any(f.status == "freigegeben" for f in item.findings)
                  or status.gepruefte_arten(item.profile_no)):
        arten = [excel_export.ART_TEXT.get(a, a) for a in fehlt]
        return f"Es fehlt noch die {_und(arten)}."
    if not item.searched_at:
        return "Für diese Position fehlt noch ein Dokument."
    return "Der Agent hat kein passendes Dokument gefunden."


def _kette(entwurf):
    erste = entwurf.original or entwurf
    return erste, [erste] + [e for e in erste.item.email_drafts if e.original is erste]


def mail(entwurf):
    erste, kette = _kette(entwurf)
    erinnerungen = [e for e in kette if e.art == "erinnerung" and e.status == "gesendet"]
    if entwurf.status != "gesendet":
        s = "entwurf"  # „freigegeben“ (über die andere Oberfläche) zählt hier als Entwurf
    elif any(e.escalated_at for e in kette):
        s = "eskaliert"
    elif erinnerungen and entwurf is erste:
        s = "erinnert"
    else:
        s = "gesendet"
    return {"id": entwurf.id, "vorgang_id": entwurf.item.bom_id, "artikel_id": entwurf.bom_item_id,
            "status": s, "an": entwurf.to_address, "betreff": entwurf.subject,
            "text": entwurf.body, "grund": _grund(entwurf), "gesendet": _datum(entwurf.sent_at),
            "erinnert": _datum(erinnerungen[-1].sent_at) if erinnerungen else None,
            "art": entwurf.art,
            # Herstellerkontakte: eigentlicher Empfänger, Adresse fehlt, wohin es wirklich geht
            **email_entwurf.empfaenger(entwurf)}


def mails(bom):
    with lesepuffer.nur_lesen():
        return [mail(e) for i in lesepuffer.lade(bom).items for e in _mails(i)]


# Anlegen


def lege_an(inhalt, dateiname, name_=None):
    """Neuer Vorgang aus der Stückliste, wie POST /api/boms. ImportFehler geht an den Aufrufer."""
    ergebnis = lese_stueckliste(inhalt)
    hash_ = hashlib.sha256(inhalt).hexdigest()
    frueher = db.session.scalar(db.select(Bom).filter_by(source_file_hash=hash_)
                                .order_by(Bom.created_at))
    bom = Bom(name=eindeutiger_name(name_), source_file_name=dateiname, source_file_hash=hash_,
              **ergebnis.meta)
    bom.items = [BomItem(**i) for i in ergebnis.items]
    bom.issues = [BomIssue(**i) for i in ergebnis.issues]
    if frueher:  # wie routes/boms.py: erlaubt, aber gemeldet
        bom.issues.append(BomIssue(
            code="datei_doppelt", severity="info",
            message=f"Dieselbe Datei wurde am {frueher.created_at:%d.%m.%Y} schon einmal "
                    f"hochgeladen, als Vorgang „{name(frueher)}“."))
    db.session.add(bom)
    db.session.flush()
    status.aktualisiere_alle(bom)
    db.session.commit()
    klaerung = sum(i.status == "klaerung" for i in bom.items)
    kein_ex = sum(i.status == "kein_ex_teil" for i in bom.items)
    n = len(bom.items)
    satz = f"Die Stückliste mit {_anzahl(n, 'Position', 'Positionen')} ist eingelesen."
    if klaerung:
        satz += (f" Bei {_anzahl(klaerung, 'Position', 'Positionen')} fehlt eine Angabe oder "
                 "ist unklar.")
    if kein_ex:
        satz += (f" {_anzahl(kein_ex, 'Position ist', 'Positionen sind')} kein Ex-Teil.")
    protokolliere(bom.id, satz)
    return bom


def _anzahl(n, eins, mehr):
    return f"{n} {eins if n == 1 else mehr}"


# Suche im Hintergrund


def sprachen_waehlen(bom, sprachen, von=None):
    """Legt fest, welche Sprachen dieser Vorgang braucht; nur sie sucht der Agent. Speichert."""
    if not isinstance(sprachen, list) or not sprachen:
        raise Fehler("Bitte wählen Sie mindestens eine Sprache aus.")
    wahl = list(dict.fromkeys(str(s).strip().lower() for s in sprachen))
    if any(s not in LANGUAGES for s in wahl):
        raise Fehler("Eine der gewählten Sprachen kennt der Agent nicht. Bitte wählen Sie aus "
                     "der Liste.")
    wahl = [s for s in LANGUAGES if s in wahl]
    if bom.sprachen == ",".join(wahl):
        return
    bom.sprachen = ",".join(wahl)
    db.session.commit()
    namen = _und([status.SPRACHNAME.get(s, s) for s in wahl])
    protokolliere(bom.id, f"{von or 'Jemand'} hat die Sprachen für diesen Vorgang festgelegt: "
                          f"{namen}. Der Agent sucht nur Dokumente in "
                          f"{'dieser Sprache' if len(wahl) == 1 else 'diesen Sprachen'}.")


def sprachen_von(bom):
    """Für die Auswahl vor dem Start: {moeglich: [{kuerzel, name}], gewaehlt: [kuerzel]}."""
    from . import sprachversionen
    wahl = sprachversionen.gewaehlt(bom) or [
        s for s in LANGUAGES if s in sprachversionen.sprachen_liste()]
    return {"moeglich": [{"kuerzel": s, "name": status.SPRACHNAME.get(s, s)} for s in LANGUAGES],
            "gewaehlt": wahl}


def starte(bom):
    """Startet die Suche; im Hintergrund, außer OBERFLAECHE_SUCHE_SYNCHRON (Tests)."""
    wurzel = Path(current_app.config["ATEX_DOCS"])
    if not wurzel.is_dir():
        current_app.logger.error("Ordner der Ex-Dokumente fehlt: %s", wurzel)
        raise Fehler("Der Ordner mit den Ex-Dokumenten ist gerade nicht erreichbar, deshalb kann "
                     "der Agent nicht suchen. Bitte melden Sie das der Administration.", 503)
    with _sperre:
        if bom.id in _lauf:
            raise Fehler("Der Agent sucht in diesem Vorgang schon. Bitte warten Sie, bis er "
                         "fertig ist.", 409)
        _lauf[bom.id] = {"item_id": None, "suche_in": None}
    bom.search_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    _los(bom.id)


def _los(bom_id):
    if current_app.config.get("OBERFLAECHE_SUCHE_SYNCHRON"):
        _ausfuehren(bom_id)
        return
    app = current_app._get_current_object()
    threading.Thread(target=_im_hintergrund, args=(app, bom_id), daemon=True,
                     name=f"suche-vorgang-{bom_id}").start()


def setze_fort():
    """Nach einem Neustart: Suchen, die mittendrin abgebrochen sind (search_started_at steht
    noch), laufen für die noch nicht gesuchten Positionen weiter. Gibt die Vorgangs-IDs zurück."""
    if not Path(current_app.config["ATEX_DOCS"]).is_dir():
        return []
    offen = db.session.scalars(db.select(Bom.id).where(Bom.search_started_at.is_not(None))).all()
    fortgesetzt = []
    for bom_id in offen:
        with _sperre:
            if bom_id in _lauf:
                continue
            _lauf[bom_id] = {"item_id": None, "suche_in": None}
        protokolliere(bom_id, "Der ATEX-Agent wurde neu gestartet. Er setzt die unterbrochene "
                              "Suche jetzt fort.")
        _los(bom_id)
        fortgesetzt.append(bom_id)
    return fortgesetzt


def _im_hintergrund(app, bom_id):
    with app.app_context():  # eigene Sitzung (db.session gilt je App-Kontext)
        _ausfuehren(bom_id)


def _ausfuehren(bom_id):
    try:
        _ablauf(bom_id)
    except Exception as e:  # nie still: ins Protokoll und ins Log
        db.session.rollback()
        current_app.logger.exception("Suche für Vorgang %s abgebrochen", bom_id)
        protokolliere(bom_id, "Die Suche ist wegen eines Fehlers abgebrochen. Bitte starten Sie "
                              "den Agenten noch einmal. Bleibt der Fehler, melden Sie ihn bitte "
                              "der Administration.")
    finally:
        db.session.rollback()
        bom = db.session.get(Bom, bom_id)
        if bom is not None:
            bom.search_started_at = None
            db.session.commit()
        with _sperre:
            _lauf.pop(bom_id, None)


def _ablauf(bom_id):
    """Je Position: Datenbank, Ordner, Internet (suche.suche_vorgang), danach Entwurf an den
    Hersteller, wenn nichts gefunden wurde. Speichern je Position, damit das Polling den
    Fortschritt sieht."""
    bom = db.session.get(Bom, bom_id)
    wurzel = Path(current_app.config["ATEX_DOCS"])
    versuche = modelle.fuer_lauf(bom)  # Set-Point: Sprachmodell + Websuche-Versuche des Vorgangs
    status.aktualisiere_alle(bom)
    db.session.commit()
    # nach einem Neustart: Positionen, die dieser Lauf schon gesucht hat, nicht noch einmal
    seit = bom.search_started_at
    item_ids = [i.id for i in bom.items
                if not (seit and i.searched_at and i.searched_at.replace(tzinfo=None) >= seit)]
    internet_text = (f"Im Internet ruft er je Position höchstens "
                     f"{_anzahl(versuche, 'Seite', 'Seiten')} ab." if versuche
                     else "Im Internet sucht er nicht.")
    modell = modelle.vermerk(bom, versuche).lstrip(". ")  # welches Sprachmodell der Lauf nutzt
    protokolliere(bom_id, f"Der Agent startet mit {_anzahl(len(item_ids), 'Position', 'Positionen')}"
                          f". {internet_text} {modell}".strip())
    for item_id in item_ids:
        item = db.session.get(BomItem, item_id)
        vorher = status.aktualisiere(item)
        if vorher in suche.NICHT_SUCHEN:
            if vorher == "vorhanden" and not _freigegeben_hier(item):
                protokolliere(bom_id, f"{_pos(item)}: Die Dokumente liegen schon geprüft in der "
                                      "Datenbank. Der Agent musste nicht suchen.", item.id)
            continue
        pos = _pos(item)
        _setze_lauf(bom_id, item.id, "Datenbank")
        protokolliere(bom_id, f"{pos}: Der Agent sucht in der Datenbank.", item.id)
        ordner = suche.herstellerordner(wurzel, item.manufacturer)
        if suche.zertifikatsserver.an():  # mit Archiv: nur dort, nicht im Herstellerordner
            _setze_lauf(bom_id, item.id, "Dokumentenarchiv")
            protokolliere(bom_id, f"{pos}: Der Agent sucht im Dokumentenarchiv.", item.id)
        elif ordner:
            _setze_lauf(bom_id, item.id, "Ordner des Herstellers")
            protokolliere(bom_id, f"{pos}: Der Agent sucht im Ordner „{ordner.name}“ des "
                                  "Herstellers.", item.id)
        elif versuche:
            _setze_lauf(bom_id, item.id, "Internet")
            protokolliere(bom_id, f"{pos}: Für diesen Hersteller gibt es keinen Ordner. Der Agent "
                                  "sucht im Internet.", item.id)
        vorher_auto = {f.id for f in item.findings if f.auto_approved}
        try:
            einzeln = SimpleNamespace(items=[item])
            suche.suche_vorgang(einzeln, versuche,
                                melde=lambda wo: _setze_lauf(bom_id, item.id, wo))
            neu = email_entwurf.lege_entwuerfe_an(einzeln)
            db.session.commit()
        except suche.SuchFehler:
            raise
        except Exception as e:  # eine Position bricht nicht den ganzen Lauf ab
            db.session.rollback()
            current_app.logger.exception("Suche für Position %s: %s", item_id, e)
            protokolliere(bom_id, f"{pos}: Die Suche hat bei dieser Position nicht geklappt. "
                                  "Bitte starten Sie den Agenten später noch einmal.", item_id)
            continue
        finally:
            _setze_lauf(bom_id)
        auto = [f for f in item.findings if f.auto_approved and f.id not in vorher_auto]
        protokolliere(bom_id, _ergebnis(item, neu, auto), item.id)
    rechnung = {}
    for i in bom.items:
        s = artikel_status(i)
        rechnung[s] = rechnung.get(s, 0) + 1
    teile = [f"{n} {FERTIG_TEXT.get(s, STAND_TEXT.get(s, s))}"
             for s, n in sorted(rechnung.items(), key=lambda x: -x[1])]
    pruefen = rechnung.get("zu_pruefen", 0)
    stand = (f"Die einzige Position ist {FERTIG_TEXT.get(s, STAND_TEXT.get(s, s))}."
             if len(bom.items) == 1 and (s := next(iter(rechnung))) else
             f"Von den {len(bom.items)} Positionen sind {_und(teile)}.")
    protokolliere(bom_id, f"Der Agent ist fertig. {stand}"
                  + (" Bitte prüfen Sie jetzt die Funde und geben Sie sie frei." if pruefen
                     else ""))


def _ergebnis(item, neue_entwuerfe, auto=()):
    """auto: in diesem Lauf vom Agenten selbst freigegebene Funde (freigabe.automatisch)."""
    wo = f" {item.searched_in}" if item.searched_in else ""
    if auto:
        namen = _und([f"„{lesbar(PurePosixPath(f.file_path or '').name)}“" for f in auto])
        stand = STAND_TEXT.get(artikel_status(item), artikel_status(item))
        return (f"{_pos(item)}: Der Agent hat {namen} selbst freigegeben, weil "
                f"{'es' if len(auto) == 1 else 'sie'} im eigenen Bestand "
                f"{'lag' if len(auto) == 1 else 'lagen'} und die Vorprüfung nichts zu beanstanden "
                f"hatte. Die Freigabe ist als automatisch markiert. Die Position steht jetzt auf "
                f"„{stand}“.{wo}")
    if item.status == "vorschlag":
        n = sum(f.status == "vorgeschlagen" for f in item.findings)
        return (f"{_pos(item)}: {'Ein Fund wartet' if n == 1 else f'{n} Funde warten'} auf "
                f"Ihre Prüfung.{wo}")
    if item.status == "nicht_gefunden":
        mail_ = " Ein E-Mail-Entwurf an den Hersteller liegt bereit." if neue_entwuerfe else ""
        return f"{_pos(item)}: Der Agent hat nichts gefunden.{wo}{mail_}"
    stand = STAND_TEXT.get(artikel_status(item), artikel_status(item))
    return f"{_pos(item)}: Die Position steht jetzt auf „{stand}“.{wo}"


# Entscheidungen eines Menschen


def _entwuerfe_nach_entscheidung(item):
    """Steht die Position danach auf „nicht gefunden“, bekommt sie ihren Entwurf."""
    return email_entwurf.lege_entwuerfe_an(SimpleNamespace(items=[item]))


def korrigiere(item, daten, von):
    """Felder des Vertrags auf services/korrektur.korrigiere abbilden; leer = nicht geändert."""
    werte = {unser: str(daten[ihr]).strip() for ihr, unser in KORREKTUR.items()
             if str(daten.get(ihr) or "").strip()}
    try:
        neu = korrektur.korrigiere(item, {"by": von, **werte})
    except korrektur.Fehler as e:
        db.session.rollback()
        raise Fehler(str(e), e.code) from None
    db.session.commit()
    namen = _und([korrektur.FELDER[k.field] for k in neu])
    stand = STAND_TEXT.get(artikel_status(item), artikel_status(item))
    protokolliere(item.bom_id, f"{_pos(item)}: {von} hat {namen} geändert. Die Position steht "
                               f"jetzt auf „{stand}“.", item.id)


def freigeben(item, fund_id, von, doc_type=None):
    """doc_type: Dokumentart, wenn der Mensch die erkannte bei der Freigabe korrigiert."""
    finding = next((f for f in item.findings if f.id == fund_id), None)
    if finding is None:
        raise Fehler("Diesen Fund gibt es bei der Position nicht mehr. Bitte laden Sie die Seite "
                     "neu.")
    if doc_type is not None and doc_type not in DOC_TYPES:
        raise Fehler("Diese Dokumentart kennt der Agent nicht. Bitte wählen Sie eine aus der "
                     "Liste.")
    try:
        freigabe.entscheide(finding, "freigeben", von, current_app.config["ATEX_DOCS"], doc_type)
    except freigabe.FreigabeFehler as e:
        db.session.rollback()
        raise Fehler(str(e), 409) from None
    _entwuerfe_nach_entscheidung(item)
    db.session.commit()
    datei_ = lesbar(PurePosixPath(finding.file_path or "").name) or finding.url or "den Fund"
    protokolliere(item.bom_id, f"{_pos(item)}: {von} hat „{datei_}“ freigegeben. Das Dokument "
                               "steht jetzt in der Datenbank.", item.id)


def ablehnen(item, grund, von, fund_id=None):
    """Lehnt den Fund ab (ohne fund_id alle offenen) und legt den Entwurf an den Hersteller an,
    mit dem Grund im Text."""
    offen = [f for f in item.findings if f.status == "vorgeschlagen"
             and (fund_id is None or f.id == fund_id)]
    if not offen:
        raise Fehler("Zu dieser Position gibt es keinen offenen Fund, den Sie ablehnen könnten.",
                     409)
    try:
        for f in offen:
            freigabe.entscheide(f, "ablehnen", von, current_app.config["ATEX_DOCS"])
    except freigabe.FreigabeFehler as e:
        db.session.rollback()
        raise Fehler(str(e), 409) from None
    for entwurf in _entwuerfe_nach_entscheidung(item):
        angaben = email_entwurf.angaben(item)
        zeile = f"{GRUND}{grund}\n\n"
        entwurf.body = (entwurf.body[:-len(angaben)] + zeile + angaben
                        if entwurf.body.endswith(angaben) else f"{entwurf.body}\n\n{zeile}")
    db.session.commit()
    mail_ = (" Ein E-Mail-Entwurf an den Hersteller liegt bereit."
             if item.status == "nicht_gefunden" else "")
    was = "den Fund" if len(offen) == 1 else f"{len(offen)} Funde"
    protokolliere(item.bom_id, f"{_pos(item)}: {von} hat {was} abgelehnt. Der Grund ist "
                               f"„{grund.rstrip('.')}“.{mail_}", item.id)


def zertifikat(item, inhalt, dateiname, herkunft, von, doc_type=None):
    """PDF einfügen. Ohne Dokumentart geht es als Vorschlag in die Prüfung (wie im Vertrag),
    auch bei „manuell“; mit Dokumentart und „manuell“ ist es gleich freigegeben (M18)."""
    wurzel = Path(current_app.config["ATEX_DOCS"])
    sofort = herkunft == "manuell" and doc_type
    quelle = "manuell" if sofort else "hersteller"  # „hersteller“: Vorschlag, wartet auf Prüfung
    try:
        finding = hochladen.lade_hoch(item, inhalt, dateiname, quelle, von, doc_type, wurzel)
    except hochladen.UploadFehler as e:
        db.session.rollback()
        raise Fehler(str(e), e.code) from None
    except freigabe.FreigabeFehler as e:
        db.session.rollback()
        raise Fehler(str(e), 409) from None
    finding.source = herkunft
    db.session.commit()
    was = "Es ist damit freigegeben." if sofort else "Es wartet jetzt auf Ihre Prüfung."
    woher = "als Antwort des Herstellers" if herkunft == "hersteller" else "von Hand"
    protokolliere(item.bom_id, f"{_pos(item)}: {von} hat das Dokument „{dateiname}“ {woher} "
                               f"eingefügt. {was}", item.id)
    return finding


# E-Mails


def bearbeite_mail(entwurf, daten):
    if entwurf.status != "entwurf":
        raise Fehler("Diese E-Mail ist schon freigegeben oder gesendet und lässt sich nicht mehr "
                     "ändern.", 409)
    an = daten.get("an")
    erlaubt = {entwurf.to_address, email_entwurf.empfaenger(entwurf)["empfaenger_hersteller"]}
    if an is not None and str(an).strip() not in erlaubt:
        raise Fehler("Den Empfänger können Sie hier nicht ändern. Bitte ändern Sie die Adresse "
                     "in den Stammdaten bei den Herstellerkontakten.")
    for feld, unser in (("betreff", "subject"), ("text", "body")):
        if feld in daten:
            wert = daten[feld]
            if not isinstance(wert, str) or not wert.strip():
                raise Fehler(f"{'Der Betreff' if feld == 'betreff' else 'Der Text'} darf nicht "
                             "leer sein. Bitte tragen Sie etwas ein.")
            setattr(entwurf, unser, wert.strip() if feld == "betreff" else wert)
    db.session.commit()


def _sende(entwurf, von):
    """Freigabe durch den Menschen, der „senden“ drückt, dann Versand (Test-Adresse)."""
    if entwurf.status == "entwurf":
        entwurf.status, entwurf.approved_by = "freigegeben", von
    try:
        email_entwurf.sende(entwurf)
    except email_entwurf.SendeFehler as e:  # 409 ohne Herstelleradresse (KontaktFehlt)
        db.session.rollback()
        raise Fehler(str(e), e.code) from None


def sende_mail(entwurf, von):
    if entwurf.status == "gesendet":
        raise Fehler("Diese E-Mail ist schon gesendet.", 409)
    _sende(entwurf, von)
    db.session.commit()
    item = entwurf.item
    an = email_entwurf.empfaenger(entwurf)["empfaenger_hersteller"]
    wohin = (f"Sie ging an {an}." if an == entwurf.to_address
             else f"Gedacht ist sie für {an}, in dieser Umgebung ging sie an das Test-Postfach "
                  f"{entwurf.to_address}.")
    protokolliere(item.bom_id, f"{_pos(item)}: {von} hat die E-Mail an den Hersteller "
                               f"freigegeben und gesendet. {wohin}", item.id)


@contextmanager
def _frist_vorbei():
    """Demo (OBERFLAECHE_DEMO_48H): so tun, als wäre die Frist ERINNERUNG_NACH_STUNDEN um."""
    cfg = current_app.config
    if not cfg.get("OBERFLAECHE_DEMO_48H"):
        yield
        return
    alt = cfg["ERINNERUNG_NACH_STUNDEN"]
    cfg["ERINNERUNG_NACH_STUNDEN"] = 0
    try:
        yield
    finally:
        cfg["ERINNERUNG_NACH_STUNDEN"] = alt


def erinnere(entwurf, von):
    """Der nächste Schritt nach services/erinnerung.py: Erinnerung (sofort gesendet) oder
    Eskalation an den Einkauf. Ein offener Erinnerungs-Entwurf wird gesendet."""
    erste, kette = _kette(entwurf)
    item = erste.item
    if erste.status != "gesendet":
        raise Fehler("Die Anfrage ist noch nicht gesendet. Bitte senden Sie zuerst die Anfrage.",
                     409)
    wartend = next((e for e in kette if e.status != "gesendet"), None)
    if wartend is not None:
        _sende(wartend, von)
        was = f"{von} hat die Erinnerung an den Hersteller gesendet."
    else:
        gesendet = sorted((e for e in kette if e.status == "gesendet"), key=lambda e: e.sent_at)
        letzte = gesendet[-1]
        with _frist_vorbei():
            schritt_ = erinnerung.naechster_schritt(letzte)
            try:
                if schritt_["next_step"] == "erinnern":
                    neu = erinnerung.lege_erinnerung_an(letzte)
                    db.session.flush()
                    _sende(neu, von)
                    was = (f"Der Hersteller hat nicht geantwortet. {von} hat ihm eine "
                           "Erinnerung gesendet.")
                elif schritt_["next_step"] == "eskalieren":
                    erinnerung.eskaliere(letzte, von)
                    was = (f"Der Hersteller hat auch auf die Erinnerung nicht geantwortet. {von} "
                           "hat die Anfrage an den Einkauf eskaliert.")
                else:
                    raise Fehler(f"Das geht gerade nicht. {schritt_['step_note']}", 409)
            except erinnerung.SchrittFehler as e:
                db.session.rollback()
                raise Fehler(str(e), 409) from None
    db.session.commit()
    protokolliere(item.bom_id, f"{_pos(item)}: {was}", item.id)


def sende_maengelliste(bom, von):
    """Mängelliste an den Einkauf: anlegen (services/korrektur.py), freigeben, senden."""
    anzahl = len(korrektur.maengel(bom))
    try:
        liste, _ = korrektur.lege_maengelliste_an(bom)
    except korrektur.Fehler as e:
        raise Fehler(str(e), e.code) from None
    liste.status, liste.approved_by = "freigegeben", von
    try:
        korrektur.sende_maengelliste(liste)
    except email_entwurf.SendeFehler as e:
        db.session.rollback()
        raise Fehler(str(e), 502) from None
    db.session.commit()
    protokolliere(bom.id, f"{von} hat die Mängelliste mit "
                          f"{_anzahl(anzahl, 'Position', 'Positionen')} freigegeben und an den "
                          f"Einkauf gesendet ({liste.to_address}).")
    return {"ok": True, "anzahl": anzahl, "id": liste.id}


# Ausgabe und Dateien


def export(bom, zwischenstand=False):
    """(Inhalt, Dateiname) der Kundenliste; Fehler 409, solange etwas offen ist (M12).

    zwischenstand: dieselbe Mappe, als unvollständig markiert, mit der Liste, was fehlt; immer
    abrufbar, nicht zur Weitergabe an den Kunden (Entscheidung des Teams)."""
    status.aktualisiere_alle(bom)
    db.session.commit()
    offen = _offen(bom)
    if zwischenstand:
        inhalt = excel_export.zwischenstand(bom)
        protokolliere(bom.id, f"Der Zwischenstand ist als Excel-Datei ausgegeben, "
                              f"{_anzahl(len(offen), 'Position ist', 'Positionen sind')} noch "
                              "offen. Er ist nicht für den Kunden bestimmt.")
        return inhalt, excel_export.dateiname(bom, "Zwischenstand_Ex-Dokumente")
    if offen:
        n = f"{len(offen)} Position ist" if len(offen) == 1 else f"{len(offen)} Positionen sind"
        raise Fehler(f"Noch {n} offen, deshalb gibt es die Kundenliste noch nicht. Den "
                     "Zwischenstand mit der Liste, was fehlt, können Sie jederzeit "
                     "herunterladen.", 409)
    inhalt = excel_export.kundenliste(bom)
    protokolliere(bom.id, "Die Kundenliste ist als Excel-Datei ausgegeben.")
    return inhalt, excel_export.dateiname(bom, "Ex-Dokumente")


def datei(pfad, markiert=None):
    """(Pfad, markierte PDF oder None) für /files/<pfad>, nur PDFs innerhalb von ATEX_DOCS.

    markiert: id eines Vorschlags zu genau dieser Datei, dann mit Fundstelle und Belegen."""
    basis = Path(current_app.config["ATEX_DOCS"]).resolve()
    ziel = (basis / pfad).resolve()
    if not ziel.is_relative_to(basis) or not ziel.is_file() or ziel.suffix.lower() != ".pdf":
        raise Fehler("Diese Datei liegt nicht im Ordner der Ex-Dokumente.", 404)
    if markiert:
        finding = db.session.get(Finding, markiert)
        if finding is not None and freigabe.datei(finding, basis) == ziel:
            try:
                return ziel, freigabe.markierte_pdf(ziel, freigabe.stellen(finding))
            except RuntimeError:  # kaputtes oder verschlüsseltes PDF: ohne Markierung
                pass
    return ziel, None
