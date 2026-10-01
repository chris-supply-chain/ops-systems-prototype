-- ============================================================================
-- Ops OS: canonical relational model for making and moving vehicles
-- SQLite 3.35+ (foreign keys enforced). Portable to Postgres with type tweaks.
--
-- Three layers, one database:
--   LANDING  raw_*            what each outside system actually sent us, verbatim
--   CORE     master + txns    the normalized single source of truth
--   ACTION   decision_log,    what the system decided, and every write it made
--            hold, outbound_message, ops_exception, chargeback, erp_journal_*
--
-- Conventions: timestamps are UTC ISO-8601 text ('2026-09-26T15:00:00Z'),
-- dates are 'YYYY-MM-DD'. Business entities use natural keys (serials, lots,
-- PO numbers) so the tables read cleanly in the sandbox.
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ============================================================================
-- CORE / MASTER DATA
-- ============================================================================

CREATE TABLE site (
  site_id   TEXT PRIMARY KEY,
  name      TEXT NOT NULL,
  kind      TEXT NOT NULL CHECK (kind IN ('CM','OEM_PLANT','SUPPLIER','PORT','3PL','HQ')),
  operator  TEXT,
  city      TEXT,
  region    TEXT,
  country   TEXT NOT NULL,
  lat       REAL,
  lon       REAL,
  tz        TEXT NOT NULL
);

CREATE TABLE supplier (
  supplier_id        TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  tier               INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 3),
  parent_supplier_id TEXT REFERENCES supplier(supplier_id),  -- who this supplier sells into
  is_cm              INTEGER NOT NULL DEFAULT 0 CHECK (is_cm IN (0,1)),
  country            TEXT NOT NULL,
  commodity          TEXT NOT NULL,
  site_id            TEXT REFERENCES site(site_id),
  payment_terms      TEXT,
  recovery_terms     TEXT   -- JSON: contractual cost recovery for chargebacks
);

CREATE TABLE item (
  item_id             TEXT PRIMARY KEY,           -- part number
  name                TEXT NOT NULL,
  -- KIT = the sellable finished good (configured LV-1: vehicle + pack + charger),
  -- kitted at the 3PL. VEHICLE and PACK are sub-assemblies from the two supply chains.
  kind                TEXT NOT NULL CHECK (kind IN ('KIT','VEHICLE','PACK','MODULE','COMPONENT','MATERIAL')),
  commodity           TEXT NOT NULL,
  make_buy            TEXT NOT NULL CHECK (make_buy IN
                        ('KITTED','CM_BUILT','OEM_BUILT','BUY_DIRECT','BUY_CONSIGNED','CM_SOURCED','SUPPLIER_SOURCED')),
  uom                 TEXT NOT NULL DEFAULT 'EA',
  revision            TEXT,
  serialized          INTEGER NOT NULL DEFAULT 0 CHECK (serialized IN (0,1)),
  lot_controlled      INTEGER NOT NULL DEFAULT 0 CHECK (lot_controlled IN (0,1)),
  lead_time_days      INTEGER,
  moq                 INTEGER,
  order_multiple      INTEGER,
  safety_stock        INTEGER,
  std_cost            REAL,
  hts_code            TEXT,
  is_dg               INTEGER NOT NULL DEFAULT 0 CHECK (is_dg IN (0,1)),
  primary_supplier_id TEXT REFERENCES supplier(supplier_id),
  lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (lifecycle IN ('ACTIVE','PHASE_OUT','OBSOLETE'))
);

-- Approved vendor list
CREATE TABLE supplier_item (
  supplier_id    TEXT NOT NULL REFERENCES supplier(supplier_id),
  item_id        TEXT NOT NULL REFERENCES item(item_id),
  role           TEXT NOT NULL CHECK (role IN ('PRIMARY','ALTERNATE','QUALIFYING')),
  share_pct      REAL,
  lead_time_days INTEGER,
  PRIMARY KEY (supplier_id, item_id)
);

CREATE TABLE eco (
  eco_id            TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  reason            TEXT,
  change_class      TEXT NOT NULL CHECK (change_class IN ('FORM_FIT_FUNCTION','COST','QUALITY','SUPPLY','COMPLIANCE')),
  status            TEXT NOT NULL CHECK (status IN ('DRAFT','IN_REVIEW','APPROVED','IMPLEMENTED','CANCELLED')),
  effectivity_type  TEXT NOT NULL CHECK (effectivity_type IN ('DATE','SERIAL','LOT')),
  effective_date    TEXT,
  effective_serial  TEXT,
  parent_item_id    TEXT REFERENCES item(item_id),
  old_item_id       TEXT REFERENCES item(item_id),
  new_item_id       TEXT REFERENCES item(item_id),
  stock_disposition TEXT CHECK (stock_disposition IN ('USE_UP','SCRAP','REWORK','RETURN')),
  cost_delta        REAL,
  owner             TEXT,
  created_at        TEXT NOT NULL,
  approved_at       TEXT,
  implemented_at    TEXT
);

-- Multi-level BOM with effectivity. bom_level says whose BOM the line belongs to:
-- the OEM's own (pack line, consigned kit), the CM's vehicle BOM, or a supplier's
-- BOM (used to translate demand down to tier-2 and tier-3 materials).
CREATE TABLE bom_line (
  bom_line_id    INTEGER PRIMARY KEY,
  parent_item_id TEXT NOT NULL REFERENCES item(item_id),
  child_item_id  TEXT NOT NULL REFERENCES item(item_id),
  position       TEXT NOT NULL,
  qty_per        REAL NOT NULL CHECK (qty_per > 0),
  bom_level      TEXT NOT NULL CHECK (bom_level IN ('OEM','CM','SUPPLIER')),
  eff_from       TEXT NOT NULL,
  eff_to         TEXT,                     -- exclusive; NULL = open-ended
  eco_id         TEXT REFERENCES eco(eco_id),
  UNIQUE (parent_item_id, position, eff_from)
);

-- Component pricing with price breaks and effectivity
CREATE TABLE price (
  price_id    INTEGER PRIMARY KEY,
  item_id     TEXT NOT NULL REFERENCES item(item_id),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  min_qty     INTEGER NOT NULL DEFAULT 0,
  unit_price  REAL NOT NULL CHECK (unit_price >= 0),
  currency    TEXT NOT NULL DEFAULT 'USD',
  eff_from    TEXT NOT NULL,
  eff_to      TEXT,                        -- exclusive
  basis       TEXT NOT NULL CHECK (basis IN ('QUOTE','CONTRACT','ECO','SPOT')),
  source_ref  TEXT
);

CREATE TABLE station (
  station_id TEXT PRIMARY KEY,
  site_id    TEXT NOT NULL REFERENCES site(site_id),
  line       TEXT NOT NULL,
  code       TEXT NOT NULL,                -- the code the plant's MES uses
  seq        INTEGER NOT NULL,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('ASSEMBLY','TEST','INSPECTION','PACK','REWORK')),
  std_cycle_min REAL,                      -- routing standard time per unit at this station
  parallel_fixtures INTEGER NOT NULL DEFAULT 1,
  UNIQUE (site_id, line, code)
);

-- Line capacity by effective date: shifts, takt, planned rate, OEE target
CREATE TABLE line_capacity (
  site_id        TEXT NOT NULL REFERENCES site(site_id),
  line           TEXT NOT NULL,
  effective_from TEXT NOT NULL,
  shifts_per_day INTEGER NOT NULL,
  shift_minutes  INTEGER NOT NULL,
  takt_min       REAL NOT NULL,
  rated_units_per_day INTEGER NOT NULL,
  oee_target     REAL NOT NULL,
  PRIMARY KEY (site_id, line, effective_from)
);

-- MPS time fences: inside frozen nothing moves; inside slushy only planners move it
CREATE TABLE time_fence (
  site_id     TEXT PRIMARY KEY REFERENCES site(site_id),
  frozen_days INTEGER NOT NULL,
  slushy_days INTEGER NOT NULL,
  note        TEXT
);

CREATE TABLE downtime_event (
  downtime_id INTEGER PRIMARY KEY,
  site_id     TEXT NOT NULL REFERENCES site(site_id),
  line        TEXT NOT NULL,
  station_id  TEXT REFERENCES station(station_id),
  start_ts    TEXT NOT NULL,
  end_ts      TEXT NOT NULL,
  category    TEXT NOT NULL CHECK (category IN ('MATERIAL','EQUIPMENT','QUALITY','CHANGEOVER','LABOR','SYSTEMS','PLANNED')),
  reason      TEXT NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('CM_MES','OEM_MES','CM_REPORT'))
);

