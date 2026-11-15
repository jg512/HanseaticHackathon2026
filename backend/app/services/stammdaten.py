"""Stammdaten ansehen und von Hand pflegen (M18): Bauteile, Dokumente, Zuordnungen, Hersteller.

Bisher schreibt nur die Freigabe (services/freigabe.py) in die Stammdaten. Hier sieht ein Mensch
sie und korrigiert sie. Regeln:

- Jede Änderung trägt einen Namen (by). Sie landet mit altem und neuem Wert in der Tabelle
  stammdaten_aenderung, die geänderte Zeile bekommt changed_by.
- Nur ein Mensch setzt eine Zuordnung auf „geprueft“, mit Name und Zeit (M3).
- Zuordnung lösen entfernt die Zeile in part_document; die Spur bleibt im Änderungsprotokoll.
  Ein Dokument wird nur gelöscht, wenn kein Bauteil darauf zeigt. Dateien auf der Platte bleiben.
- Nach jeder Änderung, die die Vollständigkeit ändern kann, wird der Stand der Positionen mit
  derselben PRO.FILE-Nr. neu abgeleitet (status.aktualisiere).

Texte aus der Oberfläche sind fremde Eingaben: getrimmt, in der Länge begrenzt, nur gespeichert.
Das Speichern (commit) macht der Aufrufer.
"""

from datetime import date, datetime, timezone

from ..db import db
from ..models import (BomItem, Document, DocumentLanguage, Manufacturer, Part, PartDocument,
                      StammdatenAenderung)
from ..models.stammdaten import DOC_TYPES, LANGUAGES, STATUS
from . import status
from .erledigt import Fehler, text, von
from .freigabe import normalisiere

# Textfelder eines Bauteils, die ein Mensch ändern darf, mit Namen für Meldungen.
# Dazu kommen manufacturer (Name des Herstellers) und review_needed (ja/nein).
BAUTEIL_FELDER = {"name_de": "Bezeichnung (de)", "name_en": "Bezeichnung (en)",
                  "manufacturer_article_no": "Hersteller-Artikelnr.",
                  "ex_marking_raw": "Ex-Kennzeichnung", "manufacturer_url": "Hersteller-Link"}
DOKUMENT_FELDER = {"doc_type": "Dokumentart", "certificate_no": "Zertifikats-Nr.",
                   "valid_until": "Gültig bis", "languages": "Sprachen"}
MAX_WERT = 500
# Texte für Anwender
NAME_LEER = ("Der Name des Herstellers ist leer. Bitte geben Sie ihn mit Buchstaben oder Ziffern "
             "ein.")
PRUEF_TEXT = {"ungeprueft": "ungeprüft", "ki_geprueft": "KI-geprüft", "geprueft": "geprüft"}
MAX_AENDERUNGEN = 500


def _jetzt():
    return datetime.now(timezone.utc)


def _iso(wert):
    return wert.isoformat() if wert else None


def _als_text(wert):
    """Wert für das Änderungsprotokoll: leer wird None, ja/nein, Datum als JJJJ-MM-TT."""
    if wert is None or wert == "":
        return None
    if isinstance(wert, bool):
        return "ja" if wert else "nein"
    if isinstance(wert, date):
        return wert.isoformat()
    return str(wert)


def _protokoll(name, tabelle, schluessel, feld, alt, neu, note=None, part_id=None,
               document_id=None):
    db.session.add(StammdatenAenderung(
        table_name=tabelle, row_key=str(schluessel), part_id=part_id, document_id=document_id,
        field=feld, old_value=_als_text(alt), new_value=_als_text(neu), changed_by=name,
        note=note))


def _kopf(daten, erlaubt):
    """Name und Notiz der Anfrage; Fehler 400 bei unbekannten Feldern."""
    name = von(daten)
    note = text(daten, "note", "Notiz", pflicht=False)
    if unbekannt := sorted(set(daten) - set(erlaubt) - {"by", "note"}):
        raise Fehler("Diese Angabe lässt sich hier nicht speichern. Bitte laden Sie die Seite "
                     "neu.", 400)
    return name, note


