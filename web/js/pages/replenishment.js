// Replenishment: how every stock point is kept full, and whether it is.
// Each item and location has a policy (MRP, reorder point, min/max, DRP, VMI,
// consignment). The page shows the position against its levels and the action it
// implies, the OEM-owned stock sitting at the CM in Taiwan, the stock suppliers
// hold for us, and a safety-stock calculator built on actual consumption.
import { html, listeners, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';

const POLICY_LABEL = {
  MRP: 'MRP', REORDER_POINT: 'Reorder point', MIN_MAX: 'Min / max', ORDER_UP_TO: 'Order-up-to', DRP: 'DRP', VMI: 'VMI',
  CONSIGNMENT: 'Consignment',
};
const STATUS_LABEL = {
  OK: 'OK', MRP_PLANNED: 'Planned', SHORTAGE: 'Shortage', REORDER: 'Reorder now', OVER_MAX: 'Over max',
  BELOW_MIN: 'Below min', CONSTRAINED: 'Supply-bound', NO_DATA: 'No data', EXPEDITE: 'Expedite',
  UNCONFIRMED: 'Unconfirmed', BELOW_SAFETY_STOCK: 'Below SS', DEFER: 'Defer', CANCEL: 'Cancel',
  PAST_DUE_RELEASE: 'Past-due release', RELEASE: 'Release',
};

let S = null;
// The page root persists across renders, so every delegated listener is tracked and removed.
const LISTENERS = listeners();

export async function render(el, ctx) {
  LISTENERS.clear();
  injectStyle('page-replenishment', PAGE_CSS);
  const d = await api.get('/api/plan/replenishment');
  const withCalc = d.rows.filter((r) => r.ss_calc);
  const pick = ctx.query.get('ss');
  const sel = withCalc.find((r) => `${r.item_id}@${r.site_id}` === pick) || withCalc.find((r) => r.item_id === 'BMS-B') || withCalc[0];
  S = { el, ctx, d, sel: sel ? `${sel.item_id}@${sel.site_id}` : null, sl: sel ? sel.service_level : 0.98 };
  draw();
}

export function unmount() {
  LISTENERS.clear();
  S = null;
}

const q = (v, uom) => (v == null ? '—' : uom === 'kg' && !Number.isInteger(v) ? fmt.num(v, 1) : fmt.int(v));

function draw() {
  const { el, d } = S;
  const k = d.kpis;
  const stranded = d.consigned.find((c) => c.stranded);
  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-replenishment">
      <p class="rp-context">
        <span>${icon('scale', 15)}<strong>${fmt.int(k.policies)} policies</strong> across Fremont, the CM in Taiwan, the 3PL and two supplier hubs</span>
        <span>Usage is actual consumption over the last 8 weeks, per production day</span>
        <span>as of ${fmt.dateLong(d.as_of)}</span>
      </p>

      <div class="kpi-row">
        ${ui.kpi({ label: 'Need attention', value: fmt.int(k.need_action), hint: 'Policies whose position implies an action' })}
        ${ui.kpi({ label: 'Below min or reorder point', value: fmt.int(k.below_min), status: k.below_min ? { tone: 'warning', label: 'Act' } : null, hint: 'Including the MRP stock-out' })}
        ${ui.kpi({ label: 'OEM stock at the CM', value: fmt.usd(k.consigned_value, { compact: true }), hint: 'Consigned drive units, pedal units and HMIs in Taichung' })}
        ${ui.kpi({ label: 'Stranded after ECO-0042', value: fmt.usd(k.stranded_value, { compact: true }), hint: `${fmt.int(k.stranded_units)} rev-B drive units at the CM` })}
        ${ui.kpi({ label: 'Supplier-held stock visible', value: fmt.usd(k.supplier_value, { compact: true }), hint: 'Finished goods suppliers report holding for the OEM' })}
      </div>

      <div class="rp-policies">${d.policies.map((p) => html`<div class="rp-policy${p.count ? '' : ' none'}">
        <div class="row"><span class="rp-pname">${POLICY_LABEL[p.policy] || p.policy}</span><span class="spacer"></span><span class="rp-pcount">${p.count}</span></div>
        <p>${p.text}</p>
      </div>`)}</div>

      <div class="grid">
        <div class="span-12">${ui.card({
          title: 'Recommended actions',
          subtitle: 'What each policy says to do now, most urgent first',
          flush: true,
          body: html`<div data-recs></div>`,
        })}</div>

        <div class="span-12">${ui.card({
          title: 'Position by item and location',
          subtitle: 'On hand + on order − allocated, against the policy’s levels. The bar shows stock against safety stock (SS), reorder point (ROP) and max.',
          flush: true,
          body: html`<div data-rows></div>`,
        })}</div>

        <div class="span-7">${ui.card({
          title: 'Consigned at the CM · Taichung, Taiwan',
          subtitle: 'OEM-owned modules the CM consumes at the line. Our serial count vs the CM’s own daily Excel report.',
          flush: true,
          body: consignedTable(d),
        })}
        ${stranded ? html`<div class="rp-stranded">${ui.callout({
          tone: 'warning',
          title: `${fmt.int(stranded.ours)} rev-B drive units stranded (${fmt.usd(stranded.value)})`,
          body: html`${d.eco42 ? html`${d.eco42.eco_id} switched the vehicle BOM to rev C on ${fmt.date(d.eco42.effective_date)} with disposition “${fmt.title(d.eco42.stock_disposition)}”, but a serial cut-in leaves nothing to use them up in production.` : ''} Options: keep them as service spares for rev-B vehicles in the field (≈ 2,000 built), rework to rev C at Tainan Motion, or return them for credit. Every week they sit, they cost carrying charges on the OEM’s books at the CM. <a class="ent-link" href="#/inventory?item=DU-B">See DU-B in inventory →</a>`,
        })}</div>` : ''}</div>
        <div class="span-5">${ui.card({
          title: 'Supplier-held stock',
          subtitle: 'What suppliers report holding for the OEM. VMI suppliers manage it between agreed levels.',
          flush: true,
          body: supplierTable(d),
        })}</div>

        <div class="span-12">${ui.card({
          title: 'Safety stock calculator',
          subtitle: 'SS = z·σd·√LT + z·avg(d)·σLT on the last 8 weeks of actual consumption, against the stored safety stock. Pick a row to work it through.',
          body: html`<div class="rp-ss"><div data-ssrows></div><div data-sscalc class="rp-sscalc"></div></div>`,
        })}</div>

        <div class="span-12">${ui.card({
          title: 'Days of supply',
          subtitle: 'On hand ÷ usage per production day, lowest first. Darker bars are more days of supply.',
          flush: true,
          body: html`<div data-dos></div>`,
        })}</div>
      </div>
    </div>`;

  drawRecs();
  drawRows();
  drawSsRows();
  drawCalc();
  drawDos();
  LISTENERS.listen(el, 'input', '[data-sl]', (e, inp) => {
    S.sl = Number(inp.value) / 1000;
    drawCalc(true);
  });
}

// ---------------------------------------------------------------------------
// recommendations + positions
// ---------------------------------------------------------------------------
function statusChip(r) {
  return ui.chip(r.tone === 'good' ? 'good' : r.tone, STATUS_LABEL[r.status] || fmt.title(r.status));
}

const SITE_SHORT = { 'CM-TXG': 'CM · Taichung', '3PL-RNO': '3PL · Reno', 'OEM-FRE': 'Pack line · Fremont' };

function where(r) {
  const short = SITE_SHORT[r.site_id] || r.site.replace(/ (Co\.|Electronics|Cell Co\.)/, '');
  return html`<span class="small">${short}</span>`;
}

function drawRecs() {
  const host = S.el.querySelector('[data-recs]');
  const rows = S.d.recommendations;
  if (!rows.length) {
    host.innerHTML = String(ui.empty('Every stock point is inside its policy.', { icon: 'check-circle' }));
    return;
  }
  ui.dataTable(host, {
    columns: [
      { key: 'status', label: 'Status', render: statusChip, value: (r) => r.status },
      { key: 'item_id', label: 'Item', mono: true, render: (r) => itemLink(r) },
      { key: 'site', label: 'Where', render: where },
      { key: 'policy', label: 'Policy', render: (r) => POLICY_LABEL[r.policy] || r.policy },
      { key: 'action', label: 'Action', wrap: true, render: (r) => html`<span class="small">${r.action}</span>${r.order_qty ? html` <strong class="small">(${fmt.int(r.order_qty)})</strong>` : ''}${r.link ? html` <a class="ent-link small" href="${r.link}">Open →</a>` : ''}` },
    ],
    rows,
    pageSize: 10,
    dense: true,
  });
}

function itemLink(r) {
  return r.policy === 'MRP' ? html`<a class="id-link" href="#/mrp?item=${encodeURIComponent(r.item_id)}">${r.item_id}</a>` : link.item(r.item_id);
}

function band(r) {
  // which quantity the bar shows, and which levels it marks
  let val = r.on_hand ?? 0;
  const marks = [];
  if (r.policy === 'REORDER_POINT') val = r.position;
  if (r.safety_stock && r.policy !== 'VMI' && r.policy !== 'MIN_MAX') marks.push(['SS', r.safety_stock]);
  if (r.reorder_point) marks.push([r.policy === 'VMI' || r.policy === 'MIN_MAX' ? 'Min' : 'ROP', r.reorder_point]);
  if (r.max_qty) marks.push(['Max', r.max_qty]);
  const scale = Math.max(val, ...marks.map((m) => m[1]), 1) * 1.12;
  const low = marks.length ? Math.min(...marks.map((m) => m[1])) : 0;
  const tone = r.on_hand == null ? 'none' : val < low ? 'low' : (r.max_qty && val > r.max_qty ? 'high' : 'ok');
  const lbl = r.policy === 'REORDER_POINT' ? 'position' : r.policy === 'VMI' ? 'supplier FG' : 'on hand';
  return html`<div class="rp-band" title="${`${lbl} ${q(val, r.uom)} · ${marks.map((m) => `${m[0]} ${q(m[1], r.uom)}`).join(' · ')}`}">
    <div class="rp-track"><span class="rp-fill ${tone}" style="width:${Math.min(100, (val / scale) * 100).toFixed(1)}%"></span>
      ${marks.map((m) => html`<span class="rp-mark" style="left:${((m[1] / scale) * 100).toFixed(1)}%"><span>${m[0]}</span></span>`)}
    </div>
    <div class="rp-cap"><strong>${q(val, r.uom)}</strong> ${lbl}${marks.length ? html` · ${marks.map((m) => `${m[0]} ${fmt.compact(m[1])}`).join(' · ')}` : ''}</div>
  </div>`;
}

function drawRows() {
  const host = S.el.querySelector('[data-rows]');
  ui.dataTable(host, {
    columns: [
      { key: 'item_id', label: 'Item', render: (r) => html`<span class="mono">${itemLink(r)}</span><div class="muted small rp-name">${r.name}</div>` },
      { key: 'site', label: 'Where', render: (r) => html`${where(r)}<div class="muted small">${r.owner === 'OEM' ? 'OEM-owned' : r.owner === 'CM' ? 'CM-owned' : 'Supplier-owned'}</div>` },
      { key: 'policy', label: 'Policy', render: (r) => html`<span class="rp-ptag">${POLICY_LABEL[r.policy] || r.policy}</span>` },
      { key: 'on_hand', label: 'On hand', num: true, render: (r) => q(r.on_hand, r.uom) },
      { key: 'on_order', label: 'On order', num: true, value: (r) => (r.policy === 'DRP' ? r.pipeline : r.on_order), render: (r) => (r.policy === 'DRP' ? html`${q(r.pipeline, r.uom)}<div class="muted small">in transit + built</div>` : q(r.on_order, r.uom)) },
      { key: 'allocated', label: 'Alloc.', num: true, title: 'Allocated to customer orders', render: (r) => (r.allocated ? q(r.allocated, r.uom) : html`<span class="nil">—</span>`) },
      { key: 'band', label: 'Against levels', sortable: false, render: band },
      { key: 'status', label: 'Status', render: statusChip, value: (r) => r.status },
      { key: 'dos', label: 'Days', num: true, title: 'Days of supply on hand', render: (r) => (r.dos == null ? html`<span class="nil">—</span>` : fmt.num(r.dos, 1)) },
    ],
    rows: S.d.rows,
    pageSize: 30,
    search: true,
    searchPlaceholder: 'Filter items, sites, policies…',
  });
}

// ---------------------------------------------------------------------------
// consigned + supplier-held
// ---------------------------------------------------------------------------
function consignedTable(d) {
  return html`<div class="table-wrap"><table class="table dense rp-cons">
    <thead><tr><th>Part</th><th class="num">Ours · CM says</th><th class="num">Min–max</th><th class="num">Cover</th><th class="num">Value</th><th>Next receipt</th></tr></thead>
    <tbody>${d.consigned.map((c) => {
      const diff = c.cm_report != null ? c.cm_report - c.ours : null;
      return html`<tr class="${c.stranded ? 'rp-strand' : ''}">
        <td><span class="mono">${link.item(c.item_id)}</span><div class="muted small">${c.name}${c.stranded ? ' · phase-out' : ''}</div></td>
        <td class="num"><strong>${fmt.int(c.ours)}</strong> · ${c.cm_report != null ? fmt.int(c.cm_report) : '—'}${diff ? html`<div>${ui.chip('warning', `CM ${diff > 0 ? '+' : ''}${diff}`)}</div>` : c.cm_report != null ? html`<div class="muted small">match</div>` : ''}</td>
        <td class="num">${c.min != null ? `${fmt.int(c.min)}–${fmt.int(c.max)}` : html`<span class="muted small">none</span>`}</td>
        <td class="num">${c.stranded ? html`<span class="muted small">unused</span>` : c.days_cover != null ? `${fmt.num(c.days_cover, 1)}d` : '—'}</td>
        <td class="num">${fmt.usd(c.value, { compact: true })}</td>
        <td>${c.next_receipt ? html`${link.po(c.next_receipt.po_id, c.next_receipt.line_no)}<div class="small">${fmt.int(c.next_receipt.qty)} · ${fmt.date(c.next_receipt.date)}${c.next_receipt.unconfirmed ? html` · <span class="rp-unc">${icon('alert-triangle', 11)} unconfirmed</span>` : ''}</div>` : html`<span class="nil">—</span>`}</td>
      </tr>`;
    })}</tbody></table></div>
    <p class="rp-foot small muted">CM figures come from its daily bilingual Excel report (${d.cm_report_date ? fmt.date(d.cm_report_date) : 'none'}), parsed from email in the ${link.route('integrations', 'Integration Hub')}. A difference is a count to reconcile at the CM, not an error to overwrite.</p>`;
}

function supplierTable(d) {
  const byKey = new Map();
  for (const s of d.supplier_stock) {
    const key = `${s.site_id}|${s.item_id}`;
    if (!byKey.has(key)) byKey.set(key, { ...s, fg: 0, wip: 0 });
    const r = byKey.get(key);
    if (s.status === 'AVAILABLE') r.fg += s.qty;
    else r.wip += s.qty;
  }
  const vmi = Object.fromEntries(d.rows.filter((r) => r.policy === 'VMI').map((r) => [`${r.site_id}|${r.item_id}`, r]));
  return html`<div class="table-wrap"><table class="table dense">
    <thead><tr><th>Supplier · part</th><th class="num">Finished</th><th class="num">In production</th><th>Agreement</th></tr></thead>
    <tbody>${[...byKey.values()].map((s) => {
      const v = vmi[`${s.site_id}|${s.item_id}`];
      return html`<tr>
        <td><span class="small">${s.site.split(' · ')[0]}</span><div class="mono small">${s.item_id}</div></td>
        <td class="num"><strong>${fmt.int(s.fg)}</strong><div class="muted small">as of ${fmt.date(s.as_of)}</div></td>
        <td class="num">${fmt.int(s.wip)}</td>
        <td>${v ? html`${statusChip(v)}<div class="muted small">VMI ${fmt.compact(v.reorder_point)}–${fmt.compact(v.max_qty)}</div>` : html`<span class="muted small">Reported, no VMI</span>`}</td>
      </tr>`;
    })}</tbody></table></div>
    <p class="rp-foot small muted">Kestrel’s figures arrive as a weekly Excel attachment${d.kes_email ? html` (latest: “${d.kes_email.subject}”)` : ''}; the others come through the supplier portal.</p>`;
}

// ---------------------------------------------------------------------------
// safety stock calculator
// ---------------------------------------------------------------------------
function drawSsRows() {
  const host = S.el.querySelector('[data-ssrows]');
  const rows = S.d.rows.filter((r) => r.ss_calc).map((r) => ({ ...r, key: `${r.item_id}@${r.site_id}` }));
  ui.dataTable(host, {
    columns: [
      { key: 'item_id', label: 'Item', render: (r) => html`<span class="mono${r.key === S.sel ? ' rp-selrow' : ''}">${r.item_id}</span><div class="muted small">${where(r)}</div>` },
      { key: 'svc', label: 'Service', num: true, value: (r) => r.service_level, render: (r) => fmt.pct(r.service_level, 0) },
      { key: 'mean', label: 'Usage / day', num: true, value: (r) => r.ss_calc.mean, render: (r) => fmt.num(r.ss_calc.mean, 1) },
      { key: 'lt', label: 'LT days', num: true, title: 'Lead time in production days', value: (r) => r.ss_calc.lt_prod_days, render: (r) => fmt.num(r.ss_calc.lt_prod_days, 1) },
      { key: 'calc', label: 'Calc. SS', num: true, value: (r) => r.ss_calc.ss, render: (r) => html`<strong>${fmt.int(r.ss_calc.ss)}</strong>` },
      { key: 'stored', label: 'Stored', num: true, value: (r) => r.safety_stock, render: (r) => fmt.int(r.safety_stock) },
      {
        key: 'ratio', label: 'Stored ÷ calc.', num: true, value: (r) => r.ss_calc.ratio,
        render: (r) => {
          const x = r.ss_calc.ratio;
          if (x == null) return html`<span class="nil">—</span>`;
          const tone = x < 0.7 ? 'warning' : x > 1.5 ? 'info' : 'good';
          return ui.chip(tone, `${fmt.num(x, 2)}× ${x < 0.7 ? 'thin' : x > 1.5 ? 'heavy' : 'ok'}`);
        },
      },
    ],
    rows,
    pageSize: 12,
    dense: true,
    onRowClick: (r) => {
      S.sel = r.key;
      S.sl = r.service_level;
      S.ctx.setQuery({ ss: r.key }, { silent: true });
      drawSsRows();
      drawCalc();
    },
    initialSort: { key: 'ratio', dir: 'asc' },
  });
}

function drawCalc(fromSlider = false) {
  const host = S.el.querySelector('[data-sscalc]');
  const r = S.d.rows.find((x) => `${x.item_id}@${x.site_id}` === S.sel);
  if (!r || !r.ss_calc) {
    host.innerHTML = String(ui.empty('Pick a row to work through its safety stock.'));
    return;
  }
  const c = r.ss_calc;
  const z = invNorm(S.sl);
  const a = z * c.sd * Math.sqrt(c.lt_prod_days);
  const b = z * c.mean * c.lt_sd_prod_days;
  const ss = a + b;
  const rss = z * Math.sqrt(c.lt_prod_days * c.sd ** 2 + (c.mean ** 2) * c.lt_sd_prod_days ** 2);
  const body = html`
    <div class="rp-calc-head"><span class="mono strong">${r.item_id}</span> <span class="muted small">${r.name} · ${r.site}</span></div>
    <label class="rp-sl"><span class="small">Service level <strong>${(S.sl * 100).toFixed(1)}%</strong> → z = ${z.toFixed(2)}</span>
      <input type="range" min="850" max="995" step="5" value="${Math.round(S.sl * 1000)}" data-sl aria-label="Service level"></label>
    <div class="rp-formula">
      <div><span class="muted">demand variability</span> z · σd · √LT = ${z.toFixed(2)} × ${fmt.num(c.sd, 1)} × √${fmt.num(c.lt_prod_days, 1)} = <strong>${fmt.int(a)}</strong></div>
      <div><span class="muted">lead-time variability</span> z · avg(d) · σLT = ${z.toFixed(2)} × ${fmt.num(c.mean, 1)} × ${fmt.num(c.lt_sd_prod_days, 2)} = <strong>${fmt.int(b)}</strong></div>
      <div class="rp-total">Safety stock = <strong>${fmt.int(ss)}</strong> ${r.uom} <span class="rp-rss">(root-sum-square form: ${fmt.int(rss)})</span></div>
    </div>
    <div class="rp-compare">
      <div><span class="muted small">Stored</span><strong>${fmt.int(r.safety_stock)}</strong></div>
      <div><span class="muted small">Calculated</span><strong>${fmt.int(ss)}</strong></div>
      <div><span class="muted small">Days of usage</span><strong>${fmt.num(ss / (c.mean || 1), 1)}</strong></div>
    </div>
    <p class="small muted">Inputs: ${c.days} production days of actual consumption${r.usage && r.usage.includes ? html` (including ${r.usage.includes}, the part this one replaced)` : ''}; lead time ${r.lead_time} calendar days = ${fmt.num(c.lt_prod_days, 1)} production days, σLT ${fmt.num(c.lt_sd_prod_days, 2)}. ${ss > r.safety_stock * 1.3 ? 'The stored level protects less than this service level implies.' : ss < r.safety_stock * 0.7 ? 'The stored level holds more than this service level needs.' : 'The stored level is in line with the calculation.'}</p>`;
  if (fromSlider) {
    // keep the slider element (and focus) while dragging: update everything but the input
    const tmp = document.createElement('div');
    tmp.innerHTML = String(body);
    const cur = host.querySelector('[data-sl]');
    const next = tmp.querySelector('[data-sl]');
    if (cur && next) next.replaceWith(cur);
    host.replaceChildren(...tmp.childNodes);
    return;
  }
  host.innerHTML = String(body);
}

// Acklam's rational approximation to the inverse normal CDF (|error| < 1.2e-9)
function invNorm(p) {
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
  const pl = 0.02425;
  if (p < pl) {
    const qq = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * qq + c[1]) * qq + c[2]) * qq + c[3]) * qq + c[4]) * qq + c[5]) / ((((d[0] * qq + d[1]) * qq + d[2]) * qq + d[3]) * qq + 1);
  }
  if (p > 1 - pl) {
    const qq = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0] * qq + c[1]) * qq + c[2]) * qq + c[3]) * qq + c[4]) * qq + c[5]) / ((((d[0] * qq + d[1]) * qq + d[2]) * qq + d[3]) * qq + 1);
  }
  const qq = p - 0.5;
  const rr = qq * qq;
  return (((((a[0] * rr + a[1]) * rr + a[2]) * rr + a[3]) * rr + a[4]) * rr + a[5]) * qq / (((((b[0] * rr + b[1]) * rr + b[2]) * rr + b[3]) * rr + b[4]) * rr + 1);
}

// ---------------------------------------------------------------------------
// days of supply
// ---------------------------------------------------------------------------
function drawDos() {
  const rows = S.d.rows.filter((r) => r.dos != null && r.usage && r.usage.mean > 0)
    .map((r) => ({ ...r, lt_days: r.lead_time, ss_days_v: r.ss_days }));
  const max = Math.max(...rows.map((r) => r.dos), 1);
  ui.dataTable(S.el.querySelector('[data-dos]'), {
    columns: [
      { key: 'item_id', label: 'Item', mono: true, render: (r) => itemLink(r) },
      { key: 'site', label: 'Where', render: where },
      { key: 'on_hand', label: 'On hand', num: true, render: (r) => q(r.on_hand, r.uom) },
      { key: 'use', label: 'Usage / day', num: true, value: (r) => r.usage.mean, render: (r) => fmt.num(r.usage.mean, 1) },
      {
        key: 'dos', label: 'Days of supply', value: (r) => r.dos,
        render: (r) => html`<div class="rp-dos"><span class="rp-dosbar" style="width:${Math.max(2, (r.dos / max) * 100).toFixed(1)}%;background:color-mix(in srgb, var(--series-1) ${Math.round(25 + 60 * Math.min(1, r.dos / max))}%, var(--surface))"></span><span class="num">${fmt.num(r.dos, 1)}</span></div>`,
      },
      { key: 'ss_days', label: 'SS in days', num: true, render: (r) => (r.ss_days != null ? fmt.num(r.ss_days, 1) : '—') },
      { key: 'cover', label: 'Cover incl. on order', num: true, value: (r) => r.cover_days, render: (r) => (r.cover_days != null ? fmt.num(r.cover_days, 1) : '—') },
      {
        key: 'flag', label: 'Signal', value: (r) => (r.policy === 'DRP' ? 1 : r.ss_days != null && r.dos < r.ss_days ? 0 : 2),
        render: (r) => (r.policy === 'DRP'
          ? ui.chip('info', 'Allocated on arrival (backlog)')
          : r.ss_days != null && r.dos < r.ss_days ? ui.chip('critical', 'Under safety stock') : html`<span class="muted small">—</span>`),
      },
    ],
    rows,
    initialSort: { key: 'dos', dir: 'asc' },
    pageSize: 30,
    dense: true,
  });
}

const PAGE_CSS = `
.pg-replenishment .rp-context { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; margin: -4px 0 16px; font-size: 13px; color: var(--ink-2); }
.pg-replenishment .rp-context > span { display: inline-flex; align-items: center; gap: 6px; }
.pg-replenishment .rp-context svg { color: var(--sign); }
.pg-replenishment .rp-policies { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; margin: 16px 0; }
.pg-replenishment .rp-policy { padding: 12px 14px; border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface); }
.pg-replenishment .rp-policy.none { background: var(--surface-2); }
.pg-replenishment .rp-policy p { margin: 6px 0 0; font-size: 12.5px; color: var(--ink-2); line-height: 1.4; }
.pg-replenishment .rp-pname { font: 600 14px var(--font-cond); letter-spacing: .01em; }
.pg-replenishment .rp-pcount { font: 600 13px var(--font-ui); color: var(--ink-2); font-variant-numeric: tabular-nums; }
.pg-replenishment .rp-ptag { display: inline-block; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--hairline-strong); font: 600 11.5px var(--font-cond); letter-spacing: .03em; }
.pg-replenishment .rp-name { max-width: 190px; overflow: hidden; text-overflow: ellipsis; }
.pg-replenishment .rp-band { width: 168px; }
.pg-replenishment .rp-track { position: relative; height: 8px; border-radius: 4px; background: var(--meter-track); margin: 12px 0 4px; }
.pg-replenishment .rp-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; background: var(--series-1); }
.pg-replenishment .rp-fill.low { background: var(--critical); }
.pg-replenishment .rp-fill.high { background: var(--series-other); }
.pg-replenishment .rp-fill.none { width: 0 !important; }
.pg-replenishment .rp-mark { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--ink); border-radius: 1px; }
.pg-replenishment .rp-mark span { position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); font: 600 9.5px var(--font-cond); letter-spacing: .04em; color: var(--ink-2); white-space: nowrap; }
.pg-replenishment .rp-cap { font-size: 11.5px; color: var(--ink-3); white-space: normal; line-height: 1.3; }
.pg-replenishment .rp-cap strong { color: var(--ink); font-weight: 600; }
.pg-replenishment .table td { vertical-align: middle; }
.pg-replenishment .rp-strand td { background: color-mix(in srgb, var(--warning) 8%, var(--surface)); }
.pg-replenishment .rp-cons td { height: 44px; }
.pg-replenishment .rp-unc { display: inline-flex; align-items: center; gap: 3px; color: var(--ink-2); }
.pg-replenishment .rp-unc svg { color: var(--warning); }
.pg-replenishment .rp-stranded { margin-top: 12px; }
.pg-replenishment .rp-foot { padding: 10px 16px 12px; border-top: 1px solid var(--hairline); margin: 0; }
.pg-replenishment .rp-ss { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr); gap: 20px; align-items: start; }
.pg-replenishment .rp-selrow { box-shadow: inset 3px 0 0 var(--sign); padding-left: 6px; margin-left: -6px; font-weight: 600; }
.pg-replenishment .rp-sscalc { padding: 14px 16px; border: 1px solid var(--hairline); border-radius: 12px; background: var(--surface-2); display: grid; gap: 12px; position: sticky; top: calc(var(--topbar-h) + 12px); }
.pg-replenishment .rp-sl { display: grid; gap: 6px; }
.pg-replenishment .rp-sl input { width: 100%; accent-color: var(--sign); }
.pg-replenishment .rp-formula { display: grid; gap: 6px; font: 400 12.5px/1.5 var(--font-mono); padding: 10px 12px; border-radius: 8px; background: var(--surface); border: 1px solid var(--hairline); }
.pg-replenishment .rp-formula .muted { display: block; font: 600 10.5px var(--font-cond); letter-spacing: .06em; text-transform: uppercase; }
.pg-replenishment .rp-total { padding-top: 6px; border-top: 1px solid var(--hairline); font-family: var(--font-ui); font-size: 13.5px; }
.pg-replenishment .rp-rss { color: var(--ink-3); font-size: 12px; }
.pg-replenishment .rp-compare { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.pg-replenishment .rp-sscalc, .pg-replenishment .rp-sscalc > * { min-width: 0; }
.pg-replenishment .rp-formula div { overflow-wrap: anywhere; }
.pg-replenishment .rp-compare > div { display: grid; gap: 2px; padding: 8px 10px; border-radius: 8px; background: var(--surface); border: 1px solid var(--hairline); }
.pg-replenishment .rp-compare strong { font: 600 20px var(--font-ui); }
.pg-replenishment .rp-dos { display: flex; align-items: center; gap: 8px; min-width: 180px; }
.pg-replenishment .rp-dosbar { display: inline-block; height: 10px; border-radius: 0 4px 4px 0; max-width: 140px; }
@media (max-width: 1100px) {
  .pg-replenishment .rp-ss { grid-template-columns: minmax(0, 1fr); }
  .pg-replenishment .rp-sscalc { position: static; }
}
`;
