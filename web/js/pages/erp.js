// ERP: the slice of the OEM's ERP the operating loop touches. Item master, a multi-level
// BOM that honors effectivity (and costs itself as of any date), purchase orders,
// goods receipts, accounts payable with the three-way match, and the general ledger
// that supplier chargebacks post into.
import { html, raw, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const TABS = [
  { id: 'items', label: 'Item master', icon: 'boxes' },
  { id: 'bom', label: 'BOM & cost', icon: 'tree' },
  { id: 'pos', label: 'Purchase orders', icon: 'exchange' },
  { id: 'receipts', label: 'Goods receipts', icon: 'download' },
  { id: 'ap', label: 'AP · three-way match', icon: 'receipt' },
  { id: 'gl', label: 'General ledger', icon: 'ledger' },
];
const KINDS = ['KIT', 'VEHICLE', 'PACK', 'MODULE', 'COMPONENT', 'MATERIAL'];
const LEVEL_TONE = { OEM: 'info', CM: 'neutral', SUPPLIER: 'neutral' };
const MAKE_BUY = {
  KITTED: 'Kitted at 3PL', CM_BUILT: 'CM builds', OEM_BUILT: 'OEM builds', BUY_DIRECT: 'OEM buys',
  BUY_CONSIGNED: 'OEM buys, consigned to CM', CM_SOURCED: 'CM sources', SUPPLIER_SOURCED: 'Supplier sources',
};

const MAKE_BUY_SHORT = {
  KITTED: 'Kitted', CM_BUILT: 'CM builds', OEM_BUILT: 'OEM builds', BUY_DIRECT: 'OEM buys',
  BUY_CONSIGNED: 'Consigned', CM_SOURCED: 'CM sources', SUPPLIER_SOURCED: 'Sub-tier',
};

let S = null;

const addDays = (iso, n) => new Date(Date.parse(iso + 'T00:00:00Z') + n * 86400000).toISOString().slice(0, 10);
const money2 = (v) => (v == null ? '—' : `$${fmt.num(v, 2)}`);

export async function render(el, ctx) {
  injectStyle('page-erp', PAGE_CSS);
  const [items, ap, gl] = await Promise.all([api.get('/api/erp/items'), api.get('/api/erp/ap'), api.get('/api/erp/gl')]);
  S = { el, ctx, cache: { items, ap, gl }, asOf: items.as_of };
  let tab = ctx.query.get('tab');
  if (!TABS.some((t) => t.id === tab)) tab = 'items';
  const openAP = ap.by_status.OPEN + ap.by_status.SCHEDULED + ap.by_status.BLOCKED;
  const blocked = ap.rows.filter((r) => r.pay_status === 'BLOCKED');
  const recovered = gl.entries.reduce((a, e) => a + (e.chargeback_id ? e.debits : 0), 0);
  const pending = gl.pending_recoveries.reduce((a, p) => a + p.amount_usd, 0);

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-erp">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Items', value: fmt.int(items.rows.length), hint: KINDS.filter((k) => items.kinds[k]).map((k) => `${items.kinds[k]} ${fmt.title(k).toLowerCase()}`).join(' · ') })}
        ${ui.kpi({ label: 'Accounts payable open', value: fmt.usd(openAP, { compact: true }), hint: `${fmt.usd(ap.by_status.SCHEDULED, { compact: true })} scheduled in the next 14 days` })}
        ${ui.kpi({ label: 'Invoices blocked', value: fmt.int(blocked.length), hint: blocked.length ? `${fmt.usd(blocked.reduce((a, r) => a + r.amount_usd, 0))} held by the three-way match` : 'none', status: blocked.length ? { tone: 'serious', label: 'Blocked' } : null })}
        ${ui.kpi({ label: 'Supplier recoveries posted', value: fmt.usd(recovered), hint: `${fmt.usd(pending)} more in flight (sent or disputed)` })}
        ${ui.kpi({ label: 'Ledger', value: gl.balanced ? 'Balanced' : 'Out of balance', hint: `${gl.entries.length} journal entries from the loop`, status: gl.balanced ? { tone: 'good', label: 'Debits = credits' } : { tone: 'critical', label: 'Check' } })}
      </div>
      <div class="erp-tabs"></div>
      <div class="erp-body"></div>
    </div>`;
  S.tabs = ui.tabs($('.erp-tabs', el), {
    tabs: TABS.map((t) => ({ ...t, count: t.id === 'ap' && blocked.length ? blocked.length : null })),
    active: tab,
    onChange: (id) => { ctx.setQuery({ tab: id }, { silent: true }); showTab(id); },
  });
  await showTab(tab);
}

export function unmount() { S = null; }

function goTab(id, query = {}) {
  S.ctx.setQuery({ tab: id, ...query }, { silent: true });
  S.tabs.set(id);
  showTab(id);
  $('.erp-tabs', S.el).scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function showTab(id) {
  const outer = $('.erp-body', S.el);
  outer.innerHTML = String(ui.loading());
  const body = document.createElement('div');
  try {
    if (id === 'items') tabItems(body);
    else if (id === 'bom') await tabBOM(body);
    else if (id === 'pos') await tabPOs(body);
    else if (id === 'receipts') await tabReceipts(body);
    else if (id === 'ap') tabAP(body);
    else if (id === 'gl') await tabGL(body);
    if (!S) return;
    outer.innerHTML = '';
    outer.appendChild(body);
  } catch (err) {
    console.error(err);
    outer.innerHTML = String(ui.errorBox(err));
  }
}

// ---------------------------------------------------------------------------
// Item master
// ---------------------------------------------------------------------------
function tabItems(body) {
  const rows = S.cache.items.rows;
  let kind = 'ALL';
  body.innerHTML = html`
    <div class="filter-row"><span class="erp-kind"></span><span class="small muted">Click an item to explode its BOM (or see where it's used).</span></div>
    ${ui.card({ title: 'Item master', subtitle: 'Sellable kits are built at the 3PL from the CM-built vehicle and the OEM-built pack. Make/buy says who owns the material decision; that is what MRP plans against.', body: html`<div class="erp-items"></div>`, flush: true })}`;
  const cols = [
    { key: 'item_id', label: 'Item', render: (r) => html`<span class="mono nowrap">${r.item_id}</span>${r.revision ? html` <span class="tiny muted">rev ${r.revision}</span>` : ''}` },
    { key: 'name', label: 'Name', render: (r) => html`<div class="erp-name">${r.name}${r.lifecycle !== 'ACTIVE' ? html` ${ui.statusChip(r.lifecycle)}` : ''}</div><div class="tiny muted">${r.children ? `${r.children} children` : ''}${r.children && r.used_in ? ' · ' : ''}${r.used_in ? `used in ${r.used_in}` : ''}</div>` },
    { key: 'kind', label: 'Kind', render: (r) => html`<span class="erp-kind-tag k-${r.kind.toLowerCase()}">${fmt.title(r.kind)}</span>` },
    { key: 'make_buy', label: 'Make / buy', render: (r) => html`<span class="small nowrap" title="${MAKE_BUY[r.make_buy] || r.make_buy}">${MAKE_BUY_SHORT[r.make_buy] || r.make_buy}</span>` },
    { key: 'lead_time_days', label: 'LT (d)', num: true },
    { key: 'moq', label: 'MOQ / mult', num: true, render: (r) => (r.moq ? html`${fmt.int(r.moq)}<span class="muted"> / ${fmt.int(r.order_multiple)}</span>` : html`<span class="nil">—</span>`) },
    { key: 'safety_stock', label: 'SS', num: true, format: (v) => fmt.int(v) },
    { key: 'std_cost', label: 'Std cost', num: true, format: (v) => money2(v) },
    { key: 'contract_price', label: 'Contract today', num: true, render: (r) => (r.contract_price == null ? html`<span class="nil">—</span>` : html`${money2(r.contract_price)}${Math.abs(r.contract_price - (r.std_cost || 0)) > 0.004 ? html` <span class="erp-var ${r.contract_price > r.std_cost ? 'up' : 'down'}" title="vs standard cost">${r.contract_price > r.std_cost ? '▲' : '▼'}</span>` : ''}`) },
    { key: 'supplier_name', label: 'Primary source', render: (r) => (r.primary_supplier_id ? link.supplier(r.primary_supplier_id, r.supplier_name) : html`<span class="nil">—</span>`) },
  ];
  const table = ui.dataTable($('.erp-items', body), {
    columns: cols, rows, pageSize: 0, search: true, searchPlaceholder: 'Filter items…',
    onRowClick: (r) => goTab('bom', r.children ? { item: r.item_id, wu: null } : { wu: r.item_id }),
  });
  ui.segmented($('.erp-kind', body), {
    options: [{ value: 'ALL', label: 'All' }, ...KINDS.map((k) => ({ value: k, label: fmt.title(k) }))],
    value: kind, onChange: (v) => { kind = v; table.update(v === 'ALL' ? rows : rows.filter((r) => r.kind === v)); },
  });
}

// ---------------------------------------------------------------------------
// BOM explorer with effectivity and cost roll-up
// ---------------------------------------------------------------------------
async function tabBOM(body) {
  const q = S.ctx.query;
  const item = q.get('item') || 'LV1-SLATE-L';
  const date = q.get('date') || S.asOf;
  const compare = q.has('cmp') ? (q.get('cmp') || null) : addDays(S.asOf, -90);
  const wu = q.get('wu') || 'CEL-21700';
  const [d, w] = await Promise.all([
    api.get('/api/erp/bom', { item, date, compare }),
    api.get('/api/erp/where-used', { item: wu, date }),
  ]);
  const cur = d.current;
  const cmp = d.compare;
  const bd = cur.breakdown;
  const costChart = charts.bar({
    categories: bd.map((b) => b.label),
    series: [{ name: 'Cost in the roll-up', values: bd.map((b) => Math.round(b.amount * 100) / 100) }],
    horizontal: true, height: 40 + bd.length * 26, yFormat: (v) => fmt.usd(v, { compact: true }), valueLabels: true, labelWidth: 210,
    categoryFormat: (c) => (c.startsWith('CM conversion') ? 'CM price (incl. its parts)' : c),
  });
  const allItems = S.cache.items.rows;
  body.innerHTML = html`
    <div class="filter-row erp-bomctl">
      <label class="erp-lbl">Item <select class="select sm" data-b="item">${d.picker.map((p) => html`<option value="${p.item_id}"${p.item_id === item ? raw(' selected') : ''}>${p.item_id} · ${p.name}</option>`)}</select></label>
      <label class="erp-lbl">Effective on <input class="input sm" type="date" data-b="date" value="${date}"></label>
      <label class="erp-lbl">Compare with <input class="input sm" type="date" data-b="cmp" value="${compare || ''}"></label>
      <span class="small muted">Structure and cost resolve on the date you pick: BOM lines by eff_from/eff_to, prices by contract effectivity.</span>
    </div>
    <div class="grid">
      <div class="span-12">${ui.card({
        title: html`${cur.item.name} <span class="muted small mono">${cur.item.item_id}</span>`,
        subtitle: html`Multi-level BOM on ${fmt.dateLong(cur.date)} · one recursive CTE over <span class="mono">bom_line</span>, filtered by effectivity at every level`,
        actions: html`<a class="btn btn-ghost sm" href="#/sandbox?table=bom_line">${icon('database', 15)}<span>bom_line</span></a>`,
        body: bomTree(cur), flush: true,
      })}</div>
      <div class="${cmp ? 'span-6' : 'span-12'}">${ui.card({ title: html`Unit cost ${fmt.usd(cur.unit_cost, { cents: true })}`, subtitle: html`${cur.basis}. Lines inside the CM's price or a supplier's price are shown in the tree but not added again.`, body: costChart, tableToggle: true })}</div>
      ${cmp ? html`<div class="span-6">${compareCard(cur, cmp)}</div>` : ''}
      <div class="span-12">${ui.card({
        title: html`Where used · <span class="mono">${w.item ? w.item.item_id : wu}</span> ${w.item ? w.item.name : ''}`,
        subtitle: 'The same recursion run upward: every parent, kit and sub-tier material that depends on this part (on the effective date above).',
        actions: html`<select class="select sm" data-b="wu">${allItems.filter((i) => i.used_in).map((i) => html`<option value="${i.item_id}"${i.item_id === wu ? raw(' selected') : ''}>${i.item_id} · ${i.name}</option>`)}</select>`,
        body: whereUsed(w),
      })}</div>
    </div>`;
  on(body, 'change', '[data-b]', (e, t) => {
    const k = t.dataset.b;
    S.ctx.setQuery({ [k]: t.value || '' }, { silent: true });
    if (k === 'cmp' && !t.value) S.ctx.query.set('cmp', '');
    showTab('bom');
  });
  on(body, 'click', '[data-toggle-row]', (e, b) => {
    const tr = b.closest('tr');
    const key = tr.dataset.key;
    const collapsed = tr.classList.toggle('erp-collapsed');
    body.querySelectorAll('tr[data-key]').forEach((r) => {
      if (r.dataset.key.startsWith(key + '/')) r.hidden = collapsed || [...body.querySelectorAll('tr.erp-collapsed')].some((c) => c !== tr && r.dataset.key.startsWith(c.dataset.key + '/'));
    });
  });
  on(body, 'click', '[data-wu]', (e, a) => { e.preventDefault(); S.ctx.setQuery({ wu: a.dataset.wu }, { silent: true }); showTab('bom'); });
}

function bomTree(cur) {
  const rows = cur.rows;
  const hasKids = new Set(rows.map((r) => r.sort_key.split('/').slice(0, -1).join('/')).filter(Boolean));
  return html`<div class="table-wrap erp-bom"><table class="table dense">
    <thead><tr><th>Part</th><th>Position</th><th class="num">Qty per</th><th class="num">Ext qty</th><th>BOM of</th><th>Source</th><th>Effective</th><th class="num">Unit cost</th><th class="num">Ext cost</th></tr></thead>
    <tbody>${rows.map((r) => {
      const kids = hasKids.has(r.sort_key);
      const inside = !r.counted;
      return html`<tr data-key="${r.sort_key}" class="lvl-${r.lvl}${inside ? ' erp-inside' : ''}">
        <td class="erp-part" style="--lvl:${r.lvl}"><span class="erp-indent"></span>${kids ? html`<button class="erp-tog" type="button" data-toggle-row aria-label="Collapse">${icon('chevron-down', 13)}</button>` : html`<span class="erp-leaf"></span>`}<a class="id-link" href="#" data-wu="${r.child}" title="Where is this used?">${r.child}</a> <span class="small ${inside ? 'muted' : ''}">${r.name}</span>${r.lifecycle === 'PHASE_OUT' ? html` ${ui.chip('warning', 'Phase-out', { icon: false })}` : ''}</td>
        <td class="small">${r.position}</td>
        <td class="num">${fmt.num(r.qty_per, r.qty_per % 1 ? (r.qty_per < 0.01 ? 4 : 3) : 0)}</td>
        <td class="num">${fmt.num(r.ext_qty, r.ext_qty % 1 ? (r.ext_qty < 0.01 ? 4 : 2) : 0)}${r.uom !== 'EA' ? html` <span class="tiny muted">${r.uom}</span>` : ''}</td>
        <td><span class="erp-lvl l-${r.bom_level.toLowerCase()}">${r.bom_level}</span></td>
        <td class="small">${MAKE_BUY[r.make_buy] || r.make_buy}</td>
        <td class="small nowrap">${fmt.date(r.eff_from)}${r.eff_to ? ` → ${fmt.date(r.eff_to)}` : ' →'}${r.eco_id ? html` <span class="mono tiny erp-eco">${r.eco_id}</span>` : ''}</td>
        <td class="num">${money2(r.unit_cost)}</td>
        <td class="num">${r.rolls_up ? html`<b>${money2(r.ext_cost)}</b>` : r.counted ? html`<span class="muted">${money2(r.ext_cost)}</span>` : html`<span class="tiny muted" title="${r.cost_basis}">${r.bom_level === 'CM' ? 'in CM price' : 'in supplier price'}</span>`}</td>
      </tr>`;
    })}</tbody>
    <tfoot><tr><td colspan="8" class="erp-total">Unit cost on ${fmt.dateLong(cur.date)}</td><td class="num erp-total"><b>${money2(cur.unit_cost)}</b></td></tr></tfoot>
  </table></div>`;
}

function compareCard(cur, cmp) {
  const up = cmp.delta > 0;
  return ui.card({
    title: html`${up ? '+' : cmp.delta < 0 ? '−' : ''}${fmt.usd(Math.abs(cmp.delta), { cents: true })} vs ${fmt.date(cmp.date)}`,
    subtitle: `Same kit, costed on ${fmt.dateLong(cmp.date)} (${fmt.usd(cmp.unit_cost, { cents: true })}) and ${fmt.dateLong(cur.date)} (${fmt.usd(cur.unit_cost, { cents: true })})`,
    body: html`${cmp.lines.length ? html`<div class="table-wrap"><table class="table dense"><thead><tr><th>What changed</th><th class="num">Before</th><th class="num">After</th><th class="num">Δ</th></tr></thead>
      <tbody>${cmp.lines.map((l) => html`<tr><td class="small">${l.label}</td><td class="num">${money2(l.before)}</td><td class="num">${money2(l.after)}</td><td class="num"><span class="${l.delta > 0 ? 'erp-bad' : 'erp-good'}">${l.delta > 0 ? '+' : '−'}${fmt.num(Math.abs(l.delta), 2)}</span></td></tr>`)}</tbody></table></div>`
      : html`<p class="small muted">No cost changes between these dates.</p>`}
      ${cmp.ecos.length ? html`<h4 class="section-title">ECOs effective in between</h4>${cmp.ecos.map((e) => html`<div class="erp-ecorow"><span class="mono small">${e.eco_id}</span><span class="small">${e.title}</span><span class="spacer"></span><span class="small ${e.cost_delta > 0 ? 'erp-bad' : 'erp-good'}">${e.cost_delta > 0 ? '+' : '−'}$${fmt.num(Math.abs(e.cost_delta || 0), 2)}</span></div>`)}
        <p class="tiny muted">ECO cost deltas are engineering estimates; the roll-up uses the contract prices actually in effect, so the two can differ. Price steps with no ECO (contract renewals) show up only in the roll-up.</p>` : ''}`,
  });
}

function whereUsed(w) {
  if (!w.rows.length) return ui.empty('Not used in any BOM on this date.');
  const kits = w.rows.filter((r) => r.kind === 'KIT');
  return html`<div class="erp-wu">
    ${w.rows.map((r) => html`<div class="erp-wurow" style="--lvl:${r.lvl}">
      <span class="erp-wuarrow">${icon('arrow-right', 13)}</span>
      <a class="id-link" href="#" data-wu="${r.parent}">${r.parent}</a>
      <span class="small">${r.name}</span>
      <span class="small muted">needs ${fmt.num(r.qty, r.qty % 1 ? 4 : 0)} ${w.item && w.item.uom !== 'EA' ? w.item.uom : ''} of ${w.item ? w.item.item_id : ''} · ${r.bom_level} BOM</span>
    </div>`)}
    ${kits.length ? html`<p class="small muted erp-wunote">Reaches ${kits.length} sellable kit${kits.length === 1 ? '' : 's'}. A shortage or recall on this part touches every one of them. The Genealogy page answers the same question for built serials.</p>` : ''}
  </div>`;
}

// ---------------------------------------------------------------------------
// Purchase orders & receipts
// ---------------------------------------------------------------------------
async function tabPOs(body) {
  const d = S.cache.pos || (S.cache.pos = await api.get('/api/erp/pos'));
  let scope = 'open';
  const filt = () => d.rows.filter((r) => (scope === 'all' || (scope === 'open' && r.status === 'OPEN') || (scope === 'cm' && r.po_id.startsWith('47')) || (scope === 'comp' && r.po_id.startsWith('45'))));
  body.innerHTML = html`
    <div class="filter-row"><span class="erp-scope"></span><span class="small muted">45xxxxx are component POs; 47xxxxx are monthly vehicle POs to the CM (received when containers load, FCA Taichung).</span></div>
    ${ui.card({ title: 'Purchase orders', subtitle: 'Click a PO to open it in the Supplier Loop, with every promise traced to its source.', body: html`<div class="erp-pos"></div>`, flush: true })}`;
  const table = ui.dataTable($('.erp-pos', body), {
    columns: [
      { key: 'po_id', label: 'PO', render: (r) => link.po(r.po_id) },
      { key: 'supplier_name', label: 'Supplier', render: (r) => link.supplier(r.supplier_id, r.supplier_name) },
      { key: 'ship_to', label: 'Ship to', render: (r) => html`<span class="small">${r.ship_to}</span>` },
      { key: 'created_at', label: 'Created', format: (v) => fmt.date(v) },
      { key: 'buyer', label: 'Buyer' },
      { key: 'incoterm', label: 'Terms' },
      { key: 'lines', label: 'Lines', num: true },
      { key: 'items', label: 'Items', render: (r) => html`<span class="mono small">${r.items}</span>` },
      { key: 'value', label: 'Value', num: true, format: (v) => fmt.usd(v, { compact: true }) },
      { key: 'open_value', label: 'Open', num: true, format: (v) => (v ? fmt.usd(v, { compact: true }) : '—') },
      { key: 'received_pct', label: 'Received', num: true, render: (r) => html`<span class="erp-rcv">${ui.meter({ value: r.received_pct || 0, max: 1 })}<span>${fmt.pct(r.received_pct || 0, 0)}</span></span>` },
      { key: 'unconfirmed', label: 'Unconf.', num: true, render: (r) => (r.unconfirmed ? ui.chip('warning', String(r.unconfirmed)) : html`<span class="nil">—</span>`) },
      { key: 'first_need', label: 'Need window', render: (r) => html`<span class="small nowrap">${fmt.date(r.first_need)}${r.last_need !== r.first_need ? ` – ${fmt.date(r.last_need)}` : ''}</span>` },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
    ],
    rows: filt(), pageSize: 25, search: true, initialSort: { key: 'created_at', dir: 'desc' },
    onRowClick: (r) => S.ctx.go(`#/suppliers?po=${encodeURIComponent(r.po_id)}`),
  });
  ui.segmented($('.erp-scope', body), {
    options: [{ value: 'open', label: 'Open' }, { value: 'cm', label: 'CM vehicle POs' }, { value: 'comp', label: 'Component POs' }, { value: 'all', label: 'All' }],
    value: scope, onChange: (v) => { scope = v; table.update(filt()); },
  });
}

