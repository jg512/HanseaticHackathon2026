"""Bearbeiten in den Tabellen der Oberfläche: was die vorhandenen Dienste noch nicht ändern.

- Vorgang umbenennen (bom.name), mit Eintrag im Protokoll des Vorgangs.
- Position korrigieren mit den Feldern von services/korrektur.FELDER (dort auch Menge), mit
  Eintrag im Protokoll; die Regeln der Korrektur (neue Suche usw.) gelten unverändert.
- Aus dem PDF gelesene Werte eines Funds korrigieren, solange er unentschieden ist. Jede
  Korrektur steht mit Name, Zeit, altem und neuem Wert in der Tabelle finding_correction und im
  Protokoll des Vorgangs. Die Freigabe übernimmt danach die korrigierten Werte.

Weitere Werte eines Funds sind je ein Eintrag in FUND_FELDER: eine Spalte von Finding
(spalte=…) oder ein Schlüssel im JSON-Feld finding.angaben (angabe=…).

Fehler als Fehler(text, code); das Speichern (commit) macht der Aufrufer.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import PurePosixPath

from ..db import db
from ..models.bom import BomEvent
from ..models.stammdaten import DOC_TYPES
from . import korrektur
from .excel_export import ART_TEXT

MAX_NAME = 200
# Dokumentarten in Worten für das Protokoll
DOKUMENTART = {**ART_TEXT, "datenblatt": "Datenblatt", "sonstiges": "Sonstiges"}
MAX_WERT = 500


class Fehler(Exception):
    """Text für den Menschen und HTTP-Code."""

    def __init__(self, text, code=400):
        super().__init__(text)
        self.code = code


class FindingCorrection(db.Model):
    """Korrektur eines aus dem PDF gelesenen Werts eines Funds durch einen Menschen: wer, was, wann."""
    __tablename__ = "finding_correction"

    id = db.Column(db.Integer, primary_key=True)
    finding_id = db.Column(db.ForeignKey("finding.id", ondelete="CASCADE"), nullable=False,
                           index=True)
    field = db.Column(db.String, nullable=False)   # Schlüssel aus FUND_FELDER
    old_value = db.Column(db.String)
    new_value = db.Column(db.String)
    changed_by = db.Column(db.String, nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    note = db.Column(db.String)

    def to_dict(self):
        return {"id": self.id, "finding_id": self.finding_id, "field": self.field,
                "old_value": self.old_value, "new_value": self.new_value,
                "changed_by": self.changed_by, "changed_at": self.changed_at.isoformat(),
                "note": self.note}


def _jetzt():
    return datetime.now(timezone.utc)


def name(daten):
    """Name dessen, der ändert: „von“ oder „by“; ohne Namen Fehler 400."""
    for wert in (daten.get("von"), daten.get("by")):
        if isinstance(wert, str) and wert.strip():
            return wert.strip()[:MAX_NAME]
    raise Fehler("Ohne Ihren Namen wird nichts gespeichert. Bitte melden Sie sich an.")


def notiz(daten):
    wert = daten.get("note", daten.get("notiz"))
    if wert is not None and not isinstance(wert, str):
        raise Fehler("Die Notiz ließ sich nicht lesen. Bitte geben Sie sie als Text ein.")
    return (wert or "").strip()[:MAX_WERT] or None


def protokolliere(bom_id, text, item_id=None):
    db.session.add(BomEvent(bom_id=bom_id, bom_item_id=item_id, text=text))


def _pos(item):
    return f"Position {item.pos or item.row_number}"


def _und(teile):
    """„a“, „a und b“, „a, b und c“."""
    return teile[0] if len(teile) == 1 else ", ".join(teile[:-1]) + " und " + teile[-1]


# ---------- Vorgang ----------


def benenne_um(bom, daten):
    """Anfrage {"von", "name"}. Gibt (alt, neu) zurück; neu ist eindeutig, notfalls mit „ (2)“."""
    wer = name(daten)
    neu = daten.get("name")
    if not isinstance(neu, str) or not neu.strip():
        raise Fehler("Der Vorgang braucht einen Namen. Bitte geben Sie einen ein.")
    neu = neu.strip()
    if len(neu) > MAX_NAME:
        raise Fehler(f"Der Name ist zu lang. Bitte kürzen Sie ihn auf höchstens {MAX_NAME} Zeichen.")
    from .oberflaeche import eindeutiger_name, name as anzeigename
    alt = anzeigename(bom)
    if neu == alt:
        raise Fehler("Der Vorgang heißt schon so. Es gab nichts zu ändern.")
    # jeder Vorgang heißt anders: bei Gleichstand „ (2)“, „ (3)“ … (services/oberflaeche.py)
    neu = eindeutiger_name(neu, ohne_id=bom.id)
    if neu == alt:
        raise Fehler("Der Vorgang heißt schon so. Es gab nichts zu ändern.")
    bom.name = neu
    protokolliere(bom.id, f"{wer} hat den Vorgang von „{alt}“ in „{neu}“ umbenannt.")
    return alt, neu


# ---------- Position ----------


def korrigiere_position(item, daten):
    """Felder aus korrektur.FELDER (unsere Namen), dazu „von“/„by“ und „note“.

    Leere Werte lehnt korrektur.korrigiere ab (400). Gibt die neuen Korrekturen zurück.
    """
    wer = name(daten)
    werte = {f: daten[f] for f in korrektur.FELDER if f in daten}
    fremd = set(daten) - set(korrektur.FELDER) - {"von", "by", "note", "notiz"}
    if fremd:
        raise Fehler("Diese Angabe lässt sich hier nicht ändern. Ändern können Sie "
                     + _und(list(korrektur.FELDER.values())) + ".")
    for f, w in werte.items():
        if w is not None and not isinstance(w, str):
            werte[f] = str(w)
    leer = [korrektur.FELDER[f] for f, w in werte.items() if not (w or "").strip()]
    if leer:
        raise Fehler(f"{_und(leer)} darf nicht leer sein. Bitte tragen Sie den richtigen Wert ein.")
    lang = [korrektur.FELDER[f] for f, w in werte.items() if len(w.strip()) > korrektur.MAX_WERT]
    if lang:
        raise Fehler(f"Der Wert für {_und(lang)} ist zu lang. Bitte kürzen Sie ihn auf höchstens "
                     f"{korrektur.MAX_WERT} Zeichen.")
    if all(w.strip() == (getattr(item, f) or "") for f, w in werte.items()):
        raise Fehler("Diese Werte stehen schon so in der Stückliste. Es gab nichts zu ändern.")
    try:
        neu = korrektur.korrigiere(item, {"by": wer, "note": notiz(daten), **werte})
    except korrektur.Fehler as e:
        raise Fehler(str(e), e.code) from None
    teile = [f"{korrektur.FELDER[k.field]} von „{k.old_value or ''}“ auf „{k.new_value}“"
             for k in neu]
    protokolliere(item.bom_id, f"{_pos(item)}: {wer} hat {_und(teile)} geändert.", item.id)
    return neu


# ---------- Fund ----------


@dataclass
class Feld:
    """Ein korrigierbarer Wert eines Funds."""
    titel: str
    spalte: str = None        # Spalte von Finding
    angabe: str = None        # oder Schlüssel in finding.angaben (JSON)
    art: str = "text"         # text | auswahl | datum | sprachen
    optionen: tuple = field(default_factory=tuple)
    leer: bool = True         # darf geleert werden

    def lies(self, finding):
        if self.spalte:
            return getattr(finding, self.spalte)
        w = (getattr(finding, "angaben", None) or {}).get(self.angabe)
        if self.art == "liste":
            return "; ".join(self.eintrag_text(e) for e in w or []) or None
        if self.angabe == "gueltig_bis":
            return w.get("wert") if isinstance(w, dict) and w.get("status") == "datum" else None
        return w.get("wert") if isinstance(w, dict) else w

    def setze(self, finding, wert):
        if self.spalte:
            setattr(finding, self.spalte, wert)
            return
        angaben = dict(getattr(finding, "angaben", None) or {})
        alt = angaben.get(self.angabe)
        if self.art == "liste":
            angaben[self.angabe] = self.liste_neu(alt or [], wert)
        elif self.angabe == "gueltig_bis":
            # von Hand: die Fundstelle im PDF gilt nicht mehr für diesen Wert
            rest = {k: v for k, v in (alt or {}).items() if k not in ("seite", "text", "ocr")}
            angaben[self.angabe] = {**rest, "status": "datum" if wert else "keine_angabe",
                                    "wert": wert, "von_hand": True}
        elif not wert:
            angaben[self.angabe] = None
        else:
            rest = {k: v for k, v in (alt or {}).items() if k not in ("seite", "text", "ocr")} \
                if isinstance(alt, dict) else {}
            angaben[self.angabe] = {**rest, "wert": wert, "von_hand": True}
        finding.angaben = angaben  # neu zuweisen, damit das JSON-Feld als geändert gilt

    # Listen aus finding.angaben (Richtlinien, Normen, Stellen, …): als Text mit „;“ getrennt
    def eintrag_text(self, e):
        if self.angabe == "stellen":
            return (e.get("name") or "") + (f" ({e['nummer']})" if e.get("nummer") else "")
        return str(e.get("wert") or "")

    def liste_neu(self, alt, wert):
        """Neue Liste aus dem Text; Einträge, die es so schon gab, behalten ihre Fundstelle."""
        bekannt = {self.eintrag_text(e): e for e in alt}
        neu = []
        for teil in [t.strip() for t in re.split(r";|,\s+", wert or "") if t.strip()]:
            if teil in bekannt:
                neu.append(bekannt[teil])
                continue
            e = {"wert": teil, "von_hand": True}
            if self.angabe == "stellen":
                m = re.fullmatch(r"(.*?)\s*\(?(\d{4})\)?", teil)
                e = {"name": m[1].strip() if m and m[1].strip() else teil,
                     "nummer": m[2] if m else None, "rolle": "genannt", "von_hand": True}
            elif self.angabe == "normen":
                nr = re.search(r"\d{3,5}", teil)
                e["ex"] = bool(nr and EX_NORM.match(nr[0]))
            elif self.angabe == "umgebungstemperatur":
                m = TEMPERATUR.search(teil)
                if m:
                    e.update(min=_grad(m["min"]), max=_grad(m["max"]))
            neu.append(e)
        return neu

    def pruefe(self, roh):
        """Eingabe → gespeicherter Wert (Text oder None); Fehler 400 bei falschem Wert."""
        if roh is not None and not isinstance(roh, str):
            raise Fehler(f"{self.titel} ließ sich nicht lesen. Bitte geben Sie den Wert als Text ein.")
        w = (roh or "").strip()
        if not w:
            if not self.leer:
                raise Fehler(f"{self.titel} darf nicht leer sein. Bitte wählen Sie einen Wert aus.")
            return None
        if len(w) > MAX_WERT:
            raise Fehler(f"{self.titel} ist zu lang. Bitte kürzen Sie den Wert auf höchstens "
                         f"{MAX_WERT} Zeichen.")
        if self.art == "auswahl" and w not in self.optionen:
            raise Fehler(f"„{w}“ passt nicht zu {self.titel}. Bitte wählen Sie einen Wert aus der Liste.")
        if self.art == "datum":
            if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", w):  # „31.12.2030“
                w = f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
            try:
                date.fromisoformat(w)
            except ValueError:
                raise Fehler(f"Das Datum „{w}“ ließ sich nicht lesen. Bitte geben Sie es zum Beispiel "
                             "als 31.12.2030 ein.") from None
        if self.art == "liste":
            w = "; ".join(t.strip() for t in re.split(r";|,\s+", w) if t.strip())
        if self.art == "sprachen":
            teile = [t.strip().lower() for t in re.split(r"[,;\s]+", w) if t.strip()]
            falsch = [t for t in teile if not re.fullmatch(r"[a-z]{2}", t)]
            if falsch:
                raise Fehler(f"„{', '.join(falsch)}“ ist kein Sprachkürzel. Bitte geben Sie die Sprachen "
                             "als Kürzel mit Komma ein, zum Beispiel „de, en“.")
            w = ",".join(dict.fromkeys(teile))
        return w

    def beschreibe(self):
        # „liste“ ist für die Oberfläche ein Textfeld (Einträge mit Semikolon getrennt)
        d = {"titel": self.titel, "art": "text" if self.art == "liste" else self.art,
             "leer": self.leer}
        if self.optionen:
            d["optionen"] = list(self.optionen)
        if self.art == "liste":
            d["liste"] = True
        return d


# wie angaben.EX_NORM (Ex-Normen) und ein Temperaturbereich „-20 °C bis +60 °C“
EX_NORM = re.compile(r"^(?:60079|80079|1127|13463|5001[4-9]|5002\d|5003\d|61241|50281|50284"
                     r"|50303|14986|13237)\b")
TEMPERATUR = re.compile(r"(?P<min>[-–−+]?\s?\d{1,3})\s*°?\s*C?\s*(?:…|\.{2,3}|bis|to|–|-)\s*"
                        r"(?P<max>\+?\s?\d{1,3})\s*°?\s*C?")


def _grad(text):
    return int(re.sub(r"[\s+]", "", text).replace("–", "-").replace("−", "-"))


# Reihenfolge = Reihenfolge in der Oberfläche. Werte aus finding.angaben: angabe="schluessel".
FUND_FELDER = {
    "doc_type": Feld("Dokumentart", spalte="doc_type", art="auswahl", optionen=DOC_TYPES,
                     leer=False),
    "kennzeichnung": Feld("Kennzeichnung", spalte="ex_marking_raw"),
    "zertifikat": Feld("Zertifikatsnummer", spalte="certificate_no"),
    "sprachen": Feld("Sprachen", spalte="languages", art="sprachen"),
    # aus finding.angaben (services/angaben.py)
    "gueltig_bis": Feld("Gültig bis", angabe="gueltig_bis", art="datum"),
    "ausstellungsdatum": Feld("Ausstellungsdatum", angabe="ausstellungsdatum", art="datum"),
    "ausgabe": Feld("Ausgabe", angabe="ausgabe"),
    "richtlinien": Feld("Richtlinien", angabe="richtlinien", art="liste"),
    "normen": Feld("Normen", angabe="normen", art="liste"),
    "stellen": Feld("Benannte Stelle", angabe="stellen", art="liste"),
    "umgebungstemperatur": Feld("Umgebungstemperatur", angabe="umgebungstemperatur", art="liste"),
    "iecex": Feld("IECEx-Nummer", angabe="iecex", art="liste"),
}
# Werte, nach deren Korrektur die Vorprüfung der Angaben neu läuft (pruefung.pruefe_angaben)
NEU_PRUEFEN = ("gueltig_bis", "richtlinien")


def _text(w):
    return None if w is None else str(w)


def fund_angaben(finding):
    """Korrigierbare Werte eines Funds mit der letzten Korrektur je Wert."""
    korrekturen = db.session.scalars(db.select(FindingCorrection).filter_by(
        finding_id=finding.id).order_by(FindingCorrection.id)).all()
    letzte, erste = {}, {}
    for k in korrekturen:
        letzte[k.field] = k
        erste.setdefault(k.field, k)
    felder = []
    for schl, f in FUND_FELDER.items():
        k = letzte.get(schl)
        felder.append({"schluessel": schl, **f.beschreibe(), "wert": _text(f.lies(finding)),
                       "gelesen": erste[schl].old_value if schl in erste else _text(f.lies(finding)),
                       "korrektur": {"von": k.changed_by, "zeit": k.changed_at.isoformat(),
                                     "alt": k.old_value, "notiz": k.note} if k else None})
    return {"fund_id": finding.id, "offen": finding.status == "vorgeschlagen",
            "status": finding.status, "felder": felder}


def korrigiere_fund(finding, daten):
    """Anfrage {"von", "note"?, Schlüssel aus FUND_FELDER: Wert}. Nur unentschiedene Funde.

    Gibt die neuen FindingCorrection zurück; Fehler 400 ohne Änderung, 409 nach der Entscheidung.
    """
    wer = name(daten)
    if finding.status != "vorgeschlagen":
        raise Fehler(f"Dieser Fund ist schon {finding.status}. Die gelesenen Werte lassen sich nur "
                     "korrigieren, solange noch niemand über den Fund entschieden hat.", 409)
    fremd = set(daten) - set(FUND_FELDER) - {"von", "by", "note", "notiz"}
    if fremd:
        raise Fehler("Diese Angabe lässt sich hier nicht ändern. Korrigieren können Sie "
                     + _und([f.titel for f in FUND_FELDER.values()]) + ".")
    text, jetzt = notiz(daten), _jetzt()
    if finding.angaben is None and any(FUND_FELDER[k].angabe for k in daten if k in FUND_FELDER):
        _angaben_lesen(finding)  # erst lesen, was im PDF steht, dann korrigieren
    neu = []
    for schl, f in FUND_FELDER.items():
        if schl not in daten:
            continue
        wert, alt = f.pruefe(daten[schl]), _text(f.lies(finding))
        if wert == alt:
            continue
        f.setze(finding, wert)
        neu.append(FindingCorrection(finding_id=finding.id, field=schl, old_value=alt,
                                     new_value=wert, changed_by=wer, changed_at=jetzt, note=text))
    if not neu:
        raise Fehler("Diese Werte stehen schon so im Fund. Es gab nichts zu ändern.")
    db.session.add_all(neu)
    if any(k.field in NEU_PRUEFEN for k in neu):
        vorpruefung_neu(finding)
    item = finding.item
    datei = PurePosixPath(finding.file_path).name if finding.file_path else (finding.url or "")

    def lesbar(k, w):
        if k.field == "doc_type":
            return DOKUMENTART.get(w, w)
        if w and FUND_FELDER[k.field].art == "datum":
            return date.fromisoformat(w).strftime("%d.%m.%Y")
        return w
    teile = [f"{FUND_FELDER[k.field].titel} von „{lesbar(k, k.old_value) or 'leer'}“ auf "
             f"„{lesbar(k, k.new_value) or 'leer'}“" for k in neu]
    protokolliere(item.bom_id, f"{_pos(item)}: {wer} hat im Fund „{datei}“ von Hand "
                               f"{_und(teile)} korrigiert.", item.id)
    return neu


def _angaben_lesen(finding):
    from flask import current_app
    from . import angaben
    try:
        angaben.fuelle(finding, finding.item, current_app.config["ATEX_DOCS"])
    except Exception:  # Datei kaputt o. Ä.: dann eben ohne die gelesenen Werte
        current_app.logger.exception("Angaben für Fund %s nicht lesbar", finding.id)


def vorpruefung_neu(finding, heute=None):
    """Nach einer Korrektur von Hand (z. B. „Gültig bis“): die Vorprüfung der Angaben
    (pruefung.pruefe_angaben) neu, damit ein jetzt abgelaufenes Zertifikat gewarnt wird und eine
    alte Warnung zum falsch gelesenen Datum verschwindet.

    Grundlage ist das Ergebnis ohne die Vorprüfung der Angaben. Steht es noch nicht in
    angaben["vorpruefung_basis"], wird es aus Meldung und Ergebnis zurückgerechnet: Hatte die
    alte Vorprüfung eine Warnung, gilt ohne sie „warnung“, wenn auch die Zone warnt (die einzige
    andere Quelle einer Warnung), sonst „unklar“, nie „ok“.
    """
    from flask import current_app
    from . import pruefung
    a = dict(finding.angaben or {})
    basis = a.get("vorpruefung_basis")
    if basis is None:
        alt = a.get("vorpruefung") or []
        meldung = finding.check_message or ""
        for m in alt:
            meldung = meldung.replace(" " + m["text"], "").replace(m["text"], "")
        ergebnis = finding.check_result
        if any(m["stufe"] == "warnung" for m in alt):
            kennz = [k.strip() for k in (finding.ex_marking_raw or "").split(";") if k.strip()]
            zone = pruefung.pruefe_zone(finding.item.location if finding.item else None, kennz)
            ergebnis = "warnung" if zone[0] == "warnung" else "unklar"
        basis = {"ergebnis": ergebnis, "meldung": meldung.strip()}
    tage = current_app.config.get("ABLAUF_WARNUNG_TAGE", 180)
    meldungen = pruefung.pruefe_angaben(a, heute or date.today(), tage, finding.doc_type)
    if (a.get("gueltig_bis") or {}).get("von_hand"):  # das Datum steht nicht so im PDF
        meldungen = [(s, t.replace("so steht es.", "so ist es von Hand eingetragen.")
                      .replace("So steht es.", "So ist es von Hand eingetragen.")) for s, t in meldungen]
    finding.check_result, finding.check_message = pruefung.bewerte_angaben(
        basis["ergebnis"], basis["meldung"], meldungen)
    finding.angaben = dict(a, vorpruefung=[{"stufe": s, "text": t} for s, t in meldungen],
                           vorpruefung_basis=basis)