CREATE TABLE defect_code (
  defect_code         TEXT PRIMARY KEY,
  description         TEXT NOT NULL,
  category            TEXT NOT NULL CHECK (category IN
                        ('WORKMANSHIP','COMPONENT','DESIGN','TEST_EQUIPMENT','COSMETIC','FIELD')),
  default_responsible TEXT NOT NULL CHECK (default_responsible IN ('CM','SUPPLIER','OEM','NONE')),
  item_id             TEXT REFERENCES item(item_id)
);

CREATE TABLE customer (
  customer_id  TEXT PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('CONSUMER','FLEET')),
  display_name TEXT NOT NULL,
  city         TEXT,
  state        TEXT NOT NULL,
  region       TEXT NOT NULL,
  zip3         TEXT
);

CREATE TABLE gl_account (
  gl_account TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('ASSET','LIABILITY','EXPENSE','CONTRA_EXPENSE','REVENUE'))
);

CREATE TABLE fx_rate (
  currency     TEXT NOT NULL,
  rate_date    TEXT NOT NULL,
  usd_per_unit REAL NOT NULL CHECK (usd_per_unit > 0),
  PRIMARY KEY (currency, rate_date)
);

CREATE TABLE carrier (
  carrier_id TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  mode       TEXT NOT NULL CHECK (mode IN ('OCEAN','TRUCK','PARCEL','WHITE_GLOVE','AIR','DRAYAGE')),
  scac       TEXT,
  integration TEXT NOT NULL CHECK (integration IN ('EDI315','API','EMAIL','PORTAL'))
);

-- The system landscape: every system that owns a slice of the truth, and how it reaches us
CREATE TABLE source_system (
  system_id   TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('MES','QMS','ERP','TMS','WMS','PLANNING','CRM','PORTAL','EDI','EMAIL','BROKER')),
  owner       TEXT NOT NULL CHECK (owner IN ('OEM','CM','3PL','SUPPLIER','CARRIER','BROKER')),
  operator    TEXT NOT NULL,
  country     TEXT NOT NULL,
  channel     TEXT NOT NULL CHECK (channel IN ('API','WEBHOOK','EDI','SFTP_CSV','EMAIL_XLSX','EMAIL_TEXT','PORTAL','NATIVE')),
  direction   TEXT NOT NULL CHECK (direction IN ('INBOUND','OUTBOUND','BOTH')),
  frequency   TEXT NOT NULL,
  landing     TEXT,                        -- raw_* table (or core table for OEM-native systems)
  notes       TEXT
);

-- "Different options": how each feed could be integrated, with the evidence to choose
CREATE TABLE integration_option (
  system_id        TEXT NOT NULL REFERENCES source_system(system_id),
  option           TEXT NOT NULL,
  channel          TEXT NOT NULL,
  latency_minutes  REAL NOT NULL,
  touches_per_week REAL NOT NULL,          -- manual human touches
  error_rate_pct   REAL NOT NULL,
  build_weeks      REAL NOT NULL,
  run_usd_month    REAL NOT NULL,
  depends_on       TEXT,
  pros             TEXT,
  cons             TEXT,
  status           TEXT NOT NULL CHECK (status IN ('CURRENT','RECOMMENDED','CONSIDERED','REJECTED')),
  PRIMARY KEY (system_id, option)
);

-- ============================================================================
-- CORE / MAKE: serialized units, lots, as-built genealogy, station events
-- ============================================================================

-- MES work orders: one per line per SKU per day (the CM reports its own; the pack line is ours)
CREATE TABLE work_order (
  wo_id          TEXT PRIMARY KEY,
  site_id        TEXT NOT NULL REFERENCES site(site_id),
  line           TEXT NOT NULL,
  item_id        TEXT NOT NULL REFERENCES item(item_id),
  qty_planned    INTEGER NOT NULL CHECK (qty_planned >= 0),
  qty_started    INTEGER NOT NULL DEFAULT 0,
  qty_completed  INTEGER NOT NULL DEFAULT 0,
  qty_scrapped   INTEGER NOT NULL DEFAULT 0,
  sched_date     TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('RELEASED','IN_PROCESS','COMPLETE','CLOSED')),
  source         TEXT NOT NULL CHECK (source IN ('CM_MES','OEM_MES'))
);

CREATE TABLE unit (
  serial           TEXT PRIMARY KEY,
  item_id          TEXT NOT NULL REFERENCES item(item_id),
  origin           TEXT NOT NULL CHECK (origin IN ('CM_FEED','SUPPLIER_ASN','OEM_MES','PROVISIONAL')),
  supplier_id      TEXT REFERENCES supplier(supplier_id),
  build_site_id    TEXT REFERENCES site(site_id),
  line             TEXT,
  wo_id            TEXT REFERENCES work_order(wo_id),
  built_at         TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('WIP','BUILT','IN_TRANSIT','AT_3PL','ALLOCATED','SHIPPED','DELIVERED',
                      'SCRAPPED','COMPONENT','INSTALLED','RETURNED')),
  location_site_id TEXT REFERENCES site(site_id),
  on_hold          INTEGER NOT NULL DEFAULT 0 CHECK (on_hold IN (0,1)),
  asn_no           TEXT,
  firmware         TEXT
);

CREATE TABLE lot (
  lot_id       TEXT PRIMARY KEY,
  item_id      TEXT NOT NULL REFERENCES item(item_id),
  supplier_id  TEXT NOT NULL REFERENCES supplier(supplier_id),
  site_id      TEXT NOT NULL REFERENCES site(site_id),  -- where it was received
  qty_received REAL NOT NULL CHECK (qty_received > 0),
  mfg_date     TEXT,
  received_at  TEXT NOT NULL,
  po_id        TEXT,
  po_line_no   INTEGER,
  iqc_status   TEXT NOT NULL CHECK (iqc_status IN
                 ('PENDING','ACCEPTED','REJECTED','ACCEPTED_UNDER_DEVIATION','ON_HOLD','NOT_INSPECTED')),
  origin       TEXT NOT NULL CHECK (origin IN ('OEM_RECEIPT','CM_FEED','SUPPLIER_ASN')),
  FOREIGN KEY (po_id, po_line_no) REFERENCES po_line(po_id, line_no)
);

-- As-built (and as-maintained) genealogy. An edge links a parent serial to
-- exactly one child serial or child lot. removed_at keeps the history when a
-- component is swapped in rework or service. SHIPPED_WITH marries a pack to a
-- vehicle at the 3PL.
CREATE TABLE genealogy (
  genealogy_id   INTEGER PRIMARY KEY,
  parent_serial  TEXT NOT NULL REFERENCES unit(serial),
  child_serial   TEXT REFERENCES unit(serial),
  child_lot_id   TEXT REFERENCES lot(lot_id),
  child_item_id  TEXT NOT NULL REFERENCES item(item_id),
  qty            REAL NOT NULL DEFAULT 1 CHECK (qty > 0),
  relation       TEXT NOT NULL CHECK (relation IN ('INSTALLED','SHIPPED_WITH')),
  position       TEXT,
  station_id     TEXT REFERENCES station(station_id),
  installed_at   TEXT NOT NULL,
  removed_at     TEXT,
  removal_reason TEXT,
  source         TEXT NOT NULL CHECK (source IN ('CM_FEED','SUPPLIER_ASN','OEM_MES','3PL_FEED','SERVICE')),
  CHECK ((child_serial IS NULL) <> (child_lot_id IS NULL))
);

-- Lot-to-lot genealogy up the supply chain: a cell lot is made from a cathode lot
-- (tier 2), which is made from a lithium carbonate lot (tier 3). This is what lets a
-- field problem on one cell lot be traced to every sibling lot made from the same batch.
CREATE TABLE lot_link (
  child_lot_id  TEXT NOT NULL REFERENCES lot(lot_id),
  parent_lot_id TEXT NOT NULL REFERENCES lot(lot_id),
  qty           REAL,
  source        TEXT NOT NULL CHECK (source IN ('SUPPLIER_COA','SUPPLIER_ASN','OEM')),
  PRIMARY KEY (child_lot_id, parent_lot_id)
);

