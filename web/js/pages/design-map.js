// Design Map: the goals the prototype answers and where each one lives, the
// architecture in one picture, and a five-minute demo path.
import { html, raw, esc, injectStyle } from '../lib/dom.js';
import { ui } from '../lib/ui.js';
import { icon } from '../lib/icons.js';

const PAGES = {
  tower: 'Control Tower', loop: 'Closed Loop', systems: 'System Landscape', production: 'Production & Yield',
  genealogy: 'Genealogy', 'cm-feed': 'CM Feed', schedule: 'Line Schedule', mps: 'Master Schedule & Capacity',
  mrp: 'Material Plan · MRP', replenishment: 'Replenishment Planning', atp: 'ATP & Queues', inventory: 'Inventory Position',
  suppliers: 'Supplier Loop', erp: 'ERP Core', shipments: 'Shipments & Customs', quality: 'Quality Loop', warranty: 'Warranty & Chargebacks',
  integrations: 'Integration Hub', sandbox: 'Data Sandbox', contracts: 'Contracts & Recon', proof: 'Tests · Evals · Review',
  process: 'Process Lab', 'buy-build': 'Buy vs Build', 'design-map': 'Design Map',
};

const DO = [
  {
    goal: 'Build end-to-end visibility from factory to customer: WIP, first-pass yield, as-built genealogy from module to vehicle, inventory down to supplier-held stock, and shipment status.',
    pages: ['tower', 'production', 'genealogy', 'inventory', 'shipments'],
    how: 'One canonical model joins the CM\'s MES events, the OEM\'s pack-line MES, carrier and 3PL feeds, so any serial can be followed from its first station scan to a customer\'s door, and any lot forward to every unit it touched.',
  },
  {
    goal: 'Build one planning engine across both supply chains, what the contract manufacturer builds and what the OEM assembles itself: material planning at SKU-by-day, available-to-promise, and the build and fulfillment queues.',
    pages: ['schedule', 'mps', 'mrp', 'replenishment', 'atp'],
    how: 'The MPS respects line capacity and time fences; multi-level MRP nets on-hand, confirmed promises and safety stock by SKU and day for the pack line and the parts consigned to the CM; ATP pegs each order to a container or a build day across both chains.',
  },
  {
    goal: 'Run the supplier loop: PO confirmations, promise dates, RFQs, design changes, the forecast released to tiers 1 through 3, and component pricing with effectivity.',
    pages: ['suppliers', 'erp'],
    how: 'Every promise date a supplier ever gave is kept with its channel; RFQs compare landed cost; ECOs carry date, serial or lot effectivity; the released forecast is exploded through supplier BOMs down to cathode, lithium and rare-earth oxide.',
  },
  {
    goal: 'Run the physical moves: booking, importation and track-and-trace from CM to 3PL to customer, plus a visibility feed out of the contract manufacturer\'s own systems.',
    pages: ['shipments', 'cm-feed'],
    how: 'Carrier EDI 315, 3PL messages and CM MES payloads land raw, are normalized with versioned mappings, and drive shipment status, ETA risk, ISF and customs holds, and the units riding in each container.',
  },
  {
    goal: 'Close the quality loop: incoming and inline quality, deviations and trends, warranty tracing, and a defect traced to a supplier chargeback that lands in the financial system.',
    pages: ['quality', 'warranty', 'erp', 'loop'],
    how: 'A cluster of field claims traces through genealogy to one cell lot, triggers containment holds on every unit still in our control, and becomes a chargeback whose accepted amount posts a balanced debit memo to the ERP.',
  },
  {
    goal: 'Settle buy-versus-build by shipping: stand up a thin, disposable app for quality, transport and warehouse visibility, run the process instrumented, and let the evidence pick the platform to buy later.',
    pages: ['buy-build', 'systems'],
    how: 'Telemetry from the thin QMS, TMS and WMS apps reweights the capability matrix, so vendors are ranked on how the work is actually done rather than on assumed requirements.',
  },
  {
    goal: 'Engineer the process before you automate it: instrument how the work actually runs, including the exceptions nobody wrote down, delete the steps that exist only because two systems could not talk, then automate what survives.',
    pages: ['process', 'integrations'],
    how: 'Process mining over real event logs separates value steps from glue (re-keying, CSV exports, email and Excel hand-offs) and shows the cycle time that is left once the glue is deleted, before anything is automated.',
  },
  {
    goal: 'Ship with coding agents in days rather than quarters.',
    pages: ['proof', 'sandbox'],
    how: 'This prototype was built with coding agents. What makes that safe is the proof layer: unit tests with hand-computed expectations, evals against the simulator\'s golden truth, contracts on live tables, and a review record for every change.',
  },
  {
    goal: 'Embed with the team that owns a process, automate it with them, and leave them able to change it themselves.',
    pages: ['sandbox', 'contracts', 'integrations'],
    how: 'Every table, relationship, rule, mapping and decision is inspectable and written in plain SQL or versioned config, so the process owner can read it, query it and change it without filing a ticket.',
  },
];

