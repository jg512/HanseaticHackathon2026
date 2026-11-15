FROM python:3.12-slim

WORKDIR /app

# Texterkennung für Scans (app/ocr.py): Tesseract mit Deutsch, Englisch, Französisch, wie im Backend
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5100

# Tabellen anlegen und fehlende Spalten nachtragen, bei leerer Datenbank den Altbestand einlesen,
# Scans im Hintergrund per OCR lesen (Start und Import warten nicht darauf), dann den Server
# starten. Scheitert der Import (z. B. Ordner fehlt), startet der Server trotzdem.
CMD ["sh", "-c", "flask --app wsgi init-db && (flask --app wsgi import-altbestand --nur-wenn-leer || echo 'Import des Altbestands fehlgeschlagen') && (nice flask --app wsgi ocr --dauerhaft &) && gunicorn --timeout 300 --bind 0.0.0.0:5100 wsgi:app"]
