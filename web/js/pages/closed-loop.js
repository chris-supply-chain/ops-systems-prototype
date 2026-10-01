// Closed Loop: sense -> decide -> act -> learn, inside the system. Each loop shows the
// tables it reads and writes (live row counts), the decision waiting on a human, and,
// once executed, exactly which rows it wrote and which systems heard about it.
import { html, raw, esc, on, injectStyle, $ } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';

const LOOP_ICON = { QUALITY: 'shield', SUPPLY: 'truck', PROMISE: 'clock', DATA: 'database' };
const STAGE_TONE = ['info', 'warning', 'serious', 'good'];

let OFFS = [];

export function unmount() {
  OFFS.forEach((off) => off());
  OFFS = [];
}

export async function render(el, ctx) {
  injectStyle('page-closed-loop', PAGE_CSS);
  unmount();
  OFFS.push(on(el, 'click', '[data-review]', (e, t) => openDecision(t.dataset.review, ctx, el)));
  OFFS.push(on(el, 'click', '[data-act=propose]', async () => {
    const r = await api.post('/api/loop/propose', {});
    ui.toast(r.proposed.length ? `Proposed ${r.proposed.join(', ')}` : 'Nothing new to propose', 'info');
    window.dispatchEvent(new Event('ops:meta-changed'));
    draw(el, ctx);
  }));
  await draw(el, ctx);
  const want = ctx.query.get('d');
  if (want) openDecision(want, ctx, el);
}

async function draw(el, ctx) {
  const d = await api.get('/api/loop');
  const c = d.counts;
  el.innerHTML = html`
    ${ui.pageHeader({
      actions: html`<button class="btn sm" data-act="propose" title="Re-run exception detection and let the rules propose">${icon('refresh', 15)}<span>Re-detect</span></button>`,
    })}
    <div class="pg-loop">
      <div class="pl-strip">
        <div class="pl-strip-step"><b>Sense</b><span>raw feeds land verbatim; normalizers build the core</span></div>
        <span class="pl-arrow">${icon('arrow-right', 16)}</span>
        <div class="pl-strip-step"><b>Decide</b><span>rules raise exceptions and propose decisions with evidence</span></div>
        <span class="pl-arrow">${icon('arrow-right', 16)}</span>
        <div class="pl-strip-step"><b>Act</b><span>a human approves; the system writes holds, POs, promises, messages</span></div>
        <span class="pl-arrow">${icon('arrow-right', 16)}</span>
        <div class="pl-strip-step"><b>Learn</b><span>outcomes feed scorecards, evals and the next plan</span></div>
        <div class="pl-strip-counts">
          ${ui.chip(c.proposed ? 'info' : 'good', `${c.proposed} proposed`)}
          ${ui.chip('good', `${c.executed} executed`)}
          ${ui.chip('neutral', `${fmt.int(c.outbound)} outbound writes`)}
        </div>
      </div>

      ${d.loops.map((lp) => loopCard(lp))}

      <div class="grid">
        <div class="span-8">${ui.card({
          title: 'Every write to another system',
          subtitle: 'outbound_message: what the platform told the ERP, the 3PL WMS, the CM and pack-line MES, suppliers, carriers and customers',
          flush: true, body: html`<div data-outbound></div>`,
        })}</div>
        <div class="span-4">${ui.card({
          title: 'By target system', body: byTarget(d.by_target),
        })}</div>
      </div>
    </div>`;

  ui.dataTable($('[data-outbound]', el), {
    rows: d.outbound, pageSize: 12, search: true, dense: true,
    columns: [
      { key: 'msg_id', label: '#', num: true, width: 60 },
      { key: 'target_system', label: 'Target', render: (r) => ui.chip('neutral', fmt.title(r.target_system), { icon: 'external' }) },
      { key: 'message_type', label: 'Message', render: (r) => html`<span class="mono">${r.message_type}</span>` },
      { key: 'ref', label: 'Ref', render: (r) => html`<span class="mono">${r.ref || ''}</span>` },
      { key: 'decision_id', label: 'Decision', render: (r) => (r.decision_id ? html`<a class="id-link" href="#/loop?d=${r.decision_id}">${r.decision_id}</a>` : html`<span class="nil">—</span>`) },
      { key: 'created_at', label: 'When', render: (r) => html`<span class="mono small">${fmt.dt(r.created_at)}</span>` },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
    ],
  });

}

