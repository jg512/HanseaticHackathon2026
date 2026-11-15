# Oberfläche (web)

Die Oberfläche des Teams als eigener Dienst `web`: nginx liefert `index.html` und die Module in
`seiten/` aus und reicht `/api/` und `/files/` an das Backend, `/zert/` an das Dokumentenarchiv
weiter. Die Seite spricht dadurch mit derselben Adresse
(`const API = ""`), braucht keine Backend-Adresse und kein CORS.

```bash
docker compose up --build    # dann http://localhost:8000
```

Ist Port 8000 belegt, in `.env` `WEB_PORT=8010` setzen.

## Dateien

| Datei | Was |
|---|---|
| `index.html` | die Oberfläche, eine Datei ohne Build |
| `seiten/` | Module je Funktion und `tabelle.js` ([seiten/README.md](seiten/README.md)) |
| `nginx.conf` | Auslieferung und Weiterleitung, wird als `default.conf` eingebunden |
| `static/inter-latin-variabel.woff2`, `static/LIZENZ-Inter.txt` | Schrift Inter (SIL OFL 1.1), selbst ausgeliefert, nichts wird aus dem Internet geladen |

## Herkunft und Änderungen

`index.html` entstand nach dem Entwurf aus dem Team: Seitenleiste, Karten, Tabellen und ein
eigener API-Vertrag, den der Entwurf zunächst mit einer Attrappe bediente. Gegenüber dem Entwurf
ist geändert:

- Bildzeichen: ein schlichtes Buchstabenzeichen „A“ als inline SVG neben dem Namen „ATEX Agent“,
  dasselbe als Favicon (Data-URL im `<head>`)
- Anmeldung (unten): Wer entscheidet (M3), steht in der Sitzung; das freie Namensfeld der
  Einstellungen ist weg
- Zeile bearbeiten: Bezeichnung nur lesbar (das Backend korrigiert sie nicht)
- Wer entscheidet (M3): Freigeben, Ablehnen, Korrigieren, Zertifikat einfügen, E-Mail senden,
  Erinnern und Mängelliste senden zählen für den angemeldeten Zugang; das Backend setzt den
  Namen aus der Sitzung ein. Für die Rolle „Nur lesen“ sind diese Knöpfe gesperrt
- Tabellen bearbeiten wie in Excel (`seiten/tabelle.js`, beschrieben in `seiten/README.md`):
  Vorgänge (Name), Positionen eines Vorgangs (Bezeichnung, Hersteller, Hersteller-Artikelnr.,
  PRO.FILE-Nr., Menge, Einbauort), gelesene Werte eines Funds, EX-Bauteil-Liste, Mängel,
  Stammdaten und Dokumentenarchiv. Gespeichert wird erst nach einem Bestätigungsdialog.
- Projekte: Jahreskarten als Einstieg, darunter alle Vorgänge mit Suche, Jahr, Stand, Schritt,
  Zeitraum und sortierbaren Spalten; die Filter bleiben beim Wechsel in einen Vorgang erhalten
- „Angabe fehlt“ zeigt den Grund aus `notiz` des Artikels (fehlende Artikelnummer, fehlender
  Einbauort, offene Ex-Entscheidung)
- Seitenwechsel: Antworten einer verlassenen Seite zeichnen nicht mehr, Timer enden beim Wechsel
  und pausieren bei verdecktem Tab (`seitenWache`, `seiteIntervall`, `seiteTimeout`, beschrieben
  mit Selbsttest in `seiten/README.md`)
- Ausgeblendet über die Liste `AUSGEBLENDET` in `index.html` (`erweitere` übergeht diese Namen,
  der Code der Module bleibt): EX-Bauteil-Liste samt „Listen und Eingangsprüfung“ und „Ex-Teil
  oder nicht?“, Fragen, Dokumentenarchiv, „Veraltete Zertifikate“, „So läuft es“, die Seiten
  „Sprachmodelle“ und „Sprachversionen“. Treffer der Suche oben, die dorthin zeigen würden,
  fallen weg (`zielAusgeblendet`).
- Vorgang: Die Liste der Positionen nutzt die ganze Breite. Das Protokoll öffnet der Knopf
  „Protokoll“ als Blatt von rechts (Esc, „Schließen“ oder Klick daneben schließen es). In der
  Schrittleiste trägt ein übersprungener Schritt „Angaben prüfen“ ein Warnzeichen und die Zahl
  der offenen Positionen. „Agent starten“ fragt zuerst nach den Sprachen des Vorgangs
  (`POST /api/vorgaenge/<id>/start` mit `sprachen`).
- Position: Knopf „Position bearbeiten“ (Dialog, dieselbe Korrektur wie in der Liste), kurze
  Farblegende. Jeder Fund ist zugeklappt eine Zeile (Datei, Dokumentart, Sprachen, Herkunft,
  Marke der Vorprüfung, „PDF ansehen“); ein Klick klappt Vorprüfung, Werte und Belege auf. Vor
  jedem offenen Fund steht ein Kästchen: „Fund freigeben“ und „Fund ablehnen …“ gelten für alle
  angekreuzten. Vom Agenten selbst freigegebene Funde tragen „automatisch freigegeben“.
- E-Mails: Filterleiste (Alle, Entwurf, Gesendet, Erinnert, Eskaliert).
- Seitenleiste: „Benutzer“ steht für Admins unter einer eigenen Gruppe „Admin“.
- Wer nur lesen darf, sieht keine Formulare zum Ändern (`nur-schreiben`). Was aufgeklappt ist,
  merkt sich der Browser.

## Weiterleitung

- `/api/`, `/files/` → `http://backend:5000`, Wartezeit 15 Minuten (die Suche mit Websuche dauert
  Minuten, gunicorn erlaubt ebenso lange), Uploads bis 64 MB.
