// Quality Loop (QMS): incoming inspection, inline SPC and capability, deviations,
// NCRs and CAPA, and holds. Control limits instead of anecdotes, and every
// quality event linked to the lot, unit, supplier and money it touches.
import { html, raw, esc, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const TABS = [
  { id: 'iqc', label: 'Incoming (IQC)', icon: 'download' },
  { id: 'spc', label: 'Inline & SPC', icon: 'target' },
  { id: 'deviations', label: 'Deviations', icon: 'alert-triangle' },
  { id: 'ncr', label: 'NCRs & CAPA', icon: 'checklist' },
  { id: 'holds', label: 'Holds', icon: 'lock' },
];
const SUPPLIER_NAMES = {};
let S = null;

const r1 = (v) => Math.round(v * 10) / 10;
const crisp = (v) => Math.round(v) + 0.5;
const pctTxt = (x, d = 1) => (x == null ? '—' : `${(x * 100).toFixed(d)}%`);

// ---------------------------------------------------------------------------
// render
// ---------------------------------------------------------------------------
export async function render(el, ctx) {
  injectStyle('page-quality', PAGE_CSS);
  const summary = await api.get('/api/quality/summary');
  let tab = ctx.query.get('tab') || 'iqc';
  if (!TABS.some((t) => t.id === tab)) tab = 'iqc';
  S = { el, ctx, summary, tab, cache: {} };

  el.innerHTML = html`
    ${ui.pageHeader({
      actions: html`<a class="btn sm" href="#/warranty">${icon('receipt', 15)}<span>Field claims</span></a>
        <a class="btn sm" href="#/loop">${icon('loop', 15)}<span>Closed loop</span></a>`,
    })}
    <div class="pg-quality">
      <div class="kpi-row" id="q-kpis">${kpis(summary)}</div>
      <div id="q-tabs"></div>
      <div id="q-pane" class="stack"></div>
    </div>`;

  S.tabs = ui.tabs($('#q-tabs', el), { tabs: tabList(summary), active: tab, onChange: (id) => show(id) });
  wire(el);
  await show(tab, true);
  const cp = ctx.query.get('cp');
  const dev = ctx.query.get('dev');
  if (cp) openCapability(cp);
  else if (dev) openDeviation(dev);
}

export function unmount() {
  S = null;
}

function tabList(s) {
  const c = s.counts || {};
  return TABS.map((t) => ({
    ...t,
    count: t.id === 'iqc' ? c.iqc : t.id === 'deviations' ? c.deviations : t.id === 'ncr' ? c.ncr : t.id === 'holds' ? c.holds : null,
  }));
}

function kpis(s) {
  const judged = s.iqc.accepted + s.iqc.conditional + s.iqc.rejected;
  const pts = (a, b) => (a == null || b == null ? null : `${a >= b ? '+' : '−'}${Math.abs((a - b) * 100).toFixed(1)} pts`);
  const nx = s.next_expiry;
  return html`
    ${ui.kpi({
      label: 'IQC acceptance, 90d', value: judged ? pctTxt(s.iqc.accepted / judged) : '—',
      hint: `${fmt.int(s.iqc.inspected)} lots · ${fmt.int(s.iqc.rejected)} rejected · ${fmt.int(s.iqc.conditional)} sorted`,
    })}
    ${ui.kpi({
      label: 'CM end-of-line FPY, 7d', value: pctTxt(s.s60.fpy), delta: pts(s.s60.fpy, s.s60.prev),
      deltaGood: 'up', spark: s.s60.spark, hint: 'vs prior 7d',
    })}
    ${ui.kpi({
      label: 'Pack end-of-line FPY, 7d', value: pctTxt(s.p50.fpy), delta: pts(s.p50.fpy, s.p50.prev),
      deltaGood: 'up', spark: s.p50.spark, hint: 'vs prior 7d',
    })}
    ${ui.kpi({
      label: 'Open CAPAs', value: fmt.int(s.capa_open),
      hint: `${fmt.int(s.ncr_open)} NCR still open (contained)`,
    })}
    ${ui.kpi({
      label: 'Active deviations', value: fmt.int(s.deviations_active),
      hint: nx ? `${nx.id} expires ${fmt.date(nx.valid_to)} · ${fmt.int(nx.left)} units of allowance left` : 'None active',
      status: nx && nx.days <= 5 ? { tone: 'warning', label: `${nx.days}d left` } : null,
    })}
    ${ui.kpi({
      label: 'Units on hold', value: fmt.int(s.holds_units),
      hint: `${fmt.int(s.holds_active)} active hold${s.holds_active === 1 ? '' : 's'}`,
      status: s.holds_active ? { tone: 'warning', label: 'Held' } : null,
    })}`;
}

async function refreshSummary() {
  if (!S) return;
  S.summary = await api.get('/api/quality/summary');
  $('#q-kpis', S.el).innerHTML = String(kpis(S.summary));
  S.tabs.update(tabList(S.summary));
}

async function show(id, first) {
  if (!S) return;
  S.tab = id;
  if (!first) S.ctx.setQuery({ tab: id === 'iqc' ? null : id, cp: null, dev: null }, { silent: true });
  const pane = $('#q-pane', S.el);
  pane.style.opacity = '.55';
  try {
    if (id === 'spc') await showSPC(pane);
    else if (id === 'deviations') await showDeviations(pane);
    else if (id === 'ncr') await showNCR(pane);
    else if (id === 'holds') await showHolds(pane);
    else await showIQC(pane);
  } catch (err) {
    console.error(err);
    pane.innerHTML = String(ui.errorBox(err));
  }
  pane.style.opacity = '';
}

function wire(el) {
  on(el, 'click', '[data-cap]', (e, t) => {
    if (e.target.closest('a')) return;
    openCapability(t.dataset.cap);
  });
  on(el, 'keydown', '[data-cap]', (e, t) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openCapability(t.dataset.cap);
    }
  });
  on(el, 'click', '[data-release]', (e, b) => releaseHold(b));
  on(el, 'click', '[data-dev]', (e, t) => {
    if (e.target.closest('a')) return;
    openDeviation(t.dataset.dev);
  });
}

