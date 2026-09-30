// Shipments & Customs (TMS): the physical pipeline from the CM in Taichung to the
// customer's door, and from the Fremont pack line to the 3PL. It covers ETA risk,
// customs entries (ISF timing, assists, duty), the last mile against both carrier
// SLA and customer promise, DG trucks, inbound supplier legs and freight spend.
import { html, raw, esc, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const TABS = [
  { id: 'pipeline', label: 'Pipeline & ocean', icon: 'ship' },
  { id: 'customs', label: 'Customs', icon: 'shield' },
  { id: 'lastmile', label: 'Last mile', icon: 'route' },
  { id: 'trucks', label: 'DG trucks & inbound', icon: 'truck' },
  { id: 'freight', label: 'Freight spend', icon: 'receipt' },
];
const FLAG = {
  DELAYED: ['serious', 'ETA slipped'], CUSTOMS_HOLD: ['serious', 'Customs exam'], SHORT: ['critical', 'Short vs ASN'],
  LATE_ISF: ['warning', 'Late ISF'],
};
const CARRIER = { PLL: 'Pacific Link Lines', CWF: 'Crossway Freight', PPG: 'ParcelPro Ground', SDG: 'Sierra DG Freight', SKA: 'SkyAxis Air', BDR: 'Bayside Drayage' };
const REGION = { WEST: 'West', MOUNTAIN: 'Mountain', CENTRAL: 'Central', EAST: 'East' };
const LANE_LABEL = { ocean: 'Ocean', drayage: 'Drayage', dg_truck: 'DG truck', last_mile: 'Last mile' };

let S = null;

const flagChips = (flags) => (flags && flags.length
  ? html`<span class="sh-flags">${flags.map((f) => ui.chip(FLAG[f][0], FLAG[f][1]))}</span>` : html`<span class="nil">—</span>`);

export async function render(el, ctx) {
  injectStyle('page-shipments', PAGE_CSS);
  const ov = await api.get('/api/shipments/overview');
  S = { el, ctx, ov, cache: {}, off: [] };
  const k = ov.kpis;
  let tab = ctx.query.get('tab');
  if (!TABS.some((t) => t.id === tab)) tab = 'pipeline';

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-shipments">
      <div class="kpi-row">
        ${ui.kpi({ label: 'On the water', value: fmt.int(k.units_on_water), unit: 'vehicles', hint: `${k.containers_on_water} containers` })}
        ${ui.kpi({ label: 'ETA slipped', value: fmt.int(k.delayed_units), unit: 'vehicles', hint: k.delayed_units ? `up to ${fmt.num(k.delay_days, 0)} days behind the original ETA` : 'none' })}
        ${ui.kpi({ label: 'Customs holds', value: fmt.int(k.customs_holds), unit: k.customs_holds === 1 ? 'container' : 'containers', hint: 'CBP exam at Oakland', status: k.customs_holds ? { tone: 'serious', label: 'Held' } : null })}
        ${ui.kpi({ label: 'Short vs ASN', value: fmt.int(k.short_units), unit: 'vehicles', hint: 'on the CM\'s ASN, never received', status: k.short_units ? { tone: 'critical', label: 'Recon' } : null })}
        ${ui.kpi({ label: 'Last mile in flight', value: fmt.int(k.last_mile_in_flight), hint: `${fmt.int(k.delivered_7d)} delivered in 7 days · ${fmt.pct(k.last_mile_sla, 1)} within carrier SLA` })}
        ${ui.kpi({ label: 'Freight per vehicle', value: fmt.usd(k.freight_per_vehicle), hint: `${fmt.usd(k.freight_30d, { compact: true })} spent in 30 days` })}
      </div>
      <div class="sh-tabs"></div>
      <div class="sh-body"></div>
    </div>`;
  S.tabs = ui.tabs($('.sh-tabs', el), {
    tabs: TABS, active: tab,
    onChange: (id) => { ctx.setQuery({ tab: id }, { silent: true }); showTab(id); },
  });
  const onDocClick = (e) => {
    const a = e.target.closest && e.target.closest('a[href^="#/shipments?id="]');
    if (!a) return;
    e.preventDefault();
    openShipment(new URLSearchParams(a.getAttribute('href').split('?')[1]).get('id'));
  };
  document.addEventListener('click', onDocClick);
  S.off.push(() => document.removeEventListener('click', onDocClick));
  await showTab(tab);
  if (ctx.query.get('id')) openShipment(ctx.query.get('id'));
}

export function unmount() {
  if (S) S.off.forEach((f) => f());
  S = null;
}

async function showTab(id) {
  const outer = $('.sh-body', S.el);
  outer.innerHTML = String(ui.loading());
  const body = document.createElement('div');
  try {
    if (id === 'pipeline') await tabPipeline(body);
    else if (id === 'customs') await tabCustoms(body);
    else if (id === 'lastmile') await tabLastMile(body);
    else if (id === 'trucks') await tabTrucks(body);
    else if (id === 'freight') await tabFreight(body);
    if (!S) return;
    outer.innerHTML = '';
    outer.appendChild(body);
  } catch (err) {
    console.error(err);
    outer.innerHTML = String(ui.errorBox(err));
  }
}

// ---------------------------------------------------------------------------
// Lane map: a schematic of the whole pipeline with live counts per segment
// ---------------------------------------------------------------------------
function laneMap(ov) {
  const L = ov.lanes;
  const delayed = ov.vessels.filter((v) => (v.delay_days || 0) >= 1);
  const inbound = ov.inbound || [];
  const W = 1140;
  const H = 356;
  const ctr = (n) => `${n} container${n === 1 ? '' : 's'}`;
  let s = '';
  const node = (x, y, w, h, { title, big, unit, lines = [], tone, tip, href }) => {
    const cls = `lm-node${tone ? ' tone-' + tone : ''}${href ? ' lm-link' : ''}`;
    let g = `<g class="${cls}"${tip ? ` data-tip="${esc(tip)}"` : ''}${href ? ` data-href="${esc(href)}"` : ''}>`;
    g += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>`;
    if (tone) g += `<rect class="lm-bar" x="${x}" y="${y + 10}" width="3" height="${h - 20}" rx="1.5"/>`;
    g += `<text class="lm-title" x="${x + 12}" y="${y + 20}">${esc(title)}</text>`;
    if (big != null) g += `<text class="lm-big" x="${x + 12}" y="${y + 50}">${esc(big)}<tspan class="lm-unit" dx="4">${esc(unit || '')}</tspan></text>`;
    lines.forEach((ln, i) => { g += `<text class="lm-sub" x="${x + 12}" y="${y + (big != null ? 70 : 42) + i * 15}">${esc(ln)}</text>`; });
    return g + '</g>';
  };
  const arrow = (x1, y1, x2, y2, label, { dashed } = {}) => {
    let a = `<path class="lm-edge${dashed ? ' dashed' : ''}" d="M${x1},${y1} L${x2 - 7},${y2}" marker-end="url(#lm-arrow)"/>`;
    if (label) a += `<text class="lm-edge-label" x="${(x1 + x2) / 2}" y="${Math.min(y1, y2) - 6}" text-anchor="middle">${esc(label)}</text>`;
    return a;
  };
  s += `<defs><marker id="lm-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="lm-arrowhead"/></marker></defs>`;
  // lane labels
  s += `<text class="lm-lane" x="10" y="18">VEHICLES · CM-BUILT IN TAICHUNG</text>`;
  s += `<text class="lm-lane" x="10" y="218">BATTERY PACKS · OEM-BUILT IN FREMONT · AND THE PARTS THAT FEED IT</text>`;
  const y1 = 32, h1 = 118;
  // main row (22px gaps carry arrows only; transit times live in the node text)
  s += node(10, y1, 150, h1, { title: 'Taichung CM', big: fmt.int(L.cm_wip + L.cm_dock), unit: 'units', lines: [`${fmt.int(L.cm_wip)} in WIP`, `${fmt.int(L.cm_dock)} built, awaiting ctr`], tip: 'Formosa Assembly Partners, Taichung\nBuilt vehicles wait at the CM dock until a container fills (150 per 40HC)', href: '#/production' });
  s += arrow(160, y1 + 60, 182, y1 + 60);
  s += node(182, y1, 140, h1, { title: 'Port of Taichung', big: fmt.int(L.port_origin), unit: 'units', lines: [`${ctr(L.port_origin_ctr)} gated in`, 'awaiting vessel'] });
  s += arrow(322, y1 + 60, 344, y1 + 60);
  s += node(344, y1, 176, h1, {
    title: 'Pacific · ~15 day crossing', big: fmt.int(L.ocean), unit: 'units', tone: delayed.length ? 'serious' : null,
    lines: [`${ctr(L.ocean_ctr)} on ${ov.vessels.length} vessel${ov.vessels.length === 1 ? '' : 's'}`,
      ...delayed.slice(0, 2).map((v) => `${v.vessel} ${v.voyage}: +${fmt.num(v.delay_days, 0)}d`)],
    tip: ov.vessels.map((v) => `${v.vessel} ${v.voyage} · ${v.containers} ctr · ${v.units} units\nETA ${fmt.date(v.eta_current)}${v.delay_days ? ` (planned ${fmt.date(v.eta_planned)})` : ''}`).join('\n'),
  });
  s += arrow(520, y1 + 60, 542, y1 + 60);
  s += node(542, y1, 150, h1, { title: 'Oakland · CBP', big: fmt.int(L.port_dest), unit: 'units', tone: L.customs_hold_ctr ? 'serious' : null, lines: [`${ctr(L.port_dest_ctr)} at port`, L.customs_hold_ctr ? `${L.customs_hold_ctr} held for exam` : 'no holds'], tip: 'Discharged at Oakland; ISF and entry filed by the broker\nCustoms exam holds the container at the port' });
  s += arrow(692, y1 + 60, 714, y1 + 60);
  s += node(714, y1, 104, h1, { title: 'Drayage', big: fmt.int(L.drayage), unit: 'units', lines: [ctr(L.drayage_ctr), '1 day to Reno'] });
  s += arrow(818, y1 + 60, 840, y1 + 60);
  // Reno spans both rows
  s += node(840, y1, 150, 300, { title: 'Sierra 3PL · Reno', big: fmt.int(L.reno_vehicles), unit: 'vehicles', tone: L.reno_hold ? 'warning' : null, lines: [`${fmt.int(L.reno_packs)} packs`, `${fmt.int(L.reno_hold)} units on hold`, L.short_units ? `${L.short_units} short vs ASN` : '', '', 'Kitting: vehicle + pack', '+ charger = sellable LV-1'].filter((x) => x !== null), tip: 'Receives containers and DG trucks, kits orders FIFO by priority\nHolds: freight damage at receipt and quality quarantines', href: '#/inventory' });
  s += arrow(990, y1 + 60, 1012, y1 + 60);
  s += node(1012, y1, 120, h1, { title: 'Last mile', big: fmt.int(L.last_mile), unit: 'in flight', lines: [`${fmt.int(L.delivered_7d)} delivered, 7d`, 'Crossway · ParcelPro'] });
  // regions under last mile
  ['West', 'Mountain', 'Central', 'East'].forEach((r, i) => {
    const yy = y1 + h1 + 22 + i * 30;
    s += `<g class="lm-region"><rect x="1012" y="${yy}" width="120" height="24" rx="12"/><text x="1072" y="${yy + 16}" text-anchor="middle">${r} · ${['2-3', '3-4', '4-5', '5-6'][i]}d</text></g>`;
  });
  s += `<path class="lm-edge" d="M1072,${y1 + h1} L1072,${y1 + h1 + 18}" marker-end="url(#lm-arrow)"/>`;
  // pack row
  const y2 = 232, h2 = 100;
  const inbLines = inbound.slice(0, 4).map((b) => `${(b.origin_name || '').split(' · ')[0]} · ${b.po_line ? b.po_line.item_id : ''} · ${b.mode === 'OCEAN' ? 'ocean' : b.mode === 'AIR' ? 'air' : 'truck'} · ETA ${fmt.date(b.eta)}`);
  s += node(10, y2, 510, h2, { title: `Inbound supplier legs (${inbound.length})`, lines: inbLines.length ? inbLines : ['none in transit'], tip: 'Cells from Kestrel (Korea) by ocean, enclosures from Summit (Monterrey) by truck, BMS boards by air when expedited' });
  s += arrow(520, y2 + 50, 542, y2 + 50);
  s += node(542, y2, 150, h2, { title: 'Fremont pack line', big: fmt.int(L.fremont_packs), unit: 'packs built', lines: [`${fmt.int(L.fremont_wip)} in WIP`], href: '#/production' });
  s += arrow(692, y2 + 50, 714, y2 + 50);
  s += node(714, y2, 104, h2, { title: 'DG truck', big: fmt.int(L.dg_truck), unit: 'packs', lines: ['UN3480 · class 9', '~18 h, Mon & Thu'], tip: 'Lithium-ion batteries ship as hazmat LTL (UN3480, class 9)\nSierra DG Freight, Fremont to Reno' });
  s += arrow(818, y2 + 50, 840, y2 + 50);
  return html`<div class="lm-wrap"><svg class="lm" viewBox="0 0 ${W} ${H}" role="img" aria-label="Pipeline from CM to customer with live counts">${raw(s)}</svg></div>`;
}