CREATE TABLE station_event (
  event_id     INTEGER PRIMARY KEY,
  serial       TEXT NOT NULL REFERENCES unit(serial),
  station_id   TEXT NOT NULL REFERENCES station(station_id),
  event_ts     TEXT NOT NULL,
  result       TEXT NOT NULL CHECK (result IN ('PASS','FAIL','REWORK','SCRAP')),
  defect_code  TEXT REFERENCES defect_code(defect_code),
  operator_id  TEXT,
  measurements TEXT,                                   -- JSON
  source       TEXT NOT NULL CHECK (source IN ('CM_FEED','OEM_MES')),
  raw_id       INTEGER REFERENCES raw_cm_mes_event(raw_id)
);

-- ============================================================================
-- CORE / PLAN: demand, promises, build plans, forecast to tiers 1-3
-- ============================================================================

CREATE TABLE customer_order (
  order_id            TEXT PRIMARY KEY,
  customer_id         TEXT NOT NULL REFERENCES customer(customer_id),
  channel             TEXT NOT NULL CHECK (channel IN ('D2C','FLEET')),
  reserved_at         TEXT,                        -- original reservation, drives priority
  ordered_at          TEXT NOT NULL,
  requested_date      TEXT,
  ship_to_region      TEXT NOT NULL,
  ship_to_state       TEXT NOT NULL,
  status              TEXT NOT NULL CHECK (status IN ('OPEN','ALLOCATED','SHIPPED','DELIVERED','CANCELLED')),
  promised_date       TEXT,                        -- current promise to the customer (delivery date)
  first_promised_date TEXT,
  allocated_at        TEXT,
  shipped_at          TEXT,
  delivered_at        TEXT,
  total_usd           REAL
);

-- Line 1 is the configured LV-1 kit; an optional line 2 is an extra battery pack.
-- Serials are filled when the 3PL allocates physical units to the line.
CREATE TABLE order_line (
  order_id       TEXT NOT NULL REFERENCES customer_order(order_id),
  line_no        INTEGER NOT NULL,
  item_id        TEXT NOT NULL REFERENCES item(item_id),
  qty            INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0),
  unit_price_usd REAL NOT NULL,
  vehicle_serial TEXT REFERENCES unit(serial),
  pack_serial    TEXT REFERENCES unit(serial),
  PRIMARY KEY (order_id, line_no)
);

-- Independent demand forecast for sellable items (S&OP), consumed by orders in MRP
CREATE TABLE demand_forecast (
  version      TEXT NOT NULL,
  item_id      TEXT NOT NULL REFERENCES item(item_id),
  week_start   TEXT NOT NULL,
  qty          REAL NOT NULL CHECK (qty >= 0),
  published_at TEXT NOT NULL,
  PRIMARY KEY (version, item_id, week_start)
);

CREATE TABLE order_promise (
  promise_id    INTEGER PRIMARY KEY,
  order_id      TEXT NOT NULL REFERENCES customer_order(order_id),
  promised_date TEXT NOT NULL,
  reason        TEXT NOT NULL CHECK (reason IN ('INITIAL','SUPPLY_DELAY','PULL_IN','REALLOCATION','MANUAL')),
  decided_at    TEXT NOT NULL,
  pegged_to     TEXT,
  decision_id   TEXT REFERENCES decision_log(decision_id)
);

CREATE TABLE build_plan (
  plan_id   INTEGER PRIMARY KEY,
  site_id   TEXT NOT NULL REFERENCES site(site_id),
  line      TEXT NOT NULL,
  item_id   TEXT NOT NULL REFERENCES item(item_id),
  plan_date TEXT NOT NULL,
  qty       INTEGER NOT NULL CHECK (qty >= 0),
  plan_type TEXT NOT NULL CHECK (plan_type IN ('CM_COMMIT','OEM_MPS')),
  version   TEXT NOT NULL,
  UNIQUE (site_id, line, item_id, plan_date, version)
);

CREATE TABLE forecast_release (
  release_id    TEXT PRIMARY KEY,
  released_at   TEXT NOT NULL,
  horizon_weeks INTEGER NOT NULL,
  demand_basis  TEXT,
  notes         TEXT
);

CREATE TABLE forecast_line (
  release_id  TEXT NOT NULL REFERENCES forecast_release(release_id),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  item_id     TEXT NOT NULL REFERENCES item(item_id),
  tier        INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 3),
  week_start  TEXT NOT NULL,
  qty         REAL NOT NULL CHECK (qty >= 0),
  PRIMARY KEY (release_id, supplier_id, item_id, week_start)
);

CREATE TABLE supplier_commit (
  release_id   TEXT NOT NULL,
  supplier_id  TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  week_start   TEXT NOT NULL,
  commit_qty   REAL NOT NULL CHECK (commit_qty >= 0),
  responded_at TEXT NOT NULL,
  PRIMARY KEY (release_id, supplier_id, item_id, week_start),
  FOREIGN KEY (release_id, supplier_id, item_id, week_start)
    REFERENCES forecast_line(release_id, supplier_id, item_id, week_start)
);

-- ============================================================================
-- CORE / SOURCE: POs, promise dates, RFQs
-- ============================================================================

CREATE TABLE purchase_order (
  po_id           TEXT PRIMARY KEY,
  supplier_id     TEXT NOT NULL REFERENCES supplier(supplier_id),
  ship_to_site_id TEXT NOT NULL REFERENCES site(site_id),
  created_at      TEXT NOT NULL,
  buyer           TEXT NOT NULL,
  incoterm        TEXT,
  currency        TEXT NOT NULL DEFAULT 'USD',
  status          TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED','CANCELLED'))
);

CREATE TABLE po_line (
  po_id          TEXT NOT NULL REFERENCES purchase_order(po_id),
  line_no        INTEGER NOT NULL,
  item_id        TEXT NOT NULL REFERENCES item(item_id),
  qty            REAL NOT NULL CHECK (qty > 0),
  unit_price     REAL NOT NULL CHECK (unit_price >= 0),
  price_id       INTEGER REFERENCES price(price_id),
  need_date      TEXT NOT NULL,
  promise_date   TEXT,
  confirm_status TEXT NOT NULL CHECK (confirm_status IN ('UNCONFIRMED','CONFIRMED','PARTIAL','REJECTED')),
  confirmed_at   TEXT,
  received_qty   REAL NOT NULL DEFAULT 0 CHECK (received_qty >= 0),
  status         TEXT NOT NULL CHECK (status IN ('OPEN','RECEIVED','CLOSED','CANCELLED')),
  PRIMARY KEY (po_id, line_no)
);

CREATE TABLE po_promise_history (
  id           INTEGER PRIMARY KEY,
  po_id        TEXT NOT NULL,
  line_no      INTEGER NOT NULL,
  promise_date TEXT NOT NULL,
  promise_qty  REAL,
  recorded_at  TEXT NOT NULL,
  channel      TEXT NOT NULL CHECK (channel IN ('EMAIL','EXCEL','PORTAL','EDI855','BUYER','EXPEDITE')),
  raw_ref      TEXT,                   -- 'raw_email:12' / 'raw_attachment:3' / 'raw_supplier_confirmation:5'
  note         TEXT,
  FOREIGN KEY (po_id, line_no) REFERENCES po_line(po_id, line_no)
);

-- ERP goods receipts (every PO receipt, lot-controlled, serialized or bulk)
CREATE TABLE goods_receipt (
  receipt_id  TEXT PRIMARY KEY,
  po_id       TEXT NOT NULL,
  line_no     INTEGER NOT NULL,
  site_id     TEXT NOT NULL REFERENCES site(site_id),
  received_at TEXT NOT NULL,
  qty         REAL NOT NULL CHECK (qty > 0),
  lot_id      TEXT REFERENCES lot(lot_id),
  asn_no      TEXT,
  FOREIGN KEY (po_id, line_no) REFERENCES po_line(po_id, line_no)
);

-- ERP accounts payable: supplier invoices, three-way matched to PO line and receipt
CREATE TABLE supplier_invoice (
  invoice_id   TEXT PRIMARY KEY,
  supplier_id  TEXT NOT NULL REFERENCES supplier(supplier_id),
  po_id        TEXT NOT NULL,
  line_no      INTEGER NOT NULL,
  invoice_date TEXT NOT NULL,
  qty          REAL NOT NULL CHECK (qty > 0),
  unit_price   REAL NOT NULL CHECK (unit_price >= 0),
  amount_usd   REAL NOT NULL,
  match_status TEXT NOT NULL CHECK (match_status IN ('MATCHED','PRICE_VARIANCE','QTY_VARIANCE','ON_HOLD')),
  variance_usd REAL NOT NULL DEFAULT 0,
  pay_status   TEXT NOT NULL CHECK (pay_status IN ('OPEN','SCHEDULED','PAID','BLOCKED')),
  due_date     TEXT NOT NULL,
  FOREIGN KEY (po_id, line_no) REFERENCES po_line(po_id, line_no)
);

