# Abläufe im Detail

Erst die Bilder: der Fachprozess mit seinen Bahnen und die drei Abläufe, die das Ganze tragen.
Danach steht, was die Bilder weglassen: welche Datei was tut, welche Tabelle sich ändert, was bei
Fehlern passiert. Die Oberfläche spricht den Vertrag des Oberflächen-Entwurfs aus dem Team (`/api/vorgaenge`, `/api/artikel`,
`/api/mails`, umgesetzt in `backend/app/routes/oberflaeche.py` und
`services/oberflaeche.py`); dieselben Schritte gibt es auch als „klassische“ API unter
`/api/boms/…` ([api.md](api.md)).

## Prozess

Die Farben sind die Bahnen: **grün = Mensch** (Sachbearbeitung, Prüfung und Freigabe),
**blau = Agent** (Code; ein Sprachmodell nur als Vorschlag), **orange = Hersteller**,
**grau = Einkauf**. Die Ex-Frage und die Mängelliste stehen im Bild, sind in der Oberfläche aber
im Standard ausgeblendet (`EX_LISTE`).

```mermaid
flowchart TB
        m1(["Stückliste als Excel hochladen"])
        m2["Ex-Frage entscheiden"]
        m3["Mängelliste senden,<br>Korrektur eintragen"]
        m4{"Fund freigeben?"}
        m5["E-Mail prüfen und senden"]
        m6["Antwort des Herstellers<br>oder Zertifikat einfügen"]
        m7["Erinnern, danach eskalieren"]
        m8(["Kundenliste als Excel ausgeben"])
        a1["Eingangsschema prüfen,<br>EX-Bauteil-Liste vorschlagen"]
        a2{"Angaben vollständig,<br>Ex-Teil klar?"}
        a3["Mängelliste entwerfen"]
        a4{"In der Datenbank<br>geprüft vorhanden?"}
        a5["Dokumentenarchiv<br>(ohne Archiv: Ordner des Herstellers)"]
        a6["Ähnlichkeitssuche<br>(optional)"]
        a7["Websuche auf der<br>Herstellerseite"]
        a8["Vorprüfung: Typ, Zone, Belege,<br>Angaben, Sprachen, veraltet"]
        a9["E-Mail-Entwurf an den Hersteller"]
        a10["Stammdaten schreiben,<br>neue Version im Archiv"]
        a11{"Alle Positionen<br>vollständig?"}
        h1["Hersteller: antwortet mit<br>Dokument oder gar nicht"]
        e1["Einkauf: liefert<br>fehlende Angaben"]
        e2["Einkauf: übernimmt die<br>eskalierte Anfrage"]
    m1 --> a1 --> a2
    a2 -->|"Angabe fehlt"| a3 --> m3
    m3 --> e1 --> m3
    m3 -->|"korrigiert"| a2
    a2 -->|"Ex-Frage offen"| m2
    m2 -->|"Ex-Teil"| a2
    m2 -->|"kein Ex-Teil"| a11
    a2 -->|"ja"| a4
    a4 -->|"ja"| a11
    a4 -->|"nein"| a5
    a5 -->|"Typ gefunden"| a8
    a5 -->|"nichts"| a6
    a6 -->|"Typ gefunden"| a8
    a6 -->|"nichts, das zählt"| a7
    a7 -->|"PDF gefunden"| a8
    a7 -->|"nichts"| a9
    a8 --> m4
    m4 -->|"ja"| a10 --> a11
    m4 -->|"nein"| a9
    a9 --> m5 --> h1
    h1 -->|"Dokument"| m6 --> a8
    h1 -->|"keine Antwort nach 48 Stunden"| m7
    m7 -->|"Erinnerung"| h1
    m7 -->|"nach einer Erinnerung"| e2
    a11 -->|"ja"| m8
    classDef mensch fill:#E4EFD9,stroke:#466E1F,color:#212529
    classDef agent fill:#E6EFFA,stroke:#1A5FB4,color:#212529
    classDef hersteller fill:#FFEEDD,stroke:#A14A00,color:#212529
    classDef einkauf fill:#F1F1F1,stroke:#5F666D,color:#212529
    class m1,m2,m3,m4,m5,m6,m7,m8 mensch
    class a1,a2,a3,a4,a5,a6,a7,a8,a9,a10,a11 agent
    class h1 hersteller
    class e1,e2 einkauf
```

