"""Sprachmodelle aus einer Datei (MODELLE_DATEI, Vorlage config/modelle.beispiel.yaml).

Die Datei nennt beliebig viele Modelle (Chat und Embedding) mit Anbieter, Adresse und Zugang und
unter „standard“, welches Modell eine Aufgabe ohne Wahl nimmt. Sie wird bei jeder Änderung neu
gelesen (Zeitstempel), ein neues Modell braucht keinen Neustart. Fehlt die Datei, gilt genau ein
Chat-Modell „standard“ aus den alten Variablen LLM_MODEL, LLM_API_BASE, LLM_API_KEY, LLM_TIMEOUT.
Ist die Datei fehlerhaft, gibt es eine deutsche Fehlermeldung und kein Modell, nie still ein
anderes.

Je Vorgang (Bom) kann ein Mensch ein Chat-Modell und die Zahl der Web-Anfragen je Position
wählen; leer heißt Standard. fuer_lauf() setzt beides am Anfang eines Laufs: das Modell gilt
dann für alle Aufrufe von app.llm im selben App-Kontext (flask.g), ohne dass die Aufrufer
(services/internet.py) es kennen müssen.

Schlüssel verlassen den Server nie: nicht in Antworten, Logzeilen, Fehlertexten oder im Protokoll.
"""

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from flask import current_app, g, has_app_context

from ..db import db
from ..models.bom import BomEvent

log = logging.getLogger(__name__)

# Aufgabe -> Art des Modells, das sie braucht
AUFGABEN = {"websuche": "chat", "email": "chat", "rag_antwort": "chat",
            "rag_embedding": "embedding"}
# Texte für Anwender (Oberfläche, Protokoll): ohne Fachwörter
AUFGABE_TEXT = {"websuche": "Websuche", "email": "E-Mail-Entwürfe",
                "rag_antwort": "Antworten der Ähnlichkeitssuche",
                "rag_embedding": "Ähnlichkeitssuche"}
ARTEN = ("chat", "embedding")
FELDER = ("name", "art", "modell", "api_base", "api_key", "api_key_env", "timeout")
ANWENDER_FEHLT = "Ohne Anmeldung wird nichts gespeichert. Bitte melden Sie sich an."
KAPUTT = ("Die Sprachmodelle sind falsch eingerichtet. Bis das behoben ist, arbeitet der Agent "
          "ohne Sprachmodell. Bitte geben Sie der Administration Bescheid.")


class KonfigFehler(Exception):
    """Die Modell-Datei ist fehlerhaft oder das gewünschte Modell ist nicht eingerichtet."""


@dataclass(frozen=True)
class Modell:
    name: str
    art: str
    modell: str                  # LiteLLM-Schreibweise „anbieter/modell“
    api_base: str | None = None
    timeout: float = 60
    api_key: str | None = field(default=None, repr=False)  # nie nach außen geben
    zugang: str = "datei"        # woher der Schlüssel kommt: datei | env:NAME | umgebung

    @property
    def anbieter(self):
        return self.modell.split("/", 1)[0]

    def oeffentlich(self, standard_fuer=()):
        """Ansicht ohne Schlüssel: ob einer hinterlegt ist, nur ja/nein."""
        return {"name": self.name, "art": self.art, "modell": self.modell,
                "anbieter": self.anbieter, "api_base": bereinige(self.api_base or "") or None,
                "zugang_hinterlegt": bool(self.api_key) and self.api_key != "-",
                # „-“ heißt: der Anbieter braucht keinen Schlüssel (z. B. Ollama)
                "ohne_schluessel": self.api_key == "-",
                "zugang_text": ("Kein Schlüssel nötig" if self.api_key == "-" else
                                "Schlüssel hinterlegt" if self.api_key else "Schlüssel fehlt"),
                "zugang_aus": self.zugang if self.zugang != "datei" else "Datei",
                "timeout": self.timeout, "standard_fuer": list(standard_fuer)}


