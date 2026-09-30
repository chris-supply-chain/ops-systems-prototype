# Ops OS: code conventions

Standard library Python 3.9+ (no pip), SQLite, vanilla ES modules (no build step,
no npm, no CDN JS). Everything runs with `python3 app.py`.

## Layout

```
app.py                      CLI: build DB if missing, serve on :8000
ops/schema.sql              the relational model (source of truth for tables)
ops/config.py  ops/db.py    paths; connect(), q(), q1(), val(), as_of(), now()
ops/generate/               mock world simulator -> raw_* landing tables + core tables
ops/ingest/                 normalizers: raw_* -> canonical core (CM MES, ASN, carrier, 3PL, confirmations, warranty)
ops/logic/                  decision logic: genealogy, mrp, atp, exceptions, actions (closed loop), contracts, reconcile, process, scorecard
ops/api/router.py           @get / @post regex router, Request, HttpError
ops/api/server.py           stdlib ThreadingHTTPServer; imports every module in ops/api/routes/
ops/api/routes/<area>.py    one module per page/area; thin, delegates to ops/logic
web/index.html              shell
web/css/app.css             design system (tokens, layout, components, charts)
web/js/app.js               NAV, router, shell chrome, global search
web/js/lib/*.js             dom, api, format, ui, charts, icons
web/js/pages/<module>.js    one module per page
tests/test_*.py             unittest; `python3 -m unittest discover -s tests -v`
```

## The clock

The dataset has its own "today": `meta.as_of` (date) and `meta.now_utc` (instant).
Backend logic uses `ops.db.as_of(conn)` / `now(conn)`. The frontend gets them
from `GET /api/meta` and passes `now` to `fmt.rel()`. Never use the wall clock
for business logic.

## Backend routes

```python
from ops.api.router import get, post, HttpError
from ops.db import q, q1, val, as_of, now

@get(r"^/api/genealogy/trace$")
def trace(req):
    sn = req.arg("q")                     # query string arg (str), default None
    if not sn:
        raise HttpError(400, "q is required")
    return {"root": ..., "nodes": [...]}  # json.dumps(default=str)
```

- Pattern `^/api/<area>/<thing>$`. Regex groups are passed as `req.params`.
- `req.conn` is a fresh sqlite3 connection per request (dict rows, FKs on). POST
  handlers are committed automatically, and exceptions roll back.
- GET handlers never write.
- A POST that changes business data (a decision, a hold, a chargeback step, an MRP run) ends with
  `contracts.after_action(conn)`, so the contract results the app shows are never older than the data.
- Keep each endpoint under about 200ms on the full dataset. Aggregate in SQL and
  add an index in `ops/schema.sql` when needed.
- Shape the payload for the page, so the page doesn't re-join. Return ISO strings
  for dates and plain numbers.

## Feed loaders

- A feed can send the same fact twice, as a retry or as a correction with a new timestamp, so loaders are
  idempotent. An event already ingested is marked `DUPLICATE`. Every insert into `genealogy` ends with
  `ON CONFLICT DO NOTHING`, and the unique index `ux_gen_current_link` (one current link per parent, slot and
  child) makes a second copy impossible even when the event dedup misses.
- Two *different* facts that conflict, such as a second drive unit in one vehicle, are not duplicates. Land them
  and let a contract flag them; rejecting one would hide which record is wrong.

## Data dictionary

- Every table and view says what one row is (`GRAIN`) and which columns make it unique, in
  `ops/api/routes/sandbox_guide.py`. A natural primary key or a UNIQUE constraint is read from the schema. Where
  the primary key is only a row number, declare the key in `ROW_KEY`, or say in `NUMBERED` why the row number is
  the identity (a landing table, a run log). The Sandbox shows the key on every table.
- `test_system` checks every key on the built data and again after every closed loop has run. The app's clock is
  the dataset's and stands still, so anything written during a session shares one timestamp: a key like
  `(contract_id, ran_at)` holds on fresh data and breaks the moment a loop runs.

## Frontend page contract

`web/js/pages/<module>.js`:

```js
import { html, on } from '../lib/dom.js';
import { api } from '../lib/api.js';
import { ui, link } from '../lib/ui.js';
import { fmt } from '../lib/format.js';
import { charts } from '../lib/charts.js';

export async function render(el, ctx) {
  // ctx.params: path segments after the route, e.g. #/genealogy/LV1-26W31-0412 -> ['LV1-26W31-0412']
  // ctx.query:  URLSearchParams for the hash query (#/atp?sku=LV1-DUNE)
  // ctx.go(hash), ctx.setQuery({k: v}) (merges into the hash query and re-renders)
  // ctx.meta: {as_of, now_utc, ...} from /api/meta
  const data = await api.get('/api/genealogy/trace', { q: 'LV1-...' });
  el.innerHTML = html`${ui.pageHeader({...})} ...`;   // html`` escapes by default
  on(el, 'click', '[data-action=hold]', (e, target) => { ... });
}
export function unmount() {}   // optional: clear timers/listeners created outside `el`
```

Rules:
- Build markup with the `html` tagged template. It escapes interpolations, and
  nested `html` results and arrays compose. Use `raw()` only for trusted SVG you
  built yourself.
- Use `on(el, event, selector, handler)` for delegated events on the page root.
  The router hands each render a fresh `el`, so those listeners go away with the
  page. Anything bound to `document`, `document.body` or `window` (drawer
  actions, observers, timers) must be removed in `unmount`. Once the user
  navigates away, a page's `ctx.setQuery` does nothing, so a closing drawer
  can't rewrite the next page's URL.
- Put every hand-built table inside `<div class="table-wrap">` (`ui.dataTable`
  already does this), so a wide table scrolls inside its card at phone width
  instead of scrolling the page.
- Link entities with `link.serial(sn)`, `link.lot(id)`, `link.order(id)`,
  `link.po(poId, lineNo)`, `link.shipment(id)`, `link.supplier(id, name)` and
  `link.item(id)`. That's how the app feels like one system.
- Put charts in `ui.card({... tableToggle: true})` and build them with `charts.*`.
- Page-specific CSS goes through `injectStyle('page-<module>', css)` from dom.js,
  prefixed with `.pg-<module>`. Prefer shared components.

## Navigation: modules, pages and functions

`web/js/app.js` holds two tables. `PAGES` has one entry per page module (label, icon, lede, goal). `MODULES` is
the menu. The sidebar shows one module at a time, chosen in the switcher at its top (or press `M`). A module lists its
pages in sections, and under a page the functions it deep-links to with `?tab=`. A page belongs to the first module
that lists it. Items marked `ref` are cross-listed shortcuts into another module, and the menu stays on the current
module when you follow one. Each page gets an app code from its home module, such as `TMS-01`. The code shows in the
page header and matches in search.

| Module | Code | Pages (route → page module) |
|---|---|---|
| Control Tower | CT | `tower` → control-tower, `loop` → closed-loop, `systems` → systems |
| Manufacturing Execution | MES | `production`, `genealogy`, `cm-feed` |
| Production Scheduling | PS | `schedule` → line-schedule, `mps`; shortcut: ATP build queue |
| Material Planning | MRP | `mrp`, `atp` |
| Replenishment | REP | `replenishment`; shortcut: MRP |
| Warehouse Management | WMS | `inventory`; shortcuts: ATP fulfillment queue, quality holds |
| Transportation Management | TMS | `shipments` |
| ERP · Procurement & Finance | ERP | `suppliers`, `erp` |
| Quality Management | QMS | `quality`, `warranty` |
| Data Platform | DP | `integrations`, `sandbox`, `contracts`, `proof`, `process` → process-lab, `buy-build` → buy-vs-build |
| (About, outside the menu) | INFO | `design-map` |

To add a page: add it to `PAGES`, list it in a module (with its `tabs` if the page reads `?tab=`), and write
`web/js/pages/<module>.js`. The shell refuses to boot if a page is in no module or a module lists an unknown route.

Default route: `#/tower`. Entity deep links: `#/genealogy?q=<serial|lot>`,
`#/suppliers?po=<po_id>`, `#/shipments?id=<shipment_id>`, `#/atp?order=<order_id>`,
`#/warranty?claim=<id>`, `#/sandbox?table=<name>`.
