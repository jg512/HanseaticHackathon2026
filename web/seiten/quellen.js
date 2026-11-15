// Herkunft der Funde (M6 „Quelle für neue Zertifikate wird zur Prüfung angezeigt“): eine
// aufklappbare Zeile in jedem Fund der Artikelseite. Je Fund der Quellennachweis aus /api/artikel/<id> (funde[].quellennachweis,
// backend/app/services/zertifikatsserver.quellennachweis): Art, ursprünglicher Download-Link,
// Webseite (die Websuche hält sie nicht fest: „nicht bekannt“), Eingang, Dateiname. Ob die Datei
// schon im Dokumentenarchiv liegt, fragt das Modul mit einer Anfrage für alle Funde
// (/zert/api/dokumente?sha256=…); nur dann steht der Link dorthin da.
(function () {
  "use strict";

  const STATUS = { vorgeschlagen: "Vorschlag, noch nicht entschieden", freigegeben: "Freigegeben", abgelehnt: "Abgelehnt" };
  const archiv = {};   // Schlüssel (SHA-256 mit Komma) -> { zeit, docs: {sha: Dokument} } oder { fehler }
  const laeuft = new Set();

  const stil = document.createElement("style");
  stil.textContent = `
    .qn-fund { padding: 10px 0; border-bottom: 1px solid var(--linie); }
    .qn-fund:last-child { border-bottom: none; padding-bottom: 0; }
    .qn-fund .qn-datei { font-weight: 600; overflow-wrap: anywhere; }
    .qn-felder { display: grid; grid-template-columns: 210px 1fr; gap: 3px 12px; margin-top: 6px; font-size: 13px; }
    .qn-felder > span:nth-child(odd) { color: var(--grau); }
    .qn-felder a { overflow-wrap: anywhere; }
    @media (max-width: 700px) { .qn-felder { grid-template-columns: 1fr; } }
  `;
  document.head.appendChild(stil);

  const zeit = iso => {
    if (!iso) return "";
    const d = new Date(/Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return isNaN(d) ? iso : `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
  };
  // nur echte Webadressen als Link, nichts anderes (kein javascript: aus fremden Daten)
  const link = (url, text) => /^https?:\/\//i.test(url || "")
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text || url)}</a>` : esc(url || "");

  function imArchiv(funde) {
    const hashes = [...new Set(funde.map(f => f.quellennachweis?.sha256).filter(Boolean))].sort();
    if (!hashes.length) return { docs: {} };
    const k = hashes.join(",");
    const da = archiv[k];
    if ((!da || Date.now() - da.zeit > 60000) && !laeuft.has(k)) {
      laeuft.add(k);
      api(`/zert/api/dokumente?sha256=${encodeURIComponent(k)}`)
        .then(liste => { archiv[k] = { zeit: Date.now(), docs: Object.fromEntries(liste.map(d => [d.sha256, d])) }; })
        .catch(f => { console.warn("Dokumentenarchiv für die Herkunft nicht erreichbar:", f); archiv[k] = { zeit: Date.now(), fehler: true }; })
        .finally(() => { laeuft.delete(k); if (zustand.seite === "artikel" && zustand.a) neuZeichnen(); });
    }
    return da || null;
  }

  // nur die aufgeklappten Herkunftsangaben neu zeichnen, nicht die ganze Seite
  function neuZeichnen() {
    const funde = zustand.a?.funde || [];
    const stand = imArchiv(funde);
    document.querySelectorAll(".qn-inhalt[data-fund]").forEach(el => {
      const f = funde.find(x => String(x.id) === el.dataset.fund);
      if (f) el.innerHTML = fundZeilen(f, stand);
    });
  }

  function fundZeilen(f, stand) {
    const q = f.quellennachweis;
    if (!q) {  // Dokument aus der Datenbank ohne freigegebenen Fund
      return `<div class="qn-felder"><span>Herkunft</span><span>Das Dokument stammt aus der Datenbank. Woher es ursprünglich kam, ist dort nicht vermerkt.</span>
        ${f.url_quelle ? `<span>Ursprünglicher Download-Link</span><span>${link(f.url_quelle)}</span>` : ""}</div>`;
    }
    const doc = stand?.docs?.[q.sha256];
    const imArchivText = !q.sha256 ? "Ohne Prüfsumme lässt sich das nicht feststellen."
      : !stand ? "Wird nachgesehen …"
      : stand.fehler ? "Das Dokumentenarchiv ist gerade nicht erreichbar."
      : doc ? `${link(location.origin + (q.archiv_link || `/zert/api/dateien/${q.sha256}`), "Datei im Dokumentenarchiv öffnen")}${doc.quelle_art_text ? `<div class="beleg">Dort vermerkt: ${esc(doc.quelle_art_text)}</div>` : ""}`
      : f.status === "freigegeben" ? "Noch nicht im Dokumentenarchiv. Die Datei wird bei der nächsten Freigabe nachgereicht."
      : "Noch nicht im Dokumentenarchiv. Nach der Freigabe wird die Datei dort abgelegt.";
    return `<div class="qn-felder">
      <span>Herkunft</span><span>${esc(q.art_text || q.art || "")}${q.text ? `<div class="beleg">${esc(q.text)}</div>` : ""}</span>
      <span>Ursprünglicher Download-Link</span><span>${q.link ? link(q.link) : '<span class="grau">nicht bekannt</span>'}</span>
      ${q.art === "internet" ? `<span>Gefunden auf der Webseite</span><span>${q.seite_gefunden_auf ? link(q.seite_gefunden_auf) : '<span class="grau">nicht bekannt</span>'}</span>` : ""}
      ${q.abgerufen_am ? `<span>${q.art === "internet" ? "Abgerufen am" : "Eingegangen am"}</span><span>${esc(zeit(q.abgerufen_am))}</span>` : ""}
      ${q.datei ? `<span>Ursprünglicher Dateiname</span><span class="mono">${esc(q.datei)}</span>` : ""}
      <span>Im Dokumentenarchiv</span><span>${imArchivText}</span>
    </div>`;
  }

  // Seit Runde 3 gehört die Herkunft zum Fund selbst: eine Zeile unter dem Dateinamen, die
  // Einzelheiten auf Klick (index.html, fundKarte ruft window.herkunftZeile). Die Abfrage beim
  // Dokumentenarchiv läuft erst, wenn jemand die Zeile aufklappt.
  // kurz: Text der Zeile (z. B. „Aus dem Ordner des Herstellers, Fundstelle auf Seite 1“)
  window.herkunftZeile = (f, alle, kurz) => {
    const q = f.quellennachweis;
    kurz = kurz || (q ? (q.art_text || q.art || "Herkunft") : "Aus der Datenbank");
    const schl = `fund-herkunft-${f.id}`;
    const offen = auf(schl);
    return `<details class="herkunft-zeile" data-merk="${schl}" ${offen ? "open" : ""} onclick="event.stopPropagation()"
        ontoggle="if (this.open) herkunftLaden()">
      <summary>${esc(kurz)}. <u>Herkunft anzeigen</u></summary>
      <div class="qn-inhalt" data-fund="${esc(f.id)}">${fundZeilen(f, offen ? imArchiv(alle || [f]) : null)}</div>
    </details>`;
  };
  window.herkunftLaden = () => neuZeichnen();
})();
