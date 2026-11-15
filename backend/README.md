# Backend

Flask-API mit SQLAlchemy. Hier liegt die gesamte Fachlogik; die Oberfläche ruft nur diese API auf.

```
backend/
├── wsgi.py                 Einstieg für gunicorn und flask
├── app/
│   ├── __init__.py         create_app, Befehle init-db (mit services/schema.py), ocr, zert-abgleich
│   │                       (angaben-nachlesen: routes/fristen.py; benutzer-anlegen: auth.py)
│   ├── auth.py             Anmeldung, Rollen, Name aus der Sitzung (an einer Stelle)
│   ├── config.py           Einstellungen aus Umgebungsvariablen
│   ├── db.py               SQLAlchemy-Objekt
│   ├── models/
│   │   ├── stammdaten.py   Part, Manufacturer, Document, DocumentLanguage, PartDocument,
│   │   │                   StammdatenAenderung
│   │   ├── bom.py          Bom (Vorgang), BomItem (Position), BomIssue, Finding, EmailDraft,
│   │   │                   ItemCorrection, PurchasingEmail, BomEvent
│   │   ├── frage.py        Question, QuestionComment
│   │   ├── kontakt.py      HerstellerKontakt
│   │   └── benutzer.py     Benutzer
│   ├── routes/             HTTP-Endpunkte, je Datei ein Blueprint; oberflaeche.py für web/
│   ├── services/           Fachlogik ohne HTTP, je Datei ein Schritt des Ablaufs
│   └── llm/                Zugang zum Sprachmodell
├── tools/                  install_html2markdown.py (für das Image)
└── tests/
```

Regel für neue Funktionen: Die Logik kommt nach `services/`, der Endpunkt in `routes/` bleibt dünn.
`services/excel_import.py` und `routes/boms.py` zeigen das Muster.

## Die Schritte in `services/`

| Datei | Aufgabe |
|---|---|
| `excel_import.py`, `ex_liste.py`, `korrektur.py` | Stückliste lesen, Ex-Teile vorschlagen, Mängelliste und Korrektur |
| `suche.py`, `rag.py`, `internet.py`, `zertifikatsserver.py`, `sprachversionen.py` | Suchkette je Position (Datenbank, Ordner und Archiv, Ähnlichkeitssuche, Websuche, Sprachfassungen) |
| `pruefung.py`, `belege.py`, `angaben.py`, `pdf_text.py` | Vorprüfung, Belege mit Seite, weitere Angaben, PDF-Text und Texterkennung |
| `status.py`, `lesepuffer.py` | Stand einer Position (einzige Stelle, die ihn setzt), Lesen ohne Schreiben |
| `freigabe.py`, `hochladen.py`, `erledigt.py`, `stammdaten.py`, `bearbeiten.py` | Entscheidungen und Änderungen eines Menschen |
| `email_entwurf.py`, `kontakte.py`, `versand.py`, `erinnerung.py` | E-Mails an Hersteller, Empfänger, Versand, Erinnerung und Eskalation |
| `excel_export.py` | Kundenliste, Fehlliste, Zwischenstand |
| `oberflaeche.py` | Übersetzung in den Vertrag der Oberfläche, Hintergrundlauf der Suche |
| `modelle.py`, `benutzer.py`, `globalsuche.py`, `fragen.py`, `suchlinks.py`, `schema.py` | Sprachmodelle, Konten, Suchfeld, Fragen, Suchlinks, neue Spalten |

Wie sie zusammenspielen: [../docs/ablauf.md](../docs/ablauf.md).

## Sprachmodell

`app/llm` ruft Modelle über LiteLLM auf, der Anbieter wechselt ohne Codeänderung. Ein Modell ist
optional: Gefragt wird nur mit `INTERNET_LLM=1` oder `EMAIL_MIT_LLM=1`, beide sind im Standard aus. Welche Modelle es gibt, steht in **`config/modelle.yaml`** (Vorlage
`config/modelle.beispiel.yaml`, Anleitung [../config/README.md](../config/README.md)): beliebig
viele Chat- und Embedding-Modelle, je mit Anbieter, Adresse, Zugang, und unter `standard:` das
Modell je Aufgabe (`websuche`, `email`, `rag_antwort`, `rag_embedding`). `services/modelle.py`
liest und prüft die Datei, liest sie bei jeder Änderung neu (kein Neustart) und meldet Fehler
auf Deutsch beim Start im Log und unter `GET /api/modelle`; dann gibt es kein Modell, nie still
ein anderes. Ohne Datei gilt genau ein Modell „standard“ aus `LLM_MODEL`, `LLM_API_BASE`,
`LLM_API_KEY`, `LLM_TIMEOUT` (Standard: `openai/gpt-4o-mini` als Beispiel, Adresse des
Anbieters, Schlüssel aus der Umgebung).

