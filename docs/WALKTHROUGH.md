# Walkthrough: ten steps, one story

A ten-step tour of Ops OS, about ten minutes, with the numbers you will see on screen. A defect found in the field is
traced, contained, charged back to the supplier and proven, and the plan, promises and data feeds react along the way.
The numbers are for the canonical dataset (`--as-of 2026-09-26`, seed 7) and were read from the running app.

## Before you start

```bash
cd ops-systems-prototype
python3 app.py --reset --generate-only --as-of 2026-09-26   # the exact numbers in this walkthrough; about 5 seconds
python3 -m ops.proof                                         # records the test run step 10 shows; about 15 seconds
python3 app.py                                               # serves http://localhost:8000
```

- Use a browser window at about 1440 px wide, in light mode.
- To start over, press **Reset demo data** in the sidebar. It rebuilds the same world, with the same serials and
  dollars, in about 5 seconds.
- Execute the decisions in the order below. The numbers in steps 6 and 9 depend on containment running first.
- If port 8000 is busy, run `python3 app.py --port 8001`.

## The ten steps

The sidebar shows one module at a time. Switch modules with the picker at its top (or press `M`), or type a
page name in search. Each step names the module in brackets.

### 1. Control Tower: one picture, one top problem [CT]

**Look at** the KPI row and the flow line:

- 224 of 232 committed vehicles built this week (▼54 vs the prior week).
- 559 vehicles on the water in 4 containers.
- 2,260 open orders.
- 91% delivered on first promise (1,025 deliveries).

The flow reads CM line 70 → Port of Taichung 276 → Pacific 559 → Oakland + customs 150 → Reno 3PL 145 → last mile
111 → 287 delivered this week. Packs join at the 3PL: pack line 92, 3PL packs 194.

**In one line:** "The dip is real: the CM in Taichung was closed Friday for the Mid-Autumn Festival. Everything on this screen
comes from partner feeds landed raw and normalized. The queue below is ranked by severity, and each item shows its
impact."

**Top exception:** capacity-fade claims clustering on cathode batch **CA2605-103**. There are 12 claims in 30 days
($5,416) on cell lots CL2606-105 (8) and CL2606-104 (4). That is **16.7 per 1,000 pack-months in service vs 1.2 for
the rest of the fleet**. 203 units are still in our control and 1,303 are with customers.

### 2. Genealogy: trace it forward [MES]

Open **Genealogy** and pick **The field issue** (CA2605-103).

- 1,515 top-level units were built from the batch (3,006 counting sub-assemblies).
- **203 are still stoppable**, and 80 customer orders are allocated to them.
- 1,303 are with customers.
- 12 field claims, plus 1 rejected and not counted.
- The upstream trace goes to a lithium-carbonate lot at a tier-3 supplier.
- The chart, per 1,000 pack-months in service: CL2606-104 is 10.3 (389 pack-months) and CL2606-105 is 33.4 (239).
  CL2607-106 and -107 show zero, but have only 86 and 4 pack-months in service.

**In one line:** "Rates are per month in service. The young lots haven't had time to fail, so they show zero and still carry
the defect. Their cells came from the same cathode batch, per the supplier's certificates. So the scope is the batch,
not the lot."

### 3. Closed Loop: execute the containment [CT]

Open **Closed Loop**, then **D-0105 → Review and decide**.

**Look at** the evidence: 12 claims, 16.7 vs 1.2 per 1,000 pack-months, 14× the rate.

**Look at** what it will do:

- Hold 203 affected units (123 packs, 80 vehicles), plus the 87 packs kitted with those vehicles so no kit ships
  split: **290 serial holds + 5 lot holds**.
- Recovery (est.) $9,652.

Click **Execute**.

**Look at** what it wrote:

- hold 295 and unit 290: 274 at the Reno 3PL, 16 at Fremont.
- 4 outbound messages: a hold instruction to the 3PL WMS, a hold to the pack-line MES, an 8D request to Kestrel, and
  a service-campaign draft.
- CAPA opened.
- Chargeback **CB-0005 drafted for $9,652.30**, the same number as the estimate.

**In one line:** "The proposal and the execution use the same math, and a test pins them together. Everything ran in one
transaction, and each row carries the decision id."

### 4. Warranty & Chargebacks: claims become money [QMS]

- **Look at** the signal: *Cell lot CL2606-105: 33.4 per 1,000 pack-months vs a 2.3 baseline (15×).*
- In the chargeback table, take CB-0005 through **Send to supplier**, then **Record acceptance** (Kestrel accepts),
  then **Post to ERP**.

