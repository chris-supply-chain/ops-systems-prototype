// Ops OS shell: navigation, hash router, top bar (search, clock), theme.
import { html, $, $$, on } from './lib/dom.js';
import { api } from './lib/api.js';
import { fmt } from './lib/format.js';
import { ui } from './lib/ui.js';
import { icon } from './lib/icons.js';
import './lib/charts.js'; // installs chart hover, resize and table-view handlers

// ---------------------------------------------------------------------------
// Pages: one entry per page module. `goal` is the goal the page answers;
// `lede` is the one-liner under the title (placeholders use both).
// ---------------------------------------------------------------------------
const PAGES = {
  tower: { label: 'Control Tower', module: 'control-tower', icon: 'radar',
    lede: 'One picture from CM line to customer door: what is built, moving, stuck, and what needs a decision now.',
    goal: 'Build end-to-end visibility from factory to customer: WIP, first-pass yield, as-built genealogy from module to vehicle, inventory down to supplier-held stock, and shipment status.' },
  loop: { label: 'Closed Loop', module: 'closed-loop', icon: 'loop',
    lede: 'Sense, decide, act, learn. Each loop proposes a decision with its evidence, executes it as writes to real tables, and shows what changed.',
    goal: 'Turn it into scalable systems and close the loop so decisions happen within them.' },
  systems: { label: 'System Landscape', module: 'systems', icon: 'layers',
    lede: 'Every system in the loop (CM MES, QMS, ERP, TMS, 3PL WMS, supplier portals, email and Excel) with its owner, channel, latency and what it feeds.',
    goal: 'Integrate QMS, TMS, WMS and ERP into one data platform, and know when a package earns its place and when it is the slow path.' },
  production: { label: 'Production & Yield', module: 'production', icon: 'factory',
    lede: 'WIP by station, first-pass and rolled throughput yield, output against plan, for the CM line and the OEM\'s pack line.',
    goal: 'Build end-to-end visibility from factory to customer: WIP, first-pass yield…' },
  genealogy: { label: 'Genealogy', module: 'genealogy', icon: 'tree',
    lede: 'As-built trace from lot to module to vehicle to customer, forward and backward, with recall scope in one query.',
    goal: 'As-built genealogy from module to vehicle.' },
  'cm-feed': { label: 'CM Feed', module: 'cm-feed', icon: 'feed',
    lede: 'The visibility feed out of the CM\'s own MES: raw payloads, versioned mappings, quarantine and replay.',
    goal: 'A visibility feed out of the contract manufacturer\'s own systems.' },
  schedule: { label: 'Line Schedule', module: 'line-schedule', icon: 'gantt',
    lede: 'Every line, every day: the committed build against capacity and time fences, what actually ran, and the dispatch list for the next shift.',
    goal: 'One planning engine across both supply chains: what the contract manufacturer builds and what the OEM assembles itself.' },
  mps: { label: 'Master Schedule & Capacity', module: 'mps', icon: 'target',
    lede: 'The master schedule against line capacity, time fences and downtime, with capable-to-promise for what is not built yet.',
    goal: 'One planning engine across both supply chains: what the contract manufacturer builds and what the OEM assembles itself.' },
  mrp: { label: 'Material Plan · MRP', module: 'mrp', icon: 'grid',
    lede: 'Multi-level MRP at SKU-by-day across both supply chains, with the line-stop date and the action that prevents it.',
    goal: 'Material planning at SKU-by-day.' },
  atp: { label: 'ATP & Queues', module: 'atp', icon: 'clock',
    lede: 'Available-to-promise pegged to real supply, plus the build and fulfillment queues it drives.',
    goal: 'Available-to-promise, and the build and fulfillment queues.' },
  replenishment: { label: 'Replenishment Planning', module: 'replenishment', icon: 'refresh',
    lede: 'Replenishment policy by item and site (MRP, reorder point, min-max, VMI, consignment), with safety stock set from demand and lead-time variability.',
    goal: 'Build the planning engine across both supply chains: material planning at SKU-by-day, available-to-promise, and the build and fulfillment queues.' },
  inventory: { label: 'Inventory Position', module: 'inventory', icon: 'boxes',
    lede: 'Multi-echelon position from supplier-held stock to the 3PL shelf, by owner and status, reconciled against the WMS.',
    goal: 'Inventory down to supplier-held stock.' },
  shipments: { label: 'Shipments & Customs', module: 'shipments', icon: 'ship',
    lede: 'Booking, importation and track-and-trace from CM to 3PL to customer, with ETA risk and customs holds.',
    goal: 'Run the physical moves: booking, importation and track-and-trace from CM to 3PL to customer.' },
  suppliers: { label: 'Supplier Loop', module: 'suppliers', icon: 'exchange',
    lede: 'PO confirmations and promise dates, RFQs, engineering changes, forecast to tiers 1-3 and pricing with effectivity.',
    goal: 'Run the supplier loop: PO confirmations, promise dates, RFQs, design changes, the forecast released to tiers 1 through 3, and component pricing with effectivity.' },
  erp: { label: 'ERP Core', module: 'erp', icon: 'ledger',
    lede: 'The ERP slice the loop touches: item master, BOMs, POs, goods receipts, three-way match, invoices, FX, and the journal entries chargebacks post.',
    goal: 'A defect traced to a supplier chargeback that lands in the financial system.' },
  quality: { label: 'Quality Loop', module: 'quality', icon: 'shield',
    lede: 'Incoming and inline quality, deviations and trends, with control limits instead of anecdotes.',
    goal: 'Close the quality loop: incoming and inline quality, deviations and trends.' },
  warranty: { label: 'Warranty & Chargebacks', module: 'warranty', icon: 'receipt',
    lede: 'Field claims traced to the failed lot and supplier, turned into chargebacks that post to the ERP.',
    goal: 'Warranty tracing, and a defect traced to a supplier chargeback that lands in the financial system.' },
  integrations: { label: 'Integration Hub', module: 'integrations', icon: 'plug',
    lede: 'Every inbound feed traced step by step, including email and Excel from partners without APIs, and the option that removes each manual touch.',
    goal: 'Delete the steps that exist only because two systems could not talk, then automate what survives.' },
  sandbox: { label: 'Data Sandbox', module: 'sandbox', icon: 'database',
    lede: 'The whole relational model, live: every table, key and relationship, plus a read-only SQL sandbox.',
    goal: 'A single source of truth for planning, quality, inventory, delivery and decision-making.' },
  contracts: { label: 'Contracts & Recon', module: 'contracts', icon: 'checklist',
    lede: 'Data contracts that run against the live tables, and reconciliations that turn conflicting sources into one number.',
    goal: 'Turn conflicting data into one honest picture.' },
  proof: { label: 'Tests · Evals · Review', module: 'proof', icon: 'flask',
    lede: 'How logic and agent-written code are proven before they act: unit tests, evals against golden truth, contracts and human review.',
    goal: 'A real method for proving model output correct: contracts, tests, evals and review.' },
  process: { label: 'Process Lab', module: 'process-lab', icon: 'flow',
    lede: 'Process mining on how the work actually runs, so glue steps get deleted before anything is automated.',
    goal: 'Engineer the process before you automate it: instrument how the work actually runs, including the exceptions nobody wrote down.' },
  'buy-build': { label: 'Buy vs Build', module: 'buy-vs-build', icon: 'scale',
    lede: 'Thin, disposable apps instrumented in production, so evidence, not demos, picks the platform we buy later.',
    goal: 'Settle buy versus build by shipping: stand up a thin, disposable app and let the evidence pick the platform to buy.' },
  'design-map': { label: 'Design Map', module: 'design-map', icon: 'compass',
    lede: 'How this prototype maps to the role, and a five-minute demo path through it.',
    goal: 'One operating system for making and moving vehicles, from contract-manufacturer production to the customer\'s door.' },
};

