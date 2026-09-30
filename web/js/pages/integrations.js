// Integration Hub: every inbound feed with live health, the email + Excel pipeline
// traced step by step (with the original workbook), a parser playground, and the
// integration options per source ("different options, with the evidence").
import { html, raw, esc, on, $, $$, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';
import { charts } from '../lib/charts.js';

const TABS = [
  { id: 'sources', label: 'Sources', icon: 'plug' },
  { id: 'email', label: 'Email pipeline', icon: 'ledger' },
  { id: 'try', label: 'Try the parsers', icon: 'play' },
  { id: 'options', label: 'Integration options', icon: 'scale' },
];
const MIX_TONE = {
  OK: 'good', REPLAYED: 'info', WARN: 'warning', DUPLICATE: 'neutral', IGNORED: 'neutral', QUARANTINED: 'critical',
  PENDING: 'info',
};
const MIX_ORDER = ['OK', 'REPLAYED', 'WARN', 'DUPLICATE', 'IGNORED', 'PENDING', 'QUARANTINED'];
const MIX_LABEL = { OK: 'OK', REPLAYED: 'Replayed', WARN: 'Warned', DUPLICATE: 'Duplicate', IGNORED: 'Ignored', PENDING: 'Pending', QUARANTINED: 'Quarantined' };
const CHANNEL_SHORT = { API: 'API', EDI: 'EDI', EMAIL_XLSX: 'Email + Excel', EMAIL_TEXT: 'Email text', PORTAL: 'Portal', WEBHOOK: 'Webhook', NATIVE: 'Native' };
const STEP_ORDER = ['RECEIVED', 'CLASSIFIED', 'EXTRACTED', 'PARSED', 'VALIDATED', 'MAPPED', 'LOADED', 'RECONCILED'];
const STEP_TONE = { OK: 'good', WARN: 'warning', FAILED: 'critical', SKIPPED: 'neutral' };
const OPTION_TONE = { CURRENT: 'info', RECOMMENDED: 'good', CONSIDERED: 'neutral', REJECTED: 'critical' };
const HL_LABEL = { po: 'PO number', line: 'Line', date: 'Date', qty: 'Quantity', basis: 'Date basis / intent', vague: 'Hedge (abstain)' };
const MAILBOX_LABEL = { 'cm-reports@': 'CM reports', 'po-confirm@': 'PO confirmations', 'supplier-reports@': 'Supplier reports' };
const PRESETS = [
  { label: 'English, ISO date', supplier: 'BAY', subject: 'RE: PO 4500157 line 1',
    body: 'Hello,\n\nWe confirm PO 4500157 line 1: 400 pcs, delivery 2026-11-02.\n\nBest regards,\nDana Kim\nBayline Seals' },
  { label: 'Spanish, dd/mm', supplier: 'SMT', subject: 'RE: OC 4500151',
    body: 'Buen día,\n\nConfirmamos la OC 4500151 partida 2: 400 piezas, entrega 05/10/2026.\n\nSaludos,\nAna Villarreal\nSummit Die Casting' },
  { label: 'Ship date, add transit', supplier: 'VPC', subject: 'PO 4500140 confirmation',
    body: 'Hi team,\n\nConfirming order 4500140, item 1. ETD 14-Oct-2026, 1,000 units.\n\nKevin Lin' },
  { label: '"Yes, confirmed" + quote', supplier: 'NRT', subject: 'RE: PO 4500155 line 2',
    body: 'Yes, confirmed. Thank you.\n\nLuis Ortega\n\n> From: OEM Purchasing <po@oem.example>\n> Please confirm PO 4500155 line 2: 600 pcs, need date 2026-10-21.\n' },
  { label: 'Vague: must abstain', supplier: 'SMT', subject: 'RE: PO 4500151',
    body: 'Hi team,\n\nRe PO 4500151: we will need to check line 3 with production and will revert with a firm date by Friday.\n\nAna' },
];
const SUPPLIERS = [
  { value: 'SMT', label: 'Summit Die Casting (MX · dd/mm · +4d truck)' },
  { value: 'NRT', label: 'Norte Harness (MX · dd/mm · +2d)' },
  { value: 'BAY', label: 'Bayline Seals (US · +1d)' },
  { value: 'VPC', label: 'Voltaic Power (TW · +18d ocean)' },
  { value: 'PNC', label: 'Pinecrest Electronics (TW · +4d air)' },
  { value: '', label: 'Unknown sender' },
];

let S = null;

// Status chip that keeps OK as OK
const st = (v) => ui.statusChip(v, v === 'OK' ? 'OK' : undefined);

export async function render(el, ctx) {
  injectStyle('page-integrations', PAGE_CSS);
  const [ov, mail, opts] = await Promise.all([
    api.get('/api/integrations/overview'), api.get('/api/integrations/emails'), api.get('/api/integrations/options'),
  ]);
  let tab = ctx.query.get('tab') || (ctx.query.get('email') ? 'email' : 'sources');
  if (!TABS.some((t) => t.id === tab)) tab = 'sources';
  S = { el, ctx, ov, mail, opts, tab, mailbox: ctx.query.get('mailbox') || '', status: '', email: ctx.query.get('email'), detailSeq: 0, channel: '' };
  const k = ov.kpis;

  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-integrations">
      <div class="kpi-row">
        ${ui.kpi({ label: 'Sources feeding the platform', value: fmt.int(k.sources),
          hint: `${k.external} run by partners · ${k.channels.NATIVE || 0} native OEM systems` })}
        ${ui.kpi({ label: 'Messages landed, last 7 days', value: fmt.int(k.landed_7d),
          hint: `${fmt.compact(k.raw_total)} raw payloads kept verbatim` })}
        ${ui.kpi({ label: 'Processed without a human', value: fmt.pct(k.automated_share, 2),
          hint: 'Everything except quarantine: OK, warned, replayed, de-duplicated' })}
        ${ui.kpi({ label: 'In quarantine now', value: fmt.int(k.quarantined), status: k.quarantined ? { tone: 'serious', label: 'Needs a fix' } : null,
          hint: 'Unknown station S65, an EDI ack code, a parser abstention' })}
        ${ui.kpi({ label: 'Manual touches per week', value: fmt.int(k.touches_after), delta: `−${fmt.int(k.touches_before - k.touches_after)}`,
          deltaGood: 'down', deltaLabel: `from ${fmt.int(k.touches_before)} when buyers re-keyed` })}
      </div>
      <div class="ih-tabs"></div>
      <div class="ih-body"></div>
    </div>`;

  S.offs = wire(el);
  ui.tabs($('.ih-tabs', el), {
    tabs: TABS.map((t) => ({ ...t, count: t.id === 'sources' ? k.sources : t.id === 'email' ? mail.emails.length : t.id === 'options' ? opts.sources.length : null })),
    active: tab,
    onChange: (id) => { S.tab = id; ctx.setQuery({ tab: id }, { silent: true }); drawTab(); },
  });
  drawTab();
}

// #page outlives this module: remove every delegated listener this page added.
export function unmount() {
  if (S && S.offs) S.offs.forEach((off) => off());
  S = null;
}

function drawTab() {
  const body = $('.ih-body', S.el);
  if (S.tab === 'sources') body.innerHTML = String(sourcesTab());
  else if (S.tab === 'email') { body.innerHTML = String(emailTab()); drawInbox(); selectEmail(S.email || defaultEmail()); }
  else if (S.tab === 'try') body.innerHTML = String(tryTab());
  else body.innerHTML = String(optionsTab());
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------
function mixBar(status, total) {
  if (!total) return html`<div class="ih-mix empty"></div>`;
  const parts = MIX_ORDER.filter((s) => status[s]).map((s) => ({ s, n: status[s] }));
  return html`<div class="ih-mix" role="img" aria-label="${parts.map((p) => `${MIX_LABEL[p.s] || p.s} ${p.n}`).join(', ')}">${parts.map((p) =>
    html`<span class="seg tone-${MIX_TONE[p.s] || 'neutral'}" style="flex:${Math.max(p.n / total, 0.012)}" data-tip="${MIX_LABEL[p.s] || p.s}\n${fmt.int(p.n)} of ${fmt.int(total)} (${fmt.pct(p.n / total, 2)})"></span>`)}</div>`;
}

function mixLegend() {
  return html`<div class="legend ih-mix-legend">${MIX_ORDER.filter((s) => s !== 'PENDING').map((s) => html`<span class="legend-item"><span class="legend-key rect" style="--c:var(--${MIX_TONE[s] === 'neutral' ? 'ink-3' : MIX_TONE[s]})"></span>${MIX_LABEL[s]}</span>`)}</div>`;
}

function landingLinks(landing) {
  if (!landing) return html`<span class="nil">—</span>`;
  const names = landing.split(/\s*[/·]\s*/).filter(Boolean);
  return names.map((n, i) => html`${i ? ', ' : ''}${/^[a-z_]+$/.test(n) ? link.table(n) : n}`);
}

function sourceCard(s) {
  const h = s.health;
  const raw = h.kind === 'raw';
  const pages = (s.pages || []).map((p) => html`<a class="ih-page" href="#/${p}">${fmt.title(p.replace('-', '_'))}</a>`);
  return html`<article class="ih-src">
    <header class="ih-src-head">
      <div class="ih-src-title">
        <h4>${s.name}</h4>
        <p>${s.operator} · ${s.country} · owned by ${fmt.title(s.owner)}</p>
      </div>
      ${charts.sparkline(s.spark, { width: 76, height: 24 })}
    </header>
    <div class="ih-src-meta">
      <span class="tag">${fmt.title(s.channel)}</span><span class="tag">${s.direction === 'BOTH' ? 'In + out' : fmt.title(s.direction)}</span>
      <span class="muted small">${s.frequency}</span>
    </div>
    <dl class="ih-src-stats">
      <div><dt>${raw ? 'Payloads' : 'Records'}</dt><dd>${fmt.int(h.total)}</dd></div>
      <div><dt>Last 7 days</dt><dd>${fmt.int(h.last_7d)}</dd></div>
      <div><dt>Last seen</dt><dd>${h.last_at ? fmt.rel(h.last_at) : '—'}</dd></div>
    </dl>
    ${raw ? mixBar(h.status, h.total) : html`<p class="ih-native">${icon('database', 13)} Writes the core directly; no landing copy needed.</p>`}
    <div class="ih-src-foot">
      <span class="small muted">Lands in</span> <span class="small">${landingLinks(s.landing)}</span>
      ${s.recommended && s.recommended.length ? html`<div class="ih-next">${icon('arrow-right', 13)}<span>Next: ${s.recommended.join(' · ')}</span></div>` : ''}
      ${pages.length ? html`<div class="ih-pages">${pages}</div>` : ''}
    </div>
  </article>`;
}

function sourceGrid() {
  const list = S.ov.sources.filter((s) => !S.channel || s.channel === S.channel);
  return html`${list.map(sourceCard)}`;
}

function sourcesTab() {
  const ov = S.ov;
  const chans = ov.channel_order.filter((c) => ov.sources.some((s) => s.channel === c));
  const q = ov.quarantine || [];
  return html`
    ${ui.callout({ tone: 'info', title: 'Every feed lands raw first, then a normalizer writes the core',
      body: html`Partners without APIs still count: the CM's bilingual daily Excel and supplier confirmations by email are parsed with the same rigor as the MES stream. Each message ends <strong>OK</strong>, <strong>warned</strong>, <strong>replayed</strong>, <strong>de-duplicated</strong> or <strong>quarantined</strong>, and none is silently dropped. The bar under each source is that mix.` })}
    <div class="ih-src-bar">
      <div class="seg" role="group" aria-label="Channel">
        <button type="button" data-ch="" class="${!S.channel ? 'active' : ''}">All <span class="muted">${ov.sources.length}</span></button>
        ${chans.map((c) => html`<button type="button" data-ch="${c}" class="${S.channel === c ? 'active' : ''}" title="${ov.channel_label[c] || c}">${CHANNEL_SHORT[c] || c} <span class="muted">${ov.sources.filter((x) => x.channel === c).length}</span></button>`)}
      </div>
      ${mixLegend()}
    </div>
    <div class="ih-src-grid">${sourceGrid()}</div>
    <div class="grid">
      <div class="span-7">${ui.card({ title: 'Quarantine', subtitle: 'Held back from the core with a reason; a fix plus a replay releases them.', flush: true,
        body: q.length ? html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Landing table</th><th class="num">Raw id</th><th>Received</th><th>Reason</th></tr></thead>
          <tbody>${q.map((r) => html`<tr><td>${link.table(r.t)}</td><td class="num mono">${r.raw_id}</td><td class="nowrap">${fmt.dt(r.received_at)}</td><td class="wrap">${r.ingest_note}</td></tr>`)}</tbody></table></div>`
          : ui.empty('Nothing in quarantine.') })}</div>
      <div class="span-5">${ui.card({ title: 'Ingest runs', subtitle: 'One row per normalizer run (ingest_run).', flush: true,
        body: html`<div class="table-wrap"><table class="table dense"><thead><tr><th>Source</th><th class="num">In</th><th class="num">OK</th><th class="num">Warn</th><th class="num">Quar.</th><th class="num">Dup.</th></tr></thead>
          <tbody>${S.ov.runs.map((r) => html`<tr title="${r.notes || ''}"><td class="mono">${r.source}</td><td class="num">${fmt.int(r.rows_in)}</td><td class="num">${fmt.int(r.rows_ok)}</td><td class="num">${fmt.int(r.rows_warn)}</td><td class="num">${fmt.int(r.rows_quarantined)}</td><td class="num">${fmt.int(r.rows_duplicate)}</td></tr>`)}</tbody></table></div>` })}</div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Email pipeline
// ---------------------------------------------------------------------------
function defaultEmail() {
  const withAtt = S.mail.emails.find((e) => e.classified_as === 'CM_DAILY_REPORT' && e.attachments && e.ingest_status !== 'IGNORED');
  return String((withAtt || S.mail.emails[0] || {}).raw_id || '');
}

function emailTab() {
  const mbs = S.mail.mailboxes;
  return html`<div class="ih-mail">
    <section class="card flush ih-inbox">
      <header class="card-head"><div class="card-head-text"><h3 class="card-title">Inboxes</h3><p class="card-sub">${fmt.int(S.mail.emails.length)} messages kept as full RFC 822 (raw_email)</p></div></header>
      <div class="ih-inbox-filters">
        <div class="seg" role="group" aria-label="Mailbox">
          <button type="button" data-mb="" class="${!S.mailbox ? 'active' : ''}">All</button>
          ${mbs.map((m) => html`<button type="button" data-mb="${m}" class="${S.mailbox === m ? 'active' : ''}">${MAILBOX_LABEL[m] || m}</button>`)}
        </div>
        <select class="select sm" data-status aria-label="Status">
          <option value="">Any status</option>
          ${['OK', 'WARN', 'QUARANTINED', 'IGNORED'].map((s) => html`<option value="${s}"${S.status === s ? raw(' selected') : ''}>${fmt.title(s)}</option>`)}
        </select>
      </div>
      <ul class="ih-inbox-list" role="listbox" aria-label="Messages"></ul>
    </section>
    <div class="ih-detail"></div>
  </div>`;
}

function drawInbox() {
  const ul = $('.ih-inbox-list', S.el);
  if (!ul) return;
  const list = S.mail.emails.filter((e) => (!S.mailbox || e.mailbox === S.mailbox) && (!S.status || e.ingest_status === S.status));
  ul.innerHTML = list.length ? String(html`${list.map((e) => html`<li><button type="button" class="ih-msg${String(e.raw_id) === String(S.email) ? ' active' : ''}" data-email="${e.raw_id}" role="option" aria-selected="${String(e.raw_id) === String(S.email)}">
      <span class="ih-msg-top"><span class="ih-msg-from">${e.from_addr.split('@')[0]}<span class="muted">@${e.from_addr.split('@')[1]}</span></span><span class="ih-msg-time">${fmt.dt(e.received_at)}</span></span>
      <span class="ih-msg-subj">${e.attachments ? html`<span class="ih-clip" title="${e.attachments} attachment(s)">${icon('link', 12)}</span>` : ''}${e.subject}</span>
      <span class="ih-msg-foot">${st(e.ingest_status)}${e.classified_as ? html`<span class="tag">${fmt.title(e.classified_as)}</span>` : html`<span class="tag muted">unclassified</span>`}</span>
    </button></li>`)}`) : String(ui.empty('No messages match.'));
}

async function selectEmail(id) {
  if (!id) return;
  S.email = String(id);
  S.ctx.setQuery({ email: id, tab: 'email' }, { silent: true });
  $$('.ih-msg', S.el).forEach((b) => {
    const on = b.dataset.email === String(id);
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
    if (on) {
      const list = b.closest('.ih-inbox-list');
      if (list) list.scrollTop = Math.max(0, b.offsetTop - list.offsetTop - 40);
    }
  });
  const box = $('.ih-detail', S.el);
  if (!box) return;
  const seq = ++S.detailSeq;
  box.classList.add('is-refreshing');
  let d;
  try {
    d = await api.get(`/api/integrations/email/${id}`);
  } catch (err) {
    box.innerHTML = String(ui.errorBox(err));
    return;
  }
  if (!S || seq !== S.detailSeq) return;
  box.classList.remove('is-refreshing');
  box.innerHTML = String(emailDetail(d));
}

function stepper(steps, email) {
  const recorded = [...steps].sort((a, b) => (a.seq - b.seq));
  const done = new Set(recorded.map((s) => s.step));
  const terminal = email.ingest_status;
  const todo = terminal === 'IGNORED' || terminal === 'QUARANTINED' ? [] : STEP_ORDER.filter((s) => !done.has(s) && STEP_ORDER.indexOf(s) < STEP_ORDER.indexOf('RECONCILED'));
  return html`<ol class="ih-steps">
    ${recorded.map((s) => html`<li class="ih-step tone-${STEP_TONE[s.status] || 'neutral'}">
      <span class="ih-step-dot">${icon(s.status === 'OK' ? 'check' : s.status === 'FAILED' ? 'x' : s.status === 'SKIPPED' ? 'minus' : 'alert-triangle', 12)}</span>
      <div class="ih-step-body">
        <div class="ih-step-head"><span class="ih-step-name">${fmt.title(s.step)}</span>${st(s.status)}${s.duration_ms != null ? html`<span class="muted tiny">${fmt.ms(s.duration_ms)}</span>` : ''}</div>
        <div class="ih-step-detail">${s.detail || html`<span class="muted">—</span>`}</div>
      </div>
    </li>`)}
    ${todo.length && terminal !== 'OK' && terminal !== 'WARN' ? todo.map((t) => html`<li class="ih-step pending"><span class="ih-step-dot"></span><div class="ih-step-body"><span class="ih-step-name muted">${fmt.title(t)}</span></div></li>`) : ''}
    <li class="ih-step outcome tone-${ui.toneOf(terminal)}">
      <span class="ih-step-dot">${icon('target', 12)}</span>
      <div class="ih-step-body"><div class="ih-step-head"><span class="ih-step-name">Outcome</span>${st(terminal)}</div>
      <div class="ih-step-detail">${email.ingest_note || html`<span class="muted">—</span>`}</div></div>
    </li>
  </ol>`;
}

function cellText(v) {
  if (v == null || v === '') return '';
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString('en-US') : String(Math.round(v * 10000) / 10000);
  if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE';
  return String(v);
}

function sheetGrid(sh) {
  if (sh.error) return ui.callout({ tone: 'critical', title: 'Workbook could not be read', body: sh.error });
  const hdr = sh.header_row;
  const mapped = sh.mapped || {};
  const unmapped = new Set(sh.unmapped_cols || []);
  const titles = new Set(sh.title_rows || []);
  return html`<div class="ih-grid-wrap"><table class="ih-grid">
    <thead><tr><th class="rn"></th>${sh.letters.map((L, j) => html`<th class="${mapped[j] ? 'mapped' : unmapped.has(j) ? 'unmapped' : ''}">${L}</th>`)}</tr></thead>
    <tbody>${sh.rows.map((r, i) => {
      const cls = i === hdr ? 'hdr' : i === sh.totals_row ? 'totals' : titles.has(i) ? 'title' : (hdr != null && i > hdr && (sh.totals_row == null || i < sh.totals_row)) ? 'data' : '';
      const rest = r.slice(1).every((v) => v == null || v === '');
      if (titles.has(i) && rest) {
        return html`<tr class="${cls}"><th class="rn">${i + 1}</th><td colspan="${r.length || 1}" class="span-all">${cellText(r[0])}</td></tr>`;
      }
      return html`<tr class="${cls}"><th class="rn">${i + 1}</th>${r.map((v, j) => {
        const cc = [typeof v === 'number' ? 'n' : '', mapped[j] ? 'mcol' : '', unmapped.has(j) ? 'ucol' : ''].filter(Boolean).join(' ');
        return html`<td class="${cc}">${cellText(v)}${i === hdr && mapped[j] ? html`<span class="ih-field">→ ${mapped[j]}</span>` : ''}${i === hdr && unmapped.has(j) ? html`<span class="ih-field warn">ignored</span>` : ''}</td>`;
      })}</tr>`;
    })}</tbody></table></div>
    ${sh.truncated ? html`<p class="tiny muted">Showing the first ${sh.rows.length} of ${sh.n_rows} rows.</p>` : ''}`;
}

function workbook(sheets, key) {
  if (!sheets || !sheets.length) return '';
  return html`<div class="ih-wb" data-wb="${key}">
    <div class="ih-wb-bar">
      <div class="seg" role="tablist" aria-label="Sheets">${sheets.map((s, i) => html`<button type="button" data-sheet="${i}" class="${i === 0 ? 'active' : ''}">${s.name}</button>`)}</div>
      <div class="legend ih-wb-legend">
        <span class="legend-item"><span class="ih-sw hdr"></span>Header row found</span>
        <span class="legend-item"><span class="ih-sw mcol"></span>Mapped column</span>
        <span class="legend-item"><span class="ih-sw ucol"></span>Ignored column</span>
        <span class="legend-item"><span class="ih-sw title"></span>Title rows</span>
        <span class="legend-item"><span class="ih-sw totals"></span>Totals row</span>
      </div>
    </div>
    ${sheets.map((s, i) => html`<div class="ih-sheet" data-sheet-panel="${i}"${i ? raw(' hidden') : ''}>
      ${s.feed ? html`<p class="small ih-sheet-note">${icon('check', 13)} Matched as <strong>${s.feed_label || s.feed}</strong>: header on row ${s.header_row + 1}, ${Object.keys(s.mapped).length} columns mapped${s.unmapped_cols && s.unmapped_cols.length ? html`, <span class="warn-text">${s.unmapped_cols.length} ignored</span>` : ''}${s.totals_row != null ? html`, totals row ${s.totals_row + 1} checked against the sum` : ''}.</p>`
        : html`<p class="small muted">No known header pattern on this sheet.</p>`}
      ${sheetGrid(s)}
    </div>`)}
  </div>`;
}

function loadedTables(loaded) {
  const entries = Object.entries(loaded || {});
  if (!entries.length) return '';
  return entries.map(([t, rows]) => {
    if (!rows.length) return html`<p class="small muted">${link.table(t)}: no rows from this attachment (nothing changed, or superseded).</p>`;
    const cols = Object.keys(rows[0]).filter((c) => !['id', 'source_ref', 'raw_ref', 'balance_id'].includes(c));
    return html`<div class="ih-loaded"><p class="small"><strong>${fmt.int(rows.length)}</strong> row${rows.length === 1 ? '' : 's'} in ${link.table(t)}</p>
      <div class="table-wrap"><table class="table dense"><thead><tr>${cols.map((c) => html`<th>${c}</th>`)}</tr></thead>
      <tbody>${rows.slice(0, 20).map((r) => html`<tr>${cols.map((c) => html`<td class="${typeof r[c] === 'number' ? 'num' : ''}${c === 'po_id' || c === 'item_id' ? ' mono' : ''}">${c === 'po_id' ? link.po(r.po_id, r.line_no) : r[c] == null ? html`<span class="nil">—</span>` : typeof r[c] === 'number' ? (Number.isInteger(r[c]) ? fmt.int(r[c]) : fmt.num(r[c], 2)) : String(r[c])}</td>`)}</tr>`)}</tbody></table></div></div>`;
  });
}

function highlighted(segments) {
  return html`<pre class="ih-body-text">${segments.map((s) => (s.type ? html`<mark class="hl hl-${s.type}" title="${HL_LABEL[s.type] || s.type}">${s.text}</mark>` : s.text))}</pre>
  <div class="legend ih-hl-legend">${Object.entries(HL_LABEL).map(([k, v]) => html`<span class="legend-item"><mark class="hl hl-${k}">${v}</mark></span>`)}</div>`;
}

function parseResult(res, extra) {
  if (!res) return '';
  if (res.abstain) {
    return ui.callout({ tone: 'warning', title: 'Parser abstained. No date was written.',
      body: html`Reason: <strong>${res.reason}</strong>. The message goes to the buyer queue instead. A wrong promise date poisons MRP; an abstention costs one human touch.` });
  }
  return html`<dl class="kv ih-kv">
    <dt>PO line</dt><dd>${link.po(res.po, res.line)}</dd>
    <dt>Promise date</dt><dd><strong>${fmt.dateLong(res.promise)}</strong></dd>
    <dt>Quantity</dt><dd>${res.qty != null ? fmt.int(res.qty) : html`<span class="muted">not stated (line quantity kept)</span>`}</dd>
    <dt>Basis</dt><dd>${res.basis}</dd>
    ${extra || ''}
  </dl>`;
}

function emailDetail(d) {
  const e = d.email;
  const cm = /formosa-ap/.test(e.from_addr);
  return html`
    <section class="card ih-hdr">
      <div class="ih-hdr-top">
        <div class="ih-hdr-main"><h3 class="ih-subject">${e.subject}</h3>
          <p class="small"><span class="muted">From</span> <span class="mono">${e.from_addr}</span> <span class="muted">to</span> <span class="mono">${e.to_addr}</span></p>
          <p class="small muted">Received ${fmt.dt(e.received_at)} PT${d.local_time ? html` · <span class="ih-tpe">${d.local_time}</span>` : ''} · <span class="mono">${e.message_id}</span></p></div>
        <div class="ih-hdr-chips">${e.classified_as ? html`<span class="tag">${fmt.title(e.classified_as)}</span>` : ''}${st(e.ingest_status)}</div>
      </div>
      ${cm ? html`<p class="ih-cm-note small">${icon('info', 13)} The CM is in Taichung (UTC+8). Its report arrives at 18:40 Taipei, which is 03:40 in Palo Alto, and the parser reads the report date from the subject when the cell disagrees.</p>` : ''}
    </section>
    ${ui.card({ title: 'Pipeline trace', subtitle: 'The ingest_step rows for this message, in order: what each stage did, found and decided', body: stepper(d.steps, e) })}
        ${d.attachments.map((a) => ui.card({
          cls: 'ih-att',
          title: a.filename,
          subtitle: html`${fmt.int(a.size_bytes)} bytes · SHA-256 <span class="mono" title="${a.sha256}">${a.sha256.slice(0, 12)}…</span> · parser ${a.parser || '—'}`,
          actions: html`${st(a.parse_status)}<a class="btn sm" href="/api/integrations/attachment/${a.attachment_id}/download" download="${a.filename}">${icon('download', 14)}<span>Original .xlsx</span></a>`,
          body: html`
            ${a.dup_of ? ui.callout({ tone: 'info', title: 'Duplicate resend, ignored by content hash',
              body: html`Byte-identical to the attachment on ${html`<a href="#/integrations?tab=email&email=${a.dup_of.raw_id}" data-goto-email="${a.dup_of.raw_id}">email #${a.dup_of.raw_id}</a>`} (${fmt.dt(a.dup_of.received_at)}). Same SHA-256 means the same file, so it was skipped instead of being double-loaded.` }) : ''}
            ${a.parse_note ? ui.callout({ tone: a.parse_status === 'WARN' ? 'warning' : 'info', title: 'What the validator noticed', body: a.parse_note }) : ''}
            ${a.superseded_by ? html`<p class="small muted">Its supplier-stock rows were replaced by the report dated ${fmt.dateLong(a.superseded_by)}; only the latest week is the current position.</p>` : ''}
            ${a.parse_status === 'SKIPPED' ? '' : workbook(a.sheets, a.attachment_id)}
            ${a.parse_status === 'SKIPPED' ? '' : html`<h4 class="section-title">Rows it loaded</h4>${loadedTables(a.loaded)}`}`,
        }))}
        ${d.parse ? ui.card({ title: 'Free-text confirmation', subtitle: `Parsed as ${d.parse.supplier || 'unknown supplier'}; highlights show what the parser keyed on`,
          body: html`${highlighted(d.parse.segments)}${parseResult(d.parse.result)}
            ${d.loaded && d.loaded.length ? html`<h4 class="section-title">Written to po_promise_history</h4>
              <div class="table-wrap"><table class="table dense"><thead><tr><th>PO line</th><th>Item</th><th>Need</th><th>Promise</th><th>Channel</th><th>Note</th></tr></thead>
              <tbody>${d.loaded.map((r) => html`<tr><td>${link.po(r.po_id, r.line_no)}</td><td class="mono">${r.item_id}</td><td>${fmt.date(r.need_date)}</td><td><strong>${fmt.date(r.promise_date)}</strong></td><td>${fmt.title(r.channel)}</td><td class="wrap">${r.note || ''}</td></tr>`)}</tbody></table></div>` : ''}` }) : ''}
        ${!d.attachments.length && !d.parse ? ui.card({ title: 'Message body', body: html`<pre class="ih-body-text">${e.body_text || ''}</pre>` }) : ''}`;
}

// ---------------------------------------------------------------------------
// Try the parsers
// ---------------------------------------------------------------------------
function tryTab() {
  const samples = S.mail.samples || [];
  return html`<div class="grid">
    <div class="span-6">${ui.card({ title: 'Parse a supplier email', subtitle: 'The same parse_confirmation() the pipeline runs. Preview only; nothing is written.',
      body: html`<div class="ih-try">
        <div class="ih-presets">${PRESETS.map((p, i) => html`<button type="button" class="btn sm" data-preset="${i}">${p.label}</button>`)}</div>
        <label class="ih-field-l"><span>Sender</span><select class="select" data-try-supplier>${SUPPLIERS.map((s) => html`<option value="${s.value}">${s.label}</option>`)}</select></label>
        <label class="ih-field-l"><span>Subject</span><input class="input" data-try-subject type="text" placeholder="RE: PO 4500151 line 2"></label>
        <label class="ih-field-l"><span>Body</span><textarea class="textarea mono" data-try-body rows="9" placeholder="Paste a supplier's confirmation email…"></textarea></label>
        <div class="row"><button type="button" class="btn btn-primary" data-parse>${icon('play', 15)}<span>Parse</span></button><span class="small muted">Dates are read by sender locale: Mexican suppliers write dd/mm.</span></div>
        <div class="ih-try-out" aria-live="polite"></div>
      </div>` })}</div>
    <div class="span-6">${ui.card({ title: 'Drop a workbook', subtitle: 'Header detection with bilingual synonyms, merged title rows and totals. Preview only.',
      body: html`<div class="ih-try">
        <label class="ih-drop"><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" data-xlsx hidden>
          ${icon('download', 20)}<span><strong>Choose an .xlsx</strong> or drop it here</span><span class="small muted">Read in your browser, sent as base64, parsed with the standard library</span></label>
        ${samples.length ? html`<div class="ih-presets"><span class="small muted">Or try a real one:</span>${samples.map((s) => html`<button type="button" class="btn sm" data-sample="${s.attachment_id}">${s.filename}</button>`)}</div>` : ''}
        <div class="ih-xlsx-out" aria-live="polite"></div>
      </div>` })}</div>
  </div>`;
}

async function runParse() {
  const out = $('.ih-try-out', S.el);
  const body = $('[data-try-body]', S.el).value;
  const subject = $('[data-try-subject]', S.el).value;
  const supplier = $('[data-try-supplier]', S.el).value;
  out.innerHTML = String(ui.loading('Parsing'));
  try {
    const r = await api.post('/api/integrations/parse-text', { body, subject, supplier });
    const extra = r.po_line ? html`<dt>In the ERP</dt><dd>${link.po(r.po_line.po_id, r.po_line.line_no)} · ${r.po_line.item_id} · need ${fmt.date(r.po_line.need_date)} · current promise ${r.po_line.promise_date ? fmt.date(r.po_line.promise_date) : '—'}</dd>`
      : (!r.result.abstain ? html`<dt>In the ERP</dt><dd class="warn-text">No such PO line. The loader would quarantine it.</dd>` : '');
    out.innerHTML = String(html`<h4 class="section-title">What it keyed on</h4>${highlighted(r.segments)}<h4 class="section-title">Result</h4>${parseResult(r.result, extra)}<p class="tiny muted">${r.note}</p>`);
  } catch (err) {
    out.innerHTML = String(ui.errorBox(err));
  }
}

async function previewXlsx(payload) {
  const out = $('.ih-xlsx-out', S.el);
  out.innerHTML = String(ui.loading('Reading workbook'));
  try {
    const r = await api.post('/api/integrations/parse-xlsx', payload);
    out.innerHTML = String(html`<p class="small"><strong>${r.filename}</strong> · ${fmt.int(r.bytes)} bytes · ${r.sheets.length} sheet${r.sheets.length === 1 ? '' : 's'}</p>
      ${r.guess ? ui.callout({ tone: 'good', title: `Looks like: ${r.guess_label}`, body: 'Classification in the pipeline starts from the sender and subject; this is the header match on its own.' })
        : ui.callout({ tone: 'warning', title: 'No known feed matched', body: 'A new layout gets a mapping (reviewed like code) before it can load.' })}
      ${workbook(r.sheets, 'try')}<p class="tiny muted">${r.note}</p>`);
  } catch (err) {
    out.innerHTML = String(ui.errorBox(err));
  }
}

function readFile(file) {
  if (!file) return;
  if (!/\.xlsx$/i.test(file.name)) { ui.toast('Choose an .xlsx workbook', 'warning'); return; }
  if (file.size > 5 * 1024 * 1024) { ui.toast('That file is over 5 MB', 'warning'); return; }
  const fr = new FileReader();
  fr.onload = () => previewXlsx({ filename: file.name, content_b64: String(fr.result) });
  fr.onerror = () => ui.toast('Could not read the file', 'critical');
  fr.readAsDataURL(file);
}

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------
function latencyLabel(min) {
  if (min < 60) return `${fmt.int(min)} min`;
  if (min < 1440) return `${fmt.num(min / 60, min < 600 ? 1 : 0)} h`;
  return `${fmt.num(min / 1440, 1)} days`;
}

function tradeoff(src) {
  const opts = src.options;
  const lat = opts.map((o) => Math.max(1, o.latency_minutes));
  const lo = Math.log10(1);
  const hi = Math.log10(Math.max(10080, ...lat) * 1.2);
  const tMax = Math.max(4, ...opts.map((o) => o.touches_per_week));
  const ticksT = charts.niceTicks(0, tMax, 3);
  const tTop = ticksT[ticksT.length - 1];
  const xt = [[1, '1 min'], [60, '1 h'], [1440, '1 day'], [10080, '1 wk']];
  return charts.custom({
    height: 200,
    ariaLabel: `Latency versus manual touches for ${src.system.name}`,
    table: { columns: ['Option', 'Status', 'Latency', 'Touches / week', 'Error %', 'Build (wks)', 'Run $/mo'],
      rows: opts.map((o, i) => [`${i + 1}. ${o.option}`, fmt.title(o.status), latencyLabel(o.latency_minutes), fmt.num(o.touches_per_week, 0), fmt.num(o.error_rate_pct, 1), fmt.num(o.build_weeks, 0), fmt.usd(o.run_usd_month)]),
      numeric: [false, false, true, true, true, true] },
    render: (w, h) => {
      const m = { l: 36, r: 18, t: 22, b: 28 };
      const iw = Math.max(60, w - m.l - m.r);
      const ih = h - m.t - m.b;
      const sx = (v) => m.l + ((Math.log10(Math.max(1, v)) - lo) / (hi - lo)) * iw;
      const sy = (v) => m.t + ih - (v / tTop) * ih;
      let s = '';
      for (const t of ticksT) {
        const y = Math.round(sy(t)) + 0.5;
        s += `<line class="gridline" x1="${m.l}" x2="${m.l + iw}" y1="${y}" y2="${y}"/><text class="tick" x="${m.l - 8}" y="${y}" dy="0.32em" text-anchor="end">${t}</text>`;
      }
      for (const [v, label] of xt) {
        const x = sx(v);
        s += `<text class="tick" x="${x.toFixed(1)}" y="${m.t + ih + 17}" text-anchor="middle">${label}</text>`;
      }
      s += `<line class="baseline" x1="${m.l}" x2="${m.l + iw}" y1="${m.t + ih + 0.5}" y2="${m.t + ih + 0.5}"/>`;
      s += `<text class="tick" x="${m.l - 8}" y="${m.t - 12}" text-anchor="start">Manual touches / week</text>`;
      // numbered markers (the number matches the option card); nudge markers that would sit on top of each other
      const placed = [];
      opts.forEach((o, i) => {
        let x = sx(o.latency_minutes);
        const y = sy(o.touches_per_week);
        while (placed.some((p) => Math.abs(p.x - x) < 19 && Math.abs(p.y - y) < 19)) x += 19;
        placed.push({ x, y });
        const tone = OPTION_TONE[o.status] || 'neutral';
        const col = charts.STATUS_COLOR[tone];
        s += `<g data-tip="${i + 1}. ${esc(o.option)}\n${fmt.title(o.status)} · ${latencyLabel(o.latency_minutes)} · ${o.touches_per_week} touches/wk · ${o.error_rate_pct}% errors">`;
        s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="14" fill="transparent"/>`;
        s += `<circle class="marker" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="9" style="fill:${col}"/>`;
        s += `<text class="ih-dot-n" x="${x.toFixed(1)}" y="${y.toFixed(1)}" dy="0.35em" text-anchor="middle">${i + 1}</text></g>`;
      });
      return raw(s);
    },
  });
}

