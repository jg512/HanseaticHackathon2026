"""Ansichten, die nur lesen (GET): Stand frisch ableiten, ohne zu schreiben, mit wenigen Anfragen.

Die Übersicht, die Vorgangsseite und die EX-Bauteil-Liste leiten den Stand jeder Position frisch
ab (status.aktualisiere), denn Stammdaten gelten für alle Vorgänge: eine Freigabe in einem
Vorgang ändert den Stand in anderen. Bisher geschah das mit Speichern (commit) in jedem GET, und
jede Position lud ihr Bauteil, dessen geprüfte Dokumente, deren Sprachen, ihre Funde, E-Mails,
den Hersteller und seinen Kontakt einzeln nach. Bei 9 Vorgängen mit je 30 Positionen waren das
rund 3700 SQL-Anfragen je Abruf von /api/vorgaenge, davon über 100 schreibende.

nur_lesen() ändert das für die Dauer einer Ansicht:

- Der Stand wird nur im Speicher abgeleitet. Kein automatisches Speichern (no_autoflush), am Ende
  wird alles verworfen (rollback). Gespeichert wird der Stand von den schreibenden Anfragen
  (Freigabe, Stammdaten, Kontakte …), wie in status.py beschrieben.
- Bauteile mit geprüften Dokumenten und Sprachen sowie Hersteller mit Kontakt werden je Anfrage
  einmal gesammelt geladen (vorladen) und dann aus flask.g gelesen (teil, hersteller_kontakt).
- Vorgänge kommen mit Positionen, Funden und E-Mails in einem Zug (lade, vorgaenge). Belege und
  Angaben der Funde (JSON, groß) lädt erst, wer sie braucht: die Prüfseite (lade_funde).

Außerhalb von nur_lesen() fragen teil() und hersteller_kontakt() wie bisher die Datenbank, damit
eine schreibende Anfrage nie einen veralteten Zwischenstand sieht.
"""

from contextlib import contextmanager

from flask import g
from sqlalchemy import inspect
from sqlalchemy.orm import defer, selectinload, undefer

from ..db import db
from ..models import Bom, BomItem, Document, Finding, Manufacturer, Part, PartDocument
from ..models.kontakt import HerstellerKontakt

_SCHLUESSEL = "_lesepuffer"


def _puffer():
    return g.get(_SCHLUESSEL)


@contextmanager
def nur_lesen():
    """Für GET-Ansichten (siehe oben). Verschachtelt gilt der äußere Rahmen."""
    if _puffer() is not None:
        yield
        return
    s = db.session
    if s.new or s.dirty or s.deleted:  # sonst verwürfe das rollback am Ende fremde Änderungen
        raise RuntimeError("nur_lesen() mit ungespeicherten Änderungen aufgerufen")
    setattr(g, _SCHLUESSEL, {"teile": {}, "hersteller": {}})
    try:
        with s.no_autoflush:
            yield
    finally:
        g.pop(_SCHLUESSEL, None)
        s.rollback()  # im Speicher abgeleitete Stände verwerfen, nichts wird geschrieben


# --- Vorgänge mit Positionen, Funden und E-Mails ---------------------------------------------

_GROSS = (Finding.belege, Finding.angaben)  # JSON je Fund, nur die Prüfseite zeigt sie


def _optionen():
    items = selectinload(Bom.items)
    return (items.selectinload(BomItem.findings).options(*(defer(c) for c in _GROSS)),
            items.selectinload(BomItem.email_drafts))


def vorgaenge(*sortierung, meldungen=False):
    """Alle Vorgänge, ohne Angabe neueste zuerst, mit Positionen, Funden und E-Mails;
    Stammdaten und Kontakte dazu vorgeladen. meldungen: auch Meldungen und Fragen (Bom.to_dict)."""
    sortierung = sortierung or (Bom.created_at.desc(), Bom.id.desc())
    optionen = _optionen() + ((selectinload(Bom.issues), selectinload(Bom.questions))
                              if meldungen else ())
    boms = db.session.scalars(db.select(Bom).order_by(*sortierung).options(*optionen)).all()
    vorladen([i for b in boms for i in b.items])
    return boms


def lade(bom):
    """Positionen, Funde und E-Mails des Vorgangs in einem Zug, dazu Stammdaten und Kontakte."""
    if "items" in inspect(bom).unloaded:
        db.session.scalars(db.select(Bom).where(Bom.id == bom.id).options(*_optionen())).all()
    vorladen(bom.items)
    return bom


def lade_funde(item):
    """Funde der Position mit Belegen und Angaben, in einer Anfrage statt je Fund und Spalte."""
    db.session.scalars(db.select(Finding).where(Finding.bom_item_id == item.id)
                       .options(*(undefer(c) for c in _GROSS))).all()


# --- Stammdaten und Kontakte je Anfrage ------------------------------------------------------

def vorladen(items):
    """Bauteile und Hersteller dieser Positionen gesammelt laden (nur in nur_lesen())."""
    if _puffer() is None:
        return
    _lade_teile({i.profile_no for i in items if i.profile_no})
    _lade_hersteller({i.manufacturer for i in items if i.manufacturer})


def _lade_teile(profile_nos):
    teile = _puffer()["teile"]
    fehlen = [p for p in profile_nos if p not in teile]
    if not fehlen:
        return
    dokumente = selectinload(Part.documents).selectinload(PartDocument.document)
    gefunden = db.session.scalars(db.select(Part).where(Part.profile_no.in_(fehlen)).options(
        dokumente.selectinload(Document.languages))).all()
    teile.update(dict.fromkeys(fehlen))
    teile.update({p.profile_no: p for p in gefunden})


def teil(profile_no):
    """Bauteil der Stammdaten zur PRO.FILE-Nr. oder None; in nur_lesen() aus dem Puffer."""
    if not profile_no:
        return None
    if _puffer() is None:
        return db.session.scalar(db.select(Part).filter_by(profile_no=profile_no))
    _lade_teile({profile_no})
    return _puffer()["teile"][profile_no]


def _lade_hersteller(namen):
    from .freigabe import normalisiere  # freigabe -> status -> kontakte -> dieses Modul
    hersteller = _puffer()["hersteller"]
    fehlen = {normalisiere(n) for n in namen} - set(hersteller) - {"", None}
    if not fehlen:
        return
    gefunden = db.session.scalars(db.select(Manufacturer)
                                  .where(Manufacturer.normalized_name.in_(fehlen))).all()
    kontakte = {k.manufacturer_id: k for k in db.session.scalars(
        db.select(HerstellerKontakt).where(HerstellerKontakt.manufacturer_id.in_(
            [m.id for m in gefunden])))} if gefunden else {}
    hersteller.update(dict.fromkeys(fehlen, (None, None)))
    hersteller.update({m.normalized_name: (m, kontakte.get(m.id)) for m in gefunden})


def hersteller_kontakt(name):
    """(Hersteller der Stammdaten, sein Kontakt) zum Namen, je None, wenn es ihn nicht gibt."""
    from .freigabe import normalisiere
    norm = normalisiere(name)
    if not norm:
        return None, None
    if _puffer() is None:
        m = db.session.scalar(db.select(Manufacturer).filter_by(normalized_name=norm))
        return m, (db.session.get(HerstellerKontakt, m.id) if m else None)
    _lade_hersteller({name})
    return _puffer()["hersteller"][norm]
