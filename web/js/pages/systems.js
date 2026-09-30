// System Landscape: every system of record around the platform, what reaches us
// through which channel, the layers the data moves through, the write-backs that
// close the loop, and what it means that the CM sits in Taiwan.
import { html, raw, esc, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';
import { charts } from '../lib/charts.js';

const LAYER_INFO = {
  LANDING: { label: 'Landing', sub: 'raw_* · verbatim payloads', icon: 'download' },
  CORE: { label: 'Core', sub: 'canonical model · FKs enforced', icon: 'database' },
  ACTION: { label: 'Action', sub: 'decisions and every write', icon: 'bolt' },
};
const CHANNEL_SHORT = { API: 'API', EDI: 'EDI', EMAIL_XLSX: 'Email + Excel', EMAIL_TEXT: 'Email', PORTAL: 'Portal', WEBHOOK: 'Webhook', NATIVE: 'Native' };
const HOLIDAY = { '06-19': 'Dragon Boat Festival', '09-25': 'Mid-Autumn Festival', '10-09': 'National Day bridge', '10-10': 'National Day (Double Ten)' };
const st = (v) => ui.statusChip(v, v === 'OK' ? 'OK' : undefined);

let S = null;

export async function render(el, ctx) {
  injectStyle('page-systems', PAGE_CSS);
  const d = await api.get('/api/systems/landscape');
  S = { el, ctx, d };
  const k = d.kpis;
  const cm = d.cm;
  const all = d.groups.flatMap((g) => g.systems);

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-systems">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Systems in the loop', value: fmt.int(k.systems), hint: `${k.external} run by partners · ${k.systems - k.external} OEM-owned` })}
        ${ui.kpi({ label: 'Messages landed, last 7 days', value: fmt.int(k.landed_7d), hint: 'Across APIs, EDI, email, Excel, webhooks' })}
        ${ui.kpi({ label: 'Write-backs to other systems', value: fmt.int(k.writes), hint: 'outbound_message: the "act" half of each loop' })}
        ${ui.kpi({ label: 'One relational model', value: fmt.int(k.tables), unit: 'tables', hint: `${fmt.compact(k.rows)} rows · landing → core → action` })}
        ${ui.kpi({ label: 'At the CM right now', value: cm.local_now.split(' · ')[1], unit: 'Taipei', hint: `${cm.local_now.split(' · ')[0]} in Taichung · ${cm.pt_now} in Palo Alto` })}
      </div>

      ${ui.card({
        title: 'How the systems connect',
        subtitle: 'Partners on the left send what they have (API, EDI, email, Excel). Everything lands raw, is normalized into one model, and decisions write back out. Click a system for detail.',
        tableToggle: true,
        cls: 'sy-map-card',
        body: html`<div class="sy-map-scroll"><div class="sy-map-inner">${landscapeChart(d)}</div></div>
          <div class="legend sy-legend">
            <span class="legend-item"><span class="legend-key line" style="--c:var(--ink-3)"></span>Inbound: data arriving</span>
            <span class="legend-item"><span class="legend-key line" style="--c:var(--series-2)"></span>Write-back: a decision acting on another system</span>
            <span class="legend-item"><span class="sy-dot" style="--c:var(--critical)"></span>Messages in quarantine</span>
          </div>`,
      })}

      <div class="grid">
        <div class="span-7">${ui.card({ title: 'The contract manufacturer is in another country', subtitle: `${cm.name} · ${cm.city} · ${cm.tz}`, body: cmContext(cm) })}</div>
        <div class="span-5">${ui.card({ title: 'Write-backs by target system', subtitle: 'Every decision leaves the platform as a message another system acts on', tableToggle: true, body: writebacks(d.outbound) })}</div>
      </div>

      ${ui.card({ title: 'Who owns what', subtitle: 'For each business entity: the system of record, where its raw copy lands, and the canonical tables that hold the single version', flush: true, body: ownership(d.ownership) })}

      ${ui.card({ title: 'All systems', subtitle: 'Owner, channel, cadence and live volume', flush: true, body: html`<div class="sy-table"></div>` })}
    </div>`;

  ui.dataTable($('.sy-table', el), {
    rows: all,
    search: true,
    pageSize: 20,
    onRowClick: (r) => openSystem(r.system_id),
    columns: [
      { key: 'name', label: 'System', render: (r) => html`<strong>${r.name}</strong><div class="tiny muted">${r.system_id}</div>` },
      { key: 'kind', label: 'Kind', format: (v) => fmt.title(v) },
      { key: 'owner', label: 'Owner', format: (v) => fmt.title(v) },
      { key: 'operator', label: 'Operator', wrap: true },
      { key: 'country', label: 'Country' },
      { key: 'channel', label: 'Channel', format: (v) => CHANNEL_SHORT[v] || v },
      { key: 'frequency', label: 'Cadence', wrap: true },
      { key: 'last_7d', label: 'Last 7 days', num: true, value: (r) => r.health.last_7d },
      { key: 'last_at', label: 'Last seen', value: (r) => r.health.last_at, format: (v) => fmt.rel(v) },
      { key: 'quarantined', label: 'Quarantined', num: true, value: (r) => r.health.quarantined || 0 },
    ],
  });

  S.offs = [
    on(el, 'click', '[data-sys]', (e, t) => { e.preventDefault(); openSystem(t.getAttribute('data-sys')); }),
    on(el, 'keydown', '[data-sys]', (e, t) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSystem(t.getAttribute('data-sys')); } }),
  ];
  const q = ctx.query.get('system');
  if (q) openSystem(q);
}

export function unmount() {
  if (S && S.offs) S.offs.forEach((off) => off());
  S = null;
}

// ---------------------------------------------------------------------------
// landscape diagram
// ---------------------------------------------------------------------------
function trunc(s, n) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function landscapeChart(d) {
  const left = d.groups.filter((g) => g.label !== 'OEM systems');
  const right = d.groups.find((g) => g.label === 'OEM systems') || { systems: [] };
  const outByTarget = new Map(d.outbound.map((o) => [o.system_id, o]));
  const NODE_H = 44;
  const GAP = 8;
  const GROUP_H = 26;
  const leftH = left.reduce((a, g) => a + GROUP_H + g.systems.length * (NODE_H + GAP) + 10, 0);
  const H = Math.max(640, leftH + 20);
  const tableRows = [];
  for (const g of d.groups) {
    for (const s of g.systems) {
      tableRows.push([g.label, s.name, CHANNEL_SHORT[s.channel] || s.channel, fmt.int(s.health.last_7d), s.health.last_at ? fmt.rel(s.health.last_at) : '—',
        s.health.quarantined ? fmt.int(s.health.quarantined) : '0', outByTarget.has(s.system_id) ? fmt.int(outByTarget.get(s.system_id).n) : '0']);
    }
  }
  return charts.custom({
    height: H,
    ariaLabel: 'System landscape: partner systems feed the landing layer, normalizers build the core, decisions write back',
    table: { columns: ['Group', 'System', 'Channel', 'Last 7 days', 'Last seen', 'Quarantined', 'Write-backs received'], rows: tableRows,
      numeric: [false, false, true, false, true, true] },
    render: (w) => {
      const W = Math.max(w, 960);
      const colL = { x: 8, w: Math.min(300, W * 0.27) };
      const colR = { w: Math.min(280, W * 0.24) };
      colR.x = W - colR.w - 8;
      const mid = { x: colL.x + colL.w + 90, w: colR.x - (colL.x + colL.w) - 180 };
      const defs = '<defs>'
        + '<marker id="sy-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" style="fill:var(--ink-3)"/></marker>'
        + '<marker id="sy-arr-o" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" style="fill:var(--series-2)"/></marker>'
        + '</defs>';
      let paths = '';
      let blocksSvg = '';
      let nodesSvg = '';
      // layer blocks in the middle
      const layers = ['LANDING', 'CORE', 'ACTION'];
      const blockH = (H - 40 - 2 * 46) / 3;
      const blocks = {};
      layers.forEach((Lk, i) => {
        const y = 20 + i * (blockH + 46);
        blocks[Lk] = { x: mid.x, y, w: mid.w, h: blockH };
        const info = LAYER_INFO[Lk];
        const data = d.layers[Lk] || { tables: 0, rows: 0, top: [] };
        blocksSvg += `<g class="sy-layer sy-layer-${Lk.toLowerCase()}">`;
        blocksSvg += `<rect x="${mid.x}" y="${y}" width="${mid.w}" height="${blockH}" rx="14"/>`;
        blocksSvg += `<text class="sy-layer-title" x="${mid.x + 18}" y="${y + 30}">${info.label}</text>`;
        blocksSvg += `<text class="sy-layer-sub" x="${mid.x + 18}" y="${y + 50}">${esc(info.sub)}</text>`;
        blocksSvg += `<text class="sy-layer-num" x="${mid.x + mid.w - 18}" y="${y + 32}" text-anchor="end">${fmt.int(data.tables)} tables</text>`;
        blocksSvg += `<text class="sy-layer-sub" x="${mid.x + mid.w - 18}" y="${y + 50}" text-anchor="end">${fmt.compact(data.rows)} rows</text>`;
        (data.top || []).slice(0, 4).forEach((t, j) => {
          if (78 + j * 19 > blockH - 12) return;
          blocksSvg += `<a href="#/sandbox?table=${encodeURIComponent(t)}"><text class="sy-layer-table" x="${mid.x + 18}" y="${y + 78 + j * 19}">${esc(t)}</text></a>`;
        });
        blocksSvg += '</g>';
      });
      const cx = mid.x + mid.w / 2;
      for (const [a, b, label] of [['LANDING', 'CORE', 'normalizers · contracts'], ['CORE', 'ACTION', 'genealogy · MRP · ATP · rules']]) {
        const y1 = blocks[a].y + blocks[a].h + 4;
        const y2 = blocks[b].y - 4;
        blocksSvg += `<line class="sy-flow" x1="${cx}" y1="${y1}" x2="${cx}" y2="${y2}" marker-end="url(#sy-arr)"/>`;
        blocksSvg += `<text class="sy-flow-label" x="${cx + 10}" y="${(y1 + y2) / 2}" dy="0.32em">${esc(label)}</text>`;
      }
      // left: partner systems
      let y = 12;
      const nodeY = {};
      for (const g of left) {
        nodesSvg += `<text class="sy-group" x="${colL.x}" y="${y + 16}">${esc(g.label.toUpperCase())}</text>`;
        y += GROUP_H;
        for (const sys of g.systems) {
          nodeY[sys.system_id] = { x: colL.x, y, w: colL.w, h: NODE_H, side: 'L' };
          nodesSvg += node(sys, colL.x, y, colL.w, NODE_H, outByTarget.get(sys.system_id));
          y += NODE_H + GAP;
        }
        y += 10;
      }
      // right: OEM-native systems beside the core
      const rTotal = right.systems.length * (NODE_H + GAP) + GROUP_H;
      let ry = Math.max(12, blocks.CORE.y + blocks.CORE.h / 2 - rTotal / 2);
      nodesSvg += `<text class="sy-group" x="${colR.x}" y="${ry + 16}">OEM SYSTEMS · NATIVE</text>`;
      ry += GROUP_H;
      for (const sys of right.systems) {
        nodeY[sys.system_id] = { x: colR.x, y: ry, w: colR.w, h: NODE_H, side: 'R' };
        nodesSvg += node(sys, colR.x, ry, colR.w, NODE_H, outByTarget.get(sys.system_id));
        ry += NODE_H + GAP;
      }
      const Lb = blocks.LANDING;
      const Cb = blocks.CORE;
      const Ab = blocks.ACTION;
      const ins = left.flatMap((g) => g.systems);
      ins.forEach((sys, i) => {
        const n = nodeY[sys.system_id];
        const x1 = n.x + n.w;
        const y1 = n.y + n.h / 2;
        const target = sys.health.kind === 'native' ? Cb : Lb;
        const x2 = target.x;
        const y2 = target.y + 24 + ((i + 0.5) / ins.length) * (target.h - 48);
        const midx = (x1 + x2) / 2;
        paths += `<path class="sy-in" d="M${x1},${y1} C${midx},${y1} ${midx},${y2} ${x2 - 2},${y2}" marker-end="url(#sy-arr)"/>`;
      });
      right.systems.forEach((sys, i) => {
        const n = nodeY[sys.system_id];
        const x1 = n.x;
        const y1 = n.y + n.h / 2;
        const x2 = Cb.x + Cb.w;
        const y2 = Cb.y + 30 + ((i + 0.5) / right.systems.length) * (Cb.h - 60);
        const midx = (x1 + x2) / 2;
        paths += `<path class="sy-in" d="M${x1},${y1} C${midx},${y1} ${midx},${y2} ${x2 + 2},${y2}" marker-start="url(#sy-arr)" marker-end="url(#sy-arr)"/>`;
      });
      // write-backs leave the action layer through the gutter beside it and enter the target from the side
      const outs = d.outbound.filter((o) => nodeY[o.system_id]);
      const gutL = colL.x + colL.w + 34;
      const gutR = colR.x - 34;
      outs.forEach((o, i) => {
        const n = nodeY[o.system_id];
        const right2 = n.side === 'R';
        const x1 = right2 ? Ab.x + Ab.w : Ab.x;
        const y1 = Ab.y + 24 + ((i + 0.5) / outs.length) * (Ab.h - 48);
        const gx = right2 ? gutR + (i % 3) * 6 : gutL - (i % 3) * 6;
        const x2 = right2 ? n.x - 3 : n.x + n.w + 3;
        const y2 = n.y + n.h - 12;
        paths += `<path class="sy-out" d="M${x1},${y1} C${gx},${y1} ${gx},${y1} ${gx},${(y1 + y2) / 2} S${gx},${y2} ${x2},${y2}" marker-end="url(#sy-arr-o)" data-tip="${esc(`Write-backs to ${o.target}`)}\n${esc(o.types.join(' · '))}"/>`;
      });
      const s = defs + paths + blocksSvg + nodesSvg;
      return raw(s);
    },
  });
}

function node(sys, x, y, w, h, out) {
  const hl = sys.health;
  const q = hl.quarantined || 0;
  const vol = hl.kind === 'raw' ? `${fmt.compact(hl.last_7d)} / 7d` : `${fmt.compact(hl.last_7d)} rec / 7d`;
  const tip = [`${sys.name}`, `${sys.operator} · ${sys.country}`, `${CHANNEL_SHORT[sys.channel] || sys.channel} · ${sys.frequency}`,
    `${fmt.int(hl.total)} ${hl.kind === 'raw' ? 'payloads landed' : 'records'} · last ${hl.last_at ? fmt.rel(hl.last_at) : '—'}`,
    q ? `${q} in quarantine` : 'Nothing in quarantine'].join('\n');
  let s = `<g class="sy-node${hl.kind === 'native' ? ' native' : ''}" data-sys="${esc(sys.system_id)}" tabindex="0" role="button" aria-label="${esc(sys.name)}" data-tip="${esc(tip)}">`;
  s += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="9"/>`;
  s += `<text class="sy-node-name" x="${x + 12}" y="${y + 18}">${esc(trunc(sys.name, Math.floor((w - 70) / 7.2)))}</text>`;
  s += `<text class="sy-node-sub" x="${x + 12}" y="${y + 34}">${esc(CHANNEL_SHORT[sys.channel] || sys.channel)} · ${esc(vol)}</text>`;
  if (q) {
    s += `<circle cx="${x + w - 16}" cy="${y + 16}" r="8" style="fill:var(--critical)"/><text class="sy-q" x="${x + w - 16}" y="${y + 16}" dy="0.35em" text-anchor="middle">${q}</text>`;
  }
  if (out) {
    s += `<text class="sy-wb" x="${x + w - 12}" y="${y + h - 9}" text-anchor="end">↩ ${fmt.int(out.n)} write-back${out.n === 1 ? '' : 's'}</text>`;
  }
  s += '</g>';
  return s;
}

// ---------------------------------------------------------------------------
// CM context, write-backs, ownership
// ---------------------------------------------------------------------------
function cmContext(cm) {
  const holidays = (cm.holidays || []).map((h) => `${fmt.date(h)} ${HOLIDAY[h.slice(5)] || 'holiday'}`);
  const v2 = (cm.mappings || []).find((m) => m.version === 'v2');
  const quarantine = (cm.quarantine || []).reduce((a, r) => a + r.n, 0);
  const facts = [
    { icon: 'clock', title: '15 hours ahead, and no daylight saving on their side',
      body: html`It is <strong>${cm.local_now}</strong> in Taichung and ${cm.pt_now} in Palo Alto. Taiwan stays on UTC+8 all year, so the gap is 15 hours in summer and 16 in winter; a stand-up at 17:00 Pacific is 08:00 the next morning on the line. The feed normalizer converts every event to UTC; ${fmt.int(cm.tz_corrected)} events last month arrived labeled UTC while actually on Taipei time, and were corrected rather than dropped.` },
    { icon: 'ledger', title: 'Bilingual paperwork',
      body: html`${fmt.int(cm.daily_reports)} daily production reports arrived as Traditional Chinese / English Excel workbooks (${link.route('integrations?tab=email', 'see the pipeline')}). ${fmt.int(cm.chinese_defects)} MES failure events carried only a Chinese description (e.g. 馬達異音, motor noise) and were mapped to defect codes by the versioned mapping.` },
    { icon: 'calendar', title: 'Their calendar, not ours',
      body: html`Taiwan holidays shape the build plan: ${holidays.length ? holidays.join(' · ') : 'none in the window'}. The line was dark on Mid-Autumn Festival, so there is no daily report for 9/25, and none was expected.` },
    { icon: 'boxes', title: 'OEM-owned stock sitting abroad',
      body: html`${fmt.int(cm.consigned_units)} consigned modules (drive units, pedal units, HMIs) worth ${fmt.usd(cm.consigned_usd, { compact: true })} sit at the CM, bought by OEM and tracked by serial through supplier ASNs. ${cm.stranded_du_b ? html`<strong>${fmt.int(cm.stranded_du_b)}</strong> are rev-B drive units stranded by ECO-0042.` : ''}` },
    { icon: 'ship', title: 'An ocean between build and customer',
      body: html`Median ${cm.transit_median_days ? fmt.num(cm.transit_median_days, 1) : '—'} days port to port across ${fmt.int(cm.containers)} containers. Each import entry values the vehicle <strong>including the assists</strong> OEM consigned (${fmt.usd(cm.assists_per_unit, { cents: true })} per unit of drive unit, HMI and pedal unit): ${fmt.int(cm.entries)} entries, ${fmt.usd(cm.entered_value, { compact: true })} entered value, ${fmt.usd(cm.duty_usd, { compact: true })} duty (mock rate).` },
    { icon: 'plug', title: 'Their MES, their schema changes',
      body: html`The CM upgraded its MES mid-ramp (${v2 ? fmt.dt(v2.effective_from) : '—'}): field names and time format changed overnight. Mapping v2 was deployed in 2h15m and the quarantined messages were replayed. ${quarantine ? html`Right now <strong>${quarantine}</strong> messages from an undeclared rework station (S65) wait in quarantine: ${link.route('cm-feed', 'CM Feed')}.` : ''}` },
  ];
  return html`<ul class="sy-facts">${facts.map((f) => html`<li><span class="sy-fact-ic">${icon(f.icon === 'calendar' ? 'clock' : f.icon, 16)}</span><div><div class="sy-fact-title">${f.title}</div><div class="sy-fact-body">${f.body}</div></div></li>`)}</ul>`;
}

function writebacks(out) {
  if (!out.length) return ui.empty('No write-backs yet.');
  const rows = [...out].sort((a, b) => b.n - a.n);
  return html`${charts.bar({
    categories: rows.map((r) => fmt.title(r.target)),
    series: [{ name: 'Messages', values: rows.map((r) => r.n) }],
    horizontal: true,
    yFormat: (v) => (Number.isInteger(v) ? fmt.int(v) : ''),
    ariaLabel: 'Outbound messages by target system',
  })}
  <ul class="sy-wb-list">${rows.map((r) => html`<li><strong>${fmt.title(r.target)}</strong><span class="muted small">${r.types.join(' · ')}</span></li>`)}</ul>`;
}

function ownership(rows) {
  return html`<div class="table-wrap"><table class="table">
    <thead><tr><th>Entity</th><th>System of record</th><th>Raw copy lands in</th><th>Canonical tables</th><th>Where to act</th></tr></thead>
    <tbody>${rows.map((r) => html`<tr>
      <td class="wrap"><strong>${r.entity}</strong></td>
      <td class="wrap">${r.system}</td>
      <td class="wrap">${r.landing ? r.landing.split(' · ').map((t, i) => html`${i ? ', ' : ''}${link.table(t)}`) : html`<span class="muted small">native: writes the core</span>`}</td>
      <td class="wrap">${Object.entries(r.tables).map(([t, n], i) => html`${i ? ' · ' : ''}${link.table(t)} <span class="muted small">${fmt.compact(n)}</span>`)}</td>
      <td>${link.route(r.page, fmt.title(r.page.replace('-', '_')))}</td>
    </tr>`)}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// drawer
// ---------------------------------------------------------------------------
function openSystem(id) {
  if (!S) return;
  const sys = S.d.groups.flatMap((g) => g.systems).find((s) => s.system_id === id);
  if (!sys) return;
  const h = sys.health;
  const out = S.d.outbound.filter((o) => o.system_id === id);
  const statusRows = Object.entries(h.status || {}).sort((a, b) => b[1] - a[1]);
  ui.drawer.open({
    title: sys.name,
    subtitle: html`${ui.chip(sys.owner === 'OEM' ? 'info' : 'neutral', fmt.title(sys.owner), { icon: false })}<span>${sys.operator} · ${sys.country}</span>`,
    body: html`
      <dl class="kv">
        <dt>Kind</dt><dd>${fmt.title(sys.kind)}</dd>
        <dt>Channel</dt><dd>${CHANNEL_SHORT[sys.channel] || sys.channel} · ${sys.direction === 'BOTH' ? 'in and out' : fmt.title(sys.direction)}</dd>
        <dt>Cadence</dt><dd>${sys.frequency}</dd>
        <dt>Lands in</dt><dd>${(sys.landing || '').split(/\s*[/·]\s*/).filter(Boolean).map((t, i) => html`${i ? ', ' : ''}${/^[a-z_]+$/.test(t) ? link.table(t) : t}`)}</dd>
        <dt>Volume</dt><dd>${fmt.int(h.total)} ${h.kind === 'raw' ? 'payloads' : 'records'} · ${fmt.int(h.last_7d)} in the last 7 days</dd>
        <dt>Last seen</dt><dd>${h.last_at ? html`${fmt.dt(h.last_at)} PT (${fmt.rel(h.last_at)})` : '—'}</dd>
        ${h.automated_share != null ? html`<dt>Automated</dt><dd>${fmt.pct(h.automated_share, 2)} processed without a human</dd>` : ''}
      </dl>
      ${sys.notes ? ui.callout({ tone: 'info', title: 'Notes', body: sys.notes }) : ''}
      ${statusRows.length ? html`<h4 class="section-title">Landing status</h4><div class="sy-status">${statusRows.map(([k, n]) => html`<span>${st(k)} <strong>${fmt.int(n)}</strong></span>`)}</div>` : ''}
      ${out.length ? html`<h4 class="section-title">Write-backs it receives</h4><p class="small">${out.map((o) => o.types.join(' · '))}</p>` : ''}
      ${sys.options && sys.options.length ? html`<h4 class="section-title">Integration options</h4>
        <ul class="sy-opts">${sys.options.map((o) => html`<li>${ui.statusChip(o.status)} ${o.option}</li>`)}</ul>
        <p class="small">${link.route('integrations?tab=options', 'Compare the options with evidence →')}</p>` : ''}
      ${sys.pages && sys.pages.length ? html`<h4 class="section-title">Pages that use it</h4><p class="sy-pages">${sys.pages.map((p) => link.route(p, fmt.title(p.replace('-', '_'))))}</p>` : ''}`,
  });
}

const PAGE_CSS = `
.pg-systems .sy-map-scroll { overflow-x: auto; }
.pg-systems .sy-map-inner { min-width: 960px; }
.pg-systems .sy-legend { margin: 10px 0 0; }
.pg-systems .sy-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--c); display: inline-block; }
.pg-systems .chart .sy-layer rect { fill: var(--surface-2); stroke: var(--hairline-strong); stroke-width: 1; }
.pg-systems .chart .sy-layer-core rect { fill: color-mix(in srgb, var(--sign) 7%, var(--surface)); stroke: color-mix(in srgb, var(--sign) 45%, transparent); }
.pg-systems .chart .sy-layer-action rect { fill: color-mix(in srgb, var(--series-2) 7%, var(--surface)); stroke: color-mix(in srgb, var(--series-2) 45%, transparent); }
.pg-systems .chart .sy-layer-title { font: 600 19px var(--font-cond); fill: var(--ink); }
.pg-systems .chart .sy-layer-sub { font-size: 12px; fill: var(--ink-3); }
.pg-systems .chart .sy-layer-num { font: 600 15px var(--font-ui); fill: var(--ink); }
.pg-systems .chart .sy-layer-table { font: 500 12px var(--font-mono); fill: var(--link); }
.pg-systems .chart a:hover .sy-layer-table { text-decoration: underline; }
.pg-systems .chart .sy-flow { stroke: var(--ink-3); stroke-width: 2; }
.pg-systems .chart .sy-flow-label { font-size: 11.5px; fill: var(--ink-2); font-weight: 500; }
.pg-systems .chart .sy-group { font: 600 11px var(--font-cond); letter-spacing: .12em; fill: var(--ink-3); }
.pg-systems .chart .sy-node { cursor: pointer; outline: none; }
.pg-systems .chart .sy-node rect { fill: var(--surface); stroke: var(--hairline-strong); stroke-width: 1; transition: stroke .12s, fill .12s; }
.pg-systems .chart .sy-node.native rect { fill: color-mix(in srgb, var(--sign) 6%, var(--surface)); }
.pg-systems .chart .sy-node:hover rect, .pg-systems .chart .sy-node:focus-visible rect { stroke: var(--focus); stroke-width: 1.5; }
.pg-systems .chart .sy-node-name { font: 600 13px var(--font-cond); fill: var(--ink); }
.pg-systems .chart .sy-node-sub { font-size: 11.5px; fill: var(--ink-3); font-variant-numeric: tabular-nums; }
.pg-systems .chart .sy-q { fill: #fff; font-size: 10.5px; font-weight: 700; }
.pg-systems .chart .sy-wb { font-size: 11px; font-weight: 600; fill: var(--ink-2); }
.pg-systems .chart .sy-in { fill: none; stroke: var(--ink-3); stroke-width: 1.25; opacity: .45; }
.pg-systems .chart .sy-out { fill: none; stroke: var(--series-2); stroke-width: 1.75; opacity: .85; }
.pg-systems .chart .sy-out:hover { stroke-width: 3; opacity: 1; }
.pg-systems .sy-facts { list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }
.pg-systems .sy-facts li { display: flex; gap: 12px; }
.pg-systems .sy-fact-ic { flex: none; width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; background: color-mix(in srgb, var(--sign) 10%, var(--surface)); color: var(--sign); }
.pg-systems .sy-fact-title { font: 600 14.5px/1.3 var(--font-cond); }
.pg-systems .sy-fact-body { font-size: 13px; color: var(--ink-2); margin-top: 2px; }
.pg-systems .sy-wb-list { list-style: none; padding: 0; margin: 12px 0 0; display: grid; gap: 6px; }
.pg-systems .sy-wb-list li { display: flex; flex-direction: column; gap: 1px; padding-top: 6px; border-top: 1px solid var(--hairline); }
.pg-systems .sy-status { display: flex; flex-wrap: wrap; gap: 8px 14px; }
.pg-systems .sy-opts { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; font-size: 13px; }
.drawer .sy-pages, .pg-systems .sy-pages { display: flex; flex-wrap: wrap; gap: 6px 14px; }
.drawer .sy-status { display: flex; flex-wrap: wrap; gap: 8px 14px; }
.drawer .sy-opts { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; font-size: 13px; }
`;