function optionCard(o, i) {
  const tone = OPTION_TONE[o.status] || 'neutral';
  return html`<article class="ih-opt tone-${tone}${o.status === 'RECOMMENDED' ? ' rec' : ''}">
    <header><span class="ih-opt-n" style="--c:${charts.STATUS_COLOR[tone]}">${i + 1}</span>${ui.chip(tone, fmt.title(o.status))}<span class="tag">${fmt.title(o.channel)}</span></header>
    <h4>${o.option}</h4>
    <dl class="ih-opt-stats">
      <div><dt>Latency</dt><dd>${latencyLabel(o.latency_minutes)}</dd></div>
      <div><dt>Touches / wk</dt><dd>${fmt.num(o.touches_per_week, 0)}</dd></div>
      <div><dt>Errors</dt><dd>${fmt.num(o.error_rate_pct, 1)}%</dd></div>
      <div><dt>Build</dt><dd>${o.build_weeks ? `${fmt.num(o.build_weeks, 0)} wk` : '—'}</dd></div>
      <div><dt>Run</dt><dd>${o.run_usd_month ? `${fmt.usd(o.run_usd_month)}/mo` : '$0'}</dd></div>
    </dl>
    ${o.depends_on ? html`<p class="small"><span class="muted">Depends on</span> ${o.depends_on}</p>` : ''}
    ${o.pros ? html`<p class="ih-pro small">${icon('plus', 13)}<span>${o.pros}</span></p>` : ''}
    ${o.cons ? html`<p class="ih-con small">${icon('minus', 13)}<span>${o.cons}</span></p>` : ''}
  </article>`;
}