const BRING = [
  {
    need: 'Owning the data model and the decision logic',
    pages: ['sandbox', 'mrp', 'atp'],
    proof: 'One relational model with foreign keys enforced on every write, spanning landing, core and action layers, and the MRP, ATP and genealogy logic that runs on it.',
  },
  {
    need: 'LLM-native development with a real method for proving output correct',
    pages: ['proof', 'contracts'],
    proof: 'Tests, evals, contracts and review are first-class tables with a page of their own, and gates block a change that fails them.',
  },
  {
    need: 'Working software in users\' hands in days, improved from actual use',
    pages: ['buy-build', 'process'],
    proof: 'Thin apps ship with instrumentation, and the telemetry decides what gets built or bought next.',
  },
  {
    need: 'Integrating QMS, TMS, WMS and ERP into one data platform, and knowing when a package is the slow path',
    pages: ['systems', 'integrations', 'erp'],
    proof: 'Every system is listed with its owner, channel and latency, along with the integration options scored on manual touches, error rate, build and run cost.',
  },
  {
    need: 'Python, SQL and API fluency; one honest picture out of conflicting data',
    pages: ['contracts', 'sandbox'],
    proof: 'A standard-library Python API over SQLite, a read-only SQL sandbox, and reconciliations that explain every unit of disagreement between CM, carrier, 3PL and ERP.',
  },
  {
    need: 'Proposals a team can trust, then on to the next constraint',
    pages: ['loop', 'mps'],
    proof: 'The system proposes decisions with their evidence and people execute them. Once a loop runs itself, capacity is the next constraint to plan.',
  },
];

const DEMO = [
  { page: 'tower', title: 'Start at the Control Tower', say: 'The top exception: warranty claims clustering on one cell lot. Everything else on the screen is context.' },
  { page: 'genealogy', title: 'Trace the lot forward', say: 'Lot to packs to the vehicles they shipped with to customers. Count what is still in our control: at the plant, in transit, at the 3PL.' },
  { page: 'loop', title: 'Execute the containment', say: 'The system proposed holds with its evidence. Executing writes the holds and sends hold instructions to the WMS and MES. Show the rows it wrote.' },
  { page: 'warranty', title: 'Claims become a chargeback', say: 'The claims roll into a draft chargeback against the cell supplier, with every claim as an evidence line priced by contract terms.' },
  { page: 'erp', title: 'It lands in the financial system', say: 'The accepted chargeback posts a debit memo. Debits equal credits; the supplier invoice match reflects it.' },
  { page: 'sandbox', href: '#/sandbox?tab=sql', title: 'Prove it in SQL', say: 'Run “Closed-loop audit trail”: every decision and every row it caused, straight from the tables.' },
  { page: 'cm-feed', title: 'When the CM changes its MES', say: 'Fields were renamed mid-shift. The contract caught it, messages were quarantined, the mapping was versioned and the backlog replayed.' },
  { page: 'mrp', title: 'The line-stop date is computed, not guessed', say: 'A slipped BMS promise creates a shortage on a specific day. MRP shows the date and the expedite that prevents it.' },
  { page: 'atp', title: 'Re-promise, don\'t surprise', say: 'A vessel delay moves supply that orders were pegged to. Re-promise them in one action, with customer notes queued.' },
  { page: 'proof', title: 'Close on how it is proven', say: 'Tests with hand-computed expectations, evals against golden truth, contracts on live data, and a review record before any rule deploys.' },
];