## Die drei Abläufe als Sequenz

**(a) Hochladen und Suche je Position**

```mermaid
sequenceDiagram
    autonumber
    actor P as Sachbearbeiterin
    participant W as web
    participant B as backend
    participant D as db
    participant Z as Dokumentenarchiv
    participant S as Suchdienst (optional)
    participant I as SearXNG und Herstellerseite
    participant L as Sprachmodell (optional)
    P->>W: Stückliste hochladen
    W->>B: POST /api/vorgaenge
    B->>D: bom, bom_item, bom_issue
    P->>W: Agent starten
    W->>B: POST /api/vorgaenge/{id}/start
    B-->>W: Lauf startet im Hintergrund
    loop je Position, außer Angabe fehlt, kein Ex-Teil, vorhanden, erledigt
        B->>D: Pflichtdokument schon geprüft?
        alt ja
            Note over B: Stand „aus der Datenbank“, keine Suche
        else nein
            B->>Z: aktuelle PDFs des Herstellers holen
            Note over B: Typ im Text? (ohne Archiv: Ordner des Herstellers)
            B->>Z: Markierung „veraltet“ je SHA-256 (nur mit VERALTET_PRUEFUNG=1)
            opt kein Fund, der zählt, kein Archiv, SEARCH_URL gesetzt
                B->>S: POST /api/search
                S-->>B: Textstellen mit Datei und Seite
            end
            opt noch immer nichts, Hersteller bekannt, Versuche über 0
                B->>I: Suche, Seiten, PDFs (höchstens N Abrufe)
                B->>L: passenden Link wählen (nur mit INTERNET_LLM=1)
            end
            Note over B: Vorprüfung jedes Kandidaten
            alt Fund
                B->>D: finding, Stand „Fund prüfen“
            else nichts
                B->>L: Einleitung der E-Mail (nur mit EMAIL_MIT_LLM=1)
                B->>D: email_draft, Stand „E-Mail-Entwurf“
            end
        end
        B->>D: bom_event, ein Satz je Position
    end
    W->>B: GET /api/vorgaenge/{id} und ereignisse, alle paar Sekunden
```

**(b) Prüfen, freigeben oder nachfordern**

```mermaid
sequenceDiagram
    autonumber
    actor P as Prüferin
    participant W as web
    participant B as backend
    participant D as db
    participant Z as Dokumentenarchiv
    participant M as Postausgang
    P->>W: Position öffnen
    W->>B: GET /api/artikel/{id}
    B-->>W: Funde mit Vorprüfung, Belegen, Herkunft
    alt Gewählten Fund freigeben
        W->>B: POST /api/artikel/{id}/freigeben
        B->>D: Hersteller, Bauteil, Dokument, Sprachen, Zuordnung „geprueft“ mit Name
        B->>D: Stand aller Positionen mit derselben PRO.FILE-Nr.
        B->>Z: Teil anlegen, falls neu, Datei als neue Version
        Note over B,Z: scheitert das, gilt die Freigabe trotzdem
    else Fund ablehnen
        W->>B: POST /api/artikel/{id}/ablehnen
        B->>D: finding abgelehnt, ohne weiteren Fund ein email_draft
        P->>W: Entwurf prüfen, E-Mail freigeben und senden
        W->>B: POST /api/mails/{id}/senden
        B->>M: SMTP, im Standard an MAIL_TEST_ADDRESS
        Note over B: Stand „angefordert“
        alt Hersteller schickt ein Dokument
            P->>W: Antwort einfügen
            W->>B: POST /api/artikel/{id}/zertifikat
            Note over B: neuer Fund, wieder Vorprüfung und Freigabe
        else keine Antwort
            P->>W: Erinnerung senden
            W->>B: POST /api/mails/{id}/erinnern
            B->>M: Erinnerung
            Note over B: Stand „erinnert“
            P->>W: noch einmal
            W->>B: POST /api/mails/{id}/erinnern
            Note over B: Stand „eskaliert“, ohne E-Mail, der Einkauf arbeitet mit der Fehlliste
        end
    end
```