// ---------------------------------------------------------------------------
// Pipeline & ocean
// ---------------------------------------------------------------------------
async function tabPipeline(body) {
  const ov = S.ov;
  const d = S.cache.ocean || (S.cache.ocean = await api.get('/api/shipments/ocean'));
  const st = ov.stories;
  const delayedVessels = ov.vessels.filter((v) => (v.delay_days || 0) >= 1);
  const callouts = [];
  if (delayedVessels.length) {
    const v = delayedVessels[0];
    const ids = st.delayed.map((r) => r.shipment_id);
    callouts.push(ui.callout({
      tone: 'serious', title: `${v.vessel} ${v.voyage} is ${fmt.num(v.delay_days, 0)} days late: ${fmt.int(v.units)} vehicles now due ${fmt.date(v.eta_current)}`,
      body: html`The carrier revised the ETA twice (weather routing, then berth congestion at Oakland). The EDI and JSON updates are in the drawer for ${ids.map((id, i) => html`${i ? ', ' : ''}${link.shipment(id)}`)}. Every customer promise pegged to these containers moves with them: ${link.route('atp', 'ATP re-promises the affected orders')}.`,
    }));
  }
  for (const r of st.held) {
    callouts.push(ui.callout({ tone: 'serious', title: html`${link.shipment(r.shipment_id)} held for a CBP exam (${fmt.int(r.units)} vehicles)`, body: html`${r.exam_type || 'Exam'} ordered after discharge. The broker expects release in about two days, and the units stay unavailable to allocate until then. Entry ${html`<span class="mono">${r.entry_no}</span>`}.` }));
  }
  for (const r of st.short) {
    callouts.push(ui.callout({ tone: 'critical', title: html`${link.shipment(r.shipment_id)} received ${fmt.int(r.units_received)} of ${fmt.int(r.units)} on the ASN`, body: html`${fmt.int(r.units_in_transit)} serials the CM shipped on paper never arrived in Reno. They still read "in transit" everywhere else, so the gap is a reconciliation job, not a carrier claim: check the CM's dock first. The drawer lists the serials.` }));
  }
  for (const r of st.late_isf) {
    callouts.push(ui.callout({ tone: 'warning', title: html`${link.shipment(r.shipment_id)}: ISF filed ${fmt.num(r.isf_hours_before_loading, 0)} h before loading`, body: 'The ISF (10+2) is due 24 hours before the container is laden at the foreign port. Late filings risk liquidated damages and holds. Tie the broker\'s filing to the booking so it can\'t lag the load.' }));
  }
  body.innerHTML = html`
    ${ui.card({ title: 'Factory to door, right now', subtitle: 'A schematic of the pipeline, not a map. Counts are live from unit status and shipment milestones; hover a segment for detail, click to jump.', body: laneMap(ov) })}
    <div class="grid sh-callouts">${callouts.map((c) => html`<div class="span-6">${c}</div>`)}</div>
    ${ui.card({ title: 'Ocean containers', subtitle: 'CM to 3PL, one row per container. ETA current comes from the carrier feed (EDI 315 + JSON ETA updates); status is derived from milestones. Click a row for the full trace.', body: html`<div class="sh-ocean"></div>`, flush: true })}`;
  ui.dataTable($('.sh-ocean', body), {
    columns: [
      { key: 'shipment_id', label: 'Shipment', mono: true },
      { key: 'container_no', label: 'Container', mono: true },
      { key: 'vessel', label: 'Vessel / voyage', render: (r) => html`<span class="nowrap">${r.vessel} <span class="muted mono small">${r.voyage}</span></span>` },
      { key: 'etd', label: 'Departed', value: (r) => r.atd || r.etd_planned, render: (r) => (r.atd ? fmt.date(r.atd) : html`<span class="muted">${fmt.date(r.etd_planned)} plan</span>`) },
      { key: 'eta_planned', label: 'ETA planned', format: (v) => fmt.date(v) },
      { key: 'eta_current', label: 'ETA / arrived', value: (r) => r.ata || r.eta_current, render: (r) => html`<span class="nowrap">${r.ata ? fmt.date(r.ata) : fmt.date(r.eta_current)}${!r.ata && r.eta_slip_days >= 1 ? html` ${ui.chip('serious', `+${fmt.num(r.eta_slip_days, 0)}d`, { icon: false })}` : ''}</span>` },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
      { key: 'units', label: 'Units', num: true, render: (r) => html`${r.received_at ? html`${fmt.int(r.units_received)}<span class="muted"> / ${fmt.int(r.units)}</span>` : fmt.int(r.units)}` },
      { key: 'customs_status', label: 'Customs', render: (r) => ui.statusChip(r.customs_status) },
      { key: 'freight_usd', label: 'Freight', num: true, format: (v) => fmt.usd(v) },
      { key: 'flags', label: 'Flags', value: (r) => r.flags.join(' '), render: (r) => flagChips(r.flags), sortable: false },
    ],
    rows: d.rows, pageSize: 20, search: true, initialSort: { key: 'etd', dir: 'desc' },
    onRowClick: (r) => openShipment(r.shipment_id),
  });
  on(body, 'click', '.lm-link', (e, g) => S.ctx.go(g.dataset.href));
}

