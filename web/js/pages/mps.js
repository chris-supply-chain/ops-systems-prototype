// Master Schedule & Capacity: the demand -> MPS -> capacity chain.
// Booked orders and the S&OP forecast (greater-of beyond the frozen fence) against
// what lands at the kitting point in Reno. That is the CM commit offset by the ocean
// pipeline, and the pack MPS as MRP constrains it. Around it: time fences, the
// textbook MPS record with ATP, rough-cut capacity per line, the vehicle/pack
// balance, and how good the forecast has been.
import { html, raw, esc, on, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts, scaleBand, scaleLinear, niceTicks } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const FAM = {
  VEH: { label: 'Vehicles', unit: 'vehicles', site: 'CM, Taichung', supply: 'CM commit + ocean pipeline' },
  PACK: { label: 'Battery packs', unit: 'packs', site: 'OEM, Fremont', supply: 'pack MPS (MRP-constrained) + DG trucks' },
};
const ZONE_LABEL = { frozen: 'Frozen', slushy: 'Slushy', liquid: 'Liquid' };

let S = null;
let OFFS = [];

function listen(root, ev, sel, fn) {
  OFFS.push(on(root, ev, sel, fn));
}

function cleanup() {
  OFFS.forEach((off) => off());
  OFFS = [];
}

export async function render(el, ctx) {
  cleanup();
  injectStyle('page-mps', PAGE_CSS);
  const d = await api.get('/api/plan/mps');
  const fam = ctx.query.get('family') === 'PACK' ? 'PACK' : 'VEH';
  S = { el, ctx, d, fam };
  draw();
}

export function unmount() {
  cleanup();
  S = null;
}

