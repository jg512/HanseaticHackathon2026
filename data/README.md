# Daten

Die Ex-Dokumente (PDFs der Hersteller) und die Stückliste, mit denen wir im Hackathon gearbeitet
haben, kamen von den Organisatoren des Case und sind nicht öffentlich. Sie liegen deshalb nicht
in diesem Repository. Ohne sie startet der Stack trotzdem, die Suche findet nur nichts. Wer den
Agenten ausprobieren will, bringt eigene Dokumente und eine eigene Stückliste mit.

| Pfad | Inhalt | Im Git |
|---|---|---|
| `ex_dokumente/` | eigener Bestand an Ex-Dokumenten, ein Unterordner je Hersteller | nur `.gitkeep` |
| `ex_dokumente/_internet/`, `ex_dokumente/_upload/` | legt der Stack selbst an: Funde der Websuche, hochgeladene Dateien, Zwischenspeicher des Archivs | nein |

## Eigene Dokumente

PDFs je Hersteller in einen eigenen Ordner legen:

```
data/ex_dokumente/
├── Festo/
├── Pepperl+Fuchs/
└── Siemens/
```

Der Ordnername gilt als Name des Herstellers. Weicht er vom Namen in der Stückliste ab, hilft
`HERSTELLER_NAMEN` in `.env` (zum Beispiel `P+F=Pepperl+Fuchs`). Das Dokumentenarchiv liest den
Ordner beim ersten Start einmal ein (nur lesend); später dazugelegte Dateien holt
`docker compose exec zertifikatsserver flask --app wsgi import-altbestand`. Backend und Archiv
sehen den Ordner unter `/data/ex_dokumente` bzw. `/altbestand`.

## Eigene Stückliste

Eine `.xlsx`-Datei, gelesen wird nur das erste Tabellenblatt (`backend/app/services/excel_import.py`).

**Kopfangaben (optional, oberhalb der Tabelle):** Zeilen, deren erste Zelle eine dieser
Bezeichnungen trägt und deren zweite Zelle den Wert:

| Erste Zelle | Beispiel | Wofür |
|---|---|---|
| `Maschine` | `Abfüllanlage TM-1` | Name der Maschine in E-Mails und Kundenliste |
| `Ex-Bereich` | `Innenraum Zone 1, Umgebung Zone 2` | ordnet Einbauorte wie „Innenraum“ einer Zone zu |
| `Geforderte Kategorie` | `Zone 1: mindestens 2G, Zone 2: mindestens 3G` | Kategorie je Zone; ohne Angabe gilt die Mindestkategorie nach ATEX |

**Kopfzeile der Tabelle:** Die erste Zeile, in der „Pos.“ und „Bezeichnung“ stehen. Alle sieben
Spalten sind Pflicht, die Reihenfolge ist frei. Groß- und Kleinschreibung, Satzzeichen und
Zusätze in Klammern zählen nicht, „PRO.FILE-Nr. (erfunden)“ gilt also als „PRO.FILE-Nr.“.

| Spalte | Inhalt |
|---|---|
| `Pos.` | Positionsnummer |
| `PRO.FILE-Nr.` | eigene Artikelnummer, Schlüssel in der Datenbank |
| `Bezeichnung` | Text des Bauteils |
| `Hersteller` | Name des Herstellers |
| `Hersteller-Artikelnr.` | Typbezeichnung des Herstellers; danach sucht der Agent im Dokument |
| `Menge` | Anzahl |
| `Einbauort` | z. B. `Zone 1`, `Innenraum` oder `Schaltschrank außerhalb Ex-Bereich` |

Darunter eine Zeile je Position, leere Zeilen werden übersprungen. Fehlt die Kopfzeile oder eine
Spalte, oder gibt es keine Positionen, lehnt der Import die Datei mit einer Meldung ab. Fehlende
Werte in einzelnen Zeilen (keine PRO.FILE-Nr., kein Hersteller, keine Artikelnummer, kein
Einbauort, doppelte PRO.FILE-Nr.) meldet er je Zeile, ergänzt aber nichts.

Herstelleradressen für die Anfrage-E-Mails lassen sich auf der Seite Stammdaten pflegen oder als
Excel- bzw. CSV-Datei importieren ([../docs/api.md](../docs/api.md)).

## Tests ohne diese Daten

Die Backend-Tests bauen ihre Stücklisten selbst (`backend/tests/conftest.py`, Funktion `xlsx`)
und brauchen keine Datei aus diesem Ordner. Drei Tests lesen echte PDFs aus
`data/ex_dokumente/<Hersteller>/` und werden mit `skipif` übersprungen, wenn der Ordner fehlt
(`test_findings.py`, `test_tabellen.py`). Ein Test importiert `herstellerkontakte_beispiel.xlsx`
in diesem Ordner (nur erfundene Adressen unter `.example`) und überspringt sich, wenn der Ordner
im Container nicht eingebunden ist (`test_kontakte.py`).
