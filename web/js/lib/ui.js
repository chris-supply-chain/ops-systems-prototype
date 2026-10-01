// Shared UI components. Most return html`` fragments; interactive ones
// (dataTable, tabs, segmented, drawer, toast) mount into an element.
import { html, raw, esc, on, $, toHTML, SafeHTML } from './dom.js';
import { icon } from './icons.js';
import { fmt } from './format.js';
import { charts } from './charts.js';

// --- page context (set by app.js before each render) ----------------------
let CURRENT = {};
function setCurrentPage(p) { CURRENT = p || {}; }

const TONE_ICON = {
  good: 'check-circle',
  warning: 'alert-triangle',
  serious: 'alert-triangle',
  critical: 'alert-octagon',
  info: 'info',
};

// Domain enums -> status tone. Anything unknown renders neutral.
const STATUS_TONE = {
  // good / done
  OK: 'good', PASS: 'good', CONFIRMED: 'good', ACCEPTED: 'good', RELEASED: 'good', DELIVERED: 'good',
  RECEIVED: 'good', POSTED: 'good', EXECUTED: 'good', APPROVED: 'good', IMPLEMENTED: 'good', RESOLVED: 'good',
  AVAILABLE: 'good', ACKED: 'good', AWARDED: 'good', REPLAYED: 'good', REPAIRED: 'good', GOOD: 'good',
  ACTIVE: 'good', ON_TIME: 'good', COMPLETE: 'good', COMPLETED: 'good', DEPLOYED: 'good',
  // in progress / informational
  OPEN: 'info', IN_TRANSIT: 'info', ON_WATER: 'info', AT_PORT: 'info', BOOKED: 'info', GATED_IN: 'info',
  OUT_FOR_DELIVERY: 'info', SHIPPED: 'info', ALLOCATED: 'info', AT_3PL: 'info', BUILT: 'info', WIP: 'info',
  PROPOSED: 'info', SENT: 'info', QUEUED: 'info', PENDING: 'info', IN_REVIEW: 'info', EVALUATING: 'info',
  ISF_FILED: 'info', ENTRY_FILED: 'info', REQUESTED: 'info', ACKNOWLEDGED: 'info', CONTAINED: 'info',
  DIAGNOSED: 'info', INFO: 'info', IN_PRODUCTION: 'info', PLANNED: 'info',
  // attention
  UNCONFIRMED: 'warning', PARTIAL: 'warning', WARN: 'warning', WARNING: 'warning', DUPLICATE: 'warning',
  EXPIRED: 'warning', ACCEPTED_UNDER_DEVIATION: 'warning', CONDITIONAL: 'warning', PHASE_OUT: 'warning',
  QC_HOLD: 'warning', EXAM: 'warning', ON_HOLD: 'warning', HOLD: 'warning', AT_RISK: 'warning', LATE: 'warning',
  CHANGES_REQUESTED: 'warning', BELOW_SAFETY_STOCK: 'warning', DEFER: 'warning', EXPEDITE: 'serious',
  // serious
  DISPUTED: 'serious', SERIOUS: 'serious', CUSTOMS_HOLD: 'serious', EXCEPTION: 'serious', QUARANTINED: 'serious',
  REWORK: 'serious', BLOCKED: 'serious', RETURNED: 'serious', RTV: 'serious', SHORT: 'serious',
  ROLLED_BACK: 'serious', PAST_DUE_RELEASE: 'serious',
  // critical
  REJECTED: 'critical', FAIL: 'critical', FAILED: 'critical', CRITICAL: 'critical', SCRAPPED: 'critical',
  SCRAP: 'critical', LINE_DOWN: 'critical', SHORTAGE: 'critical',
  // neutral / closed
  CLOSED: 'neutral', DRAFT: 'neutral', CANCELLED: 'neutral', WRITTEN_OFF: 'neutral', SUPERSEDED: 'neutral',
  OBSOLETE: 'neutral', NOT_INSPECTED: 'neutral', COMPONENT: 'neutral', INSTALLED: 'neutral', LIQUIDATED: 'neutral',
};

function toneOf(value) {
  if (value == null) return 'neutral';
  return STATUS_TONE[String(value).toUpperCase()] || 'neutral';
}