function loopCard(lp) {
  const pending = lp.pending[0];
  const last = lp.history[0];
  return html`<section class="card pl-card">
    <header class="pl-head">
      <span class="pl-ic">${icon(LOOP_ICON[lp.key] || 'loop', 18)}</span>
      <div><h3 class="card-title">${lp.name}</h3><p class="card-sub">${lp.question}</p></div>
    </header>
    <ol class="pl-stages">${lp.stages.map((s, i) => html`<li class="pl-stage tone-${STAGE_TONE[i]}">
      <div class="pl-stage-name"><span class="pl-num">${i + 1}</span>${s.name}</div>
      <div class="pl-stage-what">${s.what}</div>
      <div class="pl-tables">${s.tables.map((t) => html`<a class="pl-table" href="#/sandbox?table=${t.table}" title="Open ${t.table} in the Data Sandbox">
        <span class="mono">${t.table}</span><span class="pl-rows">${t.rows == null ? '' : fmt.compact(t.rows)}</span></a>`)}</div>
    </li>`)}</ol>
    <div class="pl-side">
    <div class="pl-return">${icon('refresh', 13)} Learn feeds the next Sense</div>
    ${pending ? html`<div class="pl-pending">
      ${ui.callout({ tone: 'info', title: html`${pending.decision_id} · proposed`,
        body: html`<p class="pl-pend-title">${pending.title}</p>
          <div class="pl-impact">${impactChips(pending.impact)}</div>
          <button class="btn btn-primary sm" data-review="${pending.decision_id}">${icon('play', 14)}<span>Review and decide</span></button>` })}
    </div>` : last ? html`<div class="pl-pending">${ui.callout({
      tone: last.status === 'EXECUTED' ? 'good' : 'neutral',
      title: html`${last.decision_id} · ${fmt.title(last.status)}${last.decided_by ? html` by ${last.decided_by}` : ''}`,
      body: html`<p class="pl-pend-title">${last.title}</p><button class="btn sm" data-review="${last.decision_id}">See what it wrote</button>`,
    })}</div>` : ''}
    ${lp.history.length ? html`<details class="pl-hist"><summary>History (${lp.history.length})</summary>
      <ul>${lp.history.map((h) => html`<li><a href="#/loop?d=${h.decision_id}" class="id-link">${h.decision_id}</a> ${ui.statusChip(h.status)} <span>${h.title}</span></li>`)}</ul>
    </details>` : ''}
    </div>
  </section>`;
}

const IMPACT_LABEL = { hold_units: 'Affected units', kit_companions: 'Kit packs held with them', serial_holds: 'Serial holds', lot_holds: 'Lot holds', customers_exposed: 'Customers exposed', recovery_estimate_usd: 'Recovery (est.)',
  packs_protected: 'Packs protected', air_freight_usd: 'Air freight', orders_protected: 'Promises that would slip',
  messages: 'Messages', as_built_corrections: 'As-built fixes', orders: 'Orders to re-promise', max_slip_days: 'Max slip (days)' };

