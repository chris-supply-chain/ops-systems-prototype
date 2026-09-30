// Data Sandbox: the relational model, live. Browse every table with its keys,
// constraints and relationships, page through rows (following foreign keys
// row to row), run read-only SQL, and see the schema as a map.
import { html, raw, esc, on, $, $$, injectStyle } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { icon } from '../lib/icons.js';

const LAYERS = [
  { id: 'LANDING', label: 'Landing', icon: 'download', blurb: 'Raw payloads exactly as each outside system sent them.' },
  { id: 'CORE', label: 'Core', icon: 'database', blurb: 'The normalized, canonical model: one source of truth.' },
  { id: 'ACTION', label: 'Action', icon: 'bolt', blurb: 'What the system decided, and every write it made.' },
  { id: 'PLATFORM', label: 'Platform', icon: 'flask', blurb: 'Tests, evals, contracts, review and thin-app telemetry.' },
  { id: 'VIEW', label: 'Views', icon: 'eye', blurb: 'Derived, read-only projections.' },
];
const LAYER_BY_ID = Object.fromEntries(LAYERS.map((l) => [l.id, l]));

const DOMAIN_LABEL = {
  master: 'Master data', make: 'Make', plan: 'Plan', source: 'Source', move: 'Move', inventory: 'Inventory',
  quality: 'Quality', decide: 'Decide & act', finance: 'Finance', platform: 'Instrumentation',
  assurance: 'Assurance', landing: 'Landing', view: 'Views', other: 'Other',
};
const DOMAIN_ORDER = {
  LANDING: ['landing'],
  CORE: ['master', 'make', 'plan', 'source', 'move', 'inventory', 'quality', 'other'],
  ACTION: ['decide', 'plan', 'finance'],
  PLATFORM: ['assurance', 'platform'],
  VIEW: ['view'],
};

const LANES = [
  { label: 'Landing', sub: 'raw, as received', test: (t) => t.layer === 'LANDING' },
  { label: 'Master data', sub: 'core', test: (t) => t.layer === 'CORE' && t.domain === 'master' },
  { label: 'Make · Inventory', sub: 'core', test: (t) => t.layer === 'CORE' && ['make', 'inventory'].includes(t.domain) },
  { label: 'Plan · Source', sub: 'core', test: (t) => t.layer === 'CORE' && ['plan', 'source'].includes(t.domain) },
  { label: 'Move · Quality', sub: 'core', test: (t) => t.layer === 'CORE' && !['master', 'make', 'inventory', 'plan', 'source'].includes(t.domain) },
  { label: 'Action', sub: 'decide · plan · finance', test: (t) => t.layer === 'ACTION' },
  { label: 'Platform', sub: 'assurance · telemetry', test: (t) => t.layer === 'PLATFORM' },
  { label: 'Views', sub: 'derived', test: (t) => t.layer === 'VIEW' },
];

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
const RUN_KEY = IS_MAC ? '⌘↵' : 'Ctrl↵';
const DEFAULT_TABLE = 'genealogy';
const PAGE_SIZE = 50;

let S = null;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
function loadSQL() {
  try { return localStorage.getItem('ops-sandbox-sql'); } catch { return null; }
}
function saveSQL(v) {
  try { localStorage.setItem('ops-sandbox-sql', v); } catch { /* ignore */ }
}

function layerTag(layer) {
  const l = LAYER_BY_ID[layer] || { label: layer, icon: 'layers' };
  return html`<span class="layer-tag">${icon(l.icon, 13)}<span>${l.label}</span></span>`;
}

// Break long snake_case names at underscores.
function wrapName(name) {
  return raw(String(name).split('_').map(esc).join('_<wbr>'));
}

function shortCheck(expr) {
  if (!expr) return '';
  const m = expr.match(/^\s*(\w+)\s+IN\s*\((.*)\)\s*$/i);
  if (m) {
    const n = m[2].split(',').length;
    return `IN (${n} values)`;
  }
  return expr.length > 34 ? `${expr.slice(0, 33)}…` : expr;
}

function cellText(v) {
  if (v == null) return null;
  return typeof v === 'number' ? (Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000)) : String(v);
}

// What one row of a table is: the first thing to know before joining it.
function grainLine(t) {
  if (!t || !t.grain) return '';
  return html`<p class="sb-grain"><span class="sb-grain-tag">Each row</span><span>${t.grain}</span></p>`;
}

// Which columns make one row unique, and what guarantees it: what to join on, and what to deduplicate by.
// 'a|b' is whichever of the two is filled.
function rowKeyLine(t) {
  const k = t && t.row_key;
  if (!k) return '';
  const cols = k.columns.map((c, i) => html`${i ? html`<span class="sb-key-sep">+</span>` : ''}<span class="sb-key-group">${c.split('|').map((p, j) =>
    html`${j ? html`<span class="sb-key-sep">or</span>` : ''}<span class="sb-key mono">${p}</span>`)}</span>`);
  const rowNumber = k.row_number && k.how !== 'ROW_NUMBER'
    ? html` <span class="mono">${k.row_number}</span> is only a row number.` : '';
  return html`<div class="sb-grain sb-rowkey"><span class="sb-grain-tag">Row key</span>
    <span class="sb-rowkey-body"><span class="sb-key-cols">${cols}</span><span class="sb-key-how">${k.note}${rowNumber}</span></span></div>`;
}

// the columns a declared or UNIQUE row key is made of (a primary key already wears its own badge)
function rowKeyColumns(t) {
  const k = t && t.row_key;
  if (!k || k.how === 'PRIMARY_KEY' || k.how === 'ROW_NUMBER') return new Set();
  return new Set(k.columns.flatMap((c) => c.split('|')));
}

function usedIn(name) {
  const list = S.useCases.filter((u) => u.path.some((st) => st.table === name));
  if (!list.length) return '';
  return html`<p class="sb-used"><span class="muted small">Used in</span>${list.map((u) => html`<button class="badge sb-uc-chip" type="button" data-uc="${u.id}">${u.title.split(':')[0]}</button>`)}</p>`;
}

function openUseCase(id) {
  S.uc = id;
  S.tabs.set('erd');
  showTab('erd');
}

function looksJSON(s) {
  return typeof s === 'string' && s.length > 1 && (s[0] === '{' || s[0] === '[');
}

// ---------------------------------------------------------------------------
// render
// ---------------------------------------------------------------------------
export async function render(el, ctx) {
  injectStyle('page-sandbox', PAGE_CSS);
  const [tablesRes, exRes, ucRes] = await Promise.all([api.get('/api/sandbox/tables'), api.get('/api/sandbox/examples'),
    api.get('/api/sandbox/use-cases')]);
  const tables = tablesRes.tables || [];
  const byName = new Map(tables.map((t) => [t.name, t]));
  let sel = ctx.query.get('table');
  if (!sel || !byName.has(sel)) sel = byName.has(DEFAULT_TABLE) ? DEFAULT_TABLE : (tables[0] && tables[0].name);
  let tab = ctx.query.get('tab') || 'tables';
  if (!['tables', 'sql', 'erd'].includes(tab)) tab = 'tables';

  S = {
    el,
    ctx,
    tables,
    byName,
    fkTotal: tablesRes.foreign_keys || 0,
    sel,
    filter: '',
    layerFilter: null,
    examples: exRes.examples || [],
    useCases: ucRes.use_cases || [],
    uc: ctx.query.get('uc') || null,
    sql: ctx.query.get('sql') || loadSQL() || ((exRes.examples || [])[0] || {}).sql || 'SELECT * FROM site LIMIT 50;',
    erd: null,
    rows: null,
    rowState: { offset: 0, order: null, dir: 'asc' },
    ro: null,
  };

  const byLayer = (id) => tables.filter((t) => t.layer === id);
  const sumRows = (list) => list.reduce((a, t) => a + (t.rows || 0), 0);
  const nTables = tables.filter((t) => t.kind === 'table').length;
  const nViews = tables.length - nTables;
  const pipe = ['LANDING', 'CORE', 'ACTION'].map((id) => ({ ...LAYER_BY_ID[id], list: byLayer(id) }));
  const plat = { ...LAYER_BY_ID.PLATFORM, list: byLayer('PLATFORM') };

  el.innerHTML = html`
    ${ui.pageHeader({
      lede: 'The whole relational model, live. Raw payloads land untouched, normalizers write the canonical core, and every decision writes back to the action layer, all in one database with foreign keys enforced on every write.',
    })}
    <div class="pg-sandbox">
      <p class="sb-facts-line">
        <span><strong>${fmt.int(nTables)}</strong> tables</span>
        <span><strong>${fmt.int(nViews)}</strong> views</span>
        <span><strong>${fmt.int(S.fkTotal)}</strong> foreign keys, enforced</span>
        <span><strong>${fmt.compact(sumRows(tables.filter((t) => t.kind === 'table')))}</strong> rows</span>
        <span class="muted">SQLite · <span class="mono">PRAGMA foreign_keys = ON</span> · portable to Postgres</span>
      </p>
      <div class="sb-pipe" role="list">
        ${pipe.map((l, i) => html`${i ? html`<span class="sb-pipe-arrow" aria-hidden="true">${icon('arrow-right', 18)}</span>` : ''}
          <button class="sb-layer" type="button" role="listitem" data-layer="${l.id}">
            <span class="sb-layer-top">${icon(l.icon, 16)}<span class="sb-layer-name">${l.label}</span><span class="sb-layer-count">${l.list.length} tables</span></span>
            <span class="sb-layer-blurb">${l.blurb}</span>
            <span class="sb-layer-rows">${fmt.int(sumRows(l.list))} rows</span>
          </button>`)}
        <span class="sb-pipe-gap" aria-hidden="true"></span>
        <button class="sb-layer sb-layer-side" type="button" role="listitem" data-layer="PLATFORM">
          <span class="sb-layer-top">${icon(plat.icon, 16)}<span class="sb-layer-name">${plat.label}</span><span class="sb-layer-count">${plat.list.length} tables</span></span>
          <span class="sb-layer-blurb">${plat.blurb}</span>
          <span class="sb-layer-rows">${fmt.int(sumRows(plat.list))} rows</span>
        </button>
      </div>
      <div id="sb-tabs"></div>
      <div id="sb-pane"></div>
    </div>`;

  S.tabs = ui.tabs($('#sb-tabs', el), {
    tabs: [
      { id: 'tables', label: 'Tables', icon: 'table', count: tables.length },
      { id: 'sql', label: 'SQL', icon: 'code' },
      { id: 'erd', label: 'Relationships', icon: 'link', count: S.fkTotal },
    ],
    active: tab,
    onChange: (id) => showTab(id),
  });

  on(el, 'click', '.sb-layer', (e, b) => {
    S.layerFilter = b.dataset.layer;
    S.filter = '';
    S.tabs.set('tables');
    showTab('tables');
  });

  showTab(tab);
}

