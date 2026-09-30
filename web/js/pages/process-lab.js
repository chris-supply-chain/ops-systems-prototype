// Process Lab: process mining on how the work actually runs. Variants, the
// directly-follows graph, where the time goes (value, control, glue, wait), and
// what deleting the glue would leave, before anything gets automated.
import { html, raw, esc, on, $, $$, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';
import { charts } from '../lib/charts.js';

const CAT = {
  VALUE: { label: 'Value', color: 'var(--series-1)', blurb: 'The work itself' },
  CONTROL: { label: 'Control', color: 'var(--series-other)', blurb: 'Approvals and checks' },
  GLUE: { label: 'Glue', color: 'var(--serious)', blurb: 'Exists only because two systems cannot talk' },
  WAIT: { label: 'Wait / rework', color: 'var(--warning)', blurb: 'Chasing, returns, loops' },
  START: { label: 'Start / end', color: 'var(--ink-3)', blurb: '' },
};
const START = '▶ start';
const END = '■ end';

let S = null;

// 0.4 h -> '24 min', 30 h -> '30.0 h', 138 h -> '5.8 days'
function dur(h) {
  if (h == null || !isFinite(h)) return '—';
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${fmt.num(h, 1)} h`;
  return `${fmt.num(h / 24, 1)} days`;
}

export async function render(el, ctx) {
  injectStyle('page-process-lab', PAGE_CSS);
  const list = await api.get('/api/process/list');
  const procs = list.processes || [];
  let proc = ctx.query.get('p');
  if (!procs.some((p) => p.process === proc)) proc = (procs[0] || {}).process;
  S = { el, ctx, procs, proc, data: null, seq: 0 };

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-process-lab">
      <div class="pl-bar">
        <div class="pl-seg"></div>
        <span class="small muted">Event logs from process_event · categories from process_activity</span>
      </div>
      <div class="pl-body">${ui.loading('Mining the event log')}</div>
    </div>`;

  ui.segmented($('.pl-seg', el), {
    label: 'Process',
    options: procs.map((p) => ({ value: p.process, label: `${p.title} · ${fmt.int(p.cases)}` })),
    value: proc,
    onChange: (v) => { S.proc = v; ctx.setQuery({ p: v }, { silent: true }); load(); },
  });
  S.offs = [
    on(el, 'click', '[data-variant]', (e, t) => openVariant(+t.dataset.variant)),
  ];
  await load();
}

export function unmount() {
  if (S && S.offs) S.offs.forEach((off) => off());
  S = null;
}

async function load() {
  const seq = ++S.seq;
  const body = $('.pl-body', S.el);
  body.classList.add('is-refreshing');
  let d;
  try {
    d = await api.get(`/api/process/${S.proc}`);
  } catch (err) {
    body.innerHTML = String(ui.errorBox(err));
    return;
  }
  if (!S || seq !== S.seq) return;
  S.data = d;
  body.classList.remove('is-refreshing');
  body.innerHTML = String(page(d));
  wireTable(d);
}

// ---------------------------------------------------------------------------
// page body
// ---------------------------------------------------------------------------
function page(d) {
  const pt = d.projection_touched;
  const glueCat = (d.glue.categories || []).reduce((m, c) => ({ ...m, [c.category]: c }), {});
  const glueShare = (glueCat.GLUE ? glueCat.GLUE.share : 0) + (glueCat.WAIT ? glueCat.WAIT.share : 0);
  return html`
    <div class="kpi-row">
      ${ui.kpi({ label: 'Cases in the log', value: fmt.int(d.cases), hint: `${fmt.int(d.events)} events · ${fmt.int(d.variants.length)} distinct variants` })}
      ${ui.kpi({ label: 'Median cycle time', value: dur(d.median_cycle_h), hint: `p90 ${dur(d.p90_cycle_h)}` })}
      ${ui.kpi({ label: 'Match the SOP', value: fmt.pct(d.conformance.share, 0),
        status: d.conformance.share < 0.5 ? { tone: 'warning', label: 'Most do not' } : null,
        hint: `${fmt.int(d.conformance.conforming)} of ${fmt.int(d.cases)} cases match the SOP exactly` })}
      ${ui.kpi({ label: 'Time spent in glue and waiting', value: fmt.pct(glueShare, 0), hint: 'Share of all elapsed time charged to glue or wait steps' })}
      ${pt ? ui.kpi({ label: 'Median cycle if glue is deleted', value: dur(pt.median_after_h), delta: `−${fmt.pct(pt.reduction, 0)}`, deltaGood: 'down',
        deltaLabel: `from ${dur(pt.median_before_h)} on the ${fmt.int(pt.cases)} cases that touch glue` }) : ''}
    </div>

    ${designed(d)}

    ${ui.card({
      title: 'How the work actually flows',
      subtitle: 'Directly-follows graph: every box is a step, every arrow is "this happened next". Arrow width is how often; labels are the median wait before the next step. Loops are drawn underneath.',
      tableToggle: true,
      cls: 'pl-dfg-card',
      body: html`<div class="legend pl-legend">${['VALUE', 'CONTROL', 'GLUE', 'WAIT'].map((k) => html`<span class="legend-item"><span class="legend-key rect" style="--c:${CAT[k].color}"></span>${CAT[k].label}<span class="muted"> · ${CAT[k].blurb}</span></span>`)}</div>
        <div class="pl-dfg-scroll">${dfgChart(d)}</div>`,
    })}

    <div class="grid">
      <div class="span-7">${ui.card({ title: 'Where the time goes', subtitle: 'Elapsed hours charged to each step (the wait plus work before it happened), by category and by step', tableToggle: true, body: timeChart(d) })}</div>
      <div class="span-5">${ui.card({ title: 'Delete the glue, then automate', subtitle: 'What deleting every glue and wait step would leave, before writing any automation', body: projection(d) })}</div>
    </div>

    ${d.confirm_to_plan && d.confirm_to_plan.length ? ui.card({
      title: 'Supplier confirmation to plan: before and after',
      subtitle: 'Hours from the supplier confirming a date to MRP planning with it, by the path the confirmation took',
      tableToggle: true,
      body: confirmToPlan(d.confirm_to_plan),
    }) : ''}

    ${ui.card({ title: 'Variants', subtitle: 'Each distinct sequence of steps, most frequent first. Click one to see its cases.', flush: true, body: html`<div class="pl-variants"></div>` })}

    ${ui.card({ title: 'The exceptions nobody wrote down', subtitle: 'Rare variants carrying steps that are on neither the SOP nor the common path', body: rare(d) })}`;
}

function chipSeq(seq, acts, max = 99) {
  const shown = seq.slice(0, max);
  return html`<span class="pl-seq">${shown.map((a, i) => {
    const c = (acts[a] || {}).category || 'VALUE';
    return html`${i ? html`<span class="pl-arrow">→</span>` : ''}<span class="pl-step cat-${c.toLowerCase()}" title="${CAT[c] ? CAT[c].label : c}">${a}</span>`;
  })}${seq.length > max ? html`<span class="muted small"> +${seq.length - max} more</span>` : ''}</span>`;
}

function designed(d) {
  const path = d.conformance.designed || [];
  if (!path.length) return '';
  const top = d.variants[0];
  return ui.callout({
    tone: d.conformance.share >= 0.5 ? 'info' : 'warning',
    title: `The SOP says ${path.length} steps. ${fmt.pct(d.conformance.share, 0)} of cases do exactly that.`,
    body: html`<div class="pl-designed"><span class="small muted">Designed</span>${chipSeq(path, d.activities)}</div>
      ${top ? html`<div class="pl-designed"><span class="small muted">Most common (${fmt.pct(top.share, 0)})</span>${chipSeq(top.sequence, d.activities)}</div>` : ''}`,
  });
}

// ---------------------------------------------------------------------------
// directly-follows graph
// ---------------------------------------------------------------------------
function wrap(text, n) {
  const words = String(text).split(' ');
  const lines = [];
  let cur = '';
  for (const w of words) {
    if ((cur + ' ' + w).trim().length > n && cur) { lines.push(cur); cur = w; } else cur = (cur + ' ' + w).trim();
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 3);
}

function dfgChart(d) {
  const nodes = d.nodes.map((n) => ({ ...n }));
  const byName = new Map(nodes.map((n) => [n.activity, n]));
  const edges = d.edges.filter((e) => byName.has(e.from) && byName.has(e.to));
  const nLayers = Math.max(...nodes.map((n) => n.layer)) + 1;
  const layers = Array.from({ length: nLayers }, () => []);
  nodes.forEach((n) => layers[n.layer].push(n));
  // barycenter ordering: two sweeps against the previous layer
  layers.forEach((L) => L.sort((a, b) => b.count - a.count));
  for (let pass = 0; pass < 3; pass++) {
    for (let i = 1; i < nLayers; i++) {
      const pos = new Map(layers[i - 1].map((n, k) => [n.activity, k]));
      layers[i].forEach((n) => {
        const ins = edges.filter((e) => e.to === n.activity && pos.has(e.from));
        const w = ins.reduce((a, e) => a + e.count, 0);
        n.bc = w ? ins.reduce((a, e) => a + pos.get(e.from) * e.count, 0) / w : (n.bc ?? 0);
      });
      layers[i].sort((a, b) => a.bc - b.bc);
    }
  }
  const maxRows = Math.max(...layers.map((L) => L.length));
  const NODE_W = 150;
  const NODE_H = 60;
  const COL = 190;
  const ROW = 84;
  const narrowLayers = layers.filter((L) => L.every((n) => n.activity === START || n.activity === END)).length;
  const W = Math.max(nLayers * COL - narrowLayers * (NODE_W - 80) + 10, 700);
  const loopH = edges.some((e) => e.back && e.from !== e.to) ? 60 : 16;
  const H = maxRows * ROW + 40 + loopH;
  let cx = 10;
  layers.forEach((L) => {
    const off = (maxRows - L.length) * ROW / 2;
    const narrow = L.every((n) => n.activity === START || n.activity === END);
    L.forEach((n, k) => {
      n.x = cx;
      n.y = 20 + off + k * ROW;
      n.w = narrow ? 80 : NODE_W;
    });
    cx += (narrow ? 80 : NODE_W) + (COL - NODE_W);
  });
  const maxEdge = Math.max(...edges.map((e) => e.count));
  const labelled = new Set(edges.filter((e) => !e.back && e.from !== START && e.to !== END)
    .sort((a, b) => b.count - a.count).slice(0, 10).map((e) => `${e.from}|${e.to}`));
  const tableRows = edges.slice().sort((a, b) => b.count - a.count)
    .map((e) => [e.from, e.to, fmt.int(e.count), dur(e.median_h), e.back ? 'loop' : '']);
  // the figure gets its real layout width so the chart re-renders at W and the card scrolls sideways
  return html`<div class="pl-dfg-inner" style="width:${W}px">${charts.custom({
    height: H,
    ariaLabel: `Directly-follows graph for ${d.title}`,
    table: { columns: ['From', 'To', 'Times', 'Median wait', 'Loop'], rows: tableRows, numeric: [false, true, true, false] },
    render: () => {
      let e = '<defs>'
        + '<marker id="pl-arr" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto"><path d="M0,1 L9,5 L0,9 z" style="fill:var(--ink-3)"/></marker>'
        + '<marker id="pl-arr-w" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto"><path d="M0,1 L9,5 L0,9 z" style="fill:var(--warning)"/></marker>'
        + '</defs>';
      let labels = '';
      for (const ed of edges) {
        const a = byName.get(ed.from);
        const b = byName.get(ed.to);
        const sw = 1 + 7 * Math.sqrt(ed.count / maxEdge);
        const tip = `${esc(ed.from)} → ${esc(ed.to)}\n${fmt.int(ed.count)} times · median wait ${dur(ed.median_h)}`;
        if (ed.from === ed.to) {
          // self-loop: a small ring off the node's lower right corner
          const x0 = a.x + a.w - 44;
          const y0 = a.y + NODE_H;
          e += `<path class="pl-edge loop" d="M${x0},${y0} C${x0 - 4},${y0 + 19} ${x0 + 22},${y0 + 19} ${x0 + 17},${y0 + 3}" style="stroke-width:${Math.min(sw, 2.5).toFixed(1)}" marker-end="url(#pl-arr-w)" data-tip="${tip}\nRepeated: the same step again"/>`;
          labels += `<text class="pl-edge-label" x="${x0 + 26}" y="${y0 + 16}" text-anchor="start">×${fmt.int(ed.count)} again</text>`;
          continue;
        }
        if (ed.back || b.x <= a.x) {
          const x1 = a.x + a.w / 2;
          const x2 = b.x + b.w / 2;
          const yb = maxRows * ROW + 30 + loopH * 0.6;
          const y1 = a.y + NODE_H;
          const y2 = b.y + NODE_H;
          e += `<path class="pl-edge loop" d="M${x1},${y1} C${x1},${yb} ${x2},${yb} ${x2},${y2 + 4}" style="stroke-width:${Math.min(sw, 4).toFixed(1)}" marker-end="url(#pl-arr-w)" data-tip="${tip}\nLoop: work going back a step"/>`;
          continue;
        }
        const x1 = a.x + a.w;
        const y1 = a.y + NODE_H / 2;
        const x2 = b.x - 2;
        const y2 = b.y + NODE_H / 2;
        const mx = (x1 + x2) / 2;
        e += `<path class="pl-edge" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" style="stroke-width:${sw.toFixed(1)}" marker-end="url(#pl-arr)" data-tip="${tip}"/>`;
        if (labelled.has(`${ed.from}|${ed.to}`)) {
          labels += `<text class="pl-edge-label" x="${mx.toFixed(1)}" y="${((y1 + y2) / 2 - 5).toFixed(1)}" text-anchor="middle">${esc(dur(ed.median_h))}</text>`;
        }
      }
      let nodesSvg = '';
      for (const n of nodes) {
        const cat = n.activity === START || n.activity === END ? 'START' : n.category;
        const c = CAT[cat] || CAT.VALUE;
        const act = d.activities[n.activity] || {};
        const tip = `${esc(n.activity)}\n${c.label}${act.system ? ` · ${esc(act.system)}` : ''} · ${fmt.int(n.count)} events in ${fmt.int(n.cases)} cases${act.note ? `\n${esc(act.note)}` : ''}`;
        nodesSvg += `<g class="pl-node cat-${cat.toLowerCase()}" data-tip="${tip}">`;
        nodesSvg += `<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${NODE_H}" rx="9"/>`;
        nodesSvg += `<rect class="pl-node-bar" x="${n.x}" y="${n.y}" width="5" height="${NODE_H}" rx="2" style="fill:${c.color}"/>`;
        if (cat === 'START') {
          nodesSvg += `<text class="pl-node-name" x="${n.x + n.w / 2 + 2}" y="${n.y + NODE_H / 2}" dy="0.32em" text-anchor="middle">${esc(n.activity)}</text>`;
        } else {
          const lines = wrap(n.activity, 21);
          const top = n.y + 17 + (3 - lines.length) * 5;
          lines.forEach((ln, i) => { nodesSvg += `<text class="pl-node-name" x="${n.x + 13}" y="${top + i * 14}">${esc(ln)}</text>`; });
          nodesSvg += `<text class="pl-node-n" x="${n.x + n.w - 8}" y="${n.y + NODE_H - 8}" text-anchor="end">${fmt.int(n.count)}</text>`;
        }
        nodesSvg += '</g>';
      }
      return raw(`${e}${nodesSvg}${labels}`);
    },
  })}</div>`;
}

// ---------------------------------------------------------------------------
// time, projection, confirm-to-plan
// ---------------------------------------------------------------------------
function timeChart(d) {
  const acts = (d.glue.activities || []).filter((a) => a.hours_total > 0).slice(0, 12);
  const cats = (d.glue.categories || []).filter((c) => c.category in CAT).sort((a, b) => b.hours - a.hours);
  return html`
    <div class="pl-catbar" role="img" aria-label="${cats.map((c) => `${CAT[c.category].label} ${fmt.pct(c.share, 0)}`).join(', ')}">
      ${cats.map((c) => html`<span style="flex:${Math.max(c.share, 0.01)};--c:${CAT[c.category].color}" data-tip="${CAT[c.category].label}\n${fmt.pct(c.share, 1)} of elapsed time · ${fmt.int(c.touches)} human touches"></span>`)}
    </div>
    <div class="legend pl-catlegend">${cats.map((c) => html`<span class="legend-item"><span class="legend-key rect" style="--c:${CAT[c.category].color}"></span>${CAT[c.category].label} <strong>${c.share > 0 && c.share < 0.01 ? '<1%' : fmt.pct(c.share, 0)}</strong></span>`)}</div>
    ${charts.bar({
      categories: acts.map((a) => a.activity),
      series: [{ name: 'Hours charged', values: acts.map((a) => Math.round(a.hours_total)) }],
      horizontal: true,
      labelWidth: 240,
      colors: (si, ci) => CAT[acts[ci].category] ? CAT[acts[ci].category].color : null,
      yFormat: (v) => fmt.compact(v),
      ariaLabel: 'Hours charged to each step',
    })}
    <p class="tiny muted">Bars are colored by category; the ${cats.length ? CAT[cats[0].category].label.toLowerCase() : ''} steps dominate. Values are total elapsed hours across all ${fmt.int(d.cases)} cases.</p>`;
}

function projection(d) {
  const p = d.projection_touched || d.projection;
  const glue = (d.glue.activities || []).filter((a) => a.category === 'GLUE' || a.category === 'WAIT');
  if (!glue.length) {
    return ui.callout({ tone: 'good', title: 'No glue in this process', body: 'Every step is value or control. Automate the control steps if they are slow.' });
  }
  return html`
    <div class="pl-proj">
      <div><span class="small muted">Median cycle</span><div class="pl-proj-row"><strong>${dur(p.median_before_h)}</strong>${icon('arrow-right', 16)}<strong class="good">${dur(p.median_after_h)}</strong></div></div>
      <div><span class="small muted">p90 cycle</span><div class="pl-proj-row"><strong>${dur(p.p90_before_h)}</strong>${icon('arrow-right', 16)}<strong class="good">${dur(p.p90_after_h)}</strong></div></div>
      <div><span class="small muted">Human touches per case</span><div class="pl-proj-row"><strong>${fmt.num(p.touches_before, 1)}</strong>${icon('arrow-right', 16)}<strong class="good">${fmt.num(p.touches_after, 1)}</strong></div></div>
    </div>
    <p class="small muted">${d.projection_touched ? `Measured on the ${fmt.int(d.projection_touched.cases)} cases that touch a glue or wait step.` : ''}</p>
    <h4 class="section-title">Delete these steps</h4>
    <ul class="pl-delete">${glue.map((a) => html`<li>
      <span class="pl-step cat-${a.category.toLowerCase()}">${a.activity}</span>
      <span class="small muted">${fmt.int(a.events)}× · median ${dur(a.median_h)} · ${fmt.pct(a.share, 0)} of time</span>
      ${(d.activities[a.activity] || {}).note ? html`<span class="small">${d.activities[a.activity].note}</span>` : ''}
    </li>`)}</ul>`;
}

function confirmToPlan(rows) {
  const slow = rows[0];
  const fast = rows[rows.length - 1];
  return html`${charts.bar({
    categories: rows.map((r) => r.path),
    series: [{ name: 'Median time to plan', values: rows.map((r) => r.median_h / 24) }],
    horizontal: true,
    labelWidth: 230,
    // values are in days. Axis ticks land on multiples of 0.5: label whole days, leave half days blank;
    // bar values (never exact halves here) get human units.
    yFormat: (v) => (v === 0 ? '0' : Number.isInteger(v) ? `${v} d` : Number.isInteger(v * 2) ? '' : dur(v * 24)),
    ariaLabel: 'Median hours from supplier confirmation to the plan, by path',
  })}
  <p class="small">Re-keyed by hand, a supplier's date reached the plan in a median <strong>${dur(slow.median_h)}</strong> (${fmt.int(slow.cases)} confirmations) because the planner only exports open POs on Mondays. The platform parser gets the same email into MRP in <strong>${dur(rows[1] ? rows[1].median_h : fast.median_h)}</strong>, close to EDI or the portal at ${dur(fast.median_h)}, without asking the supplier to change anything.</p>`;
}

