"""Prüfregeln im Code, nicht im Sprachmodell (Regel 4). Ohne Datenbank und ohne PDF testbar.

- Gehört das Dokument zum Typ der Position? finde_typ sucht die Hersteller-Typbezeichnung ohne
  Rücksicht auf Schreibweise und trennt den genauen Typ von Reihe, Muster und Stichwort.
- Passt die Gerätekategorie zur Zone des Einbauorts? Zone 0: 1G, Zone 1: 1G/2G, Zone 2: 1G-3G.
- bewerte fasst beides zu ok | warnung | unklar mit Begründung zusammen. „ok“ gibt es nur für
  den genauen Typ mit passender Kategorie; alles, was nicht sicher ist, bleibt „unklar“.

- pruefe_angaben: Meldungen zu den weiteren Angaben (services/angaben.py): abgelaufen
  (Warnung), läuft bald ab (Hinweis), Konformitätserklärung nur nach 94/9/EG (Warnung; bei
  Bescheinigungen und unbekannter Art ein Hinweis), Hersteller weicht ab (Hinweis).

Ob jede Angabe eines Vorschlags im Dokument belegt ist, auch in Tabellen, prüft
services/belege.py. Vollständigkeit je Bauteil (M20) rechnet services/status.py.
"""

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache

# --- Typ ------------------------------------------------------------------------------------

# Auslassung in der Stückliste: „DSNU-…-EX“, „KEL 94XX“
AUSLASSUNG = re.compile(r"…|\.{3}|(?<=\d)X{2,}\b", re.I)
REIHE = re.compile(r"\b(?:Serie|Reihe|Baureihe|series)\b", re.I)
# ein Wort aus Kleinbuchstaben, z. B. „Kameragehäuse“: dann ist es eine Beschreibung
WORT = re.compile(r"(?<![^\W\d_])[A-ZÄÖÜa-zäöüß]?[a-zäöüß]{3,}(?![^\W\d_])")
FUELLWORT = re.compile(r"\bEx-Ausführung\b|\bAusführung\b|\bSerie\b|\bReihe\b|\bBaureihe\b"
                       r"|\bZone\s*\d+\b|\bEx\b", re.I)
TRENNER = re.compile(r"[\s\-./,;:_()]+")


@dataclass(frozen=True)
class Typangabe:
    text: str      # wie in der Stückliste
    art: str       # typ | muster | reihe | beschreibung
    begriff: str   # wonach gesucht wird
    offen: bool = False  # Muster wie „VPPM-6…“: im Dokument darf direkt etwas folgen („VPPM-6L“)
    # Muster mit zu kurzem Anfang („NJ…-N“): der Teil hinter der Auslassung („N“). Dann zählt
    # nur eine Bezeichnung, die mit dem Anfang beginnt und diesen Schluss als eigenes Glied hat.
    schluss: str = ""


@dataclass(frozen=True)
class Treffer:
    genau: bool         # der ganze Begriff, nicht Teil einer längeren Bezeichnung
    laenge: int         # so viele Buchstaben und Ziffern stimmen überein
    im_text: bool       # sonst nur im Dateinamen
    seite: int | None   # ab 1, None beim Dateinamen
    wie_geschrieben: str
    textstelle: str     # Text um die Fundstelle


def lies_typangabe(text):
    """Ordnet die Hersteller-Artikelnr. der Stückliste ein, ohne etwas zu ergänzen."""
    text = (text or "").strip()
    if REIHE.search(text):
        art = "reihe"
    elif AUSLASSUNG.search(text):
        art = "muster"
    elif WORT.search(text):
        art = "beschreibung"
    else:
        return Typangabe(text, "typ", text)
    vorn = AUSLASSUNG.split(text)[0]
    # „NJ…“ ist zu kurz, um alles mit „NJ“ am Anfang zu nehmen
    offen = art == "muster" and vorn[-1:].isalnum() and len(_normiert(vorn)[0]) >= 3
    begriff = vorn if art == "muster" else " ".join(TRENNER.split(FUELLWORT.sub(" ", vorn)))
    begriff = begriff.strip(" -./,;:_")
    schluss = ""
    if art == "muster" and 0 < len(_normiert(begriff)[0]) < 3:
        schluss = AUSLASSUNG.split(text)[-1].strip(" -./,;:_")
        schluss = schluss if re.fullmatch(r"[^\W_]+", schluss) else ""
    return Typangabe(text, art, begriff, offen, schluss)


