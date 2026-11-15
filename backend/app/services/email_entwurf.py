"""E-Mail an den Hersteller für fehlende Dokumente (M16, M17).

Alle Fakten (Typ, Hersteller, Dokumente, Maschine) setzt der Code ein. Das Sprachmodell darf
höchstens den einleitenden Absatz formulieren, und nur wenn EMAIL_MIT_LLM gesetzt ist; sonst und
bei jedem Zweifel gilt die feste Vorlage. Ein Mensch gibt frei.

Empfänger (services/kontakte.py): Der Entwurf nennt die gespeicherte Adresse des Herstellers als
eigentlichen Empfänger. Standard EMAIL_NUR_TEST: verschickt wird trotzdem nur an
MAIL_TEST_ADDRESS. Ohne gespeicherte Adresse wird nicht gesendet (KontaktFehlt, 409).
"""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone

from flask import current_app

from .. import llm
from ..db import db
from ..models import EmailDraft
from ..models.bom import BomEvent
from . import kontakte, modelle, status, versand

MODELL_WARTEZEIT = 60  # Sekunden für alle Modell-Anfragen zusammen, danach gilt die Vorlage

# Dokumentart -> Zeile der Anfrage; was schon geprüft in der Datenbank liegt, wird nicht erfragt
DOKUMENTE = (("eu_konformitaetserklaerung", "EU-Konformitätserklärung (zwingend erforderlich)"),
             ("eu_baumusterpruefbescheinigung", "EU-Baumusterprüfbescheinigung, falls vorhanden"),
             ("bedienungsanleitung", "Betriebsanleitung, falls vorhanden"))

ANWEISUNG = (
    "Schreibe auf Deutsch nur die Anrede und einen kurzen einleitenden Absatz (zwei bis drei "
    "Sätze) einer geschäftlichen E-Mail: Wir bitten den Hersteller um die Ex-Dokumente zu seinem "
    "Produkt, für die Dokumentation einer Abfüllanlage für explosionsgefährdete Bereiche. "
    "Sachlich, höflich, kurz. Nenne Hersteller und Typbezeichnung genau so, wie sie angegeben "
    "sind, Zeichen für Zeichen. Keine Aufzählung, keine Grußformel, keine Namen, Telefonnummern, "
    "Fristen oder Platzhalter: Die Angaben zum Bauteil, die Liste der Dokumente und den Gruß "
    "fügen wir selbst an. Gib nur diesen Text zurück.")


# Zeile der Anfrage, wenn die Konformitätserklärung nur in anderen Sprachen vorliegt
PFLICHT_FASSUNG = ("EU-Konformitätserklärung in deutscher Sprache (zwingend erforderlich). Uns "
                   "liegt sie bisher nur in einer anderen Sprache vor.")


SendeFehler = versand.SendeFehler  # der Postausgang hat die E-Mail nicht angenommen (502)


class KontaktFehlt(SendeFehler):
    """Für den Hersteller ist keine E-Mail-Adresse gespeichert: nicht senden (409)."""
    code = 409


def braucht_entwurf(item):
    """Position ohne Fund, noch ohne Entwurf, mit bekanntem Hersteller."""
    return item.status == "nicht_gefunden" and not item.email_drafts and bool(item.manufacturer)


def betreff(item):
    return f"Anfrage Ex-Dokumente: {item.manufacturer} {item.manufacturer_article_no}"


def einleitung(item):
    return ("Sehr geehrte Damen und Herren,\n\n"
            "für die Dokumentation einer Abfüllanlage für explosionsgefährdete Bereiche benötigen "
            f"wir die Ex-Dokumente zu Ihrem Produkt {item.manufacturer_article_no} "
            f"({item.manufacturer}).")