function rare(d) {
  if (!d.rare.length) return ui.empty('Every case follows one of the common variants.');
  return html`<ul class="pl-rare">${d.rare.map((v) => html`<li>
    <div class="pl-rare-head"><strong>Variant #${v.rank}</strong><span class="small muted">${fmt.int(v.cases)} case${v.cases === 1 ? '' : 's'} · median ${dur(v.median_cycle_h)}</span>
      <button type="button" class="btn sm" data-variant="${v.rank}">Cases</button></div>
    ${v.distinct_steps.length ? html`<div class="small">Steps nobody planned for: ${v.distinct_steps.map((a, i) => html`${i ? ', ' : ''}<span class="pl-step cat-${((d.activities[a] || {}).category || 'VALUE').toLowerCase()}">${a}</span>`)}</div>` : ''}
    ${v.repeated_steps.length ? html`<div class="small">Repeated: ${v.repeated_steps.join(', ')}</div>` : ''}
  </li>`)}</ul>`;
}

// ---------------------------------------------------------------------------
// variants table, drawers
// ---------------------------------------------------------------------------
function wireTable(d) {
  const holder = $('.pl-variants', S.el);
  if (!holder) return;
  ui.dataTable(holder, {
    rows: d.variants,
    pageSize: 12,
    onRowClick: (r) => openVariant(r.rank),
    columns: [
      { key: 'rank', label: '#', num: true, width: 44 },
      { key: 'sequence', label: 'Sequence', wrap: true, sortable: false, render: (r) => chipSeq(r.sequence, d.activities, 9) },
      { key: 'cases', label: 'Cases', num: true },
      { key: 'share', label: 'Share', num: true, render: (r) => html`<span class="pl-share">${fmt.pct(r.share, 1)}${ui.meter({ value: r.share, max: 1 })}</span>` },
      { key: 'median_cycle_h', label: 'Median cycle', num: true, format: (v) => dur(v) },
      { key: 'p90_cycle_h', label: 'p90', num: true, format: (v) => dur(v) },
    ],
  });
}