@dataclass
class Bestand:
    modelle: dict           # Name -> Modell, in der Reihenfolge der Datei
    standard: dict          # Aufgabe -> Name oder None
    quelle: str             # datei | umgebung
    datei: str

    def chat_modelle(self):
        return [m for m in self.modelle.values() if m.art == "chat"]

    def standard_fuer(self, name):
        return [a for a, n in self.standard.items() if n == name]


_sperre = threading.Lock()
_cache = {"schluessel": None, "bestand": None, "fehler": None}


# Lesen und prüfen


def _datei():
    return (current_app.config.get("MODELLE_DATEI") or "").strip()


def bestand():
    """Die eingerichteten Modelle; KonfigFehler, wenn die Datei fehlerhaft ist."""
    pfad = _datei()
    try:
        stand = os.stat(pfad) if pfad else None
    except FileNotFoundError:
        stand = None
    except OSError as e:
        raise KonfigFehler(f"{pfad}: nicht lesbar ({e.strerror})") from None
    if stand is None:
        return _aus_umgebung(pfad)
    schluessel = (pfad, stand.st_mtime_ns, stand.st_size)
    with _sperre:
        if _cache["schluessel"] != schluessel:
            try:
                neu, fehler = _lies(Path(pfad)), None
                log.info("Sprachmodelle aus %s: %s", pfad, ", ".join(
                    f"{m.name} ({m.art}, {m.modell})" for m in neu.modelle.values()))
            except KonfigFehler as e:
                neu, fehler = None, str(e)
                log.error("Sprachmodelle: %s", fehler)
            _cache.update(schluessel=schluessel, bestand=neu, fehler=fehler)
        if _cache["fehler"]:
            raise KonfigFehler(_cache["fehler"])
        return _cache["bestand"]


def _aus_umgebung(pfad):
    """Ohne Datei: ein Chat-Modell „standard“ aus LLM_MODEL usw., wie vor der Modell-Datei."""
    cfg = current_app.config
    m = Modell(name="standard", art="chat", modell=cfg["LLM_MODEL"],
               api_base=cfg["LLM_API_BASE"] or None, timeout=cfg["LLM_TIMEOUT"],
               api_key=cfg["LLM_API_KEY"] or None, zugang="umgebung")
    standard = {a: ("standard" if art == "chat" else None) for a, art in AUFGABEN.items()}
    return Bestand({m.name: m}, standard, "umgebung", pfad)


def _lies(pfad):
    import yaml  # PyYAML kommt mit litellm ins Image

    try:
        daten = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    except yaml.MarkedYAMLError as e:
        # nur Zeile und Problem, nie den Ausschnitt: dort könnte ein Schlüssel stehen
        wo = f"Zeile {e.problem_mark.line + 1}: " if e.problem_mark else ""
        raise KonfigFehler(f"{pfad}: kein gültiges YAML ({wo}{e.problem or e.context})") from None
    except yaml.YAMLError:
        raise KonfigFehler(f"{pfad}: kein gültiges YAML") from None
    except (OSError, UnicodeDecodeError) as e:
        raise KonfigFehler(f"{pfad}: nicht lesbar ({e})") from None
    return pruefe(daten, str(pfad))