def angaben(item):
    """Fester Teil: Bauteil, Einsatz, Dokumente, Gruß. Kommt nie vom Sprachmodell."""
    bom = item.bom
    bauteil = [("Hersteller", item.manufacturer),
               ("Typbezeichnung", item.manufacturer_article_no),
               ("Bezeichnung", item.description),
               ("Unsere Artikelnummer", item.profile_no)]
    einsatz = [("Maschine", bom.machine), ("Ex-Bereich", bom.ex_area),
               ("Einbauort", item.location)]

    def liste(zeilen):
        return "\n".join(f"- {z}" for z in zeilen)

    geprueft = status.gepruefte_arten(item.profile_no) if item.profile_no else set()
    # Pflichtsprache (M8, Entscheidung des Teams): geprüft, aber nicht auf Deutsch -> gezielt
    # nach der deutschen Fassung fragen
    ohne_pflicht = status.pflichtsprache_fehlt(item)
    dokumente = [PFLICHT_FASSUNG if art in ohne_pflicht else text
                 for art, text in DOKUMENTE if art not in geprueft or art in ohne_pflicht]

    return (f"Bauteil:\n{liste(f'{n}: {w}' for n, w in bauteil if w)}\n\n"
            f"Einsatz:\n{liste(f'{n}: {w}' for n, w in einsatz if w)}\n\n"
            f"Bitte senden Sie uns folgende Dokumente:\n{liste(dokumente)}\n\n"
            "Die EU-Konformitätserklärung benötigen wir unbedingt in deutscher Sprache. Weitere "
            "Sprachfassungen, zum Beispiel Englisch, senden Sie gern zusätzlich mit.\n\n"
            "Vielen Dank im Voraus.\n\n"
            "Mit freundlichen Grüßen\n"
            f"{current_app.config.get('MAIL_FROM_NAME') or 'ATEX Agent'}\n")


def vorlage(item):
    """Fester Text der Anfrage. Funktioniert immer, ohne Sprachmodell."""
    return f"{einleitung(item)}\n\n{angaben(item)}"


def einleitung_ist_brauchbar(text, item):
    """Modelltext nur mit unverändertem Typ und Hersteller, ohne Platzhalter und ohne Gruß."""
    if not text or len(text) > 800 or "[" in text or "Grüß" in text:
        return False
    return item.manufacturer_article_no in text and item.manufacturer in text


def _frage_modell(app, item, modell=None):
    angaben_fuer_modell = (f"Hersteller: {item.manufacturer}\n"
                           f"Typbezeichnung: {item.manufacturer_article_no}\n"
                           f"Bezeichnung: {item.description or '-'}\n"
                           f"Einbauort: {item.location}")
    with app.app_context():  # eigener Kontext im Thread: das Modell des Vorgangs neu setzen
        modelle.setze_lauf_modell(modell)
        return llm.chat([{"role": "system", "content": ANWEISUNG},
                         {"role": "user", "content": angaben_fuer_modell}])


def _modell_einleitungen(items, modell=None):
    """Fragt das Modell parallel je Position. Ausfall, Zeitüberschreitung, Zweifel: kein Eintrag."""
    app = current_app._get_current_object()
    pool = ThreadPoolExecutor(max_workers=4)
    auftraege = {pool.submit(_frage_modell, app, i, modell): i for i in items}
    fertig, _ = wait(auftraege, timeout=MODELL_WARTEZEIT)
    pool.shutdown(wait=False, cancel_futures=True)
    texte = {}
    for auftrag in fertig:
        item = auftraege[auftrag]
        try:
            text = (auftrag.result() or "").strip()
        except Exception as e:  # Modell nicht erreichbar, Fehlerantwort usw.
            current_app.logger.warning("Sprachmodell für Pos. %s: %s", item.pos, e)
            continue
        if einleitung_ist_brauchbar(text, item):
            texte[item.id] = text
        else:
            current_app.logger.warning("Modelltext für Pos. %s verworfen: %r", item.pos, text)
    return texte


