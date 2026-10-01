// Tests · Evals · Review: how agent-written logic is proven before it is allowed to act.
// Four gates: unit + integration tests with hand-computed expectations, evals that score
// each "model" against golden truth, data contracts over every layer, and a change review
// that will not mark anything deployed without the evidence attached.
import { html, raw, esc, on, injectStyle, $ } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';
import { icon } from '../lib/icons.js';

let OFFS = [];

export function unmount() {
  OFFS.forEach((off) => off());
  OFFS = [];
}

export async function render(el, ctx) {
  injectStyle('page-proof', PAGE_CSS);
  unmount();
  OFFS.push(on(el, 'click', '[data-run]', async (e, btn) => {
    const what = btn.dataset.run;
    const label = btn.querySelector('span');
    const prev = label.textContent;
    btn.disabled = true;
    label.textContent = what === 'tests' ? 'Running (~20s)…' : 'Running…';
    try {
      const r = await api.post(`/api/proof/run-${what}`, {});
      if (what === 'tests') ui.toast(`${r.tests} tests, ${r.failures + r.errors} failing (${r.duration_s}s)`, r.failures + r.errors ? 'critical' : 'good');
      else if (what === 'evals') ui.toast(`${r.runs.filter((x) => x.gate === 'PASS').length} of ${r.runs.length} eval gates pass`, 'info');
      else ui.toast(`${r.runs.filter((x) => x.violations).length} contracts with violations`, 'info');
      window.dispatchEvent(new Event('ops:meta-changed'));
      await draw(el);
    } catch (err) {
      ui.toast(err.message, 'critical');
      btn.disabled = false;
      label.textContent = prev;
    }
  }));
  OFFS.push(on(el, 'click', '[data-eval-run]', (e, t) => openEval(t.dataset.evalRun)));
  OFFS.push(on(el, 'click', '[data-review]', (e, t) => openReview(t.dataset.review)));
  await draw(el);
}

let DATA = null;

