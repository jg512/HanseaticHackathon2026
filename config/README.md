# Konfiguration der Sprachmodelle

`modelle.beispiel.yaml` ist die Vorlage (im Git), `modelle.yaml` die echte Datei mit Schlüsseln
(nicht im Git). docker-compose hängt diesen Ordner schreibgeschützt als `/config` in das Backend,
das `/config/modelle.yaml` liest (Umgebungsvariable `MODELLE_DATEI`). Ein Sprachmodell ist
optional: Ohne Datei und mit den Schaltern im Standard fragt der Agent keins.

## Ein Modell hinzufügen, in drei Schritten

1. Einmalig: `cp config/modelle.beispiel.yaml config/modelle.yaml`.
2. In `config/modelle.yaml` unter `modelle:` einen Eintrag ergänzen, z. B.:
   ```yaml
     - name: claude-sonnet
       art: chat
       modell: anthropic/claude-sonnet-5-5
       api_key: "DEIN-SCHLUESSEL"     # oder: api_key_env: ANTHROPIC_API_KEY
   ```
   Soll es ohne Wahl gelten, unter `standard:` die Aufgabe darauf zeigen lassen
   (`websuche`, `email`; `rag_antwort` und `rag_embedding` nur für einen eigenen Suchdienst).
3. Speichern. Das Backend liest die Datei bei der nächsten Anfrage neu, ohne Neustart. Danach
   kann das Modell auf der Seite eines Vorgangs gewählt werden. Die Seite „Sprachmodelle“ mit
   „Verbindung testen“ ist in der Oberfläche ausgeblendet; denselben Test macht
   `POST /api/modelle/<name>/test` als Admin.

## Gut zu wissen

- **Schlüssel** stehen nur in `modelle.yaml` oder in Umgebungsvariablen (`api_key_env`). Die
  Oberfläche zeigt nie einen Schlüssel, nur „Zugang hinterlegt: ja/nein“; auch Antworten,
  Log und Protokoll enthalten keinen. Mit `api_key_env` muss die Variable im Container gesetzt
  sein: `ANTHROPIC_API_KEY` und `OPENAI_API_KEY` reicht docker-compose aus `.env` durch.
- **Fehler** in der Datei (Tippfehler im Eintrag, doppelter Name, fehlender Zugang, Standard
  auf ein fehlendes oder falsches Modell) meldet das Backend mit Zeile bzw. Modellname im Log und
  auf der Seite „Sprachmodelle“. Dann arbeitet der Agent ohne Sprachmodell, nie still mit
  einem anderen.
- **Ohne Datei** gilt genau ein Modell „standard“ aus `LLM_MODEL`, `LLM_API_BASE`,
  `LLM_API_KEY`, `LLM_TIMEOUT` (`.env`). Die Standardwerte sind nur ein Beispiel
  (`openai/gpt-4o-mini`, Adresse des Anbieters, Schlüssel aus der Umgebung).
- **Ob überhaupt ein Modell gefragt wird**, schalten `INTERNET_LLM=1` (Websuche wählt Links)
  und `EMAIL_MIT_LLM=1` (Einleitung der E-Mail-Entwürfe) in `.env`. In `docker-compose.yml` und
  `.env.example` sind beide aus (`0`); dann arbeitet der Agent nach festen Regeln bzw. der festen
  Vorlage, und es wird kein Modell gefragt. Schaltet man einen an und das Modell antwortet nicht
  oder falsch, fällt der Agent auf dieselben Regeln bzw. die Vorlage zurück.
- **Vorlage:** `modelle.beispiel.yaml` enthält ein Beispiel mit OpenAI (`beispiel-chat`,
  `beispiel-embedding`, Schlüssel aus `OPENAI_API_KEY`); Claude, ein OpenAI-kompatibler Anbieter
  mit eigener Adresse und Ollama stehen als auskommentierte Beispiele darin. Fehlt der Schlüssel,
  meldet das Backend einen Fehler in der Datei und arbeitet ohne Modell. Im Hackathon lief nur ein
  OpenAI-kompatibler Anbieter mit eigener Adresse; die anderen Einträge folgen der Doku von
  LiteLLM und sind nicht ausprobiert.
