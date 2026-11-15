"""Zugang zum Sprachmodell über LiteLLM: ein Aufruf für lokale und kommerzielle Anbieter.

Welche Modelle es gibt, steht in der Modell-Datei (MODELLE_DATEI, services/modelle.py), ohne
Datei in LLM_MODEL, LLM_API_BASE, LLM_API_KEY. Welches antwortet: das ausdrücklich genannte,
sonst das des laufenden Vorgangs (modelle.fuer_lauf), sonst der Standard der Aufgabe. Das Modell
liest nur Angaben aus Dokumenten und entwirft E-Mails. Es entscheidet nichts und ändert keine
Daten (M2, M3). Fehlertexte des Anbieters kommen ohne Schlüssel zurück.
"""

import logging
import time

from ..services import modelle


class LLMFehler(Exception):
    """Der Anbieter hat nicht geantwortet oder einen Fehler gemeldet (Text ohne Schlüssel)."""


def _litellm():
    import litellm  # erst hier: der Import ist langsam und nur für diesen Aufruf nötig

    litellm.suppress_debug_info = True
    return litellm


def _zugang(m):
    return {"api_base": m.api_base or None, "api_key": m.api_key or None, "timeout": m.timeout}


def chat(messages, max_tokens=1000, modell=None, aufgabe="websuche"):
    """Schickt die Nachrichten an das Modell und gibt den Antworttext zurück.

    modell: Name aus der Modell-Datei; ohne Angabe das Modell des Laufs, sonst der Standard
    der Aufgabe (einziger Aufrufer ohne Angabe ist die Websuche, services/internet.py).
    """
    m = modelle.fuer(aufgabe, modell)  # KonfigFehler: kein still anderes Modell
    try:
        antwort = _litellm().completion(model=m.modell, messages=messages, max_tokens=max_tokens,
                                        **_zugang(m))
    except Exception as e:  # noqa: BLE001  Text des Anbieters, ohne Schlüssel
        raise LLMFehler(f"{m.name}: {modelle.bereinige(f'{type(e).__name__}: {e}')}") from None
    return antwort.choices[0].message.content


def teste(name):
    """Kleine Anfrage an ein Modell: {name, erreichbar, dauer_ms, antwort | fehler}.
    KeyError, wenn das Modell nicht eingerichtet ist; KonfigFehler bei fehlerhafter Datei."""
    m = modelle.bestand().modelle[name]
    zugang = _zugang(m) | {"timeout": min(m.timeout, 30)}
    start = time.monotonic()
    try:
        if m.art == "embedding":
            r = _litellm().embedding(model=m.modell, input=["Explosionsschutz"], **zugang)
            eintrag = r.data[0]
            vektor = eintrag["embedding"] if isinstance(eintrag, dict) else eintrag.embedding
            antwort = f"Vektor mit {len(vektor)} Werten"
        else:
            r = _litellm().completion(model=m.modell, max_tokens=20, messages=[
                {"role": "user", "content": "Antworte nur mit dem Wort OK."}], **zugang)
            antwort = (r.choices[0].message.content or "").strip()[:200] or "(leere Antwort)"
        ergebnis = {"erreichbar": True, "antwort": modelle.bereinige(antwort)}
    except Exception as e:  # noqa: BLE001  jede Störung ist ein Ergebnis des Tests
        detail = modelle.bereinige(f"{type(e).__name__}: {e}")[:600]
        logging.getLogger(__name__).warning("Verbindungstest %s: %s", m.name, detail)
        ergebnis = {"erreichbar": False, "fehler": fehlertext(e), "detail": detail}
    return {"name": m.name, "modell": m.modell,
            "dauer_ms": round((time.monotonic() - start) * 1000)} | ergebnis


def fehlertext(e):
    """Was beim Anbieter schiefging, in Worten für Anwender; der Rohtext steht im Log."""
    art, text = type(e).__name__, str(e).lower()
    if ("auth" in art.lower() or "permission" in art.lower() or "authentication" in text
            or "401" in text):
        return ("Der Anbieter hat den Zugang abgelehnt. Bitte lassen Sie den Zugangsschlüssel "
                "vom Administrator prüfen.")
    if "notfound" in art.lower() or "model_not_found" in text or "does not exist" in text:
        return ("Der Anbieter kennt dieses Modell nicht. Bitte lassen Sie den Modellnamen vom "
                "Administrator prüfen.")
    if "ratelimit" in art.lower():
        return ("Der Anbieter nimmt gerade keine weiteren Anfragen an. Bitte versuchen Sie es in "
                "einigen Minuten noch einmal.")
    if "timeout" in art.lower() or "timed out" in text:
        return ("Der Anbieter hat nicht rechtzeitig geantwortet. Bitte versuchen Sie es später "
                "noch einmal.")
    if "connect" in art.lower() or "connection" in text:
        return ("Der Anbieter ist nicht erreichbar. Bitte versuchen Sie es später noch einmal "
                "oder fragen Sie den Administrator, ob die Adresse stimmt.")
    return ("Der Anbieter hat mit einem Fehler geantwortet. Bitte geben Sie dem Administrator "
            "Bescheid; die Einzelheiten stehen im Log.")