function pageLinks(list) {
  return html`<span class="rm-links">${list.map((r) => html`<a class="rm-pill" href="#/${r}">${PAGES[r] || r}</a>`)}</span>`;
}

export async function render(el) {
  injectStyle('page-design-map', PAGE_CSS);
  el.innerHTML = html`
    ${ui.pageHeader({
      lede: 'What this prototype covers and where. Every page answers one goal; this page is the index, the architecture on one page, and a five-minute demo path.',
    })}
    <div class="pg-design-map stack">
      <section class="card">
        <header class="card-head"><div class="card-head-text"><h3 class="card-title">Architecture: one database, three layers, one loop</h3>
          <p class="card-sub">Raw payloads land untouched, are normalized and proven, feed the canonical model and the decision logic, and every decision writes back to the systems it came from.</p></div></header>
        <div class="rm-arch">${architecture()}</div>
      </section>

      <section class="card flush">
        <header class="card-head"><div class="card-head-text"><h3 class="card-title">Goals → where they live</h3>
          <p class="card-sub">Each goal, the pages that answer it, and how.</p></div></header>
        <ol class="rm-list">${DO.map((d, i) => html`<li class="rm-row">
          <span class="rm-num">${String(i + 1).padStart(2, '0')}</span>
          <div class="rm-goal">${d.goal}</div>
          <div class="rm-how">${pageLinks(d.pages)}<p>${d.how}</p></div>
        </li>`)}</ol>
      </section>

      <div class="grid">
        <section class="card span-7">
          <header class="card-head"><div class="card-head-text"><h3 class="card-title">Skills it demonstrates → where they show</h3></div></header>
          <ul class="rm-bring">${BRING.map((b) => html`<li>
            <div class="rm-need">${b.need}</div>
            <p class="rm-proof">${b.proof}</p>
            ${pageLinks(b.pages)}
          </li>`)}</ul>
        </section>
        <section class="card span-5 rm-demo-card">
          <header class="card-head"><div class="card-head-text"><h3 class="card-title">Five-minute demo path</h3><p class="card-sub">Ten clicks, one story: a defect found, contained, charged back and proven.</p></div></header>
          <ol class="rm-demo">${DEMO.map((d, i) => html`<li>
            <span class="rm-step">${i + 1}</span>
            <div>
              <a class="rm-demo-title" href="${d.href || `#/${d.page}`}">${d.title} ${icon('arrow-right', 14)}</a>
              <div class="rm-demo-page">${PAGES[d.page]}</div>
              <p>${d.say}</p>
            </div>
          </li>`)}</ol>
        </section>
      </div>

      ${ui.callout({
        tone: 'info',
        title: 'About the data',
        body: html`Everything here is generated by a deterministic simulator (same seed, same world): an LV-1 e-bike kit built from a CM-built vehicle and an OEM-built battery pack, fictional suppliers across tiers 1-3, real ports, invented storylines. Every company, product, part number and price is fictional. Use <span class="mono">Reset demo data</span> in the sidebar to rebuild it.`,
      })}
    </div>`;
}

