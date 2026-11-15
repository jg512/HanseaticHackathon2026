"""Eingangsdatei in die EX-Bauteil-Liste umwandeln (E1).

Die EX-Bauteil-Liste ist unsere Liste der Positionen, die als Ex-Teil gelten, je mit Regel und
Begründung. Jede Position wird nach festen Regeln eingeordnet, nur aus dem, was in der Datei steht
(Einbauort der Zeile, Bezeichnung, Typ, Kopfangaben „Ex-Bereich“ und „Geforderte Kategorie“), dazu
die Kennzeichnung aus schon gefundenen Dokumenten der Position. Vom Team entschieden
(docs/architektur.md, „Fachliche Entscheidungen des Teams“), vom Kunden nicht bestätigt; ein Mensch bestätigt
die Liste:

- Einbauort nennt eine Zone (0, 1, 2, 20, 21, 22) oder den Ex-Bereich: Ex-Teil.
- Einbauort steht in der Kopfangabe „Ex-Bereich“ mit Zone („Innenraum Zone 1“): Ex-Teil.
- Einbauort außerhalb des Ex-Bereichs, aber zugehöriges Betriebsmittel (Kennzeichnung in Klammern
  wie „II (1)G [Ex ia Ga] IIC“, oder Bezeichnung bzw. Typ eines Trennbausteins wie Pos. 140
  „Trennschaltverstärker KCD2-SR-Ex1.LB“): Ex-Teil, braucht Dokumente. Es sitzt im sicheren
  Bereich, gehört aber zum Explosionsschutz.
- Außerhalb mit nur vagem Ex-Hinweis („Ex“, „ATEX“, „NAMUR“ in Bezeichnung oder Typ): Entscheidung
  nötig, wird aber schon gesucht, damit der Mensch mit den Dokumenten entscheiden kann.
- Außerhalb ohne Ex-Hinweis, widersprüchlich, unbekannt oder leer: Entscheidung nötig, Stand
  „klaerung“, gesucht wird erst nach der Entscheidung. Nichts fällt still heraus.
- Eine Ausschlussliste gibt es nicht. Heraus fällt eine Position nur durch die Regeln (außerhalb,
  kein Ex-Hinweis, und ein Mensch entscheidet „kein Ex-Teil“) oder durch die Entscheidung
  „kein Ex-Teil“ eines Menschen.

Die Entscheidung des Menschen: „kein_ex_teil“ (Stand, wie bisher) oder Ex-Teil bestätigt
(BomItem.ex_confirmed_by). Jede Einordnung trägt Regel, Begründung und Quelle (Zeile und Zelle).
Ohne Datenbank testbar; das Speichern macht der Aufrufer.
"""

import re

from .pruefung import zone as zone_aus_text

EX_TEIL, PRUEFEN = "ex_teil", "pruefen"  # Vorschlag der Regeln
# Einordnung nach Vorschlag und Entscheidung
EINORDNUNG_TEXT = {"ex_teil": "Ex-Teil", "kein_ex_teil": "kein Ex-Teil",
                   "klaerung": "Entscheidung nötig"}
REGEL_TEXT = {
    "zone_im_einbauort": "Einbauort nennt eine Zone",
    "ex_bereich_im_einbauort": "Einbauort nennt den Ex-Bereich",
    "bereich_aus_kopfzeile": "Einbauort steht in der Kopfangabe „Ex-Bereich“",
    "ausserhalb_ex": "Einbauort außerhalb des Ex-Bereichs",
    "zugehoeriges_betriebsmittel": "Zugehöriges Betriebsmittel außerhalb des Ex-Bereichs",
    "widerspruch": "Einbauort nennt Zone und „außerhalb“",
    "einbauort_unklar": "Einbauort keiner Zone zuzuordnen",
    "einbauort_fehlt": "Einbauort fehlt",
}

AUSSERHALB = re.compile(r"außerhalb|ausserhalb|nicht[ -]?ex|kein[ -]?ex|sicherer bereich"
                        r"|safe area|non[ -]?hazardous", re.I)