**(c) Anmeldung, und wie der Name jede Änderung erreicht**

```mermaid
sequenceDiagram
    autonumber
    actor P as Benutzerin
    participant W as web (nginx)
    participant B as backend
    participant D as db
    participant Z as Dokumentenarchiv
    P->>W: Anmeldename und Passwort
    W->>B: POST /api/auth/login
    B->>D: Konto lesen, Hash prüfen, Fehlversuche zählen
    B-->>W: Cookie atex_sitzung (signiert, HttpOnly, SameSite=Lax)
    P->>W: Fund freigeben
    W->>B: POST /api/artikel/{id}/freigeben mit Cookie
    B->>D: Konto aktiv, Sitzungsstand gültig, Rolle darf schreiben?
    Note over B: by, von und X-Anwender werden durch den Namen aus der Sitzung ersetzt
    B->>D: decided_by = Anzeigename
    P->>W: Dokument im Archiv ändern
    W->>B: auth_request GET /api/auth/pruefe mit Cookie und Methode
    B-->>W: 204 mit X-Anwender-Name, sonst 401 oder 403
    W->>Z: PATCH /api/dokumente/{id} mit Kopf X-Anwender
    Note over Z: Name kommt aus dem Kopf, nicht aus dem Körper
```

**Stand einer Position**, so wie die Oberfläche ihn nennt (Ableitung in
`backend/app/services/status.py`, Übersetzung in `services/oberflaeche.py`):

```mermaid
stateDiagram-v2
    state "wartet" as wartet
    state "Angabe fehlt" as info_fehlt
    state "kein Ex-Teil" as kein_ex
    state "wird gesucht" as suche
    state "aus der Datenbank" as in_db
    state "Fund prüfen" as zu_pruefen
    state "freigegeben" as geprueft
    state "E-Mail-Entwurf" as mail_entwurf
    state "angefordert" as angefordert
    state "erinnert" as erinnert
    state "eskaliert" as eskaliert
    [*] --> wartet: eingelesen
    [*] --> info_fehlt: Nummer fehlt oder Ex-Frage offen
    info_fehlt --> wartet: korrigiert oder als Ex-Teil bestätigt
    info_fehlt --> kein_ex: Mensch entscheidet
    wartet --> kein_ex: Mensch entscheidet
    wartet --> in_db: schon geprüft in der Datenbank
    wartet --> suche: Agent starten
    suche --> zu_pruefen: Fund
    suche --> mail_entwurf: nichts gefunden
    zu_pruefen --> geprueft: freigegeben, Pflicht erfüllt
    zu_pruefen --> mail_entwurf: abgelehnt, kein Fund übrig
    mail_entwurf --> angefordert: gesendet
    angefordert --> erinnert: Erinnerung gesendet
    erinnert --> eskaliert: an den Einkauf
    angefordert --> zu_pruefen: Antwort eingefügt
    erinnert --> zu_pruefen: Antwort eingefügt
    eskaliert --> zu_pruefen: Antwort eingefügt
    geprueft --> [*]
    in_db --> [*]
    kein_ex --> [*]
    note right of geprueft
        Auch „von Hand erledigt“ mit
        Begründung steht als freigegeben da.
    end note
```


## (a) Hochladen und Suche je Position

**Hochladen** (`POST /api/vorgaenge`, Feld `datei`, optional `name`):
`services/excel_import.py` liest die Kopfzeilen (Maschine, Ex-Bereich, geforderte Kategorie)
und die Positionen. Ohne Kopfzeile, ohne Pflichtspalte oder ohne Positionen wird die Datei ganz
abgelehnt und nichts gespeichert. Sonst entstehen `bom`, je Zeile ein `bom_item` und je
Auffälligkeit ein `bom_issue` (Codes in [../backend/README.md](../backend/README.md)). Der Name
des Vorgangs wird eindeutig gemacht („ (2)“ bei Gleichstand).