def _like(q):
    """Suchbegriff für ILIKE; % und _ gelten als Zeichen, nicht als Platzhalter."""
    q = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{q}%"


# --- Ansichten ---------------------------------------------------------------------------------

def hersteller_dict(m, anzahl=None):
    d = {"id": m.id, "name": m.name, "normalized_name": m.normalized_name}
    if anzahl is not None:
        d["part_count"] = anzahl
    return d


def zuordnungen(doc):
    return db.session.scalars(db.select(PartDocument).filter_by(document_id=doc.id)
                              .order_by(PartDocument.part_id)).all()


def dokument_dict(doc, mit_bauteilen=True):
    d = {"id": doc.id, "doc_type": doc.doc_type, "file_path": doc.file_path, "url": doc.url,
         "file_hash": doc.file_hash, "file_exists": doc.file_exists,
         "checked_at": _iso(doc.checked_at), "certificate_no": doc.certificate_no,
         "valid_until": _iso(doc.valid_until),
         "languages": sorted(s.language for s in doc.languages), "changed_by": doc.changed_by}
    if mit_bauteilen:
        d["parts"] = [{"id": pd.part.id, "profile_no": pd.part.profile_no,
                       "name_de": pd.part.name_de, "link_status": pd.status}
                      for pd in zuordnungen(doc)]
    return d


def zuordnung_dict(pd):
    """Ein Dokument des Bauteils mit dem Prüfstatus der Zuordnung."""
    return dokument_dict(pd.document, mit_bauteilen=False) | {
        "link_status": pd.status, "link_source": pd.source, "verified_by": pd.verified_by,
        "verified_at": _iso(pd.verified_at), "link_changed_by": pd.changed_by}


def bauteil_dict(part, detail=False):
    geprueft = {pd.document.doc_type for pd in part.documents if pd.status == "geprueft"}
    d = {"id": part.id, "profile_no": part.profile_no, "name_de": part.name_de,
         "name_en": part.name_en, "manufacturer_id": part.manufacturer_id,
         "manufacturer": part.manufacturer.name if part.manufacturer else None,
         "manufacturer_article_no": part.manufacturer_article_no,
         "ex_marking_raw": part.ex_marking_raw, "manufacturer_url": part.manufacturer_url,
         "legacy_last_modified": _iso(part.legacy_last_modified), "status": part.status,
         "review_needed": part.review_needed, "source": part.source,
         "changed_by": part.changed_by, "document_count": len(part.documents),
         "checked_count": sum(pd.status == "geprueft" for pd in part.documents),
         # Pflichtdokumente (M20), die nicht geprüft zugeordnet sind
         "missing_doc_types": [a for a in status.PFLICHT if a not in geprueft]}
    if detail:
        d["documents"] = [zuordnung_dict(pd)
                          for pd in sorted(part.documents, key=lambda pd: pd.document_id)]
        d["changes"] = [a.to_dict() for a in aenderungen_bauteil(part)]
        items = db.session.scalars(db.select(BomItem).filter_by(profile_no=part.profile_no)
                                   .order_by(BomItem.bom_id, BomItem.row_number))
        d["bom_items"] = [{"bom_id": i.bom_id, "item_id": i.id, "pos": i.pos,
                           "status": i.status} for i in items]
    return d


def bauteile(q=None, manufacturer_id=None, status_=None, review_needed=None):
    """Bauteile nach PRO.FILE-Nr. sortiert; q sucht in Nummer, Bezeichnung, Typ und Hersteller."""
    abfrage = (db.select(Part).outerjoin(Manufacturer, Part.manufacturer_id == Manufacturer.id)
               .order_by(Part.profile_no))
    if q and q.strip():
        muster = _like(q)
        abfrage = abfrage.where(db.or_(*(spalte.ilike(muster, escape="\\") for spalte in (
            Part.profile_no, Part.name_de, Part.name_en, Part.manufacturer_article_no,
            Manufacturer.name))))
    if manufacturer_id is not None:
        abfrage = abfrage.where(Part.manufacturer_id == manufacturer_id)
    if status_:
        abfrage = abfrage.where(Part.status == status_)
    if review_needed is not None:
        abfrage = abfrage.where(Part.review_needed == review_needed)
    return db.session.scalars(abfrage).all()


