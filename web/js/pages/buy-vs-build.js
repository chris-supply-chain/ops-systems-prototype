// Buy vs Build: thin, disposable apps instrumented in production. Their telemetry
// re-weights each capability by what people actually use, and the vendor
// scorecards are recomputed with the same fit matrix. Evidence picks the platform.
import { html, raw, esc, on, $, $$, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';
import { charts } from '../lib/charts.js';

const DOMAIN_LABEL = { QMS: 'Quality (QMS)', TMS: 'Transport (TMS)', WMS: 'Warehouse (WMS)' };
const APP_OF_PREFIX = { q: 'QUALITY_THIN', t: 'MOVE_THIN', w: 'DOCK_THIN' };
const APP_SHORT = { QUALITY_THIN: 'Quality app', MOVE_THIN: 'Move app', DOCK_THIN: 'Dock app' };

let S = null;

export async function render(el, ctx) {
  injectStyle('page-buy-vs-build', PAGE_CSS);
  const d = await api.get('/api/buy-build');
  let domain = ctx.query.get('domain');
  if (!d.domains.some((x) => x.domain === domain)) domain = d.domains[0].domain;
  S = { el, ctx, d, domain };

  const flipped = d.domains.filter((x) => x.flipped).length;
  let usdAvoided = 0;
  let weeksAvoided = 0;
  for (const s of Object.values(d.savings)) {
    usdAvoided += s.assumed.annual_usd - s.recommended.annual_usd;
    weeksAvoided += s.assumed.impl_weeks - s.recommended.impl_weeks;
  }
  const first = d.apps.map((a) => a.first).sort()[0];

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-buy-vs-build">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Thin apps in production', value: fmt.int(d.apps.length), hint: `QMS, TMS and WMS jobs · instrumented since ${fmt.date(first)}` })}
        ${ui.kpi({ label: 'Feature uses recorded', value: fmt.int(d.total_events), hint: `${d.weeks.length} weeks of app_telemetry` })}
        ${ui.kpi({ label: 'Rankings overturned', value: `${flipped} of ${d.domains.length}`,
          status: flipped ? { tone: 'warning', label: 'Flipped' } : null, hint: 'Same vendor fit scores; only the weights changed' })}
        ${ui.kpi({ label: 'License spend avoided', value: fmt.usd(usdAvoided, { compact: true }), unit: '/ yr', hint: 'Recommended vs. the pick the assumptions pointed to' })}
        ${ui.kpi({ label: 'Implementation time avoided', value: fmt.int(weeksAvoided), unit: 'weeks', hint: 'Summed across the three domains' })}
      </div>

      ${ui.callout({ tone: 'info', title: 'How the evidence picks the platform',
        body: html`Before anything was bought, each capability got a weight from what the team <em>assumed</em> mattered (usually what vendors demo best). The thin apps then ran the real work for ${d.weeks.length} weeks. Each capability's <strong>observed weight</strong> is its share of actual feature use in that domain. Every vendor is scored twice with the same 0-3 fit matrix: once with assumed weights, once with observed weights.` })}

      <div class="bb-bar"><div class="bb-seg"></div></div>
      <div class="bb-domain"></div>

      <h3 class="section-title bb-h">Thin-app telemetry</h3>
      <div class="grid">
        <div class="span-7">${ui.card({ title: 'Weekly feature uses by thin app', subtitle: 'The apps ramped as teams moved work onto them', tableToggle: true, body: weeklyChart(d) })}</div>
        <div class="span-5">${ui.card({ title: 'Who uses them', subtitle: 'Feature uses by role, per app', body: roles(d) })}</div>
      </div>
      ${ui.card({ title: 'Every instrumented feature', subtitle: 'Uses over the whole window, weekly trend, completion rate and time on task', flush: true, body: html`<div class="bb-features"></div>` })}
    </div>`;

  ui.segmented($('.bb-seg', el), {
    label: 'Domain',
    options: d.domains.map((x) => ({ value: x.domain, label: `${DOMAIN_LABEL[x.domain] || x.domain}${x.flipped ? ' · ranking flipped' : ''}` })),
    value: domain,
    onChange: (v) => { S.domain = v; ctx.setQuery({ domain: v }, { silent: true }); drawDomain(); },
  });
  drawDomain();
  featuresTable(d);
}

export function unmount() { S = null; }

// ---------------------------------------------------------------------------
// one domain
// ---------------------------------------------------------------------------
function drawDomain() {
  const dom = S.d.domains.find((x) => x.domain === S.domain);
  const byId = Object.fromEntries(dom.vendors.map((v) => [v.vendor_id, v]));
  const rec = byId[dom.recommended];
  const was = byId[dom.assumed_winner];
  $('.bb-domain', S.el).innerHTML = String(html`
    <section class="bb-rec card">
      <div class="bb-rec-main">
        <span class="bb-rec-kicker">${icon('check-circle', 15)} Evidence says</span>
        <h3>${rec.name}</h3>
        <p class="bb-rec-meta"><strong>${fmt.usd(rec.annual_usd, { compact: true })}</strong> per year · <strong>${fmt.int(rec.impl_weeks)}</strong> weeks to implement · scores <strong>${fmt.int(rec.observed_score * 100)}</strong> on observed use</p>
        <p class="bb-evidence">${dom.evidence}</p>
      </div>
      ${dom.flipped ? html`<div class="bb-rec-was">
        <span class="small muted">The assumptions pointed to</span>
        <strong>${was.name}</strong>
        <span class="small">${fmt.usd(was.annual_usd, { compact: true })}/yr · ${fmt.int(was.impl_weeks)} weeks</span>
        <span class="small">Scored ${fmt.int(was.assumed_score * 100)} assumed, <strong>${fmt.int(was.observed_score * 100)}</strong> on observed use (rank ${was.observed_rank})</span>
      </div>` : ''}
    </section>
    <div class="grid">
      <div class="span-5">${ui.card({ title: 'Vendor scores, before and after the evidence', subtitle: 'Weighted fit (0-100) with assumed weights, then with observed usage', tableToggle: true, body: slope(dom) })}</div>
      <div class="span-7">${ui.card({ title: 'What people actually used', subtitle: 'Each capability\'s weight: assumed before shipping vs. share of observed use', tableToggle: true, body: weights(dom) })}</div>
    </div>
    ${ui.card({ title: 'Fit matrix', subtitle: 'How well each option covers each capability (0 none … 3 native), with the observed weight that now counts', flush: true, body: matrix(dom) })}`);
}

function slope(dom) {
  const vs = [...dom.vendors].sort((a, b) => a.vendor_id.localeCompare(b.vendor_id));
  const color = (i) => charts.palette(i);
  const table = { columns: ['Option', 'Assumed score', 'Observed score', 'Assumed rank', 'Observed rank', 'Annual'],
    rows: vs.map((v) => [v.name, fmt.int(v.assumed_score * 100), fmt.int(v.observed_score * 100), v.assumed_rank, v.observed_rank, fmt.usd(v.annual_usd)]),
    numeric: [true, true, true, true, true] };
  const legend = charts.legend(vs.map((v, i) => ({ name: v.name, color: color(i) })), 'line');
  return charts.custom({
    height: 290,
    ariaLabel: `Vendor scores for ${dom.domain} under assumed and observed weights`,
    table,
    legend,
    render: (w, h) => {
      const m = { t: 26, b: 22, l: 40, r: 40 };
      const x1 = m.l + 8;
      const x2 = w - m.r - 8;
      const sy = (v) => m.t + (1 - v) * (h - m.t - m.b);
      let s = '';
      for (const t of [0, 0.25, 0.5, 0.75, 1]) {
        const y = Math.round(sy(t)) + 0.5;
        s += `<line class="gridline" x1="${x1}" x2="${x2}" y1="${y}" y2="${y}"/><text class="tick" x="${x1 - 8}" y="${y}" dy="0.32em" text-anchor="end">${t * 100}</text>`;
      }
      s += `<text class="bb-col" x="${x1}" y="${m.t - 12}" text-anchor="start">Assumed weights</text>`;
      s += `<text class="bb-col" x="${x2}" y="${m.t - 12}" text-anchor="end">Observed use</text>`;
      s += `<line class="baseline" x1="${x1}" x2="${x1}" y1="${m.t}" y2="${h - m.b}"/><line class="baseline" x1="${x2}" x2="${x2}" y1="${m.t}" y2="${h - m.b}"/>`;
      const spread = (vals) => {
        // keep end labels 15px apart without detaching them from their dots
        const order = vals.map((v, i) => ({ i, y: v })).sort((a, b) => a.y - b.y);
        for (let k = 1; k < order.length; k++) if (order[k].y - order[k - 1].y < 15) order[k].y = order[k - 1].y + 15;
        const out = [];
        order.forEach((o) => { out[o.i] = o.y; });
        return out;
      };
      const ly1 = spread(vs.map((v) => sy(v.assumed_score)));
      const ly2 = spread(vs.map((v) => sy(v.observed_score)));
      vs.forEach((v, i) => {
        const ya = sy(v.assumed_score);
        const yb = sy(v.observed_score);
        const c = color(i);
        const tip = `${esc(v.name)}\nAssumed ${fmt.int(v.assumed_score * 100)} (rank ${v.assumed_rank}) → observed ${fmt.int(v.observed_score * 100)} (rank ${v.observed_rank})`;
        s += `<g data-tip="${tip}"><line x1="${x1}" y1="${ya}" x2="${x2}" y2="${yb}" style="stroke:${c}" stroke-width="${v.vendor_id === dom.recommended ? 3 : 2}" stroke-linecap="round"/>`;
        s += `<line x1="${x1}" y1="${ya}" x2="${x2}" y2="${yb}" stroke="transparent" stroke-width="16"/>`;
        s += `<circle class="marker" cx="${x1}" cy="${ya}" r="5" style="fill:${c}"/><circle class="marker" cx="${x2}" cy="${yb}" r="5" style="fill:${c}"/></g>`;
        s += `<text class="bb-end" x="${x1 + 12}" y="${ly1[i]}" dy="0.32em">${fmt.int(v.assumed_score * 100)}</text>`;
        s += `<text class="bb-end" x="${x2 - 12}" y="${ly2[i]}" dy="0.32em" text-anchor="end">${fmt.int(v.observed_score * 100)}${v.vendor_id === dom.recommended ? ' ★' : ''}</text>`;
      });
      return raw(s);
    },
  });
}

function weights(dom) {
  const caps = [...dom.capabilities].sort((a, b) => b.observed - a.observed);
  return charts.bar({
    categories: caps.map((c) => c.name),
    series: [
      { name: 'Assumed weight', values: caps.map((c) => c.assumed), color: 'var(--series-other)' },
      { name: 'Observed share of use', values: caps.map((c) => c.observed), color: 'var(--series-1)' },
    ],
    horizontal: true,
    labelWidth: 230,
    yFormat: (v) => fmt.pct(v, 0),
    ariaLabel: `Assumed versus observed capability weights for ${dom.domain}`,
  });
}

function dots(n) {
  return html`<span class="bb-dots" title="${['none', 'partial', 'good', 'native'][n] || n}">${[1, 2, 3].map((k) => html`<i class="${k <= n ? 'on' : ''}"></i>`)}</span>`;
}

function matrix(dom) {
  const caps = [...dom.capabilities].sort((a, b) => b.observed - a.observed);
  const vs = dom.vendors;
  return html`<div class="table-wrap"><table class="table">
    <thead><tr><th>Capability</th><th class="num">Assumed</th><th class="num">Observed</th>
      ${vs.map((v) => html`<th class="bb-vcol${v.vendor_id === dom.recommended ? ' rec' : ''}">${v.name}${v.vendor_id === dom.recommended ? ' ★' : ''}</th>`)}</tr></thead>
    <tbody>${caps.map((c) => html`<tr>
      <td class="wrap"><strong>${c.name}</strong><div class="tiny muted">${c.description || ''}</div></td>
      <td class="num">${fmt.pct(c.assumed, 0)}</td>
      <td class="num"><strong>${c.observed < 0.01 ? '<1%' : fmt.pct(c.observed, 0)}</strong> <span class="bb-delta ${c.delta >= 0 ? 'up' : 'down'}">${c.delta >= 0 ? '▲' : '▼'}</span></td>
      ${vs.map((v) => html`<td class="${v.vendor_id === dom.recommended ? 'bb-rec-col' : ''}">${dots(c.fit[v.vendor_id] || 0)}</td>`)}
    </tr>`)}
    <tr class="bb-total"><td><strong>Weighted score (observed)</strong></td><td></td><td></td>
      ${vs.map((v) => html`<td class="${v.vendor_id === dom.recommended ? 'bb-rec-col' : ''}"><strong>${fmt.int(v.observed_score * 100)}</strong> <span class="muted small">(assumed ${fmt.int(v.assumed_score * 100)})</span></td>`)}</tr>
    <tr class="bb-total"><td><strong>Annual cost · time to value</strong></td><td></td><td></td>
      ${vs.map((v) => html`<td class="${v.vendor_id === dom.recommended ? 'bb-rec-col' : ''}">${v.annual_usd ? fmt.usd(v.annual_usd, { compact: true }) : '$0'} · ${fmt.int(v.impl_weeks)} wk</td>`)}</tr>
    </tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// telemetry
// ---------------------------------------------------------------------------
function weeklyChart(d) {
  const apps = ['QUALITY_THIN', 'MOVE_THIN', 'DOCK_THIN'];
  const sums = Object.fromEntries(apps.map((a) => [a, d.weeks.map(() => 0)]));
  for (const [feature, vals] of Object.entries(d.weekly)) {
    const app = APP_OF_PREFIX[feature[0]];
    if (!sums[app]) continue;
    vals.forEach((v, i) => { sums[app][i] += v; });
  }
  return charts.line({
    series: apps.map((a) => ({ name: APP_SHORT[a], points: d.weeks.map((w, i) => ({ x: w, y: sums[a][i] })) })),
    height: 240,
    yFormat: (v) => fmt.compact(v),
    xLabel: 'Week of',
    ariaLabel: 'Weekly feature uses by thin app',
  });
}

function roles(d) {
  const byApp = {};
  for (const r of d.by_role) (byApp[r.app] = byApp[r.app] || []).push(r);
  return html`<div class="bb-roles">${Object.entries(byApp).map(([app, rows]) => {
    const total = rows.reduce((a, r) => a + r.n, 0);
    return html`<div class="bb-role-app"><div class="bb-role-head"><strong>${APP_SHORT[app] || app}</strong><span class="muted small">${fmt.int(total)} uses</span></div>
      ${rows.map((r) => html`<div class="bb-role-row"><span class="small">${r.user_role}</span>${ui.meter({ value: r.n, max: total })}<span class="small num">${fmt.pct(r.n / total, 0)}</span></div>`)}</div>`;
  })}</div>`;
}

function featuresTable(d) {
  const rows = d.domains.flatMap((dom) => dom.capabilities.map((c) => ({ ...c, domain: dom.domain, app: APP_SHORT[APP_OF_PREFIX[c.feature_key[0]]] })));
  ui.dataTable($('.bb-features', S.el), {
    rows,
    pageSize: 25,
    search: true,
    initialSort: { key: 'usage', dir: 'desc' },
    columns: [
      { key: 'name', label: 'Capability', render: (r) => html`<strong>${r.name}</strong><div class="tiny muted mono">${r.feature_key}</div>` },
      { key: 'app', label: 'Thin app' },
      { key: 'usage', label: 'Uses', num: true },
      { key: 'trend', label: 'Weekly', sortable: false, render: (r) => charts.sparkline(d.weekly[r.feature_key] || [], { width: 96, height: 24 }) },
      { key: 'observed', label: 'Share of domain use', num: true, format: (v) => (v < 0.01 ? '<1%' : fmt.pct(v, 0)) },
      { key: 'assumed', label: 'Assumed weight', num: true, format: (v) => fmt.pct(v, 0) },
      { key: 'completion', label: 'Completed', num: true, format: (v) => fmt.pct(v, 0) },
      { key: 'avg_seconds', label: 'Avg time', num: true, format: (v) => `${fmt.int(v)} s` },
    ],
  });
}

const PAGE_CSS = `
.pg-buy-vs-build > * + * { margin-top: 16px; }
.pg-buy-vs-build .bb-bar .seg { max-width: 100%; overflow-x: auto; }
.pg-buy-vs-build .bb-domain > * + * { margin-top: 16px; }
.pg-buy-vs-build .bb-h { margin-top: 28px; }
.pg-buy-vs-build .bb-rec { display: flex; flex-wrap: wrap; gap: 16px 28px; justify-content: space-between; border-left: 4px solid var(--good); }
.pg-buy-vs-build .bb-rec-main { flex: 1 1 420px; min-width: 0; }
.pg-buy-vs-build .bb-rec-kicker { display: inline-flex; gap: 6px; align-items: center; font: 600 11.5px var(--font-cond); letter-spacing: .1em; text-transform: uppercase; color: var(--delta-good); }
.pg-buy-vs-build .bb-rec h3 { font: 600 24px/1.2 var(--font-cond); margin: 4px 0 6px; }
.pg-buy-vs-build .bb-rec-meta { color: var(--ink-2); }
.pg-buy-vs-build .bb-evidence { margin-top: 8px; font-size: 14px; color: var(--ink); max-width: 820px; }
.pg-buy-vs-build .bb-rec-was { flex: 0 1 300px; display: flex; flex-direction: column; gap: 3px; padding: 12px 14px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--hairline); }
.pg-buy-vs-build .bb-rec-was strong { text-decoration: line-through; text-decoration-color: color-mix(in srgb, var(--critical) 70%, transparent); }
.pg-buy-vs-build .bb-rec-was .small strong { text-decoration: none; }
.pg-buy-vs-build .chart .bb-col { font: 600 11.5px var(--font-cond); letter-spacing: .08em; text-transform: uppercase; fill: var(--ink-3); }
.pg-buy-vs-build .chart .bb-end { font-size: 12px; font-weight: 600; fill: var(--ink-2); font-variant-numeric: tabular-nums; paint-order: stroke; stroke: var(--surface); stroke-width: 3px; }
.pg-buy-vs-build .bb-dots { display: inline-flex; gap: 3px; }
.pg-buy-vs-build .bb-dots i { width: 9px; height: 9px; border-radius: 50%; background: var(--surface-2); border: 1px solid var(--hairline-strong); }
.pg-buy-vs-build .bb-dots i.on { background: var(--series-1); border-color: var(--series-1); }
.pg-buy-vs-build .bb-vcol { white-space: normal; min-width: 120px; }
.pg-buy-vs-build .bb-vcol.rec { color: var(--ink); }
.pg-buy-vs-build td.bb-rec-col { background: color-mix(in srgb, var(--good) 7%, transparent); }
.pg-buy-vs-build .bb-delta { font-size: 9px; margin-left: 2px; }
.pg-buy-vs-build .bb-delta.up { color: var(--delta-good); }
.pg-buy-vs-build .bb-delta.down { color: var(--delta-bad); }
.pg-buy-vs-build tr.bb-total td { background: var(--surface-2); }
.pg-buy-vs-build .bb-roles { display: grid; gap: 14px; }
.pg-buy-vs-build .bb-role-head { display: flex; justify-content: space-between; margin-bottom: 4px; }
.pg-buy-vs-build .bb-role-row { display: grid; grid-template-columns: 110px minmax(0, 1fr) 40px; gap: 10px; align-items: center; padding: 2px 0; }
.pg-buy-vs-build .bb-role-row .num { text-align: right; }
`;
