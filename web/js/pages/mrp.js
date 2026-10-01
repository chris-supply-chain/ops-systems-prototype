// Material Plan · MRP. The classic time-phased record for every part the OEM buys,
// computed live: the MPS (CM commit, pack MPS, 3PL kitting) is exploded through
// the BOM with effectivity, then each item is netted day by day against on-hand
// and supplier promise dates. Click a gross requirement to peg it to the builds
// and customer promises behind it. Below: the multi-level BOM, the demand we
// release to tiers 2-3, and the textbook cases that prove the netting.
import { html, raw, listeners, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const SITE_ORDER = ['OEM-FRE', 'CM-TXG', '3PL-RNO'];
const SITE_NOTE = {
  'OEM-FRE': 'Pack line components, received at Fremont',
  'CM-TXG': 'OEM-owned modules staged at the CM, Taiwan',
  '3PL-RNO': 'Bought straight into the 3PL for kitting',
};
const MSG_LABEL = {
  SHORTAGE: 'Shortage', PAST_DUE_RELEASE: 'Past-due release', EXPEDITE: 'Expedite', BELOW_SAFETY_STOCK: 'Below SS',
  UNCONFIRMED: 'Unconfirmed', RELEASE: 'Release', DEFER: 'Defer', CANCEL: 'Cancel',
};
const DOW = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const POS_LABEL = {
  WHEEL_F: 'Front wheel', WHEEL_R: 'Rear wheel', DRIVE_UNIT: 'Drive unit', PEDAL_UNIT: 'Pedal unit', HMI: 'HMI',
  BMS: 'BMS', AFE: 'AFE IC', EXTRA_PACK: 'Extra pack',
};
const LOT_RULE = {
  MOQ_MULTIPLE: 'At least the MOQ, rounded up to the multiple', LOT_FOR_LOT: 'Lot-for-lot', FIXED: 'Fixed quantity',
  PERIOD_ORDER: 'Period order quantity',
};

let S = null;
// The page root persists across renders, so every delegated listener is tracked and removed.
const LISTENERS = listeners();

export async function render(el, ctx) {
  LISTENERS.clear();
  injectStyle('page-mrp', PAGE_CSS);
  const item = ctx.query.get('item') || undefined;
  const d = await api.get('/api/plan/mrp', { item });
  S = {
    el, ctx, d,
    bucket: ctx.query.get('bucket') === 'week' ? 'week' : 'day',
    bom: { item: ctx.query.get('bom') || 'LV1-SLATE-L', date: ctx.query.get('on') || d.as_of, data: null },
  };
  draw();
  loadBom();
  if (ctx.query.get('peg') != null) openPeg(Number(ctx.query.get('peg')));
}

export function unmount() {
  LISTENERS.clear();
  S = null;
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------
function draw() {
  const { el, d } = S;
  const r = d.record;
  const shortItems = d.items.filter((i) => i.first_short);
  const acting = d.items.filter((i) => ['SHORTAGE', 'PAST_DUE_RELEASE', 'EXPEDITE'].includes(i.status));
  const totalMsgs = d.items.reduce((a, i) => a + i.messages, 0);
  const run = d.latest_run;
  const earliest = shortItems.map((i) => i.first_short).sort()[0];

  el.innerHTML = html`
    ${ui.pageHeader({
      actions: html`${ui.button({ label: 'Run history', icon: 'clock', variant: 'ghost', attrs: { 'data-action': 'runs' } })}
        ${ui.button({ label: 'Re-run MRP', icon: 'refresh', variant: 'primary', attrs: { 'data-action': 'rerun' } })}`,
    })}
    <div class="pg-mrp">
      <p class="mr-context">
        <span>${icon('layers', 15)}<strong>Computed live</strong> from today's MPS, BOM, stock and supplier promises</span>
        <span>as of ${fmt.dateLong(d.as_of)}</span>
        <span>${d.days} daily buckets</span>
        ${run ? html`<span class="muted">Last stored run <strong class="mono">#${run.run_id}</strong> · ${fmt.dt(run.ran_at)} · ${run.triggered_by}</span>` : ''}
      </p>

      <div class="kpi-row">
        ${ui.kpi({ label: 'Items planned', value: fmt.int(d.items.length), hint: 'Every part the OEM buys, at 3 stock points' })}
        ${ui.kpi({
          label: 'Stock-out risk', value: fmt.int(shortItems.length), unit: shortItems.length === 1 ? 'item' : 'items',
          status: shortItems.length ? { tone: 'critical', label: 'Line stop' } : { tone: 'good', label: 'Clear' },
          hint: earliest ? `${shortItems.map((i) => i.item_id).join(', ')} runs out ${fmt.date(earliest)}` : 'No item runs out inside the horizon',
        })}
        ${ui.kpi({ label: 'Items to act on', value: fmt.int(acting.length), hint: 'Shortage, past-due release or expedite' })}
        ${ui.kpi({ label: 'Action messages', value: fmt.int(totalMsgs), hint: 'Expedite · defer · cancel · unconfirmed · release' })}
      </div>

      <div class="grid mr-main">
        <div class="span-3">${ui.card({ title: 'Items', subtitle: 'By stock point and BOM level', flush: true, body: picker(d) })}</div>
        <div class="span-9 mr-right">
          ${itemCard(d, r)}
          ${ui.card({
            title: 'Time-phased record',
            subtitle: 'Gross requirements come from the MPS through the BOM; scheduled receipts land on the supplier’s promise date, not our need date.',
            actions: html`<div data-bucket></div>`,
            flush: true,
            body: html`<div data-grid></div>`,
          })}
          ${ui.card({
            title: 'Projected available balance',
            subtitle: 'As-is (current supply) against the recommended plan (reschedules + planned orders), with safety stock',
            tableToggle: true,
            body: projectionChart(d, r),
          })}
          ${ui.card({ title: 'Action messages', subtitle: 'What the planner should do, most urgent first', flush: true, body: html`<div data-msgs></div>` })}
          ${ui.card({ title: 'Scheduled receipts', subtitle: 'Open PO lines for this item: our need date against the supplier’s promise', flush: true, body: html`<div data-rcpts></div>` })}
        </div>

        <div class="span-12">${ui.card({
          title: 'Bill of materials: multi-level explosion',
          subtitle: 'Parent → child with quantity per and extended quantity, effective on the chosen date. ECO cut-ins swap the child on their effective date.',
          flush: true,
          body: html`<div class="row wrap mr-bomctl" data-bomctl></div><div data-bom>${ui.loading('Exploding BOM')}</div>`,
        })}</div>

        <div class="span-12">${ui.card({
          title: 'Demand released to tiers 2–3',
          subtitle: 'Our purchase schedule (receipts + planned orders) exploded through supplier BOMs and offset by their lead times, plus what the CM’s own suppliers must ship for the vehicle MPS. This is the weekly forecast release.',
          flush: true,
          body: visibility(d),
        })}</div>

        <div class="span-12">${textbook(d)}</div>
      </div>
    </div>`;

  ui.segmented(el.querySelector('[data-bucket]'), {
    options: [{ value: 'day', label: 'Days' }, { value: 'week', label: 'Weeks' }],
    value: S.bucket, label: 'Bucket size',
    onChange: (v) => {
      S.bucket = v;
      S.ctx.setQuery({ bucket: v === 'week' ? 'week' : null }, { silent: true });
      drawGrid();
    },
  });
  drawGrid();
  drawMessages(r);
  drawReceipts(r);
  drawBomControls();

  LISTENERS.listen(el, 'click', '[data-pick]', (e, b) => {
    e.preventDefault();
    S.ctx.setQuery({ item: b.dataset.pick, peg: null });
  });
  LISTENERS.listen(el, 'click', '[data-peg]', (e, td) => openPeg(Number(td.dataset.peg)));
  LISTENERS.listen(el, 'keydown', '[data-peg]', (e, td) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPeg(Number(td.dataset.peg)); }
  });
  LISTENERS.listen(el, 'click', '[data-action=rerun]', (e, b) => rerun(b));
  LISTENERS.listen(el, 'click', '[data-action=runs]', () => openRuns());
  LISTENERS.listen(el, 'click', '[data-mrp-item]', (e, a) => {
    e.preventDefault();
    S.ctx.setQuery({ item: a.dataset.mrpItem, peg: null });
  });
  LISTENERS.listen(el, 'change', '[data-bom-root]', (e, sel) => { S.bom.item = sel.value; syncBom(); });
  LISTENERS.listen(el, 'change', '[data-bom-date]', (e, inp) => { if (inp.value) { S.bom.date = inp.value; syncBom(); } });
  LISTENERS.listen(el, 'click', '[data-bom-on]', (e, btn) => { S.bom.date = btn.dataset.bomOn; syncBom(); });
}

// ---------------------------------------------------------------------------
// item picker
// ---------------------------------------------------------------------------
function picker(d) {
  const sel = d.record.item_id;
  return html`<div class="mr-picker">${SITE_ORDER.map((site) => {
    const list = d.items.filter((i) => i.site_id === site);
    if (!list.length) return '';
    return html`<div class="mr-group">
      <div class="mr-group-head"><span>${list[0].site}</span><span class="mr-group-note">${SITE_NOTE[site] || ''}</span></div>
      ${list.map((i) => html`<a href="#" class="mr-item${i.item_id === sel ? ' active' : ''}" data-pick="${i.item_id}"${i.item_id === sel ? raw(' aria-current="true"') : ''}>
        <span class="mr-dot tone-${i.tone}" aria-hidden="true"></span>
        <span class="mr-item-main"><span class="mono mr-item-id">${i.item_id}</span><span class="mr-item-name">${i.name}</span></span>
        <span class="mr-item-meta">
          <span class="mr-llc" title="Low-level code (BOM depth)">L${i.llc ?? '–'}</span>
          <span class="mr-status tone-${i.tone}">${i.status ? MSG_LABEL[i.status] || fmt.title(i.status) : 'OK'}</span>
        </span>
      </a>`)}
    </div>`;
  })}</div>`;
}

// ---------------------------------------------------------------------------
// item header: parameters + what the record says
// ---------------------------------------------------------------------------
function itemCard(d, r) {
  const q = (v) => qty(v, r.uom, true);
  const next = r.receipt_detail.filter((x) => x.index >= (r.first_short ? daysFrom(d.as_of, r.first_short) : 0))[0];
  const notes = [];
  if (r.first_short) {
    const alt = r.alternates[0];
    notes.push(ui.callout({
      tone: 'critical',
      title: `Runs out on ${fmt.dateLong(r.first_short)}: ${r.site.toLowerCase().includes('fremont') ? 'pack line' : 'line'} stop risk`,
      body: html`Projected balance bottoms at <strong>${q(r.min_projected)}</strong> on ${fmt.date(r.min_projected_date)} with the supply we have.
        ${next ? html` The next receipt, ${link.po(next.po_id, next.line_no)} (${q(next.qty)}), is promised <strong>${fmt.date(next.promise_date)}</strong>${next.need_date ? html` but we needed it ${fmt.date(next.need_date)}` : ''}.` : ''}
        ${alt ? html` ${alt.deviation} lets up to <strong>${fmt.int(alt.allowance)}</strong> ${alt.item} boards (of ${fmt.int(alt.on_hand)} on hand) cover it until ${fmt.date(alt.valid_to)}, and the record already uses them.` : ''}
        Lead time is ${r.lead_time} days, so a new order cannot fix it. The fix is to pull supply in: ${link.route('loop', 'the Closed Loop has a proposed expedite')}.`,
    }));
  } else if (r.messages.some((m) => m.message === 'EXPEDITE' || m.message === 'PAST_DUE_RELEASE')) {
    const ex = r.messages.filter((m) => m.message === 'EXPEDITE');
    notes.push(ui.callout({
      tone: 'warning',
      title: `${ex.length} receipt${ex.length === 1 ? '' : 's'} to pull in`,
      body: html`Safety stock is breached before the next receipt lands. Rescheduling existing POs in covers it without new orders.`,
    }));
  }
  const unconf = r.receipt_detail.filter((x) => !x.confirmed);
  if (unconf.length) {
    notes.push(ui.callout({
      tone: 'info',
      title: `${unconf.length} receipt${unconf.length === 1 ? '' : 's'} without a supplier promise`,
      body: html`Planned on our need date until the supplier confirms: ${unconf.slice(0, 4).map((x, i) => html`${i ? ', ' : ''}${link.po(x.po_id, x.line_no)}`)}${unconf.length > 4 ? ` +${unconf.length - 4}` : ''}.`,
    }));
  }
  return ui.card({
    title: html`<span class="mono">${r.item_id}</span> · ${r.name}`,
    subtitle: html`${r.site} · ${r.make_buy === 'BUY_CONSIGNED' ? 'bought by OEM, consigned to the CM' : r.make_buy === 'BUY_DIRECT' ? 'bought by OEM' : fmt.title(r.make_buy)} · supplier ${link.supplier(r.supplier_id, r.supplier_name)}`,
    actions: html`<a class="btn btn-ghost sm" href="#/replenishment">${icon('scale', 15)}<span>Policy</span></a>`,
    body: html`<dl class="kv mr-kv">
        <dt>On hand</dt><dd><strong>${q(r.on_hand)}</strong> ${r.uom}</dd>
        <dt>Safety stock</dt><dd>${q(r.safety_stock)}</dd>
        <dt>Lead time</dt><dd>${r.lead_time} days</dd>
        <dt>Lot sizing</dt><dd>${LOT_RULE[r.lot_rule] || fmt.title(r.lot_rule)} · MOQ ${q(r.moq)} · multiple ${q(r.mult)}</dd>
        <dt>Horizon totals</dt><dd>gross ${q(r.gross_total)} · receipts ${q(r.receipts_total)}${r.alt_total ? html` · alternate ${q(r.alt_total)}` : ''} · planned ${q(r.planned_total)}</dd>
        <dt>Used in</dt><dd>${r.parents.map((p, i) => html`${i ? ', ' : ''}<span class="mono">${p}</span>`)} · low-level code ${r.llc}</dd>
      </dl>
      ${notes.length ? html`<div class="mr-notes">${notes}</div>` : ''}`,
  });
}

// ---------------------------------------------------------------------------
// the time-phased grid
// ---------------------------------------------------------------------------
function buckets(d) {
  // Days, or Monday-based weeks (first bucket is the partial week containing as-of)
  const days = d.dates.map((ds, i) => ({ i, ds, dow: new Date(`${ds}T00:00:00Z`).getUTCDay() }));
  if (S.bucket === 'day') return days.map((x) => ({ from: x.i, to: x.i, ...x }));
  const out = [];
  for (const x of days) {
    if (!out.length || x.dow === 1) out.push({ from: x.i, to: x.i, ds: x.ds, dow: x.dow });
    else out[out.length - 1].to = x.i;
  }
  return out;
}

function drawGrid() {
  const { el, d } = S;
  const r = d.record;
  const host = el.querySelector('[data-grid]');
  const bs = buckets(d);
  const sum = (arr, b) => { let t = 0; for (let i = b.from; i <= b.to; i++) t += arr[i] || 0; return t; };
  const last = (arr, b) => arr[b.to];
  const minIn = (arr, b) => { let m = Infinity; for (let i = b.from; i <= b.to; i++) m = Math.min(m, arr[i]); return m; };
  const rcptAt = new Map();
  for (const x of r.receipt_detail) {
    const i = Math.max(0, x.index);
    if (!rcptAt.has(i)) rcptAt.set(i, []);
    rcptAt.get(i).push(x);
  }
  const pegDays = new Set(r.pegged_days);
  const pastDueRel = r.releases.filter((x) => x.past_due);
  const hasAlt = r.alternates.length || r.alt.some((v) => v);
  const ss = r.safety_stock || 0;
  const u = r.uom;

  // header groups (weeks) over day columns
  let weekRow = '';
  if (S.bucket === 'day') {
    const groups = [];
    for (const b of bs) {
      if (!groups.length || b.dow === 1) groups.push({ from: b.i, n: 1, ds: b.ds });
      else groups[groups.length - 1].n++;
    }
    weekRow = html`<tr class="mr-weeks"><th class="mr-sticky">Week</th>${pastDueRel.length ? html`<th></th>` : ''}${groups.map((g, gi) => html`<th colspan="${g.n}" class="mon">${gi === 0 && g.n < 7 ? `${fmt.date(g.ds)}–` : `Wk of ${fmt.date(g.ds)}`}</th>`)}</tr>`;
  }
  const head = html`<thead>${weekRow}<tr class="mr-days"><th class="mr-sticky">${S.bucket === 'day' ? 'Day' : 'Week of'}</th>${pastDueRel.length ? html`<th class="mr-pd">Past due</th>` : ''}${bs.map((b) => html`<th class="${cls(b)}">${S.bucket === 'day'
    ? html`<span class="mr-dow">${DOW[b.dow]}</span>${Number(b.ds.slice(8))}${Number(b.ds.slice(8)) === 1 || b.i === 0 ? html`<span class="mr-mon">${fmt.date(b.ds).split(' ')[0]}</span>` : ''}`
    : fmt.date(b.ds)}</th>`)}</tr></thead>`;

  const row = (label, sub, cells, rowCls = '') => html`<tr class="${rowCls}"><th class="mr-sticky" scope="row"><span class="mr-rl">${label}</span>${sub ? html`<span class="mr-rs">${sub}</span>` : ''}</th>${cells}</tr>`;
  const pdCell = (v) => (pastDueRel.length ? html`<td class="mr-pd">${v || ''}</td>` : '');

  const grossCells = bs.map((b) => {
    const v = sum(r.gross, b);
    const pegable = S.bucket === 'day' && v > 0 && pegDays.has(b.i);
    return pegable
      ? html`<td class="${cls(b)} peg" data-peg="${b.i}" tabindex="0" title="Peg ${qty(v, u)} on ${fmt.date(b.ds)}"><span>${qty(v, u)}</span></td>`
      : html`<td class="${cls(b)}">${qty(v, u)}</td>`;
  });
  const rcptCells = bs.map((b) => {
    const v = sum(r.receipts, b);
    if (!v) return html`<td class="${cls(b)}"></td>`;
    const list = [];
    for (let i = b.from; i <= b.to; i++) list.push(...(rcptAt.get(i) || []));
    const unconf = list.some((x) => !x.confirmed);
    const past = list.some((x) => x.past_due);
    const tip = list.map((x) => `${x.ref} · ${qty(x.qty, u)} · ${x.confirmed ? `promised ${x.promise_date}` : `unconfirmed (need ${x.need_date})`}${x.past_due ? ' · PAST DUE' : ''}`).join('\n');
    return html`<td class="${cls(b)} rcpt${unconf ? ' unconf' : ''}${past ? ' pastdue' : ''}" data-tip="${`Scheduled receipt\n${tip}`}"><span>${qty(v, u)}</span>${unconf ? html`<sup>?</sup>` : ''}</td>`;
  });
  const altCells = bs.map((b) => html`<td class="${cls(b)}">${qty(sum(r.alt, b), u)}</td>`);
  const projCells = (arr, kind) => bs.map((b) => {
    const v = last(arr, b);
    const m = S.bucket === 'week' ? minIn(arr, b) : v;
    const neg = m < 0;
    const below = !neg && m < ss;
    const tipTxt = S.bucket === 'week' && m !== v ? `\nLowest in the week: ${qty(m, u, true)}` : '';
    return html`<td class="${cls(b)} proj${neg ? ' neg' : below ? ' below' : ''}"${neg || below || tipTxt ? raw(` data-tip="${kind} ${fmt.date(b.ds)}: ${qty(v, u, true)}${neg ? ' (stock-out)' : below ? ` (below safety stock ${qty(ss, u, true)})` : ''}${tipTxt.replace(/"/g, '')}"`) : ''}>${neg ? html`<span class="mr-neg-ic">${icon('alert-octagon', 11)}</span>` : ''}${qty(v, u, true)}</td>`;
  });
  const plCells = bs.map((b) => html`<td class="${cls(b)}">${qty(sum(r.planned_receipts, b), u)}</td>`);
  const relCells = bs.map((b) => html`<td class="${cls(b)}">${qty(sum(r.planned_releases, b), u)}</td>`);

  host.innerHTML = html`<div class="mr-scroll" tabindex="0" aria-label="Time-phased MRP record"><table class="mr-grid${S.bucket === 'week' ? ' weeks' : ''}">
      ${head}
      <tbody>
        ${row('Gross requirements', S.bucket === 'day' ? 'click to peg' : 'from the MPS via the BOM', html`${pdCell('')}${grossCells}`, 'r-gross')}
        ${row('Scheduled receipts', 'supplier promise dates', html`${pdCell('')}${rcptCells}`)}
        ${hasAlt ? row('Alternate used', r.alternates.map((a) => `${a.item} under ${a.deviation}`).join(', ') || 'approved deviation', html`${pdCell('')}${altCells}`) : ''}
        ${row('Projected available', `as-is · SS ${qty(ss, u, true)}`, html`${pdCell('')}${projCells(r.projected, 'Projected')}`, 'r-proj')}
        ${row('Planned order receipts', 'lot-sized', html`${pdCell('')}${plCells}`)}
        ${row('Planned order releases', `offset ${r.lead_time}d`, html`${pdCell(pastDueRel.reduce((a, x) => a + x.qty, 0) ? qty(pastDueRel.reduce((a, x) => a + x.qty, 0), u) : '')}${relCells}`)}
        ${row('Projected (recommended)', 'after reschedules + orders', html`${pdCell('')}${projCells(r.recommended, 'Recommended')}`, 'r-rec')}
      </tbody>
    </table></div>
    <div class="mr-legend">
      <span>Blank = 0</span>
      <span class="mr-lg"><span class="mr-lg-sw neg"></span>${icon('alert-octagon', 11)} Stock-out</span>
      <span class="mr-lg"><span class="mr-lg-sw below"></span>Below safety stock</span>
      <span class="mr-lg"><span class="mr-lg-uc">123<sup>?</sup></span>Unconfirmed: planned on our need date</span>
      ${S.bucket === 'day' ? html`<span class="mr-lg"><span class="mr-lg-sw wkend"></span>Weekend (no build)</span>` : ''}
      ${S.bucket === 'week' ? html`<span>Weekly flows are sums; balances are end of week (a stock-out inside the week is still flagged)</span>` : ''}
    </div>`;
}

function cls(b) {
  const c = [];
  if (S.bucket === 'day') {
    if (b.dow === 0 || b.dow === 6) c.push('wkend');
    if (b.dow === 1) c.push('mon');
  }
  return c.join(' ');
}

function qty(v, uom, showZero = false) {
  if (v == null || v === '') return showZero ? '—' : '';
  const n = Number(v);
  if (!n && !showZero) return '';
  if (uom === 'kg' && !Number.isInteger(n)) return fmt.num(n, 1);
  return Math.abs(n) >= 10000 ? fmt.compact(n) : fmt.int(n);
}

function daysFrom(asOf, ds) {
  return Math.round((Date.parse(`${ds}T00:00:00Z`) - Date.parse(`${asOf}T00:00:00Z`)) / 86400000);
}

// ---------------------------------------------------------------------------
// projection chart, messages, receipts
// ---------------------------------------------------------------------------
function projectionChart(d, r) {
  const pts = (arr) => arr.map((y, i) => ({ x: d.dates[i], y }));
  const ss = r.safety_stock || 0;
  return charts.line({
    series: [
      { name: 'As-is (current supply)', points: pts(r.projected) },
      { name: 'Recommended plan', points: pts(r.recommended) },
    ],
    height: 230,
    refLines: ss ? [{ y: ss, label: `Safety stock ${qty(ss, r.uom, true)}`, tone: 'warning' }] : [],
    yFormat: (v) => fmt.compact(v),
    xLabel: 'Date',
    ariaLabel: `Projected available balance for ${r.item_id}`,
  });
}

function drawMessages(r) {
  const host = S.el.querySelector('[data-msgs]');
  if (!r.messages.length) {
    host.innerHTML = String(ui.empty('No action messages: supply matches the plan.', { icon: 'check-circle' }));
    return;
  }
  ui.dataTable(host, {
    columns: [
      { key: 'message', label: 'Action', render: (m) => ui.chip(m.tone, MSG_LABEL[m.message] || fmt.title(m.message)), value: (m) => m.message },
      { key: 'date', label: 'Date', format: (v) => fmt.date(v) },
      { key: 'qty', label: 'Qty', num: true, format: (v) => qty(v, r.uom, true) },
      { key: 'ref', label: 'PO line', render: (m) => (m.po_id ? link.po(m.po_id, m.line_no) : html`<span class="nil">—</span>`) },
      { key: 'detail', label: 'Detail', wrap: true, render: (m) => html`<span class="small">${m.detail || ''}</span>` },
    ],
    rows: r.messages,
    pageSize: 8,
    dense: true,
  });
}

function drawReceipts(r) {
  const host = S.el.querySelector('[data-rcpts]');
  if (!r.receipt_detail.length) {
    host.innerHTML = String(ui.empty('No open PO lines for this item.'));
    return;
  }
  ui.dataTable(host, {
    columns: [
      { key: 'ref', label: 'PO line', render: (x) => (x.po_id ? link.po(x.po_id, x.line_no) : html`<span class="small">${x.ref}</span>`), value: (x) => x.ref },
      { key: 'qty', label: 'Qty', num: true, format: (v) => qty(v, r.uom, true) },
      { key: 'need_date', label: 'Need', format: (v) => fmt.date(v) },
      { key: 'promise_date', label: 'Promise', render: (x) => (x.confirmed ? fmt.date(x.promise_date) : html`<span class="muted">none</span>`), value: (x) => x.promise_date },
      {
        key: 'slip', label: 'Slip', num: true,
        value: (x) => (x.confirmed && x.need_date ? daysFrom(x.need_date, x.promise_date) : null),
        render: (x) => {
          if (!x.confirmed) return ui.chip('warning', 'Unconfirmed');
          const s = x.need_date ? daysFrom(x.need_date, x.promise_date) : 0;
          return s > 0 ? ui.chip('serious', `+${s}d late`) : html`<span class="muted small">on time</span>`;
        },
      },
    ],
    rows: r.receipt_detail,
    pageSize: 8,
    dense: true,
  });
}

// ---------------------------------------------------------------------------
// pegging drawer
// ---------------------------------------------------------------------------
async function openPeg(day) {
  if (!S) return;
  const r = S.d.record;
  S.ctx.setQuery({ peg: day }, { silent: true });
  ui.drawer.open({
    title: `Pegging · ${r.item_id}`,
    subtitle: `Gross requirement on ${fmt.dateLong(S.d.dates[day] || S.d.as_of)}`,
    body: ui.loading('Tracing the requirement'),
    width: 720,
    onClose: () => S && S.ctx.setQuery({ peg: null }, { silent: true }),
  });
  let p;
  try {
    p = await api.get('/api/plan/mrp/peg', { item: r.item_id, day });
  } catch (err) {
    ui.drawer.open({ title: `Pegging · ${r.item_id}`, body: ui.errorBox(err), width: 720 });
    return;
  }
  const late = p.orders.filter((o) => o.slip_days > 0).length;
  const body = ui.drawer.open({
    title: `Pegging · ${p.item_id}`,
    subtitle: `${qty(p.gross, r.uom, true)} ${r.uom} needed ${fmt.dateLong(p.date)}`,
    width: 720,
    onClose: () => S && S.ctx.setQuery({ peg: null }, { silent: true }),
    body: html`<div class="pg-mrp mr-peg">
      <h4 class="section-title">1 · The builds that need it</h4>
      <div class="table-wrap"><table class="table dense">
        <thead><tr><th>Parent</th><th>Build date</th><th class="num">Build qty</th><th class="num">${p.item_id} qty</th></tr></thead>
        <tbody>${p.parents.length ? p.parents.map((x) => html`<tr>
          <td><span class="mono">${x.parent === 'KIT' ? 'Kits' : x.parent}</span> <span class="muted small">${x.parent_name}</span></td>
          <td>${fmt.date(x.parent_date)}</td>
          <td class="num">${x.build_qty != null ? fmt.int(x.build_qty) : '—'}</td>
          <td class="num"><strong>${qty(x.qty, r.uom, true)}</strong></td></tr>`) : html`<tr><td colspan="4" class="muted">No pegging recorded for this bucket.</td></tr>`}</tbody>
      </table></div>
      ${p.note ? html`<p class="small muted mr-pnote">${icon('info', 13)} ${p.note}</p>` : ''}
      <h4 class="section-title">2 · The customer promises that ride on them</h4>
      ${p.orders_total ? ui.callout({
        tone: late ? 'warning' : 'info',
        title: `${fmt.int(p.orders_total)} open order${p.orders_total === 1 ? '' : 's'} depend on ${p.parents.length > 1 ? 'these builds' : 'this build'}`,
        body: html`From ATP pegging: each order took its ${p.parents.some((x) => x.kind === 'PACK') ? 'pack' : p.parents.some((x) => x.kind === 'KIT') ? 'kit slot' : 'vehicle'} from this supply. ${late ? html`<strong>${late}</strong> already ship later than promised on today’s plan${bindingNote(p)}.` : 'All still meet their promise.'} If this requirement is not met, these are the promises that move.`,
      }) : html`<p class="muted small">No open customer orders are pegged to this supply (it covers safety stock or later demand).</p>`}
      <div data-peg-orders></div>
    </div>`,
  });
  if (p.orders.length) {
    ui.dataTable(body.querySelector('[data-peg-orders]'), {
      columns: [
        { key: 'order_id', label: 'Order', mono: true, render: (o) => link.order(o.order_id) },
        { key: 'kit', label: 'Kit', mono: true },
        { key: 'state', label: 'Ship to', render: (o) => `${o.state} · ${fmt.title(o.region)}` },
        { key: 'promised_date', label: 'Promised', format: (v) => fmt.date(v) },
        { key: 'atp_promise', label: 'ATP today', format: (v) => fmt.date(v) },
        { key: 'slip_days', label: 'Slip', num: true, render: (o) => (o.slip_days > 0 ? ui.chip('warning', `+${o.slip_days}d`) : html`<span class="muted small">on time</span>`) },
        {
          key: 'binding_source', label: 'Binding supply', wrap: true,
          render: (o) => (o.binding_sku ? html`<span class="small"><span class="mono">${o.binding_sku}</span> · ${o.binding_source}<span class="muted"> · kit-able ${fmt.date(o.binding_available)}</span></span>` : html`<span class="nil">—</span>`),
        },
      ],
      rows: p.orders,
      pageSize: 12,
      dense: true,
    });
  }
}

function bindingNote(p) {
  const bl = p.binding_late || {};
  const parts = [];
  if (bl.vehicle) parts.push(`${bl.vehicle} waiting on the vehicle`);
  if (bl.pack) parts.push(`${bl.pack} waiting on the pack`);
  return parts.length ? ` (${parts.join(', ')}: the later of the two parts sets the ship date)` : '';
}

// ---------------------------------------------------------------------------
// re-run and history
// ---------------------------------------------------------------------------
async function rerun(btn) {
  btn.disabled = true;
  const label = btn.querySelector('span');
  if (label) label.textContent = 'Running…';
  try {
    const res = await api.post('/api/plan/mrp/run', { triggered_by: 'manual (MRP page)' });
    const short = res.shortages || [];
    ui.toast(short.length
      ? `MRP run #${res.run.run_id}: ${short.map((s) => `${s.item_id} short from ${fmt.date(s.bucket_date)}`).join(', ')}`
      : `MRP run #${res.run.run_id}: no stock-outs inside the horizon`, short.length ? 'warning' : 'good');
    window.dispatchEvent(new Event('ops:meta-changed'));
    if (S) {
      const { el, ctx } = S;
      await render(el, ctx);
    }
  } catch (err) {
    ui.toast(err.message, 'critical');
    btn.disabled = false;
    if (label) label.textContent = 'Re-run MRP';
  }
}