// ---------------------------------------------------------------------------
// architecture diagram (hand-laid SVG)
// ---------------------------------------------------------------------------
function architecture() {
  const W = 1180;
  const H = 468;
  const widths = [158, 184, 162, 186, 160, 172];
  const gap = 28;
  const xs = [];
  widths.reduce((x, w) => { xs.push(x); return x + w + gap; }, 12);
  const cx = (i) => xs[i];
  const cw = (i) => widths[i];
  const top = 48;
  const bottom = 368;
  const heads = ['Sources', 'Landing · raw', 'Normalize · prove', 'Core · canonical', 'Decide', 'Act · write back'];
  let s = '';
  s += `<defs>
    <marker id="rm-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" class="rm-arrowhead"/></marker>
    <marker id="rm-arrow-loop" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" class="rm-arrowhead-loop"/></marker>
  </defs>`;
  heads.forEach((h, i) => {
    s += `<text class="rm-head" x="${cx(i)}" y="28">${esc(h.toUpperCase())}</text>`;
  });

  // sources
  const sources = [
    ['CM MES', 'Taiwan · JSON over HTTPS'],
    ['Supplier ASN · EDI 855', 'serials, confirmations'],
    ['Carriers · EDI 315', 'ocean, truck, parcel'],
    ['3PL WMS', 'receipts, kits, shipments'],
    ['ERP', 'POs, receipts, invoices, GL'],
    ['CRM', 'warranty cases'],
    ['Email + Excel', 'partners without APIs'],
  ];
  const sh = 38;
  const sg = (bottom - top - sources.length * sh) / (sources.length - 1);
  sources.forEach(([t, sub], i) => {
    const y = top + i * (sh + sg);
    s += box(cx(0), y, cw(0), sh, t, sub, 'rm-box');
    s += `<path class="rm-arrow" d="M${cx(0) + cw(0)},${y + sh / 2} L${cx(1) - 4},${y + sh / 2}" marker-end="url(#rm-arrow)"/>`;
  });

  // landing
  s += listBox(cx(1), top, cw(1), bottom - top, 'raw_* tables', 'exactly as received', [
    'raw_cm_mes_event', 'raw_supplier_asn', 'raw_supplier_confirmation', 'raw_carrier_event', 'raw_3pl_message',
    'raw_warranty_case', 'raw_email', 'raw_attachment', '', '', 'ingest_step (every hop)',
  ], 'rm-box rm-landing', 23);

  // normalize + prove
  const prove = [
    ['Versioned mappings', 'a CM schema change is a new mapping version, not a fire drill'],
    ['Data contracts', 'SQL that returns violating rows; zero rows = pass'],
    ['Quarantine & replay', 'bad payloads are held, fixed and replayed, never dropped'],
    ['Evals & tests', 'parsers, MRP and ATP scored against golden truth'],
  ];
  const ph = 66;
  const pg = (bottom - top - prove.length * ph) / (prove.length - 1);
  prove.forEach(([t, sub], i) => {
    const y = top + i * (ph + pg);
    s += box(cx(2), y, cw(2), ph, t, sub, 'rm-box', 24);
  });
  s += `<path class="rm-arrow" d="M${cx(1) + cw(1)},${(top + bottom) / 2} L${cx(2) - 4},${(top + bottom) / 2}" marker-end="url(#rm-arrow)"/>`;
  s += `<path class="rm-arrow" d="M${cx(2) + cw(2)},${(top + bottom) / 2} L${cx(3) - 4},${(top + bottom) / 2}" marker-end="url(#rm-arrow)"/>`;

  // core
  s += listBox(cx(3), top, cw(3), bottom - top, 'Canonical model', 'one truth, FKs enforced', [
    'unit', 'lot', 'genealogy', 'station_event', 'work_order', 'bom_line', 'customer_order', 'po_line',
    'goods_receipt', 'shipment', 'inventory_balance', 'quality_event', 'warranty_claim',
  ], 'rm-box rm-core', 20);

  // decide
  const decide = [
    ['Genealogy trace', 'recall scope, one query'],
    ['MPS · capacity · CTP', 'line rates, time fences'],
    ['MRP · SKU × day', 'multi-level, effectivity'],
    ['ATP & queues', 'pegged to real supply'],
    ['Exception rules', 'severity, impact, owner'],
  ];
  const dh = 52;
  const dg = (bottom - top - decide.length * dh) / (decide.length - 1);
  decide.forEach(([t, sub], i) => {
    const y = top + i * (dh + dg);
    s += box(cx(4), y, cw(4), dh, t, sub, 'rm-box');
    s += `<path class="rm-arrow faint" d="M${cx(3) + cw(3)},${y + dh / 2} L${cx(4) - 4},${y + dh / 2}" marker-end="url(#rm-arrow)"/>`;
    s += `<path class="rm-arrow faint" d="M${cx(4) + cw(4)},${y + dh / 2} L${cx(5) - 4},${y + dh / 2}" marker-end="url(#rm-arrow)"/>`;
  });

  // act
  s += listBox(cx(5), top, cw(5), bottom - top, 'Action layer', 'every decision, every write', [
    'decision_log', 'hold', 'outbound_message', 'planned_order', 'order_promise', 'chargeback',
    '→ erp_journal_entry', 'supplier_scorecard', 'change_review',
  ], 'rm-box rm-act', 24);

  // the loop back to sources
  const lx1 = cx(5) + cw(5) / 2;
  const lx0 = cx(0) + cw(0) / 2;
  const ly = 412;
  s += `<path class="rm-loop" d="M${lx1},${bottom} L${lx1},${ly - 12} Q${lx1},${ly} ${lx1 - 12},${ly} L${lx0 + 12},${ly} Q${lx0},${ly} ${lx0},${ly - 12} L${lx0},${bottom + 6}" marker-end="url(#rm-arrow-loop)"/>`;
  s += `<text class="rm-loop-label" x="${(lx0 + lx1) / 2}" y="${ly + 22}" text-anchor="middle">CLOSED LOOP · writes back: WMS &amp; MES holds · supplier expedites · PO releases · ERP debit memos · customer re-promises</text>`;
  s += `<text class="rm-loop-sub" x="${(lx0 + lx1) / 2}" y="${ly + 40}" text-anchor="middle">Outcomes roll into the supplier scorecard, planning parameters and the next forecast: the learn step.</text>`;

  return raw(`<svg class="rm-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Architecture: sources to landing to normalize and prove to core to decide to act, with a loop writing back to sources">${s}</svg>`);
}

