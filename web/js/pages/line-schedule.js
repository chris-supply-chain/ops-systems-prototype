// Line Schedule: every line, every day. The committed build against rated capacity and the site's time fences, what
// actually ran, the pack days MRP says the BMS shortage would cut, and the next shift's dispatch list in run order.
import { html, listeners, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';

// one validated categorical slot per SKU, the same everywhere on the page; the vehicle colourways take the slot
// nearest their paint (Dune sand, Slate blue-grey, Fern green, Ember orange) so the key is easy to remember
const SKU = {
  'LV1-DUNE': { short: 'Dune', color: 'var(--series-4)' },
  'LV1-SLATE': { short: 'Slate', color: 'var(--series-1)' },
  'LV1-FERN': { short: 'Fern', color: 'var(--series-3)' },
  'LV1-EMBER': { short: 'Ember', color: 'var(--series-2)' },
  'PK-STD': { short: 'Standard pack', color: 'var(--series-7)' },
  'PK-LRG': { short: 'Large pack', color: 'var(--series-5)' },
};
const ZONE = { past: 'Actual', frozen: 'Frozen', slushy: 'Slushy', liquid: 'Liquid' };
const TZ = { 'Asia/Taipei': 'Taipei time', 'America/Los_Angeles': 'Pacific time' };

let S = null;
// The page root persists across renders, so every delegated listener is tracked and removed.
const LISTENERS = listeners();

export async function render(el, ctx) {
  LISTENERS.clear();
  injectStyle('page-line-schedule', PAGE_CSS);
  const weeks = ctx.query.get('weeks') === '8' ? 8 : 4;
  const d = await api.get('/api/schedule/board', { back: 7, ahead: weeks === 8 ? 49 : 21 });
  S = { el, ctx, d, weeks };
  draw();
}

export function unmount() {
  LISTENERS.clear();
  S = null;
}

const shortSku = (id) => (SKU[id] ? SKU[id].short : id);
const minutes = (hhmm) => { const [h, m] = hhmm.split(/[:–-]/).map(Number); return h * 60 + m; };

function draw() {
  const { el, d } = S;
  const k = d.kpis;
  el.innerHTML = html`
    ${ui.pageHeader({ actions: html`<a class="btn" href="#/mps">${icon('target', 16)}<span>Master schedule</span></a>
      <a class="btn" href="#/mrp?item=BMS-B">${icon('grid', 16)}<span>Material plan</span></a>` })}
    <div class="pg-ls">
      ${insights(d)}
      <div class="kpi-row">
        ${k.load.map((l) => {
          const ln = d.lines.find((x) => x.line === l.line);
          return ui.kpi({
            label: `${l.name} · next 5 days`, value: fmt.pct(l.load, 0),
            hint: `${fmt.int(l.planned)} of ${fmt.int(l.capacity)} units · frozen to ${fmt.date(ln.fence.frozen_to)}`,
            status: l.load >= 1 ? { tone: 'warning', label: 'At capacity' } : null,
          });
        })}
        ${ui.kpi({ label: 'Adherence, last 5 days', value: fmt.pct(k.adherence, 0), hint: 'Built ÷ committed, all three lines' })}
        ${ui.kpi({
          label: 'Packs MRP would cut', value: fmt.int(k.mrp_cut_next),
          hint: k.mrp_cut_next ? `${k.mrp_cut_days.map((x) => fmt.date(x)).join(', ')} · BMS-B short` : 'Material covers the plan',
          status: k.mrp_cut_next ? { tone: 'critical', label: 'Line stop risk' } : null,
        })}
      </div>
      ${ui.card({
        title: 'Schedule board',
        subtitle: 'Units per line per day against rated capacity. Past days show what ran; inside the frozen window the schedule is released work orders; beyond it the plan can still move. Click a day for its work orders.',
        actions: html`<div class="ls-range" id="ls-range"></div>`,
        body: html`${legend(d)}${board(d)}`,
      })}
      <div class="grid">
        ${d.lines.map((ln) => html`<div class="span-4">${dispatchCard(ln)}</div>`)}
      </div>
      ${ui.card({ title: 'Lines, capacity and time fences', subtitle: 'Rated capacity is units per day at the target OEE; the fence says how far out the schedule is committed.', flush: true, body: capacityTable(d) })}
    </div>`;

  ui.segmented(el.querySelector('#ls-range'), {
    label: 'Horizon', value: String(S.weeks),
    options: [{ value: '4', label: '4 weeks' }, { value: '8', label: '8 weeks' }],
    onChange: (v) => S.ctx.setQuery({ weeks: v === '8' ? '8' : null }),
  });
  LISTENERS.listen(el, 'click', '.ls-cell[data-date]', (e, td) => openDay(+td.dataset.li, td.dataset.date));
  LISTENERS.listen(el, 'keydown', '.ls-cell[data-date]', (e, td) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDay(+td.dataset.li, td.dataset.date); }
  });
}