function openRuns() {
  const body = ui.drawer.open({
    title: 'MRP run history',
    subtitle: 'Every stored run keeps its planned orders and action messages',
    width: 680,
    body: html`<div data-runs></div><p class="small muted mr-pnote">Runs are stored by the nightly job, by closed-loop decisions (after an expedite or a containment) and by this page. The grid above is always computed live.</p>`,
  });
  ui.dataTable(body.querySelector('[data-runs]'), {
    columns: [
      { key: 'run_id', label: 'Run', num: true, render: (x) => html`<span class="mono">#${x.run_id}</span>` },
      { key: 'ran_at', label: 'Ran', format: (v) => fmt.dt(v) },
      { key: 'triggered_by', label: 'Triggered by', wrap: true },
      { key: 'messages', label: 'Messages', num: true },
      { key: 'planned_orders', label: 'Planned orders', num: true },
      { key: 'short_items', label: 'Stock-outs', render: (x) => (x.short_items.length ? ui.chip('critical', x.short_items.join(', ')) : ui.chip('good', 'None')), value: (x) => x.short_items.length },
    ],
    rows: S.d.runs,
    initialSort: { key: 'run_id', dir: 'desc' },
    dense: true,
  });
}

// ---------------------------------------------------------------------------
// BOM explosion
// ---------------------------------------------------------------------------
function drawBomControls() {
  const host = S.el.querySelector('[data-bomctl]');
  const b = S.bom;
  const roots = (b.data && b.data.roots) || [{ item_id: b.item, name: b.item, kind: 'KIT' }];
  const ecos = ((b.data && b.data.all_ecos) || []).filter((e) => e.effective_date && e.status === 'IMPLEMENTED');
  host.innerHTML = html`
    <label class="sr-only" for="mr-bom-root">Parent item</label>
    <select class="select sm" id="mr-bom-root" data-bom-root>${groupRoots(roots).map(([kind, list]) => html`<optgroup label="${fmt.title(kind)}">${list.map((x) => html`<option value="${x.item_id}"${x.item_id === b.item ? raw(' selected') : ''}>${x.item_id} · ${x.name}</option>`)}</optgroup>`)}</select>
    <label class="sr-only" for="mr-bom-date">Effective on</label>
    <input class="input sm" type="date" id="mr-bom-date" data-bom-date value="${b.date}">
    <div class="row wrap mr-quick">
      <button type="button" class="btn btn-ghost sm" data-bom-on="${S.d.as_of}">Today</button>
      ${ecos.map((e) => html`<button type="button" class="btn btn-ghost sm" data-bom-on="${shift(e.effective_date, -1)}" title="${e.title}">Before ${e.eco_id}</button>`)}
    </div>`;
}