def dokumente(q=None, doc_type=None, ohne_bauteil=False):
    """Dokumente nach id; q sucht in Datei, URL und Zertifikats-Nr."""
    abfrage = db.select(Document).order_by(Document.id)
    if q and q.strip():
        muster = _like(q)
        abfrage = abfrage.where(db.or_(*(spalte.ilike(muster, escape="\\") for spalte in (
            Document.file_path, Document.url, Document.certificate_no))))
    if doc_type:
        abfrage = abfrage.where(Document.doc_type == doc_type)
    if ohne_bauteil:
        abfrage = abfrage.where(~db.exists().where(PartDocument.document_id == Document.id))
    return db.session.scalars(abfrage).all()


def hersteller():
    """Alle Hersteller mit der Zahl ihrer Bauteile, nach Name."""
    anzahl = db.func.count(Part.id)
    zeilen = db.session.execute(
        db.select(Manufacturer, anzahl).outerjoin(Part, Part.manufacturer_id == Manufacturer.id)
        .group_by(Manufacturer.id).order_by(db.func.lower(Manufacturer.name)))
    # Herstellerkontakte (services/kontakte.py): Adresse und ob sie fehlt
    from ..models import HerstellerKontakt
    adressen = {k.manufacturer_id: k.email for k in db.session.scalars(db.select(HerstellerKontakt))}
    return [hersteller_dict(m, n) | {"email": adressen.get(m.id),
                                     "kontakt_fehlt": not adressen.get(m.id)}
            for m, n in zeilen]


def aenderungen_bauteil(part):
    """Änderungen am Bauteil, an seinen Zuordnungen und an seinen jetzigen Dokumenten."""
    A = StammdatenAenderung
    bedingung = A.part_id == part.id
    if doc_ids := [pd.document_id for pd in part.documents]:
        bedingung = db.or_(bedingung, db.and_(A.table_name == "document",
                                              A.document_id.in_(doc_ids)))
    return db.session.scalars(db.select(A).where(bedingung).order_by(A.id.desc())).all()


def aenderungen(tabelle=None, document_id=None, limit=200):
    A = StammdatenAenderung
    abfrage = db.select(A).order_by(A.id.desc()).limit(min(max(limit, 1), MAX_AENDERUNGEN))
    if tabelle:
        abfrage = abfrage.where(A.table_name == tabelle)
    if document_id is not None:
        abfrage = abfrage.where(A.document_id == document_id)
    return db.session.scalars(abfrage).all()


# --- Stand der Positionen ----------------------------------------------------------------------

def aktualisiere_positionen(profile_nos):
    """Stand aller Positionen mit diesen PRO.FILE-Nummern neu ableiten; gibt die Anzahl zurück."""
    profile_nos = {p for p in profile_nos if p}
    if not profile_nos:
        return 0
    db.session.flush()
    items = db.session.scalars(db.select(BomItem).where(BomItem.profile_no.in_(profile_nos))).all()
    for item in items:
        status.aktualisiere(item)
    return len(items)


# --- Bauteile ----------------------------------------------------------------------------------

def _hersteller_zu(name, wer, note):
    """Hersteller zum Namen finden oder anlegen (wie bei der Freigabe); leer: None."""
    if not name:
        return None
    norm = normalisiere(name)
    if not norm:
        raise Fehler(NAME_LEER, 400)
    m = db.session.scalar(db.select(Manufacturer).filter_by(normalized_name=norm))
    if m is None:
        m = Manufacturer(name=name, normalized_name=norm)
        db.session.add(m)
        db.session.flush()
        _protokoll(wer, "manufacturer", m.id, "angelegt", None, name, note)
    return m