// ---------------------------------------------------------------------------
// Incoming inspection
// ---------------------------------------------------------------------------
async function showIQC(pane) {
  const d = S.cache.iqc || (S.cache.iqc = await api.get('/api/quality/iqc'));
  d.suppliers.forEach((s) => { SUPPLIER_NAMES[s.supplier_id] = s.supplier_name; });
  const sup = d.suppliers;
  const legendChips = html`<div class="q-legend">
    <span class="legend-item"><span class="legend-key rect" style="--c:var(--series-other)"></span>Accepted</span>${ui.chip('warning', 'Conditional: sorted, then accepted')}${ui.chip('critical', 'Rejected: return to vendor')}
  </div>`;
  const chart = charts.bar({
    categories: sup.map((s) => s.supplier_name),
    series: [
      { name: 'Accepted', values: sup.map((s) => s.accepted), color: 'var(--series-other)' },
      { name: 'Conditional (sorted)', values: sup.map((s) => s.conditional), color: 'var(--warning)' },
      { name: 'Rejected', values: sup.map((s) => s.rejected), color: 'var(--critical)' },
    ],
    stacked: true, horizontal: true, legend: false, totals: true, yFormat: (v) => fmt.int(v),
    categoryLabel: 'Supplier', ariaLabel: 'Incoming lots by supplier and disposition',
  });

  pane.innerHTML = String(html`
    ${d.pending.length ? ui.callout({
      tone: 'info', title: `${d.pending.length} lot${d.pending.length === 1 ? '' : 's'} waiting on incoming inspection`,
      body: html`${d.pending.map((p) => html`<span class="q-pend">${link.lot(p.lot_id)} ${p.item_name} · ${fmt.int(p.qty_received)} received ${fmt.rel(p.received_at)}</span>`)}`,
    }) : ''}
    <div class="grid">
      <div class="span-7">${ui.card({
        title: 'Incoming lots by supplier',
        subtitle: 'Every lot received at Fremont is sampled on arrival. Accepted lots recede; exceptions carry their status color.',
        body: html`${legendChips}${chart}
          <div class="table-wrap q-sup-wrap"><table class="table dense"><thead><tr><th>Supplier</th><th class="num">Lots</th><th>Acceptance</th><th class="num">Cost of rejects</th></tr></thead>
          <tbody>${sup.map((s) => html`<tr>
            <td>${link.supplier(s.supplier_id, s.supplier_name)}<span class="muted small"> · ${s.items.join(', ')}</span></td>
            <td class="num">${fmt.int(s.lots)}</td>
            <td><div class="q-used">${ui.meter({ value: s.accept_rate || 0, max: 1, tone: s.rejected ? 'warning' : 'good' })}<span class="num small">${pctTxt(s.accept_rate, 0)}</span></div></td>
            <td class="num">${s.cost_usd ? fmt.usd(s.cost_usd) : html`<span class="nil">—</span>`}</td></tr>`)}</tbody></table></div>`,
      })}</div>
      <div class="span-5">${ui.card({
        title: 'What incoming inspection caught, and what it can’t',
        subtitle: 'Each exception links to its NCR, 8D and the money recovered.',
        body: html`<div class="stack">${d.highlights.map(highlight)}${d.latent.filter((l) => l.claims >= 3).map(latent)}</div>`,
      })}</div>
    </div>
    ${ui.card({ title: 'Inspection log', subtitle: 'One row per received lot · sample size by lot size, accept on zero defects (c = 0)', flush: true, body: html`<div id="q-iqc-table"></div>` })}
    <div class="grid">
      <div class="span-5">${ui.card({
        title: 'Sampling plan in use (c = 0)',
        subtitle: 'Sample size grows with lot size; one defect rejects or triggers a 100% sort.',
        body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Lot size received</th><th class="num">Sample</th><th class="num">Accept on</th><th class="num">Lots</th></tr></thead>
          <tbody>${d.plan.map((p) => html`<tr><td class="num" style="text-align:left">${fmt.int(p.lot_min)}${p.lot_max !== p.lot_min ? html` – ${fmt.int(p.lot_max)}` : ''}</td>
            <td class="num">${fmt.int(p.n)}</td><td class="num">0</td><td class="num">${fmt.int(p.lots)}</td></tr>`)}</tbody></table></div>`,
      })}</div>
      <div class="span-7">${ui.card({
        title: 'Passed incoming inspection, failing in the field',
        subtitle: 'Warranty claims traced through genealogy back to the lot. IQC samples OCV/IR at receipt; capacity fade is latent.',
        flush: true,
        body: d.latent.length ? html`<div class="table-wrap"><table class="table">
          <thead><tr><th>Lot</th><th>Supplier</th><th>At receipt</th><th class="num">Field claims</th><th>Claims reported</th></tr></thead>
          <tbody>${d.latent.map((l) => html`<tr>
            <td>${link.lot(l.lot_id)} <span class="mono small muted">${l.item_id}</span></td><td>${link.supplier(l.supplier_id, SUPPLIER_NAMES[l.supplier_id])}</td>
            <td class="small">${fmt.title(l.iqc_status)}${l.n != null ? html` <span class="muted">· ${fmt.int(l.bad)} of ${fmt.int(l.n)} defective</span>` : ''}</td>
            <td class="num strong">${fmt.int(l.claims)}</td><td class="small">${fmt.date(l.first_claim)} → ${fmt.date(l.last_claim)}</td></tr>`)}</tbody></table></div>`
          : ui.empty('No field claims trace back to an OEM-received lot.'),
      })}</div>
    </div>`);

  ui.dataTable($('#q-iqc-table', pane), {
    rows: d.inspections,
    search: true,
    searchPlaceholder: 'Filter by lot, item, supplier…',
    pageSize: 15,
    initialSort: { key: 'received_at', dir: 'desc' },
    columns: [
      { key: 'lot_id', label: 'Lot', render: (r) => link.lot(r.lot_id) },
      { key: 'item_id', label: 'Item', render: (r) => html`<span class="mono small" title="${r.item_name}">${r.item_id}</span>` },
      { key: 'supplier_name', label: 'Supplier', render: (r) => link.supplier(r.supplier_id, r.supplier_name) },
      { key: 'received_at', label: 'Received', render: (r) => html`<span title="${fmt.dt(r.received_at)}">${fmt.date(r.received_at)}</span>` },
      { key: 'lot_qty', label: 'Lot qty', num: true },
      { key: 'n', label: 'Sampled', num: true },
      { key: 'bad', label: 'Defective', num: true, render: (r) => (r.bad ? html`<span class="strong">${fmt.int(r.bad)}</span>` : '0') },
      { key: 'result', label: 'Result', render: (r) => iqcResult(r) },
      { key: 'ncr_no', label: 'NCR', render: (r) => (r.ncr_no ? html`<a class="id-link" href="#/quality?tab=ncr">${r.ncr_no}</a>` : html`<span class="nil">—</span>`) },
      { key: 'cost_usd', label: 'Cost', num: true, render: (r) => (r.cost_usd ? fmt.usd(r.cost_usd) : html`<span class="nil">—</span>`) },
    ],
  });
}

function iqcResult(r) {
  if (r.status === 'OPEN') return ui.statusChip('PENDING', 'Pending');
  if (r.result === 'ACCEPT') return html`<span class="q-accept">${icon('check', 13)}Accepted</span>`;
  const label = r.result === 'REJECT' ? `Rejected · ${fmt.title(r.disposition)}` : `Conditional · ${fmt.title(r.disposition)}`;
  return ui.chip(r.result === 'REJECT' ? 'critical' : 'warning', label);
}

function highlight(h) {
  const tone = h.result === 'REJECT' ? 'critical' : 'warning';
  const verb = h.result === 'REJECT' ? 'rejected' : 'accepted after a 100% sort';
  const cb = (h.chargebacks || [])[0];
  return ui.callout({
    tone,
    title: html`${h.supplier_name} · ${h.item_name} lot ${h.lot_id} ${verb}`,
    body: html`<div>${fmt.int(h.bad)} of ${fmt.int(h.n)} sampled nonconforming (${h.defect_desc || h.defect_code}). Disposition: ${fmt.title(h.disposition)}.</div>
      ${h.root_cause ? html`<div class="small">Root cause: ${h.root_cause}</div>` : ''}
      <div class="q-chain">${link.lot(h.lot_id)}
        ${h.ncr_no ? html`${icon('arrow-right', 13)}<a class="id-link" href="#/quality?tab=ncr">${h.ncr_no}</a>` : ''}
        ${h.capa ? html`${icon('arrow-right', 13)}<a class="id-link" href="#/quality?tab=ncr">${h.capa.capa_id}</a><span class="muted small">${h.capa.d_stage}</span>` : ''}
        ${cb ? html`${icon('arrow-right', 13)}<a class="id-link" href="#/warranty?cb=${encodeURIComponent(cb.chargeback_id)}">${cb.chargeback_id}</a>${ui.statusChip(cb.status)}<span class="small">${fmt.usd(cb.amount_usd)}</span>` : ''}
      </div>`,
  });
}

function latent(l) {
  return ui.callout({
    tone: 'serious',
    title: html`Lot ${l.lot_id} passed IQC and is failing in the field`,
    body: html`<div>${l.bad != null ? `${fmt.int(l.bad)} of ${fmt.int(l.n)} sampled at receipt were defective; ` : ''}${fmt.int(l.claims)} warranty claims now trace back to it through pack genealogy.</div>
      <div class="q-chain"><a class="ent-link" href="#/genealogy?q=${encodeURIComponent(l.lot_id)}">${icon('tree', 14)} Trace the lot forward</a>
        <a class="ent-link" href="#/warranty">${icon('receipt', 14)} See the field signal</a></div>`,
  });
}

// ---------------------------------------------------------------------------
// Inline & SPC
// ---------------------------------------------------------------------------
async function showSPC(pane) {
  const [l1, l2, cap] = await Promise.all([
    S.cache.inL1 || api.get('/api/quality/inline', { line: 'L1' }),
    S.cache.inL2 || api.get('/api/quality/inline', { line: 'L2' }),
    S.cache.cap || api.get('/api/quality/capability'),
  ]);
  S.cache.inL1 = l1;
  S.cache.inL2 = l2;
  S.cache.cap = cap;
  const all = l1;   // trend, pareto and station FPY do not depend on the line filter
  const yMax = Math.max(0.06, ...[l1, l2].flatMap((d) => d.pchart.points.map((p) => Math.max(p.p, p.ucl)))) * 1.12;
  const ooc = [...l2.pchart.points.filter((p) => p.ooc).map((p) => ({ ...p, line: 'L2' })),
    ...l1.pchart.points.filter((p) => p.ooc).map((p) => ({ ...p, line: 'L1' }))].sort((a, b) => a.day.localeCompare(b.day));
  const variable = cap.plans.filter((p) => p.kind === 'VARIABLE');
  const attribute = cap.plans.filter((p) => p.kind !== 'VARIABLE');

  const trend = charts.line({
    series: [
      { name: 'CM line 1', points: all.trend.L1.map((p) => ({ x: p.x, y: p.y })) },
      { name: 'CM line 2', points: all.trend.L2.map((p) => ({ x: p.x, y: p.y })) },
      { name: 'Pack line', points: all.trend.P1.map((p) => ({ x: p.x, y: p.y })) },
    ],
    height: 230, markers: true, yFormat: (v) => fmt.num(v, 2), xLabel: 'Week of', ariaLabel: 'Defects per unit by week',
  });
  const pareto = all.pareto.slice(0, 10);
  const paretoChart = charts.bar({
    categories: pareto.map((p) => p.defect_code),
    series: [{ name: 'Failures, last 30 days', values: pareto.map((p) => p.fails) }],
    horizontal: true, labelWidth: 110, yFormat: (v) => fmt.int(v), categoryLabel: 'Defect code',
    ariaLabel: 'Defect Pareto, last 30 days',
  });

  pane.innerHTML = String(html`
    ${ui.card({
      title: 'End-of-line test (S60): first-pass failures, p-chart by line',
      subtitle: html`Each point is one day's share of vehicles that failed their first end-of-line test (Taichung local days, last 60). Limits are p̄ ± 3σ for that day's sample size and are recomputed after removing out-of-control days.`,
      tableToggle: true,
      body: html`<div class="q-pc-multi">
          <div>${pcHead('Line 1', l1)}${pChart(l1, yMax)}</div>
          <div>${pcHead('Line 2', l2)}${pChart(l2, yMax)}</div>
        </div>
        <div class="q-legend">
          <span class="legend-item"><span class="legend-key line" style="--c:var(--series-1)"></span>Daily failure share</span>
          <span class="legend-item"><span class="legend-key line" style="--c:var(--ink-2)"></span>Center line p̄</span>
          <span class="legend-item"><span class="legend-key line dashed" style="--c:var(--ink-3)"></span>Upper control limit</span>
          ${ui.chip('critical', 'Out of control (beyond UCL)')}
        </div>
        ${ooc.length ? html`<div class="q-ooc">${ooc.map((p) => oocRow(p))}</div>` : ''}`,
    })}
    ${ui.card({
      title: 'Process capability against the control plan',
      subtitle: 'Last 90 days of measurements from the test stations. Cpk uses within-process σ (moving range / 1.128), Ppk the overall σ. Status: ≥ 1.33 capable, 1.00–1.33 marginal, < 1.00 not capable.',
      body: html`<div class="q-cap-grid">${variable.map(capTile)}</div>
        ${attribute.length ? html`<p class="section-title">Attribute checks in the control plan</p>
        <div class="table-wrap"><table class="table dense"><thead><tr><th>Check</th><th>Item</th><th>Method</th><th>Frequency</th><th>Reaction plan</th></tr></thead>
          <tbody>${attribute.map((a) => html`<tr><td class="mono small">${a.characteristic}</td><td>${link.item(a.item_id)}</td>
            <td class="wrap small">${a.method}</td><td class="small">${a.frequency}</td><td class="wrap small">${a.reaction_plan}</td></tr>`)}</tbody></table></div>` : ''}`,
    })}
    <div class="grid">
      <div class="span-7">${ui.card({
        title: 'Inline defects per unit, by week',
        subtitle: 'Every failed test or inspection per unit started, across all stations. The learning curve flattens; L2 is younger.',
        tableToggle: true, body: trend,
      })}</div>
      <div class="span-5">${ui.card({
        title: 'Defect Pareto, last 30 days',
        subtitle: 'Failed station events by defect code, both plants.',
        tableToggle: true,
        body: html`${paretoChart}<p class="small muted q-pareto-note">Top 3 make up ${pctTxt(pareto[Math.min(2, pareto.length - 1)] ? pareto[Math.min(2, pareto.length - 1)].cum_pct : null, 0)} of failures: ${pareto.slice(0, 3).map((p, i) => html`${i ? ', ' : ''}<span class="mono">${p.defect_code}</span> ${p.description ? html`<span class="muted">(${p.description})</span>` : ''}`)}.</p>`,
      })}</div>
    </div>
    ${ui.card({
      title: 'First-pass yield by station, last 14 days',
      subtitle: 'Rolled throughput yield is the product of station first-pass yields: the share of units that make it through every station without a single retest.',
      body: stationFPY(all),
    })}`);
}

function pcHead(label, d) {
  const pc = d.pchart;
  const per = d.per_line[label.endsWith('1') ? 'L1' : 'L2'] || { n: 0, fails: 0 };
  return html`<div class="q-pc-head"><span class="strong">${label}</span>
    <span class="muted small">p̄ ${pctTxt(pc.pbar, 2)}${pc.revised ? ` (revised from ${pctTxt(pc.pbar_initial, 2)})` : ''} · ${fmt.int(per.n)} vehicles first tested</span>
    ${pc.ooc.length ? ui.chip('critical', `${pc.ooc.length} out of control`) : ui.chip('good', 'In control')}</div>`;
}

function oocRow(p) {
  const codes = Object.entries(p.codes || {}).map(([k, v]) => `${k} ×${v}`).join(', ');
  return html`<div class="q-ooc-row">
    ${ui.chip('critical', `${fmt.date(p.day)} · ${p.line}`)}
    <span class="num">${fmt.int(p.fails)} of ${fmt.int(p.n)} failed (${pctTxt(p.p)}) vs UCL ${pctTxt(p.ucl)}</span>
    <span class="mono small muted">${codes}</span>
    <span class="spacer"></span>
    ${p.cause ? html`<span class="small">Assignable cause on record: <a class="id-link" href="#/quality?tab=ncr">${p.cause.qe_id}</a> <span class="muted">${p.cause.root_cause || ''}</span></span>`
      : html`<span class="small">${icon('search', 13)} No NCR on record: investigate with the CM</span>`}
  </div>`;
}

function pChart(d, yMax) {
  const pts = d.pchart.points;
  const pbar = d.pchart.pbar;
  const table = {
    columns: ['Day', 'Tested', 'Failed', 'Failure share', 'UCL', 'Status'],
    rows: pts.map((p) => [fmt.dateLong(p.day), fmt.int(p.n), fmt.int(p.fails), pctTxt(p.p), pctTxt(p.ucl), p.ooc ? 'Out of control' : 'In control']),
    numeric: [true, true, true, true, false],
  };
  return charts.custom({
    height: 210,
    ariaLabel: `p-chart of first-test failures, line ${d.line}`,
    table,
    render: (w, h) => {
      if (!pts.length) return raw('');
      const m = { t: 30, r: 58, b: 26, l: 44 };
      const iw = Math.max(40, w - m.l - m.r);
      const ih = h - m.t - m.b;
      const ms = pts.map((p) => Date.parse(`${p.day}T00:00:00Z`));
      const x0 = Math.min(...ms);
      const x1 = Math.max(...ms);
      const sx = charts.scaleLinear([x0, x1 === x0 ? x0 + 1 : x1], [m.l, m.l + iw]);
      const sy = charts.scaleLinear([0, yMax], [m.t + ih, m.t]);
      let s = '';
      for (const t of charts.niceTicks(0, yMax, 4)) {
        if (t > yMax + 1e-9) continue;
        const y = crisp(sy(t));
        s += `<line class="gridline" x1="${m.l}" x2="${m.l + iw}" y1="${y}" y2="${y}"/>`;
        s += `<text class="tick" x="${m.l - 8}" y="${y}" dy="0.32em" text-anchor="end">${esc(`${Math.round(t * 1000) / 10}%`)}</text>`;
      }
      const every = Math.max(1, Math.ceil(pts.length / Math.max(2, Math.floor(iw / 70))));
      pts.forEach((p, i) => {
        if (i % every) return;
        s += `<text class="tick" x="${r1(sx(ms[i]))}" y="${m.t + ih + 17}" text-anchor="middle">${esc(fmt.date(p.day))}</text>`;
      });
      s += `<line class="baseline" x1="${m.l}" x2="${m.l + iw}" y1="${crisp(sy(0))}" y2="${crisp(sy(0))}"/>`;
      // variable-n control limit as a step line
      let ucl = '';
      pts.forEach((p, i) => {
        const xa = i === 0 ? sx(ms[0]) : (sx(ms[i - 1]) + sx(ms[i])) / 2;
        const xb = i === pts.length - 1 ? sx(ms[i]) : (sx(ms[i]) + sx(ms[i + 1])) / 2;
        const y = r1(sy(p.ucl));
        ucl += `${i ? 'L' : 'M'}${r1(xa)},${y}L${r1(xb)},${y}`;
      });
      s += `<path d="${ucl}" fill="none" style="stroke:var(--ink-3)" stroke-width="1.25" stroke-dasharray="4 3"/>`;
      const cy = crisp(sy(pbar));
      s += `<line x1="${m.l}" x2="${m.l + iw}" y1="${cy}" y2="${cy}" style="stroke:var(--ink-2)" stroke-width="1.25"/>`;
      // limit labels live in the right margin so they never sit on data
      s += `<text class="ref-label" x="${m.l + iw + 6}" y="${cy}" dy="0.32em">${esc(`p̄ ${pctTxt(pbar, 1)}`)}</text>`;
      const lastUcl = pts[pts.length - 1].ucl;
      s += `<text class="ref-label" x="${m.l + iw + 6}" y="${r1(sy(lastUcl))}" dy="0.32em">UCL</text>`;
      const line = pts.map((p, i) => `${i ? 'L' : 'M'}${r1(sx(ms[i]))},${r1(sy(p.p))}`).join('');
      s += `<path class="series-line" d="${line}" style="stroke:var(--series-1)" stroke-width="2"/>`;
      let lastLabel = null;
      pts.forEach((p, i) => {
        const x = r1(sx(ms[i]));
        const y = r1(sy(p.p));
        if (p.ooc) {
          s += `<circle class="marker" cx="${x}" cy="${y}" r="5.5" style="fill:var(--critical)"/>`;
          // short date labels, staggered when two out-of-control days sit close together
          let ly = Math.max(10, y - 12);
          if (lastLabel && Math.abs(x - lastLabel.x) < 64 && Math.abs(ly - lastLabel.y) < 14) ly = lastLabel.y - 14 < 10 ? lastLabel.y + 14 : lastLabel.y - 14;
          const anchor = x > m.l + iw * 0.85 ? 'end' : x < m.l + iw * 0.15 ? 'start' : 'middle';
          s += `<text class="ref-label" x="${x}" y="${r1(ly)}" text-anchor="${anchor}">${esc(fmt.date(p.day))}</text>`;
          lastLabel = { x, y: ly };
        } else {
          s += `<circle class="marker" cx="${x}" cy="${y}" r="3.5" style="fill:var(--series-1)"/>`;
        }
        const codes = Object.entries(p.codes || {}).map(([k, v]) => `${k} ×${v}`).join(', ');
        const tip = [`${fmt.dateLong(p.day)} · line ${d.line}`,
          `${p.fails} of ${p.n} failed first test (${pctTxt(p.p)})`,
          `UCL ${pctTxt(p.ucl)} · ${p.ooc ? 'OUT OF CONTROL' : 'in control'}`,
          codes ? `Defects: ${codes}` : '',
          p.ooc ? (p.cause ? `Assignable cause: ${p.cause.qe_id}` : 'No NCR on record') : ''].filter(Boolean).join('\n');
        s += `<circle cx="${x}" cy="${y}" r="11" fill="transparent" data-tip="${esc(tip)}"/>`;
      });
      return raw(s);
    },
  });
}

function capTile(p) {
  const st = p.stats || {};
  const tone = p.status === 'CAPABLE' ? 'good' : p.status === 'MARGINAL' ? 'warning' : p.status === 'NOT_CAPABLE' ? 'critical' : 'neutral';
  const spec = [p.lsl != null ? `LSL ${p.lsl}` : null, p.target != null ? `target ${p.target}` : null, p.usl != null ? `USL ${p.usl}` : null]
    .filter(Boolean).join(' · ');
  return html`<article class="q-cap" data-cap="${p.cp_id}" tabindex="0" role="button" aria-label="Open ${p.characteristic} capability detail">
    <div class="q-cap-head">
      <div><div class="q-cap-name">${capLabel(p)}</div>
        <div class="muted small">${p.station_code} · ${p.item_name || 'all vehicles'} · <span class="mono">${p.unit}</span></div></div>
      ${ui.chip(tone, fmt.title(p.status))}
    </div>
    <div class="q-cap-nums">
      <div><span class="q-cap-big">${st.cpk != null ? fmt.num(st.cpk, 2) : '—'}</span><span class="muted small">Cpk</span></div>
      <div><span class="q-cap-mid">${st.ppk != null ? fmt.num(st.ppk, 2) : '—'}</span><span class="muted small">Ppk</span></div>
      <div><span class="q-cap-mid">${fmt.int(st.out)}</span><span class="muted small">out of spec</span></div>
    </div>
    ${hist(p)}
    <div class="q-cap-foot small muted"><span>${spec}</span><span class="num">n ${fmt.int(st.n)} · x̄ ${fmt.num(st.mean, 2)} · σ ${fmt.num(st.sd_within, 3)}</span></div>
  </article>`;
}

function capLabel(p) {
  const names = {
    motor_db: 'Motor noise', brake_nm: 'Brake torque', leak_ccm: 'Water-test leak rate',
    capacity_ah: 'Pack capacity', ir_mohm: 'Internal resistance',
  };
  const base = names[p.characteristic] || p.characteristic;
  return p.item_id === 'PK-STD' ? `${base} · Standard` : p.item_id === 'PK-LRG' ? `${base} · Large` : base;
}

function hist(p) {
  const bins = p.hist || [];
  if (!bins.length) return '';
  const table = {
    columns: ['Bin', 'Measurements'],
    rows: bins.map((b) => [`${fmt.num(b.x0, 2)} – ${fmt.num(b.x1, 2)}`, fmt.int(b.count)]),
    numeric: [true],
  };
  return charts.custom({
    height: 74,
    ariaLabel: `Histogram of ${p.characteristic} against specification limits`,
    table,
    render: (w, h) => {
      const m = { t: 12, r: 4, b: 4, l: 4 };
      const iw = w - m.l - m.r;
      const ih = h - m.t - m.b;
      const lo = bins[0].x0;
      const hi = bins[bins.length - 1].x1;
      const maxC = Math.max(1, ...bins.map((b) => b.count));
      const sx = charts.scaleLinear([lo, hi], [m.l, m.l + iw]);
      const sy = charts.scaleLinear([0, maxC], [m.t + ih, m.t]);
      let s = '';
      for (const b of bins) {
        const x = sx(b.x0) + 1;
        const bw = Math.max(1, sx(b.x1) - sx(b.x0) - 2);
        const outside = (p.lsl != null && b.x1 <= p.lsl) || (p.usl != null && b.x0 >= p.usl);
        const bh = b.count ? Math.max(1.5, sy(0) - sy(b.count)) : 0;
        if (bh) s += `<rect x="${r1(x)}" y="${r1(sy(0) - bh)}" width="${r1(bw)}" height="${r1(bh)}" rx="1.5" style="fill:${outside ? 'var(--critical)' : 'var(--series-1)'}"/>`;
        s += `<rect x="${r1(sx(b.x0))}" y="${m.t}" width="${r1(Math.max(1, sx(b.x1) - sx(b.x0)))}" height="${ih}" fill="transparent" data-tip="${esc(`${fmt.num(b.x0, 2)} – ${fmt.num(b.x1, 2)} ${p.unit}\n${b.count} measurement${b.count === 1 ? '' : 's'}${outside ? ' · outside spec' : ''}`)}"/>`;
      }
      s += `<line class="baseline" x1="${m.l}" x2="${m.l + iw}" y1="${crisp(sy(0))}" y2="${crisp(sy(0))}"/>`;
      for (const [v, lab, col] of [[p.lsl, 'LSL', 'var(--critical)'], [p.usl, 'USL', 'var(--critical)'], [p.target, 'T', 'var(--ink-3)']]) {
        if (v == null || v < lo || v > hi) continue;
        const x = crisp(sx(v));
        s += `<line x1="${x}" x2="${x}" y1="${m.t - 2}" y2="${m.t + ih}" style="stroke:${col}" stroke-width="1.25"/>`;
        s += `<text class="tick" x="${x}" y="${m.t - 3}" text-anchor="middle">${lab}</text>`;
      }
      return raw(s);
    },
  });
}

function stationFPY(d) {
  const lines = [['CM-TXG', 'L1', 'CM line 1 · Taichung'], ['CM-TXG', 'L2', 'CM line 2 · Taichung'], ['OEM-FRE', 'P1', 'Pack line · Fremont']];
  return html`<div class="q-fpy">${lines.map(([site, line, label]) => {
    const rows = d.stations.filter((r) => r.site_id === site && r.line === line);
    if (!rows.length) return '';
    return html`<div class="q-fpy-line">
      <div class="q-fpy-head"><span class="strong">${label}</span><span class="muted small">RTY</span><span class="q-fpy-rty">${pctTxt(d.rty[line])}</span></div>
      <table class="table dense"><tbody>${rows.map((r) => html`<tr>
        <td class="mono small">${r.code}</td><td class="small wrap">${r.name}</td>
        <td class="num small">${fmt.int(r.units)}</td>
        <td class="q-fpy-bar">${ui.meter({ value: r.fpy, max: 1, tone: r.fpy < 0.97 ? 'warning' : 'good' })}</td>
        <td class="num small strong">${pctTxt(r.fpy)}</td></tr>`)}</tbody></table></div>`;
  })}</div>`;
}

async function openCapability(cpId) {
  const body = ui.drawer.open({ title: 'Capability', subtitle: cpId, body: ui.loading('Loading measurements'), width: 680 });
  S && S.ctx.setQuery({ cp: cpId, dev: null }, { silent: true });
  try {
    const d = await api.get(`/api/quality/capability/${encodeURIComponent(cpId)}`);
    const p = d.plan;
    const st = d.stats || {};
    const lim = d.limits;
    const refs = [];
    if (lim) {
      refs.push({ y: lim.center, label: `x̄ ${fmt.num(lim.center, 2)}`, color: 'var(--ink-2)' });
      refs.push({ y: lim.ucl, label: `UCL ${fmt.num(lim.ucl, 2)}`, color: 'var(--ink-3)' });
      refs.push({ y: lim.lcl, label: `LCL ${fmt.num(lim.lcl, 2)}`, color: 'var(--ink-3)' });
    }
    if (p.lsl != null) refs.push({ y: p.lsl, label: `LSL ${p.lsl}`, tone: 'critical' });
    if (p.usl != null) refs.push({ y: p.usl, label: `USL ${p.usl}`, tone: 'critical' });
    const ys = d.points.map((x) => x.v).concat(refs.map((r) => r.y));
    const lo = Math.min(...ys);
    const hi = Math.max(...ys);
    const pad = (hi - lo) * 0.08 || 1;
    const chart = charts.line({
      series: [{ name: capLabel(p), points: d.points.map((x) => ({ x: x.t, y: x.v, flag: x.flag })) }],
      height: 250, zero: false, yMin: lo - pad, yMax: hi + pad, refLines: refs,
      yFormat: (v) => fmt.num(v, 1), tooltipY: (v) => `${fmt.num(v, 2)} ${p.unit}`,
      tooltipX: (ms) => fmt.dt(new Date(ms).toISOString()), ariaLabel: 'Individuals chart',
    });
    const outOfSpec = d.points.filter((x) => x.out_of_spec).length;
    const beyond = d.points.filter((x) => x.flag).length;
    const tone = d.status === 'CAPABLE' ? 'good' : d.status === 'MARGINAL' ? 'warning' : d.status === 'NOT_CAPABLE' ? 'critical' : 'neutral';
    ui.drawer.open({
      title: `${capLabel(p)} · ${p.station_code}`,
      subtitle: html`${ui.chip(tone, fmt.title(d.status))}<span class="mono">${p.cp_id}</span><span>${p.item_name || 'all vehicles'}</span>`,
      width: 680,
      body: html`
        <dl class="kv">
          <dt>Specification</dt><dd>${[p.lsl != null ? `LSL ${p.lsl}` : null, p.target != null ? `target ${p.target}` : null, p.usl != null ? `USL ${p.usl}` : null].filter(Boolean).join(' · ')} ${p.unit}</dd>
          <dt>Method</dt><dd>${p.method} · ${p.frequency}</dd>
          <dt>Capability</dt><dd class="num">Cpk ${fmt.num(st.cpk, 2)} · Ppk ${fmt.num(st.ppk, 2)}${st.cp != null ? ` · Cp ${fmt.num(st.cp, 2)}` : ''}</dd>
          <dt>Process</dt><dd class="num">n ${fmt.int(d.n_total)} (90 days) · mean ${fmt.num(st.mean, 3)} · σ within ${fmt.num(st.sd_within, 3)} · σ overall ${fmt.num(st.sd_overall, 3)}</dd>
          <dt>Out of spec</dt><dd class="num">${fmt.int(st.out)} measurements (${pctTxt(st.pct_out, 2)})</dd>
        </dl>
        ${ui.card({
          title: 'Individuals chart, last 160 measurements',
          subtitle: `Control limits x̄ ± 2.66 × MR̄. ${beyond} point${beyond === 1 ? '' : 's'} beyond control limits, ${outOfSpec} beyond spec in this window.`,
          tableToggle: true, body: chart,
        })}
        ${d.by_line.length > 1 ? html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Line</th><th class="num">n</th><th class="num">Mean</th><th class="num">Cpk</th><th class="num">Ppk</th><th>Status</th></tr></thead>
          <tbody>${d.by_line.map((b) => html`<tr><td class="mono">${b.line}</td><td class="num">${fmt.int(b.n)}</td><td class="num">${fmt.num(b.mean, 2)}</td>
            <td class="num">${fmt.num(b.cpk, 2)}</td><td class="num">${fmt.num(b.ppk, 2)}</td><td>${ui.statusChip(b.status === 'CAPABLE' ? 'GOOD' : b.status === 'MARGINAL' ? 'WARNING' : 'CRITICAL', fmt.title(b.status))}</td></tr>`)}</tbody></table></div>` : ''}
        ${ui.callout({ tone: 'info', title: 'Reaction plan', body: p.reaction_plan })}`,
    });
  } catch (err) {
    ui.drawer.open({ title: 'Capability', body: ui.errorBox(err) });
  }
}

