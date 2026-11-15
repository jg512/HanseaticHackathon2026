# Module der Oberfläche

Je Funktion eine Datei. `index.html` lädt sie nach dem Hauptskript; sie hängen sich mit
`erweitere(art, eintrag)` ein und benutzen dessen Helfer (`api`, `esc`, `$`, `toast`, `tun`,
`mitName`, `sperre`, `nameHinweis`, `anwender`, `angemeldet`, `istAdmin`, `darfSchreiben`,
`sperrgrund`, `chip`, `ICON`, `zustand`).

| Art | Eintrag | Wirkung |
|---|---|---|
| `seiten` | `{name, muster, vorgang?, zeige(m, app)}` | eigene Seite unter einem `#/…`-Pfad; mit `vorgang: true` ist `m[1]` die Vorgangs-ID und `zustand.v` geladen |
| `nav` | `{name, href, text, icon?, vorgang?, zahl?, sichtbar?, unten?}` | Eintrag in der Leiste: unter „Daten“ oder, mit `vorgang: true`, im geöffneten Vorgang (`href(v)`); `sichtbar()` blendet ihn je nach Rolle aus (z. B. `sichtbar: istAdmin`), `unten: true` stellt ihn unter „Einstellungen“ |
| `vorgang` | `{titel, schluessel?, zeichne(v, el)}` | Teil im Bereich „Listen, Prüfungen und Einstellungen“ unter der Tabelle der Vorgangsseite |
| `artikel` | `{titel, schluessel?, zeichne(a, el)}` | Teil im Bereich „Weitere Möglichkeiten“ unter den Funden der Artikelseite |

Abschnitte werden bei jedem Neuzeichnen der Seite neu aufgerufen (alle paar Sekunden, wenn sich
etwas geändert hat): geladene Daten im Modul merken und sofort zeichnen, dann im Hintergrund
auffrischen. Solange ein Eingabefeld den Fokus hat, zeichnet die Seite nicht neu.

Jeder Abschnitt ist ein zugeklappter Teil (`<details class="teil">`, Titel im Kopf, deshalb keine
eigene `<h2>`). `el` ist der Inhalt des Teils. Ist dort etwas zu tun, setzt das Modul mit
`teilMarke(el, "2 Positionen betroffen", "rot")` eine Marke auf den geschlossenen Kopf, mit
`teilKurz(el, "Nichts veraltet")` einen kurzen Stand daneben. `teilOeffnen("vorgang-<schluessel>")`
klappt einen Teil auf und scrollt hin (so springt der Knopf „Einstellungen“ zum Teil
`einstellungen`). Ob ein Teil offen ist, merkt sich der Browser.

## Dateien und Dateinamen

- Jedes Dateifeld entsteht mit `dateiwahl(attr, {knopf?, knopfId?, name?, titel?, klasse?, leise?})`:
  Knopf „Datei auswählen“, daneben der gewählte Name („Keine Datei ausgewählt“). Das echte
  `<input type="file">` liegt durchsichtig über dem Knopf (kein `display:none`), bleibt mit Tab
  erreichbar und für `set_input_files` in Tests. Setzt ein Modul `feld.files` selbst, ruft es
  danach `dateiwahlZeigen(feld)`. Kein rohes `<input type="file">` mehr in `web/`.
- `dateiLesbar(text)` zeigt Namen wie „Konformit#U00e4tserkl#U00e4rung“ als „Konformitätserklärung“.
  Nur zum Anzeigen: Links und Anfragen behalten den Pfad, wie er auf der Platte steht. Was das
  Backend als Text liefert (Fund, Protokoll, Vorprüfung), ist dort schon umgewandelt
  (`pdf_text.lesbar`).

## Ruhige Seiten

- Eine Hauptsache je Seite auf dem ersten Bildschirm, alles Seltene einen Klick entfernt:
  `<details data-merk="schlüssel">` (der Browser merkt sich den Zustand, `auf(schl)`,
  `offenAttr(schl)`), Tastatur geht von selbst (Tab, Enter, Leertaste).
- Kennzahlen als eine schmale Zeile (`.kennzeile`, `.standzahlen`), nicht als große Kacheln,
  wenn dieselben Zahlen schon als Filter darunter stehen.
- Farben der Gruppen (`GRUPPEN` in `index.html`) überall gleich: Ring, Balken, Kennzahlzeile.
- Formulare, die nur zum Ändern da sind, tragen die Klasse `nur-schreiben`: Wer nur lesen darf
  (`body.leser`), sieht sie nicht. Knöpfe mitten im Text bleiben mit `sperre()` gesperrt.