// ---------------------------------------------------------------------------
// Customs
// ---------------------------------------------------------------------------
async function tabCustoms(body) {
  const d = S.cache.customs || (S.cache.customs = await api.get('/api/shipments/customs'));
  const rows = d.rows;
  const t = d.totals;
  const assistSum = d.assists.reduce((a, x) => a + (x.unit_price || 0), 0);
  const isfRows = rows.filter((r) => r.isf_hours_before_loading != null).sort((a, b) => a.loaded_at.localeCompare(b.loaded_at));
  const isfChart = charts.bar({
    categories: isfRows.map((r) => r.shipment_id.replace('OC-', '')),
    series: [{ name: 'Hours filed before loading', values: isfRows.map((r) => r.isf_hours_before_loading) }],
    colors: isfRows.map((r) => (r.isf_hours_before_loading < 24 ? 'var(--critical)' : 'var(--series-1)')),
    height: 210, yFormat: (v) => `${fmt.num(v, 0)} h`, valueLabels: false,
  });
  body.innerHTML = html`
    <div class="kpi-row">
      ${ui.kpi({ label: 'Entered value', value: fmt.usd(t.entered_value, { compact: true }), hint: `${rows.length} entries` })}
      ${ui.kpi({ label: 'Duty (mock rate)', value: fmt.usd(t.duty_usd, { compact: true }), hint: 'HTS 8711.60.00 at an illustrative 15%' })}
      ${ui.kpi({ label: 'MPF + HMF', value: fmt.usd(t.mpf_usd + t.hmf_usd, { compact: true }), hint: 'MPF 0.3464% capped per entry · HMF 0.125%' })}
      ${ui.kpi({ label: 'Late ISF', value: fmt.int(d.late_isf), status: d.late_isf ? { tone: 'warning', label: '< 24 h' } : null, hint: 'filed less than 24 h before loading' })}
      ${ui.kpi({ label: 'Exams', value: fmt.int(d.exams), status: d.exams ? { tone: 'serious', label: 'Held' } : null })}
    </div>
    <div class="grid sh-gap">
      <div class="span-7">${ui.card({ title: 'ISF lead time by container', subtitle: 'Hours between the ISF filing and loading at Taichung. The rule is at least 24.', body: isfChart, tableToggle: true })}</div>
      <div class="span-5">${ui.callout({ tone: 'info', title: 'Assists belong in entered value', body: html`
        The OEM buys the drive unit, HMI and pedal unit and consigns them to the CM free of charge. That makes them assists: dutiable, and added to the CM's price in entered value.
        <div class="sh-assist">
          <div class="row"><span>CM price per vehicle (today)</span><span class="spacer"></span><b>${d.cm_price ? `$${fmt.num(d.cm_price, 2)}` : '—'}</b></div>
          ${d.assists.map((a) => html`<div class="row"><span>+ ${a.name} <span class="mono tiny muted">${a.item_id}</span></span><span class="spacer"></span><span>$${fmt.num(a.unit_price, 2)}</span></div>`)}
          <div class="row sh-assist-total"><span>Declared per vehicle</span><span class="spacer"></span><b>$${fmt.num((d.cm_price || 0) + assistSum, 2)}</b></div>
        </div>
        <p class="tiny muted">Leaving assists out under-declares roughly $${fmt.num(assistSum, 0)} per vehicle. Declared value per unit is in the table below. Duty rate is illustrative, not tariff advice.</p>` })}</div>
    </div>
    ${ui.card({ title: 'Customs entries', subtitle: `One entry per container, filed by Bayline Customs Brokerage · HTS ${rows[0] ? rows[0].hts_code : '8711.60.00'} (electric-motor cycles) on every entry`, body: html`<div class="sh-customs"></div>`, flush: true })}`;
  ui.dataTable($('.sh-customs', body), {
    columns: [
      { key: 'entry_no', label: 'Entry', mono: true },
      { key: 'shipment_id', label: 'Shipment', render: (r) => link.shipment(r.shipment_id) },
      { key: 'vessel', label: 'Vessel', render: (r) => html`<span class="nowrap">${r.vessel} <span class="mono small muted">${r.voyage}</span></span>` },
      { key: 'isf_hours_before_loading', label: 'ISF lead', num: true, render: (r) => (r.isf_hours_before_loading == null ? html`<span class="nil">—</span>` : r.isf_hours_before_loading < 24 ? ui.chip('critical', `${fmt.num(r.isf_hours_before_loading, 0)} h`) : html`${fmt.num(r.isf_hours_before_loading, 0)} h`) },
      { key: 'asn_qty', label: 'Units', num: true },
      { key: 'value_per_unit', label: 'Value / unit', num: true, format: (v) => `$${fmt.num(v, 2)}` },
      { key: 'entered_value', label: 'Entered value', num: true, format: (v) => fmt.usd(v) },
      { key: 'duty_usd', label: 'Duty', num: true, format: (v) => fmt.usd(v) },
      { key: 'fees', label: 'MPF + HMF', num: true, value: (r) => r.mpf_usd + r.hmf_usd, format: (v) => fmt.usd(v, { cents: true }) },
      { key: 'status', label: 'Status', render: (r) => html`<span class="nowrap">${ui.statusChip(r.status)}</span>${r.exam_type ? html`<div class="tiny muted">${r.exam_type}</div>` : r.released_at ? html`<div class="tiny muted">released ${fmt.date(r.released_at)}</div>` : ''}` },
    ],
    rows, pageSize: 20, initialSort: { key: 'isf_hours_before_loading', dir: 'asc' },
    onRowClick: (r) => openShipment(r.shipment_id),
  });
}

