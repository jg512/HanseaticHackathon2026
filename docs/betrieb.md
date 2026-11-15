# Betrieb: Befehle und Einstellungen

Für den lokalen Betrieb mit Docker Compose. Ein Deployment auf einen Server gibt es nicht; das
Projekt lief nur lokal.

## Befehle

Alle aus dem Hauptordner des Repositorys:

```bash
docker compose up --build -d          # im Hintergrund starten
docker compose logs -f backend        # Ausgaben eines Dienstes (ebenso zertifikatsserver, web)
docker compose ps                     # was läuft
docker compose down                   # stoppen, Daten bleiben in den Volumes
docker compose down -v                # stoppen und ALLE Daten löschen: Datenbanken, Archiv, OCR
```

Im Backend (`docker compose exec backend flask --app wsgi <befehl>`):

| Befehl | Wirkung |
|---|---|
| `init-db` | legt fehlende Tabellen an und ergänzt fehlende Spalten; läuft bei jedem Start |
| `ocr` | liest Scans unter `ATEX_DOCS` per Texterkennung in den Zwischenspeicher; läuft beim Start im Hintergrund |
| `zert-abgleich` | überträgt freigegebene Dokumente, die das Dokumentenarchiv noch nicht hat |
| `angaben-nachlesen [--alle]` | liest die Angaben (Gültigkeit, Normen …) für ältere Vorschläge nach |
| `benutzer-anlegen` | legt ein Konto an der Kommandozeile an |

Im Dokumentenarchiv (`docker compose exec zertifikatsserver flask --app wsgi <befehl>`):

| Befehl | Wirkung |
|---|---|
| `init-db` | Tabellen, neue Spalten, fehlende Normausgaben; läuft bei jedem Start |
| `import-altbestand [--nur-wenn-leer]` | liest den alten Ordner ein; bekannte Dateien (gleicher SHA-256) werden übersprungen |
| `ocr [--dauerhaft]` | Texterkennung für Scans; läuft beim Start dauerhaft im Hintergrund |
| `neu-auslesen` | liest alle Dokumente mit der aktuellen Auslesung neu, von Hand gesetzte Angaben bleiben |
| `regeln-pruefen` | rechnet die Regeln für „veraltet“ neu |

## Daten

| Volume / Ordner | Inhalt |
|---|---|
| `db-data` | Datenbank des Backends |
| `zert-db-data`, `zert-ablage` | Datenbank und Dateien des Dokumentenarchivs |
| `ocr-cache` | Text der Scans aus der Texterkennung des Backends |
| `data/ex_dokumente/` | Altbestand der PDFs, nur lesend; darin `_internet/` (Funde der Websuche) und `_upload/` (hochgeladene Dateien, Zwischenspeicher des Archivs) |