-- Replenishment policy per item and location: how each stock point is kept full
CREATE TABLE replenishment_policy (
  item_id        TEXT NOT NULL REFERENCES item(item_id),
  site_id        TEXT NOT NULL REFERENCES site(site_id),
  policy         TEXT NOT NULL CHECK (policy IN ('MRP','REORDER_POINT','MIN_MAX','ORDER_UP_TO','DRP','VMI','CONSIGNMENT')),
  owner          TEXT NOT NULL CHECK (owner IN ('OEM','CM','SUPPLIER','3PL')),
  lead_time_days REAL NOT NULL,
  lt_std_days    REAL NOT NULL DEFAULT 0,
  review_days    INTEGER NOT NULL DEFAULT 1,
  service_level  REAL NOT NULL,
  safety_stock   REAL NOT NULL,
  reorder_point  REAL,
  max_qty        REAL,
  lot_size_rule  TEXT NOT NULL CHECK (lot_size_rule IN ('LOT_FOR_LOT','FIXED','MOQ_MULTIPLE','PERIOD_ORDER')),
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (item_id, site_id)
);

CREATE TABLE rfq (
  rfq_id              TEXT PRIMARY KEY,
  item_id             TEXT NOT NULL REFERENCES item(item_id),
  title               TEXT NOT NULL,
  annual_qty          INTEGER NOT NULL,
  created_at          TEXT NOT NULL,
  due_at              TEXT NOT NULL,
  status              TEXT NOT NULL CHECK (status IN ('OPEN','EVALUATING','AWARDED','CANCELLED')),
  awarded_supplier_id TEXT REFERENCES supplier(supplier_id),
  award_rationale     TEXT,
  eco_id              TEXT REFERENCES eco(eco_id)
);

CREATE TABLE rfq_quote (
  rfq_id         TEXT NOT NULL REFERENCES rfq(rfq_id),
  supplier_id    TEXT NOT NULL REFERENCES supplier(supplier_id),
  unit_price     REAL NOT NULL,
  moq            INTEGER NOT NULL,
  lead_time_days INTEGER NOT NULL,
  tooling_usd    REAL NOT NULL DEFAULT 0,
  freight_per_unit REAL NOT NULL DEFAULT 0,
  submitted_at   TEXT NOT NULL,
  valid_until    TEXT,
  notes          TEXT,
  PRIMARY KEY (rfq_id, supplier_id)
);

-- ============================================================================
-- CORE / MOVE: shipments, track-and-trace, importation
-- ============================================================================

CREATE TABLE shipment (
  shipment_id    TEXT PRIMARY KEY,
  leg            TEXT NOT NULL CHECK (leg IN ('CM_TO_3PL','PLANT_TO_3PL','3PL_TO_CUSTOMER','SUPPLIER_TO_PLANT','SUPPLIER_TO_CM')),
  mode           TEXT NOT NULL CHECK (mode IN ('OCEAN','TRUCK_FTL','TRUCK_LTL_DG','PARCEL','WHITE_GLOVE','AIR')),
  origin_site_id TEXT NOT NULL REFERENCES site(site_id),
  dest_site_id   TEXT REFERENCES site(site_id),
  order_id       TEXT REFERENCES customer_order(order_id),
  carrier        TEXT NOT NULL REFERENCES carrier(carrier_id),
  booking_ref    TEXT,
  container_no   TEXT,
  vessel         TEXT,
  voyage         TEXT,
  bol_no         TEXT,
  tracking_no    TEXT,
  pol_site_id    TEXT REFERENCES site(site_id),
  pod_site_id    TEXT REFERENCES site(site_id),
  etd_planned    TEXT,
  eta_planned    TEXT,
  eta_current    TEXT,
  atd            TEXT,
  ata            TEXT,
  received_at    TEXT,
  status         TEXT NOT NULL CHECK (status IN
                   ('BOOKED','GATED_IN','ON_WATER','AT_PORT','CUSTOMS_HOLD','IN_TRANSIT',
                    'OUT_FOR_DELIVERY','DELIVERED','RECEIVED','EXCEPTION')),
  asn_qty        INTEGER,
  dg_class       TEXT,
  freight_usd    REAL
);

CREATE TABLE shipment_unit (
  shipment_id TEXT NOT NULL REFERENCES shipment(shipment_id),
  serial      TEXT NOT NULL REFERENCES unit(serial),
  PRIMARY KEY (shipment_id, serial)
);

CREATE TABLE shipment_event (
  event_id    INTEGER PRIMARY KEY,
  shipment_id TEXT NOT NULL REFERENCES shipment(shipment_id),
  event_ts    TEXT NOT NULL,
  code        TEXT NOT NULL CHECK (code IN
                ('BOOKED','GATE_IN','LOADED','DEPARTED','ETA_UPDATE','ARRIVED','DISCHARGED',
                 'CUSTOMS_FILED','CUSTOMS_HOLD','CUSTOMS_RELEASED','OUT_GATE','RECEIVED',
                 'PICKED_UP','OUT_FOR_DELIVERY','DELIVERED','EXCEPTION')),
  location    TEXT,
  detail      TEXT,
  source      TEXT NOT NULL CHECK (source IN ('CARRIER','3PL_FEED','BROKER','OEM')),
  raw_ref     TEXT               -- 'raw_carrier_event:123' / 'raw_3pl_message:45'
);

CREATE TABLE freight_rate (
  rate_id     INTEGER PRIMARY KEY,
  carrier     TEXT NOT NULL REFERENCES carrier(carrier_id),
  lane        TEXT NOT NULL,                       -- e.g. 'TWTXG-USOAK', 'FRE-RNO', 'RNO-WEST'
  basis       TEXT NOT NULL CHECK (basis IN ('PER_CONTAINER','PER_SHIPMENT','PER_UNIT','PER_KG')),
  rate_usd    REAL NOT NULL CHECK (rate_usd >= 0),
  valid_from  TEXT NOT NULL,
  valid_to    TEXT,
  contract    TEXT
);

CREATE TABLE customs_entry (
  entry_no       TEXT PRIMARY KEY,
  shipment_id    TEXT NOT NULL UNIQUE REFERENCES shipment(shipment_id),
  broker         TEXT NOT NULL,
  isf_filed_at   TEXT,
  entry_filed_at TEXT,
  hts_code       TEXT NOT NULL,
  entered_value  REAL NOT NULL,
  duty_rate      REAL NOT NULL,
  duty_usd       REAL NOT NULL,
  mpf_usd        REAL NOT NULL,
  hmf_usd        REAL NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('ISF_FILED','ENTRY_FILED','EXAM','RELEASED','LIQUIDATED')),
  exam_type      TEXT,
  released_at    TEXT
);

-- ============================================================================
-- CORE / INVENTORY (non-serialized stock; serialized units live in `unit`)
-- ============================================================================

CREATE TABLE inventory_balance (
  balance_id   INTEGER PRIMARY KEY,
  site_id      TEXT NOT NULL REFERENCES site(site_id),
  item_id      TEXT NOT NULL REFERENCES item(item_id),
  lot_id       TEXT REFERENCES lot(lot_id),
  owner        TEXT NOT NULL CHECK (owner IN ('OEM','CM','SUPPLIER')),
  stock_status TEXT NOT NULL CHECK (stock_status IN ('AVAILABLE','QC_HOLD','BLOCKED','IN_PRODUCTION')),
  qty          REAL NOT NULL CHECK (qty >= 0),
  as_of        TEXT NOT NULL,
  source       TEXT NOT NULL CHECK (source IN ('OEM_ERP','CM_REPORT','SUPPLIER_REPORT','3PL_WMS'))
);

-- What the CM says in its daily Excel report (parsed from email), kept beside what
-- its MES events say, so the two can be reconciled rather than silently merged
CREATE TABLE cm_output_report (
  report_date TEXT NOT NULL,
  line        TEXT NOT NULL,
  item_id     TEXT NOT NULL REFERENCES item(item_id),
  planned     INTEGER,
  actual      INTEGER NOT NULL,
  wip         INTEGER,
  scrapped    INTEGER,
  scrap_twd   REAL,
  scrap_usd   REAL,
  source_ref  TEXT NOT NULL,
  PRIMARY KEY (report_date, line, item_id)
);