export function unmount() {
  if (S && S.ro) S.ro.disconnect();
  if (S && S.onResize) window.removeEventListener('resize', S.onResize);
  S = null;
}

function showTab(id) {
  if (!S) return;
  if (S.ro) { S.ro.disconnect(); S.ro = null; }
  S.ctx.setQuery({ tab: id === 'tables' ? null : id, table: S.sel, sql: null, uc: id === 'erd' ? S.uc : null }, { silent: true });
  const pane = $('#sb-pane', S.el);
  if (id === 'sql') showSQL(pane);
  else if (id === 'erd') showERD(pane);
  else showTables(pane);
}

// ---------------------------------------------------------------------------
// Tables tab
// ---------------------------------------------------------------------------
function showTables(pane) {
  pane.innerHTML = html`<div class="sb-split">
    <aside class="card flush sb-list" aria-label="Tables">
      <div class="sb-list-head">
        <label class="dt-search">${icon('search', 15)}<input class="input sm" id="sb-filter" type="text" placeholder="Filter tables…" value="${S.filter}" aria-label="Filter tables"></label>
        <div class="sb-chips" id="sb-layer-chips"></div>
      </div>
      <div class="sb-groups" id="sb-groups"></div>
    </aside>
    <div class="sb-detail" id="sb-detail"></div>
  </div>`;
  const input = $('#sb-filter', pane);
  input.addEventListener('input', () => { S.filter = input.value.trim().toLowerCase(); renderList(); });
  on(pane, 'click', '.sb-item', (e, b) => selectTable(b.dataset.table));
  on(pane, 'click', '[data-clear-layer]', () => { S.layerFilter = null; renderList(); });
  renderList();
  selectTable(S.sel, { scroll: true });
}

function renderList() {
  const groupsEl = $('#sb-groups', S.el);
  const chips = $('#sb-layer-chips', S.el);
  if (!groupsEl) return;
  chips.innerHTML = S.layerFilter
    ? html`<button class="badge sb-layer-chip" type="button" data-clear-layer>${LAYER_BY_ID[S.layerFilter].label} only ${icon('x', 11)}</button>`
    : '';
  const f = S.filter;
  const match = (t) => (!f || t.name.includes(f) || (t.description || '').toLowerCase().includes(f))
    && (!S.layerFilter || t.layer === S.layerFilter);
  let out = '';
  for (const layer of LAYERS) {
    const inLayer = S.tables.filter((t) => t.layer === layer.id && match(t));
    if (!inLayer.length) continue;
    const domains = DOMAIN_ORDER[layer.id] || [];
    const extra = [...new Set(inLayer.map((t) => t.domain))].filter((d) => !domains.includes(d));
    const blocks = [...domains, ...extra].map((d) => ({ d, list: inLayer.filter((t) => t.domain === d) })).filter((b) => b.list.length);
    out += String(html`<div class="sb-layer-group">
      <div class="sb-group-head">${icon(layer.icon, 14)}<span>${layer.label}</span><span class="muted">${inLayer.length}</span></div>
      ${blocks.map((b) => html`${blocks.length > 1 || (layer.id !== 'LANDING' && layer.id !== 'VIEW') ? html`<div class="sb-domain">${DOMAIN_LABEL[b.d] || b.d}</div>` : ''}
        ${b.list.map((t) => html`<button class="sb-item${t.name === S.sel ? ' active' : ''}" type="button" data-table="${t.name}" title="${t.description}">
          <span class="sb-item-name">${wrapName(t.name)}</span><span class="sb-item-count">${t.rows == null ? 'view' : fmt.compact(t.rows)}</span>
        </button>`)}`)}
    </div>`);
  }
  groupsEl.innerHTML = out || String(ui.empty('No table matches that filter.'));
}