function groupRoots(roots) {
  const m = new Map();
  for (const r of roots) {
    if (!m.has(r.kind)) m.set(r.kind, []);
    m.get(r.kind).push(r);
  }
  return [...m.entries()];
}

function shift(ds, n) {
  const t = new Date(`${ds}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}

function syncBom() {
  S.ctx.setQuery({ bom: S.bom.item === 'LV1-SLATE-L' ? null : S.bom.item, on: S.bom.date === S.d.as_of ? null : S.bom.date }, { silent: true });
  loadBom();
}

async function loadBom() {
  if (!S) return;
  const host = S.el.querySelector('[data-bom]');
  host.style.opacity = S.bom.data ? '.55' : '';
  try {
    S.bom.data = await api.get('/api/plan/bom', { item: S.bom.item, date: S.bom.date });
  } catch (err) {
    host.innerHTML = String(ui.errorBox(err));
    host.style.opacity = '';
    return;
  }
  if (!S) return;
  host.style.opacity = '';
  drawBomControls();
  drawBom();
}

function drawBom() {
  const host = S.el.querySelector('[data-bom]');
  const b = S.bom.data;
  const planned = new Set(S.d.items.map((i) => i.item_id));
  const rows = [];
  const walk = (nodes, depth) => nodes.forEach((n) => { rows.push({ ...n, depth }); walk(n.children || [], depth + 1); });
  walk(b.tree, 0);
  const ecoTitle = Object.fromEntries((b.all_ecos || []).map((e) => [e.eco_id, e.title]));
  const eff = (n) => {
    if (!n.eco_id && !n.eff_to) return html`<span class="muted small">Current</span>`;
    return html`<span class="small">${n.eco_id ? html`from ${fmt.date(n.eff_from)}` : ''}${n.eff_to ? html`${n.eco_id ? ' ' : ''}until ${fmt.date(n.eff_to)}` : ''}</span>`;
  };
  const desc = (n) => n.name;
  const supplier = (n) => {
    if (n.make_buy === 'OEM_BUILT') return html`<span class="small">OEM pack line</span>`;
    if (!n.supplier_id) return html`<span class="nil">—</span>`;
    return html`<span class="small">${n.supplier_name || n.supplier_id}${n.tier ? html` <span class="muted">· T${n.tier}</span>` : ''}</span>`;
  };
  const ownerChip = (lvl) => ({ OEM: ui.chip('info', 'OEM', { icon: false }), CM: ui.chip('neutral', 'CM', { icon: false }), SUPPLIER: ui.chip('neutral', 'Supplier', { icon: false }) }[lvl]);
  host.innerHTML = html`<div class="table-wrap mr-bom"><table class="table dense">
      <thead><tr><th>Lvl</th><th>Position</th><th>Item</th><th>Description</th><th class="num">Qty per</th><th class="num">Extended</th><th>UoM</th><th>BOM of</th><th>Supplier</th><th>Effectivity</th><th>Change</th></tr></thead>
      <tbody>
        <tr class="mr-bom-root"><td class="num">0</td><td class="muted small">${fmt.title(b.root.kind)}</td><td class="mono"><strong>${b.root.item_id}</strong></td><td>${b.root.name}</td><td class="num">1</td><td class="num">1</td><td>${b.root.uom}</td><td></td><td></td><td class="small muted">as of ${fmt.date(b.date)}</td><td></td></tr>
        ${rows.map((n) => html`<tr class="${n.active ? '' : 'inactive'}">
          <td class="num muted">${n.level}</td>
          <td><span class="mr-indent" style="padding-left:${n.depth * 16}px">${n.depth ? html`<span class="mr-elbow">└</span>` : ''}<span class="small">${POS_LABEL[n.position] || fmt.title(n.position)}</span></span></td>
          <td class="mono">${planned.has(n.item_id) ? html`<a class="id-link" href="#" data-mrp-item="${n.item_id}">${n.item_id}</a>` : link.item(n.item_id)}</td>
          <td class="wrap">${desc(n)}</td>
          <td class="num">${trim(n.qty_per)}</td>
          <td class="num"><strong>${trim(n.ext_qty)}</strong></td>
          <td class="small">${n.uom}</td>
          <td>${ownerChip(n.bom_level)}</td>
          <td class="wrap">${supplier(n)}</td>
          <td class="nowrap">${eff(n)}</td>
          <td class="wrap small mr-change"${n.eco_id && ecoTitle[n.eco_id] ? raw(` title="${ecoTitle[n.eco_id].replace(/"/g, '')}"`) : ''}>${n.eco_id ? html`<span class="mono">${n.eco_id}</span> ` : ''}${n.alternates.map((a, i) => html`${i ? '; ' : ''}${a.state === 'superseded' ? 'replaced' : 'replaced by'} <span class="mono">${a.item_id}</span> ${a.state === 'superseded' ? `(until ${fmt.date(a.eff_to)})` : `(from ${fmt.date(a.eff_from)})`}`)}${!n.active ? html` ${ui.chip('neutral', 'Not yet effective', { icon: false })}` : ''}</td>
        </tr>`)}
      </tbody></table></div>
      <p class="mr-bomfoot small muted">${fmt.int(b.lines)} lines · ${b.ecos.length ? html`ECO cut-ins in this tree: ${b.ecos.map((e, i) => html`${i ? ', ' : ''}<span class="mono">${e.eco_id}</span> (${e.old_item_id || '—'} → ${e.new_item_id || '—'}, ${fmt.date(e.effective_date)})`)}.` : 'No ECO cut-ins in this tree.'} Pick a date before a cut-in to see the old part come back.</p>`;
}

function trim(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (Number.isInteger(n)) return fmt.int(n);
  return n >= 1 ? fmt.num(n, 2).replace(/\.?0+$/, '') : String(Number(n.toPrecision(3)));
}

// ---------------------------------------------------------------------------
// tier visibility
// ---------------------------------------------------------------------------
function visibility(d) {
  const v = d.visibility;
  const tierChip = (row) => (row.make_buy === 'CM_SOURCED'
    ? ui.chip('neutral', 'CM supplier', { icon: false })
    : ui.chip('neutral', `Tier ${row.tier ?? '?'}`, { icon: false }));
  return html`<div class="table-wrap mr-vis"><table class="table dense">
      <thead><tr><th>Item</th><th class="mr-desc">Description</th><th>Supplier</th><th>Tier</th><th>Needed for</th>${v.weeks.map((w) => html`<th class="num">${fmt.date(w)}</th>`)}<th class="num">Total</th></tr></thead>
      <tbody>${v.rows.map((row) => {
        const mx = Math.max(...row.weeks, 0);
        return html`<tr>
          <td class="mono">${link.item(row.item_id)}</td>
          <td class="wrap">${row.name}</td>
          <td class="small">${row.supplier_name || row.supplier_id}</td>
          <td>${tierChip(row)}</td>
          <td class="small muted">${forLabel(row.parents)}</td>
          ${row.weeks.map((q) => {
            const pct = mx > 0 && q > 0 ? Math.round(8 + 30 * (q / mx)) : 0;
            return html`<td class="num mr-heat"${pct ? raw(` style="background:color-mix(in srgb, var(--series-1) ${pct}%, var(--surface))"`) : ''}>${q ? qty(q, row.uom) : ''}</td>`;
          })}
          <td class="num"><strong>${qty(row.total, row.uom)}</strong> <span class="muted small">${row.uom}</span></td>
        </tr>`;
      })}</tbody></table></div>
    <p class="mr-bomfoot small muted">The stronger the blue, the larger the requirement within each row. Requirements offset beyond the ${d.days}-day horizon fall off this view: lithium carbonate (tier 3 behind the cathode) is released from the 26-week forecast in the ${link.route('suppliers', 'Supplier Loop')}.</p>`;
}

const TB_LABEL = {
  planned_receipts: 'Planned receipts', planned_releases: 'Planned releases', messages: 'Messages',
  alt: 'Alternate used', first_short: 'First short day', projected_min: 'Lowest balance',
};

function tbVal(k, v, expected) {
  if (v == null) return '—';
  if (k === 'messages') {
    const names = v.map((m) => MSG_LABEL[m] || fmt.title(m)).join(', ');
    return expected ? `includes ${names}` : names;
  }
  if (typeof v === 'object') {
    const e = Object.entries(v);
    return e.length ? e.map(([d, q]) => `d${d}: ${q}`).join(' · ') : 'none';
  }
  return k === 'first_short' ? `d${v}` : String(v);
}

function forLabel(parents) {
  const out = [];
  if (parents.some((p) => p.startsWith('LV1') || p === 'VEHICLE')) out.push('Vehicle builds at the CM');
  if (parents.some((p) => p.startsWith('PK-'))) out.push('Pack builds');
  out.push(...parents.filter((p) => !p.startsWith('LV1') && p !== 'VEHICLE' && !p.startsWith('PK-')));
  return out.join(', ');
}

// ---------------------------------------------------------------------------
// textbook cases
// ---------------------------------------------------------------------------
function textbook(d) {
  const all = d.textbook.every((t) => t.ok);
  const fmtMap = (m) => {
    const e = Object.entries(m || {});
    return e.length ? e.map(([k, v]) => `d${k}: ${v}`).join(', ') : 'none';
  };
  return html`<details class="card mr-tb">
    <summary class="mr-tb-sum">
      <span class="card-title">How we know the netting is right</span>
      ${ui.chip(all ? 'good' : 'critical', `${d.textbook.filter((t) => t.ok).length} / ${d.textbook.length} textbook cases pass`)}
      <span class="muted small">The same netting function runs against hand-computed cases on every change (eval EV-MRP-TEXTBOOK)</span>
      <span class="spacer"></span>${icon('chevron-down', 16)}
    </summary>
    <div class="mr-tb-body">
      <div class="mr-tb-grid">${d.textbook.map((t) => html`<div class="mr-tb-case">
        <div class="row"><span class="mono strong">${t.id}</span><span class="spacer"></span>${ui.chip(t.ok ? 'good' : 'critical', t.ok ? 'Pass' : 'Fail')}</div>
        <p class="small ink-2">${t.why}</p>
        <dl class="kv small">
          <dt>Inputs</dt><dd class="mono">OH ${t.args.on_hand} · SS ${t.args.safety_stock} · LT ${t.args.lead_time} · MOQ ${t.args.moq} · ×${t.args.mult}${t.args.rule ? ` · ${t.args.rule}` : ''}</dd>
          <dt>Gross</dt><dd class="mono">${fmtMap(t.args.gross)}</dd>
          <dt>Receipts</dt><dd class="mono">${(t.args.receipts || []).map((x) => `d${x.index}: ${x.qty}${x.confirmed === false ? ' (unconfirmed)' : ''}`).join(', ') || 'none'}</dd>
          ${t.args.alternates ? html`<dt>Alternate</dt><dd class="mono">${t.args.alternates.map((a) => `${a.on_hand} on hand, allowance ${a.allowance}, to d${a.valid_to}`).join('; ')}</dd>` : ''}
        </dl>
        <table class="table dense mr-tb-cmp"><thead><tr><th>Check</th><th>Expected</th><th>Actual</th></tr></thead>
          <tbody>${Object.keys(t.expect).map((k) => html`<tr>
            <td class="small">${TB_LABEL[k] || fmt.title(k)}</td>
            <td class="mono small wrap">${tbVal(k, t.expect[k], true)}</td>
            <td class="mono small wrap">${tbVal(k, t.actual[k], false)}</td>
          </tr>`)}</tbody></table>
      </div>`)}</div>
      <p class="small">${link.route('proof', 'See every test, eval and review gate →')}</p>
    </div>
  </details>`;
}

const PAGE_CSS = `
.pg-mrp .mr-context { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; margin: -4px 0 16px; font-size: 13px; color: var(--ink-2); }
.pg-mrp .mr-context > span { display: inline-flex; align-items: center; gap: 6px; }
.pg-mrp .mr-context svg { color: var(--sign); }
.pg-mrp .mr-main { margin-top: 16px; align-items: start; }
.pg-mrp .mr-right > * + * { margin-top: 16px; }
.pg-mrp .mr-picker { padding: 4px 0 8px; }
.pg-mrp .mr-group + .mr-group { border-top: 1px solid var(--hairline); }
.pg-mrp .mr-group-head { display: grid; gap: 2px; padding: 12px 16px 6px; font: 600 11.5px/1.2 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
.pg-mrp .mr-group-note { font: 400 11.5px/1.3 var(--font-ui); letter-spacing: 0; text-transform: none; }
.pg-mrp .mr-item { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 7px 16px; color: var(--ink); text-decoration: none; border-left: 3px solid transparent; }
.pg-mrp .mr-item:hover { background: var(--row-hover); }
.pg-mrp .mr-item.active { background: color-mix(in srgb, var(--sign) 8%, var(--surface)); border-left-color: var(--sign); }
.pg-mrp .mr-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--tone, var(--good)); box-shadow: 0 0 0 2px var(--surface); }
.pg-mrp .mr-dot.tone-neutral { background: var(--ink-3); }
.pg-mrp .mr-item-main { display: grid; min-width: 0; }
.pg-mrp .mr-item-id { font-size: 12.5px; font-weight: 500; }
.pg-mrp .mr-item-name { font-size: 12px; color: var(--ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.pg-mrp .mr-item-meta { display: grid; justify-items: end; gap: 2px; }
.pg-mrp .mr-llc { font: 500 10.5px var(--font-mono); color: var(--ink-3); }
.pg-mrp .mr-status { font: 600 11px/1 var(--font-cond); letter-spacing: .03em; color: var(--ink-2); }
.pg-mrp .mr-status.tone-good { color: var(--ink-3); font-weight: 500; }
.pg-mrp .mr-kv { grid-template-columns: minmax(110px, max-content) minmax(0, 1fr); }
.pg-mrp .mr-notes { display: grid; gap: 10px; margin-top: 14px; }
.pg-mrp .mr-scroll { overflow-x: auto; border-top: 1px solid var(--hairline); }
.pg-mrp .mr-scroll:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }
.pg-mrp .mr-grid { border-collapse: separate; border-spacing: 0; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.pg-mrp .mr-grid.weeks { width: 100%; }
.pg-mrp .mr-grid th, .pg-mrp .mr-grid td { height: 30px; padding: 0 7px; min-width: 42px; text-align: right; border-bottom: 1px solid var(--hairline); white-space: nowrap; }
.pg-mrp .mr-grid thead th { background: var(--surface-2); font: 600 11px/1.1 var(--font-cond); color: var(--ink-3); letter-spacing: .03em; }
.pg-mrp .mr-grid .mr-weeks th { height: 24px; text-align: left; text-transform: uppercase; letter-spacing: .06em; font-size: 10.5px; }
.pg-mrp .mr-grid .mr-days th { height: 34px; vertical-align: bottom; padding-bottom: 5px; }
.pg-mrp .mr-dow { display: block; font-weight: 500; font-size: 10px; color: var(--ink-3); }
.pg-mrp .mr-mon { display: block; font-size: 9.5px; color: var(--ink-2); text-transform: uppercase; }
.pg-mrp .mr-grid .mr-sticky { position: sticky; left: 0; z-index: 2; background: var(--surface); text-align: left; min-width: 196px; max-width: 196px; border-right: 1px solid var(--hairline-strong); }
.pg-mrp .mr-grid thead .mr-sticky { background: var(--surface-2); z-index: 3; }
.pg-mrp .mr-rl { display: block; font: 600 13px/1.15 var(--font-cond); color: var(--ink); }
.pg-mrp .mr-rs { display: block; font: 400 11px/1.2 var(--font-ui); color: var(--ink-3); white-space: normal; }
.pg-mrp .mr-grid tbody th { height: 38px; }
.pg-mrp .mr-grid .wkend { background: color-mix(in srgb, var(--surface-2) 85%, var(--canvas)); color: var(--ink-3); }
.pg-mrp .mr-grid .mon { border-left: 1px solid var(--hairline-strong); }
.pg-mrp .mr-grid .mr-pd { background: color-mix(in srgb, var(--serious) 10%, var(--surface)); border-right: 1px solid var(--hairline-strong); }
.pg-mrp .mr-grid tr.r-proj td, .pg-mrp .mr-grid tr.r-rec td { font-weight: 500; }
.pg-mrp .mr-grid tr.r-rec td { color: var(--ink-2); }
.pg-mrp .mr-grid td.neg { background: color-mix(in srgb, var(--critical) 14%, var(--surface)); color: var(--ink); font-weight: 700; }
.pg-mrp .mr-grid td.below { background: color-mix(in srgb, var(--warning) 18%, var(--surface)); }
.pg-mrp .mr-neg-ic { color: var(--critical); margin-right: 2px; vertical-align: -1px; }
.pg-mrp .mr-grid td.peg { cursor: pointer; }
.pg-mrp .mr-grid td.peg span { border-bottom: 1px solid var(--axis); }
.pg-mrp .mr-grid td.peg:hover, .pg-mrp .mr-grid td.peg:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; background: var(--row-hover); }
.pg-mrp .mr-grid td.rcpt { font-weight: 600; color: var(--ink); }
.pg-mrp .mr-grid td.unconf span { border-bottom: 1px dotted var(--ink-2); font-weight: 500; }
.pg-mrp .mr-grid td.pastdue { background: color-mix(in srgb, var(--serious) 12%, var(--surface)); }
.pg-mrp .mr-grid sup { font-size: 9px; color: var(--ink-2); margin-left: 1px; }
.pg-mrp .mr-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 10px 16px 12px; font-size: 12px; color: var(--ink-3); border-top: 1px solid var(--hairline); }
.pg-mrp .mr-lg { display: inline-flex; align-items: center; gap: 5px; }
.pg-mrp .mr-lg svg { color: var(--critical); }
.pg-mrp .mr-lg-sw { display: inline-block; width: 14px; height: 12px; border-radius: 3px; border: 1px solid var(--hairline); }
.pg-mrp .mr-lg-sw.neg { background: color-mix(in srgb, var(--critical) 14%, var(--surface)); }
.pg-mrp .mr-lg-sw.below { background: color-mix(in srgb, var(--warning) 18%, var(--surface)); }
.pg-mrp .mr-lg-sw.wkend { background: color-mix(in srgb, var(--surface-2) 85%, var(--canvas)); }
.pg-mrp .mr-lg-uc { border-bottom: 1px dotted var(--ink-2); color: var(--ink-2); font-variant-numeric: tabular-nums; }
.pg-mrp .mr-lg-uc sup { font-size: 9px; }
.pg-mrp .mr-bomctl { padding: 0 16px 12px; gap: 8px; }
.pg-mrp .mr-change { min-width: 200px; }
.pg-mrp .mr-bomctl .select { max-width: 280px; }
.pg-mrp .mr-bomctl .input { width: 150px; }
.pg-mrp .mr-quick { gap: 4px; }
.pg-mrp .mr-bom tr.inactive td { color: var(--ink-3); }
.pg-mrp .mr-bom tr.mr-bom-root td { background: var(--surface-2); }
.pg-mrp .mr-indent { display: inline-flex; align-items: center; gap: 6px; }
.pg-mrp .mr-elbow { color: var(--axis); font-family: var(--font-mono); }
.pg-mrp .mr-bomfoot { padding: 10px 16px 12px; border-top: 1px solid var(--hairline); }
.pg-mrp .mr-vis td.mr-heat { color: var(--ink); border-left: 2px solid var(--surface); }
.pg-mrp .mr-vis th.mr-desc { min-width: 170px; }
.pg-mrp .mr-tb { padding: 0; }
.pg-mrp .mr-tb-sum { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 14px 18px; cursor: pointer; list-style: none; }
.pg-mrp .mr-tb-sum::-webkit-details-marker { display: none; }
.pg-mrp .mr-tb[open] .mr-tb-sum svg { transform: rotate(180deg); }
.pg-mrp .mr-tb-body { padding: 0 18px 16px; border-top: 1px solid var(--hairline); }
.pg-mrp .mr-tb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; margin: 14px 0; }
.pg-mrp .mr-tb-case { padding: 12px 14px; border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface-2); display: grid; gap: 8px; align-content: start; }
.pg-mrp .mr-tb-case { min-width: 0; }
.pg-mrp .mr-tb-cmp { background: var(--surface); border: 1px solid var(--hairline); border-radius: 8px; table-layout: fixed; }
.pg-mrp .mr-tb-cmp td { white-space: normal; overflow-wrap: anywhere; padding-top: 5px; padding-bottom: 5px; height: auto; }
.pg-mrp .mr-tb-cmp th:first-child { width: 34%; }
.pg-mrp .mr-tb-cmp th { background: var(--surface); }
.pg-mrp .mr-tb-case dd.mono { font-size: 11.5px; overflow-wrap: anywhere; }
.pg-mrp.mr-peg .table-wrap { border: 1px solid var(--hairline); border-radius: 10px; }
.pg-mrp .mr-pnote { display: flex; align-items: center; gap: 6px; margin: 8px 0 0; }
@media (max-width: 1100px) {
  .pg-mrp .mr-picker { max-height: 340px; overflow: auto; }
}
`;