CREATE TABLE cm_stock_report (
  report_date TEXT NOT NULL,
  item_id     TEXT NOT NULL REFERENCES item(item_id),
  on_hand     INTEGER NOT NULL,
  qc_hold     INTEGER NOT NULL DEFAULT 0,
  source_ref  TEXT NOT NULL,
  PRIMARY KEY (report_date, item_id)
);

-- What the 3PL's WMS says it holds (for reconciliation against `unit`)
CREATE TABLE wms_snapshot (
  site_id       TEXT NOT NULL REFERENCES site(site_id),
  item_id       TEXT NOT NULL REFERENCES item(item_id),
  snapshot_at   TEXT NOT NULL,
  qty_available INTEGER NOT NULL,
  qty_allocated INTEGER NOT NULL,
  qty_hold      INTEGER NOT NULL,
  PRIMARY KEY (site_id, item_id, snapshot_at)
);

-- ============================================================================
-- CORE / QUALITY
-- ============================================================================

-- Control plan: what is measured where, against which limits, and what to do when it drifts
CREATE TABLE control_plan (
  cp_id          TEXT PRIMARY KEY,
  site_id        TEXT NOT NULL REFERENCES site(site_id),
  station_id     TEXT REFERENCES station(station_id),
  item_id        TEXT REFERENCES item(item_id),
  characteristic TEXT NOT NULL,                    -- measurement key in station_event.measurements
  unit           TEXT NOT NULL,
  lsl            REAL,
  usl            REAL,
  target         REAL,
  method         TEXT NOT NULL,
  frequency      TEXT NOT NULL,
  reaction_plan  TEXT NOT NULL
);

CREATE TABLE deviation (
  deviation_id TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  item_id      TEXT NOT NULL REFERENCES item(item_id),
  supplier_id  TEXT REFERENCES supplier(supplier_id),
  site_id      TEXT NOT NULL REFERENCES site(site_id),
  reason       TEXT NOT NULL,
  qty_limit    INTEGER NOT NULL CHECK (qty_limit > 0),
  qty_used     INTEGER NOT NULL DEFAULT 0 CHECK (qty_used >= 0),
  valid_from   TEXT NOT NULL,
  valid_to     TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('REQUESTED','APPROVED','EXPIRED','CLOSED','REJECTED')),
  risk         TEXT NOT NULL CHECK (risk IN ('LOW','MEDIUM','HIGH')),
  requested_by TEXT,
  approved_by  TEXT,
  approved_at  TEXT,
  eco_id       TEXT REFERENCES eco(eco_id)
);

CREATE TABLE quality_event (
  qe_id          TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('IQC','INLINE','EOL','FIELD','AUDIT')),
  site_id        TEXT NOT NULL REFERENCES site(site_id),
  item_id        TEXT NOT NULL REFERENCES item(item_id),
  lot_id         TEXT REFERENCES lot(lot_id),
  serial         TEXT REFERENCES unit(serial),
  supplier_id    TEXT REFERENCES supplier(supplier_id),
  defect_code    TEXT REFERENCES defect_code(defect_code),
  qty_inspected  INTEGER NOT NULL DEFAULT 0,
  qty_defective  INTEGER NOT NULL DEFAULT 0,
  result         TEXT NOT NULL CHECK (result IN ('ACCEPT','REJECT','CONDITIONAL')),
  disposition    TEXT CHECK (disposition IN ('ACCEPT','USE_AS_IS','REWORK','RTV','SCRAP','SORT')),
  status         TEXT NOT NULL CHECK (status IN ('OPEN','CONTAINED','CLOSED')),
  detected_at    TEXT NOT NULL,
  closed_at      TEXT,
  root_cause     TEXT,
  ncr_no         TEXT,
  deviation_id   TEXT REFERENCES deviation(deviation_id),
  cost_usd       REAL NOT NULL DEFAULT 0
);

-- Corrective and preventive action (8D) tied to an NCR / quality event
CREATE TABLE capa (
  capa_id        TEXT PRIMARY KEY,
  qe_id          TEXT REFERENCES quality_event(qe_id),
  supplier_id    TEXT REFERENCES supplier(supplier_id),
  title          TEXT NOT NULL,
  d_stage        TEXT NOT NULL CHECK (d_stage IN ('D1','D2','D3','D4','D5','D6','D7','D8')),
  containment    TEXT,
  root_cause     TEXT,
  corrective     TEXT,
  preventive     TEXT,
  owner          TEXT NOT NULL,
  opened_at      TEXT NOT NULL,
  due_date       TEXT NOT NULL,
  closed_at      TEXT,
  status         TEXT NOT NULL CHECK (status IN ('OPEN','CONTAINED','VERIFIED','CLOSED')),
  decision_id    TEXT REFERENCES decision_log(decision_id)
);

CREATE TABLE warranty_claim (
  claim_id         TEXT PRIMARY KEY,
  serial           TEXT NOT NULL REFERENCES unit(serial),     -- the vehicle
  order_id         TEXT REFERENCES customer_order(order_id),
  reported_at      TEXT NOT NULL,
  symptom          TEXT NOT NULL,
  defect_code      TEXT REFERENCES defect_code(defect_code),
  failed_item_id   TEXT REFERENCES item(item_id),
  failed_serial    TEXT REFERENCES unit(serial),
  failed_lot_id    TEXT REFERENCES lot(lot_id),
  supplier_id      TEXT REFERENCES supplier(supplier_id),     -- responsible party
  cost_parts_usd   REAL NOT NULL DEFAULT 0,
  cost_labor_usd   REAL NOT NULL DEFAULT 0,
  cost_logistics_usd REAL NOT NULL DEFAULT 0,
  status           TEXT NOT NULL CHECK (status IN ('OPEN','DIAGNOSED','REPAIRED','CLOSED','REJECTED')),
  raw_id           INTEGER REFERENCES raw_warranty_case(raw_id),
  chargeback_id    TEXT REFERENCES chargeback(chargeback_id)
);

-- ============================================================================
-- ACTION / FINANCE: supplier recovery lands in the ERP
-- ============================================================================

CREATE TABLE chargeback (
  chargeback_id  TEXT PRIMARY KEY,
  supplier_id    TEXT NOT NULL REFERENCES supplier(supplier_id),
  basis          TEXT NOT NULL CHECK (basis IN ('WARRANTY','IQC_REJECT','LINE_DOWN','CONTAINMENT')),
  title          TEXT NOT NULL,
  amount_usd     REAL NOT NULL CHECK (amount_usd >= 0),
  status         TEXT NOT NULL CHECK (status IN ('DRAFT','SENT','ACCEPTED','DISPUTED','POSTED','WRITTEN_OFF')),
  created_at     TEXT NOT NULL,
  sent_at        TEXT,
  responded_at   TEXT,
  posted_at      TEXT,
  debit_memo_no  TEXT,
  je_id          TEXT REFERENCES erp_journal_entry(je_id),
  decision_id    TEXT REFERENCES decision_log(decision_id),
  notes          TEXT
);

CREATE TABLE chargeback_line (
  chargeback_id TEXT NOT NULL REFERENCES chargeback(chargeback_id),
  line_no       INTEGER NOT NULL,
  ref_type      TEXT NOT NULL CHECK (ref_type IN ('CLAIM','QUALITY_EVENT','COST')),
  ref_id        TEXT,
  description   TEXT NOT NULL,
  amount_usd    REAL NOT NULL,
  PRIMARY KEY (chargeback_id, line_no)
);

CREATE TABLE erp_journal_entry (
  je_id       TEXT PRIMARY KEY,
  doc_type    TEXT NOT NULL CHECK (doc_type IN ('DEBIT_MEMO','ACCRUAL','ADJUSTMENT')),
  posted_at   TEXT NOT NULL,
  supplier_id TEXT REFERENCES supplier(supplier_id),
  memo        TEXT NOT NULL,
  source_ref  TEXT,
  status      TEXT NOT NULL CHECK (status IN ('POSTED','REVERSED'))
);

CREATE TABLE erp_journal_line (
  je_id       TEXT NOT NULL REFERENCES erp_journal_entry(je_id),
  line_no     INTEGER NOT NULL,
  gl_account  TEXT NOT NULL REFERENCES gl_account(gl_account),
  debit_usd   REAL NOT NULL DEFAULT 0 CHECK (debit_usd >= 0),
  credit_usd  REAL NOT NULL DEFAULT 0 CHECK (credit_usd >= 0),
  cost_center TEXT,
  memo        TEXT,
  PRIMARY KEY (je_id, line_no)
);