// ---------------------------------------------------------------------------
// Modules: the menu. One module is on screen at a time (the switcher at the top
// of the sidebar); its sections list pages and, under a page, the functions it
// deep-links to (?tab=). A page belongs to the first module that lists it
// without `ref`; `ref` items are cross-listed shortcuts into another module.
// ---------------------------------------------------------------------------
export const MODULES = [
  { id: 'ct', code: 'CT', name: 'Control Tower', icon: 'radar',
    desc: 'Visibility, exceptions and decisions',
    sections: [
      { title: 'Monitor', items: [{ route: 'tower' }] },
      { title: 'Decide', items: [{ route: 'loop' }] },
      { title: 'Landscape', items: [{ route: 'systems' }] },
    ] },
  { id: 'mes', code: 'MES', name: 'Manufacturing Execution', icon: 'factory',
    desc: 'Shop floor, yield, genealogy, CM feed',
    sections: [
      { title: 'Shop floor', items: [{ route: 'production' }] },
      { title: 'Traceability', items: [{ route: 'genealogy' }] },
      { title: 'Contract manufacturer', items: [{ route: 'cm-feed', tabs: [
        ['quarantine', 'Quarantine'], ['inspector', 'Message inspector'], ['mappings', 'Mappings'],
        ['recon', 'CM Excel vs MES'], ['asn', 'ASN & provisional']] }] },
    ] },
  { id: 'ps', code: 'PS', name: 'Production Scheduling', icon: 'calendar',
    desc: 'Line schedule, MPS, capacity, fences',
    sections: [
      { title: 'Schedule', items: [{ route: 'schedule' }, { route: 'mps' }] },
      { title: 'Queues', items: [{ route: 'atp', tab: 'build', label: 'Build queue', ref: true }] },
    ] },
  { id: 'mrp', code: 'MRP', name: 'Material Planning', icon: 'grid',
    desc: 'MRP by SKU and day, ATP, queues',
    sections: [
      { title: 'Plan', items: [{ route: 'mrp' }] },
      { title: 'Promise', items: [{ route: 'atp', tabs: [
        ['atp', 'ATP by week'], ['pipeline', 'Supply pipeline'], ['risk', 'Promises at risk'],
        ['build', 'Build queue'], ['fulfill', 'Fulfillment queue']] }] },
    ] },
  { id: 'rep', code: 'REP', name: 'Replenishment', icon: 'refresh',
    desc: 'Policies, safety stock, actions',
    sections: [
      { title: 'Replenish', items: [{ route: 'replenishment' }] },
      { title: 'Related', items: [{ route: 'mrp', label: 'Material Plan · MRP', ref: true }] },
    ] },
  { id: 'wms', code: 'WMS', name: 'Warehouse Management', icon: 'warehouse',
    desc: 'Stock from supplier to 3PL shelf',
    sections: [
      { title: 'Inventory', items: [{ route: 'inventory' }] },
      { title: '3PL operations', items: [
        { route: 'atp', tab: 'fulfill', label: 'Fulfillment queue', ref: true },
        { route: 'quality', tab: 'holds', label: 'Quality holds', ref: true }] },
    ] },
  { id: 'tms', code: 'TMS', name: 'Transportation Management', icon: 'truck',
    desc: 'Ocean, customs, DG trucks, last mile',
    sections: [
      { title: 'Execution', items: [{ route: 'shipments', tabs: [
        ['pipeline', 'Pipeline & ocean'], ['customs', 'Customs'], ['lastmile', 'Last mile'],
        ['trucks', 'DG trucks & inbound'], ['freight', 'Freight spend']] }] },
    ] },
  { id: 'erp', code: 'ERP', name: 'ERP · Procurement & Finance', icon: 'ledger',
    desc: 'Purchasing, suppliers, items, ledger',
    sections: [
      { title: 'Procurement', items: [{ route: 'suppliers', tabs: [
        ['pos', 'POs & promise dates'], ['forecast', 'Forecast to tiers 1-3'], ['rfqs', 'RFQs'],
        ['ecos', 'ECOs & effectivity'], ['pricing', 'Pricing'], ['scorecard', 'Scorecard']] }] },
      { title: 'Finance & master data', items: [{ route: 'erp', tabs: [
        ['items', 'Item master'], ['bom', 'BOM & cost'], ['pos', 'Purchase orders'], ['receipts', 'Goods receipts'],
        ['ap', 'AP · three-way match'], ['gl', 'General ledger']] }] },
    ] },
  { id: 'qms', code: 'QMS', name: 'Quality Management', icon: 'shield',
    desc: 'IQC, SPC, holds, warranty, recovery',
    sections: [
      { title: 'Quality', items: [{ route: 'quality', tabs: [
        ['iqc', 'Incoming (IQC)'], ['spc', 'Inline & SPC'], ['deviations', 'Deviations'], ['ncr', 'NCRs & CAPA'],
        ['holds', 'Holds']] }] },
      { title: 'Field', items: [{ route: 'warranty' }] },
    ] },
  { id: 'dp', code: 'DP', name: 'Data Platform', icon: 'database',
    desc: 'Integrations, data model, proof',
    sections: [
      { title: 'Integrate', items: [{ route: 'integrations', tabs: [
        ['sources', 'Sources'], ['email', 'Email pipeline'], ['try', 'Try the parsers'], ['options', 'Integration options']] }] },
      { title: 'Model', items: [{ route: 'sandbox', tabs: [['tables', 'Tables'], ['sql', 'SQL'], ['erd', 'Relationships']] }] },
      { title: 'Assure', items: [
        { route: 'contracts', tabs: [['contracts', 'Data contracts'], ['recon', 'Reconciliation']] },
        { route: 'proof' }] },
      { title: 'Improve', items: [{ route: 'process' }, { route: 'buy-build' }] },
    ] },
];
// pages that live outside the module menu (reached from the sidebar footer and search)
const ABOUT = { id: 'about', code: 'INFO', name: 'About this prototype', icon: 'help', hidden: true,
  sections: [{ title: 'About', items: [{ route: 'design-map' }] }] };