## Seitenwechsel: nur die aktuelle Seite zeichnet

Wechselt jemand die Seite, während eine Anfrage der alten Seite noch läuft, darf deren Antwort
nichts mehr zeichnen (sonst stünde die alte Seite unter der neuen Adresse). Dafür gibt es in
`index.html` einen Zähler, den `route()` bei jedem Wechsel hochzählt, und drei Helfer:

| Helfer | Wirkung |
|---|---|
| `seitenWache()` | merkt sich den Zähler und gibt eine Funktion zurück, die `true` sagt, solange dieselbe Seite offen ist |
| `seiteIntervall(fn, ms, wache?)` | Abfrage im Takt; endet beim Verlassen der Seite. Ein Takt fällt aus, solange der vorige noch läuft oder der Browser-Tab verdeckt ist; wird der Tab wieder sichtbar, fragt sie sofort einmal |
| `seiteTimeout(fn, ms, wache?)` | einmal verzögert (z. B. beim Tippen in einem Suchfeld); endet beim Verlassen der Seite |

Regeln für jedes Modul:
- `zeige(m, app, wache)` bekommt die Wache der Seite als drittes Argument. Nach jedem `await`
  zuerst `if (!wache()) return;`, dann zeichnen.
- Ladefunktionen, die auch ein Takt oder ein Knopf aufruft, holen sich am Anfang ihre eigene
  `seitenWache()` und prüfen sie nach dem `await`.
- Nie `setInterval` oder `setTimeout` für eine Seite direkt, immer über die beiden Helfer, mit
  der Wache aus `zeige` (sonst meldet sich nach einem schnellen Wechsel ein Timer bei der neuen
  Seite an). Die Tabellen (`tabelle.js`) rufen `nachher` nur, wenn die Seite noch dieselbe ist.

### Selbsttest mit langsamem Netz

So lässt sich nachprüfen, dass beim schnellen Durchklicken immer die richtige Seite steht:

1. Anmelden, einen Vorgang mit gesuchter Stückliste öffnen (im Beispiel Vorgang 1 und Position 5).
2. In der Konsole des Browsers jede Anfrage um 3 bis 8 Sekunden verzögern:
   ```js
   window._echtFetch = window._echtFetch || window.fetch;
   window.fetch = (...a) => new Promise(z => setTimeout(z, 3000 + Math.random() * 5000)).then(() => window._echtFetch(...a));
   ```
3. Schnell (unter einer Sekunde je Klick) durch die Seiten gehen, besonders: Vorgang → Einstellungen,
   Vorgang → Stammdaten, Vorgang → Dokumentenarchiv, E-Mails → Fragen, Position → Dokumentenarchiv,
   Position → Vorgang, Übersicht → Projekte → Vorgang, EX-Bauteil-Liste → Sprachversionen →
   Korrektur, Vorgang 1 → Vorgang 2.
4. Nach jedem Durchgang 20 Sekunden zusehen: Die Überschrift muss immer zur Adresse passen, auch
   wenn die späten Antworten eintreffen. `seitenTimer.size` ist danach höchstens 1 (der Takt der
   offenen Seite).
5. `window.fetch = window._echtFetch` stellt das Netz zurück.

Automatisch (nach Schritt 2 in die Konsole kopieren; Ergebnis nach einigen Minuten in
`navTest`, `fehler` muss 0 sein). Die Wege setzen Vorgang 1 mit Position 5 voraus; die Überschrift
bei `#/v/1` auf den Namen des eigenen Vorgangs ändern:

```js
(async () => {
  const soll = { "#/": "Übersicht", "#/projekte": "Projekte", "#/v/1": "Testanlage TM-1", "#/mails/1": "E-Mails an Hersteller",
    "#/v/1/fragen": "Fragen", "#/v/1/ex-liste": "EX-Bauteil-Liste", "#/v/1/sprachen": "Sprachversionen", "#/v/1/korrektur": "Korrektur",
    "#/stammdaten": "Stammdaten", "#/zertifikate": "Dokumentenarchiv", "#/sprachmodelle": "Sprachmodelle", "#/einstellungen": "Einstellungen" };
  const passt = () => { const h = document.querySelector("#app h1")?.textContent.trim() || "", s = soll[location.hash];
    return !h || (location.hash.includes("/a/") ? h.startsWith("Position ") : !s || h === s); };
  const wege = [["#/v/1", "#/einstellungen"], ["#/v/1", "#/stammdaten"], ["#/v/1", "#/zertifikate"], ["#/mails/1", "#/v/1/fragen"],
    ["#/v/1/a/5", "#/zertifikate"], ["#/v/1/a/5", "#/v/1"], ["#/", "#/projekte", "#/v/1"], ["#/v/1/ex-liste", "#/v/1/sprachen", "#/v/1/korrektur"]];
  window.navTest = { fehler: 0, fertig: false };
  for (const weg of wege) {
    for (const z of weg) { location.hash = z; await new Promise(r => setTimeout(r, 700)); }
    for (let i = 0; i < 60; i++) { await new Promise(r => setTimeout(r, 300)); if (!passt()) navTest.fehler++; }
  }
  navTest.fertig = true;
})();
```