async function tabReceipts(body) {
  const d = S.cache.receipts || (S.cache.receipts = await api.get('/api/erp/receipts'));
  const rows = d.rows;
  const weekOf = (iso) => { const t = new Date(iso.slice(0, 10) + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7)); return t.toISOString().slice(0, 10); };
  const byWk = {};
  for (const r of rows) byWk[weekOf(r.received_at)] = (byWk[weekOf(r.received_at)] || 0) + (r.value || 0);
  const wks = Object.keys(byWk).sort().slice(-16);
  const chart = charts.bar({ categories: wks.map((w) => fmt.date(w)), series: [{ name: 'Receipts value', values: wks.map((w) => Math.round(byWk[w])) }], height: 200, yFormat: (v) => fmt.usd(v, { compact: true }) });
  const rejected = rows.filter((r) => r.iqc_status === 'REJECTED');
  body.innerHTML = html`<div class="grid">
    <div class="span-8">${ui.card({ title: 'Receipts value by week', subtitle: 'Goods receipts at contract price: Fremont lots, consigned modules at the CM, chargers at the 3PL', body: chart, tableToggle: true })}</div>
    <div class="span-4">${rejected.length ? ui.callout({ tone: 'serious', title: `${rejected.length} receipt${rejected.length === 1 ? '' : 's'} failed incoming inspection`, body: html`${rejected.map((r) => html`<div>${link.lot(r.lot_id)} · ${fmt.int(r.qty)} × ${r.item_id} from ${r.supplier_name}, returned to vendor. The invoice for it is blocked in ${html`<a href="#" data-goto="ap">AP</a>`}.</div>`)}` }) : ui.callout({ tone: 'good', title: 'No receipts rejected at IQC' })}
      ${ui.callout({ tone: 'info', title: 'Lot-controlled, serialized or bulk', body: 'Lot-controlled parts get a lot at receipt (the genealogy key for recalls). Serialized modules arrive on an ASN; each serial becomes a unit. Bulk items carry neither.' })}</div>
    <div class="span-12">${ui.card({ title: 'Goods receipts', body: html`<div class="erp-rcvt"></div>`, flush: true })}</div>
  </div>`;
  ui.dataTable($('.erp-rcvt', body), {
    columns: [
      { key: 'receipt_id', label: 'Receipt', mono: true },
      { key: 'received_at', label: 'Received', format: (v) => fmt.dt(v) },
      { key: 'po', label: 'PO line', value: (r) => `${r.po_id}-${r.line_no}`, render: (r) => link.po(r.po_id, r.line_no) },
      { key: 'item_id', label: 'Item', render: (r) => html`<span class="mono">${r.item_id}</span> <span class="small muted">${r.item_name}</span>` },
      { key: 'supplier_name', label: 'Supplier', render: (r) => link.supplier(r.supplier_id, r.supplier_name) },
      { key: 'site_id', label: 'Site', mono: true },
      { key: 'qty', label: 'Qty', num: true, format: (v) => fmt.int(v) },
      { key: 'lot_id', label: 'Lot / ASN', render: (r) => (r.lot_id ? link.lot(r.lot_id) : html`<span class="mono small muted">${r.asn_no || '—'}</span>`) },
      { key: 'iqc_status', label: 'IQC', render: (r) => (r.iqc_status ? ui.statusChip(r.iqc_status) : html`<span class="small muted">serialized</span>`) },
      { key: 'value', label: 'Value', num: true, format: (v) => fmt.usd(v) },
    ],
    rows, pageSize: 25, search: true, initialSort: { key: 'received_at', dir: 'desc' },
  });
  on(body, 'click', '[data-goto]', (e, a) => { e.preventDefault(); goTab(a.dataset.goto); });
}