def _mit_modell(items):
    """Einleitungen vom Sprachmodell des Vorgangs (sonst Standard „email“); im Protokoll steht
    je Entwurf, welches Modell ihn formuliert hat oder dass die Vorlage gilt."""
    bom_id = items[0].bom_id
    try:
        modell = modelle.fuer("email", getattr(items[0].bom, "llm_modell", None)).name
    except modelle.KonfigFehler as e:
        current_app.logger.warning("E-Mail-Entwürfe ohne Sprachmodell: %s", e)
        for i in items:
            db.session.add(BomEvent(bom_id=bom_id, bom_item_id=i.id, text=(
                f"Position {i.pos or i.row_number}: Das Sprachmodell steht nicht zur "
                "Verfügung. Der E-Mail-Entwurf nutzt deshalb die feste Vorlage.")))
        return {}
    texte = _modell_einleitungen(items, modell)
    for i in items:
        text = (f"Die Einleitung des E-Mail-Entwurfs hat das Sprachmodell „{modell}“ geschrieben."
                if i.id in texte else
                f"Das Sprachmodell „{modell}“ hat keine brauchbare Einleitung geliefert. Der "
                "E-Mail-Entwurf nutzt deshalb die feste Vorlage.")
        db.session.add(BomEvent(bom_id=bom_id, bom_item_id=i.id,
                                text=f"Position {i.pos or i.row_number}: {text}"))
    return texte


def lege_entwuerfe_an(bom):
    """Legt Entwürfe für alle Positionen an, die einen brauchen. Gibt die neuen zurück.

    Speichern (commit) macht der Aufrufer.
    """
    items = [i for i in bom.items if braucht_entwurf(i)]
    einleitungen = {i.id: einleitung(i) for i in items}
    if items and current_app.config.get("EMAIL_MIT_LLM", False):
        einleitungen.update(_mit_modell(items))
    test = current_app.config["MAIL_TEST_ADDRESS"]
    neu = []
    for i in items:
        # Empfänger aus den Herstellerkontakten; ohne Adresse vorerst die Test-Adresse
        adresse = kontakte.empfaenger(i)["versand_an"] or test
        entwurf = EmailDraft(to_address=adresse, subject=betreff(i),
                             body=f"{einleitungen[i.id]}\n\n{angaben(i)}")
        i.email_drafts.append(entwurf)
        neu.append(entwurf)
        status.aktualisiere(i)  # Hinweis, falls die Adresse des Herstellers fehlt
    return neu


def mit_position(entwurf):
    """Entwurf mit den Angaben der Position, für die Ansicht."""
    i = entwurf.item
    d = entwurf.to_dict()
    d.update(pos=i.pos, description=i.description, manufacturer=i.manufacturer,
             manufacturer_article_no=i.manufacturer_article_no, location=i.location,
             searched_in=i.searched_in, item_status=i.status)
    d.update(empfaenger(entwurf))
    return d


def empfaenger(entwurf):
    """empfaenger_hersteller, kontakt_fehlt, versand_an für Ansicht und Oberfläche. Gesendet:
    versand_an ist die Adresse, an die es ging."""
    e = kontakte.empfaenger(entwurf.item)
    d = {"empfaenger_hersteller": e["empfaenger_hersteller"],
         "kontakt_fehlt": e["kontakt_fehlt"], "versand_an": e["versand_an"]}
    if entwurf.status == "gesendet":
        d["versand_an"] = entwurf.to_address
    return d


def versende(an, titel, text):
    """Eine E-Mail über das eingestellte Postfach (services/versand.py); SendeFehler, wenn sie
    nicht angenommen wurde."""
    versand.sende(an, titel, text)


def sende(entwurf):
    """Schickt einen freigegebenen Entwurf. Speichern macht der Aufrufer.

    Ohne gespeicherte Adresse des Herstellers: KontaktFehlt. Mit EMAIL_NUR_TEST (Standard) geht
    die E-Mail an MAIL_TEST_ADDRESS, nie an die Adresse des Herstellers.
    """
    e = kontakte.empfaenger(entwurf.item)
    if e["kontakt_fehlt"]:
        raise KontaktFehlt(kontakte.fehlt_text(entwurf.item))
    adresse = e["versand_an"]
    versende(adresse, entwurf.subject, entwurf.body)
    entwurf.to_address = adresse
    entwurf.status = "gesendet"
    entwurf.sent_at = datetime.now(timezone.utc)
    status.aktualisiere(entwurf.item)