-- ============================================================================
-- ACTION / CLOSED LOOP: decisions, holds, outbound writes, exceptions
-- ============================================================================

CREATE TABLE decision_log (
  decision_id     TEXT PRIMARY KEY,
  loop            TEXT NOT NULL CHECK (loop IN ('QUALITY','SUPPLY','PROMISE','DATA')),
  rule_id         TEXT NOT NULL,
  trigger_ref     TEXT NOT NULL,
  title           TEXT NOT NULL,
  rationale       TEXT NOT NULL,
  inputs_json     TEXT,
  proposed_action TEXT NOT NULL,
  impact_json     TEXT,
  status          TEXT NOT NULL CHECK (status IN ('PROPOSED','EXECUTED','REJECTED','SUPERSEDED')),
  proposed_at     TEXT NOT NULL,
  decided_by      TEXT,
  executed_at     TEXT,
  outcome_json    TEXT,                 -- the rows the execution wrote
  achieved_json   TEXT                  -- re-measured from the data right after execution: each impact_json
                                        -- figure as it now stands, and whether the triggering exception cleared
);

CREATE TABLE hold (
  hold_id     TEXT PRIMARY KEY,
  scope_type  TEXT NOT NULL CHECK (scope_type IN ('SERIAL','LOT')),
  serial      TEXT REFERENCES unit(serial),
  lot_id      TEXT REFERENCES lot(lot_id),
  site_id     TEXT REFERENCES site(site_id),
  reason      TEXT NOT NULL,
  placed_at   TEXT NOT NULL,
  released_at TEXT,
  decision_id TEXT REFERENCES decision_log(decision_id),
  CHECK ((serial IS NULL) <> (lot_id IS NULL))
);

-- Every write the platform makes to another system (the "act" in closed loop)
CREATE TABLE outbound_message (
  msg_id        INTEGER PRIMARY KEY,
  target_system TEXT NOT NULL CHECK (target_system IN
                  ('ERP','WMS_3PL','CM_MES','OEM_MES','SUPPLIER_PORTAL','CUSTOMER_COMMS','TMS','EDI')),
  message_type  TEXT NOT NULL,
  ref           TEXT,
  payload       TEXT,                                -- JSON
  created_at    TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('QUEUED','SENT','ACKED','FAILED')),
  decision_id   TEXT REFERENCES decision_log(decision_id)
);

CREATE TABLE ops_exception (
  exception_id TEXT PRIMARY KEY,                     -- rule:ref, stable across re-detection
  rule_id      TEXT NOT NULL,
  domain       TEXT NOT NULL CHECK (domain IN ('MAKE','PLAN','SOURCE','MOVE','QUALITY','DATA')),
  severity     TEXT NOT NULL CHECK (severity IN ('CRITICAL','SERIOUS','WARNING','INFO')),
  title        TEXT NOT NULL,
  detail       TEXT,
  ref_type     TEXT,
  ref_id       TEXT,
  impact_units INTEGER,
  impact_unit  TEXT,                                 -- what impact_units counts: vehicles, orders, messages ...
  impact_usd   REAL,
  detected_at  TEXT NOT NULL,
  owner        TEXT,
  status       TEXT NOT NULL CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
  resolved_at  TEXT,
  route        TEXT,                                 -- where in the app to act on it
  decision_id  TEXT REFERENCES decision_log(decision_id)
);

-- MRP output: every run is kept, with its planned orders and action messages
CREATE TABLE mrp_run (
  run_id           INTEGER PRIMARY KEY,
  ran_at           TEXT NOT NULL,
  as_of            TEXT NOT NULL,
  horizon_days     INTEGER NOT NULL,
  forecast_version TEXT,
  triggered_by     TEXT NOT NULL,
  items_planned    INTEGER NOT NULL,
  planned_orders   INTEGER NOT NULL,
  messages         INTEGER NOT NULL,
  summary_json     TEXT
);

CREATE TABLE planned_order (
  planned_order_id TEXT PRIMARY KEY,
  run_id           INTEGER NOT NULL REFERENCES mrp_run(run_id),
  item_id          TEXT NOT NULL REFERENCES item(item_id),
  site_id          TEXT NOT NULL REFERENCES site(site_id),
  order_type       TEXT NOT NULL CHECK (order_type IN ('PURCHASE','CM_BUILD','OEM_BUILD','KIT')),
  supplier_id      TEXT REFERENCES supplier(supplier_id),
  qty              REAL NOT NULL CHECK (qty > 0),
  release_date     TEXT NOT NULL,
  due_date         TEXT NOT NULL,
  firm             INTEGER NOT NULL DEFAULT 0 CHECK (firm IN (0,1)),
  status           TEXT NOT NULL CHECK (status IN ('PLANNED','RELEASED','CANCELLED')),
  released_ref     TEXT
);

CREATE TABLE mrp_message (
  message_id  INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES mrp_run(run_id),
  item_id     TEXT NOT NULL REFERENCES item(item_id),
  site_id     TEXT REFERENCES site(site_id),
  message     TEXT NOT NULL CHECK (message IN ('RELEASE','PAST_DUE_RELEASE','EXPEDITE','DEFER','CANCEL',
                                               'SHORTAGE','UNCONFIRMED','BELOW_SAFETY_STOCK')),
  ref         TEXT,
  bucket_date TEXT NOT NULL,
  qty         REAL,
  detail      TEXT
);

-- The "learn" step: outcomes roll up into the scorecard that sourcing reads
CREATE TABLE supplier_scorecard (
  supplier_id          TEXT NOT NULL REFERENCES supplier(supplier_id),
  week_start           TEXT NOT NULL,
  otd_pct              REAL,
  ppm                  REAL,
  confirm_hours_median REAL,
  promise_slip_days    REAL,
  chargeback_usd       REAL,
  score                REAL,
  PRIMARY KEY (supplier_id, week_start)
);

-- ============================================================================
-- LANDING / SANDBOX: raw payloads exactly as received, plus pipeline audit
-- ============================================================================

CREATE TABLE mapping_version (
  source         TEXT NOT NULL,
  version        TEXT NOT NULL,
  effective_from TEXT NOT NULL,
  spec_json      TEXT NOT NULL,
  notes          TEXT,
  PRIMARY KEY (source, version)
);

CREATE TABLE ingest_run (
  run_id          INTEGER PRIMARY KEY,
  source          TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  mapping_version TEXT,
  rows_in         INTEGER NOT NULL DEFAULT 0,
  rows_ok         INTEGER NOT NULL DEFAULT 0,
  rows_warn       INTEGER NOT NULL DEFAULT 0,
  rows_quarantined INTEGER NOT NULL DEFAULT 0,
  rows_duplicate  INTEGER NOT NULL DEFAULT 0,
  notes           TEXT
);

-- Email is still how half the world integrates: every message and attachment kept verbatim
CREATE TABLE raw_email (
  raw_id        INTEGER PRIMARY KEY,
  mailbox       TEXT NOT NULL,                     -- e.g. 'cm-reports@', 'po-confirm@', 'supplier-reports@'
  received_at   TEXT NOT NULL,
  from_addr     TEXT NOT NULL,
  to_addr       TEXT NOT NULL,
  subject       TEXT NOT NULL,
  message_id    TEXT NOT NULL UNIQUE,
  body_text     TEXT,
  mime          TEXT NOT NULL,                     -- full RFC 822 message
  classified_as TEXT,                              -- template / feed it matched
  ingest_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (ingest_status IN ('PENDING','OK','WARN','QUARANTINED','IGNORED')),
  ingest_note   TEXT
);

CREATE TABLE raw_attachment (
  attachment_id INTEGER PRIMARY KEY,
  raw_id        INTEGER NOT NULL REFERENCES raw_email(raw_id),
  filename      TEXT NOT NULL,
  content_type  TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL,
  sha256        TEXT NOT NULL,
  content       BLOB NOT NULL,
  parser        TEXT,
  parse_status  TEXT NOT NULL DEFAULT 'PENDING' CHECK (parse_status IN ('PENDING','OK','WARN','FAILED','SKIPPED')),
  rows_parsed   INTEGER,
  rows_loaded   INTEGER,
  parse_note    TEXT
);