| Anbieter | `modell` | `api_base` | Zugang |
|---|---|---|---|
| OpenAI-kompatibler Anbieter mit eigener Adresse | `openai/<modellname>` | Adresse des Anbieters | `api_key` oder `api_key: "-"` ohne Schlüssel |
| lokal mit Ollama | `ollama/llama3.1:8b` | `http://ollama:11434` | `api_key: "-"` |
| Claude (Anthropic) | `anthropic/claude-sonnet-5-5`, `…-opus-5-5`, `…-haiku-5-5` | – | `api_key` oder `api_key_env: ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | – | `api_key` oder `api_key_env: OPENAI_API_KEY` |

Im Hackathon lief nur die erste Zeile; die anderen folgen der Dokumentation von LiteLLM.

**Wahl je Vorgang:** `bom.llm_modell` (Name aus der Datei, leer = Standard) und
`bom.internet_versuche` (Web-Anfragen je Position, leer = `INTERNET_VERSUCHE_PRO_TEIL`), gesetzt
über `PUT /api/vorgaenge/<id>/einstellungen`. Am Anfang jedes Suchlaufs setzt
`modelle.fuer_lauf(bom)` das Modell für den App-Kontext (`flask.g`); `llm.chat` nimmt es von
dort, so erreicht die Wahl auch `services/internet.py`, ohne dass es sie kennt. E-Mail-Entwürfe
bestimmen ihr Modell selbst aus dem Vorgang (`email_entwurf._mit_modell`), auch außerhalb eines
Laufs.

**Wofür das Modell arbeitet** (in `docker-compose.yml`, `.env.example` und im Code beides aus, `0`):

| Schalter | Aufgabe |
|---|---|
| `INTERNET_LLM=1` | Websuche: wählt aus höchstens 15 Links einer Herstellerseite; nur wörtlich genannte URLs zählen |
| `EMAIL_MIT_LLM=1` | E-Mail-Entwürfe: Anrede und Einleitung; Fakten setzt der Code ein, Zweifel ergeben die Vorlage |

Schlüssel verlassen den Server nie: `GET /api/modelle` sagt nur „Zugang hinterlegt ja/nein“,
Fehlertexte der Anbieter laufen durch `modelle.bereinige`.

Aufruf im Code: `from app import llm` und `llm.chat([{"role": "user", "content": "..."}])`,
optional `modell="name"`. Für einen eigenen Suchdienst: `modelle.name_fuer("rag_antwort")` ist
der Name des Modells für den laufenden Vorgang; `services/rag.py` schickt ihn in der Anfrage mit.

## Meldungen der Eingangsprüfung

| Code | Stufe | Bedeutung |
|---|---|---|
| `profile_no_fehlt` | error | Position ohne PRO.FILE-Nr. |
| `profile_no_doppelt` | warning | dieselbe PRO.FILE-Nr. steht schon in einer früheren Position |
| `hersteller_fehlt` | warning | kein Hersteller |
| `herstellerartikelnr_fehlt` | warning | keine Hersteller-Artikelnr. |
| `einbauort_fehlt` | info | kein Einbauort, die Zone ist unbekannt |

Eine Datei ohne Kopfzeile, ohne Pflichtspalte oder ohne Positionen wird ganz abgelehnt.

## Tests

Aus dem Hauptordner des Repositorys, mit dem Image aus `docker compose build` und dem Code des
Ordners (absolute Pfade bei `-v`; der Name des Images hängt vom Ordnernamen ab, `docker images`
zeigt ihn):

```bash
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD/backend:/app" \
  -v "$PWD/data/ex_dokumente:/data/ex_dokumente:ro" -e ATEX_DOCS=/data/ex_dokumente \
  <projekt>-backend python -m pytest -q -p no:cacheprovider
