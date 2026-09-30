// Production & Yield (MES): the CM's two vehicle lines in Taichung and the OEM's pack
// line in Fremont. WIP by station, first-pass and rolled-throughput yield, output
// against plan, line balance against takt, OEE, and the Paretos behind them.
import { html, raw, esc, on, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const LINE_IDS = ['L1', 'L2', 'P1'];
const SEQ = ['var(--seq-100)', 'var(--seq-200)', 'var(--seq-300)', 'var(--seq-450)', 'var(--seq-550)', 'var(--seq-700)'];
const FAIL_BUCKETS = [
  { max: 0.0, color: 'var(--surface-2)', label: '0%' },
  { max: 0.01, color: SEQ[0], label: '≤1%' },
  { max: 0.025, color: SEQ[1], label: '≤2.5%' },
  { max: 0.05, color: SEQ[2], label: '≤5%' },
  { max: 0.1, color: SEQ[3], label: '≤10%' },
  { max: 0.2, color: SEQ[4], label: '≤20%' },
  { max: 1.01, color: SEQ[5], label: '>20%' },
];
const textW = (s, px = 11) => String(s).length * px * 0.58;

let S = null;

export async function render(el, ctx) {
  injectStyle('page-production', PAGE_CSS);
  const lineId = LINE_IDS.includes(ctx.query.get('line')) ? ctx.query.get('line') : 'L1';
  const d = await api.get('/api/production/overview', { line: lineId });
  S = { el, ctx, d };
  const L = d.line;
  const k = d.kpis;
  const tzLabel = L.tz === 'Asia/Taipei' ? 'Taipei, UTC+8' : 'Pacific';
  const lastLabel = k.last_day ? fmt.dateLong(k.last_day) : '—';
  const planAttain = attainment(d.output_vs_plan.slice(-7));

  el.innerHTML = html`
    ${ui.pageHeader({ actions: html`<div class="pg-production-seg" data-seg></div>` })}
    <div class="pg-production">
      <p class="pp-context">
        <span class="pp-place">${icon(L.site_id === 'CM-TXG' ? 'factory' : 'bolt', 15)}<strong>${L.label}</strong> · ${L.place}</span>
        <span>${tzLabel}</span>
        <span>1 shift × ${fmt.int(L.shift_minutes)} min</span>
        <span>takt <strong>${fmt.num(L.takt, 1)} min</strong></span>
        <span>rated ${fmt.int(L.rated_per_day)}/day</span>
        <span class="muted">Source: ${L.site_id === 'CM-TXG' ? html`the CM's MES via the ${link.route('cm-feed', 'CM feed')}` : 'OEM pack-line MES (native)'}</span>
      </p>

      <div class="kpi-row">
        ${ui.kpi({
          label: 'Built, last shift', value: fmt.int(k.output_last), unit: `/ ${fmt.int(k.plan_last)} plan`,
          delta: k.output_prev != null ? k.output_last - k.output_prev : null, deltaLabel: 'vs prior shift',
          hint: `${fmt.date(k.last_day)} · ${L.tz === 'Asia/Taipei' ? 'Taipei' : 'Fremont'}`,
        })}
        ${ui.kpi({
          label: 'Work in process', value: fmt.int(k.wip), unit: 'units',
          hint: k.wip_rework ? `${k.wip_rework} waiting in rework` : 'Carried across shifts',
        })}
        ${ui.kpi({
          label: 'First-pass yield', value: k.fpy_last == null ? '—' : fmt.pct(k.fpy_last, 1),
          hint: `Last shift · no fail anywhere`,
        })}
        ${ui.kpi({
          label: 'Rolled yield · 7 days', value: k.rty7 == null ? '—' : fmt.pct(k.rty7, 1),
          hint: `Product of ${d.station_fpy7.length} station FPYs`,
        })}
        ${ui.kpi({
          label: 'OEE · 7 days', value: k.oee7 == null ? '—' : fmt.pct(k.oee7, 0),
          hint: d.oee ? `${k.oee7 >= d.oee.target ? 'At' : 'Below'} ${fmt.pct(d.oee.target, 0)} target` : '',
        })}
        ${ui.kpi({
          label: 'Plan attainment · 7 days', value: planAttain == null ? '—' : fmt.pct(planAttain, 0),
          hint: 'Built ÷ committed', spark: k.output_spark,
        })}
      </div>

      ${d.insights.length ? html`<div class="pp-insights">${d.insights.map((i) => ui.callout({
        tone: i.tone, title: i.title,
        body: html`${i.body}${i.link ? html` <a class="ent-link" href="${i.link}">${i.link_label || 'Open'} →</a>` : ''}`,
      }))}</div>` : ''}

      <div class="grid">
        <div class="span-7">${ui.card({
          title: 'Work in process by station',
          subtitle: `Where each unit is waiting now. Queued after a pass, or held in rework after a fail. Click a station for its units.`,
          body: html`${wipFlow(d)}<div class="pp-wiplist" data-wip></div>`,
        })}</div>
        <div class="span-5">${ui.card({
          title: 'OEE, last 7 production days',
          subtitle: d.oee ? `Ideal cycle = bottleneck ${d.bottleneck} at ${fmt.num(d.oee.ideal_cycle, 1)} min/unit` : '',
          body: oeeBlock(d),
        })}</div>

        <div class="span-7">${ui.card({
          title: 'Output vs plan', subtitle: `Final-station passes (${L.final}) per production day against the build plan`,
          tableToggle: true, body: outputChart(d),
        })}</div>
        <div class="span-5">${ui.card({
          title: 'Line balance', subtitle: `Effective cycle per station (standard ÷ parallel fixtures) against takt ${fmt.num(d.takt, 1)} min`,
          tableToggle: true, body: balanceChart(d),
        })}</div>

        <div class="span-12">${ui.card({
          title: 'Station yield, last 30 days',
          subtitle: 'Share of units failing their first attempt at each station (first-pass fails ÷ units). The stronger the blue, the more first-attempt failures. Hover a cell for counts.',
          tableToggle: true, body: fpyGrid(d),
        })}</div>

        <div class="span-6">${ui.card({
          title: 'Defect Pareto, last 30 days', subtitle: 'Failed first attempts by defect code', tableToggle: true,
          body: defectChart(d),
        })}</div>
        <div class="span-6">${ui.card({
          title: 'Downtime Pareto, last 30 days', subtitle: 'Minutes lost by reason (feeds OEE availability)', tableToggle: true,
          body: downtimeChart(d),
        })}</div>

        <div class="span-12">${ui.card({
          title: 'Work orders', subtitle: 'MES work orders for the last week and the frozen days ahead (one per line, SKU and day)',
          flush: true, body: html`<div data-wo></div>`,
        })}</div>
      </div>
    </div>`;

  const segEl = el.querySelector('[data-seg]');
  ui.segmented(segEl, {
    options: d.lines.map((l) => ({ value: l.id, label: `${l.id === 'P1' ? 'Pack P1' : 'CM ' + l.id} · ${l.wip} WIP` })),
    value: lineId, label: 'Line',
    onChange: (v) => ctx.setQuery({ line: v }),
  });

  const nowMs = Date.parse(d.now);
  const wipRows = d.wip_by_station.flatMap((st) => st.units.map((u) => ({ ...u, position: st.code,
    age: (nowMs - Date.parse(u.last_event)) / 3600000 })));
  ui.dataTable(el.querySelector('[data-wip]'), {
    columns: [
      { key: 'serial', label: 'Unit', mono: true, render: (r) => html`<a class="id-link" href="#" data-unit="${r.serial}">${r.serial}</a>` },
      { key: 'item_id', label: 'SKU', mono: true },
      { key: 'position', label: 'At' },
      { key: 'state', label: 'State', render: (r) => (r.state === 'rework' ? ui.chip('serious', 'Rework') : ui.chip('info', 'Queued')) },
      { key: 'last_event', label: `Last event (${L.tz === 'Asia/Taipei' ? 'Taipei' : 'PT'})`, render: (r) => html`${r.last_code} ${fmt.title(r.result)} · ${fmt.dt(r.last_event, L.tz)}` },
      { key: 'age', label: 'Waiting', num: true, format: (v) => `${fmt.num(v, 0)} h` },
    ],
    rows: wipRows, pageSize: 8, dense: true, initialSort: { key: 'age', dir: 'desc' }, empty: 'No units in process.',
  });

  ui.dataTable(el.querySelector('[data-wo]'), {
    columns: [
      { key: 'wo_id', label: 'Work order', mono: true },
      { key: 'item_id', label: 'SKU', render: (r) => link.item(r.item_id) },
      { key: 'sched_date', label: 'Day', format: (v) => fmt.date(v) },
      { key: 'qty_planned', label: 'Planned', num: true },
      { key: 'qty_started', label: 'Started', num: true },
      { key: 'qty_completed', label: 'Completed', num: true },
      { key: 'qty_scrapped', label: 'Scrap', num: true },
      { key: 'progress', label: 'Progress', sortable: false, width: 140,
        value: (r) => (r.qty_planned ? r.qty_completed / r.qty_planned : 0),
        render: (r) => ui.meter({ value: Math.min(r.qty_completed, r.qty_planned), max: r.qty_planned || 1,
          tone: r.status === 'RELEASED' ? null : (r.qty_completed >= r.qty_planned ? 'good' : 'info') }) },
      { key: 'status', label: 'Status', render: (r) => ui.statusChip(r.status) },
    ],
    rows: d.work_orders, pageSize: 12, initialSort: { key: 'sched_date', dir: 'desc' },
  });

  on(el, 'click', '[data-station]', (e, t) => openStation(t.dataset.station));
  on(el, 'click', '[data-unit]', (e, t) => { e.preventDefault(); openUnit(t.dataset.unit); });
}

export function unmount() {
  if (ui.drawer.isOpen) ui.drawer.close();
  S = null;
}

function attainment(rows) {
  const plan = rows.reduce((a, r) => a + (r.plan || 0), 0);
  const act = rows.reduce((a, r) => a + (r.actual || 0), 0);
  return plan ? act / plan : null;
}

// ---------------------------------------------------------------------------
// WIP flow: stations as stops on a line, units as markers queued above them
// ---------------------------------------------------------------------------
function wipFlow(d) {
  const st = d.wip_by_station;
  const maxStack = Math.max(4, ...st.map((s) => s.queued + s.rework));
  const dot = 11;
  const height = (w) => 118 + Math.min(maxStack, 10) * (dot + 3);
  const tableData = {
    columns: ['Station', 'Queued', 'In rework', 'Units'],
    rows: st.map((s) => [`${s.code} · ${s.name}`, s.queued, s.rework, s.units.map((u) => u.serial).join(', ') || '—']),
    numeric: [true, true, false],
  };
  return html`${charts.custom({
    height,
    ariaLabel: 'Work in process by station',
    table: tableData,
    render: (w, H) => {
      const pad = 18;
      const n = st.length;
      const step = (w - pad * 2) / n;
      const baseY = H - 64;
      const narrow = step < 74;
      let s = `<line class="pp-route" x1="${pad}" x2="${w - pad}" y1="${baseY}" y2="${baseY}"/>`;
      st.forEach((stn, i) => {
        const cx = pad + step * i + step / 2;
        const total = stn.queued + stn.rework;
        const shown = Math.min(total, 10);
        const units = stn.units.slice(0, shown);
        units.forEach((u, j) => {
          const cy = baseY - 18 - j * (dot + 3);
          const cls = u.state === 'rework' ? 'pp-dot rework' : 'pp-dot';
          s += `<circle class="${cls}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${dot / 2}"/>`;
        });
        if (total > shown) {
          s += `<text class="pp-more" x="${cx.toFixed(1)}" y="${(baseY - 18 - shown * (dot + 3) - 2).toFixed(1)}" text-anchor="middle">+${total - shown}</text>`;
        }
        const countY = baseY - 18 - Math.max(shown, 1) * (dot + 3) - (total > shown ? 14 : 2);
        s += `<text class="pp-count${total ? '' : ' zero'}" x="${cx.toFixed(1)}" y="${countY.toFixed(1)}" text-anchor="middle">${total}</text>`;
        const bn = d.bottleneck === stn.code;
        s += `<circle class="pp-stop${bn ? ' bottleneck' : ''}" cx="${cx.toFixed(1)}" cy="${baseY}" r="7"/>`;
        s += `<text class="pp-code" x="${cx.toFixed(1)}" y="${baseY + 24}" text-anchor="middle">${esc(stn.code)}</text>`;
        if (!narrow) {
          const maxChars = Math.max(6, Math.floor(step / 6.2));
          const words = stn.name.split(' ');
          let l1 = '';
          let l2 = '';
          for (const wd of words) {
            if ((l1 + ' ' + wd).trim().length <= maxChars && !l2) l1 = (l1 + ' ' + wd).trim();
            else l2 = (l2 + ' ' + wd).trim();
          }
          if (l2.length > maxChars) l2 = l2.slice(0, maxChars - 1) + '…';
          s += `<text class="pp-name" x="${cx.toFixed(1)}" y="${baseY + 39}" text-anchor="middle">${esc(l1)}</text>`;
          if (l2) s += `<text class="pp-name" x="${cx.toFixed(1)}" y="${baseY + 52}" text-anchor="middle">${esc(l2)}</text>`;
        }
        const tip = [`${stn.code} · ${stn.name}`, `${stn.queued} queued · ${stn.rework} in rework`]
          .concat(stn.units.slice(0, 6).map((u) => `${u.serial}: last ${u.last_code} ${u.result.toLowerCase()}${u.defect_code ? ' (' + u.defect_code + ')' : ''}`))
          .concat(bn ? ['Bottleneck station'] : []).join('\n');
        s += `<rect class="pp-hit" data-station="${esc(stn.code)}" data-tip="${esc(tip)}" x="${(cx - step / 2).toFixed(1)}" y="0" width="${step.toFixed(1)}" height="${H}" fill="transparent" tabindex="0"/>`;
      });
      return raw(s);
    },
    legend: html`<div class="legend"><span class="legend-item"><span class="pp-lg-dot"></span>Queued after a pass</span><span class="legend-item"><span class="pp-lg-dot rework"></span>In rework after a fail</span><span class="legend-item"><span class="pp-lg-stop"></span>Bottleneck station</span></div>`,
  })}`;
}

// ---------------------------------------------------------------------------
// OEE ladder: availability × performance × quality, with the 30-day trend
// ---------------------------------------------------------------------------
function oeeBlock(d) {
  const o = d.oee;
  if (!o) return ui.empty('Not enough production days for OEE.');
  const rows = [
    { k: 'Availability', v: o.availability, hint: `${fmt.int(o.run_min)} of ${fmt.int(o.planned_min)} planned min ran · ${fmt.int(o.downtime_min)} min down` },
    { k: 'Performance', v: o.performance, hint: `${fmt.int(o.units)} units × ${fmt.num(o.ideal_cycle, 1)} min ideal ÷ ${fmt.int(o.run_min)} run min` },
    { k: 'Quality', v: o.quality, hint: `${fmt.int(o.good_units)} of ${fmt.int(o.units)} right first time` },
  ];
  const perfNote = o.performance < 0.8 && d.takt && d.oee.ideal_cycle
    ? html`<p class="small muted pp-note">${icon('info', 14)} Most of the performance loss is planned. The line is committed at takt ${fmt.num(d.takt, 1)} min, slower than its ${fmt.num(o.ideal_cycle, 1)}-min bottleneck, which caps it at ${fmt.int(Math.floor((d.line.shift_minutes || 570) / o.ideal_cycle))}/day. Against takt, performance is ${fmt.pct(Math.min(1, (o.units * d.takt) / o.run_min), 0)}.</p>`
    : '';
  const series = [{ name: 'OEE', points: d.oee_daily.filter((x) => x.oee != null).map((x) => ({ x: x.day, y: x.oee })) }];
  return html`<div class="pp-oee">
      <div class="pp-oee-total"><span class="pp-oee-val">${fmt.pct(o.oee, 1)}</span><span class="muted small">OEE · target ${fmt.pct(o.target, 0)}</span></div>
      ${rows.map((r) => html`<div class="pp-oee-row">
        <div class="row"><span class="strong">${r.k}</span><span class="spacer"></span><span class="num strong">${fmt.pct(r.v, 1)}</span></div>
        ${ui.meter({ value: r.v, max: 1 })}
        <div class="tiny muted">${r.hint}</div>
      </div>`)}
      ${perfNote}
    </div>
    ${charts.line({ series, height: 150, yMin: 0, yMax: 1, yFormat: (v) => fmt.pct(v, 0),
      refLines: [{ y: o.target, label: `Target ${fmt.pct(o.target, 0)}`, tone: 'good' }], ariaLabel: 'Daily OEE' })}`;
}

// ---------------------------------------------------------------------------
// output vs plan
// ---------------------------------------------------------------------------
function outputChart(d) {
  const rows = d.output_vs_plan;
  return charts.bar({
    categories: rows.map((r) => r.day),
    categoryFormat: (c) => fmt.date(c),
    categoryLabel: 'Day',
    series: [
      { name: 'Plan', values: rows.map((r) => r.plan), color: 'var(--series-other)' },
      { name: 'Built', values: rows.map((r) => r.actual), colorIndex: 0 },
    ],
    height: 230, yFormat: (v) => fmt.int(v), ariaLabel: 'Output vs plan by day',
  });
}

// ---------------------------------------------------------------------------
// line balance: effective cycle per station, takt line, bottleneck emphasized
// ---------------------------------------------------------------------------
function balanceChart(d) {
  const b = d.balance;
  const takt = d.takt || 0;
  const maxV = Math.max(takt, ...b.map((x) => x.effective)) * 1.15;
  return charts.custom({
    height: 230,
    ariaLabel: 'Line balance',
    table: {
      columns: ['Station', 'Standard cycle (min)', 'Parallel fixtures', 'Effective (min)', 'vs takt'],
      rows: b.map((x) => [`${x.code} · ${x.name}`, fmt.num(x.std_cycle, 1), x.parallel, fmt.num(x.effective, 2),
        takt ? fmt.pct(x.effective / takt, 0) : '—']),
      numeric: [true, true, true, true],
    },
    render: (w, H) => {
      const m = { t: 18, r: 12, b: 28, l: 40 };
      const iw = w - m.l - m.r;
      const ih = H - m.t - m.b;
      const sy = charts.scaleLinear([0, maxV], [m.t + ih, m.t]);
      const band = charts.scaleBand(b.map((x) => x.code), [m.l, m.l + iw], 0.35);
      const bw = Math.min(24, band.bandwidth);
      let s = '';
      for (const t of charts.niceTicks(0, maxV, 4)) {
        if (t > maxV) continue;
        const y = Math.round(sy(t)) + 0.5;
        s += `<line class="gridline" x1="${m.l}" x2="${m.l + iw}" y1="${y}" y2="${y}"/><text class="tick" x="${m.l - 8}" y="${y}" dy="0.32em" text-anchor="end">${t}</text>`;
      }
      b.forEach((x) => {
        const bx = band(x.code) + (band.bandwidth - bw) / 2;
        const y = sy(x.effective);
        const h = sy(0) - y;
        const bn = x.code === d.bottleneck;
        const r = Math.min(4, bw / 2, h);
        s += `<path class="pp-bal${bn ? ' bn' : ''}" d="M${bx},${sy(0)}V${y + r}A${r},${r} 0 0 1 ${bx + r},${y}H${bx + bw - r}A${r},${r} 0 0 1 ${bx + bw},${y + r}V${sy(0)}Z"/>`;
        s += `<text class="value-label" x="${bx + bw / 2}" y="${y - 5}" text-anchor="middle">${fmt.num(x.effective, 1)}</text>`;
        s += `<text class="tick" x="${bx + bw / 2}" y="${m.t + ih + 17}" text-anchor="middle">${esc(x.code)}</text>`;
        const tip = `${x.code} · ${x.name}\nStandard ${fmt.num(x.std_cycle, 1)} min${x.parallel > 1 ? ` ÷ ${x.parallel} fixtures` : ''} = ${fmt.num(x.effective, 2)} min effective\n${takt ? fmt.pct(x.effective / takt, 0) + ' of takt' : ''}${bn ? '\nBottleneck' : ''}`;
        s += `<rect class="bar-hit" data-tip="${esc(tip)}" x="${band(x.code) - (band.step - band.bandwidth) / 2}" y="${m.t}" width="${band.step}" height="${ih}" fill="transparent"/>`;
      });
      s += `<line class="baseline" x1="${m.l}" x2="${m.l + iw}" y1="${Math.round(sy(0)) + 0.5}" y2="${Math.round(sy(0)) + 0.5}"/>`;
      if (takt) {
        const ty = Math.round(sy(takt)) + 0.5;
        s += `<line class="pp-takt" x1="${m.l}" x2="${m.l + iw}" y1="${ty}" y2="${ty}"/>`;
        s += `<text class="ref-label" x="${m.l + iw}" y="${ty - 5}" text-anchor="end">Takt ${fmt.num(takt, 1)} min</text>`;
      }
      return raw(s);
    },
  });
}

// ---------------------------------------------------------------------------
// station × day first-attempt fail grid
// ---------------------------------------------------------------------------
function fpyGrid(d) {
  const codes = d.station_fpy7.map((s) => s.code);
  const names = Object.fromEntries(d.station_fpy7.map((s) => [s.code, s.name]));
  const daysSet = new Set();
  for (const c of codes) for (const p of d.fpy_trend[c] || []) daysSet.add(p.day);
  const days = Array.from(daysSet).sort().slice(-30);
  const cell = {};
  for (const c of codes) for (const p of d.fpy_trend[c] || []) cell[`${c}|${p.day}`] = p;
  const bucket = (fail) => FAIL_BUCKETS.find((b) => fail <= b.max) || FAIL_BUCKETS[FAIL_BUCKETS.length - 1];
  const fpy7 = Object.fromEntries(d.station_fpy7.map((s) => [s.code, s]));
  const legend = html`<div class="legend pp-scale"><span class="muted tiny">First-attempt fail rate</span>${FAIL_BUCKETS.map((b) => html`<span class="legend-item"><span class="legend-key rect" style="--c:${b.color}"></span>${b.label}</span>`)}</div>`;
  return charts.custom({
    height: (w) => 36 + codes.length * 26,
    ariaLabel: 'Station yield by day',
    legend,
    table: {
      columns: ['Station', ...days.map((x) => fmt.date(x)), '7-day FPY'],
      rows: codes.map((c) => [c, ...days.map((x) => (cell[`${c}|${x}`] ? fmt.pct(cell[`${c}|${x}`].fpy, 0) : '—')),
        fpy7[c].fpy == null ? '—' : fmt.pct(fpy7[c].fpy, 1)]),
      numeric: days.map(() => true).concat([true]),
    },
    render: (w, H) => {
      const labelW = w < 560 ? 40 : 190;
      const rightW = 64;
      const top = 4;
      const gw = w - labelW - rightW - 8;
      const cw = gw / Math.max(days.length, 1);
      const ch = 22;
      let s = '';
      codes.forEach((c, ri) => {
        const y = top + ri * 26;
        const label = w < 560 ? c : `${c} · ${names[c]}`;
        const maxChars = Math.floor((labelW - 8) / 6.6);
        s += `<text class="cat-label" x="${labelW - 8}" y="${y + ch / 2}" dy="0.32em" text-anchor="end">${esc(label.length > maxChars ? label.slice(0, maxChars - 1) + '…' : label)}</text>`;
        days.forEach((day, ci) => {
          const p = cell[`${c}|${day}`];
          const x = labelW + ci * cw;
          if (!p) {
            s += `<rect class="pp-cell empty" x="${(x + 1).toFixed(1)}" y="${y}" width="${Math.max(1, cw - 2).toFixed(1)}" height="${ch}" rx="3"/>`;
            return;
          }
          const fail = 1 - p.fpy;
          const b = bucket(fail);
          const tip = `${c} · ${fmt.dateLong(day)}\nFPY ${fmt.pct(p.fpy, 1)}: ${p.first_pass} of ${p.units} passed first time`;
          s += `<rect class="pp-cell" data-tip="${esc(tip)}" style="fill:${b.color}" x="${(x + 1).toFixed(1)}" y="${y}" width="${Math.max(1, cw - 2).toFixed(1)}" height="${ch}" rx="3"/>`;
        });
        const f7 = fpy7[c].fpy;
        s += `<text class="pp-fpy7" x="${w - 4}" y="${y + ch / 2}" dy="0.32em" text-anchor="end">${f7 == null ? '—' : fmt.pct(f7, 1)}</text>`;
      });
      const every = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor(gw / 64))));
      days.forEach((day, ci) => {
        if (ci % every) return;
        s += `<text class="tick" x="${(labelW + ci * cw + cw / 2).toFixed(1)}" y="${top + codes.length * 26 + 14}" text-anchor="middle">${esc(fmt.date(day))}</text>`;
      });
      s += `<text class="tick" x="${w - 4}" y="${top + codes.length * 26 + 14}" text-anchor="end">7-day</text>`;
      return raw(s);
    },
  });
}

// ---------------------------------------------------------------------------
// Paretos
// ---------------------------------------------------------------------------
function defectChart(d) {
  const agg = new Map();
  for (const r of d.defects) {
    const key = r.defect_code || 'Unmapped';
    const cur = agg.get(key) || { code: key, desc: r.description || 'No defect code sent', fails: 0, stations: new Set(), resp: r.default_responsible };
    cur.fails += r.fails;
    cur.stations.add(r.station);
    agg.set(key, cur);
  }
  const rows = Array.from(agg.values()).sort((a, b) => b.fails - a.fails).slice(0, 10);
  if (!rows.length) return ui.empty('No first-attempt failures in the last 30 days.');
  const total = rows.reduce((a, r) => a + r.fails, 0);
  return charts.bar({
    categories: rows.map((r) => `${r.code} · ${r.desc}`),
    series: [{ name: 'Fails', values: rows.map((r) => r.fails) }],
    horizontal: true, labelWidth: 240, yFormat: (v) => fmt.int(v), categoryLabel: 'Defect',
    ariaLabel: `Defect Pareto, ${total} failures`,
  });
}

function downtimeChart(d) {
  const rows = d.downtime.slice(0, 9);
  if (!rows.length) return ui.empty('No downtime recorded in the last 30 days.');
  return charts.bar({
    categories: rows.map((r) => `${fmt.title(r.category)} · ${r.reason}`),
    series: [{ name: 'Minutes', values: rows.map((r) => Math.round(r.minutes)) }],
    horizontal: true, labelWidth: 240, yFormat: (v) => `${fmt.int(v)} min`, categoryLabel: 'Reason',
    ariaLabel: 'Downtime Pareto',
  });
}

// ---------------------------------------------------------------------------
// drawers
// ---------------------------------------------------------------------------
function openStation(code) {
  if (!S) return;
  const st = S.d.wip_by_station.find((s) => s.code === code);
  if (!st) return;
  const tz = S.d.line.tz;
  ui.drawer.open({
    title: `${st.code} · ${st.name}`,
    subtitle: html`${S.d.line.label} · ${st.queued} queued · ${st.rework} in rework`,
    body: st.units.length ? html`<div class="table-wrap"><table class="table dense">
        <thead><tr><th>Unit</th><th>State</th><th>Last event</th><th>When (${tz === 'Asia/Taipei' ? 'Taipei' : 'PT'})</th></tr></thead>
        <tbody>${st.units.map((u) => html`<tr>
          <td class="mono"><a class="id-link" href="#" data-unit="${u.serial}">${u.serial}</a></td>
          <td>${u.state === 'rework' ? ui.chip('serious', 'Rework') : ui.chip('info', 'Queued')}</td>
          <td>${u.last_code} ${fmt.title(u.result)}${u.defect_code ? html` <span class="muted">(${u.defect_code})</span>` : ''}</td>
          <td class="num">${fmt.dt(u.last_event, tz)}</td></tr>`)}</tbody></table></div>
        <p class="small muted">Click a serial for its station history and as-built record.</p>`
      : ui.empty('No units waiting at this station right now.'),
  });
}

async function openUnit(serial) {
  const body = ui.drawer.open({ title: serial, subtitle: 'Loading…', body: ui.loading('Loading unit') });
  let u;
  try {
    u = await api.get('/api/production/unit', { serial });
  } catch (err) {
    body.innerHTML = String(ui.errorBox(err));
    return;
  }
  const tz = u.unit.build_site_id === 'CM-TXG' ? 'Asia/Taipei' : 'America/Los_Angeles';
  const tzShort = tz === 'Asia/Taipei' ? 'Taipei' : 'PT';
  ui.drawer.open({
    title: serial,
    subtitle: html`${u.unit.item_name} · ${ui.statusChip(u.unit.status)}${u.unit.on_hold ? ui.chip('warning', 'On hold') : ''}`,
    body: html`
      <dl class="kv">
        <dt>Work order</dt><dd class="mono">${u.unit.wo_id || '—'}</dd>
        <dt>Line</dt><dd>${u.unit.build_site_id} · ${u.unit.line || '—'}</dd>
        <dt>Built</dt><dd>${u.unit.built_at ? html`${fmt.dt(u.unit.built_at, tz)} ${tzShort} · ${fmt.dt(u.unit.built_at, 'America/Los_Angeles')} PT` : 'Not yet'}</dd>
        <dt>Firmware</dt><dd class="mono">${u.unit.firmware || '—'}</dd>
      </dl>
      <p><a class="btn sm" href="#/genealogy?q=${encodeURIComponent(serial)}">${icon('tree', 15)}<span>Open as-built genealogy</span></a></p>
      ${u.quarantined.length ? ui.callout({ tone: 'serious', title: `${u.quarantined.length} event(s) for this unit are quarantined`,
        body: html`${u.quarantined.map((qq) => html`<div class="small">raw_id ${qq.raw_id}: ${qq.ingest_note}</div>`)}<a class="ent-link" href="#/cm-feed">CM Feed →</a>` }) : ''}
      <h4 class="section-title">Station history (${tzShort})</h4>
      ${ui.timeline(u.events.map((e) => ({
        ts: e.event_ts, tsLabel: `${fmt.dt(e.event_ts, tz)} ${tzShort}`,
        tone: e.result === 'PASS' ? 'good' : e.result === 'FAIL' ? 'critical' : e.result === 'SCRAP' ? 'critical' : 'serious',
        title: html`${e.code} · ${e.station_name}: <strong>${fmt.title(e.result)}</strong>${e.defect_code ? html` · ${e.defect_code}` : ''}`,
        meta: e.raw_id ? `raw_cm_mes_event ${e.raw_id}` : e.source,
        detail: e.measurements ? Object.entries(e.measurements).map(([k2, v]) => `${k2}=${v}`).join(' · ') : (e.description || ''),
      })))}
      <h4 class="section-title">As-built children</h4>
      <div class="table-wrap"><table class="table dense"><thead><tr><th>Position</th><th>Child</th><th>Item</th><th class="num">Qty</th><th>Status</th></tr></thead>
      <tbody>${u.children.map((c) => html`<tr>
        <td>${fmt.title(c.position || '')}</td>
        <td class="mono">${c.child_serial ? link.serial(c.child_serial) : link.lot(c.child_lot_id)}</td>
        <td class="mono">${c.child_item_id}</td><td class="num">${fmt.num(c.qty, c.qty % 1 ? 2 : 0)}</td>
        <td>${c.removed_at ? html`<span class="muted small">Removed ${fmt.date(c.removed_at)}: ${c.removal_reason || ''}</span>` : ui.chip('good', 'Installed')}</td></tr>`)}</tbody></table></div>`,
  });
}

const PAGE_CSS = `
.pg-production .pp-context { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; margin: -4px 0 16px; font-size: 13px; color: var(--ink-2); }
.pg-production .pp-place { display: inline-flex; align-items: center; gap: 6px; color: var(--ink); }
.pg-production .pp-place svg { color: var(--sign); }
.pg-production .pp-insights { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; margin: 16px 0; }
.pg-production-seg .seg button { white-space: nowrap; }
.pg-production .pp-route { stroke: var(--sign); stroke-width: 4; stroke-linecap: round; opacity: .85; }
.pg-production .pp-stop { fill: var(--surface); stroke: var(--sign); stroke-width: 3; }
.pg-production .pp-stop.bottleneck { fill: var(--sign-yellow); }
.pg-production .pp-dot { fill: var(--series-1); stroke: var(--surface); stroke-width: 2; }
.pg-production .pp-dot.rework { fill: var(--serious); }
.pg-production .pp-count { font: 600 13px var(--font-ui); fill: var(--ink); }
.pg-production .pp-count.zero { fill: var(--ink-3); font-weight: 500; }
.pg-production .pp-more { font: 500 11px var(--font-ui); fill: var(--ink-3); }
.pg-production .pp-code { font: 600 12.5px var(--font-cond); fill: var(--ink); letter-spacing: .02em; }
.pg-production .pp-name { font: 400 10.5px var(--font-ui); fill: var(--ink-3); }
.pg-production .pp-hit { cursor: pointer; }
.pg-production .pp-lg-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--series-1); display: inline-block; }
.pg-production .pp-lg-dot.rework { background: var(--serious); }
.pg-production .pp-lg-stop { width: 10px; height: 10px; border-radius: 50%; background: var(--sign-yellow); border: 2px solid var(--sign); display: inline-block; box-sizing: border-box; }
.pg-production .pp-oee { display: grid; gap: 12px; margin-bottom: 8px; }
.pg-production .pp-oee-total { display: flex; align-items: baseline; gap: 10px; }
.pg-production .pp-oee-val { font: 600 34px/1 var(--font-ui); letter-spacing: -.01em; }
.pg-production .pp-oee-row { display: grid; gap: 5px; }
.pg-production .pp-note { display: flex; gap: 6px; align-items: flex-start; }
.pg-production .pp-note svg { flex: none; margin-top: 2px; color: var(--info); }
.pg-production .pp-bal { fill: var(--series-other); }
.pg-production .pp-bal.bn { fill: var(--series-1); }
.pg-production .pp-takt { stroke: var(--ink-2); stroke-width: 1.5; }
.pg-production .pp-cell { stroke: var(--surface); stroke-width: 0; }
.pg-production .pp-cell.empty { fill: transparent; stroke: var(--grid); stroke-width: 1; }
.pg-production .pp-cell:hover { stroke: var(--ink); stroke-width: 1.5; }
.pg-production .pp-fpy7 { font: 600 12px var(--font-ui); fill: var(--ink); font-variant-numeric: tabular-nums; }
.pg-production .pp-scale { flex-wrap: wrap; }
.pg-production .pp-wiplist { margin-top: 10px; }
`;