-- Step-by-step trace of every ingested message: received -> classified -> extracted ->
-- parsed -> validated -> mapped -> loaded -> reconciled
CREATE TABLE ingest_step (
  step_id     INTEGER PRIMARY KEY,
  source      TEXT NOT NULL,
  source_ref  TEXT NOT NULL,                       -- 'raw_email:42', 'raw_attachment:7', 'raw_cm_mes_event:9'
  seq         INTEGER NOT NULL,
  step        TEXT NOT NULL CHECK (step IN ('RECEIVED','CLASSIFIED','EXTRACTED','PARSED','VALIDATED','MAPPED',
                                            'LOADED','RECONCILED','QUARANTINED','REPLAYED')),
  status      TEXT NOT NULL CHECK (status IN ('OK','WARN','FAILED','SKIPPED')),
  at          TEXT NOT NULL,
  duration_ms REAL,
  detail      TEXT
);

CREATE TABLE raw_cm_mes_event (
  raw_id          INTEGER PRIMARY KEY,
  source_system   TEXT NOT NULL,
  received_at     TEXT NOT NULL,
  payload         TEXT NOT NULL,
  ingest_status   TEXT NOT NULL DEFAULT 'PENDING' CHECK (ingest_status IN
                    ('PENDING','OK','WARN','DUPLICATE','QUARANTINED','REPLAYED')),
  ingest_note     TEXT,
  mapping_version TEXT,
  run_id          INTEGER REFERENCES ingest_run(run_id)
);

CREATE TABLE raw_supplier_asn (
  raw_id        INTEGER PRIMARY KEY,
  supplier_id   TEXT NOT NULL REFERENCES supplier(supplier_id),
  received_at   TEXT NOT NULL,
  payload       TEXT NOT NULL,
  ingest_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (ingest_status IN ('PENDING','OK','WARN','QUARANTINED')),
  ingest_note   TEXT
);

CREATE TABLE raw_carrier_event (
  raw_id        INTEGER PRIMARY KEY,
  carrier       TEXT NOT NULL,
  received_at   TEXT NOT NULL,
  format        TEXT NOT NULL CHECK (format IN ('EDI315','JSON')),
  payload       TEXT NOT NULL,
  ingest_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (ingest_status IN ('PENDING','OK','WARN','QUARANTINED','DUPLICATE')),
  ingest_note   TEXT
);

CREATE TABLE raw_3pl_message (
  raw_id        INTEGER PRIMARY KEY,
  received_at   TEXT NOT NULL,
  msg_type      TEXT NOT NULL CHECK (msg_type IN ('RECEIPT','ALLOCATION','SHIP_CONFIRM','INVENTORY_SNAPSHOT')),
  payload       TEXT NOT NULL,
  ingest_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (ingest_status IN ('PENDING','OK','WARN','QUARANTINED')),
  ingest_note   TEXT
);

CREATE TABLE raw_supplier_confirmation (
  raw_id        INTEGER PRIMARY KEY,
  supplier_id   TEXT NOT NULL REFERENCES supplier(supplier_id),
  received_at   TEXT NOT NULL,
  channel       TEXT NOT NULL CHECK (channel IN ('EMAIL','PORTAL','EDI855')),
  payload       TEXT NOT NULL,
  ingest_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (ingest_status IN ('PENDING','OK','WARN','QUARANTINED')),
  ingest_note   TEXT
);

CREATE TABLE raw_warranty_case (
  raw_id        INTEGER PRIMARY KEY,
  received_at   TEXT NOT NULL,
  payload       TEXT NOT NULL,
  ingest_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (ingest_status IN ('PENDING','OK','WARN','QUARANTINED')),
  ingest_note   TEXT
);

-- ============================================================================
-- PLATFORM: process instrumentation, thin-app telemetry, buy-vs-build, contracts
-- ============================================================================

CREATE TABLE process_activity (
  process  TEXT NOT NULL,
  activity TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('VALUE','CONTROL','GLUE','WAIT')),
  system   TEXT NOT NULL,
  note     TEXT,
  PRIMARY KEY (process, activity)
);

CREATE TABLE process_event (
  id         INTEGER PRIMARY KEY,
  process    TEXT NOT NULL,
  case_id    TEXT NOT NULL,
  activity   TEXT NOT NULL,
  ts         TEXT NOT NULL,
  actor_role TEXT NOT NULL,
  FOREIGN KEY (process, activity) REFERENCES process_activity(process, activity)
);

CREATE TABLE app_telemetry (
  id         INTEGER PRIMARY KEY,
  app        TEXT NOT NULL CHECK (app IN ('QUALITY_THIN','MOVE_THIN','DOCK_THIN')),
  ts         TEXT NOT NULL,
  user_role  TEXT NOT NULL,
  feature    TEXT NOT NULL,
  duration_s REAL,
  outcome    TEXT
);

CREATE TABLE capability (
  capability_id  TEXT PRIMARY KEY,
  domain         TEXT NOT NULL CHECK (domain IN ('QMS','TMS','WMS')),
  name           TEXT NOT NULL,
  description    TEXT,
  assumed_weight REAL NOT NULL,          -- what the team believed before shipping the thin app
  feature_key    TEXT                    -- links to app_telemetry.feature
);

CREATE TABLE vendor (
  vendor_id    TEXT PRIMARY KEY,
  domain       TEXT NOT NULL CHECK (domain IN ('QMS','TMS','WMS')),
  name         TEXT NOT NULL,
  annual_usd   REAL NOT NULL,
  impl_weeks   INTEGER NOT NULL,
  notes        TEXT
);

CREATE TABLE vendor_capability (
  vendor_id     TEXT NOT NULL REFERENCES vendor(vendor_id),
  capability_id TEXT NOT NULL REFERENCES capability(capability_id),
  fit           INTEGER NOT NULL CHECK (fit BETWEEN 0 AND 3),   -- 0 none .. 3 native
  PRIMARY KEY (vendor_id, capability_id)
);

CREATE TABLE data_contract (
  contract_id TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  domain      TEXT NOT NULL,
  layer       TEXT NOT NULL CHECK (layer IN ('LANDING','CORE','ACTION')),
  severity    TEXT NOT NULL CHECK (severity IN ('CRITICAL','SERIOUS','WARNING')),
  description TEXT NOT NULL,
  check_sql   TEXT NOT NULL,             -- returns violating rows; zero rows = pass
  owner       TEXT NOT NULL
);

CREATE TABLE contract_run (
  run_id      INTEGER PRIMARY KEY,
  contract_id TEXT NOT NULL REFERENCES data_contract(contract_id),
  ran_at      TEXT NOT NULL,
  violations  INTEGER NOT NULL,
  sample_json TEXT,
  duration_ms REAL
);

-- ============================================================================
-- ASSURANCE: how we prove logic (and agent-written code) correct before it acts
--   tests      unittest results, hand-computed expectations
--   evals      each "model" (normalizer, classifier, parser, ATP, MRP) scored
--              against golden truth; a suite below threshold fails its gate
--   contracts  data_contract / contract_run above
--   review     every mapping / rule / logic change carries its evidence and a
--              human decision before it deploys
-- ============================================================================

CREATE TABLE eval_suite (
  suite_id    TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  target      TEXT NOT NULL,               -- the code under evaluation, e.g. 'ops.ingest.cm_mes'
  method      TEXT NOT NULL CHECK (method IN ('GOLDEN_TRUTH','LABELED_SET','BACKTEST','TEXTBOOK_CASES')),
  metric      TEXT NOT NULL,
  threshold   REAL NOT NULL,
  description TEXT NOT NULL
);

-- Golden expectations captured from the simulator's ground truth (or hand-built)
CREATE TABLE eval_golden (
  suite_id      TEXT NOT NULL REFERENCES eval_suite(suite_id),
  case_id       TEXT NOT NULL,
  input_ref     TEXT,
  expected_json TEXT NOT NULL,
  PRIMARY KEY (suite_id, case_id)
);

CREATE TABLE eval_run (
  run_id          INTEGER PRIMARY KEY,
  suite_id        TEXT NOT NULL REFERENCES eval_suite(suite_id),
  ran_at          TEXT NOT NULL,
  subject_version TEXT NOT NULL,
  cases           INTEGER NOT NULL,
  passed          INTEGER NOT NULL,
  score           REAL NOT NULL,
  gate            TEXT NOT NULL CHECK (gate IN ('PASS','FAIL')),
  prev_score      REAL,                     -- the suite's score on its previous run (NULL on its first)
  regressed       INTEGER NOT NULL DEFAULT 0 CHECK (regressed IN (0,1)),  -- score fell below prev_score
  metrics_json    TEXT
);