function box(x, y, w, h, title, sub, cls, wrapAt = 0) {
  let t = `<g class="${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8"/>`;
  t += `<text class="rm-title" x="${x + 10}" y="${y + 17}">${esc(title)}</text>`;
  if (sub) {
    if (wrapAt) {
      lines(sub, wrapAt).slice(0, 3).forEach((ln, i) => {
        t += `<text class="rm-sub" x="${x + 10}" y="${y + 33 + i * 13}">${esc(ln)}</text>`;
      });
    } else {
      t += `<text class="rm-sub" x="${x + 10}" y="${y + 31}">${esc(sub)}</text>`;
    }
  }
  return `${t}</g>`;
}

function listBox(x, y, w, h, title, sub, items, cls, step = 24) {
  let t = `<g class="${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>`;
  t += `<text class="rm-title" x="${x + 12}" y="${y + 20}">${esc(title)}</text>`;
  t += `<text class="rm-sub" x="${x + 12}" y="${y + 35}">${esc(sub)}</text>`;
  items.forEach((it, i) => {
    if (!it) return;
    t += `<text class="rm-mono" x="${x + 12}" y="${y + 58 + i * step}">${esc(it)}</text>`;
  });
  return `${t}</g>`;
}

function lines(text, max) {
  const words = String(text).split(' ');
  const out = [];
  let cur = '';
  for (const w of words) {
    if ((cur + ' ' + w).trim().length > max) {
      if (cur) out.push(cur);
      cur = w;
    } else cur = (cur + ' ' + w).trim();
  }
  if (cur) out.push(cur);
  return out;
}