async function selectTable(name, { scroll } = {}) {
  if (!S || !name) return;
  S.sel = name;
  S.rowState = { offset: 0, order: null, dir: 'asc' };
  S.ctx.setQuery({ table: name }, { silent: true });
  $$('.sb-item', S.el).forEach((b) => b.classList.toggle('active', b.dataset.table === name));
  if (scroll) {
    // Scroll only the list, never the page.
    const act = $('.sb-item.active', S.el);
    const box = $('#sb-groups', S.el);
    if (act && box) {
      const a = act.getBoundingClientRect();
      const b = box.getBoundingClientRect();
      if (a.top < b.top || a.bottom > b.bottom) box.scrollTop += a.top - b.top - b.height / 3;
    }
  }
  const det = $('#sb-detail', S.el);
  if (!det) return;
  det.innerHTML = String(ui.loading('Loading table'));
  let d;
  try {
    d = await api.get(`/api/sandbox/table/${encodeURIComponent(name)}`);
  } catch (err) {
    det.innerHTML = String(ui.errorBox(err));
    return;
  }
  if (!S || S.sel !== name) return;
  const fkOut = d.columns.filter((c) => c.fk);
  const keyCols = rowKeyColumns(d);
  det.innerHTML = html`
    <section class="card sb-head-card">
      <div class="sb-title-row">
        <h2 class="sb-name">${wrapName(d.name)}</h2>
        ${layerTag(d.layer)}
        ${d.domain && d.layer !== 'LANDING' && d.layer !== 'VIEW' ? html`<span class="badge">${DOMAIN_LABEL[d.domain] || d.domain}</span>` : ''}
        <span class="spacer"></span>
        ${ui.button({ label: 'Query this table', icon: 'code', size: 'sm', attrs: { 'data-act': 'query' } })}
        ${d.kind === 'table' ? ui.button({ label: 'Relationships', icon: 'link', size: 'sm', attrs: { 'data-act': 'erd' } }) : ''}
      </div>
      ${d.description ? html`<p class="sb-desc">${d.description}</p>` : ''}
      ${grainLine(d)}
      ${rowKeyLine(d)}
      ${usedIn(d.name)}
      <div class="sb-facts">
        <span><strong>${d.rows == null ? '—' : fmt.int(d.rows)}</strong> rows</span>
        <span><strong>${d.columns.length}</strong> columns</span>
        <span><strong>${fkOut.length}</strong> references out</span>
        <span><strong>${d.referenced_by.length}</strong> referenced by</span>
        <span><strong>${d.indexes.length}</strong> indexes</span>
      </div>
    </section>

    <section class="card flush">
      <header class="card-head"><div class="card-head-text"><h3 class="card-title">Columns</h3><p class="card-sub">Keys and constraints are read from the live schema (PRAGMA table_info / foreign_key_list). Row key marks the columns that make a row unique.</p></div></header>
      <div class="table-wrap"><table class="table sb-cols">
        <thead><tr><th class="num" style="width:36px">#</th><th>Column</th><th>Type</th><th>Keys &amp; constraints</th><th>Default</th></tr></thead>
        <tbody>${d.columns.map((c) => html`<tr>
          <td class="num muted">${c.cid + 1}</td>
          <td class="mono strong">${c.name}</td>
          <td class="mono muted">${c.type || '—'}</td>
          <td class="sb-cons">
            ${c.pk ? html`<span class="badge badge-pk">${icon('key', 11)}PK${d.columns.filter((x) => x.pk).length > 1 ? `·${c.pk}` : ''}</span>` : ''}
            ${keyCols.has(c.name) ? html`<span class="badge badge-rowkey" title="Part of the row key: ${d.row_key.columns.join(' + ')}">ROW KEY</span>` : ''}
            ${c.fk ? html`<button class="badge badge-fk" type="button" data-goto="${c.fk.table}">FK → ${c.fk.table}.${c.fk.column || '?'}</button>` : ''}
            ${c.notnull && !c.pk ? html`<span class="badge">NOT NULL</span>` : ''}
            ${c.unique ? html`<span class="badge">UNIQUE</span>` : ''}
            ${c.check ? html`<span class="badge sb-check" title="CHECK (${c.check})">CHECK ${shortCheck(c.check)}</span>` : ''}
          </td>
          <td class="mono muted">${c.default ?? ''}</td>
        </tr>`)}</tbody>
      </table></div>
    </section>

    <div class="grid">
      <section class="card span-6">
        <header class="card-head"><div class="card-head-text"><h3 class="card-title">References</h3><p class="card-sub">Tables this one points at (parents).</p></div></header>
        ${fkOut.length ? html`<ul class="sb-refs">${fkOut.map((c) => html`<li>
          <span class="mono">${c.name}</span><span class="sb-arrow">${icon('arrow-right', 14)}</span>
          <button class="sb-ref" type="button" data-goto="${c.fk.table}"><span class="mono">${c.fk.table}</span><span class="mono muted">.${c.fk.column}</span></button>
        </li>`)}</ul>` : html`<p class="muted small">No foreign keys: this is a root table.</p>`}
      </section>
      <section class="card span-6">
        <header class="card-head"><div class="card-head-text"><h3 class="card-title">Referenced by</h3><p class="card-sub">Tables that point at this one (children).</p></div></header>
        ${d.referenced_by.length ? html`<ul class="sb-refs">${d.referenced_by.map((r) => html`<li>
          <button class="sb-ref" type="button" data-goto="${r.table}"><span class="mono">${r.table}</span><span class="mono muted">.${r.column}</span></button>
          <span class="sb-arrow">${icon('arrow-right', 14)}</span><span class="mono">${r.to_column}</span>
        </li>`)}</ul>` : html`<p class="muted small">Nothing references this table.</p>`}
      </section>
    </div>

    <section class="card flush" id="sb-rows-card">
      <header class="card-head">
        <div class="card-head-text"><h3 class="card-title">Rows</h3><p class="card-sub" id="sb-rows-sub">Click a header to sort. Click a foreign-key value to open the row it points at.</p></div>
        <div class="card-actions" id="sb-rows-pager"></div>
      </header>
      <div id="sb-rows"></div>
    </section>

    <section class="card">
      <header class="card-head"><div class="card-head-text"><h3 class="card-title">Definition</h3><p class="card-sub">Exactly as stored in sqlite_master.</p></div></header>
      ${ui.codeBlock(d.create_sql || '', 'sql')}
      ${d.indexes.length ? html`<div class="section-title">Indexes</div>
        <ul class="sb-refs">${d.indexes.map((ix) => html`<li><span class="mono">${ix.name}</span>${ix.unique ? html`<span class="badge">UNIQUE</span>` : ''}<span class="muted mono">(${ix.columns.join(', ')})</span></li>`)}</ul>` : ''}
      ${d.table_checks.length ? html`<div class="section-title">Table checks</div>
        <ul class="sb-refs">${d.table_checks.map((c) => html`<li><span class="mono">CHECK (${c})</span></li>`)}</ul>` : ''}
    </section>`;

  S.detail = d;
  on(det, 'click', '[data-goto]', (e, b) => {
    const t = b.dataset.goto;
    if (S.byName.has(t)) selectTable(t, { scroll: true });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  on(det, 'click', '[data-act=query]', () => {
    S.sql = `SELECT *\nFROM ${d.name}\nLIMIT 100;`;
    saveSQL(S.sql);
    S.tabs.set('sql');
    showTab('sql');
    runQuery();
  });
  on(det, 'click', '[data-act=erd]', () => {
    S.tabs.set('erd');
    showTab('erd');
  });
  on(det, 'click', '[data-uc]', (e, b) => openUseCase(b.dataset.uc));
  loadRows();
}

async function loadRows() {
  const name = S.sel;
  const holder = $('#sb-rows', S.el);
  if (!holder) return;
  const st = S.rowState;
  holder.classList.add('refetching');
  let res;
  try {
    res = await api.get(`/api/sandbox/rows/${encodeURIComponent(name)}`, {
      limit: PAGE_SIZE, offset: st.offset, order: st.order, dir: st.dir,
    });
  } catch (err) {
    holder.innerHTML = String(ui.errorBox(err));
    return;
  }
  if (!S || S.sel !== name) return;
  holder.classList.remove('refetching');
  S.rows = res;
  const fkCols = new Map((S.detail ? S.detail.columns : []).filter((c) => c.fk).map((c) => [c.name, c.fk]));
  const total = res.total;
  const from = res.rows.length ? res.offset + 1 : 0;
  const to = res.offset + res.rows.length;
  const pager = $('#sb-rows-pager', S.el);
  pager.innerHTML = html`<span class="dt-range">${fmt.int(from)}–${fmt.int(to)}${total != null ? html` of ${fmt.int(total)}` : ''}</span>
    <button class="btn sm icon-btn" type="button" data-pg="prev" aria-label="Previous page"${res.offset <= 0 ? raw(' disabled') : ''}>${icon('chevron-left', 15)}</button>
    <button class="btn sm icon-btn" type="button" data-pg="next" aria-label="Next page"${(total != null ? to >= total : res.rows.length < PAGE_SIZE) ? raw(' disabled') : ''}>${icon('chevron-right', 15)}</button>`;
  pager.onclick = (e) => {
    const b = e.target.closest('[data-pg]');
    if (!b || b.disabled) return;
    st.offset = Math.max(0, st.offset + (b.dataset.pg === 'next' ? PAGE_SIZE : -PAGE_SIZE));
    loadRows();
  };
  if (!res.rows.length) {
    holder.innerHTML = String(ui.empty(res.offset ? 'No more rows.' : 'This table is empty in the current dataset.'));
    return;
  }
  holder.innerHTML = html`<div class="table-wrap sb-rows-wrap"><table class="table dense sb-rows">
    <thead><tr>${res.columns.map((c) => html`<th class="sortable${st.order === c ? ' sorted' : ''}" data-col="${c}">${c}<span class="caret">${st.order === c ? (st.dir === 'asc' ? '▲' : '▼') : ''}</span></th>`)}</tr></thead>
    <tbody>${res.rows.map((r, ri) => html`<tr>${r.map((v, ci) => {
      const col = res.columns[ci];
      const txt = cellText(v);
      if (txt == null) return html`<td><span class="nil">null</span></td>`;
      const isNum = typeof v === 'number';
      const fk = fkCols.get(col);
      if (fk) {
        return html`<td><button class="sb-fk" type="button" data-t="${fk.table}" data-c="${fk.column}" data-v="${txt}" title="Open ${fk.table} where ${fk.column} = ${txt}">${txt}</button></td>`;
      }
      if (looksJSON(txt) || txt.length > 48) {
        return html`<td><button class="sb-long${looksJSON(txt) ? ' json' : ''}" type="button" data-r="${ri}" data-c="${ci}" title="Open full value">${txt.length > 48 ? `${txt.slice(0, 47)}…` : txt}</button></td>`;
      }
      return html`<td class="${isNum ? 'num' : ''}">${txt}</td>`;
    })}</tr>`)}</tbody>
  </table></div>`;
  on(holder, 'click', 'th[data-col]', (e, th) => {
    const c = th.dataset.col;
    if (st.order === c) st.dir = st.dir === 'asc' ? 'desc' : 'asc';
    else { st.order = c; st.dir = 'asc'; }
    st.offset = 0;
    loadRows();
  });
  on(holder, 'click', '.sb-fk', (e, b) => openLookup(b.dataset.t, b.dataset.c, b.dataset.v));
  on(holder, 'click', '.sb-long', (e, b) => {
    const v = S.rows.rows[+b.dataset.r][+b.dataset.c];
    const col = S.rows.columns[+b.dataset.c];
    const isJson = looksJSON(v);
    ui.drawer.open({
      title: `${S.sel}.${col}`,
      subtitle: isJson ? 'JSON value, pretty-printed' : 'Full value',
      body: isJson ? ui.codeBlock(ui.prettyJSON(v), 'json') : html`<pre class="code">${v}</pre>`,
    });
  });
}

// Follow a foreign key: show the row(s) it points at; FK values inside are
// themselves clickable, so you can walk the graph row to row.
async function openLookup(table, col, val, trail = []) {
  let res;
  try {
    res = await api.get(`/api/sandbox/lookup/${encodeURIComponent(table)}`, { col, val });
  } catch (err) {
    ui.drawer.open({ title: table, body: ui.errorBox(err) });
    return;
  }
  const path = [...trail, { table, col, val }];
  const body = html`
    ${path.length > 1 ? html`<div class="sb-trail">${path.map((p, i) => html`${i ? html`<span class="muted">${icon('chevron-right', 12)}</span>` : ''}<span class="mono">${p.table}</span>`)}</div>` : ''}
    ${res.rows.length ? res.rows.map((r) => html`<dl class="kv sb-kv">${res.columns.map((c, i) => {
      const v = cellText(r[i]);
      const fk = res.fks[c];
      let dd;
      if (v == null) dd = html`<span class="nil">null</span>`;
      else if (fk) dd = html`<button class="sb-fk" type="button" data-t="${fk.table}" data-c="${fk.column}" data-v="${v}">${v}</button>`;
      else if (looksJSON(v)) dd = ui.codeBlock(ui.prettyJSON(v), 'json');
      else dd = html`<span class="${typeof r[i] === 'number' ? 'num' : ''}">${v}</span>`;
      return html`<dt class="mono">${c}</dt><dd>${dd}</dd>`;
    })}</dl>`) : ui.empty('No row found. The reference is dangling, which a data contract should catch.')}
    <p class="muted small">${html`<a href="#/sandbox?table=${encodeURIComponent(table)}" data-drawer-close>Open ${table} in the table browser</a>`}</p>`;
  const b = ui.drawer.open({
    title: `${table}`,
    subtitle: html`<span class="mono">${col} = ${val}</span>${res.rows.length > 1 ? html`<span class="muted">· ${res.rows.length} rows</span>` : ''}`,
    body,
  });
  b.onclick = (e) => {
    const x = e.target.closest('.sb-fk');
    if (x) openLookup(x.dataset.t, x.dataset.c, x.dataset.v, path);
  };
}

// ---------------------------------------------------------------------------
// SQL tab
// ---------------------------------------------------------------------------
function showSQL(pane) {
  pane.innerHTML = html`<div class="sb-sql">
    <section class="card">
      <div class="sb-sql-bar">
        <select class="select" id="sb-ex" aria-label="Example queries">
          <option value="">Example queries…</option>
          ${S.examples.map((x) => html`<option value="${x.id}">${x.title}</option>`)}
        </select>
        <span class="sb-ro">${icon('lock', 14)}Read-only · 3 s limit · 1,000 rows</span>
        <span class="spacer"></span>
        <button class="btn btn-ghost sm" type="button" id="sb-clear">Clear</button>
        <button class="btn btn-primary" type="button" id="sb-run">${icon('play', 14)}<span>Run</span><span class="kbd sb-kbd">${RUN_KEY}</span></button>
      </div>
      <p class="sb-ex-desc" id="sb-ex-desc" hidden></p>
      <textarea class="textarea sb-editor" id="sb-editor" spellcheck="false" autocapitalize="off" autocomplete="off" aria-label="SQL editor">${S.sql}</textarea>
      <p class="sb-hint muted small">Tables are joinable by their foreign keys. Try the examples: recursive CTEs over <span class="mono">genealogy</span> and <span class="mono">bom_line</span>, lineage from <span class="mono">raw_cm_mes_event</span>, the closed-loop audit trail.</p>
    </section>
    <div id="sb-result"></div>
  </div>`;
  const ed = $('#sb-editor', pane);
  const ex = $('#sb-ex', pane);
  const desc = $('#sb-ex-desc', pane);
  ed.addEventListener('input', () => { S.sql = ed.value; saveSQL(S.sql); });
  ed.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      runQuery();
    } else if (e.key === 'Tab' && !e.shiftKey) {
      e.preventDefault();
      const s = ed.selectionStart;
      ed.setRangeText('  ', s, ed.selectionEnd, 'end');
      S.sql = ed.value;
    }
  });
  ex.addEventListener('change', () => {
    const x = S.examples.find((q) => q.id === ex.value);
    if (!x) { desc.hidden = true; return; }
    ed.value = x.sql;
    S.sql = x.sql;
    saveSQL(S.sql);
    desc.hidden = false;
    desc.textContent = x.description;
    runQuery();
  });
  $('#sb-run', pane).addEventListener('click', runQuery);
  $('#sb-clear', pane).addEventListener('click', () => {
    ed.value = '';
    S.sql = '';
    saveSQL('');
    ed.focus();
  });
  if (S.lastResult) renderResult(S.lastResult);
}