// ---------------------------------------------------------------------------
// Deviations
// ---------------------------------------------------------------------------
async function showDeviations(pane) {
  const d = S.cache.dev || (S.cache.dev = await api.get('/api/quality/deviations'));
  const active = d.deviations.filter((x) => x.effective_status === 'APPROVED');
  pane.innerHTML = String(html`
    ${active.map((x) => ui.callout({
      tone: x.days_left <= 5 ? 'warning' : 'info',
      title: html`${x.deviation_id} expires ${fmt.dateLong(x.valid_to)} (${x.days_left} day${x.days_left === 1 ? '' : 's'}) with ${fmt.int(x.remaining)} of ${fmt.int(x.qty_limit)} units of allowance left`,
      body: html`<div>${x.title}. ${x.reason}.</div>
        ${x.item_id === 'BMS-A' ? html`<div>When it lapses, every pack needs rev B, and Pinecrest's rev B promise has slipped. The material plan shows the line-stop date this creates; the closed loop proposes extending the deviation alongside an air expedite.</div>
          <div class="q-chain"><a class="ent-link" href="#/mrp?item=BMS-B">${icon('grid', 14)} BMS-B material plan</a><a class="ent-link" href="#/loop">${icon('loop', 14)} Proposed decision</a></div>` : ''}`,
    }))}
    ${ui.card({ title: 'Deviations', subtitle: 'Approved exceptions to the drawing or process, each with a quantity limit and an expiry. Usage is traced from genealogy where the part is traceable.', flush: true, body: html`<div id="q-dev-table"></div>` })}`);
  ui.dataTable($('#q-dev-table', pane), {
    rows: d.deviations,
    pageSize: 0,
    onRowClick: (r) => openDeviation(r.deviation_id),
    columns: [
      { key: 'deviation_id', label: 'Deviation', render: (r) => html`<span class="id-link">${r.deviation_id}</span>` },
      { key: 'title', label: 'What is allowed', wrap: true, render: (r) => html`<span>${r.title}</span>` },
      { key: 'item_id', label: 'Item', render: (r) => link.item(r.item_id) },
      { key: 'supplier_name', label: 'Supplier', render: (r) => (r.supplier_id ? link.supplier(r.supplier_id, r.supplier_name) : html`<span class="nil">—</span>`) },
      { key: 'site_id', label: 'Site', render: (r) => html`<span class="mono small">${r.site_id}</span>` },
      { key: 'qty_used', label: 'Used / limit', value: (r) => r.qty_used / r.qty_limit, render: (r) => html`<div class="q-used">${ui.meter({ value: r.qty_used, max: r.qty_limit, tone: r.qty_used / r.qty_limit >= 0.9 ? 'warning' : undefined })}<span class="num small">${fmt.int(r.qty_used)} / ${fmt.int(r.qty_limit)}</span></div>` },
      { key: 'valid_to', label: 'Valid', render: (r) => html`<span class="small">${fmt.date(r.valid_from)} → ${fmt.date(r.valid_to)}</span>` },
      { key: 'days_left', label: 'Expiry', num: true, render: (r) => (r.effective_status === 'APPROVED' ? (r.days_left <= 5 ? ui.chip('warning', `${r.days_left}d left`) : html`<span class="num">${r.days_left}d</span>`) : html`<span class="nil">—</span>`) },
      { key: 'effective_status', label: 'Status', render: (r) => ui.statusChip(r.effective_status) },
      { key: 'risk', label: 'Risk', render: (r) => ui.chip(r.risk === 'HIGH' ? 'critical' : r.risk === 'MEDIUM' ? 'warning' : 'neutral', fmt.title(r.risk), { icon: false }) },
      { key: 'eco_id', label: 'ECO', render: (r) => (r.eco_id ? html`<a class="id-link" href="#/suppliers?tab=eco&eco=${encodeURIComponent(r.eco_id)}">${r.eco_id}</a>` : html`<span class="nil">—</span>`) },
    ],
  });
}