**EX-Bauteil-Liste:** `services/ex_liste.py` ordnet jede Position nach Einbauort und
Ex-Hinweisen ein und rechnet das bei jedem Abruf neu. Ist die Regel unsicher, steht die Position
auf `klaerung` (Oberfläche: „Angabe fehlt“) und wird nicht gesucht, bis ein Mensch sie als
Ex-Teil bestätigt oder „kein Ex-Teil“ entscheidet.

**Lauf** (`POST /api/vorgaenge/<id>/start`): Der Lauf läuft in einem Hintergrund-Thread des
einen gunicorn-Prozesses; `409`, solange schon einer läuft. `bom.search_started_at` markiert
den offenen Lauf. Startet das Backend mitten im Lauf neu, setzt die erste Anfrage danach ihn für
die noch nicht gesuchten Positionen fort (Eintrag im Protokoll). Am Anfang setzt
`modelle.fuer_lauf(bom)` das Sprachmodell und die Zahl der Web-Abrufe dieses Vorgangs.

Je Position (`services/suche.py`, Reihenfolge im Kopf der Datei):

1. **Datenbank:** `status.aktualisiere` erkennt, ob die EU-Konformitätserklärung für diese
   PRO.FILE-Nr. geprüft und in der Pflichtsprache vorliegt. Dann `vorhanden`, keine Suche.
   Übersprungen werden auch `klaerung`, `kein_ex_teil` und `erledigt`.
2. **Dokumentenarchiv:** Mit `ZERTIFIKATSSERVER_URL` liefert `ordner_fuer` nur einen
   Zwischenspeicher mit allen aktuellen PDFs des Herstellers im Archiv
   (`zertifikatsserver.auszug`, unter `_upload/zertifikatsserver/<Hersteller>/`; Dateien, die
   schon unter `ATEX_DOCS` liegen, werden kopiert statt geladen). Der Herstellerordner wird dann
   nicht durchsucht, und die Ähnlichkeitssuche fällt weg: nach dem Archiv kommt gleich das
   Internet. Antwortet das Archiv nicht, sucht der Agent nur in der Datenbank und im Internet.
   Ohne Archiv steht der Herstellerordner unter `ATEX_DOCS` für das Archiv (Entwicklung, Tests).
   Gesucht wird die Typbezeichnung im PDF-Text und im Dateinamen. Für jeden Treffer fragt
   `zertifikatsserver.pruefe` das Archiv über den SHA-256 nach einer Markierung „veraltet“:
   ein Verdacht steht in der Vorprüfung. Ein Nachweis (Konformitätserklärung,
   Baumusterprüfbescheinigung) mit Vorprüfung „ok“ ist danach automatisch freigegeben
   (`freigabe.automatisch`, Schalter `AUTOMATISCH_FREIGEBEN`).
3. **Ähnlichkeitssuche** (optional, nur mit `SEARCH_URL` und ohne Archiv, nur wenn Schritt 2
   nichts hat, das zählt; der Suchdienst gehört nicht zu diesem Repository):
   `services/rag.py` fragt den Dienst mit Hersteller, Typ und Bezeichnung. Die Treffer
   sind nur Kandidaten; jede Datei geht durch dieselbe Prüfung wie in Schritt 2. Ohne
   Typ-Treffer höchstens zwei Vorschläge, immer „unklar“, nur aus den Ordnern der Position.
   Baut der Dienst noch oder antwortet nicht, geht es weiter.