// ---------------------------------------------------------------------------
// AP & three-way match
// ---------------------------------------------------------------------------
function matchDots(r) {
  return html`<span class="erp-dots">${r.checks.map((c) => html`<span class="erp-dot ${c.ok ? 'ok' : 'bad'}" title="${c.check}: ${c.detail}">${icon(c.ok ? 'check' : 'x', 11)}</span>`)}</span>`;
}

function tabAP(body) {
  const d = S.cache.ap;
  const rows = d.rows;
  const blocked = rows.filter((r) => r.match_status !== 'MATCHED');
  const agingKeys = ['overdue', '0-15', '16-30', '31-60', '60+'];
  const aging = charts.bar({
    categories: agingKeys.map((k) => (k === 'overdue' ? 'Overdue' : `${k} days`)),
    series: [{ name: 'Unpaid by days to due', values: agingKeys.map((k) => Math.round(d.aging[k])) }],
    height: 190, yFormat: (v) => fmt.usd(v, { compact: true }),
  });
  const mc = d.match_counts;
  body.innerHTML = html`<div class="grid">
    <div class="span-5">${ui.card({ title: 'Three-way match', subtitle: 'Every invoice is checked against its PO line (price) and the goods receipt (quantity), and against what passed incoming inspection.', body: html`
      <div class="erp-mc">
        <div><span class="erp-mcv">${fmt.int(mc.MATCHED)}</span><span class="small muted">matched</span></div>
        <div><span class="erp-mcv">${fmt.int(mc.PRICE_VARIANCE)}</span><span class="small muted">price variance</span></div>
        <div><span class="erp-mcv ${mc.QTY_VARIANCE ? 'erp-bad' : ''}">${fmt.int(mc.QTY_VARIANCE)}</span><span class="small muted">qty variance</span></div>
        <div><span class="erp-mcv">${fmt.int(mc.ON_HOLD)}</span><span class="small muted">on hold</span></div>
      </div>
      ${blocked.map((r) => ui.callout({ tone: 'serious', title: html`${r.invoice_id} blocked: ${fmt.usd(r.amount_usd)} from ${r.supplier_name}`, body: html`${r.checks.map((c) => html`<div class="erp-check">${c.ok ? ui.chip('good', c.check) : ui.chip('critical', c.check)} <span class="small">${c.detail}</span></div>`)}
        <div class="small muted erp-why">The receipt happened, so a two-way match would have paid it. The third check, against IQC, is what stops paying for parts that went back to the vendor.</div>` }))}
      ${mc.PRICE_VARIANCE ? '' : html`<p class="small muted">No price variances on posted invoices. The price risk is still upstream, on open PO lines issued at superseded contract prices: ${html`<a class="ent-link" href="#/suppliers?tab=pricing">Supplier Loop › Pricing</a>`}.</p>`}` })}</div>
    <div class="span-7">${ui.card({ title: 'Unpaid invoices by due date', subtitle: html`As of ${fmt.dateLong(d.as_of)} · paid ${fmt.usd(d.by_status.PAID, { compact: true })} · scheduled ${fmt.usd(d.by_status.SCHEDULED, { compact: true })} · open ${fmt.usd(d.by_status.OPEN, { compact: true })} · blocked ${fmt.usd(d.by_status.BLOCKED, { compact: true })}`, body: aging, tableToggle: true })}</div>
    <div class="span-12">${ui.card({ title: 'Supplier invoices', subtitle: 'Dots are the three checks: price = PO · quantity ≤ received · quantity ≤ accepted at IQC. Click a row for the match detail.', body: html`<div class="erp-inv"></div>`, flush: true })}</div>
  </div>`;
  ui.dataTable($('.erp-inv', body), {
    columns: [
      { key: 'invoice_id', label: 'Invoice', mono: true },
      { key: 'supplier_name', label: 'Supplier', render: (r) => link.supplier(r.supplier_id, r.supplier_name) },
      { key: 'po', label: 'PO line', value: (r) => `${r.po_id}-${r.line_no}`, render: (r) => link.po(r.po_id, r.line_no) },
      { key: 'item_id', label: 'Item', mono: true },
      { key: 'invoice_date', label: 'Date', format: (v) => fmt.date(v) },
      { key: 'qty', label: 'Qty', num: true, format: (v) => fmt.int(v) },
      { key: 'unit_price', label: 'Price', num: true, format: (v) => money2(v) },
      { key: 'amount_usd', label: 'Amount', num: true, format: (v) => fmt.usd(v) },
      { key: 'checks', label: '3-way match', value: (r) => r.checks.filter((c) => c.ok).length, render: (r) => html`${matchDots(r)}${r.match_status !== 'MATCHED' ? html` <span class="tiny erp-bad">${fmt.title(r.match_status)}</span>` : ''}` },
      { key: 'due_date', label: 'Due', render: (r) => html`<span class="small nowrap">${fmt.date(r.due_date)} <span class="muted">${r.pay_status === 'PAID' ? '' : r.days_to_due < 0 ? `${-r.days_to_due}d late` : `in ${r.days_to_due}d`}</span></span>` },
      { key: 'pay_status', label: 'Pay', render: (r) => ui.statusChip(r.pay_status) },
    ],
    rows, pageSize: 25, search: true, initialSort: { key: 'invoice_date', dir: 'desc' }, onRowClick: (r) => openInvoice(r),
  });
}