async function openDeviation(id) {
  const d = S.cache.dev || (S.cache.dev = await api.get('/api/quality/deviations'));
  const x = d.deviations.find((v) => v.deviation_id === id);
  if (!x) return;
  S.ctx.setQuery({ dev: id, cp: null }, { silent: true });
  const usage = x.usage || [];
  const chart = usage.length ? charts.bar({
    categories: usage.map((u) => u.day),
    categoryFormat: (c) => fmt.date(c),
    series: [{ name: 'Used', values: usage.map((u) => (x.item_id === 'TIR-1' ? u.units : u.qty)) }],
    height: 170, yFormat: (v) => fmt.int(v), valueLabels: usage.length <= 12, categoryLabel: 'Day',
    ariaLabel: 'Deviation usage by day',
  }) : ui.empty('Usage for this deviation is reported by the site, not traceable to individual installs.');
  ui.drawer.open({
    title: `${x.deviation_id} · ${x.title}`,
    subtitle: html`${ui.statusChip(x.effective_status)}${ui.chip(x.risk === 'HIGH' ? 'critical' : x.risk === 'MEDIUM' ? 'warning' : 'neutral', `${fmt.title(x.risk)} risk`, { icon: false })}`,
    width: 620,
    body: html`
      <dl class="kv">
        <dt>Item</dt><dd>${link.item(x.item_id)} ${x.item_name}</dd>
        <dt>Supplier</dt><dd>${x.supplier_id ? link.supplier(x.supplier_id, x.supplier_name) : '—'}</dd>
        <dt>Site</dt><dd>${x.site_name || x.site_id}</dd>
        <dt>Why</dt><dd>${x.reason}</dd>
        <dt>Allowance</dt><dd>${ui.meter({ value: x.qty_used, max: x.qty_limit, tone: x.qty_used / x.qty_limit >= 0.9 ? 'warning' : undefined })}<div class="small num" style="margin-top:4px">${fmt.int(x.qty_used)} used of ${fmt.int(x.qty_limit)} · ${fmt.int(x.remaining)} left</div></dd>
        <dt>Valid</dt><dd>${fmt.dateLong(x.valid_from)} → ${fmt.dateLong(x.valid_to)}${x.effective_status === 'APPROVED' ? ` (${x.days_left} days left)` : ''}</dd>
        <dt>Requested by</dt><dd>${x.requested_by || '—'}</dd>
        <dt>Approved by</dt><dd>${x.approved_by || '—'}${x.approved_at ? ` · ${fmt.date(x.approved_at)}` : ''}</dd>
        ${x.eco_id ? html`<dt>Engineering change</dt><dd><a class="id-link" href="#/suppliers?tab=eco&eco=${encodeURIComponent(x.eco_id)}">${x.eco_id}</a> ${x.eco_title || ''}</dd>` : ''}
      </dl>
      ${ui.card({ title: 'Usage traced from genealogy', subtitle: x.usage_basis, body: chart })}
      ${x.quality_events.length ? ui.card({
        title: 'Linked quality events', flush: true,
        body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Event</th><th>Kind</th><th>Lot</th><th>Result</th><th>Disposition</th><th>Detected</th></tr></thead>
          <tbody>${x.quality_events.map((e) => html`<tr><td class="mono small">${e.qe_id}</td><td class="small">${fmt.title(e.kind)}</td><td>${link.lot(e.lot_id)}</td>
            <td>${ui.statusChip(e.result)}</td><td class="small">${fmt.title(e.disposition)}</td><td class="small">${fmt.date(e.detected_at)}</td></tr>`)}</tbody></table></div>`,
      }) : ''}`,
  });
}

// ---------------------------------------------------------------------------
// NCRs & CAPA
// ---------------------------------------------------------------------------
async function showNCR(pane) {
  const d = S.cache.ncr || (S.cache.ncr = await api.get('/api/quality/ncr'));
  pane.innerHTML = String(html`
    ${ui.card({
      title: 'Corrective actions (8D)',
      subtitle: 'Each CAPA carries its discipline stage, owner and due date. D3 contains, D4 finds root cause, D5–D7 fix and prevent, D8 closes.',
      body: html`<div class="q-capa-grid">${d.capas.map(capaCard)}</div>`,
    })}
    ${ui.card({ title: 'Nonconformances and quality events', subtitle: 'Inline NCRs, incoming rejects and audits, with root cause and cost.', flush: true, body: html`<div id="q-ncr-table"></div>` })}`);
  ui.dataTable($('#q-ncr-table', pane), {
    rows: d.ncrs,
    pageSize: 0,
    onRowClick: (r) => openNCR(r, d),
    columns: [
      { key: 'qe_id', label: 'Event', render: (r) => html`<span class="id-link">${r.qe_id}</span>` },
      { key: 'kind', label: 'Kind', render: (r) => html`<span class="small">${fmt.title(r.kind)}</span>` },
      { key: 'site_id', label: 'Site', render: (r) => html`<span class="mono small">${r.site_id}</span>` },
      { key: 'item_id', label: 'Item', render: (r) => link.item(r.item_id) },
      { key: 'defect_code', label: 'Defect', wrap: true, render: (r) => html`<span class="mono small">${r.defect_code}</span> <span class="muted small">${r.defect_desc || ''}</span>` },
      { key: 'bad', label: 'Defective / checked', num: true, value: (r) => r.bad, render: (r) => `${fmt.int(r.bad)} / ${fmt.int(r.n)}` },
      { key: 'disposition', label: 'Disposition', render: (r) => html`<span class="small">${fmt.title(r.disposition)}</span>` },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
      { key: 'detected_at', label: 'Detected', render: (r) => html`<span class="small">${fmt.date(r.detected_at)}</span>` },
      { key: 'cost_usd', label: 'Cost', num: true, render: (r) => (r.cost_usd ? fmt.usd(r.cost_usd) : html`<span class="nil">—</span>`) },
    ],
  });
}

function d8(stage, closed) {
  const n = +String(stage || 'D0').slice(1);
  return html`<div class="q-d8" aria-label="8D stage ${stage}">${[1, 2, 3, 4, 5, 6, 7, 8].map((i) => html`<span class="${i < n || closed ? 'done' : i === n ? 'cur' : ''}">D${i}</span>`)}</div>`;
}

function capaCard(c) {
  const closed = c.status === 'CLOSED';
  return html`<article class="q-capa">
    <div class="row top"><div class="q-capa-title"><span class="mono small muted">${c.capa_id} · ${c.qe_id}</span><div class="strong">${c.title}</div></div>
      <span class="spacer"></span>${ui.statusChip(c.status)}</div>
    ${d8(c.d_stage, closed)}
    <dl class="kv small">
      <dt>Supplier</dt><dd>${c.supplier_id ? link.supplier(c.supplier_id, c.supplier_name) : 'CM process'}</dd>
      <dt>Containment</dt><dd>${c.containment || '—'}</dd>
      <dt>Root cause</dt><dd>${c.root_cause || html`<span class="muted">not yet confirmed</span>`}</dd>
      <dt>Corrective</dt><dd>${c.corrective || html`<span class="muted">open</span>`}</dd>
      ${c.preventive ? html`<dt>Preventive</dt><dd>${c.preventive}</dd>` : ''}
      <dt>Owner · due</dt><dd>${c.owner} · ${closed ? html`closed ${fmt.date(c.closed_at)}` : html`${fmt.date(c.due_date)} ${c.overdue ? ui.chip('serious', 'Overdue') : html`<span class="muted">(${c.days_to_due}d)</span>`}`}</dd>
    </dl>
  </article>`;
}

function openNCR(r, d) {
  const capa = d.capas.find((c) => c.qe_id === r.qe_id);
  ui.drawer.open({
    title: `${r.qe_id} · ${r.defect_desc || r.defect_code}`,
    subtitle: html`${ui.statusChip(r.status)}<span>${fmt.title(r.kind)} · ${r.site_id}</span>`,
    width: 600,
    body: html`<dl class="kv">
        <dt>Item</dt><dd>${link.item(r.item_id)} ${r.item_name || ''}</dd>
        ${r.lot_id ? html`<dt>Lot</dt><dd>${link.lot(r.lot_id)}</dd>` : ''}
        <dt>Supplier</dt><dd>${r.supplier_id ? link.supplier(r.supplier_id, r.supplier_name) : 'CM / OEM process'}</dd>
        <dt>Found</dt><dd>${fmt.int(r.bad)} of ${fmt.int(r.n)} · ${fmt.dateLong(r.detected_at)}</dd>
        <dt>Disposition</dt><dd>${fmt.title(r.disposition)}</dd>
        <dt>Root cause</dt><dd>${r.root_cause || '—'}</dd>
        ${r.ecos.length ? html`<dt>Fixed by</dt><dd>${r.ecos.map((e) => html`<a class="id-link" href="#/suppliers?tab=eco&eco=${encodeURIComponent(e)}">${e}</a> `)}</dd>` : ''}
        ${r.deviation_id ? html`<dt>Deviation</dt><dd><a class="id-link" href="#/quality?tab=deviations&dev=${encodeURIComponent(r.deviation_id)}">${r.deviation_id}</a></dd>` : ''}
        <dt>Cost of quality</dt><dd>${r.cost_usd ? fmt.usd(r.cost_usd) : '—'}</dd>
        ${r.closed_at ? html`<dt>Closed</dt><dd>${fmt.dateLong(r.closed_at)}</dd>` : ''}
      </dl>
      ${capa ? html`<p class="section-title">Corrective action</p>${capaCard(capa)}` : ''}`,
  });
}

// ---------------------------------------------------------------------------
// Holds
// ---------------------------------------------------------------------------
async function showHolds(pane) {
  const d = await api.get('/api/quality/holds');
  pane.innerHTML = String(html`
    ${ui.callout({
      tone: 'info', title: 'Holds are the containment step of every quality loop',
      body: html`A hold placed here reaches the site that has the unit: the 3PL's WMS, the CM's MES or our pack line. Containment holds proposed by the closed loop (for example a suspect cell lot) land here with their decision attached, and releasing one writes the instruction back out.
        <div class="q-chain"><a class="ent-link" href="#/loop">${icon('loop', 14)} Closed Loop</a></div>`,
    })}
    ${d.groups.length ? html`<div class="q-legend">${d.groups.map((g) => html`<span class="q-group">${ui.chip('warning', `${g.count} held`)}<span class="small">${g.decision_title || g.reason}</span>${g.decision_id ? html`<a class="id-link" href="#/loop">${g.decision_id}</a>` : ''}</span>`)}</div>` : ''}
    ${ui.card({ title: `Active holds (${d.active.length})`, flush: true, body: html`<div id="q-holds-active"></div>` })}
    ${d.released.length ? ui.card({ title: 'Released', subtitle: 'Most recent first', flush: true, body: html`<div id="q-holds-released"></div>` }) : ''}`);
  const cols = [
    { key: 'hold_id', label: 'Hold', render: (r) => html`<span class="mono small">${r.hold_id}</span>` },
    { key: 'ref', label: 'On', value: (r) => r.serial || r.lot_id, render: (r) => (r.serial ? link.serial(r.serial) : link.lot(r.lot_id)) },
    { key: 'item_id', label: 'Item', render: (r) => (r.item_id ? html`<span class="mono small">${r.item_id}</span>` : html`<span class="nil">—</span>`) },
    { key: 'site_name', label: 'Where', render: (r) => html`<span class="small">${r.site_name || r.site_id || '—'}</span>` },
    { key: 'reason', label: 'Reason', wrap: true },
    { key: 'placed_at', label: 'Placed', render: (r) => html`<span class="small" title="${fmt.dt(r.placed_at)}">${fmt.rel(r.placed_at)}</span>` },
    { key: 'decision_id', label: 'Decision', render: (r) => (r.decision_id ? html`<a class="id-link" href="#/loop">${r.decision_id}</a>` : html`<span class="muted small">3PL receipt</span>`) },
  ];
  ui.dataTable($('#q-holds-active', pane), {
    rows: d.active, pageSize: 20, empty: 'No active holds.',
    columns: [...cols, { key: 'act', label: '', sortable: false, render: (r) => html`<button class="btn sm" type="button" data-release="${r.hold_id}">${icon('check', 14)}<span>Release</span></button>` }],
  });
  if (d.released.length) {
    ui.dataTable($('#q-holds-released', pane), {
      rows: d.released, pageSize: 10,
      columns: [...cols, { key: 'released_at', label: 'Released', render: (r) => html`<span class="small">${fmt.dt(r.released_at)}</span>` }],
    });
  }
}

const releaseTimers = new WeakMap();
async function releaseHold(btn) {
  const id = btn.dataset.release;
  if (!btn.classList.contains('confirm')) {
    btn.classList.add('confirm', 'btn-danger');
    btn.querySelector('span').textContent = 'Confirm release';
    releaseTimers.set(btn, setTimeout(() => {
      btn.classList.remove('confirm', 'btn-danger');
      const sp = btn.querySelector('span');
      if (sp) sp.textContent = 'Release';
    }, 4000));
    return;
  }
  clearTimeout(releaseTimers.get(btn));
  btn.disabled = true;
  try {
    const res = await api.post(`/api/quality/holds/${encodeURIComponent(id)}/release`, { note: 'Disposition complete: inspected and cleared' });
    ui.toast(`Hold released. ${res.decision_id} logged; release sent to ${fmt.title(res.target_system)}.`, 'good');
    window.dispatchEvent(new Event('ops:meta-changed'));
    await refreshSummary();
    await showHolds($('#q-pane', S.el));
  } catch (err) {
    btn.disabled = false;
    ui.toast(`Release failed: ${err.message}`, 'critical', 6000);
  }
}

// ---------------------------------------------------------------------------
const PAGE_CSS = `
.pg-quality .q-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin: 0 0 10px; font-size: 12px; color: var(--ink-2); }
.pg-quality .q-group { display: inline-flex; align-items: center; gap: 8px; }
.pg-quality .q-accept { display: inline-flex; align-items: center; gap: 5px; color: var(--ink-3); font-size: 12.5px; }
.pg-quality .q-sup-wrap { margin-top: 12px; }
.pg-quality .q-used { display: flex; align-items: center; gap: 8px; min-width: 150px; }
.pg-quality .q-used .meter { flex: 1; min-width: 70px; }
.pg-quality .q-chain { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-top: 8px; color: var(--ink-3); }
.pg-quality .q-chain .ent-link { display: inline-flex; align-items: center; gap: 5px; margin-right: 10px; }
.pg-quality .q-pend { display: inline-flex; gap: 6px; align-items: center; margin-right: 14px; }
.pg-quality .q-pc-multi { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.pg-quality .q-pc-head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin-bottom: 6px; }
.pg-quality .q-ooc { display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
.pg-quality .q-ooc-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; padding: 8px 10px; border: 1px solid var(--hairline); border-radius: 8px; background: var(--surface-2); font-size: 13px; }
.pg-quality .q-cap-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; }
.pg-quality .q-cap { display: flex; flex-direction: column; gap: 8px; padding: 12px 12px 10px; border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface); cursor: pointer; transition: border-color .12s, box-shadow .12s; min-width: 0; }
.pg-quality .q-cap:hover { border-color: var(--hairline-strong); box-shadow: var(--shadow-md); }
.pg-quality .q-cap-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.pg-quality .q-cap-name { font: 600 14.5px/1.2 var(--font-cond); }
.pg-quality .q-cap-nums { display: flex; gap: 18px; align-items: baseline; }
.pg-quality .q-cap-nums > div { display: flex; flex-direction: column; gap: 1px; }
.pg-quality .q-cap-big { font: 600 26px/1 var(--font-ui); }
.pg-quality .q-cap-mid { font: 600 17px/1.2 var(--font-ui); }
.pg-quality .q-cap-foot { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 10px; }
.pg-quality .q-pareto-note { margin-top: 6px; }
.pg-quality .q-fpy { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
.pg-quality .q-fpy-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.pg-quality .q-fpy-head .strong { flex: 1; }
.pg-quality .q-fpy-rty { font: 600 18px/1 var(--font-ui); }
.pg-quality .q-fpy-bar { width: 70px; }
.pg-quality .q-capa-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.pg-quality .q-capa, .drawer .q-capa { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px; border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface); min-width: 0; }
.pg-quality .q-capa-title, .drawer .q-capa-title { min-width: 0; }
.q-d8 { display: flex; gap: 3px; }
.q-d8 span { flex: 1; min-width: 0; height: 22px; display: flex; align-items: center; justify-content: center; border-radius: 4px; border: 1px solid var(--hairline); background: var(--surface-2); color: var(--ink-3); font: 600 10.5px/1 var(--font-cond); letter-spacing: .02em; }
.q-d8 span.done { background: color-mix(in srgb, var(--good) 16%, var(--surface)); border-color: color-mix(in srgb, var(--good) 35%, transparent); color: var(--ink); }
.q-d8 span.cur { background: var(--sign); border-color: var(--sign); color: var(--sign-ink); }
@media (max-width: 900px) {
  .pg-quality .q-pc-multi { grid-template-columns: minmax(0, 1fr); }
}
`;