@lru_cache(maxsize=4096)
def _normiert(text):
    """Nur Buchstaben und Ziffern, groß; dazu je Zeichen die Stelle im Originaltext."""
    zeichen, stelle = [], []
    for i, c in enumerate(text):
        if c.isalnum():
            for g in c.upper():
                zeichen.append(g)
                stelle.append(i)
    return "".join(zeichen), stelle


def _links_frei(text, start):
    if start == 0:
        return True
    davor = text[start - 1]
    if davor.isalnum():
        return False
    return not (davor in "-./," and start >= 2 and text[start - 2].isalnum())


# Was hinter einer Fundstelle zeigt, dass die Bezeichnung dort weitergeht:
# „KCD2-SOT-EX1.LB.SP“, „VACF-B-K1-1A-1-⏎EX4-M“, „MP377 15" Touch“, „IM 152-1 PN“
# kurzer Zusatz wie „ PN“ oder „ 15“, aber nicht „ II“ (Anfang der Ex-Kennzeichnung)
_ZUSATZ = r" (?!I{1,3}(?![^\W_]))[A-Z0-9]{1,3}(?![^\W_])"
WEITER = re.compile(rf"[^\W_]|[-./,][^\W_]|-[ \t]*\n\s*[^\W_]|{_ZUSATZ}")
BEZEICHNUNG = re.compile(rf"(?:-[ \t]*\n[ \t]*|[^\s_])*(?:{_ZUSATZ}[\"”]?)?")


def _stellen(begriff, text):
    """Fundstellen von begriff in text, ohne Rücksicht auf Groß/klein, Leer- und Satzzeichen."""
    nadel = _normiert(begriff)[0]
    heu, stelle = _normiert(text)
    if not nadel:
        return
    i = heu.find(nadel)
    while i >= 0:
        start, ende = stelle[i], stelle[i + len(nadel) - 1] + 1
        if _links_frei(text, start):
            yield start, ende
        i = heu.find(nadel, i + 1)


def _lesbar(text):
    return re.sub(r"\s+", " ", re.sub(r"-[ \t]*\n\s*", "-", text)).strip()


def _treffer(angabe, begriff, text, start, ende, genau, seite):
    if not (genau and angabe.art == "typ"):  # die längere Bezeichnung im Dokument zeigen
        ende = BEZEICHNUNG.match(text, ende).end()
    wie = _lesbar(text[start:ende]).rstrip(",.;:)")
    stelle = _lesbar(text[max(0, start - 120):ende + 150])
    return Treffer(genau, len(_normiert(begriff)[0]), seite is not None, seite, wie, stelle)


def _teilbegriffe(begriff, mindestens):
    """„VACF-B-A1-1“ -> „VACF-B-A1-1“, „VACF-B-A1“, „VACF-B“, „VACF“: vom längsten zum kürzesten.

    Gekürzte Begriffe brauchen einen Buchstaben, sonst passen sie auf jede Zahl im Text.
    """
    for m in reversed(list(re.finditer(r"[^\W_]+", begriff))):
        teil = begriff[:m.end()]
        if len(_normiert(teil)[0]) < mindestens:
            continue
        if teil == begriff or re.search(r"[^\W\d_]", teil):
            yield teil


def _muster_mit_schluss(angabe, seiten):
    """Treffer für ein Muster wie „NJ…-N“: Anfang, dann Ziffern und Glieder ohne Leerzeichen,
    dann der Schluss als eigenes Glied („NJ0,8-5GM-N...“, „NJ 8-18GM-N“; nicht „NJ10-30GK-SN“).
    Groß und klein zählen, weil der Anfang so kurz ist."""
    muster = re.compile(rf"(?<![^\W_]){re.escape(angabe.begriff)}[ \t]?(?=\d)[^\s_]*?"
                        rf"[-./]{re.escape(angabe.schluss)}(?![^\W_])")
    for nr, text in enumerate(seiten, start=1):
        if m := muster.search(text):
            return _treffer(angabe, angabe.begriff + angabe.schluss, text, m.start(), m.end(),
                            False, nr)
    return None


