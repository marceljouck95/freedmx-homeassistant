/*
 * freeDMX Raum-Karte für Home Assistant
 * Version 1.2.0
 * NEU 1.2: Pfeiltasten für Pan/Tilt im Kalibrier-Modus
 * NEU 1.1: Kalibrieren – tatsächlichen Lichtpunkt im Plan antippen
 *
 * - Grundriss des Raums (Maße aus der freeDMX-Integration: Konfigurieren → Raum)
 * - Moving Heads an ihrem Montageort, Lichtpunkt live auf dem Boden
 * - Antippen oder Ziehen im Plan richtet den gewählten Kopf aus (Dienst freedmx.aim)
 * - Erlaubter Bereich sichtbar; Ziele außerhalb werden auf den Rand begrenzt
 * - Ein/Aus und Helligkeit des gewählten Kopfs, mehrere Köpfe gemeinsam bewegen
 * - Alle Einstellungen im visuellen Karten-Editor, Größe frei verstellbar
 *
 * Installation: Datei nach /config/www/ kopieren, Ressource
 * /local/freedmx-raum-card.js (JavaScript-Modul) eintragen.
 */

const FR_VERSION = "1.2.0";
const frEd = (sr, k) => { try { if (window.EinheitDesign) window.EinheitDesign.adopt(sr, k); else (window.__edQ = window.__edQ || []).push([sr, k]); } catch (e) { /* Design-Zentrale optional */ } };

const FR_DEFAULTS = {
  title: "DMX Raum",
  entities: [],
  rotate: false,
  show_grid: true,
  show_labels: true,
  show_beams: true,
  show_controls: true,
  move_together: false,
};

const FR_COLORS = ["#3fc1ff", "#ffc83d", "#4cd790", "#ff7ad9", "#b48cff", "#ff8a4c"];