Neue Spalten in den Modellen ergänzt der Start selbst, ohne `ALTER TABLE` von Hand
([datenmodell.md](datenmodell.md#neue-spalten-in-bestehenden-datenbanken)).

## Einstellungen

Gesetzt in `.env` (Vorlage `.env.example`), durchgereicht von `docker-compose.yml`, gelesen in
`backend/app/config.py`. „Compose“ ist der Standard in `docker-compose.yml`, „Code“ der Wert ohne
jede Angabe (z. B. in Tests). Einstellungen des Archivs stehen in
[../zertifikatsserver/README.md](../zertifikatsserver/README.md#einstellungen).

### Sprachmodelle

| Variable | Compose | Code | Bedeutung |
|---|---|---|---|
| `MODELLE_DATEI` | `/config/modelle.yaml` | dasselbe | Modelldatei ([../config/README.md](../config/README.md)); fehlt sie, gelten die `LLM_*` |
| `LLM_MODEL`, `LLM_API_BASE`, `LLM_API_KEY` | `openai/gpt-4o-mini`, leer, leer | dasselbe | ein Modell ohne Modelldatei; Beispielwerte, leere Adresse = die des Anbieters, leerer Schlüssel = aus der Umgebung |
| `LLM_TIMEOUT` | – | `60` | Sekunden je Anfrage |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | leer | – | Schlüssel für `api_key_env` in der Modelldatei |
| `INTERNET_LLM` | `0` | aus | `1` = Sprachmodell wählt Links bei der Websuche |
| `EMAIL_MIT_LLM` | `0` | aus | `1` = Sprachmodell schreibt Anrede und Einleitung der E-Mail |

### Suche

| Variable | Compose | Code | Bedeutung |
|---|---|---|---|
| `ATEX_DOCS` | `/data/ex_dokumente` | dasselbe | Ordner der Ex-Dokumente |
| `SEARCH_URL` | leer | leer | Adresse eines eigenen Suchdienstes für die Ähnlichkeitssuche (nicht in diesem Repository); leer = Stufe aus |
| `ZERTIFIKATSSERVER_URL` | `http://zertifikatsserver:5100` | leer | Dokumentenarchiv; leer = Suche ohne Archiv |
| `INTERNET_VERSUCHE_PRO_TEIL` | `10` | `10` | Web-Abrufe je Position, 0 = aus, höchstens 50; je Vorgang änderbar |
| `INTERNET_SUCH_URL` | SearXNG im Compose | leer | Einstieg der Websuche, `{q}` = Anfrage; leer = Startseite des Herstellers |
| `INTERNET_BROWSER` | `1` | aus | Seiten über Headless-Chromium statt einfacher Abrufe |
| `INTERNET_TIMEOUT` | – | `10` | Sekunden je Web-Abruf |
| `WEBSUCHE_URL` | DuckDuckGo | dasselbe | Suchlink, den der Mensch im eigenen Browser öffnet |
| `OCR`, `OCR_SPRACHEN`, `OCR_CACHE` | `1`, `deu+eng+fra`, `/ocr` | dasselbe | Texterkennung für Scans |
| `AUTOMATISCH_FREIGEBEN` | `1` | `1` | Nachweise aus Ordner oder Archiv mit Vorprüfung „ok“ gibt der Agent selbst frei, markiert als „automatisch freigegeben“; `0` = immer ein Mensch |
| `ABLAUF_WARNUNG_TAGE` | `180` | `180` | Vorprüfung nennt Zertifikate, die so bald ablaufen |
| `HERSTELLER_AEHNLICHKEIT` | `0.9` | `0.9` | ab dieser Ähnlichkeit gilt der Hersteller im Dokument als der der Stückliste |

### E-Mail

| Variable | Compose | Code | Bedeutung |
|---|---|---|---|
| `SMTP_HOST`, `SMTP_PORT` | `mail`, `1025` | `localhost`, `1025` | Postausgang |
| `SMTP_SICHERHEIT` | `keine` | `keine` | `keine`, `starttls` oder `ssl`; ein anderer Wert hält den Start an |
| `SMTP_BENUTZER`, `SMTP_PASSWORT` | leer | leer | beide leer = ohne Anmeldung; das Passwort nur in `.env` |
| `MAIL_FROM`, `MAIL_FROM_NAME`, `MAIL_ANTWORT_AN` | `atex-agent@example.com`, leer, leer | dasselbe | Absender, Anzeigename, Antwortadresse |
| `EMAIL_NUR_TEST` | `1` | `1` | jede E-Mail an einen Hersteller geht an `MAIL_TEST_ADDRESS` |
| `MAIL_TEST_ADDRESS` | `test@example.com` | dasselbe | Testadresse |
| `EINKAUF_ADRESSE` | leer | leer | Empfänger der Mängelliste; leer = Testadresse |
| `ERINNERUNG_NACH_STUNDEN`, `ERINNERUNGEN_MAX` | `48`, `1` | dasselbe | Frist bis zur Erinnerung, Erinnerungen vor der Eskalation |
| `OBERFLAECHE_DEMO_48H` | – | `1` | der Knopf „Erinnerung senden“ übergeht die Frist; `0` = echte Frist |

Wohin E-Mails gehen, sagt `GET /api/mail/einstellungen` und die E-Mail-Seite in einem Satz:

| Art | Einstellung | Wohin |
|---|---|---|
| Test-Postfach (Standard) | `SMTP_HOST` ist Mailpit, ohne `SMTP_BENUTZER` | alles nach Mailpit, nichts verlässt den Rechner |
| echtes Postfach, nur Testadresse | `SMTP_*` gesetzt, `EMAIL_NUR_TEST=1` | E-Mails an Hersteller gehen an `MAIL_TEST_ADDRESS` |
| wirklich an den Hersteller | `SMTP_*` gesetzt, `EMAIL_NUR_TEST=0` | an die gespeicherte Adresse; ohne Adresse wird nie gesendet |

### Sprachen

| Variable | Compose | Code | Bedeutung |
|---|---|---|---|
| `PFLICHTSPRACHE` | `de` | `de` | ohne die EU-Konformitätserklärung in dieser Sprache ist eine Position nicht vollständig |
| `SPRACHEN` | `de,en` | `de,en` | gesuchte und gezeigte Sprachen; eine fehlende ist nur ein Hinweis |

### Anmeldung

| Variable | Compose | Code | Bedeutung |
|---|---|---|---|
| `SECRET_KEY` | Entwicklungswert | leer (Entwicklungswert, Warnung) | signiert das Sitzungs-Cookie; für jeden Betrieb außer der lokalen Entwicklung setzen |
| `ADMIN_BENUTZER`, `ADMIN_NAME`, `ADMIN_PASSWORT` | Demo-Werte aus `.env.example` | – | Konto, das beim Start entsteht, wenn es den Anmeldenamen noch nicht gibt; ein vorhandenes bleibt unverändert |
| `SITZUNG_STUNDEN` | `12` | `12` | Stunden ohne Anfrage, nach denen die Anmeldung endet; jede Anfrage verlängert sie |
| `EX_LISTE` | aus | aus | `1` = unsichere Positionen warten auf die Ex-Entscheidung eines Menschen (EX-Bauteil-Liste); die Seite dazu ist in der Oberfläche ausgeblendet |
| `VERALTET_PRUEFUNG` | aus | aus | `1` = Markierungen „veraltet“ des Dokumentenarchivs fließen in Suche und Vorprüfung ein |
| `SESSION_COOKIE_SECURE` | leer | aus | `1` = Cookie nur über HTTPS |
| `LOGIN_MAX_FEHLVERSUCHE`, `LOGIN_SPERRE_MINUTEN`, `LOGIN_VERZOEGERUNG` | – | `5`, `15`, `0.5` | Sperre nach Fehlversuchen, Wartezeit je Fehlversuch |
| `LOGIN_PFLICHT` | – | `1` | `0` = ohne Anmeldung offen, nur für Tests |

### Sonstiges

| Variable | Compose | Code | Bedeutung |
|---|---|---|---|
| `WEB_PORT` | `8000` | – | Port der Oberfläche auf dem Rechner |
| `OEFFENTLICHE_ADRESSE` | `http://localhost:<WEB_PORT>` | `http://localhost:8000` | Adresse der Oberfläche im Browser; daraus die Archiv-Links („DL-Link“) der Kundenliste |
| `ZEITZONE` | `Europe/Berlin` | dasselbe | Uhrzeiten im Protokoll; gespeichert wird in UTC |
| `DATABASE_URL` | PostgreSQL `db` | `sqlite:///atex.db` | Datenbank des Backends |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Werte aus `.env.example` | – | Zugang der Datenbank `db` (nur lokale Entwicklung) |
| `OBERFLAECHE_SUCHE_SYNCHRON` | – | aus | `1` = Suche im Anfrage-Thread, für Tests |