// ---------------------------------------------------------------------------
// Last mile
// ---------------------------------------------------------------------------
async function tabLastMile(body) {
  const d = S.cache.lm || (S.cache.lm = await api.get('/api/shipments/last-mile', { days: 60 }));
  const stats = d.stats;
  const cats = stats.map((g) => REGION[g.region] || g.region);
  const whoCarries = [...new Set(stats.map((g) => g.carrier))].map((c) => `${CARRIER[c]}: ${stats.filter((g) => g.carrier === c).map((g) => REGION[g.region]).join(', ')}`).join(' · ');
  const sla = charts.bar({
    categories: cats,
    series: [{ name: 'Average transit (business days)', values: stats.map((g) => g.avg_transit) }, { name: 'Lane SLA (days)', values: stats.map((g) => g.sla_days) }],
    height: 220, yFormat: (v) => fmt.num(v, 0),
  });
  const otd = charts.bar({
    categories: cats,
    series: [{ name: 'Within carrier SLA', values: stats.map((g) => g.sla_rate) }, ...(d.promises_available ? [{ name: 'On or before customer promise', values: stats.map((g) => g.otd_promise) }] : [])],
    height: 220, yMin: 0, yMax: 1, yFormat: (v) => fmt.pct(v, 0),
  });
  const worst = [...stats].filter((g) => g.otd_promise != null).sort((a, b) => a.otd_promise - b.otd_promise)[0];
  body.innerHTML = html`
    <div class="grid">
      ${d.carriers.map((c) => html`<div class="span-3">${ui.kpi({ label: c.name, value: fmt.pct(c.sla_rate, 1), hint: `${fmt.int(c.delivered)} delivered in ${d.days} days · ${c.exceptions} exceptions` })}</div>`)}
      <div class="span-6">${worst ? ui.callout({ tone: worst.otd_promise < 0.9 ? 'warning' : 'info', title: `${REGION[worst.region]} misses the customer promise most: ${fmt.pct(worst.otd_promise, 0)} on time`, body: `SLA and promise measure different things. The carrier can be within its ${worst.sla_days}-day lane standard while the customer still gets the bike after the date we promised, because the promise is set at order time and the kit ships later. Promise logic lives in ATP.` }) : ''}</div>
      <div class="span-6">${ui.card({ title: 'Transit time vs lane SLA', subtitle: `Shipped in the last ${d.days} days, business days (Sundays excluded). ${whoCarries}.`, body: sla, tableToggle: true })}</div>
      <div class="span-6">${ui.card({ title: 'On time: carrier SLA vs customer promise', subtitle: 'Same shipments, two definitions of on time', body: otd, tableToggle: true })}</div>
      <div class="span-12">${ui.card({ title: 'Transit-day distribution', subtitle: 'How the tail looks by lane: days from 3PL pickup to delivery', body: distTable(stats) })}</div>
      <div class="span-7">${ui.card({ title: `In flight (${d.in_flight.length})`, body: html`<div class="sh-inflight"></div>`, flush: true })}</div>
      <div class="span-5">${ui.card({ title: 'Delivery exceptions', subtitle: 'Carrier exception scans (failed attempts, address issues)', body: html`<div class="sh-exc"></div>`, flush: true })}</div>
    </div>`;
  ui.dataTable($('.sh-inflight', body), {
    columns: [
      { key: 'shipment_id', label: 'Shipment', render: (r) => link.shipment(r.shipment_id) },
      { key: 'order_id', label: 'Order', render: (r) => link.order(r.order_id) },
      { key: 'carrier', label: 'Carrier', render: (r) => html`<span class="small">${(CARRIER[r.carrier] || r.carrier).split(' ')[0]}</span>` },
      { key: 'state', label: 'To' },
      { key: 'shipped_at', label: 'Shipped', format: (v) => fmt.dt(v) },
      { key: 'promised_date', label: 'Promise', format: (v) => fmt.date(v) },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
    ],
    rows: d.in_flight, pageSize: 12, search: true, initialSort: { key: 'shipped_at', dir: 'asc' },
    onRowClick: (r) => openShipment(r.shipment_id),
  });
  ui.dataTable($('.sh-exc', body), {
    columns: [
      { key: 'event_ts', label: 'When', format: (v) => fmt.dt(v) },
      { key: 'shipment_id', label: 'Shipment', render: (r) => link.shipment(r.shipment_id) },
      { key: 'location', label: 'Note', wrap: true, render: (r) => html`<span class="small">${r.location || ''}</span>` },
      { key: 'status', label: 'Now', render: (r) => ui.statusChip(r.status) },
    ],
    rows: d.exceptions, pageSize: 12,
  });
}

