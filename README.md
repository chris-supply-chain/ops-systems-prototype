# Ops OS: operations systems prototype

**A unified operations interface prototype: toggle between QMS, replenishment, production scheduling, TMS and six
more modules in one place, all running on one shared data model.**

> This is a prototype built to explore unified operations UX. All data comes from a simulator, and every company,
> product, part number and price in it is fictional.

![Control Tower: one picture from the contract manufacturer's line to the customer's door](docs/screenshots/control-tower.png)

## The problem

Operations systems live in silos. The MES knows what was built, the QMS knows what failed, the ERP knows what was
paid, the TMS knows what is on the water, and planning lives in spreadsheets. People bridge the gaps by email and
Excel. So when a defect shows up in the field, it takes days to find the units still on the shelf, the supplier that
caused it and the customers who will feel it.

Ops OS explores one view across the manufacturing value chain: suppliers, a contract manufacturer's assembly line, a
battery-pack line, ocean freight, a 3PL warehouse and the customer's door. Every module reads the same data, so a
problem spotted in one module becomes a decision that updates the others. A quality hold reaches the warehouse, the
supplier chargeback reaches the ledger, and the new delivery date reaches the customer.

## Run it locally

You need Python 3.9 or newer. On macOS and Linux there is nothing else to install and no build step. It is tested on
macOS with Python 3.9 and 3.12.

```bash
git clone https://github.com/chris-supply-chain/ops-systems-prototype.git
cd ops-systems-prototype
python3 app.py
```

The first run builds the mock database (about 5 seconds, about 55 MB in `data/`), starts a local server and opens
<http://localhost:8000> in your browser. Press `Ctrl+C` to stop it. On Windows, Python needs the time-zone database
first: run `py -m pip install tzdata`, then `py app.py`.

| To do this | Run |
|---|---|
| Rebuild the exact dataset the walkthrough uses | `python3 app.py --reset --as-of 2026-09-26` |
| Serve on another port, without opening a browser | `python3 app.py --port 8001 --no-browser` |
| Run the test suite | `python3 -m unittest discover -s tests` |

## Modules

Pick a module in the switcher at the top of the sidebar, or press `M`. The menu below it then shows that module's
pages. Search finds any page, serial number, lot, order or PO.

![The module switcher: ten modules in one app](docs/screenshots/module-switcher.png)

| Module | What it shows and does |
|---|---|
| **Control Tower** | One picture from factory to customer, problems ranked by impact, and proposed decisions you can review and execute. |
| **Manufacturing Execution (MES)** | Work in progress, first-pass yield and defects by station, the full parts history of every serial number, and the contract manufacturer's live data feed. |
| **Production Scheduling** | Each line's daily plan against its capacity, the master production schedule, and the next shift's build sequence. |
| **Material Planning (MRP)** | What to buy and when, by part and by day, plus the delivery date a new order can be promised (available-to-promise). |
| **Replenishment** | How each part is restocked at each site (reorder point, min-max, vendor-managed, consignment) and how much safety stock it needs. |
| **Warehouse Management (WMS)** | Inventory everywhere it sits, from supplier stock to the 3PL shelf, checked against the warehouse's own count. |
| **Transportation Management (TMS)** | Ocean shipments, customs filings, hazardous-goods trucking for batteries, last-mile delivery and freight spend. |
| **ERP · Procurement & Finance** | Purchase orders and supplier promise dates, forecasts shared with suppliers, quotes and engineering changes, costed bills of materials, invoice matching and the general ledger. |
| **Quality Management (QMS)** | Incoming inspection, process control charts, quality holds, corrective actions, and warranty claims traced back to a supplier lot and billed to that supplier. |
| **Data Platform** | Every inbound data feed step by step (email and Excel included), the full data model with read-only SQL, automated data checks, and the tests that prove the logic. |

## A ten-minute tour

[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) follows one story across the modules, with the numbers you will see at
each step:

1. **Control Tower.** Warranty claims are clustering on one batch of battery material.
2. **Genealogy.** Trace the batch forward: 203 affected units are still in our hands, and 1,303 are with customers.
3. **Closed Loop.** Execute the containment. It places 295 holds and tells the warehouse and the pack line.
4. **Warranty.** The claims become a $9,652.30 chargeback to the cell supplier.
5. **ERP.** The accepted chargeback posts a balanced entry to the ledger.
6. **Data Sandbox.** One SQL query shows every decision and every row it wrote.
7. **CM Feed.** The contract manufacturer changed its data format without notice. Bad messages were held and
   replayed, and nothing was lost.
8. **Material Plan.** A late part would stop the pack line on a specific day, and the plan pulls in supply to prevent it.
9. **ATP.** A delayed ship moves 382 customer delivery dates in one action, with a note to each customer.
10. **Tests · Evals · Review.** How every rule is proven before it runs.

## How it works

```
partner systems ──▶  LANDING    every message stored exactly as received
                        │       normalizers: versioned mappings, quarantine and replay
                        ▼
                     CORE       one relational model, foreign keys enforced (92 tables, 4 views)
                        │       planning (MRP, ATP), parts tracing, exception rules
                        ▼
                     ACTION     decisions, plus every row each decision writes
                        │       holds, expedites, debit memos, new delivery dates
                        ▼
                     back to the systems that must act

ASSURANCE: tests, evals, data contracts and change reviews gate every rule
```

- **One data model.** All ten modules read and write the same SQLite database, so there is nothing to reconcile
  between screens.
- **Closed loops.** Four loops (quality, supply, promise, data) turn a detected problem into a proposed decision
  with its evidence. Executing it writes real rows and messages, and every write carries the decision's ID.
- **A simulated world.** A deterministic simulator generates about a year of operations: a contract manufacturer
  in Taiwan, a pack line in California, weekly ocean sailings, a 3PL in Nevada and 25 suppliers across three tiers.
  It plants problems for the loops to find. The same seed always gives the same data.
- **Proven, not assumed.** 82 automated tests with hand-computed answers, 6 evaluations scored against the
  simulator's ground truth, 21 data contracts that re-run after every action, and a review record for every rule
  change. The prototype was built with an AI coding agent, and this proof layer is what keeps that safe.

## Tech stack

- **Backend:** Python 3.9+ standard library only (`http.server`, `sqlite3`), with a JSON API of about 100 routes.
- **Database:** SQLite, a single file with 92 tables and 4 views.
- **Frontend:** plain JavaScript ES modules and CSS. No framework, no dependencies, no build step.
- **Data:** a deterministic simulator that also produces the raw inputs: EDI-style messages, emails and Excel files.
- **Tests:** Python `unittest`, including end-to-end runs of every closed loop.

## Project layout

```
app.py            start here: builds the database if it is missing, then serves the app
ops/schema.sql    the data model
ops/generate/     the simulator that creates the mock world
ops/ingest/       loaders for each data feed (manufacturing, shipping, 3PL, email + Excel, warranty)
ops/logic/        planning, parts tracing, closed-loop decisions, data contracts, evals
ops/api/          the HTTP server and JSON routes
web/              the browser app: shell, design system, one module per page
tests/            unit, integration and end-to-end tests
docs/             walkthrough, code conventions, design system, screenshots
```

## Status

This is a prototype built to explore unified operations UX. It is not production software: it runs locally for one
user, with no authentication, and every number in it comes from the simulator.