def pruefe(daten, datei="modelle.yaml"):
    """Prüft den Inhalt der Datei und baut den Bestand. Jeder Fehler: KonfigFehler mit Ort."""
    def fehler(text):
        raise KonfigFehler(f"{datei}: {text}")

    if not isinstance(daten, dict):
        fehler("oben müssen „modelle:“ und „standard:“ stehen.")
    for k in daten:
        if k not in ("modelle", "standard"):
            fehler(f"unbekannter Eintrag „{k}“ (erlaubt: modelle, standard).")
    liste = daten.get("modelle")
    if not isinstance(liste, list) or not liste:
        fehler("unter „modelle:“ fehlt die Liste der Modelle (je Modell eine Zeile mit „- name:“).")
    modelle = {}
    for nr, eintrag in enumerate(liste, 1):
        m = _pruefe_modell(eintrag, f"Modell Nr. {nr}", fehler)
        if m.name in modelle:
            fehler(f"der Name „{m.name}“ kommt zweimal vor; jeder Name nur einmal.")
        modelle[m.name] = m
    if not any(m.art == "chat" for m in modelle.values()):
        fehler("kein Modell mit „art: chat“; mindestens eines ist nötig.")

    standard_roh = daten.get("standard") or {}
    if not isinstance(standard_roh, dict):
        fehler("„standard:“ muss Aufgabe: Name enthalten, z. B. „websuche: beispiel-chat“.")
    standard = {}
    for aufgabe, art in AUFGABEN.items():
        name = standard_roh.get(aufgabe)
        if name is None:  # nicht angegeben: das erste Modell der passenden Art
            name = next((m.name for m in modelle.values() if m.art == art), None)
        elif name not in modelle:
            fehler(f"standard.{aufgabe} nennt „{name}“, das steht nicht unter „modelle:“.")
        elif modelle[name].art != art:
            fehler(f"standard.{aufgabe} braucht ein Modell mit „art: {art}“, „{name}“ ist "
                   f"„{modelle[name].art}“.")
        standard[aufgabe] = name
    for k in standard_roh:
        if k not in AUFGABEN:
            fehler(f"unbekannte Aufgabe „standard.{k}“ (erlaubt: {', '.join(AUFGABEN)}).")
    return Bestand(modelle, standard, "datei", datei)


def _pruefe_modell(eintrag, wo, fehler):
    if not isinstance(eintrag, dict):
        fehler(f"{wo}: Angaben als „schluessel: wert“ unter „- name: …“ erwartet.")
    name = eintrag.get("name")
    if not isinstance(name, str) or not name.strip():
        fehler(f"{wo}: „name“ fehlt.")
    name = name.strip()
    if not re.fullmatch(r"[\w.\-]+", name):
        fehler(f"{wo}: Name „{name}“ nur aus Buchstaben, Ziffern, Punkt, Binde- und Unterstrich.")
    wo = f"Modell „{name}“"
    for k in eintrag:
        if k not in FELDER:
            fehler(f"{wo}: unbekannter Eintrag „{k}“ (erlaubt: {', '.join(FELDER)}).")
    art = eintrag.get("art", "chat")
    if art not in ARTEN:
        fehler(f"{wo}: „art“ ist chat oder embedding, nicht „{art}“.")
    modell = eintrag.get("modell")
    if not isinstance(modell, str) or "/" not in modell.strip("/"):
        fehler(f"{wo}: „modell“ fehlt oder ist nicht „anbieter/modell“ (z. B. openai/beispiel-modell).")
    api_base = eintrag.get("api_base")
    if api_base is not None and not isinstance(api_base, str):
        fehler(f"{wo}: „api_base“ muss eine Adresse sein.")
    timeout = eintrag.get("timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        fehler(f"{wo}: „timeout“ ist eine Zahl von Sekunden größer 0.")
    hat_key, hat_env = "api_key" in eintrag, "api_key_env" in eintrag
    if hat_key and hat_env:
        fehler(f"{wo}: entweder „api_key“ oder „api_key_env“, nicht beides.")
    if hat_key:
        key, zugang = eintrag["api_key"], "datei"
        if not isinstance(key, str) or not key:
            fehler(f"{wo}: „api_key“ in Anführungszeichen angeben; ohne Schlüssel: api_key: \"-\".")
    elif hat_env:
        variable = eintrag["api_key_env"]
        if not isinstance(variable, str) or not variable:
            fehler(f"{wo}: „api_key_env“ nennt den Namen einer Umgebungsvariablen.")
        key, zugang = os.environ.get(variable, ""), f"env:{variable}"
        if not key:
            fehler(f"{wo}: die Umgebungsvariable {variable} (api_key_env) ist nicht gesetzt "
                   "oder leer.")
    else:
        fehler(f"{wo}: kein Zugang angegeben: „api_key“ oder „api_key_env“ eintragen "
               "(Anbieter ohne Schlüssel, z. B. Ollama: api_key: \"-\").")
    return Modell(name=name, art=art, modell=modell.strip(), api_base=(api_base or "").strip() or None,
                  timeout=timeout, api_key=key, zugang=zugang)


# Schlüssel aus Texten entfernen


_MUSTER = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9_.\-]{6,}"
                     r"|(?:api[_-]?key|x-api-key|authorization)[\"']?\s*[:=]\s*[\"']?[^\s\"',}]{4,})",
                     re.I)