function distTable(stats) {
  const maxDay = Math.max(...stats.flatMap((g) => Object.keys(g.dist).map(Number)));
  const minDay = Math.min(...stats.flatMap((g) => Object.keys(g.dist).map(Number)));
  const daysArr = [];
  for (let i = minDay; i <= maxDay; i++) daysArr.push(i);
  return html`<div class="table-wrap"><table class="table dense sh-dist"><thead><tr><th>Lane</th><th class="num">SLA</th>${daysArr.map((d) => html`<th class="num">${d}d</th>`)}<th class="num">Within SLA</th></tr></thead>
    <tbody>${stats.map((g) => {
      const tot = Object.values(g.dist).reduce((a, b) => a + b, 0) || 1;
      return html`<tr><td class="nowrap">${CARRIER[g.carrier]} · ${REGION[g.region] || g.region}</td><td class="num">${g.sla_days}d</td>${daysArr.map((d) => {
        const n = g.dist[String(d)] || 0;
        const share = n / tot;
        const over = d > g.sla_days;
        return html`<td class="num sh-dcell ${over && n ? 'over' : ''}" style="--w:${Math.max(n ? 3 : 0, Math.round(share * 100))}%" data-tip="${REGION[g.region]} · ${d} days\n${n} shipments (${fmt.pct(share, 0)})${over && n ? '\npast the lane SLA' : ''}">${n || ''}</td>`;
      })}<td class="num">${fmt.pct(g.sla_rate, 1)}</td></tr>`;
    })}</tbody></table></div>
    <p class="tiny muted">Each cell's fill is that lane's share of shipments on that transit day; red fill means past the lane SLA.</p>`;
}