// ---------------------------------------------------------------------------------------------------------- insights
function insights(d) {
  const out = [];
  const cm = d.lines.filter((ln) => ln.site_id === 'CM-TXG' && ln.dispatch);
  const late = cm.map((ln) => {
    const end = minutes(ln.dispatch.shift.split('–')[1]);
    return { ln, over: minutes(ln.dispatch.finish) - end };
  }).filter((x) => x.over > 0);
  if (late.length) {
    const w = late.sort((a, b) => b.over - a.over)[0];
    const co = w.ln.dispatch.rows.filter((r) => r.changeover_min).length;
    out.push(ui.callout({
      tone: 'warning',
      title: `The CM commit leaves no time for changeovers: ${w.ln.name} runs ${w.over} min past its shift on ${w.ln.dispatch.dow}`,
      body: html`Each CM line is committed at exactly its rated capacity (${fmt.int(w.ln.capacity.rated_units_per_day)} a day at ${w.ln.capacity.takt_min} min takt fills the ${fmt.int(w.ln.capacity.shift_minutes)}-minute shift), and the line also needs ${co} colour changeovers of about ${w.ln.avg_changeover_min} min. That is why the lines built ${fmt.pct(d.kpis.adherence, 0)} of commit last week. Ask the CM to plan to demonstrated rate, or buy the changeover time back with longer colour batches.`,
    }));
  }
  if (d.kpis.mrp_cut_next) {
    out.push(ui.callout({
      tone: 'critical',
      title: `MRP cuts ${fmt.int(d.kpis.mrp_cut_next)} packs from the pack line on ${d.kpis.mrp_cut_days.map((x) => fmt.date(x)).join(', ')}`,
      body: html`BMS-B boards run out before the next receipt lands, so the released pack work orders can't all run. <a href="#/mrp?item=BMS-B">See the material plan</a> or <a href="#/loop">review the proposed expedite in the Closed Loop</a>.`,
    }));
  }
  return out.length ? html`<div class="stack">${out}</div>` : '';
}

// ----------------------------------------------------------------------------------------------------------- board
function legend(d) {
  const skus = d.items.filter((it) => SKU[it.item_id]);
  return html`<div class="ls-legend">
    <span class="ls-lg-group">${skus.map((it) => html`<span class="ls-lg"><span class="ls-sw" style="background:${SKU[it.item_id].color}"></span>${shortSku(it.item_id)}</span>`)}</span>
    <span class="ls-lg-group">
      <span class="ls-lg"><span class="ls-sw zone z-past"></span>Actual</span>
      <span class="ls-lg"><span class="ls-sw zone z-frozen"></span>Frozen</span>
      <span class="ls-lg"><span class="ls-sw zone z-slushy"></span>Slushy</span>
      <span class="ls-lg"><span class="ls-sw zone z-liquid"></span>Liquid</span>
      <span class="ls-lg"><span class="ls-cut-key">−17</span>MRP cut</span>
    </span>
  </div>`;
}

function board(d) {
  const today = d.as_of;
  return html`<div class="table-wrap ls-wrap"><table class="ls-board">
    <thead><tr><th class="ls-line-h" scope="col">Line</th>
      ${d.days.map((iso) => {
        const dt = new Date(`${iso}T12:00:00Z`);
        const dow = dt.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' });
        const dom = dt.getUTCDate();
        const off = dt.getUTCDay() === 0 || dt.getUTCDay() === 6;
        return html`<th scope="col" class="ls-day-h${iso === today ? ' today' : ''}${off ? ' off' : ''}">
          <span class="ls-dow">${iso === today ? 'Today' : dow}</span><span class="ls-dom">${dom === 1 || iso === d.days[0] ? dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : dom}</span></th>`;
      })}</tr></thead>
    <tbody>${d.lines.map((ln, li) => html`<tr>
      <th class="ls-line" scope="row"><strong>${ln.name}</strong><span>${ln.where} · ${fmt.int(ln.capacity.rated_units_per_day)}/day</span></th>
      ${ln.days.map((x) => cell(ln, li, x, today))}
    </tr>`)}</tbody>
  </table></div>`;
}