4. **Websuche** (nur wenn bis hier nichts zählt, nur für die fünf Hersteller in
   `internet.HERSTELLER`, höchstens `INTERNET_VERSUCHE_PRO_TEIL` Abrufe): Einstieg über SearXNG
   mit `site:<domain> <Typ> ATEX`, Seiten über Headless-Chromium, jede Seite nur als Markdown
   ausgewertet. Mit `INTERNET_LLM=1` wählt das Sprachmodell aus höchstens 15 Links; übernommen
   wird nur eine URL, die wörtlich in der Liste stand. PDFs landen in `ATEX_DOCS/_internet/` und
   gehen durch dieselbe Prüfung. robots.txt wird beachtet; sperrt ein Host, wird er im Lauf nicht
   mehr gefragt. Links, die das Sprachmodell nicht wählt, bleiben als Reserve: Bringt seine
   Auswahl keinen Fund, geht die Suche dort weiter (`internet.RESERVE`). Ein Datenblatt ist kein
   Fund, auch wenn es den Typ nennt (`suche.dokumentart`: Dateiname oder Kopf sagt Datenblatt;
   die Mehrzahl „Konformitätserklärungen“ in einer Zulassungsliste zählt nicht als Erklärung).
   Ein Typmuster mit kurzem Anfang wie „NJ…-N“ trifft Bezeichnungen, die so beginnen und den
   Schluss als eigenes Glied haben („NJ0,8-5GM-N“, `pruefung._muster_mit_schluss`).
5. **Sprachversionen:** Fehlt einem Fund die Pflichtsprache, sucht ein zweiter Durchgang im
   Herstellerordner die anderen Sprachfassungen (`services/sprachversionen.py`).
6. **Sprachen des Vorgangs:** Wer den Agenten startet, wählt die Sprachen (`Bom.sprachen`). Funde,
   die nur in anderen Sprachen vorliegen, werden nicht vorgeschlagen (`suche._nur_gewaehlte`);
   Funde ohne erkennbare Sprache (Scans) bleiben. Ist die Pflichtsprache nicht gewählt, gilt die
   erste gewählte.
7. **Ältere Fassungen:** Hat die Position mehrere Konformitätserklärungen, fällt jede weg, zu
   der eine neuere vorliegt (`suche.aeltere_fassungen`): gleicher Typ im Dokument und späteres
   Datum, oder nur 94/9/EG neben einer Erklärung nach 2014/34/EU; jeweils nur, wenn die neuere
   alle Sprachen der älteren enthält. Das Datum ist das Ausstellungsdatum, sonst der Stand aus
   dem Seitenkopf (`angaben.kopfdatum`). „Gesucht in“ nennt, wie viele weggelassen wurden.
8. **Automatische Freigabe:** siehe Schritt 2.

**Vorprüfung** jedes Kandidaten: `services/pruefung.py` (Typ genau im Text? Kategorie gegen
Zone? Ergebnis `ok`, `warnung` oder `unklar` mit Grund), `services/belege.py` (Typ,
Kennzeichnung, Zertifikatsnummer, Kategorie je mit Seite, Tabelle, Zeile oder „unbelegt“),
`services/angaben.py` (Gültigkeit, Ausstellung, Richtlinien, Normen, benannte Stelle … je mit
Seite; nur Regeln, kein Sprachmodell). Das Ergebnis wird ein `finding` mit Status
`vorgeschlagen`.

**Ende je Position:** Gibt es einen Vorschlag, steht sie auf `vorschlag` („Fund prüfen“). Sonst
`nicht_gefunden`, und `email_entwurf.lege_entwuerfe_an` legt einen Entwurf an; mit
`EMAIL_MIT_LLM=1` schreibt das Sprachmodell Anrede und Einleitung aus Hersteller, Typ,
Bezeichnung und Einbauort, die Fakten setzt der Code ein, ein unbrauchbarer Text fällt auf die
feste Vorlage zurück. `searched_in` sagt in ganzen Sätzen, wo gesucht wurde; `bom_event` hält
eine Zeile je Position fest.

**Fehler:** Scheitert eine Position, steht das im Protokoll, und der Lauf geht weiter. Fehlt der
Ordner `ATEX_DOCS` ganz, startet der Lauf nicht (`503`). Archiv, Suchdienst oder Sprachmodell
nicht erreichbar: Der Lauf arbeitet ohne diese Stufe weiter und sagt es in „gesucht in“.

