// Contracts & Recon: the invariants the data must satisfy, written as SQL and run
// against the live tables, and the places where two systems describe the same
// physical thing and have to agree. A contract that fails is a row you can open;
// a reconciliation that disagrees says why, or says who has to go and look.
import { html, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

let S = null;

const LAYERS = [
  { id: 'LANDING', label: 'Landing', hint: 'raw payloads as received' },
  { id: 'CORE', label: 'Core', hint: 'the canonical model' },
  { id: 'ACTION', label: 'Action', hint: 'what the system wrote' },
];
const SEV_TONE = { CRITICAL: 'critical', SERIOUS: 'serious', WARNING: 'warning' };
const RECON_TONE = {
  MATCH: ['good', 'Match'], EXPLAINED: ['info', 'Explained'], OPEN: ['serious', 'Open'], NO_REPORT: ['warning', 'No report'],
  NOT_POSTED: ['neutral', 'Not posted'], EXCEPTION: ['warning', 'Damaged'],
};
const SERIAL_COLS = new Set(['serial', 'parent_serial', 'child_serial', 'vehicle', 'as_built', 'eol_read']);

// ---------------------------------------------------------------------------
export async function render(el, ctx) {
  injectStyle('page-contracts', PAGE_CSS);
  S = { el, ctx, tab: ctx.query.get('tab') === 'recon' ? 'recon' : 'contracts', filter: 'ALL' };
  el.innerHTML = String(html`${ui.pageHeader({})}${ui.loading('Loading contracts and reconciliations')}`);
  const [list, recon] = await Promise.all([api.get('/api/contracts/list'), api.get('/api/contracts/recon')]);
  if (!S) return;
  S.list = list;
  S.recon = recon;
  draw();
  wire();
  const c = ctx.query.get('contract');
  if (c) openContract(c);
}

export function unmount() {
  if (S && S.offDrawer) S.offDrawer();
  S = null;
}

function wire() {
  on(S.el, 'click', '[data-action=run-all]', (e, b) => runAll(b));
  on(S.el, 'click', '[data-open-contract]', (e, t) => { e.preventDefault(); openContract(t.dataset.openContract); });
  on(S.el, 'click', '[data-recon-rows]', (e, b) => openRecon(b.dataset.reconRows));
  on(S.el, 'click', '[data-layer]', (e, b) => { S.filter = S.filter === b.dataset.layer ? 'ALL' : b.dataset.layer; drawTab(); });
  S.offDrawer = on(document.body, 'click', '.drawer [data-action=run-one]', (e, b) => runOne(b));
}

// ---------------------------------------------------------------------------
function draw() {
  const sum = S.list.summary;
  const rs = S.recon.summary;
  const nRecon = S.recon.recons.length;
  S.el.innerHTML = String(html`
    ${ui.pageHeader({
      actions: html`${ui.button({ label: 'Run all contracts', icon: 'play', variant: 'primary', attrs: { 'data-action': 'run-all' } })}
        <a class="btn" href="#/proof">${icon('flask', 16)}<span>Tests · evals</span></a>`,
    })}
    <div class="pg-contracts">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Contracts passing', value: html`${fmt.int(sum.passing)}<span class="kpi-unit">of ${fmt.int(sum.total)}</span>`,
          hint: sum.last_run ? `Last run ${fmt.rel(sum.last_run, S.list.now)} · after every ingest and action` : 'Never run' })}
        ${ui.kpi({ label: 'Failing · critical', value: fmt.int(sum.failing_by_severity.CRITICAL || 0),
          status: sum.failing_by_severity.CRITICAL ? { tone: 'critical', label: 'Critical' } : { tone: 'good', label: 'None' },
          hint: 'Wrong data a decision would act on' })}
        ${ui.kpi({ label: 'Failing · serious or warning', value: fmt.int((sum.failing_by_severity.SERIOUS || 0) + (sum.failing_by_severity.WARNING || 0)),
          hint: `${fmt.int(sum.failing_by_severity.SERIOUS || 0)} serious · ${fmt.int(sum.failing_by_severity.WARNING || 0)} warning` })}
        ${ui.kpi({ label: 'Violating rows', value: fmt.int(sum.violations), hint: 'Each one opens to the row and its entity' })}
        ${ui.kpi({ label: 'Reconciliations that agree', value: html`${fmt.int(rs.MATCH + rs.EXPLAINED)}<span class="kpi-unit">of ${fmt.int(nRecon)}</span>`,
          status: rs.OPEN ? { tone: 'serious', label: `${rs.OPEN} open` } : { tone: 'good', label: 'All agree' },
          hint: `${fmt.int(rs.MATCH)} match · ${fmt.int(rs.EXPLAINED)} explained by the data` })}
      </div>
      <div class="ct-tabs" id="ct-tabs"></div>
      <div id="ct-tab"></div>
    </div>`);
  ui.tabs($('#ct-tabs', S.el), {
    active: S.tab,
    tabs: [
      { id: 'contracts', label: 'Data contracts', icon: 'checklist', count: sum.failing ? `${sum.failing} failing` : 'all pass' },
      { id: 'recon', label: 'Reconciliation', icon: 'scale', count: rs.OPEN ? `${rs.OPEN} open` : 'all agree' },
    ],
    onChange: (id) => { S.tab = id; S.ctx.setQuery({ tab: id === 'recon' ? 'recon' : null }, { silent: true }); drawTab(); },
  });
  drawTab();
}

function drawTab() {
  const host = $('#ct-tab', S.el);
  if (!host) return;
  if (S.tab === 'recon') drawRecon(host);
  else drawContracts(host);
}

// --------------------------------------------------------------------------- contracts
function drawContracts(host) {
  const items = S.list.contracts;
  host.innerHTML = String(html`
    <div class="layer-strip">${LAYERS.map((l, i) => {
      const xs = items.filter((c) => c.layer === l.id);
      const bad = xs.filter((c) => c.status === 'FAIL');
      return html`${i ? html`<span class="ls-arrow">${icon('arrow-right', 16)}</span>` : ''}
        <button type="button" class="ls-box${S.filter === l.id ? ' active' : ''}" data-layer="${l.id}">
          <span class="ls-name">${l.label}</span><span class="ls-hint">${l.hint}</span>
          <span class="ls-count"><strong>${fmt.int(xs.length - bad.length)}</strong> of ${fmt.int(xs.length)} pass</span>
          ${bad.length ? ui.chip(worstTone(bad), `${bad.length} failing`) : ui.chip('good', 'Clean')}
        </button>`;
    })}</div>
    <div class="ct-filter" id="ct-filter"></div>
    ${ui.card({
      title: 'Data contracts', subtitle: 'SQL that returns the violating rows: zero rows is a pass. Results are stored per run, so drift shows as a trend.',
      flush: true, body: html`<div id="ct-table"></div>`,
    })}`);
  const failing = items.filter((c) => c.status === 'FAIL').length;
  ui.segmented($('#ct-filter', host), {
    options: [{ value: 'ALL', label: `All · ${items.length}` }, { value: 'FAIL', label: `Failing · ${failing}` },
      ...LAYERS.map((l) => ({ value: l.id, label: l.label }))],
    value: S.filter, label: 'Filter contracts',
    onChange: (v) => { S.filter = v; drawTab(); },
  });
  ui.dataTable($('#ct-table', host), {
    rows: filterContracts(items), pageSize: 0,
    onRowClick: (r) => openContract(r.contract_id),
    columns: [
      { key: 'status', label: 'Result', render: (r) => (r.status === 'FAIL'
        ? ui.chip(SEV_TONE[r.severity], `${fmt.int(r.violations)} violating`)
        : r.status === 'PASS' ? ui.chip('good', 'Pass') : ui.chip('neutral', 'Not run')) },
      { key: 'contract_id', label: 'ID', render: (r) => html`<a class="id-link" href="#/contracts?contract=${encodeURIComponent(r.contract_id)}" data-open-contract="${r.contract_id}">${r.contract_id}</a>` },
      { key: 'name', label: 'Contract', wrap: true, render: (r) => html`<div class="ct-name">${r.name}</div><div class="tiny muted">${r.domain}</div>` },
      { key: 'layer', label: 'Layer', render: (r) => html`<span class="small">${fmt.title(r.layer)}</span>` },
      { key: 'severity', label: 'Severity', render: (r) => ui.chip(SEV_TONE[r.severity], fmt.title(r.severity)) },
      { key: 'owner', label: 'Owner', render: (r) => html`<span class="small">${r.owner}</span>` },
      { key: 'history', label: 'Trend', sortable: false, render: (r) => (r.history.length > 1
        ? charts.sparkline(r.history.map((h) => h.violations), { width: 72, height: 22 })
        : html`<span class="tiny muted">${r.history.length} run</span>`) },
      { key: 'ran_at', label: 'Last run', render: (r) => (r.ran_at ? html`<span class="small">${fmt.rel(r.ran_at, S.list.now)}</span>` : html`<span class="nil">—</span>`) },
      { key: 'duration_ms', label: 'Time', num: true, render: (r) => html`<span class="small">${fmt.ms(r.duration_ms)}</span>` },
    ],
  });
}

function filterContracts(items) {
  if (S.filter === 'ALL') return items;
  if (S.filter === 'FAIL') return items.filter((c) => c.status === 'FAIL');
  return items.filter((c) => c.layer === S.filter);
}

function worstTone(bad) {
  if (bad.some((c) => c.severity === 'CRITICAL')) return 'critical';
  if (bad.some((c) => c.severity === 'SERIOUS')) return 'serious';
  return 'warning';
}

async function runAll(btn) {
  btn.disabled = true;
  const label = btn.querySelector('span');
  if (label) label.textContent = 'Running…';
  try {
    const r = await api.post('/api/contracts/run', {});
    if (!S) return;
    S.list = { now: S.list.now, summary: r.summary, contracts: r.contracts };
    ui.toast(`Ran ${r.ran} contracts in ${fmt.ms(r.ms)}: ${fmt.int(r.summary.failing)} failing, ${fmt.int(r.violations)} violating rows`,
      r.summary.failing ? 'warning' : 'good');
    draw();
    window.dispatchEvent(new Event('ops:meta-changed'));
  } catch (err) {
    ui.toast(err.message, 'critical');
    btn.disabled = false;
    if (label) label.textContent = 'Run all contracts';
  }
}

// --------------------------------------------------------------------------- contract drawer
async function openContract(id) {
  const c = S.list.contracts.find((x) => x.contract_id === id);
  if (!c) { ui.toast(`No contract ${id}`, 'warning'); return; }
  S.ctx.setQuery({ contract: id }, { silent: true });
  const body = ui.drawer.open({
    title: `${c.contract_id} · ${c.name}`,
    subtitle: html`${ui.chip(SEV_TONE[c.severity], fmt.title(c.severity))} <span class="small muted">${fmt.title(c.layer)} layer · owner ${c.owner}</span>`,
    width: 680, body: contractBody(c),
    onClose: () => { if (S) S.ctx.setQuery({ contract: null }, { silent: true }); },
  });
  const host = $('#cv-rows', body);
  try {
    const v = await api.get(`/api/contracts/violations/${encodeURIComponent(id)}`);
    if (!S || !host.isConnected) return;
    host.innerHTML = String(violationsBlock(v));
  } catch (err) {
    host.innerHTML = String(ui.errorBox(err));
  }
}

function contractBody(c) {
  const hist = c.history;
  return html`<div class="pg-contracts-drawer">
    <p class="cv-desc">${c.description}</p>
    <div class="row wrap cv-meta">
      ${c.status === 'FAIL' ? ui.chip(SEV_TONE[c.severity], `${fmt.int(c.violations)} violating at last run`) : ui.chip('good', 'Passing at last run')}
      <span class="small muted">${c.ran_at ? `ran ${fmt.rel(c.ran_at, S.list.now)} · ${fmt.ms(c.duration_ms)}` : 'never run'}</span>
      <span class="spacer"></span>
      ${ui.button({ label: 'Run this contract', icon: 'play', size: 'sm', attrs: { 'data-action': 'run-one', 'data-id': c.contract_id } })}
    </div>
    <h4 class="section-title">Check</h4>
    ${ui.codeBlock(c.check_sql.replace(/\n {8}/g, '\n'), 'sql')}
    <h4 class="section-title">Violating rows, live</h4>
    <div id="cv-rows">${ui.loading('Running the check')}</div>
    ${hist.length > 1 ? html`<h4 class="section-title">History · last ${hist.length} runs</h4>${charts.line({
      series: [{ name: 'Violations', points: hist.map((h, i) => ({ x: `Run ${i + 1}`, y: h.violations })) }],
      height: 140, yFormat: (v) => fmt.int(v), markers: true, xType: 'cat', xLabel: 'Run',
      ariaLabel: `Violations per run for ${c.contract_id}`,
    })}` : html`<p class="tiny muted">One run so far. Run it again to start a trend.</p>`}
  </div>`;
}

function violationsBlock(v) {
  if (!v.rows.length) {
    return ui.callout({ tone: 'good', title: 'No rows violate this contract', body: `Checked just now in ${fmt.ms(v.ms)}.` });
  }
  return html`<p class="small muted">${fmt.int(v.count)}${v.truncated ? '+' : ''} rows in ${fmt.ms(v.ms)}. Identifiers open the entity.</p>
    <div class="table-wrap cv-table"><table class="table dense">
      <thead><tr>${v.columns.map((c) => html`<th>${c.replace(/_/g, ' ')}</th>`)}</tr></thead>
      <tbody>${v.rows.map((r) => html`<tr>${v.columns.map((c) => html`<td>${cellFor(c, r[c], r)}</td>`)}</tr>`)}</tbody>
    </table></div>`;
}

function cellFor(col, val, row) {
  if (val == null || val === '') return html`<span class="nil">—</span>`;
  if (SERIAL_COLS.has(col)) return link.serial(val);
  if (col === 'po_id') return link.po(val, row.line_no);
  if (col === 'shipment_id') return link.shipment(val);
  if (col === 'lot_id') return link.lot(val);
  if (col === 'item_id') return link.item(val);
  if (col === 'supplier_id') return link.supplier(val);
  if (col === 'order_id') return link.order(val);
  if (col === 'chargeback_id') return html`<a class="id-link" href="#/warranty?cb=${encodeURIComponent(val)}">${val}</a>`;
  if (col === 'tbl') return link.table(val);
  if (col === 'raw_id' || col === 'line_no' || col === 'entry_no') return html`<span class="mono small">${val}</span>`;
  if (/_at$/.test(col) && typeof val === 'string') return html`<span class="small">${fmt.dt(val)}</span>`;
  if (/usd|price/.test(col) && typeof val === 'number') return fmt.usd(val, { cents: true });
  if (typeof val === 'number') return Number.isInteger(val) ? fmt.int(val) : fmt.num(val, 2);
  if (col === 'ingest_note' || String(val).length > 40) return html`<span class="small">${val}</span>`;
  return html`<span class="mono small">${val}</span>`;
}

async function runOne(btn) {
  const id = btn.dataset.id;
  btn.disabled = true;
  try {
    const r = await api.post('/api/contracts/run', { contract_id: id });
    if (!S) return;
    S.list = { now: S.list.now, summary: r.summary, contracts: r.contracts };
    draw();
    ui.toast(`${id}: ${r.violations ? `${fmt.int(r.violations)} violating rows` : 'passing'}`, r.violations ? 'warning' : 'good');
    openContract(id);
  } catch (err) {
    ui.toast(err.message, 'critical');
    btn.disabled = false;
  }
}

// --------------------------------------------------------------------------- reconciliation
function drawRecon(host) {
  const recs = S.recon.recons;
  host.innerHTML = String(html`
    <p class="recon-intro ink-2">Where two systems describe the same physical thing, both numbers sit side by side.
      <strong>Match</strong>: they agree. <strong>Explained</strong>: they disagree for a reason the data itself proves.
      <strong>Open</strong>: someone has to go and look, and the card says who.</p>
    <div class="grid">${recs.map((r) => html`<div class="span-6 recon-cell">${reconCard(r)}</div>`)}</div>`);
}

function fmtVal(v, unit) {
  if (unit === 'usd') return fmt.usd(v, { cents: true });
  return fmt.int(v);
}

function reconCard(r) {
  const [tone, label] = RECON_TONE[r.status] || ['neutral', r.status];
  const unit = r.a.unit === 'usd' ? 'usd' : null;
  const deltaTxt = r.id === 'invoice'
    ? `${fmt.usd(r.delta)} at stake`
    : `Δ ${r.delta > 0 ? '+' : r.delta < 0 ? '−' : ''}${fmtVal(Math.abs(r.delta), unit)}`;
  return ui.card({
    cls: `recon-card tone-${tone}`,
    title: r.title, subtitle: r.question,
    actions: ui.chip(tone, label),
    body: html`
      <div class="vs">
        <div class="vs-side"><span class="vs-sys">${r.a.system}</span><span class="vs-num">${fmtVal(r.a.value, unit)}</span><span class="tiny muted">${r.a.unit === 'usd' ? '' : r.a.unit}</span></div>
        <div class="vs-mid"><span class="vs-delta ${r.delta ? 'off' : 'on'}">${deltaTxt}</span></div>
        <div class="vs-side right"><span class="vs-sys">${r.b.system}</span><span class="vs-num">${fmtVal(r.b.value, unit)}</span><span class="tiny muted">${r.b.unit === 'usd' ? '' : r.b.unit}</span></div>
      </div>
      <p class="recon-why">${r.explanation}</p>
      ${reconExtras(r)}
      <div class="recon-foot">
        <span class="small muted">Owner <strong class="ink-2">${r.owner}</strong></span>
        <span class="recon-tables">${r.tables.map((t) => link.table(t))}</span>
        <span class="spacer"></span>
        ${r.rows.length ? html`<button type="button" class="btn btn-ghost sm" data-recon-rows="${r.id}">${icon('table', 14)}<span>${fmt.int(r.rows.length)} rows</span></button>` : ''}
      </div>`,
  });
}

function reconExtras(r) {
  if (r.id === 'asn-receipt' && r.missing.length) {
    return html`<div class="recon-list"><span class="small muted">Missing from ${r.missing[0].shipment_id}:</span> ${r.missing.map((m, i) => html`${i ? ', ' : ''}${link.serial(m.serial)}`)}
      ${r.damaged.length ? html`<span class="small muted"> · damaged on receipt:</span> ${r.damaged.map((d, i) => html`${i ? ', ' : ''}${link.serial(d.serial)}`)}` : ''}</div>`;
  }
  if (r.id === 'cm-output' && r.handled.length) {
    return html`<ul class="recon-handled">${r.handled.map((h) => html`<li>${ui.statusChip(h.ingest_status, h.ingest_status === 'IGNORED' ? 'Ignored' : 'Warned')} <span class="small">${h.ingest_note}</span></li>`)}</ul>`;
  }
  if (r.id === 'invoice') {
    const matched = r.match.filter((m) => m.match_status === 'MATCHED').reduce((a, m) => a + m.n, 0);
    return html`<div class="recon-list small">${ui.chip('good', `${fmt.int(matched)} invoices matched`)} ${r.exceptions.map((e) => html`${ui.chip('serious', `${e.invoice_id} ${fmt.title(e.match_status)}`)} <span class="muted">${fmt.usd(e.variance_usd)} blocked on ${link.po(e.po_id, e.line_no)}</span> `)}</div>`;
  }
  if (r.id === 'chargeback-erp') {
    return html`<div class="recon-list small">${r.rows.map((c) => html`${ui.chip((RECON_TONE[c.check] || ['neutral'])[0], `${c.chargeback_id} ${fmt.title(c.status)}`, { icon: false })} `)}</div>`;
  }
  return '';
}

function openRecon(id) {
  const r = S.recon.recons.find((x) => x.id === id);
  if (!r) return;
  const [tone, label] = RECON_TONE[r.status] || ['neutral', r.status];
  const body = ui.drawer.open({
    title: r.title, width: 860,
    subtitle: html`${ui.chip(tone, label)} <span class="small muted">${r.question}</span>`,
    body: html`<div class="pg-contracts-drawer"><p class="cv-desc">${r.explanation}</p>
      <div class="rr-table" id="rr-table"></div>
      <p class="tiny muted">Rows come from ${r.tables.map((t, i) => html`${i ? ', ' : ''}${link.table(t)}`)}.</p></div>`,
  });
  ui.dataTable($('#rr-table', body), {
    rows: r.rows, pageSize: 25, dense: true, search: r.rows.length > 12,
    columns: r.columns.map((c) => ({
      key: c.key, label: c.label, num: !!c.num, wrap: c.key === 'why',
      render: (row) => reconCell(c.key, row[c.key], row),
    })),
  });
}

function reconCell(key, val, row) {
  if (key === 'status' || key === 'check') {
    const [tone, label] = RECON_TONE[val] || ['neutral', fmt.title(val)];
    return ui.chip(tone, label);
  }
  if (val == null || val === '') return html`<span class="nil">—</span>`;
  if (key === 'shipment_id') return link.shipment(val);
  if (key === 'po_id') return link.po(val, row.line_no);
  if (key === 'item_id') return link.item(val);
  if (key === 'supplier_id') return link.supplier(val);
  if (key === 'chargeback_id') return html`<a class="id-link" href="#/warranty?cb=${encodeURIComponent(val)}">${val}</a>`;
  if (key === 'day' || key === 'po_date') return fmt.date(val);
  if (key === 'received_at') return html`<span class="small">${fmt.dt(val)}</span>`;
  if (key === 'delta' || key === 'residual') {
    return html`<span class="${val ? 'delta-off' : 'muted'}">${val > 0 ? '+' : ''}${fmt.int(val)}</span>`;
  }
  if (/usd|price|debit|credit/.test(key) && typeof val === 'number') return fmt.usd(val, { cents: true });
  if (typeof val === 'number') return Number.isInteger(val) ? fmt.int(val) : fmt.num(val, 2);
  if (key === 'why' || key === 'explanation') return html`<span class="small">${val}</span>`;
  if (key === 'ref' || key === 'je_id') return html`<span class="mono small">${val}</span>`;
  return String(val);
}

const PAGE_CSS = `
.pg-contracts { display: flex; flex-direction: column; gap: 16px; }
.pg-contracts .ct-tabs { margin-top: 6px; overflow-x: auto; }
.pg-contracts .layer-strip { display: flex; align-items: stretch; gap: 8px; margin: 2px 0 14px; flex-wrap: wrap; }
.pg-contracts .ls-arrow { display: flex; align-items: center; color: var(--ink-3); }
.pg-contracts .ls-box { flex: 1 1 200px; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; padding: 12px 14px; text-align: left; font: inherit; color: var(--ink); background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius); box-shadow: var(--shadow-sm); cursor: pointer; transition: border-color .12s, background .12s; }
.pg-contracts .ls-box:hover { border-color: var(--hairline-strong); }
.pg-contracts .ls-box.active { border-color: var(--sign); box-shadow: 0 0 0 1px var(--sign); }
.pg-contracts .ls-box:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.pg-contracts .ls-name { font: 600 15.5px/1.2 var(--font-cond); }
.pg-contracts .ls-hint { font-size: 12px; color: var(--ink-3); }
.pg-contracts .ls-count { font-size: 13px; color: var(--ink-2); margin-top: 2px; }
.pg-contracts .ct-filter { margin-bottom: 12px; overflow-x: auto; }
.pg-contracts .ct-name { font-weight: 500; }
.pg-contracts .recon-intro { margin: 2px 0 14px; max-width: 900px; font-size: 13.5px; line-height: 1.55; }
.pg-contracts .recon-cell { min-width: 0; display: flex; }
.pg-contracts .recon-cell > .card { flex: 1; display: flex; flex-direction: column; }
.pg-contracts .recon-cell > .card > .card-body { flex: 1; display: flex; flex-direction: column; }
.pg-contracts .recon-card { border-top: 3px solid var(--tone); }
.pg-contracts .vs { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; gap: 12px; padding: 12px 14px; background: var(--surface-2); border: 1px solid var(--hairline); border-radius: 10px; }
.pg-contracts .vs-side { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.pg-contracts .vs-side.right { align-items: flex-end; text-align: right; }
.pg-contracts .vs-sys { font-size: 12px; color: var(--ink-3); }
.pg-contracts .vs-num { font: 600 24px/1.15 var(--font-ui); letter-spacing: -.01em; white-space: nowrap; }
.pg-contracts .vs-delta { display: inline-block; padding: 4px 10px; border-radius: 999px; font: 600 12.5px/1.2 var(--font-ui); white-space: nowrap; border: 1px solid var(--hairline); background: var(--surface); }
.pg-contracts .vs-delta.off { color: var(--delta-bad); border-color: color-mix(in srgb, var(--critical) 35%, transparent); }
.pg-contracts .vs-delta.on { color: var(--delta-good); }
.pg-contracts .recon-why { margin: 12px 0 8px; font-size: 13px; line-height: 1.55; color: var(--ink-2); }
.pg-contracts .recon-list { margin: 0 0 8px; line-height: 1.9; }
.pg-contracts .recon-handled { list-style: none; margin: 0 0 8px; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.pg-contracts .recon-handled li { display: flex; gap: 8px; align-items: flex-start; }
.pg-contracts .recon-foot { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--hairline); }
.pg-contracts .recon-tables { display: inline-flex; flex-wrap: wrap; gap: 4px 10px; font-size: 12.5px; }
.pg-contracts .delta-off { color: var(--delta-bad); font-weight: 600; }
.pg-contracts-drawer .cv-desc { margin: 0 0 10px; font-size: 13.5px; color: var(--ink-2); line-height: 1.5; }
.pg-contracts-drawer .cv-meta { margin-bottom: 4px; }
.pg-contracts-drawer .cv-table { max-height: 360px; }
.pg-contracts-drawer .rr-table { margin: 4px 0 10px; }
.pg-contracts-drawer .delta-off { color: var(--delta-bad); font-weight: 600; }
@media (max-width: 700px) {
  .pg-contracts .ls-arrow { display: none; }
  .pg-contracts .vs { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .pg-contracts .vs-mid { grid-column: 1 / -1; grid-row: 2; justify-self: center; }
  .pg-contracts .vs-num { font-size: 20px; }
}
`;