// --- page header -----------------------------------------------------------
function pageHeader({ kicker, num, title, lede, goal, actions } = {}) {
  kicker = kicker ?? CURRENT.group;
  num = num ?? CURRENT.num;
  title = title ?? CURRENT.label;
  lede = lede === undefined ? CURRENT.lede : lede;
  goal = goal === undefined ? CURRENT.goal : goal;
  const n = num != null ? String(num).padStart(2, '0') : null;
  return html`<header class="page-head">
    <div class="page-head-main">
      ${kicker ? html`<span class="exit-tab">${kicker}${n ? html`<span class="dot">·</span>${n}` : ''}</span>` : ''}
      <h1 class="page-title">${title}</h1>
      ${lede ? html`<p class="page-lede">${lede}</p>` : ''}
      ${goal ? html`<p class="page-goal"><span class="goal-tag">Goal</span><span class="goal-text">${goal}</span></p>` : ''}
    </div>
    ${actions ? html`<div class="page-actions">${actions}</div>` : ''}
  </header>`;
}

// --- chips -----------------------------------------------------------------
function chip(tone = 'neutral', label = '', { icon: ic } = {}) {
  const name = ic === false ? null : (ic || TONE_ICON[tone]);
  return html`<span class="chip tone-${tone}">${name
    ? html`<span class="chip-ic">${icon(name, 13)}</span>`
    : html`<span class="chip-dot"></span>`}<span class="chip-label">${label}</span></span>`;
}

function statusChip(value, label) {
  if (value == null || value === '') return html`<span class="nil">—</span>`;
  return chip(toneOf(value), label ?? fmt.title(value));
}

// --- KPI tile ----------------------------------------------------------------
function kpi({ label, value, unit, delta, deltaGood = 'up', deltaLabel, hint, spark, status, cls } = {}) {
  let deltaHtml = '';
  if (delta != null && delta !== '') {
    let dir = 0;
    let text;
    if (typeof delta === 'number') {
      dir = Math.sign(delta);
      text = fmt.num(Math.abs(delta), Math.abs(delta) < 10 && !Number.isInteger(delta) ? 1 : 0);
    } else {
      const s = String(delta).trim();
      if (/^[+▲]/.test(s)) dir = 1;
      else if (/^[-−▼]/.test(s)) dir = -1;
      text = s.replace(/^[+\-−▲▼]\s*/, '');
    }
    const good = dir === 0 ? null : (dir > 0) === (deltaGood === 'up');
    const cls2 = good == null ? 'flat' : good ? 'good' : 'bad';
    const glyph = dir > 0 ? '▲' : dir < 0 ? '▼' : '•';
    deltaHtml = html`<span class="kpi-delta ${cls2}"><span class="glyph">${glyph}</span>${text}</span>`;
  }
  let statusHtml = '';
  if (status) {
    const st = typeof status === 'string' ? { tone: status, label: fmt.title(status) } : status;
    statusHtml = chip(st.tone, st.label);
  }
  const sparkHtml = spark && spark.length > 1 ? charts.sparkline(spark) : '';
  return html`<div class="kpi${cls ? ' ' + cls : ''}">
    <div class="kpi-top"><span class="kpi-label">${label}</span>${statusHtml}</div>
    <div class="kpi-value">${value ?? '—'}${unit ? html`<span class="kpi-unit">${unit}</span>` : ''}</div>
    ${(deltaHtml || deltaLabel) ? html`<div class="kpi-foot">${deltaHtml}${deltaLabel ? html`<span class="kpi-delta-label">${deltaLabel}</span>` : ''}</div>` : ''}
    ${hint ? html`<div class="kpi-hint">${hint}</div>` : ''}
    ${sparkHtml ? html`<div class="kpi-spark">${sparkHtml}</div>` : ''}
  </div>`;
}

// --- card ------------------------------------------------------------------
function card({ title, subtitle, actions, body, flush, tableToggle, id, cls } = {}) {
  const hasHead = title || subtitle || actions || tableToggle;
  const toggle = tableToggle
    ? html`<button class="btn btn-ghost sm" type="button" data-toggle-table aria-pressed="false">${icon('table', 15)}<span>Table</span></button>`
    : '';
  return html`<section class="card${flush ? ' flush' : ''}${cls ? ' ' + cls : ''}"${id ? raw(` id="${esc(id)}"`) : ''}>
    ${hasHead ? html`<header class="card-head">
      <div class="card-head-text">${title ? html`<h3 class="card-title">${title}</h3>` : ''}${subtitle ? html`<p class="card-sub">${subtitle}</p>` : ''}</div>
      ${(actions || tableToggle) ? html`<div class="card-actions">${actions || ''}${toggle}</div>` : ''}
    </header>` : ''}
    <div class="card-body">${body ?? ''}</div>
  </section>`;
}