const PAGE_CSS = `
.pg-design-map .rm-arch { overflow-x: auto; }
.pg-design-map .rm-svg { display: block; width: 100%; min-width: 980px; height: auto; }
.pg-design-map .rm-head { font: 600 11px var(--font-cond); letter-spacing: .12em; fill: var(--ink-3); }
.pg-design-map .rm-box rect { fill: var(--surface-2); stroke: var(--hairline-strong); stroke-width: 1; }
.pg-design-map .rm-landing rect { fill: color-mix(in srgb, var(--sign-yellow) 9%, var(--surface)); }
.pg-design-map .rm-core rect { fill: color-mix(in srgb, var(--sign) 7%, var(--surface)); stroke: var(--sign); stroke-width: 2; }
.pg-design-map .rm-act rect { fill: color-mix(in srgb, var(--series-2) 7%, var(--surface)); stroke: color-mix(in srgb, var(--series-2) 55%, transparent); }
.pg-design-map .rm-title { font: 600 12.5px var(--font-cond); fill: var(--ink); letter-spacing: .01em; }
.pg-design-map .rm-sub { font: 400 10.5px var(--font-ui); fill: var(--ink-3); }
.pg-design-map .rm-mono { font: 400 10.5px var(--font-mono); fill: var(--ink-2); }
.pg-design-map .rm-arrow { stroke: var(--ink-3); stroke-width: 1.25; fill: none; }
.pg-design-map .rm-arrow.faint { opacity: .55; }
.pg-design-map .rm-arrowhead { fill: var(--ink-3); }
.pg-design-map .rm-loop { stroke: var(--sign); stroke-width: 2.5; fill: none; }
.pg-design-map .rm-arrowhead-loop { fill: var(--sign); }
:root[data-theme="dark"] .pg-design-map .rm-loop { stroke: var(--link); }
:root[data-theme="dark"] .pg-design-map .rm-arrowhead-loop { fill: var(--link); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .pg-design-map .rm-loop { stroke: var(--link); }
  :root:not([data-theme="light"]) .pg-design-map .rm-arrowhead-loop { fill: var(--link); }
}
.pg-design-map .rm-loop-label { font: 600 11.5px var(--font-cond); letter-spacing: .06em; fill: var(--ink); }
.pg-design-map .rm-loop-sub { font: 400 11.5px var(--font-ui); fill: var(--ink-3); }

.pg-design-map .rm-list { list-style: none; padding: 0; margin: 0; }
.pg-design-map .rm-row { display: grid; grid-template-columns: 44px minmax(0, 1.05fr) minmax(0, 1fr); gap: 16px; padding: 16px 18px; border-top: 1px solid var(--hairline); }
.pg-design-map .rm-num { font: 700 22px/1 var(--font-cond); color: var(--sign); letter-spacing: .02em; padding-top: 2px; }
:root[data-theme="dark"] .pg-design-map .rm-num { color: var(--link); }
.pg-design-map .rm-goal { font-size: 14px; color: var(--ink); line-height: 1.5; }
.pg-design-map .rm-how p { margin-top: 8px; font-size: 13px; color: var(--ink-2); line-height: 1.5; }
.pg-design-map .rm-links { display: flex; flex-wrap: wrap; gap: 6px; }
.pg-design-map .rm-pill { display: inline-flex; align-items: center; height: 24px; padding: 0 10px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--sign) 40%, transparent); background: color-mix(in srgb, var(--sign) 7%, var(--surface)); color: var(--ink); font: 500 12.5px/1 var(--font-cond); letter-spacing: .02em; white-space: nowrap; }
.pg-design-map .rm-pill:hover { border-color: var(--sign); text-decoration: none; background: color-mix(in srgb, var(--sign) 14%, var(--surface)); }
.pg-design-map .rm-bring { list-style: none; padding: 0; margin: 0; display: grid; gap: 14px; }
.pg-design-map .rm-bring li { padding-bottom: 14px; border-bottom: 1px solid var(--hairline); }
.pg-design-map .rm-bring li:last-child { border-bottom: 0; padding-bottom: 0; }
.pg-design-map .rm-need { font: 600 15px/1.3 var(--font-cond); }
.pg-design-map .rm-proof { margin: 4px 0 8px; font-size: 13px; color: var(--ink-2); }
.pg-design-map .rm-demo { list-style: none; padding: 0; margin: 0; display: grid; gap: 14px; }
.pg-design-map .rm-demo li { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 10px; }
.pg-design-map .rm-step { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 6px; background: var(--sign); color: var(--sign-ink); font: 700 12px/1 var(--font-cond); box-shadow: inset 0 0 0 1.5px var(--sign), inset 0 0 0 2.5px rgba(244,246,242,.8); }
.pg-design-map .rm-demo-title { display: inline-flex; align-items: center; gap: 5px; font: 600 14.5px/1.25 var(--font-cond); color: var(--ink); }
.pg-design-map .rm-demo-title:hover { color: var(--link); text-decoration: none; }
.pg-design-map .rm-demo-page { font-size: 11.5px; color: var(--ink-3); margin-top: 1px; }
.pg-design-map .rm-demo p { font-size: 13px; color: var(--ink-2); margin-top: 3px; }
@media (max-width: 900px) {
  .pg-design-map .rm-row { grid-template-columns: 34px minmax(0, 1fr); }
  .pg-design-map .rm-how { grid-column: 2; }
}
`;