const LOCATION_LABEL = { '3PL-RNO': '3PL, Reno', 'OEM-FRE': 'Fremont', IN_TRANSIT: 'In transit' };
const EVIDENCE_LABEL = {
  lot: 'Lot with the most claims', scope_ref: 'Scope', sibling_lots: 'Cell lots from the batch', claims: 'Claims',
  per_1000_pack_months: 'Claims per 1,000 pack-months', baseline_per_1000_pack_months: 'Rest of fleet, per 1,000 pack-months',
  baseline_claims: 'Rest of fleet, claims', packs_built_from_scope: 'Packs built from the batch',
  packs_in_service: 'Of those, with customers', cells_in_stock: 'Cells still in stock',
  item: 'Item', stockout_day: 'Runs out on', po_ref: 'PO line to pull in', po_id: 'PO', line_no: 'Line', promise: 'Supplier promise',
  qty: 'Quantity to air-ship', expedite_day: 'Air arrival', gap_days: 'Days short', packs_lost: 'Packs lost without action',
  air_usd: 'Air freight', orders_hit: 'Promises that would slip',
  quarantined: 'Messages in quarantine', vehicles_with_wrong_du: 'Vehicles with the wrong drive unit on record',
  orders: 'Orders at risk', avg_slip_days: 'Average slip (days)', by_source: 'At risk by container',
};

function impactChips(impact) {
  if (!impact) return '';
  return Object.entries(impact).map(([k, v]) => html`<span class="pl-chip">${IMPACT_LABEL[k] || fmt.title(k)} <b>${typeof v === 'number'
    ? (k.endsWith('_usd') ? fmt.usd(v, { compact: v >= 10000 }) : fmt.int(v)) : v}</b></span>`);
}

function byTarget(rows) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return html`<ul class="pl-bt">${rows.map((r) => html`<li>
    <span class="pl-bt-name">${fmt.title(r.target_system)}</span>
    ${ui.meter({ value: r.n, max })}<span class="pl-bt-n">${fmt.int(r.n)}</span></li>`)}</ul>`;
}

// ---------------------------------------------------------------- decision drawer

async function openDecision(id, ctx, pageEl) {
  const body = ui.drawer.open({ title: id, subtitle: 'Loading…', body: ui.loading(), width: 640 });
  let d;
  try {
    d = await api.get(`/api/loop/decision/${encodeURIComponent(id)}`);
  } catch (e) {
    body.innerHTML = ui.errorBox(e).toString();
    return;
  }
  drawDecision(d, ctx, pageEl);
}

let DRAWER_OFFS = [];

function drawDecision(d, ctx, pageEl) {
  const dec = d.decision;
  const proposed = dec.status === 'PROPOSED';
  const body = ui.drawer.open({
    title: dec.title,
    subtitle: html`${dec.decision_id} · ${fmt.title(dec.loop)} loop · rule <span class="mono">${dec.rule_id}</span> · ${fmt.title(dec.status)}`,
    width: 680,
    body: html`<div class="pl-dr">
      <h4 class="section-title">Why</h4>
      <p class="pl-dr-text">${dec.rationale}</p>
      ${dec.inputs ? html`<h4 class="section-title">Evidence the rule used</h4>${kv(dec.inputs)}` : ''}
      <h4 class="section-title">What it will do</h4>
      <p class="pl-dr-text">${dec.proposed_action}</p>
      ${dec.impact ? html`<div class="pl-impact">${impactChips(dec.impact)}</div>` : ''}
      ${proposed ? html`<div class="pl-dr-actions">
        <label class="pl-actor">Approve as <input class="input sm" data-actor value="Ops lead" maxlength="60"></label>
        <button class="btn btn-primary" data-exec>${icon('play', 15)}<span>Execute</span></button>
        <button class="btn btn-ghost" data-reject>Reject</button>
      </div>
      <p class="muted small">Executing writes real rows (holds, PO lines, promises, mappings, messages) inside one transaction. Reset demo data from the sidebar to replay.</p>`
      : outcome(dec, d)}
      <h4 class="section-title">Audit it yourself</h4>
      ${ui.codeBlock(d.audit_sql, 'sql')}
      <p class="small muted">Paste into the ${link.route('sandbox', 'Data Sandbox')} SQL tab, or open the tables:
        ${['decision_log', 'hold', 'outbound_message', 'order_promise', 'chargeback', 'change_review'].map((t) => html` ${link.table(t)}`)}</p>
    </div>`,
  });
  DRAWER_OFFS.forEach((off) => off());
  DRAWER_OFFS = [];
  DRAWER_OFFS.push(on(body, 'click', '[data-exec]', async (e, btn) => {
    btn.disabled = true;
    btn.querySelector('span').textContent = 'Executing…';
    const actor = (body.querySelector('[data-actor]') || {}).value || 'Ops lead';
    try {
      const out = await api.post('/api/loop/execute', { decision_id: dec.decision_id, actor });
      ui.toast(`${dec.decision_id} ${out.status.toLowerCase()}`, out.status === 'EXECUTED' ? 'good' : 'warning');
      window.dispatchEvent(new Event('ops:meta-changed'));
      const fresh = await api.get(`/api/loop/decision/${encodeURIComponent(dec.decision_id)}`);
      drawDecision(fresh, ctx, pageEl);
      if (pageEl && document.contains(pageEl)) draw(pageEl, ctx);
    } catch (err) {
      ui.toast(err.message, 'critical');
      btn.disabled = false;
      btn.querySelector('span').textContent = 'Execute';
    }
  }));
  DRAWER_OFFS.push(on(body, 'click', '[data-reject]', async () => {
    await api.post('/api/loop/reject', { decision_id: dec.decision_id, actor: 'Ops lead' });
    ui.toast(`${dec.decision_id} rejected`, 'neutral');
    ui.drawer.close();
    if (pageEl && document.contains(pageEl)) draw(pageEl, ctx);
  }));
}

