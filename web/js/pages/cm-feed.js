// CM Feed: the visibility feed out of the CM's own MES (Formosa Assembly Partners,
// Taichung, UTC+8). Raw payloads land verbatim; a mapping-driven normalizer writes
// station events, units and genealogy; nothing is dropped. Every message ends OK,
// WARN, DUPLICATE, QUARANTINED or REPLAYED, with a reason and a trace.
import { html, raw, esc, on, $, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

const TPE = 'Asia/Taipei';
const PT = 'America/Los_Angeles';
const STATUSES = ['', 'OK', 'WARN', 'DUPLICATE', 'QUARANTINED', 'REPLAYED'];
const PICKS = [
  { label: 'Timestamp corrected', status: 'WARN', q: 'TZ_CORRECTED' },
  { label: 'Chinese defect text', status: 'WARN', q: 'Chinese' },
  { label: 'Provisional component', status: 'WARN', q: 'provisional' },
  { label: 'Retry-storm duplicate', status: 'DUPLICATE', q: '' },
  { label: 'Replayed after v2', status: 'REPLAYED', q: '' },
  { label: 'Unknown station S65', status: 'QUARANTINED', q: 'S65' },
];

let S = null;

const both = (ts) => (ts ? html`<span class="nowrap">${fmt.dt(ts, TPE)} <span class="muted">Taipei</span></span> <span class="nowrap muted">· ${fmt.dt(ts, PT)} PT</span>` : '—');

function dur(sec) {
  if (sec == null) return '—';
  if (sec < 90) return `${Math.round(sec)} s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  return `${fmt.num(sec / 3600, 1)} h`;
}

export async function render(el, ctx) {
  injectStyle('page-cm-feed', PAGE_CSS);
  const d = await api.get('/api/cm-feed/overview');
  S = { el, ctx, d, list: null, listState: { status: '', q: '' } };
  const k = d.kpis;
  const tab = ctx.query.get('tab') || (k.quarantined ? 'quarantine' : 'inspector');

  el.innerHTML = html`
    ${ui.pageHeader({ actions: html`${ui.chip('good', `Mapping ${k.active_mapping} active`, { icon: 'check-circle' })}` })}
    <div class="pg-cm-feed">
      <div class="cf-pipe">
        <div class="cf-node"><span class="cf-k">Source</span><strong>FAP MES</strong><span class="muted small">CM-owned · Taichung, Taiwan · UTC+8</span></div>
        <span class="cf-arrow">${icon('arrow-right', 16)}</span>
        <div class="cf-node"><span class="cf-k">Transport</span><strong>JSON webhook</strong><span class="muted small">HTTPS → our gateway, stamped on receipt</span></div>
        <span class="cf-arrow">${icon('arrow-right', 16)}</span>
        <div class="cf-node"><span class="cf-k">Landing</span><strong>${link.table('raw_cm_mes_event')}</strong><span class="muted small">${fmt.int(k.total)} payloads, verbatim</span></div>
        <span class="cf-arrow">${icon('arrow-right', 16)}</span>
        <div class="cf-node"><span class="cf-k">Normalizer</span><strong>mapping ${k.active_mapping}</strong><span class="muted small">versioned data, not code</span></div>
        <span class="cf-arrow">${icon('arrow-right', 16)}</span>
        <div class="cf-node"><span class="cf-k">Core</span><strong>${link.table('station_event')} · ${link.table('unit')} · ${link.table('genealogy')}</strong><span class="muted small">WIP, FPY, as-built</span></div>
      </div>

      <div class="kpi-row">
        ${ui.kpi({ label: 'Messages · 7 days', value: fmt.int(k.n7), hint: html`Last ${fmt.rel(k.last_received, d.now)} · ${fmt.int(k.last_day_n)} on the last shift` })}
        ${ui.kpi({ label: 'Median latency', value: dur(k.p50), hint: `p95 ${dur(k.p95)}, driven by the outage burst` })}
        ${ui.kpi({ label: 'Quarantined now', value: fmt.int(k.quarantined), hint: 'Held with a reason, never dropped' })}
        ${ui.kpi({ label: 'Duplicates suppressed', value: fmt.int(k.duplicates), hint: 'Deduped on event identity' })}
        ${ui.kpi({ label: 'Timestamps corrected', value: fmt.int(k.tz_fixed), hint: 'Taipei local labeled as UTC' })}
        ${ui.kpi({ label: 'Provisional components', value: fmt.int(k.provisional), hint: 'Installed with no supplier ASN' })}
      </div>

      <div class="grid">
        <div class="span-8">${ui.card({
          title: 'Messages received per hour, last 14 days',
          subtitle: 'Day-shift rhythm in Taipei. The outage shows as a gap followed by a burst of late arrivals.',
          tableToggle: true, body: hourlyChart(d),
        })}</div>
        <div class="span-4">${ui.card({ title: 'Where every message ended', subtitle: `${fmt.int(k.total)} messages since SOP`, body: statusList(d) })}</div>

        <div class="span-7">${ui.card({
          title: 'Incidents the pipeline absorbed',
          subtitle: 'Derived from the landing table and normalizer outcomes, newest first',
          body: incidents(d),
        })}</div>
        <div class="span-5">${ui.card({
          title: 'Latency: event to receipt, last 14 days',
          subtitle: `Median ${dur(k.p50)} · p95 ${dur(k.p95)}. The long tail is the buffered outage.`,
          tableToggle: true,
          body: charts.bar({
            categories: d.latency_hist.map((b) => b.bucket), series: [{ name: 'Messages', values: d.latency_hist.map((b) => b.n) }],
            horizontal: true, labelWidth: 86, yFormat: (v) => fmt.int(v), categoryLabel: 'Latency', ariaLabel: 'Latency histogram',
          }),
        })}</div>

        <div class="span-12">
          <div data-tabs></div>
          <div class="cf-panel" data-panel></div>
        </div>
      </div>
    </div>`;

  const tabsEl = $('[data-tabs]', el);
  ui.tabs(tabsEl, {
    tabs: [
      { id: 'quarantine', label: 'Quarantine', count: d.quarantine.length, icon: 'alert-octagon' },
      { id: 'inspector', label: 'Message inspector', icon: 'search' },
      { id: 'mappings', label: 'Mappings', icon: 'layers' },
      { id: 'recon', label: 'CM Excel vs MES', icon: 'checklist' },
      { id: 'asn', label: 'ASN & provisional', count: d.provisional.length, icon: 'link' },
    ],
    active: tab,
    onChange: (id) => { ctx.setQuery({ tab: id }, { silent: true }); drawPanel(id); },
  });
  drawPanel(tab);

  on(el, 'click', '[data-raw]', (e, t) => { e.preventDefault(); openMessage(+t.dataset.raw); });
  on(el, 'click', '[data-pick]', async (e, t) => {
    const p = PICKS[+t.dataset.pick];
    const r = await api.get('/api/cm-feed/messages', { status: p.status, q: p.q, limit: 1 });
    if (r.rows.length) openMessage(r.rows[0].raw_id);
    else ui.toast('No message of that kind', 'info');
  });
  const raw0 = ctx.query.get('raw');
  if (raw0) openMessage(+raw0);
}

export function unmount() {
  if (ui.drawer.isOpen) ui.drawer.close();
  S = null;
}

// ---------------------------------------------------------------------------
function hourlyChart(d) {
  const byHour = new Map(d.hourly.map((h) => [h.hour, h]));
  const end = Date.parse(d.now);
  const start = end - 14 * 86400000;
  const pts = [];
  const late = [];
  for (let t = Math.ceil(start / 3600000) * 3600000; t <= end; t += 3600000) {
    const key = new Date(t).toISOString().slice(0, 13) + ':00:00Z';
    const h = byHour.get(key);
    pts.push({ x: key, y: h ? h.n : 0 });
    late.push({ x: key, y: h ? h.late : 0 });
  }
  return charts.line({
    series: [{ name: 'Received', points: pts, width: 1.5 }, { name: 'Arrived > 30 min late', points: late, width: 1.5 }],
    height: 240, area: true, yFormat: (v) => fmt.int(v), xLabel: 'Hour (UTC)',
    tooltipX: (ms) => `${fmt.dt(new Date(ms).toISOString(), TPE)} Taipei`, ariaLabel: 'Messages per hour',
  });
}

function statusList(d) {
  const total = d.status_groups.reduce((a, g) => a + g.n, 0) || 1;
  return html`<ul class="cf-status">${d.status_groups.map((g) => html`<li>
      <div class="row">${ui.statusChip(g.status)}<span class="small cf-status-g">${g.group}</span><span class="spacer"></span><span class="num strong">${fmt.int(g.n)}</span></div>
      ${ui.meter({ value: Math.max(g.n / total, g.n ? 0.004 : 0), max: 1, tone: ui.toneOf(g.status) === 'good' ? null : ui.toneOf(g.status) })}
    </li>`)}</ul>
    <div class="cf-picks"><span class="tiny muted">Inspect an example</span>${PICKS.map((p, i) => html`<button class="btn sm btn-ghost" type="button" data-pick="${i}">${p.label}</button>`)}</div>`;
}

function incidents(d) {
  return ui.timeline(d.incidents.map((i) => ({
    ts: i.start, tsLabel: `${fmt.dt(i.start, TPE)} Taipei`, tone: i.tone,
    meta: `${fmt.int(i.messages)} message${i.messages === 1 ? '' : 's'}`,
    title: i.title,
    detail: html`${i.detail} <span class="cf-outcome">${i.outcome}</span>`,
  })));
}

// ---------------------------------------------------------------------------
function drawPanel(id) {
  if (!S) return;
  const panel = $('[data-panel]', S.el);
  const d = S.d;
  if (id === 'quarantine') {
    const byFix = new Map();
    for (const qq of d.quarantine) {
      const key = qq.fix;
      const cur = byFix.get(key) || { fix: qq.fix, n: 0, reason: qq.note.replace(/ on L\d.*/, '') };
      cur.n += 1;
      byFix.set(key, cur);
    }
    panel.innerHTML = html`${d.quarantine.length ? html`<div class="cf-qhead">
        ${ui.callout({ tone: 'neutral', title: 'Quarantine is a state, not a trash can',
          body: 'Each message keeps its payload, its reason and a suggested fix. When the fix ships as a new mapping version, after change review, the quarantine replays in arrival order.' })}
        ${Array.from(byFix.values()).map((g) => ui.callout({ tone: 'critical', title: `${g.n} × ${g.reason}`,
          body: html`${g.fix} <a class="ent-link" href="#/loop">Closed Loop →</a>` }))}
      </div>` : ''}
      <div data-q style="margin-top:12px"></div>`;
    ui.dataTable($('[data-q]', panel), {
      columns: [
        { key: 'raw_id', label: 'Raw id', mono: true, render: (r) => html`<a class="id-link" href="#" data-raw="${r.raw_id}">#${r.raw_id}</a>` },
        { key: 'received_at', label: 'Received', render: (r) => both(r.received_at) },
        { key: 'serial', label: 'Unit', render: (r) => link.serial(r.serial) },
        { key: 'station', label: 'Station', mono: true },
        { key: 'line', label: 'Line' },
        { key: 'note', label: 'Reason', wrap: true, render: (r) => html`<span class="small">${r.note}</span>` },
        { key: 'mapping_version', label: 'Mapping', mono: true },
      ],
      rows: d.quarantine, pageSize: 10, empty: 'Nothing in quarantine.',
      onRowClick: (r) => openMessage(r.raw_id),
    });
  } else if (id === 'inspector') {
    panel.innerHTML = html`<div class="filter-row cf-filter">
        <label class="dt-search">${icon('search', 15)}<input class="input sm" type="text" data-iq placeholder="Serial or note text (e.g. LV1-26W38, TZ_CORRECTED)" value="${S.listState.q}"></label>
        <select class="select sm" data-is aria-label="Status">${STATUSES.map((s) => html`<option value="${s}"${s === S.listState.status ? raw(' selected') : ''}>${s ? fmt.title(s) : 'All statuses'}</option>`)}</select>
        <span class="muted small" data-icount></span>
      </div><div data-ilist></div>`;
    const load = async () => {
      const r = await api.get('/api/cm-feed/messages', { status: S.listState.status, q: S.listState.q, limit: 200 });
      $('[data-icount]', panel).textContent = `Newest ${fmt.int(r.rows.length)} of ${fmt.int(r.total)}`;
      ui.dataTable($('[data-ilist]', panel), {
        columns: [
          { key: 'raw_id', label: 'Raw id', mono: true, render: (x) => html`<a class="id-link" href="#" data-raw="${x.raw_id}">#${x.raw_id}</a>` },
          { key: 'received_at', label: 'Received (Taipei)', render: (x) => fmt.dt(x.received_at, TPE) },
          { key: 'serial', label: 'Unit', mono: true },
          { key: 'station', label: 'Station', mono: true },
          { key: 'result', label: 'CM result', mono: true },
          { key: 'event_time', label: 'Event time as sent', mono: true },
          { key: 'mapping_version', label: 'Map' },
          { key: 'ingest_status', label: 'Outcome', render: (x) => ui.statusChip(x.ingest_status) },
          { key: 'ingest_note', label: 'Note', wrap: true, render: (x) => (x.ingest_note ? html`<span class="small">${x.ingest_note}</span>` : '') },
        ],
        rows: r.rows, pageSize: 15, dense: true, onRowClick: (x) => openMessage(x.raw_id),
      });
    };
    let tmr = null;
    $('[data-iq]', panel).addEventListener('input', (e) => {
      S.listState.q = e.target.value.trim();
      clearTimeout(tmr);
      tmr = setTimeout(load, 250);
    });
    $('[data-is]', panel).addEventListener('change', (e) => { S.listState.status = e.target.value; load(); });
    load();
  } else if (id === 'mappings') {
    const m = d.mappings;
    const cur = m[m.length - 1] || {};
    const dict = Object.entries(cur.defect_text || {});
    panel.innerHTML = html`<div class="grid">
      <div class="span-7">${ui.card({
        title: `Mapping ${m.length > 1 ? m[m.length - 2].version + ' → ' : ''}${cur.version || ''}`,
        subtitle: 'Field-by-field: which payload key feeds which canonical field. A new payload shape is a new mapping row plus a review, not a code deploy.',
        flush: true,
        body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Canonical field</th><th>${m.length > 1 ? m[m.length - 2].version : 'Before'} key</th><th>${cur.version} key</th><th></th></tr></thead>
          <tbody>${d.mapping_diff.map((x) => html`<tr><td>${fmt.title(x.field)}</td><td class="mono">${x.a || '—'}</td><td class="mono">${x.b || '—'}</td>
            <td>${x.changed ? ui.chip('warning', 'Changed') : ui.chip('neutral', 'Same', { icon: false })}</td></tr>`)}</tbody></table></div>`,
      })}</div>
      <div class="span-5">${ui.card({
        title: 'Versions',
        body: ui.timeline(m.slice().reverse().map((x) => ({
          ts: x.effective_from, tsLabel: `${fmt.dt(x.effective_from, TPE)} Taipei`, tone: x.version === cur.version ? 'good' : 'neutral',
          title: html`<span class="mono">${x.version}</span> · ${x.notes}`,
          detail: `Stations: ${x.stations.join(', ')} · ${x.defect_codes} defect codes mapped`,
        }))),
      })}
      ${ui.callout({ tone: 'serious', title: 'Missing: station S65', body: 'The CM routes EOL rework through S65, which no mapping version knows. Mapping v2.1 adds the alias once S65 exists in station master data.' })}</div>
      <div class="span-12">${ui.card({
        title: 'Bilingual defect dictionary',
        subtitle: 'The CM sometimes sends only the Traditional Chinese description. The mapping resolves it to the CM code and then to ours.',
        flush: true,
        body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>CM description (繁體中文)</th><th>Our defect code</th></tr></thead>
          <tbody>${dict.map(([zh, code]) => html`<tr><td class="cf-zh">${zh}</td><td class="mono">${code}</td></tr>`)}</tbody></table></div>`,
      })}</div></div>`;
  } else if (id === 'recon') {
    const diffs = d.recon.filter((r) => r.excel !== r.mes).length;
    panel.innerHTML = html`<div class="grid">
      <div class="span-7">${ui.card({
        title: 'Daily output: the CM\'s Excel vs its own MES events',
        subtitle: 'The CM emails a bilingual Excel every evening. We parse it and keep it beside the event stream, reconciled, never merged.',
        flush: true,
        body: html`<div data-rt></div>`,
      })}</div>
      <div class="span-5">
        ${ui.callout({ tone: diffs ? 'warning' : 'good', title: diffs ? `${diffs} report-days disagree with the MES` : 'Every report-day ties out with the MES events',
          body: 'An Excel number and an event count agreeing is itself evidence the feed is complete for that day. A disagreement is the first sign of a dropped or quarantined message.' })}
        ${ui.card({
          title: 'Consigned stock: CM count vs our serials',
          subtitle: `CM report ${d.stock_recon[0] ? fmt.date(d.stock_recon[0].report_date) : ''} vs serial-level units at the CM now`,
          flush: true,
          body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Item</th><th class="num">CM Excel</th><th class="num">Our serials</th><th class="num">Diff</th></tr></thead>
            <tbody>${d.stock_recon.map((r) => html`<tr><td>${link.item(r.item_id)}</td><td class="num">${fmt.int(r.excel)}</td><td class="num">${fmt.int(r.system_now)}</td>
              <td class="num">${r.excel - r.system_now ? ui.chip('warning', `${r.excel - r.system_now > 0 ? '+' : ''}${r.excel - r.system_now}`) : ui.chip('good', '0')}</td></tr>`)}</tbody></table></div>
            <p class="small muted cf-pad">Drive units that reached the CM without an ASN (the emergency hand-carry) are invisible to serial-level stock until they're scanned into a vehicle, so the CM's shelf count runs ahead of ours.</p>`,
        })}
      </div></div>`;
    ui.dataTable($('[data-rt]', panel), {
      columns: [
        { key: 'report_date', label: 'Report date', format: (v) => fmt.date(v) },
        { key: 'line', label: 'Line' },
        { key: 'planned', label: 'Plan', num: true },
        { key: 'excel', label: 'Excel built', num: true },
        { key: 'mes', label: 'MES S80 passes', num: true },
        { key: 'diff', label: 'Diff', num: true, value: (r) => r.excel - r.mes, render: (r) => (r.excel - r.mes ? ui.chip('warning', String(r.excel - r.mes)) : ui.chip('good', '0')) },
        { key: 'email_id', label: 'Source', render: (r) => (r.email_id ? html`<a class="ent-link" href="#/integrations?email=${r.email_id}">email #${r.email_id}</a>` : '—') },
      ],
      rows: d.recon, pageSize: 12, dense: true,
    });
  } else if (id === 'asn') {
    panel.innerHTML = html`<div class="grid">
      <div class="span-7">${ui.card({
        title: `Installed with no supplier ASN (${d.provisional.length})`,
        subtitle: 'Drive units scanned at S20 that no ASN ever described. No motor, controller or magnet-lot genealogy below them.',
        flush: true, body: html`<div data-pv></div>`,
      })}</div>
      <div class="span-5">${ui.card({
        title: 'Resolved when the ASN arrived',
        subtitle: 'Provisional units the supplier ASN later completed',
        body: d.asn_resolved.length ? html`${d.asn_resolved.map((a) => html`<div class="cf-asn">
            <div class="row"><span class="mono strong">${a.asn_no}</span>${ui.chip('good', 'Resolved')}<span class="spacer"></span><span class="small muted">${a.supplier_id}</span></div>
            <div class="small">Shipped ${fmt.date(a.ship_date)} · ASN received ${fmt.dt(a.received_at, TPE)} Taipei</div>
            <div class="tiny muted">${a.ingest_note}</div></div>`)}`
          : ui.empty('No late ASNs.'),
      })}
      ${ui.callout({ tone: 'serious', title: 'Why this matters', body: 'A magnet-lot or controller recall traces through the drive unit. With no ASN, the trace stops at the drive unit, and the vehicle it sits in drops out of the recall scope. The data contract on installed drive units flags every one.' })}</div></div>`;
    ui.dataTable($('[data-pv]', panel), {
      columns: [
        { key: 'serial', label: 'Drive unit', render: (r) => link.serial(r.serial) },
        { key: 'item_id', label: 'Item', mono: true },
        { key: 'parent_serial', label: 'Installed in', render: (r) => link.serial(r.parent_serial) },
        { key: 'station', label: 'Station', mono: true },
        { key: 'installed_at', label: 'Installed', render: (r) => both(r.installed_at) },
      ],
      rows: d.provisional, pageSize: 10, dense: true, empty: 'No provisional units.',
    });
  }
}