def _setze_bauteil(part, daten, name, note):
    """Übernimmt geänderte Felder aus daten, je mit Eintrag im Protokoll; gibt die Anzahl zurück."""
    n = 0
    for feld, titel in BAUTEIL_FELDER.items():
        if feld not in daten:
            continue
        neu = text(daten, feld, titel, pflicht=False, laenge=MAX_WERT)
        if feld == "manufacturer_url" and neu and not neu.lower().startswith(("http://", "https://")):
            raise Fehler("Der Hersteller-Link ist keine Webadresse. Bitte geben Sie ihn mit "
                         "„https://“ am Anfang ein.", 400)
        if neu != getattr(part, feld):
            _protokoll(name, "part", part.id, feld, getattr(part, feld), neu, note, part.id)
            setattr(part, feld, neu)
            n += 1
    if "manufacturer" in daten:
        neu = _hersteller_zu(text(daten, "manufacturer", "Hersteller", pflicht=False,
                                  laenge=MAX_WERT), name, note)
        if neu is not part.manufacturer:
            _protokoll(name, "part", part.id, "manufacturer",
                       part.manufacturer.name if part.manufacturer else None,
                       neu.name if neu else None, note, part.id)
            part.manufacturer = neu
            n += 1
    if "review_needed" in daten:
        neu = daten["review_needed"]
        if not isinstance(neu, bool):
            raise Fehler("Bei „Prüfung nötig“ geht nur ja oder nein. Bitte setzen oder "
                         "entfernen Sie den Haken.", 400)
        if neu != part.review_needed:
            _protokoll(name, "part", part.id, "review_needed", part.review_needed, neu, note,
                       part.id)
            part.review_needed = neu
            n += 1
    return n


def lege_bauteil_an(daten):
    """Neues Bauteil von Hand: profile_no Pflicht, sonst Felder wie beim Ändern."""
    name, note = _kopf(daten, {"profile_no", *BAUTEIL_FELDER, "manufacturer", "review_needed"})
    profile_no = text(daten, "profile_no", "PRO.FILE-Nr.", laenge=MAX_WERT)
    if db.session.scalar(db.select(Part).filter_by(profile_no=profile_no)):
        raise Fehler(f"Ein Bauteil mit der PRO.FILE-Nr. {profile_no} gibt es schon. Bitte "
                     "ändern Sie dort, was fehlt.", 409)
    part = Part(profile_no=profile_no, source="manual", status="ungeprueft",
                review_needed=False, changed_by=name)
    db.session.add(part)
    db.session.flush()
    _protokoll(name, "part", part.id, "angelegt", None, profile_no, note, part.id)
    _setze_bauteil(part, daten, name, note)
    db.session.flush()
    return part


def aendere_bauteil(part, daten):
    """Felder aus BAUTEIL_FELDER, manufacturer, review_needed. Die PRO.FILE-Nr. bleibt fest."""
    name, note = _kopf(daten, {*BAUTEIL_FELDER, "manufacturer", "review_needed"})
    if not _setze_bauteil(part, daten, name, note):
        raise Fehler("Diese Werte stehen schon so da. Es gab nichts zu ändern.", 400)
    part.changed_by = name


# --- Zuordnungen -------------------------------------------------------------------------------

def _schluessel(pd):
    return f"{pd.part_id}/{pd.document_id}"


def _beschreibe_zuordnung(pd):
    doc = pd.document
    teile = [doc.doc_type, doc.file_path or doc.url or f"Dokument {doc.id}", f"Status {pd.status}"]
    if pd.verified_by:
        teile.append(f"geprüft von {pd.verified_by}"
                     + (f" am {pd.verified_at:%d.%m.%Y}" if pd.verified_at else ""))
    return "; ".join(teile)