// How a measure reads once it has happened, where that differs from the proposal's wording.
const ACHIEVED_LABEL = { orders_protected: 'Promises kept from slipping', recovery_estimate_usd: 'Recovery drafted',
  orders: 'Orders re-promised', messages: 'Messages replayed', as_built_corrections: 'As-built records fixed' };

// Expected impact beside the same figures counted again from the data after execution (decisions.measure).
function achieved(dec) {
  const a = dec.achieved;
  if (!a || !a.figures) return '';
  const exp = dec.impact || {};
  const show = (k, v) => (typeof v === 'number' ? (k.endsWith('_usd') ? fmt.usd(v) : fmt.int(v)) : (v ?? '—'));
  return html`<h4 class="section-title">What it achieved</h4>
    <p class="small muted">Counted again from the data after it ran, not taken from the code that acted.</p>
    <table class="table dense pl-writes"><thead><tr><th>Measure</th><th class="num">Expected</th><th class="num">Achieved</th></tr></thead><tbody>
      ${Object.keys(exp).filter((k) => k in a.figures).map((k) => html`<tr><td>${ACHIEVED_LABEL[k] || IMPACT_LABEL[k] || fmt.title(k)}</td>
        <td class="num">${show(k, exp[k])}</td><td class="num">${show(k, a.figures[k])}</td></tr>`)}
    </tbody></table>
    ${(a.exceptions || []).map((e) => html`<p class="small">Triggering exception <span class="mono">${e.exception_id}</span>:
      ${e.after && e.after.status === 'RESOLVED' ? ui.chip('good', 'Cleared') : ui.chip('warning', 'Still detected')}
      <span class="muted">was ${fmt.title(e.before.status)}, now ${e.after ? fmt.title(e.after.status) : 'gone'}</span></p>`)}`;
}