// --- callout, meter, timeline ------------------------------------------------
function callout({ tone = 'info', title, body, icon: ic } = {}) {
  const name = ic === false ? null : (ic || TONE_ICON[tone] || 'info');
  return html`<div class="callout tone-${tone}">
    <div class="callout-title">${name ? html`<span class="callout-ic">${icon(name, 16)}</span>` : ''}<span>${title}</span></div>
    ${body ? html`<div class="callout-body">${body}</div>` : ''}
  </div>`;
}

function meter({ value = 0, max = 1, tone, label } = {}) {
  const pct = max ? Math.max(0, Math.min(1, Number(value) / Number(max))) : 0;
  return html`<div class="meter${tone ? ' tone-' + tone : ''}" role="meter" aria-valuenow="${value}" aria-valuemin="0" aria-valuemax="${max}"${label ? raw(` aria-label="${esc(label)}"`) : ''}><span style="width:${(pct * 100).toFixed(1)}%"></span></div>`;
}

function timeline(items = []) {
  return html`<ol class="timeline">${items.map((it) => html`<li class="tl-item tone-${it.tone || 'neutral'}">
    <span class="tl-dot"></span>
    <div class="tl-row">${it.ts ? html`<span class="tl-ts">${it.tsLabel || fmt.dt(it.ts)}</span>` : ''}${it.meta ? html`<span class="tl-meta">${it.meta}</span>` : ''}</div>
    <div class="tl-title">${it.title}</div>
    ${it.detail ? html`<div class="tl-detail">${it.detail}</div>` : ''}
  </li>`)}</ol>`;
}

// --- code ------------------------------------------------------------------
const SQL_KW = new Set(('SELECT FROM WHERE AND OR NOT NULL IS IN AS ON JOIN LEFT RIGHT INNER OUTER CROSS GROUP BY ORDER HAVING ' +
  'LIMIT OFFSET WITH RECURSIVE UNION ALL DISTINCT CASE WHEN THEN ELSE END COUNT SUM AVG MIN MAX COALESCE CAST INTEGER ' +
  'TEXT REAL CREATE TABLE VIEW INDEX PRIMARY KEY FOREIGN REFERENCES CHECK DEFAULT UNIQUE EXISTS BETWEEN LIKE ASC DESC ' +
  'INSERT INTO VALUES UPDATE SET DELETE EXPLAIN QUERY PLAN ROUND ABS LENGTH SUBSTR GROUP_CONCAT JULIANDAY DATE DATETIME ' +
  'USING OVER PARTITION WINDOW ROWS RANGE CURRENT ROW FILTER IIF NULLIF TRUE FALSE PRAGMA PRINTF REPLACE UPPER LOWER ' +
  'TRIM INSTR STRFTIME TOTAL LAG LEAD ROW_NUMBER RANK DENSE_RANK').split(' '));

function highlightSQL(src) {
  const re = /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|([\s\S])/g;
  let out = '';
  let m;
  while ((m = re.exec(src))) {
    if (m[1]) out += `<span class="tok-com">${esc(m[1])}</span>`;
    else if (m[2]) out += `<span class="tok-str">${esc(m[2])}</span>`;
    else if (m[3]) out += `<span class="tok-num">${esc(m[3])}</span>`;
    else if (m[4]) out += SQL_KW.has(m[4].toUpperCase()) ? `<span class="tok-kw">${esc(m[4])}</span>` : esc(m[4]);
    else out += esc(m[5]);
  }
  return out;
}