CREATE TABLE eval_case (
  run_id    INTEGER NOT NULL REFERENCES eval_run(run_id),
  case_id   TEXT NOT NULL,
  input_ref TEXT,
  expected  TEXT,
  actual    TEXT,
  passed    INTEGER NOT NULL CHECK (passed IN (0,1)),
  note      TEXT,
  PRIMARY KEY (run_id, case_id)
);

CREATE TABLE test_run (
  run_id      INTEGER PRIMARY KEY,
  ran_at      TEXT NOT NULL,
  suite       TEXT NOT NULL,
  tests       INTEGER NOT NULL,
  failures    INTEGER NOT NULL,
  errors      INTEGER NOT NULL,
  skipped     INTEGER NOT NULL,
  duration_s  REAL NOT NULL,
  detail_json TEXT                         -- per-test name, status, message
);

CREATE TABLE change_review (
  change_id      TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  component      TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('MAPPING','RULE','LOGIC','SCHEMA','PARAMETER')),
  author         TEXT NOT NULL,
  proposed_at    TEXT NOT NULL,
  diff_summary   TEXT NOT NULL,
  test_run_id    INTEGER REFERENCES test_run(run_id),
  eval_run_id    INTEGER REFERENCES eval_run(run_id),
  contracts_ok   INTEGER CHECK (contracts_ok IN (0,1)),
  checklist_json TEXT,
  reviewer       TEXT,
  status         TEXT NOT NULL CHECK (status IN ('OPEN','APPROVED','CHANGES_REQUESTED','DEPLOYED','ROLLED_BACK')),
  decided_at     TEXT,
  notes          TEXT,
  decision_id    TEXT REFERENCES decision_log(decision_id)
);

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX ix_unit_item_status      ON unit(item_id, status);
CREATE INDEX ix_unit_location         ON unit(location_site_id, status);
CREATE INDEX ix_gen_parent            ON genealogy(parent_serial);
CREATE INDEX ix_gen_child_serial      ON genealogy(child_serial);
CREATE INDEX ix_gen_child_lot         ON genealogy(child_lot_id);
-- The grain, enforced: one current link per parent, slot and child. A feed retry the event dedup misses (its timestamp
-- was corrected) cannot record a link twice; feed writers insert with ON CONFLICT DO NOTHING. Two different children
-- in one slot is a conflicting fact, not a duplicate: it lands, and contracts flag it.
CREATE UNIQUE INDEX ux_gen_current_link ON genealogy(parent_serial, position, COALESCE(child_serial, child_lot_id))
  WHERE removed_at IS NULL;
CREATE INDEX ix_lotlink_parent       ON lot_link(parent_lot_id);
CREATE INDEX ix_se_serial             ON station_event(serial, event_ts);
CREATE INDEX ix_se_station_ts         ON station_event(station_id, event_ts);
CREATE INDEX ix_lot_item              ON lot(item_id, received_at);
CREATE INDEX ix_order_status          ON customer_order(status, ordered_at);
CREATE INDEX ix_ol_item               ON order_line(item_id);
CREATE INDEX ix_ol_vehicle            ON order_line(vehicle_serial);
CREATE INDEX ix_ol_pack               ON order_line(pack_serial);
CREATE INDEX ix_plo_run_item          ON planned_order(run_id, item_id, due_date);
CREATE INDEX ix_mrpmsg_run            ON mrp_message(run_id, item_id);
CREATE INDEX ix_promise_order         ON order_promise(order_id);
CREATE INDEX ix_bp_item_date          ON build_plan(item_id, plan_date);
CREATE INDEX ix_pol_item              ON po_line(item_id, status);
CREATE INDEX ix_pph_line              ON po_promise_history(po_id, line_no);
CREATE INDEX ix_su_serial             ON shipment_unit(serial);
CREATE INDEX ix_sev_shipment          ON shipment_event(shipment_id, event_ts);
CREATE INDEX ix_ship_order            ON shipment(order_id);
CREATE INDEX ix_ship_leg_status       ON shipment(leg, status);
CREATE INDEX ix_inv_item_site         ON inventory_balance(item_id, site_id);
CREATE INDEX ix_qe_item               ON quality_event(item_id, detected_at);
CREATE INDEX ix_qe_lot                ON quality_event(lot_id);
CREATE INDEX ix_wc_lot                ON warranty_claim(failed_lot_id);
CREATE INDEX ix_wc_serial             ON warranty_claim(serial);
CREATE INDEX ix_raw_cm_status         ON raw_cm_mes_event(ingest_status);
CREATE INDEX ix_raw_cm_received       ON raw_cm_mes_event(received_at);
CREATE INDEX ix_pe_case               ON process_event(process, case_id, ts);
CREATE INDEX ix_tel_app_feature       ON app_telemetry(app, feature);
CREATE INDEX ix_fl_item_week          ON forecast_line(item_id, week_start);
CREATE INDEX ix_hold_serial           ON hold(serial);
CREATE INDEX ix_unit_wo               ON unit(wo_id);
CREATE INDEX ix_wo_site_date          ON work_order(site_id, sched_date);
CREATE INDEX ix_gr_po                 ON goods_receipt(po_id, line_no);
CREATE INDEX ix_inv_po                ON supplier_invoice(po_id, line_no);
CREATE INDEX ix_istep_ref             ON ingest_step(source_ref, seq);
CREATE INDEX ix_att_email             ON raw_attachment(raw_id);
CREATE INDEX ix_email_received        ON raw_email(received_at);
CREATE INDEX ix_outbound_decision     ON outbound_message(decision_id);

-- ============================================================================
-- VIEWS
-- ============================================================================

-- Active as-built edges only (history kept in genealogy.removed_at)
CREATE VIEW v_genealogy_active AS
  SELECT * FROM genealogy WHERE removed_at IS NULL;

-- One row per PO line with the slip the planner cares about
CREATE VIEW v_po_line_status AS
  SELECT pl.*, po.supplier_id, po.ship_to_site_id, po.created_at AS po_created_at,
         CAST(julianday(COALESCE(pl.promise_date, pl.need_date)) - julianday(pl.need_date) AS INTEGER) AS days_late_vs_need,
         (SELECT COUNT(*) FROM po_promise_history h
           WHERE h.po_id = pl.po_id AND h.line_no = pl.line_no) AS promise_changes
  FROM po_line pl JOIN purchase_order po USING (po_id);

-- Multi-echelon inventory position: serialized units + non-serialized balances
CREATE VIEW v_inventory_position AS
  SELECT u.location_site_id AS site_id, u.item_id,
         CASE WHEN u.on_hold = 1 THEN 'HOLD' ELSE u.status END AS bucket,
         'OEM' AS owner, COUNT(*) AS qty, 'unit' AS source_table
  FROM unit u
  JOIN item i ON i.item_id = u.item_id
  WHERE i.kind IN ('VEHICLE','PACK') AND u.status IN ('BUILT','IN_TRANSIT','AT_3PL','ALLOCATED')
  GROUP BY 1,2,3
  UNION ALL
  SELECT u.location_site_id, u.item_id, 'COMPONENT', 'OEM', COUNT(*), 'unit'
  FROM unit u JOIN item i ON i.item_id = u.item_id
  WHERE i.kind IN ('MODULE','COMPONENT') AND u.status = 'COMPONENT' AND u.location_site_id IS NOT NULL
  GROUP BY 1,2
  UNION ALL
  SELECT b.site_id, b.item_id, b.stock_status, b.owner, SUM(b.qty), 'inventory_balance'
  FROM inventory_balance b
  GROUP BY 1,2,3,4;

-- First-pass yield by station and day: a unit passes first time if its first
-- event at that station is a PASS.
CREATE VIEW v_fpy_daily AS
  WITH firsts AS (
    SELECT serial, station_id, MIN(event_ts) AS first_ts
    FROM station_event GROUP BY serial, station_id
  )
  SELECT se.station_id, substr(se.event_ts, 1, 10) AS day,
         COUNT(*) AS units,
         SUM(CASE WHEN se.result = 'PASS' THEN 1 ELSE 0 END) AS first_pass,
         ROUND(1.0 * SUM(CASE WHEN se.result = 'PASS' THEN 1 ELSE 0 END) / COUNT(*), 4) AS fpy
  FROM firsts f
  JOIN station_event se ON se.serial = f.serial AND se.station_id = f.station_id AND se.event_ts = f.first_ts
  GROUP BY se.station_id, day;