def _alle_schluessel():
    keys = set()
    try:
        b = bestand() if has_app_context() else None
    except KonfigFehler:
        b = None
    for m in (b.modelle.values() if b else ()):
        if m.api_key and m.api_key != "-" and len(m.api_key) >= 4:
            keys.add(m.api_key)
    if has_app_context():
        k = current_app.config.get("LLM_API_KEY") or ""
        if k != "-" and len(k) >= 4:
            keys.add(k)
    return keys


def bereinige(text):
    """Text ohne Schlüssel: alle eingerichteten Schlüssel und typische Muster durch *** ersetzt."""
    text = str(text or "")
    for k in sorted(_alle_schluessel(), key=len, reverse=True):
        text = text.replace(k, "***")
    return _MUSTER.sub("***", text)


# Wahl je Lauf und je Aufgabe


def setze_lauf_modell(name):
    """Modell für alle folgenden Aufrufe von app.llm in diesem App-Kontext; None = Standard."""
    g.llm_modell = name or None


def lauf_modell():
    return g.get("llm_modell") if has_app_context() else None


def fuer(aufgabe, gewaehlt=None):
    """Das Modell für eine Aufgabe: gewählt, sonst das des Laufs (Chat), sonst der Standard."""
    art = AUFGABEN[aufgabe]
    b = bestand()
    name = gewaehlt or (lauf_modell() if art == "chat" else None) or b.standard.get(aufgabe)
    if not name:
        raise KonfigFehler(f"Für „{AUFGABE_TEXT[aufgabe]}“ ist kein Sprachmodell eingerichtet.")
    m = b.modelle.get(name)
    if m is None:
        raise KonfigFehler(f"Sprachmodell „{name}“ ist nicht (mehr) eingerichtet "
                           f"({b.datei or 'Umgebungsvariablen'}).")
    if m.art != art:
        raise KonfigFehler(f"Sprachmodell „{name}“ ist ein {m.art}-Modell, gebraucht wird {art}.")
    return m


def name_fuer(aufgabe):
    """Name des Modells für eine Aufgabe im laufenden Kontext, None ohne nutzbares Modell.
    Für den Suchdienst: diesen Namen in der Anfrage mitschicken (er liest dieselbe Datei)."""
    try:
        return fuer(aufgabe).name
    except KonfigFehler:
        return None


def genutzt():
    """Welche Aufgabe des Backends überhaupt ein Sprachmodell fragt (Schalter der Installation)."""
    cfg = current_app.config
    return {"websuche": bool(cfg.get("INTERNET_LLM")), "email": bool(cfg.get("EMAIL_MIT_LLM"))}


def hinweise(nutzung):
    texte = []
    if not nutzung["websuche"]:
        texte.append("Die Websuche nutzt derzeit kein Sprachmodell. Welche Links sie öffnet, "
                     "entscheiden feste Regeln.")
    if not nutzung["email"]:
        texte.append("Die E-Mail-Entwürfe nutzen derzeit kein Sprachmodell. Sie entstehen aus "
                     "der festen Vorlage.")
    return texte


# Einstellungen eines Vorgangs


def _obergrenze():
    from . import internet  # erst hier: internet importiert app.llm, das dieses Modul braucht
    return internet.OBERGRENZE


def versuche_wirksam(bom, versuche=None):
    """Web-Anfragen je Position: Angabe des Aufrufs, sonst die des Vorgangs, sonst der Standard."""
    if versuche is None:
        versuche = getattr(bom, "internet_versuche", None)
    if versuche is None:
        versuche = current_app.config["INTERNET_VERSUCHE_PRO_TEIL"]
    return max(0, min(int(versuche), _obergrenze()))