function draw() {
  const { el, d } = S;
  const k = d.kpis;
  const veh = d.grids.VEH;
  const pack = d.grids.PACK;
  const rccpRisk = d.rccp.filter((r) => r.weeks.some((w) => w.load_vs_demonstrated && w.load_vs_demonstrated > 1.02));
  const vehShort = veh.atp_cumulative[0] < 0 ? -veh.atp_cumulative[0] : 0;
  const packShort = Math.max(0, ...pack.pab.map((v) => -v));

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-mps">
      <p class="mp-context">
        <span>${icon('target', 15)}<strong>${d.forecast_version}</strong> forecast · weekly buckets from ${fmt.date(d.start)}</span>
        <span>CM frozen to ${fmt.date(d.fences.VEH.frozen_until)} · slushy to ${fmt.date(d.fences.VEH.slushy_until)}</span>
        <span>Fremont frozen to ${fmt.date(d.fences.PACK.frozen_until)} · slushy to ${fmt.date(d.fences.PACK.slushy_until)}</span>
      </p>

      <div class="kpi-row">
        ${ui.kpi({ label: 'Open order backlog', value: fmt.int(k.backlog), unit: 'kits', hint: `${fmt.int(k.backlog_open)} open · ${fmt.int(k.backlog_allocated)} allocated · +${fmt.int(k.extra_packs)} extra packs` })}
        ${ui.kpi({ label: 'Demand run-rate', value: fmt.num(k.orders_per_week, 0), unit: '/ week', hint: `Orders, last 4 weeks · S&OP says ${fmt.num(k.forecast_per_week, 0)}/week next 4` })}
        ${ui.kpi({ label: 'CM committed output', value: fmt.num(k.cm_weekly, 0), unit: 'vehicles / wk', hint: `Next 4 weeks · built ${fmt.num(k.cm_built_weekly, 0)}/wk last 4` })}
        ${ui.kpi({ label: 'Pack MPS', value: fmt.num(k.pack_weekly, 0), unit: 'packs / wk', hint: 'Next 4 weeks, before the BMS constraint' })}
        ${ui.kpi({
          label: 'Weeks of backlog', value: k.weeks_of_backlog != null ? fmt.num(k.weeks_of_backlog, 1) : '—', unit: 'weeks',
          hint: 'Backlog ÷ committed weekly output',
          status: k.weeks_of_backlog > 6 ? { tone: 'warning', label: 'Supply-bound' } : null,
        })}
      </div>

      <div class="mp-insights">
        ${vehShort ? ui.callout({
          tone: 'serious',
          title: `Week of ${fmt.date(d.weeks[0])}: ${fmt.int(vehShort)} more vehicle promises than vehicles`,
          body: html`${fmt.int(veh.booked[0] + veh.past_due)} orders must ship this week to keep their promise (${fmt.int(veh.past_due)} already past their ship-by date) and ${fmt.int(veh.landing[0])} vehicles land. The delayed sailing is inside the frozen zone, so re-promise or reallocate: ${link.route('atp', 'ATP & Queues')} · ${link.route('loop', 'Closed Loop')}.`,
        }) : ''}
        ${d.pack_loss.length ? ui.callout({
          tone: 'critical',
          title: `BMS-B shortage removes ${fmt.int(d.pack_loss_total)} packs from the pack MPS`,
          body: html`MRP constrains the pack plan on ${d.pack_loss.map((x, i) => html`${i ? ', ' : ''}${fmt.date(x.date)} (${fmt.int(x.lost)} of ${fmt.int(x.planned)})`)}. Packs landing in the week of ${fmt.date(d.weeks[0])} drop from ${fmt.int(pack.landing_unconstrained[0])} to ${fmt.int(pack.landing[0])}, so the pack balance goes to ${fmt.int(Math.min(...pack.pab))}. ${link.route('mrp?item=BMS-B', 'Open the BMS-B record →')}`,
        }) : ''}
        ${rccpRisk.length ? ui.callout({
          tone: 'warning',
          title: `The plan asks more than the lines demonstrate`,
          body: html`${rccpRisk.map((r, i) => html`${i ? ' ' : ''}<strong>${r.label}</strong> demonstrates ${fmt.num(r.demonstrated_per_day, 1)}/day against a plan of up to ${fmt.int(Math.max(...r.weeks.map((w) => (w.workdays ? w.planned / w.workdays : 0))))}/day.`)} Rated capacity says the plan fits; the last four weeks say it doesn’t. Expect the gap to come out of the backlog burn-down.`,
        }) : ''}
      </div>

      <div class="filter-row mp-filter">
        <span class="small muted">Family</span><div data-fam></div>
        <span class="small muted">${FAM[S.fam].site} · supply = ${FAM[S.fam].supply}</span>
      </div>

      <div class="grid">
        <div class="span-12">${ui.card({
          title: html`Demand vs supply at the kitting point · ${FAM[S.fam].label}`,
          subtitle: 'Bars: orders due to ship each week (to keep their promise) and forecast not yet booked. Line: supply landing in Reno. Zones show what can still change at the source.',
          tableToggle: true,
          body: html`${fenceStrip(d, S.fam)}${demandChart(d, S.fam)}`,
        })}</div>

        <div class="span-12">${ui.card({
          title: html`MPS record · ${FAM[S.fam].label}`,
          subtitle: 'Textbook MPS: inside the frozen zone demand is booked orders only; beyond it, the greater of forecast and orders. ATP is cumulative supply not yet promised (with look-ahead).',
          flush: true,
          body: mpsGrid(d, S.fam),
        })}</div>

        <div class="span-12">${ui.card({
          title: 'Rough-cut capacity: planned load against each line',
          subtitle: 'Planned builds per week against rated capacity (line_capacity × workdays) and against what each line demonstrated over the last 4 weeks. Above the red 100% line the plan asks for more than the line has been doing.',
          tableToggle: true,
          body: html`${rccpChart(d)}${rccpTable(d)}`,
        })}</div>

        <div class="span-7">${ui.card({
          title: 'Vehicles vs packs landing in Reno',
          subtitle: `A kit needs one of each; the 3PL can kit about ${fmt.int(d.kit_capacity_week)} a week`,
          tableToggle: true,
          body: balanceChart(d),
        })}</div>
        <div class="span-5">${ui.card({
          title: 'Pack output lost to the BMS-B shortage',
          subtitle: 'constrained_pack_plan: the pack MPS as MRP says it can actually be built',
          tableToggle: true,
          body: lossChart(d),
        })}</div>

        <div class="span-12">${ui.card({
          title: 'S&OP forecast versions and accuracy',
          subtitle: 'D2C orders per week (fleet volume is contracted, not forecast) against each published S&OP version. Accuracy is scored only on complete weeks after a version was published.',
          tableToggle: true,
          body: sopBlock(d),
        })}</div>
      </div>
    </div>`;

  ui.segmented(el.querySelector('[data-fam]'), {
    options: [{ value: 'VEH', label: 'Vehicles' }, { value: 'PACK', label: 'Battery packs' }],
    value: S.fam, label: 'Family',
    onChange: (v) => S.ctx.setQuery({ family: v === 'PACK' ? 'PACK' : null }),
  });
}

// ---------------------------------------------------------------------------
// fences
// ---------------------------------------------------------------------------
function fenceStrip(d, fam) {
  const f = d.fences[fam];
  const src = fam === 'VEH' ? 'CM builds' : 'Pack builds';
  return html`<div class="mp-fences" role="list">
    <div class="mp-fence frozen" role="listitem"><span class="mp-fz">${icon('lock', 13)} Frozen</span>
      <span>${src} through ${fmt.date(f.frozen_until)} (${f.frozen_days}d)</span><span class="muted">lands by ${fmt.date(f.landing_frozen_until)}</span></div>
    <div class="mp-fence slushy" role="listitem"><span class="mp-fz">${icon('alert-triangle', 13)} Slushy</span>
      <span>to ${fmt.date(f.slushy_until)} (${f.slushy_days}d): planner-approved changes only</span><span class="muted">lands by ${fmt.date(f.landing_slushy_until)}</span></div>
    <div class="mp-fence liquid" role="listitem"><span class="mp-fz">${icon('check-circle', 13)} Liquid</span>
      <span>after that: MPS follows demand</span><span class="muted">${f.note}</span></div>
  </div>`;
}

// ---------------------------------------------------------------------------
// demand vs supply (custom SVG: stacked columns + supply line + fence zones)
// ---------------------------------------------------------------------------
function demandChart(d, fam) {
  const g = d.grids[fam];
  const weeks = d.weeks;
  const booked = g.booked.map((v, i) => v + (i === 0 ? g.past_due : 0));
  const extra = g.forecast.map((f, i) => (g.zone[i] === 'frozen' ? 0 : Math.max(0, f - booked[i])));
  const table = {
    columns: ['Week', 'Orders due to ship', 'Forecast not yet booked', 'Supply landing', 'Projected balance', 'Zone'],
    rows: weeks.map((w, i) => [fmt.date(w), fmt.int(booked[i]), fmt.int(extra[i]), fmt.int(g.landing[i]), fmt.int(g.pab[i]), ZONE_LABEL[g.zone[i]]]),
    numeric: [true, true, true, true, false],
  };
  return charts.custom({
    height: 300,
    ariaLabel: `${FAM[fam].label}: demand vs supply by week`,
    table,
    legend: html`<div class="legend">
      <span class="legend-item"><span class="legend-key rect" style="--c:var(--series-1)"></span>Orders due to ship</span>
      <span class="legend-item"><span class="legend-key rect" style="--c:var(--series-other)"></span>Forecast not yet booked</span>
      <span class="legend-item"><span class="legend-key line" style="--c:var(--series-2)"></span>Supply landing in Reno</span>
    </div>`,
    render: (w, H) => {
      const m = { t: 28, r: 14, b: 26, l: 46 };
      const x = scaleBand(weeks, [m.l, w - m.r], 0.28);
      const top = Math.max(...booked.map((b, i) => b + extra[i]), ...g.landing, 1);
      const ticks = niceTicks(0, top * 1.05, 5);
      const y = scaleLinear([0, ticks[ticks.length - 1]], [H - m.b, m.t]);
      let s = '';
      // fence zones
      let i = 0;
      while (i < weeks.length) {
        let j = i;
        while (j + 1 < weeks.length && g.zone[j + 1] === g.zone[i]) j++;
        const x0 = x(weeks[i]) - (x.step - x.bandwidth) / 2;
        const x1 = x(weeks[j]) + x.bandwidth + (x.step - x.bandwidth) / 2;
        if (g.zone[i] !== 'liquid') s += `<rect class="mp-zone ${g.zone[i]}" x="${x0.toFixed(1)}" y="${m.t - 20}" width="${(x1 - x0).toFixed(1)}" height="${H - m.b - m.t + 20}"/>`;
        s += `<text class="mp-zone-label" x="${(x0 + 6).toFixed(1)}" y="${m.t - 7}">${esc(ZONE_LABEL[g.zone[i]])}</text>`;
        i = j + 1;
      }
      for (const t of ticks) {
        const yy = Math.round(y(t)) + 0.5;
        s += `<line class="gridline" x1="${m.l}" x2="${w - m.r}" y1="${yy}" y2="${yy}"/>`;
        s += `<text class="tick" x="${m.l - 8}" y="${yy}" dy="0.32em" text-anchor="end">${esc(fmt.compact(t))}</text>`;
      }
      s += `<line class="baseline" x1="${m.l}" x2="${w - m.r}" y1="${Math.round(y(0)) + 0.5}" y2="${Math.round(y(0)) + 0.5}"/>`;
      const bw = Math.min(24, x.bandwidth);
      weeks.forEach((wk, k) => {
        const cx = x(wk) + x.bandwidth / 2;
        const bx = cx - bw / 2;
        const y0 = y(0);
        const yb = y(booked[k]);
        const ye = y(booked[k] + extra[k]);
        if (booked[k] > 0) s += colPath(bx, yb, bw, y0 - yb, extra[k] > 0 ? 0 : 4, 'var(--series-1)');
        if (extra[k] > 0) s += colPath(bx, ye, bw, Math.max(0, yb - ye - 2), 4, 'var(--series-other)');
        s += `<text class="tick" x="${cx.toFixed(1)}" y="${H - m.b + 17}" text-anchor="middle">${esc(fmt.date(wk))}</text>`;
      });
      // supply line with ringed markers
      const pts = weeks.map((wk, k) => [x(wk) + x.bandwidth / 2, y(g.landing[k])]);
      s += `<path class="series-line" d="${pts.map((p, k) => `${k ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('')}" style="stroke:var(--series-2)" stroke-width="2"/>`;
      pts.forEach((p) => { s += `<circle class="marker" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4" style="fill:var(--series-2)"/>`; });
      // hover targets: one per week
      weeks.forEach((wk, k) => {
        const tip = [`Week of ${fmt.date(wk)} · ${ZONE_LABEL[g.zone[k]]}`,
          `Orders due to ship: ${fmt.int(booked[k])}${k === 0 && g.past_due ? ` (${fmt.int(g.past_due)} past due)` : ''}`,
          `Forecast not yet booked: ${fmt.int(extra[k])}`,
          `Supply landing: ${fmt.int(g.landing[k])}`,
          `Projected balance: ${fmt.int(g.pab[k])}`].join('\n');
        s += `<rect class="mp-hit" x="${(x(wk) - (x.step - x.bandwidth) / 2).toFixed(1)}" y="${m.t}" width="${x.step.toFixed(1)}" height="${H - m.b - m.t}" fill="transparent" data-tip="${esc(tip)}"/>`;
      });
      return raw(s);
    },
  });
}