async function draw(el) {
  const d = await api.get('/api/proof');
  DATA = d;
  const s = d.summary;
  const t = d.latest_test;
  el.innerHTML = html`
    ${ui.pageHeader({})}
    <div class="pg-proof">
      <div class="pf-gates">
        ${gate('Tests', 'flask', t ? `${t.tests - t.failures - t.errors} / ${t.tests}` : '—', t ? (t.failures + t.errors ? 'critical' : 'good') : 'neutral',
          t ? `passing · ${fmt.rel(t.ran_at)} · ${t.duration_s}s` : 'never run', 'Unit tests with hand-computed expectations, plus end-to-end runs of every closed loop.',
          html`<button class="btn sm" data-run="tests">${icon('play', 14)}<span>Run tests</span></button>`)}
        ${gate('Evals', 'target', `${s.evals_pass} / ${s.evals_total}`, s.evals_pass === s.evals_total && !s.evals_regressed ? 'good' : 'warning',
          `suites at or above threshold${s.evals_regressed ? ` · ${s.evals_regressed} fell since the last run` : ''}`,
          'Each model (normalizer, classifier, parser, ATP, MRP) scored against golden truth, and against its own last run.',
          html`<button class="btn sm" data-run="evals">${icon('play', 14)}<span>Run evals</span></button>`)}
        ${gate('Contracts', 'checklist', `${s.contracts_total - s.contracts_failing} / ${s.contracts_total}`, s.contracts_failing ? 'warning' : 'good',
          'clean', 'SQL invariants over landing, core and action layers; violations are the planted story problems.',
          html`<button class="btn sm" data-run="contracts">${icon('play', 14)}<span>Run contracts</span></button> <a class="btn sm btn-ghost" href="#/contracts">Details</a>`)}
        ${gate('Review', 'badge-check', `${s.reviews_deployed}`, s.reviews_open ? 'info' : 'good',
          `deployed · ${s.reviews_open} open`, 'No mapping, rule or logic change deploys without tests, evals, contracts and a named reviewer.',
          html`<a class="btn sm btn-ghost" href="#/loop">Changes come from the loops</a>`)}
      </div>

      ${ui.card({
        title: 'The method, for code a coding agent wrote',
        body: html`<ol class="pf-method">
          <li><b>Contracts</b> say what must always be true of the data (genealogy complete, promises dated, journals balanced). They run after every ingest and every closed-loop action. <span class="mono">ops/logic/contracts.py</span></li>
          <li><b>Tests</b> pin the logic to numbers worked out by hand: MRP records, ATP allocation rules, parser edge cases, recovery math. A regression the eval caught (ship date read as delivery date) now has its own test. <span class="mono">tests/</span></li>
          <li><b>Evals</b> score each model on realistic volume against truth. The simulator knows what physically happened, so the normalizer, trace, classifier and parser are graded, not just run. Every run is also compared with the one before it, so a drop is flagged even while the score still passes. <span class="mono">ops/logic/evals.py</span></li>
          <li><b>Review</b> ties a change to its evidence (test run, eval run, contract result) and a named approver. The closed loop cannot deploy a mapping change that fails a gate or makes an eval worse. <span class="mono">change_review</span></li>
        </ol>`,
      })}

      ${ui.card({ title: 'Eval suites', subtitle: 'Latest run per suite; click a row for the cases (failures first)', flush: true,
        body: html`<div data-evals></div>` })}

      <div class="grid">
        <div class="span-7">${ui.card({ title: 'Change reviews', subtitle: 'Every mapping, rule and logic change with the evidence that let it deploy',
          flush: true, body: html`<div data-reviews></div>` })}</div>
        <div class="span-5">${ui.card({ title: 'Latest test run', subtitle: t ? `${fmt.dt(t.ran_at)} · ${t.suite}` : '',
          body: testList(t), actions: d.tests.length > 1 ? html`<span class="muted small">history:</span>${charts.sparkline(d.tests.slice().reverse().map((x) => x.tests))}` : '' })}</div>
      </div>
    </div>`;

  ui.dataTable($('[data-evals]', el), {
    rows: d.suites, pageSize: 20,
    onRowClick: (r) => r.latest && openEval(r.latest.run_id),
    columns: [
      { key: 'suite_id', label: 'Suite', render: (r) => html`<div class="pf-suite"><span class="mono">${r.suite_id}</span><span>${r.name}</span></div>` },
      { key: 'method', label: 'Method', render: (r) => html`<div class="pf-suite"><span class="small">${fmt.title(r.method)}</span><span class="mono tiny muted">${r.target}</span></div>` },
      { key: 'score', label: 'Latest', num: true, value: (r) => (r.latest ? r.latest.score : null),
        render: (r) => (r.latest ? html`<div class="pf-score">${ui.meter({ value: r.latest.score, max: 1, tone: r.latest.gate === 'PASS' ? 'good' : 'critical' })}
          <span>${fmt.pct(r.latest.score, 1)}</span></div>` : '—') },
      { key: 'gate', label: 'Gate', value: (r) => (r.latest ? r.latest.gate : ''), render: (r) => html`<div class="pf-suite">
        ${r.latest ? ui.chip(r.latest.gate === 'PASS' ? 'good' : 'critical', r.latest.gate === 'PASS' ? 'Pass' : 'Fail') : '—'}
        <span class="tiny muted">${r.metric} ≥ ${fmt.pct(r.threshold, 1)} · ${r.latest ? fmt.int(r.latest.cases) : 0} cases</span></div>` },
      { key: 'vs_last', label: 'Vs last run', num: true,
        value: (r) => (r.latest && r.latest.prev_score != null ? r.latest.score - r.latest.prev_score : null), render: (r) => vsLast(r.latest) },
      { key: 'trend', label: 'Trend', sortable: false, render: (r) => charts.sparkline(r.history.map((h) => h.score)) },
    ],
  });
  ui.dataTable($('[data-reviews]', el), {
    rows: d.reviews, pageSize: 10,
    onRowClick: (r) => openReview(r.change_id),
    columns: [
      { key: 'change_id', label: 'Change', render: (r) => html`<span class="mono">${r.change_id}</span>` },
      { key: 'title', label: 'What', render: (r) => html`<div class="pf-cr"><span>${r.title}</span><span class="muted small">${r.author}</span></div>` },
      { key: 'evidence', label: 'Evidence', sortable: false, render: (r) => html`<div class="pf-ev">
        ${r.tests ? ui.chip(r.test_failures ? 'critical' : 'good', `${r.tests} tests`) : ''}
        ${r.suite_id ? ui.chip(r.eval_gate === 'PASS' ? 'good' : 'critical', `${r.suite_id} ${fmt.pct(r.eval_score || 0, 1)}`) : ''}
        ${r.contracts_ok != null ? ui.chip(r.contracts_ok ? 'good' : 'critical', r.contracts_ok ? 'contracts ok' : 'contracts fail') : ''}</div>` },
      { key: 'status', label: 'Status', render: (r) => html`<div class="pf-suite">${ui.statusChip(r.status)}<span class="tiny muted">${r.reviewer || ''}</span></div>` },
    ],
  });
}