async function runQuery() {
  const ed = $('#sb-editor', S.el);
  const out = $('#sb-result', S.el);
  if (!ed || !out) return;
  const sql = ed.value.trim();
  const btn = $('#sb-run', S.el);
  btn.disabled = true;
  out.classList.add('refetching');
  try {
    const res = await api.post('/api/sandbox/sql', { sql });
    S.lastResult = { ok: true, res, sql };
  } catch (err) {
    S.lastResult = { ok: false, err, sql };
  } finally {
    if (btn) btn.disabled = false;
    out.classList.remove('refetching');
  }
  if (S) renderResult(S.lastResult);
}

function renderResult(r, out = $('#sb-result', S.el)) {
  if (!out) return;
  if (!r.ok) {
    out.innerHTML = String(ui.callout({ tone: 'critical', title: 'Query failed', body: html`<span class="mono small">${r.err.message}</span>` }));
    return;
  }
  const { res } = r;
  out.innerHTML = html`<section class="card flush">
    <header class="card-head">
      <div class="card-head-text"><h3 class="card-title">Result</h3>
        <p class="card-sub">${fmt.int(res.row_count)} row${res.row_count === 1 ? '' : 's'} · ${fmt.ms(res.ms)}${res.truncated ? ' · truncated at 1,000 rows' : ''}</p></div>
      ${res.truncated ? html`<div class="card-actions">${ui.chip('warning', 'Truncated')}</div>` : ''}
    </header>
    <div class="sb-result-table"></div>
  </section>`;
  const holder = $('.sb-result-table', out);
  if (!res.columns.length) {
    holder.innerHTML = String(ui.empty('The statement ran and returned no columns.'));
    return;
  }
  const numericCol = res.columns.map((c, i) => res.rows.length > 0 && res.rows.every((row) => row[i] == null || typeof row[i] === 'number'));
  ui.dataTable(holder, {
    columns: res.columns.map((c, i) => ({
      key: i,
      label: c,
      num: numericCol[i],
      mono: !numericCol[i],
      value: (row) => row[i],
      render: (row) => {
        const v = row[i];
        if (v == null) return html`<span class="nil">null</span>`;
        if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
        const s = String(v);
        return s.length > 80 ? html`<span title="${s}">${s.slice(0, 79)}…</span>` : s;
      },
    })),
    rows: res.rows,
    pageSize: 100,
    search: res.rows.length > 10,
    empty: 'The query returned no rows.',
    maxHeight: 560,
    dense: true,
  });
}

// ---------------------------------------------------------------------------
// Relationships tab
// ---------------------------------------------------------------------------
async function showERD(pane) {
  const cats = [...new Set(S.useCases.map((u) => u.category))];
  pane.innerHTML = html`<div class="sb-erd stack">
    <section class="card sb-uc-card">
      <div class="sb-uc-bar">
        <label class="sb-uc-label" for="sb-uc">${icon('filter', 15)}<span>Use case</span></label>
        <select class="select" id="sb-uc">
          <option value="">All tables</option>
          ${cats.map((c) => html`<optgroup label="${c}">${S.useCases.filter((u) => u.category === c).map((u) => html`<option value="${u.id}"${u.id === S.uc ? ' selected' : ''}>${u.title}</option>`)}</optgroup>`)}
        </select>
        <span class="muted small sb-uc-hint">Pick a question: the map lights up the tables it walks through, in order, and below is the query that answers it.</span>
      </div>
      <div id="sb-uc-detail"></div>
    </section>
    <section class="card">
      <header class="card-head">
        <div class="card-head-text"><h3 class="card-title">Schema map</h3><p class="card-sub">Every table, in lanes by layer and domain. Click one to trace its foreign keys across the model.</p></div>
        <div class="card-actions erd-legend">
          <span class="legend-item"><span class="legend-key line" style="--c:var(--series-1)"></span>references (parent)</span>
          <span class="legend-item"><span class="legend-key line" style="--c:var(--series-2)"></span>referenced by (child)</span>
        </div>
      </header>
      <div class="erd-wrap" id="erd-wrap"><div class="erd-lanes" id="erd-lanes">${ui.loading('Reading the schema')}</div><svg class="erd-edges" id="erd-edges" aria-hidden="true"></svg></div>
    </section>
    <section class="card" id="erd-focus-card">
      <header class="card-head">
        <div class="card-head-text"><h3 class="card-title" id="erd-focus-title">Relationships</h3><p class="card-sub">Parents on the left (tables it references), children on the right (tables that reference it), joined column to column.</p></div>
        <div class="card-actions">${ui.button({ label: 'Open in table browser', icon: 'table', size: 'sm', attrs: { 'data-act': 'open-table' } })}</div>
      </header>
      <div id="erd-focus-meta"></div>
      <div class="erd-focus-wrap" id="erd-focus"></div>
      <div id="erd-sample"></div>
    </section>
  </div>`;
  if (!S.erd) {
    try {
      S.erd = await api.get('/api/sandbox/erd');
    } catch (err) {
      $('#erd-lanes', pane).innerHTML = String(ui.errorBox(err));
      return;
    }
  }
  if (!S) return;
  const erd = S.erd;
  S.erdBy = new Map(erd.tables.map((t) => [t.name, t]));
  const lanes = LANES.map((l) => ({ ...l, list: erd.tables.filter(l.test) })).filter((l) => l.list.length);
  $('#erd-lanes', pane).innerHTML = html`${lanes.map((l) => html`<div class="erd-lane">
    <div class="erd-lane-head"><span>${l.label}</span><small>${l.sub}</small></div>
    ${l.list.map((t) => html`<button class="erd-box${t.kind === 'view' ? ' view' : ''}" type="button" data-table="${t.name}" title="${t.description}">
      <span class="erd-name">${wrapName(t.name)}</span><span class="erd-rows">${t.rows == null ? 'view' : fmt.compact(t.rows)}</span>
    </button>`)}
  </div>`)}`;
  on(pane, 'click', '.erd-box', (e, b) => focusTable(b.dataset.table));
  $('#sb-uc', pane).addEventListener('change', (e) => setUseCase(e.target.value || null));
  on(pane, 'click', '.sb-uc-step', (e, b) => focusTable(b.dataset.table));
  on(pane, 'click', '[data-act=uc-run]', () => runUseCase());
  on(pane, 'click', '[data-act=uc-sql]', () => {
    const u = currentUseCase();
    if (!u) return;
    S.sql = u.sql;
    saveSQL(S.sql);
    S.tabs.set('sql');
    showTab('sql');
    runQuery();
  });
  on(pane, 'click', '[data-act=open-table]', () => {
    S.tabs.set('tables');
    showTab('tables');
  });
  on(pane, 'click', '.erd-node', (e, g) => focusTable(g.dataset.table));
  on(pane, 'keydown', '.erd-node', (e, g) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); focusTable(g.dataset.table); } });

  const wrap = $('#erd-wrap', pane);
  const focusEl = $('#erd-focus', pane);
  let lastW = 0;
  S.ro = new ResizeObserver(() => {
    drawEdges();
    const w = focusEl.clientWidth;
    if (Math.abs(w - lastW) > 2) { lastW = w; drawFocus(); }
  });
  S.ro.observe(wrap);
  S.ro.observe(focusEl);
  const u = currentUseCase();
  const start = u && !u.path.some((st) => st.table === S.sel) ? u.path[0].table : S.sel;
  renderUseCase();
  focusTable(S.erdBy.has(start) ? start : DEFAULT_TABLE, { initial: true });
}