function colPath(x, y, w, h, r, color) {
  if (h <= 0.5) return '';
  const rr = Math.min(r, h, w / 2);
  const d = rr
    ? `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`
    : `M${x},${y + h}V${y}H${x + w}V${y + h}Z`;
  return `<path d="${d}" style="fill:${color}"/>`;
}

// ---------------------------------------------------------------------------
// MPS record
// ---------------------------------------------------------------------------
function mpsGrid(d, fam) {
  const g = d.grids[fam];
  const n = (v) => (v == null ? '' : fmt.int(v));
  const booked = g.booked.map((v, i) => v + (i === 0 ? g.past_due : 0));
  const skus = d.mps_by_sku.filter((r) => (fam === 'VEH' ? r.item_id.startsWith('LV1') : r.item_id.startsWith('PK')));
  const row = (label, sub, cells, cls = '') => html`<tr class="${cls}"><th scope="row" class="mp-sticky"><span class="mp-rl">${label}</span>${sub ? html`<span class="mp-rs">${sub}</span>` : ''}</th>${cells}</tr>`;
  const zc = (i) => `z-${g.zone[i]}`;
  return html`<div class="mp-scroll"><table class="mp-grid">
      <thead>
        <tr><th class="mp-sticky">Week of</th>${d.weeks.map((w, i) => html`<th class="${zc(i)}">${fmt.date(w)}<span class="mp-zn">${ZONE_LABEL[g.zone[i]]}</span></th>`)}</tr>
      </thead>
      <tbody>
        ${row('Forecast', d.forecast_version, d.weeks.map((w, i) => html`<td class="${zc(i)}">${n(g.forecast[i])}</td>`))}
        ${row('Booked orders', 'due to ship that week', d.weeks.map((w, i) => html`<td class="${zc(i)}">${n(booked[i])}${i === 0 && g.past_due ? html`<span class="mp-pd" title="${g.past_due} already past their ship-by date">+${g.past_due} late</span>` : ''}</td>`))}
        ${row('Projected demand', 'orders in frozen · max(forecast, orders) beyond', d.weeks.map((w, i) => html`<td class="${zc(i)} strong">${n(g.projected_demand[i])}</td>`), 'mp-strong')}
        ${row('MPS', fam === 'VEH' ? 'CM commit, by build week' : 'pack MPS, by build week', d.weeks.map((w, i) => html`<td class="${zc(i)}">${n(g.mps_build[i])}</td>`))}
        ${row('Supply landing', 'available to kit in Reno', d.weeks.map((w, i) => html`<td class="${zc(i)}">${n(g.landing[i])}${g.landing_unconstrained[i] > g.landing[i] ? html`<span class="mp-pd" title="Before the MRP constraint: ${g.landing_unconstrained[i]}">−${g.landing_unconstrained[i] - g.landing[i]} BMS</span>` : ''}</td>`))}
        ${row('Projected available', 'running balance', d.weeks.map((w, i) => html`<td class="${zc(i)}${g.pab[i] < 0 ? ' neg' : ''}">${g.pab[i] < 0 ? html`<span class="mp-neg">${icon('alert-octagon', 11)}</span>` : ''}${n(g.pab[i])}</td>`), 'mp-strong')}
        ${row('Available to promise', 'cumulative, with look-ahead', d.weeks.map((w, i) => html`<td class="${zc(i)}${g.atp_cumulative[i] < 0 ? ' neg' : ''}">${n(g.atp_cumulative[i])}</td>`))}
        <tr class="mp-sep"><th class="mp-sticky" colspan="${d.weeks.length + 1}">MPS by SKU (builds)</th></tr>
        ${skus.map((r) => row(html`<span class="mono">${r.item_id}</span>`, '', d.weeks.map((w, i) => html`<td class="${zc(i)}">${n(r.weeks[i])}</td>`)))}
      </tbody>
    </table></div>
    <p class="mp-foot small muted">Starting stock at the 3PL (${fmt.int(g.on_hand_start)} unallocated) is inside the first week’s supply. Negative ATP means promises already exceed supply in that window: nothing more can be promised there, and ${link.route('atp', 'ATP')} moves new orders later.</p>`;
}