def _setze_status(pd, neu, name):
    """Prüfstatus setzen; „geprueft“ nur mit Name und Zeit (M3), sonst beide leer."""
    if neu not in STATUS:
        raise Fehler("Diesen Prüfstatus gibt es nicht. Bitte wählen Sie ungeprüft, KI-geprüft "
                     "oder geprüft.", 400)
    pd.status = neu
    pd.verified_by, pd.verified_at = (name, _jetzt()) if neu == "geprueft" else (None, None)
    pd.changed_by = name


def ordne_zu(part, daten):
    """Vorhandenes Dokument dem Bauteil zuordnen. {"by", "document_id", "status" optional}."""
    name, note = _kopf(daten, {"document_id", "status"})
    doc_id = daten.get("document_id")
    if type(doc_id) is not int:
        raise Fehler("Bitte wählen Sie zuerst ein Dokument aus.", 400)
    doc = db.session.get(Document, doc_id)
    if doc is None:
        raise Fehler("Dieses Dokument gibt es nicht mehr. Bitte laden Sie die Liste neu.", 404)
    if db.session.get(PartDocument, (part.id, doc.id)):
        raise Fehler("Das Dokument ist dem Bauteil schon zugeordnet.", 409)
    pd = PartDocument(part=part, document=doc, source="manual", changed_by=name)
    _setze_status(pd, daten.get("status", "ungeprueft"), name)
    db.session.add(pd)
    db.session.flush()
    _protokoll(name, "part_document", _schluessel(pd), "zuordnung", None,
               _beschreibe_zuordnung(pd), note, part.id, doc.id)
    return aktualisiere_positionen({part.profile_no})


def setze_pruefstatus(pd, daten):
    """Prüfstatus einer Zuordnung ändern. {"by", "status", "note" optional}."""
    name, note = _kopf(daten, {"status"})
    alt = pd.status
    if daten.get("status") == alt:
        raise Fehler(f"Die Zuordnung steht schon auf „{PRUEF_TEXT.get(alt, alt)}“. Es gab "
                     "nichts zu ändern.", 400)
    _setze_status(pd, daten.get("status"), name)
    _protokoll(name, "part_document", _schluessel(pd), "status", alt, pd.status, note,
               pd.part_id, pd.document_id)
    return aktualisiere_positionen({pd.part.profile_no})


def loese(pd, daten):
    """Zuordnung entfernen; die Zeile verschwindet, die Spur bleibt im Protokoll."""
    name, note = _kopf(daten, set())
    part = pd.part
    _protokoll(name, "part_document", _schluessel(pd), "zuordnung", _beschreibe_zuordnung(pd),
               None, note, pd.part_id, pd.document_id)
    db.session.delete(pd)
    db.session.flush()
    db.session.expire(part, ["documents"])
    return aktualisiere_positionen({part.profile_no})


# --- Dokumente ---------------------------------------------------------------------------------

def _datum(wert):
    """JJJJ-MM-TT, auch TT.MM.JJJJ, oder leer (None)."""
    if wert is None or (isinstance(wert, str) and not wert.strip()):
        return None
    if isinstance(wert, str):
        w = wert.strip()
        if len(teile := w.split(".")) == 3 and all(t.isdigit() for t in teile):  # „31.12.2030“
            w = f"{teile[2]}-{int(teile[1]):02d}-{int(teile[0]):02d}"
        try:
            return date.fromisoformat(w[:10])
        except ValueError:
            pass
    raise Fehler("Das Datum bei „Gültig bis“ ließ sich nicht lesen. Bitte geben Sie es zum "
                 "Beispiel als 31.12.2030 ein oder lassen Sie das Feld leer.", 400)


def _sprachen(wert):
    if not isinstance(wert, list) or not all(isinstance(s, str) for s in wert):
        raise Fehler("Die Sprachen ließen sich nicht lesen. Bitte geben Sie Kürzel mit Komma "
                     "ein, zum Beispiel: de, en.", 400)
    neu = {s.strip().lower() for s in wert if s.strip()}
    if fremd := sorted(neu - set(LANGUAGES)):
        raise Fehler(f"Die Sprache „{', '.join(fremd)}“ kennt der Agent nicht. Möglich sind "
                     f"{', '.join(LANGUAGES)}.", 400)
    return neu