function highlightJSON(src) {
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|\b(true|false|null)\b|([\s\S])/g;
  let out = '';
  let m;
  while ((m = re.exec(src))) {
    if (m[1]) out += m[2] ? `<span class="tok-key">${esc(m[1])}</span>${esc(m[2])}` : `<span class="tok-str">${esc(m[1])}</span>`;
    else if (m[3]) out += `<span class="tok-num">${esc(m[3])}</span>`;
    else if (m[4]) out += `<span class="tok-kw">${esc(m[4])}</span>`;
    else out += esc(m[5]);
  }
  return out;
}

function prettyJSON(text) {
  if (typeof text !== 'string') return JSON.stringify(text, null, 2);
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
}

function codeBlock(text, lang = 'sql') {
  const src = String(text ?? '');
  const body = lang === 'sql' ? highlightSQL(src) : lang === 'json' ? highlightJSON(src) : esc(src);
  return html`<pre class="code lang-${lang}"><code>${raw(body)}</code></pre>`;
}

// --- states ------------------------------------------------------------------
function empty(msg = 'Nothing here yet.', { icon: ic = 'info' } = {}) {
  return html`<div class="empty"><span class="empty-ic">${icon(ic, 22)}</span><div>${msg}</div></div>`;
}

function loading(label = 'Loading') {
  return html`<div class="loading" role="status"><span class="loading-bar"></span><span>${label}…</span></div>`;
}

function errorBox(err) {
  const msg = err && err.message ? err.message : String(err);
  return callout({ tone: 'critical', title: 'Something went wrong', body: html`<span class="mono small">${msg}</span>` });
}

// --- buttons -----------------------------------------------------------------
const ATTR_NAME = /^[a-zA-Z_:][-a-zA-Z0-9_:.]*$/;
function attrs(obj = {}) {
  return raw(Object.entries(obj)
    .filter(([k, v]) => ATTR_NAME.test(k) && v != null && v !== false)
    .map(([k, v]) => (v === true ? ` ${k}` : ` ${k}="${esc(v)}"`))
    .join(''));
}

function button({ label, icon: ic, variant = 'secondary', size, attrs: a = {}, type = 'button', title } = {}) {
  const cls = ['btn', variant && variant !== 'secondary' ? `btn-${variant}` : '', size || '', !label && ic ? 'icon-btn' : '']
    .filter(Boolean).join(' ');
  const extra = { ...a };
  if (title) extra.title = title;
  if (!label && title) extra['aria-label'] = title;
  return html`<button type="${type}" class="${cls}"${attrs(extra)}>${ic ? icon(ic, size === 'sm' ? 15 : 16) : ''}${label ? html`<span>${label}</span>` : ''}</button>`;
}

