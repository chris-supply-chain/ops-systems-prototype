// Inventory · WMS: one multi-echelon position from supplier-held stock to the 3PL
// shelf, by owner and status, valued at standard cost, with days of cover against
// forward demand, reconciled against the 3PL's WMS snapshot.
import { html, raw, esc, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const OWNER_TONE = { OEM: 'info', CM: 'neutral', SUPPLIER: 'neutral' };
const KIND_ORDER = ['VEHICLE', 'PACK', 'MODULE', 'COMPONENT'];
const KIND_LABEL = { VEHICLE: 'Vehicles', PACK: 'Packs', MODULE: 'Modules', COMPONENT: 'Components' };

let S = null;

function coverTone(p) {
  if (p.dos == null) return null;
  if (p.point_of_use === 'CM_OWNED') return 'neutral';
  if (p.dos < 3) return 'critical';
  if (p.dos < 7) return 'warning';
  return 'good';
}

export async function render(el, ctx) {
  injectStyle('page-inventory', PAGE_CSS);
  const d = await api.get('/api/inventory/overview');
  S = { el, ctx, d };
  const E = Object.fromEntries(d.echelons.map((e) => [e.id, e]));
  const oemValue = d.echelons.filter((e) => e.owner === 'OEM').reduce((a, e) => a + e.value, 0);
  const veh = d.items.filter((i) => i.kind === 'VEHICLE');
  const pipeline = veh.reduce((a, i) => a + i.by.CM_FG + i.by.TRANSIT + i.by['3PL'], 0);
  const vehAvail = veh.reduce((a, i) => a + i.available, 0);
  const vehUse = veh.reduce((a, i) => a + (i.daily_use || 0), 0);
  const thin = d.items.filter((i) => i.dos != null && i.dos < 3 && ['PLANT', 'CM_CONSIGNED'].includes(i.point_of_use));
  const wmsVar = d.wms ? d.wms.rows.reduce((a, r) => a + Math.abs(r.diff_available) + Math.abs(r.diff_hold), 0) : 0;

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-inventory">
      <div class="inv-route" role="list">${d.echelons.map((e, i) => html`
        <div class="inv-stop" role="listitem">
          <div class="inv-stop-top"><span class="inv-num">${i + 1}</span>${ui.chip(OWNER_TONE[e.owner], e.owner === 'OEM' ? 'OEM-owned' : e.owner === 'CM' ? 'CM-owned' : 'Supplier-owned', { icon: false })}</div>
          <div class="inv-stop-label">${e.label}</div>
          <div class="inv-stop-val">${fmt.usd(e.value, { compact: true })}</div>
          <div class="inv-stop-sub">${fmt.compact(e.qty_units)} units · ${e.items} items</div>
          <ul class="inv-buckets">${e.buckets.slice(0, 3).map((b) => html`<li><span class="truncate">${b.bucket}</span><span class="num">${fmt.compact(b.qty)}</span></li>`)}</ul>
        </div>`)}
      </div>

      <div class="kpi-row">
        ${ui.kpi({ label: 'OEM-owned inventory', value: fmt.usd(oemValue, { compact: true }), hint: 'At standard cost, all echelons' })}
        ${ui.kpi({ label: 'Supplier-held, visible', value: fmt.usd(E.SUPPLIER.value, { compact: true }), hint: 'From VMI and supplier reports' })}
        ${ui.kpi({ label: 'Vehicles in the pipeline', value: fmt.int(pipeline), hint: `${fmt.int(E.CM_FG.qty_units)} at the CM · ${fmt.int(E.TRANSIT.buckets.reduce((a, b) => a + (/DG truck/.test(b.bucket) ? 0 : b.qty), 0))} in transit · rest at Reno` })}
        ${ui.kpi({ label: 'Vehicles free at the 3PL', value: fmt.int(vehAvail), hint: vehUse ? `${fmt.num(vehAvail / vehUse, 1)} days at ${fmt.num(vehUse, 0)}/day allocation` : '' })}
        ${ui.kpi({ label: 'Line-feeding items < 3 days', value: fmt.int(thin.length), hint: thin.length ? `${thin.map((i) => i.item_id).slice(0, 4).join(', ')} at the point of use` : 'Every input covered 3+ days' })}
        ${ui.kpi({ label: 'WMS variance', value: fmt.int(wmsVar), unit: 'units', hint: d.wms ? `vs 3PL snapshot ${fmt.dt(d.wms.snapshot_at)} PT` : '' })}
      </div>

      <div class="inv-insights">${insights(d)}</div>

      <div class="grid">
        <div class="span-12">${ui.card({
          title: 'Position by item across echelons',
          subtitle: 'Units by echelon. Cover = usable stock at the point of use ÷ forward daily need (MPS/CM commit exploded through the BOM with effectivity), or trailing allocations for finished goods.',
          flush: true, body: html`<div data-items></div>`,
        })}</div>
        <div class="span-7">${ui.card({
          title: 'WMS vs system of record', subtitle: d.wms ? `3PL snapshot ${fmt.dt(d.wms.snapshot_at)} PT, against serial-level receipts, allocations and holds as of the same instant` : '',
          flush: true, body: wmsTable(d),
        })}</div>
        <div class="span-5">${ui.card({
          title: 'Value by echelon', subtitle: 'Standard cost, by item kind', tableToggle: true, body: valueChart(d),
        })}</div>
        <div class="span-12">${ui.card({
          title: 'Fremont plant stock by lot', subtitle: 'Lot-controlled pack components on hand: IQC status and age since receipt', flush: true,
          body: html`<div data-lots></div>`,
        })}</div>
      </div>
    </div>`;

  ui.dataTable($('[data-items]', el), {
    search: true, searchPlaceholder: 'Filter items…', pageSize: 40, dense: true,
    columns: [
      { key: 'item_id', label: 'Item', render: (r) => html`<a class="id-link" href="#/inventory?item=${encodeURIComponent(r.item_id)}">${r.item_id}</a><div class="tiny muted inv-name">${r.name}</div>` },
      { key: 'dos', label: 'Cover', num: true, value: (r) => r.dos,
        title: 'Usable stock at the point of use ÷ need per day',
        render: (r) => (r.dos == null ? html`<span class="nil">—</span>` : ui.chip(coverTone(r), `${fmt.num(r.dos, 1)} d`, { icon: coverTone(r) === 'neutral' ? false : undefined })) },
      { key: 'daily_use', label: 'Need/day', num: true, value: (r) => r.daily_use, format: (v) => fmt.num(v, v < 10 ? 1 : 0),
        title: 'Forward daily need (or trailing allocations for finished goods)' },
      { key: 'SUPPLIER', label: 'Supplier-held', num: true, value: (r) => r.by.SUPPLIER || null },
      { key: 'INBOUND', label: 'Inbound', num: true, value: (r) => r.by.INBOUND || null },
      { key: 'PLANT', label: 'Fremont', num: true, value: (r) => r.by.PLANT || null },
      { key: 'CM', label: 'At the CM', num: true, value: (r) => (r.by.CM_CONSIGNED + r.by.CM_OWNED) || null,
        title: 'Consigned (OEM-owned) or CM-owned parts at Taichung',
        render: (r) => (r.by.CM_CONSIGNED || r.by.CM_OWNED ? html`${fmt.int(r.by.CM_CONSIGNED + r.by.CM_OWNED)}<div class="tiny muted">${r.by.CM_CONSIGNED ? 'consigned' : 'CM-owned'}</div>` : html`<span class="nil">—</span>`) },
      { key: 'CM_FG', label: 'CM WIP/FG', num: true, value: (r) => r.by.CM_FG || null },
      { key: 'TRANSIT', label: 'In transit', num: true, value: (r) => r.by.TRANSIT || null },
      { key: 'available', label: '3PL free', num: true, value: (r) => (r.by['3PL'] ? r.available : null) },
      { key: 'allocated', label: 'Allocated', num: true, value: (r) => r.allocated || null },
      { key: 'hold', label: 'Hold', num: true, value: (r) => r.hold || null },
      { key: 'value_oem', label: 'OEM value', num: true, value: (r) => r.value_oem || null, format: (v) => fmt.usd(v, { compact: true }) },
    ],
    rows: d.items, initialSort: { key: 'dos', dir: 'asc' },
  });

  ui.dataTable($('[data-lots]', el), {
    pageSize: 12, dense: true,
    columns: [
      { key: 'lot_id', label: 'Lot', render: (r) => link.lot(r.lot_id) },
      { key: 'item_id', label: 'Item', render: (r) => html`${link.item(r.item_id)} <span class="tiny muted">${r.name}</span>` },
      { key: 'supplier_id', label: 'Supplier', render: (r) => link.supplier(r.supplier_id) },
      { key: 'qty', label: 'On hand', num: true },
      { key: 'iqc_status', label: 'IQC', render: (r) => ui.statusChip(r.iqc_status) },
      { key: 'received_at', label: 'Received', format: (v) => fmt.date(v) },
      { key: 'age_days', label: 'Age', num: true, format: (v) => `${v} d` },
      { key: 'po_id', label: 'PO', render: (r) => link.po(r.po_id, r.po_line_no) },
    ],
    rows: d.fre_lots, initialSort: { key: 'age_days', dir: 'desc' },
  });

  on(el, 'click', 'a[href^="#/inventory?item="]', (e, a) => {
    e.preventDefault();
    const id = decodeURIComponent(a.getAttribute('href').split('item=')[1]);
    openItem(id);
  });
  const deep = ctx.query.get('item');
  if (deep) openItem(deep);
}

export function unmount() {
  if (ui.drawer.isOpen) ui.drawer.close();
  S = null;
}

// ---------------------------------------------------------------------------
function insights(d) {
  const out = [];
  const ins = d.insights;
  const b = ins.bms;
  if (b) {
    const nr = b.next_receipt;
    out.push(ui.callout({
      tone: b.cover_days != null && b.cover_days < 7 ? 'critical' : 'warning',
      title: `BMS boards: ${fmt.num(b.cover_days, 1)} days of cover at the pack line`,
      body: html`${fmt.int(b.b_on_hand)} rev B on hand, plus ${fmt.int(Math.min(b.a_on_hand, b.a_allowance))} rev A still usable under ${b.deviation ? b.deviation.deviation_id : 'the deviation'} (valid to ${b.deviation ? fmt.date(b.deviation.valid_to) : '—'}),
        against ${fmt.num(b.daily_use, 0)}/day. ${nr ? html`Next receipt: ${link.po(nr.po_id, nr.line_no)}, ${fmt.int(nr.qty)} pcs, promised ${fmt.date(nr.promise_date || nr.need_date)} (needed ${fmt.date(nr.need_date)}).` : ''}
        <a class="ent-link" href="#/mrp?item=BMS-B">See the line-stop date in MRP →</a>`,
    }));
  }
  if (ins.unaccounted && ins.unaccounted.length) {
    const u = ins.unaccounted;
    out.push(ui.callout({
      tone: 'serious',
      title: `${u.length} vehicles on a container's ASN never reached the 3PL`,
      body: html`${link.shipment(u[0].shipment_id)} (${u[0].container_no}) was received ${fmt.date(u[0].received_at)} without
        ${u.map((x, i) => html`${i ? ', ' : ''}${link.serial(x.serial)}`)}. They still read "in transit" because nothing has
        told us otherwise. Likely left on the CM's dock. <a class="ent-link" href="#/contracts">Contract & recon →</a>`,
    }));
  }
  if (ins.stranded) {
    const s = ins.stranded;
    out.push(ui.callout({
      tone: 'warning',
      title: `${fmt.int(s.qty)} rev-B drive units stranded at the CM: ${fmt.usd(s.value, { compact: true })}`,
      body: html`OEM-owned consigned stock with no forward requirement since ${s.eco ? s.eco.eco_id : 'the ECO'} cut in at ${link.serial(s.cut_in)}
        (${s.eco ? fmt.date(s.eco.effective_date) : ''}). It needs a disposition decision: service spares, rework to rev C, or return to Tainan Motion.`,
    }));
  }
  const cm = (ins.cm_stock || []).filter((r) => r.excel !== r.system);
  if (cm.length) {
    out.push(ui.callout({
      tone: 'info',
      title: `CM's Excel counts ${cm.map((r) => `${r.item_id} ${r.excel - r.system > 0 ? '+' : ''}${r.excel - r.system}`).join(', ')} vs our serials`,
      body: html`The CM's daily report (parsed from email) and serial-level units disagree. Units hand-carried without an ASN are
        on the CM's shelf but not in our serial registry until they're scanned. <a class="ent-link" href="#/cm-feed?tab=recon">CM Feed reconciliation →</a>`,
    }));
  }
  const g = ins.gasket;
  if (g && g.installed_after_expiry) {
    out.push(ui.callout({
      tone: 'warning',
      title: `${fmt.int(g.installed_after_expiry)} EPDM gaskets installed after ${g.deviation.deviation_id} expired`,
      body: html`The use-up deviation for GSK-A was valid to ${fmt.date(g.deviation.valid_to)}, but the line kept drawing EPDM stock until it ran out.
        That also hides silicone (GSK-B) demand: ${fmt.num(g.gsk_b_trailing, 1)}/day trailing vs ${fmt.num(g.gsk_b_forward, 0)}/day from now on.
        <a class="ent-link" href="#/quality">Quality Loop →</a>`,
    }));
  }
  const fresh = (ins.freshness || []).filter((f) => f.as_of && f.as_of.slice(0, 10) < d.as_of);
  if (fresh.length) {
    out.push(ui.callout({
      tone: 'neutral',
      title: 'Supplier-held stock is only as fresh as the last report',
      body: html`${fresh.map((f, i) => html`${i ? '; ' : ''}${f.item_id} at ${f.site_id.replace('SUP-', '')} as of ${fmt.date(f.as_of.slice(0, 10))}`)} (${fresh[0].source === 'SUPPLIER_REPORT' ? 'weekly VMI workbook, parsed from email' : fresh[0].source}).
        <a class="ent-link" href="#/integrations">Integration Hub →</a>`,
    }));
  }
  return out;
}