// A run against the one before it. A fall is flagged even when both runs clear the threshold.
function vsLast(run) {
  if (!run || run.prev_score == null) return html`<span class="tiny muted">first run</span>`;
  const pts = (run.score - run.prev_score) * 100;
  if (run.regressed) return ui.chip('serious', `Fell ${fmt.num(-pts, 1)} pts`);
  return html`<span class="small muted">${run.score > run.prev_score ? `▲ ${fmt.num(pts, 1)} pts` : 'No change'}</span>`;
}

function gate(title, ic, value, tone, sub, blurb, actions) {
  return html`<section class="card pf-gate tone-${tone}">
    <div class="pf-gate-top"><span class="pf-gate-ic">${icon(ic, 18)}</span><span class="pf-gate-title">${title}</span>${ui.chip(tone, tone === 'good' ? 'Green' : tone === 'critical' ? 'Red' : tone === 'warning' ? 'Watch' : 'Info')}</div>
    <div class="pf-gate-val">${value}</div><div class="pf-gate-sub">${sub}</div>
    <p class="pf-gate-blurb">${blurb}</p>
    <div class="pf-gate-act">${actions}</div>
  </section>`;
}

function testList(t) {
  if (!t) return ui.empty('No test runs yet. Run the suite: python3 -m ops.proof');
  let results = [];
  try { results = JSON.parse(t.detail_json || '[]'); } catch (e) { results = []; }
  if (!Array.isArray(results) || !results.length) {
    return html`<p class="small muted">${t.tests} tests recorded (historical run, no per-test detail). Click “Run tests” for a fresh run with every test listed.</p>`;
  }
  const groups = {};
  for (const r of results) {
    const mod = r.test.split('.').slice(0, 2).join('.');
    (groups[mod] = groups[mod] || []).push(r);
  }
  return html`<div class="pf-tests">${Object.entries(groups).map(([mod, rs]) => html`<details ${rs.some((r) => r.status !== 'pass') ? raw('open') : ''}>
    <summary><span class="mono small">${mod}</span> ${ui.chip(rs.every((r) => r.status === 'pass') ? 'good' : 'critical', `${rs.filter((r) => r.status === 'pass').length}/${rs.length}`)}</summary>
    <ul>${rs.map((r) => html`<li class="pf-t ${r.status}">${ui.chip(r.status === 'pass' ? 'good' : r.status === 'skip' ? 'neutral' : 'critical', r.status)}
      <span class="mono small">${r.test.split('.').pop()}</span>${r.doc ? html`<span class="small muted">${r.doc}</span>` : ''}
      ${r.message ? html`<span class="small pf-msg">${r.message}</span>` : ''}</li>`)}</ul>
  </details>`)}</div>`;
}