function frEsc(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const fmt = (v) => (Math.round(v * 100) / 100).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* Gleiche Begrenzung wie in der Integration (geometry.clamp_point): Richtung vom Kopf bleibt erhalten. */
function frClamp(room, mount, x, y) {
  const m = Math.max(0, Math.min(room.margin || 0, room.width / 2, room.depth / 2));
  const x0 = m, x1 = room.width - m, y0 = m, y1 = room.depth - m;
  if (x >= x0 && x <= x1 && y >= y0 && y <= y1) return [x, y];
  const ox = Math.max(x0, Math.min(x1, mount.x)), oy = Math.max(y0, Math.min(y1, mount.y));
  const dx = x - ox, dy = y - oy;
  let t = 1;
  if (dx > 1e-9) t = Math.min(t, (x1 - ox) / dx); else if (dx < -1e-9) t = Math.min(t, (x0 - ox) / dx);
  if (dy > 1e-9) t = Math.min(t, (y1 - oy) / dy); else if (dy < -1e-9) t = Math.min(t, (y0 - oy) / dy);
  t = Math.max(0, t);
  return [Math.max(x0, Math.min(x1, ox + dx * t)), Math.max(y0, Math.min(y1, oy + dy * t))];
}

class FreedmxRaumCard extends HTMLElement {
  static getConfigElement() { return document.createElement("freedmx-raum-card-editor"); }
  static getStubConfig() { return { ...FR_DEFAULTS }; }

  setConfig(config) {
    this.__rawCfg = config;
    this._config = { ...FR_DEFAULTS, ...(config || {}) };
    if (!Array.isArray(this._config.entities)) this._config.entities = [];
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      frEd(this.shadowRoot, "freedmx");
      this._build();
    }
    this._sig = null;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, min_columns: 3, rows: 8, min_rows: 3 }; }

  /* ---------- Daten ---------- */
  _heads() {
    const h = this._hass;
    if (!h) return [];
    let ids = this._config.entities.filter((e) => h.states[e]);
    if (!ids.length) ids = Object.keys(h.states).filter((e) => e.startsWith("light.") && h.states[e].attributes?.montage && h.states[e].attributes?.lamp_id);
    return ids.map((id, i) => {
      const s = h.states[id];
      const a = s.attributes || {};
      return {
        id,
        name: a.friendly_name || id,
        on: s.state === "on",
        available: s.state !== "unavailable",
        bri: a.brightness ?? 0,
        lamp: a.lamp_id,
        entry: a.config_entry_id,
        room: a.raum,
        mount: a.montage,
        set: a.montage_gesetzt,
        target: a.ziel,
        color: FR_COLORS[i % FR_COLORS.length],
      };
    }).filter((x) => x.mount && x.room && x.lamp);
  }

  /* ---------- Aufbau (einmal) ---------- */
  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%;
          --primary-text-color:var(--ed-text,#e8eef6);--secondary-text-color:var(--ed-sub,#8c9bb0);--divider-color:var(--ed-line,rgba(255,255,255,.08));
          --ha-card-background:var(--ed-bg,linear-gradient(160deg,#0f1d2e,#0a1420));--ha-card-border-radius:var(--ed-radius,22px);
          --primary-color:var(--ed-cool,#3fc1ff); color:var(--ed-text,#e8eef6); }
        ha-card { height:100%; box-sizing:border-box; padding:14px; display:flex; flex-direction:column; gap:10px; overflow:auto; backdrop-filter:var(--ed-blur,none);-webkit-backdrop-filter:var(--ed-blur,none); }
        .head { display:flex; align-items:center; gap:10px; justify-content:space-between; flex-wrap:wrap; }
        .title { font-size:.78rem; font-weight:600; text-transform:uppercase; letter-spacing:1.4px; color:var(--secondary-text-color); }
        .dim { font-size:.8rem; color:var(--secondary-text-color); }
        .chips { display:flex; gap:6px; flex-wrap:wrap; }
        .chip { border:1px solid var(--ed-line,rgba(255,255,255,.12)); background:var(--ed-tile,rgba(255,255,255,.045)); color:inherit; border-radius:var(--ed-r-pill,999px); padding:6px 12px; font:inherit; font-size:.85rem; cursor:pointer; display:flex; align-items:center; gap:6px; }
        .chip .dot { width:10px; height:10px; border-radius:50%; }
        .chip.sel { outline:2px solid var(--c); background:color-mix(in srgb, var(--c) 18%, transparent); }
        .chip.off { opacity:.55; }
        .plan { flex:1 1 auto; min-height:140px; display:flex; }
        svg { width:100%; height:100%; touch-action:none; user-select:none; cursor:crosshair; }
        .room { fill:color-mix(in srgb, var(--ed-tile,#ffffff) 60%, transparent); stroke:var(--ed-line2,rgba(255,255,255,.35)); stroke-width:.04; }
        .zone { fill:color-mix(in srgb, var(--ed-green,#4cd790) 9%, transparent); stroke:var(--ed-green,#4cd790); stroke-width:.03; stroke-dasharray:.12 .08; }
        .grid line { stroke:var(--ed-line,rgba(255,255,255,.07)); stroke-width:.012; }
        .wall { stroke:var(--ed-sun,#ffc83d); stroke-width:.08; }
        .lbl { font-size:.22px; fill:var(--secondary-text-color); }
        .beam { stroke-width:.04; stroke-linecap:round; opacity:.55; }
        .spot { stroke:#fff; stroke-width:.03; }
        .spot.off { fill-opacity:.15 !important; stroke-dasharray:.06 .05; }
        .headm { stroke:#fff; stroke-width:.03; }
        .ghost { fill:none; stroke-width:.04; stroke-dasharray:.08 .06; }
        .info { display:flex; gap:10px; align-items:center; flex-wrap:wrap; font-size:.85rem; }
        .info b { font-weight:600; }
        .ctl { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
        .btn { border:1px solid var(--ed-line,rgba(255,255,255,.12)); background:var(--ed-tile,rgba(255,255,255,.045)); color:inherit; border-radius:var(--ed-r-tile,12px); padding:8px 12px; font:inherit; font-size:.85rem; cursor:pointer; }
        .btn.on { background:color-mix(in srgb, var(--ed-sun,#ffc83d) 25%, transparent); }
        input[type=range] { flex:1 1 120px; accent-color:var(--primary-color); }
        .warn { font-size:.8rem; color:var(--ed-sun,#ffc83d); }
        .empty { color:var(--secondary-text-color); font-size:.9rem; padding:12px 0; }
      </style>
      <ha-card>
        <div class="head"><div><div class="title"></div><div class="dim"></div></div><div class="chips"></div></div>
        <div class="plan" data-no-swipe><svg></svg></div>
        <div class="info"></div>
        <div class="ctl"></div>
        <div class="warn"></div>
      </ha-card>`;
    const sr = this.shadowRoot;
    this.$ = {
      title: sr.querySelector(".title"), dim: sr.querySelector(".dim"), chips: sr.querySelector(".chips"),
      svg: sr.querySelector("svg"), info: sr.querySelector(".info"), ctl: sr.querySelector(".ctl"), warn: sr.querySelector(".warn"),
    };
    this._sel = new Set();
    this._ghost = null;   // lokales Ziel während des Ziehens {x,y}
    this._drag = false;
    this._lastSend = 0;

    const svg = this.$.svg;
    const pt = (ev) => {
      const p = svg.createSVGPoint();
      p.x = ev.clientX; p.y = ev.clientY;
      const m = svg.getScreenCTM();
      if (!m) return null;
      const q = p.matrixTransform(m.inverse());
      return this._fromView(q.x, q.y);
    };
    svg.addEventListener("pointerdown", (ev) => {
      const p = pt(ev); if (!p) return;
      this._drag = true; svg.setPointerCapture(ev.pointerId);
      if (this._cal) { this._ghost = p; this._render(true); return; }
      this._aim(p, true);
    });
    svg.addEventListener("pointermove", (ev) => {
      if (!this._drag) return; const p = pt(ev); if (!p) return;
      if (this._cal) { this._ghost = p; this._render(true); return; }
      this._aim(p, false);
    });
    const end = (ev) => {
      if (!this._drag) return; this._drag = false; const p = pt(ev);
      if (this._cal) { if (p) this._calibrate(p); return; }
      if (p) this._aim(p, true);
      setTimeout(() => { this._ghost = null; this._render(true); }, 900);
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", () => { this._drag = false; });

    this.$.chips.addEventListener("click", (ev) => {
      const b = ev.target.closest(".chip"); if (!b) return;
      const id = b.dataset.id;
      if (this._config.move_together) { this._sel.has(id) ? this._sel.delete(id) : this._sel.add(id); if (!this._sel.size) this._sel.add(id); }
      else this._sel = new Set([id]);
      this._render(true);
    });
    this.$.ctl.addEventListener("click", (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      const ids = [...this._sel];
      if (b.dataset.act === "toggle") {
        const anyOn = this._heads().some((x) => this._sel.has(x.id) && x.on);
        this._hass.callService("light", anyOn ? "turn_off" : "turn_on", { entity_id: ids });
      } else if (b.dataset.act === "jog") {
        const h = this._heads().find((x) => this._sel.has(x.id));
        if (!h || !h.target) return;
        const step = Number(this._step || 2) * (b.dataset.d.endsWith("+") ? 1 : -1);
        let pan = Number(h.target.pan), tilt = Number(h.target.tilt);
        if (b.dataset.d.startsWith("pan")) pan += step; else tilt += step;
        pan = Math.max(0, Math.min(100, pan)); tilt = Math.max(0, Math.min(100, tilt));
        const data = { lamp_id: h.lamp, pan: Math.round(pan * 100) / 100, tilt: Math.round(tilt * 100) / 100, ohne_begrenzung: true };
        if (h.entry) data.config_entry_id = h.entry;
        this._hass.callService("freedmx", "move", data).catch((e) => { this._note = `Fehler: ${e.message || e}`; this._render(true); });
      } else if (b.dataset.act === "step") {
        this._step = Number(b.dataset.v); this._ctlBuilt = false; this._render(true);
      } else if (b.dataset.act === "cal") {
        this._cal = !this._cal; this._ghost = null; this._ctlBuilt = false; this._render(true);
      } else if (b.dataset.act === "center") {
        const r = this._heads()[0]?.room; if (r) this._aim({ x: r.width / 2, y: r.depth / 2 }, true);
      }
    });
    this.$.ctl.addEventListener("change", (ev) => {
      if (ev.target.type !== "range") return;
      this._hass.callService("light", "turn_on", { entity_id: [...this._sel], brightness_pct: Number(ev.target.value) });
    });
  }

  /* ---------- Plan-Koordinaten ---------- */
  _room() { return this._heads()[0]?.room || { width: 3.5, depth: 10, margin: 0.3, limit: true }; }
  _toView(x, y) { const r = this._room(); return this._config.rotate ? [y, r.width - x] : [x, y]; }
  _fromView(u, v) { const r = this._room(); return this._config.rotate ? { x: r.width - v, y: u } : { x: u, y: v }; }

  _aim(p, force) {
    const heads = this._heads().filter((x) => this._sel.has(x.id));
    if (!heads.length) return;
    this._ghost = p;
    this._render(true);
    const now = Date.now();
    if (!force && now - this._lastSend < 120) return;   // beim Ziehen höchstens ~8x pro Sekunde senden
    this._lastSend = now;
    for (const h of heads) {
      const data = { lamp_id: h.lamp, x: Math.round(p.x * 100) / 100, y: Math.round(p.y * 100) / 100 };
      if (h.entry) data.config_entry_id = h.entry;
      this._hass.callService("freedmx", "aim", data).catch((e) => { this.$.warn.textContent = `Fehler: ${e.message || e}`; });
    }
  }

  _calibrate(p) {
    const h = this._heads().find((x) => this._sel.has(x.id));
    this._cal = false; this._ctlBuilt = false;
    if (!h) { this._render(true); return; }
    const r = h.room;
    const data = { lamp_id: h.lamp, x: Math.round(Math.max(0, Math.min(r.width, p.x)) * 100) / 100, y: Math.round(Math.max(0, Math.min(r.depth, p.y)) * 100) / 100 };
    if (h.entry) data.config_entry_id = h.entry;
    this._hass.callService("freedmx", "calibrate", data)
      .then(() => { this._note = `${h.name} kalibriert – die Integration lädt kurz neu.`; setTimeout(() => { this._note = ""; this._ghost = null; this._render(true); }, 6000); this._render(true); })
      .catch((e) => { this._note = `Fehler: ${e.message || e}`; this._render(true); });
  }

  /* ---------- Zeichnen ---------- */
  _render(force) {
    if (!this._hass || !this._config || !this.$) return;
    const c = this._config;
    const heads = this._heads();
    const sig = JSON.stringify([heads.map((x) => [x.id, x.on, x.bri, x.target, x.mount, x.room, x.set, x.available]), c]);
    if (!force && sig === this._sig) return;
    this._sig = sig;

    this.$.title.textContent = c.title || "";
    if (!heads.length) {
      this.$.dim.textContent = "";
      this.$.chips.innerHTML = "";
      this.$.svg.innerHTML = "";
      this.$.info.innerHTML = `<div class="empty">Keine Moving Heads mit Montagedaten gefunden. Lampe mit Pan/Tilt in der freeDMX-Integration anlegen (Konfigurieren → Lampe bearbeiten).</div>`;
      this.$.ctl.innerHTML = ""; this.$.warn.textContent = "";
      return;
    }
    if (![...this._sel].some((id) => heads.find((x) => x.id === id))) this._sel = new Set([heads[0].id]);

    const r = heads[0].room;
    const W = Number(r.width), D = Number(r.depth), M = Math.max(0, Math.min(Number(r.margin) || 0, W / 2, D / 2));
    this.$.dim.textContent = `${fmt(W)} × ${fmt(D)} m · ${r.limit ? `Begrenzung an (Rand ${fmt(M)} m)` : "Begrenzung aus"}`;

    this.$.chips.innerHTML = heads.map((x) =>
      `<button class="chip ${this._sel.has(x.id) ? "sel" : ""} ${x.on ? "" : "off"}" style="--c:${x.color}" data-id="${frEsc(x.id)}"><span class="dot" style="background:${x.color}"></span>${frEsc(x.name)}</button>`).join("");

    // SVG
    const vw = c.rotate ? D : W, vh = c.rotate ? W : D, pad = 0.45;
    const P = (x, y) => this._toView(x, y);
    const rect = (x0, y0, x1, y1, cls) => {
      const [a, b] = P(x0, y0), [e, f] = P(x1, y1);
      return `<rect class="${cls}" x="${Math.min(a, e)}" y="${Math.min(b, f)}" width="${Math.abs(e - a)}" height="${Math.abs(f - b)}" rx=".05"/>`;
    };
    let s = `<g>${rect(0, 0, W, D, "room")}`;
    if (c.show_grid) {
      s += `<g class="grid">`;
      for (let x = 1; x < W; x++) { const [a, b] = P(x, 0), [e, f] = P(x, D); s += `<line x1="${a}" y1="${b}" x2="${e}" y2="${f}"/>`; }
      for (let y = 1; y < D; y++) { const [a, b] = P(0, y), [e, f] = P(W, y); s += `<line x1="${a}" y1="${b}" x2="${e}" y2="${f}"/>`; }
      s += `</g>`;
    }
    if (r.limit) s += rect(M, M, W - M, D - M, "zone");
    { const [a, b] = P(0, 0), [e, f] = P(W, 0); s += `<line class="wall" x1="${a}" y1="${b}" x2="${e}" y2="${f}"/>`; }
    if (c.show_labels) {
      const [bx, by] = P(W / 2, 0), [dx, dy] = P(W, D / 2);
      s += `<text class="lbl" x="${bx}" y="${by - 0.12}" text-anchor="middle">${fmt(W)} m · Kopfwand</text>`;
      s += `<text class="lbl" x="${dx + 0.12}" y="${dy}" dominant-baseline="middle" ${c.rotate ? 'text-anchor="middle"' : ""}>${c.rotate ? "" : fmt(D) + " m"}</text>`;
    }
    const sz = Math.max(0.12, Math.min(W, D) * 0.045);
    for (const h of heads) {
      const [hx, hy] = P(Number(h.mount.x), Number(h.mount.y));
      const t = h.target;
      if (t && c.show_beams) {
        // Liegt der Punkt außerhalb (oder trifft der Strahl den Boden nicht), am Raumrand gestrichelt zeigen.
        const inside = t.boden && t.x >= 0 && t.x <= W && t.y >= 0 && t.y <= D;
        const [cx0, cy0] = inside ? [t.x, t.y] : frClamp({ width: W, depth: D, margin: 0 }, h.mount, t.x, t.y);
        const [tx, ty] = P(cx0, cy0);
        s += `<line class="beam" x1="${hx}" y1="${hy}" x2="${tx}" y2="${ty}" stroke="${h.color}" ${inside ? "" : 'stroke-dasharray=".1 .08"'}/>`;
        s += `<circle class="spot ${h.on ? "" : "off"}" cx="${tx}" cy="${ty}" r="${sz * 1.25}" fill="${h.color}" fill-opacity="${inside ? ".75" : ".2"}"/>`;
      }
      s += `<rect class="headm" x="${hx - sz}" y="${hy - sz}" width="${sz * 2}" height="${sz * 2}" rx="${sz * 0.4}" fill="${h.color}" ${this._sel.has(h.id) ? "" : 'fill-opacity=".55"'}/>`;
      if (c.show_labels) s += `<text class="lbl" x="${hx}" y="${hy + sz * 2.8}" text-anchor="middle">${frEsc(h.name)}</text>`;
    }
    if (this._ghost && this._cal) {
      const [u, v] = P(this._ghost.x, this._ghost.y);
      s += `<g stroke="#fff" stroke-width=".04"><line x1="${u - sz * 2}" y1="${v}" x2="${u + sz * 2}" y2="${v}"/><line x1="${u}" y1="${v - sz * 2}" x2="${u}" y2="${v + sz * 2}"/></g>`;
    } else if (this._ghost) {
      for (const h of heads.filter((x) => this._sel.has(x.id))) {
        const [gx, gy] = r.limit ? frClamp({ width: W, depth: D, margin: M }, h.mount, this._ghost.x, this._ghost.y) : [this._ghost.x, this._ghost.y];
        const [u, v] = P(gx, gy);
        s += `<circle class="ghost" cx="${u}" cy="${v}" r="${sz * 1.6}" stroke="${h.color}"/>`;
      }
    }
    s += `</g>`;
    this.$.svg.setAttribute("viewBox", `${-pad} ${-pad} ${vw + pad * 2} ${vh + pad * 2}`);
    this.$.svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    this.$.svg.innerHTML = s;

    // Info zum gewählten Kopf
    const cur = heads.find((x) => this._sel.has(x.id));
    const t = cur?.target;
    this.$.info.innerHTML = cur
      ? `<span><b>${frEsc(cur.name)}</b></span>` +
        (t && t.boden ? `<span>x ${fmt(t.x)} m · y ${fmt(t.y)} m</span><span class="dim">Pan ${fmt(t.pan)} % · Tilt ${fmt(t.tilt)} %</span>` : `<span class="dim">Position unbekannt</span>`) +
        (t && !t.boden ? `<span class="warn">Strahl trifft den Boden nicht – im Plan tippen, um ihn auszurichten</span><span class="dim">Pan ${fmt(t.pan)} % · Tilt ${fmt(t.tilt)} %</span>` : "")
      : "";

    if (c.show_controls) {
      const anyOn = heads.some((x) => this._sel.has(x.id) && x.on);
      const bri = Math.round(((cur?.bri || 0) / 255) * 100);
      if (this._cal) {
        if (!this._ctlBuilt) {
          const st = Number(this._step || 2);
          this.$.ctl.innerHTML = `<button class="btn on" data-act="cal">Abbrechen</button>
            <button class="btn" data-act="jog" data-d="pan-">◀ Pan</button><button class="btn" data-act="jog" data-d="pan+">Pan ▶</button>
            <button class="btn" data-act="jog" data-d="tilt-">▲ Tilt</button><button class="btn" data-act="jog" data-d="tilt+">Tilt ▼</button>
            ${[0.5, 2, 10].map((v) => `<button class="btn ${st === v ? "on" : ""}" data-act="step" data-v="${v}">${String(v).replace(".", ",")} %</button>`).join("")}`;
          this._ctlBuilt = true;
        }
      } else if (!this._ctlBuilt || this._ctlOn !== anyOn) {
        this.$.ctl.innerHTML = `<button class="btn ${anyOn ? "on" : ""}" data-act="toggle">${anyOn ? "Aus" : "Ein"}</button>
          <button class="btn" data-act="center">Raummitte</button>
          <button class="btn ${this._cal ? "on" : ""}" data-act="cal">${this._cal ? "Abbrechen" : "Kalibrieren"}</button>
          <input type="range" min="1" max="100" step="1" value="${bri || 100}" title="Helligkeit">`;
        this._ctlBuilt = true; this._ctlOn = anyOn;
      } else {
        const rg = this.$.ctl.querySelector("input[type=range]");
        if (rg && this.shadowRoot.activeElement !== rg && bri) rg.value = bri;
      }
    } else { this.$.ctl.innerHTML = ""; this._ctlBuilt = false; }

    const unset = heads.filter((x) => !x.set).map((x) => x.name);
    if (this._cal) { this.$.warn.textContent = `Kalibrieren ${cur?.name || ""}: Lichtpunkt mit den Pfeilen auf eine gut sichtbare Stelle am Boden fahren (Begrenzung ist dabei aus), dann im Plan genau auf diese Stelle tippen.`; return; }
    if (this._note) { this.$.warn.textContent = this._note; return; }
    this.$.warn.textContent = unset.length
      ? `Montage nicht eingestellt (Platzhalter): ${unset.join(", ")} – Einstellungen → freeDMX → Konfigurieren → Lampe bearbeiten.`
      : (heads.some((x) => !x.available) ? "freeDMX nicht verbunden." : "");
  }
}

const FR_SCHEMA = [
  { name: "title", label: "Titel", selector: { text: {} } },
  { name: "entities", label: "Moving Heads (leer = alle automatisch)", selector: { entity: { domain: "light", multiple: true, integration: "freedmx" } } },
  {
    type: "grid", name: "", schema: [
      { name: "rotate", label: "Plan drehen (Kopfwand links)", selector: { boolean: {} } },
      { name: "move_together", label: "Mehrere Köpfe gemeinsam bewegen", selector: { boolean: {} } },
      { name: "show_grid", label: "Meter-Raster", selector: { boolean: {} } },
      { name: "show_labels", label: "Beschriftung", selector: { boolean: {} } },
      { name: "show_beams", label: "Strahl und Lichtpunkt", selector: { boolean: {} } },
      { name: "show_controls", label: "Ein/Aus und Helligkeit", selector: { boolean: {} } },
    ],
  },
];

class FreedmxRaumCardEditor extends HTMLElement {
  setConfig(config) { this._config = { ...FR_DEFAULTS, ...(config || {}) }; this._render(); }
  set hass(hass) { this._hass = hass; if (this._form) this._form.hass = hass; else this._render(); }
  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      const note = document.createElement("div");
      note.style.cssText = "font-size:.85rem;opacity:.8;margin-bottom:8px";
      note.textContent = "Raummaße, Randabstand und Montageorte stellst du in der Integration ein: Einstellungen → Geräte & Dienste → freeDMX → Konfigurieren (Raum bzw. Lampe bearbeiten). Größe der Karte im Reiter „Layout“.";
      this.appendChild(note);
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => s.label || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        this._config = { ...this._config, ...ev.detail.value };
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = FR_SCHEMA;
    this._form.data = this._config;
  }
}

if (!customElements.get("freedmx-raum-card")) {
  customElements.define("freedmx-raum-card", FreedmxRaumCard);
  customElements.define("freedmx-raum-card-editor", FreedmxRaumCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "freedmx-raum-card", name: "freeDMX Raum", description: "Grundriss mit Moving Heads: Lichtpunkt antippen oder ziehen, Bewegung auf den Raum begrenzt", preview: false });
  console.info(`%c freeDMX Raum-Karte ${FR_VERSION} `, "background:#3fc1ff;color:#000;border-radius:4px");
}