// ---------------------------------------------------------------------------
async function openMessage(rawId) {
  const body = ui.drawer.open({ title: `raw_cm_mes_event #${rawId}`, subtitle: 'Loading…', body: ui.loading('Loading message'), width: 760 });
  let m;
  try {
    m = await api.get(`/api/cm-feed/message/${rawId}`);
  } catch (err) {
    body.innerHTML = String(ui.errorBox(err));
    return;
  }
  const ev = m.events[0];
  const r = m.raw;
  ui.drawer.open({
    title: `raw_cm_mes_event #${rawId}`,
    subtitle: html`${ui.statusChip(r.ingest_status)} <span class="mono small">mapping ${m.mapping_used || '—'}</span> ${m.serial ? link.serial(m.serial) : ''}`,
    width: 760,
    body: html`
      <dl class="kv">
        <dt>Received</dt><dd>${both(r.received_at)}</dd>
        <dt>Source</dt><dd>${r.source_system} · ingest run ${r.run_id ?? '—'}</dd>
        ${r.ingest_note ? html`<dt>Outcome</dt><dd>${r.ingest_note}</dd>` : ''}
        ${m.duplicate_of ? html`<dt>Duplicate of</dt><dd><a class="id-link" href="#" data-raw="${m.duplicate_of}">#${m.duplicate_of}</a> (the event below was written from that message)</dd>` : ''}
      </dl>
      ${m.fields.length ? html`<h4 class="section-title">Field by field</h4>
        <div class="table-wrap"><table class="table dense"><thead><tr><th>Canonical</th><th>Payload key</th><th>As sent</th><th>Stored</th></tr></thead>
        <tbody>${m.fields.map((f) => html`<tr class="${f.present ? '' : 'cf-missing'}"><td>${fmt.title(f.canonical)}</td><td class="mono">${f.raw_key}</td>
          <td class="mono">${f.present ? (f.raw_value === '' ? html`<span class="muted">(empty)</span>` : String(f.raw_value)) : html`<span class="cf-miss">missing</span>`}</td>
          <td class="mono">${f.value == null ? html`<span class="muted">—</span>` : String(f.value)}${f.column ? html`<div class="tiny muted">${f.column.includes('.') ? f.column : 'station_event.' + f.column}</div>` : ''}</td></tr>`)}</tbody></table></div>` : ''}
      <div class="cf-side">
        <div><h4 class="section-title">Raw payload, verbatim</h4>${ui.codeBlock(ui.prettyJSON(m.payload), 'json')}</div>
        <div><h4 class="section-title">Normalized row${m.events.length === 1 ? '' : 's'} (${link.table('station_event')})</h4>
          ${ev ? ui.codeBlock(JSON.stringify(m.events.map((e) => ({ event_id: e.event_id, serial: e.serial, station_id: e.station_id, event_ts: e.event_ts, result: e.result, defect_code: e.defect_code, operator_id: e.operator_id, measurements: e.measurements, raw_id: e.raw_id })), null, 2), 'json')
            : ui.callout({ tone: r.ingest_status === 'QUARANTINED' ? 'critical' : 'neutral', title: 'No row written', body: r.ingest_note || '' })}
          ${ev ? html`<p class="small muted">Event time stored in UTC: ${fmt.dt(ev.event_ts, TPE)} Taipei = ${fmt.dt(ev.event_ts, PT)} PT</p>` : ''}
        </div>
      </div>
      ${m.edges.length ? html`<h4 class="section-title">Genealogy edges written</h4>
        <div class="table-wrap"><table class="table dense"><thead><tr><th>Child</th><th>Item</th><th>Relation</th><th>Change</th></tr></thead>
        <tbody>${m.edges.map((g) => html`<tr><td class="mono">${g.child_serial ? link.serial(g.child_serial) : link.lot(g.child_lot_id)}</td><td class="mono">${g.child_item_id}</td>
          <td>${fmt.title(g.relation)} · ${fmt.title(g.position || '')}</td>
          <td class="small">${g.removed_at && ev && g.removed_at === ev.event_ts ? `Removed: ${g.removal_reason}` : 'Installed'}</td></tr>`)}</tbody></table></div>` : ''}
      ${m.steps.length ? html`<h4 class="section-title">Pipeline trace (${link.table('ingest_step')})</h4>
        ${ui.timeline(m.steps.map((s) => ({ ts: s.at, tsLabel: fmt.dt(s.at, TPE) + ' Taipei', tone: s.status === 'OK' ? 'good' : s.status === 'WARN' ? 'warning' : 'critical', title: html`${fmt.title(s.step)} · ${ui.statusChip(s.status)}`, detail: s.detail })))}` : ''}
    `,
  });
}

const PAGE_CSS = `
.pg-cm-feed .cf-pipe { display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap; margin: -4px 0 18px; }
.pg-cm-feed .cf-node { flex: 1 1 150px; min-width: 0; display: grid; gap: 3px; padding: 10px 12px; background: var(--surface); border: 1px solid var(--hairline); border-radius: 10px; font-size: 13px; }
.pg-cm-feed .cf-node strong { font-weight: 600; overflow-wrap: anywhere; }
.pg-cm-feed .cf-k { font: 600 10.5px/1 var(--font-cond); letter-spacing: .12em; text-transform: uppercase; color: var(--sign); }
.pg-cm-feed .cf-arrow { display: flex; align-items: center; color: var(--ink-3); }
.pg-cm-feed .cf-status { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.pg-cm-feed .cf-status li { display: grid; gap: 5px; }
.pg-cm-feed .cf-status-g { color: var(--ink-2); min-width: 0; }
.pg-cm-feed .cf-picks { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--hairline); }
.pg-cm-feed .cf-picks .tiny { width: 100%; }
.pg-cm-feed .cf-outcome { display: inline-block; margin-top: 3px; font-weight: 600; color: var(--ink); }
.pg-cm-feed .cf-panel { margin-top: 14px; }
.pg-cm-feed .cf-qhead { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.pg-cm-feed .cf-filter .dt-search { flex: 1 1 280px; }
.pg-cm-feed .cf-filter .dt-search input { width: 100%; }
.pg-cm-feed .cf-zh { font-size: 14px; }
.pg-cm-feed .cf-pad { padding: 10px 18px 14px; }
.pg-cm-feed .cf-asn { padding: 10px 0; border-bottom: 1px solid var(--hairline); display: grid; gap: 3px; }
.pg-cm-feed .cf-asn:last-child { border-bottom: 0; }
.drawer .cf-side { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
.drawer .cf-side .code { max-height: 360px; overflow: auto; font-size: 11.5px; }
.drawer .cf-miss { color: var(--critical); font-weight: 600; }
.drawer tr.cf-missing td { background: color-mix(in srgb, var(--critical) 6%, transparent); }
@media (max-width: 700px) { .drawer .cf-side { grid-template-columns: minmax(0, 1fr); } }
`;
