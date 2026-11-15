FROM python:3.12-slim

WORKDIR /app

# Texterkennung für Scans (app/services/pdf_text.py): Tesseract mit Deutsch, Englisch, Französisch
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

COPY tools/install_html2markdown.py /tmp/
RUN python /tmp/install_html2markdown.py && html2markdown --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Headless-Browser für die Internet-Suche: besteht die Cloudflare-/Akamai-Prüfung der Hersteller
RUN apt-get update && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 5000

# Tabellen anlegen (falls noch nicht vorhanden), Scans im Hintergrund per OCR lesen (beim ersten
# Start einige Minuten, die Suche wartet nicht darauf), dann den Server starten.
# Langes Zeitlimit: die Suche mit Websuche kann Minuten dauern.
# Ein Prozess (Protokoll und Lauf der Suche liegen im Arbeitsspeicher), mehrere Threads: die
# Oberfläche bleibt bedienbar, während die Suche läuft.
CMD ["sh", "-c", "flask --app wsgi init-db && (nice flask --app wsgi ocr &) && gunicorn --threads 8 --timeout 900 --bind 0.0.0.0:5000 wsgi:app"]