// --- data table --------------------------------------------------------------
function dataTable(el, opts = {}) {
  const o = { pageSize: 50, search: false, empty: 'No rows', ...opts };
  const cols = (o.columns || []).map((c) => ({ sortable: true, ...c, key: String(c.key) }));
  const state = {
    rows: o.rows || [],
    key: o.initialSort ? String(o.initialSort.key) : null,
    dir: (o.initialSort && o.initialSort.dir) || 'desc',
    page: 0,
    q: '',
  };
  el.classList.add('dt');
  const maxH = o.maxHeight ? (typeof o.maxHeight === 'number' ? `${o.maxHeight}px` : String(o.maxHeight)) : null;
  el.innerHTML = html`${o.search ? html`<div class="dt-toolbar">
      <label class="dt-search">${icon('search', 15)}<input class="input sm" type="text" placeholder="${o.searchPlaceholder || 'Filter rows…'}" aria-label="Filter rows"></label>
      <span class="dt-count"></span>${o.toolbar || ''}
    </div>` : ''}
    <div class="table-wrap"${maxH ? raw(` style="max-height:${esc(maxH)}"`) : ''}>
      <table class="table${o.dense ? ' dense' : ''}"><thead></thead><tbody></tbody></table>
    </div>
    <div class="dt-foot" hidden></div>`;
  const thead = $('thead', el);
  const tbody = $('tbody', el);
  const foot = $('.dt-foot', el);
  const countEl = $('.dt-count', el);
  const wrap = $('.table-wrap', el);
  let view = [];

  const val = (c, r) => (c.value ? c.value(r) : r[c.key]);

  function cell(c, r) {
    if (c.render) {
      const v = c.render(r);
      return v == null ? '' : v;
    }
    const v = val(c, r);
    if (v == null || v === '') return html`<span class="nil">—</span>`;
    if (c.format) return c.format(v, r);
    if (typeof v === 'number') return Number.isInteger(v) ? fmt.int(v) : fmt.num(v, 2);
    return String(v);
  }

  function renderHead() {
    thead.innerHTML = html`<tr>${cols.map((c) => {
      const sorted = state.key === c.key;
      const align = c.align || (c.num ? 'right' : 'left');
      const style = [c.width ? `width:${typeof c.width === 'number' ? c.width + 'px' : c.width}` : '', `text-align:${align}`]
        .filter(Boolean).join(';');
      const cls = [c.num ? 'num' : '', c.sortable ? 'sortable' : '', sorted ? 'sorted' : ''].filter(Boolean).join(' ');
      const ariaSort = sorted ? (state.dir === 'asc' ? 'ascending' : 'descending') : 'none';
      return html`<th class="${cls}" style="${style}" data-key="${c.key}" aria-sort="${ariaSort}"${c.title ? raw(` title="${esc(c.title)}"`) : ''}>${c.label}${c.sortable
        ? html`<span class="caret">${sorted ? (state.dir === 'asc' ? '▲' : '▼') : ''}</span>` : ''}</th>`;
    })}</tr>`;
  }

  function compute() {
    let rows = state.rows;
    if (state.q) {
      const q = state.q.toLowerCase();
      rows = rows.filter((r) => cols.some((c) => {
        const v = val(c, r);
        return v != null && String(v).toLowerCase().includes(q);
      }));
    }
    if (state.key != null) {
      const c = cols.find((x) => x.key === state.key);
      if (c) {
        const dir = state.dir === 'asc' ? 1 : -1;
        rows = rows.map((r, i) => [r, i]).sort((a, b) => {
          const va = val(c, a[0]);
          const vb = val(c, b[0]);
          const na = va == null || va === '';
          const nb = vb == null || vb === '';
          if (na && nb) return a[1] - b[1];
          if (na) return 1;
          if (nb) return -1;
          const cmp = typeof va === 'number' && typeof vb === 'number'
            ? va - vb
            : String(va).localeCompare(String(vb), undefined, { numeric: true, sensitivity: 'base' });
          return cmp !== 0 ? cmp * dir : a[1] - b[1];
        }).map((x) => x[0]);
      }
    }
    return rows;
  }

  function renderBody() {
    const rows = compute();
    const total = rows.length;
    const ps = o.pageSize || Math.max(total, 1);
    const pages = Math.max(1, Math.ceil(total / ps));
    if (state.page >= pages) state.page = pages - 1;
    view = rows.slice(state.page * ps, state.page * ps + ps);
    if (!total) {
      tbody.innerHTML = html`<tr class="dt-empty"><td colspan="${cols.length}">${o.empty}</td></tr>`;
    } else {
      tbody.innerHTML = html`${view.map((r, i) => html`<tr data-i="${i}"${o.onRowClick ? raw(' class="clickable"') : ''}>${cols.map((c) => {
        const cls = [c.num ? 'num' : '', c.mono ? 'mono' : '', c.wrap ? 'wrap' : '', c.cls || ''].filter(Boolean).join(' ');
        const align = !c.num && c.align ? raw(` style="text-align:${esc(c.align)}"`) : '';
        return html`<td${cls ? raw(` class="${cls}"`) : ''}${align}>${cell(c, r)}</td>`;
      })}</tr>`)}`;
    }
    if (countEl) countEl.textContent = `${fmt.int(total)} row${total === 1 ? '' : 's'}`;
    if (pages > 1) {
      const from = state.page * ps + 1;
      const to = Math.min(total, from + ps - 1);
      foot.hidden = false;
      foot.innerHTML = html`<span class="dt-range">${fmt.int(from)}–${fmt.int(to)} of ${fmt.int(total)}</span>
        <span class="spacer"></span>
        <button class="btn sm" type="button" data-pg="prev"${state.page === 0 ? raw(' disabled') : ''}>${icon('chevron-left', 14)}<span>Prev</span></button>
        <button class="btn sm" type="button" data-pg="next"${state.page >= pages - 1 ? raw(' disabled') : ''}><span>Next</span>${icon('chevron-right', 14)}</button>`;
    } else {
      foot.hidden = true;
      foot.innerHTML = '';
    }
  }

  on(thead, 'click', 'th.sortable', (e, th) => {
    const k = th.dataset.key;
    if (state.key === k) state.dir = state.dir === 'asc' ? 'desc' : 'asc';
    else {
      state.key = k;
      const c = cols.find((x) => x.key === k);
      state.dir = c && c.num ? 'desc' : 'asc';
    }
    state.page = 0;
    renderHead();
    renderBody();
  });
  on(foot, 'click', '[data-pg]', (e, b) => {
    state.page += b.dataset.pg === 'next' ? 1 : -1;
    renderBody();
    wrap.scrollTop = 0;
  });
  if (o.onRowClick) {
    on(tbody, 'click', 'tr[data-i]', (e, tr) => {
      if (e.target.closest('a,button,input,select,textarea,label')) return;
      const r = view[+tr.dataset.i];
      if (r) o.onRowClick(r, e, tr);
    });
  }
  if (o.search) {
    const inp = $('.dt-search input', el);
    inp.addEventListener('input', () => {
      state.q = inp.value.trim();
      state.page = 0;
      renderBody();
    });
  }
  renderHead();
  renderBody();
  return {
    el,
    update(rows) { state.rows = rows || []; renderBody(); },
    get rows() { return state.rows; },
    get view() { return view; },
  };
}