def finde_typ(angabe, seiten, dateiname=""):
    """Bester Treffer für die Typangabe in den Seiten, sonst im Dateinamen, sonst None.

    Genau heißt: der ganze Begriff steht da und geht nicht weiter. Beim Typ zählt ein genauer
    Treffer auf jeder Seite, sonst die erste Fundstelle. Ohne Fundstelle wird der Begriff von
    hinten gekürzt (bis 3 Zeichen im Text, 2 im Dateinamen).
    """
    for quelle, mindestens in ((seiten, 3), ((dateiname,), 2)):
        teil = None
        for begriff in _teilbegriffe(angabe.begriff, mindestens):
            for nr, text in enumerate(quelle, start=1):
                for start, ende in _stellen(begriff, text):
                    seite = nr if quelle is seiten else None
                    ganz = begriff == angabe.begriff
                    genau = ganz and not WEITER.match(text, ende)
                    if genau and angabe.art == "typ":
                        return _treffer(angabe, begriff, text, start, ende, True, seite)
                    if teil is None and (ganz and angabe.offen
                                         or not re.match(r"[^\W_]", text[ende:ende + 1])):
                        teil = _treffer(angabe, begriff, text, start, ende, genau, seite)
            if teil:
                return teil
        if quelle is seiten and angabe.schluss and (teil := _muster_mit_schluss(angabe, seiten)):
            return teil
    return None


def steht_im_text(wert, text, angabe=None):
    """Steht wert in text, ohne Rücksicht auf Groß/klein, Leer- und Satzzeichen? Für die Belege.

    Mit angabe (Typ der Stückliste) zählt wie in finde_typ nur der ganze Typ, der nicht
    weitergeht. Kurze Werte wie „II 2G“ (bis 6 Zeichen) nicht mitten in einer Bezeichnung.
    """
    if angabe is not None and angabe.art == "typ":
        return any(not WEITER.match(text, ende) for _, ende in _stellen(angabe.begriff, text))
    nadel = _normiert(wert or "")[0]
    if not nadel:
        return False
    heu, stelle = _normiert(text)
    i = heu.find(nadel)
    while i >= 0:
        start, ende = stelle[i], stelle[i + len(nadel) - 1] + 1
        if len(nadel) > 6 or ((start == 0 or not text[start - 1].isalnum())
                              and not text[ende:ende + 1].isalnum()):
            return True
        i = heu.find(nadel, i + 1)
    return False


def pruefe_typ(angabe, treffer, text_lesbar=True):
    """(ergebnis, meldung): „ok“ nur, wenn der genaue Typ im Text steht. treffer None: der Typ
    steht nirgends (nur bei Dateien, die ein Mensch hochgeladen hat)."""
    if treffer is None:
        meldung = (f"Der Typ „{angabe.text}“ steht weder im Dokument noch im Dateinamen."
                   if angabe.text else "Die Stückliste nennt keinen Typ. Deshalb lässt sich "
                                       "nicht prüfen, ob das Dokument dazu passt.")
        return "unklar", meldung + ("" if text_lesbar else " Im Dokument ist kein Text lesbar.")
    wie = f"„{treffer.wie_geschrieben}“"
    if angabe.art == "muster":
        meldung = (f"Die Stückliste nennt nur ein Typmuster („{angabe.text}“), im Dokument steht "
                   f"{wie}. Ob der eingebaute Typ dazu gehört, ist nicht geprüft.")
    elif angabe.art == "reihe":
        meldung = (f"Die Stückliste nennt nur eine Baureihe („{angabe.text}“), im Dokument steht "
                   f"{wie}. Ob der eingebaute Typ dazu gehört, ist nicht geprüft.")
    elif angabe.art == "beschreibung":
        meldung = (f"Die Stückliste nennt keinen Typ, nur „{angabe.text}“. Das Dokument passt nur "
                   f"über das Stichwort {wie}.")
    elif not treffer.genau:
        meldung = (f"Im Dokument steht nur {wie}, nicht genau der Typ „{angabe.text}“. "
                   "Ob das Dokument dazu gehört, ist nicht sicher.")
    elif treffer.im_text:
        return "ok", (f"Der Typ „{angabe.text}“ steht genau so im Dokument, auf Seite "
                      f"{treffer.seite}.")
    else:
        meldung = f"Der Typ „{angabe.text}“ steht nur im Dateinamen."
    if not treffer.im_text:
        meldung += (" Im Dokument ist kein Text lesbar, geprüft ist nur der Dateiname."
                    if not text_lesbar else " Im lesbaren Text des Dokuments steht er nicht.")
    return "unklar", meldung


# --- Zone und Kategorie ---------------------------------------------------------------------