EX_BEREICH = re.compile(r"\bex[ -]?(?:bereich|zone)|explosionsgefährdet|explosionsgefaehrdet"
                        r"|\batex\b", re.I)
ZONE = re.compile(r"\bzone\s*(0|1|2|20|21|22)\b", re.I)
# Ex-Hinweis in Typ oder Bezeichnung: „Ex“ als eigenes Kürzel („Ex e“, „EX4“, „KCD2-SR-Ex1.LB“),
# „ATEX“, „NAMUR“ oder eine Kennzeichnung („II 2G“)
EX_KENNUNG = re.compile(r"(?<![a-zäöüß])ex(?![a-zäöüß])|\batex\b|\bnamur\b"
                        r"|\bII\s*\(?[123]\)?\s*[GD]\b", re.I)
# Zugehöriges Betriebsmittel: Kennzeichnung mit Kategorie oder Zündschutzart in Klammern,
# „II (1)G“, „[Ex ia Ga]“, „[Ex ib]“
KLAMMER_KENNZEICHNUNG = re.compile(r"\(\s*[123]\s*\)\s*[GD]\b|\[\s*E?Ex\s*i[abc]?\b", re.I)
# Bezeichnung eines Trennbausteins (Stromkreise führen in den Ex-Bereich)
TRENNBAUSTEIN = re.compile(r"trenn(?:schalt)?verstärker|trennverstaerker|trennschaltverstaerker"
                           r"|speisetrenner|trennbarriere|sicherheitsbarriere|zener[- ]?barriere"
                           r"|messumformerspeisegerät|isolat(?:ing|ed) (?:amplifier|barrier)"
                           r"|switch amplifier|safety barrier", re.I)
# Typ eines Trennbausteins: „-Ex“ mit Kanalzahl, z. B. „KCD2-SR-Ex1.LB“, „KFD2-SOT2-Ex2“
TYP_TRENNBAUSTEIN = re.compile(r"-ex\d", re.I)
# Kopfangabe „Geforderte Kategorie“: „Zone 1: mindestens 2G · Zone 2: mindestens 3G“
KATEGORIE = re.compile(r"zone\s*(\d+)\s*:\s*(?:mindestens|min\.)?\s*([123])\s*([GD])", re.I)
# Ohne Kopfangabe: Mindestkategorie nach ATEX (Richtlinie 2014/34/EU)
ATEX_KATEGORIE = {0: "1G", 1: "2G", 2: "3G", 20: "1D", 21: "2D", 22: "3D"}


def zonen_name(z):
    """("G", 1) -> „Zone 1“, ("D", 1) -> „Zone 21“."""
    art, nr = z
    return f"Zone {nr + (20 if art == 'D' else 0)}"


def bereiche(ex_area):
    """Kopfangabe „Innenraum Zone 1, Umgebung Zone 2; Gas …“ -> {"innenraum": "Zone 1", …}."""
    gefunden = {}
    for teil in re.split(r"[,;·]", ex_area or ""):
        m = ZONE.search(teil)
        name = ZONE.sub("", teil).strip(" :-–").lower()
        if m and name:
            gefunden[name] = f"Zone {m.group(1)}"
    return gefunden


def kategorie(zone, required_category):
    """Geforderte Kategorie für die Zone und woher sie kommt, z. B. („2G“, „Kopfangabe“)."""
    nr = int(zone.split()[-1])
    for z, k, art in KATEGORIE.findall(required_category or ""):
        if int(z) == nr:
            return f"{k}{art.upper()}", "Kopfangabe „Geforderte Kategorie“"
    return ATEX_KATEGORIE.get(nr), ("Mindestkategorie nach ATEX, weil die Kopfangabe die Zone "
                                    "nicht nennt")


