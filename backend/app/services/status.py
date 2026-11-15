"""Stand einer Position aus den Daten ableiten (M14) und Vollständigkeit rechnen (M12, M20).

Einzige Stelle, die BomItem.status setzt. Nach jeder Änderung an Vorschlägen, Stammdaten oder
E-Mails aktualisiere(item) aufrufen; das Speichern (commit) macht der Aufrufer. Ob eine Position
ein Ex-Teil ist, schlägt services/ex_liste.py vor; unsichere Fälle stehen auf „klaerung“.

Vollständig („vorhanden“) heißt: jede Art aus PFLICHT liegt geprüft vor, und zwar in der
Pflichtsprache PFLICHTSPRACHE (Standard „de“, Entscheidung des Teams zu M8). Eine nur englisch
geprüfte Konformitätserklärung reicht nicht; die Position bleibt offen, ihr Hinweis sagt
„Deutsche Fassung fehlt (Pflicht)“, bis die deutsche Fassung geprüft vorliegt oder ein Mensch die
Position von Hand erledigt. Baumusterprüfbescheinigung und Anleitung sind freiwillig: sie werden
gezeigt, halten aber nie etwas auf (baumuster_hinweis ist nur ein Hinweis).
"""

from . import ex_liste, lesepuffer

# Ohne diese Dokumentarten ist ein Bauteil nicht vollständig (M20)
PFLICHT = ("eu_konformitaetserklaerung",)
# Positionen in diesem Stand halten die Ausgabe nicht auf (M12). „erledigt“ ist die bewusste
# Abweichung von M20 durch einen Menschen und bleibt in Kundenliste und Fehlliste sichtbar.
ERLEDIGT = ("vorhanden", "kein_ex_teil", "erledigt")


def gepruefte_arten(profile_no):
    """Dokumentarten, die für diese PRO.FILE-Nr. geprüft in der Datenbank liegen."""
    part = lesepuffer.teil(profile_no)
    if part is None:
        return set()
    return {pd.document.doc_type for pd in part.documents if pd.status == "geprueft"}


def fehlende_arten(item):
    """Pflichtdokumente, die für die Position noch nicht geprüft vorliegen."""
    return [a for a in PFLICHT if a not in gepruefte_arten(item.profile_no)]


def pflichtsprache_fehlt(item):
    """Pflichtdokumentarten, die geprüft vorliegen, aber in keinem geprüften Dokument in der
    Pflichtsprache: {Art: {vorhandene Sprachen}} (leere Menge: Sprache unbekannt)."""
    from . import sprachversionen  # sprachversionen -> suche -> status
    if not item.profile_no:
        return {}
    p = sprachversionen.pflichtsprache(item)
    da = sprachversionen.gepruefte_sprachen(item.profile_no)
    return {art: da[art] for art in PFLICHT if art in da and p not in da[art]}


def fehlende_sprachen(item):
    """{Art: [Pflichtsprache]} wie pflichtsprache_fehlt, für Ausgaben und Oberfläche."""
    from . import sprachversionen
    p = sprachversionen.pflichtsprache(item)
    return {art: [p] for art in pflichtsprache_fehlt(item)}


SPRACHNAME = {"de": "Deutsch", "en": "Englisch", "fr": "Französisch"}
FASSUNG = {"de": "Deutsche", "en": "Englische", "fr": "Französische"}


def pflicht_text(item):
    """„Deutsche Fassung fehlt (Pflicht)“ mit Erklärung, oder None."""
    from . import sprachversionen
    from .excel_export import ART_TEXT
    fehlt = pflichtsprache_fehlt(item)
    if not fehlt:
        return None
    p = sprachversionen.pflichtsprache(item)
    saetze = [f"Die {FASSUNG.get(p, p.upper()).lower()} Fassung der EU-Konformitätserklärung "
              "fehlt noch, sie ist Pflicht."]
    for art, da in fehlt.items():
        name = ART_TEXT.get(art, art)
        if da:
            saetze.append(f"Die {name} liegt geprüft nur auf "
                          f"{' und '.join(SPRACHNAME.get(s, s) for s in sorted(da))} vor.")
        else:
            saetze.append(f"Bei der geprüften {name} ist die Sprache nicht bekannt. Bitte tragen "
                          "Sie die Sprache in den Stammdaten beim Dokument ein.")
    return " ".join(saetze)


