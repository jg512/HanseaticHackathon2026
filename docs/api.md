# API des Backends

Basis lokal: `http://localhost:5000`. Alle Antworten sind JSON, auch Fehler (`{"error": "…"}`,
auch bei `404` und `405`); nur Dateien (PDF, PNG, Excel) nicht.

**Anmeldung:** Jede Anfrage außer `GET /health`, `POST /api/auth/login` und
`POST /api/auth/logout` braucht eine Sitzung (Cookie `atex_sitzung`), sonst `401`. Die Rolle
`leser` bekommt bei allem außer `GET`/`HEAD`/`OPTIONS` ein `403`. Wo unten „`by` (Name)“ steht,
setzt der Server für angemeldete Benutzer den Namen aus der Sitzung ein; was der Client schickt,
zählt nicht. Einzelheiten: [Anmeldung und Benutzer](#anmeldung-und-benutzer).

## `GET /health`

`{"ok": true}`, wenn das Backend läuft und die Datenbank erreicht.

## `POST /api/boms`

Lädt eine Stückliste hoch und legt einen Vorgang an.

- Anfrage: `multipart/form-data` mit der Excel-Datei im Feld `file`
- `201`: der Vorgang mit Positionen und Meldungen, Aufbau wie bei `GET /api/boms/<id>`
- `400`: `{"error": "..."}`, wenn die Datei nicht zum Eingangsschema passt. Es wird nichts gespeichert.

```bash
curl -F file=@meine_stueckliste.xlsx http://localhost:5000/api/boms
```

## `GET /api/boms`

Alle Vorgänge, der neueste zuerst, ohne Positionen.

## `GET /api/boms/<id>`

Ein Vorgang mit Positionen und Meldungen. `404`, wenn es ihn nicht gibt.

```json
{
  "id": 1,
  "machine": "Abfüllanlage TM-1",
  "ex_area": "Innenraum Zone 1, Umgebung Zone 2; Gas, Explosionsgruppe IIC",
  "required_category": "Zone 1: mindestens 2G · Zone 2: mindestens 3G",
  "source_file_name": "meine_stueckliste.xlsx",
  "created_at": "2026-11-13T15:00:00",
  "item_count": 30,
  "issue_count": 7,
  "items": [
    {"id": 1, "row_number": 9, "pos": "10", "profile_no": "99000010",
     "description": "Magnetspule für Ventil", "manufacturer": "Festo",
     "manufacturer_article_no": "VACF-B-K1-1A-1-EX4-M", "quantity": "4",
     "location": "Zone 1", "status": "offen", "status_note": null, "searched_in": null}
  ],
  "issues": [
    {"row_number": 36, "pos": "280", "code": "hersteller_fehlt",
     "severity": "warning", "message": "Hersteller fehlt."}
  ]
}
```

Die Codes der Meldungen stehen in [../backend/README.md](../backend/README.md).

Jede Position trägt außerdem `status` (siehe [datenmodell.md](datenmodell.md)), `status_note`
und `searched_in`.

## `POST /api/boms/<id>/items/<item_id>/ex-teil`

Ein Mensch entscheidet, ob eine Position ein Ex-Teil ist.

- Anfrage: `{"ex_teil": false, "by": "Name"}`. `false` nimmt die Position aus der Suche
  (`kein_ex_teil`), `true` nimmt die Entscheidung zurück.
- `200`: die Position. `400` ohne `by`. `404` bei unbekannter Position.

## Suche

### `POST /api/boms/<id>/search`

Sucht für alle Positionen die Ex-Dokumente und legt Vorschläge an. Positionen im Stand
`klaerung`, `kein_ex_teil`, `vorhanden` und `erledigt` werden übersprungen.

- `200`: der Vorgang wie bei `GET /api/boms/<id>`
- `404` bei unbekanntem Vorgang, `503`, wenn der Ordner der Ex-Dokumente fehlt

Reihenfolge je Position: Datenbank, Dokumentenarchiv (ohne Archiv der Ordner des Herstellers),
Ähnlichkeitssuche (nur ohne Archiv, nur mit `SEARCH_URL`, wenn der Ordner nichts hat, das zählt),
Internet. Vorschläge der Ähnlichkeitssuche
haben `source: "suchdienst"` (Oberfläche: Quelle „Ähnlichkeitssuche“); steht der Typ nicht im
Dokument, ist `matched_term` leer, `check_result` nie `ok`, `page` und `snippet` zeigen die
ähnliche Stelle. Die Anfrage an den Suchdienst trägt den Namen des Chat-Modells des Vorgangs
(`modelle.name_fuer("rag_antwort")`, nur mit Modelldatei); kennt der Dienst ihn nicht (`400`),
geht sie ohne Namen noch einmal. Sprachversionen sucht der zweite Durchgang auch zu Funden der
Ähnlichkeitssuche mit Typ-Treffer, in deren Ordner.

`searched_in` besteht aus ganzen Sätzen für die Sachbearbeiterin (`suche.ordner_satz`,
`rag_satz`, `internet_satz`, Sätze von `zertifikatsserver.pruefe`), zum Beispiel: „Gesucht hat
der Agent in der Datenbank und im Ordner des Herstellers. Die Ähnlichkeitssuche hat zwei mögliche
Dokumente gefunden. Im Internet hat der Agent auf festo.com gesucht und dabei 3 Seiten von
höchstens 10 abgerufen. Die Webseite festo.com lässt keine automatischen Abrufe zu.“ Die
Rohtexte von `internet.py` stehen im Log.

**Schnittstelle des Suchdienstes** (`services/rag.py`). Der Dienst gehört nicht zu diesem
Repository; wer die Stufe nutzen will, stellt einen eigenen bereit und trägt seine Adresse in
`SEARCH_URL` ein. Das Backend schickt `POST <SEARCH_URL>/api/search` mit
`{"q": "<Hersteller Typ Bezeichnung>", "k": 20}` und, mit Modelldatei, `"modell"`. Erwartet wird
eine JSON-Liste von Textstellen, die beste zuerst, je mit `relpath` (Pfad der PDF relativ zu
`ATEX_DOCS`), `page` (ab 1) und `text`. Pfade außerhalb von `ATEX_DOCS` und Nicht-PDFs werden
verworfen. `503` heißt „Index wird noch gebaut“, jeder andere Fehler „nicht erreichbar“; in
beiden Fällen sucht das Backend ohne diese Stufe weiter.

## Prüfung und Freigabe

### `GET /api/boms/<id>/findings`

Positionen mit Vorschlägen: je Position ihre Angaben und unter `findings` die Vorschläge.

```json
{"id": 12, "bom_item_id": 3, "source": "ordner", "doc_type": "eu_konformitaetserklaerung",
 "file_path": "Festo/Festo_SMT-8F-I-8,2V-K5,0-OE-EX_Konf.pdf", "url": null,
 "file_hash": "…", "languages": "de,en", "page": 1,
 "matched_term": "SMT-8F-I-8,2V-K5,0-OE-EX", "snippet": "…",
 "ex_marking_raw": "II 1 GD EEx ia IIC T4...T6", "certificate_no": "KEMA 04ATEX1114 X",
 "check_result": "ok", "check_message": "Typ … steht genau so im Dokument (Seite 1). …",
 "status": "vorgeschlagen", "decided_by": null, "decided_at": null}
```

`check_result` ist `ok`, `warnung` oder `unklar`. `ok` gibt es nur bei genauem Typ im Text und
passender Kategorie für die Zone.

### `POST /api/findings/<id>/decision`

- Anfrage: `{"decision": "freigeben" | "ablehnen", "by": "Name"}`, bei der Freigabe optional
  `"doc_type"`, um die Dokumentart zu korrigieren
- `200`: der Vorschlag und `item_status`, der neue Stand der Position
- `400` ohne `by` oder bei unbekanntem Wert, `404`, `409`, wenn schon entschieden

Die Freigabe überträgt das Dokument in die Stammdaten (Hersteller, Bauteil, Dokument, Sprachen,
Zuordnung mit Status `geprueft`).

### `GET /api/findings/<id>/file`

Die PDF-Datei des Vorschlags. `404`, wenn sie fehlt oder außerhalb des Dokumentenordners läge.

### `GET /api/findings/<id>/page.png`

Die Seite der Fundstelle als Bild, der gefundene Begriff ist markiert, wenn er als Text vorliegt.

## E-Mails

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `POST` | `/api/boms/<id>/emails` | – | legt für jede Position im Stand `nicht_gefunden` einen Entwurf an, gibt alle Entwürfe zurück |
| `GET` | `/api/boms/<id>/emails` | – | alle Entwürfe des Vorgangs mit den Angaben der Position |
| `PUT` | `/api/emails/<id>` | `{"subject", "body"}` | Text ändern, nur im Stand `entwurf`, sonst `409` |
| `POST` | `/api/emails/<id>/approve` | `{"by": "Name"}` | Freigabe durch einen Menschen |
| `POST` | `/api/emails/<id>/send` | – | Versand, nur nach Freigabe (`409`), bei SMTP-Fehler `502` (Satz für Anwender, Grund im Log) |
| `POST` | `/api/emails/<id>/reminder` | – | neuer Entwurf „Erinnerung“ zur Anfrage, nur wenn fällig (sonst `409`); `201` |
| `POST` | `/api/emails/<id>/escalate` | `{"by": "Name"}` | an den Einkauf eskalieren, nur wenn fällig (sonst `409`); die Position steht dann auf `eskaliert` |

Gesendete E-Mails tragen den fälligen Schritt (E7, E8): `next_step` (`warten`, `erinnern`,
`eskalieren`, `erledigt`), `due_at`, `reminders_sent`, `step_note`. Frist
`ERINNERUNG_NACH_STUNDEN` ab der letzten gesendeten E-Mail, `ERINNERUNGEN_MAX` Erinnerungen vor der
Eskalation. Nichts davon geschieht von selbst.

Empfänger: siehe [Herstellerkontakte](#herstellerkontakte). Jeder Entwurf trägt zusätzlich
`empfaenger_hersteller` (gespeicherte Adresse oder `null`), `kontakt_fehlt` und `versand_an`. Mit
`EMAIL_NUR_TEST` (Standard) geht der Versand an `MAIL_TEST_ADDRESS`, nie an den Hersteller. Ohne
gespeicherte Adresse antwortet `…/send` mit `409` und sagt, wo die Adresse einzutragen ist. Nach
dem Senden steht die Position auf `angefordert`.

### Postausgang (`services/versand.py`)

Jede E-Mail des Agenten geht über `versand.sende`: Anfragen und Erinnerungen an Hersteller, die
Mängelliste an den Einkauf, die Test-E-Mail. Das Postfach kommt aus der Umgebung:

| Variable | Standard (Compose) | Bedeutung |
|---|---|---|
| `SMTP_HOST`, `SMTP_PORT` | `mail`, `1025` (Mailpit) | Postausgangsserver |
| `SMTP_BENUTZER`, `SMTP_PASSWORT` | leer | beide leer: ohne Anmeldung; nur zusammen gültig |
| `SMTP_SICHERHEIT` | `keine` | `keine`, `starttls` (`smtplib.SMTP` + `starttls()`), `ssl` (`smtplib.SMTP_SSL`); ein anderer Wert hält den Start an (`versand.KonfigFehler`) |
| `MAIL_FROM`, `MAIL_FROM_NAME` | `atex-agent@example.com`, leer | Absender, Anzeigename optional |
| `MAIL_ANTWORT_AN` | leer | `Reply-To` |
| `MAIL_TEST_ADDRESS` | `test@example.com` | Ziel aller E-Mails, solange `EMAIL_NUR_TEST=1` |

Wohin welche E-Mail geht:

| E-Mail | `EMAIL_NUR_TEST=1` (Standard) | `EMAIL_NUR_TEST=0` |
|---|---|---|
| Anfrage an den Hersteller | `MAIL_TEST_ADDRESS` | gespeicherte Adresse des Herstellers; ohne Adresse nie (`409`) |
| Erinnerung | `MAIL_TEST_ADDRESS` | wie die Anfrage |
| Eskalation an den Einkauf | keine E-Mail, nur der Stand `eskaliert` und ein Protokolleintrag | ebenso |
| Mängelliste an den Einkauf | `MAIL_TEST_ADDRESS` | `EINKAUF_ADRESSE`, leer: `MAIL_TEST_ADDRESS` |
| Test-E-Mail (Admin) | `MAIL_TEST_ADDRESS` | `MAIL_TEST_ADDRESS` |

Mit Mailpit landet alles im Test-Postfach (http://localhost:8025). Mit einem echten Postfach und
`EMAIL_NUR_TEST=1` kommt jede E-Mail wirklich an, aber nur an der Testadresse. Fehler beim Versand
(Anmeldung, TLS, abgelehnter Empfänger, Zeitüberschreitung, nicht erreichbar): `502` mit einem
Satz für Anwender; Host, Port, Modus, Benutzer und Grund stehen im Log, das Passwort nie. Nichts
wird dann als gesendet markiert.

| Methode | Pfad | Antwort |
|---|---|---|
| `GET` | `/api/mail/einstellungen` | `{"versand": "testpostfach" \| "echt_an_testadresse" \| "echt", "absender", "testadresse", "antwort_an", "nur_test", "einkauf", "satz"}`, ohne Zugangsdaten; `satz` zeigt die E-Mail-Seite |
| `POST` | `/api/mail/test` | nur Admins (`403` sonst): eine E-Mail an `MAIL_TEST_ADDRESS`, `{"ok", "an", "text"}`; `502` mit dem Grund als Satz |

`versand: "testpostfach"` heißt: ohne Anmeldung an `mail`, `mailpit`, `localhost` oder
`127.0.0.1`.

## Ausgabe

### `GET /api/boms/<id>/missing`

Stand der Vollständigkeit: `total`, `done`, `complete` und unter `open` die offenen Positionen mit
`missing_doc_types`, `missing_languages` (`{"eu_konformitaetserklaerung": ["de"]}`, wenn die
Erklärung geprüft nur in anderen Sprachen vorliegt) und `missing_documents` (Text, z. B.
„EU-Konformitätserklärung (deutsche Fassung)“).

### `GET /api/boms/<id>/missing.xlsx`

Dieselbe Liste als Excel („Liste, was fehlt“), immer abrufbar.

### `GET /api/boms/<id>/export`

Die Kundenliste als Excel im festen Schema mit 17 Spalten. `409` mit `{"error", "open"}`, solange
Positionen offen sind (M12). Mit `?zwischenstand=1` jederzeit der Zwischenstand, siehe
[Zwischenstand](#zwischenstand).

Spalte „DL-Link“ (`zertifikatsserver.download_links`): je Dokument der Dokumentspalten 6 bis 15
ein Link, eine Zeile je Link, in der Reihenfolge der Spalten und darin der Dateinamen; doppelte
Dokumente einmal. Der Link ist der ursprüngliche Download-Link des Herstellers (`document.url`, bei
Funden aus dem Internet gesetzt), sonst der stabile Link ins Dokumentenarchiv
`<OEFFENTLICHE_ADRESSE>/zert/api/dokumente/<id>/datei`, wenn das Archiv die Datei kennt (eine
Anfrage über den SHA-256, Ergebnis eine Minute gemerkt). Ist nichts bekannt, bleibt die Zelle leer;
die allgemeine Webseite des Herstellers (`part.manufacturer_url`) ist kein Download-Link und steht
dort nicht mehr. Ist das Archiv nicht erreichbar, stehen nur die Links der Hersteller da (Hinweis
im Log), und die Ausgabe fragt eine Minute lang nicht nach. Die Archiv-Links öffnen sich nur für
angemeldete Benutzer (nginx prüft `/zert/`).

## Korrekturschleife (E5)

Fehlen Angaben in der Stückliste, geht eine Mängelliste an den Einkauf. Die Antwort trägt ein
Mensch je Position ein; danach werden nur die korrigierten Positionen neu gesucht.

### `GET /api/boms/<id>/korrektur`

Alles für die Seite „Korrektur“:

- `defects`: Positionen im Stand `klaerung` oder mit offener Meldung der Eingangsprüfung, ohne
  `kein_ex_teil` und `erledigt`; je Position ihre Angaben und unter `missing` die Texte, was fehlt
- `purchasing_emails`: die Mängellisten, die neueste zuerst
- `corrections`: alle Korrekturen des Vorgangs mit `pos`, `field`, `old_value`, `new_value`,
  `changed_by`, `changed_at`, `note`
- `to_search`: IDs der korrigierten Positionen, die seit der Korrektur nicht gesucht wurden

### Mängelliste an den Einkauf

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `GET` | `/api/boms/<id>/maengelliste` | – | alle Mängellisten des Vorgangs, die neueste zuerst |
| `POST` | `/api/boms/<id>/maengelliste` | – | Entwurf mit den aktuellen Mängeln (`201`); ein offener Entwurf wird neu aufgebaut (`200`). `409` ohne Mängel oder wenn eine freigegebene Liste noch nicht gesendet ist |
| `PUT` | `/api/maengellisten/<id>` | `{"subject", "body"}` | Text ändern, nur im Stand `entwurf`, sonst `409` |
| `POST` | `/api/maengellisten/<id>/approve` | `{"by": "Name"}` | Freigabe durch einen Menschen |
| `POST` | `/api/maengellisten/<id>/send` | – | Versand, nur nach Freigabe (`409`), bei SMTP-Fehler `502` |

Je Position nennt der Text Pos., PRO.FILE-Nr., Bezeichnung, Hersteller und Typ und was fehlt.
Empfänger ist `EINKAUF_ADRESSE`, ohne Angabe `MAIL_TEST_ADDRESS`; im Hackathon fängt Mailpit
jede E-Mail ab.

### `POST /api/boms/<id>/items/<item_id>/korrektur`

Ein Mensch korrigiert Angaben einer Position.

- Anfrage: `{"by": "Name", "note": "optional"}` und mindestens eines der Felder
  `manufacturer`, `manufacturer_article_no`, `profile_no`, `location` (Text, nicht leer)
- `200`: die Position mit neu abgeleitetem Stand und unter `corrections` ihre Korrekturen
- `400` ohne `by`, bei leerem Wert oder wenn sich nichts ändert, `404` bei unbekannter Position

Jede geänderte Angabe wird mit altem und neuem Wert, Name und Zeit gespeichert. Passende Meldungen
der Eingangsprüfung (z. B. `hersteller_fehlt`) werden im Namen der Person abgehakt. Die alte
Suche gilt nicht mehr: unentschiedene Vorschläge und nicht freigegebene Entwürfe an den Hersteller
fallen weg, entschiedene Vorschläge und freigegebene oder gesendete E-Mails bleiben.

### `POST /api/boms/<id>/korrektur/suche`

Sucht nur korrigierte Positionen neu, auf demselben Weg wie `POST /api/boms/<id>/search`.

- Anfrage (optional): `{"item_ids": [...], "internet_versuche": 0 bis 50}`. Ohne `item_ids` alle
  aus `to_search`.
- `200`: die gesuchten Positionen
- `400` bei falschen Werten, `409`, wenn keine korrigierte Position wartet oder eine gewählte
  nicht korrigiert ist, `503`, wenn der Ordner der Ex-Dokumente fehlt

### `POST /api/boms/<id>/korrektur/excel`

Korrigierte Stückliste für denselben Vorgang, `multipart/form-data` mit der Datei im Feld `file`
und dem Namen im Feld `by`. Die Positionen werden über die Pos. zugeordnet; übernommen werden nur
geänderte, nicht leere Werte der vier Felder, je als Korrektur wie oben.

- `200`: `{"changed": [Positionen], "unknown_pos": [...], "missing_pos": [...]}`. Neue
  Positionen werden nicht angelegt.
- `400` ohne Namen, bei unlesbarer Datei oder wenn keine Pos. zum Vorgang passt

## EX-Bauteil-Liste (E1)

Die Eingangsdatei wird in die Liste der Ex-Teile umgewandelt, nach festen Regeln in
`services/ex_liste.py`: unsere Liste der Positionen, die als Ex-Teil gelten, je mit Regel und
Begründung. Die Regeln hat das Team entschieden
([architektur.md](architektur.md#fachliche-entscheidungen-des-teams)), der Case-Geber hat sie
nicht bestätigt; ein Mensch bestätigt die Liste. Erkannt wird am Einbauort (Zone 0, 1, 2, 20, 21, 22) und an
Ex-Hinweisen in Bezeichnung oder Typ („Ex“, „ATEX“, „NAMUR“, eine Kennzeichnung). Eine
Ausschlussliste gibt es nicht. Heraus fällt eine Position, wenn sie außerhalb des Ex-Bereichs
sitzt, keinen Ex-Hinweis trägt und ein Mensch „kein Ex-Teil“ bestätigt, oder wenn ein Mensch
„kein Ex-Teil“ entscheidet.

| Regel | Einbauort, Beispiel | Vorschlag | Stand der Position |
|---|---|---|---|
| `zone_im_einbauort` | „Zone 1“, „Zone 21“ | Ex-Teil | wie sonst |
| `bereich_aus_kopfzeile` | „Innenraum“, in „Ex-Bereich“ steht „Innenraum Zone 1“ | Ex-Teil | wie sonst |
| `ex_bereich_im_einbauort` | „Ex-Bereich Halle 2“, ohne Zone | Ex-Teil | wie sonst |
| `zugehoeriges_betriebsmittel` | „Schaltschrank außerhalb Ex“ mit „Trennschaltverstärker“, Typ „KCD2-SR-Ex1.LB“, oder einer Kennzeichnung in Klammern „II (1)G [Ex ia Ga] IIC“ (auch aus einem gefundenen Dokument) | Ex-Teil | wie sonst; `hinweis`: die Kategorie in Klammern gilt für die Stromkreise im Ex-Bereich |
| `ausserhalb_ex`, nur vager Ex-Hinweis | „außerhalb Ex“, Typ „PS-24 Ex“, „NAMUR“, „ATEX“ | Entscheidung nötig | wird gesucht, `status_note` nennt die offene Entscheidung |
| `ausserhalb_ex`, ohne Ex-Hinweis | „außerhalb Ex“, Typ „PS-24“ | Entscheidung nötig | `klaerung` |
| `widerspruch` | „außerhalb Zone 1“ | Entscheidung nötig | `klaerung` |
| `einbauort_unklar` | „Halle 3“ | Entscheidung nötig | `klaerung` |
| `einbauort_fehlt` | leer | Entscheidung nötig | `klaerung` |

Die geforderte Kategorie je Zone kommt aus der Kopfangabe „Geforderte Kategorie“, sonst aus der
Mindestkategorie nach ATEX (Zone 0: 1G, Zone 1: 2G, Zone 2: 3G, Zone 20 bis 22: 1D bis 3D).
Nennt der Einbauort eine Zone, die in der Kopfangabe „Ex-Bereich“ fehlt, steht das unter `hinweis`.

### `GET /api/boms/<id>/ex-liste`

Alle Positionen mit Vorschlag, Entscheidung und Einordnung. `404` bei unbekanntem Vorgang.

```json
{"bom_id": 1, "machine": "Abfüllanlage TM-1", "ex_area": "…", "required_category": "…",
 "total": 30, "ex_teil": 26, "klaerung": 4, "kein_ex_teil": 0,
 "file_name": "EX-Bauteil-Liste_Abfüllanlage_TM-1_Vorgang-1.xlsx",
 "items": [
   {"item_id": 14, "row_number": 22, "pos": "140", "profile_no": "99000140",
    "description": "Trennschaltverstärker", "manufacturer": "Pepperl+Fuchs",
    "manufacturer_article_no": "KCD2-SR-Ex1.LB", "quantity": "2",
    "location": "Schaltschrank außerhalb Ex", "status": "offen",
    "vorschlag": "pruefen", "suchen": true, "regel": "ausserhalb_ex",
    "regel_text": "Einbauort außerhalb des Ex-Bereichs",
    "grund": "Einbauort außerhalb des Ex-Bereichs, aber Bezeichnung oder Typ tragen „Ex“: …",
    "quelle": "Zeile 22, Einbauort: „Schaltschrank außerhalb Ex“",
    "zone": null, "kategorie": null, "kategorie_quelle": null, "ex_kennung": true,
    "hinweis": null, "entscheidung": null, "entschieden": null,
    "einordnung": "klaerung", "einordnung_text": "Entscheidung nötig"}
 ]}
```

`vorschlag` ist `ex_teil` oder `pruefen`, `entscheidung` ist `ex_teil`, `kein_ex_teil` oder
`null`. `einordnung` ist die Entscheidung oder, ohne sie, der Vorschlag (`ex_teil`, `klaerung`).

### `GET /api/boms/<id>/ex-liste.xlsx`

Dieselbe Liste als Excel, immer abrufbar: Blatt „EX-Bauteil-Liste“ mit den Ex-Teilen, Blatt
„Nicht in der Liste“ mit den offenen Entscheidungen und „kein Ex-Teil“, je mit Zone, Kategorie,
Regel, Begründung, Quelle und Entscheidung.

### Geändert: `POST /api/boms/<id>/items/<item_id>/ex-teil`

- Anfrage: `{"ex_teil": false | true | null, "by": "Name"}`. `false` wie bisher: `kein_ex_teil`.
  `true` bestätigt die Position als Ex-Teil (`ex_confirmed_by`, `ex_confirmed_at`); sie wird dann
  gesucht, auch ohne Einbauort. `null` nimmt die Entscheidung zurück, es gilt wieder der Vorschlag
  der Regeln. Bisher hieß `true` „zurücknehmen“.
- `400` ohne `by`, ohne `ex_teil` oder bei einem anderen Wert. Die letzte Entscheidung gilt.

## Sprachversionen

### `GET /api/boms/<id>/sprachen`

Entscheidung des Teams: Die deutsche Fassung der EU-Konformitätserklärung ist
Pflicht, andere Sprachen sind willkommen, aber freiwillig.

- `pflichtsprache` (`PFLICHTSPRACHE`, Standard `de`) gilt für das Pflichtdokument
  (EU-Konformitätserklärung, M20). Liegt es geprüft nur in anderen Sprachen vor, ist die Position
  nicht vollständig: Hinweis „Deutsche Fassung fehlt (Pflicht). …“, Kundenliste gesperrt (M12),
  Fehlliste mit „EU-Konformitätserklärung (deutsche Fassung)“, und die Anfrage an den Hersteller
  fragt gezielt nach der deutschen Fassung. Ein Mensch kann die Position mit Begründung von Hand
  erledigen.
- `sprachen` (`SPRACHEN`, Standard `de,en`, wie die Spalten (d) und (e) der Kundenliste, die
  Pflichtsprache immer zuerst) sind die Sprachen, die gesucht und gezeigt werden. Fehlt eine
  davon, oder fehlt bei Baumusterprüfbescheinigung und Anleitung die deutsche Fassung, steht das
  unter `fehlende_sprachen` und hält nichts auf.

Je Position die Sprachen je Dokumentart. Es zählen Vorschläge, Freigaben und die geprüften
Dokumente des Bauteils in der Datenbank; für die Vollständigkeit nur die geprüften. Geprüft werden
EU-Konformitätserklärung, EU-Baumusterprüfbescheinigung und Bedienungsanleitung, und nur, wenn die
Art schon vorliegt; eine ganz fehlende Art meldet `/missing`. `404` bei unbekanntem Vorgang.

```json
{"sprachen": ["de", "en"], "pflichtsprache": "de",
 "items": [{"id": 11, "pos": "210", "status": "vorschlag",
            "sprachen": {"eu_konformitaetserklaerung": ["en"],
                         "eu_baumusterpruefbescheinigung": ["de"]},
            "pflicht_fehlt": {"eu_konformitaetserklaerung": ["de"]},
            "fehlende_sprachen": {"eu_baumusterpruefbescheinigung": ["en"]}}]}
```

`pflicht_fehlt` nennt nur das Pflichtdokument ohne Pflichtsprache; `fehlende_sprachen` nur die
freiwilligen Lücken (dort fehlt `de` beim Pflichtdokument nie, es steht unter `pflicht_fehlt`).

Grenzfälle: Ein zweisprachiges PDF mit Deutsch zählt als deutsche Fassung. Die Sprache eines
Dokuments kommt aus dem Text, bei Scans aus dem Kürzel am Ende des Dateinamens (`_de`, `_ger`,
`_d_e`); ein Mensch hat das Dokument bei der Freigabe gesehen. Ist die Sprache gar nicht bekannt,
gilt die deutsche Fassung als nicht belegt: in den Stammdaten die Sprachen des Dokuments eintragen
(`PATCH /api/stammdaten/documents/<id>` mit `languages`, der Stand wird dann neu abgeleitet) oder
von Hand erledigen.

Die Suche (`POST /api/boms/<id>/search`) macht nach Ordner und Internet einen zweiten Durchgang im
Herstellerordner: Fehlt einer Dokumentart eine Sprache, werden Dateien mit gleichem Namen bis auf
das Sprachkürzel oder mit derselben eigenen Zertifikatsnummer und derselben Dokumentart zu
Vorschlägen. Bezug sind die Funde aus dem Ordner und die geprüften Dokumente des Bauteils (so
findet eine nur englisch geprüfte Erklärung ihre deutsche Fassung). Dateien in der Pflichtsprache
werden zuerst geprüft; ist eine Lücke geschlossen, kommt für sie kein weiterer Vorschlag. Ein schon
vorgeschlagener Scan ohne Dokumentart und Sprache wird so als Sprachversion gedeutet.
`check_message` beginnt dann mit „Das ist vermutlich die deutsche Fassung von <Datei>, denn …“.

## Belege je Vorschlag

Jeder Vorschlag in `GET /api/boms/<id>/findings` (und in den Antworten von Entscheidung und
Upload) trägt `belege`: je Angabe des Systems, ob sie im Dokument steht. Geprüft wird im Code
(`services/belege.py`), bevor ein neuer Vorschlag gespeichert wird, auch in Tabellen der Seiten.

```json
"belege": [
  {"wert": "typ", "angabe": "KCD2-SR-Ex1.LB", "belegt": false, "seite": 1, "tabelle": 2,
   "zeile": 4, "hinweis": "Genau dieser Typ steht nicht im Dokument. Im Dokument steht nur „KCD2-SOT-EX1.LB“."},
  {"wert": "kennzeichnung", "angabe": "II 3 G", "belegt": true, "seite": 1, "tabelle": 3,
   "zeile": 4, "hinweis": "In derselben Tabellenzeile: Zertifikatsnummer „CML 19ATEX4410X“."},
  {"wert": "zertifikat", "angabe": "CML 19ATEX4410X", "belegt": true, "seite": 1, "tabelle": 3,
   "zeile": 4, "hinweis": "In derselben Tabellenzeile: Kennzeichnung „II 3 G“."},
  {"wert": "kategorie", "angabe": "(1)G, 3G", "belegt": true, "seite": 1, "tabelle": 3,
   "zeile": 3, "hinweis": "Aus der Kennzeichnung „II (1) G“, Einbauort „Schaltschrank außerhalb Ex“."}
]
```

- `wert`: `typ` (Hersteller-Artikelnr. der Position), `kennzeichnung` und `zertifikat` (je Wert
  aus `ex_marking_raw` und `certificate_no` ein Eintrag), `kategorie` (aus den belegten
  Kennzeichnungen, verglichen mit der Zone des Einbauorts)
- `belegt: true`: steht auf Seite `seite`, in einer Tabelle zusätzlich `tabelle` und `zeile` (je
  Seite ab 1). `belegt: false`: steht nicht im Dokument, `hinweis` sagt warum; Seite, Tabelle und
  Zeile zeigen dann höchstens eine ähnliche Stelle zum Nachsehen.
- Der Typ ist nur belegt, wenn genau dieser Typ im Text steht; Typmuster, Baureihe und Stichwort
  sind nie belegt. Die Kategorie ist unbelegt, wenn der Einbauort keine Zone nennt.
- Scans: Werte aus OCR-Text gelten als belegt, der Hinweis sagt aber, dass der Text per OCR gelesen
  ist und auf Scans keine Tabellen ausgewertet sind.
- Ist eine Angabe unbelegt, wird aus `check_result` `ok` ein `unklar`, `check_message` nennt die
  unbelegten Angaben. `warnung` und `unklar` bleiben.
- `belege: null`: noch nicht geprüft (Vorschlag ohne Datei). Ältere offene Vorschläge prüft
  `GET /api/boms/<id>/findings` beim ersten Abruf nach.

`GET /api/findings/<id>/page.png?seite=<Nr>&begriff=<Text>` zeigt eine andere Seite des Dokuments,
z. B. die eines Belegs, mit dem Begriff markiert.

## Angaben aus dem Dokument

Jeder Vorschlag mit Datei trägt `angaben` (JSON, `services/angaben.py`): was der Code sonst noch
im PDF liest, nur mit Regeln, jede Angabe mit `seite` (ab 1) und `text` (Textstelle), `ocr: true`,
wenn die Seite per OCR gelesen ist. Fehlt etwas, ist es `null` bzw. `[]`; geraten wird nichts.

```json
"angaben": {
  "version": 1,
  "gueltig_bis": {"status": "datum", "wert": "2027-12-31", "seite": 1, "text": "… Gültig bis: 31.12.2027 …"},
  "ausstellungsdatum": {"wert": "2020-02-17", "seite": 1, "text": "Datum der Bescheinigung: 17. Februar 2020 …"},
  "ausgabe": {"wert": "9", "seite": 1, "text": "… KEMA 04ATEX2242 Ausgabe Nr.: 9 …"},
  "richtlinien": [{"wert": "2014/34/EU", "art": "atex", "seite": 1, "text": "…"}],
  "normen": [{"wert": "EN 60079-7:2015+A1:2018", "ex": true, "seite": 1, "text": "…"}],
  "stellen": [{"name": "DEKRA Certification B.V.", "nummer": "0344", "rolle": "pruefung", "seite": 1, "text": "…"}],
  "hersteller": {"wert": "Siemens AG", "ort": "76181 Karlsruhe, Deutschland", "passt": true,
                 "stueckliste": "Siemens", "seite": 1, "text": "…"},
  "unterschrift": {"ort": "Mannheim", "datum": "2021-11-22", "unterzeichner": ["ppa. Michael Kessler"], "seite": 1, "text": "…"},
  "kennzeichnungen": [{"wert": "II 2 G Ex eb IIC T4 Gb", "geraet": "II 2 G", "zuendschutz": "Ex eb",
                       "gruppe": "IIC", "temperatur": "T4", "epl": "Gb", "seite": 1, "text": "…"}],
  "umgebungstemperatur": [{"wert": "-20 °C … +70 °C", "min": -20, "max": 70, "seite": 3, "text": "…"}],
  "besondere_bedingungen": {"x": true, "keine": false, "seite": 4, "text": "(17) Besondere Bedingungen …"},
  "iecex": [{"wert": "IECEx PTB 09.0035U", "seite": 1, "text": "…"}],
  "vorpruefung": [{"stufe": "hinweis", "text": "Das Zertifikat läuft am 31.12.2027 ab, also in 120 Tagen. So steht es auf Seite 1."}]
}
```

- `gueltig_bis.status`: `datum` (Datum direkt hinter „gültig bis“, „valid until“, „expiry date“,
  „valable jusqu'au“; auch „31.12.28“), `unbefristet` oder `keine_angabe`. „bis 19.04.2016“ ohne
  „gültig“, Jahre von Normen und Unterschriftsdaten zählen nicht. ATEX-Bescheinigungen haben in der
  Regel kein Ablaufdatum; in den 93 PDFs des Case stand keins.
- `ausstellungsdatum`: spätestes Datum mit Stichwort davor („Datum“, „Date of certification“,
  „issued on“) oder als „Ort, Datum“; ohne Stichwort keins (anders als der Zertifikatsserver, der
  dann das späteste Datum als „unsicher“ nimmt). `ausgabe`: die hinter der eigenen Nummer.
- `stellen[].rolle`: `pruefung` (stellt die Bescheinigung aus), `qm` (überwacht die Fertigung),
  `genannt`. Die vierstellige Nummer nur, wenn der Text sie hinter „benannte Stelle Nr.“,
  „Notified Body number“, „(0102)“ … nennt. Ohne Fund das Kürzel aus der Nummer der Bescheinigung.
- `hersteller.passt`: Vergleich mit dem Hersteller der Position (ohne Rechtsform, „P+F“ =
  „Pepperl+Fuchs“, OCR-Fehler im Namen toleriert), `null`, wenn einer fehlt.
- `vorpruefung` (nur einmal je Vorschlag, auch an `check_message` angehängt). Eine `warnung`
  macht `check_result` zu `warnung`, ein `hinweis` ändert es nicht. Ein Rat, die Entscheidung
  trifft ein Mensch. Regeln und ihre Grundlage:
  - Datum hinter „gültig bis“ liegt vor heute → `warnung` „Das Zertifikat ist seit dem … abgelaufen“
    (Logik).
  - läuft in `ABLAUF_WARNUNG_TAGE` (Standard 180) Tagen ab → `hinweis` (einstellbare Schwelle).
  - nur 94/9/EG, nicht 2014/34/EU: EU-Konformitätserklärung → `warnung` (seit 20.04.2016 muss sie
    2014/34/EU nennen; 94/9/EG ist nach Art. 43 der Richtlinie 2014/34/EU aufgehoben);
    Baumusterprüfbescheinigung → `hinweis` (gilt nach Art. 41 Abs. 2 der Richtlinie 2014/34/EU
    weiter, die Erklärung muss aber 2014/34/EU nennen); Dokumentart unbekannt → `hinweis`.
  - Hersteller im Dokument weicht von der Stückliste ab → `hinweis` (Logik; Vergleich ohne
    Rechtsform, Anfangsbuchstaben wie „P+F“ zählen, OCR-Fehler bis `HERSTELLER_AEHNLICHKEIT`,
    Standard 0.9).
  - per OCR gelesene Daten sagen in der Meldung, dass sie nicht sicher gelesen sind.
- `angaben: null`: noch nicht gelesen (ohne Datei, oder vor dieser Funktion angelegt).
  Nachlesen: `flask --app wsgi angaben-nachlesen` (nur fehlende; `--alle` auch älterer
  Regelstand). Der Befehl läuft nie von selbst und ändert keine Stammdaten.
- Freigabe: `gueltig_bis` geht nach `document.valid_until`, wenn dort noch nichts steht.

### `GET /api/fristen?tage=180`

Zertifikate, die bis heute + `tage` ablaufen oder schon abgelaufen sind (Standard
`ABLAUF_WARNUNG_TAGE`, 0 bis 3650, sonst `400`), nach Datum:

```json
{"stichtag": "2026-11-13", "tage": 180, "anzahl": 2, "abgelaufen": 1, "eintraege": [
  {"quelle": "stammdaten", "gueltig_bis": "2025-01-31", "abgelaufen": true, "tage": -651,
   "dokument_id": 4, "doc_type": "eu_baumusterpruefbescheinigung", "zertifikat": "PTB 01 ATEX 1016",
   "datei": "Festo/x.pdf", "teile": [{"profile_no": "99000010", "artikel": "VACF-…", "hersteller": "Festo", "status": "geprueft"}]},
  {"quelle": "vorschlag", "gueltig_bis": "2027-03-01", "abgelaufen": false, "tage": 108,
   "finding_id": 12, "bom_id": 3, "position": "20", "doc_type": "…", "zertifikat": "…", "datei": "…",
   "teile": [{"profile_no": "…", "artikel": "…", "hersteller": "…", "status": "vorgeschlagen"}],
   "beleg": {"seite": 1, "text": "Gültig bis: 01.03.2027", "ocr": false}}]}
```

`quelle`: `stammdaten` (`document.valid_until`), `vorschlag` (offener Vorschlag mit „gültig bis“
aus dem Dokument), `freigegeben` (freigegebener Vorschlag, dessen Dokument noch kein
`valid_until` hat, z. B. vor dieser Funktion freigegeben). Für eine spätere Seite „Fristen“.

## Suche: Einstellungen und Links

- `GET /api/boms/search-settings`: `{"internet_versuche": Standard, "internet_versuche_max": 50}`
  für die Oberfläche.
- `GET /api/boms/<id>/links`: `{"<item_id>": [{"label", "url"}, …]}`, Suchlinks je Position für
  den Browser des Anwenders (Herstellerseite, Websuche). Das Backend ruft sie nicht selbst ab.

## Dokument zu einer Position hochladen (M18, E6)

`POST /api/boms/<id>/items/<item_id>/documents`, `multipart/form-data`: `file` (PDF, höchstens
30 MB), `by` (Name), `source` (`manuell` oder `hersteller`), `doc_type` (bei `manuell` Pflicht).

- `manuell`: „Zertifikat manuell einfügen“; die Angabe des Menschen ist die Freigabe, das Dokument
  landet sofort in den Stammdaten.
- `hersteller`: Antwort des Herstellers; wird ein normaler Vorschlag mit Vorprüfung.
- `201`: der Vorschlag mit `item_status`. `400` ohne Namen, Quelle, Dokumentart oder bei einer
  Datei, die kein PDF ist, `413` über 30 MB, `404`.

## Von Hand erledigen

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `POST` | `/api/boms/<id>/items/<item_id>/resolve` | `{"by", "note"}` | Position bewusst erledigt (Abweichung von M20), Stand `erledigt`; `409` wenn sie schon fertig ist |
| `POST` | `/api/boms/<id>/items/<item_id>/reopen` | `{"by"}` | nimmt das zurück, der Stand wird neu abgeleitet |
| `POST` | `/api/boms/<id>/issues/<issue_id>/resolve` | `{"by", "comment"?}` | Meldung der Eingangsprüfung abhaken |
| `POST` | `/api/boms/<id>/issues/<issue_id>/reopen` | `{"by"}` | Haken zurücknehmen |

Antwort ist jeweils die Position bzw. Meldung. `400` ohne Namen oder Begründung, `404`, `409`.

## Fragen

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `GET` | `/api/boms/<id>/questions` | – | alle Fragen des Vorgangs mit Kommentaren, offene zuerst |
| `POST` | `/api/boms/<id>/questions` | `{"by", "text", "item_id"?}` | neue Frage, zum Vorgang oder zu einer Position; `201` |
| `POST` | `/api/questions/<id>/comments` | `{"by", "text"}` | Kommentar; `201`, die Frage mit allen Kommentaren |
| `POST` | `/api/questions/<id>/resolve` | `{"by", "answer"}` | Antwort geben und klären |
| `POST` | `/api/questions/<id>/reopen` | `{"by"}` | wieder öffnen |

## Stammdaten (M18)

Bauteile, Dokumente, Zuordnungen und Hersteller der ATEX-Datenbank ansehen und von Hand pflegen.
Bisher schreibt nur die Freigabe hinein. Jede schreibende Anfrage braucht `by` (Name), optional
`note`; jede Änderung steht mit altem und neuem Wert im Änderungsprotokoll
(`stammdaten_aenderung`, siehe [datenmodell.md](datenmodell.md#änderungen-von-hand)). Fehler
kommen als `{"error": "..."}` auf Deutsch: `400` bei fehlendem Namen, falschem Wert, unbekanntem
Feld oder wenn sich nichts ändert, `404`, `409` bei Konflikten.

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `GET` | `/api/stammdaten/werte` | – | feste Werte: `doc_types`, `languages`, `status` |
| `GET` | `/api/stammdaten/parts` | Filter `q`, `manufacturer_id`, `status`, `review_needed=1` | Bauteile nach PRO.FILE-Nr.; `q` sucht in Nummer, Bezeichnung, Typ und Hersteller |
| `POST` | `/api/stammdaten/parts` | `{"by", "profile_no", ...Felder}` | Bauteil von Hand anlegen (`201`, Quelle `manual`); `409`, wenn es die PRO.FILE-Nr. gibt |
| `GET` | `/api/stammdaten/parts/<id>` | – | Bauteil mit `documents`, `changes` und `bom_items` (Positionen mit derselben PRO.FILE-Nr.) |
| `PATCH` | `/api/stammdaten/parts/<id>` | `{"by", "note", ...Felder}` | Felder ändern: `name_de`, `name_en`, `manufacturer` (Name; unbekannt: wird angelegt), `manufacturer_article_no`, `ex_marking_raw`, `manufacturer_url` (`http(s)://` oder leer), `review_needed` (`true`/`false`). Leerer Text leert das Feld. Die PRO.FILE-Nr. bleibt fest |
| `POST` | `/api/stammdaten/parts/<id>/documents` | `{"by", "document_id", "status"}` | vorhandenes Dokument zuordnen (`201`); `status` Standard `ungeprueft`. `409`, wenn schon zugeordnet |
| `PATCH` | `/api/stammdaten/parts/<id>/documents/<doc_id>` | `{"by", "status", "note"}` | Prüfstatus der Zuordnung: `ungeprueft`, `ki_geprueft`, `geprueft` |
| `DELETE` | `/api/stammdaten/parts/<id>/documents/<doc_id>` | `{"by", "note"}` | Zuordnung lösen: die Zeile in `part_document` fällt weg, das Dokument bleibt |
| `GET` | `/api/stammdaten/documents` | Filter `q` (Datei, URL, Zertifikats-Nr.), `doc_type`, `unlinked=1` | Dokumente, je mit `parts` (zugeordnete Bauteile mit `link_status`) |
| `GET` | `/api/stammdaten/documents/<id>` | – | Dokument mit `parts` und `changes` |
| `PATCH` | `/api/stammdaten/documents/<id>` | `{"by", "note", "doc_type", "certificate_no", "valid_until", "languages"}` | Felder ändern; `valid_until` als `JJJJ-MM-TT` oder leer, `languages` als Liste aus `de`, `en`, `fr` |
| `DELETE` | `/api/stammdaten/documents/<id>` | `{"by", "note"}` | Dokument löschen, nur ohne Zuordnung (sonst `409`). Die Datei im Ordner bleibt |
| `GET` | `/api/stammdaten/documents/<id>/file` | – | die PDF-Datei, nur innerhalb von `ATEX_DOCS` wie bei `/api/findings/<id>/file`; sonst `404` |
| `GET` | `/api/stammdaten/manufacturers` | – | Hersteller nach Name mit `part_count`, `email` (aus den [Herstellerkontakten](#herstellerkontakte), sonst `null`) und `kontakt_fehlt` |
| `PATCH` | `/api/stammdaten/manufacturers/<id>` | `{"by", "name", "note"}` | Namen ändern; `409`, wenn es den Hersteller (gleicher `normalized_name`) schon gibt |
| `GET` | `/api/stammdaten/changes` | Filter `table`, `limit` (Standard 200, höchstens 500) | Änderungsprotokoll, das neueste zuerst |

Ein Bauteil in Listen und Detail:

```json
{"id": 3, "profile_no": "99000030", "name_de": "Näherungsschalter für Zylinder", "name_en": null,
 "manufacturer_id": 1, "manufacturer": "Festo", "manufacturer_article_no": "SMT-8F-I-8,2V-K5,0-OE-EX",
 "ex_marking_raw": "II 1 GD EEx ia IIC T4...T6", "manufacturer_url": null,
 "legacy_last_modified": null, "status": "ungeprueft", "review_needed": false,
 "source": "manual", "changed_by": "Anna", "document_count": 1, "checked_count": 1,
 "missing_doc_types": []}
```

`missing_doc_types` sind die Pflichtdokumente (M20), die nicht geprüft zugeordnet sind. Im Detail
trägt jedes Dokument unter `documents` die Felder des Dokuments und dazu `link_status`,
`link_source`, `verified_by`, `verified_at`, `link_changed_by`.

Regeln:

- Nur ein Mensch setzt `geprueft` (M3): die Anfrage setzt `verified_by` = `by` und
  `verified_at` = jetzt. Jeder andere Status leert beide.
- Jede Änderung setzt `changed_by` der Zeile (Hersteller haben keine solche Spalte; dort steht der
  Name nur im Protokoll).
- Nach Zuordnen, Lösen, Ändern des Prüfstatus, der Dokumentart und der Sprachen wird der Stand aller
  Positionen mit derselben PRO.FILE-Nr. neu abgeleitet. Die Antwort trägt dann `items_updated`
  (Anzahl der Positionen).
- Ein umbenannter Hersteller wird bei einer späteren Freigabe nur unter dem neuen Namen
  wiedergefunden; eine Position mit dem alten Namen legt ihn neu an.

## Zertifikatsserver in der Suche

Mit `ZERTIFIKATSSERVER_URL` fragt die Ordnersuche je Position den Zertifikatsserver nach den
Markierungen ihrer Funde (über den SHA-256, `GET /api/dokumente?sha256=…` des Servers):

- bestätigt veraltet: wird nicht vorgeschlagen und zählt nicht als Fund; die Suche geht weiter
  (Internet, sonst „nicht gefunden“ und E-Mail an den Hersteller). „gesucht in“ nennt die Zahl.
- Verdacht: der Vorschlag bleibt, `check_message` nennt den Grund, `check_result` ist nie `ok`.
- Server nicht erreichbar: Suche wie ohne Server, „gesucht in“ sagt es.

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `GET` | `/api/boms/<id>/veraltet` | – | `{"items": [Position + "veraltet": [{dateiname, pdf_url, gruende}]]}`: Positionen, für die ein bestätigt veraltetes Dokument noch zählt (offener Vorschlag oder Stammdaten-Zuordnung nicht „ungeprueft“) |
| `POST` | `/api/boms/<id>/veraltet/suche` | `{"by", "item_ids"?, "internet_versuche"?}` | Zuordnungen dieser Dokumente in den Stammdaten auf `ungeprueft` (mit Protokoll), offene Vorschläge davon verworfen, dann neue Suche dieser Positionen; `409` wenn keine betroffen ist |

`503`, wenn kein Server eingestellt oder erreichbar ist.

### Eine Ablage: Freigaben zum Server, Dokumente nur im Server in die Suche

Beides nur mit `ZERTIFIKATSSERVER_URL`; ohne sie ändert sich nichts (`services/zertifikatsserver.py`).

**Freigabe -> Server.** Gibt ein Mensch einen Vorschlag frei (`POST /api/findings/<id>/decision`,
Oberfläche „freigeben“, PDF „manuell“ einfügen), prüft das Backend nach dem Speichern über den
SHA-256 (`GET /api/dokumente?sha256=…`), ob der Server die Datei kennt. Wenn nicht:

1. Teil suchen: `GET /api/teile?hersteller=…&artikelnr=…` (Hersteller und Hersteller-Artikelnr.
   der Position), nur ein Treffer „genau“ zählt; sonst `POST /api/teile` mit PRO.FILE-Nr. und
   Bezeichnung.
2. Hochladen als neue Version: `POST /api/teile/<id>/dokumente` mit `datei`, `von` = Name des
   Freigebenden, `doc_type` des Vorschlags, `quelle` = `atex_agent`, dazu der Quellennachweis als
   eigene Felder (`zertifikatsserver.quelle_felder`):

   | Feld | Inhalt |
   |---|---|
   | `quelle_art` | aus `finding.source`: `internet` → `internet`, `hersteller` → `hersteller`, `manuell` → `hochgeladen`, `ordner` → `altbestand`, `suchdienst` → `atex_agent` |
   | `quelle_link` | `finding.url`, nur `http(s)`; bei Funden der Websuche der Link auf die Datei selbst (nach Weiterleitungen) |
   | `quelle_abgerufen_am` | Änderungszeit der Datei unter `ATEX_DOCS` (UTC) für `internet`, `hersteller`, `manuell`: das Backend legt die Datei beim Eingang ab |
   | `quelle_dateiname` | Internet: letzter Teil des Links; Ordner: Pfad relativ zu `ATEX_DOCS`; sonst der Dateiname |

   `quelle_seite` (Webseite, auf der der Link stand) fehlt: die Websuche (`services/internet.py`)
   hält sie nicht fest, das Archiv zeigt sie als „nicht bekannt“. Der Satz `hinweis` = „Im ATEX-Agent
   freigegeben von <Name>; Quelle: …“ geht weiter mit, für ältere Archive ohne diese Felder; das
   neue Archiv kürzt ihn auf „Im ATEX-Agent freigegeben von <Name>.“. Die bisherige Version derselben
   Dokumentart geht im Server ins Archiv.

Ergebnis steht im Protokoll des Vorgangs (`bom_event`, wie es die Oberfläche zeigt) und im Log. Ist der
Server weg oder lehnt er ab, **gilt die Freigabe trotzdem**; der Eintrag nennt den Grund.
Nachgeholt wird mit `flask --app wsgi zert-abgleich` (alle freigegebenen Vorschläge, deren Datei
der Server nicht kennt; Ausgabe je Datei, Rückgabewert 1, wenn etwas nicht ging) und nebenbei bei
der nächsten Freigabe (höchstens 5 Dateien). Wiederholbar: eine Datei, die der Server kennt,
geht nicht noch einmal hin. Ohne Hersteller-Artikelnr. der Position wird nichts übertragen.

**Server -> Suche.** `zertifikatsserver.ordner(ATEX_DOCS, hersteller)` fragt
`GET /api/dokumente?hersteller=…&ablage=aktuell&ausgemustert=nein&dateiart=pdf` und lädt die PDFs
(andere Dateiarten des Archivs nie; ältere Archive ohne `dateiart` liefern nur PDFs), die nicht
schon irgendwo unter `ATEX_DOCS` liegen (gleicher SHA-256) und nicht als veraltet bestätigt sind,
nach `ATEX_DOCS/_upload/zertifikatsserver/<Hersteller>/` (geprüft über den SHA-256). Diesen
Ordner gibt sie zurück, sonst `None` (auch wenn der Server nicht antwortet). Was der Server
inzwischen als veraltet bestätigt, ausgemustert oder abgelöst hat, wandert nach
`_upload/zertifikatsserver/_zurueckgezogen/<Hersteller>/` und wird nicht mehr gefunden (gelöscht
wird nichts). Die Dateien liegen innerhalb von `ATEX_DOCS`, Vorschau `/files/…` und Pfadprüfung
gelten wie für jeden Fund.

### Schnittstelle des Dokumentenarchivs (Auszug)

Der Zertifikatsserver heißt für Anwender „Dokumentenarchiv“; die Oberfläche spricht ihn unter
`/zert/api/…` an (nginx, mit Anmeldung). Vollständige Tabelle in
[../zertifikatsserver/README.md](../zertifikatsserver/README.md#api). Neu oder geändert:

| Methode und Pfad | Zweck |
|---|---|
| `GET /api/begriffe` | alle Texte für Anwender je technischem Wert (Dokumentarten, Stände, Herkunft, Feldnamen des Protokolls …), Prüfregeln mit ihren Dokumentarten, erlaubte Dateiformate |
| `POST /api/teile/<id>/dokumente` | neue Version: PDF oder ein erlaubtes anderes Format (Word, Excel, PowerPoint, OpenDocument, RTF, Text, CSV, PNG, JPG, GIF, TIFF, DWG, DXF, STEP, ZIP, 7z; Endung und Inhalt müssen passen, bis `DATEI_MAX_MB`); bei anderen Formaten ist `doc_type` Pflicht; Quellennachweis `quelle_art`, `quelle_link`, `quelle_seite`, `quelle_abgerufen_am`, `quelle_dateiname`, `quelle_hinweis`; abgelehnte Dateien: `400` mit einem Satz für Anwender |
| `POST /api/dokumente` | Datei ohne Teil ablegen: `datei`, `hersteller`, `doc_type`?, Quellennachweis; gleiche Datei schon da: `200` mit `bekannt: true` |
| `PATCH /api/dokumente/<id>` | dazu die Felder `quelle_*`: Quellennachweis korrigieren, mit Protokoll |
| `GET /api/dokumente/<id>/datei` | die Datei mit ihrem Typ; nur PDF und PNG/JPG/GIF im Browser, alles andere als Anhang, immer `nosniff`; `…/pdf` ist derselbe Link unter altem Namen |
| `GET /api/dateien/<sha256>` | dieselbe Datei über ihren SHA-256 (stabiler Link, den das Backend ohne Nachfrage bilden kann) |
| `GET /api/dokumente?dateiart=&familie=&auslesbar=&quelle_art=` | weitere Filter |
| `POST /api/normen/bestaetigen` | Vorgaben der Normtabelle unverändert bestätigen: `{"normen": ["60079-0"], "von"}` |

Dokumente tragen `dateiart`, `dateiart_text`, `familie`, `auslesbar` (nur PDFs: Angaben und
Prüfregeln), `auslesbar_grund` (Satz für Anwender), `datei_url`, `quelle_art`; im Detail
`quellennachweis` (`art`, `art_text`, `link`, `seite_gefunden_auf`, `abgerufen_am`,
`uebernommen_von`, `uebernommen_am`, `dateiname`, `hinweis`, `sha256`, `archiv_link`, `text`), dazu
die Texterkennung (`per_ocr`, `ocr_seiten`, `ocr_stand`, `ocr_stand_text`, `ocr_hinweis`). Neben
technischen Werten stehen Texte: `doc_type_text`, `markierungsstand_text`, `ablage_text`,
`stand_text`, `status_text`, `stufe_text`, im Protokoll `objekt_text`, `feld_text`, `alt_text`,
`neu_text`. Wer schreibt, kommt aus dem Kopf `X-Anwender` (nginx, Anmeldung), sonst aus `von`.

## Vertrag der Oberfläche

Die HTML-Oberfläche des Teams (`web/index.html`, nach dem Entwurf aus dem Team) spricht einen
eigenen Vertrag, den der Entwurf mit einer Attrappe vorgab. Das Backend erfüllt ihn mit `routes/oberflaeche.py` und `services/oberflaeche.py`, einem Adapter auf unser
Datenmodell und unsere Dienste, ohne eigene Fachlogik. Fehler dort als `{"fehler": "…"}` mit
`400`, `404`, `409` (dazu `502` beim Versand, `503` ohne Ex-Ordner); der Rest der API bleibt bei
`{"error": "…"}`.

| Vertrag | bei uns |
|---|---|
| Vorgang, Artikel, Fund, Mail | `bom`, `bom_item`, `finding`, `email_draft` (Anfrage samt Erinnerungen); Mängelliste = `purchasing_email` |
| `GET /api/vorgaenge` | alle Vorgänge mit Zählern je Stand (siehe [Bearbeiten in den Tabellen](#bearbeiten-in-den-tabellen-der-oberfläche) für die Zusatzfelder) |
| `POST /api/vorgaenge` | wie `POST /api/boms` (`excel_import`), multipart `datei`, optional `name`; Antwort `{id}` |
| `GET /api/vorgaenge/<id>` | ein Vorgang mit seinen Artikeln (Stand im Vertrag, siehe unten) |
| `POST /api/vorgaenge/<id>/start` | optional `{"sprachen": ["de", "en"]}` (aus `de`, `en`, `fr`, mindestens eine, sonst `400`): die Sprachen, die dieser Vorgang braucht, gespeichert in `Bom.sprachen`; `GET /api/vorgaenge/<id>` nennt sie unter `sprachen` (`moeglich`, `gewaehlt`). Dann `suche.suche_vorgang` je Position in einem Hintergrund-Thread, Speichern je Position, danach `email_entwurf.lege_entwuerfe_an`; `409`, solange er läuft, `503` ohne Ex-Ordner |
| `GET /api/vorgaenge/<id>/ereignisse?seit=N` | Protokoll des Laufs und der Aktionen |
| `GET /api/artikel/<id>` | ein Artikel mit Funden, Prüfergebnis, Herkunft und `notiz` |
| `POST /api/artikel/<id>/freigeben`, `POST /api/artikel/<id>/ablehnen` | `freigabe.entscheide`; ablehnen ohne `fund_id` lehnt alle offenen Funde ab, der Grund steht im Entwurf als Zeile „Grund der Anfrage: …“ |
| `POST /api/artikel/<id>/korrigieren` | `korrektur.korrigiere` (`hersteller`, `artikelnr`, `einbauort`, zusätzlich `profile_no`) |
| `POST /api/artikel/<id>/zertifikat` | `hochladen.lade_hoch` |
| `GET /api/vorgaenge/<id>/mails` | Anfragen und Erinnerungen des Vorgangs mit Empfänger und Stand |
| `PUT /api/mails/<id>`, `POST /api/mails/<id>/senden` | Text ändern; senden = Freigabe durch den, der drückt, und Versand an `versand_an` (Standard: `MAIL_TEST_ADDRESS`); ohne Adresse des Herstellers `409` |
| `POST /api/mails/<id>/erinnern` | `erinnerung`: erst Erinnerung (angelegt und gleich gesendet), dann Eskalation an den Einkauf |
| `POST /api/vorgaenge/<id>/maengel/senden` | `korrektur.lege_maengelliste_an`, freigeben, `sende_maengelliste` |
| `GET /api/vorgaenge/<id>/export` | `excel_export.kundenliste`, `409` solange etwas offen ist; `?zwischenstand=1` jederzeit (`excel_export.zwischenstand`) |
| `GET /files/<pfad>` | PDF aus `ATEX_DOCS` (nur darin, nur `.pdf`); `?markiert=<Fund-id>` mit Fundstelle und Belegen wie `GET /api/findings/<id>/file?markiert=1` |

Stand eines Artikels:

| unser Stand | Vertrag |
|---|---|
| `kein_ex_teil` | `kein_ex` |
| `offen` | `wartet` |
| `klaerung` | `info_fehlt` (auch bei offener Ex-Frage; Grund in `notiz`) |
| `vorschlag` | `zu_pruefen` |
| `vorhanden` | `geprueft`, wenn ein Mensch in diesem Vorgang einen Fund dazu freigegeben hat, sonst `in_db` |
| `nicht_gefunden` | `mail_entwurf` |
| `angefordert` | `angefordert`, nach gesendeter Erinnerung `erinnert` |
| `eskaliert` | `eskaliert` |
| `erledigt` (von Hand) | `geprueft`, Begründung in `notiz`, `freigabe.von` = wer erledigt hat |
| Hintergrundsuche bearbeitet die Position | `suche`, `suche_in`: `Datenbank`, dann `Ordner des Herstellers` (oder `Internet`, wenn es keinen Ordner gibt und das Internet an ist), dann je nach Stufe `Ähnlichkeitssuche` oder `Internet` |

Ein Fund: `felder` aus `finding.belege` (Typ, Kennzeichnung, Zertifikatsnummer, Kategorie; je
belegt mit `{seite, text}`, unbelegt `null`), dazu Dokumenttyp und Sprachen (immer ohne Beleg, mit
`"abgeleitet": true`: vom Agenten erkannt, die Oberfläche zeigt sie neutral und zählt sie nicht als
unbelegt).
Aus `finding.angaben` (siehe [Angaben aus dem Dokument](#angaben-aus-dem-dokument)) nach dem
Dokumenttyp „Gültig bis“ (z. B. „01.03.2027, noch 108 Tage gültig“) und „Ausgestellt“ („Am
17.02.2020, Ausgabe 9“), nach den Belegen „Zündschutzart“, „Umgebungstemperatur“, „Besondere
Bedingungen“, „IECEx-Nummer“, „Benannte Stelle“, „Hersteller laut Dokument“, „Richtlinien“,
„Genannte Normen“, „Unterschrift“; jeweils nur, wenn das Dokument dazu etwas sagt. Text aus
eingescannten Seiten sagt im Beleg, dass er nicht sicher gelesen ist.
`quelle` (`services/oberflaeche.QUELLE`): `ordner` → „Ordner des Herstellers“, `suchdienst` →
„Ähnlichkeitssuche“, `internet` → „Internet“, `manuell` → „von Hand eingefügt“, `hersteller` →
„Antwort des Herstellers“; bei `in_db` die
geprüften Dokumente der Stammdaten mit `quelle: "Datenbank"`. `url` zeigt auf
`/files/<pfad>?markiert=<id>`. Zusätzlich zum Vertrag: `pruefung` (`ergebnis`, `meldung` der
Prüfregeln), `status` des Vorschlags; am Artikel `gesucht_in`; an der Mail `art`.

Herkunft je Fund (M6), Block `quellennachweis` (`zertifikatsserver.quellennachweis`; nicht unter
`quelle`, denn `quelle` ist der Text des Vertrags, den `index.html` zeigt). Fehlt bei Dokumenten aus
den Stammdaten ohne freigegebenen Fund (`id` negativ):

```json
{"art": "internet", "art_text": "Im Internet gefunden",
 "link": "https://www.festo.com/media/ke.pdf", "seite_gefunden_auf": null, "seite_bekannt": false,
 "abgerufen_am": "2026-11-13T23:26:29", "datei": "ke.pdf", "sha256": "…",
 "archiv_link": "/zert/api/dateien/<sha256>",
 "text": "Die Suche hält nicht fest, auf welcher Webseite der Link stand."}
```

`archiv_link` steht nur mit `ZERTIFIKATSSERVER_URL` da und ist nicht geprüft (keine Anfrage je
Fund); ob das Archiv die Datei hat, fragt die Oberfläche (`web/seiten/quellen.js`) mit einer
Anfrage für alle Funde einer Position. `seite_bekannt` ist `false` bei Funden der Websuche, sonst
`null`; `abgerufen_am` nur bei `internet`, `hersteller`, `manuell`.

Abweichungen vom Vertrag und von der Attrappe:

- Wer entscheidet (M3): der angemeldete Benutzer (siehe [Anmeldung](#anmeldung-und-benutzer)).
  Nur mit `LOGIN_PFLICHT=0`: `von` (oder `by`) im JSON bzw. Formular, sonst der Kopf
  `X-Anwender`, sonst „Oberfläche“.
- Protokoll (`bom_event`) und Name des Vorgangs (`bom.name`) stehen in der Datenbank. Dass eine
  Suche läuft, steht in `bom.search_started_at`: Nach einem Neustart mitten im Lauf setzt das
  Backend sie bei der ersten Anfrage für die noch nicht gesuchten Positionen fort (Eintrag im
  Protokoll). Welche Position gerade dran ist, liegt im Arbeitsspeicher; das setzt einen Prozess
  voraus, wie im Dockerfile (gunicorn mit einem Prozess und mehreren Threads).
- `korrigieren`: auch `bezeichnung`; eine neue Bezeichnung allein lässt Vorschläge und Entwürfe
  stehen und stößt keine neue Suche an.
- `freigeben`: optional `doc_type`, wenn der Mensch die erkannte Dokumentart korrigiert (`400`
  bei unbekannter Art). Jeder Fund trägt dafür `doc_type`.
- `zertifikat`: ohne `doc_type` geht das PDF als Vorschlag in die Prüfung, auch bei `manuell`
  (wie im Vertrag); mit `doc_type` und `manuell` ist es sofort freigegeben wie bei
  `POST /api/boms/<id>/items/<item_id>/documents`.
- `PUT /api/mails/<id>`: `an` lässt sich nicht ändern (`400`, wenn es weder `to_address` noch
  `empfaenger_hersteller` ist); die Adresse kommt aus den Herstellerkontakten.
- `erinnern`: Standard `OBERFLAECHE_DEMO_48H=1` tut so, als wäre die Frist
  `ERINNERUNG_NACH_STUNDEN` vorbei (Demo-Knopf der Oberfläche); mit `0` gilt die echte Frist
  (`409` vorher). Die Erinnerung wird gleich gesendet; der Klick ist die Freigabe.
- `ablehnen`, wenn zur Position schon eine Anfrage gesendet ist: kein neuer Entwurf, die Position
  bleibt `angefordert` (`lege_entwuerfe_an` legt je Position nur einen an).
- `OBERFLAECHE_SUCHE_SYNCHRON=1` lässt die Suche im Anfrage-Thread laufen (Tests).

## Sprachmodelle und Einstellungen je Vorgang

Modelle stehen in `config/modelle.yaml` (`services/modelle.py`, [../config/README.md](../config/README.md)).
Keine Antwort enthält einen Schlüssel. Fehler als `{"error": "…"}` in Worten für Anwender; wo
es dem Administrator hilft, steht die technische Meldung (ohne Schlüssel) zusätzlich in `detail`.

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `GET` | `/api/modelle` | – | `{quelle: "datei"\|"umgebung", datei, standard: {aufgabe: name}, aufgaben: {aufgabe: Text}, sprachmodell_genutzt: {websuche, email}, hinweise: [Text], modelle: [{name, art, modell, anbieter, api_base, zugang_hinterlegt, ohne_schluessel, zugang_aus, timeout, standard_fuer: [aufgabe]}]}`; `503`, wenn die Datei fehlerhaft ist (`error` für Anwender, `detail` mit Zeile bzw. Modellname) |
| `POST` | `/api/modelle/<name>/test` | – | kleine Anfrage an das Modell (Chat: „OK“, Embedding: ein Vektor): `{name, modell, erreichbar, dauer_ms, antwort}` oder `{…, fehler, detail}`: `fehler` sagt in Worten, was los ist (Zugang abgelehnt, nicht erreichbar, Modell unbekannt …), `detail` ist der bereinigte Rohtext des Anbieters; `404` unbekannter Name, `503` fehlerhafte Datei |
| `GET` | `/api/vorgaenge/<id>/einstellungen` | – | siehe unten |
| `PUT` | `/api/vorgaenge/<id>/einstellungen` | `{"von", "modell"?, "internet_versuche"?}` | ändert nur genannte Schlüssel; `null` oder `""` = Standard; je Änderung ein Eintrag im Protokoll („Y hat für diesen Vorgang das Sprachmodell „X“ gewählt.“, „Y hat die Websuche für diesen Vorgang auf höchstens N Versuche je Position gestellt.“). `400`: Name fehlt, Modell unbekannt oder kein Chat-Modell, Versuche keine ganze Zahl von 0 bis `internet.OBERGRENZE` (50), nichts zu ändern; `409` während der Agent sucht; `503` Modell gewählt bei fehlerhafter Datei. Antwort wie `GET` |

Antwort von `GET …/einstellungen`:

| Feld | Bedeutung |
|---|---|
| `modell` | gewähltes Chat-Modell, `null` = Standard |
| `modell_auswahl` | `[{name, modell, anbieter}]`, die Chat-Modelle der Datei |
| `standard` | `{websuche, email}`: Modell ohne Wahl |
| `sprachmodell_genutzt` | `{websuche: INTERNET_LLM, email: EMAIL_MIT_LLM}`: ob die Aufgabe überhaupt ein Modell fragt |
| `modell_wirksam` | `{websuche, email}`: Name, den der nächste Lauf nimmt; `null`, wenn die Aufgabe keins nutzt (Schalter aus, Websuche 0 Versuche, Fehler) |
| `hinweise` | z. B. „Die Websuche nutzt derzeit kein Sprachmodell. …“ |
| `modell_fehler` | Datei fehlerhaft oder gewähltes Modell nicht mehr eingerichtet, sonst `null` |
| `internet_versuche`, `internet_versuche_standard`, `internet_versuche_max`, `internet_versuche_wirksam` | Wert des Vorgangs (`null` = Standard), `INTERNET_VERSUCHE_PRO_TEIL`, Obergrenze, was der nächste Lauf nimmt |
| `sekunden_je_versuch_max` | `3 × INTERNET_TIMEOUT`, längste Dauer eines Versuchs (für die Zeitangabe) |
| `laeuft` | der Agent sucht gerade (dann `PUT` → `409`) |

Wo die Einstellungen wirken: Lauf der Oberfläche (`POST /api/vorgaenge/<id>/start`, auch nach
einem Neustart fortgesetzt), `POST /api/boms/<id>/search`, `POST /api/boms/<id>/korrektur/suche`
und `POST /api/boms/<id>/veraltet/suche`; eine Angabe `internet_versuche` im Aufruf geht dem Wert
des Vorgangs vor. E-Mail-Entwürfe nehmen das Modell des Vorgangs immer, auch nach einer
Entscheidung außerhalb eines Laufs. Der Eintrag „Agent startet: …“ im Protokoll endet mit einem
Satz zum Modell des Laufs („Der Agent nutzt das Sprachmodell „X“, das für diesen Vorgang gewählt
ist.“, „… das Standard-Sprachmodell „X“.“ oder „Der Agent arbeitet ohne Sprachmodell.“); je
E-Mail-Entwurf mit Modell steht ein Eintrag, welches Modell die Einleitung geschrieben hat oder
dass die feste Vorlage gilt.

## Anmeldung und Benutzer

Code: `app/auth.py` (Prüfung an einer Stelle, `before_request`), `routes/auth.py`,
`services/benutzer.py`, Tabelle `benutzer` ([datenmodell.md](datenmodell.md#benutzer)).
Fehler hier mit beiden Schlüsseln `{"fehler": "…", "error": "…"}`.

| Anfrage | Wirkung |
|---|---|
| `POST /api/auth/login` `{"login", "passwort"}` | `200` wie `GET /api/auth/ich` und Sitzungs-Cookie; `401` immer mit demselben Text (unbekannt, falsch, deaktiviert); `429` gesperrt nach `LOGIN_MAX_FEHLVERSUCHE` (5) Fehlversuchen für `LOGIN_SPERRE_MINUTEN` (15). Jeder Fehlversuch wartet `LOGIN_VERZOEGERUNG` (0,5 s). Anmeldename ohne Rücksicht auf Groß- und Kleinschreibung |
| `POST /api/auth/logout` | `{"ok": true}`, Sitzung leer; auch ohne Anmeldung |
| `GET /api/auth/ich` | `{"id", "login", "name", "rolle", "rolle_text"}`; `401` ohne Anmeldung |
| `POST /api/auth/passwort` `{"alt", "neu"}` | eigenes Passwort, mindestens 8 Zeichen; `400`, wenn das alte nicht stimmt. Andere Sitzungen des Kontos enden, diese bleibt |
| `GET /api/auth/pruefe` | für nginx (`auth_request` vor `/zert/`): `204` mit Kopf `X-Anwender-Name` (Anzeigename, URL-kodiert), `401` ohne Anmeldung, `403`, wenn ein Leser schreiben will (Methode im Kopf `X-Original-Method`) |
| `GET /api/benutzer` | nur `admin`: alle Konten (`id`, `login`, `name`, `rolle`, `rolle_text`, `aktiv`, `gesperrt`, `angelegt_am`, `angelegt_von`, `letzte_anmeldung`), nie ein Passwort oder Hash |
| `POST /api/benutzer` `{"login", "name", "rolle", "passwort"}` | nur `admin`: `201` das Konto; `409`, wenn Anmeldename oder Anzeigename (ohne Groß/klein) schon vergeben sind |
| `PATCH /api/benutzer/<id>` `{"name"?, "rolle"?, "aktiv"?, "passwort"?}` | nur `admin`: ändern; `passwort` setzt es zurück und hebt eine Sperre auf. Neues Passwort und Deaktivieren beenden die Sitzungen des Kontos. `409`, wenn der letzte aktive Admin seine Rolle oder `aktiv` verlöre. Kein `DELETE` (`405`): Namen bleiben im Protokoll |

Rollen: `admin` (alles und die Benutzerverwaltung), `pruefer` (alle Arbeit: hochladen, suchen,
freigeben, ablehnen, korrigieren, E-Mails, Export, Stammdaten, Zertifikatsserver), `leser` (nur
lesen). Ohne Anmeldung: `401` `{"fehler": "Sie sind nicht angemeldet. …", "error": …}`, für jeden
Pfad außer den offenen (auch unbekannte Pfade, die API verrät nichts). Schreibende Anfragen, die
der Browser als `Sec-Fetch-Site: cross-site` oder `same-site` meldet, bekommen `403` (zusätzlich
zu `SameSite=Lax` am Cookie gegen fremde Seiten).

**Name aus der Sitzung:** Für angemeldete Benutzer ersetzt der Server in jeder schreibenden
Anfrage `by` (immer) und `von` (wenn vorhanden) im JSON und im Formular sowie den Kopf
`X-Anwender` durch den Anzeigenamen (`AnfrageMitAnwender` in `app/auth.py`). Ohne Körper gilt
`{"by": Name}`. So gilt das auch für Routen, die später dazukommen; `auth.anwender()` liefert
den Namen direkt.

Einstellungen: `LOGIN_PFLICHT` (Standard `1`; `0` nur für Tests, dann offen wie früher),
`SECRET_KEY`, `SITZUNG_STUNDEN` (12, ab der letzten Anfrage), `SESSION_COOKIE_SECURE`,
`ADMIN_BENUTZER`, `ADMIN_NAME`, `ADMIN_PASSWORT` (Start-Admin, siehe README). Befehle:
`flask --app wsgi init-db` (legt den Start-Admin an, wenn es den Anmeldenamen noch nicht gibt),
`flask --app wsgi benutzer-anlegen` (fragt Name, Rolle, Passwort).

## Bearbeiten in den Tabellen der Oberfläche

`routes/bearbeiten.py`, `services/bearbeiten.py`. Die Oberfläche (`web/seiten/tabelle.js`) ruft
diese Endpunkte erst, wenn ein Mensch im Bestätigungsdialog „Ja, speichern“ gedrückt hat. Jede
Anfrage braucht den Namen als `von` (oder `by`, sonst Kopf `X-Anwender`), sonst `400`. Fehler
wie im Vertrag der Oberfläche als `{"fehler": "…"}` in ganzen Sätzen.

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `PATCH` | `/api/vorgaenge/<id>` | `{"von", "name"}` | Vorgang umbenennen. Der Name ist eindeutig, bei Gleichstand mit „ (2)“ … (`oberflaeche.eindeutiger_name`). Antwort: Vorgang ohne Artikel, `name` der gespeicherte, `gewuenscht` der geschickte. Eintrag im Protokoll |
| `PATCH` | `/api/artikel/<id>` | `{"von", "note"?, Felder aus korrektur.FELDER}` | Position korrigieren: `description`, `manufacturer`, `manufacturer_article_no`, `profile_no`, `location`, `quantity`. Regeln wie `POST /api/boms/<id>/items/<item_id>/korrektur` (Verlauf, Meldungen abhaken, neue Suche außer bei Bezeichnung und Menge); leer `400`. Antwort: Artikel wie in der Liste des Vorgangs plus `korrekturen`. Eintrag im Protokoll |
| `GET` | `/api/artikel/<id>/fundangaben` | – | je Fund `{"<fund_id>": {offen, status, felder: [{schluessel, titel, art, optionen?, leer, wert, gelesen, korrektur}]}}`; `korrektur` ist die letzte Korrektur (`von`, `zeit`, `alt`, `notiz`) oder `null` |
| `PATCH` | `/api/funde/<id>` | `{"von", "note"?, Werte aus bearbeiten.FUND_FELDER}` | aus dem PDF gelesene Werte korrigieren: `doc_type`, `kennzeichnung`, `zertifikat`, `sprachen` (Kürzel mit Komma) und aus `finding.angaben` `gueltig_bis`, `ausstellungsdatum` (Datum, auch als 31.12.2030), `ausgabe`, `richtlinien`, `normen`, `stellen`, `umgebungstemperatur`, `iecex` (Listen mit `;` getrennt, in `fundangaben` mit `liste: true`); nur solange der Fund `vorgeschlagen` ist, sonst `409`. Jede Änderung steht in der Tabelle `finding_correction` und im Protokoll; die Freigabe übernimmt die korrigierten Werte. Antwort wie bei `fundangaben` |

`GET /api/vorgaenge` liefert je Vorgang zusätzlich `maschine` und `datei` (Filter der
Projektseite). `korrektur.FELDER` enthält jetzt auch `quantity` (Menge); wie die Bezeichnung
stößt sie keine neue Suche an, und die korrigierte Excel übernimmt sie ebenfalls.

Werte aus `finding.angaben` sind je ein Eintrag in `FUND_FELDER` mit `angabe=`. Ein Wert, der in
`angaben` als `{"wert": …}` steht, wird dort ersetzt und mit `"von_hand": true` markiert; die
Fundstelle (Seite, Text) fällt dann weg. Listen behalten bei unveränderten Einträgen deren
Fundstelle. Nach einer Korrektur von `gueltig_bis` oder `richtlinien` läuft die Vorprüfung der
Angaben neu (`bearbeiten.vorpruefung_neu`): ein jetzt abgelaufenes Datum gibt eine Warnung („…
so ist es von Hand eingetragen.“), eine falsch gelesene alte Warnung verschwindet. Die Freigabe
trägt `gueltig_bis` in `Document.valid_until` ein, wenn dort noch nichts steht.

## Herstellerkontakte

E-Mail-Adresse je Hersteller, in der Anwendung pflegbar (Wunsch aus dem Team). Eigene Tabelle
`hersteller_kontakt` ([datenmodell.md](datenmodell.md#herstellerkontakte)), jede Änderung im
Änderungsprotokoll der Stammdaten (`table_name` = `hersteller_kontakt`). Fehler als
`{"error": "…"}` auf Deutsch, für Anwender formuliert: `400` (fehlender Name `by`, ungültige
Adresse, Sprache nicht `de`/`en`, unbekanntes Feld, nichts geändert), `404`. Für die Oberfläche
vorgesehen: Seite „Stammdaten“, Bereich „Herstellerkontakte“ (darauf verweisen die Texte).

| Methode | Pfad | Anfrage | Wirkung |
|---|---|---|---|
| `GET` | `/api/kontakte` | Filter `fehlt=1` (nur ohne Adresse) bzw. `fehlt=0`, `q` (Teil von Name, Adresse, Ansprechpartner) | alle Hersteller, nach Name |
| `PUT` | `/api/kontakte/<hersteller_id>` | `{"email", "ansprechpartner"?, "sprache"?, "notiz"?, "by"}` | Kontakt setzen; fehlende Felder bleiben, leere werden geleert, leere `email` entfernt die Adresse |
| `POST` | `/api/kontakte` | `{"hersteller", "email", "ansprechpartner"?, "sprache"?, "notiz"?, "by"}` | für einen Hersteller ohne `hersteller_id` (nur aus einer Stückliste): legt ihn in den Stammdaten an (`201`); ist er bekannt, wie `PUT` (`200`) |
| `POST` | `/api/kontakte/import` | multipart: `datei` (`.xlsx` oder `.csv`), `by` | Adressen aus einer Datei übernehmen, Bericht je Zeile |
| `GET` | `/api/kontakte/export.xlsx` | – | alle Zeilen als Excel im Aufbau der Importdatei (wieder einlesbar) |

Eine Zeile (`GET`; die Antwort von `PUT` und `POST` trägt dazu `entwuerfe_aktualisiert`):

```json
{"hersteller_id": 3, "hersteller": "Festo", "normalized_name": "festo",
 "email": "ex-dokumente@festo.example", "ansprechpartner": null, "sprache": "de",
 "notiz": null, "geaendert_von": "Jan", "geaendert_am": "2026-11-13T15:02:11.123456",
 "fehlt": false, "in_stammdaten": true, "offene_entwuerfe": 0}
```

`hersteller_id` ist `null` (und `in_stammdaten` `false`) für Hersteller, die bisher nur in einer
Stückliste stehen; sie bekommen ihre Adresse über `POST /api/kontakte`. `fehlt`: keine Adresse.
`offene_entwuerfe`: ungesendete Anfragen an diesen Hersteller. `geaendert_am` in UTC.

Regeln:

- Zugeordnet wird über den normalisierten Namen (klein, ohne Leer- und Satzzeichen):
  „Pepperl + Fuchs“ = „Pepperl+Fuchs“. Nichts wird geraten.
- Eine Adresse: Text mit genau einem `@`, ohne Leerzeichen, ohne Komma oder Semikolon (keine
  Liste), höchstens 254 Zeichen.
- Wird eine Adresse gespeichert, geändert oder entfernt, ziehen die offenen Entwürfe an diesen
  Hersteller nach (`entwuerfe_aktualisiert`): ihre Felder unten, ohne `EMAIL_NUR_TEST` auch
  `to_address`, und der Hinweis der Position.

Import: Kopfzeile in den ersten 20 Zeilen mit „Hersteller“ und „E-Mail“, dazu optional
„Ansprechpartner“, „Sprache“, „Notiz“ (Groß/klein und Bindestriche egal). CSV mit `;`, `,` oder
Tabulator, UTF-8 oder Windows-1252. Leere Zellen ändern nichts, der Import leert nie. Antwort `200`:

```json
{"datei": "herstellerkontakte_beispiel.xlsx",
 "uebernommen": [{"zeile": 2, "hersteller": "Festo", "hersteller_id": 3,
                  "email": "ex-dokumente@festo.example"}],
 "unveraendert": [], "ohne_email": [{"zeile": 9, "hersteller": "Breco"}],
 "unbekannt": [{"zeile": 6, "hersteller": "Unbekannt AG", "grund": "…"}],
 "ungueltig": [{"zeile": 7, "hersteller": "Festo",
                "grund": "Der Hersteller steht schon in Zeile 2. Diese Zeile wurde nicht übernommen."}],
 "angelegt": ["Muster Sensorik GmbH"], "entwuerfe_aktualisiert": 2}
```

`unbekannt`: weder in den Stammdaten noch in einer Stückliste. `angelegt`: Hersteller, die bisher
nur in einer Stückliste standen und jetzt in den Stammdaten stehen. `400` ohne Namen, bei einer
anderen Dateiart, unlesbarer Datei, ohne Kopfzeile oder ohne Spalte „E-Mail“.

### Empfänger einer Anfrage

| Feld (Entwurf in `/api/boms/<id>/emails` und Mail der Oberfläche) | Inhalt |
|---|---|
| `empfaenger_hersteller` | gespeicherte Adresse des Herstellers oder `null` |
| `kontakt_fehlt` | `true`, wenn keine Adresse gespeichert ist |
| `versand_an` | wohin die E-Mail wirklich geht: mit `EMAIL_NUR_TEST` (Standard) `MAIL_TEST_ADDRESS`, sonst `empfaenger_hersteller`; `null`, wenn die Adresse fehlt (dann wird nicht gesendet); bei gesendeten E-Mails die Adresse, an die sie ging |

Die Oberfläche kann daraus schreiben: „Empfänger: x@… (in dieser Umgebung an das Test-Postfach)“,
wenn `versand_an` und `empfaenger_hersteller` verschieden sind. Ohne Adresse:

- der Entwurf ist markiert (`kontakt_fehlt`), die Position bekommt den Hinweis „Für den Hersteller
  … ist noch keine E-Mail-Adresse hinterlegt. Bitte tragen Sie die Adresse in den Stammdaten bei
  den Herstellerkontakten ein, dann können Sie die Anfrage senden.“ (in `status_note` und in
  `notiz` der Oberfläche, hinter einem anderen Hinweis oder dem Grund der Anfrage),
- `GET /api/vorgaenge/<id>` zählt solche Entwürfe in `kontakte_fehlen`, jeder Artikel trägt
  `kontakt_fehlt`,
- Senden (`POST /api/mails/<id>/senden`, `…/erinnern`, `POST /api/emails/<id>/send`) antwortet
  `409` mit demselben Text; nichts wird gesendet.

`EMAIL_NUR_TEST=0` schickt wirklich an die Adresse des Herstellers; in dieser Umgebung nicht
setzen. Die Sprache des Kontakts (`sprache`) wird gespeichert und gezeigt; die Anfrage bleibt
Deutsch, englische Texte der Vorlage gibt es noch nicht.

## Pflichtsprache und Hinweise in der Oberfläche

| Wo | Feld | Inhalt |
|---|---|---|
| Artikel (Liste und Detail) | `deutsch_fehlt` | `true`, wenn die EU-Konformitätserklärung geprüft vorliegt, aber nicht in der Pflichtsprache (Standard `de`), und die Position nicht erledigt ist |
| Artikel (Liste und Detail) | `kontakt_fehlt` | `true`, wenn eine ungesendete Anfrage wartet und der Hersteller keine Adresse hat |
| Artikel (Detail) | `hinweise` | Liste von Sätzen, die nichts aufhalten: fehlende freiwillige Sprachfassungen, bei Kategorie 1 und 2 der Hinweis auf die EU-Baumusterprüfbescheinigung |
| Vorgang | `deutsch_fehlt` | Zahl der Artikel mit `deutsch_fehlt` |
| Artikel (Liste und Detail), `freigabe`, Fund | `automatisch` | `true`, wenn der Agent selbst freigegeben hat (`finding.auto_approved`); beim Artikel nur, wenn alle Freigaben der Position automatisch waren |
| Vorgang | `kontakte_fehlen` | Zahl der ungesendeten Anfragen an Hersteller ohne Adresse |
| Mail | `empfaenger_hersteller`, `kontakt_fehlt`, `versand_an` | wie oben |

Der Hinweis zur Baumusterprüfbescheinigung (Entscheidung des Teams) erscheint, wenn die
geforderte Kategorie der Position (aus Zone und Kopfangabe, wie in der EX-Bauteil-Liste) 1 oder 2
ist und weder ein geprüftes Dokument noch ein Vorschlag dieser Art da ist. Er ist keine Regel und
hält nichts auf.

## Zwischenstand

`GET /api/vorgaenge/<id>/export?zwischenstand=1` und `GET /api/boms/<id>/export?zwischenstand=1`:
jederzeit abrufbar, auch mit offenen Positionen (Entscheidung des Teams: die
Kundenliste bleibt nach M12 gesperrt, „Paket raus plus Liste, was fehlt“ geht über den
Zwischenstand). Dateiname `Zwischenstand_Ex-Dokumente_<Maschine>_Vorgang-<id>.xlsx`. Blätter:

1. „Zwischenstand“: Überschrift „ZWISCHENSTAND – unvollständig, nicht zur Weitergabe an den
   Kunden“, ein Satz mit Datum und Uhrzeit (`ZEITZONE`), Maschine, Vorgang, Zahl der Positionen
   gesamt, erledigt, offen und ohne deutsche Fassung, darunter „Was noch fehlt“ mit den Spalten
   der Fehlliste.
2. „Ex-Dokumente (Zwischenstand)“: die Kundenliste mit ihren 17 Spalten, mit dem, was geprüft
   vorliegt.
3. „Vermerke“, wenn Positionen von Hand erledigt sind.

Über die Oberfläche steht ein Eintrag im Protokoll des Vorgangs. Ohne den Parameter bleibt
`export` wie bisher (`409`, solange etwas offen ist).

## Suche über die ganze Anwendung

`GET /api/suche?q=…&je=6` (`routes/globalsuche.py`, `services/globalsuche.py`), das Suchfeld oben
in der Oberfläche. Braucht eine Anmeldung, nur lesend (auch Rolle `leser`).

- Verglichen wird ohne Groß- und Kleinschreibung und ohne Leer- und Satzzeichen: „VTUG10“ findet
  „VTUG-10-VI-EX2E“. Mehrere Wörter müssen alle passen, gleich in welchem Feld. Unter zwei Zeichen
  sucht nichts (`zu_kurz: true`).
- Gruppen: `vorgaenge` (Name, Maschine, Datei), `positionen` (Bezeichnung, Hersteller,
  Hersteller-Artikelnr., PRO.FILE-Nr., „pos 10“), `bauteile` und `dokumente` der Stammdaten
  (Dokumente nach Dateiname oder Zertifikatsnummer), `hersteller` (aus `/api/kontakte`, auch die
  nur aus Stücklisten), `archiv` (Dokumente im Dokumentenarchiv nach Dateiname, Hersteller,
  Typen; die Liste ist 60 s gemerkt, ein Archiv, das nicht antwortet, wird 30 s nicht gefragt und
  die Gruppe trägt dann einen `hinweis`).
- Je Gruppe höchstens `je` Treffer (1 bis 50), die besten zuerst (Wert gleich, dann Anfang, dann
  irgendwo), dazu `anzahl` aller Treffer.

```json
{"q": "vtug10", "anzahl": 2, "zu_kurz": false, "dauer_ms": 9,
 "gruppen": [{"art": "positionen", "titel": "Positionen", "anzahl": 1, "hinweis": null,
              "treffer": [{"titel": "Position 20: Ventilinsel",
                           "kontext": "Festo · VTUG-10-VI-EX2E · PRO.FILE 99000020 · Vorgang „Abfüllanlage Test“",
                           "ziel": "#/v/1/a/2"}]}]}
```

`ziel` ist der Pfad der Oberfläche: `#/v/<id>`, `#/v/<id>/a/<id>`, `#/stammdaten/teil/<id>`,
`#/stammdaten/dokument/<id>`, `#/stammdaten/hersteller`, `#/zertifikate/dok/<id>`.

## Ansichten schreiben nicht

`GET /api/vorgaenge`, `/api/vorgaenge/<id>`, `/api/vorgaenge/<id>/mails`, `/api/artikel/<id>`,
`/api/boms`, `/api/boms/<id>`, `/api/boms/<id>/ex-liste`, `/api/boms/<id>/sprachen` und
`/api/boms/<id>/korrektur` liefern den Stand der Positionen frisch abgeleitet, speichern ihn aber
nicht mehr (vorher speicherte jeder Abruf). Felder und Werte der Antworten sind gleich geblieben.
Gespeichert wird er bei jeder Änderung (Freigabe, Stammdaten, Kontakte, Korrektur, Fund
korrigieren); `GET /api/boms/<id>/export` und `GET /api/vorgaenge/<id>/export` speichern ihn wie
bisher vor der Ausgabe.
