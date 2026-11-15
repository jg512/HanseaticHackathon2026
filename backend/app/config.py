"""Konfiguration, alles über Umgebungsvariablen (siehe .env.example)."""

import os


class Config:
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///atex.db")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # größte Stückliste: 20 MB

    # Ordner mit den Ex-Dokumenten (eigener Bestand, ohne Dokumentenarchiv dessen Ersatz)
    ATEX_DOCS = os.environ.get("ATEX_DOCS", "/data/ex_dokumente")
    # Ähnlichkeitssuche (services/rag.py), leer = Stufe aus. Der Suchdienst gehört nicht zu
    # diesem Repository; mit einem eigenen Dienst hier dessen Adresse eintragen
    SEARCH_URL = os.environ.get("SEARCH_URL", "")
    # Texterkennung für Scans (services/pdf_text.py), „0“ = aus; Ergebnisse je Datei in OCR_CACHE
    OCR = os.environ.get("OCR", "1") != "0"
    OCR_SPRACHEN = os.environ.get("OCR_SPRACHEN", "deu+eng+fra")
    OCR_CACHE = os.environ.get("OCR_CACHE", "/ocr")
    # Funde aus Ordner und Archiv ohne Befund der Vorprüfung gibt der Agent selbst frei, markiert
    # als „automatisch freigegeben“ (services/freigabe.automatisch), „0“ = immer ein Mensch
    AUTOMATISCH_FREIGEBEN = os.environ.get("AUTOMATISCH_FREIGEBEN", "1") != "0"

    # Internet-Stufe (services/internet.py): Web-Anfragen je Position, 0 = aus, höchstens 50.
    # Der Anwender kann den Wert beim Start der Suche ändern.
    INTERNET_VERSUCHE_PRO_TEIL = int(os.environ.get("INTERNET_VERSUCHE_PRO_TEIL", 10))
    # Suchseite mit {q} für die Anfrage, z. B. eine eigene SearXNG-Instanz:
    # "https://searx.example/search?q={q}". Leer: Einstieg über die Startseite des Herstellers.
    INTERNET_SUCH_URL = os.environ.get("INTERNET_SUCH_URL", "")
    INTERNET_TIMEOUT = int(os.environ.get("INTERNET_TIMEOUT", 10))  # Sekunden je Anfrage
    # "1": das Sprachmodell wählt aus der Linkliste einer Seite, sonst nur die Bewertung im Code
    INTERNET_LLM = os.environ.get("INTERNET_LLM", "") == "1"
    # "1": Seiten und PDFs über einen echten (Headless-)Browser laden (Playwright). Der besteht
    # die Cloudflare-Prüfung der Hersteller. Leer: ehrliche httpx-Abrufe (werden gesperrt).
    INTERNET_BROWSER = os.environ.get("INTERNET_BROWSER", "") == "1"
    HTML2MARKDOWN = os.environ.get("HTML2MARKDOWN", "html2markdown")  # Programm, HTML -> Markdown

    # Sprachmodell über LiteLLM: LLM_MODEL ist „anbieter/modell“, nur ein Beispiel. Leere
    # Adresse = die des Anbieters; leerer Schlüssel = LiteLLM sucht ihn in der Umgebung (z. B.
    # OPENAI_API_KEY). Gefragt wird ein Modell nur mit INTERNET_LLM=1 oder EMAIL_MIT_LLM=1.
    LLM_MODEL = os.environ.get("LLM_MODEL", "openai/gpt-4o-mini")
    LLM_API_BASE = os.environ.get("LLM_API_BASE", "")
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
    LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", 60))  # Sekunden

    # Websuche, die der Anwender im eigenen Browser öffnet (das Backend ruft sie nicht ab)
    WEBSUCHE_URL = os.environ.get("WEBSUCHE_URL", "https://duckduckgo.com/?q={q}")

    # Postausgang (services/versand.py). Ohne Benutzer und Passwort: ohne Anmeldung (Mailpit).
    # SMTP_SICHERHEIT: keine | starttls | ssl; ein anderer Wert hält den Start an.
    SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
    SMTP_PORT = os.environ.get("SMTP_PORT", "1025")
    SMTP_BENUTZER = os.environ.get("SMTP_BENUTZER", "")
    SMTP_PASSWORT = os.environ.get("SMTP_PASSWORT", "")
    SMTP_SICHERHEIT = os.environ.get("SMTP_SICHERHEIT", "keine")
    MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "")
    MAIL_ANTWORT_AN = os.environ.get("MAIL_ANTWORT_AN", "")
    # Mit EMAIL_NUR_TEST gehen E-Mails nur an diese Adresse, nie an echte Hersteller
    MAIL_TEST_ADDRESS = os.environ.get("MAIL_TEST_ADDRESS", "test@example.com")
    MAIL_FROM = os.environ.get("MAIL_FROM", "atex-agent@example.com")
    # "1": das Sprachmodell formuliert die Einleitung der E-Mail, sonst gilt die feste Vorlage
    EMAIL_MIT_LLM = os.environ.get("EMAIL_MIT_LLM", "") == "1"
    # Ohne Antwort des Herstellers: nach so vielen Stunden ist eine Erinnerung fällig, nach so
    # vielen Erinnerungen die Eskalation an den Einkauf (E7, E8). 0 Stunden: sofort (Demo).
    ERINNERUNG_NACH_STUNDEN = float(os.environ.get("ERINNERUNG_NACH_STUNDEN", 48))
    ERINNERUNGEN_MAX = int(os.environ.get("ERINNERUNGEN_MAX", 1))
    # Empfänger der Mängelliste an den Einkauf (E5); leer: die Test-Adresse. Der Einkauf ist
    # intern, nie ein Hersteller; im Standard fängt Mailpit jede E-Mail ab.
    EINKAUF_ADRESSE = os.environ.get("EINKAUF_ADRESSE", "")

    # Pflichtsprachen der Dokumente (M8), Kürzel mit Komma. Standard wie die Spalten (d) und (e)
    # der Kundenliste. Fehlende Sprachen werden gemeldet und im Ordner nachgesucht.
    SPRACHEN = [s.strip().lower() for s in os.environ.get("SPRACHEN", "de,en").split(",")
                if s.strip()]

    # EX-Bauteil-Liste (services/ex_liste.py): „1“ = unsichere Positionen warten auf die
    # Ex-Entscheidung eines Menschen. Standard aus: jede Position mit Artikelnummer wird gesucht.
    EX_LISTE = os.environ.get("EX_LISTE", "") == "1"
    # „1“ = Markierungen „veraltet“ des Dokumentenarchivs fließen in Suche und Vorprüfung ein
    VERALTET_PRUEFUNG = os.environ.get("VERALTET_PRUEFUNG", "") == "1"

    # Zertifikatsserver (zertifikatsserver/): Markierungen „veraltet“ je Datei; leer = ohne Server
    ZERTIFIKATSSERVER_URL = os.environ.get("ZERTIFIKATSSERVER_URL", "")

    # HTML-Oberfläche (routes/oberflaeche.py): „1“ = die Suche läuft im Anfrage-Thread statt im
    # Hintergrund (für Tests). OBERFLAECHE_DEMO_48H „0“ = „erinnern“ wartet die echte Frist
    # ERINNERUNG_NACH_STUNDEN ab; Standard: der Knopf tut so, als wären die 48 h vorbei (Demo).
    OBERFLAECHE_SUCHE_SYNCHRON = os.environ.get("OBERFLAECHE_SUCHE_SYNCHRON", "") == "1"
    OBERFLAECHE_DEMO_48H = os.environ.get("OBERFLAECHE_DEMO_48H", "1") != "0"

    # Zeitzone für Uhrzeiten im Protokoll der Oberfläche; gespeichert wird in UTC
    ZEITZONE = os.environ.get("ZEITZONE", "Europe/Berlin")

    # Gültigkeit aus dem Dokument (services/angaben.py): läuft ein Zertifikat in so vielen Tagen
    # ab, nennt die Vorprüfung das als Hinweis; auch Standard für GET /api/fristen
    ABLAUF_WARNUNG_TAGE = int(os.environ.get("ABLAUF_WARNUNG_TAGE", 180))
    # Hersteller im Dokument gegen die Stückliste: so ähnlich (0–1) muss der Anfang des Namens
    # mindestens sein, wenn er nicht genau enthalten ist (OCR verliest einzelne Zeichen)
    HERSTELLER_AEHNLICHKEIT = float(os.environ.get("HERSTELLER_AEHNLICHKEIT", 0.9))

    # Sprachmodelle aus einer Datei (services/modelle.py, Vorlage config/modelle.beispiel.yaml):
    # mehrere Modelle, Standard je Aufgabe, Wahl je Vorgang. Fehlt die Datei, gilt LLM_* oben.
    MODELLE_DATEI = os.environ.get("MODELLE_DATEI", "/config/modelle.yaml")

    # Anmeldung (app/auth.py). LOGIN_PFLICHT „0“: ohne Anmeldung offen, nur für Tests.
    # SECRET_KEY signiert das Sitzungs-Cookie; fehlt er, gilt ein Entwicklungswert (Warnung beim Start).
    LOGIN_PFLICHT = os.environ.get("LOGIN_PFLICHT", "1") != "0"
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    # Stunden ohne Anfrage, nach denen die Sitzung endet; jede Anfrage verlängert sie
    SITZUNG_STUNDEN = float(os.environ.get("SITZUNG_STUNDEN", 12))
    SESSION_COOKIE_NAME = "atex_sitzung"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "") == "1"  # nur über HTTPS
    LOGIN_MAX_FEHLVERSUCHE = int(os.environ.get("LOGIN_MAX_FEHLVERSUCHE", 5))  # dann gesperrt
    LOGIN_SPERRE_MINUTEN = int(os.environ.get("LOGIN_SPERRE_MINUTEN", 15))
    LOGIN_VERZOEGERUNG = float(os.environ.get("LOGIN_VERZOEGERUNG", 0.5))  # Sekunden je Fehlversuch

    # Herstellerkontakte (services/kontakte.py): „0“ = E-Mails gehen wirklich an die Adresse des
    # Herstellers. Standard: jede E-Mail an einen Hersteller geht an MAIL_TEST_ADDRESS, der
    # Entwurf nennt den eigentlichen Empfänger nur. Ohne gespeicherte Adresse wird nicht gesendet.
    EMAIL_NUR_TEST = os.environ.get("EMAIL_NUR_TEST", "1") != "0"
    # Pflichtsprache der Pflichtdokumente (M8, M20): ohne die EU-Konformitätserklärung in dieser
    # Sprache ist eine Position nicht vollständig. SPRACHEN sind die Sprachen, die gesucht und
    # gezeigt werden; fehlt dort eine, ist das nur ein Hinweis.
    PFLICHTSPRACHE = os.environ.get("PFLICHTSPRACHE", "de").strip().lower() or "de"

    # Öffentliche Adresse der Oberfläche (Dienst web), wie sie im Browser steht. Daraus baut die
    # Kundenliste die Links ins Dokumentenarchiv (<Adresse>/zert/api/dokumente/<id>/datei), damit
    # sie auch erreicht, wer die Excel-Datei öffnet. Auf einem Server die echte Adresse eintragen.
    OEFFENTLICHE_ADRESSE = os.environ.get("OEFFENTLICHE_ADRESSE", "http://localhost:8000")