def baumuster_hinweis(item):
    """Hinweis für Geräte der Kategorien 1 und 2 ohne EU-Baumusterprüfbescheinigung, sonst None.

    Nur ein Hinweis, keine Regel: Die Bescheinigung ist freiwillig (Entscheidung des
    Teams) und hält die Ausgabe nie auf.
    """
    if item.status == "kein_ex_teil":
        return None
    kategorie = ex_liste.einordnen(item).get("kategorie")
    if not kategorie or kategorie[0] not in "12":
        return None
    art = "eu_baumusterpruefbescheinigung"
    if item.profile_no and art in gepruefte_arten(item.profile_no):
        return None
    if any(f.doc_type == art and f.status != "abgelehnt" for f in item.findings):
        return None
    return (f"Für Geräte der Kategorie {kategorie} gibt es meist eine "
            "EU-Baumusterprüfbescheinigung. Sie fehlt hier noch, hält die Ausgabe aber nicht auf.")


def _einstellung(name):
    from flask import current_app, has_app_context
    return has_app_context() and current_app.config.get(name)


def aktualisiere(item):
    """Setzt item.status und item.status_note neu und gibt den Status zurück."""
    if item.status == "kein_ex_teil":  # Entscheidung eines Menschen bleibt stehen
        return item.status
    item.status_note = None
    # EX-Bauteil-Liste (E1): (Text, sperrt) oder None; ohne EX_LISTE fragt der Agent nicht
    frage = ex_liste.frage(item) if _einstellung("EX_LISTE") else None
    if item.resolved_at:  # von Hand erledigt: bleibt stehen, bis ein Mensch es zurücknimmt
        item.status = "erledigt"
        item.status_note = (f"{item.resolved_by} hat die Position am "
                            f"{item.resolved_at:%d.%m.%Y} von Hand erledigt. Begründung: "
                            f"{item.resolution_note}")
    elif item.profile_no and not fehlende_arten(item) and not pflichtsprache_fehlt(item):
        item.status = "vorhanden"
    elif not item.profile_no or not item.manufacturer_article_no:
        fehlt = [t for t, da in (("die PRO.FILE-Nummer", item.profile_no),
                                 ("die Hersteller-Artikelnummer", item.manufacturer_article_no))
                 if not da]
        item.status, item.status_note = "klaerung", (
            f"Es fehlt {' und '.join(fehlt)}. Ohne sie kann der Agent nicht suchen.")
    elif frage and frage[1]:  # Regeln unsicher, ohne Entscheidung keine Suche
        item.status, item.status_note = "klaerung", frage[0]
    elif any(f.status == "vorgeschlagen" for f in item.findings):
        item.status = "vorschlag"
    elif eskaliert := next((e for e in item.email_drafts if e.escalated_at), None):
        item.status = "eskaliert"
        item.status_note = (f"{eskaliert.escalated_by} hat die Anfrage am "
                            f"{eskaliert.escalated_at:%d.%m.%Y} an den Einkauf eskaliert.")
    elif any(e.status == "gesendet" for e in item.email_drafts):
        item.status = "angefordert"
    elif item.searched_at:
        item.status = "nicht_gefunden"
    else:
        item.status = "offen"
    if frage and item.status not in ERLEDIGT and not item.status_note:
        item.status_note = frage[0]  # wird gesucht, die Ex-Entscheidung steht noch aus
    if item.status not in ERLEDIGT:
        hinweise = [item.status_note, pflicht_text(item)]
        if not any(hinweise):
            hinweise.append(teilweise_vorhanden(item))
        from . import kontakte  # Anfrage wartet, aber der Hersteller hat keine Adresse
        hinweise.append(kontakte.kontakt_hinweis(item))
        item.status_note = " ".join(h for h in hinweise if h) or None
    return item.status


def teilweise_vorhanden(item):
    """Hinweis, wenn schon Dokumente geprüft vorliegen, aber ein Pflichtdokument fehlt."""
    from .excel_export import ART_TEXT  # excel_export importiert status
    geprueft = gepruefte_arten(item.profile_no) if item.profile_no else set()
    if not geprueft:
        return None
    def und(teile):
        return ", ".join(teile[:-1]) + " und " + teile[-1] if len(teile) > 1 else "".join(teile)
    def mit_artikel(a):
        return {"datenblatt": "das Datenblatt", "sonstiges": "ein sonstiges Dokument"}.get(
            a, f"die {ART_TEXT.get(a, a)}")
    da = und(sorted(mit_artikel(a) for a in geprueft))
    fehlt = und([mit_artikel(a) for a in fehlende_arten(item)])
    verb = "liegen" if len(geprueft) > 1 else "liegt"
    return f"Geprüft {verb} schon {da} vor. Es fehlt noch {fehlt}."


def aktualisiere_alle(bom):
    for item in bom.items:
        aktualisiere(item)


def offene_positionen(bom):
    """Positionen, die die Ausgabe noch aufhalten."""
    return [i for i in bom.items if i.status not in ERLEDIGT]