# „II 2G“, „II 2 G“, „II 2GD“, „II 1/2G“, „II (1) G“, „II 2 G (1) D“
_KAT = r"\(?[123](?:[ \t]*/[ \t]*[123])?\)?[ \t]*(?:G[ \t]?D|G|D)(?![a-z])"
KENNZEICHNUNG = re.compile(rf"(?<![A-Za-z])II[ \t]*{_KAT}(?:[ \t]*{_KAT})*")
KATEGORIE = re.compile(r"(\()?([123])(?:[ \t]*/[ \t]*([123]))?\)?[ \t]*(G[ \t]?D|G|D)")


def kennzeichnungen(text):
    """Ex-Kennzeichnungen wie geschrieben, z. B. „II 2G Ex ia IIC T4 Gb“, ohne Dubletten."""
    gefunden = []
    for m in KENNZEICHNUNG.finditer(text):
        zeile = text[m.start():m.start() + 200].split("\n")
        k = zeile[0].strip()
        if k == m.group().strip() and len(zeile) > 1 and re.match(r"\s*E?Ex ", zeile[1]):
            k += " " + zeile[1].strip()  # „II 2 G (1) G⏎Ex eb [ia Ga/ib] IIC T4 Gb“
        k = re.sub(r"\s+", " ", k)[:80]
        if k not in gefunden:
            gefunden.append(k)
    return gefunden


def kategorien(kennzeichnung):
    """„II 2 G (1) D“ -> {(2, "G", False), (1, "D", True)}: (Kategorie, Gas/Staub, zugehörig).

    Zugehörig (in Klammern) heißt: das Gerät selbst steht außerhalb, nur seine Stromkreise
    führen in den Ex-Bereich. Bei „1/2“ zählt für den Einbauort die 2.
    """
    m = KENNZEICHNUNG.match(kennzeichnung)
    if not m:
        return set()
    return {(max(int(a), int(b or a)), g_oder_d, bool(klammer))
            for klammer, a, b, art in KATEGORIE.findall(m.group()[2:])
            for g_oder_d in "GD" if g_oder_d in art}


# wie ex_liste.AUSSERHALB (ein Test prüft, dass beide gleich sind)
AUSSERHALB = re.compile(r"außerhalb|ausserhalb|nicht[ -]?ex|kein[ -]?ex|sicherer bereich"
                        r"|safe area|non[ -]?hazardous", re.I)


def zone(einbauort):
    """„Zone 1“ -> ("G", 1), „Zone 21“ -> ("D", 1), „außerhalb Ex“ oder „sicherer Bereich“ ->
    "ausserhalb", sonst None."""
    text = (einbauort or "").lower()
    zonen = [int(z) for z in re.findall(r"\bzone\s*(0|1|2|20|21|22)\b", text)]
    if zonen:
        z = min(zonen, key=lambda z: z % 20)  # die strengste Zone zählt
        return ("D", z - 20) if z >= 20 else ("G", z)
    if AUSSERHALB.search(text):
        return "ausserhalb"
    return None


def _namen(kats):
    return ", ".join(f"({k}){a}" if zug else f"{k}{a}" for k, a, zug in sorted(kats))


def kategorie_namen(kennz):
    """[„II 2G Ex h“, „II (1) D“] -> „(1)D, 2G“, leer ohne lesbare Kategorie."""
    return _namen(set().union(*(kategorien(k) for k in kennz)) if kennz else set())