function openVariant(rank) {
  const d = S && S.data;
  const v = d && d.variants.find((x) => x.rank === rank);
  if (!v) return;
  // the drawer lives outside the page root, so it gets its own listener, removed on close
  let off = null;
  const body = ui.drawer.open({
    title: `Variant #${v.rank}`,
    subtitle: `${fmt.int(v.cases)} cases · ${fmt.pct(v.share, 1)} · median ${dur(v.median_cycle_h)}`,
    body: html`${chipSeq(v.sequence, d.activities)}
      <h4 class="section-title">Cases</h4>
      <div class="pl-cases">${v.case_ids.map((c) => html`<button type="button" class="pl-case-btn id-link" data-case="${c}">${c}</button>`)}</div>
      ${v.cases > v.case_ids.length ? html`<p class="tiny muted">Showing ${v.case_ids.length} of ${fmt.int(v.cases)}.</p>` : ''}
      <div class="pl-case-detail"></div>`,
    onClose: () => { if (off) off(); },
  });
  off = on(body, 'click', '[data-case]', (e, t) => {
    body.querySelectorAll('[data-case]').forEach((b) => b.classList.toggle('active', b === t));
    openCase(t.dataset.case);
  });
  const first = body.querySelector('[data-case]');
  if (first) first.click();
}

