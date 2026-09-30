// Genealogy: type any serial, lot or order. A unit shows its as-built tree (and the
// as-maintained history of swapped parts) down to tier-2/3 material lots, plus its
// life across every system. A lot or batch shows everything made from it and the
// recall scope, split by what is still in our control.
import { html, raw, esc, on, injectStyle, $ } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const KIND_ICON = { VEHICLE: 'bolt', PACK: 'layers', MODULE: 'boxes', COMPONENT: 'plug', MATERIAL: 'flask', KIT: 'boxes' };
const SOURCE_LABEL = { CM_FEED: 'CM MES', OEM_MES: 'OEM MES', SUPPLIER_ASN: 'Supplier ASN', '3PL_FEED': '3PL WMS',
  SERVICE: 'Service', SUPPLIER_COA: 'Supplier COA' };
let OFFS = [];

export function unmount() {
  OFFS.forEach((off) => off());
  OFFS = [];
}

export async function render(el, ctx) {
  injectStyle('page-genealogy', PAGE_CSS);
  unmount();
  const q = (ctx.query.get('q') || '').trim();
  const history = ctx.query.get('history') === '1';
  const ex = await api.get('/api/genealogy/examples');
  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-gen">
      <form class="pg-search" data-search>
        <span class="pg-search-ic">${icon('search', 17)}</span>
        <input class="input" name="q" value="${q}" placeholder="Serial, lot or order: LV1-26W31-0412, CL2606-104, CA2605-103, SO-100245" autocomplete="off" spellcheck="false">
        <button class="btn btn-primary" type="submit">Trace</button>
      </form>
      <div class="pg-examples"><span class="muted small">Try:</span>${ex.examples.map((x) => html`<button class="pg-ex" type="button" data-q="${x.q}" title="${x.why}">
        <b>${x.label}</b><span class="mono">${x.q}</span></button>`)}</div>
      <div data-result>${q ? ui.loading('Tracing') : intro()}</div>
    </div>`;
  OFFS.push(on(el, 'submit', '[data-search]', (e) => {
    e.preventDefault();
    const v = e.target.q.value.trim();
    if (v) ctx.setQuery({ q: v, history: null });
  }));
  OFFS.push(on(el, 'click', '[data-q]', (e, t) => ctx.setQuery({ q: t.dataset.q, history: null })));
  OFFS.push(on(el, 'change', '[data-history]', (e, t) => ctx.setQuery({ history: t.checked ? '1' : null })));
  OFFS.push(on(el, 'click', '[data-toggle-node]', (e, t) => { t.closest('li').classList.toggle('collapsed'); }));
  if (!q) return;
  const box = $('[data-result]', el);
  try {
    const hit = await api.get('/api/genealogy/resolve', { q });
    if (!hit || !hit.kind) {
      box.innerHTML = ui.empty(`Nothing matches “${q}”. Serials look like LV1-26W31-0412 or PKS-26W31-0412; lots like CL2606-104.`).toString();
      return;
    }
    if (hit.kind === 'unit') {
      const d = await api.get(`/api/genealogy/unit/${encodeURIComponent(hit.id)}`, history ? { history: 1 } : {});
      box.innerHTML = unitView(d, history, hit.via_order).toString();
    } else if (hit.kind === 'lot') {
      const d = await api.get(`/api/genealogy/lot/${encodeURIComponent(hit.id)}`);
      box.innerHTML = lotView(d).toString();
      const t = $('[data-incontrol]', box);
      if (t) {
        ui.dataTable(t, {
          rows: d.scope.in_control_units, pageSize: 10, search: true, dense: true,
          columns: [
            { key: 'serial', label: 'Serial', render: (r) => link.serial(r.serial) },
            { key: 'kind', label: 'Kind', render: (r) => fmt.title(r.kind) },
            { key: 'item_id', label: 'Item', render: (r) => html`<span class="mono">${r.item_id}</span>` },
            { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.on_hold ? 'HOLD' : r.status) },
            { key: 'location_site_id', label: 'Where', render: (r) => r.location_site_id || 'In transit' },
          ],
        });
      }
    }
  } catch (err) {
    box.innerHTML = ui.errorBox(err).toString();
  }
}

function intro() {
  return ui.callout({
    tone: 'info', title: 'One trace across four owners',
    body: html`The CM's MES (Taiwan) reports what went into each vehicle. Supplier ASNs report what is inside each drive unit.
      Kestrel's certificates report which cathode batch each cell lot came from. The 3PL reports which pack was married to which
      vehicle, and service records every swap. Genealogy stitches them into one tree with a recursive query, so a single lot
      question comes back in milliseconds.`,
  });
}

// ---------------------------------------------------------------- unit

function unitView(d, history, viaOrder) {
  const u = d.unit;
  let eol = null;
  try { eol = d.eol ? JSON.parse(d.eol) : null; } catch (e) { eol = null; }
  const asBuiltDu = (d.tree.children.find((c) => c.position === 'DRIVE_UNIT' && !c.removed_at) || {}).id;
  const mismatch = eol && eol.du_sn && asBuiltDu && eol.du_sn !== asBuiltDu;
  const order = d.orders[0];
  return html`
    ${viaOrder ? html`<p class="muted small">Order ${link.order(viaOrder)} → its vehicle:</p>` : ''}
    <section class="card pg-unit-head">
      <div class="pg-unit-id">
        <span class="pg-unit-ic">${icon(KIND_ICON[u.item_kind] || 'boxes', 22)}</span>
        <div><div class="pg-serial">${u.serial}</div><div class="muted">${u.item_name} · <span class="mono">${u.item_id}</span></div></div>
      </div>
      <div class="pg-unit-facts">
        <div><span>Status</span>${ui.statusChip(u.status)} ${u.on_hold ? ui.chip('critical', 'On hold') : ''}</div>
        <div><span>Where</span>${u.location_name || (u.status === 'DELIVERED' ? 'With the customer' : u.status === 'IN_TRANSIT' ? 'In transit' : '—')}</div>
        <div><span>Source</span>${SOURCE_LABEL[u.origin] || u.origin}${u.origin === 'PROVISIONAL' ? html` ${ui.chip('warning', 'No ASN yet')}` : ''}</div>
        ${u.built_at ? html`<div><span>Built</span>${fmt.dt(u.built_at)}${u.line ? html` · line ${u.line}` : ''}</div>` : ''}
        ${u.wo_id ? html`<div><span>Work order</span><span class="mono">${u.wo_id}</span></div>` : ''}
        ${u.firmware ? html`<div><span>Firmware</span><span class="mono">${u.firmware}</span></div>` : ''}
        ${u.supplier_id ? html`<div><span>Supplier</span>${link.supplier(u.supplier_id)}${u.asn_no ? html` · <span class="mono">${u.asn_no}</span>` : ''}</div>` : ''}
        ${order ? html`<div><span>Order</span>${link.order(order.order_id)} · ${fmt.title(order.status)} · ship to ${order.ship_to_state}</div>` : ''}
      </div>
    </section>
    ${mismatch ? ui.callout({ tone: 'critical', title: 'The as-built record disagrees with the end-of-line test',
      body: html`The S60 tester read drive unit <span class="mono">${eol.du_sn}</span> over CAN, but genealogy says
        <span class="mono">${asBuiltDu}</span> is installed. The swap happened in the CM's new rework bay (S65), whose messages are
        quarantined. ${link.route('loop', 'The data loop fixes it')} and contract C-GEN-05 proves it.` }) : ''}
    ${d.holds.filter((h) => !h.released_at).map((h) => ui.callout({ tone: 'critical', title: `On hold since ${fmt.dt(h.placed_at)}`, body: h.reason }))}
    <div class="grid">
      <div class="span-7">${ui.card({
        title: 'As built', subtitle: history ? 'Including parts removed in rework or service (struck through)' : 'Active parts, down to supplier material lots',
        actions: html`<label class="pg-hist"><input type="checkbox" data-history ${history ? raw('checked') : ''}> Show removed parts</label>`,
        body: html`<ul class="pg-tree root">${d.tree.children.map((n) => node(n))}</ul>
          ${d.removed.length && !history ? html`<p class="small muted">${d.removed.length} part(s) were swapped out over this unit's life. Tick “Show removed parts”.</p>` : ''}`,
      })}
      ${d.parents.length ? ui.card({ title: 'Where used', subtitle: 'The units this one sits inside',
        body: html`<ul class="pg-parents">${d.parents.map((p) => html`<li>${icon(KIND_ICON[p.kind] || 'boxes', 15)} ${link.serial(p.serial)}
          <span class="muted small">${fmt.title(p.relation)} · ${p.position || ''} · ${fmt.title(p.status)}</span></li>`)}</ul>` }) : ''}
      <details class="pg-sql"><summary>The query behind this tree</summary>${ui.codeBlock(d.sql, 'sql')}</details>
      </div>
      <div class="span-5">${ui.card({
        title: 'Life story', subtitle: 'Every system that touched it, in time order',
        body: ui.timeline(d.timeline.map((e) => ({ ts: e.ts, meta: e.system, title: e.title, detail: e.detail, tone: e.tone }))),
      })}</div>
    </div>`;
}

function node(n) {
  const removed = !!n.removed_at;
  const isLot = n.kind === 'lot';
  const label = isLot ? link.lot(n.id) : link.serial(n.id);
  const meta = [
    n.qty && n.qty !== 1 ? `× ${fmt.num(n.qty, n.qty % 1 ? 2 : 0)}` : null,
    n.supplier_id ? n.supplier_id : null,
    n.station_id ? n.station_id.replace('TXG-', '').replace('FRE-', '') : null,
    n.installed_at ? fmt.date(n.installed_at) : null,
    n.mfg_date ? `mfg ${fmt.date(n.mfg_date)}` : null,
  ].filter(Boolean);
  return html`<li class="${removed ? 'removed' : ''} ${n.children.length ? 'has-kids' : ''}">
    <div class="pg-node">
      ${n.children.length ? html`<button class="pg-caret" type="button" data-toggle-node aria-label="Toggle">${icon('chevron-down', 14)}</button>` : html`<span class="pg-caret-sp"></span>`}
      <span class="pg-node-ic">${icon(isLot ? (n.relation === 'MADE_FROM' ? 'flask' : 'layers') : (KIND_ICON[n.item_kind] || 'boxes'), 15)}</span>
      <span class="pg-pos">${(n.position || '').replace(/_/g, ' ')}</span>
      <span class="pg-id">${label}</span>
      <span class="pg-name">${n.item_name}</span>
      <span class="pg-meta">${meta.join(' · ')}</span>
      <span class="pg-src">${SOURCE_LABEL[n.source] || n.source || ''}</span>
      ${n.on_hold ? ui.chip('critical', 'Hold') : ''}
      ${n.origin === 'PROVISIONAL' ? ui.chip('warning', 'No ASN') : ''}
      ${removed ? html`<span class="pg-removed">removed ${fmt.date(n.removed_at)}: ${n.removal_reason || ''}</span>` : ''}
    </div>
    ${n.children.length ? html`<ul class="pg-tree">${n.children.map((c) => node(c))}</ul>` : ''}
  </li>`;
}

// ---------------------------------------------------------------- lot / batch

function lotView(d) {
  const l = d.lot;
  const s = d.scope;
  const claimsBy = {};
  const valid = s.claims.filter((c) => c.status !== 'REJECTED');
  for (const c of valid) claimsBy[c.failed_lot_id || '—'] = (claimsBy[c.failed_lot_id || '—'] || 0) + 1;
  const rejected = s.claims.length - valid.length;
  const batch = d.downstream.length > 0;
  const statusRows = s.by_status;
  return html`
    <section class="card pg-unit-head">
      <div class="pg-unit-id">
        <span class="pg-unit-ic">${icon(batch ? 'flask' : 'layers', 22)}</span>
        <div><div class="pg-serial">${l.lot_id}</div>
          <div class="muted">${l.item_name} · ${link.supplier(l.supplier_id, l.supplier_name)} · tier ${l.tier}</div></div>
      </div>
      <div class="pg-unit-facts">
        <div><span>Quantity</span>${fmt.num(l.qty_received, 0)}</div>
        <div><span>Received</span>${fmt.dt(l.received_at)} · ${l.site_name}</div>
        ${l.mfg_date ? html`<div><span>Made</span>${fmt.dateLong(l.mfg_date)}</div>` : ''}
        <div><span>Incoming QC</span>${ui.statusChip(l.iqc_status)}</div>
        <div><span>Used in</span>${fmt.int(s.units_total)} units (${fmt.int(s.packs)} packs, ${fmt.int(s.vehicles)} vehicles)</div>
      </div>
    </section>
    ${d.decision ? ui.callout({
      tone: d.decision.status === 'EXECUTED' ? 'good' : 'serious',
      title: d.decision.status === 'EXECUTED' ? `Contained by ${d.decision.decision_id}` : `Containment proposed: ${d.decision.decision_id}`,
      body: html`${d.decision.title}. <a class="ent-link" href="#/loop?d=${d.decision.decision_id}">${d.decision.status === 'PROPOSED' ? 'Review and decide' : 'See what it wrote'} →</a>`,
    }) : ''}
    <div class="kpi-row">
      ${ui.kpi({ label: 'Units built from it', value: fmt.int(s.top_level), hint: `${fmt.int(s.units_total)} including sub-assemblies` })}
      ${ui.kpi({ label: 'Still in our control', value: fmt.int(s.in_control), hint: s.orders_in_control ? `3PL, Fremont, in transit · ${fmt.int(s.orders_in_control)} customer orders allocated to them` : '3PL, Fremont, in transit', status: s.in_control ? { tone: 'serious', label: 'Stoppable' } : null })}
      ${ui.kpi({ label: 'With customers', value: fmt.int(s.with_customer), hint: `${fmt.int(s.orders_with_customer)} orders` })}
      ${ui.kpi({ label: 'On hold', value: fmt.int(s.on_hold) })}
      ${ui.kpi({ label: 'Field claims', value: fmt.int(valid.length), hint: Object.entries(claimsBy).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k}: ${v}`).concat(rejected ? [`${rejected} rejected, not counted`] : []).join(' · ') })}
    </div>
    <div class="grid">
      <div class="span-5">
        ${d.upstream.length ? ui.card({ title: 'Made from (upstream)', subtitle: 'Supplier certificates link lots across tiers',
          body: html`<ol class="pg-chain">${d.upstream.map((u) => html`<li>${icon('flask', 15)} ${link.lot(u.lot_id)}
            <span class="muted small">${u.name} · ${u.supplier_name} (tier ${u.tier}) · ${fmt.num(u.qty_received, 0)} ${u.uom}</span></li>`)}</ol>` }) : ''}
        ${batch ? ui.card({ title: 'Capacity-fade claims per 1,000 pack-months in service, by cell lot from this batch',
          subtitle: 'Per month in service, so young lots are judged on the time they have had. A lot with little time in service can show zero and still carry the defect.',
          tableToggle: true,
          body: charts.bar({
            height: 200, horizontal: true, categories: d.downstream.map((x) => `${x.lot_id} · ${fmt.int(Math.round(x.pack_months || 0))} pack-mo`),
            series: [{ name: 'Claims per 1,000 pack-months', values: d.downstream.map((x) => x.rate || 0) }],
            yFormat: (v) => fmt.num(v, 1), valueLabels: true,
          }) }) : ''}
        ${ui.card({ title: 'Where it is now', tableToggle: true, body: charts.bar({
          height: 200, horizontal: true,
          categories: statusRows.map((r) => `${fmt.title(r.status)} · ${fmt.title(r.kind)}`),
          series: [{ name: 'Units', values: statusRows.map((r) => r.n) }], yFormat: (v) => fmt.int(v),
        }) })}
      </div>
      <div class="span-7">
        ${ui.card({ title: 'In our control: stoppable today', subtitle: 'Top-level units only; packs already married to a vehicle count with the vehicle',
          flush: true, body: html`<div data-incontrol></div>` })}
        ${s.claims.length ? ui.card({ title: 'Field claims traced here', flush: true, body: html`<div class="table-wrap"><table class="table dense">
          <thead><tr><th>Claim</th><th>Reported</th><th>Vehicle</th><th>Symptom</th><th>Cell lot</th><th class="num">Cost</th><th>Recovery</th></tr></thead>
          <tbody>${s.claims.slice(0, 40).map((c) => html`<tr><td>${link.claim(c.claim_id)}</td><td class="mono small">${fmt.date(c.reported_at)}</td>
            <td>${link.serial(c.serial)}</td><td class="small pg-wrap">${c.symptom}</td><td>${c.failed_lot_id ? link.lot(c.failed_lot_id) : '—'}</td>
            <td class="num">${fmt.usd(c.cost_usd)}</td><td>${c.chargeback_id ? html`<a class="id-link" href="#/warranty?cb=${c.chargeback_id}">${c.chargeback_id}</a>` : html`<span class="muted small">unbilled</span>`}</td></tr>`)}</tbody>
        </table></div>` }) : ''}
        <details class="pg-sql"><summary>The forward-trace query (lot links, then genealogy, recursively)</summary>${ui.codeBlock(d.sql, 'sql')}</details>
      </div>
    </div>`;
}

const PAGE_CSS = `
.pg-gen { display: grid; gap: 14px; }
.pg-gen .grid { margin: 0; }
.pg-search { display: flex; gap: 8px; align-items: center; position: relative; }
.pg-search .input { flex: 1; height: 42px; padding-left: 38px; font: 500 15px/1 var(--font-mono); }
.pg-search-ic { position: absolute; left: 12px; color: var(--ink-3); display: inline-flex; }
.pg-search .btn { height: 42px; }
.pg-examples { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.pg-ex { display: grid; gap: 1px; text-align: left; padding: 6px 10px; border-radius: 9px; border: 1px solid var(--hairline);
  background: var(--surface); cursor: pointer; font: inherit; color: var(--ink); }
.pg-ex:hover { border-color: var(--sign); }
.pg-ex b { font: 600 12px/1.2 var(--font-cond); letter-spacing: .02em; }
.pg-ex .mono { font-size: 11.5px; color: var(--link); }
.pg-unit-head { display: grid; grid-template-columns: minmax(240px, 320px) 1fr; gap: 18px; padding: 16px 18px; align-items: start; }
.pg-unit-id { display: flex; gap: 12px; align-items: center; }
.pg-unit-ic { display: inline-flex; width: 44px; height: 44px; border-radius: 10px; align-items: center; justify-content: center;
  background: var(--sign); color: var(--sign-ink); flex: none; }
.pg-serial { font: 600 20px/1.2 var(--font-mono); letter-spacing: -.01em; }
.pg-unit-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px 16px; font-size: 13px; }
.pg-unit-facts > div { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pg-unit-facts > div > span:first-child { color: var(--ink-3); font: 600 11px/1 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; min-width: 74px; }
.pg-hist { display: inline-flex; gap: 6px; align-items: center; font-size: 12.5px; color: var(--ink-2); }
.pg-tree { list-style: none; margin: 0; padding: 0 0 0 18px; border-left: 1px solid var(--hairline); }
.pg-tree.root { padding-left: 0; border-left: 0; }
.pg-tree li { margin: 2px 0; }
.pg-tree li.collapsed > ul { display: none; }
.pg-tree li.collapsed > .pg-node .pg-caret { transform: rotate(-90deg); }
.pg-node { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; padding: 4px 6px; border-radius: 7px; font-size: 13px; }
.pg-node:hover { background: var(--row-hover); }
.pg-caret { border: 0; background: none; padding: 0; color: var(--ink-3); cursor: pointer; display: inline-flex; transition: transform .12s; }
.pg-caret-sp { width: 14px; }
.pg-node-ic { color: var(--ink-2); display: inline-flex; }
.pg-pos { font: 600 10.5px/1 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); min-width: 72px; }
.pg-name { color: var(--ink-2); }
.pg-meta { color: var(--ink-3); font: 400 11.5px/1.3 var(--font-mono); }
.pg-src { font-size: 11px; padding: 1px 6px; border-radius: 999px; background: var(--surface-2); color: var(--ink-3); border: 1px solid var(--hairline); }
li.removed > .pg-node { opacity: .72; }
li.removed > .pg-node .pg-id, li.removed > .pg-node .pg-name { text-decoration: line-through; }
.pg-removed { font-size: 11.5px; color: var(--serious); width: 100%; padding-left: 42px; }
.pg-parents, .pg-chain { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.pg-parents li, .pg-chain li { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pg-sql { margin-top: 4px; }
.pg-wrap { white-space: normal; min-width: 180px; }
.pg-sql summary { cursor: pointer; font-size: 12.5px; color: var(--ink-2); margin-bottom: 8px; }
@media (max-width: 800px) { .pg-unit-head { grid-template-columns: 1fr; } .pg-pos { min-width: 0; } }
`;
