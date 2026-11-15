"""Anschluss an einen Suchdienst (SEARCH_URL): Ähnlichkeitssuche (RAG) über die Ex-Dokumente.

Der Suchdienst selbst gehört nicht zu diesem Repository. Erwartet wird POST <SEARCH_URL>/api/search
mit {"q", "k"} und als Antwort eine Liste von Textstellen (docs/api.md).

Der Dienst liefert Textstellen (Datei, Seite, Text), die beste zuerst, aus Vektor-, Stichwort- und
Bezeichnersuche. Hier werden daraus Kandidaten, eine je Datei. Ob eine Datei zur Position passt,
entscheidet danach dieselbe Prüfung wie im Ordner (suche.py): Die Ähnlichkeit allein macht
nichts „ok“.

Ohne SEARCH_URL ist die Stufe aus. Antwortet der Dienst nicht oder hat er noch keinen Index, geht
die Suche ohne ihn weiter; „gesucht in“ sagt das.
"""

from dataclasses import dataclass
from pathlib import Path

import httpx
from flask import current_app

WARTEZEIT = 30   # Sekunden je Anfrage (Abfrage auf der CPU: unter einer Sekunde)
TEXTSTELLEN = 20  # so viele Textstellen holen, daraus werden die Kandidaten-Dateien


class NichtErreichbar(Exception):
    """Der Suchdienst ist eingestellt, antwortet aber nicht brauchbar."""


class IndexFehlt(Exception):
    """Der Suchdienst läuft, baut seinen Index aber noch (HTTP 503)."""


@dataclass(frozen=True)
class Kandidat:
    pfad: Path         # absolut, unter ATEX_DOCS des Backends
    seite: int | None  # ab 1
    text: str          # die ähnlichste Textstelle der Datei
    rang: int          # 1 = beste Datei


def _url():
    return (current_app.config.get("SEARCH_URL") or "").rstrip("/")


def an():
    return bool(_url())


def anfrage(item):
    """Hersteller, Typ und Bezeichnung der Position. Mit Bezeichnung findet der Dienst mehr
    (in unserer Messung 39 statt 35 von 55 Ordnerfunden unter den ersten fünf)."""
    teile = [item.manufacturer, item.manufacturer_article_no, item.description]
    return " ".join(t.strip() for t in teile if t and t.strip())


def _modell():
    """Name des Chat-Modells des Vorgangs (sonst Standard „rag_antwort“) für den Suchdienst; er
    liest dieselbe Modelldatei. None ohne nutzbares Modell und ohne Modelldatei (dann kennt der
    Dienst nur seine Umgebung, ein Name wie „standard“ wäre ihm unbekannt)."""
    from . import modelle
    try:
        if modelle.bestand().quelle != "datei":
            return None
    except modelle.KonfigFehler:
        return None
    return modelle.name_fuer("rag_antwort")


def textstellen(frage, k=TEXTSTELLEN):
    """Rohe Treffer des Dienstes: [{name, path, relpath, page, text, rang}]."""
    anfrage_ = {"q": frage, "k": k}
    if modell := _modell():
        anfrage_["modell"] = modell
    try:
        r = httpx.post(_url() + "/api/search", json=anfrage_, timeout=WARTEZEIT)
        if r.status_code == 400 and "modell" in anfrage_:
            # Der Dienst kennt das Modell nicht (andere Modelldatei): ohne Modell weiter, die
            # reine Suche braucht keins
            current_app.logger.warning("Suchdienst kennt das Modell %r nicht: %s",
                                       anfrage_["modell"], r.text[:200])
            anfrage_.pop("modell")
            r = httpx.post(_url() + "/api/search", json=anfrage_, timeout=WARTEZEIT)
    except httpx.HTTPError as e:
        raise NichtErreichbar(f"Suchdienst nicht erreichbar ({e.__class__.__name__})") from e
    if r.status_code == 503:
        try:
            grund = r.json().get("detail") or ""
        except ValueError:
            grund = ""
        raise IndexFehlt(grund)
    try:
        r.raise_for_status()
        daten = r.json()
        if not isinstance(daten, list):
            raise ValueError
        return daten
    except (httpx.HTTPError, ValueError) as e:
        raise NichtErreichbar(f"Suchdienst nicht erreichbar ({e.__class__.__name__})") from e


def _unter(wurzel, relpath):
    """Pfad unter wurzel, nie außerhalb („..“ aus der Antwort zählt nicht); sonst None."""
    if not relpath:
        return None
    pfad = (wurzel / relpath).resolve()
    try:
        pfad.relative_to(wurzel.resolve())
    except ValueError:
        return None
    return pfad


def kandidaten(item, wurzel, k=TEXTSTELLEN):
    """Kandidaten-Dateien zur Position, beste zuerst: nur PDFs, die das Backend selbst unter
    ATEX_DOCS findet, je Datei die beste Textstelle."""
    wurzel = Path(wurzel)
    gesehen, ergebnis = set(), []
    for t in textstellen(anfrage(item), k):
        pfad = _unter(wurzel, t.get("relpath"))
        if pfad is None or pfad in gesehen or pfad.suffix.lower() != ".pdf" or not pfad.is_file():
            continue
        gesehen.add(pfad)
        seite = t.get("page")
        ergebnis.append(Kandidat(pfad, seite if isinstance(seite, int) else None,
                                 str(t.get("text") or ""), len(ergebnis) + 1))
    return ergebnis