## (b) Prüfen, freigeben oder nachfordern

**Ansehen:** `GET /api/artikel/<id>` liefert die Funde mit Vorprüfung, Belegen, Angaben und
Herkunft (`quellennachweis`). Das PDF kommt über `/files/<pfad>?markiert=<fund>`, mit gelb
markierter Fundstelle, grün belegten und orange unbelegten Angaben.

**Freigeben** (`POST /api/artikel/<id>/freigeben`, `fund_id`, optional `doc_type`):
`freigabe.entscheide` legt Hersteller, Bauteil, Dokument, Sprachen und die Zuordnung
`part_document` mit Status `geprueft`, Name und Zeit an; aus den Angaben geht nur
`gueltig_bis` nach `document.valid_until`. Danach leitet es den Stand aller Positionen mit
derselben PRO.FILE-Nr. neu ab, auch in anderen Vorgängen. `zertifikatsserver.nach_freigabe`
lädt die Datei als neue Version ins Archiv (`quelle=atex_agent`, Quellennachweis in
`quelle_*`), wenn das Archiv sie noch nicht kennt. Scheitert das, gilt die Freigabe trotzdem;
das Protokoll sagt es, nachgeholt wird mit `flask --app wsgi zert-abgleich` oder bei der nächsten
Freigabe. Ist danach immer noch kein Pflichtdokument da (z. B. nur eine Anleitung freigegeben),
entsteht ein E-Mail-Entwurf.

**Ablehnen** (`POST /api/artikel/<id>/ablehnen`, `grund`, optional `fund_id`): Der Fund wird
`abgelehnt` und bei der nächsten Suche nicht wieder vorgeschlagen. Bleibt kein Vorschlag übrig,
entsteht der Entwurf; der Grund steht darin als Zeile „Grund der Anfrage: …“. Ist schon eine
Anfrage gesendet, bleibt die Position `angefordert`.

**Senden** (`PUT /api/mails/<id>` zum Ändern, `POST /api/mails/<id>/senden`): Senden ist
Freigabe und Versand in einem Klick. Der Empfänger kommt aus den Herstellerkontakten
(`hersteller_kontakt`); ohne Adresse `409`. Mit `EMAIL_NUR_TEST=1` (Standard) geht die E-Mail an
`MAIL_TEST_ADDRESS`, der Entwurf nennt die Adresse des Herstellers nur. `services/versand.py`
schickt über SMTP; Fehler werden ein Satz für die Oberfläche, der Grund steht im Log.

**Antwort des Herstellers** (`POST /api/artikel/<id>/zertifikat`, Herkunft `hersteller`): Das PDF
wird ein Vorschlag mit Vorprüfung, wie ein Fund. Mit Herkunft `manuell` und einer Dokumentart ist
es sofort freigegeben (Zertifikat von Hand einfügen, M18).

**Erinnern und eskalieren** (`POST /api/mails/<id>/erinnern`): `services/erinnerung.py` sagt, was
fällig ist. Der erste Druck legt eine Erinnerung an und sendet sie, nach `ERINNERUNGEN_MAX`
Erinnerungen (Standard 1) eskaliert der nächste Druck an den Einkauf: Die Position steht dann auf
`eskaliert`, eine E-Mail geht dabei nicht hinaus, der Einkauf arbeitet mit der Fehlliste. Die
Frist `ERINNERUNG_NACH_STUNDEN` (48) übergeht der Knopf im Standard
(`OBERFLAECHE_DEMO_48H=1`, für die Demo); mit `0` antwortet er vor Ablauf mit `409`.

**Von Hand erledigen:** Ein Mensch kann eine Position mit Begründung erledigen
(`POST /api/boms/<id>/items/<item_id>/resolve`); sie hält die Ausgabe dann nicht mehr auf und
bleibt in Kunden- und Fehlliste sichtbar.

**Ausgabe:** `GET /api/vorgaenge/<id>/export` gibt die Kundenliste erst, wenn jede Position auf
`vorhanden`, `kein_ex_teil` oder `erledigt` steht (sonst `409`); `?zwischenstand=1` jederzeit,
deutlich als Zwischenstand markiert. Die Fehlliste: `GET /api/boms/<id>/missing.xlsx`.

