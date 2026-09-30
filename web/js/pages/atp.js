// ATP & Queues: promise dates pegged to real supply across both supply chains
// (CM-built vehicles from Taiwan, OEM-built packs from Fremont) and the 3PL's
// kitting capacity. One allocation run answers everything on the page: what a new
// order can be promised, weekly ATP, which lot each promise pegs to, the orders
// a delay has put at risk, and the build and fulfillment queues.
import { html, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

let S = null;

const SKUS = [
  { id: 'LV1-DUNE', label: 'Dune' }, { id: 'LV1-SLATE', label: 'Slate' }, { id: 'LV1-FERN', label: 'Fern' },
  { id: 'LV1-EMBER', label: 'Ember' }, { id: 'PK-STD', label: 'Pack · Std' }, { id: 'PK-LRG', label: 'Pack · Large' },
];
const COLORS = [
  { sku: 'LV1-DUNE', name: 'Dune', swatch: '#C9A77C' }, { sku: 'LV1-SLATE', name: 'Slate', swatch: '#5E6B73' },
  { sku: 'LV1-FERN', name: 'Fern', swatch: '#5E7F52' }, { sku: 'LV1-EMBER', name: 'Ember', swatch: '#C2562E' },
];
const KIND = {
  STOCK: { label: '3PL stock', icon: 'boxes' }, CONTAINER: { label: 'On the water', icon: 'ship' },
  CM_FG: { label: 'At the CM', icon: 'factory' }, CM_BUILD: { label: 'CM build', icon: 'factory' },
  TRUCK: { label: 'DG truck', icon: 'truck' }, PLANT: { label: 'At Fremont', icon: 'factory' },
  PACK_MPS: { label: 'Pack MPS', icon: 'target' }, OTHER: { label: 'Other', icon: 'info' },
};
const BLOCKED = {
  READY: { label: 'Ships today', tone: 'good' }, VEHICLE: { label: 'Waiting on vehicle', tone: 'warning' },
  PACK: { label: 'Waiting on pack', tone: 'warning' }, BOTH: { label: 'Waiting on both', tone: 'warning' },
  CAPACITY: { label: 'Kitting capacity', tone: 'info' }, FLEET_WINDOW: { label: 'Fleet window not open', tone: 'neutral' },
  NO_SUPPLY: { label: 'No supply in horizon', tone: 'critical' },
};
const BINDING_TONE = { vehicle: 'warning', pack: 'warning', 'vehicle and pack': 'warning', 'kitting capacity': 'info' };
const TABS = ['atp', 'pipeline', 'risk', 'build', 'fulfill'];

// ---------------------------------------------------------------------------
export async function render(el, ctx) {
  injectStyle('page-atp', PAGE_CSS);
  const tab = ctx.query.get('tab');
  S = { el, ctx, tab: TABS.includes(tab) ? tab : 'atp', sku: ctx.query.get('sku') || 'LV1-SLATE', filter: 'ALL' };
  el.innerHTML = String(html`${ui.pageHeader({})}${ui.loading('Allocating every open order against supply')}`);
  S.d = await api.get('/api/atp/summary');
  draw();
  wire();
  const kit = ctx.query.get('kit');
  if (kit) runCheck({ kit });
  const order = ctx.query.get('order');
  if (order) openOrder(order);
}

export function unmount() {
  if (S && S.offDrawer) S.offDrawer();
  S = null;
}

function wire() {
  const el = S.el;
  on(el, 'submit', '#ctp-form', (e) => { e.preventDefault(); runCheck(); });
  on(el, 'click', '[data-kit]', (e, t) => {
    const f = $('#ctp-form', S.el);
    if (f) f.kit.value = t.dataset.kit;
    runCheck({ kit: t.dataset.kit });
    const card = $('#ctp-card', S.el);
    if (card && window.innerWidth < 1100) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  on(el, 'click', 'a[href^="#/atp?order="], [data-order]', (e, t) => {
    e.preventDefault();
    openOrder(t.dataset.order || decodeURIComponent(t.getAttribute('href').split('order=')[1]));
  });
  S.offDrawer = on(document.body, 'click', '.drawer a[href^="#/atp?order="]', (e, t) => {
    e.preventDefault();
    openOrder(decodeURIComponent(t.getAttribute('href').split('order=')[1]));
  });
}

// ---------------------------------------------------------------------------
function draw() {
  const d = S.d;
  const k = d.kpis;
  const top = d.risk.groups[0];
  const best = k.earliest_new;
  S.el.innerHTML = String(html`
    ${ui.pageHeader({
      actions: html`<a class="btn sm" href="#/mrp?item=BMS-B">${icon('grid', 15)}<span>Material plan</span></a>
        <a class="btn sm" href="#/loop">${icon('loop', 15)}<span>Closed loop</span></a>`,
    })}
    <div class="pg-atp">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Open orders', value: fmt.int(k.open_orders),
          hint: `+${fmt.int(k.allocated_today)} allocated and kitting today` })}
        ${ui.kpi({ label: 'Promises at risk', value: fmt.int(k.at_risk),
          status: k.at_risk ? { tone: 'serious', label: 'At risk' } : { tone: 'good', label: 'Holding' },
          hint: k.at_risk ? `avg +${fmt.num(k.at_risk_avg_slip, 1)} days · ${top ? top.cause.replace('Delayed sailing: ', '') : ''}`
            : (k.repromised ? `${fmt.int(k.repromised)} re-promised through the closed loop` : 'ATP still meets every promise') })}
        ${ui.kpi({ label: 'Average days to promise', value: fmt.num(k.avg_days_to_promise, 1), unit: 'days',
          hint: 'Open orders, from today, at current ATP' })}
        ${ui.kpi({ label: 'A new order today', value: best ? fmt.date(best.promise) : '—',
          hint: best ? `${best.name.replace('LV-1 · ', '')} · ${fmt.int(best.days_out)} days · West` : 'No supply in horizon' })}
        ${ui.kpi({ label: 'Uncommitted, next 12 weeks', value: fmt.int(k.atp_12w_vehicles), unit: 'vehicles',
          hint: `${fmt.int(k.atp_12w_packs)} packs · the rest is already promised` })}
      </div>

      <div class="grid">
        <div class="span-5">${ui.card({
          id: 'ctp-card', title: 'Promise check · capable-to-promise',
          subtitle: 'A new order joins the back of the queue. It needs a vehicle, a pack and a kitting slot in Reno.',
          body: html`${ctpForm()}<div id="ctp-result" class="ctp-result">${ui.empty('Pick a configuration and check: the answer pegs to a real container, CM build or pack-line day.', { icon: 'clock' })}</div>`,
        })}</div>
        <div class="span-7">${ui.card({
          title: 'New-order promise by configuration',
          subtitle: 'If ordered now for delivery in the West region. Click a cell to see the pegging.',
          body: kitMatrix(d.kit_promises),
        })}</div>
      </div>

      <div class="atp-tabs" id="atp-tabs"></div>
      <div class="atp-tab-body" id="atp-tab"></div>
    </div>`);

  ui.tabs($('#atp-tabs', S.el), {
    active: S.tab,
    tabs: [
      { id: 'atp', label: 'ATP by week', icon: 'grid' },
      { id: 'pipeline', label: 'Supply pipeline', icon: 'route' },
      { id: 'risk', label: 'Promises at risk', icon: 'alert-triangle', count: d.risk.orders.length },
      { id: 'build', label: 'Build queue', icon: 'factory' },
      { id: 'fulfill', label: 'Fulfillment queue', icon: 'boxes', count: d.fulfillment.total },
    ],
    onChange: (id) => { S.tab = id; S.ctx.setQuery({ tab: id }, { silent: true }); drawTab(); },
  });
  drawTab();
}

// --------------------------------------------------------------------------- CTP
function ctpForm() {
  const kits = S.d.kit_promises;
  return html`<form id="ctp-form" class="ctp-form" autocomplete="off">
    <label class="fld wide"><span>Configuration</span>
      <select class="select" name="kit">${kits.map((k) => html`<option value="${k.kit}"${k.kit === 'LV1-SLATE-L' ? ' selected' : ''}>${k.name.replace('LV-1 · ', 'LV-1 ')}</option>`)}</select>
    </label>
    <label class="fld"><span>Ship-to region</span>
      <select class="select" name="region">${S.d.regions.map((r) => html`<option value="${r}">${fmt.title(r)}</option>`)}</select>
    </label>
    <label class="fld"><span>Extra pack</span>
      <select class="select" name="extra"><option value="">None</option><option value="PK-STD">Standard</option><option value="PK-LRG">Large</option></select>
    </label>
    <label class="fld"><span>Quantity</span><input class="input" name="qty" type="number" min="1" max="25" value="1"></label>
    <div class="fld ctp-go">${ui.button({ label: 'Check promise', icon: 'clock', variant: 'primary', type: 'submit' })}</div>
  </form>`;
}

async function runCheck(over = {}) {
  const f = $('#ctp-form', S.el);
  if (!f) return;
  if (over.kit) f.kit.value = over.kit;
  const body = { kit: f.kit.value, region: f.region.value, extra_pack: f.extra.value || null, qty: Number(f.qty.value || 1) };
  const out = $('#ctp-result', S.el);
  out.innerHTML = String(ui.loading('Allocating behind every open order'));
  try {
    const r = await api.post('/api/atp/check', body);
    if (!S) return;
    out.innerHTML = String(ctpResult(r));
  } catch (err) {
    out.innerHTML = String(ui.errorBox(err));
  }
}

function ctpResult(r) {
  if (!r.promise) return ui.callout({ tone: 'critical', title: 'No supply inside the horizon', body: r.text });
  const u = r.units[r.units.length - 1];
  return html`<div class="ctp-answer">
      <div class="ctp-date">
        <span class="ctp-label">Promise to the customer</span>
        <span class="ctp-big">${fmt.dateLong(r.promise)}</span>
        <span class="small muted">${fmt.int(r.days_out)} days out · ships from Reno ${fmt.date(r.ship_date)}${r.qty > 1 ? ` · ${r.qty} units (last one)` : ''}</span>
      </div>
      <div class="ctp-bind">${ui.chip(BINDING_TONE[r.binding] || 'neutral', `Binding: ${r.binding}`)}</div>
    </div>
    <ul class="peg-list">${u.pegs.map((pg) => html`<li>
      <span class="peg-ic">${icon((KIND[pg.kind] || KIND.OTHER).icon, 16)}</span>
      <div><div class="small"><span class="mono">${pg.sku}</span> · <span class="muted">${pg.role}</span></div>
      <div class="peg-text">${pegText(pg)}</div></div>
    </li>`)}</ul>
    <p class="small ink-2 ctp-text">${r.summary}</p>
    ${r.displaced_orders ? ui.callout({ tone: 'warning', title: `Taking this order would push ${r.displaced_orders} existing orders later`, body: 'An order that cannot complete does not block the orders behind it, so a new order can take a part a blocked order was waiting for.' }) : ''}
    <p class="tiny muted">${r.method}.</p>`;
}

function pegText(pg) {
  if (pg.kind === 'CONTAINER') {
    const sid = pg.source.split(' · ')[0];
    return html`On container ${link.shipment(sid)} (${pg.source.split(' · ')[1] || ''}), kit-able in Reno ${fmt.date(pg.available)}`;
  }
  return pg.text.charAt(0).toUpperCase() + pg.text.slice(1);
}

function kitMatrix(kits) {
  const by = new Map(kits.map((k) => [k.kit, k]));
  const cell = (kit) => {
    const k = by.get(kit);
    if (!k) return html`<div class="km-cell empty">—</div>`;
    return html`<button type="button" class="km-cell" data-kit="${k.kit}" title="Check ${k.name}">
      <span class="km-top"><span class="km-date">${k.promise ? fmt.date(k.promise) : '—'}</span>${ui.chip(BINDING_TONE[k.binding] || 'neutral', k.binding)}</span>
      <span class="km-sub">ships ${fmt.date(k.ship_date)} · ${fmt.int(k.days_out)} days out</span>
    </button>`;
  };
  return html`<div class="kit-matrix" role="table" aria-label="New-order promise by configuration">
    <div class="km-head" role="row"><span></span><span>Standard pack</span><span>Large pack</span></div>
    ${COLORS.map((c) => html`<div class="km-row" role="row">
      <span class="km-color"><span class="swatch" style="background:${c.swatch}"></span>${c.name}</span>
      ${cell(`${c.sku}-S`)}${cell(`${c.sku}-L`)}
    </div>`)}
  </div>
  <p class="tiny muted km-note">${kitNote(kits)}</p>`;
}

function kitNote(kits) {
  const bind = (suffix) => [...new Set(kits.filter((k) => k.kit.endsWith(suffix)).map((k) => k.binding))].join(' or ');
  const s = bind('-S');
  const l = bind('-L');
  const bms = S.d.build_queue.bms_first_short;
  const why = bms && l.includes('pack') ? `, because MRP cuts the pack line while BMS-B is short from ${fmt.date(bms)}` : '';
  return `Standard-pack kits are bound by ${s || '—'}; large-pack kits by ${l || '—'}${why}.`;
}

// --------------------------------------------------------------------------- tabs
function drawTab() {
  const host = $('#atp-tab', S.el);
  if (!host) return;
  ({ atp: drawAtp, pipeline: drawPipeline, risk: drawRisk, build: drawBuild, fulfill: drawFulfill })[S.tab](host);
}

function skuPicker(host) {
  const holder = document.createElement('div');
  holder.className = 'atp-sku';
  host.prepend(holder);
  ui.segmented(holder, {
    options: SKUS.map((s) => ({ value: s.id, label: s.label })), value: S.sku, label: 'SKU',
    onChange: (v) => { S.sku = v; S.ctx.setQuery({ sku: v }, { silent: true }); drawTab(); },
  });
}

function drawAtp(host) {
  const rows = S.d.atp_table[S.sku] || [];
  let cs = 0;
  let cc = 0;
  const pts = rows.map((r) => { cs += r.supply; cc += r.committed; return { week: r.week, cs, cc }; });
  const first = rows.find((r) => r.atp_cumulative > 0);
  const label = SKUS.find((s) => s.id === S.sku).label;
  host.innerHTML = String(html`<div class="tab-pad">
    <div class="grid">
      <div class="span-7">${ui.card({
        title: `Cumulative supply vs committed · ${label}`,
        subtitle: 'Supply by the week it can be kitted in Reno. The gap between the lines is ATP: what a new order can still have.',
        tableToggle: true,
        body: charts.line({
          series: [
            { name: 'Supply', points: pts.map((p) => ({ x: p.week, y: p.cs })) },
            { name: 'Committed', points: pts.map((p) => ({ x: p.week, y: p.cc })) },
          ],
          height: 240, yFormat: (v) => fmt.compact(v), xLabel: 'Week of', ariaLabel: `Cumulative supply and committed demand for ${label}`,
        }),
      })}</div>
      <div class="span-5">${ui.card({ title: 'Weekly ATP', subtitle: 'Classic ATP: supply minus what open orders already peg to.', flush: true, body: html`<div id="atp-week-table"></div>` })}</div>
    </div>
    ${first ? ui.callout({ tone: 'info', title: `Everything ${label} can be kitted before the week of ${fmt.date(first.week)} is already promised`,
      body: html`Open orders peg to all of it. The first uncommitted ${label} lands that week (${fmt.int(first.atp_cumulative)} units), which is why a new order today is promised in late November. Check any configuration above.` })
      : ui.callout({ tone: 'warning', title: `No uncommitted ${label} inside 12 weeks`, body: 'Every unit of supply in the window is pegged to an open order.' })}
  </div>`);
  skuPicker(host);
  ui.dataTable($('#atp-week-table', host), {
    rows, pageSize: 0, dense: true,
    columns: [
      { key: 'week', label: 'Week of', render: (r) => fmt.date(r.week) },
      { key: 'supply', label: 'Supply', num: true },
      { key: 'committed', label: 'Committed', num: true },
      { key: 'free', label: 'Uncommitted', num: true, value: (r) => r.supply - r.committed },
      { key: 'atp_cumulative', label: 'Cum. ATP', num: true, render: (r) => html`<span class="${r.atp_cumulative > 0 ? 'strong' : 'muted'}">${fmt.int(r.atp_cumulative)}</span>` },
    ],
  });
}

function drawPipeline(host) {
  const lots = S.d.pipeline.filter((l) => l.sku === S.sku);
  const isPack = S.sku.startsWith('PK');
  const groups = isPack
    ? [['STOCK'], ['TRUCK', 'PLANT'], ['PACK_MPS']]
    : [['STOCK'], ['CONTAINER'], ['CM_FG'], ['CM_BUILD']];
  const names = isPack ? ['3PL stock', 'Trucks & Fremont', 'Pack MPS'] : ['3PL stock', 'On the water', 'At the CM', 'CM builds'];
  const weeks = Array.from({ length: 12 }, (_, i) => i);
  const asOf = S.d.as_of;
  const wkLabel = (i) => fmt.date(addDays(asOf, 7 * i));
  const series = groups.map((g, gi) => ({
    name: names[gi], values: weeks.map((w) => lots.filter((l) => l.week === w && g.includes(l.kind)).reduce((a, l) => a + l.qty, 0)),
  }));
  const label = SKUS.find((s) => s.id === S.sku).label;
  const flagged = lots.filter((l) => l.note);
  host.innerHTML = String(html`<div class="tab-pad">
    ${ui.card({
      title: `Where ${label} supply comes from, by week it can be kitted`,
      subtitle: isPack ? 'Stock in Reno, DG trucks and Fremont stock, then the pack line’s MPS after MRP cuts for component shortages.'
        : 'Stock in Reno, containers on the water (ETA plus port, customs and drayage), units at the CM, then the CM’s committed builds.',
      tableToggle: true,
      body: charts.bar({ categories: weeks.map(wkLabel), series, stacked: true, height: 230, yFormat: (v) => fmt.compact(v), categoryLabel: 'Week of', ariaLabel: `${label} supply by source and week` }),
    })}
    ${flagged.length ? html`<div class="pipe-flags">${flagged.slice(0, 4).map((l) => ui.callout({
      tone: l.note.startsWith('CBP') ? 'warning' : 'serious',
      title: html`${l.kind === 'CONTAINER' ? l.shipment_id : l.source}: ${l.note}`,
      body: html`${fmt.int(l.qty)} ${isPack ? 'packs' : 'vehicles'}, kit-able ${fmt.date(l.available)}; ${fmt.int(l.committed)} already pegged to open orders.`,
    }))}</div>` : ''}
    ${ui.card({ title: 'Supply lots', subtitle: 'Every lot, with how much of it open orders already peg to', flush: true, body: html`<div id="pipe-table"></div>` })}
  </div>`);
  skuPicker(host);
  ui.dataTable($('#pipe-table', host), {
    rows: lots, pageSize: 20, search: true, searchPlaceholder: 'Filter by source, container, note…',
    initialSort: { key: 'available', dir: 'asc' },
    columns: [
      { key: 'available', label: 'Kit-able in Reno', render: (r) => fmt.date(r.available) },
      { key: 'kind', label: 'Source', render: (r) => html`<span class="kind-tag">${icon((KIND[r.kind] || KIND.OTHER).icon, 14)}${(KIND[r.kind] || KIND.OTHER).label}</span>` },
      { key: 'source', label: 'Lot', render: (r) => (r.shipment_id ? html`${link.shipment(r.shipment_id)} <span class="small muted">${r.source.split(' · ')[1] || ''}</span>` : html`<span class="small">${r.source}</span>`) },
      { key: 'qty', label: 'Qty', num: true },
      { key: 'committed', label: 'Pegged', num: true },
      { key: 'uncommitted', label: 'Free', num: true, render: (r) => html`<span class="${r.uncommitted ? 'strong' : 'muted'}">${fmt.int(r.uncommitted)}</span>` },
      { key: 'note', label: 'Note', wrap: true, render: (r) => (r.note ? ui.chip(r.note.startsWith('CBP') ? 'warning' : 'serious', r.note) : html`<span class="nil">—</span>`) },
    ],
  });
}

function drawRisk(host) {
  const risk = S.d.risk;
  const dec = risk.decision;
  const rows = risk.orders;
  const slips = [...new Set(rows.map((r) => r.slip_days))].sort((a, b) => a - b);
  let head;
  if (dec && dec.status === 'PROPOSED' && rows.length) {
    head = ui.callout({ tone: 'serious', title: html`${dec.decision_id}: ${dec.title}`,
      body: html`<div>Proposed in the closed loop ${fmt.rel(dec.proposed_at, S.d.now)}. Executing it moves each promise to its new ATP date (order_promise, reason SUPPLY_DELAY), keeps the original on record for on-time reporting, and queues a customer notice per order.</div>
        <div class="risk-actions"><a class="btn sm btn-primary" href="#/loop">${icon('loop', 14)}<span>Review the decision</span></a></div>` });
  } else if (dec && dec.status === 'EXECUTED') {
    head = ui.callout({ tone: 'good', title: html`${dec.decision_id} executed ${fmt.rel(dec.executed_at, S.d.now)}`,
      body: html`${fmt.int(S.d.kpis.repromised)} orders carry a SUPPLY_DELAY promise. ${rows.length ? `${rows.length} more have slipped since.` : 'Every open promise holds against current ATP.'}` });
  } else if (!rows.length) {
    head = ui.callout({ tone: 'good', title: 'Every open promise holds against current ATP', body: 'ATP is re-run against carrier ETAs and the constrained pack plan on every page load.' });
  }
  host.innerHTML = String(html`<div class="tab-pad">
    ${head || ''}
    ${rows.length ? html`<div class="grid">
      <div class="span-5">${ui.card({ title: 'Why they slipped', subtitle: 'Grouped by the supply lot each order now pegs to', flush: true, body: html`<div id="risk-groups"></div>` })}</div>
      <div class="span-7">${ui.card({
        title: 'Orders by days of slip', subtitle: 'New ATP date minus the date the customer was promised', tableToggle: true,
        body: charts.bar({ categories: slips.map((s) => `+${s}d`), series: [{ name: 'Orders', values: slips.map((s) => rows.filter((r) => r.slip_days === s).length) }], height: 200, yFormat: (v) => fmt.int(v), categoryLabel: 'Slip', ariaLabel: 'Orders at risk by days of slip' }),
      })}</div>
    </div>
    ${ui.card({ title: `${fmt.int(rows.length)} orders whose promise ATP can no longer keep`, subtitle: 'Open an order for its pegging and promise history', flush: true, body: html`<div id="risk-table"></div>` })}` : ''}
  </div>`);
  if (!rows.length) return;
  ui.dataTable($('#risk-groups', host), {
    rows: risk.groups, pageSize: 0, dense: true,
    columns: [
      { key: 'cause', label: 'Cause', wrap: true },
      { key: 'orders', label: 'Orders', num: true },
      { key: 'avg_slip', label: 'Avg slip', num: true, render: (r) => `+${fmt.num(r.avg_slip, 1)}d` },
      { key: 'max_slip', label: 'Max', num: true, render: (r) => `+${r.max_slip}d` },
    ],
  });
  ui.dataTable($('#risk-table', host), {
    rows, pageSize: 20, search: true, searchPlaceholder: 'Filter by order, state, source…',
    initialSort: { key: 'slip_days', dir: 'desc' },
    onRowClick: (r) => openOrder(r.order_id),
    columns: [
      { key: 'order_id', label: 'Order', render: (r) => link.order(r.order_id) },
      { key: 'channel', label: 'Channel', render: (r) => html`<span class="small">${r.channel}</span>` },
      { key: 'state', label: 'Ship to', render: (r) => html`<span class="small">${r.state} · ${fmt.title(r.region)}</span>` },
      { key: 'promised', label: 'Promised', render: (r) => fmt.date(r.promised) },
      { key: 'new_promise', label: 'ATP now', render: (r) => html`<span class="strong">${fmt.date(r.new_promise)}</span>` },
      { key: 'slip_days', label: 'Slip', num: true, render: (r) => html`<span class="slip">+${r.slip_days}d</span>` },
      { key: 'pegged_to', label: 'Pegged to', wrap: true, render: (r) => html`<span class="small">${r.pegged_to}</span>` },
    ],
  });
}

function drawBuild(host) {
  const b = S.d.build_queue;
  const lost = b.pack.reduce((a, r) => a + r.lost, 0);
  const days = new Set(b.cm.map((r) => r.date)).size;
  host.innerHTML = String(html`<div class="tab-pad">
    ${ui.card({
      title: 'CM build queue · Taichung', subtitle: `Next ${days} workdays of the CM's commit by line. Work orders are released about five days out.`,
      flush: true, body: html`<div id="cm-queue"></div>${b.fence ? html`<p class="tiny muted fence-note">${icon('lock', 13)}<span>Time fence: the CM commit is frozen ${b.fence.frozen_days} days out (material is staged at the CM), slushy to ${b.fence.slushy_days}. Changes inside the frozen fence are a conversation with the CM, not a plan edit.</span></p>` : ''}`,
    })}
    <div class="grid">
      <div class="span-7">${ui.card({
        title: 'Pack line MPS · Fremont', subtitle: 'Packs planned per build day vs what MRP says the components support',
        tableToggle: true,
        body: charts.bar({
          categories: b.pack.map((r) => fmt.date(r.date)),
          series: [
            { name: 'Planned', values: b.pack.map((r) => r.std_planned + r.lrg_planned) },
            { name: 'After MRP', values: b.pack.map((r) => r.std_constrained + r.lrg_constrained) },
          ],
          height: 230, yFormat: (v) => fmt.int(v), categoryLabel: 'Build day', ariaLabel: 'Pack line planned vs constrained',
        }),
      })}</div>
      <div class="span-5">${lost ? ui.callout({ tone: 'critical', title: `BMS-B runs short from ${fmt.date(b.bms_first_short)}: MRP cuts ${fmt.int(lost)} packs over the next ${b.pack.length} build days`,
        body: html`<p>Those packs never reach Reno, so large-pack kits slide and the fulfillment queue fills with orders waiting on a pack.</p>
          <p>The supply decision (air-expedite part of the slipped Pinecrest line and extend DEV-0012) is waiting in the ${html`<a href="#/loop">closed loop</a>`}. The netting behind it is on the ${html`<a href="#/mrp?item=BMS-B">material plan</a>`}.</p>` })
        : ui.callout({ tone: 'good', title: 'Components support the full pack schedule', body: 'MRP finds no shortage inside the next ten build days.' })}</div>
    </div>
  </div>`);
  ui.dataTable($('#cm-queue', host), {
    rows: b.cm, pageSize: 0, dense: true,
    columns: [
      { key: 'date', label: 'Build day', render: (r) => fmt.date(r.date) },
      { key: 'line', label: 'Line' },
      ...COLORS.map((c) => ({ key: c.sku, label: c.name, num: true, value: (r) => r.split[c.sku] || 0 })),
      { key: 'qty', label: 'Total', num: true, render: (r) => html`<span class="strong">${fmt.int(r.qty)}</span>` },
      { key: 'wo_released', label: 'WOs', num: true, render: (r) => (r.wo_released ? fmt.int(r.wo_released) : html`<span class="nil">—</span>`) },
      { key: 'fence', label: 'Fence', render: (r) => ui.chip(r.fence === 'FROZEN' ? 'info' : 'neutral', fmt.title(r.fence), { icon: r.fence === 'FROZEN' ? 'lock' : false }) },
      { key: 'lands_in_reno', label: 'In Reno', render: (r) => fmt.date(r.lands_in_reno) },
    ],
  });
}

function drawFulfill(host) {
  const f = S.d.fulfillment;
  const order = ['READY', 'VEHICLE', 'PACK', 'BOTH', 'CAPACITY', 'FLEET_WINDOW', 'NO_SUPPLY'].filter((k) => f.counts[k]);
  host.innerHTML = String(html`<div class="tab-pad">
    <div class="grid">
      <div class="span-5">${ui.card({
        title: 'Why open orders are waiting', subtitle: `All ${fmt.int(f.total)} open orders, by what their ATP date waits on`,
        tableToggle: true,
        body: charts.bar({ categories: order.map((k) => BLOCKED[k].label), series: [{ name: 'Orders', values: order.map((k) => f.counts[k]) }], horizontal: true, yFormat: (v) => fmt.int(v), categoryLabel: 'Waiting on', ariaLabel: 'Open orders by what they wait on' }),
      })}</div>
      <div class="span-7">${ui.card({
        title: 'How the queue is ordered',
        body: html`<ol class="rules">
          <li><strong>Fleet orders</strong> go first once they are inside 14 days of their requested date.</li>
          <li><strong>Everyone else</strong> by reservation time, then order time. Reservations from before launch keep their place.</li>
          <li>An order that cannot complete (a vehicle but no pack) <strong>does not block</strong> the orders behind it.</li>
          <li>The 3PL kits ${fmt.int(S.d.kitting_capacity.weekday)} a weekday and ${fmt.int(S.d.kitting_capacity.saturday)} on Saturday; ${fmt.int(f.allocated_today)} orders were allocated this morning and ship at 2pm.</li>
        </ol>`,
      })}</div>
    </div>
    <div class="fq-filter" id="fq-filter"></div>
    ${ui.card({ title: 'Fulfillment queue, in allocation order', subtitle: `All ${fmt.int(f.total)} open orders; # is the order the 3PL will allocate them in`, flush: true, body: html`<div id="fq-table"></div>` })}
  </div>`);
  const table = ui.dataTable($('#fq-table', host), {
    rows: filtered(f.rows), pageSize: 25, search: true, searchPlaceholder: 'Filter by order, kit, state…',
    onRowClick: (r) => openOrder(r.order_id),
    columns: [
      { key: 'rank', label: '#', num: true },
      { key: 'order_id', label: 'Order', render: (r) => link.order(r.order_id) },
      { key: 'channel', label: 'Channel', render: (r) => (r.channel === 'FLEET' ? ui.chip('info', 'Fleet', { icon: false }) : html`<span class="small">D2C</span>`) },
      { key: 'kit', label: 'Kit', render: (r) => html`<span class="mono small">${r.kit}</span>${r.extra_pack ? html` <span class="small muted">+ ${r.extra_pack}</span>` : ''}` },
      { key: 'basis', label: 'Priority basis', render: (r) => html`<span class="small"><span class="muted">${r.basis_kind}</span> ${fmt.date(r.basis)}</span>` },
      { key: 'promised', label: 'Promised', render: (r) => fmt.date(r.promised) },
      { key: 'ship_date', label: 'ATP ship', render: (r) => fmt.date(r.ship_date) },
      { key: 'atp_promise', label: 'ATP delivery', render: (r) => html`<span class="${r.atp_promise > r.promised ? 'slip' : ''}">${fmt.date(r.atp_promise)}</span>` },
      { key: 'blocked', label: 'Waiting on', render: (r) => ui.chip(BLOCKED[r.blocked].tone, waitLabel(r)) },
    ],
  });
  ui.segmented($('#fq-filter', host), {
    options: [{ value: 'ALL', label: 'All' }, ...order.map((k) => ({ value: k, label: `${BLOCKED[k].label} · ${fmt.int(f.counts[k])}` }))],
    value: S.filter, label: 'Filter queue',
    onChange: (v) => { S.filter = v; table.update(filtered(f.rows)); },
  });
}

function waitLabel(r) {
  const [, color, size] = r.kit.split('-');
  if (r.blocked === 'VEHICLE') return `Vehicle · ${fmt.title(color)}`;
  if (r.blocked === 'PACK') return `Pack · ${size === 'L' ? 'Large' : 'Standard'}${r.extra_pack ? ' + extra' : ''}`;
  if (r.blocked === 'FLEET_WINDOW' && r.window_opens) return `Window opens ${fmt.date(r.window_opens)}`;
  return BLOCKED[r.blocked].label;
}

function filtered(rows) {
  return S.filter === 'ALL' ? rows : rows.filter((r) => r.blocked === S.filter);
}

// --------------------------------------------------------------------------- order drawer
async function openOrder(id) {
  if (!S) return;
  S.ctx.setQuery({ order: id }, { silent: true });
  const body = ui.drawer.open({ title: id, subtitle: 'Loading…', body: ui.loading(), width: 620,
    onClose: () => { if (S) S.ctx.setQuery({ order: null }, { silent: true }); } });
  try {
    const d = await api.get(`/api/atp/order/${encodeURIComponent(id)}`);
    if (!S || !ui.drawer.isOpen) return;
    const o = d.order;
    const sub = document.querySelector('.drawer-sub');
    if (sub) {
      sub.innerHTML = String(html`${ui.statusChip(o.status)} <span class="small muted">${o.channel} · ${o.display_name} · ${o.city || ''}, ${o.state}</span>`);
      sub.hidden = false;
    }
    body.innerHTML = String(orderBody(d));
  } catch (err) {
    body.innerHTML = String(ui.errorBox(err));
  }
}

function orderBody(d) {
  const o = d.order;
  const a = d.allocation;
  const s = d.shipment;
  const moved = o.first_promised_date && o.promised_date !== o.first_promised_date;
  return html`<div class="pg-atp-drawer">
    <dl class="kv">
      <dt>Ordered</dt><dd>${fmt.dt(o.ordered_at)}${o.reserved_at ? html` <span class="small muted">· reserved ${fmt.dateLong(o.reserved_at)}</span>` : ''}</dd>
      ${o.requested_date ? html`<dt>Requested</dt><dd>${fmt.dateLong(o.requested_date)}</dd>` : ''}
      <dt>Ship to</dt><dd>${o.ship_to_state} · ${fmt.title(o.ship_to_region)}</dd>
      <dt>Promised</dt><dd><span class="strong">${fmt.dateLong(o.promised_date)}</span>${moved ? html` <span class="small muted">first promised ${fmt.dateLong(o.first_promised_date)}</span>` : ''}</dd>
      ${o.delivered_at ? html`<dt>Delivered</dt><dd>${fmt.dt(o.delivered_at)} ${ui.chip(o.delivered_at.slice(0, 10) <= o.first_promised_date ? 'good' : 'warning', o.delivered_at.slice(0, 10) <= o.first_promised_date ? 'On time' : 'Late')}</dd>` : ''}
      <dt>Total</dt><dd>${fmt.usd(o.total_usd)}</dd>
    </dl>
    <h4 class="section-title">Lines</h4>
    <div class="table-wrap"><table class="table dense"><thead><tr><th>#</th><th>Item</th><th>Vehicle</th><th>Pack</th><th class="num">Price</th></tr></thead>
      <tbody>${d.lines.map((l) => html`<tr><td>${l.line_no}</td><td><span class="mono small">${l.item_id}</span><div class="tiny muted">${l.name}</div></td>
        <td>${l.vehicle_serial ? link.serial(l.vehicle_serial) : html`<span class="nil">—</span>`}</td>
        <td>${l.pack_serial ? link.serial(l.pack_serial) : html`<span class="nil">—</span>`}</td>
        <td class="num">${fmt.usd(l.unit_price_usd)}</td></tr>`)}</tbody></table></div>
    ${a ? html`<h4 class="section-title">ATP today</h4>
      ${ui.callout({ tone: a.at_risk ? 'serious' : 'info',
        title: html`${a.promise ? `Delivers ${fmt.dateLong(a.promise)}` : 'No supply in horizon'}${a.at_risk ? ' · later than promised' : ''} · #${fmt.int(a.queue_position)} of ${fmt.int(a.queue_total)} in the queue`,
        body: html`<ul class="peg-list">${a.pegs.map((pg) => html`<li><span class="peg-ic">${icon((KIND[pg.kind] || KIND.OTHER).icon, 16)}</span>
          <div><div class="small"><span class="mono">${pg.sku}</span> · <span class="muted">${pg.role}</span></div><div class="peg-text">${pegText(pg)}</div></div></li>`)}</ul>
          <div class="small">Binding: <strong>${a.binding}</strong>${a.fleet_window_opens ? html` · fleet window opens ${fmt.date(a.fleet_window_opens)}` : ''}</div>` })}` : ''}
    <h4 class="section-title">Promise history</h4>
    ${d.history.length ? ui.timeline(d.history.map((h) => ({
      ts: h.decided_at, tone: h.reason === 'INITIAL' ? 'info' : 'serious',
      title: html`${fmt.dateLong(h.promised_date)} · ${fmt.title(h.reason)}`,
      detail: html`${h.pegged_to ? html`<span class="small">Pegged to ${h.pegged_to}</span>` : ''}${h.decision_id ? html`<div class="small"><a href="#/loop">${h.decision_id}</a> · ${h.decision_title || ''}</div>` : ''}`,
    }))) : ui.empty('No promise recorded.')}
    ${s ? html`<h4 class="section-title">Last mile</h4>
      <dl class="kv"><dt>Shipment</dt><dd>${link.shipment(s.shipment_id)} · ${s.carrier} · <span class="mono small">${s.tracking_no || ''}</span></dd>
        <dt>Status</dt><dd>${ui.statusChip(s.status)}</dd></dl>
      ${ui.timeline(s.events.map((e) => ({ ts: e.event_ts, tone: e.code === 'DELIVERED' ? 'good' : e.code === 'EXCEPTION' ? 'serious' : 'info', title: fmt.title(e.code), detail: e.location || '' })))}` : ''}
    ${d.claims.length ? html`<h4 class="section-title">Warranty</h4>${d.claims.map((c) => html`<div class="small">${link.claim(c.claim_id)} · ${fmt.date(c.reported_at)} · ${c.symptom}</div>`)}` : ''}
  </div>`;
}