function wmsTable(d) {
  if (!d.wms) return ui.empty('No WMS snapshot yet.');
  return html`<div class="table-wrap"><table class="table dense">
    <thead><tr><th>SKU</th><th class="num">WMS free</th><th class="num">System free</th><th class="num">Diff</th><th class="num">WMS hold</th><th class="num">System hold</th><th>Why</th></tr></thead>
    <tbody>${d.wms.rows.map((r) => html`<tr>
      <td>${link.item(r.sku)}</td><td class="num">${fmt.int(r.wms_available)}</td><td class="num">${fmt.int(r.system_available)}</td>
      <td class="num">${r.diff_available ? ui.chip('warning', `${r.diff_available > 0 ? '+' : ''}${r.diff_available}`) : ui.chip('good', '0')}</td>
      <td class="num">${fmt.int(r.wms_hold)}</td><td class="num">${fmt.int(r.system_hold)}</td>
      <td class="small wrap">${r.causes.length ? r.causes.join('; ') : html`<span class="muted">Ties out</span>`}</td></tr>`)}</tbody>
  </table></div>
  <p class="small muted inv-pad">${icon('info', 14)} Units on hold are freight damage recorded on receipt (hold table). One-number-per-SKU comes from rebuilding the 3PL's position at the snapshot instant from our own events, not from trusting either side.</p>`;
}