## (c) Anmeldung und der Name bei jeder Änderung

- **Anmelden** (`POST /api/auth/login`): Konto in `benutzer`, Passwort-Hash scrypt. Falsches
  Passwort, unbekanntes oder deaktiviertes Konto geben denselben Text (`401`); jeder Fehlversuch
  wartet 0,5 s; nach 5 Fehlversuchen ist das Konto 15 Minuten gesperrt (`429`).
- **Sitzung:** signiertes Cookie `atex_sitzung` (HttpOnly, SameSite=Lax, 8 Stunden ab der
  Anmeldung). Im Cookie und am Konto steht `sitzungs_stand`; ein neues Passwort oder eine
  Deaktivierung erhöht ihn, ältere Cookies gelten dann nicht mehr. Das Backend lädt das Konto
  bei jeder Anfrage.
- **Prüfung an einer Stelle:** `before_request` in `backend/app/auth.py`. Ohne Sitzung `401`
  (außer `/health`, Anmelden, Abmelden), Rolle `leser` bei allem außer `GET`, `HEAD`, `OPTIONS`
  `403`.
- **Der Name:** Die Request-Klasse `AnfrageMitAnwender` ersetzt bei schreibenden Anfragen `by`
  und `von` in JSON und Formular sowie den Kopf `X-Anwender` durch den Anzeigenamen aus der
  Sitzung. So schreibt jede Route den richtigen Namen, auch eine, die später dazukommt.
- **Archiv:** nginx fragt vor jedem `/zert/` per `auth_request` `GET /api/auth/pruefe` (mit
  Cookie und der Methode im Kopf `X-Original-Method`). `204` bringt den Namen im Kopf
  `X-Anwender-Name`; nginx reicht ihn URL-kodiert als `X-Anwender` weiter und überschreibt einen
  gleichnamigen Kopf des Browsers. Das Archiv nimmt den Namen aus diesem Kopf, nicht aus `von`.
  Sein Port 5100 ist deshalb nur an `127.0.0.1` gebunden. Ruft das Backend das Archiv selbst
  (Freigabe), schickt es den Namen des Freigebenden als `von`.
- **Oberfläche:** Bei `401`, auch aus den Modulen, zeigt die Seite die Anmeldung und kehrt
  danach auf dieselbe Seite zurück. Für Leser blendet sie Formulare zum Ändern aus.

## Stand einer Position

`bom_item.status` setzt nur `services/status.py`; die Oberfläche übersetzt ihn
(`services/oberflaeche.py`, `STATUS`):

| Gespeichert | Oberfläche | Bedeutung |
|---|---|---|
| `offen` | wartet | noch nicht gesucht |
| – | wird gesucht | der Lauf bearbeitet gerade diese Position (nur im Arbeitsspeicher) |
| `klaerung` | Angabe fehlt | PRO.FILE-Nr. oder Hersteller-Artikelnr. fehlt, oder die Ex-Frage ist offen |
| `vorschlag` | Fund prüfen | mindestens ein Vorschlag wartet auf einen Menschen |
| `vorhanden` | aus der Datenbank / freigegeben | Pflichtdokument geprüft vorhanden; „freigegeben“, wenn es in diesem Vorgang freigegeben wurde |
| `nicht_gefunden` | E-Mail-Entwurf | gesucht, nichts gefunden |
| `angefordert` | angefordert / erinnert | Anfrage gesendet; „erinnert“, sobald eine Erinnerung gesendet ist |
| `eskaliert` | eskaliert | an den Einkauf eskaliert, bleibt offen |
| `erledigt` | freigegeben | von Hand erledigt, Begründung in der Notiz |
| `kein_ex_teil` | kein Ex-Teil | Entscheidung eines Menschen |

Ein Vorschlag geht vor: Kommt zu einer angefragten oder eskalierten Position eine Antwort
herein, steht sie wieder auf „Fund prüfen“.