def _wirksam(bom, versuche):
    """Aufgabe -> Modellname, den der nächste Lauf nimmt (None: kein Modell), und ein Text für
    Anwender, wenn ein Modell gebraucht, aber nicht nutzbar ist (Einzelheiten ins Log)."""
    nutzung = genutzt()
    wirksam, fehler = {}, None
    for aufgabe in nutzung:
        if not nutzung[aufgabe] or (aufgabe == "websuche" and not versuche):
            wirksam[aufgabe] = None
            continue
        try:
            wirksam[aufgabe] = fuer(aufgabe, bom.llm_modell).name
        except KonfigFehler as e:
            log.warning("Sprachmodell für Vorgang %s: %s", bom.id, e)
            wirksam[aufgabe], fehler = None, _fehlertext(bom)
    return wirksam, fehler


def _fehlertext(bom):
    """Warum der Vorgang kein Sprachmodell bekommt, für Anwender."""
    try:
        b = bestand()
    except KonfigFehler:
        return KAPUTT
    if bom.llm_modell and bom.llm_modell not in b.modelle:
        return (f"Das gewählte Sprachmodell „{bom.llm_modell}“ steht nicht mehr zur Verfügung. "
                "Bis Sie ein anderes wählen, arbeitet der Agent ohne Sprachmodell.")
    return KAPUTT


def einstellungen(bom, laeuft=False):
    """Einstellungen des Vorgangs mit den Werten, die der nächste Lauf wirklich nimmt."""
    cfg = current_app.config
    versuche = versuche_wirksam(bom)
    try:
        b = bestand()
        auswahl = [{"name": m.name, "modell": m.modell, "anbieter": m.anbieter}
                   for m in b.chat_modelle()]
        standard = {a: b.standard.get(a) for a in ("websuche", "email")}
        fehler = None
    except KonfigFehler:
        auswahl, standard, fehler = [], {}, KAPUTT
    wirksam, fehler_lauf = _wirksam(bom, versuche)
    if bom.llm_modell and not fehler and bom.llm_modell not in [m["name"] for m in auswahl]:
        fehler = _fehlertext(bom)  # auch wenn der nächste Lauf gerade kein Modell bräuchte
    nutzung = genutzt()
    return {"vorgang_id": bom.id, "laeuft": laeuft,
            "modell": bom.llm_modell, "modell_auswahl": auswahl, "standard": standard,
            "sprachmodell_genutzt": nutzung, "modell_wirksam": wirksam,
            "hinweise": hinweise(nutzung), "modell_fehler": fehler or fehler_lauf,
            "internet_versuche": bom.internet_versuche,
            "internet_versuche_standard": cfg["INTERNET_VERSUCHE_PRO_TEIL"],
            "internet_versuche_max": _obergrenze(), "internet_versuche_wirksam": versuche,
            "sekunden_je_versuch_max": 3 * cfg.get("INTERNET_TIMEOUT", 10)}


class EingabeFehler(Exception):
    def __init__(self, text, code=400):
        super().__init__(text)
        self.code = code


def _versuche_text(n):
    if n == 0:
        return "keine Websuche"
    return f"{n} Versuch je Position" if n == 1 else f"{n} Versuche je Position"