def pruefe_zone(einbauort, kennz):
    """(ergebnis, meldung) für den Einbauort und die Kennzeichnungen eines Dokuments.

    Eine Kategorie in Klammern („II (1)G [Ex ia Ga]“, zugehöriges Betriebsmittel) gilt für die
    Stromkreise im Ex-Bereich und wird nie als passende Kategorie für eine Zone gezählt."""
    z = zone(einbauort)
    kats = set().union(*(kategorien(k) for k in kennz)) if kennz else set()
    if z is None:
        return "unklar", (f"Der Einbauort „{einbauort or ''}“ nennt keine Zone. Deshalb lässt "
                          "sich die Kategorie nicht prüfen.")
    if not kats:
        return "unklar", ("Im Text steht keine lesbare Ex-Kennzeichnung. Deshalb lässt sich die "
                          "Kategorie nicht prüfen.")
    if z == "ausserhalb":
        if any(zug for _, _, zug in kats):
            return "ok", (f"Das Gerät ist ein zugehöriges Betriebsmittel, das Dokument nennt die "
                          f"Kategorie {_namen(kats)}. Es darf außerhalb des Ex-Bereichs eingebaut "
                          "werden, das passt.")
        return "unklar", (f"Der Einbauort liegt außerhalb des Ex-Bereichs, das Dokument nennt "
                          f"aber die Kategorie {_namen(kats)}. Bitte prüfen Sie den Einbauort "
                          "und das Dokument.")
    art, nr = z
    zonename = f"Zone {nr + (20 if art == 'D' else 0)}"
    eigene = {(k, a, zug) for k, a, zug in kats if a == art and not zug}
    if not eigene:
        if any(a == art for _, a, _ in kats):
            return "warnung", (f"Das Dokument nennt nur die Kategorie {_namen(kats)} in "
                               "Klammern. Ein solches zugehöriges Betriebsmittel muss außerhalb "
                               f"des Ex-Bereichs stehen, laut Stückliste sitzt es aber in "
                               f"{zonename}.")
        return "unklar", (f"Für {zonename} steht keine passende Kategorie im Dokument, nur "
                          f"{_namen(kats)}.")
    passend = {k for k in eigene if k[0] <= nr + 1}
    verlangt = " oder ".join(f"{k}{art}" for k in range(1, nr + 2))
    if passend == eigene:
        return "ok", f"Die Kategorie {_namen(eigene)} passt zu {zonename}."
    if not passend:
        return "warnung", (f"Die Kategorie {_namen(eigene)} reicht für {zonename} nicht aus. "
                           f"Verlangt ist {verlangt}.")
    return "unklar", (f"Das Dokument nennt mehrere Kategorien ({_namen(eigene)}), zu {zonename} "
                      f"passt nur {_namen(passend)}. Welche zum Typ gehört, ist nicht sicher.")


def bewerte(typ, zone_):
    """Typ- und Zonenprüfung zusammen: Widerspruch -> warnung, beides sicher -> ok, sonst unklar."""
    ergebnisse = {typ[0], zone_[0]}
    ergebnis = "warnung" if "warnung" in ergebnisse else "ok" if ergebnisse == {"ok"} else "unklar"
    return ergebnis, f"{typ[1]} {zone_[1]}"


# --- Angaben aus dem Dokument (services/angaben.py) -----------------------------------------

RECHTSFORM = re.compile(r"\b(?:gmbh|ag|se|kg|co|bv|b\.v\.|ltd|inc|llc|sa|spa"
                        r"|aktiengesellschaft)\b")
# OCR verliest einzelne Zeichen („Pepperi“ statt „Pepperl“); Standard für HERSTELLER_AEHNLICHKEIT
AEHNLICHKEIT = 0.9
# seit diesem Tag gilt die Richtlinie 2014/34/EU, 94/9/EG ist aufgehoben (Art. 43 2014/34/EU)
RICHTLINIE_NEU_AB = "20.04.2016"
ERKLAERUNG = ("eu_konformitaetserklaerung",)
BESCHEINIGUNG = ("eu_baumusterpruefbescheinigung",)


def _woerter(name):
    return re.findall(r"[a-z0-9äöüß]+", RECHTSFORM.sub(" ", (name or "").lower()))


def hersteller_passt(dokument, stueckliste, aehnlichkeit=AEHNLICHKEIT):
    """Passt der Hersteller im Dokument zum Hersteller der Stückliste? None, wenn einer fehlt.

    Allgemeine Regeln, keine Namensliste: ohne Rechtsform, Groß/klein und Satzzeichen ist der
    eine Name im anderen enthalten („Siemens“ in „Siemens Aktiengesellschaft“); oder die
    Stückliste nennt die Anfangsbuchstaben („P+F“ für „Pepperl+Fuchs“); oder der Anfang ist bis
    auf einzelne Zeichen gleich (OCR, Schwelle aehnlichkeit).
    """
    dw, sw = _woerter(dokument), _woerter(stueckliste)
    d, s = "".join(dw), "".join(sw)
    if not d or not s:
        return None
    if s in d or d in s:
        return True
    if 2 <= len(s) <= 4 and s == "".join(w[0] for w in dw):
        return True
    return len(s) >= 6 and SequenceMatcher(None, d[:len(s)], s).ratio() >= aehnlichkeit


def _tt(iso):
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _auf_seite(a):
    return f" auf Seite {a['seite']}" if a.get("seite") else ""


