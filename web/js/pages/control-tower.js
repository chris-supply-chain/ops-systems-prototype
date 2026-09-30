// Control Tower: where every vehicle and pack is right now (factory to door), what is
// wrong (the exception queue, worst first), and what the system wants a human to decide.
import { html, raw, esc, on, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const SEV_TONE = { CRITICAL: 'critical', SERIOUS: 'serious', WARNING: 'warning', INFO: 'info' };
const LOOP_ICON = { QUALITY: 'shield', SUPPLY: 'truck', PROMISE: 'clock', DATA: 'database' };

export async function render(el, ctx) {
  injectStyle('page-control-tower', PAGE_CSS);
  const d = await api.get('/api/tower');
  fmt.setNow(d.now);
  const k = d.kpis;
  const crit = (k.exceptions.CRITICAL || 0);
  const serious = (k.exceptions.SERIOUS || 0);

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-tower">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Vehicles built, 7 days', value: fmt.int(k.vehicles_7d), unit: `/ ${fmt.int(k.vehicles_plan7)} committed`,
          delta: k.vehicles_7d - k.vehicles_prev7, deltaLabel: 'vs prior 7 days', hint: 'Formosa Assembly Partners, Taichung' })}
        ${ui.kpi({ label: 'Packs built, 7 days', value: fmt.int(k.packs_7d), unit: `/ ${fmt.int(k.packs_plan7)} MPS`,
          delta: k.packs_7d - k.packs_prev7, deltaLabel: 'vs prior 7 days', hint: 'OEM pack line, Fremont' })}
        ${ui.kpi({ label: 'On the water', value: fmt.int(k.on_water), unit: 'vehicles',
          hint: `${k.containers_on_water} container${k.containers_on_water === 1 ? '' : 's'} crossing the Pacific` })}
        ${ui.kpi({ label: 'Open orders', value: fmt.int(k.backlog),
          hint: k.promise_lead_days != null ? `New orders promised in about ${Math.round(k.promise_lead_days)} days` : '' })}
        ${ui.kpi({ label: 'Delivered on promise, 30 days', value: k.otd_30d == null ? '—' : fmt.pct(k.otd_30d, 0),
          hint: `${fmt.int(k.delivered_30d)} deliveries against first promise` })}
        ${ui.kpi({ label: 'Open exceptions', value: fmt.int(crit + serious), unit: 'critical + serious',
          status: crit ? { tone: 'critical', label: `${crit} critical` } : null, hint: 'Queue below, worst first' })}
      </div>

      ${ui.card({
        title: 'Factory to door, right now',
        subtitle: 'Every serialized vehicle and pack, by where it physically is. Click a stop to work it.',
        cls: 'pt-flow-card',
        body: flowChart(d),
        tableToggle: true,
      })}

      <div class="grid">
        <div class="span-7">${ui.card({
          title: 'Exception queue',
          subtitle: 'Rules over the core data raise these; they resolve themselves when the condition clears.',
          flush: true,
          body: exceptionList(d.exceptions),
        })}</div>
        <div class="span-5">
          ${ui.card({
            title: 'Decisions waiting on you',
            subtitle: 'Proposed by the closed loops, with evidence. Executing one writes to the systems it touches.',
            body: decisionList(d.decisions),
            actions: html`<a class="btn sm" href="#/loop">${icon('loop', 15)}<span>Closed Loop</span></a>`,
          })}
          ${d.resolved.length ? ui.card({ title: 'Recently resolved', body: resolvedList(d.resolved) }) : ''}
        </div>
      </div>

      <div class="grid">
        <div class="span-6">${ui.card({
          title: 'Built per week vs plan', subtitle: 'Vehicles at the CM against its commit; packs in Fremont against the MPS',
          tableToggle: true, body: outputChart(d.daily),
        })}</div>
        <div class="span-6">${ui.card({
          title: 'Deliveries per day', subtitle: 'Delivered on or before the first promise vs after it',
          tableToggle: true, body: deliveryChart(d.deliveries),
        })}</div>
      </div>

      ${ui.card({
        title: 'Data and assurance',
        subtitle: 'The picture above is only as good as the feeds behind it and the checks on them.',
        body: assurance(d),
      })}
    </div>`;

  OFFS.forEach((off) => off());
  OFFS = [on(el, 'click', '[data-go]', (e, t) => { ctx.go(t.dataset.go); })];
}

let OFFS = [];
export function unmount() {
  OFFS.forEach((off) => off());
  OFFS = [];
}

// ---------------------------------------------------------------- flow

function flowChart(d) {
  const stops = d.flow;
  const packs = d.pack_flow;
  const table = {
    columns: ['Stop', 'Units', 'Detail'],
    numeric: [true, false],
    rows: [...stops.map((s) => [s.label, fmt.int(s.value), s.detail]), ...packs.map((s) => [s.label, fmt.int(s.value), s.detail])],
  };
  return charts.custom({
    height: (w) => (w < 700 ? 420 : 290),
    ariaLabel: 'Factory to door flow',
    table,
    render: (w, h) => raw(w < 700 ? flowVertical(stops, packs, w, h) : flowHorizontal(stops, packs, w, h)),
  });
}

function stopMarker(x, y, s, tone) {
  const hot = s.id === 'ocean' ? 'var(--series-1)' : 'var(--sign)';
  return `<g class="pt-stop" data-go="${esc(s.route)}" data-tip="${esc(`${s.label}\n${fmt.int(s.value)} units · ${s.detail}`)}" tabindex="0">
    <circle cx="${x}" cy="${y}" r="15" fill="transparent"/>
    <circle cx="${x}" cy="${y}" r="9" style="fill:var(--surface);stroke:${tone || hot}" stroke-width="3"/>
  </g>`;
}

function flowHorizontal(stops, packs, w, h) {
  const pad = 70;
  const y = 88;
  const x = (i) => pad + (i * (w - pad * 2)) / (stops.length - 1);
  let out = '';
  for (let i = 0; i < stops.length - 1; i++) {
    const ocean = stops[i + 1].id === 'ocean' || stops[i].id === 'ocean';
    out += `<line x1="${x(i)}" y1="${y}" x2="${x(i + 1)}" y2="${y}" style="stroke:${ocean ? 'var(--series-1)' : 'var(--sign)'}" stroke-width="5" stroke-linecap="round"/>`;
  }
  stops.forEach((s, i) => {
    const cx = x(i);
    out += stopMarker(cx, y, s);
    out += `<text x="${cx}" y="${y - 44}" text-anchor="middle" class="pt-val">${fmt.int(s.value)}</text>`;
    out += `<text x="${cx}" y="${y - 24}" text-anchor="middle" class="pt-lab">${esc(s.label)}</text>`;
    out += `<text x="${cx}" y="${y + 30}" text-anchor="middle" class="pt-sub">${esc(s.sub)}</text>`;
    out += `<text x="${cx}" y="${y + 46}" text-anchor="middle" class="pt-det">${esc(s.detail)}</text>`;
  });
  // the pack chain runs underneath and meets the vehicles at the 3PL, where kits are built
  const i3 = stops.findIndex((s) => s.id === '3pl');
  const py = y + 120;
  const xs = [x(i3 - 2), x(i3 - 1), x(i3)];
  out += `<line x1="${xs[0]}" y1="${py}" x2="${xs[2]}" y2="${py}" style="stroke:var(--series-3)" stroke-width="4" stroke-linecap="round"/>`;
  out += `<line x1="${xs[2]}" y1="${y + 56}" x2="${xs[2]}" y2="${py - 12}" style="stroke:var(--ink-3)" stroke-width="1.5" stroke-dasharray="3 3"/>`;
  out += `<text x="${xs[2] + 8}" y="${(y + 56 + py) / 2 + 4}" class="pt-det">kitting: vehicle + pack + charger</text>`;
  packs.forEach((s, j) => {
    out += stopMarker(xs[j], py, s, 'var(--series-3)');
    out += `<text x="${xs[j]}" y="${py + 28}" text-anchor="middle" class="pt-lab">${esc(s.label)} · ${fmt.int(s.value)}</text>`;
    out += `<text x="${xs[j]}" y="${py + 44}" text-anchor="middle" class="pt-det">${esc(s.detail)}</text>`;
  });
  const lx = 12;
  const ly = h - 8;
  out += `<g class="pt-legend"><line x1="${lx}" y1="${ly - 4}" x2="${lx + 18}" y2="${ly - 4}" style="stroke:var(--sign)" stroke-width="4"/>
    <text x="${lx + 24}" y="${ly}">Vehicles (CM-built, Taiwan)</text>
    <line x1="${lx + 190}" y1="${ly - 4}" x2="${lx + 208}" y2="${ly - 4}" style="stroke:var(--series-1)" stroke-width="4"/>
    <text x="${lx + 214}" y="${ly}">Ocean leg</text>
    <line x1="${lx + 290}" y1="${ly - 4}" x2="${lx + 308}" y2="${ly - 4}" style="stroke:var(--series-3)" stroke-width="4"/>
    <text x="${lx + 314}" y="${ly}">Packs (OEM-built, Fremont)</text></g>`;
  return out;
}

function flowVertical(stops, packs, w, h) {
  const x = 40;
  const y = (i) => 24 + (i * (h - 60)) / (stops.length - 1);
  let out = '';
  for (let i = 0; i < stops.length - 1; i++) {
    out += `<line x1="${x}" y1="${y(i)}" x2="${x}" y2="${y(i + 1)}" style="stroke:${stops[i + 1].id === 'ocean' || stops[i].id === 'ocean' ? 'var(--series-1)' : 'var(--sign)'}" stroke-width="5"/>`;
  }
  stops.forEach((s, i) => {
    out += stopMarker(x, y(i), s);
    out += `<text x="${x + 24}" y="${y(i) - 2}" class="pt-lab">${esc(s.label)} · <tspan class="pt-valsm">${fmt.int(s.value)}</tspan></text>`;
    out += `<text x="${x + 24}" y="${y(i) + 14}" class="pt-det">${esc(s.detail)}</text>`;
  });
  const i3 = stops.findIndex((s) => s.id === '3pl');
  out += `<text x="${x + 24}" y="${y(i3) + 30}" class="pt-det">+ ${fmt.int(packs[2].value)} packs at the 3PL · ${fmt.int(packs[0].value)} in Fremont</text>`;
  return out;
}

// ---------------------------------------------------------------- queues

function exceptionList(rows) {
  if (!rows.length) return ui.empty('No open exceptions.');
  return html`<ul class="pt-ex">${rows.map((e) => html`<li class="pt-ex-row">
    <div class="pt-ex-sev">${ui.chip(SEV_TONE[e.severity] || 'info', fmt.title(e.severity))}</div>
    <div class="pt-ex-main">
      <a class="pt-ex-title" href="${e.route || '#/tower'}">${e.title}</a>
      <div class="pt-ex-detail">${e.detail || ''}</div>
      <div class="pt-ex-meta">
        <span>${fmt.title(e.domain)}</span>
        ${e.owner ? html`<span>Owner: ${e.owner}</span>` : ''}
        ${e.impact_units != null ? html`<span>${fmt.int(e.impact_units)} ${e.impact_unit || 'units'}</span>` : ''}
        ${e.impact_usd ? html`<span>${fmt.usd(e.impact_usd, { compact: true })}</span>` : ''}
        <span>${fmt.rel(e.detected_at)}</span>
        ${e.status === 'ACKNOWLEDGED' ? ui.chip('info', 'Acknowledged') : ''}
      </div>
    </div>
    <div class="pt-ex-act">
      ${e.decision_id ? html`<a class="btn sm" href="#/loop?d=${e.decision_id}">${e.decision_status === 'PROPOSED' ? 'Decide' : 'Decision'} ${icon('arrow-right', 14)}</a>` : html`<a class="btn sm btn-ghost" href="${e.route || '#/tower'}">Open ${icon('arrow-right', 14)}</a>`}
    </div>
  </li>`)}</ul>`;
}

function decisionList(rows) {
  if (!rows.length) {
    return ui.callout({ tone: 'good', title: 'Nothing waiting', body: 'Every proposed decision has been executed or rejected. Reset the demo data to replay the loops.' });
  }
  return html`<ul class="pt-dec">${rows.map((dd) => {
    let impact = {};
    try { impact = JSON.parse(dd.impact_json || '{}'); } catch (e) { impact = {}; }
    const chips = Object.entries(impact).slice(0, 3).map(([kk, v]) => html`<span class="pt-dec-chip">${impactLabel(kk)}: <b>${impactValue(kk, v)}</b></span>`);
    return html`<li><a class="pt-dec-row" href="#/loop?d=${dd.decision_id}">
      <span class="pt-dec-ic">${icon(LOOP_ICON[dd.loop] || 'loop', 17)}</span>
      <span class="pt-dec-main"><span class="pt-dec-loop">${fmt.title(dd.loop)} loop · ${dd.decision_id}</span>
        <span class="pt-dec-title">${dd.title}</span>
        <span class="pt-dec-chips">${chips}</span></span>
      <span class="pt-dec-go">${icon('chevron-right', 16)}</span>
    </a></li>`;
  })}</ul>`;
}

const IMPACT_LABEL = { hold_units: 'Affected units', kit_companions: 'Kit packs held with them', serial_holds: 'Serial holds', lot_holds: 'Lot holds', customers_exposed: 'Customers exposed', recovery_estimate_usd: 'Recovery (est.)',
  packs_protected: 'Packs protected', air_freight_usd: 'Air freight', orders_protected: 'Promises that would slip', messages: 'Messages',
  as_built_corrections: 'As-built fixes', orders: 'Orders', max_slip_days: 'Max slip (days)' };
export function impactLabel(k) { return IMPACT_LABEL[k] || fmt.title(k); }
export function impactValue(k, v) {
  if (typeof v !== 'number') return v;
  return k.endsWith('_usd') ? fmt.usd(v, { compact: v >= 10000 }) : fmt.int(v);
}

function resolvedList(rows) {
  return html`<ul class="pt-res">${rows.map((r) => html`<li>${ui.chip('good', 'Resolved')}<span>${r.title}</span>
    <span class="muted small">${fmt.rel(r.resolved_at)}${r.decision_id ? html` · ${r.decision_id}` : ''}</span></li>`)}</ul>`;
}

// ---------------------------------------------------------------- charts

function outputChart(rows) {
  const pt = (key) => rows.map((r) => ({ x: r.week, y: r[key] || 0 }));
  return charts.line({
    height: 240,
    markers: true,
    series: [
      { name: 'Vehicles built', points: pt('veh') },
      { name: 'CM commit', points: pt('veh_plan'), dashed: true, color: 'var(--series-other)' },
      { name: 'Packs built', points: pt('pk') },
      { name: 'Pack MPS', points: pt('pk_plan'), dashed: true, color: 'var(--series-other)' },
    ],
    yFormat: (v) => fmt.int(v),
  });
}

function deliveryChart(rows) {
  return charts.bar({
    height: 240,
    stacked: true,
    categories: rows.map((r) => fmt.date(r.day)),
    series: [
      { name: 'On promise', values: rows.map((r) => r.ontime || 0) },
      { name: 'Late', values: rows.map((r) => (r.n || 0) - (r.ontime || 0)) },
    ],
    colors: ['var(--good)', 'var(--critical)'],
    yFormat: (v) => fmt.int(v),
  });
}

function assurance(d) {
  const a = d.assurance;
  return html`<div class="pt-assure">
    <div class="pt-feeds">${d.feeds.map((f) => html`<a class="pt-feed" href="${f.route}">
      <span class="pt-feed-name">${f.name}</span>
      <span class="pt-feed-last">${f.last ? fmt.rel(f.last) : 'never'}</span>
      <span class="pt-feed-n">${fmt.int(f.count_7d)} msgs · 7d</span>
    </a>`)}</div>
    <div class="pt-gates">
      <a class="pt-gate" href="#/cm-feed">${ui.chip(d.quarantined ? 'serious' : 'good', d.quarantined ? `${d.quarantined} quarantined` : 'Nothing quarantined')}<span>CM feed quarantine</span></a>
      <a class="pt-gate" href="#/contracts">${ui.chip(a.contracts.failing ? 'warning' : 'good', `${a.contracts.failing || 0} of ${a.contracts.n} failing`)}<span>Data contracts</span></a>
      <a class="pt-gate" href="#/proof">${ui.chip(a.evals.pass === a.evals.n ? 'good' : 'warning', `${a.evals.pass || 0} of ${a.evals.n} passing`)}<span>Eval gates</span></a>
    </div>
  </div>`;
}

const PAGE_CSS = `
.pg-tower { display: grid; gap: 16px; }
.pg-tower .grid { margin: 0; }
.pt-flow-card .card-body { padding-top: 4px; }
.pt-val { font: 600 26px/1 var(--font-ui); fill: var(--ink); }
.pt-valsm { font-weight: 700; fill: var(--ink); }
.pt-lab { font: 600 13px/1 var(--font-cond); fill: var(--ink); letter-spacing: .01em; }
.pt-sub { font: 500 11.5px/1 var(--font-ui); fill: var(--ink-2); }
.pt-det { font: 400 11px/1 var(--font-mono); fill: var(--ink-3); }
.pt-legend text { font: 500 11.5px/1 var(--font-ui); fill: var(--ink-2); }
.pt-stop { cursor: pointer; outline: none; }
.pt-stop:hover circle:last-child, .pt-stop:focus circle:last-child { stroke-width: 5; }
.pt-ex { list-style: none; margin: 0; padding: 0; }
.pt-ex-row { display: grid; grid-template-columns: 104px 1fr auto; gap: 12px; align-items: start; padding: 12px 16px;
  border-top: 1px solid var(--hairline); }