def aendere(bom, daten, von):
    """Ändert Modell und/oder Versuche des Vorgangs; je Änderung ein Eintrag im Protokoll.
    Fehlt ein Schlüssel, bleibt der Wert; null oder "" heißt Standard. Speichert (commit)."""
    if not von:
        raise EingabeFehler(ANWENDER_FEHLT)
    if "modell" not in daten and "internet_versuche" not in daten:
        raise EingabeFehler("Es gibt nichts zu speichern. Bitte ändern Sie zuerst das "
                            "Sprachmodell oder die Zahl der Versuche.")
    neu_modell, neu_versuche = bom.llm_modell, bom.internet_versuche
    b = None
    if "modell" in daten:
        wert = daten["modell"]
        if wert in (None, ""):
            neu_modell = None
        elif not isinstance(wert, str):
            raise EingabeFehler("Dieses Sprachmodell kennt der Agent nicht. Bitte wählen Sie "
                                "eines aus der Liste.")
        else:
            try:
                b = bestand()
            except KonfigFehler as e:
                log.warning("Sprachmodelle: %s", e)
                raise EingabeFehler(KAPUTT, 503) from None
            m = b.modelle.get(wert)
            if m is None:
                raise EingabeFehler(f"Das Sprachmodell „{wert}“ ist nicht eingerichtet. Bitte "
                                    "wählen Sie eines aus der Liste: "
                                    + ", ".join(x.name for x in b.chat_modelle()) + ".")
            if m.art != "chat":
                raise EingabeFehler(f"„{wert}“ ist nur für die Ähnlichkeitssuche da und kann "
                                    "keine Texte schreiben. Bitte wählen Sie ein anderes "
                                    "Sprachmodell aus der Liste.")
            neu_modell = wert
    if "internet_versuche" in daten:
        wert, grenze = daten["internet_versuche"], _obergrenze()
        if wert in (None, ""):
            neu_versuche = None
        elif type(wert) is not int or not 0 <= wert <= grenze:
            raise EingabeFehler(f"Bitte geben Sie bei den Versuchen eine ganze Zahl von 0 bis "
                                f"{grenze} ein. Lassen Sie das Feld leer, gilt der Standard.")
        else:
            neu_versuche = wert
    texte = []
    if neu_modell != bom.llm_modell:
        if neu_modell:
            texte.append(f"{von} hat für diesen Vorgang das Sprachmodell „{neu_modell}“ gewählt.")
        else:
            try:
                standard = (b or bestand()).standard.get("websuche")
            except KonfigFehler:
                standard = None
            texte.append(f"{von} hat für diesen Vorgang wieder das Standard-Sprachmodell "
                         + (f"„{standard}“ eingestellt." if standard else "eingestellt."))
    if neu_versuche != bom.internet_versuche:
        if neu_versuche is None:
            n = current_app.config["INTERNET_VERSUCHE_PRO_TEIL"]
            texte.append(f"{von} hat für die Websuche dieses Vorgangs wieder den Standard "
                         f"eingestellt, das heißt {_versuche_text(n)}.")
        elif neu_versuche == 0:
            texte.append(f"{von} hat die Websuche für diesen Vorgang abgeschaltet.")
        else:
            texte.append(f"{von} hat die Websuche für diesen Vorgang auf höchstens "
                         f"{_versuche_text(neu_versuche)} gestellt.")
    bom.llm_modell, bom.internet_versuche = neu_modell, neu_versuche
    for text in texte:
        db.session.add(BomEvent(bom_id=bom.id, text=text))
    db.session.commit()
    return texte


def fuer_lauf(bom, versuche=None):
    """Am Anfang eines Suchlaufs: das Modell des Vorgangs für diesen App-Kontext setzen und die
    wirksamen Web-Anfragen je Position zurückgeben (Angabe des Aufrufs vor der des Vorgangs)."""
    setze_lauf_modell(getattr(bom, "llm_modell", None))
    return versuche_wirksam(bom, versuche)


def vermerk(bom, versuche):
    """Satz hinter „Agent startet …“: welches Sprachmodell der Lauf nutzt, oder keines."""
    wirksam, fehler = _wirksam(bom, versuche)
    if fehler:
        return ". " + fehler
    namen = {n for n in wirksam.values() if n}
    if not namen:
        return ". Der Agent arbeitet ohne Sprachmodell."
    if len(namen) == 1:
        name = namen.pop()
        if bom.llm_modell:
            return f". Der Agent nutzt das Sprachmodell „{name}“, das für diesen Vorgang gewählt ist."
        return f". Der Agent nutzt das Standard-Sprachmodell „{name}“."
    return (f". Der Agent nutzt für die Websuche das Sprachmodell „{wirksam['websuche']}“ und "
            f"für die E-Mail-Entwürfe „{wirksam['email']}“.")
