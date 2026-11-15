// Abschnitte je Position (Artikelseite) und „Veraltete Zertifikate“ (Vorgangsseite).
// Karten „Position bearbeiten“ und „Ex-Teil oder nicht?“, dazu, was die eingebaute Artikelseite
// sonst nicht zeigt:
// Prüfergebnis je Fund, ganze PDF ohne Markierungen, wo gesucht wurde.
// Die eingebaute Seite spricht /api/artikel/…; Stand, Erledigt-Felder, Ex-Liste und Suchlinks
// kommen hier aus /api/boms/<Vorgang>/… (Vorgang = bom, Artikel = bom_item, siehe docs/api.md).
(function () {
  "use strict";

  // Dokumentarten wie DOC_TYPES im Backend (Texte wie DOKUMENTART in index.html)
  const DOKUMENTART = {
    eu_konformitaetserklaerung: "EU-Konformitätserklärung",
    eu_baumusterpruefbescheinigung: "EU-Baumusterprüfbescheinigung",
    uk_konformitaetserklaerung: "UK-Konformitätserklärung",
    uk_baumusterpruefbescheinigung: "UK-Baumusterprüfbescheinigung",
    bedienungsanleitung: "Bedienungsanleitung",
    datenblatt: "Datenblatt",
    sonstiges: "Sonstiges",
  };
  const PRUEFUNG = { ok: "Vorprüfung ohne Befund", warnung: "Vorprüfung mit Warnung", unklar: "Unklar, bitte genau prüfen" };
  // Stand der Position, wie ihn das Backend führt (die eingebaute Seite zeigt eine Übersetzung)
  const STAND = {
    offen: "Offen", klaerung: "Klärung nötig", vorschlag: "Vorschlag prüfen",
    nicht_gefunden: "Nicht gefunden", angefordert: "Angefordert", eskaliert: "Eskaliert",
    vorhanden: "Vorhanden", kein_ex_teil: "Kein Ex-Teil", erledigt: "Erledigt",
  };
  const MAX_MB = 30;

  const stil = document.createElement("style");
  stil.textContent = `
    .am-klein { font-size: 12.5px; color: var(--grau); }
    .am-abstand { margin-top: 10px; }
    .am-pruefung { border-left: 3px solid var(--linie); padding: 6px 10px; margin-top: 6px; font-size: 13px; }
    .am-pruefung.am-ok { background: var(--gruen-hell); border-color: var(--gruen-marke); color: var(--gruen); }
    .am-pruefung.am-warnung { background: var(--rot-hell); border-color: var(--rot); color: #5c120d; }
    .am-pruefung.am-unklar { background: var(--gelb-hell); border-color: var(--gelb); color: #5a4000; }
    .am-pruefung.am-ohne { background: var(--flaeche); color: var(--grau); }
    .am-fund { padding: 10px 0; border-bottom: 1px solid var(--linie); }
    .am-fund:last-child { border-bottom: none; padding-bottom: 0; }
    .am-fund .am-datei { font-weight: 600; overflow-wrap: anywhere; }
    .am-links { display: flex; gap: 6px 16px; flex-wrap: wrap; margin-top: 6px; font-size: 13px; }
    .am-select { font: inherit; padding: 7px 9px; border: 1px solid #ced4da; border-radius: 0; background: #fff; max-width: 100%; }
    .am-select:focus { outline: none; border-color: var(--gruen-marke); box-shadow: 0 0 0 3px rgba(187, 208, 61, .35); }
    .am-formular { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 0 14px; }
    .am-notiz { min-height: 70px; }
    .am-kasten { background: var(--flaeche); border-left: 3px solid var(--linie); padding: 8px 12px; }
    .am-kasten.am-gruen { background: var(--gruen-hell); border-color: var(--gruen-marke); }
    .am-kasten.am-gelb { background: var(--gelb-hell); border-color: var(--gelb); }
    .am-liste { margin: 2px 0 0; padding-left: 18px; }
  `;
  document.head.appendChild(stil);

  // UTC ohne Zone vom Backend → Ortszeit
  function zeit(iso) {
    if (!iso) return "";
    const d = new Date(/Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    return isNaN(d) ? iso : `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
  }

  // ---------- daten je vorgang: positionen (roh), ex-liste, suchlinks ----------
  const lager = {};    // vorgang → { items: {id: Position}, ex: {id: Zeile}, links: {id: [...]}, fehler: {}, laeuft }
  const entwurf = {};  // artikel → { notiz, art, quelle, datei, hinweis }: Eingaben überleben das Neuzeichnen
  let sichtbar = [];   // gezeichnete Abschnitte: { el, neu }

  const merke = aid => (entwurf[aid] = entwurf[aid] || { notiz: "", art: "", quelle: "manuell", datei: null, hinweis: "" });

  function lade(bom) {
    const l = (lager[bom] = lager[bom] || { fehler: {} });
    if (l.laeuft) return l.laeuft;
    const hole = (schl, pfad, form) => api(pfad).then(
      d => { l[schl] = form(d); delete l.fehler[schl]; },
      e => { l.fehler[schl] = e.message; });
    l.laeuft = Promise.all([
      hole("items", `/api/boms/${bom}`, d => Object.fromEntries(d.items.map(i => [i.id, i]))),
      hole("ex", `/api/boms/${bom}/ex-liste`, d => Object.fromEntries(d.items.map(z => [z.item_id, z]))),
      hole("links", `/api/boms/${bom}/links`, d => d),
    ]).then(() => {
      l.laeuft = null; l.geladen = true;
      sichtbar = sichtbar.filter(s => s.el.isConnected);
      sichtbar.forEach(s => s.neu());
    });
    return l.laeuft;
  }
  // nach einer eigenen Aktion: frische Daten, bevor die Seite neu zeichnet
  async function frisch(bom) {
    if (lager[bom]?.laeuft) await lager[bom].laeuft;
    await lade(bom);
  }

  const tippt = el => el.contains(document.activeElement)
    && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);

  function setze(el, html) {
    const teil = el.closest("details.teil") || el;  // ohne Inhalt verschwindet der ganze Teil
    if (html === null) { teil.hidden = true; el._html = null; return; }
    teil.hidden = false;
    if (el._html === html || tippt(el)) return;
    el._html = html;
    el.innerHTML = html;
    // gewählte Datei wieder einsetzen (ein Dateifeld lässt sich nicht per HTML vorbelegen)
    const feld = el.querySelector("input[type=file][data-artikel]");
    const datei = feld && entwurf[feld.dataset.artikel]?.datei;
    if (datei) try { const dt = new DataTransfer(); dt.items.add(datei); feld.files = dt.files; dateiwahlZeigen(feld); } catch {}
  }

  // marke(a, l): Text für die Marke auf dem geschlossenen Kopf des Teils, wenn etwas offen ist
  function abschnitt(titel, male, schluessel, marke) {
    erweitere("artikel", {
      titel, schluessel,
      zeichne(a, el) {
        const bom = zustand.vid;
        const neu = () => {
          const l = lager[bom] || { fehler: {} };
          setze(el, male(a, l, bom));
          if (marke) teilMarke(el, marke(a, l));
        };
        // el hängt hier noch nicht im Dokument (index.html hängt den Rahmen danach ein):
        // verwaiste Abschnitte räumt lade() auf, wenn die Daten da sind
        sichtbar.push({ el, neu });
        // wer ein Feld verlässt, sieht den Stand, der inzwischen geladen wurde
        el.addEventListener("focusout", () => setTimeout(() => el.isConnected && neu(), 0));
        neu();
        lade(bom);
      },
    });
  }

  const laedt = (l, schl) => l.fehler[schl]
    ? `<span class="am-klein">Das ließ sich gerade nicht laden. Bitte laden Sie die Seite gleich noch einmal.</span>`
    : `<span class="am-klein">lädt …</span>`;

  // Prüfergebnis, Links auf die PDF und „Gesucht in“ stehen seit Runde 3 oben im Fund selbst
  // (index.html, fundKarte), damit man die Vorprüfung vor dem Freigeben sieht.

  // ---------- 2. suchlinks für den eigenen browser ----------
  abschnitt("Suchlinks im Internet", (a, l) => {
    const kopf = "";  // Überschrift im Kopf des Teils
    if (!l.links) return kopf + laedt(l, "links");
    const eigene = l.links[String(a.id)] || [];
    if (!eigene.length) return `${kopf}<span class="am-klein">Ohne Hersteller-Artikelnummer gibt es keine Suchlinks.</span>`;
    return `${kopf}
      <div class="zeile">${eigene.map(x => /^https?:\/\//i.test(x.url)
        ? `<a class="knopf leise" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.label)} ↗</a>` : "").join("")}</div>
      <div class="am-klein am-abstand">Die Links öffnen sich in Ihrem Browser. Ein gefundenes PDF laden Sie herunter und fügen es unter „Zertifikat selbst einfügen“ ein.</div>`;
  }, "suchlinks");

  // ---------- 3. zertifikat mit dokumentart einfügen (M18) ----------
  abschnitt("Zertifikat selbst einfügen", (a, l, bom) => {
    const kopf = "";  // Überschrift im Kopf des Teils
    if (!darfSchreiben()) return null;  // nur zum Einfügen da: für Leser ausgeblendet
    if (a.status === "kein_ex") return `${kopf}<span class="am-klein">Die Position ist kein Ex-Teil und braucht deshalb kein Zertifikat.</span>`;
    const e = merke(a.id);
    const manuell = e.quelle !== "hersteller";
    const arten = Object.entries(DOKUMENTART).map(([k, t]) => `<option value="${k}" ${e.art === k ? "selected" : ""}>${esc(t)}</option>`).join("");
    return `${kopf}
      <div class="am-formular">
        <div><label for="am-quelle-${a.id}">Woher stammt das PDF?</label>
          <select class="am-select" id="am-quelle-${a.id}" onchange="artikelModul.quelle(${a.id}, this.value)">
            <option value="manuell" ${manuell ? "selected" : ""}>Selbst gefunden</option>
            <option value="hersteller" ${manuell ? "" : "selected"}>Antwort des Herstellers</option>
          </select></div>
        <div><label for="am-art-${a.id}">Dokumentart${manuell ? ", bitte immer angeben" : ""}</label>
          <select class="am-select" id="am-art-${a.id}" onchange="artikelModul.art(${a.id}, this.value)">
            <option value="" ${e.art ? "" : "selected"}>${manuell ? "bitte wählen …" : "automatisch erkennen"}</option>${arten}
          </select></div>
        <div><label for="am-datei-${a.id}">PDF, höchstens ${MAX_MB} MB</label>
          ${dateiwahl(`id="am-datei-${a.id}" data-artikel="${a.id}" accept=".pdf,application/pdf" onchange="artikelModul.datei(${a.id}, this)"`, { name: e.datei?.name })}</div>
      </div>
      <div class="unbelegt am-abstand" id="am-dateihinweis-${a.id}" ${e.hinweis ? "" : "hidden"}>${esc(e.hinweis)}</div>
      <div class="am-klein am-abstand">${manuell
        ? "Mit der Dokumentart gilt das Dokument sofort als freigegeben und steht in den Stammdaten. Die Vorprüfung läuft trotzdem mit."
        : "Das PDF wird vorgeprüft und wartet dann oben unter „Funde“ auf Ihre Freigabe."}</div>
      <div class="zeile am-abstand"><button onclick="artikelModul.einfuegen(${bom}, ${a.id})" ${sperre()}>${manuell ? "Zertifikat einfügen und freigeben" : "Antwort als Vorschlag einfügen"}</button>${nameHinweis()}</div>`;
  }, "einfuegen");

  // ---------- 4. ex-teil oder nicht ----------
  abschnitt("Ex-Teil oder nicht?", (a, l, bom) => {
    const kopf = "";  // Überschrift im Kopf des Teils
    if (!l.ex) return kopf + laedt(l, "ex");
    const z = l.ex[a.id];
    if (!z) return `${kopf}<span class="am-klein">Diese Position steht nicht in der EX-Bauteil-Liste.</span>`;
    const vorschlag = z.vorschlag === "ex_teil" ? "Ex-Teil" : "Bitte entscheiden Sie";
    const fakten = [z.zone, z.kategorie ? `geforderte Kategorie ${z.kategorie}${z.kategorie_quelle ? ` (${z.kategorie_quelle})` : ""}` : null].filter(Boolean);
    const knopf = (text, wert, klasse) => `<button class="${klasse}" onclick="artikelModul.exTeil(${bom}, ${a.id}, ${wert})"
      ${darfSchreiben() ? (z.entscheidung === { true: "ex_teil", false: "kein_ex_teil", null: null }[wert] ? "disabled" : "") : sperre()}>${text}</button>`;
    return `${kopf}
      <div class="am-kasten ${z.vorschlag === "ex_teil" ? "am-gruen" : "am-gelb"}">
        <div><strong>Vorschlag der Regeln: ${esc(vorschlag)}.</strong> ${esc(z.regel_text)}</div>
        <div>${esc(z.grund)}</div>
        <div class="am-klein">${esc(z.quelle)}${fakten.length ? ` · ${esc(fakten.join(" · "))}` : ""}</div>
        ${z.hinweis ? `<div class="am-klein">${esc(z.hinweis)}</div>` : ""}
      </div>
      <div class="am-abstand">${z.entscheidung
        ? `<strong>Entschieden ist: ${z.entscheidung === "ex_teil" ? "Die Position ist ein Ex-Teil" : "Die Position ist kein Ex-Teil"}.</strong> <span class="grau">${esc(z.entschieden)}</span>`
        : `<span class="grau">Noch hat niemand entschieden. Bis dahin gilt der Vorschlag der Regeln: ${esc(z.einordnung_text)}.</span>`}</div>
      <div class="zeile am-abstand">
        ${knopf("Als Ex-Teil bestätigen", true, "")}${knopf("Als kein Ex-Teil markieren", false, "leise")}${knopf("Entscheidung zurücknehmen", null, "leise")}${nameHinweis()}</div>
      <div class="am-klein am-abstand">Ein Ex-Teil wird gesucht, auch wenn der Einbauort keine Zone nennt. Kein Ex-Teil wird nicht gesucht und steht nicht in der Kundenliste.</div>`;
  }, "ex-teil", (a, l) => {
    const z = l.ex?.[a.id];
    return z && !z.entscheidung && z.einordnung === "klaerung" ? "Entscheidung offen" : "";
  });

  // ---------- 5. von hand erledigen ----------
  abschnitt("Von Hand erledigen", (a, l, bom) => {
    const kopf = "";  // Überschrift im Kopf des Teils
    if (!l.items) return kopf + laedt(l, "items");
    const i = l.items[a.id];
    if (!i) return `${kopf}<span class="am-klein">Diese Position gibt es im Vorgang nicht mehr. Bitte laden Sie die Seite neu.</span>`;
    if (i.resolved_at) {
      return `${kopf}
        <div class="am-kasten am-gruen">
          <div><strong>${esc(i.resolved_by)}</strong> hat die Position am ${esc(zeit(i.resolved_at))} von Hand erledigt.</div>
          <div>${i.resolution_note ? `Die Begründung lautet: ${esc(i.resolution_note)}` : "Eine Begründung ist nicht angegeben."}</div>
        </div>
        <div class="zeile am-abstand"><button class="leise" onclick="artikelModul.zuruecknehmen(${bom}, ${a.id})" ${sperre()}>Erledigung zurücknehmen</button>${nameHinweis()}
          <span class="am-klein">Danach richtet sich der Stand der Position wieder nach ihren Dokumenten.</span></div>`;
    }
    if (!darfSchreiben()) return null;
    if (i.status === "vorhanden" || i.status === "kein_ex_teil") {
      return `${kopf}<span class="am-klein">Das ist nicht nötig, denn die Position ist ${i.status === "vorhanden" ? "vollständig" : "kein Ex-Teil"}.</span>`;
    }
    const e = merke(a.id);
    return `${kopf}
      <div class="am-klein">Die Position steht auf „${esc(STAND[i.status] || i.status)}“.${i.status_note ? ` ${esc(i.status_note)}` : ""}</div>
      <div class="am-klein">Als erledigt hält die Position die Kundenliste nicht mehr auf, auch wenn Dokumente fehlen. In Kundenliste und Fehlliste steht sie weiter, mit Ihrem Namen und Ihrer Begründung.</div>
      <label for="am-notiz-${a.id}">Begründung, bitte immer angeben</label>
      <textarea class="am-notiz" id="am-notiz-${a.id}" maxlength="2000" oninput="artikelModul.notiz(${a.id}, this.value)"
        placeholder="Warum ist die Position auch ohne die fehlenden Dokumente erledigt?">${esc(e.notiz)}</textarea>
      <div class="zeile am-abstand"><button onclick="artikelModul.erledigen(${bom}, ${a.id})" ${sperre()}>Position als erledigt markieren</button>${nameHinweis()}</div>`;
  }, "erledigen", (a, l) => l.items?.[a.id]?.resolved_at ? "von Hand erledigt" : "");

  // ---------- 6. vorgang: veraltete zertifikate ----------
  const veraltet = {};  // vorgang → { stand: "ok" | "aus" | "fehler", items, text, zeit, laeuft, sucht }
  const VERALTET_ALTER = 30000;  // der Abruf fragt das Dokumentenarchiv; nicht bei jedem Neuzeichnen

  function veraltetHtml(v, d) {
    const kopf = "";  // die Überschrift steht im Kopf des Teils (index.html, abschnitte)
    const neu = `<a href="javascript:void 0" onclick="artikelModul.veraltetLaden(${v.id})">Jetzt neu prüfen</a>`;
    if (!d.stand) return `${kopf}<span class="am-klein">lädt …</span>`;
    if (d.stand === "aus") return `${kopf}<span class="am-klein">Das Dokumentenarchiv ist gerade nicht erreichbar. Deshalb lässt sich hier nicht prüfen, ob Zertifikate veraltet sind. ${neu}</span>`;
    if (d.stand === "fehler") return `${kopf}<span class="am-klein">Das ließ sich gerade nicht prüfen. Bitte versuchen Sie es gleich noch einmal. ${neu}</span>`;
    if (d.sucht) return `${kopf}<span class="chip s-suche"><span class="punkt"></span>Die betroffenen Positionen werden neu gesucht …</span>
      <span class="am-klein">Das kann einige Minuten dauern.</span>`;
    if (!d.items.length) return `${kopf}<span class="am-klein">Keine Position dieses Vorgangs stützt sich auf ein Dokument, das im Dokumentenarchiv als veraltet bestätigt ist. ${neu}</span>`;
    return `${kopf}
      <div class="am-klein" style="margin-top:-6px">${d.items.length === 1 ? "Eine Position stützt" : `${d.items.length} Positionen stützen`} sich noch auf ein Dokument, das im Dokumentenarchiv als veraltet bestätigt ist. ${neu}</div>
      <div class="tabelle am-abstand" title="Diese Liste ergibt sich aus dem Dokumentenarchiv und lässt sich hier nicht ändern."><table><thead><tr><th>Pos.</th><th>Bezeichnung</th><th>Veraltetes Dokument</th><th>Gründe</th></tr></thead><tbody>
      ${d.items.map(i => `<tr class="klick" onclick="location.hash='#/v/${v.id}/a/${i.id}'" title="Position öffnen">
        <td>${esc(i.pos)}</td>
        <td>${esc(i.description)}<br><span class="grau">${esc(i.manufacturer)} · <span class="mono">${esc(i.manufacturer_article_no)}</span></span></td>
        <td>${i.veraltet.map(x => `<div class="mono" style="overflow-wrap:anywhere">${String(x.pdf_url || "").startsWith("/api/")
          ? `<a href="${API}/zert${esc(x.pdf_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(x.dateiname)}</a>` : esc(x.dateiname)}</div>`).join("")}</td>
        <td>${i.veraltet.map(x => x.gruende.length ? `<ul class="am-liste">${x.gruende.map(g => `<li>${esc(g)}</li>`).join("")}</ul>` : `<span class="grau">Kein Grund angegeben.</span>`).join("")}</td>
      </tr>`).join("")}
      </tbody></table></div>
      <div class="zeile am-abstand"><button onclick="artikelModul.veraltetSuchen(${v.id})" ${sperre()}>Betroffene Positionen neu suchen</button>${nameHinweis()}</div>
      <div class="am-klein am-abstand">Die veralteten Dokumente gelten dann in den Stammdaten wieder als ungeprüft, und nur diese Positionen werden neu gesucht. Findet die Suche nichts Neues, liegt gleich ein E-Mail-Entwurf an den Hersteller bereit.</div>`;
  }

  // 503 heißt „kein Dokumentenarchiv“: leiser Hinweis, deshalb nicht über api()
  async function veraltetLaden(vid) {
    const d = (veraltet[vid] = veraltet[vid] || {});
    if (d.laeuft) return d.laeuft;
    d.laeuft = (async () => {
      try {
        const r = await fetch(`${API}/api/boms/${vid}/veraltet`);
        const j = await r.json().catch(() => ({}));
        if (r.ok) { d.stand = "ok"; d.items = j.items || []; }
        else if (r.status === 503 || r.status === 502) { d.stand = "aus"; d.items = []; }
        else { d.stand = "fehler"; d.text = j.error || j.fehler || r.statusText; }
      } catch (e) { d.stand = "fehler"; d.text = e.message; }
      d.zeit = Date.now(); d.laeuft = null;
      veraltetNeu(vid);
    })();
    return d.laeuft;
  }
  function veraltetNeu(vid) {
    const el = veraltet[vid]?.el;
    if (el?.isConnected && zustand.v?.id === vid) { setze(el, veraltetHtml(zustand.v, veraltet[vid])); veraltetMarke(el, veraltet[vid]); }
  }
  // Marke auf dem geschlossenen Kopf, wenn Positionen betroffen sind
  function veraltetMarke(el, d) {
    const n = d.stand === "ok" ? (d.items || []).length : 0;
    teilMarke(el, n ? `${n === 1 ? "1 Position betroffen" : `${n} Positionen betroffen`}` : "", "rot");
    teilKurz(el, d.stand === "ok" && !n ? "Nichts veraltet" : d.stand === "aus" ? "Das Dokumentenarchiv ist gerade nicht erreichbar." : "");
  }

  erweitere("vorgang", {
    titel: "Veraltete Zertifikate", schluessel: "veraltet",
    zeichne(v, el) {
      const d = (veraltet[v.id] = veraltet[v.id] || {});
      d.el = el;
      setze(el, veraltetHtml(v, d));
      veraltetMarke(el, d);
      if (!d.sucht && (!d.zeit || Date.now() - d.zeit > VERALTET_ALTER)) veraltetLaden(v.id);
    },
  });

  // ---------- aktionen (aus onclick) ----------
  window.artikelModul = {
    notiz(aid, wert) { merke(aid).notiz = wert; },
    art(aid, wert) { merke(aid).art = wert; },
    quelle(aid, wert) {
      merke(aid).quelle = wert;
      document.activeElement.blur();
      sichtbar.filter(s => s.el.isConnected).forEach(s => s.neu());
    },
    datei(aid, feld) {
      const e = merke(aid), f = feld.files[0] || null;
      e.datei = null; e.hinweis = "";
      if (f && !/\.pdf$/i.test(f.name)) { e.hinweis = `„${f.name}“ ist keine PDF-Datei. Bitte wählen Sie ein PDF aus.`; feld.value = ""; }
      else if (f && f.size > MAX_MB * 1024 * 1024) { e.hinweis = `„${f.name}“ ist größer als ${MAX_MB} MB. Bitte wählen Sie eine kleinere Datei.`; feld.value = ""; }
      else e.datei = f;
      const h = $(`#am-dateihinweis-${aid}`);
      if (h) { h.textContent = e.hinweis; h.hidden = !e.hinweis; }
    },

    einfuegen(bom, aid) {
      const e = merke(aid);
      if (!e.datei) return toast(e.hinweis || "Bitte wählen Sie zuerst ein PDF aus.");
      if (e.quelle !== "hersteller" && !e.art) return toast("Bitte wählen Sie die Dokumentart aus.");
      mitName(von => tun(async () => {
        const fd = new FormData();
        fd.append("file", e.datei); fd.append("by", von); fd.append("source", e.quelle);
        if (e.art) fd.append("doc_type", e.art);
        const f = await api(`/api/boms/${bom}/items/${aid}/documents`, { method: "POST", body: fd });
        const text = `${DOKUMENTART[f.doc_type] || f.doc_type || "Dokument"} ${e.quelle === "hersteller" ? "ist eingefügt und wartet auf Ihre Freigabe" : "ist eingefügt und freigegeben"}.`
          + ` ${PRUEFUNG[f.check_result] || "Nicht vorgeprüft"}. Die Position steht jetzt auf „${STAND[f.item_status] || f.item_status}“.`;
        delete entwurf[aid];
        await frisch(bom);
        toast(text);
      }));
    },

    exTeil(bom, aid, wert) {
      const text = wert === true ? "Die Position gilt jetzt als Ex-Teil." : wert === false ? "Die Position gilt jetzt als kein Ex-Teil." : "Ihre Entscheidung ist zurückgenommen.";
      mitName(von => tun(async () => {
        await api(`/api/boms/${bom}/items/${aid}/ex-teil`, { method: "POST", json: { ex_teil: wert, by: von } });
        await frisch(bom);
      }, text));
    },

    erledigen(bom, aid) {
      const note = merke(aid).notiz.trim();
      if (!note) { $(`#am-notiz-${aid}`)?.focus(); return toast("Bitte schreiben Sie eine Begründung."); }
      document.activeElement.blur();
      mitName(von => tun(async () => {
        await api(`/api/boms/${bom}/items/${aid}/resolve`, { method: "POST", json: { by: von, note } });
        merke(aid).notiz = "";
        await frisch(bom);
      }, "Die Position ist als erledigt markiert."));
    },

    zuruecknehmen(bom, aid) {
      mitName(von => tun(async () => {
        await api(`/api/boms/${bom}/items/${aid}/reopen`, { method: "POST", json: { by: von } });
        await frisch(bom);
      }, "Die Erledigung ist zurückgenommen."));
    },

    veraltetLaden(vid) {
      const d = veraltet[vid];
      if (d && !d.laeuft) { d.stand = null; veraltetNeu(vid); }
      return veraltetLaden(vid);
    },

    veraltetSuchen(vid) {
      mitName(async von => {
        const d = veraltet[vid];
        d.sucht = true; veraltetNeu(vid);
        let text;
        try {
          const r = await api(`/api/boms/${vid}/veraltet/suche`, { method: "POST", json: { by: von } });
          text = r.items.length === 1 ? "Eine Position ist neu gesucht." : `${r.items.length} Positionen sind neu gesucht.`;
        } catch (e) { text = `Die neue Suche hat nicht geklappt. ${e.message}`; }
        d.sucht = false; d.zeit = 0;
        toast(text);
        await veraltetLaden(vid);
        if (zustand.seite === "vorgang" && zustand.vid === vid) await aktualisieren(vid, true);
      });
    },
  };
})();