async function openEval(runId) {
  const body = ui.drawer.open({ title: 'Eval run', subtitle: 'Loading…', body: ui.loading(), width: 720 });
  const d = await api.get(`/api/proof/eval/${runId}`);
  const r = d.run;
  let m = {};
  try { m = JSON.parse(r.metrics_json || '{}'); } catch (e) { m = {}; }
  const conf = m.confusion ? Object.entries(m.confusion).sort((a, b) => b[1] - a[1]) : null;
  ui.drawer.open({
    title: `${r.suite_id} · ${r.name}`,
    subtitle: html`${fmt.dt(r.ran_at)} · version <span class="mono">${r.subject_version}</span> · ${r.passed}/${r.cases} cases · ${fmt.pct(r.score, 2)} · ${r.gate}${r.prev_score != null ? ` · last run ${fmt.pct(r.prev_score, 2)}` : ''}`,
    width: 760,
    body: html`${r.regressed ? ui.callout({ tone: 'warning', title: 'Regressed since the last run',
        body: `Scored ${fmt.pct(r.score, 2)}, below the previous run's ${fmt.pct(r.prev_score, 2)}. A fall is flagged whether or not the gate passes.` }) : ''}
      <p class="small">${r.description}</p>
      ${m.precision != null ? html`<div class="pf-ev">${ui.chip(m.precision >= 1 ? 'good' : 'critical', `precision ${fmt.pct(m.precision, 1)}`)}
        ${ui.chip(m.coverage >= 0.8 ? 'good' : 'warning', `coverage ${fmt.pct(m.coverage, 1)}`)} ${ui.chip('neutral', `abstained correctly ${m.abstain_correct}`)}</div>` : ''}
      ${conf ? html`<h4 class="section-title">Confusion (truth → predicted)</h4><table class="table dense"><tbody>${conf.map(([k, v]) => {
        const [a, b] = k.split('->');
        return html`<tr class="${a === b ? '' : 'pf-bad'}"><td class="mono small">${a}</td><td>→</td><td class="mono small">${b}</td><td class="num">${v}</td></tr>`;
      })}</tbody></table>` : ''}
      ${m.historical ? html`<p class="muted small">Historical run (summary only).</p>` : html`<h4 class="section-title">Cases, failures first</h4>
      <div class="table-wrap pf-cases"><table class="table dense"><thead><tr><th>Case</th><th>Expected</th><th>Actual</th><th>Result</th></tr></thead>
      <tbody>${d.cases.map((c) => html`<tr class="${c.passed ? '' : 'pf-bad'}"><td class="mono small">${c.case_id}</td>
        <td class="mono small pf-cell">${c.expected || ''}</td><td class="mono small pf-cell">${c.actual || ''}${c.note ? html`<div class="pf-note">${c.note}</div>` : ''}</td>
        <td>${ui.chip(c.passed ? 'good' : 'critical', c.passed ? 'pass' : 'fail')}</td></tr>`)}</tbody></table></div>`}`,
  });
}

function openReview(id) {
  const r = (DATA.reviews || []).find((x) => x.change_id === id);
  if (!r) return;
  let checklist = [];
  try { checklist = JSON.parse(r.checklist_json || '[]'); } catch (e) { checklist = []; }
  ui.drawer.open({
    title: `${r.change_id} · ${r.title}`,
    subtitle: html`${fmt.title(r.kind)} · <span class="mono">${r.component}</span> · ${fmt.title(r.status)}`,
    body: html`<dl class="pf-kv">
        <dt>Author</dt><dd>${r.author}</dd><dt>Proposed</dt><dd>${fmt.dt(r.proposed_at)}</dd>
        <dt>Reviewer</dt><dd>${r.reviewer || '—'}</dd><dt>Decided</dt><dd>${r.decided_at ? fmt.dt(r.decided_at) : '—'}</dd>
        ${r.decision_id ? html`<dt>From decision</dt><dd><a class="id-link" href="#/loop?d=${r.decision_id}">${r.decision_id}</a></dd>` : ''}
      </dl>
      <h4 class="section-title">Diff</h4><p>${r.diff_summary}</p>
      <h4 class="section-title">Evidence</h4>
      <div class="pf-ev">${r.tests ? ui.chip(r.test_failures ? 'critical' : 'good', `test run: ${r.tests} tests, ${r.test_failures || 0} failing`) : ui.chip('neutral', 'no test run linked')}
        ${r.suite_id ? ui.chip(r.eval_gate === 'PASS' ? 'good' : 'critical', `${r.suite_id}: ${fmt.pct(r.eval_score || 0, 2)} (${r.eval_gate})`) : ''}
        ${r.contracts_ok != null ? ui.chip(r.contracts_ok ? 'good' : 'critical', r.contracts_ok ? 'contracts clean' : 'contract violations') : ''}</div>
      ${checklist.length ? html`<h4 class="section-title">Checklist</h4><ul class="pf-check">${checklist.map((x) => html`<li>${icon('check', 14)} ${x}</li>`)}</ul>` : ''}
      ${r.notes ? html`<h4 class="section-title">Notes</h4><p>${r.notes}</p>` : ''}`,
  });
}

const PAGE_CSS = `
.pg-proof { display: grid; gap: 16px; }
.pg-proof .grid { margin: 0; }
.pf-gates { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
.pf-gate { --tone: var(--ink-3); padding: 14px 16px; border-top: 3px solid var(--tone); display: grid; gap: 4px; align-content: start; }
.pf-gate.tone-good { --tone: var(--good); } .pf-gate.tone-warning { --tone: var(--warning); }
.pf-gate.tone-critical { --tone: var(--critical); } .pf-gate.tone-info { --tone: var(--info); }
.pf-gate-top { display: flex; gap: 8px; align-items: center; }
.pf-gate-ic { display: inline-flex; color: var(--ink-2); }
.pf-gate-title { font: 700 13px/1 var(--font-cond); letter-spacing: .08em; text-transform: uppercase; flex: 1; }
.pf-gate-val { font: 600 30px/1.1 var(--font-ui); margin-top: 6px; }
.pf-gate-sub { font-size: 12.5px; color: var(--ink-3); }
.pf-gate-blurb { font-size: 12.5px; color: var(--ink-2); margin: 6px 0 8px; line-height: 1.45; }
.pf-gate-act { display: flex; flex-wrap: wrap; gap: 6px; }
.pf-method { margin: 0; padding-left: 18px; display: grid; gap: 8px; font-size: 13.5px; color: var(--ink-2); line-height: 1.5; }
.pf-method b { color: var(--ink); }
.pf-method .mono { color: var(--ink-3); font-size: 11.5px; }
.pf-suite { display: grid; gap: 2px; }
.pf-score { display: grid; grid-template-columns: 80px auto; gap: 8px; align-items: center; justify-content: end; }
.pf-cr { display: grid; gap: 2px; }
.pf-ev { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0; white-space: normal; max-width: 300px; }
.pf-suite { white-space: normal; }
.pf-cr { white-space: normal; min-width: 220px; }
.pf-tests details { border-top: 1px solid var(--hairline); padding: 6px 0; }
.pf-tests summary { cursor: pointer; display: flex; gap: 8px; align-items: center; }
.pf-tests ul { list-style: none; margin: 6px 0 0; padding: 0; display: grid; gap: 6px; }
.pf-t { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; }
.pf-msg { color: var(--critical); width: 100%; }
.pf-cases { max-height: 460px; }
.table td.pf-cell { max-width: 260px; white-space: normal; overflow-wrap: anywhere; }
.pf-note { color: var(--serious); font-family: var(--font-ui); }
tr.pf-bad td { background: color-mix(in srgb, var(--critical) 7%, transparent); }
.pf-kv { display: grid; grid-template-columns: 130px 1fr; gap: 4px 12px; margin: 0 0 8px; font-size: 13px; }
.pf-kv dt { color: var(--ink-3); }
.pf-kv dd { margin: 0; }
.pf-check { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; font-size: 13px; }
.pf-check li { display: flex; gap: 6px; align-items: center; }
`;