// ---------------------------------------------------------------------------
// RCCP
// ---------------------------------------------------------------------------
function rccpChart(d) {
  const series = d.rccp.map((r, i) => ({
    name: `${r.label} vs demonstrated`,
    points: r.weeks.filter((w) => w.load_vs_demonstrated != null).map((w) => ({ x: w.week, y: w.load_vs_demonstrated })),
    colorIndex: i,
  }));
  return charts.line({
    series,
    height: 220,
    yMin: 0.6,
    yMax: 1.4,
    markers: true,
    refLines: [{ y: 1, tone: 'critical' }],
    yFormat: (v) => `${Math.round(v * 100)}%`,
    xLabel: 'Week',
    ariaLabel: 'Planned load as a share of demonstrated capacity per line',
  });
}

function rccpTable(d) {
  const tone = (v) => (v == null ? '' : v > 1.05 ? 'over' : v > 0.98 ? 'tight' : v < 0.8 ? 'under' : 'ok');
  return html`<div class="table-wrap mp-rccp"><table class="table dense">
    <thead><tr><th>Line</th><th>Measure</th>${d.weeks.map((w) => html`<th class="num">${fmt.date(w)}</th>`)}</tr></thead>
    <tbody>${d.rccp.map((r) => html`
      <tr><td rowspan="4" class="mp-line"><strong>${r.label}</strong><div class="muted small">${fmt.num(r.demonstrated_per_day, 1)}/day demonstrated</div></td>
        <td class="small">Planned</td>${r.weeks.map((w) => html`<td class="num">${fmt.int(w.planned)}</td>`)}</tr>
      <tr><td class="small">Rated capacity</td>${r.weeks.map((w) => html`<td class="num muted">${fmt.int(w.capacity)}</td>`)}</tr>
      <tr><td class="small">Demonstrated</td>${r.weeks.map((w) => html`<td class="num muted">${fmt.int(w.demonstrated)}</td>`)}</tr>
      <tr class="mp-load"><td class="small">Load vs demonstrated</td>${r.weeks.map((w) => html`<td class="num mp-${tone(w.load_vs_demonstrated)}" title="Planned ÷ demonstrated; rated load ${w.load != null ? Math.round(w.load * 100) + '%' : '—'}">${w.load_vs_demonstrated != null ? `${Math.round(w.load_vs_demonstrated * 100)}%` : '—'}</td>`)}</tr>`)}
    </tbody></table></div>
    <div class="mp-key small muted">
      <span><span class="mp-sw over"></span>${icon('alert-triangle', 12)} Over (&gt;105%)</span>
      <span><span class="mp-sw tight"></span>Tight (98–105%)</span>
      <span><span class="mp-sw ok"></span>OK</span>
      <span><span class="mp-sw under"></span>Under-loaded (&lt;80%)</span>
      ${rateChanges(d).map((t) => html`<span>${t}</span>`)}
    </div>`;
}