.pt-ex-row:first-child { border-top: 0; }
.pt-ex-title { font-weight: 600; color: var(--ink); }
.pt-ex-detail { color: var(--ink-2); font-size: 13px; margin-top: 2px; }
.pt-ex-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 6px; font-size: 12px; color: var(--ink-3); align-items: center; }
.pt-dec { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.pt-dec-row { display: grid; grid-template-columns: 30px 1fr 18px; gap: 10px; align-items: center; padding: 10px 12px;
  border: 1px solid var(--hairline); border-radius: 10px; color: var(--ink); text-decoration: none; background: var(--surface-2); }
.pt-dec-row:hover { border-color: var(--sign); text-decoration: none; }
.pt-dec-ic { display: inline-flex; width: 30px; height: 30px; border-radius: 8px; align-items: center; justify-content: center;
  background: var(--sign); color: var(--sign-ink); }
.pt-dec-main { display: grid; gap: 2px; min-width: 0; }
.pt-dec-loop { font: 600 11px/1.2 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
.pt-dec-title { font-weight: 600; font-size: 13.5px; }
.pt-dec-chips { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 12px; color: var(--ink-2); }
.pt-dec-go { color: var(--ink-3); }
.pt-res { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.pt-res li { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; font-size: 13px; }
.pt-assure { display: grid; gap: 14px; }
.pt-feeds { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
.pt-feed { display: grid; gap: 2px; padding: 10px 12px; border: 1px solid var(--hairline); border-radius: 10px; color: var(--ink);
  text-decoration: none; background: var(--surface-2); }
.pt-feed:hover { border-color: var(--sign); text-decoration: none; }
.pt-feed-name { font-weight: 600; font-size: 13px; }
.pt-feed-last { font: 500 12px/1.3 var(--font-mono); color: var(--ink-2); }
.pt-feed-n { font-size: 12px; color: var(--ink-3); }
.pt-gates { display: flex; flex-wrap: wrap; gap: 10px 22px; }
.pt-gate { display: inline-flex; gap: 8px; align-items: center; color: var(--ink-2); font-size: 13px; text-decoration: none; }
.pt-gate:hover span:last-child { text-decoration: underline; }
@media (max-width: 700px) {
  .pt-ex-row { grid-template-columns: 1fr; gap: 6px; }
}
`;