## Anmeldung und Rechte

Wer angemeldet ist, steht in der Sitzung des Servers; `index.html` holt es von `/api/auth/ich`.

| Helfer | Wirkung |
|---|---|
| `angemeldet()` | `{id, login, name, rolle, rolle_text}` oder `null` |
| `anwender()` | Anzeigename, sonst `""`; nur zum Anzeigen, nicht zum Prüfen von Rechten |
| `istAdmin()` | Rolle `admin` (Benutzer, Sprachmodelle testen, Test-E-Mail) |
| `darfSchreiben()` | angemeldet und nicht Rolle `leser` |
| `sperre()` | `disabled title="…"` für schreibende Knöpfe, leer, wenn `darfSchreiben()` |
| `nameHinweis()`, `sperrgrund()` | der Satz dazu für Leser oder ohne Anmeldung |
| `mitName(fn)` | ruft `fn(name)` nur, wenn `darfSchreiben()`, sonst ein Hinweis |

Schreibende Knöpfe nie über `anwender()` sperren: Auch ein Leser hat einen Namen. Der Server
prüft die Rolle ohnehin (403); die Oberfläche soll den Knopf nur gar nicht erst anbieten.

Vorgang = `bom`, Artikel = `bom_item`: die IDs sind dieselben wie in `/api/boms/…` (docs/api.md).
`api()` zeigt Fehler aus `{"fehler": …}` und `{"error": …}`. Der Zertifikatsserver liegt unter
`/zert/api/…`.

## Tabellen zum Bearbeiten (`tabelle.js`)

`tabelle.js` liegt hier, weil nginx nur `/`, `/static/` und `/seiten/` ausliefert. `index.html`
lädt es vor dem Hauptskript: Der Wächter für ungespeicherte Änderungen muss vor dem Router am
`hashchange` hängen. Jede Tabelle, die man ändern kann, zeichnet die Seite mit
`Tabelle.html(def)` (Felder der Definition im Kopf von `tabelle.js`):

- Zelle anklicken und tippen; F2, Enter oder Doppelklick bearbeitet; Enter, Tab, Umschalt+Tab
  und Pfeile bewegen; Esc stellt zurück; Entf leert; Strg+Z nimmt zurück; Strg+C/Strg+V
  kopieren und fügen Blöcke ein (tab-getrennt wie aus Excel); die Leertaste schaltet Haken.
- Nichts geht sofort an den Server. Geänderte Zellen sind markiert, die Leiste über der Tabelle
  zählt sie. „Prüfen und speichern“ zeigt jede Änderung (bisher, neu), die Folgen (`folgen`) und
  ein Notizfeld (`notiz`); erst „Ja, speichern“ ruft `speichern` je Zeile (oder einmal
  `speichernAlle`) über `mitName`. Zeilen mit Fehler behalten ihre Werte und zeigen den Text des
  Servers; gibt `speichern` `{hinweis}` zurück, steht der Hinweis im Ergebnis.
- Der Zustand liegt in `tabelle.js` je Tabelle, Zeile und Spalte und übersteht das Neuzeichnen
  der Seite. Hat sich der Wert auf dem Server inzwischen geändert, zeigt der Dialog den Konflikt.
- Gesperrte Zellen haben eine graue Ecke und einen Tooltip mit dem Grund (`gesperrt`); wer nicht
  schreiben darf (`darfSchreiben()`, z. B. die Rolle „Nur lesen“), kann keine Zelle ändern. Wer die Seite mit offenen Änderungen verlässt oder neu lädt,
  wird vorher gefragt.
- Abgeleitete Tabellen (Protokolle, Sprachversionen, Versionen) bleiben einfache Tabellen mit
  dem Hinweis 🔒, dass sie sich nicht ändern lassen.
- Texte für Anwender: ganze Sätze mit „Sie“, ohne Technikwörter.