function rateChanges(d) {
  // rated capacity is effective-dated: call out a step change inside the horizon
  const out = [];
  for (const r of d.rccp) {
    const per = r.weeks.map((w) => (w.workdays ? w.capacity / w.workdays : null));
    const first = per.find((v) => v != null);
    const k = per.findIndex((v) => v != null && first != null && Math.abs(v - first) > 0.01);
    if (k > 0) out.push(`${r.label} rated capacity steps from ${fmt.int(first)} to ${fmt.int(per[k])}/day in the week of ${fmt.date(r.weeks[k].week)}.`);
  }
  return out;
}

// ---------------------------------------------------------------------------
// balance, loss, S&OP
// ---------------------------------------------------------------------------
function balanceChart(d) {
  return charts.line({
    series: [
      { name: 'Vehicles landing', points: d.balance.map((b) => ({ x: b.week, y: b.vehicles })) },
      { name: 'Packs landing', points: d.balance.map((b) => ({ x: b.week, y: b.packs })) },
      { name: 'Packs before the BMS constraint', points: d.balance.map((b) => ({ x: b.week, y: b.packs_unconstrained })), dashed: true, colorIndex: 1 },
    ],
    height: 230,
    markers: true,
    xLabel: 'Week',
    ariaLabel: 'Vehicles and packs landing in Reno per week',
  });
}