def einordnen(item):
    """Vorschlag der Regeln für eine Position, ohne Entscheidung eines Menschen.

    Gibt ein dict mit vorschlag (ex_teil | pruefen), suchen (ob die Suche schon vor der
    Entscheidung läuft), regel, grund, quelle, zone, kategorie, kategorie_quelle, ex_kennung und
    hinweis zurück.
    """
    bom = item.bom
    ort = (item.location or "").strip()
    quelle = f"Zeile {item.row_number}, Einbauort: „{ort}“" if ort else \
        f"Zeile {item.row_number}, Einbauort leer"
    ex_kennung = bool(EX_KENNUNG.search(f"{item.description or ''} "
                                        f"{item.manufacturer_article_no or ''}"))
    zugehoerig = zugehoeriges_betriebsmittel(item)
    d = dict(vorschlag=PRUEFEN, suchen=False, quelle=quelle, zone=None, kategorie=None,
             kategorie_quelle=None, ex_kennung=ex_kennung, hinweis=None)

    z = zone_aus_text(ort)
    zone = zonen_name(z) if isinstance(z, tuple) else None
    aussen = bool(AUSSERHALB.search(ort))
    if not ort:
        d.update(regel="einbauort_fehlt", grund="In der Stückliste steht kein Einbauort. "
                                                     "Bitte entscheiden Sie, ob die Position ein "
                                                     "Ex-Teil ist.")
    elif zone and aussen:
        d.update(regel="widerspruch", zone=zone,
                 grund=f"Der Einbauort nennt {zone} und zugleich „außerhalb“, das passt nicht "
                       "zusammen. Bitte entscheiden Sie, ob die Position ein Ex-Teil ist.")
    elif aussen and zugehoerig:
        d.update(vorschlag=EX_TEIL, suchen=True, regel="zugehoeriges_betriebsmittel", grund=(
            f"Die Position sitzt außerhalb des Ex-Bereichs, ist nach {zugehoerig} aber "
            "zugehöriges Betriebsmittel. Ihre Stromkreise führen in den Ex-Bereich, deshalb "
            "braucht sie Ex-Dokumente."),
                 hinweis="Die Kategorie in Klammern gilt für die Stromkreise im Ex-Bereich, "
                         "nicht für den Einbauort.")
    elif aussen:
        d.update(regel="ausserhalb_ex", suchen=ex_kennung, grund=(
            "Die Position sitzt außerhalb des Ex-Bereichs, Bezeichnung oder Typ deuten aber auf "
            "Explosionsschutz hin. Bitte entscheiden Sie, ob sie Ex-Dokumente braucht, die Suche "
            "läuft schon." if ex_kennung else
            "Die Position sitzt außerhalb des Ex-Bereichs, und nichts deutet auf "
            "Explosionsschutz hin. Bitte entscheiden Sie, ob sie trotzdem ein Ex-Teil ist."))
    elif zone:
        d.update(vorschlag=EX_TEIL, suchen=True, regel="zone_im_einbauort", zone=zone,
                 grund=f"Der Einbauort liegt in {zone}.")
    elif zone := next((z_ for n, z_ in bereiche(bom.ex_area).items() if n in ort.lower()), None):
        d.update(vorschlag=EX_TEIL, suchen=True, regel="bereich_aus_kopfzeile", zone=zone,
                 grund=f"Der Einbauort „{ort}“ gehört laut Kopf der Stückliste zu {zone}.",
                 quelle=f"{quelle}; Kopfangabe „Ex-Bereich“: „{bom.ex_area}“")
    elif EX_BEREICH.search(ort):
        d.update(vorschlag=EX_TEIL, suchen=True, regel="ex_bereich_im_einbauort",
                 grund="Der Einbauort liegt im Ex-Bereich, eine Zone steht aber nicht dabei.",
                 hinweis="Ohne Zone lässt sich die Kategorie nicht prüfen.")
    else:
        d.update(regel="einbauort_unklar",
                 grund=f"Zum Einbauort „{ort}“ steht weder eine Zone in der Zeile noch im Kopf "
                       "der Stückliste. Bitte entscheiden Sie, ob die Position ein Ex-Teil ist.")

    if d["zone"]:
        d["kategorie"], d["kategorie_quelle"] = kategorie(d["zone"], bom.required_category)
        genannt = {f"Zone {n}" for n in ZONE.findall(bom.ex_area or "")}
        if not bom.ex_area:
            d["hinweis"] = ("Im Kopf der Stückliste fehlt die Angabe „Ex-Bereich“. Die Zone "
                            "stammt nur aus dem Einbauort.")
        elif genannt and d["zone"] not in genannt:
            d["hinweis"] = (f"{d['zone']} steht nicht im Kopf der Stückliste unter "
                            f"„Ex-Bereich“, dort stehen nur {' und '.join(sorted(genannt))}.")
    return d