// --- tabs & segmented ----------------------------------------------------------
function tabs(el, { tabs: list = [], active, onChange } = {}) {
  let items = list;
  let cur = active ?? (items[0] && items[0].id);
  function draw() {
    el.innerHTML = html`<div class="tabs" role="tablist">${items.map((t) => html`<button class="tab${t.id === cur ? ' active' : ''}" type="button" role="tab" aria-selected="${t.id === cur ? 'true' : 'false'}" data-tab="${t.id}">${t.icon ? icon(t.icon, 15) : ''}<span>${t.label}</span>${t.count != null && t.count !== ''
      ? html`<span class="tab-count">${typeof t.count === 'number' ? fmt.int(t.count) : t.count}</span>` : ''}</button>`)}</div>`;
  }
  draw();
  on(el, 'click', '[data-tab]', (e, b) => {
    const id = b.dataset.tab;
    if (id === cur) return;
    cur = id;
    draw();
    if (onChange) onChange(id);
  });
  return {
    set(id) { cur = id; draw(); },
    get active() { return cur; },
    update(next) { items = next; draw(); },
  };
}

function segmented(el, { options = [], value, onChange, label } = {}) {
  const opts = options.map((o) => (typeof o === 'object' ? o : { value: o, label: o }));
  let cur = value ?? (opts[0] && opts[0].value);
  function draw() {
    el.innerHTML = html`<div class="seg" role="group"${label ? raw(` aria-label="${esc(label)}"`) : ''}>${opts.map((o) => html`<button type="button" class="${String(o.value) === String(cur) ? 'active' : ''}" aria-pressed="${String(o.value) === String(cur) ? 'true' : 'false'}" data-value="${o.value}">${o.label}</button>`)}</div>`;
  }
  draw();
  on(el, 'click', '[data-value]', (e, b) => {
    const opt = opts.find((o) => String(o.value) === b.dataset.value);
    if (!opt || String(opt.value) === String(cur)) return;
    cur = opt.value;
    draw();
    if (onChange) onChange(cur);
  });
  return { set(v) { cur = v; draw(); }, get value() { return cur; } };
}