async function openCase(id) {
  const box = document.querySelector('.drawer .pl-case-detail');
  if (!box || !S) return;
  box.innerHTML = String(ui.loading('Loading case'));
  try {
    const c = await api.get(`/api/process/${S.proc}/case/${encodeURIComponent(id)}`);
    const po = /^(4[57]\d{5})-(\d+)$/.exec(id);
    box.innerHTML = String(html`<h4 class="section-title">Case ${po ? link.po(po[1], po[2]) : id} · ${dur(c.cycle_h)} end to end</h4>
      ${ui.timeline(c.events.map((ev) => ({
        ts: ev.ts,
        tone: ev.category === 'GLUE' ? 'serious' : ev.category === 'WAIT' ? 'warning' : ev.category === 'CONTROL' ? 'neutral' : 'info',
        title: ev.activity,
        meta: `${ev.actor_role} · ${ev.system}${ev.gap_h ? ` · +${dur(ev.gap_h)}` : ''}`,
        detail: ev.note || '',
      })))}`);
  } catch (err) {
    box.innerHTML = String(ui.errorBox(err));
  }
}

const PAGE_CSS = `
.pg-process-lab .pl-bar { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; margin-bottom: 16px; }
.pg-process-lab .pl-bar .pl-seg { min-width: 0; max-width: 100%; }
.pg-process-lab .pl-bar .seg { max-width: 100%; overflow-x: auto; }
.pg-process-lab .pl-body > * + * { margin-top: 16px; }
.pg-process-lab .pl-body.is-refreshing { opacity: .55; transition: opacity .12s; }
.pg-process-lab .pl-legend { margin: 0 0 8px; }
.pg-process-lab .pl-dfg-scroll { overflow-x: auto; }
.pg-process-lab .chart .pl-edge { fill: none; stroke: var(--ink-3); opacity: .42; stroke-linecap: round; }
.pg-process-lab .chart .pl-edge.loop { stroke: var(--warning); opacity: .7; }
.pg-process-lab .chart .pl-edge:hover { opacity: .95; }
.pg-process-lab .chart .pl-edge-label { font-size: 11px; font-weight: 600; fill: var(--ink-2); paint-order: stroke; stroke: var(--surface); stroke-width: 4px; pointer-events: none; }
.pg-process-lab .chart .pl-node rect:first-child { fill: var(--surface); stroke: var(--hairline-strong); stroke-width: 1; }
.pg-process-lab .chart .pl-node.cat-glue rect:first-child { fill: color-mix(in srgb, var(--serious) 10%, var(--surface)); }
.pg-process-lab .chart .pl-node.cat-wait rect:first-child { fill: color-mix(in srgb, var(--warning) 12%, var(--surface)); }
.pg-process-lab .chart .pl-node.cat-start rect:first-child { fill: var(--surface-2); }
.pg-process-lab .chart .pl-node:hover rect:first-child { stroke: var(--focus); stroke-width: 1.5; }
.pg-process-lab .chart .pl-node-name { font: 600 12px var(--font-cond); fill: var(--ink); }
.pg-process-lab .chart .pl-node-n { font-size: 11px; font-weight: 600; fill: var(--ink-3); font-variant-numeric: tabular-nums; }
.pg-process-lab .pl-seq { display: inline-flex; flex-wrap: wrap; gap: 4px 2px; align-items: center; }
.pg-process-lab .pl-arrow, .drawer .pl-arrow { color: var(--ink-3); font-size: 11px; margin: 0 2px; }
.pg-process-lab .pl-step, .drawer .pl-step { display: inline-flex; align-items: center; min-height: 22px; padding: 1px 7px; border-radius: 6px; font-size: 12px; line-height: 1.3;
  background: var(--surface-2); border: 1px solid var(--hairline); border-left: 3px solid var(--c, var(--series-1)); color: var(--ink); }
.pg-process-lab .pl-step.cat-value, .drawer .pl-step.cat-value { --c: var(--series-1); }
.pg-process-lab .pl-step.cat-control, .drawer .pl-step.cat-control { --c: var(--series-other); }
.pg-process-lab .pl-step.cat-glue, .drawer .pl-step.cat-glue { --c: var(--serious); background: color-mix(in srgb, var(--serious) 10%, var(--surface)); }
.pg-process-lab .pl-step.cat-wait, .drawer .pl-step.cat-wait { --c: var(--warning); background: color-mix(in srgb, var(--warning) 12%, var(--surface)); }
.drawer .pl-seq { display: flex; flex-wrap: wrap; gap: 4px 2px; align-items: center; }
.pg-process-lab .pl-designed { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; margin-top: 6px; }
.pg-process-lab .pl-designed > .small { min-width: 150px; }
.pg-process-lab .pl-catbar { display: flex; gap: 2px; height: 14px; border-radius: 999px; overflow: hidden; }
.pg-process-lab .pl-catbar span { background: var(--c); min-width: 4px; }
.pg-process-lab .pl-catlegend { margin: 8px 0 12px; }
.pg-process-lab .pl-proj { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.pg-process-lab .pl-proj-row { display: flex; align-items: center; gap: 6px; font-size: 18px; margin-top: 2px; }
.pg-process-lab .pl-proj-row .icon { color: var(--ink-3); }
.pg-process-lab .pl-proj-row .good { color: var(--delta-good); }
.pg-process-lab .pl-delete { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.pg-process-lab .pl-delete li { display: flex; flex-direction: column; gap: 3px; align-items: flex-start; }
.pg-process-lab .pl-share { display: inline-flex; flex-direction: column; gap: 3px; min-width: 70px; align-items: flex-end; }
.pg-process-lab .pl-share .meter { width: 70px; }
.pg-process-lab .pl-rare { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.pg-process-lab .pl-rare li { display: grid; gap: 4px; padding-bottom: 12px; border-bottom: 1px solid var(--hairline); }
.pg-process-lab .pl-rare li:last-child { border-bottom: 0; padding-bottom: 0; }
.pg-process-lab .pl-rare-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.pg-process-lab .pl-rare .pl-step { margin: 1px 0; }
.drawer .pl-cases { display: flex; flex-wrap: wrap; gap: 6px 8px; }
.drawer .pl-case-btn { border: 1px solid var(--hairline); background: var(--surface); border-radius: 6px; padding: 2px 7px; cursor: pointer; }
.drawer .pl-case-btn:hover { background: var(--row-hover); }
.drawer .pl-case-btn.active { border-color: var(--link); background: color-mix(in srgb, var(--link) 10%, var(--surface)); }
.drawer .pl-case-detail:not(:empty) { margin-top: 14px; }
`;