def aendere_dokument(doc, daten):
    """Felder aus DOKUMENT_FELDER. Gibt die Zahl der neu abgeleiteten Positionen zurück."""
    name, note = _kopf(daten, DOKUMENT_FELDER)
    neu = {}
    if "doc_type" in daten:
        if daten["doc_type"] not in DOC_TYPES:
            raise Fehler("Diese Dokumentart kennt der Agent nicht. Bitte wählen Sie eine aus "
                         "der Liste.", 400)
        neu["doc_type"] = daten["doc_type"]
    if "certificate_no" in daten:
        neu["certificate_no"] = text(daten, "certificate_no", "Zertifikats-Nr.", pflicht=False,
                                     laenge=MAX_WERT)
    if "valid_until" in daten:
        neu["valid_until"] = _datum(daten["valid_until"])

    n = 0
    for feld, wert in neu.items():
        if wert != getattr(doc, feld):
            _protokoll(name, "document", doc.id, feld, getattr(doc, feld), wert, note,
                       document_id=doc.id)
            setattr(doc, feld, wert)
            n += 1
    if "languages" in daten:
        sprachen = _sprachen(daten["languages"])
        alt = {s.language for s in doc.languages}
        if sprachen != alt:
            _protokoll(name, "document", doc.id, "languages", ",".join(sorted(alt)),
                       ",".join(sorted(sprachen)), note, document_id=doc.id)
            for s in [s for s in doc.languages if s.language not in sprachen]:
                doc.languages.remove(s)
            for s in sorted(sprachen - alt):
                doc.languages.append(DocumentLanguage(language=s))
            n += 1
    if not n:
        raise Fehler("Diese Werte stehen schon so da. Es gab nichts zu ändern.", 400)
    doc.changed_by = name
    # Art (M20) und Sprachen (Pflichtsprache, M8) entscheiden über die Vollständigkeit
    if "doc_type" in neu or "languages" in daten:
        return aktualisiere_positionen({pd.part.profile_no for pd in zuordnungen(doc)})
    return 0


def loesche_dokument(doc, daten):
    """Dokument aus der Datenbank löschen, nur ohne Zuordnung. Die Datei bleibt liegen."""
    name, note = _kopf(daten, set())
    if n := len(zuordnungen(doc)):
        bauteile = "einem Bauteil" if n == 1 else f"{n} Bauteilen"
        raise Fehler(f"Das Dokument gehört noch zu {bauteile}. Bitte lösen Sie zuerst die "
                     "Zuordnungen.", 409)
    beschreibung = "; ".join(x for x in (doc.doc_type, doc.file_path or doc.url,
                                         doc.certificate_no and f"Zertifikat {doc.certificate_no}")
                             if x)
    _protokoll(name, "document", doc.id, "geloescht", beschreibung, None, note,
               document_id=doc.id)
    db.session.delete(doc)  # Sprachen gehen mit (cascade), die Datei bleibt


# --- Hersteller --------------------------------------------------------------------------------

def aendere_hersteller(m, daten):
    """Namen eines Herstellers ändern. {"by", "name", "note" optional}."""
    name, note = _kopf(daten, {"name"})
    neu = text(daten, "name", "Name des Herstellers", laenge=MAX_WERT)
    norm = normalisiere(neu)
    if not norm:
        raise Fehler(NAME_LEER, 400)
    if neu == m.name:
        raise Fehler("Diese Werte stehen schon so da. Es gab nichts zu ändern.", 400)
    andere = db.session.scalar(db.select(Manufacturer).where(
        Manufacturer.normalized_name == norm, Manufacturer.id != m.id))
    if andere:
        raise Fehler(f"Den Hersteller „{andere.name}“ gibt es schon.", 409)
    _protokoll(name, "manufacturer", m.id, "name", m.name, neu, note)
    m.name, m.normalized_name = neu, norm
