// Benutzerverwaltung (C1), nur für Admins: Konten ansehen, anlegen, Rolle ändern, deaktivieren
// und wieder aktivieren, Passwort zurücksetzen. Backend: /api/benutzer (docs/api.md). Konten
// werden nie gelöscht: ihre Namen stehen im Protokoll. Den letzten aktiven Admin schützt der Server.
// Pfad: #/benutzer; der Eintrag in der Leiste erscheint nur für Admins (sichtbar, unten).
(function () {
  const ROLLEN = { admin: "Administration", pruefer: "Prüfung & Freigabe", leser: "Nur lesen" };
  const ROLLEN_TEXT = {
    admin: "darf alles und vergibt die Zugänge.",
    pruefer: "erledigt die ganze Arbeit, von der Stückliste bis zur Kundenliste.",
    leser: "kann alles ansehen, aber nichts ändern.",
  };
  const z = { liste: null, offen: null, fehler: "" };  // offen: id des kontos mit offenem passwort-feld

  const stil = document.createElement("style");
  stil.textContent = `
    .bv-gitter { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0 12px; align-items: end; }
    .bv-gitter select { width: 100%; }
    .bv-rechts { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; margin-top: 12px; }
    .bv-aktion { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
    .bv-aktion button { padding: 3px 10px; font-size: 12.5px; white-space: nowrap; }
    .bv-tabelle select { padding: 4px 6px; font-size: 13px; }
    .bv-aus td { color: var(--grau); }
    .bv-pw { display: flex; gap: 6px; align-items: center; justify-content: flex-end; margin-top: 8px; }
    .bv-pw input { max-width: 220px; padding: 4px 8px; font-size: 13px; }
    .bv-rollen { font-size: 12.5px; color: var(--grau); margin: 10px 0 0; padding-left: 18px; }
    @media (max-width: 900px) { .bv-gitter { grid-template-columns: 1fr 1fr; } }`;
  document.head.appendChild(stil);

  // gespeichert in UTC; ohne zonenangabe (sqlite, postgres ohne zone) als UTC lesen
  const zeit = iso => {
    if (!iso) return "noch nie";
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
    const tag = d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
    return `${tag}, ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
  };

  async function laden() {
    try { z.liste = await api("/api/benutzer"); z.fehler = ""; }
    catch (e) { z.fehler = e.message; }
    zeichnen();
  }

  function zeile(b) {
    const selbst = b.id === angemeldet()?.id;
    const stand = !b.aktiv ? `<span class="chip s-kein_ex">deaktiviert</span>`
      : b.gesperrt ? `<span class="chip s-info_fehlt" title="Nach zu vielen falschen Versuchen gesperrt. Ein neues Passwort hebt die Sperre auf.">gesperrt</span>`
      : `<span class="chip s-geprueft">aktiv</span>`;
    return `<tr class="${b.aktiv ? "" : "bv-aus"}">
      <td><strong>${esc(b.name)}</strong>${selbst ? ' <span class="grau">(Sie)</span>' : ""}</td>
      <td class="mono">${esc(b.login)}</td>
      <td><select onchange="benutzerVerwaltung.rolle(${b.id}, this)" aria-label="Rolle von ${esc(b.name)}">${Object.entries(ROLLEN).map(([k, t]) =>
        `<option value="${k}" ${k === b.rolle ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></td>
      <td>${stand}</td>
      <td class="grau">${zeit(b.letzte_anmeldung)}</td>
      <td><div class="bv-aktion">
        <button class="leise" onclick="benutzerVerwaltung.passwortFeld(${b.id})">Passwort neu setzen</button>
        ${b.aktiv ? `<button class="gefahr" onclick="benutzerVerwaltung.aktiv(${b.id}, false)">Zugang deaktivieren</button>`
          : `<button class="leise" onclick="benutzerVerwaltung.aktiv(${b.id}, true)">Zugang wieder aktivieren</button>`}</div>
        ${z.offen === b.id ? `<div class="bv-pw">
          <input type="password" id="bv-pw-${b.id}" placeholder="Neues Passwort, mindestens 8 Zeichen" autocomplete="new-password"
            onkeydown="if (event.key === 'Enter') benutzerVerwaltung.passwort(${b.id})">
          <button onclick="benutzerVerwaltung.passwort(${b.id})">Passwort speichern</button>
          <button class="icon" title="Abbrechen" onclick="benutzerVerwaltung.passwortFeld(null)">✕</button></div>` : ""}</td>
    </tr>`;
  }

  function zeichnen() {
    if (zustand.seite !== "benutzer") return;
    if (!istAdmin()) { $("#app").innerHTML = `<div class="karte unbelegt">Diese Seite ist nur für die Administration. Zugänge und Passwörter ändert die Administration für Sie.</div>`; return; }
    const liste = z.liste;
    $("#app").innerHTML = `
      <div class="kopfzeile"><div><h1>Benutzer</h1>
        <div class="grau">Hier vergeben Sie die Zugänge zum ATEX-Agenten. Ein Zugang wird nie gelöscht, nur deaktiviert, damit sein Name im Protokoll erhalten bleibt.</div></div></div>
      ${z.fehler ? `<div class="hinweis rot">${esc(z.fehler)}</div>` : ""}
      <div class="karte">
        <h2>Alle Zugänge${liste ? ` (${liste.length})` : ""}</h2>
        ${!liste ? `<div class="leer">lädt …</div>` : `<div class="tabelle"><table class="bv-tabelle">
          <thead><tr><th>Name</th><th>Anmeldename</th><th>Rolle</th><th>Stand</th><th>Zuletzt angemeldet</th><th></th></tr></thead>
          <tbody>${liste.map(zeile).join("")}</tbody></table></div>`}
      </div>
      <details class="karte" data-merk="bv-neu" ${offenAttr("bv-neu")}>
        <summary style="cursor:pointer"><h2 style="display:inline;margin:0">Neuen Zugang anlegen</h2></summary>
        <div style="height:10px"></div>
        <div class="bv-gitter">
          <div><label for="bv-name">Name (steht im Protokoll)</label><input type="text" id="bv-name" placeholder="Vor- und Nachname"></div>
          <div><label for="bv-login">Anmeldename</label><input type="text" id="bv-login" placeholder="z. B. m.mustermann" autocapitalize="none" spellcheck="false"></div>
          <div><label for="bv-rolle">Rolle</label><select id="bv-rolle">${Object.entries(ROLLEN).map(([k, t]) =>
            `<option value="${k}" ${k === "pruefer" ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
          <div><label for="bv-passwort">Erstes Passwort, mindestens 8 Zeichen</label><input type="password" id="bv-passwort" autocomplete="new-password"></div>
        </div>
        <ul class="bv-rollen">${Object.entries(ROLLEN).map(([k, t]) => `<li><strong>${esc(t)}</strong> ${esc(ROLLEN_TEXT[k])}</li>`).join("")}</ul>
        <div class="bv-rechts"><span class="grau" style="font-size:12px">Teilen Sie der Person das Passwort persönlich mit. Sie kann es danach unter Einstellungen ändern.</span>
          <button onclick="benutzerVerwaltung.anlegen()">Zugang anlegen</button></div>
      </details>`;
    if (z.offen) $(`#bv-pw-${z.offen}`)?.focus();
  }

  async function aendern(id, daten, ok) {
    try {
      const b = await api(`/api/benutzer/${id}`, { method: "PATCH", json: daten });
      toast(ok);
      if (b.id === angemeldet()?.id) {  // eigene rolle geändert: neu laden, wer man ist
        const neu = await api("/api/auth/ich");
        angemeldetWeiter(neu);
        return;
      }
    } catch (e) { toast(e.message); }
    await laden();
  }

  window.benutzerVerwaltung = {
    async anlegen() {
      const daten = { name: $("#bv-name").value.trim(), login: $("#bv-login").value.trim(),
        rolle: $("#bv-rolle").value, passwort: $("#bv-passwort").value };
      if (!daten.name || !daten.login || !daten.passwort) return toast("Bitte geben Sie Name, Anmeldename und Passwort ein.");
      document.activeElement.blur();
      try {
        const b = await api("/api/benutzer", { method: "POST", json: daten });
        toast(`Der Zugang für ${b.name} ist angelegt.`);
        await laden();
      } catch (e) { toast(e.message); }
    },
    rolle(id, el) {
      const b = z.liste.find(x => x.id === id);
      aendern(id, { rolle: el.value }, `${b.name} hat jetzt die Rolle „${ROLLEN[el.value]}“.`);
    },
    aktiv(id, wert) {
      const b = z.liste.find(x => x.id === id);
      aendern(id, { aktiv: wert }, wert ? `${b.name} kann sich wieder anmelden.` : `${b.name} ist deaktiviert und abgemeldet.`);
    },
    passwortFeld(id) { z.offen = id; zeichnen(); },
    passwort(id) {
      const pw = $(`#bv-pw-${id}`).value;
      if (pw.length < 8) return toast("Das Passwort ist zu kurz. Bitte wählen Sie eines mit mindestens 8 Zeichen.");
      const b = z.liste.find(x => x.id === id);
      z.offen = null;
      document.activeElement.blur();
      aendern(id, { passwort: pw }, `Das neue Passwort für ${b.name} ist gespeichert. ${b.name} muss sich damit neu anmelden.`);
    },
  };

  async function zeige() {
    zustand.seite = "benutzer";
    z.offen = null;
    zeichnen();
    if (istAdmin()) await laden();
  }

  erweitere("seiten", { name: "benutzer", muster: /^#\/benutzer\/?$/, zeige });
  erweitere("nav", { name: "benutzer", href: "#/benutzer", text: "Benutzer", icon: ICON.benutzer, unten: true, sichtbar: istAdmin });
})();