// --- drawer ----------------------------------------------------------------
const drawer = (() => {
  let root = null;
  let onCloseCb = null;
  let lastFocus = null;
  function ensure() {
    if (root) return root;
    root = document.createElement('div');
    root.className = 'drawer-root';
    root.hidden = true;
    root.innerHTML = `<div class="drawer-scrim" data-drawer-close></div>
      <aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header class="drawer-head">
          <div class="drawer-head-text"><h2 class="drawer-title" id="drawer-title"></h2><div class="drawer-sub"></div></div>
          <button class="btn btn-ghost icon-btn drawer-close" type="button" data-drawer-close aria-label="Close">${icon('x', 18)}</button>
        </header>
        <div class="drawer-body"></div>
      </aside>`;
    document.body.appendChild(root);
    root.addEventListener('click', (e) => { if (e.target.closest('[data-drawer-close]')) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && root && !root.hidden) close(); });
    return root;
  }
  function open({ title, subtitle, body, width, onClose } = {}) {
    ensure();
    lastFocus = document.activeElement;
    $('.drawer-title', root).textContent = title || '';
    const sub = $('.drawer-sub', root);
    sub.innerHTML = subtitle instanceof SafeHTML ? subtitle.s : esc(subtitle || '');
    sub.hidden = !subtitle;
    const b = $('.drawer-body', root);
    b.innerHTML = toHTML(body);
    b.scrollTop = 0;
    $('.drawer', root).style.width = width ? (typeof width === 'number' ? `${width}px` : width) : '';
    root.hidden = false;
    document.body.classList.add('drawer-open');
    onCloseCb = onClose || null;
    requestAnimationFrame(() => { const c = $('.drawer-close', root); if (c) c.focus(); });
    return b;
  }
  function close() {
    if (!root || root.hidden) return;
    root.hidden = true;
    document.body.classList.remove('drawer-open');
    const cb = onCloseCb;
    onCloseCb = null;
    if (cb) cb();
    if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus();
  }
  return {
    open,
    close,
    get body() { return root ? $('.drawer-body', root) : null; },
    get isOpen() { return !!root && !root.hidden; },
  };
})();

// --- toast -----------------------------------------------------------------
function toast(msg, tone = 'neutral', ms = 3600) {
  let stack = document.querySelector('.toasts');
  if (!stack) {
    stack = document.createElement('div');
    stack.className = 'toasts';
    stack.setAttribute('role', 'status');
    stack.setAttribute('aria-live', 'polite');
    document.body.appendChild(stack);
  }
  const t = document.createElement('div');
  t.className = `toast tone-${tone}`;
  const name = TONE_ICON[tone];
  t.innerHTML = `${name ? `<span class="toast-ic">${icon(name, 16)}</span>` : ''}<span class="toast-msg"></span>`;
  t.querySelector('.toast-msg').textContent = msg;
  stack.appendChild(t);
  setTimeout(() => {
    t.classList.add('out');
    setTimeout(() => t.remove(), 220);
  }, ms);
}

// --- entity links ------------------------------------------------------------
const enc = encodeURIComponent;
const NIL = () => html`<span class="nil">—</span>`;

export const link = {
  serial: (sn, label) => (sn ? html`<a class="id-link" href="#/genealogy?q=${enc(sn)}">${label ?? sn}</a>` : NIL()),
  lot: (id, label) => (id ? html`<a class="id-link" href="#/genealogy?q=${enc(id)}">${label ?? id}</a>` : NIL()),
  order: (id, label) => (id ? html`<a class="id-link" href="#/atp?order=${enc(id)}">${label ?? id}</a>` : NIL()),
  po: (po, line, label) => (po
    ? html`<a class="id-link" href="#/suppliers?po=${enc(po)}${line != null ? `&line=${enc(line)}` : ''}">${label ?? (line != null ? `${po}-${line}` : po)}</a>`
    : NIL()),
  shipment: (id, label) => (id ? html`<a class="id-link" href="#/shipments?id=${enc(id)}">${label ?? id}</a>` : NIL()),
  supplier: (id, name) => (id ? html`<a class="ent-link" href="#/suppliers?supplier=${enc(id)}">${name || id}</a>` : NIL()),
  item: (id, label) => (id ? html`<a class="id-link" href="#/inventory?item=${enc(id)}">${label ?? id}</a>` : NIL()),
  claim: (id, label) => (id ? html`<a class="id-link" href="#/warranty?claim=${enc(id)}">${label ?? id}</a>` : NIL()),
  table: (name, label) => (name ? html`<a class="id-link" href="#/sandbox?table=${enc(name)}">${label ?? name}</a>` : NIL()),
  route: (route, label) => html`<a class="ent-link" href="#/${route}">${label}</a>`,
};

export const ui = {
  setCurrentPage,
  toneOf,
  pageHeader,
  kpi,
  card,
  chip,
  statusChip,
  callout,
  meter,
  timeline,
  codeBlock,
  prettyJSON,
  empty,
  loading,
  errorBox,
  button,
  attrs,
  dataTable,
  tabs,
  segmented,
  drawer,
  toast,
};