function optionsTab() {
  return html`
    ${ui.callout({ tone: 'info', title: 'Every feed has more than one way in',
      body: html`Start with what the partner already does (email, Excel), automate the parsing, measure it, then move each feed to the cheapest structured channel the partner will actually adopt. The trade-off plot puts <strong>latency</strong> (log scale) against <strong>manual touches per week</strong>; the good corner is bottom-left.` })}
    ${S.opts.sources.map((src) => ui.card({
      title: src.system.name,
      subtitle: html`${src.system.operator} · ${fmt.title(src.system.channel)} today · current: <strong>${src.current || '—'}</strong>${src.recommended.length ? html` · next: <strong>${src.recommended.join(' · ')}</strong>` : ''}`,
      tableToggle: true,
      cls: 'ih-opts-card',
      body: html`<div class="ih-opts-layout"><div class="ih-opts-plot">${tradeoff(src)}</div>
        <div class="ih-opts-grid">${src.options.map((o, i) => optionCard(o, i))}</div></div>`,
    }))}`;
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------
function wire(el) {
  return [
    on(el, 'click', '[data-email]', (e, b) => { e.preventDefault(); selectEmail(b.dataset.email); }),
    on(el, 'click', '[data-ch]', (e, b) => {
      S.channel = b.dataset.ch;
      $$('[data-ch]', el).forEach((x) => x.classList.toggle('active', x === b));
      const grid = $('.ih-src-grid', el);
      if (grid) grid.innerHTML = String(sourceGrid());
    }),
    on(el, 'click', '[data-goto-email]', (e, a) => { e.preventDefault(); selectEmail(a.dataset.gotoEmail); }),
    on(el, 'click', '[data-mb]', (e, b) => {
      S.mailbox = b.dataset.mb;
      $$('[data-mb]', el).forEach((x) => x.classList.toggle('active', x === b));
      drawInbox();
    }),
    on(el, 'change', '[data-status]', (e, s) => { S.status = s.value; drawInbox(); }),
    on(el, 'click', '[data-sheet]', (e, b) => {
      const wb = b.closest('.ih-wb');
      $$('[data-sheet]', wb).forEach((x) => x.classList.toggle('active', x === b));
      $$('[data-sheet-panel]', wb).forEach((p) => { p.hidden = p.dataset.sheetPanel !== b.dataset.sheet; });
    }),
    on(el, 'click', '[data-preset]', (e, b) => {
      const p = PRESETS[+b.dataset.preset];
      $('[data-try-body]', el).value = p.body;
      $('[data-try-subject]', el).value = p.subject;
      $('[data-try-supplier]', el).value = p.supplier;
      runParse();
    }),
    on(el, 'click', '[data-parse]', () => runParse()),
    on(el, 'keydown', '[data-try-body]', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); runParse(); } }),
    on(el, 'click', '[data-sample]', (e, b) => previewXlsx({ attachment_id: +b.dataset.sample })),
    on(el, 'change', '[data-xlsx]', (e, inp) => readFile(inp.files && inp.files[0])),
    on(el, 'dragover', '.ih-drop', (e, d) => { e.preventDefault(); d.classList.add('over'); }),
    on(el, 'dragleave', '.ih-drop', (e, d) => d.classList.remove('over')),
    on(el, 'drop', '.ih-drop', (e, d) => {
      e.preventDefault();
      d.classList.remove('over');
      readFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
    }),
  ];
}

