// Supplier Loop: PO confirmations and promise dates traced to the email, Excel row or
// EDI segment they came from; the forecast released to tiers 1-3 against what suppliers
// committed; RFQs by total cost of ownership; ECO cut-ins as they actually happened;
// pricing with effectivity; and a scorecard computed from receipts, not opinions.
import { html, raw, esc, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const TABS = [
  { id: 'pos', label: 'POs & promise dates', icon: 'exchange' },
  { id: 'forecast', label: 'Forecast to tiers 1-3', icon: 'layers' },
  { id: 'rfqs', label: 'RFQs', icon: 'scale' },
  { id: 'ecos', label: 'ECOs & effectivity', icon: 'code' },
  { id: 'pricing', label: 'Pricing', icon: 'ledger' },
  { id: 'scorecard', label: 'Scorecard', icon: 'target' },
];
const CHANNEL = { EDI855: 'EDI 855', PORTAL: 'Portal', EMAIL: 'Email', EXCEL: 'Excel', BUYER: 'Buyer', EXPEDITE: 'Expedite' };
const FLAG = {
  SLIPPED: ['serious', 'Slipped'],
  UNCONFIRMED_72H: ['warning', 'Unconfirmed 72h+'],
  ACK_QUARANTINED: ['critical', 'Ack quarantined'],
  EMAIL_ABSTAINED: ['warning', 'Parser abstained'],
  PRICE_EXPOSURE: ['warning', 'Price above contract'],
};
const SHORTFALL_STEPS = [
  { max: 0.005, cls: 'hf0', label: 'Covered' },
  { max: 0.05, cls: 'hf1', label: '≤5%' },
  { max: 0.12, cls: 'hf2', label: '≤12%' },
  { max: 0.2, cls: 'hf3', label: '≤20%' },
  { max: 0.3, cls: 'hf4', label: '≤30%' },
  { max: Infinity, cls: 'hf5', label: '>30%' },
];

let S = null;

const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
// Drawer close: drop our keys from the URL, but only if the live hash still carries them.
// (When the router navigates away it closes the drawer first; the new hash must survive.)
const clearKeys = (keys) => {
  if (!S) return;
  const live = new URLSearchParams((location.hash.split('?')[1]) || '');
  if (!location.hash.startsWith('#/suppliers') || !keys.some((k) => live.has(k))) return;
  S.ctx.setQuery(Object.fromEntries(keys.map((k) => [k, null])), { silent: true });
};
const qtyFmt = (v, uom) => (uom === 'kg' ? `${fmt.num(v, 0)} kg` : fmt.int(v));
const shortCls = (cover) => {
  if (cover == null) return 'hf-none';
  const s = Math.max(0, 1 - cover);
  return SHORTFALL_STEPS.find((x) => s <= x.max).cls;
};

// ---------------------------------------------------------------------------
export async function render(el, ctx) {
  injectStyle('page-suppliers', PAGE_CSS);
  const ov = await api.get('/api/suppliers/overview');
  S = { el, ctx, ov, cache: {}, off: [] };
  const k = ov.kpis;
  let tab = ctx.query.get('tab');
  if (!TABS.some((t) => t.id === tab)) tab = 'pos';
  const afe = ov.story.afe_slip;
  const hmi = ov.story.hmi_unconfirmed;

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-suppliers">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Open PO lines', value: fmt.int(k.open_lines), hint: `${fmt.usd(k.open_value, { compact: true })} still to receive` })}
        ${ui.kpi({ label: 'Unconfirmed', value: fmt.int(k.unconfirmed), hint: `${k.unconfirmed_72h} past 72 hours`, status: k.unconfirmed_72h ? { tone: 'warning', label: `${k.unconfirmed_72h} late` } : null })}
        ${ui.kpi({ label: 'Late promises', value: fmt.int(k.slipped), hint: k.slipped ? `promised after need, avg ${fmt.num(k.slip_days_avg, 1)} days` : 'none' })}
        ${ui.kpi({ label: 'Held for a human', value: fmt.int(k.quarantined_inputs), hint: 'acks and emails the parsers would not guess at' })}
        ${ui.kpi({ label: 'Over contract', value: fmt.usd(k.price_exposure_usd, { compact: true }), hint: `${k.price_exposure_lines} open lines priced above the contract at delivery` })}
        ${ui.kpi({ label: 'Tier-1 commit coverage', value: fmt.pct(k.tier1_commit_coverage, 1), hint: `${k.latest_release}; silent: ${k.silent_suppliers.join(', ') || 'none'}` })}
      </div>
      <div class="grid sp-stories">
        ${afe ? html`<div class="span-6">${ui.callout({
          tone: 'serious',
          title: html`Pinecrest pushed BMS-B ${link.po(afe.po_id, afe.line_no)} from ${fmt.date(afe.prev_promise || afe.need_date)} to ${fmt.date(afe.promise_date)}`,
          body: html`It arrived as a changed ETA in Pinecrest's weekly open-order workbook, ${fmt.rel(afe.recorded_at)}, with the remark “${afe.note}”. The row is traced in the PO drawer. Tier-2 Microvolt had committed only 70% of AFE ICs weeks earlier: ${html`<a href="#" data-goto-tab="forecast">see the early warning</a>`}. Downstream: ${link.route('mrp', 'MRP line-stop date')} and ${html`<a href="#" data-goto-tab="rfqs">RFQ-0012 second source</a>`}.`,
        })}</div>` : ''}
        ${hmi ? html`<div class="span-6">${ui.callout({
          tone: 'warning',
          title: html`${link.po(hmi.po_id, hmi.line_no)} (${fmt.int(hmi.qty)} HMI) still has no usable promise, ${fmt.int(hmi.age_hours / 24)} days after the PO`,
          body: html`Hsinchu Display's EDI 855 came back with ACK status <span class="mono">BP</span> (partial shipment, balance backordered). The mapping doesn't know that code, so the ack sits in quarantine instead of being guessed into a promise date. Need date ${fmt.date(hmi.need_date)}. Fix the mapping, or call the supplier.`,
        })}</div>` : ''}
      </div>
      <div class="sp-tabs"></div>
      <div class="sp-body"></div>
    </div>`;

  S.tabs = ui.tabs($('.sp-tabs', el), {
    tabs: TABS.map((t) => ({ ...t, count: t.id === 'pos' ? k.open_lines : null })),
    active: tab,
    onChange: (id) => { ctx.setQuery({ tab: id }, { silent: true }); showTab(id); },
  });

  on(el, 'click', '[data-goto-tab]', (e, a) => {
    e.preventDefault();
    const id = a.dataset.gotoTab;
    S.tabs.set(id);
    ctx.setQuery({ tab: id }, { silent: true });
    showTab(id);
    $('.sp-tabs', el).scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  // same-page entity links open drawers instead of re-rendering the page
  const onDocClick = (e) => {
    const a = e.target.closest && e.target.closest('a[href^="#/suppliers?"]');
    if (!a) return;
    const usp = new URLSearchParams(a.getAttribute('href').split('?')[1]);
    if (usp.get('po')) { e.preventDefault(); openPO(usp.get('po'), usp.get('line')); }
    else if (usp.get('supplier')) { e.preventDefault(); openSupplier(usp.get('supplier')); }
  };
  document.addEventListener('click', onDocClick);
  S.off.push(() => document.removeEventListener('click', onDocClick));

  await showTab(tab);
  if (ctx.query.get('po')) openPO(ctx.query.get('po'), ctx.query.get('line'));
  else if (ctx.query.get('supplier')) openSupplier(ctx.query.get('supplier'));
}

export function unmount() {
  if (S) S.off.forEach((f) => f());
  S = null;
}

async function showTab(id) {
  const outer = $('.sp-body', S.el);
  outer.innerHTML = String(ui.loading());
  // a fresh host per render, so delegated listeners never pile up on a reused element
  const body = document.createElement('div');
  try {
    if (id === 'pos') await tabPOs(body);
    else if (id === 'forecast') await tabForecast(body);
    else if (id === 'rfqs') await tabRFQs(body);
    else if (id === 'ecos') await tabECOs(body);
    else if (id === 'pricing') await tabPricing(body);
    else if (id === 'scorecard') await tabScorecard(body);
    if (!S) return;
    outer.innerHTML = '';
    outer.appendChild(body);
  } catch (err) {
    console.error(err);
    outer.innerHTML = String(ui.errorBox(err));
  }
}

// ---------------------------------------------------------------------------
// POs & promise dates
// ---------------------------------------------------------------------------
function flagChips(flags) {
  if (!flags || !flags.length) return html`<span class="nil">—</span>`;
  return html`<span class="sp-flags">${flags.map((f) => ui.chip(FLAG[f][0], FLAG[f][1]))}</span>`;
}

function slipChip(d) {
  if (d == null || d <= 0) return html`<span class="nil">—</span>`;
  return ui.chip(d >= 5 ? 'serious' : 'warning', `+${d}d`);
}

async function tabPOs(body) {
  const data = S.cache.lines || (S.cache.lines = await api.get('/api/suppliers/lines', { scope: 'all' }));
  const all = data.rows;
  const f = { supplier: S.ctx.query.get('supplier_f') || '', item: '', scope: 'open', flag: 'all' };
  const asOf = S.ov.as_of;
  const since60 = new Date(Date.parse(asOf) - 60 * 86400000).toISOString().slice(0, 10);

  // median hours to confirm by channel, lines created in the last 60 days
  const byCh = {};
  for (const r of all) {
    if (r.hours_to_confirm == null || (r.po_created_at || '') < since60 || r.supplier_id === 'FAP') continue;
    (byCh[r.channel] = byCh[r.channel] || []).push(r.hours_to_confirm);
  }
  const med = (a) => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const chOrder = ['EDI855', 'PORTAL', 'EMAIL', 'EXCEL'].filter((c) => byCh[c]);
  const chChart = charts.bar({
    categories: chOrder.map((c) => CHANNEL[c]),
    series: [{ name: 'Median hours to promise', values: chOrder.map((c) => Math.round(med(byCh[c]) * 10) / 10) }],
    horizontal: true, height: 150, yFormat: (v) => `${fmt.num(v, 0)} h`, valueLabels: true,
  });

  // open lines by need week: promise on time vs behind vs unconfirmed
  const open = all.filter((r) => r.status === 'OPEN');
  const weekOf = (d) => { const t = new Date(d + 'T00:00:00Z'); const wd = (t.getUTCDay() + 6) % 7; t.setUTCDate(t.getUTCDate() - wd); return t.toISOString().slice(0, 10); };
  const wkSet = [...new Set(open.map((r) => weekOf(r.need_date)))].sort().slice(0, 10);
  const bucket = (pred) => wkSet.map((w) => open.filter((r) => weekOf(r.need_date) === w && pred(r)).length);
  const riskChart = charts.bar({
    categories: wkSet.map((w) => fmt.date(w)),
    series: [
      { name: 'Promised on time', values: bucket((r) => r.promise_date && r.promise_date <= r.need_date), color: 'var(--good)' },
      { name: 'Promised late', values: bucket((r) => r.promise_date && r.promise_date > r.need_date), color: 'var(--serious)' },
      { name: 'Unconfirmed', values: bucket((r) => !r.promise_date), color: 'var(--warning)' },
    ],
    stacked: true, height: 190, yFormat: (v) => fmt.int(v),
  });

  const sups = S.ov.suppliers;
  const items = S.ov.items;
  body.innerHTML = html`
    <div class="filter-row sp-filters">
      <label class="sp-lbl">Supplier <select class="select sm" data-f="supplier"><option value="">All suppliers</option>${sups.map((s) => html`<option value="${s.supplier_id}">${s.name}</option>`)}</select></label>
      <label class="sp-lbl">Item <select class="select sm" data-f="item"><option value="">All items</option>${items.map((i) => html`<option value="${i.item_id}">${i.item_id} · ${i.name}</option>`)}</select></label>
      <span class="sp-scope"></span>
      <span class="sp-flagf"></span>
    </div>
    <div class="grid">
      <div class="span-7">${ui.card({ title: 'Open lines by need week', subtitle: 'Where the unconfirmed and late promises sit in time', body: riskChart, tableToggle: true })}</div>
      <div class="span-5">${ui.card({ title: 'Hours from PO to promise, by channel', subtitle: 'Median, lines created in the last 60 days. The channel is the latency.', body: html`${chChart}<p class="small muted sp-note">Email and Excel promises now parse automatically; see the ${link.route('integrations', 'Integration Hub')} for the steps and the options that remove the wait.</p>`, tableToggle: true })}</div>
      <div class="span-12">${ui.card({ title: 'PO lines', subtitle: 'Need date is what the plan requires; promise is what the supplier committed, through whichever channel they use. Click a line to trace every promise to its source document.', body: html`<div class="sp-lines"></div>`, flush: true })}</div>
    </div>`;

  const cols = [
    { key: 'po_line', label: 'PO line', value: (r) => `${r.po_id}-${String(r.line_no).padStart(2, '0')}`, render: (r) => link.po(r.po_id, r.line_no), mono: true },
    { key: 'supplier_name', label: 'Supplier', render: (r) => html`<span class="sp-sup">${r.supplier_name}${r.tier > 1 ? html` <span class="muted small">T${r.tier}</span>` : ''}</span>` },
    { key: 'item_id', label: 'Item', render: (r) => html`<span class="mono">${r.item_id}</span>` },
    { key: 'open_qty', label: 'Open qty', num: true, value: (r) => r.qty - (r.received_qty || 0), format: (v) => fmt.int(v) },
    { key: 'need_date', label: 'Need', format: (v) => fmt.date(v) },
    { key: 'promise_date', label: 'Promise', render: (r) => (r.promise_date ? fmt.date(r.promise_date) : ui.chip('warning', 'None')) },
    { key: 'slip_days', label: 'Slip', num: true, render: (r) => slipChip(r.promise_date ? r.slip_days : null) },
    { key: 'channel', label: 'Channel', render: (r) => html`<span class="sp-ch">${CHANNEL[r.channel] || r.channel}</span>` },
    { key: 'hours_to_confirm', label: 'Hrs to confirm', num: true, format: (v) => fmt.num(v, 0) },
    { key: 'flags', label: 'Flags', value: (r) => r.flags.join(' '), render: (r) => flagChips(r.flags), sortable: false },
  ];
  const filter = () => all.filter((r) => (f.scope === 'all' || r.status === 'OPEN')
    && (!f.supplier || r.supplier_id === f.supplier) && (!f.item || r.item_id === f.item)
    && (f.flag === 'all' || (f.flag === 'SLIPPED' && r.flags.includes('SLIPPED'))
      || (f.flag === 'UNCONF' && r.confirm_status === 'UNCONFIRMED' && r.status === 'OPEN')
      || (f.flag === 'QUAR' && (r.flags.includes('ACK_QUARANTINED') || r.flags.includes('EMAIL_ABSTAINED')))
      || (f.flag === 'PRICE' && r.flags.includes('PRICE_EXPOSURE'))));
  const table = ui.dataTable($('.sp-lines', body), {
    columns: cols, rows: filter(), pageSize: 20, search: true, searchPlaceholder: 'Filter lines…',
    initialSort: { key: 'need_date', dir: 'asc' }, onRowClick: (r) => openPO(r.po_id, r.line_no),
  });
  ui.segmented($('.sp-scope', body), {
    options: [{ value: 'open', label: 'Open' }, { value: 'all', label: 'All lines' }], value: f.scope,
    onChange: (v) => { f.scope = v; table.update(filter()); },
  });
  ui.segmented($('.sp-flagf', body), {
    options: [{ value: 'all', label: 'Any flag' }, { value: 'SLIPPED', label: 'Slipped' }, { value: 'UNCONF', label: 'Unconfirmed' },
      { value: 'QUAR', label: 'Quarantined' }, { value: 'PRICE', label: 'Price' }],
    value: f.flag, onChange: (v) => { f.flag = v; table.update(filter()); },
  });
  on(body, 'change', 'select[data-f]', (e, s) => { f[s.dataset.f] = s.value; table.update(filter()); });
}

// ---------------------------------------------------------------------------
// PO drawer: every promise traced to its source document
// ---------------------------------------------------------------------------
function sourceBlock(src) {
  if (!src) return html`<span class="muted small">No source document recorded (entered by a buyer).</span>`;
  const hub = html`<a class="ent-link small" href="#/integrations?ref=${encodeURIComponent(src.email_ref || src.ref)}">${icon('external', 13)} Integration Hub</a>`;
  if (src.kind === 'excel') {
    const head = src.header || [];
    const hi = head.findIndex((h) => /eta|到貨/i.test(String(h || '')));
    // the columns the parser actually reads; description and order qty stay in the file
    const keep = head.map((h, i) => i).filter((i) => /po no|採購|line|項次|part|料號|open|未交|etd|出貨|eta|到貨|remark|備註/i.test(String(head[i] || '')));
    return html`<div class="sp-src">
      <div class="sp-src-head">${icon('table', 14)}<span class="mono">${src.filename}</span><span class="muted small">from ${src.from} · ${fmt.dt(src.received_at)}</span><span class="spacer"></span>${hub}</div>
      ${src.row ? html`<div class="table-wrap sp-xl"><table class="table dense"><thead><tr><th class="muted">Row</th>${keep.map((i) => html`<th class="${i === hi ? 'sp-hot' : ''}">${head[i] ?? ''}</th>`)}</tr></thead>
        <tbody><tr><td class="muted mono">${src.row_no ?? ''}</td>${keep.map((i) => { const c = src.row[i]; return html`<td class="${i === hi ? 'sp-hot' : ''} ${typeof c === 'number' ? 'num' : ''}">${c ?? ''}</td>`; })}</tr></tbody></table></div>
        <p class="tiny muted">Sheet “${src.sheet}”, row ${src.row_no}. The header was found on row ${src.header_row} by matching the bilingual column names. The ETA column is the promise; description and order quantity are omitted here.</p>`
        : html`<p class="small muted">${src.note || 'Workbook parsed; row not located.'}</p>`}
    </div>`;
  }
  if (src.kind === 'email') {
    return html`<div class="sp-src">
      <div class="sp-src-head">${icon('link', 14)}<span>${src.subject}</span><span class="muted small">from ${src.from} · ${fmt.dt(src.received_at)}</span><span class="spacer"></span>${hub}</div>
      <pre class="sp-mail">${src.body}</pre>
      ${src.note ? html`<p class="tiny muted">Parser: ${src.note}</p>` : ''}
    </div>`;
  }
  const payload = src.kind === 'edi' ? src.payload.split('~').filter(Boolean).join('~\n') + '~' : ui.prettyJSON(src.payload);
  return html`<div class="sp-src">
    <div class="sp-src-head">${icon('code', 14)}<span>${src.kind === 'edi' ? 'EDI 855 purchase order acknowledgment' : 'Supplier portal confirmation'}</span><span class="muted small">${fmt.dt(src.received_at)}</span><span class="spacer"></span>${hub}</div>
    ${ui.codeBlock(payload, src.kind === 'edi' ? 'text' : 'json')}
    ${src.note ? html`<p class="tiny muted">Ingest: ${src.note}</p>` : ''}
  </div>`;
}

function lineDetail(l) {
  let prev = null;
  const items = (l.history || []).map((h) => {
    const moved = prev && prev !== h.promise_date ? days(prev, h.promise_date) : null;
    prev = h.promise_date;
    return {
      ts: h.recorded_at,
      tone: moved > 0 ? 'serious' : moved < 0 ? 'good' : 'info',
      meta: html`<span class="sp-ch">${CHANNEL[h.channel] || h.channel}</span>`,
      title: html`Promise ${fmt.dateLong(h.promise_date)}${h.promise_qty ? html` · ${fmt.int(h.promise_qty)}` : ''}${moved ? html` <span class="sp-moved ${moved > 0 ? 'late' : 'early'}">${moved > 0 ? '+' : '−'}${Math.abs(moved)}d</span>` : ''}`,
      detail: html`${h.note ? html`<div class="small">“${h.note}”</div>` : ''}${sourceBlock(h.source)}`,
    };
  });
  for (const qx of l.quarantined || []) {
    items.push({
      ts: qx.received_at, tone: 'critical', meta: html`<span class="sp-ch">${CHANNEL[qx.channel] || qx.channel}</span>`,
      title: html`Held in quarantine: not applied`,
      detail: html`<div class="small">${qx.reason}</div>${sourceBlock(qx.source)}`,
    });
  }
  items.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  const receipts = l.receipts || [];
  const inv = l.invoices || [];
  return html`
    ${l.flags.length ? html`<div class="sp-dflags">${flagChips(l.flags)}${l.exposure_usd ? html`<span class="small muted">PO $${fmt.num(l.unit_price, 2)} vs contract $${fmt.num(l.effective_price, 2)} at delivery: ${fmt.usd(l.exposure_usd)} exposure</span>` : ''}</div>` : ''}
    <dl class="kv sp-kv">
      <dt>Item</dt><dd>${link.item(l.item_id)} ${l.item_name}</dd>
      <dt>Quantity</dt><dd>${qtyFmt(l.qty, l.uom)} ordered · ${qtyFmt(l.received_qty || 0, l.uom)} received</dd>
      <dt>Need / promise</dt><dd>${fmt.dateLong(l.need_date)} / ${l.promise_date ? fmt.dateLong(l.promise_date) : html`<span class="muted">none</span>`} ${slipChip(l.promise_date ? l.slip_days : null)}</dd>
      <dt>Price</dt><dd>$${fmt.num(l.unit_price, 2)} / ${l.uom} · ${fmt.usd(l.line_value)}</dd>
      <dt>Confirmed</dt><dd>${l.confirmed_at ? html`${fmt.dt(l.confirmed_at)} <span class="muted">(${fmt.num(l.hours_to_confirm, 0)} h after the PO)</span>` : html`<span class="muted">not yet</span>`}</dd>
    </dl>
    <h4 class="section-title">Promise history (${items.length})</h4>
    ${items.length ? ui.timeline(items) : ui.empty('No acknowledgement received yet.')}
    ${receipts.length ? html`<h4 class="section-title">Receipts</h4>
      <div class="table-wrap"><table class="table dense"><thead><tr><th>Receipt</th><th>Received</th><th class="num">Qty</th><th>Lot / ASN</th></tr></thead>
      <tbody>${receipts.map((g) => html`<tr><td class="mono">${g.receipt_id}</td><td>${fmt.dt(g.received_at)}</td><td class="num">${fmt.int(g.qty)}</td><td>${g.lot_id ? link.lot(g.lot_id) : html`<span class="mono small">${g.asn_no || '—'}</span>`}</td></tr>`)}</tbody></table></div>` : ''}
    ${inv.length ? html`<h4 class="section-title">Invoices</h4>
      <div class="table-wrap"><table class="table dense"><thead><tr><th>Invoice</th><th>Date</th><th class="num">Qty</th><th class="num">Amount</th><th>Match</th><th>Pay</th></tr></thead>
      <tbody>${inv.map((v) => html`<tr><td class="mono">${v.invoice_id}</td><td>${fmt.date(v.invoice_date)}</td><td class="num">${fmt.int(v.qty)}</td><td class="num">${fmt.usd(v.amount_usd)}</td><td>${ui.statusChip(v.match_status)}</td><td>${ui.statusChip(v.pay_status)}</td></tr>`)}</tbody></table></div>` : ''}`;
}

async function openPO(po, line) {
  let d;
  try { d = await api.get('/api/suppliers/po', { po, line }); } catch (err) { ui.toast(err.message, 'critical'); return; }
  if (!S) return;
  S.ctx.setQuery({ po, line: line || null, supplier: null }, { silent: true });
  const lines = d.lines;
  let focus = Number(line) || (lines.find((l) => l.status === 'OPEN') || lines[0]).line_no;
  const h = d.po;
  const bodyHtml = html`<div class="sp-drawer">
    <dl class="kv">
      <dt>Supplier</dt><dd>${link.supplier(h.supplier_id, h.supplier_name)} <span class="muted small">tier ${h.tier} · ${h.country}</span></dd>
      <dt>Ship to</dt><dd>${h.ship_to_name}</dd>
      <dt>Created</dt><dd>${fmt.dt(h.created_at)} by ${h.buyer} · ${h.incoterm} · ${h.currency}</dd>
      <dt>Status</dt><dd>${ui.statusChip(h.status)} · ${lines.length} line${lines.length === 1 ? '' : 's'}</dd>
    </dl>
    <div class="table-wrap sp-polines"><table class="table dense"><thead><tr><th>Line</th><th>Item</th><th class="num">Qty</th><th>Need</th><th>Promise</th><th>Status</th><th>Flags</th></tr></thead>
      <tbody>${lines.map((l) => html`<tr class="clickable ${l.line_no === focus ? 'sp-sel' : ''}" data-line="${l.line_no}"><td class="mono">${l.line_no}</td><td class="mono">${l.item_id}</td><td class="num">${fmt.int(l.qty)}</td><td>${fmt.date(l.need_date)}</td><td>${l.promise_date ? fmt.date(l.promise_date) : '—'}</td><td>${ui.statusChip(l.status === 'OPEN' ? l.confirm_status : l.status)}</td><td>${flagChips(l.flags)}</td></tr>`)}</tbody></table></div>
    <div class="sp-linedetail"></div>
  </div>`;
  const b = ui.drawer.open({
    title: `PO ${po}`, subtitle: `${h.supplier_name} · ${fmt.usd(lines.reduce((a, l) => a + l.line_value, 0))}`, body: bodyHtml, width: 780,
    onClose: () => clearKeys(['po', 'line']),
  });
  const showLine = (n) => {
    focus = n;
    const l = lines.find((x) => x.line_no === n);
    $('.sp-linedetail', b).innerHTML = String(html`<h4 class="section-title">Line ${n}</h4>${lineDetail(l)}`);
    b.querySelectorAll('tr[data-line]').forEach((tr) => tr.classList.toggle('sp-sel', Number(tr.dataset.line) === n));
  };
  showLine(focus);
  b.onclick = (e) => {
    const tr = e.target.closest('tr[data-line]');
    if (tr && !e.target.closest('a')) { showLine(Number(tr.dataset.line)); S && S.ctx.setQuery({ line: tr.dataset.line }, { silent: true }); }
  };
}

async function openSupplier(id) {
  let d;
  try { d = await api.get('/api/suppliers/supplier', { id }); } catch (err) { ui.toast(err.message, 'critical'); return; }
  if (!S) return;
  S.ctx.setQuery({ supplier: id, po: null, line: null }, { silent: true });
  const s = d.supplier;
  const sc = d.scorecard;
  const t = s.recovery_terms;
  const lc = d.latest_commit;
  const body = html`<div class="sp-drawer">
    <dl class="kv">
      <dt>Tier</dt><dd>${s.tier}${s.parent_supplier_id ? html` · sells into ${link.supplier(s.parent_supplier_id, s.parent_name)}` : ''}${s.is_cm ? html` · ${ui.chip('info', 'Contract manufacturer')}` : ''}</dd>
      <dt>Commodity</dt><dd>${s.commodity}</dd>
      <dt>Country</dt><dd>${s.country}${s.site_name ? html` · ${s.site_name}` : ''}</dd>
      <dt>Payment terms</dt><dd>${s.payment_terms || '—'}</dd>
      ${t ? html`<dt>Cost recovery</dt><dd>Parts ${fmt.pct(t.parts_pct, 0)} · labor $${t.labor_rate_usd}/h (cap ${t.labor_hours_cap} h) · admin $${t.admin_fee_usd} per claim · respond in ${t.response_days} days</dd>` : ''}
      ${lc ? html`<dt>Latest forecast</dt><dd>${lc.release_id}: ${lc.n ? html`committed ${fmt.pct(lc.commit_qty / lc.qty, 0)} of ${fmt.compact(lc.qty)}` : html`<span class="muted">no response</span>`}</dd>` : ''}
    </dl>
    ${sc ? html`<h4 class="section-title">Scorecard</h4>
      <div class="sp-scmini">
        ${ui.kpi({ label: 'Score', value: sc.score == null ? '—' : fmt.num(sc.score, 0), hint: 'OTD 40 · speed 20 · quality 20 · slips 20' })}
        ${ui.kpi({ label: 'OTD to first promise', value: sc.otd_first_promise == null ? '—' : fmt.pct(sc.otd_first_promise, 0), hint: `${sc.otd_n} receipts, 90 days` })}
        ${ui.kpi({ label: 'Hours to promise', value: sc.confirm_hours_median == null ? '—' : fmt.num(sc.confirm_hours_median, 0), hint: 'median, 60 days' })}
      </div>` : ''}
    ${d.sub_tier.length ? html`<h4 class="section-title">Sub-tier visibility</h4>
      <ul class="sp-tree">${d.sub_tier.map((x) => html`<li style="--lvl:${x.lvl}">${link.supplier(x.supplier_id, x.name)} <span class="muted small">tier ${x.tier} · ${x.commodity} · ${x.country}</span></li>`)}</ul>` : ''}
    ${d.avl.length ? html`<h4 class="section-title">Approved items</h4>
      <div class="table-wrap"><table class="table dense"><thead><tr><th>Item</th><th>Role</th><th class="num">Share</th><th class="num">LT (d)</th></tr></thead>
      <tbody>${d.avl.map((a) => html`<tr><td>${link.item(a.item_id)} <span class="small muted">${a.name}</span></td><td>${ui.statusChip(a.role === 'PRIMARY' ? 'ACTIVE' : a.role === 'QUALIFYING' ? 'IN_REVIEW' : 'OPEN', fmt.title(a.role))}</td><td class="num">${fmt.pct((a.share_pct || 0) / 100, 0)}</td><td class="num">${a.lead_time_days ?? '—'}</td></tr>`)}</tbody></table></div>` : ''}
    ${d.open_lines.length ? html`<h4 class="section-title">Open PO lines (${d.open_lines.length})</h4>
      <div class="table-wrap"><table class="table dense"><thead><tr><th>PO line</th><th>Item</th><th class="num">Qty</th><th>Need</th><th>Promise</th><th>Flags</th></tr></thead>
      <tbody>${d.open_lines.slice(0, 25).map((l) => html`<tr><td>${link.po(l.po_id, l.line_no)}</td><td class="mono">${l.item_id}</td><td class="num">${fmt.int(l.qty)}</td><td>${fmt.date(l.need_date)}</td><td>${l.promise_date ? fmt.date(l.promise_date) : '—'}</td><td>${flagChips(l.flags)}</td></tr>`)}</tbody></table></div>` : ''}
    ${d.stock.length ? html`<h4 class="section-title">Stock they hold for us</h4>
      <div class="table-wrap"><table class="table dense"><thead><tr><th>Item</th><th>Status</th><th class="num">Qty</th><th>As of</th><th>Source</th></tr></thead>
      <tbody>${d.stock.map((x) => html`<tr><td class="mono">${x.item_id}</td><td>${ui.statusChip(x.stock_status)}</td><td class="num">${fmt.int(x.qty)}</td><td>${fmt.date(x.as_of)}</td><td class="small muted">${fmt.title(x.source)}</td></tr>`)}</tbody></table></div>` : ''}
    ${d.chargebacks.length ? html`<h4 class="section-title">Chargebacks</h4>
      <div class="table-wrap"><table class="table dense"><thead><tr><th>ID</th><th>Title</th><th class="num">Amount</th><th>Status</th></tr></thead>
      <tbody>${d.chargebacks.map((x) => html`<tr><td class="mono"><a class="id-link" href="#/warranty">${x.chargeback_id}</a></td><td class="wrap small">${x.title}</td><td class="num">${fmt.usd(x.amount_usd)}</td><td>${ui.statusChip(x.status)}</td></tr>`)}</tbody></table></div>` : ''}
  </div>`;
  ui.drawer.open({
    title: s.name, subtitle: `${s.supplier_id} · ${s.commodity}`, body, width: 720,
    onClose: () => clearKeys(['supplier']),
  });
}

// ---------------------------------------------------------------------------
// Forecast to tiers 1-3
// ---------------------------------------------------------------------------
async function tabForecast(body) {
  const release = S.ctx.query.get('release') || null;
  const item = S.ctx.query.get('fitem') || 'BMS-B';
  const d = await api.get('/api/suppliers/forecast', { release, item });
  const rel = d.release;
  const w = d.warning;
  const mvs = w.first_drop.MVS;
  const pnc = w.first_drop.PNC;
  const sname = (id) => (d.grid.find((g) => g.supplier_id === id) || {}).name || id;
  const warnChart = charts.line({
    series: ['MVS', 'PNC'].filter((id) => w.series[id]).map((id) => ({
      name: `${sname(id)} (tier ${id === 'MVS' ? 2 : 1})`,
      points: w.series[id].filter((p) => p.cover != null).map((p) => ({ x: p.released_at.slice(0, 10), y: p.cover })),
    })),
    height: 230, yMin: 0.5, yMax: 1.02, markers: true, zero: false,
    yFormat: (v) => fmt.pct(v, 0), refLines: [{ y: 0.9, label: '90% commit', tone: 'warning' }],
    tooltipY: (v) => fmt.pct(v, 1),
  });
  const heat = heatGrid(d);
  body.innerHTML = html`
    <div class="filter-row">
      <label class="sp-lbl">Release <select class="select sm" data-fr="release">${d.releases.map((r) => html`<option value="${r.release_id}"${r.release_id === rel.release_id ? raw(' selected') : ''}>${r.release_id} · ${fmt.date(r.released_at)}</option>`)}</select></label>
      <span class="small muted">${fmt.int(rel.lines)} forecast lines released ${fmt.dt(rel.released_at)} · ${fmt.int(rel.commits)} commits back · basis: ${rel.demand_basis}</span>
    </div>
    <div class="grid">
      <div class="span-12">${ui.card({
        title: 'Commit coverage by supplier and week',
        subtitle: 'The MRP explosion, released as a forecast: tier 1 at the dock date, tier 2 four weeks earlier, tier 3 eight. Stronger color means a bigger shortfall between what we forecast and what the supplier committed.',
        body: html`<div class="sp-hlegend-row">${heatLegend()}</div>${heat}`,
      })}</div>
      <div class="span-7">${ui.card({ title: 'Early warning: tier-2 commit fell weeks before the tier-1 slip', subtitle: 'Near-term (8-week) commit coverage in each weekly release', body: warnChart, tableToggle: true })}</div>
      <div class="span-5">${ui.callout({
        tone: 'info', title: w.lead_weeks ? `${fmt.num(w.lead_weeks, 1)} weeks of warning, if anyone had been looking` : 'Early warning',
        body: html`<ul class="sp-bullets">
          ${mvs ? html`<li><b>${sname('MVS')}</b> (tier 2, AFE ICs inside Pinecrest's BMS boards) dropped to <b>${fmt.pct(mvs.cover, 0)}</b> in ${mvs.release_id} (${fmt.date(mvs.released_at)}).</li>` : ''}
          ${pnc ? html`<li><b>${sname('PNC')}</b> (tier 1) kept committing ~98% until ${pnc.release_id} (${fmt.date(pnc.released_at)}), then fell to ${fmt.pct(pnc.cover, 0)}.</li>` : ''}
          ${w.slip ? html`<li>The PO itself slipped on ${fmt.date(w.slip.recorded_at)}: ${link.po(w.slip.po_id, w.slip.line_no)} → ${fmt.date(w.slip.promise_date)}.</li>` : ''}
          <li>The forecast we release to tiers 1-3 is also our early-warning system. A tier-2 commit under 90% should open a supply exception the day it lands.</li>
        </ul>`,
      })}</div>
      <div class="span-12">${ui.card({
        title: html`Forecast waterfall · ${d.waterfall.item ? html`<span class="mono">${d.waterfall.item.item_id}</span> ${d.waterfall.item.name}` : item}`,
        subtitle: 'Each row is a weekly release, each column a target week: read down a column to see how the number for that week moved. Shading is the commit shortfall.',
        actions: html`<select class="select sm" data-fr="item">${d.forecast_items.map((i) => html`<option value="${i.item_id}"${i.item_id === item ? raw(' selected') : ''}>T${i.tier} · ${i.item_id}</option>`)}</select>`,
        body: waterfall(d), flush: true,
      })}</div>
    </div>`;
  on(body, 'change', 'select[data-fr]', (e, s) => {
    const next = { release: s.dataset.fr === 'release' ? s.value : rel.release_id, item: s.dataset.fr === 'item' ? s.value : item };
    S.ctx.setQuery({ release: next.release, fitem: next.item }, { silent: true });
    showTab('forecast');
  });
  on(body, 'click', '[data-sup]', (e, t) => openSupplier(t.dataset.sup));
}

function heatLegend() {
  return html`<span class="sp-hlegend small">${SHORTFALL_STEPS.map((s) => html`<span class="sp-hkey"><span class="sp-hsw ${s.cls}"></span>${s.label}</span>`)}<span class="sp-hkey"><span class="sp-hsw hf-none"></span>No response</span><span class="sp-hkey"><span class="sp-hsw hf-pend"></span>Awaiting</span></span>`;
}

function heatGrid(d) {
  const weeks = d.weeks;
  const tiers = [1, 2, 3];
  const rows = [];
  for (const t of tiers) {
    const g = d.grid.filter((x) => x.tier === t);
    if (!g.length) continue;
    rows.push(html`<tr class="sp-tier"><th colspan="${weeks.length + 3}">Tier ${t}${t === 1 ? ' · we buy from them' : t === 2 ? ' · they supply our tier 1s' : ' · raw materials'}</th></tr>`);
    for (const s of g) {
      const pend = !s.responded && s.ever_responded;
      rows.push(html`<tr>
        <th class="sp-hname" data-sup="${s.supplier_id}"><span class="ent-link">${s.name}</span><span class="tiny muted">${s.items.map((i) => i.item_id).join(', ')}${s.parent_name ? ` → ${s.parent_name}` : ''}</span></th>
        <td class="num sp-hcov">${s.cover == null ? html`<span class="muted">${pend ? 'awaiting' : 'no reply'}</span>` : fmt.pct(s.cover, 0)}</td>
        ${weeks.map((wk) => {
          const c = s.cells[wk];
          if (!c) return html`<td class="sp-hc hf-empty"></td>`;
          const cls = c.cover == null ? (pend ? 'hf-pend' : 'hf-none') : shortCls(c.cover);
          const unit = (s.items[0] || {}).uom === 'kg' ? ' kg' : '';
          const tip = `${s.name} · week of ${fmt.date(wk)}\nForecast ${fmt.compact(c.qty)}${unit}` +
            (c.commit == null ? `\n${pend ? 'Awaiting this release\'s commit' : 'No commit received'}` : `\nCommitted ${fmt.compact(c.commit)}${unit} (${fmt.pct(c.cover, 0)})`);
          return html`<td class="sp-hc ${cls}" data-tip="${tip}"></td>`;
        })}
        <td class="num small muted sp-hqty">${fmt.compact(Math.round(s.qty))}${(s.items[0] || {}).uom === 'kg' ? ' kg' : ''}</td>
      </tr>`);
    }
  }
  return html`<div class="table-wrap sp-heat"><table class="sp-htable">
    <colgroup><col class="c-name"><col class="c-cov">${weeks.map(() => html`<col>`)}<col class="c-qty"></colgroup>
    <thead><tr><th class="sp-hname">Supplier</th><th class="num">Commit</th>${weeks.map((wk, i) => html`<th class="sp-hw">${i % 2 === 0 ? fmt.date(wk) : ''}</th>`)}<th class="num">Forecast</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function waterfall(d) {
  const wf = d.waterfall;
  if (!wf.rows.length) return ui.empty('No forecast lines for this item.');
  const uom = wf.item && wf.item.uom === 'kg' ? ' kg' : '';
  return html`<div class="table-wrap sp-wf"><table class="table dense">
    <thead><tr><th>Release</th>${wf.weeks.map((wk) => html`<th class="num">${fmt.date(wk)}</th>`)}</tr></thead>
    <tbody>${wf.rows.map((r) => html`<tr><th class="mono nowrap">${r.release_id}</th>${wf.weeks.map((wk) => {
      const c = r.cells[wk];
      if (!c) return html`<td></td>`;
      const cover = c.commit == null ? null : (c.qty ? c.commit / c.qty : 1);
      const cls = cover == null ? 'hf-none' : shortCls(cover);
      return html`<td class="num sp-wfc ${cls}" data-tip="${r.release_id} · week of ${fmt.date(wk)}\nForecast ${fmt.int(c.qty)}${uom}${c.commit == null ? '\nNo commit' : `\nCommitted ${fmt.int(c.commit)}${uom} (${fmt.pct(cover, 0)})`}">${fmt.compact(c.qty)}</td>`;
    })}</tr>`)}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// RFQs
// ---------------------------------------------------------------------------
async function tabRFQs(body) {
  const d = S.cache.rfqs || (S.cache.rfqs = await api.get('/api/suppliers/rfqs'));
  body.innerHTML = html`<div class="stack">${d.rfqs.map((r) => {
    const rec = r.recommendation;
    const chart = charts.bar({
      categories: r.quotes.map((x) => x.supplier_id + (x.incumbent ? ' (inc.)' : '')),
      series: [{ name: 'Total cost of ownership / yr', values: r.quotes.map((x) => x.tco) }],
      horizontal: true, height: 40 + r.quotes.length * 34, yFormat: (v) => fmt.usd(v, { compact: true }), valueLabels: true,
    });
    return ui.card({
      title: html`<span class="mono">${r.rfq_id}</span> · ${r.title}`,
      subtitle: html`${link.item(r.item_id)} ${r.item_name} · ${fmt.int(r.annual_qty)} / yr · issued ${fmt.date(r.created_at)} · due ${fmt.date(r.due_at)}${r.eco_id ? html` · for ${r.eco_id}` : ''}`,
      actions: ui.statusChip(r.status),
      body: html`<div class="grid">
        <div class="span-12"><div class="table-wrap"><table class="table dense sp-rfqt">
          <thead><tr><th>Supplier</th><th class="num">Unit</th><th class="num">Freight</th><th class="num">Landed</th><th class="num">MOQ</th><th class="num">LT (d)</th><th class="num">Tooling</th><th class="num">TCO / yr</th><th class="num">vs incumbent</th><th class="num">Payback</th><th>Notes</th></tr></thead>
          <tbody>${r.quotes.map((x, i) => html`<tr class="${i === 0 ? 'sp-best' : ''}">
            <td>${link.supplier(x.supplier_id, x.supplier_name)}${x.incumbent ? html` ${ui.chip('info', 'Incumbent', { icon: false })}` : ''}</td>
            <td class="num">$${fmt.num(x.unit_price, 2)}</td><td class="num">$${fmt.num(x.freight_per_unit, 2)}</td><td class="num">$${fmt.num(x.landed_unit, 2)}</td>
            <td class="num">${fmt.int(x.moq)}</td><td class="num">${x.lead_time_days}</td><td class="num">${x.tooling ? fmt.usd(x.tooling) : '—'}</td>
            <td class="num strong">${fmt.usd(x.tco)}</td>
            <td class="num">${x.vs_incumbent == null || x.incumbent ? '—' : html`<span class="${x.vs_incumbent < 0 ? 'sp-good' : 'sp-bad'}">${x.vs_incumbent < 0 ? '−' : '+'}${fmt.usd(Math.abs(x.vs_incumbent))}</span>`}</td>
            <td class="num">${x.payback_months ? `${fmt.num(x.payback_months, 1)} mo` : '—'}</td>
            <td class="wrap small">${x.notes || ''}</td></tr>`)}</tbody></table></div>
          <p class="tiny muted">TCO = (unit + freight) × annual volume + tooling. Quality cost is the supplier's recovered chargebacks to date.</p>
        </div>
        <div class="${r.quotes.length > 1 ? 'span-7' : 'span-12'}">${ui.callout({ tone: r.status === 'AWARDED' ? 'good' : 'info', title: rec.action, body: rec.rationale })}</div>
        ${r.quotes.length > 1 ? html`<div class="span-5">${chart}</div>` : ''}
      </div>`,
    });
  })}</div>`;
}

// ---------------------------------------------------------------------------
// ECOs & effectivity
// ---------------------------------------------------------------------------
async function tabECOs(body) {
  const d = S.cache.ecos || (S.cache.ecos = await api.get('/api/suppliers/ecos'));
  const ecos = d.ecos;
  let sel = S.ctx.query.get('eco') || 'ECO-0042';
  if (!ecos.some((e) => e.eco_id === sel)) sel = ecos[0].eco_id;
  body.innerHTML = html`<div class="grid">
    <div class="span-5">${ui.card({ title: 'Engineering changes', subtitle: 'Effectivity by date, serial or lot. Click one to see how the cut-in actually went.', body: html`<div class="sp-ecolist"></div>`, flush: true })}</div>
    <div class="span-7 sp-ecodetail"></div>
  </div>`;
  ui.dataTable($('.sp-ecolist', body), {
    columns: [
      { key: 'eco_id', label: 'Change', wrap: true, render: (e) => html`<div class="sp-eco"><span class="mono small">${e.eco_id}</span><span class="small">${e.title}</span><span class="tiny muted">${fmt.title(e.effectivity_type)} effectivity${e.effective_date ? ` · ${fmt.date(e.effective_date)}` : ''}</span></div>` },
      { key: 'status', label: 'Status', render: (e) => ui.statusChip(e.status) },
      { key: 'cost_delta', label: 'Δ cost', num: true, format: (v) => `${v < 0 ? '−' : '+'}$${fmt.num(Math.abs(v), 2)}` },
    ],
    rows: ecos, pageSize: 0, onRowClick: (e) => { S.ctx.setQuery({ eco: e.eco_id }, { silent: true }); showEco(e.eco_id); },
  });
  const showEco = (id) => {
    const e = ecos.find((x) => x.eco_id === id);
    $('.sp-ecodetail', body).innerHTML = String(ecoDetail(e));
    body.querySelectorAll('.sp-ecolist tbody tr').forEach((tr, i) => tr.classList.toggle('sp-sel', (ecos[i] || {}).eco_id === id));
  };
  showEco(sel);
}

function ecoDetail(e) {
  const r = e.reality || {};
  let insight = null;
  if (r.old_uses_after != null) {
    if (r.serial_effectivity && r.residual_old > 0 && !r.old_uses_after) {
      insight = { tone: 'warning', title: `${fmt.int(r.residual_old)} ${e.old_item_id} stranded at ${r.residual_where || 'site'}: ${fmt.usd(r.residual_value)}`, body: `Serial effectivity cut in cleanly at ${r.cut_in_unit}, and nothing used rev ${e.old_item_id.slice(-1)} after it. The disposition says use-up, but rev ${e.old_item_id.slice(-1)} has no remaining demand. Move the stock to service spares or negotiate a return before it ages.` };
    } else if (r.old_uses_after > 0) {
      const dev = (e.deviations || [])[0];
      insight = { tone: 'info', title: `${e.old_item_id} was still used ${fmt.int(r.old_uses_after)} times after the effective date`, body: `Use-up ran until ${fmt.dateLong(r.old_last_use)}${dev ? `, under ${dev.deviation_id} (${fmt.int(dev.qty_used)} of ${fmt.int(dev.qty_limit)} allowed)` : ''}. ${r.residual_old ? `${fmt.int(r.residual_old)} still on hand (${fmt.usd(r.residual_value)}).` : 'Old stock is exhausted.'} Effectivity on paper and effectivity on the line are different dates; genealogy records the real one.` };
    }
  } else if (r.planned_units_13w != null) {
    insight = { tone: e.cost_delta > 0 ? 'warning' : 'good', title: `${e.cost_delta > 0 ? 'Adds' : 'Saves'} ${fmt.usd(Math.abs(r.impact_annual))} a year at planned volume`, body: `${fmt.int(r.planned_units_13w)} ${r.basis} × ${e.cost_delta > 0 ? '+' : '−'}$${fmt.num(Math.abs(e.cost_delta), 2)} = ${fmt.usd(Math.abs(r.impact_13w))} per quarter.${r.open_po_lines && r.open_po_lines.length ? ` ${r.open_po_lines.length} open PO lines deliver after the effective date and will need the new revision.` : ''}` };
  }
  return ui.card({
    title: html`<span class="mono">${e.eco_id}</span> · ${e.title}`,
    subtitle: e.reason,
    actions: ui.statusChip(e.status),
    body: html`
      <dl class="kv">
        <dt>Class</dt><dd>${fmt.title(e.change_class)} · owner ${e.owner}</dd>
        <dt>Effectivity</dt><dd>${fmt.title(e.effectivity_type)}${e.effective_date ? ` from ${fmt.dateLong(e.effective_date)}` : ''}${r.cut_in_unit && r.serial_effectivity ? html` · first serial ${link.serial(r.cut_in_unit)}` : ''}</dd>
        ${e.old_item_id ? html`<dt>Part change</dt><dd>${link.item(e.old_item_id)} ${e.old_name} → ${link.item(e.new_item_id)} ${e.new_name}</dd>` : ''}
        ${e.parent_item_id ? html`<dt>Applies to</dt><dd>${link.item(e.parent_item_id)} ${e.parent_name}</dd>` : ''}
        <dt>Cost</dt><dd>${e.cost_delta < 0 ? '−' : '+'}$${fmt.num(Math.abs(e.cost_delta || 0), 2)} per unit · stock disposition ${e.stock_disposition ? fmt.title(e.stock_disposition) : '—'}</dd>
        <dt>Timeline</dt><dd>created ${fmt.date(e.created_at)}${e.approved_at ? ` · approved ${fmt.date(e.approved_at)}` : ''}${e.implemented_at ? ` · implemented ${fmt.date(e.implemented_at)}` : ''}</dd>
      </dl>
      ${r.old_uses_after != null ? html`<h4 class="section-title">Cut-in, as it actually happened</h4>
        <div class="sp-cutin">
          ${ui.kpi({ label: 'First unit with new part', value: r.cut_in_unit ? r.cut_in_unit : '—', hint: r.cut_in_at ? fmt.dt(r.cut_in_at) : '' , cls: 'sp-kpi-mono' })}
          ${ui.kpi({ label: 'Old part used after effectivity', value: fmt.int(r.old_uses_after), hint: r.old_last_use ? `last ${fmt.date(r.old_last_use)}` : 'none' })}
          ${ui.kpi({ label: 'Old stock left', value: fmt.int(r.residual_old), hint: r.residual_old ? `${fmt.usd(r.residual_value)} at ${r.residual_where}` : 'exhausted' })}
          ${ui.kpi({ label: 'Builds old / new rev', value: `${fmt.int(r.old_total_uses)} / ${fmt.int(r.new_total_uses)}`, hint: 'installs in genealogy' })}
        </div>` : ''}
      ${insight ? ui.callout(insight) : ''}
      ${(e.deviations || []).length ? html`<h4 class="section-title">Deviations</h4>${e.deviations.map((v) => html`<div class="sp-dev">
          <div class="row"><span class="mono">${v.deviation_id}</span><span class="small">${v.title}</span><span class="spacer"></span>${ui.statusChip(v.status)}</div>
          <div class="row small muted">${fmt.int(v.qty_used)} of ${fmt.int(v.qty_limit)} used · valid ${fmt.date(v.valid_from)}–${fmt.date(v.valid_to)}</div>
          ${ui.meter({ value: v.qty_used, max: v.qty_limit, tone: v.qty_used / v.qty_limit > 0.9 ? 'warning' : null })}
        </div>`)}` : ''}
      ${r.open_po_lines && r.open_po_lines.length ? html`<h4 class="section-title">Open PO lines delivering after the effective date</h4>
        <div class="row wrap">${r.open_po_lines.map((l) => html`<span class="sp-pill">${link.po(l.po_id, l.line_no)} <span class="small muted">${l.item_id} · ${fmt.int(l.qty)} · ${fmt.date(l.need_date)}</span></span>`)}</div>` : ''}
      ${(e.bom_lines || []).length ? html`<h4 class="section-title">BOM lines carrying this ECO</h4>
        <div class="table-wrap"><table class="table dense"><thead><tr><th>Parent</th><th>Child</th><th>Position</th><th class="num">Qty</th><th>From</th><th>To</th></tr></thead>
        <tbody>${e.bom_lines.map((b) => html`<tr><td class="mono">${b.parent_item_id}</td><td class="mono">${b.child_item_id}</td><td>${b.position}</td><td class="num">${fmt.num(b.qty_per, b.qty_per % 1 ? 3 : 0)}</td><td>${fmt.date(b.eff_from)}</td><td>${b.eff_to ? fmt.date(b.eff_to) : 'open'}</td></tr>`)}</tbody></table></div>
        <p class="tiny muted">See the full structure on any date in the ${html`<a href="#/erp?tab=bom&item=${e.parent_item_id || 'LV1-SLATE-L'}">ERP BOM explorer</a>`}.</p>` : ''}`,
  });
}

// ---------------------------------------------------------------------------
// Pricing
// ---------------------------------------------------------------------------
async function tabPricing(body) {
  const d = S.cache.pricing || (S.cache.pricing = await api.get('/api/suppliers/pricing'));
  const ex = d.exposure;
  const stale = ex.filter((e) => e.cause.startsWith('PO issued'));
  const stepped = ex.filter((e) => !e.cause.startsWith('PO issued'));
  const sum = (rows) => rows.reduce((a, e) => a + e.exposure_usd, 0);
  const items = [...new Map(d.prices.map((p) => [p.item_id, p.item_name])).entries()];
  body.innerHTML = html`<div class="grid">
    <div class="span-12">${ui.callout({
      tone: ex.length ? 'warning' : 'good',
      title: ex.length ? `${ex.length} open PO lines are priced above the contract in effect on their delivery date: ${fmt.usd(d.exposure_total)}` : 'Every open PO line matches the contract price in effect at delivery',
      body: ex.length ? html`Two different failures. ${stale.length ? html`<b>${stale.length} line${stale.length === 1 ? ' was' : 's were'} issued at a price that had already been superseded</b> (${[...new Set(stale.map((e) => e.po_id))].map((p, i) => html`${i ? ', ' : ''}${link.po(p)}`)}: ${fmt.usd(sum(stale))}); the buyer copied an old PO. ` : ''}${stepped.length ? html`${stepped.length} more were priced correctly when issued, but the contract steps down before they deliver (${fmt.usd(sum(stepped))}). ` : ''}Issue change orders before the invoices arrive, or the three-way match will pass the old price straight through, because it checks invoice against PO, not against the contract.` : '',
    })}</div>
    <div class="span-12">${ui.card({ title: 'Open lines not re-priced', body: html`<div class="sp-expo"></div>`, flush: true })}</div>
    <div class="span-8">${ui.card({ title: 'Price effectivity', subtitle: 'Every contract price row as a span of time. The line is today.', body: priceGantt(d), tableToggle: true })}</div>
    <div class="span-4">${ui.card({ title: 'Price on a date', subtitle: 'The same lookup the costing and PO checks use', body: html`
      <div class="sp-calc">
        <label class="sp-lbl">Item <select class="select sm" data-pc="item">${items.map(([id, n]) => html`<option value="${id}"${id === 'CEL-21700' ? raw(' selected') : ''}>${id}</option>`)}</select></label>
        <label class="sp-lbl">Supplier <select class="select sm" data-pc="supplier"></select></label>
        <label class="sp-lbl">Date <input class="input sm" type="date" data-pc="date" value="${d.as_of}"></label>
        <label class="sp-lbl">Qty <input class="input sm" type="number" min="0" step="1000" data-pc="qty" value="20000"></label>
      </div>
      <div class="sp-calc-out"></div>` })}</div>
    <div class="span-12">${ui.card({ title: 'All price rows', body: html`<div class="sp-prices"></div>`, flush: true })}</div>
  </div>`;
  ui.dataTable($('.sp-expo', body), {
    columns: [
      { key: 'po', label: 'PO line', value: (e) => `${e.po_id}-${e.line_no}`, render: (e) => link.po(e.po_id, e.line_no) },
      { key: 'item_id', label: 'Item', mono: true },
      { key: 'supplier_id', label: 'Supplier', render: (e) => link.supplier(e.supplier_id) },
      { key: 'deliver', label: 'Delivers', format: (v) => fmt.date(v) },
      { key: 'unit_price', label: 'PO price', num: true, format: (v) => `$${fmt.num(v, 2)}` },
      { key: 'effective_price', label: 'Contract at delivery', num: true, render: (e) => html`$${fmt.num(e.effective_price, 2)} <span class="tiny muted">${e.effective_ref}</span>` },
      { key: 'open_qty', label: 'Open qty', num: true, format: (v) => fmt.int(v) },
      { key: 'exposure_usd', label: 'Exposure', num: true, format: (v) => fmt.usd(v) },
      { key: 'cause', label: 'Why', wrap: true, render: (e) => html`<span class="small">${e.cause}</span>` },
    ],
    rows: ex, pageSize: 0, initialSort: { key: 'exposure_usd', dir: 'desc' }, empty: 'No exposure',
  });
  ui.dataTable($('.sp-prices', body), {
    columns: [
      { key: 'item_id', label: 'Item', mono: true },
      { key: 'item_name', label: 'Name' },
      { key: 'supplier_name', label: 'Supplier' },
      { key: 'min_qty', label: 'Min qty', num: true, format: (v) => fmt.int(v) },
      { key: 'unit_price', label: 'Price', num: true, format: (v) => `$${fmt.num(v, 2)}` },
      { key: 'eff_from', label: 'From', format: (v) => fmt.date(v) },
      { key: 'eff_to', label: 'To', render: (p) => (p.eff_to ? fmt.date(p.eff_to) : html`<span class="muted">open</span>`) },
      { key: 'basis', label: 'Basis', render: (p) => ui.chip(p.basis === 'CONTRACT' ? 'good' : p.basis === 'ECO' ? 'info' : 'neutral', fmt.title(p.basis), { icon: false }) },
      { key: 'source_ref', label: 'Source', mono: true },
    ],
    rows: d.prices, pageSize: 20, search: true,
  });
  const calcEl = $('.sp-calc', body);
  const supSel = $('[data-pc=supplier]', body);
  const fillSup = () => {
    const it = $('[data-pc=item]', body).value;
    const sups = [...new Map(d.prices.filter((p) => p.item_id === it).map((p) => [p.supplier_id, p.supplier_name])).entries()];
    supSel.innerHTML = String(html`${sups.map(([id, n]) => html`<option value="${id}">${id} · ${n}</option>`)}`);
  };
  const run = async () => {
    const out = $('.sp-calc-out', body);
    const p = { item: $('[data-pc=item]', body).value, supplier: supSel.value, date: $('[data-pc=date]', body).value, qty: $('[data-pc=qty]', body).value };
    try {
      const r = await api.get('/api/suppliers/price-on', p);
      out.innerHTML = String(html`
        <div class="sp-calc-res">${r.price ? html`<span class="sp-price">$${fmt.num(r.price.unit_price, 2)}</span><span class="small muted">per unit · ${r.price.source_ref} · ${fmt.title(r.price.basis)}${r.price.min_qty ? ` · break at ${fmt.int(r.price.min_qty)}` : ''}</span>` : html`<span class="muted">No price in effect on that date.</span>`}</div>
        <div class="table-wrap"><table class="table dense"><thead><tr><th class="num">Min</th><th class="num">Price</th><th>Window</th><th>Why</th></tr></thead>
        <tbody>${r.candidates.map((c) => html`<tr class="${r.price && c.price_id === r.price.price_id ? 'sp-best' : ''}"><td class="num">${fmt.int(c.min_qty)}</td><td class="num">$${fmt.num(c.unit_price, 2)}</td><td class="small">${fmt.date(c.eff_from)}–${c.eff_to ? fmt.date(c.eff_to) : 'open'}</td><td class="small">${r.price && c.price_id === r.price.price_id ? 'applies' : !c.in_effect ? 'not in effect' : !c.qty_ok ? 'below break' : 'lower break wins'}</td></tr>`)}</tbody></table></div>`);
    } catch (err) { out.innerHTML = String(ui.errorBox(err)); }
  };
  fillSup();
  run();
  on(calcEl, 'change', '[data-pc]', (e, t) => { if (t.dataset.pc === 'item') fillSup(); run(); });
}

function priceGantt(d) {
  const rows = d.prices;
  const asOf = Date.parse(d.as_of + 'T00:00:00Z');
  const t0 = Math.min(...rows.map((r) => Date.parse(r.eff_from + 'T00:00:00Z')));
  const t1 = asOf + 120 * 86400000;
  const basisIdx = { CONTRACT: 0, ECO: 1, QUOTE: 2, SPOT: 3 };
  const label = (r) => `${r.item_id} · ${r.supplier_id}${r.min_qty ? ` · ≥${fmt.compact(r.min_qty)}` : ''}`;
  const rowH = 20;
  const H = rows.length * rowH + 34;
  const legend = charts.legend(Object.keys(basisIdx).filter((b) => rows.some((r) => r.basis === b)).map((b) => ({ name: fmt.title(b), color: charts.palette(basisIdx[b]) })), 'rect');
  return charts.custom({
    height: H, legend, ariaLabel: 'Price effectivity timeline',
    render: (w) => {
      const lw = Math.min(190, Math.max(120, w * 0.3));
      const x = charts.scaleLinear([t0, t1], [lw + 8, w - 12]);
      let s = '';
      const months = [];
      const dt = new Date(t0);
      dt.setUTCDate(1);
      while (dt.getTime() <= t1) { if (dt.getTime() >= t0) months.push(dt.getTime()); dt.setUTCMonth(dt.getUTCMonth() + 1); }
      for (const m of months) {
        const xx = Math.round(x(m)) + 0.5;
        s += `<line class="gridline" x1="${xx}" x2="${xx}" y1="4" y2="${H - 22}"/>`;
        s += `<text class="tick" x="${xx}" y="${H - 8}" text-anchor="middle">${esc(new Date(m).toLocaleString('en-US', { month: 'short', timeZone: 'UTC' }))}</text>`;
      }
      rows.forEach((r, i) => {
        const y = 6 + i * rowH;
        const a = Date.parse(r.eff_from + 'T00:00:00Z');
        const b = r.eff_to ? Date.parse(r.eff_to + 'T00:00:00Z') : t1;
        const x0 = x(a), x1 = Math.max(x0 + 3, x(b) - 1);
        const tip = `${r.item_id} from ${r.supplier_name}\n$${fmt.num(r.unit_price, 2)}${r.min_qty ? ` at ≥${fmt.int(r.min_qty)}` : ''} · ${fmt.title(r.basis)} ${r.source_ref || ''}\n${fmt.date(r.eff_from)} → ${r.eff_to ? fmt.date(r.eff_to) : 'open'}`;
        s += `<text class="cat-label" x="${lw}" y="${y + 10}" dy="0.32em" text-anchor="end" style="font-size:11px">${esc(label(r))}</text>`;
        s += `<rect x="${x0.toFixed(1)}" y="${y + 3}" width="${(x1 - x0).toFixed(1)}" height="14" rx="3" style="fill:${charts.palette(basisIdx[r.basis] ?? 7)};opacity:.85" data-tip="${esc(tip)}"/>`;
        const txt = `$${fmt.num(r.unit_price, 2)}`;
        const tw = txt.length * 6.4 + 8;
        // label inside the fill: white on blue/orange, near-black on aqua/yellow (contrast)
        const ink = [2, 3].includes(basisIdx[r.basis]) ? '#0b0b0b' : '#fff';
        if (x1 - x0 > tw) s += `<text x="${(x0 + 5).toFixed(1)}" y="${y + 10}" dy="0.32em" style="font-size:10.5px;fill:${ink};font-weight:600;pointer-events:none">${esc(txt)}</text>`;
        else s += `<text class="value-label" x="${(x1 + 4).toFixed(1)}" y="${y + 10}" dy="0.32em" style="font-size:10.5px">${esc(txt)}</text>`;
      });
      const tx = Math.round(x(asOf)) + 0.5;
      s += `<line x1="${tx}" x2="${tx}" y1="0" y2="${H - 22}" style="stroke:var(--ink-2);stroke-width:1.5"/>`;
      s += `<text class="ref-label" x="${tx + 4}" y="10">today</text>`;
      return raw(s);
    },
    table: { columns: ['Item · supplier', 'Price', 'From', 'To', 'Basis'], rows: rows.map((r) => [label(r), `$${fmt.num(r.unit_price, 2)}`, fmt.date(r.eff_from), r.eff_to ? fmt.date(r.eff_to) : 'open', fmt.title(r.basis)]) },
  });
}

// ---------------------------------------------------------------------------
// Scorecard
// ---------------------------------------------------------------------------
async function tabScorecard(body) {
  const d = S.cache.score || (S.cache.score = await api.get('/api/suppliers/scorecard'));
  const rows = d.rows;
  const scored = rows.filter((r) => r.score != null);
  const worst = scored[0];
  const chart = charts.bar({
    categories: scored.map((r) => r.name),
    series: [{ name: 'Score', values: scored.map((r) => r.score) }],
    horizontal: true, height: 40 + scored.length * 30, yMin: 0, yMax: 100, yFormat: (v) => fmt.num(v, 0), valueLabels: true,
  });
  const pctCell = (v, n) => (v == null ? html`<span class="nil">—</span>` : html`${fmt.pct(v, 0)}${n != null ? html` <span class="tiny muted">n=${n}</span>` : ''}`);
  const bits = (r) => [
    r.otd_first_promise != null ? `on time to its first promise ${fmt.pct(r.otd_first_promise, 0)} of the time (${r.otd_n} receipts)` : null,
    r.confirm_hours_median != null ? `a promise takes ${fmt.num(r.confirm_hours_median, 0)} hours to arrive` : null,
    r.slipped ? `${r.slipped} of ${r.confirmed_recent} recent lines slipped` : null,
    r.iqc_rejects ? `${r.iqc_rejects} lot${r.iqc_rejects === 1 ? '' : 's'} rejected at IQC (${fmt.pct(r.iqc_ppm / 1e6, 1)} of sampled parts defective)` : null,
    r.chargeback_usd ? `${fmt.usd(r.chargeback_usd)} charged back` : null,
  ].filter(Boolean);
  body.innerHTML = html`<div class="grid">
    <div class="span-7">${ui.card({ title: 'Supplier score', subtitle: 'On-time 40 · confirmation speed 20 · incoming quality 20 · promise slips 20', body: chart, tableToggle: true })}</div>
    <div class="span-5">${worst ? ui.callout({ tone: 'serious', title: `${worst.name} scores lowest (${fmt.num(worst.score, 0)})`, body: html`${bits(worst).join('; ').replace(/^./, (c) => c.toUpperCase())}. The score is computed from the same receipts, promises and inspections the MRP and the ERP run on, so nobody has to maintain it by hand.` }) : ''}
      ${scored[1] ? ui.callout({ tone: 'warning', title: `Next: ${scored[1].name} (${fmt.num(scored[1].score, 0)})`, body: `${bits(scored[1]).join('; ').replace(/^./, (c) => c.toUpperCase())}.` }) : ''}</div>
    <div class="span-12">${ui.card({ title: 'Scorecard', subtitle: 'Trailing windows: receipts in the last 90 days, PO lines created in the last 60. Computed from receipts, promise history, IQC and chargebacks.', body: html`<div class="sp-sc"></div>`, flush: true })}</div>
  </div>`;
  ui.dataTable($('.sp-sc', body), {
    columns: [
      { key: 'name', label: 'Supplier', render: (r) => link.supplier(r.supplier_id, r.name) },
      { key: 'score', label: 'Score', num: true, render: (r) => (r.score == null ? html`<span class="nil">—</span>` : html`<span class="sp-score">${fmt.num(r.score, 0)}</span>`) },
      { key: 'otd_first_promise', label: 'OTD to 1st promise', num: true, render: (r) => pctCell(r.otd_first_promise, r.otd_n) },
      { key: 'otd_need', label: 'OTD to need', num: true, render: (r) => pctCell(r.otd_need) },
      { key: 'confirm_hours_median', label: 'Hrs to promise', num: true, format: (v) => fmt.num(v, 0) },
      { key: 'slip_rate', label: 'Slips', num: true, render: (r) => (r.slip_rate == null ? html`<span class="nil">—</span>` : html`${r.slipped}/${r.confirmed_recent}`) },
      { key: 'late_open_lines', label: 'Late open', num: true },
      { key: 'iqc_lots', label: 'IQC lots', num: true, render: (r) => html`${fmt.int(r.iqc_lots)}${r.iqc_rejects ? html` <span class="sp-bad">(${r.iqc_rejects} rej)</span>` : ''}` },
      { key: 'field_claims', label: 'Field claims', num: true },
      { key: 'chargeback_usd', label: 'Chargebacks', num: true, format: (v) => (v ? fmt.usd(v) : '—') },
    ],
    rows, pageSize: 0, initialSort: { key: 'score', dir: 'asc' }, onRowClick: (r) => openSupplier(r.supplier_id),
  });
}

// ---------------------------------------------------------------------------
const PAGE_CSS = `
.pg-suppliers .sp-stories { margin-top: 16px; }
.pg-suppliers .sp-tabs { margin-top: 20px; }
.pg-suppliers .sp-body { margin-top: 16px; }
.pg-suppliers .sp-lbl { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.pg-suppliers .sp-lbl .select, .pg-suppliers .sp-lbl .input { min-width: 0; }
.pg-suppliers .sp-filters { margin: 0 0 14px; }
.pg-suppliers .sp-note { margin-top: 10px; }
.pg-suppliers .sp-ch, .sp-drawer .sp-ch { font: 500 11.5px/1 var(--font-mono); color: var(--ink-2); letter-spacing: .02em; }
.pg-suppliers .sp-flags, .sp-drawer .sp-flags { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.pg-suppliers .sp-sup { white-space: nowrap; }
.pg-suppliers .sp-bullets, .sp-drawer .sp-bullets { margin: 4px 0 0; padding-left: 18px; }
.pg-suppliers .sp-bullets li + li { margin-top: 6px; }
.pg-suppliers .sp-best td { background: color-mix(in srgb, var(--good) 7%, transparent); }
.pg-suppliers .sp-rfqt td.wrap { min-width: 240px; }
.pg-suppliers .sp-good { color: var(--delta-good); font-weight: 600; }
.pg-suppliers .sp-bad { color: var(--delta-bad); font-weight: 600; }
.pg-suppliers .sp-sel td, .sp-drawer .sp-sel td { background: color-mix(in srgb, var(--info) 9%, transparent); }
.pg-suppliers .sp-score { font-weight: 600; }
/* heat grid */
.pg-suppliers .sp-heat { max-height: none; }
.pg-suppliers .sp-htable { border-collapse: separate; border-spacing: 2px; font-size: 12px; width: 100%; min-width: 880px; table-layout: fixed; }
.pg-suppliers .sp-htable col.c-name { width: 250px; }
.pg-suppliers .sp-htable col.c-cov { width: 72px; }
.pg-suppliers .sp-htable col.c-qty { width: 84px; }
.pg-suppliers .sp-htable tbody th.sp-hname .tiny { white-space: nowrap !important; overflow: hidden; text-overflow: ellipsis; }
.pg-suppliers .sp-htable th { font-weight: 500; text-align: left; color: var(--ink-3); font-size: 11px; white-space: nowrap; }
.pg-suppliers .sp-htable th.sp-hw { font: 500 10.5px/1 var(--font-mono); color: var(--ink-3); text-align: left; padding: 0 0 4px; }
.pg-suppliers .sp-htable .sp-hname { position: sticky; left: 0; z-index: 1; background: var(--surface); padding: 3px 10px 3px 0; cursor: pointer; overflow: hidden; }
.pg-suppliers .sp-htable td.sp-hname, .pg-suppliers .sp-htable tbody th.sp-hname { display: table-cell; }
.pg-suppliers .sp-htable tbody th.sp-hname .ent-link { display: block; font-weight: 600; color: var(--ink); }
.pg-suppliers .sp-htable tbody th.sp-hname .tiny { display: block; font-weight: 400; white-space: normal; }
.pg-suppliers .sp-htable .sp-tier th { padding: 12px 0 4px; font: 600 11px/1 var(--font-cond); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
.pg-suppliers .sp-hcov { padding: 0 8px; min-width: 64px; font-variant-numeric: tabular-nums; }
.pg-suppliers .sp-hqty { padding-left: 8px; white-space: nowrap; }
.pg-suppliers .sp-hc { height: 24px; border-radius: 3px; }
/* shortfall ramp: one hue, light to dark on light surfaces; dim to bright on dark (anchor flips) */
.pg-suppliers { --hf1: #cde2fb; --hf2: #9ec5f4; --hf3: #6da7ec; --hf4: #2a78d6; --hf5: #0d366b; --hf-ink4: #fff; --hf-ink5: #fff; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .pg-suppliers { --hf1: #16304f; --hf2: #1c4a82; --hf3: #256abf; --hf4: #3987e5; --hf5: #9ec5f4; --hf-ink4: #fff; --hf-ink5: #0b0b0b; }
}
:root[data-theme="dark"] .pg-suppliers { --hf1: #16304f; --hf2: #1c4a82; --hf3: #256abf; --hf4: #3987e5; --hf5: #9ec5f4; --hf-ink4: #fff; --hf-ink5: #0b0b0b; }
.pg-suppliers .hf0 { background: var(--surface-2); box-shadow: inset 0 0 0 1px var(--hairline); }
.pg-suppliers .hf1 { background: var(--hf1); }
.pg-suppliers .hf2 { background: var(--hf2); }
.pg-suppliers .hf3 { background: var(--hf3); }
.pg-suppliers .hf4 { background: var(--hf4); color: var(--hf-ink4); }
.pg-suppliers .hf5 { background: var(--hf5); color: var(--hf-ink5); }
.pg-suppliers .hf-none { background: repeating-linear-gradient(135deg, var(--surface-2) 0 4px, var(--hairline-strong) 4px 5px); }
.pg-suppliers .hf-pend { background: var(--surface-2); box-shadow: inset 0 0 0 1px var(--hairline-strong); }
.pg-suppliers .hf-empty { background: transparent; }
.pg-suppliers .sp-hlegend { display: inline-flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; color: var(--ink-2); }
.pg-suppliers .sp-hlegend-row { margin: -2px 0 10px; }
.pg-suppliers .sp-hkey { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.pg-suppliers .sp-hsw { width: 14px; height: 12px; border-radius: 2px; display: inline-block; }
.pg-suppliers .sp-wf table { white-space: nowrap; }
.pg-suppliers .sp-wf th.mono { position: sticky; left: 0; background: var(--surface); z-index: 1; font-size: 12px; }
.pg-suppliers .sp-wfc { font-size: 11.5px; padding: 4px 6px !important; }
.pg-suppliers .sp-wfc.hf0 { box-shadow: none; }
/* ECOs */
.pg-suppliers .sp-eco { display: flex; flex-direction: column; gap: 2px; min-width: 200px; }
.pg-suppliers .sp-cutin { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 6px 0 14px; }
.pg-suppliers .sp-kpi-mono .kpi-value { font: 600 17px/1.3 var(--font-mono); word-break: break-all; }
.pg-suppliers .sp-dev { padding: 8px 0; border-top: 1px solid var(--hairline); }
.pg-suppliers .sp-dev .meter { margin-top: 6px; }
.pg-suppliers .sp-pill { display: inline-flex; gap: 6px; align-items: center; padding: 4px 8px; border: 1px solid var(--hairline); border-radius: 999px; }
/* pricing */
.pg-suppliers .sp-calc { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 10px; }
.pg-suppliers .sp-calc .sp-lbl { flex-direction: column; align-items: stretch; gap: 4px; }
.pg-suppliers .sp-calc-out { margin-top: 14px; }
.pg-suppliers .sp-calc-res { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.pg-suppliers .sp-price { font: 600 28px/1 var(--font-ui); color: var(--ink); }
/* drawer (outside .pg-suppliers) */
.sp-drawer .kv { margin-bottom: 14px; }
.sp-drawer .sp-polines { margin: 6px 0 4px; }
.sp-drawer .sp-dflags { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
.sp-drawer .sp-moved { font: 600 11.5px/1 var(--font-mono); padding: 2px 5px; border-radius: 4px; }
.sp-drawer .sp-moved.late { background: color-mix(in srgb, var(--serious) 18%, transparent); }
.sp-drawer .sp-moved.early { background: color-mix(in srgb, var(--good) 18%, transparent); }
.sp-drawer .sp-src { margin-top: 8px; border: 1px solid var(--hairline); border-radius: 10px; padding: 10px 12px; background: var(--surface-2); }
.sp-drawer .sp-src-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12.5px; margin-bottom: 6px; }
.sp-drawer .sp-mail { margin: 0; white-space: pre-wrap; font: 12px/1.5 var(--font-mono); color: var(--ink-2); max-height: 180px; overflow: auto; }
.sp-drawer .sp-xl table { font-size: 11.5px; white-space: nowrap; }
.sp-drawer .sp-xl th { text-transform: none; letter-spacing: 0; font: 500 11px/1.2 var(--font-ui); }
.sp-drawer .sp-xl td:last-child { white-space: normal; min-width: 150px; }
.sp-drawer .sp-hot { background: color-mix(in srgb, var(--sign-yellow) 30%, transparent) !important; font-weight: 600; }
.sp-drawer .code { max-height: 200px; }
.sp-drawer .sp-scmini { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.sp-drawer .sp-tree { list-style: none; padding: 0; margin: 0; }
.sp-drawer .sp-tree li { padding: 4px 0 4px calc((var(--lvl) - 1) * 18px); border-top: 1px solid var(--hairline); }
@media (max-width: 700px) {
  .pg-suppliers .sp-calc { grid-template-columns: 1fr; }
  .sp-drawer .sp-scmini { grid-template-columns: 1fr; }
}
`;