// ---------------------------------------------------------------------------
// DG trucks & inbound
// ---------------------------------------------------------------------------
async function tabTrucks(body) {
  const d = S.cache.trucks || (S.cache.trucks = await api.get('/api/shipments/trucks'));
  const rows = d.rows;
  const done = rows.filter((r) => r.picked_up && r.arrived);
  const hrs = done.map((r) => (Date.parse(r.arrived) - Date.parse(r.picked_up)) / 3600000);
  const avgH = hrs.length ? hrs.reduce((a, b) => a + b, 0) / hrs.length : null;
  const last8 = rows.slice(0, 16).reverse();
  const chart = charts.bar({
    categories: last8.map((r) => r.shipment_id.replace('TR-', '')),
    series: [{ name: 'Standard', values: last8.map((r) => r.std) }, { name: 'Large', values: last8.map((r) => r.lrg) }],
    stacked: true, height: 210, yFormat: (v) => fmt.int(v),
  });
  const packs = rows.reduce((a, r) => a + r.asn_qty, 0);
  body.innerHTML = html`
    <div class="kpi-row">
      ${ui.kpi({ label: 'DG trucks', value: fmt.int(rows.length), hint: 'Fremont to Reno, Mondays and Thursdays' })}
      ${ui.kpi({ label: 'Packs moved', value: fmt.int(packs) })}
      ${ui.kpi({ label: 'Average transit', value: avgH == null ? '—' : `${fmt.num(avgH, 1)} h`, hint: 'pickup to delivery' })}
      ${ui.kpi({ label: 'Freight per pack', value: packs ? `$${fmt.num(rows.reduce((a, r) => a + (r.freight_usd || 0), 0) / packs, 2)}` : '—', hint: '$1,180 per hazmat LTL shipment' })}
      ${ui.kpi({ label: 'Inbound supplier legs', value: fmt.int(d.inbound.length), hint: 'in transit to Fremont or the 3PL' })}
    </div>
    <div class="grid sh-gap">
      <div class="span-7">${ui.card({ title: 'Packs per truck', subtitle: 'Last 16 DG shipments, standard vs large', body: chart, tableToggle: true })}</div>
      <div class="span-5">${ui.callout({ tone: 'info', title: 'Why packs ride separately', body: 'Standalone lithium-ion packs ship as UN3480, class 9 dangerous goods, on a hazmat-certified LTL carrier with placards and trained drivers. The CM ships vehicles without packs, and the 3PL marries a pack to a vehicle at kitting. That marriage is recorded in genealogy as SHIPPED_WITH, so a cell-lot recall still reaches the customer.' })}</div>
      <div class="span-12">${ui.card({ title: 'DG truck shipments', body: html`<div class="sh-trucks"></div>`, flush: true })}</div>
      <div class="span-12">${ui.card({ title: 'Inbound supplier legs', subtitle: 'Supplier to Fremont (or to the 3PL for chargers). Each one pegs to a PO line and its current promise.', body: html`<div class="sh-inb"></div>`, flush: true })}</div>
    </div>`;
  ui.dataTable($('.sh-trucks', body), {
    columns: [
      { key: 'shipment_id', label: 'Shipment', render: (r) => link.shipment(r.shipment_id) },
      { key: 'pro', label: 'PRO', mono: true },
      { key: 'picked_up', label: 'Picked up', format: (v) => fmt.dt(v) },
      { key: 'arrived', label: 'Arrived', format: (v) => fmt.dt(v) },
      { key: 'std', label: 'Std', num: true },
      { key: 'lrg', label: 'Large', num: true },
      { key: 'asn_qty', label: 'Packs', num: true },
      { key: 'dg_class', label: 'DG', render: (r) => html`<span class="mono small">UN3480 · ${r.dg_class || '9'}</span>` },
      { key: 'per_pack', label: '$ / pack', num: true, format: (v) => `$${fmt.num(v, 2)}` },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
    ],
    rows, pageSize: 15, initialSort: { key: 'picked_up', dir: 'desc' }, onRowClick: (r) => openShipment(r.shipment_id),
  });
  ui.dataTable($('.sh-inb', body), {
    columns: [
      { key: 'shipment_id', label: 'Shipment', render: (r) => link.shipment(r.shipment_id) },
      { key: 'origin_name', label: 'From', render: (r) => html`<span class="small">${r.origin_name}</span>` },
      { key: 'dest_name', label: 'To', render: (r) => html`<span class="small">${(r.dest_name || '').replace('OEM Pack Line · ', '').replace('Sierra Fulfillment · ', '3PL ')}</span>` },
      { key: 'mode', label: 'Mode', render: (r) => html`<span class="small">${fmt.title(r.mode)}</span>` },
      { key: 'carrier', label: 'Carrier', render: (r) => html`<span class="small">${CARRIER[r.carrier] || r.carrier}</span>` },
      { key: 'po', label: 'PO line', value: (r) => (r.po_line ? `${r.po_line.po_id}-${r.po_line.line_no}` : ''), render: (r) => (r.po_line ? html`${link.po(r.po_line.po_id, r.po_line.line_no)} <span class="mono small muted">${r.po_line.item_id}</span>` : html`<span class="nil">—</span>`) },
      { key: 'asn_qty', label: 'Qty', num: true, format: (v) => fmt.int(v) },
      { key: 'etd', label: 'ETD', format: (v) => fmt.date(v) },
      { key: 'eta', label: 'ETA', format: (v) => fmt.date(v) },
      { key: 'status', label: 'Status', render: (r) => html`${ui.statusChip(r.status)}${r.dg_class ? html` <span class="mono tiny muted">DG ${r.dg_class}</span>` : ''}` },
    ],
    rows: d.inbound, pageSize: 0, onRowClick: (r) => openShipment(r.shipment_id), empty: 'Nothing inbound',
  });
}