const PAGE_CSS = `
.pg-integrations .ih-tabs { margin-top: 18px; }
.pg-integrations .ih-body { margin-top: 16px; }
.pg-integrations .ih-body > * + * { margin-top: 16px; }
.pg-integrations .tag { display: inline-flex; align-items: center; height: 20px; padding: 0 7px; border-radius: 6px; font: 600 11px/1 var(--font-cond);
  letter-spacing: .04em; text-transform: uppercase; color: var(--ink-2); background: var(--surface-2); border: 1px solid var(--hairline); white-space: nowrap; }
.pg-integrations .tag.muted { color: var(--ink-3); }
.pg-integrations .warn-text { color: var(--delta-bad); }
.pg-integrations .ih-mix-legend { margin: 0; }
.pg-integrations .ih-src-bar { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center; justify-content: space-between; }
.pg-integrations .ih-src-bar .seg { max-width: 100%; overflow-x: auto; }
.pg-integrations .ih-src-bar .seg .muted { margin-left: 3px; font-size: 11.5px; }
.pg-integrations .ih-src-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 12px; }
.pg-integrations .ih-src { background: var(--surface); border: 1px solid var(--hairline); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 9px; box-shadow: var(--shadow-sm); min-width: 0; }
.pg-integrations .ih-src-head { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.pg-integrations .ih-src-title { min-width: 0; }
.pg-integrations .ih-src-title h4 { font: 600 15px/1.25 var(--font-cond); }
.pg-integrations .ih-src-title p { font-size: 12px; color: var(--ink-3); margin-top: 2px; }
.pg-integrations .ih-src-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pg-integrations .ih-src-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin: 0; }
.pg-integrations .ih-src-stats dt { font-size: 11.5px; color: var(--ink-3); }
.pg-integrations .ih-src-stats dd { margin: 0; font-weight: 600; font-size: 15px; font-variant-numeric: tabular-nums; }
.pg-integrations .ih-mix { display: flex; gap: 2px; height: 8px; border-radius: 999px; overflow: hidden; background: var(--surface-2); }
.pg-integrations .ih-mix .seg { background: var(--tone); min-width: 3px; }
.pg-integrations .ih-mix .seg.tone-neutral { background: var(--ink-3); }
.pg-integrations .ih-native { font-size: 12px; color: var(--ink-3); display: flex; align-items: center; gap: 6px; }
.pg-integrations .ih-src-foot { border-top: 1px solid var(--hairline); padding-top: 8px; display: flex; flex-direction: column; gap: 5px; }
.pg-integrations .ih-next { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--ink-2); }
.pg-integrations .ih-pages { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.pg-integrations .ih-page { font-size: 12.5px; font-weight: 500; }
.pg-integrations .ih-mail { display: grid; grid-template-columns: minmax(300px, 380px) minmax(0, 1fr); gap: 16px; align-items: start; }
.pg-integrations .ih-inbox { position: sticky; top: calc(var(--topbar-h) + 12px); max-height: calc(100vh - var(--topbar-h) - 40px); display: flex; flex-direction: column; }
.pg-integrations .ih-inbox-filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--hairline); }
.pg-integrations .ih-inbox-filters .seg button { padding: 0 8px; }
.pg-integrations .ih-inbox-list { list-style: none; margin: 0; padding: 0; overflow: auto; flex: 1; }
.pg-integrations .ih-msg { display: flex; flex-direction: column; gap: 4px; width: 100%; text-align: left; border: 0; border-bottom: 1px solid var(--hairline);
  background: transparent; padding: 10px 14px; cursor: pointer; color: var(--ink); font: inherit; }
.pg-integrations .ih-msg:hover { background: var(--row-hover); }
.pg-integrations .ih-msg.active { background: color-mix(in srgb, var(--sign) 8%, var(--surface)); box-shadow: inset 3px 0 0 var(--tab-bar); }
.pg-integrations .ih-msg-top { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }
.pg-integrations .ih-msg-from { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pg-integrations .ih-msg-time { color: var(--ink-3); white-space: nowrap; font-variant-numeric: tabular-nums; }
.pg-integrations .ih-msg-subj { font-size: 13px; line-height: 1.35; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.pg-integrations .ih-clip { display: inline-flex; margin-right: 4px; color: var(--ink-3); vertical-align: -1px; }
.pg-integrations .ih-msg-foot { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.pg-integrations .ih-detail { min-width: 0; }
.pg-integrations .ih-detail > * + * { margin-top: 16px; }
.pg-integrations .ih-detail.is-refreshing { opacity: .55; transition: opacity .12s; }
.pg-integrations .ih-hdr-top { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.pg-integrations .ih-hdr-main { min-width: 0; flex: 1 1 360px; }
.pg-integrations .ih-subject { font: 600 18px/1.3 var(--font-cond); overflow-wrap: anywhere; margin-bottom: 4px; }
.pg-integrations .ih-hdr-main p + p { margin-top: 2px; }
.pg-integrations .ih-hdr-main .mono { overflow-wrap: anywhere; }
.pg-integrations .ih-hdr-chips { display: flex; gap: 6px; align-items: flex-start; flex-wrap: wrap; }
.pg-integrations .ih-tpe { color: var(--ink-2); font-weight: 500; }
.pg-integrations .ih-cm-note { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--hairline); color: var(--ink-2); display: flex; gap: 6px; align-items: flex-start; }
.pg-integrations .ih-cm-note .icon { flex: none; margin-top: 2px; }
.pg-integrations .ih-detail-grid { align-items: start; }
.pg-integrations .ih-detail-grid > [class*="span-"] > * + * { margin-top: 16px; }
.pg-integrations .ih-steps { list-style: none; margin: 0; padding: 0 0 0 4px; position: relative; }
.pg-integrations .ih-steps::before { content: ""; position: absolute; left: 13px; top: 8px; bottom: 8px; width: 2px; background: var(--hairline); }
.pg-integrations .ih-step { position: relative; display: flex; gap: 12px; padding: 0 0 14px; }
.pg-integrations .ih-step:last-child { padding-bottom: 0; }
.pg-integrations .ih-step-dot { position: relative; z-index: 1; flex: none; width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center;
  background: color-mix(in srgb, var(--tone, var(--neutral)) 18%, var(--surface)); color: var(--tone, var(--neutral));
  box-shadow: 0 0 0 2px var(--surface), inset 0 0 0 1.5px var(--tone, var(--neutral)); }
.pg-integrations .ih-step.pending .ih-step-dot { background: var(--surface); box-shadow: 0 0 0 2px var(--surface), inset 0 0 0 1.5px var(--hairline-strong); }
.pg-integrations .ih-step-body { min-width: 0; flex: 1; padding-top: 1px; display: grid; grid-template-columns: minmax(170px, 210px) minmax(0, 1fr); gap: 4px 16px; align-items: baseline; }
.pg-integrations .ih-step-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pg-integrations .ih-att .card-title { overflow-wrap: anywhere; }
.pg-integrations .ih-att .card-head { flex-wrap: wrap; }
.pg-integrations .ih-step-name { font: 600 14px/1.3 var(--font-cond); }
.pg-integrations .ih-step-detail { font-size: 13px; color: var(--ink-2); overflow-wrap: anywhere; }
.pg-integrations .ih-step.outcome { padding-top: 4px; }
.pg-integrations .ih-wb-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.pg-integrations .ih-wb-bar .seg { max-width: 100%; overflow-x: auto; }
.pg-integrations .ih-wb-legend { margin: 0; }
.pg-integrations .ih-sw { width: 14px; height: 10px; border-radius: 2px; display: inline-block; border: 1px solid var(--hairline); }
.pg-integrations .ih-sw.hdr { background: color-mix(in srgb, var(--series-1) 22%, var(--surface)); }
.pg-integrations .ih-sw.mcol { background: color-mix(in srgb, var(--series-1) 8%, var(--surface)); }
.pg-integrations .ih-sw.ucol { background: color-mix(in srgb, var(--warning) 22%, var(--surface)); }
.pg-integrations .ih-sw.title { background: var(--surface-2); }
.pg-integrations .ih-sw.totals { background: color-mix(in srgb, var(--series-6) 14%, var(--surface)); }
.pg-integrations .ih-sheet-note { margin-bottom: 8px; color: var(--ink-2); }
.pg-integrations .ih-sheet-note .icon { color: var(--good); vertical-align: -2px; margin-right: 4px; }
.pg-integrations .ih-grid td.span-all { white-space: nowrap; }
.pg-integrations .ih-grid-wrap { overflow: auto; max-height: 420px; border: 1px solid var(--hairline); border-radius: 8px; }
.pg-integrations .ih-grid { border-collapse: separate; border-spacing: 0; font-size: 12.5px; min-width: 100%; }
.pg-integrations .ih-grid th, .pg-integrations .ih-grid td { border-right: 1px solid var(--hairline); border-bottom: 1px solid var(--hairline); padding: 4px 8px; white-space: nowrap; height: 26px; }
.pg-integrations .ih-grid thead th { position: sticky; top: 0; z-index: 2; background: var(--surface-2); font: 500 11px var(--font-mono); color: var(--ink-3); text-align: center; }
.pg-integrations .ih-grid thead th.mapped { color: var(--series-1); }
.pg-integrations .ih-grid thead th.unmapped { color: var(--delta-bad); }
.pg-integrations .ih-grid th.rn { position: sticky; left: 0; z-index: 1; background: var(--surface-2); font: 500 11px var(--font-mono); color: var(--ink-3); text-align: right; min-width: 30px; }
.pg-integrations .ih-grid thead th.rn { z-index: 3; }
.pg-integrations .ih-grid td.n { text-align: right; font-variant-numeric: tabular-nums; }
.pg-integrations .ih-grid td.mcol { background: color-mix(in srgb, var(--series-1) 5%, transparent); }
.pg-integrations .ih-grid td.ucol { background: color-mix(in srgb, var(--warning) 14%, transparent); }
.pg-integrations .ih-grid tr.title td { background: var(--surface-2); color: var(--ink-2); font-weight: 600; }
.pg-integrations .ih-grid tr.hdr td { background: color-mix(in srgb, var(--series-1) 16%, var(--surface)); font-weight: 600; }
.pg-integrations .ih-grid tr.hdr td.ucol { background: color-mix(in srgb, var(--warning) 26%, var(--surface)); }
.pg-integrations .ih-grid tr.totals td { background: color-mix(in srgb, var(--series-6) 11%, var(--surface)); font-weight: 600; }
.pg-integrations .ih-field { display: block; font: 500 10.5px var(--font-mono); color: var(--series-1); margin-top: 1px; }
.pg-integrations .ih-field.warn { color: var(--delta-bad); }
.pg-integrations .ih-loaded + .ih-loaded { margin-top: 10px; }
.pg-integrations .ih-loaded .table-wrap { border: 1px solid var(--hairline); border-radius: 8px; margin-top: 4px; }
.pg-integrations .ih-body-text { white-space: pre-wrap; font: 13px/1.55 var(--font-mono); background: var(--surface-2); border: 1px solid var(--hairline);
  border-radius: 8px; padding: 12px 14px; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
.pg-integrations mark.hl { border-radius: 4px; padding: 1px 2px; color: var(--ink); background: color-mix(in srgb, var(--c) 22%, transparent); box-shadow: inset 0 -2px 0 var(--c); }
.pg-integrations mark.hl-po { --c: var(--series-1); }
.pg-integrations mark.hl-line { --c: var(--series-7); }
.pg-integrations mark.hl-date { --c: var(--series-6); }
.pg-integrations mark.hl-qty { --c: var(--series-2); }
.pg-integrations mark.hl-basis { --c: var(--ink-3); }
.pg-integrations mark.hl-vague { --c: var(--critical); }
.pg-integrations .ih-hl-legend { margin: 8px 0 12px; font-size: 11.5px; }
.pg-integrations .ih-kv { margin-top: 4px; }
.pg-integrations .ih-try { display: flex; flex-direction: column; gap: 12px; }
.pg-integrations .ih-presets { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pg-integrations .ih-field-l { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: var(--ink-2); }
.pg-integrations .ih-field-l .select, .pg-integrations .ih-field-l .input, .pg-integrations .ih-field-l .textarea { width: 100%; }
.pg-integrations .ih-field-l .textarea { font-size: 12.5px; min-height: 170px; }
.pg-integrations .ih-drop { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; text-align: center; padding: 26px 16px;
  border: 1.5px dashed var(--hairline-strong); border-radius: 12px; background: var(--surface-2); cursor: pointer; color: var(--ink-2); }
.pg-integrations .ih-drop:hover, .pg-integrations .ih-drop.over { border-color: var(--sign); background: color-mix(in srgb, var(--sign) 6%, var(--surface)); }
.pg-integrations .ih-try-out > * + *, .pg-integrations .ih-xlsx-out > * + * { margin-top: 10px; }
.pg-integrations .ih-opts-card .card-body { overflow: hidden; }
.pg-integrations .ih-opts-layout { display: grid; grid-template-columns: minmax(260px, 360px) minmax(0, 1fr); gap: 18px; align-items: start; }
.pg-integrations .ih-opts-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.pg-integrations .ih-opt { border: 1px solid var(--hairline); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 7px; background: var(--surface); min-width: 0; }
.pg-integrations .ih-opt.rec { border-color: color-mix(in srgb, var(--good) 55%, var(--hairline)); box-shadow: inset 0 3px 0 var(--good); }
.pg-integrations .ih-opt.tone-critical { opacity: .82; }
.pg-integrations .ih-opt header { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.pg-integrations .ih-opt-n { width: 20px; height: 20px; border-radius: 50%; display: inline-grid; place-items: center; background: var(--c);
  color: #fff; font: 700 11.5px/1 var(--font-ui); flex: none; }
.pg-integrations .chart .ih-dot-n { fill: #fff; font-size: 11px; font-weight: 700; pointer-events: none; }
.pg-integrations .ih-opt h4 { font: 600 14.5px/1.3 var(--font-cond); }
.pg-integrations .ih-opt-stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(78px, 1fr)); gap: 4px 10px; margin: 0; }
.pg-integrations .ih-opt-stats dt { font-size: 11px; color: var(--ink-3); }
.pg-integrations .ih-opt-stats dd { margin: 0; font-weight: 600; font-size: 13.5px; font-variant-numeric: tabular-nums; }
.pg-integrations .ih-pro, .pg-integrations .ih-con { display: flex; gap: 6px; align-items: flex-start; color: var(--ink-2); }
.pg-integrations .ih-pro .icon { color: var(--good); flex: none; margin-top: 2px; }
.pg-integrations .ih-con .icon { color: var(--critical); flex: none; margin-top: 2px; }
@media (max-width: 1100px) {
  .pg-integrations .ih-step-body { grid-template-columns: minmax(0, 1fr); }
  .pg-integrations .ih-mail { grid-template-columns: minmax(0, 1fr); }
  .pg-integrations .ih-inbox { position: static; max-height: 420px; }
  .pg-integrations .ih-opts-layout { grid-template-columns: minmax(0, 1fr); }
}
`;