function openInvoice(r) {
  ui.drawer.open({
    title: r.invoice_id, subtitle: `${r.supplier_name} · ${fmt.usd(r.amount_usd)}`, width: 560,
    body: html`<div class="erp-drawer">
      <div class="erp-3way">
        <div class="erp-3col"><div class="section-title">PO line</div><div class="big">${link.po(r.po_id, r.line_no)}</div><div class="small">${fmt.int(r.po_qty)} × ${money2(r.po_price)}</div></div>
        <div class="erp-3col"><div class="section-title">Receipts</div><div class="big">${fmt.int(r.received)}</div><div class="small">${fmt.int(r.accepted)} accepted at IQC${r.rejected_lots ? html` · ${link.lot(r.rejected_lots)} rejected` : ''}</div></div>
        <div class="erp-3col"><div class="section-title">Invoice</div><div class="big">${fmt.int(r.qty)}</div><div class="small">× ${money2(r.unit_price)} = ${fmt.usd(r.amount_usd)}</div></div>
      </div>
      <h4 class="section-title">Checks</h4>
      ${r.checks.map((c) => html`<div class="erp-check">${ui.chip(c.ok ? 'good' : 'critical', c.ok ? 'Pass' : 'Fail')} <b>${c.check}</b> <span class="small muted">${c.detail}</span></div>`)}
      <dl class="kv erp-kv">
        <dt>Invoice date</dt><dd>${fmt.dateLong(r.invoice_date)}</dd>
        <dt>Due</dt><dd>${fmt.dateLong(r.due_date)}</dd>
        <dt>Match</dt><dd>${ui.statusChip(r.match_status === 'MATCHED' ? 'OK' : 'BLOCKED', fmt.title(r.match_status))}${r.variance_usd ? html` · variance ${fmt.usd(r.variance_usd)}` : ''}</dd>
        <dt>Payment</dt><dd>${ui.statusChip(r.pay_status)}</dd>
      </dl>
    </div>`,
  });
}