function outcome(dec, d) {
  const o = dec.outcome || {};
  const w = o.writes || {};
  const gates = o.gates;
  return html`${achieved(dec)}<h4 class="section-title">What it wrote</h4>
    ${o.note ? ui.callout({ tone: 'info', title: 'Supply moved between proposal and execution', body: o.note }) : ''}
    ${o.result ? ui.callout({ tone: o.result === 'gap closed' ? 'good' : 'warning', title: `MRP re-run: ${o.result}`,
      body: o.mrp_run_id ? html`Run #${o.mrp_run_id}. ${link.route('mrp?item=BMS-B', 'Open the MRP record')}` : '' }) : ''}
    ${gates ? html`<div class="pl-gates">${Object.entries(gates).map(([k, v]) => ui.chip(v ? 'good' : 'critical', `${k.replace(/_/g, ' ')}: ${v ? 'pass' : 'fail'}`))}</div>
      <p class="small">Contract C-GEN-05 ${o.c_gen_05_before} → ${o.c_gen_05_after} violations · EV-CM-MES ${fmt.pct(o.eval_score || 0, 1)} ·
        change ${o.change_review ? html`<a class="id-link" href="#/proof">${o.change_review}</a>` : ''}</p>` : ''}
    <table class="table dense pl-writes"><thead><tr><th>Table</th><th class="num">Rows</th></tr></thead><tbody>
      ${Object.entries(w).map(([t, n]) => html`<tr><td>${link.table(t)}</td><td class="num">${typeof n === 'number' ? fmt.int(n) : n}</td></tr>`)}
    </tbody></table>
    ${o.held_by_location ? html`<p class="small">Held by location: ${Object.entries(o.held_by_location).filter(([, v]) => v).map(([k, v], i) => html`${i ? ' · ' : ''}<b>${LOCATION_LABEL[k] || k}</b> ${fmt.int(v)}`)}</p>` : ''}
    ${d.writes.outbound_message.length ? html`<h4 class="section-title">Messages sent (${fmt.int(d.writes.outbound_count)})</h4>
      <ul class="pl-msgs">${d.writes.outbound_message.slice(0, 8).map((m) => html`<li>${ui.chip('neutral', fmt.title(m.target_system), { icon: 'external' })}
        <span class="mono small">${m.message_type}</span> <span class="small muted">${m.ref || ''}</span></li>`)}</ul>` : ''}
    ${d.writes.chargeback.length ? html`<p class="small">Chargeback drafted: ${d.writes.chargeback.map((cb) => html`<a class="id-link" href="#/warranty?cb=${cb.chargeback_id}">${cb.chargeback_id}</a> ${fmt.usd(cb.amount_usd)} (${fmt.title(cb.status)}) `)}</p>` : ''}
    ${d.writes.hold_count ? html`<p class="small">${fmt.int(d.writes.hold_count)} holds placed. ${link.route('quality', 'See them in the Quality loop')}</p>` : ''}`;
}

function kv(obj) {
  return html`<dl class="pl-kv">${Object.entries(obj).map(([k, v]) => html`<dt>${EVIDENCE_LABEL[k] || fmt.title(k)}</dt><dd>${
    Array.isArray(v) ? html`<span class="mono small">${v.join(', ')}</span>`
      : typeof v === 'object' && v ? html`<span class="small">${Object.entries(v).map(([a, b], i) => html`${i ? html`<br>` : ''}${a}: <b>${typeof b === 'number' ? fmt.int(b) : String(b)}</b>`)}</span>`
        : typeof v === 'number' ? (k.endsWith('_usd') ? fmt.usd(v) : fmt.num(v, Number.isInteger(v) ? 0 : 1)) : String(v)}</dd>`)}</dl>`;
}

const PAGE_CSS = `
.pg-loop { display: grid; gap: 16px; }
.pg-loop .grid { margin: 0; }
.pl-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; padding: 12px 14px; border-radius: 12px;
  background: var(--sign); color: var(--sign-ink); }
.pl-strip-step { display: grid; gap: 1px; max-width: 220px; }
.pl-strip-step b { font: 700 13px/1.2 var(--font-cond); letter-spacing: .08em; text-transform: uppercase; color: var(--sign-yellow); }
.pl-strip-step span { font-size: 12px; color: var(--sign-muted); }
.pl-arrow { color: var(--sign-muted); display: inline-flex; }
.pl-strip-counts { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.pl-strip-counts .chip { background: rgba(255,255,255,.1); color: var(--sign-ink); }
.pl-card { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 360px); grid-template-areas: "head head" "stages side";
  gap: 12px 18px; padding: 16px 18px; }
