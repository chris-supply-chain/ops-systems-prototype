// Warranty & Chargebacks: field claims traced through genealogy to the failed lot
// and the supplier who owns it, the lot-level signal, and the chargeback
// lifecycle from draft to a debit memo posted in the ERP. Every step is a
// decision with its writes.
import { html, raw, esc, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

let S = null;
const r1 = (v) => Math.round(v * 10) / 10;
const crisp = (v) => Math.round(v) + 0.5;
const money = (n) => fmt.usd(n, { cents: true });
const STAGES = [
  { id: 'DRAFT', label: 'Draft', hint: 'priced from claims' },
  { id: 'SENT', label: 'Sent', hint: 'awaiting supplier' },
  { id: 'ACCEPTED', label: 'Accepted', hint: 'ready to post' },
  { id: 'POSTED', label: 'Posted to ERP', hint: 'debit memo booked' },
];
const NEXT = {
  DRAFT: { act: 'send', label: 'Send to supplier', icon: 'arrow-right' },
  SENT: { act: 'accept', label: 'Record acceptance', icon: 'check' },
  DISPUTED: { act: 'accept', label: 'Record acceptance', icon: 'check' },
  ACCEPTED: { act: 'post', label: 'Post to ERP', icon: 'ledger' },
};

// ---------------------------------------------------------------------------
export async function render(el, ctx) {
  injectStyle('page-warranty', PAGE_CSS);
  S = { el, ctx };
  await load();
  draw();
  wire(el);
  const claim = ctx.query.get('claim');
  const cb = ctx.query.get('cb');
  if (claim) openClaim(claim);
  else if (cb) openCB(cb);
}

export function unmount() {
  if (S && S.offDrawer) S.offDrawer();
  S = null;
}

async function load() {
  const [sum, cl, cbs] = await Promise.all([
    api.get('/api/warranty/summary'), api.get('/api/warranty/claims'), api.get('/api/warranty/chargebacks'),
  ]);
  S.sum = sum;
  S.claims = cl.claims;
  S.cbs = cbs.chargebacks;
}

function wire(el) {
  on(el, 'click', '[data-open-claim]', (e, t) => { e.preventDefault(); openClaim(t.dataset.openClaim); });
  on(el, 'click', '[data-open-cb]', (e, t) => { e.preventDefault(); openCB(t.dataset.openCb); });
  on(el, 'click', '[data-cb-act]', (e, b) => act(b));
  on(el, 'click', '[data-draft]', (e, b) => draft(b));
  // drawer actions live outside the page root
  S.offDrawer = on(document.body, 'click', '.drawer [data-cb-act], .drawer [data-draft], .drawer [data-open-cb], .drawer [data-open-claim]', (e, t) => {
    if (!S) return;
    if (t.dataset.cbAct) act(t);
    else if (t.dataset.draft) draft(t);
    else if (t.dataset.openCb) { e.preventDefault(); openCB(t.dataset.openCb); }
    else if (t.dataset.openClaim) { e.preventDefault(); openClaim(t.dataset.openClaim); }
  });
}

function draw() {
  const { sum, claims, cbs } = S;
  const k = sum.kpis;
  const flagged = sum.lots.filter((l) => l.flag);
  S.el.innerHTML = String(html`
    ${ui.pageHeader({
      actions: html`<a class="btn sm" href="#/quality">${icon('shield', 15)}<span>Quality loop</span></a>
        <a class="btn sm" href="#/erp">${icon('ledger', 15)}<span>ERP journal</span></a>`,
    })}
    <div class="pg-warranty stack">
      <div class="kpi-row">
        ${ui.kpi({
          label: 'Claims, last 30 days', value: fmt.int(k.claims_30d),
          delta: k.claims_prev_30d != null ? `${k.claims_30d >= k.claims_prev_30d ? '+' : '−'}${Math.abs(k.claims_30d - k.claims_prev_30d)}` : null,
          deltaGood: 'down', deltaLabel: `vs ${fmt.int(k.claims_prev_30d)} the 30 days before`,
        })}
        ${ui.kpi({
          label: 'Claims per 1,000 delivered', value: fmt.num(k.per_1000, 1),
          hint: `${fmt.int(k.claims_total)} claims on ${fmt.int(k.delivered)} delivered · ${fmt.int(k.rejected)} rejected`,
        })}
        ${ui.kpi({
          label: 'Recoverable, not yet billed', value: fmt.usd(k.recoverable_usd, { compact: true }),
          hint: `${fmt.int(k.recoverable_claims)} diagnosed claims · ${fmt.int(sum.unbilled.length)} suppliers`,
          status: k.recoverable_usd > 5000 ? { tone: 'warning', label: 'Bill it' } : null,
        })}
        ${ui.kpi({
          label: 'Open chargebacks', value: fmt.usd(k.open_cb_usd, { compact: true }),
          hint: `${fmt.int(k.open_cb)} open${sum.pipeline.DISPUTED ? ` · ${sum.pipeline.DISPUTED.count} disputed` : ''}`,
        })}
        ${ui.kpi({
          label: 'Recovered, posted to ERP', value: fmt.usd(k.recovered_usd, { compact: true }),
          hint: `of ${fmt.usd(k.gross_cost_usd, { compact: true })} gross warranty cost`,
        })}
      </div>
      ${flagged.map((l) => signalCallout(l))}
      <div class="grid">
        <div class="span-7">${ui.card({
          title: 'Capacity-fade claims per 1,000 pack-months in service, by cell lot',
          subtitle: 'Per month in service, so young lots compare fairly with old ones. Packs carry cells from one or two lots; each claim is traced through genealogy to the lot that makes up most of the pack.',
          tableToggle: true, body: lotChart(sum),
        })}</div>
        <div class="span-5">${ui.card({
          title: 'Claims by week reported',
          subtitle: 'Capacity fade against every other failure mode. Rejected claims excluded.',
          tableToggle: true, body: weeklyChart(sum.weekly),
        })}</div>
      </div>
      ${ui.card({
        title: 'Chargeback pipeline',
        subtitle: 'From priced draft to a debit memo in the ERP. Each step writes a decision and, where it leaves the building, an outbound message.',
        body: html`${pipeline(sum.pipeline)}<div id="w-cb-table"></div>`,
      })}
      <div class="grid">
        <div class="span-5">${ui.card({
          title: 'Recoverable, not yet billed',
          subtitle: 'Diagnosed claims owned by a supplier, priced by that supplier’s recovery terms.',
          body: unbilled(sum),
        })}</div>
        <div class="span-7">${ui.card({
          title: 'Claims per 1,000 delivered, by build week',
          subtitle: 'Build week comes from the vehicle serial. Weeks with fewer than 20 delivered units are left out.',
          tableToggle: true,
          body: charts.bar({
            categories: sum.build_weeks.map((b) => b.build_week),
            series: [{ name: 'Claims per 1,000 delivered', values: sum.build_weeks.map((b) => b.rate) }],
            height: 220, yFormat: (v) => fmt.num(v, 0), categoryLabel: 'Build week', valueLabels: false,
            ariaLabel: 'Claims per 1,000 delivered by build week',
          }),
        })}</div>
      </div>
      ${ui.card({ title: 'Field claims', subtitle: `${fmt.int(claims.length)} CRM cases, normalized and classified (rules-2026.09)`, flush: true, body: html`<div id="w-claims"></div>` })}
    </div>`);

  ui.dataTable($('#w-cb-table', S.el), {
    rows: cbs, pageSize: 0,
    onRowClick: (r) => openCB(r.chargeback_id),
    columns: [
      { key: 'chargeback_id', label: 'Chargeback', render: (r) => html`<a class="id-link" href="#/warranty?cb=${encodeURIComponent(r.chargeback_id)}" data-open-cb="${r.chargeback_id}">${r.chargeback_id}</a>` },
      { key: 'supplier_name', label: 'Supplier · what for', wrap: true, width: '30%',
        render: (r) => html`${link.supplier(r.supplier_id, r.supplier_name)}<div class="small muted w-cb-what">${r.title}</div>` },
      { key: 'basis', label: 'Basis', render: (r) => html`<span class="small">${fmt.title(r.basis)}</span>` },
      { key: 'amount_usd', label: 'Amount', num: true, render: (r) => money(r.amount_usd) },
      { key: 'status', label: 'Status', wrap: true, render: (r) => html`<div class="w-cb-status">${ui.statusChip(r.status)}${r.overdue ? ui.chip('serious', `${r.age_days}d, no reply`) : ''}</div>` },
      { key: 'age_days', label: 'Since sent', num: true, render: (r) => (r.age_days != null ? fmt.days(r.age_days) : html`<span class="nil">—</span>`) },
      { key: 'je_id', label: 'ERP entry', render: (r) => (r.je_id ? html`<span class="mono small">${r.je_id}</span>` : html`<span class="nil">—</span>`) },
      { key: 'next', label: 'Next step', sortable: false, render: (r) => nextButton(r) },
    ],
  });

  ui.dataTable($('#w-claims', S.el), {
    rows: claims, search: true, searchPlaceholder: 'Filter by claim, serial, lot, symptom…', pageSize: 15,
    initialSort: { key: 'reported_at', dir: 'desc' },
    onRowClick: (r) => openClaim(r.claim_id),
    columns: [
      { key: 'claim_id', label: 'Claim', render: (r) => html`<a class="id-link" href="#/warranty?claim=${encodeURIComponent(r.claim_id)}" data-open-claim="${r.claim_id}">${r.claim_id}</a>` },
      { key: 'reported_at', label: 'Reported', render: (r) => html`<span class="small">${fmt.date(r.reported_at)}</span>` },
      { key: 'serial', label: 'Vehicle', render: (r) => link.serial(r.serial) },
      { key: 'symptom', label: 'Symptom (customer words)', wrap: true, render: (r) => html`<span class="small">${r.symptom}</span>` },
      { key: 'defect_code', label: 'Classified', render: (r) => html`<span class="mono small">${r.defect_code || '—'}</span>${r.conflict ? html` ${ui.chip('warning', 'Review')}` : ''}` },
      { key: 'failed_item_id', label: 'Failed part', render: (r) => (r.failed_item_id ? html`<span class="mono small">${r.failed_item_id}</span>${r.failed_serial ? html` ${link.serial(r.failed_serial)}` : ''}` : html`<span class="nil">—</span>`) },
      { key: 'failed_lot_id', label: 'Lot', render: (r) => link.lot(r.failed_lot_id) },
      { key: 'supplier_name', label: 'Owner', render: (r) => (r.supplier_id ? link.supplier(r.supplier_id, r.supplier_name) : html`<span class="muted small">OEM</span>`) },
      { key: 'cost_usd', label: 'Cost', num: true, render: (r) => fmt.usd(r.cost_usd) },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
      { key: 'chargeback_id', label: 'Recovery', render: (r) => (r.chargeback_id
        ? html`<a class="id-link" href="#/warranty?cb=${encodeURIComponent(r.chargeback_id)}" data-open-cb="${r.chargeback_id}">${r.chargeback_id}</a>`
        : r.billable ? ui.chip('info', 'Billable') : html`<span class="nil">—</span>`) },
    ],
  });
}

// ---------------------------------------------------------------------------
function signalCallout(l) {
  const x = l.baseline > 0 ? l.rate / l.baseline : null;
  const kes = S.sum.unbilled.find((u) => u.supplier_id === l.supplier_id);
  const lotClaims = kes && kes.lots ? kes.lots[l.lot_id] || 0 : 0;
  return ui.callout({
    tone: 'critical',
    title: html`Cell lot ${l.lot_id}: ${fmt.num(l.rate, 1)} capacity-fade claims per 1,000 pack-months in service, against a ${fmt.num(S.sum.baseline_rate, 1)} baseline${x ? html` (${fmt.num(x, 0)}×)` : ''}`,
    body: html`<div>${fmt.int(l.claims)} claims from ${fmt.int(l.packs)} packs in service (${fmt.int(Math.round(l.pack_months))} pack-months). The lot passed incoming inspection (${fmt.title(l.iqc_status)}), so the defect is latent and the field found it first. Containment (hold what we still control) and recovery from the supplier are one decision in the closed loop.</div>
      <div class="w-actions">
        <a class="btn sm" href="#/genealogy?q=${encodeURIComponent(l.lot_id)}">${icon('tree', 14)}<span>Trace the lot forward</span></a>
        <a class="btn sm" href="#/loop">${icon('loop', 14)}<span>Containment decision</span></a>
        ${kes ? html`<button class="btn sm btn-primary" type="button" data-draft="${kes.supplier_id}">${icon('receipt', 14)}<span>Draft chargeback to ${kes.supplier_name} (${fmt.int(kes.claims)} claims, ${lotClaims} on this lot)</span></button>` : ''}
      </div>`,
  });
}

function lotChart(sum) {
  const lots = sum.lots;
  const baseline = sum.baseline_rate;
  const table = {
    columns: ['Cell lot', 'Packs in service', 'Pack-months', 'Capacity-fade claims', 'Per 1,000 pack-months', 'IQC'],
    rows: lots.map((l) => [l.lot_id, fmt.int(l.packs), fmt.int(Math.round(l.pack_months)), fmt.int(l.claims), fmt.num(l.rate, 1), fmt.title(l.iqc_status)]),
    numeric: [true, true, true, true, false],
  };
  return charts.custom({
    height: Math.max(150, lots.length * 32 + 40),
    ariaLabel: 'Capacity-fade claims per 1,000 pack-months in service by cell lot',
    table,
    render: (w, h) => {
      const m = { t: 8, r: 150, b: 24, l: 96 };
      const iw = Math.max(60, w - m.l - m.r);
      const ih = h - m.t - m.b;
      const maxV = Math.max(5, ...lots.map((l) => l.rate)) * 1.05;
      const sx = charts.scaleLinear([0, maxV], [m.l, m.l + iw]);
      const band = charts.scaleBand(lots.map((l) => l.lot_id), [m.t, m.t + ih], 0.34);
      const bh = Math.min(22, band.bandwidth);
      let s = '';
      for (const t of charts.niceTicks(0, maxV, 4)) {
        if (t > maxV) continue;
        const x = crisp(sx(t));
        s += `<line class="gridline" x1="${x}" x2="${x}" y1="${m.t}" y2="${m.t + ih}"/>`;
        s += `<text class="tick" x="${x}" y="${m.t + ih + 16}" text-anchor="middle">${esc(fmt.num(t, 0))}</text>`;
      }
      lots.forEach((l) => {
        const y = band(l.lot_id) + (band.bandwidth - bh) / 2;
        const wv = Math.max(l.rate ? 2 : 0, sx(l.rate) - sx(0));
        const col = l.flag ? 'var(--series-1)' : 'var(--series-other)';
        s += `<text class="cat-label" x="${m.l - 10}" y="${r1(y + bh / 2)}" dy="0.32em" text-anchor="end">${esc(l.lot_id)}</text>`;
        if (wv > 0) {
          const rr = Math.min(4, bh / 2, wv);
          s += `<path class="bar" d="M${r1(sx(0))},${r1(y)}H${r1(sx(0) + wv - rr)}A${rr},${rr} 0 0 1 ${r1(sx(0) + wv)},${r1(y + rr)}V${r1(y + bh - rr)}A${rr},${rr} 0 0 1 ${r1(sx(0) + wv - rr)},${r1(y + bh)}H${r1(sx(0))}Z" style="fill:${col}"/>`;
        }
        const label = `${fmt.num(l.rate, 1)}  ·  ${l.claims} of ${fmt.int(l.packs)}`;
        s += `<text class="value-label" x="${r1(sx(0) + wv + 6)}" y="${r1(y + bh / 2)}" dy="0.32em">${esc(label)}</text>`;
        const tip = `${l.lot_id}${l.flag ? ' · FLAGGED' : ''}\n${l.claims} capacity-fade claims / ${fmt.int(Math.round(l.pack_months))} pack-months (${fmt.int(l.packs)} packs in service)\n${fmt.num(l.rate, 1)} per 1,000 pack-months (baseline ${fmt.num(baseline, 1)})\nReceived ${fmt.date(l.received_at)} · IQC ${fmt.title(l.iqc_status)}`;
        s += `<rect x="0" y="${r1(band(l.lot_id) - (band.step - band.bandwidth) / 2)}" width="${w}" height="${r1(band.step)}" fill="transparent" data-tip="${esc(tip)}"/>`;
      });
      s += `<line class="baseline" x1="${crisp(sx(0))}" x2="${crisp(sx(0))}" y1="${m.t}" y2="${m.t + ih}"/>`;
      if (baseline > 0) {
        const bx = crisp(sx(baseline));
        s += `<line class="ref-line" x1="${bx}" x2="${bx}" y1="${m.t}" y2="${m.t + ih}" style="stroke:var(--ink-3)"/>`;
        s += `<text class="ref-label" x="${bx + 4}" y="${m.t + 8}">${esc(`baseline ${fmt.num(baseline, 1)}`)}</text>`;
      }
      return raw(s);
    },
  });
}

function weeklyChart(weeks) {
  return charts.bar({
    categories: weeks.map((w) => w.week),
    categoryFormat: (c) => fmt.date(c),
    series: [
      { name: 'Capacity fade', values: weeks.map((w) => w.capfade), color: 'var(--series-1)' },
      { name: 'All other failure modes', values: weeks.map((w) => w.other), color: 'var(--series-other)' },
    ],
    stacked: true, height: 230, yFormat: (v) => fmt.int(v), categoryLabel: 'Week of', ariaLabel: 'Claims by week',
  });
}

function pipeline(p) {
  const box = (st) => {
    const v = p[st.id] || { count: 0, amount_usd: 0 };
    return html`<div class="w-stage${v.count ? '' : ' empty'}">
      <div class="w-stage-top">${ui.statusChip(st.id, st.label)}</div>
      <div class="w-stage-num">${fmt.int(v.count)}</div>
      <div class="small num">${fmt.usd(v.amount_usd)}</div>
      <div class="tiny muted">${st.hint}</div>
    </div>`;
  };
  const d = p.DISPUTED;
  return html`<div class="w-pipe">
    ${STAGES.map((st, i) => html`${i ? html`<span class="w-pipe-arrow" aria-hidden="true">${icon('arrow-right', 16)}</span>` : ''}${box(st)}`)}
    <div class="w-stage w-stage-side${d ? '' : ' empty'}">
      <div class="w-stage-top">${ui.statusChip('DISPUTED')}</div>
      <div class="w-stage-num">${fmt.int(d ? d.count : 0)}</div>
      <div class="small num">${fmt.usd(d ? d.amount_usd : 0)}</div>
      <div class="tiny muted">supplier pushed back</div>
    </div>
  </div>`;
}

function nextButton(r) {
  const n = NEXT[r.status];
  if (!n) return r.status === 'POSTED' ? html`<span class="small muted">Done · ${r.debit_memo_no || ''}</span>` : html`<span class="nil">—</span>`;
  return html`<button class="btn sm" type="button" data-cb-act="${n.act}" data-cb="${r.chargeback_id}">${icon(n.icon, 14)}<span>${n.label}</span></button>`;
}

function unbilled(sum) {
  if (!sum.unbilled.length) return ui.empty('Every diagnosed supplier claim is already on a chargeback.');
  return html`<div class="w-unbilled">${sum.unbilled.map((u) => html`<div class="w-ub">
      <div class="w-ub-main">
        <div class="row">${link.supplier(u.supplier_id, u.supplier_name)}<span class="spacer"></span><span class="w-ub-amt">${money(u.recoverable_usd)}</span></div>
        <div class="small muted">${fmt.int(u.claims)} claim${u.claims === 1 ? '' : 's'} · oldest ${fmt.rel(u.oldest)} ·
          ${Object.entries(u.codes).map(([c, n]) => html`<span class="mono">${c}</span> ×${n} `)}</div>
        ${Object.keys(u.lots).length ? html`<div class="small">${Object.entries(u.lots).sort((a, b) => b[1] - a[1]).map(([lot, n]) => html`${link.lot(lot)}<span class="muted"> ×${n}</span> `)}</div>` : ''}
        ${u.needs_review.length ? html`<div class="small">${ui.chip('warning', `${u.needs_review.length} held for review`)}</div>` : ''}
      </div>
      <button class="btn sm" type="button" data-draft="${u.supplier_id}">${icon('receipt', 14)}<span>Draft chargeback</span></button>
    </div>`)}
    ${sum.kpis.pending_diagnosis ? html`<p class="small muted">${fmt.int(sum.kpis.pending_diagnosis)} more supplier claim${sum.kpis.pending_diagnosis === 1 ? ' is' : 's are'} still open, waiting for diagnosis before they can be billed.</p>` : ''}
  </div>`;
}

// ---------------------------------------------------------------------------
// drawers
// ---------------------------------------------------------------------------
async function openClaim(id) {
  if (!S) return;
  S.ctx.setQuery({ claim: id, cb: null }, { silent: true });
  ui.drawer.open({ title: id, body: ui.loading('Loading claim'), width: 640, onClose: () => S && S.ctx.setQuery({ claim: null }, { silent: true }) });
  try {
    const d = await api.get(`/api/warranty/claims/${encodeURIComponent(id)}`);
    const c = d.claim;
    const packs = d.packs || [];
    const steps = d.steps || [];
    ui.drawer.open({
      title: `${c.claim_id} · ${c.defect_desc || c.defect_code || 'Unclassified'}`,
      subtitle: html`${ui.statusChip(c.status)}<span class="mono">${c.case_no || ''}</span>${c.supplier_id ? html`<span>${c.supplier_name}</span>` : html`<span>OEM-owned failure</span>`}`,
      width: 640,
      onClose: () => S && S.ctx.setQuery({ claim: null }, { silent: true }),
      body: html`
        ${c.conflict ? ui.callout({
          tone: 'warning', title: 'Classification conflicts with the service record',
          body: html`<div>${c.conflict}</div><div class="small">The symptom classifier is scored on a labeled set in <a href="#/proof">Tests · Evals · Review</a> (EV-WARRANTY-CLS). A conflicted claim is excluded from automatic billing.</div>`,
        }) : ''}
        <blockquote class="w-quote">“${c.symptom}”</blockquote>
        <dl class="kv">
          <dt>Reported</dt><dd>${fmt.dt(c.reported_at)}${c.days_in_service != null ? html` <span class="muted">· ${c.days_in_service} days after delivery</span>` : ''}</dd>
          <dt>Vehicle</dt><dd>${link.serial(c.serial)} <span class="muted small">${c.ship_to_state || ''}${c.ship_to_region ? ` · ${fmt.title(c.ship_to_region)}` : ''}</span></dd>
          <dt>Order</dt><dd>${link.order(c.order_id)}</dd>
          <dt>Classified as</dt><dd><span class="mono">${c.defect_code || '—'}</span> <span class="muted small">rules-2026.09 · CRM category ${c.crm_category || '—'}</span></dd>
          <dt>Failed part</dt><dd>${c.failed_item_id ? html`<span class="mono">${c.failed_item_id}</span> ${c.failed_item_name || ''}` : '—'}${c.failed_serial ? html` · ${link.serial(c.failed_serial)}` : ''}</dd>
          <dt>Lot</dt><dd>${c.failed_lot_id ? html`${link.lot(c.failed_lot_id)}${d.lot ? html` <span class="muted small">· ${fmt.int(d.lot.claims)} claims across ${fmt.int(d.lot.packs)} packs · IQC ${fmt.title(d.lot.iqc_status)}${d.lot.iqc_n != null ? ` (${d.lot.iqc_bad} of ${d.lot.iqc_n})` : ''}</span>` : ''}` : '—'}</dd>
          <dt>Responsible</dt><dd>${c.supplier_id ? link.supplier(c.supplier_id, c.supplier_name) : 'OEM (not recoverable)'}</dd>
          <dt>Cost</dt><dd class="num">${fmt.usd(c.cost_usd)} <span class="muted small">parts ${fmt.usd(c.cost_parts_usd)} · labor ${fmt.usd(c.cost_labor_usd)} · logistics ${fmt.usd(c.cost_logistics_usd)}</span></dd>
          <dt>Recovery</dt><dd>${c.chargeback_id
            ? html`<a class="id-link" href="#/warranty?cb=${encodeURIComponent(c.chargeback_id)}" data-open-cb="${c.chargeback_id}">${c.chargeback_id}</a> ${ui.statusChip(c.cb_status)}`
            : d.recovery ? html`${money(d.recovery.amount_usd)} recoverable <span class="muted small">(not yet billed)</span>` : html`<span class="muted">Not recoverable</span>`}</dd>
        </dl>
        ${d.recovery && !c.chargeback_id ? html`<div class="w-math small"><span class="muted">Under ${c.supplier_name}'s terms:</span> ${d.recovery.math} = <strong>${money(d.recovery.amount_usd)}</strong></div>` : ''}
        <div class="w-actions">
          ${c.failed_lot_id ? html`<a class="btn sm" href="#/genealogy?q=${encodeURIComponent(c.failed_lot_id)}">${icon('tree', 14)}<span>Trace lot ${c.failed_lot_id}</span></a>` : ''}
          <a class="btn sm" href="#/genealogy?q=${encodeURIComponent(c.serial)}">${icon('tree', 14)}<span>Vehicle as-built</span></a>
          ${c.billable ? html`<button class="btn sm btn-primary" type="button" data-draft="${c.supplier_id}">${icon('receipt', 14)}<span>Draft chargeback to ${c.supplier_name}</span></button>` : ''}
        </div>
        ${packs.length ? html`<p class="section-title">Packs on this vehicle (as-maintained genealogy)</p>
          ${ui.timeline(packs.flatMap((p) => {
            const items = [{ ts: p.installed_at, tone: p.source === 'SERVICE' ? 'info' : 'neutral',
              title: html`${link.serial(p.serial)} <span class="mono small muted">${p.item_id}</span>`,
              meta: p.source === 'SERVICE' ? 'service install' : p.position === 'EXTRA_PACK' ? 'extra pack, shipped with order' : 'shipped with order',
              detail: p.source === 'SERVICE' ? 'Replacement pack from 3PL service stock' : 'Married to the vehicle at 3PL kitting' }];
            if (p.removed_at) {
              items.push({ ts: p.removed_at, tone: 'serious', title: html`${link.serial(p.serial)} removed`, meta: 'as-maintained change', detail: p.removal_reason });
            }
            return items;
          }).sort((a, b) => String(a.ts).localeCompare(String(b.ts))))}` : ''}
        <p class="section-title">CRM case, as received</p>
        ${d.raw ? html`<div class="small muted">raw_warranty_case #${d.raw.raw_id} · received ${fmt.dt(d.raw.received_at)} · ${fmt.title(d.raw.ingest_status)}${d.raw.ingest_note ? ` · ${d.raw.ingest_note}` : ''}</div>
          ${ui.codeBlock(ui.prettyJSON(d.raw.payload), 'json')}` : ui.empty('No raw case on file.')}
        ${steps.length ? html`<p class="section-title">Ingest trace</p>${ui.timeline(steps.map((s) => ({ ts: s.at, tone: s.status === 'OK' ? 'good' : 'warning', title: fmt.title(s.step), detail: s.detail })))}` : ''}`,
    });
  } catch (err) {
    ui.drawer.open({ title: id, body: ui.errorBox(err) });
  }
}

async function openCB(id) {
  if (!S) return;
  S.ctx.setQuery({ cb: id, claim: null }, { silent: true });
  ui.drawer.open({ title: id, body: ui.loading('Loading chargeback'), width: 700, onClose: () => S && S.ctx.setQuery({ cb: null }, { silent: true }) });
  try {
    const d = await api.get(`/api/warranty/chargebacks/${encodeURIComponent(id)}`);
    const cb = d.chargeback;
    const t = d.terms || {};
    const n = NEXT[cb.status];
    const conflicts = (d.claims || []).filter((c) => c.conflict);
    const debit = d.je_lines.reduce((a, l) => a + l.debit_usd, 0);
    const credit = d.je_lines.reduce((a, l) => a + l.credit_usd, 0);
    const claimById = Object.fromEntries((d.claims || []).map((c) => [c.claim_id, c]));
    ui.drawer.open({
      title: `${cb.chargeback_id} · ${cb.title}`,
      subtitle: html`${ui.statusChip(cb.status)}${cb.overdue ? ui.chip('serious', `${cb.age_days}d, no reply`) : ''}<span>${cb.supplier_name}</span><span class="muted">${fmt.title(cb.basis)}</span>`,
      width: 700,
      onClose: () => S && S.ctx.setQuery({ cb: null }, { silent: true }),
      body: html`
        ${n ? html`<div class="w-actions"><button class="btn btn-primary" type="button" data-cb-act="${n.act}" data-cb="${cb.chargeback_id}">${icon(n.icon, 15)}<span>${n.label}</span></button>
          <span class="small muted">${cb.status === 'ACCEPTED' ? 'Posts a debit memo: debit accounts payable, credit the recovery account.' : cb.status === 'DRAFT' ? `Publishes it to the supplier portal; they have ${t.response_days || 21} days to respond.` : 'Simulates the supplier accepting in the portal.'}</span></div>` : ''}
        ${cb.status === 'DISPUTED' ? ui.callout({
          tone: 'serious', title: 'Disputed by the supplier',
          body: html`<div>${cb.notes || ''}</div>${conflicts.length ? html`<div>Evidence check: ${conflicts.length} of ${d.claims.length} claims on this chargeback conflict with their own service record (${conflicts.map((c, i) => html`${i ? ', ' : ''}<a class="id-link" href="#/warranty?claim=${encodeURIComponent(c.claim_id)}" data-open-claim="${c.claim_id}">${c.claim_id}</a>`)}), so part of the dispute is fair. Pull those lines and re-issue.</div>` : ''}`,
        }) : ''}
        <dl class="kv">
          <dt>Supplier</dt><dd>${link.supplier(cb.supplier_id, cb.supplier_name)}</dd>
          <dt>Amount</dt><dd class="num strong">${money(cb.amount_usd)}${d.lines_total !== cb.amount_usd ? html` ${ui.chip('critical', `lines sum to ${money(d.lines_total)}`)}` : html` <span class="muted small">= sum of ${d.lines.length} lines</span>`}</dd>
          <dt>Created</dt><dd>${fmt.dt(cb.created_at)}</dd>
          <dt>Sent</dt><dd>${cb.sent_at ? html`${fmt.dt(cb.sent_at)} <span class="muted small">· response due in ${t.response_days || 21} days${cb.age_days != null ? ` · ${cb.age_days} days elapsed` : ''}</span>` : '—'}</dd>
          <dt>Supplier response</dt><dd>${cb.responded_at ? fmt.dt(cb.responded_at) : '—'}</dd>
          <dt>Posted</dt><dd>${cb.posted_at ? html`${fmt.dt(cb.posted_at)} · <span class="mono">${cb.debit_memo_no}</span> · <span class="mono">${cb.je_id}</span>` : '—'}</dd>
          ${cb.decision_id ? html`<dt>Decision</dt><dd><a class="id-link" href="#/loop">${cb.decision_id}</a></dd>` : ''}
        </dl>
        ${ui.card({
          title: 'Recovery terms (contract)',
          body: html`<div class="w-terms">
            <div><span class="muted small">Parts</span><strong>${Math.round((t.parts_pct || 0) * 100)}%</strong></div>
            <div><span class="muted small">Labor</span><strong>$${t.labor_rate_usd}/h, cap ${t.labor_hours_cap}h</strong></div>
            <div><span class="muted small">Admin fee</span><strong>$${t.admin_fee_usd} per claim</strong></div>
            <div><span class="muted small">Containment</span><strong>${fmt.title(t.containment || 'actuals')}</strong></div>
            <div><span class="muted small">Response window</span><strong>${t.response_days || 21} days</strong></div>
          </div>`,
        })}
        ${ui.card({
          title: `Lines (${d.lines.length})`, flush: true,
          body: html`<div class="table-wrap"><table class="table dense">
            <thead><tr><th class="num">#</th><th>Reference</th><th>How it was priced</th><th class="num">Amount</th></tr></thead>
            <tbody>${d.lines.map((l) => html`<tr>
              <td class="num">${l.line_no}</td>
              <td>${l.ref_type === 'CLAIM' ? html`<a class="id-link" href="#/warranty?claim=${encodeURIComponent(l.ref_id)}" data-open-claim="${l.ref_id}">${l.ref_id}</a>${claimById[l.ref_id] && claimById[l.ref_id].conflict ? html` ${ui.chip('warning', 'Conflict')}` : ''}` : l.ref_id ? html`<span class="mono small">${l.ref_id}</span>` : html`<span class="muted small">${fmt.title(l.ref_type)}</span>`}</td>
              <td class="wrap small">${l.description}</td><td class="num">${money(l.amount_usd)}</td></tr>`)}
              <tr class="w-total"><td></td><td colspan="2" class="strong">Total</td><td class="num strong">${money(d.lines_total)}</td></tr></tbody></table></div>`,
        })}
        ${d.je ? ui.card({
          title: `ERP journal entry ${d.je.je_id}`,
          subtitle: html`${fmt.title(d.je.doc_type)} · posted ${fmt.dt(d.je.posted_at)} · ${d.je.memo}`,
          flush: true,
          body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>GL account</th><th>Cost center</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead>
            <tbody>${d.je_lines.map((l) => html`<tr><td><span class="mono">${l.gl_account}</span> ${l.gl_name}</td><td class="small">${l.cost_center || ''}</td>
              <td class="num">${l.debit_usd ? money(l.debit_usd) : ''}</td><td class="num">${l.credit_usd ? money(l.credit_usd) : ''}</td></tr>`)}
            <tr class="w-total"><td class="strong">Totals</td><td>${d.balanced ? ui.chip('good', 'Balanced') : ui.chip('critical', 'Out of balance')}</td>
              <td class="num strong">${money(debit)}</td><td class="num strong">${money(credit)}</td></tr></tbody></table></div>`,
        }) : ''}
        ${d.messages.length ? html`<p class="section-title">Writes to other systems</p>${ui.timeline(d.messages.map((m) => ({
          ts: m.created_at, tone: m.status === 'ACKED' ? 'good' : 'info',
          title: html`${fmt.title(m.target_system)} · ${fmt.title(m.message_type)}`, meta: fmt.title(m.status),
          detail: html`<span class="mono small">${m.payload}</span>`,
        })))}` : ''}
        ${d.decisions.length ? html`<p class="section-title">Decision trail</p>${ui.timeline(d.decisions.map((x) => ({
          ts: x.executed_at, tone: 'good', title: x.title, meta: html`<span class="mono">${x.decision_id}</span> · ${x.decided_by || ''}`,
        })))}` : ''}`,
    });
  } catch (err) {
    ui.drawer.open({ title: id, body: ui.errorBox(err) });
  }
}

// ---------------------------------------------------------------------------
// actions (each is a decision with writes)
// ---------------------------------------------------------------------------
async function act(btn) {
  const id = btn.dataset.cb;
  const verb = btn.dataset.cbAct;
  btn.disabled = true;
  try {
    const res = await api.post(`/api/warranty/chargebacks/${encodeURIComponent(id)}/${verb}`, {});
    const msg = verb === 'send' ? `${id} sent to the supplier portal; response due ${fmt.date(res.response_due)}.`
      : verb === 'accept' ? `${id} marked accepted (simulated supplier response).`
        : `${id} posted: debit memo ${res.debit_memo_no}, journal entry ${res.je_id}.`;
    ui.toast(`${msg} Logged as ${res.decision_id}.`, 'good', 5000);
    await afterWrite(id);
  } catch (err) {
    btn.disabled = false;
    ui.toast(`${verb} failed: ${err.message}`, 'critical', 6000);
  }
}

async function draft(btn) {
  const sup = btn.dataset.draft;
  btn.disabled = true;
  try {
    const res = await api.post('/api/warranty/chargebacks/draft', { supplier_id: sup });
    ui.toast(`Drafted ${res.chargeback_id}: ${res.claims} claims, ${money(res.amount_usd)}. Logged as ${res.decision_id}.`, 'good', 5000);
    await afterWrite(res.chargeback_id);
  } catch (err) {
    btn.disabled = false;
    ui.toast(`Draft failed: ${err.message}`, 'critical', 6000);
  }
}

async function afterWrite(cbId) {
  if (!S) return;
  window.dispatchEvent(new Event('ops:meta-changed'));
  await load();
  draw();
  openCB(cbId);
}

// ---------------------------------------------------------------------------
const PAGE_CSS = `
.pg-warranty .w-cb-what { margin-top: 2px; line-height: 1.35; min-width: 16em; }
.pg-warranty .w-cb-status { display: flex; flex-wrap: wrap; gap: 4px; }
.pg-warranty .w-actions, .drawer .w-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 10px; }
.drawer .w-actions { margin-top: 0; }
.pg-warranty .w-pipe { display: flex; align-items: stretch; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.pg-warranty .w-pipe-arrow { display: flex; align-items: center; color: var(--ink-3); }
.pg-warranty .w-stage { flex: 1 1 130px; min-width: 120px; display: flex; flex-direction: column; gap: 3px; padding: 10px 12px; border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface-2); }
.pg-warranty .w-stage.empty { opacity: .6; }
.pg-warranty .w-stage-side { margin-left: 18px; border-style: dashed; }
.pg-warranty .w-stage-num { font: 600 24px/1.1 var(--font-ui); }
.pg-warranty .w-unbilled { display: flex; flex-direction: column; gap: 10px; }
.pg-warranty .w-ub { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--hairline); border-radius: 10px; }
.pg-warranty .w-ub-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.pg-warranty .w-ub-amt { font: 600 16px/1 var(--font-ui); font-variant-numeric: tabular-nums; }
.drawer .w-quote { margin: 0; padding: 10px 14px; border-left: 3px solid var(--hairline-strong); background: var(--surface-2); border-radius: 0 8px 8px 0; font-size: 14px; color: var(--ink); }
.drawer .w-math { padding: 8px 10px; border: 1px dashed var(--hairline-strong); border-radius: 8px; }
.drawer .w-terms { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px 16px; }
.drawer .w-terms > div { display: flex; flex-direction: column; gap: 2px; }
.drawer .w-total td { background: var(--surface-2); }
@media (max-width: 700px) {
  .pg-warranty .w-stage-side { margin-left: 0; }
  .pg-warranty .w-pipe-arrow { display: none; }
  .pg-warranty .w-ub { flex-direction: column; align-items: stretch; }
}
`;