```

Die Tests laufen gegen SQLite und brauchen keine laufende Datenbank; Sprachmodell, Suchdienst,
Archiv, Webseiten und Postfach sind durch Attrappen ersetzt. Ohne eigene PDFs unter
`data/ex_dokumente/<Hersteller>/` überspringen sich drei Tests, die echte Dokumente lesen, und
einer, der eine Beispieldatei mit Kontakten braucht ([../data/README.md](../data/README.md)).

## Ohne Docker

```bash
cd backend
uv venv -p 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt
flask --app wsgi init-db
flask --app wsgi run --debug
```

Ohne `DATABASE_URL` schreibt das Backend in eine SQLite-Datei unter `instance/atex.db`.

## Datenbank

Die Tabellen entstehen beim Start über `flask --app wsgi init-db` aus den Modellen. Es gibt keine
Migrationen, aber `init-db` zieht neue Spalten in bestehenden Tabellen nach
(`services/schema.py`): Es vergleicht die Modelle mit der Datenbank und ergänzt jede fehlende
Spalte per `ALTER TABLE … ADD COLUMN`, wenn sie leer bleiben darf oder einen Standard in der
Datenbank hat (`server_default`). Bestehende Zeilen bleiben. Auf der Konsole steht, was ergänzt
wurde. Gelöscht, umbenannt oder im Typ geändert wird nie; was sich nicht sicher ergänzen lässt
(NOT NULL ohne `server_default`, `unique`), steht als `WARNUNG Schema: …` mit Tabelle, Spalte
und dem Befehl für Hand da. PostgreSQL und SQLite.

Wer einem bestehenden Modell eine Spalte gibt, macht sie deshalb `nullable` oder gibt ihr einen
`server_default`; dann reicht ein Neustart des Backends (oder `flask --app wsgi init-db`).

`flask --app wsgi zert-abgleich` überträgt freigegebene Dokumente, die der Zertifikatsserver noch
nicht hat, dorthin (siehe [docs/api.md](../docs/api.md#zertifikatsserver-in-der-suche)).

## Angaben aus dem Dokument

`services/angaben.py` liest je Vorschlag mit Regeln (kein Sprachmodell) Gültigkeit („gültig bis“,
„unbefristet“), Ausstellungsdatum, Ausgabe, Richtlinien, Normen mit Ausgabe, benannte Stellen,
Hersteller, Unterschrift, zerlegte Kennzeichnung, Umgebungstemperatur, besondere Bedingungen und
IECEx-Nummer, jede mit Seite und Textstelle. Es läuft im selben Schritt wie die Belege
(`services/belege.py`, vor dem Speichern jedes neuen Vorschlags); die Vorprüfung dazu steht in
`services/pruefung.py` (`pruefe_angaben`). Die Regeln für Datum, Richtlinien und Normen stammen
aus dem Zertifikatsserver (`zertifikatsserver/app/auslesen.py`), damit beide dasselbe lesen.

- `ABLAUF_WARNUNG_TAGE` (Standard 180): Zertifikate, die so bald ablaufen, nennt die Vorprüfung;
  auch Standard für `GET /api/fristen`.
- `HERSTELLER_AEHNLICHKEIT` (Standard 0.9): so ähnlich muss der Anfang des Herstellernamens im
  Dokument dem der Stückliste sein, wenn er nicht genau enthalten ist (OCR-Fehler).
- Regeln der Vorprüfung mit ihrer Grundlage: [docs/api.md](../docs/api.md#angaben-aus-dem-dokument).
- `flask --app wsgi angaben-nachlesen [--alle]`: Angaben für ältere Vorschläge nachtragen, deren
  Datei noch da ist. Wiederholbar, läuft nur von Hand, ändert keine Stammdaten.

Gemessen auf den 93 PDFs des Case (87 aus den Herstellerordnern, 6 aus der Websuche; nicht in
diesem Repository):
Richtlinien 77, Hersteller 76, Kennzeichnung 75, Normen 71, benannte Stelle 61, besondere
Bedingungen 54, Ausstellungsdatum 47, Umgebungstemperatur 37, Ausgabe 33, Unterschrift 17,
IECEx 12, „gültig bis“ 0 (keins der Dokumente nennt ein Ablaufdatum).

## Antwortzeiten der Ansichten

Die Oberfläche fragt Übersicht, Vorgang, Artikel, E-Mails, EX-Bauteil-Liste und Korrektur alle
1,5 bis 6 Sekunden ab. Diese GETs leiten den Stand jeder Position frisch ab (Stammdaten gelten
für alle Vorgänge), schreiben aber nichts: `services/lesepuffer.py` (`nur_lesen()`) lädt
Vorgänge mit Positionen, Funden und E-Mails sowie Bauteile, Dokumente, Sprachen, Hersteller und
Kontakte je Anfrage in einem Zug und verwirft den im Speicher abgeleiteten Stand am Ende. Den
gespeicherten Stand setzen die schreibenden Wege (Freigabe auch für dieselbe PRO.FILE-Nr. in
anderen Vorgängen, Stammdaten, Kontakte, Korrekturen, Fund korrigieren).

Gemessen mit 9 Vorgängen zu je 30 Positionen auf PostgreSQL (warm, Median über HTTP):
`GET /api/vorgaenge` vorher 3,4 s mit 3682 SQL-Anfragen (126 schreibend), nachher 76 ms mit 10;
`GET /api/vorgaenge/<id>` 416 ms und 412 Anfragen vorher, 22 ms und 11 nachher; `ex-liste`
271 ms und 256 vorher, 21 ms und 11 nachher. `tests/test_anfragen_zahl.py` schlägt fehl, sobald
die Zahl der Anfragen einer dieser Ansichten mit Vorgängen oder Positionen wächst oder eine von
ihnen schreibt.