function cell(ln, li, x, today) {
  const edge = x.date === ln.fence.frozen_to ? ' fence-edge' : '';
  const cls = `ls-cell z-${x.zone}${x.date === today ? ' today' : ''}${edge}`;
  if (!x.working) {
    const label = x.reason && x.reason !== 'Weekend' ? x.reason.split(' ')[0] : '';
    return html`<td class="${cls} off" data-tip="${ln.name} · ${fmt.date(x.date)}\n${x.reason || 'No build'}">${label ? html`<span class="ls-off">${label}</span>` : ''}</td>`;
  }
  const past = x.zone === 'past';
  const shown = past ? (x.actual || 0) : x.planned;
  const ratio = past ? (x.adherence ?? 0) : (x.load ?? 0);
  const tone = past ? (ratio >= 0.98 ? 'good' : ratio >= 0.9 ? 'warning' : 'serious') : (ratio > 1.001 ? 'critical' : 'plan');
  const items = Object.entries(past && Object.keys(x.actual_by_item).length ? x.actual_by_item : x.plan_by_item);
  const total = items.reduce((a, [, v]) => a + v, 0) || 1;
  const mix = items.map(([id, v]) => `${shortSku(id)} ${v}`).join(' · ');
  const tip = [
    `${ln.name} · ${fmt.date(x.date)} · ${ZONE[x.zone]}${x.firmed ? ' (released work orders)' : ''}`,
    past ? `Built ${fmt.int(x.actual || 0)} of ${fmt.int(x.commit)} committed (${fmt.pct(x.adherence, 0)})`
      : `Planned ${fmt.int(x.planned)} of ${fmt.int(x.capacity)} capacity (${fmt.pct(x.load, 0)})`,
    mix,
    `${x.changeovers} changeover${x.changeovers === 1 ? '' : 's'} (${x.changeover_est ? '~' : ''}${x.changeover_min} min${x.changeover_est ? ', est.' : ''})`,
    x.downtime_min ? `Downtime ${x.downtime_min} min: ${x.downtime.map((e) => e.reason).join(', ')}` : '',
    x.mrp_cut ? `MRP cuts ${x.mrp_cut} packs: BMS-B short` : '',
  ].filter(Boolean).join('\n');
  return html`<td class="${cls}" data-li="${li}" data-date="${x.date}" tabindex="0" role="button" aria-label="${tip.replace(/\n/g, '. ')}" data-tip="${tip}">
    <div class="ls-num"><b>${fmt.int(shown)}</b><span>/${fmt.int(past ? x.commit : x.capacity)}</span></div>
    <div class="ls-bar tone-${tone}"><span style="width:${Math.min(100, Math.round(ratio * 100))}%"></span></div>
    <div class="ls-mix">${items.map(([id, v]) => html`<span style="flex:${v / total};background:${SKU[id] ? SKU[id].color : 'var(--series-other)'}"></span>`)}</div>
    ${x.mrp_cut || x.downtime_min ? html`<div class="ls-flags">
      ${x.mrp_cut ? html`<span class="ls-cut">−${fmt.int(x.mrp_cut)}</span>` : ''}
      ${x.downtime_min ? html`<span class="ls-dt">${icon('alert-triangle', 11)}${x.downtime_min}m</span>` : ''}
    </div>` : ''}
  </td>`;
}