**In one line:** "Each line is one claim priced by Kestrel's recovery terms, plus $12 per affected unit for inspection and sorting. Only
diagnosed claims are billed. Open or rejected claims never are, and a data contract checks it after every action."

### 5. ERP: it lands in the financial system [ERP]

Open **ERP Core → General ledger**. The entry is **JE-2026-00003, debit memo DM-0005**:

- Dr 2000 Accounts payable $9,652.30
- Cr 5410 Warranty recovery: suppliers $9,652.30
- Balanced.

**In one line:** "A defect found in the field is now a balanced entry in the books, traceable back to every claim."

### 6. Data Sandbox: prove it in SQL [DP]

Open **Data Sandbox → SQL → Example queries… → Closed-loop audit trail → Run**. It shows D-0105 with 295 holds, 4
messages, CB-0005 and JE-2026-00003. Each chargeback step (send, accept, post) is its own recorded decision.

**In one line:** "96 tables and views, 153 foreign keys enforced on every write. Nothing here is a screenshot."

Optional: on the **Relationships** tab, pick the use case **Recall scope: everything built from a bad batch**. The map
lights up the six tables the question walks through, in order: claim, lot link, lot, genealogy, unit, order line.
Each step says what one row of that table is. **Run** splits vehicles and packs by where they are. Still in our control:
80 kitted vehicles plus 123 packs not yet kitted (107 at the 3PL, 16 at Fremont), which is the Genealogy page's 203.
With customers: 1,192 delivered plus 111 shipped vehicles, its 1,303.

Optional: on **Tables → genealogy**, read the two lines under the name. **Each row** is one link, not one part: a
complete kit is 19 rows, 3 levels deep, and 40 cells drawn from one lot are one row. **Row key** says what makes a row
unique and what guarantees it: the database enforces it for current links, and `genealogy_id` is only a row number.

**In one line:** "Keys make a repeated message harmless. Contracts catch contradictions."

### 7. CM Feed: when the CM changes its MES [MES]

**Look at** the pipeline strip and the incident list:

- 29,723 payloads kept verbatim.
- 686 timestamps corrected (Taipei local time labeled as UTC).
- 131 duplicates suppressed.
- **7 messages quarantined from station S65**, a rework bay the CM opened without telling us.
- 18 drive units installed with no supplier ASN.

**In one line:** "In August the CM's MES upgrade switched to new field names and timestamp formats mid-shift. The eval dropped to 93%, messages went to
quarantine instead of into the core, and mapping v2 shipped as data, not code, then replayed. S65 is the same pattern
happening right now."

### 8. Material Plan · MRP: the line-stop date is computed [MRP, then PS]

Open **MRP → BMS-B**:

- 43 on hand, safety stock 250, lead time 42 days.
- It **runs out Sep 30** and bottoms at −145 on Oct 2.
- The next receipt, PO 4500134-5 (750 boards), is promised Oct 5 but needed Sep 29.
- The deviation DEV-0012 lets up to 132 rev-A boards cover until Sep 30.

Before executing, switch to **Production Scheduling → Line Schedule**. The pack line shows the same shortage as
MRP cuts of −17, −64 and −64 packs on Sep 30, Oct 1 and Oct 2. The callout above the board also makes a second
point. The CM commits each line at exactly its rated capacity, which leaves no time for three colour changeovers a
day, so Monday's run on CM line 2 finishes 26 minutes past the shift (line 1: 24). That is why the lines built 94%
of commit last week.

Execute **D-0106** from the Closed Loop page: 250 boards by air, arriving Sep 29 ($2,825), DEV-0012 extended, MRP
re-run. Result: **gap closed**.

**In one line:** "A new order can't fix a shortage inside lead time, so MRP flags it as a shortage and the loop pulls in
existing supply instead."

### 9. ATP & Queues: re-promise, don't surprise [MRP]

- 2,260 open orders.
- **198 promises at risk**, +4.2 days on average, pegged to the Pacific Aurora 057E sailing, which is 6 days late.
- A new order today promises **Nov 26**, 61 days out, bound by kitting capacity.

Execute **D-0108**. It re-promises **382 orders** and says why: *"Proposed for 198 orders; 382 were at risk at
execution (supply changed in between, e.g. containment holds)."*

**In one line:** "Decisions re-check at execution time. The containment took 290 units out of supply, so more promises moved.
Every customer gets a note before they would have noticed."

### 10. Tests · Evals · Review: how it's proven [DP]

At the start:

- Tests 82 / 82, recorded by the proof run in "Before you start".
- Evals 4 of 6. EV-CM-MES (98.8%) and EV-GENEALOGY (83.8%) fail on purpose, because of S65.
- Contracts 12 of 21 clean.