// --- use cases: a question, the tables it walks through in order, and the query that answers it
function currentUseCase() {
  return S.uc ? S.useCases.find((u) => u.id === S.uc) || null : null;
}

function setUseCase(id) {
  S.uc = id;
  S.ctx.setQuery({ uc: id }, { silent: true });
  renderUseCase();
  const u = currentUseCase();
  focusTable(u ? u.path[0].table : S.sel);
}

function renderUseCase() {
  const holder = $('#sb-uc-detail', S.el);
  if (!holder) return;
  const u = currentUseCase();
  if (!u) { holder.innerHTML = ''; return; }
  holder.innerHTML = html`<div class="sb-uc">
    <div class="sb-uc-head">
      <div><h3 class="sb-uc-title">${u.title}</h3><p class="sb-uc-q">${u.question}</p></div>
      ${u.page ? html`<a class="btn sm" href="${u.page}">${icon('external', 14)}<span>See it in the app</span></a>` : ''}
    </div>
    <ol class="sb-uc-path">${u.path.map((st, i) => html`<li>
      <button class="sb-uc-step" type="button" data-table="${st.table}"><span class="sb-uc-n">${i + 1}</span><span class="mono">${st.table}</span></button>
      <div class="sb-uc-text">
        <span class="sb-uc-join">${i === 0 ? 'Starts here' : html`Joins on <span class="mono">${st.join}</span>`}</span>
        <span>${st.why}</span>
        ${st.grain ? html`<span class="sb-uc-grain"><span class="sb-grain-tag">Each row</span>${st.grain}</span>` : ''}
      </div>
    </li>`)}</ol>
    <div class="sb-uc-sql">
      <div class="sb-uc-sql-head"><span class="section-title">Query</span><span class="spacer"></span>
        <button class="btn sm" type="button" data-act="uc-sql">${icon('code', 14)}<span>Open in SQL tab</span></button>
        <button class="btn btn-primary sm" type="button" data-act="uc-run">${icon('play', 14)}<span>Run</span></button></div>
      ${ui.codeBlock(u.sql, 'sql')}
    </div>
    <div id="sb-uc-result"></div>
  </div>`;
}

async function runUseCase() {
  const u = currentUseCase();
  const out = $('#sb-uc-result', S.el);
  if (!u || !out) return;
  out.classList.add('refetching');
  let r;
  try {
    r = { ok: true, res: await api.post('/api/sandbox/sql', { sql: u.sql }), sql: u.sql };
  } catch (err) {
    r = { ok: false, err, sql: u.sql };
  }
  if (!S || S.uc !== u.id) return;
  out.classList.remove('refetching');
  renderResult(r, out);
}

// the clicked table: what one row is, its key, and a few real rows
async function renderFocusMeta(name) {
  const meta = $('#erd-focus-meta', S.el);
  const sample = $('#erd-sample', S.el);
  if (!meta || !sample) return;
  const t = S.erdBy.get(name);
  meta.innerHTML = html`${grainLine(t)}${rowKeyLine(t)}
    <p class="sb-facts erd-meta-facts"><span><strong>${t.rows == null ? 'view' : fmt.int(t.rows)}</strong>${t.rows == null ? '' : ' rows'}</span>
      <span><strong>${t.columns.length}</strong> columns</span></p>
    ${usedIn(name)}`;
  sample.innerHTML = String(ui.loading('Reading rows'));
  let res;
  try {
    res = await api.get(`/api/sandbox/rows/${encodeURIComponent(name)}`, { limit: 5, offset: 0 });
  } catch (err) {
    sample.innerHTML = String(ui.errorBox(err));
    return;
  }
  if (!S || S.sel !== name) return;
  sample.innerHTML = res.rows.length ? html`<div class="section-title">First ${res.rows.length} rows</div>
    <div class="table-wrap erd-sample-wrap"><table class="table dense sb-rows">
      <thead><tr>${res.columns.map((c) => html`<th>${c}</th>`)}</tr></thead>
      <tbody>${res.rows.map((r) => html`<tr>${r.map((v) => {
        const txt = cellText(v);
        if (txt == null) return html`<td><span class="nil">null</span></td>`;
        return html`<td class="${typeof v === 'number' ? 'num' : ''}" title="${txt.length > 40 ? txt : ''}">${txt.length > 40 ? `${txt.slice(0, 39)}…` : txt}</td>`;
      })}</tr>`)}</tbody></table></div>` : String(ui.empty('This table is empty in the current dataset.'));
}

function neighbours(name) {
  const edges = S.erd.edges;
  const parents = new Map();
  const children = new Map();
  const self = [];
  for (const e of edges) {
    if (e.from_table === name && e.to_table === name) { self.push(e); continue; }
    if (e.from_table === name) {
      if (!parents.has(e.to_table)) parents.set(e.to_table, []);
      parents.get(e.to_table).push(e);
    } else if (e.to_table === name) {
      if (!children.has(e.from_table)) children.set(e.from_table, []);
      children.get(e.from_table).push(e);
    }
  }
  return { parents, children, self };
}