const ROUTES = new Map();               // route -> page, with its home module and app code
const MODULE_BY_ID = new Map();
const APPS = [];                        // every page and function, for search
for (const m of [...MODULES, ABOUT]) {
  MODULE_BY_ID.set(m.id, m);
  let n = 0;
  for (const sec of m.sections) {
    for (const it of sec.items) {
      const page = PAGES[it.route];
      if (!page) throw new Error(`module ${m.id} lists unknown route ${it.route}`);
      it.label = it.label || page.label;
      it.icon = it.icon || page.icon;
      if (it.ref || page.home) continue;
      n += 1;
      page.home = m;
      page.route = it.route;
      page.code = `${m.code}-${String(n).padStart(2, '0')}`;
      page.num = n;
      page.tabs = it.tabs || null;
      ROUTES.set(it.route, page);
    }
  }
}
for (const [route, page] of Object.entries(PAGES)) {
  if (!ROUTES.has(route)) throw new Error(`page ${route} is in no module`);
  APPS.push({ route, tab: null, label: page.label, code: page.code, module: page.home, page });
  for (const [tab, label] of page.tabs || []) APPS.push({ route, tab, label, code: page.code, module: page.home, page });
}
const DEFAULT_ROUTE = 'tower';

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------
const appEl = $('#app');
const sidebarEl = $('#sidebar');
const topbarEl = $('#topbar');
let pageEl = $('#page');
let meta = {};
const current = { item: null, mod: null, token: 0, ctx: null };

// ---------------------------------------------------------------------------
// theme
// ---------------------------------------------------------------------------
function effectiveTheme() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t === 'light' || t === 'dark') return t;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function syncThemeButtons() {
  syncThemeItem();
  const dark = effectiveTheme() === 'dark';
  $$('[data-theme-toggle]').forEach((b) => {
    b.innerHTML = String(icon(dark ? 'sun' : 'moon', b.classList.contains('side-btn') ? 15 : 17));
    b.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    b.title = dark ? 'Light theme' : 'Dark theme';
  });
}

function toggleTheme() {
  const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('ops-theme', next); } catch { /* ignore */ }
  syncThemeButtons();
}

// ---------------------------------------------------------------------------
// sidebar: module switcher + the selected module's menu
// ---------------------------------------------------------------------------
const navState = { moduleId: null, all: false };
try { navState.all = localStorage.getItem('ops-nav-all') === '1'; } catch { /* storage blocked */ }

function moduleOf(route) {
  const cur = MODULE_BY_ID.get(navState.moduleId);
  // stay in the module on screen when it lists this page (cross-listed shortcuts included)
  if (cur && cur.sections.some((sec) => sec.items.some((it) => it.route === route))) return cur;
  const page = ROUTES.get(route);
  return page ? page.home : MODULES[0];
}

function moduleBadge(m) {
  const badges = (meta && meta.nav_badges) || {};
  let count = 0;
  let critical = false;
  const seen = new Set();
  for (const sec of m.sections) {
    for (const it of sec.items) {
      if (it.ref || seen.has(it.route)) continue;
      seen.add(it.route);
      const b = badges[it.route];
      if (b && b.count) {
        count += b.count;
        critical = critical || b.tone === 'critical';
      }
    }
  }
  return { count, critical };
}

function renderSidebar() {
  sidebarEl.innerHTML = html`
    <a class="brand" href="#/${DEFAULT_ROUTE}" aria-label="Ops OS, go to Control Tower">
      <span class="brand-bolt l"></span><span class="brand-bolt r"></span>
      <span class="brand-name">OPS OS</span>
      <span class="brand-route"><span>Factory</span><span class="route-line"></span><span>Door</span></span>
    </a>
    <p class="brand-sub">Prototype · mock data</p>
    <div class="mod-switch" id="mod-switch">
      <span class="ms-label" id="ms-label">Module</span>
      <button class="ms-trigger" type="button" id="ms-trigger" aria-haspopup="listbox" aria-expanded="false"
        aria-controls="ms-pop" aria-labelledby="ms-label ms-trigger"></button>
      <div class="ms-pop" id="ms-pop" role="listbox" aria-label="Modules" hidden></div>
    </div>
    <nav class="nav" id="module-nav" aria-label="Module menu"></nav>
    <div class="side-foot">
      <a class="side-link" href="#/design-map">${icon('help', 15)}<span>About this prototype</span></a>
      <div class="side-clock" id="side-clock"><span class="label">Dataset clock</span>…</div>
      <div class="side-actions">
        <button class="side-btn" type="button" id="reset-btn">${icon('refresh', 14)}<span>Reset demo data</span></button>
        <button class="side-btn icon" type="button" data-theme-toggle></button>
      </div>
    </div>`;
}