// ---------------------------------------------------------------------------
// General ledger
// ---------------------------------------------------------------------------
async function tabGL(body) {
  const d = S.cache.gl;
  const fx = S.cache.fx || (S.cache.fx = await api.get('/api/erp/fx'));
  const tb = d.trial_balance.filter((a) => a.debits || a.credits);
  body.innerHTML = html`<div class="grid">
    <div class="span-8 stack">
      ${ui.callout({ tone: 'info', title: 'A defect becomes a journal entry', body: html`Field claim → failed part → lot → responsible supplier → chargeback priced by the supplier's recovery terms → debit memo. The debit memo reduces what we owe the supplier (Dr 2000 Accounts payable) and books the recovery against the cost it offsets (Cr 5410 warranty or Cr 5110 quality). Every line below traces back to claims in ${link.route('warranty', 'Warranty & Chargebacks')}.` })}
      ${d.entries.map((e) => ui.card({
        title: html`<span class="mono">${e.je_id}</span> · ${fmt.title(e.doc_type)}${e.debit_memo_no ? html` <span class="mono small muted">${e.debit_memo_no}</span>` : ''}`,
        subtitle: html`${fmt.dt(e.posted_at)} · ${e.supplier_id ? link.supplier(e.supplier_id, e.supplier_name) : ''} · ${e.memo}`,
        actions: html`${e.balanced ? ui.chip('good', 'Balanced') : ui.chip('critical', 'Unbalanced')}${e.chargeback_id ? html` <a class="btn btn-ghost sm" href="#/warranty">${icon('receipt', 15)}<span>${e.chargeback_id}</span></a>` : ''}`,
        body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>#</th><th>Account</th><th>Cost center</th><th>Memo</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead>
          <tbody>${e.lines.map((l) => html`<tr><td class="mono">${l.line_no}</td><td><span class="mono">${l.gl_account}</span> ${l.account_name}</td><td class="small">${l.cost_center || ''}</td><td class="small">${l.memo || ''}</td><td class="num">${l.debit_usd ? fmt.usd(l.debit_usd, { cents: true }) : ''}</td><td class="num">${l.credit_usd ? fmt.usd(l.credit_usd, { cents: true }) : ''}</td></tr>`)}</tbody>
          <tfoot><tr><td colspan="4" class="erp-total">Total</td><td class="num erp-total"><b>${fmt.usd(e.debits, { cents: true })}</b></td><td class="num erp-total"><b>${fmt.usd(e.credits, { cents: true })}</b></td></tr></tfoot></table></div>`,
      }))}
    </div>
    <div class="span-4 stack">
      ${ui.card({ title: 'Recoveries not yet posted', subtitle: 'Chargebacks still with the supplier. They post when accepted.', body: d.pending_recoveries.length ? html`<div class="table-wrap"><table class="table dense"><thead><tr><th>ID</th><th>Supplier</th><th class="num">Amount</th><th>Status</th></tr></thead>
        <tbody>${d.pending_recoveries.map((p) => html`<tr><td class="mono"><a class="id-link" href="#/warranty">${p.chargeback_id}</a></td><td>${link.supplier(p.supplier_id)}</td><td class="num">${fmt.usd(p.amount_usd)}</td><td>${ui.statusChip(p.status)}</td></tr>`)}</tbody></table></div>` : ui.empty('Nothing pending.') })}
      ${ui.card({ title: 'Trial balance · loop accounts', subtitle: 'Accounts the operating loop writes to', body: html`<div class="table-wrap"><table class="table dense erp-tb"><thead><tr><th>Account</th><th class="num">Dr</th><th class="num">Cr</th></tr></thead>
        <tbody>${tb.map((a) => html`<tr><td><span class="mono">${a.gl_account}</span> <span class="small">${a.name.replace(/:.*$/, '')}</span></td><td class="num">${a.debits ? fmt.usd(a.debits) : '—'}</td><td class="num">${a.credits ? fmt.usd(a.credits) : '—'}</td></tr>`)}</tbody>
        <tfoot><tr><td class="erp-total">Total</td><td class="num erp-total"><b>${fmt.usd(tb.reduce((s, a) => s + a.debits, 0))}</b></td><td class="num erp-total"><b>${fmt.usd(tb.reduce((s, a) => s + a.credits, 0))}</b></td></tr></tfoot></table></div>` })}
      ${ui.card({ title: 'FX rates', subtitle: 'Units per USD, last 90 days (mock). Used to book the CM\'s NTD scrap values and supplier statements.', body: html`<div class="erp-fx">${fx.series.map((s) => html`<div class="erp-fxrow"><span class="mono strong">${s.currency}</span><span class="erp-fxv">${fmt.num(s.latest, s.latest > 100 ? 1 : 3)}</span>${charts.sparkline(s.points.map((p) => p.per_usd), { width: 96, height: 24 })}<span class="small muted">${s.change_30d == null ? '' : `${s.change_30d > 0 ? '+' : '−'}${fmt.pct(Math.abs(s.change_30d), 1)} 30d`}</span></div>`)}</div>` })}
    </div>
  </div>`;
}

const PAGE_CSS = `
.pg-erp .erp-tabs { margin-top: 20px; }
.pg-erp .erp-body { margin-top: 16px; }
.pg-erp .erp-lbl { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.pg-erp .erp-bomctl { margin: 0 0 14px; }
.pg-erp .erp-name { white-space: nowrap; }
.pg-erp .erp-kind-tag { font: 600 11px/1 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; padding: 3px 6px; border-radius: 4px; background: var(--surface-2); box-shadow: inset 0 0 0 1px var(--hairline); color: var(--ink-2); white-space: nowrap; }
.pg-erp .erp-kind-tag.k-kit { background: color-mix(in srgb, var(--sign) 14%, transparent); color: var(--ink); }
.pg-erp .erp-kind-tag.k-vehicle, .pg-erp .erp-kind-tag.k-pack { background: color-mix(in srgb, var(--info) 12%, transparent); color: var(--ink); }
.pg-erp .erp-var { font-size: 10px; }
.pg-erp .erp-var.up { color: var(--delta-bad); }
.pg-erp .erp-var.down { color: var(--delta-good); }
.pg-erp .erp-good { color: var(--delta-good); font-weight: 600; }
.pg-erp .erp-bad { color: var(--delta-bad); font-weight: 600; }
/* BOM tree */
.pg-erp .erp-bom table { white-space: nowrap; }
.pg-erp .erp-part { padding-left: 8px !important; }
.pg-erp .erp-indent { display: inline-block; width: calc((var(--lvl) - 1) * 18px); }
.pg-erp .erp-tog { all: unset; cursor: pointer; display: inline-flex; width: 16px; height: 16px; align-items: center; justify-content: center; margin-right: 4px; border-radius: 4px; color: var(--ink-3); vertical-align: -3px; }
.pg-erp .erp-tog:hover { background: var(--row-hover); color: var(--ink); }
.pg-erp .erp-tog:focus-visible { outline: 2px solid var(--focus); }
.pg-erp .erp-collapsed .erp-tog { transform: rotate(-90deg); }
.pg-erp .erp-leaf { display: inline-block; width: 20px; }
.pg-erp tr.lvl-1 td { font-weight: 500; }
.pg-erp tr.erp-inside td { color: var(--ink-2); }
.pg-erp .erp-lvl { font: 600 10.5px/1 var(--font-mono); padding: 2px 5px; border-radius: 4px; box-shadow: inset 0 0 0 1px var(--hairline-strong); }
.pg-erp .erp-lvl.l-also { background: color-mix(in srgb, var(--sign) 12%, transparent); }
.pg-erp .erp-eco { color: var(--link); }
.pg-erp .erp-total { background: var(--surface-2); }
.pg-erp .erp-ecorow { display: flex; gap: 8px; align-items: baseline; padding: 5px 0; border-top: 1px solid var(--hairline); }
.pg-erp .erp-wu { display: flex; flex-direction: column; gap: 2px; }
.pg-erp .erp-wurow { display: flex; align-items: baseline; gap: 8px; padding: 5px 0 5px calc((var(--lvl) - 1) * 22px); border-top: 1px solid var(--hairline); flex-wrap: wrap; }
.pg-erp .erp-wuarrow { color: var(--ink-3); transform: translateY(2px); }
.pg-erp .erp-wunote { margin-top: 10px; }
/* POs */
.pg-erp .erp-rcv { display: inline-flex; align-items: center; gap: 6px; }
.pg-erp .erp-rcv .meter { width: 52px; }
/* AP */
.pg-erp .erp-mc { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
.pg-erp .erp-mc > div { display: flex; flex-direction: column; gap: 2px; padding: 10px; border-radius: 10px; background: var(--surface-2); }
.pg-erp .erp-mcv { font: 600 24px/1.1 var(--font-ui); }
.pg-erp .erp-check, .erp-drawer .erp-check { display: flex; align-items: center; gap: 8px; margin-top: 6px; flex-wrap: wrap; }
.pg-erp .erp-why { margin-top: 8px; }
.pg-erp .erp-dots { display: inline-flex; gap: 3px; }
.pg-erp .erp-dot { display: inline-flex; width: 16px; height: 16px; border-radius: 50%; align-items: center; justify-content: center; color: #fff; }
.pg-erp .erp-dot.ok { background: var(--good); }
.pg-erp .erp-dot.bad { background: var(--critical); }
.erp-drawer .erp-3way { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.erp-drawer .erp-3col { padding: 10px 12px; border-radius: 10px; background: var(--surface-2); box-shadow: inset 0 0 0 1px var(--hairline); }
.erp-drawer .erp-3col .section-title { margin: 0 0 6px; }
.erp-drawer .big { font: 600 18px/1.2 var(--font-ui); }
.erp-drawer .erp-kv { margin-top: 14px; }
/* FX */
.pg-erp .erp-fx { display: flex; flex-direction: column; gap: 8px; }
.pg-erp .erp-fxrow { display: grid; grid-template-columns: 44px 70px 96px 1fr; align-items: center; gap: 8px; }
.pg-erp .erp-fxv { font-variant-numeric: tabular-nums; text-align: right; }
@media (max-width: 700px) {
  .pg-erp .erp-mc { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .erp-drawer .erp-3way { grid-template-columns: 1fr; }
}
`;