// ---------------------------------------------------------------------------
// Freight spend & rates
// ---------------------------------------------------------------------------
async function tabFreight(body) {
  const d = S.cache.freight || (S.cache.freight = await api.get('/api/shipments/freight'));
  const wk = d.weekly.slice(-16);
  const lanes = ['last_mile', 'ocean', 'drayage', 'dg_truck'];
  const chart = charts.bar({
    categories: wk.map((w) => fmt.date(w.week)),
    series: lanes.map((l) => ({ name: LANE_LABEL[l], values: wk.map((w) => Math.round(w[l])) })),
    stacked: true, height: 240, yFormat: (v) => fmt.usd(v, { compact: true }),
  });
  const gri = d.gri;
  const ocean = S.ov.lanes;
  const griCtr = S.cache.ocean ? S.cache.ocean.rows.filter((r) => r.freight_usd === (gri && gri.new)).length : null;
  body.innerHTML = html`
    <div class="kpi-row">
      ${ui.kpi({ label: 'Freight to date', value: fmt.usd(d.total, { compact: true }) })}
      ${Object.entries(d.by_lane).map(([k, v]) => ui.kpi({ label: LANE_LABEL[k] || k, value: fmt.usd(v, { compact: true }), hint: `${fmt.pct(v / d.total, 0)} of spend` }))}
      ${ui.kpi({ label: 'Per vehicle delivered', value: fmt.usd(d.per_vehicle), hint: 'ocean + drayage + average last mile' })}
    </div>
    <div class="grid sh-gap">
      <div class="span-8">${ui.card({ title: 'Freight spend by week and lane', subtitle: 'Ocean by departure, drayage by out-gate, DG trucks by pickup, last mile by ship date (contract rates)', body: chart, tableToggle: true })}</div>
      <div class="span-4">${gri ? ui.callout({ tone: 'warning', title: `Ocean GRI: $${fmt.int(gri.old)} → $${fmt.int(gri.new)} per container from ${fmt.date(gri.valid_from)}`, body: html`A ${fmt.pct(gri.new / gri.old - 1, 0)} general rate increase on Taichung–Oakland. At 150 vehicles a container it adds $${fmt.num((gri.new - gri.old) / 150, 2)} per vehicle${griCtr != null ? `, and ${griCtr} containers have already sailed at the new rate` : ''}. Last mile is still ${fmt.pct((d.by_lane.last_mile || 0) / d.total, 0)} of spend, so it's where the next dollar of savings is.` }) : ''}</div>
      <div class="span-7">${ui.card({ title: 'Contract rates', subtitle: 'freight_rate with validity windows', body: html`<div class="sh-rates"></div>`, flush: true })}</div>
      <div class="span-5">${ui.card({ title: 'Carriers & integration', subtitle: 'How each carrier\'s status reaches us', body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Carrier</th><th>Mode</th><th>SCAC</th><th>Feed</th></tr></thead>
        <tbody>${d.carriers.map((c) => html`<tr><td>${c.name}</td><td class="small">${fmt.title(c.mode)}</td><td class="mono small">${c.scac}</td><td>${ui.chip(c.integration === 'EMAIL' ? 'warning' : 'good', c.integration === 'EDI315' ? 'EDI 315' : fmt.title(c.integration), { icon: false })}</td></tr>`)}</tbody></table></div>
        <p class="tiny muted">Email-only carriers are the ones the ${link.route('integrations', 'Integration Hub')} is working to move onto an API.</p>` })}</div>
    </div>`;
  ui.dataTable($('.sh-rates', body), {
    columns: [
      { key: 'carrier_name', label: 'Carrier' },
      { key: 'lane', label: 'Lane', mono: true },
      { key: 'basis', label: 'Basis', render: (r) => html`<span class="small nowrap">${fmt.title(r.basis).replace('Per ', '/ ')}</span>` },
      { key: 'rate_usd', label: 'Rate', num: true, format: (v) => `$${fmt.num(v, 2)}` },
      { key: 'valid_from', label: 'From', format: (v) => fmt.date(v) },
      { key: 'valid_to', label: 'To', render: (r) => (r.valid_to ? fmt.date(r.valid_to) : html`<span class="muted">open</span>`) },
      { key: 'contract', label: 'Contract', wrap: true, render: (r) => html`<span class="mono small">${r.contract}</span>` },
    ],
    rows: d.rates, pageSize: 0,
  });
}

// ---------------------------------------------------------------------------
// Shipment drawer: milestones with their raw source, units, customs
// ---------------------------------------------------------------------------
const EVENT_TONE = { CUSTOMS_HOLD: 'serious', EXCEPTION: 'serious', ETA_UPDATE: 'warning', DELIVERED: 'good', RECEIVED: 'good', CUSTOMS_RELEASED: 'good' };

async function openShipment(id) {
  let d;
  try { d = await api.get('/api/shipments/detail', { id }); } catch (err) { ui.toast(err.message, 'critical'); return; }
  if (!S) return;
  const s = d.shipment;
  S.ctx.setQuery({ id: s.shipment_id }, { silent: true });
  const slip = s.eta_current && s.eta_planned ? Math.round((Date.parse(s.eta_current) - Date.parse(s.eta_planned)) / 86400000) : 0;
  const items = d.events.map((e) => ({
    ts: e.event_ts, tone: EVENT_TONE[e.code] || 'info',
    meta: html`<span class="sh-src">${e.source}</span>`,
    title: html`${fmt.title(e.code)}${e.location ? html` <span class="muted small">· ${e.location}</span>` : ''}`,
    detail: html`${e.detail ? html`<div class="small">${e.detail}</div>` : ''}${e.raw ? html`<div class="sh-raw"><span class="tiny muted">${e.raw.format} · ${e.raw_ref}</span>${e.raw.payload ? ui.codeBlock(e.raw.format === 'JSON' ? ui.prettyJSON(e.raw.payload) : e.raw.payload, e.raw.format === 'JSON' ? 'json' : 'text') : html`<div class="tiny muted">${e.raw.note || e.raw.status}</div>`}</div>` : ''}`,
  }));
  const mix = Object.entries(d.unit_mix);
  const short = d.short_units || [];
  const ce = d.customs;
  const bodyHtml = html`<div class="sh-drawer">
    ${short.length ? ui.callout({ tone: 'critical', title: `${short.length} serials on the ASN were never received`, body: html`<div class="row wrap">${short.map((sn) => link.serial(sn))}</div><p class="small">They left the CM on paper only. Ask the CM to confirm they are still on its dock, then re-book them on the next sailing.</p>` }) : ''}
    ${slip >= 1 && !s.ata ? ui.callout({ tone: 'serious', title: `ETA moved ${slip} days: ${fmt.date(s.eta_planned)} → ${fmt.date(s.eta_current)}`, body: html`Promises pegged to these units move with it: ${link.route('atp', 'see ATP')}.` }) : ''}
    ${ce && ce.status === 'EXAM' ? ui.callout({ tone: 'serious', title: `Customs hold: ${ce.exam_type || 'exam'}`, body: `Entry ${ce.entry_no} is under exam. The units are at the port and can't be allocated.` }) : ''}
    <dl class="kv">
      <dt>Leg</dt><dd>${fmt.title(s.leg)} · ${fmt.title(s.mode)}${s.dg_class ? html` · ${ui.chip('warning', `DG class ${s.dg_class}`, { icon: false })}` : ''}</dd>
      <dt>Route</dt><dd>${s.origin_name}${s.pol_name ? ` · ${s.pol_name}` : ''} → ${s.pod_name ? `${s.pod_name} → ` : ''}${s.dest_name || (d.order ? `${d.order.ship_to_state} (${d.order.ship_to_region})` : '—')}</dd>
      <dt>Carrier</dt><dd>${s.carrier_name || s.carrier}${s.vessel ? html` · ${s.vessel} <span class="mono">${s.voyage}</span>` : ''}</dd>
      ${s.container_no ? html`<dt>Container</dt><dd class="mono">${s.container_no}${s.booking_ref ? ` · booking ${s.booking_ref}` : ''}${s.bol_no ? ` · B/L ${s.bol_no}` : ''}</dd>` : ''}
      ${s.tracking_no ? html`<dt>Tracking</dt><dd class="mono">${s.tracking_no}</dd>` : ''}
      <dt>Departure</dt><dd>${s.atd ? fmt.dt(s.atd) : html`<span class="muted">planned ${fmt.dt(s.etd_planned)}</span>`}</dd>
      <dt>Arrival</dt><dd>${s.ata ? fmt.dt(s.ata) : html`ETA ${fmt.dt(s.eta_current || s.eta_planned)}${slip >= 1 ? html` ${ui.chip('serious', `+${slip}d`, { icon: false })}` : ''}`}${s.received_at && s.leg !== '3PL_TO_CUSTOMER' ? html` · received ${fmt.dt(s.received_at)}` : ''}</dd>
      <dt>Status</dt><dd>${ui.statusChip(s.status)} · ${fmt.int(d.units.length)} units${s.asn_qty != null ? ` (ASN ${fmt.int(s.asn_qty)})` : ''}${s.freight_usd ? ` · freight ${fmt.usd(s.freight_usd)}` : ''}</dd>
      ${d.order ? html`<dt>Order</dt><dd>${link.order(d.order.order_id)} · ${fmt.title(d.order.channel)} · promise ${fmt.date(d.order.promised_date)}${d.order.delivered_at ? ` · delivered ${fmt.dt(d.order.delivered_at)}` : ''}</dd>` : ''}
      ${d.po_line ? html`<dt>PO line</dt><dd>${link.po(d.po_line.po_id, d.po_line.line_no)} · ${d.po_line.item_id} ${d.po_line.name} · ${fmt.int(d.po_line.qty)} · promise ${fmt.date(d.po_line.promise_date)}</dd>` : ''}
    </dl>
    ${ce ? html`<h4 class="section-title">Customs entry</h4>
      <dl class="kv">
        <dt>Entry</dt><dd class="mono">${ce.entry_no} · ${ce.broker}</dd>
        <dt>Status</dt><dd>${ui.statusChip(ce.status)}${ce.exam_type ? ` · ${ce.exam_type}` : ''}</dd>
        <dt>ISF filed</dt><dd>${fmt.dt(ce.isf_filed_at)}</dd>
        <dt>Entry filed</dt><dd>${ce.entry_filed_at ? fmt.dt(ce.entry_filed_at) : '—'}</dd>
        <dt>Value / duty</dt><dd>${fmt.usd(ce.entered_value)} · duty ${fmt.usd(ce.duty_usd)} (${fmt.pct(ce.duty_rate, 0)} mock) · MPF ${fmt.usd(ce.mpf_usd, { cents: true })} · HMF ${fmt.usd(ce.hmf_usd, { cents: true })}</dd>
        <dt>Released</dt><dd>${ce.released_at ? fmt.dt(ce.released_at) : '—'}</dd>
      </dl>` : ''}
    <h4 class="section-title">Milestones (${items.length})</h4>
    ${items.length ? ui.timeline(items) : ui.empty('No milestones yet.')}
    ${d.units.length ? html`<h4 class="section-title">Units (${fmt.int(d.units.length)}) ${mix.map(([k, n]) => html`<span class="sh-mix mono">${k} ${n}</span>`)}</h4>
      <div class="sh-units">${d.units.map((u) => html`<span class="sh-unit${u.on_hold ? ' hold' : ''}${u.status === 'IN_TRANSIT' && s.received_at && s.leg === 'CM_TO_3PL' ? ' missing' : ''}" title="${u.item_id} · ${fmt.title(u.status)}${u.on_hold ? ' · on hold' : ''}">${link.serial(u.serial)}</span>`)}</div>` : ''}
  </div>`;
  ui.drawer.open({
    title: s.shipment_id,
    subtitle: `${fmt.title(s.leg)} · ${s.container_no || s.tracking_no || s.carrier}`,
    body: bodyHtml, width: 720,
    onClose: () => { if (S) S.ctx.setQuery({ id: null }, { silent: true }); },
  });
}

const PAGE_CSS = `
.pg-shipments .sh-tabs { margin-top: 20px; }
.pg-shipments .sh-body { margin-top: 16px; }
.pg-shipments .sh-body > div > * + * { margin-top: 16px; }
.pg-shipments .sh-gap { margin-top: 16px; }
.pg-shipments .sh-callouts { margin-top: 16px; }
.pg-shipments .sh-flags { display: inline-flex; flex-wrap: wrap; gap: 4px; }
/* lane map */
.pg-shipments .lm-wrap { overflow-x: auto; }
.pg-shipments .lm { display: block; width: 100%; min-width: 900px; height: auto; font-family: var(--font-ui); }
.pg-shipments .lm-node rect { fill: var(--surface-2); stroke: var(--hairline-strong); stroke-width: 1; }
.pg-shipments .lm-node.tone-serious rect:first-child { stroke: color-mix(in srgb, var(--serious) 70%, transparent); }
.pg-shipments .lm-node.tone-warning rect:first-child { stroke: color-mix(in srgb, var(--warning) 80%, transparent); }
.pg-shipments .lm-node .lm-bar { stroke: none; }
.pg-shipments .lm-node.tone-serious .lm-bar { fill: var(--serious); }
.pg-shipments .lm-node.tone-warning .lm-bar { fill: var(--warning); }
.pg-shipments .lm-link { cursor: pointer; }
.pg-shipments .lm-link:hover rect:first-child { fill: var(--row-hover); }
.pg-shipments .lm-title { font: 600 12.5px var(--font-cond); fill: var(--ink-2); letter-spacing: .02em; }
.pg-shipments .lm-big { font: 600 24px var(--font-ui); fill: var(--ink); }
.pg-shipments .lm-unit { font: 500 11.5px var(--font-ui); fill: var(--ink-3); }
.pg-shipments .lm-sub { font: 400 11.5px var(--font-ui); fill: var(--ink-2); }
.pg-shipments .lm-lane { font: 600 10.5px var(--font-cond); letter-spacing: .12em; fill: var(--ink-3); }
.pg-shipments .lm-edge { stroke: var(--axis); stroke-width: 1.5; fill: none; }
.pg-shipments .lm-arrowhead { fill: var(--axis); }
.pg-shipments .lm-edge-label { font: 500 10.5px var(--font-ui); fill: var(--ink-3); }
.pg-shipments .lm-region rect { fill: var(--surface); stroke: var(--hairline-strong); }
.pg-shipments .lm-region text { font: 500 11px var(--font-ui); fill: var(--ink-2); }
/* customs */
.pg-shipments .sh-assist { margin: 10px 0 6px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.pg-shipments .sh-assist .row { gap: 12px; }
.pg-shipments .sh-assist-total { border-top: 1px solid var(--hairline-strong); padding-top: 6px; }
/* last mile distribution */
.pg-shipments .sh-dist td.sh-dcell { min-width: 52px; background: linear-gradient(90deg, color-mix(in srgb, var(--series-1) 24%, transparent) var(--w), transparent var(--w)); background-clip: padding-box; }
.pg-shipments .sh-dist td.sh-dcell.over { background: linear-gradient(90deg, color-mix(in srgb, var(--critical) 26%, transparent) var(--w), transparent var(--w)); }
/* drawer */
.sh-drawer .kv { margin-bottom: 12px; }
.sh-drawer .sh-src { font: 500 11px/1 var(--font-mono); color: var(--ink-3); }
.sh-drawer .sh-raw { margin-top: 6px; }
.sh-drawer .sh-raw .code { margin-top: 4px; max-height: 140px; font-size: 11.5px; }
.sh-drawer .sh-mix { font-size: 11px; font-weight: 500; color: var(--ink-2); margin-left: 8px; text-transform: none; letter-spacing: 0; }
.sh-drawer .sh-units { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 12px; max-height: 260px; overflow: auto; padding: 8px; border-radius: 8px; background: var(--surface-2); }
.sh-drawer .sh-unit.hold .id-link { color: var(--serious); }
.sh-drawer .sh-unit.missing { outline: 2px solid color-mix(in srgb, var(--critical) 60%, transparent); border-radius: 4px; padding: 0 3px; }
.sh-drawer .callout + .callout { margin-top: 10px; }
.sh-drawer .callout { margin-bottom: 12px; }
`;