function renderSwitcher() {
  const m = MODULE_BY_ID.get(navState.moduleId) || MODULES[0];
  const trig = $('#ms-trigger');
  trig.innerHTML = navState.all
    ? html`<span class="ms-code all">${icon('apps', 15)}</span><span class="ms-name">All modules</span><span class="ms-caret">${icon('updown', 15)}</span>`
    : html`<span class="ms-code">${m.code}</span><span class="ms-name">${m.name}</span><span class="ms-caret">${icon('updown', 15)}</span>`;
  $('#ms-pop').innerHTML = html`<div class="ms-pop-head">Switch module<span class="kbd">M</span></div>
    <div class="ms-grid">${MODULES.map((x) => {
      const b = moduleBadge(x);
      const sel = !navState.all && x.id === m.id;
      return html`<button class="ms-opt${sel ? ' selected' : ''}" type="button" role="option" data-mod="${x.id}" aria-selected="${sel ? 'true' : 'false'}">
        <span class="ms-code">${x.code}</span>
        <span class="ms-opt-text"><span class="ms-opt-name">${x.name}</span><span class="ms-opt-desc">${x.desc}</span></span>
        ${b.count ? html`<span class="ms-badge${b.critical ? ' critical' : ''}" title="${b.count} open exception${b.count === 1 ? '' : 's'}">${b.count}</span>` : ''}
        ${sel ? html`<span class="ms-check">${icon('check', 15)}</span>` : ''}
      </button>`;
    })}</div>
    <div class="ms-sep"></div>
    <button class="ms-opt all${navState.all ? ' selected' : ''}" type="button" role="option" data-mod="*" aria-selected="${navState.all ? 'true' : 'false'}">
      <span class="ms-code all">${icon('apps', 15)}</span>
      <span class="ms-opt-text"><span class="ms-opt-name">All modules</span><span class="ms-opt-desc">Every page, grouped by module</span></span>
    </button>`;
}

function navItem(it) {
  return html`<a class="nav-item" href="#/${it.route}${it.tab ? `?tab=${it.tab}` : ''}" data-route="${it.route}"${it.tab ? html` data-tab="${it.tab}"` : ''}>
    <span class="ni-icon">${icon(it.icon, 17)}</span>
    <span class="ni-label">${it.label}</span>
    ${it.ref ? html`<span class="ni-ref" title="Opens in ${ROUTES.get(it.route).home.name}">${ROUTES.get(it.route).home.code}</span>`
      : html`<span class="ni-badge" data-badge="${it.route}" hidden></span>`}
  </a>`;
}

function renderModuleNav() {
  const nav = $('#module-nav');
  if (navState.all) {
    nav.innerHTML = html`${MODULES.map((m) => html`<div class="nav-module">
        <button class="nm-head" type="button" data-mod="${m.id}" title="Open ${m.name}"><span class="ms-code sm">${m.code}</span><span>${m.name}</span></button>
        ${m.sections.flatMap((sec) => sec.items.filter((it) => !it.ref)).map((it) => navItem(it))}
      </div>`)}`;
  } else {
    const m = MODULE_BY_ID.get(navState.moduleId) || MODULES[0];
    nav.innerHTML = html`${m.sections.map((sec) => html`<div class="nav-section">${sec.title}</div>
        ${sec.items.map((it) => html`${navItem(it)}${it.tabs ? html`<div class="nav-subs">${it.tabs.map(([tab, label], i) => html`
          <a class="nav-sub" href="#/${it.route}?tab=${tab}" data-route="${it.route}" data-tab="${tab}"${i === 0 ? html` data-first="1"` : ''}>${label}</a>`)}</div>` : ''}`)}`)}`;
  }
  renderBadges();
  syncActive();
}

function setModule(id, { navigateHome = false } = {}) {
  if (id === '*') {
    navState.all = true;
  } else {
    navState.all = false;
    navState.moduleId = id;
  }
  try { localStorage.setItem('ops-nav-all', navState.all ? '1' : '0'); } catch { /* ignore */ }
  renderSwitcher();
  renderModuleNav();
  if (navigateHome && id !== '*') {
    const m = MODULE_BY_ID.get(id);
    const first = m.sections[0].items[0];
    const { route } = parseHash();
    if (!m.sections.some((sec) => sec.items.some((it) => it.route === route))) {
      location.hash = `#/${first.route}${first.tab ? `?tab=${first.tab}` : ''}`;
    }
  }
}

// --- the switcher popover ---------------------------------------------------
function openSwitcher() {
  closePopovers();
  renderSwitcher();
  const pop = $('#ms-pop');
  const r = $('#ms-trigger').getBoundingClientRect();
  const width = Math.min(Math.max(r.width, 660), window.innerWidth - r.left - 12);
  pop.style.left = `${Math.round(r.left)}px`;
  pop.style.top = `${Math.round(r.bottom + 6)}px`;
  pop.style.width = `${Math.round(width)}px`;
  pop.style.maxHeight = `${Math.round(window.innerHeight - r.bottom - 18)}px`;
  pop.hidden = false;
  $('#ms-trigger').setAttribute('aria-expanded', 'true');
  const sel = pop.querySelector('.ms-opt.selected') || pop.querySelector('.ms-opt');
  if (sel) sel.focus();
}
function closeSwitcher({ focus = false } = {}) {
  const pop = $('#ms-pop');
  if (!pop || pop.hidden) return;
  pop.hidden = true;
  $('#ms-trigger').setAttribute('aria-expanded', 'false');
  if (focus) $('#ms-trigger').focus();
}
function wireSwitcher() {
  on(sidebarEl, 'click', '#ms-trigger', () => ($('#ms-pop').hidden ? openSwitcher() : closeSwitcher()));
  on(sidebarEl, 'click', '.ms-opt', (e, b) => {
    closeSwitcher();
    setModule(b.dataset.mod, { navigateHome: true });
  });
  on(sidebarEl, 'click', '.nm-head', (e, b) => setModule(b.dataset.mod, { navigateHome: true }));
  on(sidebarEl, 'keydown', '#ms-pop', (e) => {
    const opts = $$('.ms-opt', $('#ms-pop'));
    const i = opts.indexOf(document.activeElement);
    if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
      e.preventDefault();
      const n = opts.length;
      const step = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1;
      opts[((i < 0 ? 0 : i) + step + n) % n].focus();
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      opts[e.key === 'Home' ? 0 : opts.length - 1].focus();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeSwitcher({ focus: true });
    } else if (e.key === 'Tab') {
      closeSwitcher();
    } else if (/^[a-z]$/i.test(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      // type-ahead on module code or name
      const k = e.key.toLowerCase();
      const start = i + 1;
      for (let j = 0; j < opts.length; j += 1) {
        const o = opts[(start + j) % opts.length];
        if ((o.textContent || '').trim().toLowerCase().startsWith(k)) { o.focus(); break; }
      }
    }
  });
  on(sidebarEl, 'keydown', '#ms-trigger', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); openSwitcher(); }
  });
}