function valueChart(d) {
  const kinds = KIND_ORDER.filter((k) => d.items.some((i) => i.kind === k));
  const cats = d.echelons.map((e) => e.label);
  const series = kinds.map((k, si) => ({
    name: KIND_LABEL[k],
    values: d.echelons.map((e) => Math.round(d.items.filter((i) => i.kind === k)
      .reduce((a, i) => a + (i.by[e.id] || 0) * (i.std_cost || 0), 0))),
    colorIndex: si,
  }));
  return charts.bar({
    categories: cats, series, stacked: true, horizontal: true, labelWidth: 170, totals: true,
    yFormat: (v) => fmt.usd(v, { compact: true }), categoryLabel: 'Echelon', ariaLabel: 'Inventory value by echelon',
  });
}

// ---------------------------------------------------------------------------
async function openItem(id) {
  if (!S) return;
  S.ctx.setQuery({ item: id }, { silent: true });
  const body = ui.drawer.open({ title: id, subtitle: 'Loading…', body: ui.loading('Loading item'), width: 640,
    onClose: () => { if (S) S.ctx.setQuery({ item: null }, { silent: true }); } });
  let r;
  try {
    r = await api.get('/api/inventory/item', { id });
  } catch (err) {
    body.innerHTML = String(ui.errorBox(err));
    return;
  }
  const it = r.item;
  const row = S.d.items.find((x) => x.item_id === id);
  const ech = Object.fromEntries(S.d.echelons.map((e) => [e.id, e]));
  ui.drawer.open({
    title: `${it.item_id} · ${it.name}`,
    subtitle: html`${fmt.title(it.kind)} · ${fmt.title(it.make_buy)} ${it.lifecycle !== 'ACTIVE' ? ui.statusChip(it.lifecycle) : ''}`,
    width: 640,
    onClose: () => { if (S) S.ctx.setQuery({ item: null }, { silent: true }); },
    body: html`
      <dl class="kv">
        <dt>Supplier</dt><dd>${it.primary_supplier_id ? link.supplier(it.primary_supplier_id, `${it.supplier_name} · tier ${it.tier}`) : 'OEM-built'}</dd>
        <dt>Standard cost</dt><dd>${fmt.usd(it.std_cost, { cents: true })} / ${it.uom}</dd>
        <dt>Lead time · MOQ</dt><dd>${it.lead_time_days != null ? `${it.lead_time_days} d` : '—'} · ${it.moq != null ? fmt.int(it.moq) : '—'}${it.order_multiple ? ` (×${fmt.int(it.order_multiple)})` : ''}</dd>
        <dt>Safety stock</dt><dd>${it.safety_stock != null ? fmt.int(it.safety_stock) : '—'}</dd>
        <dt>Need per day</dt><dd>${row && row.daily_use != null ? html`${fmt.num(row.daily_use, 1)} <span class="muted small">(${row.use_basis})</span>` : '—'}</dd>
        <dt>Cover</dt><dd>${row && row.dos != null ? ui.chip(coverTone(row), `${fmt.num(row.dos, 1)} days`) : '—'}</dd>
      </dl>
      <h4 class="section-title">Where it is</h4>
      ${r.positions.length ? html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Echelon</th><th>Bucket</th><th>Site</th><th class="num">Qty</th></tr></thead>
        <tbody>${r.positions.map((p) => html`<tr><td>${ech[p.echelon] ? ech[p.echelon].label : p.echelon}</td><td>${p.bucket}</td>
          <td class="mono small">${p.site_id || '—'}</td><td class="num">${fmt.int(p.qty)}</td></tr>`)}</tbody></table></div>` : ui.empty('No stock anywhere.')}
      ${r.open_po.length ? html`<h4 class="section-title">Open purchase order lines</h4>
        <div class="table-wrap"><table class="table dense"><thead><tr><th>PO line</th><th>Ship to</th><th class="num">Open</th><th>Need</th><th>Promise</th><th>Status</th></tr></thead>
        <tbody>${r.open_po.map((p) => html`<tr><td>${link.po(p.po_id, p.line_no)}</td><td class="mono small">${p.ship_to_site_id}</td>
          <td class="num">${fmt.int(p.qty - p.received_qty)}</td><td>${fmt.date(p.need_date)}</td>
          <td>${p.promise_date ? html`${fmt.date(p.promise_date)}${p.promise_date > p.need_date ? html` <span class="inv-late">+${Math.round((Date.parse(p.promise_date) - Date.parse(p.need_date)) / 86400000)}d</span>` : ''}` : html`<span class="muted">—</span>`}</td>
          <td>${ui.statusChip(p.confirm_status)}</td></tr>`)}</tbody></table></div>` : ''}
      ${r.lots.length ? html`<h4 class="section-title">Recent lots</h4>
        <div class="table-wrap"><table class="table dense"><thead><tr><th>Lot</th><th>Site</th><th class="num">Received</th><th class="num">On hand</th><th>IQC</th></tr></thead>
        <tbody>${r.lots.map((l) => html`<tr><td>${link.lot(l.lot_id)}</td><td class="mono small">${l.site_id}</td><td class="num">${fmt.int(l.qty_received)}</td>
          <td class="num">${l.on_hand != null ? fmt.int(l.on_hand) : html`<span class="muted">—</span>`}</td><td>${ui.statusChip(l.iqc_status)}</td></tr>`)}</tbody></table></div>` : ''}
      ${r.units.length ? html`<h4 class="section-title">Serialized units (sample)</h4>
        <div class="inv-units">${r.units.map((u) => html`<span class="inv-unit">${link.serial(u.serial)} ${ui.statusChip(u.on_hold ? 'HOLD' : u.status)}</span>`)}</div>` : ''}
      ${r.policies.length ? html`<h4 class="section-title">Replenishment policy</h4>
        ${r.policies.map((p) => html`<p class="small">${ui.chip('info', fmt.title(p.policy), { icon: false })} at <span class="mono">${p.site_id}</span> ·
          owner ${p.owner} · LT ${fmt.num(p.lead_time_days, 0)} d · SS ${fmt.int(p.safety_stock)}${p.reorder_point != null ? ` · ROP ${fmt.int(p.reorder_point)}` : ''}${p.max_qty != null ? ` · max ${fmt.int(p.max_qty)}` : ''}
          <a class="ent-link" href="#/replenishment">Replenishment →</a></p>`)}` : ''}`,
  });
}

const PAGE_CSS = `
.pg-inventory .inv-route { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 10px; margin: -4px 0 16px; position: relative; }
.pg-inventory .inv-route::before { content: ""; position: absolute; left: 14px; right: 14px; top: 23px; height: 3px; border-radius: 2px; background: var(--sign); opacity: .8; }
.pg-inventory .inv-stop { position: relative; min-width: 0; display: grid; gap: 3px; align-content: start; padding: 10px 11px 11px; background: var(--surface); border: 1px solid var(--hairline); border-radius: 10px; box-shadow: var(--shadow-sm); }
.pg-inventory .inv-stop-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.pg-inventory .inv-num { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; background: var(--sign); color: var(--sign-ink); font: 600 11.5px var(--font-cond); box-shadow: 0 0 0 3px var(--surface); }
.pg-inventory .inv-stop .chip { height: 19px; font-size: 10.5px; padding: 0 6px; }
.pg-inventory .inv-stop-label { font: 600 13.5px/1.2 var(--font-cond); margin-top: 4px; }
.pg-inventory .inv-stop-val { font: 600 21px/1.1 var(--font-ui); letter-spacing: -.01em; }
.pg-inventory .inv-stop-sub { font-size: 11.5px; color: var(--ink-3); }
.pg-inventory .inv-buckets { list-style: none; margin: 4px 0 0; padding: 6px 0 0; border-top: 1px solid var(--hairline); display: grid; gap: 2px; font-size: 11.5px; color: var(--ink-2); }
.pg-inventory .inv-buckets li { display: flex; justify-content: space-between; gap: 6px; min-width: 0; }
.pg-inventory .inv-buckets .num { color: var(--ink); font-weight: 500; }
.pg-inventory .inv-insights { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; margin: 16px 0; }
.pg-inventory .inv-name { max-width: 190px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pg-inventory .inv-pad { padding: 10px 18px 14px; display: flex; gap: 6px; align-items: flex-start; }
.pg-inventory .inv-pad svg { flex: none; margin-top: 2px; color: var(--info); }
.drawer .inv-late { color: var(--delta-bad); font-weight: 600; font-size: 12px; }
.drawer .inv-units { display: flex; flex-wrap: wrap; gap: 6px 12px; }
.drawer .inv-unit { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; }
@media (max-width: 1280px) { .pg-inventory .inv-route { grid-template-columns: repeat(4, minmax(0, 1fr)); } .pg-inventory .inv-route::before { display: none; } }
@media (max-width: 640px) { .pg-inventory .inv-route { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
`;