function lossChart(d) {
  if (!d.pack_loss.length) return ui.empty('No pack output is lost: every component covers the pack MPS.', { icon: 'check-circle' });
  return html`${charts.bar({
    categories: d.pack_loss.map((x) => x.date),
    categoryFormat: (c) => fmt.date(c),
    series: [{ name: 'Packs lost', values: d.pack_loss.map((x) => Math.round(x.lost)) }],
    height: 200,
    valueLabels: true,
    ariaLabel: 'Pack output lost per day',
  })}<p class="small muted mp-lossnote">${fmt.int(d.pack_loss_total)} packs lost of ${fmt.int(d.pack_loss.reduce((a, x) => a + x.planned, 0))} planned on those days. The Closed Loop’s expedite (split the late BMS line and air-freight it) closes the gap; re-run MRP after it executes.</p>`;
}

function sopBlock(d) {
  const actual = d.sop.actual;
  const series = [
    { name: 'D2C orders (actual)', points: actual.map((a) => ({ x: a.week, y: a.orders, flag: a.complete ? null : 'warning' })) },
    ...d.sop.versions.map((v, i) => ({ name: v.version, points: v.weeks.map((w) => ({ x: w.week, y: w.qty })), colorIndex: i + 1, dashed: i === 0 })),
  ];
  return html`${charts.line({
    series,
    height: 240,
    xLabel: 'Week',
    ariaLabel: 'S&OP versions against actual orders',
  })}
  <div class="table-wrap mp-acc"><table class="table dense">
    <thead><tr><th>Version</th><th>Published</th><th class="num">Weeks scored</th><th class="num">MAPE</th><th class="num">Bias</th><th>Reading</th></tr></thead>
    <tbody>${d.sop.versions.map((v) => html`<tr>
      <td class="mono">${v.version}</td><td>${fmt.date(v.published_at)}</td><td class="num">${v.accuracy_weeks}</td>
      <td class="num">${v.mape != null ? fmt.pct(v.mape, 1) : '—'}</td>
      <td class="num">${v.bias != null ? `${v.bias > 0 ? '+' : ''}${fmt.pct(v.bias, 1)}` : '—'}</td>
      <td class="small">${v.accuracy_weeks === 0 ? 'Not enough complete weeks since publication' : v.bias > 0.02 ? 'Ran high: planned more demand than came' : v.bias < -0.02 ? 'Ran low: demand beat the plan' : 'On target so far'}</td>
    </tr>`)}</tbody></table></div>
  <p class="small muted mp-foot">The current week is incomplete (flagged) and is not scored. The forecast only drives supply beyond the frozen zone; inside it, booked orders do.</p>`;
}