// -------------------------------------------------------------------------------------------------------- day drawer
function openDay(li, date) {
  const ln = S.d.lines[li];
  const x = ln.days.find((y) => y.date === date);
  if (!x) return;
  const past = x.zone === 'past';
  ui.drawer.open({
    title: `${ln.name} · ${fmt.date(x.date)}`,
    subtitle: `${ln.where} · ${ZONE[x.zone]}${x.firmed ? ' · released work orders' : ''}`,
    body: html`<div class="ls-drawer">
      <div class="kpi-row compact">
        ${ui.kpi({ label: past ? 'Built' : 'Planned', value: fmt.int(past ? x.actual || 0 : x.planned), hint: past ? `of ${fmt.int(x.commit)} committed` : `of ${fmt.int(x.capacity)} capacity` })}
        ${ui.kpi({ label: past ? 'Adherence' : 'Load', value: fmt.pct(past ? x.adherence : x.load, 0) })}
        ${ui.kpi({ label: 'Changeovers', value: fmt.int(x.changeovers), hint: `${x.changeover_est ? '~' : ''}${x.changeover_min} min${x.changeover_est ? ' (est.)' : ''}` })}
      </div>
      ${x.mrp_cut ? ui.callout({ tone: 'critical', title: `MRP cuts ${x.mrp_cut} packs on this day`, body: html`BMS-B is short. <a href="#/mrp?item=BMS-B">Material plan</a> · <a href="#/loop">Closed Loop</a>` }) : ''}
      <h4 class="section-title">Work orders</h4>
      ${x.work_orders.length ? html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Work order</th><th>Item</th><th class="num">Qty</th><th class="num">Done</th><th>Status</th></tr></thead>
        <tbody>${x.work_orders.map((w) => html`<tr><td class="mono small">${w.wo_id}</td><td>${link.item(w.item_id)} <span class="muted small">${shortSku(w.item_id)}</span></td>
          <td class="num">${fmt.int(w.qty)}</td><td class="num">${fmt.int(w.completed)}</td><td>${ui.statusChip(w.status)}</td></tr>`)}</tbody></table></div>`
        : html`<p class="muted small">No work orders released for this day yet. The line runs to the ${ln.plan_type === 'CM_COMMIT' ? 'CM commit' : 'pack MPS'} (${ln.plan_version}).</p>`}
      <h4 class="section-title">${past ? 'Built by item' : 'Plan by item'}</h4>
      <div class="ls-items">${Object.entries(past ? x.actual_by_item : x.plan_by_item).map(([id, v]) => html`<span class="ls-item"><span class="ls-sw" style="background:${SKU[id] ? SKU[id].color : 'var(--series-other)'}"></span>${shortSku(id)} <b>${fmt.int(v)}</b></span>`)}</div>
      ${x.downtime.length ? html`<h4 class="section-title">Downtime</h4><ul class="ls-events">${x.downtime.map((e) => html`<li>${ui.chip('warning', fmt.title(e.category))} ${e.reason} · ${e.minutes} min</li>`)}</ul>` : ''}
    </div>`,
  });
}

// ---------------------------------------------------------------------------------------------------------- dispatch
function dispatchCard(ln) {
  const dp = ln.dispatch;
  if (!dp) return ui.card({ title: `${ln.name} · dispatch`, body: ui.empty('Nothing scheduled in the window.') });
  const end = minutes(dp.shift.split('–')[1]);
  const over = minutes(dp.finish) - end;
  return ui.card({
    title: `${ln.name} · dispatch`,
    subtitle: `${dp.dow} ${fmt.date(dp.date)} · shift ${dp.shift} ${TZ[dp.tz] || dp.tz} · run order by batch`,
    flush: true,
    body: html`<div class="table-wrap"><table class="table dense ls-dispatch">
      <thead><tr><th class="num">#</th><th>Item · work order</th><th class="num">Qty</th><th>Runs</th></tr></thead>
      <tbody>${dp.rows.map((r) => html`${r.changeover_min ? html`<tr class="ls-co"><td></td><td colspan="3">Changeover · ${r.changeover_min} min</td></tr>` : ''}
        <tr class="${r.over_shift ? 'ls-over' : ''}"><td class="num">${r.seq}</td>
          <td><span class="ls-sw" style="background:${SKU[r.item_id] ? SKU[r.item_id].color : 'var(--series-other)'}"></span> ${shortSku(r.item_id)}
            <span class="ls-wo mono">${r.wo_id || 'planned, not released'}</span></td>
          <td class="num">${fmt.int(r.qty)}</td><td class="mono small">${r.start}–${r.end}</td></tr>`)}</tbody></table></div>
      <div class="ls-dp-foot${over > 0 ? ' warn' : ''}">${icon(over > 0 ? 'alert-triangle' : 'check-circle', 14)}
        <span>${fmt.int(dp.total)} of ${fmt.int(dp.capacity)} units · finishes ${dp.finish}${over > 0 ? `, ${over} min past the shift` : ', inside the shift'}</span></div>
      ${dp.material_short ? html`<div class="ls-dp-foot crit">${icon('alert-octagon', 14)}<span>MRP cuts ${fmt.int(dp.material_short)} packs this day: BMS-B short</span></div>` : ''}`,
  });
}

// ---------------------------------------------------------------------------------------------------------- capacity
function capacityTable(d) {
  return html`<div class="table-wrap"><table class="table">
    <thead><tr><th>Line</th><th>Makes</th><th class="num">Rated / day</th><th class="num">Takt</th><th>Shift</th><th class="num">OEE target</th>
      <th>Frozen to</th><th>Slushy to</th><th class="num">Avg changeover</th><th>Next change</th></tr></thead>
    <tbody>${d.lines.map((ln) => {
      const c = ln.capacity;
      const ch = ln.capacity_change;
      return html`<tr><td><strong>${ln.name}</strong> <span class="muted small">${ln.where}</span></td><td>${ln.family}</td>
        <td class="num">${fmt.int(c.rated_units_per_day)}</td><td class="num">${c.takt_min} min</td>
        <td>${c.shifts_per_day} × ${fmt.int(c.shift_minutes)} min</td><td class="num">${fmt.pct(c.oee_target, 0)}</td>
        <td>${fmt.date(ln.fence.frozen_to)} <span class="muted small">${ln.fence.frozen_days} d</span></td>
        <td>${fmt.date(ln.fence.slushy_to)} <span class="muted small">${ln.fence.slushy_days} d</span></td>
        <td class="num">${ln.avg_changeover_min} min</td>
        <td>${ch ? html`${fmt.int(ch.rated_units_per_day)}/day from ${fmt.date(ch.effective_from)} <span class="muted small">takt ${ch.takt_min} min</span>` : html`<span class="nil">—</span>`}</td></tr>`;
    })}</tbody></table></div>`;
}

const PAGE_CSS = `
.pg-ls .ls-legend { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 20px; margin: -2px 0 12px; font-size: 12.5px; color: var(--ink-2); }
.pg-ls .ls-lg-group { display: inline-flex; flex-wrap: wrap; gap: 6px 14px; }
.pg-ls .ls-lg { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.pg-ls .ls-sw, .ls-drawer .ls-sw, .pg-ls .ls-dispatch .ls-sw { display: inline-block; width: 11px; height: 11px; border-radius: 3px; vertical-align: -1px; }
.pg-ls .ls-sw.zone { width: 16px; height: 12px; box-shadow: inset 0 0 0 1px var(--hairline-strong); }
.pg-ls .z-past { background: var(--surface-2); }
.pg-ls .z-frozen { background: color-mix(in srgb, var(--info) 9%, var(--surface)); }
.pg-ls .z-slushy { background: color-mix(in srgb, var(--info) 4%, var(--surface)); }
.pg-ls .z-liquid { background: var(--surface); }
.pg-ls .ls-cut-key { display: inline-flex; align-items: center; height: 16px; padding: 0 4px; border-radius: 4px; background: var(--critical); color: #fff; font: 700 10px/1 var(--font-ui); }
.pg-ls .ls-wrap { border: 1px solid var(--hairline); border-radius: 10px; }
.pg-ls .ls-board { border-collapse: separate; border-spacing: 0; font-size: 12px; min-width: 100%; }
.pg-ls .ls-board th, .pg-ls .ls-board td { border-bottom: 1px solid var(--hairline); }
.pg-ls .ls-line-h, .pg-ls .ls-line { position: sticky; left: 0; z-index: 2; background: var(--surface); text-align: left; border-right: 1px solid var(--hairline-strong); }
.pg-ls .ls-line-h { padding: 8px 12px; font: 600 11px/1 var(--font-cond); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3); }
.pg-ls .ls-line { min-width: 150px; padding: 10px 12px; vertical-align: middle; }
.pg-ls .ls-line strong { display: block; font: 600 14px/1.2 var(--font-cond); }
.pg-ls .ls-line span { display: block; font-size: 11.5px; color: var(--ink-3); margin-top: 2px; font-weight: 400; }
.pg-ls .ls-day-h { min-width: 50px; padding: 6px 4px; text-align: center; font-weight: 500; color: var(--ink-3); border-left: 1px solid var(--hairline); }
.pg-ls .ls-day-h.off { color: color-mix(in srgb, var(--ink-3) 70%, transparent); }
.pg-ls .ls-day-h.today { color: var(--ink); box-shadow: inset 0 -3px 0 var(--sign-yellow); }
.pg-ls .ls-dow { display: block; font: 600 10.5px/1.2 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; }
.pg-ls .ls-dom { display: block; font: 500 12px/1.3 var(--font-mono); white-space: nowrap; }
.pg-ls .ls-cell { position: relative; min-width: 52px; height: 72px; padding: 6px 5px 5px; border-left: 1px solid var(--hairline); vertical-align: top; cursor: pointer; }
.pg-ls .ls-cell.off { cursor: default; background-image: repeating-linear-gradient(135deg, transparent 0 5px, var(--hairline) 5px 6px); }
.pg-ls .ls-cell[data-date]:hover { box-shadow: inset 0 0 0 2px var(--link); }
.pg-ls .ls-cell[data-date]:focus-visible { outline: none; box-shadow: inset 0 0 0 2px var(--focus); }
.pg-ls .ls-cell.today { box-shadow: inset 2px 0 0 var(--sign-yellow), inset -2px 0 0 var(--sign-yellow); }
.pg-ls .ls-cell.fence-edge { border-right: 2px dashed var(--ink-2); }
.pg-ls .ls-off { display: block; font: 600 10px/1.1 var(--font-cond); letter-spacing: .04em; text-transform: uppercase; color: var(--ink-3); writing-mode: vertical-rl; transform: rotate(180deg); margin: 2px auto 0; }
.pg-ls .ls-num { display: flex; align-items: baseline; justify-content: center; gap: 1px; font-variant-numeric: tabular-nums; }
.pg-ls .ls-num b { font: 600 15px/1 var(--font-ui); color: var(--ink); }
.pg-ls .ls-num span { font-size: 10.5px; color: var(--ink-3); }
.pg-ls .ls-bar { height: 4px; margin: 7px 2px 0; border-radius: 2px; background: var(--grid); overflow: hidden; }
.pg-ls .ls-bar span { display: block; height: 100%; border-radius: 2px; background: var(--tone, var(--seq-450)); }
.pg-ls .ls-bar.tone-plan { --tone: var(--seq-450); }
.pg-ls .ls-mix { display: flex; gap: 2px; height: 4px; margin: 4px 2px 0; border-radius: 2px; overflow: hidden; }
.pg-ls .ls-mix span { display: block; height: 100%; }
.pg-ls .ls-flags { display: flex; justify-content: center; gap: 3px; margin-top: 5px; }
.pg-ls .ls-cut { padding: 1px 4px; border-radius: 3px; background: var(--critical); color: #fff; font: 700 10px/1.2 var(--font-ui); }
.pg-ls .ls-dt { display: inline-flex; align-items: center; gap: 1px; font: 600 10px/1.2 var(--font-ui); color: var(--ink-2); }
.pg-ls .ls-dt .icon { color: var(--warning); }
.pg-ls .ls-dispatch td, .pg-ls .ls-dispatch th { white-space: nowrap; }
.pg-ls .ls-wo { display: block; margin: 2px 0 0 17px; font-size: 11px; color: var(--ink-3); }
.pg-ls .ls-co td { padding-top: 2px !important; padding-bottom: 2px !important; font-size: 11.5px; color: var(--ink-3); font-style: italic; background: var(--surface-2); }
.pg-ls .ls-over td { color: var(--delta-bad); }
.pg-ls .ls-dp-foot { display: flex; align-items: center; gap: 8px; padding: 10px 16px 12px; border-top: 1px solid var(--hairline); font-size: 12.5px; color: var(--ink-2); }
.pg-ls .ls-dp-foot .icon { color: var(--good); flex: none; }
.pg-ls .ls-dp-foot.warn .icon { color: var(--warning); }
.pg-ls .ls-dp-foot.crit .icon { color: var(--critical); }
.ls-drawer .kpi-row.compact { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.ls-drawer .ls-items { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 13px; }
.ls-drawer .ls-item { display: inline-flex; align-items: center; gap: 6px; }
.ls-drawer .ls-events { margin: 0; padding-left: 18px; display: grid; gap: 6px; font-size: 13px; }
`;