Execute **D-0107**. Its gates are unit tests, EV-CM-MES ≥ 99.5% (now 100%), EV-GENEALOGY = 100%, and C-GEN-05
dropping from 3 violations to 0. Change review **CR-0015** is deployed.

Afterwards: evals 6 / 6 and contracts 14 / 21. **In one line:** "The 7 still failing are real open problems with owners,
such as 473 gaskets used past a deviation and a PO priced at a superseded contract price. The loops fix what they own;
contracts keep the rest visible."

Optional: press **Run tests** to run the real suite live, which takes about 20 seconds.

## Design notes

**Is this real data?**
No. Every company, product, part number and price is fictional. A deterministic simulator builds a year of operations: a CM in Taichung, a pack line in Fremont, weekly sailings
to Oakland, a 3PL in Reno, and 25 suppliers across three tiers. The problems are planted on purpose. The same seed
always gives the same data, and a test enforces it.

**How do you know the numbers are right?**
There are four layers:

- 82 tests with hand-computed expectations, covering MRP netting, ATP allocation, parsers and recovery math, plus
  end-to-end runs of every loop.
- Six evals graded against ground truth the simulator knows.
- 21 data contracts that run after every action.
- A review record with its gates for every rule or mapping change.

The whole suite also passes for any dataset date: every weekday, and dates from Sep 2026 to Jun 2027.

**Why flag at 5× the fleet rate, with a floor of 10 per 1,000 pack-months?**
Two reasons. Counting claims per pack without normalizing for time in service hides young lots. Without a floor, a
fleet with almost no claims would make any three claims look like a cluster. I checked the rule across a range of
background failure rates. It flags only the true batch, and when the batch is only 3× the fleet, it correctly stays
quiet.

**Why contain the batch rather than the lot?**
Kestrel's certificates link CL2606-104 through 107 to cathode batch CA2605-103. The lots with no claims have only 90
pack-months in service between them, too few to show a defect that appears after weeks of use.

**What if the supplier disputes?**
CB-0002 shows that path. Formosa Assembly blames the pad compound from its brake supplier and wants a joint teardown.
Claims where the technician's evidence contradicts the classifier are held for review instead of being billed.

**Why did the re-promise move 382 orders when it said 198?**
The proposal is computed when it's raised; execution re-checks against current supply. The containment in between
took 290 units out of supply. The decision reports both numbers rather than hiding the difference.

**How would this connect to the real systems?**
The landing layer keeps every payload as received, and mappings are versioned data, so a partner's change is a new
mapping row and a replay. The Integration Hub compares the options for each source by latency, manual touches, error
rate and build cost:

- An API endpoint at the CM.
- EDI 855 for the top suppliers.
- A portal for the long tail.
- Precision-first email and Excel parsing where there's no API, which abstains rather than guesses.

**What stops genealogy from double counting when the CM re-sends data?**
Two layers, with different jobs. A message sent twice is caught when it lands: same serial, station, time and result.
A retry with a corrected timestamp gets past that, so the database enforces the grain itself, one current link per
parent, slot and child, and every loader skips a link it already has instead of failing the load. A contradiction is
different. A second drive unit in a vehicle whose first was never removed isn't a duplicate, so it lands and contract
C-GEN-06 flags the slot, because rejecting it would hide which record is wrong. A 3PL re-allocating a pack is neither:
it's a real change, so the old kit link closes and stays as history.

**Where would a real rollout start?**
The data spine behind this Control Tower: the CM feed, genealogy and exceptions. Then one loop end to end, containment
to chargeback to the ERP, measured by time to containment. Before automating anything, process-mine how the work runs
now and delete the steps that exist only because two systems couldn't talk.

**Buy or build?**
Build thin, disposable apps instrumented in production, and let that evidence pick the platform to buy (see Buy vs
Build). Buy where a package already fits; build where the loop is the differentiator.

**Where did a coding agent fit in?**
It wrote code under the same proof method. Change reviews name it as author. For example, CR-0014 (MRP 1.0) exists
because the textbook eval failed a lot-sizing case in version 0.9. It was fixed and all six cases pass.

## Troubleshooting

- **Reset demo data** in the sidebar rebuilds everything in about 5 seconds, with the same numbers. It also clears the
  recorded test run, so press **Run tests** on Tests · Evals · Review before step 10.
- If a page shows stale code after an update, hard-reload the browser.
- If the server was stopped, run `python3 app.py` again. It reuses `data/ops.db` and rebuilds it automatically only if
  the schema changed.