def zugehoeriges_betriebsmittel(item):
    """Woran die Position als zugehöriges Betriebsmittel zu erkennen ist, sonst None: eine
    Kennzeichnung in Klammern (in Bezeichnung, Typ oder einem gefundenen Dokument der Position)
    oder Bezeichnung bzw. Typ eines Trennbausteins."""
    text = f"{item.description or ''} {item.manufacturer_article_no or ''}"
    # erst die Stückliste (gilt schon vor der Suche), dann die gefundenen Dokumente
    if KLAMMER_KENNZEICHNUNG.search(text):
        return "der Kennzeichnung in Klammern"
    if TRENNBAUSTEIN.search(item.description or ""):
        return f"der Bezeichnung „{item.description}“"
    if TYP_TRENNBAUSTEIN.search(item.manufacturer_article_no or ""):
        return f"dem Typ {item.manufacturer_article_no}"
    for f in getattr(item, "findings", None) or []:
        if f.status != "abgelehnt" and f.ex_marking_raw and \
                KLAMMER_KENNZEICHNUNG.search(f.ex_marking_raw):
            return f"der Kennzeichnung {f.ex_marking_raw} im gefundenen Dokument"
    return None


def frage(item):
    """Offene Ex-Frage oder None, wenn die Position als Ex-Teil gilt.

    Gibt (Text, sperrt) zurück: sperrt heißt Stand „klaerung“, sonst nur ein Hinweis zum Stand.
    """
    if item.ex_confirmed_at:
        return None
    d = einordnen(item)
    return None if d["vorschlag"] == EX_TEIL else (d["grund"], not d["suchen"])


def zeile(item):
    """Position mit Vorschlag, Entscheidung und Einordnung, für Oberfläche und Excel."""
    d = einordnen(item)
    if item.status == "kein_ex_teil":
        entscheidung, von = "kein_ex_teil", item.status_note
    elif item.ex_confirmed_at:
        entscheidung = "ex_teil"
        von = (f"{item.ex_confirmed_by} hat am {item.ex_confirmed_at:%d.%m.%Y} bestätigt, dass "
               "die Position ein Ex-Teil ist.")
    else:
        entscheidung = von = None
    einordnung = entscheidung or ("ex_teil" if d["vorschlag"] == EX_TEIL else "klaerung")
    d.update(item_id=item.id, row_number=item.row_number, pos=item.pos,
             profile_no=item.profile_no, description=item.description,
             manufacturer=item.manufacturer, manufacturer_article_no=item.manufacturer_article_no,
             quantity=item.quantity, location=item.location, status=item.status,
             regel_text=REGEL_TEXT[d["regel"]], entscheidung=entscheidung, entschieden=von,
             einordnung=einordnung, einordnung_text=EINORDNUNG_TEXT[einordnung])
    return d


def liste(bom):
    """EX-Bauteil-Liste eines Vorgangs: alle Positionen eingeordnet, dazu die Zählung."""
    zeilen = [zeile(i) for i in bom.items]
    anzahl = {k: sum(z["einordnung"] == k for z in zeilen) for k in EINORDNUNG_TEXT}
    return {"bom_id": bom.id, "machine": bom.machine, "ex_area": bom.ex_area,
            "required_category": bom.required_category, "total": len(zeilen),
            "ex_teil": anzahl["ex_teil"], "kein_ex_teil": anzahl["kein_ex_teil"],
            "klaerung": anzahl["klaerung"], "items": zeilen}