function focusTable(name, { initial } = {}) {
  if (!S || !S.erdBy || !S.erdBy.has(name)) return;
  S.sel = name;
  S.ctx.setQuery({ table: name }, { silent: true });
  const { parents, children } = neighbours(name);
  const u = currentUseCase();
  const steps = u ? new Map(u.path.map((st, i) => [st.table, i + 1])) : null;
  $$('.erd-box', S.el).forEach((b) => {
    const t = b.dataset.table;
    b.classList.toggle('focus', t === name);
    b.classList.toggle('parent', !u && parents.has(t));
    b.classList.toggle('child', !u && children.has(t) && !parents.has(t));
    b.classList.toggle('uc', !!u && steps.has(t));
    b.classList.toggle('dim', u ? !steps.has(t) && t !== name : t !== name && !parents.has(t) && !children.has(t));
    const old = b.querySelector('.erd-step');
    if (old) old.remove();
    if (u && steps.has(t)) b.insertAdjacentHTML('afterbegin', `<span class="erd-step">${steps.get(t)}</span>`);
  });
  renderFocusMeta(name);
  const title = $('#erd-focus-title', S.el);
  if (title) {
    title.innerHTML = html`<span class="mono">${name}</span> <span class="muted small">· ${parents.size} parent${parents.size === 1 ? '' : 's'} · ${children.size} child${children.size === 1 ? '' : 'ren'}</span>`;
  }
  drawEdges();
  drawFocus();
  if (!initial) {
    const card = $('#erd-focus-card', S.el);
    if (card && card.getBoundingClientRect().top > window.innerHeight) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function drawEdges() {
  if (!S || !S.erd) return;
  const wrap = $('#erd-wrap', S.el);
  const svg = $('#erd-edges', S.el);
  if (!wrap || !svg) return;
  const name = S.sel;
  const base = wrap.getBoundingClientRect();
  const W = wrap.scrollWidth;
  const H = wrap.scrollHeight;
  svg.setAttribute('width', W);
  svg.setAttribute('height', H);
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const focusBox = $(`.erd-box[data-table="${CSS.escape(name)}"]`, wrap);
  if (!focusBox) { svg.innerHTML = ''; return; }
  const rel = (el) => {
    const r = el.getBoundingClientRect();
    return {
      l: r.left - base.left + wrap.scrollLeft, r: r.right - base.left + wrap.scrollLeft,
      t: r.top - base.top + wrap.scrollTop, b: r.bottom - base.top + wrap.scrollTop,
      cx: r.left + r.width / 2 - base.left + wrap.scrollLeft, cy: r.top + r.height / 2 - base.top + wrap.scrollTop,
    };
  };
  const u = currentUseCase();
  if (u) { svg.innerHTML = useCaseEdges(u, wrap, rel); return; }
  const f = rel(focusBox);
  const { parents, children } = neighbours(name);
  const path = (a, b) => {
    if (Math.abs(a.cx - b.cx) < 6) {
      // same lane: hug the lane's right edge so the loop stays in the gutter
      const dx = 9 + Math.min(5, Math.abs(a.cy - b.cy) / 60);
      return `M${a.r},${a.cy} C${a.r + dx},${a.cy} ${b.r + dx},${b.cy} ${b.r},${b.cy}`;
    }
    const x1 = b.cx > a.cx ? a.r : a.l;
    const x2 = b.cx > a.cx ? b.l : b.r;
    const mx = (x1 + x2) / 2;
    return `M${x1},${a.cy} C${mx},${a.cy} ${mx},${b.cy} ${x2},${b.cy}`;
  };
  let s = `<defs>
    <marker id="erd-arrow-p" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" style="fill:var(--series-1)"/></marker>
    <marker id="erd-arrow-c" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" style="fill:var(--series-2)"/></marker>
  </defs>`;
  for (const t of parents.keys()) {
    const el = $(`.erd-box[data-table="${CSS.escape(t)}"]`, wrap);
    if (!el) continue;
    s += `<path class="erd-edge" d="${path(f, rel(el))}" style="stroke:var(--series-1)" marker-end="url(#erd-arrow-p)"/>`;
  }
  for (const t of children.keys()) {
    if (parents.has(t)) continue;
    const el = $(`.erd-box[data-table="${CSS.escape(t)}"]`, wrap);
    if (!el) continue;
    s += `<path class="erd-edge" d="${path(rel(el), f)}" style="stroke:var(--series-2)" marker-end="url(#erd-arrow-c)"/>`;
  }
  svg.innerHTML = s;
}

// the use case's path on the map: step to step, solid where a foreign key joins them, dashed where the join is
// by value (a date range or a text reference) rather than a declared key
function useCaseEdges(u, wrap, rel) {
  let s = `<defs><marker id="erd-arrow-u" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" style="fill:var(--sign)"/></marker></defs>`;
  for (let i = 1; i < u.path.length; i += 1) {
    const a = $(`.erd-box[data-table="${CSS.escape(u.path[i - 1].table)}"]`, wrap);
    const b = $(`.erd-box[data-table="${CSS.escape(u.path[i].table)}"]`, wrap);
    if (!a || !b) continue;
    const ta = u.path[i - 1].table;
    const tb = u.path[i].table;
    const keyed = S.erd.edges.some((e) => (e.from_table === ta && e.to_table === tb) || (e.from_table === tb && e.to_table === ta));
    const p = rel(a);
    const q = rel(b);
    let d;
    if (Math.abs(p.cx - q.cx) < 6) {
      const dx = 12;
      d = `M${p.r},${p.cy} C${p.r + dx},${p.cy} ${q.r + dx},${q.cy} ${q.r},${q.cy}`;
    } else {
      const x1 = q.cx > p.cx ? p.r : p.l;
      const x2 = q.cx > p.cx ? q.l : q.r;
      const mx = (x1 + x2) / 2;
      d = `M${x1},${p.cy} C${mx},${p.cy} ${mx},${q.cy} ${x2},${q.cy}`;
    }
    s += `<path class="erd-edge uc${keyed ? '' : ' by-value'}" d="${d}" marker-end="url(#erd-arrow-u)"/>`;
  }
  return s;
}

function drawFocus() {
  if (!S || !S.erd) return;
  const holder = $('#erd-focus', S.el);
  if (!holder) return;
  const name = S.sel;
  const t = S.erdBy.get(name);
  if (!t) { holder.innerHTML = ''; return; }
  const { parents, children, self } = neighbours(name);
  const W = Math.max(760, holder.clientWidth || 900);
  const ROW = 21;
  const HEAD = 32;
  const GAP = 14;
  const fW = 300;
  const sideW = Math.min(250, Math.max(180, (W - fW) / 2 - 70));
  const fx = (W - fW) / 2;
  const fy = 12;
  const fH = HEAD + t.columns.length * ROW + 8;
  const colIndex = new Map(t.columns.map((c, i) => [c.name, i]));
  const rowY = (top, i) => top + HEAD + i * ROW + ROW / 2 + 2;

  // Order boxes by the focus row they connect to, so links cross as little as possible.
  const pList = [...parents.entries()].map(([pt, es]) => {
    const cols = [...new Set(es.map((e) => e.to_col))];
    const at = Math.min(...es.map((e) => colIndex.get(e.from_col) ?? 0));
    return { name: pt, es, cols, at, h: HEAD + cols.length * ROW + 8 };
  }).sort((a, b) => a.at - b.at || a.name.localeCompare(b.name));
  const cList = [...children.entries()].map(([ct, es]) => {
    const cols = [...new Set(es.map((e) => e.from_col))];
    const at = Math.min(...es.map((e) => colIndex.get(e.to_col) ?? 0));
    return { name: ct, es, cols, at, h: HEAD + cols.length * ROW + 8 };
  }).sort((a, b) => a.at - b.at || a.name.localeCompare(b.name));
  const stackH = (list) => list.reduce((a, x) => a + x.h, 0) + Math.max(0, list.length - 1) * GAP;
  const place = (list) => {
    const total = stackH(list);
    let y = total < fH ? fy + (fH - total) / 2 : fy;
    for (const x of list) { x.y = y; y += x.h + GAP; }
  };
  place(pList);
  place(cList);
  const H = Math.max(fy + fH, fy + stackH(pList), fy + stackH(cList)) + 16;
  const px = 12;
  const cx = W - 12 - sideW;

  const box = (x, y, w, h, title, rows, kind) => {
    let s = `<g class="erd-node ${kind}" data-table="${esc(title)}"${kind === 'focus' ? '' : ' tabindex="0" role="button"'}>`;
    s += `<rect class="erd-node-box" x="${x}" y="${y}" width="${w}" height="${h}" rx="8"/>`;
    s += `<path class="erd-node-head" d="M${x},${y + 8} a8,8 0 0 1 8,-8 h${w - 16} a8,8 0 0 1 8,8 v${HEAD - 8} h${-w} z"/>`;
    const tbl = S.erdBy.get(title);
    const rowsTxt = tbl && tbl.rows != null ? fmt.compact(tbl.rows) : (tbl && tbl.kind === 'view' ? 'view' : '');
    s += `<text class="erd-node-title" x="${x + 12}" y="${y + 20}">${esc(title)}</text>`;
    s += `<text class="erd-node-rows" x="${x + w - 12}" y="${y + 20}" text-anchor="end">${esc(rowsTxt)}</text>`;
    rows.forEach((r, i) => {
      const ry = rowY(y, i);
      if (i % 2 === 1) s += `<rect class="erd-row-alt" x="${x + 1}" y="${ry - ROW / 2}" width="${w - 2}" height="${ROW}"/>`;
      s += `<text class="erd-col${r.strong ? ' strong' : ''}" x="${x + 12}" y="${ry}" dy="0.34em">${esc(r.name)}</text>`;
      if (r.tag) s += `<text class="erd-tag ${r.tagCls || ''}" x="${x + w - 12}" y="${ry}" dy="0.34em" text-anchor="end">${esc(r.tag)}</text>`;
    });
    return `${s}</g>`;
  };

  let s = '';
  // edges first so boxes sit on top
  for (const p of pList) {
    for (const e of p.es) {
      const y1 = rowY(p.y, p.cols.indexOf(e.to_col));
      const y2 = rowY(fy, colIndex.get(e.from_col) ?? 0);
      const x1 = px + sideW;
      const x2 = fx;
      const mx = (x1 + x2) / 2;
      s += `<path class="erd-link" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" style="stroke:var(--series-1)"/>`;
      s += `<circle class="erd-link-dot" cx="${x2}" cy="${y2}" r="3" style="fill:var(--series-1)"/>`;
    }
  }
  for (const c of cList) {
    for (const e of c.es) {
      const y1 = rowY(fy, colIndex.get(e.to_col) ?? 0);
      const y2 = rowY(c.y, c.cols.indexOf(e.from_col));
      const x1 = fx + fW;
      const x2 = cx;
      const mx = (x1 + x2) / 2;
      s += `<path class="erd-link" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" style="stroke:var(--series-2)"/>`;
      s += `<circle class="erd-link-dot" cx="${x2}" cy="${y2}" r="3" style="fill:var(--series-2)"/>`;
    }
  }
  for (const p of pList) s += box(px, p.y, sideW, p.h, p.name, p.cols.map((c) => ({ name: c, tag: 'PK', tagCls: 'pk' })), 'parent');
  for (const c of cList) s += box(cx, c.y, sideW, c.h, c.name, c.cols.map((col) => ({ name: col, tag: 'FK', tagCls: 'fk' })), 'child');
  s += box(fx, fy, fW, fH, name, t.columns.map((c) => ({
    name: c.name,
    strong: !!c.pk || !!c.fk,
    tag: c.pk ? 'PK' : c.fk ? `FK → ${c.fk.table}` : (c.type || '').toLowerCase(),
    tagCls: c.pk ? 'pk' : c.fk ? 'fk' : 'type',
  })), 'focus');
  if (!pList.length) s += `<text class="erd-empty" x="${px + sideW / 2}" y="${fy + 24}" text-anchor="middle">no parents</text>`;
  if (!cList.length) s += `<text class="erd-empty" x="${cx + sideW / 2}" y="${fy + 24}" text-anchor="middle">no children</text>`;
  const selfNote = self.length ? html`<p class="muted small erd-self">Self-reference: ${self.map((e) => html`<span class="mono">${e.from_col} → ${e.to_col}</span>`)} (a hierarchy inside one table).</p>` : '';
  holder.innerHTML = html`<div class="erd-focus-scroll"><svg class="erd-focus-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Relationships of ${name}">${raw(s)}</svg></div>${selfNote}`;
}

// ---------------------------------------------------------------------------
// page styles
// ---------------------------------------------------------------------------
const PAGE_CSS = `
.pg-sandbox .sb-facts-line { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink-2); margin: -6px 0 12px; }
.pg-sandbox .sb-facts-line strong { color: var(--ink); font-weight: 600; }
.pg-sandbox .sb-pipe { display: flex; align-items: stretch; gap: 8px; margin-bottom: 18px; }
.pg-sandbox .sb-pipe-arrow { display: flex; align-items: center; color: var(--ink-3); flex: none; }
.pg-sandbox .sb-pipe-gap { flex: none; width: 10px; border-left: 1px dashed var(--hairline-strong); margin: 6px 4px 6px 8px; }
.pg-sandbox .sb-layer { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; gap: 5px; text-align: left; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--hairline); background: var(--surface); box-shadow: var(--shadow-sm); color: var(--ink); transition: border-color .12s, transform .12s; }
.pg-sandbox .sb-layer:hover { border-color: var(--hairline-strong); transform: translateY(-1px); }
.pg-sandbox .sb-layer-side { background: var(--surface-2); }
.pg-sandbox .sb-layer-top { display: flex; align-items: center; gap: 7px; color: var(--ink-2); }
.pg-sandbox .sb-layer-name { font: 600 14.5px/1 var(--font-cond); letter-spacing: .02em; color: var(--ink); text-transform: uppercase; }
.pg-sandbox .sb-layer-count { margin-left: auto; font-size: 12px; color: var(--ink-3); white-space: nowrap; }
.pg-sandbox .sb-layer-blurb { font-size: 12.5px; color: var(--ink-2); line-height: 1.35; }
.pg-sandbox .sb-layer-rows { font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; margin-top: auto; }

.pg-sandbox .layer-tag { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--hairline-strong); font: 600 11px/1 var(--font-cond); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-2); }

.pg-sandbox .sb-split { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 16px; align-items: start; }
.pg-sandbox .sb-list { position: sticky; top: calc(var(--topbar-h) + 12px); max-height: calc(100vh - var(--topbar-h) - 28px); display: flex; flex-direction: column; }
.pg-sandbox .sb-list-head { padding: 12px 12px 8px; border-bottom: 1px solid var(--hairline); display: grid; gap: 8px; }
.pg-sandbox .sb-list-head .dt-search, .pg-sandbox .sb-list-head .dt-search .input { width: 100%; }
.pg-sandbox .sb-chips:empty { display: none; }
.pg-sandbox .sb-layer-chip { cursor: pointer; }
.pg-sandbox .sb-groups { overflow: auto; padding: 4px 8px 12px; }
.pg-sandbox .sb-group-head { display: flex; align-items: center; gap: 6px; padding: 14px 6px 4px; font: 600 11.5px/1 var(--font-cond); letter-spacing: .1em; text-transform: uppercase; color: var(--ink); }
.pg-sandbox .sb-group-head .muted { margin-left: auto; font-family: var(--font-ui); letter-spacing: 0; }
.pg-sandbox .sb-domain { padding: 8px 8px 3px; font-size: 11px; color: var(--ink-3); }
.pg-sandbox .sb-item { width: 100%; display: flex; align-items: center; gap: 8px; min-height: 28px; padding: 4px 8px; border: 0; border-radius: 7px; background: none; text-align: left; color: var(--ink); }
.pg-sandbox .sb-item:hover { background: var(--row-hover); }
.pg-sandbox .sb-item.active { background: color-mix(in srgb, var(--sign) 12%, var(--surface)); box-shadow: inset 2px 0 0 var(--tab-bar); }
.pg-sandbox .sb-item-name { flex: 1; min-width: 0; font: 500 12.5px/1.3 var(--font-mono); overflow-wrap: anywhere; }
.pg-sandbox .sb-item-count { font-size: 11.5px; color: var(--ink-3); font-variant-numeric: tabular-nums; }

.pg-sandbox .sb-detail { min-width: 0; display: grid; gap: 16px; }
.pg-sandbox .sb-title-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.pg-sandbox .sb-name { font: 600 22px/1.15 var(--font-mono); letter-spacing: -.01em; overflow-wrap: anywhere; margin-right: 4px; }
.pg-sandbox .sb-desc { margin-top: 8px; color: var(--ink-2); font-size: 14px; max-width: 760px; }
.pg-sandbox .sb-facts { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 12px; font-size: 12.5px; color: var(--ink-3); }
.pg-sandbox .sb-facts strong { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.pg-sandbox .sb-cons { white-space: normal !important; padding-top: 6px !important; padding-bottom: 6px !important; }
.pg-sandbox .sb-cons .badge { margin: 2px 4px 2px 0; }
.pg-sandbox .sb-check { max-width: 280px; overflow: hidden; text-overflow: ellipsis; }
.pg-sandbox .sb-refs { list-style: none; padding: 0; display: grid; gap: 6px; font-size: 13px; }
.pg-sandbox .sb-refs li { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.pg-sandbox .sb-arrow { display: inline-flex; color: var(--ink-3); }
.pg-sandbox .sb-ref { display: inline-flex; align-items: baseline; border: 0; background: none; padding: 0; color: var(--link); cursor: pointer; font-size: 13px; }
.pg-sandbox .sb-ref:hover { text-decoration: underline; }

.pg-sandbox .sb-rows-wrap { max-height: 520px; }
.pg-sandbox .sb-rows td { font: 400 12px/1 var(--font-mono); }
.pg-sandbox .sb-rows th { text-transform: none; letter-spacing: 0; font: 600 12px/1 var(--font-mono); }
.pg-sandbox .sb-fk, .pg-drawer-fk { border: 0; background: none; padding: 0; color: var(--link); font: inherit; cursor: pointer; text-decoration: underline; text-decoration-color: color-mix(in srgb, var(--link) 35%, transparent); text-underline-offset: 2px; }
.pg-sandbox .sb-long { border: 0; background: none; padding: 0; font: inherit; color: var(--ink); cursor: pointer; text-align: left; }
.pg-sandbox .sb-long.json { color: var(--ink-2); }
.pg-sandbox .sb-long:hover { color: var(--link); }
.pg-sandbox .refetching { opacity: .55; transition: opacity .15s; }

.pg-sandbox .sb-sql-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
.pg-sandbox .sb-sql-bar .select { min-width: 280px; max-width: 100%; }
.pg-sandbox .sb-ro { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink-3); }
.pg-sandbox .sb-kbd { margin-left: 4px; background: transparent; color: inherit; border-color: color-mix(in srgb, currentColor 35%, transparent); }
.pg-sandbox .sb-ex-desc { margin: -2px 0 10px; font-size: 13px; color: var(--ink-2); }
.pg-sandbox .sb-editor { width: 100%; min-height: 240px; background: var(--code-bg); color: var(--code-ink); border-color: transparent; caret-color: var(--sign-yellow); }
.pg-sandbox .sb-editor:focus { background: var(--code-bg); border-color: var(--focus); }
.pg-sandbox .sb-hint { margin-top: 10px; }
.pg-sandbox #sb-result { margin-top: 16px; }

.pg-sandbox .erd-legend { gap: 14px; font-size: 12px; color: var(--ink-2); flex-wrap: wrap; justify-content: flex-end; }
.pg-sandbox .erd-wrap { position: relative; overflow-x: auto; }
.pg-sandbox .erd-lanes { position: relative; z-index: 1; display: grid; grid-template-columns: repeat(8, minmax(118px, 1fr)); gap: 10px; min-width: 1000px; }
.pg-sandbox .erd-lane { display: flex; flex-direction: column; gap: 5px; padding: 8px 6px 10px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--hairline); }
.pg-sandbox .erd-lane-head { padding: 2px 4px 6px; font: 600 12px/1.1 var(--font-cond); letter-spacing: .06em; text-transform: uppercase; color: var(--ink); }
.pg-sandbox .erd-lane-head small { display: block; margin-top: 3px; font: 400 11px/1.2 var(--font-ui); letter-spacing: 0; text-transform: none; color: var(--ink-3); }
.pg-sandbox .erd-box { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; width: 100%; padding: 6px 8px; border-radius: 7px; border: 1px solid var(--hairline-strong); background: var(--surface); color: var(--ink); text-align: left; transition: opacity .15s, border-color .15s, box-shadow .15s, background .15s; }
.pg-sandbox .erd-box:hover { border-color: var(--ink-3); }
.pg-sandbox .erd-box.view { border-style: dashed; }
.pg-sandbox .erd-name { font: 500 11px/1.25 var(--font-mono); overflow-wrap: anywhere; }
.pg-sandbox .erd-rows { font-size: 10.5px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.pg-sandbox .erd-box.dim { opacity: .42; }
.pg-sandbox .erd-box.focus { background: var(--sign); border-color: var(--sign); color: #fff; box-shadow: 0 0 0 2px color-mix(in srgb, var(--sign) 30%, transparent); opacity: 1; }
.pg-sandbox .erd-box.focus .erd-rows { color: rgba(255,255,255,.75); }
.pg-sandbox .erd-box.parent { border-color: var(--series-1); box-shadow: inset 3px 0 0 var(--series-1); opacity: 1; }
.pg-sandbox .erd-box.child { border-color: var(--series-2); box-shadow: inset 3px 0 0 var(--series-2); opacity: 1; }
.pg-sandbox .erd-box { position: relative; }
.pg-sandbox .erd-box.uc { border-color: var(--sign); box-shadow: inset 3px 0 0 var(--sign); opacity: 1; padding-left: 26px; }
.pg-sandbox .erd-box.uc.focus { box-shadow: 0 0 0 2px color-mix(in srgb, var(--sign) 30%, transparent); }
.pg-sandbox .erd-step { position: absolute; left: 6px; top: 6px; min-width: 15px; height: 15px; padding: 0 3px; display: inline-flex; align-items: center; justify-content: center; border-radius: 4px; background: var(--sign-yellow); color: #1a1a19; font: 700 10px/1 var(--font-ui); }
.pg-sandbox .erd-edge.uc { stroke: var(--sign); stroke-width: 2; opacity: .9; }
.pg-sandbox .erd-edge.uc.by-value { stroke-dasharray: 5 4; }
.pg-sandbox .erd-edges { position: absolute; left: 0; top: 0; z-index: 2; pointer-events: none; overflow: visible; }
.pg-sandbox .erd-edge { fill: none; stroke-width: 1.5; opacity: .85; }

.pg-sandbox .erd-focus-wrap { min-width: 0; }
.pg-sandbox .erd-focus-scroll { overflow-x: auto; }
.pg-sandbox .erd-focus-svg { display: block; }
.pg-sandbox .erd-node-box { fill: var(--surface); stroke: var(--hairline-strong); stroke-width: 1; }
.pg-sandbox .erd-node-head { fill: var(--surface-2); }
.pg-sandbox .erd-node.parent .erd-node-head { fill: color-mix(in srgb, var(--series-1) 14%, var(--surface)); }
.pg-sandbox .erd-node.child .erd-node-head { fill: color-mix(in srgb, var(--series-2) 14%, var(--surface)); }
.pg-sandbox .erd-node.focus .erd-node-head { fill: var(--sign); }
.pg-sandbox .erd-node.focus .erd-node-box { stroke: var(--sign); stroke-width: 1.5; }
.pg-sandbox .erd-node.focus .erd-node-title, .pg-sandbox .erd-node.focus .erd-node-rows { fill: #fff; }
.pg-sandbox .erd-node:not(.focus) { cursor: pointer; }
.pg-sandbox .erd-node:not(.focus):hover .erd-node-box, .pg-sandbox .erd-node:focus-visible .erd-node-box { stroke: var(--ink-3); }
.pg-sandbox .erd-node:focus { outline: none; }
.pg-sandbox .erd-row-alt { fill: var(--surface-2); opacity: .7; }
.pg-sandbox .erd-node-title { font: 600 12.5px var(--font-mono); fill: var(--ink); }
.pg-sandbox .erd-node-rows { font: 400 11px var(--font-ui); fill: var(--ink-3); }
.pg-sandbox .erd-col { font: 400 11.5px var(--font-mono); fill: var(--ink-2); }
.pg-sandbox .erd-col.strong { fill: var(--ink); font-weight: 500; }
.pg-sandbox .erd-tag { font: 500 10.5px var(--font-mono); fill: var(--ink-3); }
.pg-sandbox .erd-tag.pk { fill: color-mix(in srgb, var(--sign-yellow) 70%, var(--ink)); font-weight: 600; }
.pg-sandbox .erd-tag.fk { fill: var(--series-1); }
.pg-sandbox .erd-link { fill: none; stroke-width: 1.5; opacity: .9; }
.pg-sandbox .erd-link-dot { stroke: var(--surface); stroke-width: 1.5; }
.pg-sandbox .erd-empty { font: 400 12px var(--font-ui); fill: var(--ink-3); }
.pg-sandbox .erd-self { margin-top: 10px; }

.sb-trail { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; font-size: 12px; color: var(--ink-2); }
.sb-kv { padding: 12px 14px; border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface-2); }
.sb-kv dt { font-size: 12px; }
.sb-kv .code { margin-top: 2px; }
.drawer-body .sb-fk { border: 0; background: none; padding: 0; color: var(--link); font: 500 12.5px var(--font-mono); cursor: pointer; text-decoration: underline; text-decoration-color: color-mix(in srgb, var(--link) 35%, transparent); text-underline-offset: 2px; }

.pg-sandbox .sb-grain { display: flex; align-items: baseline; gap: 8px; margin-top: 10px; font-size: 13.5px; color: var(--ink); max-width: 860px; }
.pg-sandbox .sb-grain-tag { flex: none; padding: 2px 6px; border-radius: 4px; background: var(--sign); color: var(--sign-ink); font: 600 10px/1.3 var(--font-cond); letter-spacing: .1em; text-transform: uppercase; }
.pg-sandbox .sb-rowkey { margin-top: 8px; }
.pg-sandbox .sb-rowkey-body { display: grid; gap: 4px; min-width: 0; }
.pg-sandbox .sb-key-cols, .pg-sandbox .sb-key-group { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
.pg-sandbox .sb-key-group { display: inline-flex; min-width: 0; }
.pg-sandbox .sb-key { padding: 1px 6px; border-radius: 4px; border: 1px solid color-mix(in srgb, var(--series-3) 42%, transparent); background: color-mix(in srgb, var(--series-3) 11%, var(--surface)); font-size: 12.5px; line-height: 1.45; color: var(--ink); overflow-wrap: anywhere; }
.pg-sandbox .sb-key-sep { color: var(--ink-3); font-size: 12px; }
.pg-sandbox .sb-key-how { font-size: 12.5px; line-height: 1.45; color: var(--ink-2); }
.pg-sandbox .badge-rowkey { border-color: color-mix(in srgb, var(--series-3) 42%, transparent); background: color-mix(in srgb, var(--series-3) 11%, var(--surface)); color: var(--ink); }
.pg-sandbox .sb-used { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.pg-sandbox .sb-uc-chip { cursor: pointer; }
.pg-sandbox .sb-uc-chip:hover { border-color: var(--link); color: var(--link); }
.pg-sandbox .sb-uc-card { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
.pg-sandbox .sb-uc-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 12px; }
.pg-sandbox .sb-uc-label { display: inline-flex; align-items: center; gap: 6px; font: 600 13px/1 var(--font-cond); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-2); }
.pg-sandbox .sb-uc-bar .select { min-width: min(320px, 100%); max-width: 100%; }
.pg-sandbox .sb-uc-hint { flex: 1 1 260px; }
.pg-sandbox .sb-uc { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; padding-top: 14px; border-top: 1px solid var(--hairline); }
.pg-sandbox .sb-uc-sql { min-width: 0; }
.pg-sandbox .sb-uc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.pg-sandbox .sb-uc-title { font: 600 17px/1.2 var(--font-cond); }
.pg-sandbox .sb-uc-q { margin-top: 3px; color: var(--ink-2); font-size: 13.5px; }
.pg-sandbox .sb-uc-path { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.pg-sandbox .sb-uc-path li { display: grid; grid-template-columns: minmax(170px, 230px) minmax(0, 1fr); gap: 12px; align-items: start; padding: 8px 10px; border-radius: 10px; background: var(--surface-2); }
.pg-sandbox .sb-uc-step { display: inline-flex; align-items: center; gap: 8px; border: 0; background: none; padding: 0; color: var(--link); font-size: 13px; cursor: pointer; text-align: left; }
.pg-sandbox .sb-uc-step:hover .mono { text-decoration: underline; }
.pg-sandbox .sb-uc-n { flex: none; width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; border-radius: 5px; background: var(--sign-yellow); color: #1a1a19; font: 700 11px/1 var(--font-ui); }
.pg-sandbox .sb-uc-text { display: grid; gap: 3px; font-size: 13px; color: var(--ink); }
.pg-sandbox .sb-uc-join { font-size: 12px; color: var(--ink-3); }
.pg-sandbox .sb-uc-grain { display: flex; align-items: baseline; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.pg-sandbox .sb-uc-sql-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.pg-sandbox .erd-meta-facts { margin-top: 8px; }
.pg-sandbox #erd-focus-meta { margin: -4px 0 12px; }
.pg-sandbox #erd-sample { margin-top: 12px; }
.pg-sandbox .erd-sample-wrap { max-height: 260px; }

@media (max-width: 700px) {
  .pg-sandbox .sb-uc-path li { grid-template-columns: minmax(0, 1fr); gap: 6px; }
}

@media (max-width: 1100px) {
  .pg-sandbox .sb-split { grid-template-columns: minmax(0, 1fr); }
  .pg-sandbox .sb-list { position: static; max-height: 360px; }
  .pg-sandbox .sb-pipe { flex-wrap: wrap; }
  .pg-sandbox .sb-pipe-arrow, .pg-sandbox .sb-pipe-gap { display: none; }
  .pg-sandbox .sb-layer { flex: 1 1 200px; }
}
`;