def pruefe_angaben(angaben, heute, warn_tage=180, doc_type=None):
    """Meldungen zu den Angaben eines Dokuments: [(stufe, text)], stufe „warnung“ | „hinweis“.

    Jede Regel folgt aus der Richtlinie oder aus einfacher Logik:
    - Datum hinter „gültig bis“ liegt vor heute -> warnung (Logik).
    - läuft in warn_tage (ABLAUF_WARNUNG_TAGE) Tagen ab -> hinweis (einstellbare Schwelle).
    - nur 94/9/EG, nicht 2014/34/EU: in einer EU-Konformitätserklärung -> warnung (seit
      20.04.2016 muss sie 2014/34/EU nennen, Art. 43 und Anhang X); in einer
      Baumusterprüfbescheinigung -> hinweis (gilt nach Art. 41 Abs. 2 weiter); Art unbekannt ->
      hinweis.
    - Hersteller im Dokument weicht von der Stückliste ab -> hinweis (Logik, Vergleich siehe
      hersteller_passt).
    Per OCR gelesene Werte sagen, dass sie unsicher sind. Eine Vorprüfung, kein Urteil.
    """
    meldungen = []
    bis = angaben.get("gueltig_bis") or {}
    if bis.get("status") == "datum" and bis.get("wert"):
        tag = date.fromisoformat(bis["wert"])
        ocr = (" Das Datum stammt aus einem eingescannten Blatt und ist nicht sicher gelesen. "
               "Bitte prüfen Sie es im Dokument.") if bis.get("ocr") else ""
        if tag < heute:
            meldungen.append(("warnung", f"Das Zertifikat ist seit dem {_tt(bis['wert'])} "
                                         f"abgelaufen, so steht es{_auf_seite(bis)}.{ocr}"))
        elif (tag - heute).days <= warn_tage:
            meldungen.append(("hinweis", f"Das Zertifikat läuft am {_tt(bis['wert'])} ab, also in "
                                         f"{(tag - heute).days} Tagen. So steht es"
                                         f"{_auf_seite(bis)}.{ocr}"))
    richtl = {r["wert"]: r for r in angaben.get("richtlinien") or []}
    if "94/9/EG" in richtl and "2014/34/EU" not in richtl:
        seite = _auf_seite(richtl["94/9/EG"])
        if doc_type in ERKLAERUNG:
            meldungen.append(("warnung", f"Die Konformitätserklärung nennt{seite} nur die alte "
                              f"Richtlinie 94/9/EG. Seit dem {RICHTLINIE_NEU_AB} muss sie die "
                              "Richtlinie 2014/34/EU nennen. Bitte fordern Sie beim Hersteller "
                              "eine aktuelle Erklärung an."))
        elif doc_type in BESCHEINIGUNG:
            meldungen.append(("hinweis", f"Das Zertifikat wurde nach der alten Richtlinie 94/9/EG "
                              "ausgestellt. Es gilt nach der Übergangsregel der Richtlinie "
                              "2014/34/EU weiter. Die Konformitätserklärung zu diesem Gerät muss "
                              "aber die neue Richtlinie nennen."))
        else:
            meldungen.append(("hinweis", f"Das Dokument nennt{seite} nur die alte Richtlinie "
                              "94/9/EG. Ist es eine Konformitätserklärung, ist sie veraltet, denn "
                              f"seit dem {RICHTLINIE_NEU_AB} muss sie die Richtlinie 2014/34/EU "
                              "nennen."))
    h = angaben.get("hersteller") or {}
    if h.get("passt") is False:
        meldungen.append(("hinweis", f"Als Hersteller steht im Dokument „{h['wert']}“"
                                     f"{_auf_seite(h)}, in der Stückliste aber "
                                     f"„{h.get('stueckliste')}“. Bitte prüfen Sie, ob das "
                                     "Dokument zu diesem Bauteil gehört."))
    return meldungen


def bewerte_angaben(ergebnis, meldung, meldungen):
    """Eine Warnung macht das Ergebnis zu „warnung“, Hinweise ändern es nicht; alles steht in
    der Meldung."""
    if not meldungen:
        return ergebnis, meldung
    if any(s == "warnung" for s, _ in meldungen):
        ergebnis = "warnung"
    zusatz = " ".join(t for _, t in meldungen)
    return ergebnis, f"{meldung} {zusatz}".strip() if meldung else zusatz
