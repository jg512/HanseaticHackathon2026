// Korrektur und Fragen je Vorgang.
//
// Korrektur (#/v/<id>/korrektur): Mängel der Stückliste, Mängelliste an den Einkauf mit
// Prüfung (anlegen, bearbeiten, freigeben, senden), Mängel als Tabelle (seiten/tabelle.js) mit Verlauf,
// erneute Suche nur der korrigierten Positionen, korrigierte Excel hochladen.
// Fragen (#/v/<id>/fragen): stellen, kommentieren, beantworten und klären, wieder öffnen.
//
// Endpunkte: docs/api.md, Abschnitte „Korrekturschleife (E5)“ und „Fragen“.
(function () {
  "use strict";

  const FELDER = {
    description: "Bezeichnung", manufacturer: "Hersteller", manufacturer_article_no: "Hersteller-Artikelnr.",
    profile_no: "PRO.FILE-Nr.", location: "Einbauort", quantity: "Menge",
  };
  // Stand der Position im Backend -> Chip der Oberfläche (Tabelle in docs/api.md)
  const STAND = {
    kein_ex_teil: "kein_ex", offen: "wartet", klaerung: "info_fehlt", vorschlag: "zu_pruefen",
    vorhanden: "geprueft", nicht_gefunden: "mail_entwurf", angefordert: "angefordert",
    eskaliert: "eskaliert", erledigt: "geprueft",
  };
  const LISTE = {
    entwurf: ["Entwurf", "s-wartet"], freigegeben: ["Freigegeben", "s-suche"],
    gesendet: ["Gesendet", "s-geprueft"],
  };
  const FRAGE = { offen: ["Offen", "s-zu_pruefen"], geklaert: ["Geklärt", "s-geprueft"] };

  const S = {
    vid: null, stand: null, bom: null,      // Korrektur: /korrektur und /api/boms/<id>
    meldung: null,                          // bleibender Hinweis oben: { art, html }
    sucht: false, datei: null, dateiFehler: "",
    auf: {},                                // aufgeklappte <details>
    fvid: null, fragen: null, fbom: null,   // Fragen
    frage: {},                              // gewählte Frage je Vorgang
    offen: {}, geholt: {},                  // offene Fragen je Vorgang für die Leiste
  };

  const css = document.createElement("style");
  css.textContent = `
    .kq select, .kq input[type=number] { font: inherit; padding: 7px 9px; border: 1px solid #ced4da; border-radius: 0; background: #fff; max-width: 100%; }
    .kq select:focus, .kq input[type=number]:focus { outline: none; border-color: var(--gruen-marke); box-shadow: 0 0 0 3px rgba(187, 208, 61, .35); }
    .kq textarea.klein { min-height: 84px; }
    .kq h2 .n { color: var(--grau); font-weight: 500; }
    .kq-text { white-space: pre-wrap; font: inherit; margin: 0; overflow-wrap: anywhere; }
    .kq-brief { border: 1px solid var(--linie); border-radius: var(--radius); background: var(--flaeche); padding: 12px 14px; }
    .kq-zwei { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
    .kq-rechts { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; margin-top: 10px; }
    .kq-klein { font-size: 12px; color: var(--grau); }
    .kq-fehlt { font-size: 12.5px; color: var(--rot); font-weight: 600; }
    .kq details { border-top: 1px solid var(--linie); margin-top: 14px; padding-top: 10px; }
    .kq details > summary { cursor: pointer; font-weight: 600; }
    .kq details.kq-karte { border-top: 1px solid var(--linie); margin-top: 0; padding-top: 14px; }
    .kq details.kq-karte > summary { list-style: none; display: flex; align-items: center; gap: 8px; }
    .kq details.kq-karte > summary::-webkit-details-marker { display: none; }
    .kq details.kq-karte > summary::before { content: "▸"; color: var(--grau); }
    .kq details.kq-karte[open] > summary::before { content: "▾"; }
    .kq details.kq-karte > summary h2 { margin: 0; }
    .kq details.kq-karte[open] > summary { margin-bottom: 12px; }
    .kq-alt { color: var(--grau); text-decoration: line-through; }
    .kq-meldung { display: flex; gap: 10px; align-items: flex-start; }
    .kq-meldung > div { flex: 1; }
    .kq-spalten { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 16px; align-items: start; }
    .kq-frage { padding: 10px 12px; border-bottom: 1px solid var(--linie); cursor: pointer; }
    .kq-frage:last-child { border-bottom: none; }
    .kq-frage:hover { background: var(--flaeche); }
    .kq-frage.an { background: var(--limette-hell); box-shadow: inset 3px 0 0 var(--limette); }
    .kq-frage .kurz { font-weight: 600; font-size: 13px; margin: 3px 0 1px; overflow-wrap: anywhere; }
    .kq-kommentar { display: grid; grid-template-columns: 170px minmax(0, 1fr); gap: 4px 12px; padding: 8px 0; border-bottom: 1px solid var(--linie); }
    .kq-kommentar:last-child { border-bottom: none; }
    @media (max-width: 900px) { .kq-spalten, .kq-zwei { grid-template-columns: 1fr; } .kq-kommentar { grid-template-columns: 1fr; } }
  `;
  document.head.appendChild(css);

  // ---------- helfer ----------
  const zeit = iso => {
    if (!iso) return "";
    const d = new Date(/Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    // „13.11.2026, 14:05 Uhr“
    return isNaN(d) ? String(iso) : `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
  };
  const pille = ([text, klasse]) => `<span class="chip ${klasse}">${esc(text)}</span>`;
  const standText = s => (STATUS[STAND[s]] || { text: s }).text;
  // Chip mit dem Stand, den auch die Artikelliste zeigt; sonst aus dem Stand des Backends abgeleitet
  function standChip(itemId, status) {
    const a = zustand.v?.artikel?.find(x => x.id === itemId);
    return chip(a ? a.status : STAND[status] || status, a?.suche_in);
  }
  const posName = i => `Position ${i.pos || "?"}: ${i.description || "–"}`;
  const mitZahl = (n, eins, mehr) => `${n} ${n === 1 ? eins : mehr}`;
  const tippt = () => typeof eingabeAktiv === "function" && eingabeAktiv();
  const hier = (seite, vid) => location.hash.startsWith(`#/v/${vid}/${seite}`);
  const kopf = (v, titel, unter) => `
    <div class="kopfzeile"><div><a href="#/v/${v.id}" class="grau" style="text-decoration:none">← ${esc(v.name)}</a>
      <h1>${titel}</h1><div class="grau">${unter}</div></div></div>
    ${darfSchreiben() ? "" : `<div class="hinweis" style="background:var(--gelb-hell);color:var(--gelb);border-left:3px solid var(--gelb)">${esc(sperrgrund())}</div>`}`;
  const details = (schl, titel, inhalt) => `
    <details ${S.auf[schl] ? "open" : ""} ontoggle="kq.auf('${schl}', this.open)"><summary>${titel}</summary>
      <div style="margin-top:10px">${inhalt}</div></details>`;

  // Getippte Werte über ein Neuzeichnen retten: Felder mit data-halten="<Wert vom Server>" behalten
  // ihre Eingabe, solange der Wert vom Server derselbe ist (nach dem Speichern gilt der neue).
  function zeichne(app, html) {
    const alt = {};
    app.querySelectorAll("[data-halten]").forEach(el => { alt[el.id] = [el.dataset.halten, el.value]; });
    const y = window.scrollY;
    app.innerHTML = `<div class="kq">${html}</div>`;
    app.querySelectorAll("[data-halten]").forEach(el => {
      const a = alt[el.id];
      if (a && a[0] === el.dataset.halten) el.value = a[1];
    });
    window.scrollTo(0, y);
  }
  const halten = wert => `data-halten="${esc(wert ?? "")}"`;
  const leeren = (...ids) => ids.forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });

  async function aktion(fn, ok, laden) {
    try {
      const r = await fn();
      if (ok) toast(typeof ok === "function" ? ok(r) : ok);
      await laden();
      return r;
    } catch (e) { toast(`Das hat nicht geklappt. ${e.message}`); }
  }

  // ====================================================================
  // Korrektur
  // ====================================================================
  async function korrekturSeite(m, app, wache = seitenWache()) {
    const vid = +m[1];
    if (S.vid !== vid) Object.assign(S, { vid, stand: null, bom: null, meldung: null, datei: null, dateiFehler: "", versucheEigen: null, versucheVorgang: null });
    app.innerHTML = `<div class="leer">lädt …</div>`;
    await korrekturLaden(vid, true);
    if (!wache() || !hier("korrektur", vid)) return;
    seiteIntervall(() => korrekturLaden(vid).catch(() => {}), 6000, wache);
  }

  async function korrekturLaden(vid, erzwingen = false) {
    if (!erzwingen && (tippt() || S.sucht)) return;
    const wache = seitenWache();
    let stand, bom, v, einst;
    try {
      [stand, bom, v, einst] = await Promise.all([
        api(`/api/boms/${vid}/korrektur`), api(`/api/boms/${vid}`), api(`/api/vorgaenge/${vid}`),
        // Websuche dieses Vorgangs (Seite „Sprachmodelle“); ohne Antwort gilt der Wert des Backends
        api(`/api/vorgaenge/${vid}/einstellungen`).catch(() => null),
      ]);
    } catch (e) {
      console.error("Korrektur nicht ladbar:", e);
      if (!wache()) return;
      if (erzwingen && !S.stand) throw new Error("Die Seite Korrektur ließ sich nicht laden. Bitte versuchen Sie es gleich noch einmal.");
      $("#api-status").textContent = "Die Verbindung ist unterbrochen …";
      return;
    }
    if (!wache() || !hier("korrektur", vid) || (!erzwingen && tippt())) return;
    const vorgabe = einst ? [einst.internet_versuche_wirksam, einst.internet_versuche_max] : [null, null];
    const geaendert = JSON.stringify([stand, bom.items, v, vorgabe]) !== JSON.stringify([S.stand, S.bom?.items, zustand.v, [S.versucheVorgang, S.versucheMax]]);
    S.stand = stand; S.bom = bom; zustand.v = v;
    [S.versucheVorgang, S.versucheMax] = vorgabe;
    if (erzwingen || geaendert) zeichneKorrektur();
  }
  const kNeu = () => korrekturLaden(S.vid, true);

  function zeichneKorrektur() {
    const app = $("#app"), v = zustand.v;
    if (!S.stand || !S.bom || !v) return;
    zeichneNav();
    zeichne(app, `
      ${kopf(v, "Korrektur", "Fehlen Angaben in der Stückliste, schicken Sie dem Einkauf eine Mängelliste. Seine Antwort tragen Sie hier ein, danach wird nur die korrigierte Position neu gesucht.")}
      ${S.meldung ? `<div class="hinweis ${S.meldung.art} kq-meldung"><div>${S.meldung.html}</div>
        <button class="icon" title="Hinweis schließen" onclick="kq.meldungZu()">✕</button></div>` : ""}
      ${maengelKarte()}
      ${listeKarte()}
      ${sucheKarte()}
      ${excelKarte()}
      ${S.stand.corrections.length ? `<details class="karte kq-karte" data-merk="kq-verlauf" ${offenAttr("kq-verlauf")}><summary><h2>Alle Korrekturen <span class="n">(${S.stand.corrections.length})</span></h2></summary>
        ${verlauf(S.stand.corrections, true)}</details>` : ""}`);
  }

  // Mängel als Tabelle (seiten/tabelle.js): fehlende Angaben direkt eintragen; gespeichert wird je
  // Zeile erst nach Bestätigung (PATCH /api/artikel/<id>, Regeln der Korrektur wie bisher)
  function maengelKarte() {
    const maengel = S.stand.defects;
    const f = (s, titel, mehr = {}) => ({ s, titel, zeige: w => w ? esc(w) : '<span class="unbelegt">fehlt</span>', ...mehr });
    return `<div class="karte"><h2>Mängel <span class="n">(${maengel.length})</span></h2>
      ${maengel.length ? `<p class="kq-klein" style="margin-top:-6px">Tragen Sie die Antwort des Einkaufs direkt in die Zellen ein. Andere Positionen korrigieren Sie in der <a href="#/v/${S.vid}">Tabelle des Vorgangs</a>.</p>
        ${Tabelle.html({
          id: `maengel-${S.vid}`, titel: "Mängel", zeilen: maengel, schluessel: d => d.bom_item_id, name: d => `Position ${d.pos}`,
          spalten: [
            { s: "pos", titel: "Pos.", gesperrt: "Die Positionsnummer kennzeichnet die Zeile der Stückliste und lässt sich nicht ändern." },
            f("description", "Bezeichnung", { zeige: w => esc(w) }),
            f("profile_no", "PRO.FILE-Nr.", { klasse: "mono" }),
            f("manufacturer", "Hersteller"),
            f("manufacturer_article_no", "Hersteller-Art.-Nr.", { klasse: "mono" }),
            f("quantity", "Menge", { zeige: w => esc(w) }),
            f("location", "Einbauort"),
            { s: "missing", titel: "Was fehlt", gesperrt: "Das ergibt sich aus der Eingangsprüfung. Der Hinweis verschwindet, sobald die Angabe korrigiert ist.",
              zeige: (w, d) => d.missing.map(x => `<div class="kq-fehlt">${esc(x)}</div>`).join("") || standChip(d.bom_item_id, d.status) },
          ],
          zusatz: { zelle: d => `<a href="#/v/${S.vid}/a/${d.bom_item_id}" title="Artikel öffnen">Artikel</a>` },
          notiz: "Notiz zur Korrektur, falls Sie eine festhalten möchten",
          folgen: (d, werte) => positionsFolgen(werte),
          speichern: (d, werte, opt) => positionSpeichern(d.bom_item_id, werte, opt),
          nachher: kNeu,
        })}`
      : `<div class="hinweis gruen" style="margin:0">Es fehlt nichts. Jede Position hat PRO.FILE-Nr., Hersteller, Typ und Einbauort, oder jemand hat über sie entschieden.</div>`}
    </div>`;
  }

  // höchstens eine offene Mängelliste; gesendete bleiben zum Nachlesen
  function listeKarte() {
    const listen = S.stand.purchasing_emails, maengel = S.stand.defects;
    const offen = listen.find(m => m.status !== "gesendet");
    const gesendet = listen.filter(m => m.status === "gesendet");
    let h = `<div class="karte"><h2>Mängelliste an den Einkauf</h2>`;
    if (maengel.length && (!offen || offen.status === "entwurf")) {
      h += `<div class="zeile" style="justify-content:space-between;margin-bottom:${offen ? 12 : 0}px">
        <div><strong>${mitZahl(maengel.length, "Position hat", "Positionen haben")} Mängel.</strong>
          <div class="kq-klein">${offen ? "Beim Neuaufbauen kommt der aktuelle Stand in die Liste. Ihre Änderungen am Text gehen dabei verloren." : "Sie prüfen den Text, geben ihn frei und senden ihn."}</div></div>
        <div class="zeile"><button class="${offen ? "leise" : ""}" onclick="kq.listeAnlegen(${offen ? "true" : "false"})" ${sperre()}>${offen ? "Mängelliste neu aufbauen" : "Mängelliste anlegen"}</button>${nameHinweis()}</div>
      </div>`;
    }
    if (offen) h += offeneListe(offen);
    else if (!maengel.length) h += `<div class="grau">Es gibt keine Mängel, also braucht es auch keine Mängelliste.</div>`;
    if (gesendet.length) h += details("gesendet", `Gesendete Mängellisten (${gesendet.length})`, gesendet.map(m => `
      <div style="margin-bottom:14px"><div><strong>${esc(m.subject)}</strong> ${pille(LISTE.gesendet)}</div>
        <div class="kq-klein" style="margin:2px 0 6px">Freigegeben von ${esc(m.approved_by)}, am ${esc(zeit(m.sent_at))} an ${esc(m.to_address)} gesendet.</div>
        <div class="kq-brief"><pre class="kq-text">${esc(m.body)}</pre></div></div>`).join(""));
    return h + `</div>`;
  }

  function offeneListe(m) {
    const kopfzeile = `<div class="zeile" style="justify-content:space-between">
        <strong>Mängelliste ${m.id}</strong>${pille(LISTE[m.status] || [m.status, "s-wartet"])}</div>
      <div class="kq-klein" style="margin:2px 0 8px">An den Einkauf, <span class="mono">${esc(m.to_address)}</span>, angelegt am ${esc(zeit(m.created_at))}. E-Mails gehen in dieser Umgebung an ein Test-Postfach.</div>`;
    if (m.status === "entwurf") return `${kopfzeile}
      <label for="kq-betreff">Betreff</label><input type="text" id="kq-betreff" value="${esc(m.subject)}" ${darfSchreiben() ? "" : "readonly"} ${halten(m.id + "|" + m.subject)}>
      <label for="kq-text">Text</label><textarea id="kq-text" style="min-height:300px" ${darfSchreiben() ? "" : "readonly"} ${halten(m.id + "|" + m.body)}>${esc(m.body)}</textarea>
      <div class="kq-rechts">${nameHinweis()}
        <button class="leise" onclick="kq.listeSpeichern(${m.id})" ${sperre()}>Text speichern</button>
        <button onclick="kq.listeFreigeben(${m.id})" ${sperre()} title="Speichert den Text und gibt ihn zum Senden frei">Mängelliste freigeben</button></div>`;
    return `${kopfzeile}
      <div class="kq-brief"><div class="kq-klein">Betreff</div><div style="font-weight:600;margin-bottom:10px">${esc(m.subject)}</div>
        <pre class="kq-text">${esc(m.body)}</pre></div>
      <div class="kq-rechts"><span style="margin-right:auto">Freigegeben von <strong>${esc(m.approved_by)}</strong>, aber noch nicht gesendet.</span>
        ${nameHinweis()}<button onclick="kq.listeSenden(${m.id})" ${sperre()}>Mängelliste senden</button></div>`;
  }

  function verlauf(korrekturen, mitPos) {
    return `<div class="kq-klein" style="margin-bottom:4px">🔒 Der Verlauf lässt sich nicht ändern. Jede neue Korrektur kommt als eigene Zeile dazu.</div>
      <div class="tabelle" title="Der Verlauf lässt sich nicht ändern."><table><thead><tr><th>Wann</th>${mitPos ? "<th>Pos.</th>" : ""}<th>Feld</th><th>Vorher</th><th>Nachher</th><th>Von</th><th>Notiz</th></tr></thead><tbody>
      ${korrekturen.slice().reverse().map(k => `<tr>
        <td class="grau" style="white-space:nowrap">${esc(zeit(k.changed_at))}</td>
        ${mitPos ? `<td><a href="#/v/${S.vid}/a/${k.bom_item_id}" title="Artikel öffnen">${esc(k.pos)}</a></td>` : ""}
        <td>${esc(FELDER[k.field] || k.field)}</td>
        <td>${k.old_value ? `<span class="kq-alt">${esc(k.old_value)}</span>` : '<span class="grau">(leer)</span>'}</td><td><strong>${esc(k.new_value)}</strong></td>
        <td>${esc(k.changed_by)}</td><td class="grau">${esc(k.note) || "–"}</td></tr>`).join("")}
    </tbody></table></div>`;
  }

  // erneuter Start nur für korrigierte Positionen, nicht für den ganzen Vorgang. Vorgabe der
  // Versuche ist der wirksame Wert des Vorgangs (internet_versuche_wirksam); gesendet wird die
  // Zahl nur, wenn jemand sie hier ändert, sonst gilt im Backend die Einstellung des Vorgangs.
  const versucheFeld = () => S.versucheEigen ?? S.versucheVorgang ?? "";
  function sucheKarte() {
    const zuSuchen = S.bom.items.filter(i => S.stand.to_search.includes(i.id));
    return `<div class="karte"><h2>Neu suchen <span class="n">(${zuSuchen.length})</span></h2>
      ${!zuSuchen.length ? `<div class="grau">Keine korrigierte Position wartet auf die Suche. Positionen, denen noch Angaben fehlen, werden erst gesucht, wenn die Angaben da sind.</div>` : `
        <div class="tabelle"><table><thead><tr><th>Pos.</th><th>Bezeichnung</th><th>Hersteller</th><th>Hersteller-Art.-Nr.</th><th>Stand</th></tr></thead><tbody>
          ${zuSuchen.map(i => `<tr><td>${esc(i.pos)}</td><td>${esc(i.description)}</td><td>${esc(i.manufacturer)}</td>
            <td class="mono">${esc(i.manufacturer_article_no)}</td><td>${standChip(i.id, i.status)}</td></tr>`).join("")}</tbody></table></div>
        <div class="zeile" style="margin-top:12px;align-items:flex-end">
          <div><label for="kq-versuche" style="margin-top:0" title="Mit 0 sucht der Agent nicht im Internet.">Suchversuche im Internet je Position</label>
            <input type="number" id="kq-versuche" ${darfSchreiben() ? "" : "disabled"} min="0" max="${S.versucheMax ?? 50}" step="1" value="${esc(versucheFeld())}" style="width:150px" ${halten("versuche|" + versucheFeld())}></div>
          <button onclick="kq.suche()" ${S.sucht ? "disabled" : sperre()}>${S.sucht ? '<span class="punkt"></span>Suche läuft …' : "Korrigierte Positionen suchen"}</button>
          ${nameHinweis()}
          <span class="kq-klein">${S.sucht ? "Das dauert einige Sekunden, mit Suche im Internet länger." : `Die übrigen Positionen werden nicht noch einmal gesucht.${S.versucheVorgang != null ? ` Für diesen Vorgang ${S.versucheVorgang === 0 ? "ist die Websuche abgeschaltet" : S.versucheVorgang === 1 ? "gilt 1 Versuch je Position" : `gelten ${S.versucheVorgang} Versuche je Position`}. Ändern Sie die Zahl hier, gilt sie nur für diese Suche.` : ""}`}</span>
        </div>`}
    </div>`;
  }

  function excelKarte() {
    // selten gebraucht: zugeklappt, offen nur, solange eine Datei gewählt ist
    return `<details class="karte kq-karte nur-schreiben" data-merk="kq-excel" ${S.datei || auf("kq-excel") ? "open" : ""}><summary><h2>Korrigierte Stückliste hochladen</h2></summary>
      <div class="grau" style="margin-bottom:10px">Laden Sie hier dieselbe Stückliste mit den Korrekturen des Einkaufs hoch. Geänderte Werte werden über die Positionsnummer zugeordnet und mit Ihrem Namen als Korrektur übernommen, neue Positionen kommen nicht dazu.</div>
      <div class="zeile">${dateiwahl('id="kq-datei" onchange="kq.dateiGewaehlt(this)"', {
          titel: "Korrigierte Stückliste als Excel-Datei", name: S.datei && !S.dateiFehler ? S.datei.name : "" })}
        <button id="kq-hochladen" onclick="kq.excel()" ${S.datei && !S.dateiFehler ? sperre() : "disabled"}>Korrekturen übernehmen</button>${nameHinweis()}</div>
      <div class="unbelegt" id="kq-datei-fehler" style="margin-top:8px">${esc(S.dateiFehler)}</div>
    </details>`;
  }

  // ---------- aktionen: korrektur ----------
  const K = {
    auf(schl, offen) { S.auf[schl] = offen; },
    meldungZu() { S.meldung = null; zeichneKorrektur(); },

    listeAnlegen(neu) {
      mitName(by => aktion(() => api(`/api/boms/${S.vid}/maengelliste`, { method: "POST", json: { by } }),
        neu ? "Die Mängelliste ist neu aufgebaut." : "Die Mängelliste ist angelegt.", kNeu));
    },
    listeSpeichern(id) {
      const d = { subject: $("#kq-betreff").value, body: $("#kq-text").value };
      document.activeElement?.blur();
      mitName(by => aktion(() => api(`/api/maengellisten/${id}`, { method: "PUT", json: { ...d, by } }), "Der Text der Mängelliste ist gespeichert.", kNeu));
    },
    listeFreigeben(id) {  // geänderter Text wird vorher gespeichert
      const d = { subject: $("#kq-betreff").value, body: $("#kq-text").value };
      document.activeElement?.blur();
      mitName(by => aktion(async () => {
        await api(`/api/maengellisten/${id}`, { method: "PUT", json: { ...d, by } });
        await api(`/api/maengellisten/${id}/approve`, { method: "POST", json: { by } });
      }, `${by} hat die Mängelliste freigegeben.`, kNeu));
    },
    listeSenden(id) {
      mitName(by => aktion(() => api(`/api/maengellisten/${id}/send`, { method: "POST", json: { by } }),
        r => `Die Mängelliste ist an ${r.to_address || "den Einkauf"} gesendet.`, kNeu));
    },

    suche() {
      const roh = $("#kq-versuche").value.trim();
      const versuche = roh === "" ? null : Number(roh);
      const max = S.versucheMax ?? 50;
      if (versuche !== null && !(Number.isInteger(versuche) && versuche >= 0 && versuche <= max)) return toast(`Bitte geben Sie für die Suchversuche eine ganze Zahl von 0 bis ${max} ein.`);
      // nur eine geänderte Zahl mitschicken; sonst nimmt das Backend die Einstellung des Vorgangs
      const eigen = versuche !== null && versuche !== S.versucheVorgang;
      const vid = S.vid;
      mitName(async () => {
        S.sucht = true; S.versucheEigen = eigen ? versuche : null; S.meldung = null;
        zeichneKorrektur();
        try {
          const gesucht = await api(`/api/boms/${vid}/korrektur/suche`, { method: "POST", json: eigen ? { internet_versuche: versuche } : {} });
          S.meldung = { art: "gruen", html: `<strong>Die Suche ist fertig.</strong> ${gesucht.map(i =>
            `<a href="#/v/${vid}/a/${i.id}">Position ${esc(i.pos)}</a>: ${esc(standText(i.status))}`).join(", ")}.
            Neue Funde können Sie jetzt beim Artikel prüfen.` };
        } catch (e) {
          S.meldung = { art: "rot", html: `<strong>Die Suche hat nicht geklappt.</strong> ${esc(e.message)} Bitte versuchen Sie es noch einmal.` };
        }
        S.sucht = false;
        if (S.vid !== vid || !hier("korrektur", vid)) return;
        await kNeu(); window.scrollTo(0, 0);
      });
    },

    // falsche Datei schon vor dem Senden melden
    dateiGewaehlt(feld) {
      const f = feld.files[0] || null;
      S.datei = f;
      S.dateiFehler = f && !f.name.toLowerCase().endsWith(".xlsx")
        ? `„${f.name}“ ist keine Excel-Datei. Bitte laden Sie die korrigierte Stückliste als Excel-Datei (.xlsx) hoch.` : "";
      $("#kq-datei-fehler").textContent = S.dateiFehler;
      $("#kq-hochladen").disabled = !f || !!S.dateiFehler || !darfSchreiben();
    },
    excel() {
      const f = S.datei, vid = S.vid;
      if (!f) return toast("Bitte wählen Sie zuerst die korrigierte Stückliste aus.");
      if (S.dateiFehler) return toast(S.dateiFehler);
      mitName(async by => {
        const fd = new FormData(); fd.append("file", f); fd.append("by", by);
        try {
          const r = await api(`/api/boms/${vid}/korrektur/excel`, { method: "POST", body: fd });
          const teile = [r.changed.length ? `<strong>Aus ${esc(f.name)} ${r.changed.length === 1 ? "ist eine Position" : `sind ${r.changed.length} Positionen`} korrigiert</strong> (${r.changed.map(i => `Position ${esc(i.pos)}`).join(", ")}).`
            : `<strong>In ${esc(f.name)} hat sich nichts geändert.</strong>`];
          if (r.unknown_pos.length) teile.push(`Die Positionen ${esc(r.unknown_pos.join(", "))} gibt es in diesem Vorgang nicht, sie sind nicht übernommen.`);
          if (r.missing_pos.length) teile.push(`In der Datei fehlen die Positionen ${esc(r.missing_pos.join(", "))}.`);
          if (r.changed.length) teile.push("Unten können Sie die korrigierten Positionen neu suchen lassen.");
          S.meldung = { art: "gruen", html: teile.join(" ") };
          S.datei = null;
        } catch (e) {
          S.meldung = { art: "rot", html: `<strong>Die Datei wurde nicht übernommen.</strong> ${esc(e.message)}` };
        }
        if (S.vid !== vid || !hier("korrektur", vid)) return;
        await kNeu(); window.scrollTo(0, 0);
      });
    },
  };

  // ====================================================================
  // Fragen
  // ====================================================================
  async function fragenSeite(m, app, wache = seitenWache()) {
    const vid = +m[1];
    if (S.fvid !== vid) Object.assign(S, { fvid: vid, fragen: null, fbom: null });
    app.innerHTML = `<div class="leer">lädt …</div>`;
    await fragenLaden(vid, true);
    if (!wache() || !hier("fragen", vid)) return;
    seiteIntervall(() => fragenLaden(vid).catch(() => {}), 6000, wache);
  }

  async function fragenLaden(vid, erzwingen = false) {
    if (!erzwingen && tippt()) return;
    const wache = seitenWache();
    let fragen, bom;
    try {
      [fragen, bom] = await Promise.all([
        api(`/api/boms/${vid}/questions`),
        S.fbom && S.fbom.id === vid ? S.fbom : api(`/api/boms/${vid}`),  // Positionen ändern sich hier nicht
      ]);
    } catch (e) {
      console.error("Fragen nicht ladbar:", e);
      if (!wache()) return;
      if (erzwingen && !S.fragen) throw new Error("Die Fragen ließen sich nicht laden. Bitte versuchen Sie es gleich noch einmal.");
      $("#api-status").textContent = "Die Verbindung ist unterbrochen …";
      return;
    }
    if (!wache() || !hier("fragen", vid) || (!erzwingen && tippt())) return;
    const geaendert = JSON.stringify(fragen) !== JSON.stringify(S.fragen);
    S.fragen = fragen; S.fbom = bom;
    S.offen[vid] = fragen.filter(q => q.status === "offen").length; S.geholt[vid] = Date.now();
    if (erzwingen || geaendert) zeichneFragen();
  }
  const fNeu = () => fragenLaden(S.fvid, true);

  const bezug = q => q.item ? posName(q.item) : "Ganzer Vorgang";

  function zeichneFragen() {
    const app = $("#app"), v = zustand.v, fragen = S.fragen;
    if (!fragen || !v) return;
    zeichneNav();
    const offen = fragen.filter(q => q.status === "offen").length;
    let wahl = fragen.find(q => q.id === S.frage[S.fvid]) || fragen[0];
    if (wahl) S.frage[S.fvid] = wahl.id;
    zeichne(app, `
      ${kopf(v, "Fragen", "Hier stehen Fragen, die noch jemand beantworten muss, zum ganzen Vorgang oder zu einer Position. Fragen ändern nichts am Stand oder an der Kundenliste.")}
      <div class="kq-spalten">
        <div>
          <div class="karte nur-schreiben"><h2>Neue Frage stellen</h2>
            <label for="kq-bezug" style="margin-top:0">Bezug</label>
            <select id="kq-bezug" style="width:100%" ${halten("bezug")}><option value="">Ganzer Vorgang</option>
              ${S.fbom.items.map(i => `<option value="${i.id}">${esc(posName(i))}</option>`).join("")}</select>
            <label for="kq-frage">Frage</label>
            <textarea id="kq-frage" class="klein" placeholder="zum Beispiel: Gilt der Schaltschrank außerhalb des Ex-Bereichs als Ex-Teil?" ${halten("frage")}></textarea>
            <div class="kq-rechts">${nameHinweis()}<button onclick="kq.frageStellen()" ${sperre()}>Frage stellen</button></div>
          </div>
          <div class="karte" style="padding:0"><h2 style="padding:14px 14px 0">Fragen <span class="n">(${offen} von ${fragen.length} offen)</span></h2>
            ${fragen.length ? fragen.map(q => `<div class="kq-frage ${wahl && q.id === wahl.id ? "an" : ""}" onclick="kq.frageWaehlen(${q.id})">
              ${pille(FRAGE[q.status] || [q.status, "s-wartet"])} <span class="kq-klein">${q.comments.length ? mitZahl(q.comments.length, "Kommentar", "Kommentare") : ""}</span>
              <div class="kurz">${esc(q.text.length > 90 ? q.text.slice(0, 90) + "…" : q.text)}</div>
              <div class="kq-klein">${esc(bezug(q))}</div></div>`).join("")
            : `<div class="leer">Zu diesem Vorgang gibt es noch keine Fragen. Stellen Sie die erste oben.</div>`}
          </div>
        </div>
        <div>${wahl ? frageKarte(wahl) : `<div class="karte leer">Wählen Sie links eine Frage aus.</div>`}</div>
      </div>`);
  }

  function frageKarte(q) {
    const offen = q.status === "offen";
    return `<div class="karte">
      <div class="zeile" style="justify-content:space-between"><h2 style="margin:0">Frage ${q.id}</h2>${pille(FRAGE[q.status] || [q.status, "s-wartet"])}</div>
      <div class="kq-klein" style="margin:6px 0 10px">Gestellt von <strong>${esc(q.created_by)}</strong> am ${esc(zeit(q.created_at))}.
        Bezug: ${q.item ? `<a href="#/v/${S.fvid}/a/${q.item.id}">${esc(bezug(q))}</a>` : "Ganzer Vorgang"}</div>
      <div class="kq-brief"><pre class="kq-text">${esc(q.text)}</pre></div>
      ${!offen ? `<div class="hinweis gruen" style="margin:12px 0 0"><div class="kq-klein" style="color:inherit">Antwort von <strong>${esc(q.resolved_by)}</strong> am ${esc(zeit(q.resolved_at))}</div>
        <pre class="kq-text" style="color:var(--text);font-weight:500">${esc(q.answer)}</pre></div>` : ""}

      <h2 style="margin-top:18px">Kommentare <span class="n">(${q.comments.length})</span></h2>
      ${q.comments.length ? q.comments.map(c => `<div class="kq-kommentar">
        <div><strong>${esc(c.created_by)}</strong><div class="kq-klein">${esc(zeit(c.created_at))}</div></div>
        <pre class="kq-text">${esc(c.text)}</pre></div>`).join("") : `<div class="grau">Noch hat niemand kommentiert.</div>`}
      <label for="kq-kommentar" class="nur-schreiben">Kommentar</label>
      <textarea id="kq-kommentar" class="klein nur-schreiben" placeholder="zum Beispiel: beim Hersteller nachgefragt, Antwort steht noch aus" ${halten("kommentar|" + q.id)}></textarea>
      <div class="kq-rechts nur-schreiben"><button class="leise" onclick="kq.kommentiere(${q.id})">Kommentar speichern</button></div>

      ${offen ? `<h2 class="nur-schreiben" style="margin-top:18px">Antwort</h2>
        <textarea id="kq-antwort" class="klein nur-schreiben" aria-label="Antwort" placeholder="Die Antwort, zum Beispiel: laut Einkauf gilt …" ${halten("antwort|" + q.id)}></textarea>
        <div class="kq-rechts nur-schreiben"><button onclick="kq.klaere(${q.id})">Beantworten und klären</button></div>`
      : `<div class="kq-rechts nur-schreiben" style="border-top:1px solid var(--linie);padding-top:12px;margin-top:14px">
        <span class="kq-klein" style="margin-right:auto">Die bisherige Antwort bleibt als Kommentar erhalten.</span>
        ${nameHinweis()}<button class="leise" onclick="kq.oeffne(${q.id})" ${sperre()}>Frage wieder öffnen</button></div>`}
    </div>`;
  }

  const F = {
    frageWaehlen(id) { S.frage[S.fvid] = id; zeichneFragen(); },

    frageStellen() {
      const text = $("#kq-frage").value.trim(), itemId = $("#kq-bezug").value;
      if (!text) return toast("Bitte schreiben Sie zuerst Ihre Frage.");
      document.activeElement?.blur();
      mitName(by => aktion(async () => {
        const q = await api(`/api/boms/${S.fvid}/questions`, { method: "POST", json: { by, text, ...(itemId ? { item_id: +itemId } : {}) } });
        S.frage[S.fvid] = q.id;
        leeren("kq-frage", "kq-bezug");
      }, "Ihre Frage ist gestellt.", fNeu));
    },
    kommentiere(id) {
      const text = $("#kq-kommentar").value.trim();
      if (!text) return toast("Bitte schreiben Sie zuerst Ihren Kommentar.");
      document.activeElement?.blur();
      mitName(by => aktion(async () => {
        await api(`/api/questions/${id}/comments`, { method: "POST", json: { by, text } });
        leeren("kq-kommentar");
      }, "Ihr Kommentar ist gespeichert.", fNeu));
    },
    klaere(id) {
      const answer = $("#kq-antwort").value.trim();
      if (!answer) return toast("Bitte schreiben Sie zuerst die Antwort.");
      document.activeElement?.blur();
      mitName(by => aktion(() => api(`/api/questions/${id}/resolve`, { method: "POST", json: { by, answer } }),
        "Die Frage ist beantwortet und geklärt.", fNeu));
    },
    oeffne(id) {
      mitName(by => aktion(() => api(`/api/questions/${id}/reopen`, { method: "POST", json: { by } }),
        "Die Frage ist wieder offen.", fNeu));
    },
  };

  // Zahl der offenen Fragen in der Leiste: gemerkt, höchstens alle 20 s im Hintergrund aufgefrischt
  function offeneFragen(v) {
    if (!v) return 0;
    const vid = v.id;
    if (Date.now() - (S.geholt[vid] || 0) > 20000) {
      S.geholt[vid] = Date.now();
      fetch(`${API}/api/boms/${vid}/questions`).then(r => r.ok ? r.json() : null).then(fragen => {
        if (!fragen) return;
        const n = fragen.filter(q => q.status === "offen").length;
        if (n !== S.offen[vid]) { S.offen[vid] = n; if (zustand.v && zustand.v.id === vid) zeichneNav(); }
      }).catch(() => {});
    }
    return S.offen[vid] || 0;
  }

  window.kq = { ...K, ...F };

  const ICON_STIFT = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3Z"/><path d="m14.5 7.5 3 3"/></svg>';
  const ICON_FRAGE = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1 1-1.1 1.8"/><circle cx="12" cy="17" r=".6" fill="currentColor"/></svg>';

  erweitere("seiten", { name: "korrektur", muster: /^#\/v\/(\d+)\/korrektur\b/, vorgang: true, zeige: korrekturSeite });
  erweitere("seiten", { name: "fragen", muster: /^#\/v\/(\d+)\/fragen\b/, vorgang: true, zeige: fragenSeite });
  erweitere("nav", { name: "korrektur", vorgang: true, href: v => `#/v/${v.id}/korrektur`, text: "Korrektur", icon: ICON_STIFT,
    zahl: v => v?.zaehler?.info_fehlt || 0 });
  erweitere("nav", { name: "fragen", vorgang: true, href: v => `#/v/${v.id}/fragen`, text: "Fragen", icon: ICON_FRAGE, zahl: offeneFragen });
})();
