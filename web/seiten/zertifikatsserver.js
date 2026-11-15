// Dokumentenarchiv (Dienst „zertifikatsserver“): Zertifikate, Anleitungen, Datenblätter und andere
// Dateien mit Versionen je Teil und Herkunft (Quellennachweis) ansehen und bearbeiten, Verdacht
// „veraltet“ markieren, bestätigen oder verwerfen.
// Wörter für Anwender kommen aus GET /zert/api/begriffe (zertifikatsserver/app/begriffe.py); die
// Tafeln unten sind nur der Stand, bis die Antwort da ist.
//
// Spricht über /zert/api/… direkt mit dem Zertifikatsserver (nginx reicht weiter); nur die neue
// Suche für die betroffenen Positionen eines Vorgangs läuft über das Backend (/api/boms/…).
// Seiten: #/zertifikate (Dokumente), …/dok/<id>, …/teile, …/teile/<id>, …/neue-suche, …/import
(function () {
  const STAND = {
    ohne: ["Unauffällig", "s-kein_ex"], verdacht: ["Möglicherweise veraltet", "s-zu_pruefen"],
    bestaetigt: ["Veraltet, Ersatz wird gesucht", "s-info_fehlt"], verworfen: ["Geprüft, gilt weiter", "s-geprueft"],
  };
  const VERSION = { aktuell: ["Aktuelle Version", "s-geprueft"], abgeloest: ["Durch neuere Version ersetzt", "s-wartet"], beendet: ["Beendet, ohne Nachfolger", "s-kein_ex"] };
  const ARTEN = {
    eu_konformitaetserklaerung: "EU-Konformitätserklärung", eu_baumusterpruefbescheinigung: "EU-Baumusterprüfbescheinigung",
    uk_konformitaetserklaerung: "UK-Konformitätserklärung", uk_baumusterpruefbescheinigung: "UK-Baumusterprüfbescheinigung",
    bedienungsanleitung: "Bedienungsanleitung", datenblatt: "Datenblatt", zeichnung: "Zeichnung", sonstiges: "Sonstiges Dokument",
    unbekannt: "Dokumentart noch offen",
  };
  // Herkunft einer Datei (Quellennachweis), wie begriffe.QUELLE_ART im Archiv
  const HERKUNFT = {
    hochgeladen: "Von Hand hochgeladen", hersteller: "Vom Hersteller geschickt", internet: "Im Internet gefunden",
    altbestand: "Übernommener Bestand", atex_agent: "Bei einer Freigabe im ATEX-Agent übernommen",
  };
  const FAMILIE = { pdf: "PDF", office: "Office-Datei", bild: "Bild", zeichnung: "Zeichnung", archiv: "Archiv (ZIP)", text: "Textdatei" };
  // Stand einer Position im Backend, für die Liste „Ersatz wird gesucht“
  const POSITION = {
    offen: "Offen", klaerung: "Klärung nötig", vorschlag: "Vorschlag prüfen", nicht_gefunden: "Nicht gefunden",
    angefordert: "Angefordert", eskaliert: "An den Einkauf eskaliert", vorhanden: "Vorhanden", kein_ex_teil: "Kein Ex-Teil",
    erledigt: "Von Hand erledigt",
  };
  const NAME = "Dokumentenarchiv";
  const SPRACHEN = ["de", "en", "fr"];
  const RICHTLINIEN = ["94/9/EG", "2014/34/EU"];
  const FELDNAME = {
    doc_type: "Dokumentart", ausstellungsdatum: "Ausstellungsdatum", gueltig_bis: "Gültig bis",
    zertifikatsnummern: "Zertifikatsnummern", sprachen: "Sprachen", richtlinien: "Richtlinien", normen: "Normen",
    hersteller: "Hersteller", artikelnr: "Artikelnr.", profile_no: "PRO.FILE-Nr.", bezeichnung: "Bezeichnung",
  };
  // Werte eines Dokuments in der Tabelle „Werte“ (seiten/tabelle.js): [Feld, Titel, Art]; Art
  // auswahl | datum | liste (Text mit Komma) | text. Ein neuer Wert des Servers ist ein Eintrag.
  const WERTE_FELDER = [
    ["doc_type", "Dokumentart", "auswahl"],
    ["ausstellungsdatum", "Ausstellungsdatum", "datum"],
    ["gueltig_bis", "Gültig bis", "datum"],
    ["zertifikatsnummern", "Zertifikatsnummern, mit Komma getrennt", "liste"],
    ["normen", "Normausgaben, mit Komma getrennt, zum Beispiel 60079-0:2018", "liste"],
    ["sprachen", "Sprachen, mit Komma getrennt, zum Beispiel de, en", "liste"],
    ["richtlinien", `Richtlinien, mit Komma getrennt: ${RICHTLINIEN.join(" oder ")}`, "liste"],
  ];
  const GESPERRT = "🔒";
  const ICON_ZS = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3h9l4 4v14H6Z"/><path d="M15 3v4h4"/><path d="m9 14 2 2 4-4"/></svg>';

  // was zwischen den Aufrufen der Seite bleibt: Filter, Auswahl, zuletzt geladene Listen
  const Z = {
    lauf: 0, m: null, meldung: null, bleibt: false, anzahl: 25,  // anzahl: so viele Zeilen zeigt die Liste
    alle: null, hersteller: [], regeln: null, begriffe: null,
    filter: { hersteller: "", stand: "", regel: "", ablage: "", lesbar: "", verknuepft: "", familie: "", herkunft: "", ausgemustert: false },
    auswahl: new Set(), sichtbar: [],
    dok: null, dokTeile: [], pdfOffen: true, pdfSeite: 0,
    teilSuche: { hersteller: "", artikelnr: "" }, teile: null, teil: null,
    neueSuche: null, vorgaenge: null, vorgang: null, betroffen: null, betroffenFehler: "", suchErgebnis: null, sucht: false,
    importe: null, normen: null,
  };

  // nginx meldet auch das fehlende Archiv als „Backend nicht erreichbar“
  const zapi = (pfad, opt) => api("/zert/api" + pfad, opt).catch(f => {
    throw f.message === "Backend nicht erreichbar" ? new Error("Das Dokumentenarchiv ist gerade nicht erreichbar. Bitte versuchen Sie es später noch einmal.") : f;
  });
  const pdf = url => "/zert" + url;
  // stabiler Link auf die Datei (ältere Antworten kennen nur pdf_url)
  const dateiUrl = d => pdf(d.datei_url || d.pdf_url);
  const webLink = (url, text) => /^https?:\/\//i.test(url || "") ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" style="word-break:break-all">${esc(text || url)}</a>` : esc(url || "");
  const datum = iso => iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "";
  // „13.11.2026, 14:05 Uhr“ in Ortszeit; das Archiv speichert UTC ohne Zone
  const zeit = iso => {
    if (!iso) return "";
    const d = new Date(/Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return isNaN(d) ? iso : `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
  };
  const art = a => (Z.begriffe?.doc_type || ARTEN)[a] || ARTEN[a] || a;
  const pille = (tafel, code) => { const [text, klasse] = tafel[code] || [code, "s-kein_ex"]; return `<span class="chip ${klasse}">${esc(text)}</span>`; };
  const liste = text => (text || "").split(",").map(x => x.trim()).filter(Boolean);
  const wert = x => Array.isArray(x) ? x.join(", ") : x;
  const neben = text => text ? `<div class="beleg">${esc(text)}</div>` : "";
  const leerZeile = text => `<p class="grau" style="margin:0 0 4px">${esc(text)}</p>`;
  const optionen = (paare, gewaehlt) => paare.map(([w, t]) => `<option value="${esc(w)}" ${String(w) === String(gewaehlt) ? "selected" : ""}>${esc(t)}</option>`).join("");
  const feld = id => document.getElementById(id);
  const text = id => (feld(id)?.value || "").trim();
  const meine = () => zustand.seite === "zertifikate";
  // Lesbarkeit: aus der Texterkennung (Werte können falsch sein), wartet darauf, oder nicht lesbar
  const OCR_STAND = { offen: "Texterkennung folgt", kein_text: "kein Text erkannt", fehler: "Texterkennung gescheitert" };
  const undListe = a => a.length > 1 ? a.slice(0, -1).join(", ") + " und " + a[a.length - 1] : a.join("");
  // Andere Dateien als PDF: das Archiv liest nichts, die Angaben trägt ein Mensch ein
  const lesbarkeit = d => d.auslesbar === false ? `<span class="grau" title="${esc(d.auslesbar_grund || "")}">${esc(d.dateiart_text || "Datei")}, Angaben von Hand</span>`
    : d.per_ocr ? `<span class="unbelegt" title="${esc(d.ocr_hinweis || "")}">Text aus der Texterkennung, bitte Werte prüfen</span>`
    : d.lesbar ? "" : `<span class="unbelegt" title="${esc(d.ocr_hinweis || "")}">Scan ohne lesbaren Text${OCR_STAND[d.ocr_stand] ? ", " + OCR_STAND[d.ocr_stand] : ""}</span>`;
  const istPdf = d => d.auslesbar !== false;

  const stil = document.createElement("style");
  stil.textContent = `
    .zs-kacheln { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
    .zs-kacheln .statkarte { padding: 6px 12px; text-decoration: none; color: inherit; box-shadow: none; border-radius: 99px; gap: 6px; }
    .zs-kacheln .statkarte > div { display: flex; align-items: baseline; gap: 6px; flex-direction: row-reverse; }
    .zs-kacheln .statkarte .label { margin: 0; font-size: 12.5px; }
    .zs-kacheln .statkarte .wert { font-size: 15px; }
    .zs-kacheln .statkarte.an { border-color: var(--limette); background: var(--limette-hell); }
    .zs-mehr { display: flex; gap: 8px 14px; align-items: center; flex-wrap: wrap; margin-top: 10px; font-size: 12.5px; }
    .zs-filter details.zs-weitere { flex-basis: 100%; }
    .zs-filter details.zs-weitere > summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--gruen); }
    .zs-filter details.zs-weitere[open] > summary { margin-bottom: 8px; }
    .zs-filter details.zs-weitere > div { display: flex; gap: 10px 14px; flex-wrap: wrap; align-items: flex-end; }
    .zs-kacheln .statkarte.rot .wert { color: var(--rot); } .zs-kacheln .statkarte.gelb .wert { color: var(--gelb); }
    .zs-kacheln .statkarte.grau .wert { color: var(--grau); }
    .zs-reiter { display: flex; gap: 2px; flex-wrap: wrap; border-bottom: 1px solid var(--linie); margin-bottom: 16px; }
    .zs-reiter a { padding: 9px 14px; color: var(--text); text-decoration: none; font-weight: 500; border-bottom: 3px solid transparent; margin-bottom: -1px; }
    .zs-reiter a:hover { background: var(--limette-hell); }
    .zs-reiter a.an { font-weight: 600; border-bottom-color: var(--limette); }
    .zs-reiter .zahl { background: var(--rot-hell); color: var(--rot); border-radius: 2px; padding: 0 6px; font-size: 11px; font-weight: 700; margin-left: 6px; }
    .zs select, .zs input[type=date], .zs input[type=number] { font: inherit; padding: 6px 8px; border: 1px solid #ced4da; border-radius: 0; background: #fff; max-width: 100%; }
    .zs select:focus, .zs input[type=date]:focus, .zs input[type=number]:focus { outline: none; border-color: var(--gruen-marke); box-shadow: 0 0 0 3px rgba(187, 208, 61, .35); }
    .zs-filter { display: flex; gap: 10px 14px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 12px; }
    .zs-filter label { margin: 0 0 3px; }
    .zs-haken { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text); margin: 0; cursor: pointer; }
    .zs-liste { max-height: 540px; overflow: auto; border: 1px solid var(--linie); }
    .zs-liste th { position: sticky; top: 0; background: var(--weiss); z-index: 1; box-shadow: 0 1px 0 var(--linie); }
    .zs-liste td { padding: 7px 8px; hyphens: auto; overflow-wrap: anywhere; }
    .zs-liste th { padding-left: 8px; padding-right: 8px; }
    .zs-liste td.mono { font-size: 12.5px; }
    .zs-liste .zs-regeln { font-size: 12px; color: var(--grau); margin-top: 3px; line-height: 1.35; hyphens: manual; }
    .zs-liste .zs-links { white-space: nowrap; }
    @media (max-width: 1400px) { .zs-liste .zs-links { display: flex; flex-direction: column; gap: 4px; } .zs-liste .zs-punkt { display: none; } }
    .zs-liste tr.gewaehlt td { background: var(--limette-hell); }
    .zs-liste td.haken, .zs-liste th.haken { width: 34px; text-align: center; cursor: pointer; }
    .zs-auswahl { border-top: 3px solid var(--limette); }
    @media (min-height: 760px) { .zs-auswahl { position: sticky; bottom: 0; z-index: 2; margin-bottom: 0; } }
    .zs-gitter { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 4px 16px; }
    .zs details { border-top: 1px solid var(--linie); padding: 10px 0; }
    .zs details > summary { cursor: pointer; font-weight: 600; }
    .zs details[open] > summary { margin-bottom: 8px; }
    .zs h3 { font-size: 13.5px; margin: 14px 0 6px; }
    .zs .feld { grid-template-columns: 170px 1fr; }
    .zs-seite { background: none; border: none; padding: 0; color: var(--gruen); font-weight: 600; text-decoration: underline; cursor: pointer; }
    .zs-seite:hover { background: none; }
    .zs iframe.pdf { height: 720px; }
    .zs-normen input[type=text] { padding: 5px 7px; }
    .zs-herkunft .feld { grid-template-columns: 200px 1fr; }
    .zs-sha { font-size: 11.5px; word-break: break-all; }
`;
  document.head.appendChild(stil);

  // ---------- Rahmen: Kopf, Kacheln, Reiter ----------

  function melde(text, art = "gruen") {
    Z.meldung = { text, art };
    const el = feld("zs-meldung");
    if (el) el.innerHTML = meldung();
    toast(text);
  }
  const meldung = () => Z.meldung ? `<div class="hinweis ${Z.meldung.art}">${esc(Z.meldung.text)}</div>` : "";

  function zahlen() {
    const a = Z.alle || [];
    const z = s => a.filter(d => d.markierungsstand === s).length;
    return {
      alle: a.length, verdacht: z("verdacht"), verworfen: z("verworfen"),
      // wie GET /api/neue-suche: bestätigt und noch aktuell (nicht durch eine neue Version abgelöst)
      bestaetigt: a.filter(d => d.markierungsstand === "bestaetigt" && d.ablage === "aktuell").length,
      ausgemustert: a.filter(d => d.ausgemustert).length,
    };
  }

  function rahmen(reiter, inhalt, zurueck) {
    const z = zahlen();
    // schlanke Kacheln als Filter; die gewählte ist hervorgehoben
    const gleich = f => reiter === "dokumente" && Object.entries(Z.filter).every(([k, w]) => (f[k] ?? (k === "ausgemustert" ? false : "")) === w);
    const kachel = (titel, zahl, farbe, filter) => `<a class="statkarte klick ${farbe} ${gleich(filter) ? "an" : ""}" href="#/zertifikate" onclick="zs.kachel(${esc(JSON.stringify(filter))})" title="In der Liste zeigen">
      <div><div class="label">${titel}</div><div class="wert">${zahl}</div></div></a>`;
    const tab = (name, href, titel, zahl) => `<a href="${href}" class="${reiter === name ? "an" : ""}">${titel}${zahl ? `<span class="zahl">${zahl}</span>` : ""}</a>`;
    return `<div class="zs">
      <div class="kopfzeile"><div>${zurueck || ""}<h1>${NAME}</h1>
        <div class="grau">Hier liegen Zertifikate, Erklärungen, Anleitungen, Datenblätter und andere Dateien zu den Teilen, jeweils mit ihren Versionen und ihrer Herkunft. Prüfregeln melden, wenn ein Dokument veraltet sein könnte, und Sie entscheiden darüber.</div></div></div>
      <div id="zs-meldung">${meldung()}</div>
      ${darfSchreiben() ? "" : `<div class="hinweis" style="background:var(--gelb-hell);color:var(--gelb);border-left:3px solid #d9a400">${nameHinweis()}</div>`}
      <div class="zs-kacheln">
        ${kachel("Dokumente", z.alle, "", {})}
        ${kachel("Möglicherweise veraltet", z.verdacht, "gelb", { stand: "verdacht" })}
        ${kachel("Veraltet, Ersatz wird gesucht", z.bestaetigt, "rot", { stand: "bestaetigt", ablage: "aktuell" })}
        ${kachel("Geprüft, gilt weiter", z.verworfen, "", { stand: "verworfen" })}
        ${kachel("Im Archiv abgelegt", z.ausgemustert, "grau", { ausgemustert: true })}
      </div>
      <div class="zs-reiter">
        ${tab("dokumente", "#/zertifikate", "Dokumente")}
        ${tab("teile", "#/zertifikate/teile", "Teile")}
        ${tab("neue-suche", "#/zertifikate/neue-suche", "Ersatz wird gesucht", z.bestaetigt)}
        ${tab("import", "#/zertifikate/import", "Bestand einlesen und Prüfregeln")}
      </div>
      ${inhalt}
    </div>`;
  }

  // ---------- Laden und Seitenwechsel ----------

  async function grunddaten() {
    const [hersteller, regeln, alle, begriffe] = await Promise.all([zapi("/hersteller"), zapi("/regeln"), zapi("/dokumente"),
      Z.begriffe ? Z.begriffe : zapi("/begriffe").catch(() => null)]);
    Object.assign(Z, { hersteller, regeln, alle, begriffe });
    for (const id of [...Z.auswahl]) if (!alle.some(d => d.id === id)) Z.auswahl.delete(id);
  }

  async function zeige(m, app, behalten = false) {
    const lauf = ++Z.lauf;
    if (!behalten && !Z.bleibt) { Z.meldung = null; Z.suchErgebnis = null; }
    Z.bleibt = false;
    Z.m = m;
    const bereich = m[1] || "dokumente", id = m[2] ? +m[2] : null;
    if (!Z.alle && !behalten) app.innerHTML = `<div class="leer">lädt …</div>`;
    try {
      const extra =
        bereich === "dok" && id ? ladeDokument(id)
        : bereich === "teile" && id ? zapi(`/teile/${id}`).then(t => { Z.teil = t; })
        : bereich === "teile" ? ladeTeile()
        : bereich === "neue-suche" ? ladeNeueSuche()
        : bereich === "import" ? zapi("/importe").then(x => { Z.importe = x; })
        : null;
      await Promise.all([grunddaten(), extra]);
    } catch (f) {
      console.error("Dokumentenarchiv (zertifikatsserver) nicht ladbar:", f);
      if (lauf !== Z.lauf || !meine()) return;
      // 404 heißt: das Archiv antwortet, nur dieses Dokument oder Teil gibt es nicht (Befund B4)
      if (f.status === 404 && id && (bereich === "dok" || bereich === "teile")) {
        const dok = bereich === "dok";
        app.innerHTML = `<div class="zs"><div class="kopfzeile"><div><h1>${NAME}</h1></div></div>
          <div class="hinweis gelb"><strong>${dok ? `Ein Dokument mit der Nummer ${id} gibt es im Dokumentenarchiv nicht.` : `Ein Teil mit der Nummer ${id} gibt es im Dokumentenarchiv nicht.`}</strong>
            Vielleicht ist der Link veraltet. Alle ${dok ? "Dokumente" : "Teile"} finden Sie in der Liste.</div>
          <a class="knopf" href="${dok ? "#/zertifikate" : "#/zertifikate/teile"}">Zur Liste der ${dok ? "Dokumente" : "Teile"}</a></div>`;
        return;
      }
      app.innerHTML = `<div class="zs"><div class="kopfzeile"><div><h1>${NAME}</h1></div></div>
        <div class="hinweis rot"><strong>Das Dokumentenarchiv ist gerade nicht erreichbar.</strong> Bitte versuchen Sie es später noch einmal.</div>
        <button class="leise" onclick="zs.neu()">Noch einmal versuchen</button></div>`;
      return;
    }
    if (lauf !== Z.lauf || !meine()) return;
    zeichneNav();
    if (bereich === "dok" && id) zeichneDokument();
    else if (bereich === "teile" && id) zeichneTeil();
    else if (bereich === "teile") zeichneTeile();
    else if (bereich === "neue-suche") zeichneNeueSuche();
    else if (bereich === "import") { Z.normen = null; zeichneImport(); }
    else zeichneDokumente();
  }
  // neu laden nach einer Aktion; aufgeklappte Abschnitte bleiben offen
  async function neu() {
    if (!meine() || !Z.m) return;
    const titel = d => d.querySelector("summary")?.textContent.replace(/\(.*$/, "").trim();
    const offen = [...document.querySelectorAll(".zs details[open]")].map(titel);
    await zeige(Z.m, $("#app"), true);
    for (const d of document.querySelectorAll(".zs details")) if (offen.includes(titel(d))) d.open = true;
  }

  // schreibende Aktion: Name nötig, Meldung zeigen, Seite neu laden
  function schreibe(fn, ok, danach) {
    document.activeElement?.blur();
    return mitName(async von => {
      let antwort;
      try { antwort = await fn(von); }
      catch (f) { return melde(`Das wurde nicht gespeichert. ${f.message}`, "rot"); }
      melde(typeof ok === "function" ? ok(antwort) : ok);
      if (danach) danach(antwort); else await neu();
    });
  }
  const post = (pfad, json) => zapi(pfad, { method: "POST", json });

  // ---------- Dokumente: Filter, Liste mit Auswahl, Aktionen ----------

  function gefiltert() {
    const f = Z.filter, suche = zustand.suche;
    return (Z.alle || []).filter(d =>
      (f.ausgemustert || !d.ausgemustert)
      && (!f.hersteller || d.hersteller === f.hersteller)
      && (!f.stand || d.markierungsstand === f.stand)
      && (!f.regel || d.regeln.includes(f.regel))
      && (!f.ablage || d.ablage === f.ablage)
      && (!f.lesbar || d.lesbar === (f.lesbar === "ja"))
      && (!f.verknuepft || (d.teile.length > 0) === (f.verknuepft === "ja"))
      && (!f.familie || (d.familie || "pdf") === f.familie)
      && (!f.herkunft || d.quelle_art === f.herkunft)
      && (!suche || [d.id, d.dateiname, d.hersteller, art(d.doc_type), ...d.teile.map(t => t.artikelnr)].join(" ").toLowerCase().includes(suche)));
  }

  function zeichneDokumente() {
    const f = Z.filter, r = Z.regeln.regeln;
    const wahl = (id, titel, paare) => `<div><label for="zs-f-${id}">${titel}</label><select id="zs-f-${id}" onchange="zs.filter('${id}', this.value)">${optionen([["", "Alle"], ...paare], f[id])}</select></div>`;
    // drei Filter immer sichtbar, die übrigen auf Klick; aufgeklappt bleibt es, solange einer gesetzt ist
    const weitereAn = ["familie", "herkunft", "ablage", "lesbar", "verknuepft"].filter(k => f[k]).length + (f.ausgemustert ? 1 : 0);
    $("#app").innerHTML = rahmen("dokumente", `
      <div class="karte">
        <div class="zs-filter">
          ${wahl("hersteller", "Hersteller", Z.hersteller.map(h => [h.name, `${h.name} (${h.dokumente})`]))}
          ${wahl("stand", "Stand", Object.entries(STAND).map(([k, v]) => [k, v[0]]))}
          ${wahl("regel", "Prüfregel", [...Object.entries(r), ["manuell", "Von Hand gemeldet"]])}
          <button class="leise" onclick="zs.kachel({})" style="margin-left:auto">Filter zurücksetzen</button>
          <details class="zs-weitere" data-merk="zs-weitere-filter" ${weitereAn || auf("zs-weitere-filter") ? "open" : ""}>
            <summary>Weitere Filter${weitereAn ? ` (${weitereAn} gesetzt)` : ""}</summary>
            <div>
              ${wahl("familie", "Dateiart", Object.entries(FAMILIE))}
              ${wahl("herkunft", "Herkunft", Object.entries(HERKUNFT))}
              ${wahl("ablage", "Gebrauch", [["aktuell", "In Gebrauch"], ["archiv", "Im Archiv"]])}
              ${wahl("lesbar", "Text", [["ja", "Text gelesen"], ["nein", "Kein Text gelesen"]])}
              ${wahl("verknuepft", "Teil", [["ja", "Einem Teil zugeordnet"], ["nein", "Noch keinem Teil zugeordnet"]])}
              <label class="zs-haken" style="padding-bottom:7px"><input type="checkbox" ${f.ausgemustert ? "checked" : ""} onchange="zs.filter('ausgemustert', this.checked)"> Im Archiv abgelegte zeigen</label>
            </div>
          </details>
        </div>
        <div id="zs-liste"></div>
      </div>
      <div class="karte zs-auswahl" id="zs-auswahl" hidden></div>
      <div class="karte nur-schreiben">
        <details style="border:none;padding:0"><summary>Datei ablegen, ohne sie einem Teil zuzuordnen</summary>
          <p class="grau" style="margin:0 0 6px">Zum Beispiel ein Datenblatt, eine Zeichnung oder ein ZIP-Archiv vom Hersteller. Einem Teil ordnen Sie die Datei später auf ihrer Seite zu.</p>
          <div class="zs-gitter">
            <div><label for="zs-lhersteller">Hersteller</label><input type="text" id="zs-lhersteller" list="zs-lhliste"><datalist id="zs-lhliste">${Z.hersteller.map(h => `<option value="${esc(h.name)}">`).join("")}</datalist></div>
            ${dateiFelder("zs-l")}
          </div>
          <div class="zeile" style="margin-top:10px"><button onclick="zs.ablegen()" ${sperre()}>Datei ablegen</button>${nameHinweis()}</div>
        </details>
      </div>`);
    zeichneListe();
  }

  function zeichneListe() {
    const el = feld("zs-liste");
    if (!el) return;
    const dok = gefiltert(), r = Z.regeln.regeln;
    Z.sichtbar = dok.map(d => d.id);  // „alle gefilterten“ meint auch die Zeilen hinter „Weitere anzeigen“
    const seite = dok.slice(0, Z.anzahl), rest = dok.length - seite.length;
    const alleAn = dok.length > 0 && dok.every(d => Z.auswahl.has(d.id));
    const abgeleitet = "Das ergibt sich aus Prüfregeln, Markierungen und Gebrauch und lässt sich hier nicht ändern.";
    el.innerHTML = dok.length ? `
      <div class="grau" style="font-size:12.5px;margin-bottom:6px">${dok.length === 1 ? "1 Dokument" : `${dok.length} Dokumente`}${rest ? `, davon sehen Sie die ersten ${seite.length}` : ""}. ${darfSchreiben() ? "Mit einem Haken wählen Sie Dokumente aus, um sie als veraltet zu markieren. Dokumentart und Datum korrigieren Sie direkt in der Tabelle." : ""}</div>
      <div>${Tabelle.html({
        id: "zs-dokumente", rahmenKlasse: "zs-liste", titel: "Dokumente", zeilen: seite, name: d => `Dokument ${d.id} (${d.dateiname})`, hinweis: false,
        zeileKlasse: d => Z.auswahl.has(d.id) ? "gewaehlt" : "",
        // Haken zum Markieren nur für alle, die schreiben dürfen (Leser sehen keine Auswahl)
        vorne: !darfSchreiben() ? undefined : { klasse: "haken", kopf: `<input type="checkbox" id="zs-alle" ${alleAn ? "checked" : ""} onchange="zs.alle(this.checked)" aria-label="alle gefilterten auswählen" title="alle gefilterten auswählen">`,
          zelle: d => `<td class="haken" onclick="event.stopPropagation();zs.waehle(${d.id})"><input type="checkbox" ${Z.auswahl.has(d.id) ? "checked" : ""} aria-label="Dokument ${d.id} auswählen"></td>` },
        spalten: [
          { s: "id", titel: "Nr.", gesperrt: "Die Nummer des Dokuments lässt sich nicht ändern." },
          // lange Namen dürfen nach „_“ umbrechen, sonst wäre die Spalte so breit wie der Name
          { s: "dateiname", titel: "Datei", gesperrt: "Der Dateiname lässt sich nicht ändern.", stil: "min-width:170px",
            zeige: (w, d) => `<strong style="font-weight:600">${esc(w).replace(/_/g, "_<wbr>")}</strong>${lesbarkeit(d) ? `<div>${lesbarkeit(d)}</div>` : ""}` },
          { s: "hersteller", titel: "Hersteller", gesperrt: "Der Hersteller hängt am Teil. Sie ändern ihn unter „Teile“." },
          { s: "doc_type", titel: "Art", art: "auswahl", optionen: Object.entries(ARTEN), leer: false, stil: "min-width:150px" },
          { s: "ausstellungsdatum", titel: "Datum", art: "datum", stil: "white-space:nowrap" },
          // Stand mit den Prüfregeln darunter und dem Gebrauch, wenn das Dokument nicht mehr in
          // Gebrauch ist: eine Spalte statt drei, damit die Liste ohne seitliches Rollen passt.
          // Die Gründe im Einzelnen stehen auf der Seite des Dokuments.
          { s: "markierungsstand", titel: "Stand und Prüfregeln", gesperrt: `Den Stand ändern Sie mit den Knöpfen unten: markieren, bestätigen oder verwerfen. ${abgeleitet}`,
            wert: d => [STAND[d.markierungsstand]?.[0] || d.markierungsstand, ...d.regeln.map(x => r[x] || "Von Hand gemeldet"),
              ...(d.ablage === "aktuell" ? [] : [d.ablage_text || "Im Archiv"])].join(", "),
            zeige: (w, d) => pille(STAND, d.markierungsstand)
              + (d.regeln.length ? `<div class="zs-regeln">${d.regeln.map(x => esc(r[x] || "Von Hand gemeldet")).join("<br>")}</div>` : "")
              + (d.ablage === "aktuell" ? "" : `<div class="zs-regeln">${esc(d.ablage_text || "Im Archiv")}</div>`) },
          { s: "teile", titel: "Teile", wert: d => [...new Set(d.teile.map(t => t.artikelnr))].sort().join(", "), klasse: "mono", gesperrt: "Die Zuordnung ändern Sie auf der Seite des Dokuments." },
        ],
        // im schmalen Fenster stehen die beiden Links untereinander
        zusatz: { zelle: d => `<span class="zs-links"><a href="#/zertifikate/dok/${d.id}">Öffnen</a><span class="zs-punkt"> · </span>${d.im_browser === false
          ? `<a href="${esc(dateiUrl(d))}">Herunterladen</a>` : `<a href="${esc(dateiUrl(d))}" target="_blank" rel="noopener">${istPdf(d) ? "PDF" : "Ansehen"}</a>`}</span>` },
        notiz: "Grund für das Änderungsprotokoll, falls Sie einen festhalten möchten",
        folgen: () => ["Danach prüfen die Prüfregeln das Dokument neu, wenn es ein PDF ist. Dabei kann ein Verdacht dazukommen oder wegfallen."],
        speichern: (d, werte, { von, notiz }) => zapi(`/dokumente/${d.id}`, { method: "PATCH", json: { von, grund: notiz, ...werte } }),
        nachher: neu,
      })}</div>
      ${rest ? `<div class="zs-mehr"><button class="leise" onclick="zs.mehr(25)">Weitere ${Math.min(rest, 25)} anzeigen</button>
        ${rest > 25 ? `<button class="leise" onclick="zs.mehr(${rest})">Alle ${dok.length} anzeigen</button>` : ""}<span class="grau">Noch ${rest === 1 ? "1 Dokument" : `${rest} Dokumente`} nicht angezeigt.</span></div>` : ""}
      ${darfSchreiben() ? `<div class="tg-tipp">Klicken Sie in eine Zelle und tippen Sie los. Gespeichert wird erst, wenn Sie es bestätigen.</div>` : ""}` : `<div class="leer">Kein Dokument passt zu diesem Filter. Setzen Sie die Filter zurück, um alle zu sehen.</div>`;
    zeichneAuswahl();
  }

  // Leiste für die Markierung: nur, solange Dokumente ausgewählt sind
  function zeichneAuswahl() {
    const el = feld("zs-auswahl");
    if (!el) return;
    const n = Z.auswahl.size, versteckt = [...Z.auswahl].filter(id => !Z.sichtbar.includes(id)).length;
    el.hidden = !n;
    if (!n) { el.innerHTML = ""; return; }
    const grund = text("zs-grund"), sofort = feld("zs-sofort")?.checked;
    const aus = n && darfSchreiben() ? "" : n ? sperre() : 'disabled title="Bitte wählen Sie zuerst Dokumente aus."';
    el.innerHTML = `
      <div class="zeile" style="justify-content:space-between">
        <div><strong style="font-size:15px">${n} ausgewählt</strong>${versteckt ? ` <span class="grau">, davon ${versteckt} außerhalb des Filters</span>` : ""}
          <div class="beleg">Eine Markierung versteckt und löscht nichts. Bestätigen Sie einen Verdacht, gilt das Dokument als veraltet, und es wird Ersatz gesucht.</div></div>
        ${n ? `<button class="leise" onclick="zs.alle(false, true)">Auswahl aufheben</button>` : ""}
      </div>
      <div class="zeile" style="margin-top:8px">
        <input type="text" id="zs-grund" value="${esc(grund)}" placeholder="Grund, nötig beim Markieren und Verwerfen" style="flex:1;min-width:240px">
        <label class="zs-haken"><input type="checkbox" id="zs-sofort" ${sofort ? "checked" : ""}> beim Markieren gleich bestätigen</label>
      </div>
      <div class="zeile" style="margin-top:10px">
        <button class="leise" onclick="zs.markieren()" ${aus}>Als veraltet markieren</button>
        <button onclick="zs.bestaetigen()" ${aus}>Als veraltet bestätigen</button>
        <button class="leise" onclick="zs.verwerfen()" ${aus}>Verdacht verwerfen</button>
        ${nameHinweis()}
      </div>`;
  }

  function waehle(id) {
    if (Z.auswahl.has(id)) Z.auswahl.delete(id); else Z.auswahl.add(id);
    const zeile = document.querySelector(`[data-tg="zs-dokumente"] tr[data-z="${id}"]`);
    if (zeile) {
      zeile.classList.toggle("gewaehlt", Z.auswahl.has(id));
      zeile.querySelector("td.haken input").checked = Z.auswahl.has(id);
    }
    if (feld("zs-alle")) feld("zs-alle").checked = Z.sichtbar.length > 0 && Z.sichtbar.every(x => Z.auswahl.has(x));
    zeichneAuswahl();
  }

  function alle(an, wirklichAlle) {
    if (wirklichAlle) Z.auswahl.clear();
    else for (const id of Z.sichtbar) an ? Z.auswahl.add(id) : Z.auswahl.delete(id);
    zeichneListe();
  }

  function filter(name, w) { Z.filter[name] = w; Z.anzahl = 25; zeichneDokumente(); }
  function kachel(f) {
    Z.filter = { hersteller: "", stand: "", regel: "", ablage: "", lesbar: "", verknuepft: "", familie: "", herkunft: "", ausgemustert: false, ...f };
    Z.anzahl = 25;
    if (feld("zs-liste")) zeichneDokumente();
  }
  function mehr(n) { Z.anzahl += n; zeichneListe(); }

  const ohneVerdacht = a => a.ohne_markierung?.length ? `. Bei ${a.ohne_markierung.length} davon gab es keinen offenen Verdacht` : "";
  // ids: Auswahl der Liste oder das eine Dokument der Detailseite; Grund aus dem Feld daneben
  function markierung(aktion, ids, grundFeld, sofort) {
    const grund = text(grundFeld);
    if (!ids.length) return toast("Bitte wählen Sie zuerst Dokumente aus.");
    if (aktion !== "bestaetigen" && !grund) { feld(grundFeld)?.focus(); return toast("Bitte geben Sie einen Grund an."); }
    const n = ids.length === 1 ? "1 Dokument" : `${ids.length} Dokumente`;
    const leeren = () => { if (feld(grundFeld)) feld(grundFeld).value = ""; };
    if (aktion === "markieren") return schreibe(von => post("/markierungen", { dokument_ids: ids, von, grund, bestaetigen: !!sofort }).then(a => { leeren(); return a; }),
      `${n} als möglicherweise veraltet markiert${sofort ? " und gleich als veraltet bestätigt. Jetzt wird Ersatz gesucht" : ""}.`);
    if (aktion === "bestaetigen") return schreibe(von => post("/markierungen/bestaetigen", { dokument_ids: ids, von, grund }).then(a => { leeren(); return a; }),
      a => `${n} als veraltet bestätigt. Jetzt wird Ersatz gesucht${ohneVerdacht(a)}.`);
    return schreibe(von => post("/markierungen/verwerfen", { dokument_ids: ids, von, grund }).then(a => { leeren(); return a; }),
      a => `Der Verdacht ist für ${n} verworfen${ohneVerdacht(a)}.`);
  }

  // ---------- Dokument ----------

  async function ladeDokument(id) {
    if (Z.dok?.id !== id) { Z.dok = null; Z.pdfSeite = 0; }
    const d = await zapi(`/dokumente/${id}`);
    Z.dokTeile = await zapi(`/teile?hersteller=${encodeURIComponent(d.hersteller)}`).catch(() => []);
    Z.dok = d;
  }

  const seitenKnopf = s => s ? `<button class="zs-seite" onclick="zs.pdfSeite(${+s})" title="PDF auf dieser Seite zeigen">Seite ${+s}</button>` : "–";

  function protokoll(eintraege) {
    if (!eintraege.length) return leerZeile("Bisher hat niemand etwas von Hand geändert.");
    // Texte kommen vom Archiv (feld_text, alt_text, neu_text); ältere Antworten: Feldname übersetzt
    const was = a => a.feld_text || FELDNAME[a.feld] || a.feld;
    const vorher = a => a.alt_text ?? (esc(wert(a.alt)) || "leer"), nachher = a => a.neu_text ?? (esc(wert(a.neu)) || "leer");
    return `<div class="beleg">${GESPERRT} Das Protokoll lässt sich nicht ändern.</div>
      <div class="tabelle" title="Das Protokoll lässt sich nicht ändern."><table><thead><tr><th>Wann</th><th>Wer</th><th>Was</th><th>Vorher</th><th>Nachher</th></tr></thead><tbody>
      ${eintraege.map(a => `<tr><td style="white-space:nowrap">${zeit(a.am)}</td><td>${esc(a.von)}</td>
        <td>${esc(a.objekt_text || a.objekt)}: ${esc(was(a))}${neben(a.grund)}</td>
        <td>${esc(vorher(a))}</td><td>${esc(nachher(a))}</td></tr>`).join("")}</tbody></table></div>`;
  }

  // alle Versionen eines Teils, je Dokumentart die neueste oben, mit Link auf jede PDF
  function versionen(t, eigenesDok) {
    if (!t.historie.length) return leerZeile("Diesem Teil ist noch kein Dokument zugeordnet.");
    return `<div class="beleg">${GESPERRT} Versionen entstehen durch Zuordnen, Hochladen, Beenden und Umhängen. Die Tabelle selbst lässt sich nicht ändern.</div>
      <div class="tabelle" title="Die Versionen lassen sich hier nicht von Hand ändern."><table><thead><tr><th>Art</th><th>Version</th><th>Stand</th><th>Dokument</th><th>Gültig ab</th><th>Hinweis</th></tr></thead><tbody>
      ${t.historie.map(v => `<tr ${v.dokument_id === eigenesDok ? 'style="background:var(--limette-hell)"' : ""}>
        <td>${esc(art(v.doc_type))}</td><td>${v.version}</td><td>${pille(VERSION, v.stand)}</td>
        <td><a href="${esc(dateiUrl(v))}" ${v.dateiart && v.dateiart !== "pdf" ? "" : 'target="_blank" rel="noopener"'}>${esc(v.dateiname)}</a>
          <div class="beleg">${v.dokument_id === eigenesDok ? "dieses Dokument" : `<a href="#/zertifikate/dok/${v.dokument_id}">Dokument ${v.dokument_id}</a>`} · ${esc(v.ablage_text || (v.ablage === "archiv" ? "Im Archiv" : "In Gebrauch"))} · ${esc((STAND[v.markierungsstand] || [v.markierungsstand])[0])}</div></td>
        <td>${datum(v.gueltig_ab)}${v.abgeloest_am ? neben(`bis ${datum(v.abgeloest_am)}`) : ""}</td>
        <td>${esc(v.beendet_grund || v.hinweis || "")}${neben(v.beleg)}${v.quellennachweis ? neben(`Herkunft dieser Version: ${v.quellennachweis.text}`) : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function zeichneDokument() {
    const d = Z.dok, w = d.werte;
    const offen = d.markierungen.some(m => m.status === "verdacht");
    const eigene = d.teile_historie.flatMap(t => t.historie.filter(v => v.dokument_id === d.id));
    const zuordenbar = Object.keys(ARTEN).filter(a => a !== "unbekannt");
    const sprachen = [...new Set([...SPRACHEN, ...w.sprachen])].sort();
    const haken = (gruppe, werte, gesetzt) => werte.map(x => `<label class="zs-haken" style="margin-right:12px"><input type="checkbox" name="${gruppe}" value="${esc(x)}" ${gesetzt.includes(x) ? "checked" : ""}> ${esc(x)}</label>`).join("");
    const teilWahl = (id, ohne) => `<select id="${id}">${optionen(Z.dokTeile.filter(t => t.id !== ohne).map(t => [t.id, `${t.artikelnr} (Teil ${t.id})`]))}</select>`;

    $("#app").innerHTML = rahmen("dokumente", `
      <div class="karte">
        <div class="zeile" style="justify-content:space-between;align-items:flex-start">
          <div><h1 style="font-size:18px;word-break:break-word">${esc(d.dateiname)}</h1>
            <div class="grau">Dokument ${d.id} · ${esc(d.hersteller)} · ${esc(art(d.doc_type))} · ${esc(d.dateiart_text || "PDF")}${d.ausgemustert ? ` · <span class="unbelegt">Im Archiv abgelegt von ${esc(d.ausgemustert_von)}. Grund: ${esc(d.ausgemustert_grund)}</span>` : ""}</div></div>
          <div class="zeile">${pille(STAND, d.markierungsstand)}
            ${d.im_browser === false ? "" : `<button class="leise" onclick="zs.pdfAuf()">${Z.pdfOffen ? "Ansicht schließen" : "Datei ansehen"}</button>
            <a class="knopf leise" href="${esc(dateiUrl(d))}" target="_blank" rel="noopener">In neuem Tab öffnen</a>`}
            <a class="knopf leise" href="${esc(dateiUrl(d))}?download=1">Datei herunterladen</a></div>
        </div>
        ${d.im_browser === false ? `<div class="hinweis" style="margin-top:10px">Diese Datei zeigt der Browser nicht an. Laden Sie sie herunter, um sie zu öffnen.</div>`
          : `<div id="zs-pdf">${Z.pdfOffen ? pdfRahmen() : ""}</div>`}

        <div class="raster" style="margin-top:12px">
          <div>
            <div class="feld"><span class="grau">Dokumentart</span><span>${esc(art(d.doc_type))}</span></div>
            <div class="feld"><span class="grau">Ausstellungsdatum</span><span>${datum(d.ausstellungsdatum) || "–"}</span></div>
            <div class="feld"><span class="grau">Gültig bis</span><span>${datum(d.gueltig_bis) || "–"}</span></div>
            ${istPdf(d) ? `<div class="feld"><span class="grau">Seiten</span><span>${d.seiten}${lesbarkeit(d) ? ` · ${lesbarkeit(d)}` : ""}${d.lesbar && d.seiten_ohne_text.length ? ` · Seiten ohne Text: ${esc(d.seiten_ohne_text.join(", "))}` : ""}${neben(d.ocr_hinweis)}</span></div>`
              : `<div class="feld"><span class="grau">Datei</span><span>${esc(d.dateiart_text || "")}, ${Math.max(1, Math.round((d.groesse || 0) / 1024))} KB</span></div>`}
          </div>
          <div>
            <div class="feld"><span class="grau">Gebrauch</span><span>${esc(d.ablage_text || d.ablage)}</span></div>
            <div class="feld"><span class="grau">Angelegt</span><span>von ${esc(d.angelegt_von)} am ${zeit(d.angelegt_am)}</span></div>
            ${d.altpfade.length ? `<div class="feld"><span class="grau">Im übernommenen Bestand unter</span><span class="mono" style="word-break:break-all">${esc(dateiLesbar(d.altpfade.join(", ")))}</span></div>` : ""}
            ${istPdf(d) ? `<div class="feld"><span class="grau">Prüfregeln zuletzt angewendet</span><span>${zeit(d.geprueft_am) || "–"}</span></div>` : ""}
          </div>
        </div>
      </div>

      ${herkunftKarte(d)}

      <div class="karte">
        <h2>Verdacht und Gründe</h2>
        ${(d.hinweise || []).map(h => `<p class="grau" style="margin:0 0 8px">${esc(h)}</p>`).join("")}
        ${istPdf(d) ? "" : `<p class="grau" style="margin:0 0 8px">Für diese Datei laufen keine Prüfregeln, weil das Archiv aus ihr nichts liest. Sie können sie trotzdem von Hand als veraltet melden.</p>`}
        ${d.markierungen.length ? `<div class="beleg">${GESPERRT} Der Verlauf lässt sich nicht ändern. Neue Markierungen setzen Sie mit den Knöpfen darunter.</div>
          <div class="tabelle" title="Der Verlauf der Markierungen lässt sich nicht ändern."><table><thead><tr><th>Regel</th><th>Grund</th><th>Seite</th><th>Stand</th><th>Wer</th></tr></thead><tbody>
          ${d.markierungen.map(m => `<tr><td>${esc(m.regel_titel)}${neben(m.stufe_text || m.stufe)}</td>
            <td>${esc(m.grund)}${m.textstelle ? `<div class="beleg">„${esc(m.textstelle)}“</div>` : ""}</td>
            <td>${seitenKnopf(m.seite)}</td><td>${pille(STAND, m.status)}</td>
            <td>${esc(m.entschieden_von || m.erstellt_von)}${neben(m.entscheidung_grund)}${neben(zeit(m.entschieden_am || m.erstellt_am))}</td></tr>`).join("")}</tbody></table></div>`
          : leerZeile(istPdf(d) ? "Keine Prüfregel meldet einen Verdacht, und niemand hat das Dokument markiert." : "Niemand hat diese Datei als veraltet gemeldet.")}
        <div class="zeile nur-schreiben" style="margin-top:12px">
          <input type="text" id="zs-dgrund" placeholder="Grund, nötig beim Markieren und Verwerfen" style="flex:1;min-width:240px">
          <label class="zs-haken"><input type="checkbox" id="zs-dsofort"> gleich bestätigen</label>
        </div>
        <div class="zeile nur-schreiben" style="margin-top:10px">
          <button class="leise" onclick="zs.dokMarkierung('markieren')" ${sperre()}>Als veraltet markieren</button>
          ${offen ? `<button onclick="zs.dokMarkierung('bestaetigen')" ${sperre()}>Als veraltet bestätigen</button>` : ""}
          ${d.markierungen.some(m => m.status !== "verworfen") ? `<button class="leise" onclick="zs.dokMarkierung('verwerfen')" ${sperre()}>Verdacht verwerfen</button>` : ""}
          ${nameHinweis()}
        </div>
      </div>

      <div class="karte">
        <h2>${istPdf(d) ? "Angaben aus dem PDF" : "Angaben"}</h2>
        ${!istPdf(d) ? `<p class="grau" style="margin:0 0 8px">${esc(d.auslesbar_grund || "")}</p>` : ""}
        ${d.angaben.length ? `<div class="beleg">${GESPERRT} ${istPdf(d) ? "So wurde es aus dem PDF gelesen." : "Diese Angaben hat jemand von Hand eingetragen."} Korrigieren können Sie die Werte unten unter „Werte“.</div>
          <div class="tabelle" title="Diese Tabelle lässt sich nicht ändern. Korrigieren Sie die Werte darunter."><table><thead><tr><th>Angabe</th><th>Wert</th><th>Seite</th><th>Beleg</th></tr></thead><tbody>
          ${d.angaben.map(a => `<tr><td>${esc(a.art_text || a.art)}</td><td>${esc(a.art === "dokumentart" ? art(a.wert) : a.wert)}${a.sicher ? "" : ' <span class="unbelegt">unsicher gelesen</span>'}${a.quelle === "manuell" ? ' <span class="chip s-suche">von Hand</span>' : a.quelle === "ocr" ? ' <span class="chip s-zu_pruefen" title="Dieser Wert stammt aus der Texterkennung eines Scans. Bitte prüfen Sie ihn im PDF.">Texterkennung</span>' : ""}</td>
            <td>${seitenKnopf(a.seite)}</td><td>${a.textstelle ? `„${esc(a.textstelle)}“` : ""}${neben(a.hinweis)}</td></tr>`).join("")}</tbody></table></div>`
          : !istPdf(d) ? leerZeile("Noch hat niemand Angaben eingetragen. Tragen Sie sie unten unter „Werte“ ein.")
          : leerZeile("Aus dem PDF ließ sich keine Angabe lesen" + (d.lesbar ? "." : ", denn es ist ein Scan ohne lesbaren Text." + (d.ocr_hinweis ? " " + d.ocr_hinweis : "")))}

        <h3>Werte</h3>
        ${werteTabelle(d)}
      </div>

      <div class="karte">
        <h2>Teile und Versionen</h2>
        ${d.teile_historie.length ? d.teile_historie.map(t => `
          <h3><a href="#/zertifikate/teile/${t.id}">${esc(t.hersteller)} ${esc(t.artikelnr)}</a>${t.profile_no ? ` <span class="grau" style="font-weight:400">· PRO.FILE ${esc(t.profile_no)}</span>` : ""}</h3>
          ${versionen(t, d.id)}`).join("") : leerZeile("Das Dokument gehört noch zu keinem Teil.")}

        <details class="nur-schreiben" style="margin-top:12px"><summary>Einem Teil als neue Version zuordnen</summary>
          ${Z.dokTeile.length ? `<div class="zs-gitter">
            <div><label for="zs-zteil">Teil</label>${teilWahl("zs-zteil")}</div>
            <div><label for="zs-zart">Dokumentart</label><select id="zs-zart">${optionen(zuordenbar.map(a => [a, art(a)]), d.doc_type)}</select></div>
            <div><label for="zs-znotiz">Hinweis, falls nötig</label><input type="text" id="zs-znotiz"></div></div>
            <div class="zeile" style="margin-top:10px"><button onclick="zs.zuordnen()" ${sperre()}>Zuordnen</button>${nameHinweis()}</div>`
          : `<p class="grau" style="margin:0">Für diesen Hersteller gibt es noch kein Teil. Legen Sie es unter <a href="#/zertifikate/teile">Teile</a> an.</p>`}
        </details>

        ${eigene.length ? `<details class="nur-schreiben"><summary>Version beenden, umhängen oder wiederherstellen</summary>
          <label for="zs-vgrund">Grund, nötig beim Beenden</label><input type="text" id="zs-vgrund">
          ${Z.dokTeile.length > 1 ? `<label for="zs-ziel">Umhängen zu</label>${teilWahl("zs-ziel")}` : ""}
          <table style="margin-top:8px"><tbody>${eigene.map(v => `<tr>
            <td><strong>${esc(v.artikelnr)}</strong>: ${esc(art(v.doc_type))}, Version ${v.version} ${pille(VERSION, v.stand)}</td>
            <td style="text-align:right;white-space:nowrap">${v.stand === "aktuell" ? `
              ${Z.dokTeile.some(t => t.id !== v.teil_id) ? `<button class="leise" onclick="zs.umhaengen(${v.id}, ${v.teil_id})" ${sperre()}>Version umhängen</button>` : ""}
              <button class="gefahr" onclick="zs.beenden(${v.id})" ${sperre()}>Version beenden</button>`
              : `<button class="leise" onclick="zs.versionWieder(${v.id})" ${sperre()}>Version wieder aktuell machen</button>`}</td></tr>`).join("")}</tbody></table>
          ${nameHinweis()}
        </details>` : ""}

        <details class="nur-schreiben"><summary>${d.ausgemustert ? "Wieder in Gebrauch nehmen" : "Ins Archiv legen statt löschen"}</summary>
          <p class="grau" style="margin:0 0 6px">Dabei wird nichts gelöscht. Die Datei ist danach nicht mehr in Gebrauch, ihre aktuellen Versionen enden, und ihr Link bleibt erhalten. Sie können sie jederzeit wieder in Gebrauch nehmen.</p>
          <label for="zs-ausgrund">Grund${d.ausgemustert ? ", falls nötig" : ""}</label><input type="text" id="zs-ausgrund">
          <div class="zeile" style="margin-top:10px">${d.ausgemustert
            ? `<button onclick="zs.dokWieder()" ${sperre()}>Dokument wieder in Gebrauch nehmen</button>`
            : `<button class="gefahr" onclick="zs.ausmustern()" ${sperre()}>Dokument ins Archiv legen</button>`}${nameHinweis()}</div>
        </details>
      </div>

      <div class="karte"><h2>Änderungsprotokoll</h2>${protokoll(d.aenderungen)}</div>`,
      `<a href="#/zertifikate" class="grau" style="text-decoration:none">← Dokumente</a>`);
  }

  const pdfRahmen = () => `<iframe class="pdf" title="Ansicht der Datei" src="${esc(dateiUrl(Z.dok))}${Z.pdfSeite ? `#page=${Z.pdfSeite}` : ""}"></iframe>`;

  // ---------- Herkunft (Quellennachweis) ----------

  // Karte mit dem Quellennachweis des Dokuments; die Felder lassen sich in einer Tabelle
  // korrigieren (PATCH /dokumente/<id> mit quelle_*), jede Änderung steht im Protokoll.
  function herkunftKarte(d) {
    const q = d.quellennachweis;
    if (!q) return "";
    const archiv = location.origin + "/zert" + q.archiv_link;
    const zeilen = [
      { feld: "quelle_art", titel: "Herkunft", wert: q.art || "", art: "auswahl", optionen: Object.entries(HERKUNFT) },
      { feld: "quelle_link", titel: "Ursprünglicher Download-Link", wert: q.link || "", art: "text" },
      { feld: "quelle_seite", titel: "Gefunden auf der Webseite", wert: q.seite_gefunden_auf || "", art: "text" },
      { feld: "quelle_abgerufen_am", titel: "Abgerufen am", wert: (q.abgerufen_am || "").slice(0, 10), art: "datum" },
      { feld: "quelle_von", titel: "Übernommen von", wert: q.uebernommen_von || "", art: "text" },
      { feld: "quelle_dateiname", titel: "Ursprünglicher Dateiname", wert: q.dateiname || "", art: "text" },
      { feld: "quelle_hinweis", titel: "Hinweis zur Herkunft", wert: q.hinweis || "", art: "text" },
    ];
    const zeigen = (v, z) => {
      if (!v) return z.feld === "quelle_seite" && q.art === "internet" ? '<span class="grau">nicht bekannt</span>' : '<span class="grau">–</span>';
      if (z.feld === "quelle_art") return esc(HERKUNFT[v] || v);
      if (z.feld === "quelle_link" || z.feld === "quelle_seite") return webLink(v);
      if (z.feld === "quelle_abgerufen_am") return esc(q.abgerufen_am && q.abgerufen_am.length > 10 && q.abgerufen_am.slice(0, 10) === v ? zeit(q.abgerufen_am) : datum(v));
      if (z.feld === "quelle_dateiname") return esc(dateiLesbar(v));  // gespeichert bleibt der Name, wie er kam
      return esc(v);
    };
    return `<div class="karte zs-herkunft">
        <h2>Herkunft</h2>
        <p style="margin:0 0 8px">${esc(q.text)}</p>
        ${Tabelle.html({
          id: `zs-herkunft-${d.id}`, titel: `Herkunft von Dokument ${d.id}`, zeilen, schluessel: z => z.feld, name: z => z.titel,
          spalten: [
            { s: "titel", titel: "Angabe", gesperrt: "Das ist die Bezeichnung der Angabe.", still: true, klasse: "grau", stil: "width:260px" },
            { s: "wert", titel: "Wert", art: z => z.art, optionen: z => z.optionen || [], leer: z => z.feld !== "quelle_art", zeige: zeigen },
          ],
          notiz: "Grund, falls Sie einen festhalten möchten, zum Beispiel: Link beim Hersteller nachgesehen",
          folgen: () => ["Die Änderung steht mit Ihrem Namen im Änderungsprotokoll. Links müssen mit http:// oder https:// beginnen."],
          speichernAlle: async (offen, { von, notiz }) => {
            const daten = { von, grund: notiz };
            for (const { z, werte } of offen) daten[z.feld] = werte.wert || null;
            await zapi(`/dokumente/${d.id}`, { method: "PATCH", json: daten });
          },
          nachher: neu,
        })}
        <div class="feld" style="margin-top:10px"><span class="grau">Angelegt im Archiv</span><span>${zeit(q.uebernommen_am)}</span></div>
        <div class="feld"><span class="grau">Link auf die Datei im Archiv</span><span>${webLink(archiv)}${neben("Dieser Link bleibt gültig, auch wenn das Dokument ersetzt oder ins Archiv gelegt wird. Wer ihn öffnet, muss angemeldet sein.")}</span></div>
        <div class="feld"><span class="grau" title="Ein Fingerabdruck der Datei. Gleiche Prüfsumme heißt: genau dieselbe Datei.">Prüfsumme der Datei</span><span class="mono zs-sha">${esc(q.sha256)}</span></div>
      </div>`;
  }
  function pdfAuf() { Z.pdfOffen = !Z.pdfOffen; zeichneDokument(); }
  function pdfSeite(s) {
    Z.pdfOffen = true; Z.pdfSeite = s;
    zeichneDokument();
    feld("zs-pdf")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Werte eines Dokuments: eine Zeile je Feld aus WERTE_FELDER, dazu je Wert die Seite als Beleg.
  // Gespeichert wird alles in einem Aufruf (die Regeln prüfen danach einmal neu).
  function werteTabelle(d) {
    const w = d.werte;
    const seiteSpalte = istPdf(d);  // zu anderen Dateien gibt es keine Seiten
    const zeilen = WERTE_FELDER.filter(([f]) => f in w || f === "doc_type").map(([f, titel, art_]) => ({
      feld: f, titel, art: art_ === "liste" ? "text" : art_, liste: art_ === "liste",
      optionen: f === "doc_type" ? Object.entries(ARTEN) : [], leer: f !== "doc_type",
      wert: art_ === "liste" ? (w[f] || []).join(", ") : (w[f] ?? ""), seite: "" }));
    return Tabelle.html({
      id: `zs-werte-${d.id}`, titel: `Werte von Dokument ${d.id}`, zeilen, schluessel: z => z.feld, name: z => z.titel.replace(/,.*$/, ""),
      spalten: [
        { s: "titel", titel: "Angabe", gesperrt: "Das ist die Bezeichnung der Angabe.", still: true, klasse: "grau", stil: "width:260px" },
        { s: "wert", titel: "Wert", art: z => z.art, optionen: z => z.optionen, leer: z => z.leer,
          zeige: (v, z) => !v ? '<span class="grau">–</span>' : z.feld === "doc_type" ? esc(art(v)) : z.art === "datum" ? esc(datum(v)) : esc(v) },
        ...(seiteSpalte ? [{ s: "seite", titel: "Seite (Beleg, optional)", kopfTitel: `Auf welcher Seite des PDFs steht der neue Wert? Geben Sie eine Zahl von 1 bis ${Math.max(d.seiten, 1)} an. Das gilt nur zusammen mit einem neuen Wert.`,
          stil: "width:120px", zeige: v => v ? `S. ${esc(v)}` : '<span class="grau">–</span>' }] : []),
      ],
      notiz: "Grund, falls Sie einen festhalten möchten, zum Beispiel: Seite 2 falsch gelesen",
      folgen: (z, werte) => [
        "wert" in werte ? "Der neue Wert gilt als von Hand gesetzt, mit Ihrem Namen, und ersetzt den gelesenen." : "Eine Seite allein, ohne neuen Wert, ändert nichts.",
        istPdf(d) ? "Danach prüfen die Prüfregeln das Dokument neu. Dabei kann ein Verdacht dazukommen oder wegfallen." : "Für diese Datei laufen keine Prüfregeln.",
      ],
      speichernAlle: async (offen, { von, notiz }) => {
        const daten = { von, grund: notiz }, seiten = {};
        for (const { z, werte } of offen) {
          if ("wert" in werte) daten[z.feld] = z.liste ? liste(werte.wert) : (werte.wert || null);
          const s = parseInt(werte.seite, 10);
          if ("wert" in werte && s) seiten[z.feld] = s;
        }
        if (Object.keys(seiten).length) daten.seiten = seiten;
        await zapi(`/dokumente/${d.id}`, { method: "PATCH", json: daten });
      },
      nachher: neu,
    });
  }

  function zuordnen() {
    const d = Z.dok, teilId = +feld("zs-zteil").value;
    const t = Z.dokTeile.find(x => x.id === teilId);
    schreibe(von => post(`/teile/${teilId}/verknuepfungen`, { dokument_id: d.id, von, doc_type: feld("zs-zart").value, hinweis: text("zs-znotiz") }),
      `Dokument ${d.id} ist neue Version für ${t.artikelnr}.`);
  }
  function beenden(vId) {
    const grund = text("zs-vgrund");
    if (!grund) { feld("zs-vgrund").focus(); return toast("Bitte geben Sie einen Grund an."); }
    schreibe(von => post(`/verknuepfungen/${vId}/beenden`, { von, grund }), "Die Version ist beendet.");
  }
  function umhaengen(vId, vonTeil) {
    const ziel = +feld("zs-ziel").value;
    if (ziel === vonTeil) return toast("Das Dokument hängt schon an diesem Teil. Bitte wählen Sie ein anderes.");
    const t = Z.dokTeile.find(x => x.id === ziel);
    schreibe(von => post(`/verknuepfungen/${vId}/umhaengen`, { teil_id: ziel, von, grund: text("zs-vgrund") }),
      `Das Dokument ${Z.dok.id} hängt jetzt an ${t.artikelnr}.`);
  }
  const versionWieder = vId => schreibe(von => post(`/verknuepfungen/${vId}/wiederherstellen`, { von, grund: text("zs-vgrund") }), "Die Version ist wieder aktuell.");
  function ausmustern() {
    const grund = text("zs-ausgrund");
    if (!grund) { feld("zs-ausgrund").focus(); return toast("Bitte geben Sie einen Grund an."); }
    schreibe(von => post(`/dokumente/${Z.dok.id}/ausmustern`, { von, grund }), `Das Dokument ${Z.dok.id} liegt jetzt im Archiv und ist nicht mehr in Gebrauch. Gelöscht ist nichts.`);
  }
  const dokWieder = () => schreibe(von => post(`/dokumente/${Z.dok.id}/wiederherstellen`, { von, grund: text("zs-ausgrund") }), `Das Dokument ${Z.dok.id} ist wieder in Gebrauch.`);

  // ---------- Teile ----------

  async function ladeTeile() {
    const s = Z.teilSuche, q = new URLSearchParams();
    if (s.hersteller) q.set("hersteller", s.hersteller);
    if (s.artikelnr) q.set("artikelnr", s.artikelnr);
    Z.teile = await zapi(`/teile${q.toString() ? "?" + q : ""}`);
  }

  function zeichneTeile() {
    const s = Z.teilSuche;
    $("#app").innerHTML = rahmen("teile", `
      <div class="karte">
        <div class="zs-filter">
          <div style="flex:1;min-width:200px"><label for="zs-th">Hersteller</label><input type="text" id="zs-th" list="zs-hersteller" value="${esc(s.hersteller)}" placeholder="z. B. Pepperl+Fuchs" oninput="zs.teilSuche()"></div>
          <div style="flex:1;min-width:200px"><label for="zs-tn">Hersteller-Artikelnr. / Typ</label><input type="text" id="zs-tn" value="${esc(s.artikelnr)}" placeholder="z. B. NJ50-…-N" oninput="zs.teilSuche()"></div>
        </div>
        <datalist id="zs-hersteller">${Z.hersteller.map(h => `<option value="${esc(h.name)}">`).join("")}</datalist>
        <div id="zs-teile"></div>
      </div>
      <div class="karte nur-schreiben">
        <details style="border:none;padding:0"><summary>Neues Teil anlegen</summary>
          <div class="zs-gitter">
            <div><label for="zs-nh">Hersteller</label><input type="text" id="zs-nh" list="zs-hersteller" placeholder="vorhandenen wählen oder neuen eintragen"></div>
            <div><label for="zs-nn">Hersteller-Artikelnr. / Typ</label><input type="text" id="zs-nn"></div>
            <div><label for="zs-np">PRO.FILE-Nr., falls bekannt</label><input type="text" id="zs-np"></div>
            <div><label for="zs-nb">Bezeichnung, falls bekannt</label><input type="text" id="zs-nb"></div>
          </div>
          <div class="zeile" style="margin-top:10px"><button onclick="zs.teilAnlegen()" ${sperre()}>Teil anlegen</button>${nameHinweis()}</div>
        </details>
      </div>`);
    zeichneTeilListe();
  }

  function zeichneTeilListe() {
    const el = feld("zs-teile");
    if (!el) return;
    const teile = Z.teile || [];
    el.innerHTML = `<div class="grau" style="font-size:12.5px;margin-bottom:6px">${teile.length === 1 ? "1 Teil" : `${teile.length} Teile`}.${Z.teilSuche.artikelnr ? " Bei „teilweise“ steckt eine Typbezeichnung in der anderen. Bitte prüfen Sie, ob es wirklich dasselbe Teil ist." : ""}</div>
      ${teile.length ? `<div>${teilTabelle(teile)}</div>` : `<div class="leer">Kein Teil passt zu Ihrer Suche.</div>`}`;
  }

  const TEIL_FELDER = [["hersteller", "Hersteller"], ["artikelnr", "Hersteller-Artikelnr. / Typ"], ["profile_no", "PRO.FILE-Nr."], ["bezeichnung", "Bezeichnung"]];
  const teilFolgen = werte => [
    ...("hersteller" in werte ? ["Ist der Hersteller noch unbekannt, wird er neu angelegt."] : []),
    ...("artikelnr" in werte ? ["Die Suche findet das Teil danach unter dem neuen Typ."] : []),
  ];
  const teilSpeichern = (id, werte, { von, notiz }) => zapi(`/teile/${id}`, { method: "PATCH", json: { von, grund: notiz, ...werte } });
  function teilTabelle(teile) {
    const abgeleitet = "Das ergibt sich aus den Versionen und lässt sich nicht von Hand ändern.";
    return Tabelle.html({
      id: "zs-teile", rahmenKlasse: "zs-liste", titel: "Teile", zeilen: teile, name: t => `Teil ${t.id} (${t.artikelnr})`, hinweis: false,
      spalten: [
        { s: "id", titel: "Nr.", gesperrt: "Die Nummer des Teils lässt sich nicht ändern." },
        ...TEIL_FELDER.map(([f, titel]) => ({ s: f, titel: f === "artikelnr" ? "Typ" : titel, wert: t => t[f] || "", klasse: f === "artikelnr" ? "mono" : "" })),
        ...(Z.teilSuche.artikelnr ? [{ s: "treffer", titel: "Treffer", gesperrt: "Das ist das Ergebnis Ihrer Suche.", zeige: w => w === "teilweise" ? '<span class="chip s-zu_pruefen">teilweise</span>' : esc(w || "") }] : []),
        { s: "aktuell", titel: "Aktuelle Dokumente", wert: t => t.aktuell.length, gesperrt: abgeleitet },
        { s: "historie", titel: "Versionen", wert: t => t.historie.length, gesperrt: abgeleitet },
      ],
      zusatz: { zelle: t => `<a href="#/zertifikate/teile/${t.id}">Öffnen</a>` },
      notiz: "Grund für das Änderungsprotokoll, falls Sie einen festhalten möchten",
      folgen: (t, werte) => teilFolgen(werte),
      speichern: (t, werte, opt) => teilSpeichern(t.id, werte, opt),
      nachher: async () => { await ladeTeile(); zeichneTeilListe(); },
    });
  }

  let teilTimer = null;
  function teilSuche() {
    Z.teilSuche = { hersteller: text("zs-th"), artikelnr: text("zs-tn") };
    teilTimer?.stopp();
    teilTimer = seiteTimeout(async () => {
      const stand = JSON.stringify(Z.teilSuche);
      try { await ladeTeile(); } catch (f) { return toast(`Die Teile ließen sich nicht laden. ${f.message}`); }
      if (stand === JSON.stringify(Z.teilSuche)) zeichneTeilListe();
    }, 250);
  }

  function teilAnlegen() {
    const daten = { hersteller: text("zs-nh"), artikelnr: text("zs-nn"), profile_no: text("zs-np"), bezeichnung: text("zs-nb") };
    if (!daten.hersteller || !daten.artikelnr) return toast("Bitte tragen Sie Hersteller und Artikelnummer ein.");
    schreibe(von => post("/teile", { ...daten, von }), `Das Teil ${daten.artikelnr} ist angelegt.`, t => { Z.bleibt = true; location.hash = `#/zertifikate/teile/${t.id}`; });
  }

  function zeichneTeil() {
    const t = Z.teil;
    $("#app").innerHTML = rahmen("teile", `
      <div class="karte">
        <h1 style="font-size:18px">${esc(t.hersteller)} <span class="mono" style="font-size:17px">${esc(t.artikelnr)}</span></h1>
        <div class="grau" style="margin-bottom:12px">Teil ${t.id} · PRO.FILE ${esc(t.profile_no || "–")}${t.bezeichnung ? ` · ${esc(t.bezeichnung)}` : ""} · angelegt von ${esc(t.angelegt_von)} am ${zeit(t.angelegt_am)}</div>
        <h2>Versionen</h2>
        ${versionen(t)}

        <h2 style="margin-top:16px">Angaben</h2>
        ${Tabelle.html({
          id: `zs-teil-${t.id}`, titel: `Teil ${t.id}`, schluessel: z => z.feld, name: z => z.titel,
          zeilen: TEIL_FELDER.map(([f, titel]) => ({ feld: f, titel, wert: t[f] || "" })),
          spalten: [
            { s: "titel", titel: "Angabe", gesperrt: "Das ist die Bezeichnung der Angabe.", still: true, klasse: "grau", stil: "width:220px" },
            { s: "wert", titel: "Wert" },
          ],
          notiz: "Grund für das Änderungsprotokoll, falls Sie einen festhalten möchten",
          folgen: (z, werte) => teilFolgen({ [z.feld]: werte.wert }),
          speichernAlle: (offen, opt) => teilSpeichern(t.id, Object.fromEntries(offen.map(({ z, werte }) => [z.feld, werte.wert])), opt),
          nachher: neu,
        })}

        <details class="nur-schreiben"><summary>Neue Version hochladen</summary>
          <p class="grau" style="margin:0 0 6px">Die neue Version löst die aktuelle derselben Dokumentart ab. Die alte kommt ins Archiv, ihr Link bleibt erhalten. Andere Dokumentarten des Teils bleiben aktuell.</p>
          <div class="zs-gitter">
            ${dateiFelder("zs-h", true)}
            <div><label for="zs-hnotiz">Hinweis, falls nötig</label><input type="text" id="zs-hnotiz"></div>
          </div>
          <div class="zeile" style="margin-top:10px"><button onclick="zs.hochladen()" ${sperre()}>Version hochladen</button>${nameHinweis()}</div>
        </details>
      </div>
      <div class="karte"><h2>Änderungsprotokoll</h2>${protokoll(t.aenderungen)}</div>`,
      `<a href="#/zertifikate/teile" class="grau" style="text-decoration:none">← Teile</a>`);
  }

  // Felder zum Hochladen: Datei (PDF und die erlaubten anderen Formate), Dokumentart, Herkunft.
  // p: Präfix der IDs; mitErkennen: Dokumentart darf bei PDFs leer bleiben (wird erkannt).
  function dateiFelder(p, mitErkennen) {
    const formate = (Z.begriffe?.dateiformate || []).map(f => "." + f.endung).join(",") || ".pdf";
    const arten = Object.keys(ARTEN).filter(a => a !== "unbekannt");
    return `
      <div><label for="${p}datei">Datei</label>${dateiwahl(`id="${p}datei" accept="${esc(formate)}"`)}
        <div class="beleg">Außer PDFs nimmt das Archiv Word, Excel, Bilder, Zeichnungen und ZIP-Archive bis 50 MB an. Aus diesen liest es nichts, deshalb wählen Sie die Dokumentart selbst.</div></div>
      <div><label for="${p}art">Dokumentart</label><select id="${p}art">${optionen([["", mitErkennen ? "Bei PDFs aus dem Inhalt erkennen" : "Noch offen"], ...arten.map(a => [a, art(a)])], "")}</select></div>
      <div><label for="${p}herkunft">Woher stammt die Datei?</label><select id="${p}herkunft">${optionen(Object.entries(HERKUNFT).filter(([k]) => k !== "altbestand" && k !== "atex_agent"), "hochgeladen")}</select></div>
      <div><label for="${p}link">Download-Link, falls bekannt</label><input type="url" id="${p}link" placeholder="https://…"></div>
      <div><label for="${p}seite">Webseite, auf der der Link stand, falls bekannt</label><input type="url" id="${p}seite" placeholder="https://…"></div>`;
  }

  // FormData aus dateiFelder; null mit Meldung, wenn etwas fehlt
  function dateiFormular(p, von, mitErkennen) {
    const datei = feld(`${p}datei`)?.files[0];
    if (!datei) { toast("Bitte wählen Sie zuerst eine Datei aus."); return null; }
    const docType = feld(`${p}art`).value;
    if (!/\.pdf$/i.test(datei.name) && !docType && mitErkennen) { feld(`${p}art`).focus(); toast("Aus dieser Datei liest das Archiv nichts. Bitte wählen Sie die Dokumentart aus."); return null; }
    const fd = new FormData();
    fd.append("datei", datei); fd.append("von", von); fd.append("doc_type", docType);
    fd.append("quelle_art", feld(`${p}herkunft`).value); fd.append("quelle_dateiname", datei.name);
    if (text(`${p}link`)) fd.append("quelle_link", text(`${p}link`));
    if (text(`${p}seite`)) fd.append("quelle_seite", text(`${p}seite`));
    return fd;
  }

  function hochladen() {
    const t = Z.teil, datei = feld("zs-hdatei")?.files[0];
    if (!datei) return toast("Bitte wählen Sie zuerst eine Datei aus.");
    if (!/\.pdf$/i.test(datei.name) && !feld("zs-hart").value) { feld("zs-hart").focus(); return toast("Aus dieser Datei liest das Archiv nichts. Bitte wählen Sie die Dokumentart aus."); }
    schreibe(von => {
      const fd = dateiFormular("zs-h", von, true);
      fd.append("hinweis", text("zs-hnotiz")); fd.append("quelle", "upload");
      return zapi(`/teile/${t.id}/dokumente`, { method: "POST", body: fd });
    }, a => `${datei.name} ist jetzt Version ${a.verknuepfung.version} der Dokumentart ${art(a.verknuepfung.doc_type)} für ${t.artikelnr}.${a.abgeloest.length ? " Die bisherige Version ist damit ersetzt." : ""}`);
  }

  // Datei ohne Teil ablegen (POST /dokumente)
  function ablegen() {
    const hersteller = text("zs-lhersteller"), datei = feld("zs-ldatei")?.files[0];
    if (!hersteller) { feld("zs-lhersteller").focus(); return toast("Bitte geben Sie den Hersteller an."); }
    if (!datei) return toast("Bitte wählen Sie zuerst eine Datei aus.");
    schreibe(von => {
      const fd = dateiFormular("zs-l", von, false);
      fd.append("hersteller", hersteller);
      return zapi("/dokumente", { method: "POST", body: fd });
    }, a => a.bekannt ? `Diese Datei liegt schon im Archiv, als Dokument ${a.id}.` : `${datei.name} liegt jetzt im Archiv, als Dokument ${a.id}.`,
    a => { Z.bleibt = true; location.hash = `#/zertifikate/dok/${a.id}`; });
  }

  // ---------- Neue Suche nötig ----------

  async function ladeNeueSuche() {
    const [eintraege, vorgaenge] = await Promise.all([zapi("/neue-suche"), api("/api/vorgaenge").catch(() => null)]);
    Z.neueSuche = eintraege; Z.vorgaenge = vorgaenge;
    if (Z.vorgang === null && zustand.vid) Z.vorgang = zustand.vid;  // zuletzt geöffneter Vorgang
    if (vorgaenge && !vorgaenge.some(v => v.id === Z.vorgang)) Z.vorgang = null;
    await ladeBetroffen();
  }

  async function ladeBetroffen() {
    Z.betroffen = null; Z.betroffenFehler = "";
    if (!Z.vorgang) return;
    try { Z.betroffen = (await api(`/api/boms/${Z.vorgang}/veraltet`)).items; }
    catch (f) { Z.betroffenFehler = f.message; console.error("Betroffene Positionen nicht ladbar:", f); }
  }

  function vorgangTeil() {
    if (!Z.vorgaenge) return `<p class="unbelegt" style="margin:0">Die Vorgänge sind gerade nicht abrufbar. Bitte später erneut versuchen.</p>`;
    if (!Z.vorgaenge.length) return leerZeile("Es gibt noch keinen Vorgang. Die neue Suche läuft je Vorgang, sobald es einen gibt.");
    const v = Z.vorgaenge.find(x => x.id === Z.vorgang);
    const b = Z.betroffen;
    return `
      <div class="zs-filter" style="margin-bottom:8px"><div><label for="zs-vorgang">Vorgang</label>
        <select id="zs-vorgang" onchange="zs.vorgang(this.value)">${optionen([["", "Bitte wählen Sie einen Vorgang …"], ...Z.vorgaenge.map(x => [x.id, x.name])], Z.vorgang || "")}</select></div>
        ${v ? `<a href="#/v/${v.id}" style="padding-bottom:7px">Vorgang öffnen</a>` : ""}</div>
      ${Z.suchErgebnis ? `<div class="hinweis gruen">${esc(Z.suchErgebnis)}</div>` : ""}
      ${!v ? leerZeile("Wählen Sie einen Vorgang, um seine betroffenen Positionen zu sehen und neu zu suchen.")
        : Z.betroffenFehler ? `<p class="unbelegt" style="margin:0">Die betroffenen Positionen sind gerade nicht abrufbar. ${esc(Z.betroffenFehler)}</p>`
        : !b.length ? leerZeile("Keine Position dieses Vorgangs stützt sich noch auf ein Dokument, das als veraltet bestätigt ist.")
        : `<h3>${b.length === 1 ? "Eine Position ist betroffen" : `${b.length} Positionen sind betroffen`}</h3>
          <div class="tabelle"><table><thead><tr><th>Pos.</th><th>Typ</th><th>Stand</th><th>Veraltet</th></tr></thead><tbody>
          ${b.map(i => `<tr class="klick" onclick="location.hash='#/v/${v.id}/a/${i.id}'" title="Artikel öffnen"><td>${esc(i.pos)}</td><td class="mono">${esc(i.manufacturer_article_no)}</td><td>${esc(POSITION[i.status] || i.status)}</td>
            <td>${i.veraltet.map(x => `<div>${esc(x.dateiname)}${neben(x.gruende.join(" "))}</div>`).join("")}</td></tr>`).join("")}</tbody></table></div>
          <p class="grau" style="font-size:12.5px">Die veralteten Dokumente gelten dann in den Stammdaten wieder als ungeprüft, und nur diese Positionen werden neu gesucht. Findet die Suche nichts Neues, liegt ein E-Mail-Entwurf an den Hersteller bereit.</p>
          <div class="zeile"><button onclick="zs.neuSuchen()" ${Z.sucht ? "disabled" : sperre()}>${Z.sucht ? "Die Suche läuft …" : "Betroffene Positionen neu suchen"}</button>${nameHinweis()}</div>`}`;
  }

  function zeichneNeueSuche() {
    const e = Z.neueSuche;
    $("#app").innerHTML = rahmen("neue-suche", `
      <div class="karte">
        <p class="grau" style="margin-top:0">Hier stehen Dokumente, die als veraltet bestätigt und noch in Gebrauch sind. Die Suche schlägt sie nicht mehr vor. Für betroffene Positionen eines Vorgangs suchen Sie unten Ersatz. Erledigt ist ein Dokument, sobald eine neue Version es ersetzt.</p>
        ${e.length ? `<div class="tabelle"><table><thead><tr><th>Dokument</th><th>Teile</th><th>Gründe</th><th>Bestätigt von</th></tr></thead><tbody>
          ${e.map(x => `<tr><td><a href="#/zertifikate/dok/${x.dokument.id}"><strong>${esc(x.dokument.dateiname)}</strong></a>
              <div class="beleg">${esc(x.dokument.hersteller)} · <a href="${esc(dateiUrl(x.dokument))}" target="_blank" rel="noopener">Datei öffnen</a></div></td>
            <td class="mono">${esc(x.teile.map(t => `${t.artikelnr} (V${t.version})`).join(", ")) || "–"}</td>
            <td>${x.gruende.map(g => `<div>${esc(g.grund)}</div>`).join("")}</td>
            <td>${esc([...new Set(x.gruende.map(g => g.entschieden_von || g.erstellt_von))].sort().join(", "))}</td></tr>`).join("")}</tbody></table></div>`
          : `<div class="hinweis gruen" style="margin-bottom:0">Für kein Dokument wird gerade Ersatz gesucht.</div>`}
      </div>
      ${e.length || Z.suchErgebnis ? `<div class="karte"><h2>Im Vorgang Ersatz suchen</h2><div id="zs-vteil">${vorgangTeil()}</div></div>` : ""}`);
  }

  async function vorgang(id) {
    Z.vorgang = id ? +id : null; Z.suchErgebnis = null;
    await ladeBetroffen();
    if (feld("zs-vteil")) feld("zs-vteil").innerHTML = vorgangTeil();
  }

  function neuSuchen() {
    const bom = Z.vorgang;
    mitName(async by => {
      Z.sucht = true;
      if (feld("zs-vteil")) feld("zs-vteil").innerHTML = vorgangTeil();
      try {
        const a = await api(`/api/boms/${bom}/veraltet/suche`, { method: "POST", json: { by } });
        Z.suchErgebnis = `${a.items.length === 1 ? "Eine Position ist" : `${a.items.length} Positionen sind`} neu gesucht: ${a.items.map(i => `Position ${i.pos}`).join(", ")}. Den neuen Stand sehen Sie im Vorgang.`;
        toast("Die neue Suche ist fertig.");
      } catch (f) { melde(`Die neue Suche hat nicht geklappt. ${f.message}`, "rot"); }
      Z.sucht = false;
      await neu();
    });
  }

  // ---------- Import und Regeln ----------

  function zeichneImport() {
    const laeufe = Z.importe || [], r = Z.regeln, l = laeufe[0];
    if (!Z.normen) Z.normen = [];  // neue, noch leere Zeilen
    const stand = Object.fromEntries(r.normen.map(n => [n.norm, n]));
    const zahl = (titel, w) => `<div class="statkarte"><div><div class="label">${titel}</div><div class="wert">${w ?? "–"}</div></div></div>`;
    $("#app").innerHTML = rahmen("import", `
      <div class="karte">
        <h2>Übernommener Bestand</h2>
        <p class="grau" style="margin:-6px 0 10px">Beim ersten Start hat das Archiv den alten Ordner mit den Dokumenten der Hersteller eingelesen. Jede Datei trägt als Herkunft „Übernommener Bestand“ und ihren Pfad im Ordner.</p>
        ${l ? `<div class="grau" style="margin:0 0 10px">Zuletzt eingelesen am ${zeit(l.beendet || l.gestartet)}.${l.beendet ? "" : " Das Einlesen läuft noch oder wurde abgebrochen."}</div>
          <div class="zs-kacheln" style="margin-bottom:8px">${zahl("Dateien", l.zahlen.dateien)}${zahl("Doppelt", l.zahlen.dubletten)}${zahl("Scans ohne Text", l.zahlen.nicht_lesbar)}${zahl("Mit Teil", l.zahlen.mit_teil)}${zahl("Ohne Teil", l.zahlen.ohne_teil)}</div>
          <div class="grau" style="font-size:12.5px">Neu dazugekommen sind ${l.zahlen.dokumente_neu ?? "–"} Dokumente, ${l.zahlen.teile_neu ?? "–"} Teile, ${l.zahlen.verknuepfungen ?? "–"} Versionen und ${l.zahlen.markierungen_neu ?? "–"} Markierungen.${l.zahlen.mehrdeutig ? ` ${l.zahlen.mehrdeutig} ließen sich nicht eindeutig zuordnen.` : ""}${l.zahlen.teilweise_ohne_text ? ` ${l.zahlen.teilweise_ohne_text} haben Seiten ohne Text.` : ""}</div>
          <details style="margin-top:10px"><summary>Hinweise vom Einlesen (${l.hinweise.length})</summary>
            ${l.hinweise.map(h => `<div class="beleg" style="padding:3px 0;word-break:break-word">${esc(h)}</div>`).join("") || leerZeile("Es gibt keine Hinweise.")}</details>
          ${laeufe.length > 1 ? `<details><summary>Früher eingelesen (${laeufe.length - 1})</summary><table><thead><tr><th>Wann</th><th>Ordner</th><th>Dateien</th><th>Neu</th><th>Doppelt</th></tr></thead><tbody>
            ${laeufe.slice(1).map(x => `<tr><td>${zeit(x.beendet || x.gestartet)}</td><td class="mono">${esc(x.quelle)}</td><td>${x.zahlen.dateien ?? ""}</td><td>${x.zahlen.dokumente_neu ?? ""}</td><td>${x.zahlen.dubletten ?? ""}</td></tr>`).join("")}</tbody></table></details>` : ""}`
          : leerZeile("Der alte Bestand ist noch nicht eingelesen.")}
      </div>
      <div class="karte">
        <h2>Prüfregeln</h2>
        <p class="grau" style="margin:-6px 0 8px">Die Prüfregeln lesen nur PDFs. Jede gilt nur für die Dokumentarten daneben, eine Anleitung zum Beispiel bekommt keinen Verdacht wegen einer alten Normausgabe.</p>
        <table title="Die Prüfregeln selbst lassen sich hier nicht ändern."><thead><tr><th>Prüfregel</th><th>Gilt für</th><th>Dokumente mit Verdacht</th></tr></thead><tbody>
          ${Object.entries(r.regeln).map(([k, t]) => `<tr><td>${esc(t)}</td><td class="grau" style="font-size:12.5px">${esc(undListe((r.gilt_fuer_text || {})[k] || []))}</td>
            <td><a href="#/zertifikate" onclick="zs.kachel({regel:'${esc(k)}'})">${Z.alle.filter(d => d.regeln.includes(k)).length}</a></td></tr>`).join("")}</tbody></table>
        <p class="grau" style="font-size:12.5px">Ein Dokument gilt als verdächtig alt, wenn es älter als ${esc(r.veraltet_nach_jahren)} Jahre ist. Die Richtlinie 94/9/EG gilt seit dem ${esc(r.abloesung_94_9)} nicht mehr. Bescheinigungen nach 94/9/EG gelten aber weiter.</p>
        <div class="zeile"><button class="leise" onclick="zs.regelnPruefen()" ${sperre()}>Prüfregeln auf alle Dokumente anwenden</button>${nameHinweis()}</div>
      </div>
      <div class="karte">
        <h2>Aktuelle Normausgaben</h2>
        <p class="grau" style="margin-top:-6px">Nennt ein PDF nur eine ältere Ausgabe einer Norm, meldet die Prüfregel einen Verdacht. Tragen Sie das Jahr der IEC-Ausgabe ein, zum Beispiel 2017 für IEC 60079-0:2017, die in Europa als EN IEC 60079-0:2018 erschienen ist. Nach dem Speichern prüfen die Prüfregeln alle Dokumente neu.</p>
        ${r.normen.some(n => n.zu_bestaetigen) ? `<div class="hinweis" style="background:var(--gelb-hell);color:var(--gelb);border-left:3px solid #d9a400">${esc(r.normen.find(n => n.zu_bestaetigen).pruefhinweis || "")} Noch nicht bestätigt ${r.normen.filter(n => n.zu_bestaetigen).length === 1 ? "ist" : "sind"} ${esc(undListe(r.normen.filter(n => n.zu_bestaetigen).map(n => n.norm)))}.</div>` : ""}
        ${normTabelle(r, stand)}
        <div class="zeile nur-schreiben" style="margin-top:10px"><button class="leise" onclick="zs.normDazu()">Norm hinzufügen</button>
          <span class="grau" style="font-size:12.5px">Füllen Sie die neue Zeile aus oder haken Sie „Entfernen“ an. Gespeichert wird erst mit „Prüfen und speichern“.</span></div>
      </div>`);
  }

  function normTabelle(r, stand) {
    const zeilen = [...r.normen.map(n => ({ k: n.norm, norm: n.norm, jahr: String(n.jahr), hinweis: n.hinweis || "", weg: false })),
      ...Z.normen.map(n => ({ k: n.k, norm: "", jahr: "", hinweis: "", weg: false, neu: true }))];
    return Tabelle.html({
      id: "zs-normen", titel: "Aktuelle Normausgaben", zeilen, schluessel: n => n.k, name: n => n.neu ? "Neue Zeile" : `Norm ${n.norm}`,
      leer: "Es ist keine Norm eingetragen. Deshalb meldet die Regel zu älteren Normausgaben nie einen Verdacht.",
      spalten: [
        { s: "norm", titel: "Norm", kopfTitel: "zum Beispiel 60079-0" },
        { s: "jahr", titel: "Aktuell ab (Jahr)", stil: "width:150px" },
        { s: "hinweis", titel: "Hinweis" },
        { s: "geaendert", titel: "Geändert von", wert: n => stand[n.norm] ? stand[n.norm].geaendert_von : "", gesperrt: "Hier steht, wer die Zeile zuletzt gespeichert hat.",
          zeige: (w, n) => stand[n.norm] ? `${esc(w)}${neben(zeit(stand[n.norm].geaendert_am))}` : "neu" },
        { s: "weg", titel: "Entfernen", art: "haken" },
      ],
      // Bestätigen ist eine eigene Aktion, kein Wert der Tabelle: letzte Spalte mit Knopf
      zusatz: { kopf: "Bestätigt", zelle: n => !stand[n.norm] ? "" : stand[n.norm].zu_bestaetigen
        ? `<span class="chip s-zu_pruefen">zu bestätigen</span> <button class="leise" onclick="zs.normBestaetigen('${esc(n.norm)}')" ${sperre()}>Ausgabe bestätigen</button>`
        : `<span class="grau">${esc(stand[n.norm].bestaetigt_von || "")}</span>${neben(zeit(stand[n.norm].bestaetigt_am))}` },
      notiz: false,
      folgen: (n, werte) => [werte.weg ? "Die Zeile wird entfernt." : n.neu ? "Die Zeile kommt neu dazu." : "",
        "Gespeichert wird die ganze Tabelle. Danach prüfen die Prüfregeln alle Dokumente neu."],
      speichernAlle: async (offen, { von }) => {
        const geaendert = Object.fromEntries(offen.map(({ z, werte }) => [z.k, werte]));
        const zeilen_ = zeilen.map(n => ({ ...n, ...(geaendert[n.k] || {}) }))
          .filter(n => !n.weg && (String(n.norm).trim() || String(n.jahr).trim()))
          .map(n => ({ norm: String(n.norm).trim().replace(/^((DIN|EN|IEC)[\s/]*)+/i, ""), jahr: String(n.jahr).trim(), hinweis: String(n.hinweis || "").trim() }));
        if (zeilen_.some(n => !/^60079-\d{1,2}$/.test(n.norm) || !/^(19|20)\d{2}$/.test(n.jahr))) throw new Error("Bitte geben Sie in jeder Zeile eine Norm wie „60079-0“ und ein Jahr wie „2017“ an.");
        if (new Set(zeilen_.map(n => n.norm)).size !== zeilen_.length) throw new Error("Jede Norm darf nur einmal vorkommen. Bitte entfernen Sie die doppelte Zeile.");
        const normen = zeilen_.map(n => ({ norm: n.norm, jahr: +n.jahr, ...(n.hinweis ? { hinweis: n.hinweis } : {}) }));
        const a = await zapi("/normen", { method: "PUT", json: { normen, von } });
        Z.normen = [];
        melde("Die Normausgaben sind gespeichert, und die Prüfregeln haben alle Dokumente neu geprüft.");
      },
      nachher: neu,
    });
  }
  let normNr = 0;
  function normDazu() { Z.normen.push({ k: `neu-${++normNr}` }); zeichneImport(); }
  const pruefText = z => z && typeof z === "object" ? `: ${Object.entries(z).filter(([, v]) => typeof v === "number").map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ")}` : "";
  const regelnPruefen = () => schreibe(von => post("/regeln/pruefen", { von }), "Die Prüfregeln haben alle Dokumente neu geprüft.");
  const normBestaetigen = norm => schreibe(von => post("/normen/bestaetigen", { normen: [norm], von }),
    () => `Die Ausgabe für die Norm ${norm} ist bestätigt.`);

  // ---------- einhängen ----------

  window.zs = {
    neu, kachel, filter, waehle, alle, mehr,
    markieren: () => markierung("markieren", [...Z.auswahl], "zs-grund", feld("zs-sofort")?.checked),
    bestaetigen: () => markierung("bestaetigen", [...Z.auswahl], "zs-grund"),
    verwerfen: () => markierung("verwerfen", [...Z.auswahl], "zs-grund"),
    dokMarkierung: aktion => markierung(aktion, [Z.dok.id], "zs-dgrund", feld("zs-dsofort")?.checked),
    pdfAuf, pdfSeite, zuordnen, beenden, umhaengen, versionWieder, ausmustern, dokWieder,
    teilSuche, teilAnlegen, hochladen, ablegen,
    vorgang, neuSuchen,
    normDazu, regelnPruefen, normBestaetigen,
  };

  // Suchfeld oben filtert die Dokumentliste (index.html kennt nur die eingebauten Seiten)
  $("#suche").addEventListener("input", () => { if (meine() && feld("zs-liste")) zeichneListe(); });

  erweitere("seiten", { name: "zertifikate", muster: /^#\/zertifikate(?:\/(dok|teile|neue-suche|import)(?:\/(\d+))?)?\/?$/, zeige: (m, app) => zeige(m, app) });
  // Zahl in der Leiste: offener Verdacht, über den noch niemand entschieden hat
  erweitere("nav", { name: "zertifikate", href: "#/zertifikate", text: NAME, icon: ICON_ZS, zahl: () => Z.alle ? zahlen().verdacht : 0 });
  zapi("/dokumente").then(alle => { if (!Z.alle) { Z.alle = alle; zeichneNav(); } }).catch(() => {});
})();