- Ist das Backend nicht erreichbar, antwortet nginx mit `502` und `{"fehler": "Der ATEX-Agent
  ist gerade nicht erreichbar. …"}`, das zeigt die Seite an; ist der Zertifikatsserver weg,
  heißt es unter `/zert/` „Das Dokumentenarchiv ist gerade nicht erreichbar. …“.
- `/zert/` → `http://zertifikatsserver:5100` nur nach Anmeldung: `auth_request` fragt
  `GET /api/auth/pruefe` beim Backend (mit dem Cookie, ohne Körper, Methode im Kopf
  `X-Original-Method`). `401` und `403` (Leser will schreiben) beantwortet nginx als JSON mit
  `fehler` und `error`. Den Anzeigenamen reicht nginx URL-kodiert im Kopf `X-Anwender` weiter und
  überschreibt dabei einen gleichnamigen Kopf des Browsers. `/api/` und `/files/` prüft das
  Backend selbst.
- nginx löst den Namen `backend` bei jeder Anfrage über das Docker-DNS auf; es startet also auch,
  wenn das Backend noch nicht läuft.
- Sonst liefert nginx nur `/`, `/static/` und `/seiten/` aus; `nginx.conf` und diese Datei nicht.

## Anmeldung

- Beim Laden fragt die Seite `GET /api/auth/ich`. Ohne Anmeldung (oder bei jedem späteren `401`,
  auch aus den Modulen: `window.fetch` ist umwickelt) zeigt sie die Anmeldeseite mit Bildzeichen und
  Namen; nach der Anmeldung geht es auf derselben Seite (`#`-Pfad) weiter.
- Unten links: Symbol, Name und Rolle des Zugangs und „Abmelden“. Profilbilder gibt es nicht.
- „Einstellungen“: der eigene Zugang (Name, Anmeldename, Rolle), „Passwort ändern“, die Anzeige.
- Helfer für die Module bleiben: `anwender()` ist der Anzeigename, `sperre()` und
  `nameHinweis()` sperren Knöpfe für die Rolle „Nur lesen“ mit Hinweis, `mitName(fn)` ruft `fn`
  nur, wenn Schreiben erlaubt ist. Neu: `angemeldet()` (der Zugang), `istAdmin()`,
  `darfSchreiben()`.
- `seiten/benutzer.js`: Seite „Benutzer“ (`#/benutzer`) nur für Admins: Zugänge anlegen, Rolle
  ändern, deaktivieren und wieder aktivieren, Passwort neu setzen.
- Neu im Erweiterungs-Mechanismus für `nav`: `sichtbar()` (nur zeigen, wenn wahr) und
  `unten: true` (unter „Einstellungen“ statt unter „Daten“).

## Farben und Schrift

Die Farben stehen als Variablen in `:root` in `index.html`:

| Farbe | Wert | Variable, wofür |
|---|---|---|
| Limette | `#BBD03D` | `--limette`: Hauptknöpfe, aktive Navigation, aktueller Schritt, Avatar |
| Limette hell (Hover) | `#C5D75A` | `--limette-hover` |
| Limette Fläche | `#FAFDE7` | `--limette-hell`: gewählte Zeile, Hover, Ablagefläche |
| Grün | `#75AA3B` | `--gruen-marke`: Rahmen, erledigte Schritte, Ablage |
| Grün dunkel | `#466E1F` | `--gruen`: Text in Grün, Fokusrahmen |
| Anthrazit | `#282828` | `--dunkel`: Nebenknöpfe, Meldung unten, Bildzeichen |
| Text | `#212529` | `--text` |
| Fläche | `#F8F9FA` | `--flaeche`: Seitenhintergrund |
| Linie | `#DEE2E6` | `--linie` |
| Grün hell | `#E4EFD9` | `--gruen-mittel`: Status „freigegeben“ |

**Schrift:** Inter, selbst ausgeliefert aus `static/`. Kartenüberschriften, Tabellenköpfe und
Kennzahl-Titel stehen in Großbuchstaben.

**Kontrast (WCAG 2.1, geprüft mit der Formel der relativen Leuchtdichte):**

- Limette `#BBD03D` (1,7:1) und Grün `#75AA3B` (2,8:1) sind auf Weiß zu hell für Text; sie sind
  nur Fläche, Rahmen oder Akzent. Text in Grün nutzt `--gruen` `#466E1F`: 6,0:1 auf Weiß, 5,0:1
  auf `#E4EFD9` (Status „freigegeben“).
- Hauptknopf: `#212529` auf `#BBD03D` 9,0:1, auf Hover `#C5D75A` 9,7:1.
- Grautext `--grau` `#5F666D`: 5,8:1 auf Weiß, 5,5:1 auf `#F8F9FA` (Bootstraps `#6C757D` hätte
  auf der Fläche nur 4,45:1).
- Status: Bernstein `#8A6100` auf `#FFF4D6` 5,1:1, Rot `#B3261E` auf `#FBE9E7` 5,6:1, Blau
  `#1A5FB4` auf `#E6EFFA` 5,4:1, Orange `#A14A00` auf `#FFEEDD` 5,3:1; Anthrazit-Meldung weiß auf
  `#282828` 14,7:1.
- Offen: Der Limetten-Knopf hebt sich mit 1,7:1 nur schwach vom weißen Grund ab (WCAG 1.4.11 will
  3:1 für Bedienelemente); erkennbar ist er an seiner Beschriftung.
- Fokus: 2 px Rahmen in `--gruen` um jedes per Tastatur gewählte Element.

Die Farben der Diagramme und Balken stehen im Skript (`STATUS`, `GRUPPEN`).
