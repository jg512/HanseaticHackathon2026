// Stammdaten (M18): Bauteile, Dokumente, Zuordnungen und Hersteller der ATEX-Datenbank ansehen
// und von Hand pflegen.
// Backend: /api/stammdaten/… (docs/api.md). Jede schreibende Anfrage schickt den Namen als „by“;
// nur ein Mensch setzt eine Zuordnung auf „geprüft“ (M3).
// Pfade: #/stammdaten (Bauteile), #/stammdaten/teil/<id>, #/stammdaten/dokumente,
//        #/stammdaten/dokument/<id>, #/stammdaten/hersteller, #/stammdaten/aenderungen
(function () {
  const S = "/api/stammdaten";
  const ART = {
    eu_konformitaetserklaerung: "EU-Konformitätserklärung",
    eu_baumusterpruefbescheinigung: "EU-Baumusterprüfbescheinigung",
    uk_konformitaetserklaerung: "UK-Konformitätserklärung",
    uk_baumusterpruefbescheinigung: "UK-Baumusterprüfbescheinigung",
    bedienungsanleitung: "Bedienungsanleitung", datenblatt: "Datenblatt", sonstiges: "Sonstiges",
  };
  const ART_KURZ = { eu_konformitaetserklaerung: "Konf.-Erkl.", eu_baumusterpruefbescheinigung: "Baumusterpr.",
    uk_konformitaetserklaerung: "UK-Konf.-Erkl.", uk_baumusterpruefbescheinigung: "UK-Baumusterpr." };
  const PRUEF = { ungeprueft: ["Ungeprüft", "s-wartet"], ki_geprueft: ["KI-geprüft", "s-suche"], geprueft: ["Geprüft", "s-geprueft"] };
  const BAUTEIL_FELDER = [
    ["name_de", "Bezeichnung (de)"], ["name_en", "Bezeichnung (en)"],
    ["manufacturer", "Hersteller"], ["manufacturer_article_no", "Hersteller-Artikelnr."],
    ["ex_marking_raw", "Ex-Kennzeichnung"], ["manufacturer_url", "Hersteller-Link"],
  ];
  const FELDNAME = { ...Object.fromEntries(BAUTEIL_FELDER), review_needed: "Prüfung nötig", angelegt: "angelegt",
    zuordnung: "Zuordnung", status: "Prüfstatus", geloescht: "gelöscht", doc_type: "Dokumentart",
    certificate_no: "Zertifikats-Nr.", valid_until: "Gültig bis", languages: "Sprachen", name: "Name" };
  const TABELLE = { part: "Bauteil", document: "Dokument", part_document: "Zuordnung", manufacturer: "Hersteller" };
  // stand einer stücklisten-position, wie ihn /api/boms/… nennt (nicht der stand der vorgangsseite)
  const POS_STAND = { offen: ["Offen", "s-wartet"], klaerung: ["Klärung nötig", "s-zu_pruefen"], vorschlag: ["Vorschlag prüfen", "s-suche"],
    nicht_gefunden: ["Nicht gefunden", "s-info_fehlt"], angefordert: ["Angefordert", "s-angefordert"], eskaliert: ["Eskaliert", "s-eskaliert"],
    vorhanden: ["Vorhanden", "s-geprueft"], kein_ex_teil: ["Kein Ex-Teil", "s-kein_ex"], erledigt: ["Erledigt", "s-geprueft"] };
  const BEREICHE = [["", "Bauteile"], ["dokumente", "Dokumente"], ["hersteller", "Hersteller"], ["aenderungen", "Änderungen"]];
  const NOTIZ = "zum Beispiel: laut Datenblatt, E-Mail vom 14.11.";

  // filter bleiben beim wechsel zwischen liste und einzelansicht erhalten
  const z = {
    werte: null, hersteller: [],
    f: { q: "", h: "", s: "", pruefen: false },    // bauteile
    df: { q: "", art: "", ohne: false },            // dokumente
    p: null, d: null, frei: null,
  };
  let lauf = 0, tippen = null;

  const stil = document.createElement("style");
  stil.textContent = `
    .sd-wahl, .sd-datum { font: inherit; padding: 7px 9px; border: 1px solid #ced4da; border-radius: 0; background: #fff; width: 100%; }
    .sd-wahl:focus, .sd-datum:focus { outline: none; border-color: var(--gruen-marke); box-shadow: 0 0 0 3px rgba(187, 208, 61, .35); }
    .sd-suche { display: grid; grid-template-columns: minmax(0, 1fr) 220px 170px; gap: 0 12px; align-items: end; }
    .sd-gitter { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
    .sd-haken { display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--text); margin: 10px 0 0; cursor: pointer; }
    .sd-haken input { margin: 0; }
    .sd-neben { font-size: 12px; color: var(--grau); }
    .sd-klapp { margin-top: 12px; border-top: 1px solid var(--linie); padding-top: 10px; }
    .sd-klapp > summary { cursor: pointer; font-weight: 600; }
    .sd-klapp > summary:hover { color: var(--gruen); }
    .sd-rechts { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; margin-top: 12px; }
    .sd-frage { background: var(--rot-hell); border-left: 3px solid var(--rot); padding: 10px 12px; margin-top: 10px; font-size: 13px; }
    .sd-aktion { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
    .sd-aktion button, .sd-aktion .knopf { padding: 3px 10px; font-size: 12.5px; white-space: nowrap; }
    .sd-warn { background: var(--gelb-hell); color: var(--gelb); border-left: 3px solid var(--gelb); }
    tr.sd-an td { background: var(--limette-hell); }
    tr.sd-ohne-mail td:first-child { box-shadow: inset 3px 0 0 var(--rot); }
    .sd-prot td:first-child { min-width: 170px; } .sd-prot td:last-child { min-width: 200px; }
    .sd-prot td:nth-child(2), .sd-prot td:nth-child(3) { overflow-wrap: anywhere; }
    @media (max-width: 900px) { .sd-suche, .sd-gitter { grid-template-columns: 1fr; } }`;
  document.head.appendChild(stil);

  // ---------- helfer ----------
  const art = a => ART[a] || a || "";
  const datum = iso => iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "";
  // zeiten kommen in UTC ohne zone
  const zeit = iso => {
    if (!iso) return "";
    const d = new Date(/(Z|[+-]\d\d:\d\d)$/.test(iso) ? iso : iso + "Z");
    return isNaN(d) ? iso : `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
  };
  const pruefChip = s => { const [t, k] = PRUEF[s] || [s, "s-wartet"]; return `<span class="chip ${k}">${esc(t)}</span>`; };
  const marke = (text, klasse) => `<span class="chip ${klasse}">${esc(text)}</span>`;
  const dateiName = d => dateiLesbar(String(d.file_path || d.url || "?").split("/").pop());
  const dokName = d => `${art(d.doc_type)} · ${dateiName(d)}`;
  const sprachen = d => (d.languages || []).map(s => s.toUpperCase()).join(" ");
  const istLink = u => /^https?:\/\//i.test(u || "");
  const wert = id => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
  const an = id => !!document.getElementById(id)?.checked;
  const geh = pfad => { location.hash = "#/stammdaten" + (pfad ? "/" + pfad : ""); };
  const neu = () => route();

  function abfrage(pfad, filter) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(filter)) if (v !== "" && v != null && v !== false) p.set(k, v);
    const q = p.toString();
    return api(pfad + (q ? "?" + q : ""));
  }

  // schreiben: toast, dann die seite neu laden; fehler des backends als text
  async function tu(fn, erfolg, danach) {
    try {
      const a = await fn();
      let text = typeof erfolg === "function" ? erfolg(a) : erfolg;
      if (a && a.items_updated) text += a.items_updated === 1 ? " Der Stand einer Position hat sich dadurch neu ergeben." : ` Der Stand von ${a.items_updated} Positionen hat sich dadurch neu ergeben.`;
      toast(text);
      if (danach) danach(a); else await neu();
    } catch (e) { toast(e.message); }
  }
  const koerper = (von, notizId, mehr) => { const n = wert(notizId); return { by: von, ...(n ? { note: n } : {}), ...mehr }; };

  // ---------- tabellen (seiten/tabelle.js): erst vormerken, nach bestätigung je zeile speichern ----------
  const sprachListe = text => String(text || "").split(/[\s,;]+/).map(x => x.trim().toLowerCase()).filter(Boolean);
  const sprachText = liste => [...(liste || [])].sort().join(", ");
  const mitNotiz = (von, notiz, mehr) => ({ by: von, ...(notiz ? { note: notiz } : {}), ...mehr });
  const NOTIZ_TG = "Notiz für das Änderungsprotokoll, falls Sie eine festhalten möchten";
  const artOptionen = () => (z.werte?.doc_types || Object.keys(ART)).map(a => [a, art(a)]);
  const statusOptionen = () => (z.werte?.status || Object.keys(PRUEF)).map(s => [s, (PRUEF[s] || [s])[0]]);
  // Folgen in Worten, wie in docs/api.md beschrieben
  function bauteilFolgen(werte) {
    const t = [];
    if ("manufacturer" in werte) t.push("Ist der Hersteller noch unbekannt, wird er neu angelegt. Eine andere Schreibweise eines bekannten Herstellers wird erkannt.");
    if (Object.keys(werte).some(f => f !== "review_needed" && !String(werte[f] ?? "").trim())) t.push("Ein leerer Wert löscht die Angabe.");
    return t;
  }
  function dokumentFolgen(werte) {
    const t = [];
    if ("doc_type" in werte) t.push("Mit der neuen Dokumentart ergibt sich der Stand aller Positionen mit diesen Bauteilen neu.");
    if ("status" in werte) t.push(werte.status === "geprueft" ? "Ihr Name und die Zeit werden als Prüfung eingetragen. Der Stand der betroffenen Positionen ergibt sich dann neu."
      : "Die bisherige Prüfung wird gelöscht. Der Stand der betroffenen Positionen ergibt sich dann neu.");
    return t;
  }
  async function dokumentSpeichernTG(docId, werte, { von, notiz }) {
    const { status, languages, ...felder } = werte;
    const daten = { ...felder, ...(languages !== undefined ? { languages: sprachListe(languages) } : {}) };
    if (Object.keys(daten).length) await api(`${S}/documents/${docId}`, { method: "PATCH", json: mitNotiz(von, notiz, daten) });
    return status;
  }
  const dokSpalten = () => [
    { s: "doc_type", titel: "Dokumentart", art: "auswahl", optionen: artOptionen(), leer: false },
    { s: "certificate_no", titel: "Zertifikat" },
    { s: "languages", titel: "Sprachen", wert: d => sprachText(d.languages), zeige: w => w ? esc(w.toUpperCase().replace(/,/g, "")) : "–",
      kopfTitel: "Sprachkürzel mit Komma, zum Beispiel: de, en" },
    { s: "valid_until", titel: "Gültig bis", art: "datum" },
  ];
  // eine Zeile je Feld (Detailseiten): Art und Optionen kommen aus der Zeile
  const feldSpalten = (sperre) => [
    { s: "titel", titel: "Angabe", gesperrt: "Das ist die Bezeichnung der Angabe.", still: true, klasse: "grau", stil: "width:220px" },
    { s: "wert", titel: "Wert", art: f => f.art || "text", optionen: f => f.optionen || [], leer: f => f.leer !== false, gesperrt: sperre || (() => ""),
      zeige: (w, f) => f.art === "haken" ? (w ? "☑ ja" : "☐ nein") : f.art === "auswahl" ? esc(art(w)) : f.art === "datum" ? esc(datum(w)) || "–"
        : f.feld === "languages" ? esc(String(w).toUpperCase()) || "–" : w ? esc(w) : '<span class="grau">–</span>' },
  ];

  function kopf(aktiv) {
    return `
      <div class="kopfzeile"><div><h1>Stammdaten</h1>
        <div class="grau">Hier pflegen Sie Bauteile, Dokumente und Hersteller. Jede Änderung steht mit Ihrem Namen im Änderungsprotokoll.</div></div></div>
      ${darfSchreiben() ? "" : `<div class="hinweis sd-warn">${esc(sperrgrund())} Ändern, prüfen und löschen können Sie deshalb nicht.</div>`}
      <div class="filter">${BEREICHE.map(([pfad, text]) =>
        `<button class="${pfad === aktiv ? "an" : ""}" onclick="stammdaten.geh('${pfad}')">${text}${pfad === "hersteller" && ohneMail() ? ` <span class="chip s-info_fehlt" style="padding:0 7px" title="Hersteller ohne E-Mail-Adresse">${ohneMail()}</span>` : ""}</button>`).join("")}</div>`;
  }

  function protokoll(aenderungen, leer, mitLinks) {
    if (!aenderungen.length) return `<p class="grau" style="margin:0">${esc(leer)}</p>`;
    const was = a => a.field === "zuordnung" ? "Zuordnung " + (a.old_value ? "gelöst" : "angelegt")
      : `${TABELLE[a.table_name] || a.table_name}: ${FELDNAME[a.field] || a.field}`;
    const lesbar = (a, w) => !w ? "" : a.field === "doc_type" ? art(w) : a.field === "status" ? (PRUEF[w] || [w])[0]
      : a.field === "valid_until" ? datum(w) : w;
    const wo = a => {
      const teile = [];
      if (a.part_id) teile.push(mitLinks ? `<a href="#/stammdaten/teil/${a.part_id}">Bauteil ${a.part_id}</a>` : "");
      if (a.document_id) teile.push(mitLinks && !(a.field === "geloescht") ? `<a href="#/stammdaten/dokument/${a.document_id}">Dokument ${a.document_id}</a>` : `Dokument ${a.document_id}`);
      return teile.filter(Boolean).join(" · ");
    };
    return `<div class="sd-neben" style="margin-bottom:4px">🔒 Das Protokoll lässt sich nicht ändern.</div>
      <div class="tabelle" title="Das Protokoll lässt sich nicht ändern."><table class="sd-prot"><thead><tr><th>Was</th><th>Vorher</th><th>Nachher</th><th>Von</th></tr></thead><tbody>
      ${aenderungen.map(a => `<tr>
        <td>${esc(was(a))}${wo(a) ? `<div class="sd-neben">${wo(a)}</div>` : ""}</td>
        <td>${esc(lesbar(a, a.old_value)) || "–"}</td><td>${esc(lesbar(a, a.new_value)) || "–"}</td>
        <td>${esc(a.changed_by)}<div class="sd-neben">${esc(zeit(a.changed_at))} · ${esc(a.note) || "ohne Notiz"}</div></td></tr>`).join("")}
      </tbody></table></div>`;
  }

  // ganze PDF in der seite, wie pdfZeigen in index.html
  function pdf(docId) {
    const el = $("#sd-pdf");
    if (!el) return;
    const offen = el.dataset.doc === String(docId);
    el.dataset.doc = offen ? "" : docId;
    el.innerHTML = offen ? "" : `<iframe class="pdf" src="${API}${S}/documents/${docId}/file"></iframe>`;
    document.querySelectorAll("[data-sd-pdf]").forEach(b => { b.textContent = !offen && b.dataset.sdPdf === String(docId) ? "PDF zu" : "PDF ansehen"; });
    if (!offen) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function pdfKnopf(d) {
    if (d.file_path) return d.file_exists === false ? `<span class="unbelegt">Die Datei fehlt im Ordner.</span>`
      : `<button class="leise" data-sd-pdf="${d.id}" onclick="stammdaten.pdf(${d.id})">PDF ansehen</button>`;
    return istLink(d.url) ? `<a class="knopf leise" href="${esc(d.url)}" target="_blank" rel="noopener">Link öffnen</a>` : "";
  }
  function frage(id, zeigen) {
    document.querySelectorAll(".sd-frage").forEach(el => { el.hidden = !(zeigen && el.id === id); });
  }

  // ---------- bauteile: liste ----------
  async function bauteileSeite(app) {
    app.innerHTML = `${kopf("")}
      <div class="standzahlen" id="sd-kacheln" style="margin:0 0 12px"></div>
      <div class="karte">
        <div class="sd-suche">
          <div><label for="sd-q">Suche</label><input type="text" id="sd-q" value="${esc(z.f.q)}" placeholder="PRO.FILE-Nr., Bezeichnung, Typ oder Hersteller" oninput="stammdaten.filtern()"></div>
          <div><label for="sd-h">Hersteller</label><select class="sd-wahl" id="sd-h" onchange="stammdaten.filtern(true)"><option value="">Alle</option>
            ${z.hersteller.map(m => `<option value="${m.id}" ${String(m.id) === z.f.h ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></div>
          <div><label for="sd-s">Status</label><select class="sd-wahl" id="sd-s" onchange="stammdaten.filtern(true)"><option value="">Alle</option>
            ${z.werte.status.map(s => `<option value="${esc(s)}" ${s === z.f.s ? "selected" : ""}>${esc((PRUEF[s] || [s])[0])}</option>`).join("")}</select></div>
        </div>
        <label class="sd-haken"><input type="checkbox" id="sd-pruefen" ${z.f.pruefen ? "checked" : ""} onchange="stammdaten.filtern(true)"> Nur Bauteile mit „Prüfung nötig“</label>
        <details class="sd-klapp nur-schreiben"><summary>Bauteil von Hand anlegen</summary>
          <p class="sd-neben">Das brauchen Sie nur für ein Bauteil, das noch nicht durch eine Freigabe entstanden ist. Die PRO.FILE-Nr. lässt sich danach nicht mehr ändern.</p>
          <div class="sd-gitter">
            <div><label for="sd-neu-profile_no">PRO.FILE-Nr.</label><input type="text" id="sd-neu-profile_no"></div>
            ${BAUTEIL_FELDER.map(([f, t]) => `<div><label for="sd-neu-${f}">${t}</label><input type="text" id="sd-neu-${f}"></div>`).join("")}
            <div><label for="sd-neu-notiz">Notiz, falls nötig</label><input type="text" id="sd-neu-notiz"></div>
          </div>
          <div class="sd-rechts">${nameHinweis()}<button onclick="stammdaten.anlegen()" ${sperre()}>Bauteil anlegen</button></div>
        </details>
      </div>
      <div class="karte"><div id="sd-liste"><div class="leer">lädt …</div></div></div>`;
    await ladeTeile();
  }

  async function ladeTeile() {
    const n = ++lauf, wache = seitenWache();
    let teile;
    try { teile = await abfrage(`${S}/parts`, { q: z.f.q, manufacturer_id: z.f.h, status: z.f.s, review_needed: z.f.pruefen ? "1" : "" }); }
    catch (e) { if (wache() && n === lauf && $("#sd-liste")) $("#sd-liste").innerHTML = `<div class="unbelegt">${esc(e.message)}</div>`; return; }
    if (!wache() || n !== lauf || !$("#sd-kacheln")) return;
    const fehlt = teile.filter(p => p.missing_doc_types.length).length;
    // Kennzahlen als eine schmale Zeile (früher vier Kacheln)
    const zahl = (n, text, farbe) => `<span>${farbe ? `<i style="background:${farbe}"></i>` : ""}<b>${n}</b> ${text}</span>`;
    $("#sd-kacheln").innerHTML =
      zahl(teile.length, teile.length === 1 ? "Bauteil" : "Bauteile") +
      zahl(teile.length - fehlt, "mit allen Pflichtdokumenten", "#2e7d4f") +
      zahl(fehlt, fehlt === 1 ? "mit fehlendem Pflichtdokument" : "mit fehlenden Pflichtdokumenten", "#b3261e") +
      zahl(teile.filter(p => p.review_needed).length, "mit „Prüfung nötig“", "#d9a400");
    $("#sd-liste").innerHTML = !teile.length
      ? `<div class="leer">Kein Bauteil passt zu Ihrer Suche. Bauteile entstehen, wenn Sie einen Fund freigeben, oder Sie legen oben eines von Hand an.</div>`
      : Tabelle.html({
        id: "sd-teile", titel: "Bauteile", zeilen: teile, name: p => `Bauteil ${p.profile_no}`,
        spalten: [
          { s: "profile_no", titel: "PRO.FILE-Nr.", klasse: "mono", gesperrt: "Die PRO.FILE-Nr. kennzeichnet das Bauteil und lässt sich nach dem Anlegen nicht mehr ändern." },
          { s: "name_de", titel: "Bezeichnung" },
          { s: "manufacturer", titel: "Hersteller", kopfTitel: "Ein neuer Name legt den Hersteller an." },
          { s: "manufacturer_article_no", titel: "Typ", klasse: "mono" },
          { s: "ex_marking_raw", titel: "Ex-Kennzeichnung", stil: "font-size:12.5px" },
          { s: "dokumente", titel: "Dokumente", wert: p => `${p.checked_count} von ${p.document_count} geprüft`, gesperrt: "Das ergibt sich aus den zugeordneten Dokumenten." },
          { s: "pflicht", titel: "Pflicht", wert: p => p.missing_doc_types.join(","), gesperrt: "Hier stehen Pflichtdokumente, die noch nicht geprüft zugeordnet sind. Das ergibt sich aus den Dokumenten.",
            zeige: (w, p) => p.missing_doc_types.length ? `<span class="unbelegt">fehlt ${esc(p.missing_doc_types.map(a => ART_KURZ[a] || art(a)).join(", "))}</span>` : "vollständig" },
          { s: "status", titel: "Status", gesperrt: "Der Status ergibt sich aus dem Prüfstand der Dokumente.", zeige: w => pruefChip(w) },
          { s: "review_needed", titel: "Prüfung nötig", art: "haken" },
        ],
        zusatz: { zelle: p => `<a class="knopf leise" href="#/stammdaten/teil/${p.id}" style="padding:3px 10px;white-space:nowrap">Öffnen →</a>` },
        notiz: NOTIZ_TG,
        folgen: (p, werte) => bauteilFolgen(werte),
        speichern: (p, werte, { von, notiz }) => api(`${S}/parts/${p.id}`, { method: "PATCH", json: mitNotiz(von, notiz, werte) }),
        nachher: ladeTeile,
      }) + `<p class="sd-neben" style="margin:10px 0 0">Mit „Öffnen“ sehen Sie die Dokumente und Änderungen eines Bauteils.</p>`;
  }

  function filtern(sofort) {
    const dok = !$("#sd-q");
    if (dok) z.df = { q: wert("sd-dq"), art: wert("sd-dart"), ohne: an("sd-dohne") };
    else z.f = { q: wert("sd-q"), h: wert("sd-h"), s: wert("sd-s"), pruefen: an("sd-pruefen") };
    tippen?.stopp();
    tippen = seiteTimeout(dok ? ladeDokumente : ladeTeile, sofort ? 0 : 250);
  }

  function anlegen() {
    mitName(von => {
      const profile_no = wert("sd-neu-profile_no");
      if (!profile_no) return toast("Bitte geben Sie die PRO.FILE-Nr. ein. Ohne sie lässt sich kein Bauteil anlegen.");
      const daten = koerper(von, "sd-neu-notiz", { profile_no });
      for (const [f] of BAUTEIL_FELDER) if (wert(`sd-neu-${f}`)) daten[f] = wert(`sd-neu-${f}`);
      tu(() => api(`${S}/parts`, { method: "POST", json: daten }),
        a => `Das Bauteil ${a.profile_no} ist angelegt.`, a => geh(`teil/${a.id}`));
    });
  }

  // ---------- bauteile: ein bauteil ----------
  async function bauteilSeite(app, id, wache) {
    const p = await api(`${S}/parts/${id}`);
    if (!wache()) return;
    z.p = p;
    z.frei = null;
    const fehlt = p.missing_doc_types.map(art).join(", ");
    app.innerHTML = `
      <div class="kopfzeile">
        <div><a href="#/stammdaten" class="grau" style="text-decoration:none">← Stammdaten · Bauteile</a>
          <h1>Bauteil <span class="mono" style="font-size:inherit">${esc(p.profile_no)}</span> · ${esc(p.name_de) || "Ohne Bezeichnung"}</h1>
          <div class="grau">${esc(p.manufacturer) || "Hersteller fehlt"} · <span class="mono">${esc(p.manufacturer_article_no) || "Typ fehlt"}</span>${p.changed_by ? ` · zuletzt geändert von ${esc(p.changed_by)}` : ""}</div></div>
        <div class="rechts">${fehlt ? marke(`Fehlt: ${fehlt}`, "s-info_fehlt") : marke("Pflichtdokumente geprüft", "s-geprueft")}
          ${p.review_needed ? marke("Prüfung nötig", "s-zu_pruefen") : ""}</div>
      </div>
      ${darfSchreiben() ? "" : `<div class="hinweis sd-warn">${esc(sperrgrund())} Ändern, prüfen und löschen können Sie deshalb nicht.</div>`}

      <div class="karte">
        <h2>Angaben</h2>
        ${Tabelle.html({
          id: `sd-teil-${p.id}`, titel: `Bauteil ${p.profile_no}`, schluessel: f => f.feld, name: f => f.titel,
          zeilen: [{ feld: "profile_no", titel: "PRO.FILE-Nr.", wert: p.profile_no, fest: true },
            ...BAUTEIL_FELDER.map(([f, t]) => ({ feld: f, titel: t, wert: p[f] ?? "" })),
            { feld: "review_needed", titel: "Prüfung nötig", wert: !!p.review_needed, art: "haken" }],
          spalten: feldSpalten(f => f.fest ? "Die PRO.FILE-Nr. kennzeichnet das Bauteil und lässt sich nach dem Anlegen nicht mehr ändern." : ""),
          notiz: NOTIZ_TG,
          folgen: (f, werte) => bauteilFolgen({ [f.feld]: werte.wert }),
          speichern: (f, werte, { von, notiz }) => api(`${S}/parts/${p.id}`, { method: "PATCH", json: mitNotiz(von, notiz, { [f.feld]: werte.wert }) }),
          nachher: neu,
        })}
      </div>

      <div class="karte">
        <h2>Dokumente (${p.documents.length})</h2>
        ${p.documents.length ? Tabelle.html({
          id: `sd-teil-dok-${p.id}`, titel: `Dokumente von Bauteil ${p.profile_no}`, zeilen: p.documents, name: d => `${art(d.doc_type)} (Dokument ${d.id})`,
          spalten: [
            { s: "datei", titel: "Datei", wert: d => dateiLesbar(d.file_path || d.url), gesperrt: "Hier liegt das Dokument. Der Ablageort lässt sich nicht ändern.", klasse: "mono", stil: "font-size:12px;word-break:break-all" },
            ...dokSpalten(),
            { s: "link_status", titel: "Prüfstatus", art: "auswahl", optionen: statusOptionen(), leer: false,
              zeige: (w, d) => `${pruefChip(w)}${w === d.link_status && d.verified_by ? `<div class="sd-neben">${esc(d.verified_by)}, ${esc(zeit(d.verified_at))}</div>` : ""}` },
          ],
          zusatz: { zelle: d => `<div class="sd-aktion"><a class="knopf leise" href="#/stammdaten/dokument/${d.id}">Öffnen</a>${pdfKnopf(d)}
            <button class="gefahr" onclick="stammdaten.frage('sd-loesen-${d.id}', true)" ${sperre()}>Zuordnung lösen</button></div>` },
          notiz: NOTIZ_TG,
          folgen: (d, werte) => dokumentFolgen({ ...werte, ...(werte.link_status ? { status: werte.link_status } : {}) }),
          speichern: async (d, werte, opt) => {
            const { link_status, ...rest } = werte;
            await dokumentSpeichernTG(d.id, rest, opt);
            if (link_status) await api(`${S}/parts/${p.id}/documents/${d.id}`, { method: "PATCH", json: mitNotiz(opt.von, opt.notiz, { status: link_status }) });
          },
          nachher: neu,
        }) + `
          ${p.documents.map(d => `<div class="sd-frage" id="sd-loesen-${d.id}" hidden>
            <strong>${esc(dokName(d))}</strong>: Die Zuordnung zu diesem Bauteil wird entfernt. Das Dokument und die Datei bleiben erhalten, und das Änderungsprotokoll hält fest, wer die Zuordnung gelöst hat.
            <div class="zeile" style="margin-top:8px"><button class="gefahr" onclick="stammdaten.loesen(${d.id})" ${sperre()}>Ja, Zuordnung lösen</button>
              <button class="leise" onclick="stammdaten.frage('', false)">Abbrechen</button></div></div>`).join("")}
          <label for="sd-z-notiz" class="nur-schreiben">Notiz zum Lösen einer Zuordnung, falls nötig</label>
          <div class="zeile nur-schreiben"><input type="text" id="sd-z-notiz" placeholder="${NOTIZ}" style="flex:1">${nameHinweis()}</div>
          <div id="sd-pdf"></div>`
        : `<p class="grau" style="margin:0">Diesem Bauteil ist noch kein Dokument zugeordnet.</p>`}
        <details class="sd-klapp nur-schreiben" ontoggle="if (this.open) stammdaten.freieDokumente()"><summary>Vorhandenes Dokument zuordnen</summary>
          <p class="sd-neben">Hier ordnen Sie ein vorhandenes Dokument zu, zum Beispiel eines, das noch zu keinem Bauteil gehört. Neue PDFs fügen Sie beim Artikel im Vorgang ein.</p>
          <div id="sd-frei"><span class="grau">lädt …</span></div>
        </details>
      </div>

      <div class="karte">
        <h2>In Vorgängen (${p.bom_items.length})</h2>
        ${p.bom_items.length ? `<div class="tabelle" title="Diese Liste ergibt sich aus den Vorgängen und lässt sich hier nicht ändern."><table><thead><tr><th>Vorgang</th><th>Pos.</th><th>Stand</th></tr></thead><tbody>
          ${p.bom_items.map(i => `<tr class="klick" onclick="location.hash='#/v/${i.bom_id}/a/${i.item_id}'">
            <td><a href="#/v/${i.bom_id}">Vorgang ${i.bom_id}</a></td><td>${esc(i.pos)}</td><td>${marke(...(POS_STAND[i.status] || [i.status, "s-wartet"]))}</td></tr>`).join("")}
          </tbody></table></div>` : `<p class="grau" style="margin:0">Dieses Bauteil kommt in keinem Vorgang vor. Keine Position einer Stückliste hat diese PRO.FILE-Nr.</p>`}
      </div>

      <div class="karte">
        <h2>Änderungen</h2>
        ${protokoll(p.changes, "Bisher hat niemand das Bauteil von Hand geändert. Was eine Freigabe angelegt hat, steht beim Artikel im Vorgang.")}
      </div>`;
  }

  const zDok = docId => z.p.documents.find(d => d.id === docId);
  function loesen(docId) {
    mitName(von => tu(() => api(`${S}/parts/${z.p.id}/documents/${docId}`, { method: "DELETE", json: koerper(von, "sd-z-notiz") }),
      `Die Zuordnung von ${dokName(zDok(docId))} ist gelöst.`));
  }
  async function freieDokumente() {
    const el = $("#sd-frei"), p = z.p;
    if (!el || z.frei) return;
    try {
      const eigene = new Set(p.documents.map(d => d.id));
      z.frei = (await api(`${S}/documents`)).filter(d => !eigene.has(d.id));
    } catch (e) { el.innerHTML = `<span class="unbelegt">${esc(e.message)}</span>`; return; }
    if (!$("#sd-frei")) return;
    el.innerHTML = !z.frei.length ? `<span class="grau">Es gibt kein weiteres Dokument, das Sie zuordnen könnten.</span>` : `
      <label for="sd-frei-doc">Dokument</label>
      <select class="sd-wahl" id="sd-frei-doc">${z.frei.map(d => `<option value="${d.id}">${esc(`Nr. ${d.id} · ${dokName(d)} · ${d.parts.length ? "bei " + d.parts.map(x => x.profile_no).join(", ") : "noch ohne Bauteil"}`)}</option>`).join("")}</select>
      <label class="sd-haken"><input type="checkbox" id="sd-frei-geprueft"> Gleich als geprüft markieren, mit meinem Namen</label>
      <label for="sd-frei-notiz">Notiz, falls nötig</label><input type="text" id="sd-frei-notiz">
      <div class="sd-rechts">${nameHinweis()}<button onclick="stammdaten.zuordnen()" ${sperre()}>Dokument zuordnen</button></div>`;
  }
  function zuordnen() {
    mitName(von => {
      const docId = +wert("sd-frei-doc"), d = (z.frei || []).find(x => x.id === docId);
      if (!d) return toast("Bitte wählen Sie zuerst ein Dokument aus.");
      tu(() => api(`${S}/parts/${z.p.id}/documents`, { method: "POST",
        json: koerper(von, "sd-frei-notiz", { document_id: docId, status: an("sd-frei-geprueft") ? "geprueft" : "ungeprueft" }) }),
        `${dokName(d)} ist zugeordnet.`);
    });
  }

  // ---------- dokumente ----------
  async function dokumenteSeite(app) {
    app.innerHTML = `${kopf("dokumente")}
      <div class="karte">
        <div class="sd-suche" style="grid-template-columns:minmax(0,1fr) 290px">
          <div><label for="sd-dq">Suche</label><input type="text" id="sd-dq" value="${esc(z.df.q)}" placeholder="Datei, Link oder Zertifikatsnummer" oninput="stammdaten.filtern()"></div>
          <div><label for="sd-dart">Dokumentart</label><select class="sd-wahl" id="sd-dart" onchange="stammdaten.filtern(true)"><option value="">Alle</option>
            ${z.werte.doc_types.map(a => `<option value="${esc(a)}" ${a === z.df.art ? "selected" : ""}>${esc(art(a))}</option>`).join("")}</select></div>
        </div>
        <label class="sd-haken"><input type="checkbox" id="sd-dohne" ${z.df.ohne ? "checked" : ""} onchange="stammdaten.filtern(true)"> Nur Dokumente ohne Bauteil</label>
      </div>
      <div class="karte"><div id="sd-liste"><div class="leer">lädt …</div></div></div>`;
    await ladeDokumente();
  }

  async function ladeDokumente() {
    const n = ++lauf, wache = seitenWache();
    let docs;
    try { docs = await abfrage(`${S}/documents`, { q: z.df.q, doc_type: z.df.art, unlinked: z.df.ohne ? "1" : "" }); }
    catch (e) { if (wache() && n === lauf && $("#sd-liste")) $("#sd-liste").innerHTML = `<div class="unbelegt">${esc(e.message)}</div>`; return; }
    if (!wache() || n !== lauf || !$("#sd-dq")) return;
    $("#sd-liste").innerHTML = !docs.length ? `<div class="leer">Kein Dokument passt zu Ihrer Suche.</div>`
      : Tabelle.html({
        id: "sd-dokumente", titel: "Dokumente", zeilen: docs, name: d => `Dokument ${d.id}`,
        spalten: [
          { s: "id", titel: "Nr.", gesperrt: "Die Nummer des Dokuments lässt sich nicht ändern." },
          dokSpalten()[0],
          { s: "datei", titel: "Datei", wert: d => dateiLesbar(d.file_path || d.url), gesperrt: "Hier liegt das Dokument. Der Ablageort lässt sich nicht ändern.", stil: "font-size:12px;word-break:break-all",
            zeige: (w, d) => `<span class="mono">${esc(w)}</span>${d.file_path && !d.file_exists ? ` <span class="unbelegt">Die Datei fehlt.</span>` : ""}` },
          ...dokSpalten().slice(1),
          { s: "bauteile", titel: "Bauteile", wert: d => d.parts.map(x => x.profile_no).join(", "), gesperrt: "Zuordnungen ändern Sie beim Bauteil.", zeige: w => w ? esc(w) : `<span class="grau">keines</span>` },
        ],
        zusatz: { zelle: d => `<a class="knopf leise" href="#/stammdaten/dokument/${d.id}" style="padding:3px 10px;white-space:nowrap">Öffnen →</a>` },
        notiz: NOTIZ_TG,
        folgen: (d, werte) => dokumentFolgen(werte),
        speichern: (d, werte, opt) => dokumentSpeichernTG(d.id, werte, opt),
        nachher: ladeDokumente,
      }) + `<p class="sd-neben" style="margin:10px 0 0">${docs.length} Dokument${docs.length === 1 ? "" : "e"}.</p>`;
  }

  async function dokumentSeite(app, id, wache) {
    const d = await api(`${S}/documents/${id}`);
    if (!wache()) return;
    z.d = d;
    const arten = z.werte.doc_types.includes(d.doc_type) ? z.werte.doc_types : [d.doc_type, ...z.werte.doc_types];
    app.innerHTML = `
      <div class="kopfzeile">
        <div><a href="#/stammdaten/dokumente" class="grau" style="text-decoration:none">← Stammdaten · Dokumente</a>
          <h1>Dokument ${d.id} · ${esc(art(d.doc_type))}</h1>
          <div class="grau"><span class="mono">${esc(dateiLesbar(d.file_path || d.url))}</span>${d.file_path && !d.file_exists ? ` · <span class="unbelegt">Die Datei fehlt im Ordner.</span>` : ""}${d.changed_by ? ` · zuletzt geändert von ${esc(d.changed_by)}` : ""}</div></div>
      </div>
      ${darfSchreiben() ? "" : `<div class="hinweis sd-warn">${esc(sperrgrund())} Ändern, prüfen und löschen können Sie deshalb nicht.</div>`}
      <div class="karte">
        <h2>Angaben</h2>
        <p style="margin-top:-4px">${d.parts.length ? "Bei Bauteil " + d.parts.map(x =>
          `<a href="#/stammdaten/teil/${x.id}" class="mono">${esc(x.profile_no)}</a> ${esc(x.name_de)} ${pruefChip(x.link_status)}`).join(", ")
          : `<span class="grau">Das Dokument gehört noch zu keinem Bauteil.</span>`}</p>
        ${Tabelle.html({
          id: `sd-dok-${d.id}`, titel: `Dokument ${d.id}`, schluessel: f => f.feld, name: f => f.titel,
          zeilen: [
            { feld: "doc_type", titel: "Dokumentart", wert: d.doc_type, art: "auswahl", optionen: arten.map(a => [a, art(a)]), leer: false },
            { feld: "certificate_no", titel: "Zertifikats-Nr.", wert: d.certificate_no ?? "" },
            { feld: "valid_until", titel: "Gültig bis", wert: d.valid_until ?? "", art: "datum" },
            { feld: "languages", titel: `Sprachen (${z.werte.languages.join(", ")}, mit Komma)`, wert: sprachText(d.languages) },
          ],
          spalten: feldSpalten(),
          notiz: NOTIZ_TG,
          folgen: (f, werte) => dokumentFolgen({ [f.feld]: werte.wert }),
          speichern: (f, werte, opt) => dokumentSpeichernTG(d.id, { [f.feld]: werte.wert }, opt),
          nachher: neu,
        })}
        <label for="sd-d-notiz" class="nur-schreiben">Notiz zum Löschen, falls nötig</label><input type="text" class="nur-schreiben" id="sd-d-notiz" placeholder="${NOTIZ}">
        <div class="zeile sd-klapp">
          ${pdfKnopf(d)}
          ${d.parts.length ? `<span class="sd-neben">Löschen können Sie das Dokument erst, wenn es zu keinem Bauteil mehr gehört.</span>`
            : `<button class="gefahr" onclick="stammdaten.frage('sd-loeschen', true)" ${sperre()}>Dokument löschen</button>`}
        </div>
        <div class="sd-frage" id="sd-loeschen" hidden>Das Dokument verschwindet aus den Stammdaten. Die Datei im Ordner bleibt erhalten, und das Änderungsprotokoll hält fest, wer gelöscht hat.
          <div class="zeile" style="margin-top:8px"><button class="gefahr" onclick="stammdaten.dokumentLoeschen()" ${sperre()}>Ja, Dokument löschen</button>
            <button class="leise" onclick="stammdaten.frage('', false)">Abbrechen</button></div></div>
        <div id="sd-pdf"></div>
      </div>
      <div class="karte"><h2>Änderungen</h2>${protokoll(d.changes, "Bisher hat niemand das Dokument von Hand geändert.")}</div>`;
  }

  function dokumentLoeschen() {
    mitName(von => tu(() => api(`${S}/documents/${z.d.id}`, { method: "DELETE", json: koerper(von, "sd-d-notiz") }),
      `Das Dokument ${z.d.id} ist gelöscht.`, () => geh("dokumente")));
  }

  // ---------- hersteller mit kontakt (unternehmen ↔ e-mail) ----------
  // Die Liste kommt aus /api/kontakte: alle Hersteller der Stammdaten und die, die bisher nur in
  // einer Stückliste stehen (hersteller_id null). Für diese legt POST /api/kontakte den Hersteller
  // mit der Adresse an; bekannte ändert PUT /api/kontakte/<id>. Fehlt der Endpunkt, bleibt es bei
  // Name und Bauteilen aus /api/stammdaten/manufacturers.
  const SPRACHE_ANFRAGE = [["de", "Deutsch"], ["en", "Englisch"]];
  const ohneMail = () => (z.kontakte || []).filter(k => k.fehlt).length;
  async function ladeKontakte() {
    try {
      const r = await fetch(`${API}/api/kontakte`);
      const j = r.ok ? await r.json() : null;
      z.kontakte = Array.isArray(j) ? j : null;
    } catch (e) { z.kontakte = null; console.error("Kontakte nicht ladbar:", e); }
  }

  function herstellerZeilen() {
    const teile = Object.fromEntries(z.hersteller.map(m => [m.id, m.part_count]));
    if (!z.kontakte) return z.hersteller.map(m => ({ ...m, schl: `m${m.id}`, bekannt: true }));
    return z.kontakte.map(k => ({
      schl: k.hersteller_id ? `m${k.hersteller_id}` : `n-${k.normalized_name}`, id: k.hersteller_id, bekannt: !!k.hersteller_id,
      name: k.hersteller, part_count: k.hersteller_id ? (teile[k.hersteller_id] || 0) : 0,
      email: k.email || "", ansprechpartner: k.ansprechpartner || "", sprache: k.sprache || "", knotiz: k.notiz || "",
      geaendert_von: k.geaendert_von || "", geaendert_am: k.geaendert_am || "", fehlt: k.fehlt, offen: k.offene_entwuerfe || 0,
    }));
  }

  function berichtHtml(b) {
    if (!b) return "";
    const n = l => (l || []).length;
    const saetze = [];
    if (n(b.uebernommen)) saetze.push(n(b.uebernommen) === 1 ? "Eine Adresse ist übernommen." : `${n(b.uebernommen)} Adressen sind übernommen.`);
    if (n(b.unveraendert)) saetze.push(n(b.unveraendert) === 1 ? "Eine Adresse stand schon so da." : `${n(b.unveraendert)} Adressen standen schon so da.`);
    if (n(b.angelegt)) saetze.push(`Neu in den Stammdaten ${n(b.angelegt) === 1 ? "ist" : "sind"} ${b.angelegt.join(", ")}.`);
    if (n(b.ohne_email)) saetze.push(n(b.ohne_email) === 1 ? "Eine Zeile ohne E-Mail-Adresse ist übersprungen." : `${n(b.ohne_email)} Zeilen ohne E-Mail-Adresse sind übersprungen.`);
    if (b.entwuerfe_aktualisiert) saetze.push(b.entwuerfe_aktualisiert === 1 ? "Ein wartender E-Mail-Entwurf hat jetzt eine Adresse." : `${b.entwuerfe_aktualisiert} wartende E-Mail-Entwürfe haben jetzt eine Adresse.`);
    const probleme = [...(b.unbekannt || []), ...(b.ungueltig || [])];
    return `<div class="hinweis ${probleme.length ? "sd-warn" : "gruen"}" style="margin-top:10px"><strong>${esc(b.datei)}</strong>: ${esc(saetze.join(" ") || "Die Datei enthielt keine Adressen.")}
      ${probleme.length ? `<div style="margin-top:4px">Nicht übernommen:</div><ul style="margin:2px 0 0;padding-left:18px">${probleme.map(p => `<li>Zeile ${esc(p.zeile)}${p.hersteller ? ` (${esc(p.hersteller)})` : ""}: ${esc(p.grund)}</li>`).join("")}</ul>` : ""}</div>`;
  }

  function herstellerSeite(app) {
    const mitKontakt = !!z.kontakte;
    const liste = herstellerZeilen().filter(m => !z.nurOhneMail || m.fehlt);
    const nurStueckliste = "Diesen Hersteller kennt bisher nur eine Stückliste. Mit einer E-Mail-Adresse legen Sie ihn in den Stammdaten an.";
    app.innerHTML = `${kopf("hersteller")}
      ${mitKontakt ? `<div class="karte">
        <div class="zeile" style="justify-content:space-between">
          <label class="sd-haken" style="margin:0"><input type="checkbox" ${z.nurOhneMail ? "checked" : ""} onchange="stammdaten.nurOhneMail(this.checked)"> Nur Hersteller ohne E-Mail-Adresse (${ohneMail()})</label>
          <div class="zeile">
            ${dateiwahl('id="sd-k-datei" accept=".xlsx,.csv"', { titel: "Datei mit Kontakten", klasse: "nur-schreiben" })}
            <button class="leise nur-schreiben" onclick="stammdaten.kontakteFrage()" ${sperre()}>Kontakte importieren</button>
            <a class="knopf leise" href="${API}/api/kontakte/export.xlsx">Kontakte als Excel herunterladen</a>
          </div>
        </div>
        <p class="sd-neben nur-schreiben" style="margin:8px 0 0">Die Datei braucht die Spalten Hersteller und E-Mail, dazu wahlweise Ansprechpartner, Sprache und Notiz. Leere Zellen ändern nichts.</p>
        <div class="sd-frage" id="sd-k-frage" hidden></div>
        ${berichtHtml(z.importBericht)}
      </div>` : ""}
      <div class="karte">
        ${!liste.length && !z.nurOhneMail ? `<div class="leer">Noch gibt es keinen Hersteller. Hersteller entstehen, wenn Sie eine Stückliste hochladen, einen Fund freigeben oder ein Bauteil anlegen.</div>` : Tabelle.html({
          id: "sd-hersteller", titel: "Hersteller", zeilen: liste, schluessel: m => m.schl, name: m => `Hersteller „${m.name}“`,
          leer: "Jeder Hersteller hat eine E-Mail-Adresse.",
          zeileKlasse: m => m.fehlt ? "sd-ohne-mail" : "",
          spalten: [
            { s: "name", titel: "Name", gesperrt: m => m.bekannt ? "" : nurStueckliste,
              zeige: (w, m) => `${esc(w)}${m.bekannt ? "" : `<div class="sd-neben">bisher nur in einer Stückliste</div>`}` },
            { s: "part_count", titel: "Bauteile", gesperrt: "Die Zahl der Bauteile ergibt sich aus den Stammdaten.",
              zeige: (w, m) => w ? `<a href="#/stammdaten" onclick="stammdaten.nurHersteller(${m.id})">${w}</a>` : "0" },
            ...(mitKontakt ? [
              { s: "email", titel: "E-Mail", zeige: w => w ? `<span class="mono" style="font-size:12.5px">${esc(w)}</span>` : '<span class="unbelegt">E-Mail fehlt</span>' },
              { s: "ansprechpartner", titel: "Ansprechpartner" },
              { s: "sprache", titel: "Sprache der Anfrage", art: "auswahl", optionen: SPRACHE_ANFRAGE, leerText: "–" },
              { s: "knotiz", titel: "Notiz" },
              { s: "offen", titel: "Wartende Entwürfe", gesperrt: "So viele E-Mail-Entwürfe an diesen Hersteller sind noch nicht gesendet.",
                zeige: (w, m) => w ? `<span class="${m.fehlt ? "unbelegt" : ""}">${w}</span>` : '<span class="grau">–</span>' },
              { s: "geaendert", titel: "Zuletzt geändert", wert: m => m.geaendert_von, gesperrt: "Hier steht, wer den Kontakt zuletzt gespeichert hat.",
                zeige: (w, m) => w ? `${esc(w)}<div class="sd-neben">${esc(zeit(m.geaendert_am))}</div>` : '<span class="grau">–</span>' },
            ] : []),
          ],
          notiz: NOTIZ_TG,
          folgen: (m, werte) => [
            ..."name" in werte ? [`Der Name gilt für alle ${m.part_count} Bauteile des Herstellers. Eine spätere Freigabe findet ihn nur unter dem neuen Namen wieder.`] : [],
            ..."email" in werte ? [werte.email ? "Anfragen an diesen Hersteller gehen künftig an diese Adresse. Wartende Entwürfe bekommen sie auch." : "Ohne Adresse steht der Hersteller danach unter „E-Mail fehlt“, und Entwürfe an ihn lassen sich nicht senden."] : [],
            ...!m.bekannt ? ["Der Hersteller wird dabei in den Stammdaten angelegt."] : [],
            ..."sprache" in werte ? ["E-Mail-Anfragen an diesen Hersteller werden in dieser Sprache entworfen."] : [],
          ],
          speichern: async (m, werte, { von, notiz }) => {
            const { name, ...kontakt } = werte;
            if (name !== undefined && m.bekannt) await api(`${S}/manufacturers/${m.id}`, { method: "PATCH", json: mitNotiz(von, notiz, { name }) });
            if (!Object.keys(kontakt).length) return;
            const neu_ = { email: m.email, ansprechpartner: m.ansprechpartner, sprache: m.sprache, knotiz: m.knotiz, ...kontakt };
            const daten = { by: von, email: neu_.email || "", ansprechpartner: neu_.ansprechpartner || null,
              sprache: neu_.sprache || null, notiz: neu_.knotiz || null };
            if (m.bekannt) await api(`/api/kontakte/${m.id}`, { method: "PUT", json: daten });
            else await api("/api/kontakte", { method: "POST", json: { ...daten, hersteller: m.name } });
          },
          nachher: neu,
        })}
      </div>`;
  }
  function kontakteFrage() {
    const f = $("#sd-k-datei")?.files[0], el = $("#sd-k-frage");
    if (!f) return toast("Bitte wählen Sie zuerst eine Excel- oder CSV-Datei aus.");
    if (!/\.(xlsx|csv)$/i.test(f.name)) return toast(`„${f.name}“ ist keine Excel- oder CSV-Datei. Bitte wählen Sie eine andere Datei.`);
    el.innerHTML = `<strong>${esc(f.name)}</strong>: Die Kontakte aus der Datei ersetzen die Angaben derselben Hersteller. Leere Zellen ändern nichts. Jede Änderung steht mit Ihrem Namen im Protokoll.
      <div class="zeile" style="margin-top:8px"><button onclick="stammdaten.kontakteImport()" ${sperre()}>Ja, Kontakte importieren</button>
        <button class="leise" onclick="stammdaten.frage('', false)">Abbrechen</button></div>`;
    el.hidden = false;
  }
  function kontakteImport() {
    const f = $("#sd-k-datei")?.files[0];
    if (!f) return toast("Bitte wählen Sie zuerst eine Datei aus.");
    mitName(von => {
      const fd = new FormData(); fd.append("datei", f); fd.append("by", von);
      tu(() => api("/api/kontakte/import", { method: "POST", body: fd }),
        a => { z.importBericht = a; return `Die Datei ${f.name} ist eingelesen. Das Ergebnis steht oben auf der Seite.`; });
    });
  }
  function nurOhneMail(an) { z.nurOhneMail = an; herstellerSeite($("#app")); }
  function nurHersteller(id) { z.f = { q: "", h: String(id), s: "", pruefen: false }; }
  // ---------- änderungsprotokoll über alles ----------
  async function aenderungenSeite(app, wache) {
    const aenderungen = await api(`${S}/changes`);
    if (!wache()) return;
    app.innerHTML = `${kopf("aenderungen")}
      <div class="karte"><h2>Alle Änderungen an den Stammdaten</h2>
        ${protokoll(aenderungen, "Bisher hat niemand die Stammdaten von Hand geändert.", true)}
        ${aenderungen.length >= 200 ? `<p class="sd-neben" style="margin:10px 0 0">Hier stehen die neuesten 200 Änderungen.</p>` : ""}</div>`;
  }

  // wache (index.html, seitenWache): nach jedem await nur zeichnen, wenn die Seite noch dieselbe ist
  async function zeige(m, app, wache = seitenWache()) {
    lauf++;
    const [, bereich, id] = m;
    if (!document.getElementById("sd-liste") && !app.querySelector(".sd-gitter")) app.innerHTML = `<div class="leer">lädt …</div>`;
    const [werte, hersteller] = await Promise.all([z.werte || api(`${S}/werte`), api(`${S}/manufacturers`), ladeKontakte()]);
    if (!wache()) return;
    [z.werte, z.hersteller] = [werte, hersteller];
    if (bereich === "teil" && id) return bauteilSeite(app, +id, wache);
    if (bereich === "dokument" && id) return dokumentSeite(app, +id, wache);
    if (bereich === "dokumente") return dokumenteSeite(app);
    if (bereich === "hersteller") return herstellerSeite(app);
    if (bereich === "aenderungen") return aenderungenSeite(app, wache);
    return bauteileSeite(app);
  }

  window.stammdaten = { geh, filtern, anlegen, loesen, frage, pdf, freieDokumente, zuordnen, kontakteFrage, kontakteImport, nurOhneMail,
    dokumentLoeschen, nurHersteller };

  const SYMBOL = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>';
  erweitere("seiten", { name: "stammdaten", muster: /^#\/stammdaten(?:\/([a-z]+))?(?:\/(\d+))?/, zeige });
  erweitere("nav", { name: "stammdaten", href: "#/stammdaten", text: "Stammdaten", icon: SYMBOL });
})();