function syncActive() {
  const { route, query } = parseHash();
  const tab = query.get('tab');
  let subOn = false;
  $$('.nav-sub', sidebarEl).forEach((a) => {
    const on = a.dataset.route === route && (tab ? a.dataset.tab === tab : a.dataset.first === '1');
    subOn = subOn || on;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  $$('.nav-item', sidebarEl).forEach((a) => {
    // a cross-listed item (with its own tab) is active only on that tab
    const on = a.dataset.route === route && (!a.dataset.tab || a.dataset.tab === tab);
    a.classList.toggle('active', on);
    a.classList.toggle('has-active-sub', on && subOn);
    if (on && !subOn) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (current.item) setCrumbs(current.item, tab);
  // keep the active entry in view inside the sidebar's own scroll (never scroll the page)
  const act = sidebarEl.querySelector('.nav-sub.active') || sidebarEl.querySelector('.nav-item.active');
  if (act) {
    const r = act.getBoundingClientRect();
    const sr = sidebarEl.getBoundingClientRect();
    if (r.top < sr.top + 60 || r.bottom > sr.bottom - 80) {
      sidebarEl.scrollTop = Math.max(0, sidebarEl.scrollTop + (r.top - sr.top) - sidebarEl.clientHeight / 2);
    }
  }
}

let resetTimer = null;
function onResetClick(btn) {
  const label = btn.querySelector('span');
  if (!btn.classList.contains('confirm')) {
    btn.classList.add('confirm');
    label.textContent = 'Click again to rebuild';
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => {
      btn.classList.remove('confirm');
      label.textContent = 'Reset demo data';
    }, 4000);
    return;
  }
  clearTimeout(resetTimer);
  btn.disabled = true;
  label.textContent = 'Rebuilding…';
  ui.toast('Rebuilding the mock database from the same seed…', 'info', 8000);
  api.post('/api/admin/reset')
    .then(() => { location.reload(); })
    .catch((err) => {
      btn.disabled = false;
      btn.classList.remove('confirm');
      label.textContent = 'Reset demo data';
      ui.toast(`Reset failed: ${err.message}`, 'critical', 6000);
    });
}

function renderBadges() {
  const badges = (meta && meta.nav_badges) || {};
  $$('[data-badge]', sidebarEl).forEach((el) => {
    const b = badges[el.dataset.badge];
    if (b && b.count > 0) {
      el.hidden = false;
      el.textContent = b.count > 99 ? '99+' : String(b.count);
      el.classList.toggle('critical', b.tone === 'critical');
      el.title = `${b.count} open exception${b.count === 1 ? '' : 's'}`;
    } else {
      el.hidden = true;
    }
  });
}

// ---------------------------------------------------------------------------
// top bar
// ---------------------------------------------------------------------------
function renderTopbar() {
  topbarEl.innerHTML = html`
    <button class="btn btn-ghost icon-btn menu-btn" type="button" id="menu-btn" aria-label="Open navigation">${icon('menu', 18)}</button>
    <nav class="crumbs" id="crumbs" aria-label="Breadcrumb"></nav>
    <div class="top-spacer"></div>
    <div class="search" id="search" role="combobox" aria-haspopup="listbox" aria-expanded="false">
      <span class="s-icon">${icon('search', 16)}</span>
      <input class="input" id="search-input" type="text" autocomplete="off" spellcheck="false"
        placeholder="Search apps, serials, lots, orders, POs…" aria-label="Search" aria-controls="search-pop">
      <span class="kbd search-kbd">/</span>
      <div class="search-pop" id="search-pop" role="listbox" hidden></div>
    </div>
    <span class="chip-clock" id="asof-chip" title="The dataset's clock. All logic runs against this instant, not your wall clock."><b>AS OF</b><span>…</span></span>
    <span class="chip-mock" title="Every number in this app is generated mock data">Mock data</span>
    <div class="top-pop-wrap">
      <button class="btn btn-ghost icon-btn bell-btn" type="button" id="bell-btn" aria-haspopup="true" aria-expanded="false" aria-controls="bell-pop" aria-label="Notifications">
        ${icon('bell', 18)}<span class="bell-count" id="bell-count" hidden></span>
      </button>
      <div class="top-pop bell-pop" id="bell-pop" hidden></div>
    </div>
    <div class="top-pop-wrap">
      <button class="user-btn" type="button" id="user-btn" aria-haspopup="true" aria-expanded="false" aria-controls="user-pop" aria-label="Account menu">
        <span class="avatar">OL</span>
      </button>
      <div class="top-pop user-pop" id="user-pop" hidden>
        <div class="up-head"><span class="avatar lg">OL</span><div><strong>Ops lead</strong><span>OEM Operations · all sites</span></div></div>
        <a class="up-item" href="#/design-map">${icon('help', 16)}<span>About this prototype</span></a>
        <button class="up-item" type="button" data-theme-toggle-item>${icon('moon', 16)}<span>Dark theme</span></button>
        <a class="up-item" href="#/proof">${icon('flask', 16)}<span>Tests · Evals · Review</span></a>
        <div class="up-foot">Roles and sign-in are out of scope for this prototype.</div>
      </div>
    </div>`;
}

function setCrumbs(item, tab) {
  const m = moduleOf(item.route);
  const t = tab && item.tabs ? (item.tabs.find(([k]) => k === tab) || [])[1] : null;
  $('#crumbs').innerHTML = html`<span class="crumb-code">${m.code}</span><span class="crumb-mod">${m.name}</span>
    <span class="sep">/</span>${t ? html`<a href="#/${item.route}">${item.label}</a><span class="sep">/</span><strong>${t}</strong>` : html`<strong>${item.label}</strong>`}`;
}

function renderAlerts() {
  const alerts = (meta && meta.alerts) || [];
  const open = (meta && meta.alerts_open) || 0;
  const count = $('#bell-count');
  if (count) {
    count.hidden = !open;
    count.textContent = open > 99 ? '99+' : String(open);
    count.classList.toggle('critical', alerts.some((a) => a.severity === 'CRITICAL'));
  }
  const pop = $('#bell-pop');
  if (!pop) return;
  const tone = { CRITICAL: 'critical', SERIOUS: 'serious', WARNING: 'warning', INFO: 'info' };
  pop.innerHTML = html`<div class="bp-head"><strong>Open exceptions</strong><span>${open} open</span></div>
    ${alerts.length ? alerts.map((a) => html`<a class="bp-item" href="${a.route || '#/tower'}">
        <span class="bp-sev tone-${tone[a.severity] || 'info'}">${icon(a.severity === 'CRITICAL' ? 'alert-octagon' : 'alert-triangle', 15)}</span>
        <span class="bp-text"><span class="bp-title">${a.title}</span><span class="bp-sub">${fmt.title(a.severity)} · ${fmt.title(a.domain)}</span></span>
      </a>`) : html`<div class="bp-empty">Nothing open. Every exception is resolved or acknowledged.</div>`}
    <a class="bp-foot" href="#/tower">Open the exception queue ${icon('arrow-right', 14)}</a>`;
}

function syncThemeItem() {
  const b = $('[data-theme-toggle-item]');
  if (!b) return;
  const dark = effectiveTheme() === 'dark';
  b.innerHTML = String(html`${icon(dark ? 'sun' : 'moon', 16)}<span>${dark ? 'Light theme' : 'Dark theme'}</span>`);
}

function togglePop(btnId, popId) {
  const pop = $(`#${popId}`);
  const wasHidden = pop.hidden;
  closePopovers();
  if (wasHidden) {
    pop.hidden = false;
    $(`#${btnId}`).setAttribute('aria-expanded', 'true');
    const first = pop.querySelector('a, button');
    if (first) first.focus();
  }
}

function closePopovers() {
  closeSwitcher();
  [['bell-btn', 'bell-pop'], ['user-btn', 'user-pop']].forEach(([b, p]) => {
    const pop = $(`#${p}`);
    if (pop && !pop.hidden) {
      pop.hidden = true;
      $(`#${b}`).setAttribute('aria-expanded', 'false');
    }
  });
}

function pacificParts(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return null;
  const p = {};
  new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles', weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(d).forEach((x) => { p[x.type] = x.value; });
  return p;
}

function renderClock() {
  const iso = meta.now_utc || (meta.as_of ? `${meta.as_of}T15:00:00Z` : null);
  const p = iso ? pacificParts(iso) : null;
  const chip = $('#asof-chip span');
  const side = $('#side-clock');
  if (!p) {
    if (chip) chip.textContent = '—';
    return;
  }
  if (chip) chip.textContent = `${p.weekday} ${p.month} ${p.day} · ${p.hour}:${p.minute} PT`;
  if (side) {
    side.innerHTML = html`<span class="label">Dataset clock</span><strong>${p.weekday} ${p.month} ${p.day}, ${p.year}</strong><br>${p.hour}:${p.minute} PT${meta.seed ? html` · seed ${meta.seed}` : ''}`;
  }
}

// --- global search ---------------------------------------------------------
const SEARCH_GROUPS = {
  app: 'Apps', serial: 'Serials', lot: 'Lots', order: 'Orders', po: 'Purchase orders', shipment: 'Shipments',
  supplier: 'Suppliers', item: 'Items', table: 'Tables',
};
const search = { timer: null, seq: 0, hits: [], active: -1, q: '' };

function searchEls() {
  return { box: $('#search'), input: $('#search-input'), pop: $('#search-pop') };
}

function hideSearch() {
  const { box, pop } = searchEls();
  if (!pop) return;
  pop.hidden = true;
  box.setAttribute('aria-expanded', 'false');
}

// Pages and their functions match by name, app code (TMS-01) or module, like an enterprise command field.
function appHits(q) {
  const t = q.trim().toLowerCase();
  if (t.length < 2) return [];
  const scored = [];
  for (const a of APPS) {
    const label = a.label.toLowerCase();
    // a function matches on its own name (and its module); only a page matches on its module's full name
    const hay = (a.tab ? [label, a.module.code] : [label, a.code, a.module.code, a.module.name]).join(' ').toLowerCase();
    let score = 0;
    if (a.code.toLowerCase() === t && !a.tab) score = 100;
    else if (label.startsWith(t)) score = 60;
    else if (label.includes(t)) score = 40;
    else if (a.module.code.toLowerCase() === t) score = 35;
    else if (hay.includes(t)) score = 20;
    if (!score) continue;
    scored.push({ a, score: score + (a.tab ? 0 : 5) });
  }
  scored.sort((x, y) => y.score - x.score);
  return scored.slice(0, 6).map(({ a }) => ({
    type: 'app', id: a.tab ? `${a.code}:${a.tab}` : a.code, label: a.label,
    sub: a.tab ? `${a.page.label} · ${a.module.code}` : `${a.code} · ${a.module.name}`,
    href: `#/${a.route}${a.tab ? `?tab=${a.tab}` : ''}`,
  }));
}

function renderSearch() {
  const { box, pop } = searchEls();
  if (!search.q || search.q.length < 2) { hideSearch(); return; }
  if (!search.hits.length) {
    pop.innerHTML = html`<div class="sp-empty">No app, serial, lot, order, PO, shipment, supplier or item matches “${search.q}”.</div>`;
  } else {
    const groups = [];
    const idx = new Map();
    search.hits.forEach((h, i) => {
      if (!idx.has(h.type)) { idx.set(h.type, groups.length); groups.push({ type: h.type, items: [] }); }
      groups[idx.get(h.type)].items.push({ h, i });
    });
    pop.innerHTML = html`${groups.map((g) => html`<div class="sp-group">${SEARCH_GROUPS[g.type] || g.type}</div>
      ${g.items.map(({ h, i }) => html`<a class="sp-item${i === search.active ? ' active' : ''}" role="option" href="${h.href}" data-i="${i}" aria-selected="${i === search.active ? 'true' : 'false'}">
        ${h.type === 'app' ? html`<span class="sp-app-ic">${icon(ROUTES.get(h.href.slice(2).split('?')[0])?.icon || 'apps', 15)}</span>` : ''}<span class="sp-label${h.type === 'supplier' || h.type === 'app' ? ' text' : ''}">${h.label}</span><span class="sp-sub">${h.sub || ''}</span></a>`)}`)}
      <div class="sp-foot"><span><span class="kbd">↑</span> <span class="kbd">↓</span> to move</span><span><span class="kbd">↵</span> to open</span><span><span class="kbd">esc</span> to close</span></div>`;
  }
  pop.hidden = false;
  box.setAttribute('aria-expanded', 'true');
}

async function runSearch(q) {
  const seq = ++search.seq;
  try {
    const apps = appHits(q);
    let res = { hits: [] };
    try {
      res = await api.get('/api/search', { q });
    } catch (err) {
      if (!apps.length) throw err;
    }
    if (seq !== search.seq) return null;
    search.q = q;
    search.hits = [...apps, ...(res.hits || [])];
    search.active = search.hits.length ? 0 : -1;
    renderSearch();
    return search.hits;
  } catch (err) {
    if (seq !== search.seq) return null;
    const { pop } = searchEls();
    pop.innerHTML = html`<div class="sp-empty">Search failed: ${err.message}</div>`;
    pop.hidden = false;
    return null;
  }
}

function goToHit(h) {
  if (!h) return;
  const { input } = searchEls();
  hideSearch();
  input.blur();
  location.hash = h.href;
}

function wireSearch() {
  const { box, input, pop } = searchEls();
  input.addEventListener('input', () => {
    clearTimeout(search.timer);
    const q = input.value.trim();
    if (q.length < 2) { search.q = q; search.hits = []; hideSearch(); return; }
    search.timer = setTimeout(() => runSearch(q), 160);
  });
  input.addEventListener('focus', () => { if (search.hits.length && input.value.trim() === search.q) renderSearch(); });
  input.addEventListener('keydown', async (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!search.hits.length) return;
      e.preventDefault();
      const n = search.hits.length;
      search.active = (search.active + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      renderSearch();
      const act = pop.querySelector('.sp-item.active');
      if (act) act.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const q = input.value.trim();
      if (q.length < 2) return;
      let hits = search.hits;
      if (q !== search.q || search.timer) {
        clearTimeout(search.timer);
        search.timer = null;
        hits = (await runSearch(q)) || [];
      }
      const exact = hits.find((h) => String(h.id).toLowerCase() === q.toLowerCase());
      if (exact) goToHit(exact);
      else if (hits.length === 1) goToHit(hits[0]);
      else if (search.active >= 0) goToHit(hits[search.active]);
    } else if (e.key === 'Escape') {
      hideSearch();
      input.blur();
    }
  });
  on(pop, 'mousedown', '.sp-item', (e) => e.preventDefault()); // keep focus for the click
  on(pop, 'click', '.sp-item', (e, a) => {
    e.preventDefault();
    goToHit(search.hits[+a.dataset.i]);
  });
  document.addEventListener('click', (e) => { if (!box.contains(e.target)) hideSearch(); });
}

function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

// ---------------------------------------------------------------------------
// meta
// ---------------------------------------------------------------------------
async function loadMeta() {
  try {
    meta = (await api.get('/api/meta')) || {};
  } catch (err) {
    meta = meta || {};
    ui.toast(`Could not reach the API: ${err.message}`, 'critical', 6000);
  }
  fmt.setNow(meta.now_utc || (meta.as_of ? `${meta.as_of}T15:00:00Z` : null));
  renderClock();
  renderBadges();
  renderAlerts();
  if ($('#ms-pop') && $('#ms-pop').hidden) renderSwitcher();
  if (current.ctx) current.ctx.meta = meta;
  return meta;
}

// ---------------------------------------------------------------------------
// router
// ---------------------------------------------------------------------------
function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const qi = h.indexOf('?');
  const path = qi >= 0 ? h.slice(0, qi) : h;
  const qs = qi >= 0 ? h.slice(qi + 1) : '';
  const segs = path.split('/').filter(Boolean).map((s) => {
    try { return decodeURIComponent(s); } catch { return s; }
  });
  return { route: segs[0] || '', params: segs.slice(1), query: new URLSearchParams(qs) };
}

function buildHash(route, params, query) {
  const qs = query.toString();
  return `#/${[route, ...params.map(encodeURIComponent)].join('/')}${qs ? `?${qs}` : ''}`;
}

function makeCtx(item, params, query) {
  const ctx = {
    item,
    params,
    query,
    meta,
    go(hash) { location.hash = hash.startsWith('#') ? hash : `#${hash.startsWith('/') ? '' : '/'}${hash}`; },
    setQuery(obj, opts = {}) {
      // A page that is no longer current (say its drawer closes during the next navigation) must not write its route
      // back into the URL: the address bar would name one page while another is on screen.
      if (current.ctx !== ctx) return;
      for (const [k, v] of Object.entries(obj || {})) {
        if (v == null || v === '') ctx.query.delete(k);
        else ctx.query.set(k, String(v));
      }
      const hash = buildHash(item.route, ctx.params, ctx.query);
      if (opts.silent) {
        history.replaceState(null, '', hash);
        syncActive();
      } else {
        location.hash = hash;
      }
    },
    refreshMeta: loadMeta,
  };
  return ctx;
}

// Probe a page module once per session so a page that isn't built yet shows
// the placeholder instead of a failed module import (and a console error).
const moduleStatus = new Map();
async function moduleMissing(module) {
  if (moduleStatus.get(module) === 'present') return false;
  try {
    const r = await fetch(`/js/pages/${module}.js`, { cache: 'no-store' });
    const ct = r.headers.get('content-type') || '';
    const missing = !r.ok || !/javascript/.test(ct);
    if (!missing) moduleStatus.set(module, 'present');
    return missing;
  } catch {
    return true;
  }
}

function renderPlaceholder(el, item) {
  el.innerHTML = html`${ui.pageHeader({})}
    <section class="card placeholder">
      <div class="ph-ic">${icon('cone', 30)}</div>
      <div>
        <h3>This page is being built</h3>
        <p>${item.lede}</p>
        <p class="small muted">Its data already lives in the relational model. Browse the tables behind it in the ${html`<a href="#/sandbox">Data Sandbox</a>`}, or head back to the ${html`<a href="#/${DEFAULT_ROUTE}">Control Tower</a>`}.</p>
      </div>
    </section>`;
}

function renderError(el, item, err) {
  el.innerHTML = html`${ui.pageHeader({})}${ui.errorBox(err)}`;
}

function enter(el) {
  el.classList.remove('is-loading');
  el.classList.remove('page-enter');
  void el.offsetWidth; // restart the animation
  el.classList.add('page-enter');
}

async function navigate() {
  const { route, params, query } = parseHash();
  const item = ROUTES.get(route);
  if (!item) {
    location.replace(`#/${DEFAULT_ROUTE}`);
    return;
  }
  const token = ++current.token;
  current.ctx = null;           // from here on the outgoing page is stale (see setQuery)
  closeNav();
  hideSearch();
  ui.drawer.close();
  if (current.mod && typeof current.mod.unmount === 'function') {
    try { current.mod.unmount(); } catch (err) { console.error(err); }
  }
  current.mod = null;
  current.item = item;
  const m = moduleOf(route);
  if (!m.hidden && m.id !== navState.moduleId) {
    navState.moduleId = m.id;
    renderSwitcher();
    renderModuleNav();
  }
  closePopovers();
  syncActive();
  document.title = `${item.label} · ${item.code} · Ops OS`;
  ui.setCurrentPage({ group: item.home.code, num: item.num, label: item.label, goal: item.goal, lede: item.lede });

  // A fresh #page per navigation: listeners a page bound to its root go with the old element, even from a page whose
  // unmount forgets one, so they can't pile up across visits.
  const el = pageEl.cloneNode(false);
  pageEl.replaceWith(el);
  pageEl = el;
  el.classList.remove('page-enter');
  el.classList.add('is-loading');
  el.innerHTML = '';
  window.scrollTo(0, 0);
  const ctx = makeCtx(item, params, query);
  current.ctx = ctx;

  let mod;
  try {
    if (await moduleMissing(item.module)) {
      if (token !== current.token) return;
      renderPlaceholder(el, item);
      enter(el);
      return;
    }
    mod = await import(`./pages/${item.module}.js`);
  } catch (err) {
    if (token !== current.token) return;
    console.error(err);
    renderError(el, item, err);
    enter(el);
    return;
  }
  if (token !== current.token) return;
  current.mod = mod;
  try {
    await mod.render(el, ctx);
  } catch (err) {
    if (token !== current.token) return;
    console.error(err);
    renderError(el, item, err);
  }
  if (token === current.token) enter(el);
}

// ---------------------------------------------------------------------------
// mobile nav
// ---------------------------------------------------------------------------
function openNav() { appEl.classList.add('nav-open'); }
function closeNav() { appEl.classList.remove('nav-open'); }

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
function wire() {
  on(document, 'click', '[data-theme-toggle], [data-theme-toggle-item]', () => toggleTheme());
  on(sidebarEl, 'click', '#reset-btn', (e, b) => onResetClick(b));
  on(sidebarEl, 'click', '.nav-item, .nav-sub, .side-link', () => closeNav());
  wireSwitcher();
  on(topbarEl, 'click', '#bell-btn', () => togglePop('bell-btn', 'bell-pop'));
  on(topbarEl, 'click', '#user-btn', () => togglePop('user-btn', 'user-pop'));
  on(topbarEl, 'click', '.bp-item, .bp-foot, .up-item', () => closePopovers());
  document.addEventListener('click', (e) => {
    if (!e.target.closest || !e.target.closest('.top-pop-wrap, .mod-switch')) closePopovers();
  });
  on(topbarEl, 'click', '#menu-btn', () => (appEl.classList.contains('nav-open') ? closeNav() : openNav()));
  $('#nav-scrim').addEventListener('click', closeNav);
  wireSearch();
  document.addEventListener('keydown', (e) => {
    const k = e.key.toLowerCase();
    if ((e.metaKey || e.ctrlKey) && k === 'k') {
      e.preventDefault();
      const inp = $('#search-input');
      inp.focus();
      inp.select();
    } else if (e.key === '/' && !isTyping(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      $('#search-input').focus();
    } else if ((e.key === 'm' || e.key === 'M') && !isTyping(e.target) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      if (window.matchMedia && window.matchMedia('(max-width: 960px)').matches) openNav();
      openSwitcher();
    } else if (e.key === 'Escape') {
      const pop = ['bell-pop', 'user-pop'].map((id) => $(`#${id}`)).find((p) => p && !p.hidden);
      if (pop) {
        closePopovers();
        const btn = pop.id === 'bell-pop' ? '#bell-btn' : '#user-btn';
        $(btn).focus();
      } else if (appEl.classList.contains('nav-open')) {
        closeNav();
      }
    }
  });
  if (window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const fn = () => syncThemeButtons();
    if (mq.addEventListener) mq.addEventListener('change', fn);
  }
  window.addEventListener('ops:meta-changed', () => { loadMeta(); });
  window.addEventListener('resize', () => closeSwitcher());
  sidebarEl.addEventListener('scroll', () => closeSwitcher(), { passive: true });
  window.addEventListener('hashchange', navigate);
}

async function boot() {
  renderSidebar();
  renderTopbar();
  const first = moduleOf(parseHash().route || DEFAULT_ROUTE);
  navState.moduleId = first.hidden ? MODULES[0].id : first.id;
  renderSwitcher();
  renderModuleNav();
  syncThemeButtons();
  wire();
  await loadMeta();
  if (!location.hash || location.hash === '#' || location.hash === '#/') {
    history.replaceState(null, '', `#/${DEFAULT_ROUTE}`);
  }
  navigate();
}

boot();
