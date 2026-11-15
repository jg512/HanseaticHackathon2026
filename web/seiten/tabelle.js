// Tabelle: Excel-ähnliches Bearbeiten für jede Tabelle der Oberfläche, ohne Bibliothek.
//
// Eine Seite zeichnet ihre Tabelle mit Tabelle.html(def) als HTML-Text (wie bisher mit innerHTML);
// Tastatur, Maus, Kopieren und Einfügen hängen einmal am Dokument und finden die Tabelle über
// data-tg. Geänderte Zellen werden nur vorgemerkt (Zustand hier, je Tabelle, Zeile und Spalte):
// nichts geht ans Backend, bis ein Mensch im Dialog „Ja, speichern“ drückt. Die Vormerkungen
// überleben das regelmäßige Neuzeichnen der Seiten; hat sich der Wert auf dem Server inzwischen
// geändert, zeigt der Dialog das als Konflikt.
//
// def = {
//   id: "positionen-7",                 Schlüssel des Zustands (je Tabelle eindeutig)
//   titel: "Positionen",                Überschrift im Dialog
//   zeilen: [...], schluessel: z => z.id, name: z => `Pos. ${z.pos}`,
//   spalten: [{ s, titel, art: "text"|"auswahl"|"datum"|"haken", optionen: [[wert, text]],
//               wert?: z => …, zeige?: (wert, z) => html, gesperrt?: true | "Grund" | z => "Grund",
//               leer?: false (Auswahl ohne leere Option), leerText?: "Text der leeren Option",
//               klasse?, stil?, kopfStil? }],
//   speichern: async (z, werte, {von, notiz}) => …   je Zeile; wirft Error mit dem Text des Backends;
//              gibt sie {hinweis: "…"} zurück, zeigt das Ergebnis diesen Hinweis (z. B. anderer Name)
//   folgen?: (z, werte) => ["Wirkung in Worten", …], notiz?: "Beschriftung des Notizfelds",
//   nachher?: async () => …   nach dem Speichern (Daten neu laden)
//   speichernAlle?: async ([{z, werte}], {von, notiz}) => …   statt speichern: ein Aufruf für alles
//   zusatz?: { kopf: "", zelle: z => html }   letzte Spalte ohne Bearbeitung (Links, Knöpfe)
//   vorne?: { kopf: "", zelle: z => "<td>…</td>" }   erste Spalte ohne Bearbeitung (z. B. Auswahl)
// Art, optionen und leer einer Spalte dürfen Funktionen der Zeile sein (eine Zeile je Feld);
// kopfHtml ersetzt die Überschrift (z. B. Knopf zum Sortieren); still: gesperrt ohne graue Ecke.
//   zeileKlasse?: z => "", leer?: "Text ohne Zeilen", hinweis?: false,
//   rahmenKlasse?: Klasse für den Rahmen um die Tabelle (z. B. eigener Rollbalken; die Leiste bleibt draußen)
// }
(function () {
  "use strict";

  const defs = {};      // id -> zuletzt gezeichnete Definition
  const merk = {};      // id -> { zeile: Zeile }, auch für Zeilen, die gerade ausgefiltert sind
  const stand = {};     // id -> { zeile: { spalte: { neu, alt } } }
  const fehler = {};    // id -> { zeile: Text des Backends }
  const rueck = {};     // id -> [[{ z, s, vorher }]] Gruppen für Strg+Z
  let aktiv = null;     // { t, z, s } gewählte Zelle
  let anker = null;     // { t, z, s } Beginn der Markierung
  let editor = null;    // { t, z, s, el, fertig }
  let fokusDrin = false;

  const stil = document.createElement("style");
  stil.textContent = `
    .tg-leiste { display: flex; gap: 8px 12px; align-items: center; flex-wrap: wrap; background: var(--gelb-hell); color: #5a4000; border: 1px solid #f0d27a; border-radius: 10px; padding: 8px 12px; margin-bottom: 10px; position: sticky; top: 58px; z-index: 4; }
    .tg-leiste[hidden] { display: none; }
    .tg-leiste button { padding: 4px 12px; }
    .tg-leiste .tg-fehlerliste { flex-basis: 100%; color: var(--rot); font-size: 12.5px; }
    .tg table td.tg-z { cursor: cell; position: relative; outline: none; }
    .tg table td.tg-z.tg-zu { cursor: default; }
    .tg table td.tg-z.tg-zu:not(.tg-still) { background-image: linear-gradient(135deg, transparent 0 calc(100% - 7px), #d4d8db calc(100% - 7px)); }
    .tg table td.tg-z:not(.tg-zu):hover { box-shadow: inset 0 0 0 1px #cfd4d1; }
    .tg table td.tg-wahl { background-color: var(--blau-hell); }
    .tg table td.tg-aktiv { box-shadow: inset 0 0 0 2px var(--gruen) !important; }
    .tg table td.tg-geaendert { background-color: var(--gelb-hell); }
    .tg table td.tg-geaendert::after { content: ""; position: absolute; top: 0; right: 0; border: 5px solid transparent; border-top-color: var(--orange); border-right-color: var(--orange); }
    .tg table td.tg-konflikt { background-color: var(--rot-hell); }
    .tg table tr.tg-fehler td:first-child { box-shadow: inset 3px 0 0 var(--rot); }
    .tg table td.tg-z.tg-editiert { padding: 2px; }
    .tg-ed { font: inherit; width: 100%; min-width: 90px; padding: 5px 7px; border: 1px solid var(--gruen); border-radius: 6px; background: #fff; }
    .tg-haken { font-size: 15px; line-height: 1; }
    .tg-tipp { font-size: 11.5px; color: var(--grau); margin-top: 6px; }
    .tg-schleier { position: fixed; inset: 0; background: rgba(20, 30, 25, .35); z-index: 60; display: grid; place-items: center; padding: 16px; }
    .tg-dialog { background: #fff; border-radius: 12px; box-shadow: 0 10px 40px rgba(0,0,0,.25); max-width: 860px; width: 100%; max-height: 88vh; overflow: auto; padding: 20px 22px; }
    .tg-dialog h2 { margin-bottom: 6px; }
    .tg-dialog table td { padding: 6px 8px; font-size: 13px; }
    .tg-dialog .tg-alt { color: var(--grau); text-decoration: line-through; }
    .tg-dialog .tg-neu { font-weight: 600; }
    .tg-dialog .tg-konfliktzeile { color: var(--rot); font-size: 12px; }
    .tg-dialog .tg-folgen { background: var(--gelb-hell); color: #5a4000; border-radius: 8px; padding: 8px 12px; margin-top: 12px; font-size: 13px; }
    .tg-dialog .tg-folgen ul { margin: 4px 0 0; padding-left: 18px; }
    .tg-dialog .tg-knoepfe { display: flex; gap: 8px; justify-content: flex-end; align-items: center; flex-wrap: wrap; margin-top: 16px; }
    .tg-dialog .tg-ok { color: var(--gruen); } .tg-dialog .tg-nein { color: var(--rot); }
  `;
  document.head.appendChild(stil);

  // ---------- werte ----------
  const e = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = s => (window.CSS && CSS.escape) ? CSS.escape(String(s)) : String(s).replace(/"/g, '\\"');
  const norm = v => v === true || v === false ? v : v == null ? "" : String(v);
  const gleich = (a, b) => norm(a) === norm(b);
  const datumText = iso => /^\d{4}-\d{2}-\d{2}/.test(iso || "") ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : (iso || "");
  // schreiben darf, wer angemeldet ist und nicht nur lesen darf (index.html: darfSchreiben, sperrgrund)
  const nameDa = () => typeof darfSchreiben === "function" ? darfSchreiben() : typeof anwender !== "function" || !!anwender();
  const NAME_TEXT = () => typeof sperrgrund === "function" ? sperrgrund() : "Bitte melden Sie sich an.";
  // „1 Zelle“, „3 Zellen“
  const anzahl = (n, eins, mehr) => `${n} ${n === 1 ? eins : mehr}`;

  const spalte = (def, s) => def.spalten.find(x => x.s === s);
  // Spalte mit Art, Optionen und Leer-Regel der Zeile (für Tabellen mit einer Zeile je Feld)
  const eff = (sp, z) => sp && z && (typeof sp.art === "function" || typeof sp.optionen === "function" || typeof sp.leer === "function")
    ? { ...sp, art: typeof sp.art === "function" ? sp.art(z) : sp.art, optionen: typeof sp.optionen === "function" ? sp.optionen(z) : sp.optionen,
        leer: typeof sp.leer === "function" ? sp.leer(z) : sp.leer } : sp;
  const roh = (sp, z) => sp.wert ? sp.wert(z) : z[sp.s];
  const zeileVon = (t, k) => merk[t]?.[k];
  const vorgemerkt = (t, k, s) => stand[t]?.[k]?.[s];

  function optText(sp, w) {
    const o = (sp.optionen || []).find(([v]) => gleich(v, w));
    return o ? o[1] : w;
  }
  function anzeige(sp, w, z) {
    if (sp.zeige) return sp.zeige(w, z);
    if (sp.art === "haken") return `<span class="tg-haken" aria-label="${w ? "ja" : "nein"}">${w ? "☑" : "☐"}</span>`;
    if (norm(w) === "") return '<span class="grau">–</span>';
    if (sp.art === "datum") return e(datumText(w));
    if (sp.art === "auswahl") return e(optText(sp, w));
    return e(w);
  }
  function lesbar(sp, w) {
    if (sp.art === "haken") return w ? "ja" : "nein";
    if (norm(w) === "") return sp.leerText || "(leer)";
    if (sp.art === "datum") return datumText(w);
    if (sp.art === "auswahl") return optText(sp, w);
    return String(w);
  }

  // Grund, warum eine Zelle nicht bearbeitbar ist; "" heißt bearbeitbar
  function sperrGrund(def, sp, z) {
    let g = typeof sp.gesperrt === "function" ? sp.gesperrt(z) : sp.gesperrt;
    if (g === true) g = "Diese Angabe lässt sich hier nicht ändern.";
    if (g) return g;
    if (!def.speichern && !def.speichernAlle) return "Diese Angabe lässt sich hier nicht ändern.";
    if (!nameDa()) return NAME_TEXT();
    return "";
  }

  // ---------- zeichnen ----------
  function zelle(def, sp0, z) {
    const sp = eff(sp0, z);
    const t = def.id, k = String(def.schluessel(z));
    const st = vorgemerkt(t, k, sp.s);
    const server = roh(sp, z);
    const grund = sperrGrund(def, sp, z);
    const klassen = ["tg-z", sp.klasse || ""];
    let titel = grund;
    if (grund) klassen.push("tg-zu");
    if (grund && sp.still) klassen.push("tg-still");
    if (st) {
      klassen.push("tg-geaendert");
      titel = `Geändert, aber noch nicht gespeichert. Vorher stand hier: ${lesbar(sp, server)}.`;
      if (!gleich(st.alt, server)) { klassen.push("tg-konflikt"); titel = `Jemand anderes hat diesen Wert inzwischen auf „${lesbar(sp, server)}“ geändert. Als Sie mit der Bearbeitung begonnen haben, stand hier „${lesbar(sp, st.alt)}“.`; }
    }
    if (aktiv && aktiv.t === t && aktiv.z === k && aktiv.s === sp.s) klassen.push("tg-aktiv");
    if (inWahl(t, k, sp.s)) klassen.push("tg-wahl");
    return `<td class="${klassen.join(" ")}" data-z="${e(k)}" data-s="${e(sp.s)}" tabindex="-1"${titel ? ` title="${e(titel)}"` : ""}${sp.stil ? ` style="${sp.stil}"` : ""}>${anzeige(sp, st ? st.neu : server, z)}</td>`;
  }

  function html(def) {
    const t = def.id;
    def.schluessel = def.schluessel || (z => z.id);
    def.name = def.name || (z => `Zeile ${def.schluessel(z)}`);
    defs[t] = def;
    merk[t] = merk[t] || {};
    for (const z of def.zeilen) merk[t][String(def.schluessel(z))] = z;
    // Vormerkungen, die inzwischen dem Server entsprechen, sind erledigt
    for (const [k, felder] of Object.entries(stand[t] || {})) {
      const z = merk[t][k];
      if (!z) continue;
      for (const [s, st] of Object.entries(felder)) {
        const sp = spalte(def, s);
        if (sp && gleich(st.neu, roh(sp, z))) delete felder[s];
      }
      if (!Object.keys(felder).length) delete stand[t][k];
    }
    const kopf = (def.vorne ? `<th class="${def.vorne.klasse || ""}">${def.vorne.kopf || ""}</th>` : "")
      + def.spalten.map(sp => `<th${sp.kopfStil ? ` style="${sp.kopfStil}"` : ""}${sp.kopfTitel ? ` title="${e(sp.kopfTitel)}"` : ""}>${sp.kopfHtml || e(sp.titel)}</th>`).join("")
      + (def.zusatz ? `<th>${def.zusatz.kopf || ""}</th>` : "");
    const zeilen = def.zeilen.map(z => {
      const k = String(def.schluessel(z));
      const f = fehler[t]?.[k];
      return `<tr data-z="${e(k)}" class="${def.zeileKlasse ? def.zeileKlasse(z) : ""} ${f ? "tg-fehler" : ""}"${f ? ` title="Diese Zeile ist nicht gespeichert. ${e(f)}"` : ""}>
        ${def.vorne ? def.vorne.zelle(z) : ""}${def.spalten.map(sp => zelle(def, sp, z)).join("")}${def.zusatz ? `<td>${def.zusatz.zelle(z)}</td>` : ""}</tr>`;
    }).join("");
    const leer = `<tr><td colspan="${def.spalten.length + (def.zusatz ? 1 : 0) + (def.vorne ? 1 : 0)}" class="leer">${e(def.leer || "Hier steht noch nichts.")}</td></tr>`;
    // wer nur lesen darf, bekommt keine Anleitung zum Tippen (die Zellen sind ohnehin gesperrt)
    const tipp = def.hinweis === false || !nameDa() || (!def.speichern && !def.speichernAlle) ? "" : `<div class="tg-tipp" title="${e(TASTEN)}">${e(TIPP)}</div>`;
    return `<div class="tg" data-tg="${e(t)}">${leisteHtml(t)}<div class="tabelle ${def.rahmenKlasse || ""}"><table><thead><tr>${kopf}</tr></thead><tbody>${zeilen || leer}</tbody></table></div>${tipp}</div>`;
  }

  const TIPP = "Klicken Sie in eine Zelle und tippen Sie los, auch ganze Blöcke aus Excel lassen sich mit Strg+V einfügen. Gespeichert wird erst, wenn Sie es bestätigen.";
  const TASTEN = "Enter, Tab und die Pfeiltasten wechseln die Zelle. Esc nimmt eine Eingabe zurück, Strg+Z die letzte Änderung. Zellen mit grauer Ecke lassen sich hier nicht ändern; der Grund steht, wenn Sie mit der Maus darauf zeigen.";

  function zaehle(t) {
    const zeilen = Object.values(stand[t] || {}).filter(f => Object.keys(f).length);
    return { zellen: zeilen.reduce((n, f) => n + Object.keys(f).length, 0), zeilen: zeilen.length };
  }
  function leisteHtml(t) {
    const { zellen, zeilen } = zaehle(t);
    const fe = Object.entries(fehler[t] || {});
    const name = k => { const z = zeileVon(t, k); return z && defs[t] ? defs[t].name(z) : k; };
    return `<div class="tg-leiste" data-tg-leiste="${e(t)}" ${zellen || fe.length ? "" : "hidden"}>
      <strong>${anzahl(zellen, "Änderung", "Änderungen")} in ${anzahl(zeilen, "Zeile", "Zeilen")}</strong>
      <span>${zellen === 1 ? "ist" : "sind"} noch nicht gespeichert.</span>
      <button onclick="Tabelle.pruefen('${e(t)}')" ${zellen ? "" : "disabled"}>Prüfen und speichern</button>
      <button class="leise" onclick="Tabelle.verwerfen('${e(t)}')" ${zellen ? "" : "disabled"}>Änderungen verwerfen</button>
      ${fe.length ? `<div class="tg-fehlerliste">${fe.map(([k, f]) => `${e(name(k))} ist nicht gespeichert. ${e(f)}`).join("<br>")}${zellen ? "<br>Ihre Eingaben sind noch da." : ""}</div>` : ""}
    </div>`;
  }
  function leisteNeu(t) {
    const el = document.querySelector(`[data-tg-leiste="${css(t)}"]`);
    if (el) el.outerHTML = leisteHtml(t);
  }

  const tabEl = t => document.querySelector(`[data-tg="${css(t)}"]`);
  const tdVon = (t, k, s) => tabEl(t)?.querySelector(`td.tg-z[data-z="${css(k)}"][data-s="${css(s)}"]`);
  function zelleNeu(t, k, s) {
    const td = tdVon(t, k, s), def = defs[t], z = zeileVon(t, k), sp = def && spalte(def, s);
    if (!td || !z || !sp) return;
    const tmp = document.createElement("tbody");
    tmp.innerHTML = `<tr>${zelle(def, sp, z)}</tr>`;
    const neu = tmp.querySelector("td");
    const fokus = document.activeElement === td;
    td.replaceWith(neu);
    if (fokus) neu.focus({ preventScroll: true });
  }

  // ---------- auswahl ----------
  const zeilenListe = t => [...(tabEl(t)?.querySelectorAll("tbody tr[data-z]") || [])].map(tr => tr.dataset.z);
  const spaltenListe = t => (defs[t]?.spalten || []).map(sp => sp.s);
  function rechteck() {
    if (!aktiv) return null;
    const a = anker && anker.t === aktiv.t ? anker : aktiv;
    const zs = zeilenListe(aktiv.t), ss = spaltenListe(aktiv.t);
    const z1 = zs.indexOf(a.z), z2 = zs.indexOf(aktiv.z), s1 = ss.indexOf(a.s), s2 = ss.indexOf(aktiv.s);
    if ([z1, z2, s1, s2].includes(-1)) return { t: aktiv.t, zeilen: [aktiv.z], spalten: [aktiv.s] };
    return { t: aktiv.t, zeilen: zs.slice(Math.min(z1, z2), Math.max(z1, z2) + 1), spalten: ss.slice(Math.min(s1, s2), Math.max(s1, s2) + 1) };
  }
  function inWahl(t, k, s) {
    if (!aktiv || !anker || aktiv.t !== t || anker.t !== t || (anker.z === aktiv.z && anker.s === aktiv.s)) return false;
    const r = rechteck();
    return r.zeilen.includes(k) && r.spalten.includes(s);
  }
  function markieren() {
    document.querySelectorAll("td.tg-aktiv, td.tg-wahl").forEach(td => td.classList.remove("tg-aktiv", "tg-wahl"));
    if (!aktiv) return;
    const r = rechteck();
    if (r && (r.zeilen.length > 1 || r.spalten.length > 1)) {
      for (const k of r.zeilen) for (const s of r.spalten) tdVon(r.t, k, s)?.classList.add("tg-wahl");
    }
    tdVon(aktiv.t, aktiv.z, aktiv.s)?.classList.add("tg-aktiv");
  }
  function waehle(t, k, s, erweitern) {
    if (!erweitern || !anker || anker.t !== t) anker = { t, z: k, s };
    aktiv = { t, z: k, s };
    markieren();
    const td = tdVon(t, k, s);
    if (td) { td.focus({ preventScroll: true }); td.scrollIntoView({ block: "nearest", inline: "nearest" }); }
  }
  function bewege(dz, ds, erweitern) {
    if (!aktiv) return;
    const zs = zeilenListe(aktiv.t), ss = spaltenListe(aktiv.t);
    let i = zs.indexOf(aktiv.z), j = ss.indexOf(aktiv.s);
    if (i < 0 || j < 0) return;
    if (ds && !dz && !erweitern) {  // Tab: am Zeilenende in die nächste Zeile
      j += ds;
      if (j >= ss.length) { j = 0; i = Math.min(i + 1, zs.length - 1); }
      if (j < 0) { j = ss.length - 1; i = Math.max(i - 1, 0); }
    } else {
      i = Math.max(0, Math.min(zs.length - 1, i + dz));
      j = Math.max(0, Math.min(ss.length - 1, j + ds));
    }
    waehle(aktiv.t, zs[i], ss[j], erweitern);
  }

  // ---------- vormerken ----------
  function vormerken(t, k, s, neu, gruppe) {
    const def = defs[t], z = zeileVon(t, k), sp = def && spalte(def, s);
    if (!z || !sp) return false;
    const server = roh(sp, z);
    const vorher = vorgemerkt(t, k, s);
    if (vorher ? gleich(vorher.neu, neu) : gleich(server, neu)) return false;
    gruppe.push({ z: k, s, vorher: vorher ? { ...vorher } : null });
    stand[t] = stand[t] || {};
    stand[t][k] = stand[t][k] || {};
    if (gleich(server, neu) && (!vorher || gleich(vorher.alt, server))) delete stand[t][k][s];
    else stand[t][k][s] = { neu, alt: vorher ? vorher.alt : server };
    if (!Object.keys(stand[t][k]).length) delete stand[t][k];
    zelleNeu(t, k, s);
    return true;
  }
  function gruppeAblegen(t, gruppe) {
    if (!gruppe.length) return;
    (rueck[t] = rueck[t] || []).push(gruppe);
    if (rueck[t].length > 200) rueck[t].shift();
    leisteNeu(t);
    markieren();
  }
  function zuruecknehmen(t) {
    const gruppe = rueck[t]?.pop();
    if (!gruppe) return toastText("Es gibt nichts zum Zurücknehmen.");
    for (const g of gruppe.slice().reverse()) {
      stand[t] = stand[t] || {};
      if (g.vorher) (stand[t][g.z] = stand[t][g.z] || {})[g.s] = g.vorher;
      else if (stand[t][g.z]) { delete stand[t][g.z][g.s]; if (!Object.keys(stand[t][g.z]).length) delete stand[t][g.z]; }
      zelleNeu(t, g.z, g.s);
    }
    leisteNeu(t);
    markieren();
  }
  const toastText = s => (typeof toast === "function" ? toast(s) : null);

  // Text aus Excel oder der Tastatur in den Wert der Spalte; undefined: passt nicht
  function deute(sp, text) {
    const w = String(text ?? "").replace(/\r/g, "").trim();
    if (sp.art === "haken") {
      if (/^(ja|x|1|true|wahr|☑|yes)$/i.test(w)) return true;
      if (/^(nein|0|false|falsch|☐|no|)$/i.test(w)) return false;
      return undefined;
    }
    if (sp.art === "auswahl") {
      if (w === "" && sp.leer !== false) return "";
      const o = (sp.optionen || []).find(([v, txt]) => String(v).toLowerCase() === w.toLowerCase() || String(txt).toLowerCase() === w.toLowerCase());
      return o ? o[0] : undefined;
    }
    if (sp.art === "datum") {
      if (w === "") return "";
      let m = w.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (m) return w;
      m = w.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
      return m ? `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}` : undefined;
    }
    return w;
  }

  // ---------- editor in der zelle ----------
  function bearbeite(t, k, s, start) {
    const def = defs[t], z = zeileVon(t, k), sp = def && eff(spalte(def, s), z);
    const td = tdVon(t, k, s);
    if (!z || !sp || !td) return;
    const grund = sperrGrund(def, sp, z);
    if (grund) return toastText(grund);
    const st = vorgemerkt(t, k, s);
    const jetzt = st ? st.neu : roh(sp, z);
    if (sp.art === "haken") {
      const g = [];
      vormerken(t, k, s, !jetzt, g);
      return gruppeAblegen(t, g);
    }
    let el;
    if (sp.art === "auswahl") {
      el = document.createElement("select");
      const opt = [...(sp.leer === false ? [] : [["", sp.leerText || "–"]]), ...(sp.optionen || [])];
      if (!opt.some(([v]) => gleich(v, jetzt))) opt.unshift([jetzt, String(jetzt)]);
      el.innerHTML = opt.map(([v, txt]) => `<option value="${e(v)}">${e(txt)}</option>`).join("");
      el.value = norm(jetzt);
      if (start !== undefined) { const w = deute(sp, start); if (w !== undefined) el.value = norm(w); }
    } else {
      el = document.createElement("input");
      el.type = sp.art === "datum" ? "date" : "text";
      el.value = start !== undefined && sp.art !== "datum" ? start : norm(jetzt);
    }
    el.className = "tg-ed";
    el.setAttribute("aria-label", sp.titel);
    td.classList.add("tg-editiert");
    td.innerHTML = "";
    td.appendChild(el);
    editor = { t, k, s, el, fertig: false };
    el.focus();
    if (el.tagName === "INPUT" && el.type === "text") {
      el.setSelectionRange(el.value.length, el.value.length);  // wie F2 in Excel: Schreibmarke am Ende
    }
    if (el.tagName === "SELECT") {
      // mit der Maus gewählt: gleich übernehmen; mit den Pfeiltasten erst bei Enter, Tab oder Verlassen
      let maus = false;
      el.addEventListener("pointerdown", () => { maus = true; });
      el.addEventListener("change", () => { if (maus) schliesse(true); });
    }
    el.addEventListener("blur", () => setTimeout(() => { if (editor && editor.el === el) schliesse(true, true); }, 0));
  }
  function schliesse(uebernehmen, ohneFokus) {
    const ed = editor;
    if (!ed || ed.fertig) return;
    ed.fertig = true;
    editor = null;
    const sp = eff(spalte(defs[ed.t], ed.s), zeileVon(ed.t, ed.k));
    const g = [];
    if (uebernehmen) {
      const w = sp.art === "text" || !sp.art ? ed.el.value.trim() : ed.el.value;
      vormerken(ed.t, ed.k, ed.s, w, g);
    }
    if (g.length) gruppeAblegen(ed.t, g);
    else zelleNeu(ed.t, ed.k, ed.s);
    markieren();
    if (!ohneFokus) tdVon(ed.t, ed.k, ed.s)?.focus({ preventScroll: true });
  }

  // ---------- tastatur, maus, zwischenablage ----------
  const tdAus = el => el && el.closest ? el.closest("[data-tg] td.tg-z") : null;
  const tabelleVon = td => td.closest("[data-tg]").dataset.tg;

  document.addEventListener("mousedown", ev => {
    const td = tdAus(ev.target);
    if (!td || (editor && editor.el.contains(ev.target))) return;
    if (ev.target.closest("a, button")) return;
    ev.preventDefault();  // keine Textauswahl beim Ziehen; der Fokus geht auf die Zelle
    if (editor) schliesse(true, true);
    waehle(tabelleVon(td), td.dataset.z, td.dataset.s, ev.shiftKey);
    if (ev.target.closest(".tg-haken")) bearbeite(aktiv.t, aktiv.z, aktiv.s);
  });
  document.addEventListener("dblclick", ev => {
    const td = tdAus(ev.target);
    if (!td || editor || ev.target.closest("a, button")) return;
    if (td.querySelector(".tg-haken")) return;
    bearbeite(tabelleVon(td), td.dataset.z, td.dataset.s);
  });
  // ein Klick in eine Tabelle löst keine Klicks der umgebenden Karte aus (z. B. Fund wählen)
  document.addEventListener("click", ev => {
    if (tdAus(ev.target) && !ev.target.closest("a, button") && !(editor && editor.el.contains(ev.target))) ev.stopPropagation();
  }, true);

  document.addEventListener("focusin", ev => { fokusDrin = !!(tdAus(ev.target) || ev.target.classList?.contains("tg-ed")); });

  document.addEventListener("keydown", ev => {
    if (document.querySelector(".tg-schleier")) {
      if (ev.key === "Escape") { ev.preventDefault(); dialogZu(); }
      return;
    }
    if (editor && ev.target === editor.el) {
      const sel = editor.el.tagName === "SELECT";
      if (ev.key === "Escape") { ev.preventDefault(); schliesse(false); }
      else if (ev.key === "Enter") { ev.preventDefault(); schliesse(true); bewege(ev.shiftKey ? -1 : 1, 0); }
      else if (ev.key === "Tab") { ev.preventDefault(); schliesse(true); bewege(0, ev.shiftKey ? -1 : 1); }
      else if (!sel && (ev.key === "ArrowUp" || ev.key === "ArrowDown")) { ev.preventDefault(); schliesse(true); bewege(ev.key === "ArrowUp" ? -1 : 1, 0); }
      return;
    }
    const td = tdAus(ev.target);
    if (!td || !aktiv) return;
    const t = aktiv.t, strg = ev.ctrlKey || ev.metaKey;
    const pfeil = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[ev.key];
    if (pfeil) { ev.preventDefault(); return bewege(pfeil[0], pfeil[1], ev.shiftKey); }
    if (ev.key === "Tab") { ev.preventDefault(); return bewege(0, ev.shiftKey ? -1 : 1); }
    if (ev.key === "Enter") { ev.preventDefault(); return ev.shiftKey ? bewege(-1, 0) : bearbeite(t, aktiv.z, aktiv.s); }
    if (ev.key === "F2") { ev.preventDefault(); return bearbeite(t, aktiv.z, aktiv.s); }
    if (strg && (ev.key === "z" || ev.key === "Z")) { ev.preventDefault(); return zuruecknehmen(t); }
    if (strg && (ev.key === "a" || ev.key === "A")) {
      ev.preventDefault();
      const zs = zeilenListe(t), ss = spaltenListe(t);
      anker = { t, z: zs[0], s: ss[0] }; aktiv = { t, z: zs[zs.length - 1], s: ss[ss.length - 1] };
      return markieren();
    }
    if (ev.key === "Escape") {  // vorgemerkte Werte der Markierung zurück auf den Stand des Servers
      ev.preventDefault();
      const r = rechteck(), g = [];
      for (const k of r.zeilen) for (const s of r.spalten) {
        const st = vorgemerkt(t, k, s);
        if (st) { const sp = spalte(defs[t], s); vormerken(t, k, s, roh(sp, zeileVon(t, k)), g); }
      }
      return gruppeAblegen(t, g);
    }
    if (ev.key === "Delete" || ev.key === "Backspace") {
      ev.preventDefault();
      const r = rechteck(), g = [];
      for (const k of r.zeilen) for (const s of r.spalten) {
        const z = zeileVon(t, k), sp = eff(spalte(defs[t], s), z);
        if (!z || sperrGrund(defs[t], sp, z) || sp.art === "haken" || (sp.art === "auswahl" && sp.leer === false)) continue;
        vormerken(t, k, s, "", g);
      }
      return gruppeAblegen(t, g);
    }
    if (ev.key === " " && td.querySelector(".tg-haken")) { ev.preventDefault(); return bearbeite(t, aktiv.z, aktiv.s); }
    if (ev.key.length === 1 && !strg && !ev.altKey) { ev.preventDefault(); return bearbeite(t, aktiv.z, aktiv.s, ev.key); }
  });

  document.addEventListener("copy", ev => {
    if (!tdAus(document.activeElement) || !aktiv || editor) return;
    const r = rechteck(), def = defs[r.t];
    const text = r.zeilen.map(k => r.spalten.map(s => {
      const z = zeileVon(r.t, k), sp = eff(spalte(def, s), z), st = vorgemerkt(r.t, k, s);
      const w = st ? st.neu : roh(sp, z);
      return sp.art === "haken" ? (w ? "ja" : "nein") : sp.art === "auswahl" ? (norm(w) ? optText(sp, w) : "") : sp.art === "datum" ? datumText(w) : norm(w).replace(/[\t\n]/g, " ");
    }).join("\t")).join("\n");
    ev.clipboardData.setData("text/plain", text);
    ev.preventDefault();
    toastText(`${anzahl(r.zeilen.length * r.spalten.length, "Zelle", "Zellen")} kopiert.`);
  });

  document.addEventListener("paste", ev => {
    if (!tdAus(document.activeElement) || !aktiv || editor) return;
    ev.preventDefault();
    const text = (ev.clipboardData.getData("text/plain") || "").replace(/\r/g, "");
    const block = text.replace(/\n$/, "").split("\n").map(z => z.split("\t"));
    if (!block.length) return;
    const r = rechteck(), t = r.t, def = defs[t];
    const zs = zeilenListe(t), ss = spaltenListe(t);
    const z0 = zs.indexOf(r.zeilen[0]), s0 = ss.indexOf(r.spalten[0]);
    // ein einzelner Wert füllt die ganze Markierung, wie in Excel
    const hoch = block.length === 1 && block[0].length === 1 ? r.zeilen.length : block.length;
    const breit = block.length === 1 && block[0].length === 1 ? r.spalten.length : Math.max(...block.map(z => z.length));
    const g = [];
    let gesperrt = 0, ungueltig = 0, draussen = 0, gesetzt = 0;
    for (let i = 0; i < hoch; i++) for (let j = 0; j < breit; j++) {
      const quelle = block.length === 1 && block[0].length === 1 ? block[0][0] : block[i]?.[j];
      if (quelle === undefined) continue;
      const k = zs[z0 + i], s = ss[s0 + j];
      if (k === undefined || s === undefined) { draussen++; continue; }
      const z = zeileVon(t, k), sp = eff(spalte(def, s), z);
      if (sperrGrund(def, sp, z)) { gesperrt++; continue; }
      const w = deute(sp, quelle);
      if (w === undefined) { ungueltig++; continue; }
      if (vormerken(t, k, s, w, g)) gesetzt++;
    }
    gruppeAblegen(t, g);
    const teile = [gesetzt ? `${anzahl(gesetzt, "Zelle ist", "Zellen sind")} eingefügt, aber noch nicht gespeichert.` : "Es wurde nichts eingefügt."];
    if (gesperrt) teile.push(`${anzahl(gesperrt, "Zelle lässt", "Zellen lassen")} sich hier nicht ändern.`);
    if (ungueltig) teile.push(`${anzahl(ungueltig, "Wert passt", "Werte passen")} nicht in die Spalte.`);
    if (draussen) teile.push(`${anzahl(draussen, "Wert ragte", "Werte ragten")} über die Tabelle hinaus.`);
    toastText(teile.join(" "));
  });

  // nach dem Neuzeichnen einer Seite den Fokus auf die gewählte Zelle zurücksetzen
  new MutationObserver(() => {
    if (editor && !editor.el.isConnected) schliesse(true, true);  // Seite hat das Feld weggezeichnet
    if (!aktiv || editor || !fokusDrin) return;
    const a = document.activeElement;
    if (a && a !== document.body && a.isConnected) return;
    const td = tdVon(aktiv.t, aktiv.z, aktiv.s);
    if (td) td.focus({ preventScroll: true });
  }).observe(document.documentElement, { childList: true, subtree: true });

  // ---------- prüfen und speichern ----------
  function offeneZeilen(t) {  // in der Reihenfolge der Tabelle
    const reihe = (defs[t]?.zeilen || []).map(z => String(defs[t].schluessel(z)));
    const platz = k => (reihe.indexOf(k) + 1) || Infinity;
    return Object.entries(stand[t] || {}).filter(([, f]) => Object.keys(f).length)
      .map(([k, felder]) => ({ k, z: zeileVon(t, k), felder })).sort((a, b) => platz(a.k) - platz(b.k));
  }

  function pruefen(t) {
    if (editor) schliesse(true, true);
    const def = defs[t];
    const zeilen = offeneZeilen(t).filter(x => x.z);
    if (!def || !zeilen.length) return toastText("Es gibt keine Änderungen zum Speichern.");
    const name = nameDa() && typeof anwender === "function" ? anwender() : "";
    let konflikte = 0;
    const reihen = zeilen.map(({ k, z, felder }) => Object.entries(felder).map(([s, st], i) => {
      const sp = eff(spalte(def, s), z), jetzt = roh(sp, z);
      const konflikt = !gleich(st.alt, jetzt);
      if (konflikt) konflikte++;
      return `<tr><td>${i === 0 ? `<strong>${e(def.name(z))}</strong>` : ""}</td><td>${e(sp.titel)}</td>
        <td><span class="tg-alt">${e(lesbar(sp, jetzt))}</span>${konflikt ? `<div class="tg-konfliktzeile">Jemand anderes hat das inzwischen geändert. Als Sie begonnen haben, stand hier „${e(lesbar(sp, st.alt))}“.</div>` : ""}</td>
        <td><span class="tg-neu">${e(lesbar(sp, st.neu))}</span></td></tr>`;
    }).join("")).join("");
    const folgen = def.folgen ? zeilen.map(({ z, felder }) => {
      const werte = Object.fromEntries(Object.entries(felder).map(([s, st]) => [s, st.neu]));
      const f = def.folgen(z, werte).filter(Boolean);
      return f.length ? `<li><strong>${e(def.name(z))}:</strong> ${f.map(e).join(" ")}</li>` : "";
    }).join("") : "";
    const n = zaehle(t).zellen;
    dialog(`
      <h2>Möchten Sie ${anzahl(n, "Änderung", "Änderungen")} speichern?</h2>
      <p class="grau" style="margin-top:0">${def.titel ? `${e(def.titel)}. ` : ""}Gespeichert wird erst, wenn Sie auf „Ja, speichern“ klicken. Klappt es bei einer Zeile nicht, bleiben Ihre Eingaben dort erhalten.</p>
      ${konflikte ? `<div class="hinweis rot" style="margin-bottom:10px"><strong>${konflikte === 1 ? "Einen Wert hat" : `${konflikte} Werte hat`} inzwischen jemand anderes geändert.</strong> Unter „Bisher“ steht der aktuelle Stand, und Ihre Änderung würde ihn ersetzen.</div>` : ""}
      <div class="tabelle"><table><thead><tr><th>Zeile</th><th>Spalte</th><th>Bisher</th><th>Neu</th></tr></thead><tbody>${reihen}</tbody></table></div>
      ${folgen ? `<div class="tg-folgen"><strong>Das passiert außerdem:</strong><ul>${folgen}</ul></div>` : ""}
      ${def.notiz ? `<label for="tg-notiz">${e(def.notiz)}</label><input type="text" id="tg-notiz" maxlength="500" placeholder="zum Beispiel: laut Einkauf, E-Mail vom 14.11.">` : ""}
      <div class="tg-knoepfe">
        <span class="grau" style="margin-right:auto;font-size:12.5px">${name ? `Gespeichert wird unter Ihrem Namen, <strong>${e(name)}</strong>.` : (typeof nameHinweis === "function" ? nameHinweis() : NAME_TEXT())}</span>
        <button class="leise" onclick="Tabelle.dialogZu()">Abbrechen</button>
        <button id="tg-ja" onclick="Tabelle.speichern('${e(t)}')" ${name ? "" : `disabled title="${e(NAME_TEXT())}"`}>Ja, speichern</button>
      </div>`);
    document.getElementById(def.notiz ? "tg-notiz" : "tg-ja")?.focus();
  }

  function dialog(inhalt) {
    dialogZu(true);
    const el = document.createElement("div");
    el.className = "tg-schleier";
    el.innerHTML = `<div class="tg-dialog" role="dialog" aria-modal="true">${inhalt}</div>`;
    el.addEventListener("mousedown", ev => { if (ev.target === el) dialogZu(); });
    document.body.appendChild(el);
    return el;
  }
  function dialogZu(still) {
    document.querySelectorAll(".tg-schleier").forEach(x => x.remove());
    if (!still && aktiv) tdVon(aktiv.t, aktiv.z, aktiv.s)?.focus({ preventScroll: true });
  }

  async function speichern(t) {
    const def = defs[t];
    const notiz = (document.getElementById("tg-notiz")?.value || "").trim();
    // nach dem Speichern nur neu zeichnen, wenn die Seite noch dieselbe ist (index.html: seitenWache)
    const wache = typeof seitenWache === "function" ? seitenWache() : () => true;
    const los = async von => {
      const knopf = document.getElementById("tg-ja");
      if (knopf) { knopf.disabled = true; knopf.textContent = "Speichert …"; }
      const ergebnis = [];
      if (def.speichernAlle) {  // ein Aufruf für die ganze Tabelle, z. B. wenn das Backend sie als Ganzes ersetzt
        const offen = offeneZeilen(t).filter(x => x.z).map(({ k, z, felder }) => ({ k, z, werte: Object.fromEntries(Object.entries(felder).map(([s, st]) => [s, st.neu])) }));
        try {
          await def.speichernAlle(offen, { von, notiz });
          for (const o of offen) { delete stand[t][o.k]; if (fehler[t]) delete fehler[t][o.k]; ergebnis.push({ z: o.z, ok: true, n: Object.keys(o.werte).length }); }
        } catch (f) {
          for (const o of offen) { (fehler[t] = fehler[t] || {})[o.k] = f.message; ergebnis.push({ z: o.z, ok: false, text: f.message }); }
        }
      } else for (const { k, z, felder } of offeneZeilen(t).filter(x => x.z)) {
        const werte = Object.fromEntries(Object.entries(felder).map(([s, st]) => [s, st.neu]));
        try {
          const r = await def.speichern(z, werte, { von, notiz });
          delete stand[t][k];
          if (fehler[t]) delete fehler[t][k];
          ergebnis.push({ z, ok: true, n: Object.keys(werte).length, hinweis: r?.hinweis });
        } catch (f) {
          (fehler[t] = fehler[t] || {})[k] = f.message;
          ergebnis.push({ z, ok: false, text: f.message });
        }
      }
      rueck[t] = [];
      const gut = ergebnis.filter(x => x.ok), schlecht = ergebnis.filter(x => !x.ok);
      try { if (def.nachher && wache()) await def.nachher(); } catch (f) { console.error(f); }  // auch nach Fehlern: Teile können gespeichert sein
      leisteNeu(t);
      if (!schlecht.length && !ergebnis.some(x => x.hinweis)) {
        dialogZu();
        toastText(`${anzahl(gut.reduce((n, x) => n + x.n, 0), "Änderung", "Änderungen")} gespeichert.`);
        return;
      }
      dialog(`<h2>${!schlecht.length ? "Alles ist gespeichert" : gut.length ? "Nur ein Teil ist gespeichert" : "Nichts ist gespeichert"}</h2>
        <ul style="padding-left:18px">${ergebnis.map(x => x.ok
          ? `<li class="tg-ok">${e(def.name(x.z))} ist gespeichert.${x.hinweis ? ` <span style="color:var(--text)">${e(x.hinweis)}</span>` : ""}</li>`
          : `<li class="tg-nein">${e(def.name(x.z))} ist nicht gespeichert. ${e(x.text)} <span class="grau">Ihre Eingaben sind noch da.</span></li>`).join("")}</ul>
        <div class="tg-knoepfe"><button onclick="Tabelle.dialogZu()">Schließen</button></div>`);
      // Zeilen mit Fehler sofort markieren, auch ohne Neuzeichnen der Seite
      for (const x of schlecht) {
        const tr = tabEl(t)?.querySelector(`tr[data-z="${css(String(def.schluessel(x.z)))}"]`);
        if (tr) { tr.classList.add("tg-fehler"); tr.title = `Diese Zeile ist nicht gespeichert. ${x.text}`; }
      }
    };
    if (typeof mitName === "function") return mitName(los);
    return los("");
  }

  function verwerfen(t, still) {
    if (editor && editor.t === t) schliesse(false, true);
    const zellen = Object.entries(stand[t] || {}).flatMap(([k, f]) => Object.keys(f).map(s => [k, s]));
    delete stand[t];
    delete fehler[t];
    rueck[t] = [];
    for (const [k, s] of zellen) zelleNeu(t, k, s);
    tabEl(t)?.querySelectorAll("tr.tg-fehler").forEach(tr => { tr.classList.remove("tg-fehler"); tr.removeAttribute("title"); });
    leisteNeu(t);
    if (!still) toastText(`${anzahl(zellen.length, "Änderung", "Änderungen")} verworfen.`);
  }

  const alleOffen = () => Object.keys(stand).reduce((n, t) => n + zaehle(t).zellen, 0);

  // ---------- seite verlassen: erst fragen ----------
  window.addEventListener("beforeunload", ev => {
    if (editor) schliesse(true, true);
    if (!alleOffen()) return;
    ev.preventDefault();
    ev.returnValue = "";
  });
  // läuft vor dem Router in index.html (seiten/tabelle.js wird vor dem Hauptskript geladen)
  let weiter = false;
  window.addEventListener("hashchange", ev => {
    if (weiter) { weiter = false; return; }
    if (editor) schliesse(true, true);
    const n = alleOffen();
    if (!n) return;
    ev.stopImmediatePropagation();
    const ziel = location.href;
    history.replaceState(null, "", ev.oldURL);
    dialog(`<h2>Ungespeicherte Änderungen</h2>
      <p>Sie haben ${anzahl(n, "Änderung", "Änderungen")} noch nicht gespeichert. Wenn Sie die Seite jetzt verlassen, ${n === 1 ? "geht sie" : "gehen sie"} verloren.</p>
      <div class="tg-knoepfe"><button class="leise" onclick="Tabelle.dialogZu()">Hierbleiben</button>
        <button class="gefahr" id="tg-weg">Änderungen verwerfen und weiter</button></div>`);
    document.getElementById("tg-weg").onclick = () => {
      for (const t of Object.keys(stand)) verwerfen(t, true);
      dialogZu(true);
      weiter = true;
      location.href = ziel;
    };
  });

  window.Tabelle = {
    html, pruefen, speichern, verwerfen, dialogZu, zuruecknehmen,
    offen: t => (t ? zaehle(t).zellen : alleOffen()),
    // Vormerkung von außen (Tests, Knöpfe einer Seite); gibt true zurück, wenn sich etwas ändert
    setze(t, k, s, wert) { const g = []; const r = vormerken(t, String(k), s, wert, g); gruppeAblegen(t, g); return r; },
  };
})();