// ---------------------------------------------------------------------------
function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const PAGE_CSS = `
.pg-atp { display: flex; flex-direction: column; gap: 16px; }
.pg-atp .ctp-form { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 12px; align-items: end; margin-bottom: 14px; }
.pg-atp .fld { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.pg-atp .fld > span { font-size: 12px; color: var(--ink-3); }
.pg-atp .fld.wide { grid-column: 1 / -1; }
.pg-atp .fld .select, .pg-atp .fld .input { width: 100%; }
.pg-atp .ctp-go { grid-column: 1 / -1; flex-direction: row; }
.pg-atp .ctp-result { border-top: 1px solid var(--hairline); padding-top: 14px; }
.pg-atp .ctp-answer { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.pg-atp .ctp-date { display: flex; flex-direction: column; gap: 3px; }
.pg-atp .ctp-label { font-size: 12.5px; color: var(--ink-2); }
.pg-atp .ctp-big { font: 600 30px/1.1 var(--font-ui); letter-spacing: -.01em; }
.pg-atp .ctp-text { margin: 10px 0 6px; line-height: 1.5; }
.pg-atp .peg-list, .pg-atp-drawer .peg-list { list-style: none; margin: 0 0 8px; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.pg-atp .peg-list li, .pg-atp-drawer .peg-list li { display: flex; gap: 10px; align-items: flex-start; }
.pg-atp .peg-ic, .pg-atp-drawer .peg-ic { flex: none; display: inline-flex; width: 28px; height: 28px; align-items: center; justify-content: center; border-radius: 8px; background: var(--surface-2); color: var(--ink-2); border: 1px solid var(--hairline); }
.pg-atp .peg-text, .pg-atp-drawer .peg-text { font-size: 13px; }
.pg-atp .kit-matrix { display: grid; gap: 8px; }
.pg-atp .km-head, .pg-atp .km-row { display: grid; grid-template-columns: minmax(84px, 110px) repeat(2, minmax(0, 1fr)); gap: 8px; align-items: stretch; }
.pg-atp .km-head span { font: 600 11.5px/1 var(--font-cond); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); padding: 0 2px 2px; }
.pg-atp .km-color { display: flex; align-items: center; gap: 8px; font: 600 15px/1.2 var(--font-cond); }
.pg-atp .swatch { width: 14px; height: 14px; border-radius: 4px; box-shadow: inset 0 0 0 1px rgba(0,0,0,.15); flex: none; }
.pg-atp .km-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; min-width: 0; padding: 10px 12px; text-align: left; background: var(--surface-2); border: 1px solid var(--hairline); border-radius: 10px; color: var(--ink); cursor: pointer; font: inherit; transition: border-color .12s, background .12s; }
.pg-atp .km-cell:hover { border-color: var(--hairline-strong); background: var(--row-hover); }
.pg-atp .km-cell:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.pg-atp .km-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; flex-wrap: wrap; }
.pg-atp .km-date { font: 600 20px/1.1 var(--font-ui); }
.pg-atp .km-sub { font-size: 12px; color: var(--ink-3); }
.pg-atp .km-note { margin: 10px 0 0; }
.pg-atp .atp-tabs { margin-top: 6px; overflow-x: auto; }
.pg-atp .atp-tab-body { margin-top: -2px; }
.pg-atp .tab-pad { display: flex; flex-direction: column; gap: 16px; }
.pg-atp .atp-sku { margin-bottom: 14px; overflow-x: auto; }
.pg-atp .pipe-flags { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
.pg-atp .kind-tag { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2); white-space: nowrap; }
.pg-atp .slip { color: var(--delta-bad); font-weight: 600; }
.pg-atp .risk-actions { margin-top: 10px; }
.pg-atp .rules { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--ink-2); }
.pg-atp .fq-filter { overflow-x: auto; }
.pg-atp .fence-note { display: flex; gap: 6px; align-items: flex-start; margin: 0; padding: 10px 18px 14px; border-top: 1px solid var(--hairline); }
.pg-atp .callout p { margin: 0 0 6px; }
.pg-atp .callout p:last-child { margin-bottom: 0; }
@media (max-width: 700px) {
  .pg-atp .ctp-form { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .pg-atp .km-head, .pg-atp .km-row { grid-template-columns: 64px repeat(2, minmax(0, 1fr)); }
  .pg-atp .km-cell { padding: 8px; }
  .pg-atp .km-date { font-size: 16px; }
}
`;
