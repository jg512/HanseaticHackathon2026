// EX-Bauteil-Liste, Meldungen der Eingangsprüfung, Sprachversionen und Downloads eines Vorgangs.
// Seiten: #/v/<id>/ex-liste und #/v/<id>/sprachen; dazu ein Abschnitt auf der Vorgangsseite.
(function () {
  const EINORDNUNG = {
    ex_teil:      { text: "Ex-Teil",            chip: "s-geprueft" },
    klaerung:     { text: "Entscheidung nötig", chip: "s-zu_pruefen" },
    kein_ex_teil: { text: "Kein Ex-Teil",       chip: "s-kein_ex" },
  };
  // Stufen der Eingangsprüfung, die schwerste zuerst
  const STUFEN = {
    error:   { text: "Fehler",    chip: "s-info_fehlt" },
    warning: { text: "Warnungen", chip: "s-zu_pruefen" },
    info:    { text: "Hinweise",  chip: "s-wartet" },
  };
  // Dokumentarten mit Pflichtsprachen
  const ART = {
    eu_konformitaetserklaerung: "EU-Konformitätserklärung",
    eu_baumusterpruefbescheinigung: "EU-Baumusterprüfbescheinigung",
    bedienungsanleitung: "Bedienungsanleitung",
  };

  // je Vorgang: { ex, bom, spr, zeit }; Filter gelten für die offene Seite
  const daten = {};
  const sicht = { exFilter: "alle", sprFilter: "alle", erledigteZeigen: true, abhaken: new Set() };  // abhaken: Meldungen mit offenem Kommentarfeld

  const stil = document.createElement("style");
  stil.textContent = `
    .exl-knoepfe { display: flex; gap: 4px; flex-wrap: wrap; }
    .exl-knoepfe button { padding: 3px 9px; font-size: 12px; }
    .exl-klein { font-size: 12px; color: var(--grau); margin-top: 3px; }
    .exl-abgehakt { color: var(--grau); text-decoration: line-through; }
    .exl-meldung { display: grid; grid-template-columns: 130px minmax(0, 1fr) auto; gap: 6px 12px; align-items: start; padding: 9px 0; border-bottom: 1px solid var(--linie); }
    .exl-meldung:last-child { border-bottom: none; }
    .exl-meldung .exl-tun { display: flex; gap: 6px; align-items: center; }
    .exl-meldung input[type=text] { width: 260px; max-width: 34vw; padding: 4px 7px; font-size: 13px; }
    .exl-stufe { display: flex; gap: 8px; align-items: center; margin: 14px 0 2px; }
    .exl-stufe:first-of-type { margin-top: 0; }
    .exl-sprache { display: inline-block; min-width: 30px; text-align: center; padding: 1px 7px; border-radius: 2px; font-size: 12px; font-weight: 600; margin-right: 4px; background: var(--gruen-mittel); color: var(--gruen); }
    .exl-sprache.fehlt { background: var(--rot-hell); color: var(--rot); }
    .exl-zahlen { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 16px; }
    details.exl-klapp > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; }
    details.exl-klapp > summary::-webkit-details-marker { display: none; }
    details.exl-klapp > summary::before { content: "▸"; color: var(--grau); }
    details.exl-klapp[open] > summary::before { content: "▾"; }
    details.exl-klapp[open] > summary { margin-bottom: 10px; }
    @media (max-width: 900px) { .exl-meldung { grid-template-columns: 1fr; } }`;
  document.head.appendChild(stil);

  // „13.11.2026, 14:05 Uhr“ in Ortszeit; das Backend speichert UTC ohne Zone
  const zeit = iso => {
    if (!iso) return "";
    const d = new Date(/Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return isNaN(d) ? iso : `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
  };
  const mitZahl = (n, eins, mehr) => `${n} ${n === 1 ? eins : mehr}`;
  const pille = (text, klasse) => `<span class="chip ${klasse}">${esc(text)}</span>`;
  const offeneMeldungen = bom => bom.issues.filter(i => !i.resolved_at).length;
  const mitLuecke = spr => spr.items.filter(i => Object.keys(i.fehlende_sprachen || {}).length);

  // alles zu einem Vorgang holen; ohne zwang höchstens alle 4 Sekunden
  async function lade(vid, zwang = false) {
    const d = daten[vid] || (daten[vid] = { zeit: 0 });
    if (!zwang && Date.now() - d.zeit < 4000) return false;
    d.zeit = Date.now();
    const [ex, bom, spr] = await Promise.all([
      api(`/api/boms/${vid}/ex-liste`), api(`/api/boms/${vid}`), api(`/api/boms/${vid}/sprachen`),
    ]);
    const neu = JSON.stringify([ex, bom.issues, spr]);
    const geaendert = neu !== d.abbild;
    Object.assign(d, { ex, bom, spr, abbild: neu });
    if (geaendert) zeichneNav();  // Zahlen in der Leiste
    return geaendert;
  }

  // Seite alle paar Sekunden auffrischen (z. B. während die Suche läuft); route() räumt den Timer
  // ab (index.html: seiteIntervall), eine Antwort nach dem Seitenwechsel zeichnet nicht mehr
  function auffrischen(vid, name, zeichne, wache) {
    seiteIntervall(async () => {
      if (zustand.seite !== name || eingabeAktiv()) return;
      try { if (await lade(vid) && wache()) zeichne(vid); } catch { if (wache()) $("#api-status").textContent = "Die Verbindung ist unterbrochen …"; }
    }, 5000, wache);
  }

  const kopf = (v, titel, unter, rechts = "") => `
    <div class="kopfzeile">
      <div><a href="#/v/${v.id}" class="grau" style="text-decoration:none">← ${esc(v.name)}</a><h1>${esc(titel)}</h1>
        <div class="grau">${unter}</div></div>
      <div class="rechts">${rechts}</div>
    </div>`;

  // ---------- Seite EX-Bauteil-Liste (mit Meldungen der Eingangsprüfung) ----------
  async function exSeite(m, app, wache = seitenWache()) {
    const vid = +m[1];
    sicht.exFilter = "alle"; sicht.abhaken = new Set();
    if (!sicht.springe) window.scrollTo(0, 0);
    if (daten[vid]?.ex) zeichneEx(vid); else app.innerHTML = `<div class="leer">lädt …</div>`;
    await lade(vid, true);
    if (!wache()) return;
    zeichneEx(vid);
    auffrischen(vid, "ex-liste", zeichneEx, wache);
  }

  function zeichneEx(vid) {
    const d = daten[vid], v = zustand.v;
    if (zustand.seite !== "ex-liste" || !d?.ex || !v || v.id !== vid) return;
    const ex = d.ex;
    const zeilen = ex.items.filter(z => sicht.exFilter === "alle" || z.einordnung === sicht.exFilter);
    $("#app").innerHTML = `
      ${kopf(v, "EX-Bauteil-Liste", `Die Regeln schlagen je Position vor, ob sie ein Ex-Teil ist, und nennen Grund und Quelle. Was die Regeln nicht sicher sagen können, entscheiden Sie.
        <div style="margin-top:4px">Ex-Bereich laut Stückliste: <strong>${esc(ex.ex_area) || "nicht angegeben"}</strong>. Geforderte Kategorie: <strong>${esc(ex.required_category) || "nicht angegeben"}</strong>.</div>`,
        `<a class="knopf leise" href="${API}/api/boms/${vid}/ex-liste.xlsx" download="${esc(ex.file_name || "")}">EX-Bauteil-Liste als Excel</a>`)}
      <div class="karte" style="padding:12px">
        <div class="zeile" style="justify-content:space-between">
          <div class="filter" style="margin-bottom:0">
            <button class="${sicht.exFilter === "alle" ? "an" : ""}" onclick="exListe.filter('alle')">alle ${ex.total}</button>
            ${Object.entries(EINORDNUNG).map(([k, e]) => `<button class="${sicht.exFilter === k ? "an" : ""}" onclick="exListe.filter('${k}')">${esc(e.text)} ${ex[k]}</button>`).join("")}
          </div>
          ${nameHinweis()}
        </div>
        <div style="margin-top:10px">${exTabelle(vid, zeilen)}</div>
      </div>
      ${meldungenKarte(vid, d.bom)}`;
  }

  // Tabelle (seiten/tabelle.js): Angaben der Position und die Entscheidung als Auswahl; Vorschlag
  // und Begründung leiten die Regeln ab. Gespeichert wird erst nach Bestätigung, je Zeile.
  const ENTSCHEIDUNG = [["ex_teil", "Ist Ex-Teil"], ["kein_ex_teil", "Kein Ex-Teil"]];
  const ENTSCHEIDUNG_FOLGE = {
    ex_teil: "Die Position gilt als Ex-Teil und wird gesucht, auch wenn der Einbauort keine Zone nennt.",
    kein_ex_teil: "Die Position gilt als kein Ex-Teil. Sie wird nicht gesucht und steht nicht in der Kundenliste.",
    "": "Ihre Entscheidung wird zurückgenommen. Dann gilt wieder der Vorschlag der Regeln.",
  };
  function exTabelle(vid, zeilen) {
    const abgeleitet = "Das leiten die Regeln aus Einbauort und Kopfangaben ab. Es lässt sich nicht von Hand ändern.";
    return Tabelle.html({
      id: `ex-liste-${vid}`, titel: "EX-Bauteil-Liste", zeilen, schluessel: z => z.item_id, name: z => `Position ${z.pos}`,
      leer: "Keine Position hat diese Einordnung.",
      spalten: [
        { s: "pos", titel: "Pos.", gesperrt: "Die Positionsnummer kennzeichnet die Zeile der Stückliste und lässt sich nicht ändern." },
        { s: "description", titel: "Bezeichnung", zeige: w => w ? `<strong>${esc(w)}</strong>` : '<span class="grau">?</span>' },
        { s: "manufacturer", titel: "Hersteller" },
        { s: "manufacturer_article_no", titel: "Hersteller-Art.-Nr.", klasse: "mono" },
        { s: "location", titel: "Einbauort", zeige: (w, z) => `${w ? esc(w) : '<span class="unbelegt">ohne Einbauort</span>'}
          <div class="exl-klein">${z.zone ? esc(z.zone) : "keine Zone"}${z.kategorie ? ` · mindestens ${esc(z.kategorie)}` : ""}</div>
          ${z.kategorie_quelle ? `<div class="exl-klein">${esc(z.kategorie_quelle)}</div>` : ""}` },
        { s: "vorschlag", titel: "Vorschlag der Regel", gesperrt: abgeleitet, zeige: (w, z) =>
          `${pille(w === "ex_teil" ? "Ex-Teil" : "prüfen", w === "ex_teil" ? "s-geprueft" : "s-zu_pruefen")}<div class="exl-klein">${esc(z.regel_text)}</div>` },
        { s: "grund", titel: "Begründung und Quelle", gesperrt: abgeleitet, zeige: (w, z) =>
          `${esc(w)}<div class="exl-klein">Quelle: ${esc(z.quelle) || "–"}</div>${z.hinweis ? `<div class="unbelegt">${esc(z.hinweis)}</div>` : ""}` },
        { s: "entscheidung", titel: "Entscheidung", art: "auswahl", optionen: ENTSCHEIDUNG, leerText: "nach Regel (nicht entschieden)",
          wert: z => z.entscheidung || "", stil: "min-width:190px", zeige: (w, z) => {
            const gilt = w ? (w === "ex_teil" ? ["Ex-Teil", "s-geprueft"] : ["Kein Ex-Teil", "s-kein_ex"])
              : [EINORDNUNG[z.einordnung]?.text || z.einordnung_text || z.einordnung, EINORDNUNG[z.einordnung]?.chip || "s-wartet"];
            const unter = w === (z.entscheidung || "") && z.entschieden ? esc(z.entschieden)
              : w ? "noch nicht gespeichert" : z.einordnung === "klaerung" ? "Bitte entscheiden Sie." : "Vorschlag der Regeln, noch nicht entschieden";
            return `${pille(...gilt)}<div class="exl-klein">${unter}</div>`;
          } },
      ],
      zusatz: { zelle: z => `<a href="#/v/${vid}/a/${z.item_id}" title="Artikel öffnen">Artikel</a>` },
      notiz: "Notiz zur Korrektur, falls Sie eine festhalten möchten",
      folgen: (z, werte) => {
        const { entscheidung, ...felder } = werte;
        return [...(entscheidung !== undefined ? [ENTSCHEIDUNG_FOLGE[entscheidung] || ""] : []), ...positionsFolgen(felder)];
      },
      speichern: async (z, werte, opt) => {
        const { entscheidung, ...felder } = werte;
        if (Object.keys(felder).length) await positionSpeichern(z.item_id, felder, opt);
        if (entscheidung !== undefined) {
          await api(`/api/boms/${vid}/items/${z.item_id}/ex-teil`, { method: "POST",
            json: { ex_teil: entscheidung === "ex_teil" ? true : entscheidung === "kein_ex_teil" ? false : null, by: opt.von } });
        }
      },
      nachher: async () => { await lade(vid, true); zeichneEx(vid); },
    });
  }

  // ---------- Meldungen der Eingangsprüfung ----------
  function meldungenKarte(vid, bom) {
    const meldungen = bom.issues.slice().sort((a, b) =>
      (a.row_number || 0) - (b.row_number || 0) || a.id - b.id);
    const offen = offeneMeldungen(bom);
    const stand = !meldungen.length ? `<div class="hinweis gruen" style="margin-bottom:0">Bei der Eingangsprüfung ist nichts aufgefallen.</div>`
      : !offen ? `<div class="hinweis gruen">Alle Meldungen sind abgehakt.</div>` : "";
    const stufen = [...Object.keys(STUFEN), ...new Set(meldungen.map(i => i.severity).filter(s => !STUFEN[s]))];
    // zugeklappt, die Zahl offener Meldungen steht im Kopf
    return `<details class="karte exl-klapp" id="exl-meldungen" data-merk="exl-meldungen" ${offenAttr("exl-meldungen")}>
      <summary><h2 style="margin:0">Meldungen der Eingangsprüfung</h2>
        ${meldungen.length ? pille(offen ? `${offen} von ${meldungen.length} offen` : "alle abgehakt", offen ? "s-zu_pruefen" : "s-geprueft") : pille("keine", "s-geprueft")}</summary>
      ${nameHinweis()}
      <p class="grau" style="margin-top:0">Hier steht, was beim Einlesen der Stückliste gefehlt hat oder aufgefallen ist. Abhaken ändert nichts am Stand der Positionen oder an der Kundenliste.</p>
      ${stand}
      ${stufen.map(s => {
        const gruppe = meldungen.filter(i => i.severity === s);
        if (!gruppe.length) return "";
        const noch = gruppe.filter(i => !i.resolved_at).length;
        const st = STUFEN[s] || { text: s, chip: "s-wartet" };
        return `<div class="exl-stufe">${pille(st.text, noch ? st.chip : "s-geprueft")}<span class="grau" style="font-size:12px">${noch} von ${gruppe.length} offen</span></div>
          ${gruppe.map(i => meldungZeile(vid, i)).join("")}`;
      }).join("")}
    </details>`;
  }

  function meldungZeile(vid, i) {
    const wo = `Position ${esc(i.pos) || "?"}, Zeile ${esc(i.row_number ?? "?")}`;
    if (i.resolved_at) return `<div class="exl-meldung">
      <span class="grau">${wo}</span>
      <span><div class="exl-abgehakt">${esc(i.message)}</div>
        <div class="exl-klein">Abgehakt von ${esc(i.resolved_by)} am ${esc(zeit(i.resolved_at))}.${i.comment ? ` Kommentar: ${esc(i.comment)}` : ""}</div></span>
      <span class="exl-tun">${darfSchreiben() ? `<button class="leise" onclick="exListe.oeffne(${vid}, ${i.id})">Haken zurücknehmen</button>` : ""}</span>
    </div>`;
    // das Kommentarfeld erscheint erst, wenn jemand mit dem Abhaken beginnt
    const tun = !darfSchreiben() ? "" : sicht.abhaken.has(i.id)
      ? `<input type="text" id="exl-kommentar-${i.id}" placeholder="Kommentar, falls nötig" aria-label="Kommentar zur Meldung"
          onkeydown="if (event.key === 'Enter') exListe.hakeAb(${vid}, ${i.id}); if (event.key === 'Escape') exListe.abhakenZu(${i.id})">
        <button onclick="exListe.hakeAb(${vid}, ${i.id})">Meldung abhaken</button>
        <button class="icon" title="Abbrechen" aria-label="Abbrechen" onclick="exListe.abhakenZu(${i.id})">✕</button>`
      : `<button class="leise" onclick="exListe.abhakenAuf(${i.id})">Abhaken …</button>`;
    return `<div class="exl-meldung">
      <span class="grau">${wo}</span>
      <span>${esc(i.message)}</span>
      <span class="exl-tun">${tun}</span>
    </div>`;
  }

  // ---------- Seite Sprachversionen ----------
  async function sprachSeite(m, app, wache = seitenWache()) {
    const vid = +m[1];
    sicht.sprFilter = "alle";
    window.scrollTo(0, 0);
    if (daten[vid]?.spr) zeichneSprachen(vid); else app.innerHTML = `<div class="leer">lädt …</div>`;
    await lade(vid, true);
    if (!wache()) return;
    zeichneSprachen(vid);
    auffrischen(vid, "sprachen", zeichneSprachen, wache);
  }

  // Pflichtsprache (z. B. Deutsch, hält die Ausgabe auf): nur für die EU-Konformitätserklärung
  // (status.PFLICHT); pflicht_fehlt nennt sie nur, wo diese Erklärung vorliegt. Weitere Sprachen
  // (fehlende_sprachen) sind nur ein Hinweis. Ohne „pflichtsprache“ (ältere Fassung) gilt jede
  // fehlende Sprache als Lücke.
  const pflichtFehlt = i => Object.values(i.pflicht_fehlt || {}).some(l => l.length);
  const SPRACHNAME = { de: "deutsche", en: "englische", fr: "französische" };
  const gross = t => t.charAt(0).toUpperCase() + t.slice(1);

  function zeichneSprachen(vid) {
    const d = daten[vid], v = zustand.v;
    if (zustand.seite !== "sprachen" || !d?.spr || !v || v.id !== vid) return;
    const spr = d.spr, mitPflicht = !!spr.pflichtsprache;
    const pf = String(spr.pflichtsprache || "").toUpperCase();
    const sprachen = spr.sprachen.map(s => s.toUpperCase()).join(", ");
    const artikel = Object.fromEntries(v.artikel.map(a => [a.id, a]));
    const pflicht = mitPflicht ? spr.items.filter(pflichtFehlt) : [];
    const luecken = mitLuecke(spr);
    const mitDokument = spr.items.filter(i => Object.keys(i.sprachen || {}).length);
    const filter = sicht.sprFilter || "alle";
    const zeilen = filter === "pflicht" ? pflicht : filter === "luecken" ? luecken : spr.items;
    const fassung = SPRACHNAME[spr.pflichtsprache] ? `${SPRACHNAME[spr.pflichtsprache]} Fassung` : `Fassung in ${pf}`;
    $("#app").innerHTML = `
      ${kopf(v, "Sprachversionen", mitPflicht
        ? `Die ${esc(fassung)} der EU-Konformitätserklärung ist Pflicht, weitere Sprachen sind freiwillig. Hier sehen Sie je Position und Dokumentart, welche Sprachen vorliegen und welche fehlen.`
        : `Hier sehen Sie je Position und Dokumentart, welche Sprachen (${esc(sprachen)}) vorliegen und welche fehlen.`)}
      ${mitPflicht ? `<div class="hinweis ${pflicht.length ? "rot" : "gruen"}">
        ${pflicht.length ? `<strong>Bei ${mitZahl(pflicht.length, "Position", "Positionen")} fehlt die ${esc(fassung)} der EU-Konformitätserklärung.</strong> Der Agent hat im Ordner des Herstellers schon nach weiteren Sprachfassungen gesucht.`
          : `<strong>Die Pflichtsprache ist überall da.</strong> Wo eine EU-Konformitätserklärung vorliegt, gibt es sie auch als ${esc(fassung)}.`}
        Ohne die ${esc(fassung)} der EU-Konformitätserklärung lässt sich die Kundenliste nicht ausgeben. Andere Sprachen sind freiwillig.
        ${luecken.length ? `<div class="grund" style="font-weight:400">Bei ${mitZahl(luecken.length, "Position", "Positionen")} fehlt eine weitere Sprache. Das ist nur ein Hinweis.</div>` : ""}
      </div>` : `<div class="hinweis ${luecken.length ? "rot" : "gruen"}">
        ${luecken.length ? `<strong>Bei ${mitZahl(luecken.length, "Position", "Positionen")} fehlt eine Sprache.</strong> Der Agent hat im Ordner des Herstellers schon nach weiteren Sprachfassungen gesucht.`
          : `<strong>Es fehlt keine Sprache.</strong> Wo ein Dokument vorliegt, sind alle Sprachen da.`}
        Fehlende Sprachen halten die Kundenliste nicht auf.
      </div>`}
      <div class="karte" style="padding:12px">
        <div class="filter">
          <button class="${filter === "alle" ? "an" : ""}" onclick="exListe.luecken('alle')">alle ${spr.items.length}</button>
          ${mitPflicht ? `<button class="${filter === "pflicht" ? "an" : ""}" onclick="exListe.luecken('pflicht')">${esc(gross(fassung))} fehlt (Pflicht) ${pflicht.length}</button>` : ""}
          <button class="${filter === "luecken" ? "an" : ""}" onclick="exListe.luecken('luecken')">${mitPflicht ? "weitere Sprache fehlt" : "Sprache fehlt"} ${luecken.length}</button>
          <span class="grau" style="font-size:12px;align-self:center">${mitZahl(mitDokument.length, "Position hat", "Positionen haben")} schon ein Dokument.</span>
        </div>
        <div class="exl-klein" style="margin-bottom:4px">🔒 Die Sprachen werden aus den Dokumenten erkannt und lassen sich hier nicht ändern. Korrigieren können Sie sie beim Fund oder in den Stammdaten.</div>
        <div class="tabelle" title="Die Sprachen werden aus den Dokumenten erkannt und lassen sich hier nicht ändern."><table><thead><tr><th>Pos.</th><th>Bezeichnung</th><th>Dokumentart</th><th>Vorhanden</th><th>Fehlt</th><th>Status</th></tr></thead><tbody>
        ${zeilen.map(i => sprachZeilen(vid, i, artikel[i.id], mitPflicht ? fassung : null)).join("") || `<tr><td colspan="6" class="leer">${filter === "alle" ? "Dieser Vorgang hat keine Positionen." : "Keine Position hat diese Lücke."}</td></tr>`}
        </tbody></table></div>
        <div class="grau" style="font-size:12px;margin-top:8px">Geprüft werden ${Object.values(ART).join(", ").replace(/, ([^,]*)$/, " und $1")}, sobald ein solches Dokument vorliegt. Fehlt ein Dokument ganz, steht es in der Fehlliste.</div>
      </div>`;
  }

  function sprachZeilen(vid, i, a, fassung) {
    const sprache = (s, fehlt) => `<span class="exl-sprache ${fehlt ? "fehlt" : ""}">${esc(s.toUpperCase())}</span>`;
    const arten = Object.entries(i.sprachen || {});
    const kopfzellen = n => `<td ${n > 1 ? `rowspan="${n}"` : ""}>${esc(i.pos)}</td>
      <td ${n > 1 ? `rowspan="${n}"` : ""}><a href="#/v/${vid}/a/${i.id}" style="color:inherit;text-decoration:none"><strong>${esc(a?.bezeichnung) || "?"}</strong></a>
        <div class="exl-klein">${esc(a?.hersteller) || "–"} · <span class="mono" style="font-size:12px">${esc(a?.artikelnr) || "–"}</span></div></td>`;
    const status = n => `<td ${n > 1 ? `rowspan="${n}"` : ""}>${a ? chip(a.status, a.suche_in) : esc(i.status)}</td>`;
    if (!arten.length) return `<tr>${kopfzellen(1)}<td colspan="3" class="grau">${i.status === "kein_ex_teil" ? "Kein Ex-Teil, hier wird nichts gesucht." : "Noch kein Dokument da."}</td>${status(1)}</tr>`;
    return arten.map(([art, da], n) => {
      const fehlt = (i.fehlende_sprachen || {})[art] || [];
      const pflicht = fassung ? (i.pflicht_fehlt || {})[art] || [] : [];
      const zelle = fassung
        ? [pflicht.length ? `<div class="unbelegt">${esc(gross(fassung))} fehlt (Pflicht)</div>` : "",
           fehlt.length ? `<div class="exl-klein">Freiwillig fehlt noch: ${esc(fehlt.map(x => x.toUpperCase()).join(", "))}</div>` : ""].join("") || '<span class="grau">–</span>'
        : fehlt.length ? fehlt.map(s => sprache(s, true)).join("") : '<span class="grau">–</span>';
      return `<tr>${n === 0 ? kopfzellen(arten.length) : ""}
        <td>${esc(ART[art] || art)}</td>
        <td>${da.length ? da.map(s => sprache(s, false)).join("") : '<span class="grau">nicht erkannt</span>'}</td>
        <td>${zelle}</td>
        ${n === 0 ? status(arten.length) : ""}</tr>`;
    }).join("");
  }

  // ---------- Abschnitt auf der Vorgangsseite: Downloads und Stand ----------
  function abschnitt(v, el) {
    const zeichne = () => {
      const d = daten[v.id];
      const offen = d?.bom ? offeneMeldungen(d.bom) : null;
      // Marke auf dem geschlossenen Kopf: was hier noch zu tun ist
      if (d?.ex) {
        const pflicht = d.spr.pflichtsprache ? d.spr.items.filter(pflichtFehlt).length : 0;
        const teile = [offen ? mitZahl(offen, "Meldung offen", "Meldungen offen") : "", d.ex.klaerung ? `${d.ex.klaerung} zu entscheiden` : "",
          pflicht ? `${pflicht} ohne Pflichtsprache` : ""].filter(Boolean);
        teilMarke(el, teile.join(", "), pflicht ? "rot" : "gelb");
        teilKurz(el, "Fehlliste, EX-Bauteil-Liste, Sprachversionen");
      }
      el.innerHTML = `
        <div class="zeile">
          <a class="knopf leise" href="${API}/api/boms/${v.id}/missing.xlsx" title="Offene Positionen mit den Dokumenten, die noch fehlen">Fehlliste als Excel</a>
          <a class="knopf leise" href="${API}/api/boms/${v.id}/ex-liste.xlsx" title="Ex-Teile mit Zone, Kategorie, Begründung und Entscheidung">EX-Bauteil-Liste als Excel</a>
          <span class="grau" style="font-size:12px">Beide Listen können Sie jederzeit herunterladen, auch wenn der Vorgang noch nicht vollständig ist.</span>
        </div>
        ${v.vollstaendig ? "" : `<div class="zeile" style="margin-top:8px">
          <button class="leise" onclick="exListe.zwischenstand(${v.id})" title="Kundenliste mit dem bisherigen Stand, deutlich als unvollständig gekennzeichnet">Zwischenstand der Kundenliste (unvollständig)</button>
          <span class="grau" style="font-size:12px">Der Zwischenstand ist nur zur Ansicht. Die endgültige Kundenliste gibt es erst, wenn alles vollständig ist.</span></div>`}
        ${d?.ex ? `<div class="zeile" style="margin-top:12px">
          <a href="#/v/${v.id}/ex-liste">EX-Bauteil-Liste</a>
          ${pille(`${d.ex.ex_teil} Ex-Teile`, "s-geprueft")}
          ${d.ex.klaerung ? pille(`${d.ex.klaerung} zu entscheiden`, "s-zu_pruefen") : ""}
          ${d.ex.kein_ex_teil ? pille(`${d.ex.kein_ex_teil} kein Ex-Teil`, "s-kein_ex") : ""}
          <span class="grau">·</span>
          <a href="#/v/${v.id}/ex-liste" onclick="exListe.zuMeldungen()">Meldungen der Eingangsprüfung</a>
          ${d.bom.issues.length ? pille(`${offen} von ${d.bom.issues.length} offen`, offen ? "s-zu_pruefen" : "s-geprueft") : pille("keine", "s-geprueft")}
          <span class="grau">·</span>
          <a href="#/v/${v.id}/sprachen">Sprachversionen</a>
          ${d.spr.pflichtsprache && d.spr.items.filter(pflichtFehlt).length ? pille(`${d.spr.items.filter(pflichtFehlt).length} ohne ${SPRACHNAME[d.spr.pflichtsprache] || String(d.spr.pflichtsprache).toUpperCase()} Fassung (Pflicht)`, "s-info_fehlt") : ""}
          ${mitLuecke(d.spr).length ? pille(`${mitLuecke(d.spr).length} mit fehlender Sprache`, d.spr.pflichtsprache ? "s-zu_pruefen" : "s-info_fehlt") : pille("keine Sprache fehlt", "s-geprueft")}
        </div>` : ""}`;
    };
    zeichne();
    lade(v.id).then(geaendert => { if (geaendert && el.isConnected) zeichne(); }).catch(() => {});
  }

  // ---------- Aktionen ----------
  async function schreibe(vid, fn, ok, zeichne) {
    const wache = seitenWache();
    try {
      await fn();
      toast(ok);
      if (document.activeElement?.blur) document.activeElement.blur();
      await lade(vid, true);
      if (wache()) zeichne(vid);
    } catch (e) { toast(`Das hat nicht geklappt. ${e.message}`); }
  }

  window.exListe = {
    filter(f) { sicht.exFilter = f; zeichneEx(zustand.vid); },
    luecken(f) { sicht.sprFilter = f === true ? "luecken" : f === false ? "alle" : f; zeichneSprachen(zustand.vid); },
    entscheide(vid, itemId, wert) {
      const text = wert === true ? "Die Position gilt jetzt als Ex-Teil." : wert === false ? "Die Position gilt jetzt als kein Ex-Teil." : "Ihre Entscheidung ist zurückgenommen.";
      mitName(von => schreibe(vid, () => api(`/api/boms/${vid}/items/${itemId}/ex-teil`, { method: "POST", json: { ex_teil: wert, by: von } }), text, zeichneEx));
    },
    abhakenAuf(id) {
      sicht.abhaken.add(id); zeichneEx(zustand.vid);
      $(`#exl-kommentar-${id}`)?.focus();
    },
    abhakenZu(id) { sicht.abhaken.delete(id); document.activeElement?.blur(); zeichneEx(zustand.vid); },
    hakeAb(vid, issueId) {
      const kommentar = $(`#exl-kommentar-${issueId}`)?.value.trim() || "";
      sicht.abhaken.delete(issueId);
      mitName(von => schreibe(vid, () => api(`/api/boms/${vid}/issues/${issueId}/resolve`, { method: "POST", json: { by: von, comment: kommentar } }), "Die Meldung ist abgehakt.", zeichneEx));
    },
    oeffne(vid, issueId) {
      mitName(von => schreibe(vid, () => api(`/api/boms/${vid}/issues/${issueId}/reopen`, { method: "POST", json: { by: von } }), "Der Haken ist zurückgenommen.", zeichneEx));
    },
    // Zwischenstand laden (GET /api/vorgaenge/<id>/export?zwischenstand=1, immer eine Excel-Datei);
    // einen Fehler des Backends zeigt die Meldung mit dessen Text
    async zwischenstand(vid) {
      try {
        const r = await fetch(`${API}/api/vorgaenge/${vid}/export?zwischenstand=1`);
        const typ = r.headers.get("content-type") || "";
        if (r.status === 401) return;  // die Anmeldung erscheint schon
        if (!r.ok || typ.includes("json")) {
          const j = await r.json().catch(() => ({}));
          console.error("Zwischenstand nicht abrufbar:", r.status, j);
          return toast(j.fehler || j.error || "Der Zwischenstand ließ sich gerade nicht erstellen. Bitte versuchen Sie es gleich noch einmal.");
        }
        const name = (r.headers.get("content-disposition") || "").match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i)?.[1];
        const a = document.createElement("a");
        a.href = URL.createObjectURL(await r.blob());
        a.download = name ? decodeURIComponent(name) : `Zwischenstand_Vorgang_${vid}.xlsx`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      } catch (e) { console.error(e); toast("Der Zwischenstand ließ sich gerade nicht laden. Bitte versuchen Sie es gleich noch einmal."); }
    },
    // vom Abschnitt der Vorgangsseite: nach dem Laden der Seite zu den Meldungen springen
    zuMeldungen() { sicht.springe = true; },
  };

  // nach dem ersten Zeichnen der EX-Seite zu den Meldungen rollen, wenn der Link das wollte
  const _zeichneEx = zeichneEx;
  zeichneEx = function (vid) {
    _zeichneEx(vid);
    if (sicht.springe && $("#exl-meldungen")) { sicht.springe = false; $("#exl-meldungen").open = true; $("#exl-meldungen").scrollIntoView({ block: "start" }); }
  };

  const zahl = fn => v => { const d = daten[v?.id]; return d?.ex ? fn(d) : 0; };
  erweitere("seiten", { name: "ex-liste", muster: /^#\/v\/(\d+)\/ex-liste/, vorgang: true, zeige: exSeite });
  erweitere("seiten", { name: "sprachen", muster: /^#\/v\/(\d+)\/sprachen/, vorgang: true, zeige: sprachSeite });
  // Zahl: offene Meldungen der Eingangsprüfung plus offene Ex-Entscheidungen
  erweitere("nav", { name: "ex-liste", vorgang: true, href: v => `#/v/${v.id}/ex-liste`, text: "EX-Bauteil-Liste", icon: ICON.haken.replace(/width="19" height="19"/, 'width="17" height="17"'),
    zahl: zahl(d => offeneMeldungen(d.bom) + d.ex.klaerung) });
  erweitere("nav", { name: "sprachen", vorgang: true, href: v => `#/v/${v.id}/sprachen`, text: "Sprachversionen", icon: ICON.auge.replace(/width="19" height="19"/, 'width="17" height="17"'),
    zahl: zahl(d => d.spr.pflichtsprache ? d.spr.items.filter(pflichtFehlt).length : mitLuecke(d.spr).length) });
  erweitere("vorgang", { titel: "Listen und Eingangsprüfung", schluessel: "listen", zeichne: abschnitt });
})();
