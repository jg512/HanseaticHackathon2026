// Sprachmodelle: Seite „Sprachmodelle“ unter „Daten“ (welche Modelle eingerichtet sind, Verbindung
// testen) und auf der Vorgangsseite die Karte „Einstellungen für diesen Vorgang“ (Sprachmodell und
// Versuche der Websuche je Position, beides erst nach einer Rückfrage gespeichert).
// Backend: GET /api/modelle, POST /api/modelle/<name>/test, GET/PUT /api/vorgaenge/<id>/einstellungen
// (docs/api.md). Zugangsschlüssel zeigt und nimmt die Oberfläche nie: Modelle richtet der
// Administrator in config/modelle.yaml auf dem Server ein.
(function () {
  "use strict";

  const E = {};        // Vorgangs-ID -> {daten, el, zeit, laedt, wahl: {modell, versuche}, frage}
  const S = { liste: null, fehler: null, tests: {}, lauf: 0 };
  const ALTER = 15000; // ms, nach denen die Karte die Einstellungen neu holt
  const ICON_MODELL = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9z"/><path d="M9 1.5v2.5M15 1.5v2.5M9 20v2.5M15 20v2.5M1.5 9H4M1.5 15H4M20 9h2.5M20 15h2.5"/></svg>';
  const ART = { chat: "schreibt Texte", embedding: "Ähnlichkeitssuche" };

  const stil = document.createElement("style");
  stil.textContent = `
    .sm-gitter { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0 22px; }
    .sm-gitter > div { min-width: 0; }
    .sm select, .sm input[type=number] { font: inherit; padding: 7px 9px; border: 1px solid #cfd4d1; border-radius: 8px; background: #fff; max-width: 100%; }
    .sm select { width: 100%; }
    .sm input[type=number] { width: 110px; }
    .sm select:focus, .sm input[type=number]:focus { outline: none; border-color: var(--gruen-marke); box-shadow: 0 0 0 3px rgba(187, 208, 61, .35); }
    .sm-klein { font-size: 12.5px; color: var(--grau); margin: 6px 0 0; }
    .sm-liste { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }
    .sm-liste li { margin: 2px 0; }
    .sm-warn { background: var(--gelb-hell); color: var(--gelb); border-left: 3px solid #d9a400; border-radius: 0; padding: 9px 12px; margin: 10px 0 0; font-size: 13px; }
    .sm-frage { background: var(--limette-hell); border-left: 3px solid var(--limette); padding: 10px 12px; margin-top: 12px; font-size: 13.5px; }
    .sm-wirkt { border-top: 1px solid var(--linie); margin-top: 14px; padding-top: 10px; font-size: 13px; }
    .sm-rechts { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; margin-top: 12px; }
    .sm-test { font-size: 12.5px; overflow-wrap: anywhere; }
    .sm tr.sm-testzeile td { padding-top: 0; }
    .sm-test.ok { color: var(--gruen); } .sm-test.nein { color: var(--rot); }
    .sm td { vertical-align: top; }
    @media (max-width: 900px) { .sm-gitter { grid-template-columns: 1fr; } }`;
  document.head.appendChild(stil);

  const pille = (text, klasse) => `<span class="chip ${klasse}">${esc(text)}</span>`;
  const host = url => { try { return new URL(url).host; } catch { return url || ""; } };

  // ---------- Karte auf der Vorgangsseite ----------

  function eintrag(vid) { return (E[vid] = E[vid] || { daten: null, wahl: null, frage: false }); }

  async function lade(vid) {
    const e = eintrag(vid);
    if (e.laedt) return;
    e.laedt = true;
    try {
      e.daten = await api(`/api/vorgaenge/${vid}/einstellungen`);
      e.fehler = null;
    } catch (f) { e.fehler = f.message; }
    e.laedt = false; e.zeit = Date.now();
    zeichneKarte(vid);
  }

  // was der Anwender gerade eingestellt, aber noch nicht gespeichert hat
  function wahl(e) {
    const d = e.daten;
    return e.wahl || { modell: d.modell || "", versuche: d.internet_versuche ?? "" };
  }
  function geaendert(e) {
    const w = wahl(e), d = e.daten;
    const versuche = w.versuche === "" ? null : +w.versuche;
    return (w.modell || null) !== (d.modell || null) || versuche !== (d.internet_versuche ?? null);
  }
  function versucheOk(e) {
    const v = wahl(e).versuche;
    return v === "" || (/^\d+$/.test(String(v)) && +v >= 0 && +v <= e.daten.internet_versuche_max);
  }

  const versuche = n => n === 1 ? "1 Versuch" : `${n} Versuchen`;
  // Rückfrage vor dem Speichern, je geänderte Einstellung ein Satz
  function frageModell(d, name) {
    if (!name) return `Für diesen Vorgang künftig wieder das Standard-Sprachmodell „${d.standard.websuche || "–"}“ verwenden?`;
    return `Für diesen Vorgang künftig „${name}“ verwenden?`;
  }
  function frageVersuche(d, v) {
    if (v === "" || v === null) {
      const n = d.internet_versuche_standard;
      return n ? `Für diesen Vorgang künftig wieder den Standard von ${versuche(n)} je Position verwenden?`
        : "Für diesen Vorgang künftig wieder den Standard verwenden? Das heißt im Moment: keine Websuche.";
    }
    return +v === 0 ? "Für diesen Vorgang künftig nicht mehr im Internet suchen?"
      : `Für diesen Vorgang künftig mit höchstens ${versuche(+v)} je Position im Internet suchen?`;
  }
  function dauer(d, n) {
    if (!n) return "Ohne Websuche sucht der Agent nur in der Datenbank, im Ordner des Herstellers und im Dokumentenarchiv. Findet er nichts, legt er einen E-Mail-Entwurf an den Hersteller an.";
    const min = Math.ceil(n * d.sekunden_je_versuch_max / 60);
    return `Ein Versuch dauert meist wenige Sekunden, höchstens etwa ${d.sekunden_je_versuch_max} Sekunden. Mit ${versuche(n)} kann eine Position also bis zu ${min} Minute${min === 1 ? "" : "n"} dauern.`;
  }
  // was der nächste Lauf tut, als ganzer Satz
  function naechsterLauf(d) {
    const n = d.internet_versuche_wirksam, w = d.modell_wirksam;
    const suche = n ? `sucht der Agent mit höchstens ${versuche(n)} je Position im Internet` : "sucht der Agent nicht im Internet";
    const namen = [...new Set([w.websuche, w.email].filter(Boolean))];
    const modell = !namen.length ? "arbeitet ohne Sprachmodell"
      : namen.length === 1 ? `nutzt das Sprachmodell „${namen[0]}“`
      : `nutzt für die Websuche „${w.websuche}“ und für die E-Mail-Entwürfe „${w.email}“`;
    return `Beim nächsten Lauf ${suche} und ${modell}.`;
  }

  function karteHtml(v, e) {
    const kopf = "";  // die Überschrift steht im Kopf des Teils (index.html, abschnitte)
    if (!e.daten) return kopf + (e.fehler ? `<div class="hinweis rot">Die Einstellungen ließen sich nicht laden. Bitte laden Sie die Seite neu.</div>` : `<p class="grau">Einstellungen werden geladen …</p>`);
    const d = e.daten, w = wahl(e);
    const sucht = d.laeuft || v.schritt === "suche";
    const aus = sucht || !darfSchreiben() ? "disabled" : "";  // Leser sehen die Werte, ändern sie aber nicht
    const genutzt = d.sprachmodell_genutzt, irgendwo = genutzt.websuche || genutzt.email;
    const n = w.versuche === "" ? d.internet_versuche_standard : +w.versuche;

    const aufgaben = [
      genutzt.websuche ? "Es hilft der Websuche, auf der Website des Herstellers die passenden Links auszuwählen." : "",
      genutzt.email ? "Es schreibt Anrede und Einleitung der E-Mail-Entwürfe. Hersteller, Typ und die Liste der Dokumente setzt immer der Agent selbst ein." : "",
    ].filter(Boolean);
    const modell = irgendwo ? `
        <label for="sm-modell-${v.id}" style="margin-top:0">Sprachmodell</label>
        <select id="sm-modell-${v.id}" onchange="sprachmodelle.waehle(${v.id}, 'modell', this.value)" ${aus}>
          <option value="">Standard: ${esc(d.standard.websuche || "keines eingerichtet")}</option>
          ${d.modell_auswahl.map(m => `<option value="${esc(m.name)}" ${m.name === w.modell ? "selected" : ""}>${esc(m.name)}</option>`).join("")}
          ${w.modell && !d.modell_auswahl.some(m => m.name === w.modell) ? `<option value="${esc(w.modell)}" selected>${esc(w.modell)}, steht nicht mehr zur Verfügung</option>` : ""}
        </select>
        ${aufgaben.map(t => `<p class="sm-klein">${esc(t)}</p>`).join("")}
        <p class="sm-klein">Das Sprachmodell entscheidet nichts und gibt nichts frei. Das tun immer Sie.</p>
        ${d.hinweise.map(h => `<div class="sm-warn">${esc(h)}</div>`).join("")}`
      : `<label style="margin-top:0">Sprachmodell</label>
        <p style="margin:4px 0 0">Der Agent nutzt derzeit kein Sprachmodell. Die Websuche arbeitet nach festen Regeln, und die E-Mail-Entwürfe entstehen aus einer festen Vorlage.</p>
        <p class="sm-klein">Einschalten kann es die Administration.${AUSGEBLENDET.has("sprachmodelle") ? "" : ` Welche Sprachmodelle bereitstehen, sehen Sie unter <a href="#/sprachmodelle">Sprachmodelle</a>.`}</p>`;

    const websuche = `
        <label for="sm-versuche-${v.id}" style="margin-top:0">Websuche: Versuche je Position</label>
        <input type="number" id="sm-versuche-${v.id}" min="0" max="${d.internet_versuche_max}" step="1" value="${esc(w.versuche)}"
          placeholder="${d.internet_versuche_standard}" oninput="sprachmodelle.waehle(${v.id}, 'versuche', this.value.trim(), true)" ${aus}>
        <p class="sm-klein">Erlaubt sind 0 bis ${d.internet_versuche_max}. Bei 0 sucht der Agent nicht im Internet. ${d.internet_versuche_standard ? `Lassen Sie das Feld leer, gilt der Standard von ${versuche(d.internet_versuche_standard)}.` : "Lassen Sie das Feld leer, gilt der Standard, und der sieht keine Websuche vor."}</p>
        <p class="sm-klein">Ein Versuch ist der Aufruf einer Seite oder eines PDFs auf der Website des Herstellers. Der Agent sucht dort nur, wenn er in der Datenbank, im Ordner des Herstellers und im Dokumentenarchiv nichts gefunden hat.</p>
        <p class="sm-klein" id="sm-dauer-${v.id}">${esc(dauer(d, versucheOk(e) ? n : 0))}</p>`;

    const fragen = [];
    if ((w.modell || null) !== (d.modell || null)) fragen.push(frageModell(d, w.modell));
    if ((w.versuche === "" ? null : +w.versuche) !== (d.internet_versuche ?? null)) fragen.push(frageVersuche(d, w.versuche));

    return `<div class="sm">${kopf}
      ${sucht ? `<div class="sm-warn" style="margin:0 0 12px">Der Agent sucht gerade. Sie können die Einstellungen ändern, sobald er fertig ist.</div>` : ""}
      ${d.modell_fehler ? `<div class="hinweis rot">${esc(d.modell_fehler)}</div>` : ""}
      <div class="sm-gitter"><div>${modell}</div><div>${websuche}</div></div>
      <div class="sm-wirkt">${esc(naechsterLauf(d))}</div>
      ${e.frage && fragen.length ? `<div class="sm-frage">${fragen.map(f => `<div><strong>${esc(f)}</strong></div>`).join("")}
          <div class="zeile" style="margin-top:8px"><button onclick="sprachmodelle.speichere(${v.id})" ${sperre()} ${aus}>Einstellungen übernehmen</button>
            <button class="leise" onclick="sprachmodelle.frage(${v.id}, false)">Nicht übernehmen</button></div></div>`
        : `<div class="sm-rechts">${nameHinweis()}
          <button class="leise" id="sm-zurueck-${v.id}" onclick="sprachmodelle.verwerfen(${v.id})" style="${geaendert(e) ? "" : "display:none"}">Änderung verwerfen</button>
          <button id="sm-speichern-${v.id}" onclick="sprachmodelle.frage(${v.id}, true)" ${geaendert(e) && versucheOk(e) && !sucht ? "" : "disabled"} ${sperre()}>Einstellungen speichern …</button></div>`}
    </div>`;
  }

  // kurzer Stand neben dem Titel des zugeklappten Teils (index.html, teilKurz)
  function kurz(e) {
    const n = e.daten?.internet_versuche_wirksam;
    return !e.daten ? "" : n ? `Websuche mit höchstens ${versuche(n)} je Position` : "Ohne Websuche";
  }
  function zeichneKarte(vid) {
    const e = E[vid];
    if (!e?.el?.isConnected || zustand.v?.id !== vid) return;
    teilKurz(e.el, kurz(e));
    const html = karteHtml(zustand.v, e);
    if (e.el._html === html) return;
    if (e.el.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
    e.el._html = html;
    e.el.innerHTML = html;
  }

  erweitere("vorgang", {
    titel: "Einstellungen für diesen Vorgang", schluessel: "einstellungen",
    zeichne(v, el) {
      const e = eintrag(v.id);
      e.el = el;
      el.innerHTML = karteHtml(v, e);
      el._html = el.innerHTML;
      teilKurz(el, kurz(e));
      // während und direkt nach einem Lauf öfter nachsehen (gesperrt / wieder frei)
      const sucht = v.schritt === "suche";
      if (!e.zeit || Date.now() - e.zeit > ALTER || sucht !== !!e.daten?.laeuft) lade(v.id);
    },
  });

  // ---------- Seite „Sprachmodelle“ ----------

  async function seite(m, app) {
    const lauf = ++S.lauf;
    if (!S.liste && !S.fehler) app.innerHTML = `<div class="leer">lädt …</div>`;
    // eigener Abruf statt api(): bei einer falsch eingerichteten Liste kommt auch „detail“ mit
    try {
      const r = await fetch(API + "/api/modelle"), j = await r.json().catch(() => ({}));
      if (r.ok) Object.assign(S, { liste: j, fehler: null, detail: null });
      else Object.assign(S, { liste: null, fehler: j.error || j.fehler || "Die Liste der Sprachmodelle ließ sich nicht laden. Bitte laden Sie die Seite neu.", detail: j.detail || null });
    } catch { Object.assign(S, { liste: null, fehler: "Die Liste der Sprachmodelle ließ sich nicht laden. Bitte laden Sie die Seite neu.", detail: null }); }
    if (lauf !== S.lauf || zustand.seite !== "sprachmodelle") return;
    zeichneSeite();
  }

  function testZelle(name) {
    const t = S.tests[name];
    if (!t) return "";
    if (t.laeuft) return `<div class="sm-test grau">Die Verbindung zu „${esc(name)}“ wird getestet …</div>`;
    if (t.fehlerText) return `<div class="sm-test nein">Der Test ließ sich nicht starten. ${esc(t.fehlerText)}</div>`;
    const sek = (t.dauer_ms / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 });
    return t.erreichbar
      ? `<div class="sm-test ok">„${esc(name)}“ antwortet. Die Antwort kam nach ${sek} Sekunden.</div>`
      : `<div class="sm-test nein">„${esc(name)}“ antwortet nicht. ${esc(t.fehler)}
          ${t.detail ? `<details class="sm-klein"><summary>Einzelheiten für die Administration</summary><span class="mono">${esc(t.detail)}</span></details>` : ""}</div>`;
  }

  function zeichneSeite() {
    const app = $("#app");
    const kopf = `<div class="kopfzeile"><div><h1>Sprachmodelle</h1>
      <div class="grau">Hier sehen Sie, welche Sprachmodelle der Agent nutzen kann. Welches davon ein Vorgang nimmt, wählen Sie auf der Seite des Vorgangs.</div></div></div>`;
    const erklaerung = `<details class="karte" data-merk="sm-neu" ${offenAttr("sm-neu")}><summary style="cursor:pointer"><h2 style="display:inline;margin:0">Neues Sprachmodell hinzufügen</h2></summary>
      <p style="margin:10px 0 0">Neue Sprachmodelle richtet die Administration ein. Sie erscheinen hier ohne Neustart.</p>
      <p class="sm-klein">Zugangsschlüssel werden hier nie angezeigt oder eingegeben. Sie sehen nur, ob einer hinterlegt ist. Lokale Modelle, zum Beispiel über Ollama, brauchen keinen Schlüssel.</p>
      ${istAdmin() ? `<p class="sm-klein">Für die Administration: Die Liste steht in <span class="mono">config/modelle.yaml</span>, die Anleitung in <span class="mono">config/README.md</span>.</p>` : ""}</details>`;
    if (S.fehler) {
      app.innerHTML = `<div class="sm">${kopf}<div class="hinweis rot">${esc(S.fehler)}
        ${S.detail && istAdmin() ? `<details class="sm-klein" style="margin-top:6px"><summary>Einzelheiten für die Administration</summary><span class="mono">${esc(S.detail)}</span></details>` : ""}</div>
        <button class="leise" onclick="sprachmodelle.neu()">Liste neu laden</button>${erklaerung}</div>`;
      return;
    }
    const d = S.liste, genutzt = d.sprachmodell_genutzt;
    const aufgabe = a => d.aufgaben[a] || a;
    // Schlüssel: hinterlegt, bei lokalen Modellen wie Ollama nicht nötig („-“ in der Datei), sonst fehlt er
    const zugang = mo => pille(mo.zugang_text || (mo.zugang_hinterlegt ? "Schlüssel hinterlegt" : mo.ohne_schluessel ? "Kein Schlüssel nötig" : "Schlüssel fehlt"),
      mo.zugang_hinterlegt ? "s-geprueft" : mo.ohne_schluessel ? "s-kein_ex" : "s-info_fehlt");
    // Verbindung testen: nur die Administration (Lesende können ohnehin nichts senden)
    const testKnopf = mo => istAdmin()
      ? `<button class="leise" onclick="sprachmodelle.teste('${esc(mo.name)}')" ${S.tests[mo.name]?.laeuft ? "disabled" : ""}>Verbindung testen</button>`
      : `<button class="leise" disabled title="Die Verbindung testen kann nur die Administration.">Verbindung testen</button>`;
    app.innerHTML = `<div class="sm">${kopf}
      ${d.quelle === "umgebung" ? `<div class="sm-warn" style="margin:0 0 14px">Die Administration hat noch keine eigene Liste von Sprachmodellen angelegt. Deshalb gilt das eine Sprachmodell aus der Grundeinrichtung.</div>` : ""}
      <div class="karte"><h2>Wofür der Agent ein Sprachmodell nutzt</h2>
        <ul class="sm-liste" style="margin-top:0">
          <li>Links auf der Website des Herstellers auswählen ${genutzt.websuche ? pille("eingeschaltet", "s-geprueft") : pille("ausgeschaltet", "s-kein_ex")}</li>
          <li>Anrede und Einleitung von E-Mail-Entwürfen schreiben ${genutzt.email ? pille("eingeschaltet", "s-geprueft") : pille("ausgeschaltet", "s-kein_ex")}</li>
        </ul>
        ${d.hinweise.map(h => `<p class="sm-klein">${esc(h)}</p>`).join("")}
        ${d.hinweise.length ? `<p class="sm-klein">Einschalten kann es die Administration.</p>` : ""}
        <p class="sm-klein">Ein Sprachmodell liest und schreibt nur Texte. Es entscheidet nichts und gibt nichts frei.</p></div>
      <div class="karte"><h2>Eingerichtete Sprachmodelle (${d.modelle.length})</h2>
        <div class="tabelle"><table><thead><tr><th>Name</th><th>Art</th><th>Anbieter</th><th>Modell</th><th>Schlüssel</th><th>Standard für</th><th></th></tr></thead><tbody>
        ${d.modelle.map(mo => `<tr>
          <td><strong>${esc(mo.name)}</strong></td>
          <td>${esc(ART[mo.art] || mo.art)}</td>
          <td>${esc(mo.anbieter)}${mo.api_base ? `<div class="sm-klein" style="margin:0">${esc(host(mo.api_base))}</div>` : ""}</td>
          <td class="mono">${esc(mo.modell)}</td>
          <td>${zugang(mo)}</td>
          <td>${mo.standard_fuer.length ? mo.standard_fuer.map(a => esc(aufgabe(a))).join(", ") : `<span class="grau">–</span>`}</td>
          <td style="text-align:right">${testKnopf(mo)}</td></tr>
          ${S.tests[mo.name] ? `<tr class="sm-testzeile"><td colspan="7">${testZelle(mo.name)}</td></tr>` : ""}`).join("")}
        </tbody></table></div>
        <p class="sm-klein">Mit „Verbindung testen“ schickt der Agent eine kurze Frage an den Anbieter. So sieht die Administration, ob das Sprachmodell antwortet und wie lange es dafür braucht.</p></div>
      ${erklaerung}</div>`;
  }

  // ---------- Aktionen ----------
  window.sprachmodelle = {
    waehle(vid, feld, wert, leise) {
      const e = eintrag(vid);
      e.wahl = { ...wahl(e), [feld]: wert };
      if (!leise) return zeichneKarte(vid);
      // beim Tippen nicht neu zeichnen: nur Knöpfe und Dauer anpassen
      const ok = versucheOk(e), d = e.daten;
      const knopf = $(`#sm-speichern-${vid}`), zurueck = $(`#sm-zurueck-${vid}`), h = $(`#sm-dauer-${vid}`);
      if (knopf) knopf.disabled = !(geaendert(e) && ok && darfSchreiben());
      if (zurueck) zurueck.style.display = geaendert(e) ? "" : "none";
      if (h) h.textContent = ok ? dauer(d, wert === "" ? d.internet_versuche_standard : +wert) : `Bitte geben Sie eine ganze Zahl von 0 bis ${d.internet_versuche_max} ein.`;
    },
    frage(vid, an) {
      const e = eintrag(vid);
      if (an && !versucheOk(e)) return toast(`Bitte geben Sie bei den Versuchen eine ganze Zahl von 0 bis ${e.daten.internet_versuche_max} ein.`);
      document.activeElement?.blur?.();
      e.frage = an;
      zeichneKarte(vid);
    },
    verwerfen(vid) { const e = eintrag(vid); e.wahl = null; e.frage = false; zeichneKarte(vid); },
    speichere(vid) {
      const e = eintrag(vid), w = wahl(e), d = e.daten, json = {};
      if ((w.modell || null) !== (d.modell || null)) json.modell = w.modell || null;
      if ((w.versuche === "" ? null : +w.versuche) !== (d.internet_versuche ?? null)) json.internet_versuche = w.versuche === "" ? null : +w.versuche;
      mitName(async von => {
        try {
          e.daten = await api(`/api/vorgaenge/${vid}/einstellungen`, { method: "PUT", json: { ...json, von } });
          e.wahl = null; e.frage = false; e.zeit = Date.now();
          toast("Die Einstellungen für diesen Vorgang sind gespeichert.");
        } catch (f) { e.frage = false; toast("Die Einstellungen wurden nicht gespeichert. " + f.message); }
        zeichneKarte(vid);
      });
    },
    async teste(name) {
      S.tests[name] = { laeuft: true };
      zeichneSeite();
      try { S.tests[name] = await api(`/api/modelle/${encodeURIComponent(name)}/test`, { method: "POST" }); }
      catch (f) { S.tests[name] = { fehlerText: f.message }; }
      if (zustand.seite === "sprachmodelle" && S.liste) zeichneSeite();
    },
    neu() { seite(null, $("#app")); },
  };

  erweitere("seiten", { name: "sprachmodelle", muster: /^#\/sprachmodelle\/?$/, zeige: seite });
  erweitere("nav", { name: "sprachmodelle", href: "#/sprachmodelle", text: "Sprachmodelle", icon: ICON_MODELL });
})();