const PAGE_CSS = `
.pg-mps .mp-context { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; margin: -4px 0 16px; font-size: 13px; color: var(--ink-2); }
.pg-mps .mp-context > span { display: inline-flex; align-items: center; gap: 6px; }
.pg-mps .mp-context svg { color: var(--sign); }
.pg-mps .mp-insights { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; margin: 16px 0; }
.pg-mps .mp-filter { margin: 4px 0 16px; }
.pg-mps .mp-fences { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
.pg-mps .mp-fence { display: grid; gap: 3px; padding: 9px 12px; border-radius: 10px; border: 1px solid var(--hairline); font-size: 12.5px; }
.pg-mps .mp-fence.frozen { background: color-mix(in srgb, var(--series-1) 10%, var(--surface)); }
.pg-mps .mp-fence.slushy { background: color-mix(in srgb, var(--series-1) 5%, var(--surface)); }
.pg-mps .mp-fence.liquid { background: var(--surface); }
.pg-mps .mp-fz { display: inline-flex; align-items: center; gap: 5px; font: 600 12.5px var(--font-cond); letter-spacing: .04em; text-transform: uppercase; color: var(--ink); }
.pg-mps .mp-zone.frozen { fill: color-mix(in srgb, var(--series-1) 9%, transparent); }
.pg-mps .mp-zone.slushy { fill: color-mix(in srgb, var(--series-1) 4%, transparent); }
.pg-mps .mp-zone-label { font: 600 10.5px var(--font-cond); letter-spacing: .08em; text-transform: uppercase; fill: var(--ink-3); }
.pg-mps .mp-scroll { overflow-x: auto; }
.pg-mps .mp-grid { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.pg-mps .mp-grid th, .pg-mps .mp-grid td { height: 34px; padding: 0 8px; text-align: right; border-bottom: 1px solid var(--hairline); white-space: nowrap; min-width: 64px; }
.pg-mps .mp-grid thead th { background: var(--surface-2); font: 600 11.5px/1.15 var(--font-cond); color: var(--ink-2); height: 42px; }
.pg-mps .mp-zn { display: block; font: 500 10px var(--font-cond); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
.pg-mps .mp-grid .mp-sticky { position: sticky; left: 0; z-index: 2; background: var(--surface); text-align: left; min-width: 210px; border-right: 1px solid var(--hairline-strong); }
.pg-mps .mp-grid thead .mp-sticky { background: var(--surface-2); z-index: 3; }
.pg-mps .mp-rl { display: block; font: 600 13px/1.15 var(--font-cond); color: var(--ink); }
.pg-mps .mp-rs { display: block; font: 400 11px/1.2 var(--font-ui); color: var(--ink-3); white-space: normal; }
.pg-mps .mp-grid tbody th { height: 40px; }
.pg-mps .mp-grid td.z-frozen, .pg-mps .mp-grid th.z-frozen { background: color-mix(in srgb, var(--series-1) 6%, var(--surface)); }
.pg-mps .mp-grid td.z-slushy, .pg-mps .mp-grid th.z-slushy { background: color-mix(in srgb, var(--series-1) 3%, var(--surface)); }
.pg-mps .mp-grid tr.mp-strong td { font-weight: 600; color: var(--ink); }
.pg-mps .mp-grid td.neg { background: color-mix(in srgb, var(--critical) 13%, var(--surface)); font-weight: 700; }
.pg-mps .mp-neg { color: var(--critical); margin-right: 3px; vertical-align: -1px; }
.pg-mps .mp-pd { display: block; font: 500 10px var(--font-ui); color: var(--ink-3); }
.pg-mps .mp-grid tr.mp-sep th { height: 28px; background: var(--surface-2); font: 600 11px var(--font-cond); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); }
.pg-mps .mp-foot { padding: 10px 16px 12px; border-top: 1px solid var(--hairline); }
.pg-mps .mp-rccp { margin-top: 14px; border: 1px solid var(--hairline); border-radius: 10px; }
.pg-mps .mp-rccp td.mp-line { vertical-align: top; padding-top: 8px; border-right: 1px solid var(--hairline); background: var(--surface); }
.pg-mps .mp-rccp tr.mp-load td { font-weight: 600; }
.pg-mps .mp-rccp td.mp-over { background: color-mix(in srgb, var(--serious) 20%, var(--surface)); }
.pg-mps .mp-rccp td.mp-tight { background: color-mix(in srgb, var(--warning) 18%, var(--surface)); }
.pg-mps .mp-rccp td.mp-under { background: color-mix(in srgb, var(--info) 10%, var(--surface)); }
.pg-mps .mp-key { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 10px; align-items: center; }
.pg-mps .mp-key > span { display: inline-flex; align-items: center; gap: 5px; }
.pg-mps .mp-key svg { color: var(--serious); }
.pg-mps .mp-sw { display: inline-block; width: 14px; height: 12px; border-radius: 3px; border: 1px solid var(--hairline); }
.pg-mps .mp-sw.over { background: color-mix(in srgb, var(--serious) 20%, var(--surface)); }
.pg-mps .mp-sw.tight { background: color-mix(in srgb, var(--warning) 18%, var(--surface)); }
.pg-mps .mp-sw.ok { background: var(--surface); }
.pg-mps .mp-sw.under { background: color-mix(in srgb, var(--info) 10%, var(--surface)); }
.pg-mps .mp-acc { margin-top: 12px; border: 1px solid var(--hairline); border-radius: 10px; }
.pg-mps .mp-lossnote { margin-top: 8px; }
.pg-mps .mp-hit { cursor: default; }
.pg-mps .mp-hit:hover { fill: color-mix(in srgb, var(--ink) 4%, transparent); }
@media (max-width: 760px) {
  .pg-mps .mp-fences { grid-template-columns: 1fr; }
}
`;