.pl-card > .pl-head { grid-area: head; }
.pl-card > .pl-stages { grid-area: stages; }
.pl-side { grid-area: side; display: grid; gap: 10px; align-content: start; }
@media (max-width: 1100px) { .pl-card { grid-template-columns: 1fr; grid-template-areas: "head" "stages" "side"; } }
.pl-head { display: flex; gap: 12px; align-items: flex-start; }
.pl-ic { display: inline-flex; width: 34px; height: 34px; flex: none; align-items: center; justify-content: center; border-radius: 9px;
  background: var(--sign); color: var(--sign-ink); }
.pl-stages { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.pl-stage { --tone: var(--info); position: relative; padding: 10px; border-radius: 10px; background: var(--surface-2);
  border-top: 3px solid var(--tone); display: grid; gap: 6px; align-content: start; grid-template-columns: minmax(0, 1fr); }
.pl-stage.tone-warning { --tone: var(--warning); } .pl-stage.tone-serious { --tone: var(--serious); } .pl-stage.tone-good { --tone: var(--good); }
.pl-stage-name { font: 700 12.5px/1 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; display: flex; gap: 6px; align-items: center; }
.pl-num { display: inline-flex; width: 18px; height: 18px; border-radius: 50%; align-items: center; justify-content: center;
  background: var(--ink); color: var(--surface); font-size: 11px; }
.pl-stage-what { font-size: 12px; color: var(--ink-2); line-height: 1.35; }
.pl-tables { display: flex; flex-direction: column; gap: 3px; }
.pl-table { display: flex; justify-content: space-between; gap: 6px; font-size: 11px; color: var(--link); text-decoration: none;
  padding: 2px 4px; border-radius: 4px; }
.pl-table:hover { background: color-mix(in srgb, var(--link) 10%, transparent); text-decoration: none; }
.pl-table .mono { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pl-rows { flex-shrink: 0; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.pl-return { font-size: 11.5px; color: var(--ink-3); display: flex; gap: 6px; align-items: center; }
.pl-pend-title { margin: 0 0 8px; font-weight: 600; color: var(--ink); }
.pl-impact { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.pl-chip { font-size: 12px; padding: 3px 8px; border-radius: 999px; background: var(--surface); border: 1px solid var(--hairline); color: var(--ink-2); }
.pl-chip b { color: var(--ink); }
.pl-hist summary { cursor: pointer; font-size: 12.5px; color: var(--ink-2); }
.pl-hist ul { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; font-size: 12.5px; }
.pl-hist li { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pl-bt { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.pl-bt li { display: grid; grid-template-columns: 110px 1fr 44px; gap: 8px; align-items: center; font-size: 12.5px; }
.pl-bt-n { text-align: right; font-variant-numeric: tabular-nums; }
.pl-dr-text { margin: 0 0 6px; color: var(--ink-2); line-height: 1.5; }
.pl-kv { display: grid; grid-template-columns: 170px 1fr; gap: 4px 12px; margin: 0; font-size: 13px; }
.pl-kv dt { color: var(--ink-3); }
.pl-kv dd { margin: 0; }
.pl-dr-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 14px 0 6px; }
.pl-actor { display: inline-flex; gap: 8px; align-items: center; font-size: 13px; color: var(--ink-2); }
.pl-gates { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.pl-writes { margin: 8px 0; max-width: 360px; }
.pl-msgs { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.pl-msgs li { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
@media (max-width: 1100px) { .pl-stages { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .pl-stages { grid-template-columns: 1fr; } .pl-kv { grid-template-columns: 1fr; } }
`;
