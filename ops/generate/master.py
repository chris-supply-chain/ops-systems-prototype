"""Master data: sites, suppliers (tiers 1-3), items, BOMs with effectivity,
prices with effectivity, stations, defect codes, ECOs, GL accounts.

All names are fictional. Product structure is invented for the prototype.
"""
import json

from .util import add_days, dstr

PT, TPE = "America/Los_Angeles", "Asia/Taipei"

SITES = [
    # site_id, name, kind, operator, city, region, country, lat, lon, tz
    ("HQ-PA", "OEM HQ · Palo Alto", "HQ", "OEM", "Palo Alto", "CA", "US", 37.44, -122.14, PT),
    ("OEM-FRE", "OEM Pack Line · Fremont", "OEM_PLANT", "OEM", "Fremont", "CA", "US", 37.50, -121.95, PT),
    ("CM-TXG", "Formosa Assembly Partners · Taichung", "CM", "Formosa Assembly Partners", "Taichung",
     "Central Taiwan", "TW", 24.15, 120.67, TPE),
    ("PORT-TWTXG", "Port of Taichung", "PORT", "Taichung Harbor", "Taichung", "Central Taiwan", "TW", 24.29, 120.51, TPE),
    ("PORT-USOAK", "Port of Oakland", "PORT", "Port of Oakland", "Oakland", "CA", "US", 37.80, -122.32, PT),
    ("3PL-RNO", "Sierra Fulfillment · Reno", "3PL", "Sierra Fulfillment", "Reno", "NV", "US", 39.53, -119.81, PT),
    ("SUP-KES", "Kestrel Cell · Cheongju", "SUPPLIER", "Kestrel Cell Co.", "Cheongju", "Chungbuk", "KR", 36.64, 127.49, "Asia/Seoul"),
    ("SUP-PNC", "Pinecrest Electronics · Hsinchu", "SUPPLIER", "Pinecrest Electronics", "Hsinchu", "Northern Taiwan", "TW", 24.80, 120.97, TPE),
    ("SUP-SMT", "Summit Die Casting · Monterrey", "SUPPLIER", "Summit Die Casting", "Monterrey", "Nuevo León", "MX", 25.69, -100.32, "America/Monterrey"),
    ("SUP-CSP", "Cascade Precision · Portland", "SUPPLIER", "Cascade Precision", "Portland", "OR", "US", 45.52, -122.68, PT),
    ("SUP-NRT", "Norte Harness · Tijuana", "SUPPLIER", "Norte Harness", "Tijuana", "Baja California", "MX", 32.51, -117.04, "America/Tijuana"),
    ("SUP-BAY", "Bayline Seals · Hayward", "SUPPLIER", "Bayline Seals", "Hayward", "CA", "US", 37.67, -122.08, PT),
    ("SUP-TNM", "Tainan Motion Systems · Tainan", "SUPPLIER", "Tainan Motion Systems", "Tainan", "Southern Taiwan", "TW", 22.99, 120.21, TPE),
    ("SUP-HDS", "Hsinchu Display Systems · Hsinchu", "SUPPLIER", "Hsinchu Display Systems", "Hsinchu", "Northern Taiwan", "TW", 24.78, 121.00, TPE),
    ("SUP-LMB", "Lumen Board Works · Taoyuan", "SUPPLIER", "Lumen Board Works", "Taoyuan", "Northern Taiwan", "TW", 24.99, 121.30, TPE),
    ("SUP-KBS", "Keelung Board Systems · Keelung", "SUPPLIER", "Keelung Board Systems", "Keelung", "Northern Taiwan", "TW", 25.13, 121.74, TPE),
    ("SUP-PCX", "Pacific Castings · Stockton", "SUPPLIER", "Pacific Castings", "Stockton", "CA", "US", 37.96, -121.29, PT),
    ("SUP-VPC", "Voltaic Power · New Taipei", "SUPPLIER", "Voltaic Power Co.", "New Taipei", "Northern Taiwan", "TW", 25.01, 121.46, TPE),
    ("SUP-NWC", "Northwind Cathode · Himeji", "SUPPLIER", "Northwind Cathode Materials", "Himeji", "Hyogo", "JP", 34.82, 134.69, "Asia/Tokyo"),
    ("SUP-ANL", "Andes Lithium · Antofagasta", "SUPPLIER", "Andes Lithium", "Antofagasta", "Antofagasta", "CL", -23.65, -70.40, "America/Santiago"),
]

STD_TERMS = {"parts_pct": 1.0, "labor_rate_usd": 85, "labor_hours_cap": 3, "admin_fee_usd": 150,
             "containment": "actuals", "response_days": 21}

SUPPLIERS = [
    # id, name, tier, parent, is_cm, country, commodity, site, payment terms, recovery terms
    ("FAP", "Formosa Assembly Partners", 1, None, 1, "TW", "Vehicle assembly (CM)", "CM-TXG", "Net 45",
     {**STD_TERMS, "admin_fee_usd": 100}),
    ("KES", "Kestrel Cell Co.", 1, None, 0, "KR", "Li-ion cells", "SUP-KES", "Net 60", STD_TERMS),
    ("PNC", "Pinecrest Electronics", 1, None, 0, "TW", "BMS boards (EMS)", "SUP-PNC", "Net 45", STD_TERMS),
    ("SMT", "Summit Die Casting", 1, None, 0, "MX", "Pack enclosures", "SUP-SMT", "Net 45", STD_TERMS),
    ("CSP", "Cascade Precision", 1, None, 0, "US", "Busbar kits", "SUP-CSP", "Net 30", STD_TERMS),
    ("NRT", "Norte Harness", 1, None, 0, "MX", "Pack harnesses", "SUP-NRT", "Net 45", STD_TERMS),
    ("BAY", "Bayline Seals", 1, None, 0, "US", "Gaskets & seals", "SUP-BAY", "Net 30", STD_TERMS),
    ("TNM", "Tainan Motion Systems", 1, None, 0, "TW", "Drive units & pedal units", "SUP-TNM", "Net 60", STD_TERMS),
    ("HDS", "Hsinchu Display Systems", 1, None, 0, "TW", "HMI displays", "SUP-HDS", "Net 60",
     {**STD_TERMS, "admin_fee_usd": 50}),
    ("LMB", "Lumen Board Works", 1, None, 0, "TW", "BMS boards (EMS) · qualifying", "SUP-LMB", "Net 45", STD_TERMS),
    ("KBS", "Keelung Board Systems", 1, None, 0, "TW", "BMS boards (EMS) · qualifying", "SUP-KBS", "Net 45", STD_TERMS),
    ("PCX", "Pacific Castings", 1, None, 0, "US", "Die castings · qualifying", "SUP-PCX", "Net 30", STD_TERMS),
    ("VPC", "Voltaic Power Co.", 1, None, 0, "TW", "Chargers", "SUP-VPC", "Net 45", STD_TERMS),
    # CM-managed tier 2 (they sell into the CM)
    ("CYF", "Chiayi Frameworks", 2, "FAP", 0, "TW", "Frames", None, None, None),
    ("CHW", "Changhua Wheel Co.", 2, "FAP", 0, "TW", "Wheel assemblies", None, None, None),
    ("NTR", "Nantou Rubber", 2, "FAP", 0, "TW", "Tires", None, None, None),
    ("TYB", "Taoyuan Brake Systems", 2, "FAP", 0, "TW", "Hydraulic brakes", None, None, None),
    ("TCW", "Taichung Wire & Cable", 2, "FAP", 0, "TW", "Vehicle harnesses", None, None, None),
    # sub-tier materials
    ("NWC", "Northwind Cathode Materials", 2, "KES", 0, "JP", "Cathode active material", "SUP-NWC", None, None),
    ("ANL", "Andes Lithium", 3, "NWC", 0, "CL", "Lithium carbonate", "SUP-ANL", None, None),
    ("KSM", "Kansai Magnetics", 2, "TNM", 0, "JP", "NdFeB magnets", None, None, None),
    ("SLX", "Solway Oxides", 3, "KSM", 0, "MY", "NdPr oxide", None, None, None),
    ("CAL", "Cascade Aluminum", 2, "SMT", 0, "US", "Aluminum alloy", None, None, None),
    ("MVS", "Microvolt Semiconductor", 2, "PNC", 0, "US", "Battery AFE ICs", None, None, None),
    ("HFD", "Hsinchu Foundry", 3, "MVS", 0, "TW", "300mm wafers", None, None, None),
]

COLORS = [("LV1-DUNE", "Dune", 0.30), ("LV1-SLATE", "Slate", 0.34), ("LV1-FERN", "Fern", 0.21),
          ("LV1-EMBER", "Ember", 0.15)]

# item_id: (name, kind, commodity, make_buy, uom, rev, serialized, lot, LT, moq, mult, ss, std_cost, hts, dg, supplier, lifecycle)
ITEMS = {
    "LV1-DUNE":  ("LV-1 · Dune", "VEHICLE", "Vehicle", "CM_BUILT", "EA", "A", 1, 0, 42, None, None, None, 1552.0, "8711.60.00", 0, "FAP", "ACTIVE"),
    "LV1-SLATE": ("LV-1 · Slate", "VEHICLE", "Vehicle", "CM_BUILT", "EA", "A", 1, 0, 42, None, None, None, 1552.0, "8711.60.00", 0, "FAP", "ACTIVE"),
    "LV1-FERN":  ("LV-1 · Fern", "VEHICLE", "Vehicle", "CM_BUILT", "EA", "A", 1, 0, 42, None, None, None, 1552.0, "8711.60.00", 0, "FAP", "ACTIVE"),
    "LV1-EMBER": ("LV-1 · Ember", "VEHICLE", "Vehicle", "CM_BUILT", "EA", "A", 1, 0, 42, None, None, None, 1552.0, "8711.60.00", 0, "FAP", "ACTIVE"),
    "PK-STD": ("Battery pack · Standard", "PACK", "Battery pack", "OEM_BUILT", "EA", "B", 1, 0, 3, None, None, 60, 214.0, "8507.60.00", 1, None, "ACTIVE"),
    "PK-LRG": ("Battery pack · Large", "PACK", "Battery pack", "OEM_BUILT", "EA", "B", 1, 0, 3, None, None, 50, 287.0, "8507.60.00", 1, None, "ACTIVE"),
    # direct buys for the pack line
    "CEL-21700": ("Li-ion cell 21700 · 5.0Ah", "COMPONENT", "Cells", "BUY_DIRECT", "EA", "A", 0, 1, 70, 20000, 5000, 15000, 2.95, "8507.60.00", 1, "KES", "ACTIVE"),
    "BMS-A": ("BMS board rev A", "MODULE", "Electronics", "BUY_DIRECT", "EA", "A", 1, 0, 42, 500, 250, 0, 38.00, "8537.10.91", 0, "PNC", "PHASE_OUT"),
    "BMS-B": ("BMS board rev B", "MODULE", "Electronics", "BUY_DIRECT", "EA", "B", 1, 0, 42, 500, 250, 250, 41.50, "8537.10.91", 0, "PNC", "ACTIVE"),
    "ENC-STD": ("Pack enclosure · Standard", "COMPONENT", "Castings", "BUY_DIRECT", "EA", "C", 0, 1, 28, 400, 200, 200, 22.40, "7616.99.51", 0, "SMT", "ACTIVE"),
    "ENC-LRG": ("Pack enclosure · Large", "COMPONENT", "Castings", "BUY_DIRECT", "EA", "C", 0, 1, 28, 400, 200, 160, 27.90, "7616.99.51", 0, "SMT", "ACTIVE"),
    "BUS-S": ("Busbar kit · Standard", "COMPONENT", "Busbars", "BUY_DIRECT", "EA", "A", 0, 1, 21, 500, 250, 250, 6.10, "7419.80.50", 0, "CSP", "ACTIVE"),
    "BUS-L": ("Busbar kit · Large", "COMPONENT", "Busbars", "BUY_DIRECT", "EA", "A", 0, 1, 21, 500, 250, 200, 8.40, "7419.80.50", 0, "CSP", "ACTIVE"),
    "HRN-PK": ("Pack harness & connector", "COMPONENT", "Harness", "BUY_DIRECT", "EA", "A", 0, 1, 21, 600, 300, 300, 9.75, "8544.42.90", 0, "NRT", "ACTIVE"),
    "GSK-A": ("Enclosure gasket · EPDM", "COMPONENT", "Seals", "BUY_DIRECT", "EA", "A", 0, 1, 14, 1000, 500, 0, 1.80, "4016.93.10", 0, "BAY", "PHASE_OUT"),
    "CHG-1": ("Smart charger 4A", "COMPONENT", "Chargers", "BUY_DIRECT", "EA", "A", 0, 0, 45, 1000, 500, 300, 24.00, "8504.40.95", 0, "VPC", "ACTIVE"),
    "GSK-B": ("Enclosure gasket · silicone", "COMPONENT", "Seals", "BUY_DIRECT", "EA", "B", 0, 1, 14, 1000, 500, 400, 2.35, "4016.93.10", 0, "BAY", "ACTIVE"),
    # OEM-bought, consigned to the CM
    "DU-B": ("Drive unit rev B", "MODULE", "Drive units", "BUY_CONSIGNED", "EA", "B", 1, 0, 35, 300, 100, 0, 182.00, "8501.32.45", 0, "TNM", "PHASE_OUT"),
    "DU-C": ("Drive unit rev C", "MODULE", "Drive units", "BUY_CONSIGNED", "EA", "C", 1, 0, 35, 300, 100, 180, 188.40, "8501.32.45", 0, "TNM", "ACTIVE"),
    "HMI-1": ("HMI display", "MODULE", "Displays", "BUY_CONSIGNED", "EA", "A", 1, 0, 49, 500, 250, 200, 64.00, "8528.59.33", 0, "HDS", "ACTIVE"),
    "PU-1": ("Pedal unit", "MODULE", "Drive units", "BUY_CONSIGNED", "EA", "A", 1, 0, 35, 300, 100, 180, 58.00, "8714.99.80", 0, "TNM", "ACTIVE"),
    # drive-unit internals (supplier BOM, arrive on the supplier's ASN)
    "MTR-1": ("Hub motor", "COMPONENT", "Motors", "SUPPLIER_SOURCED", "EA", "A", 1, 0, None, None, None, None, 96.00, None, 0, "TNM", "ACTIVE"),
    "CTL-B": ("Motor controller rev B", "COMPONENT", "Electronics", "SUPPLIER_SOURCED", "EA", "B", 1, 0, None, None, None, None, 44.00, None, 0, "TNM", "PHASE_OUT"),
    "CTL-C": ("Motor controller rev C", "COMPONENT", "Electronics", "SUPPLIER_SOURCED", "EA", "C", 1, 0, None, None, None, None, 49.50, None, 0, "TNM", "ACTIVE"),
    # CM-sourced
    "FRM-1": ("Frame · painted", "COMPONENT", "Frames", "CM_SOURCED", "EA", "A", 1, 0, None, None, None, None, 142.00, None, 0, "CYF", "ACTIVE"),
    "WHL-F": ("Front wheel assembly", "COMPONENT", "Wheels", "CM_SOURCED", "EA", "A", 0, 1, None, None, None, None, 38.00, None, 0, "CHW", "ACTIVE"),
    "WHL-R": ("Rear wheel assembly", "COMPONENT", "Wheels", "CM_SOURCED", "EA", "A", 0, 1, None, None, None, None, 41.00, None, 0, "CHW", "ACTIVE"),
    "TIR-1": ("Tire 20 x 3.0", "COMPONENT", "Tires", "CM_SOURCED", "EA", "A", 0, 1, None, None, None, None, 17.50, None, 0, "NTR", "ACTIVE"),
    "BRK-1": ("Hydraulic brake set", "COMPONENT", "Brakes", "CM_SOURCED", "EA", "A", 0, 1, None, None, None, None, 46.00, None, 0, "TYB", "ACTIVE"),
    "HRN-VH": ("Vehicle harness", "COMPONENT", "Harness", "CM_SOURCED", "EA", "A", 0, 1, None, None, None, None, 21.00, None, 0, "TCW", "ACTIVE"),
    # tier 2-3 materials (forecast translation)
    "CAM-NMC": ("NMC cathode active material", "MATERIAL", "Battery materials", "SUPPLIER_SOURCED", "kg", None, 0, 0, None, None, None, None, 31.0, None, 0, "NWC", "ACTIVE"),
    "LI2CO3": ("Lithium carbonate", "MATERIAL", "Battery materials", "SUPPLIER_SOURCED", "kg", None, 0, 0, None, None, None, None, 11.5, None, 0, "ANL", "ACTIVE"),
    "MAG-NDFEB": ("NdFeB magnet", "MATERIAL", "Magnets", "SUPPLIER_SOURCED", "kg", None, 0, 1, None, None, None, None, 68.0, None, 0, "KSM", "ACTIVE"),
    "REO-NDPR": ("NdPr oxide", "MATERIAL", "Rare earths", "SUPPLIER_SOURCED", "kg", None, 0, 0, None, None, None, None, 78.0, None, 0, "SLX", "ACTIVE"),
    "AL-A380": ("Aluminum alloy A380", "MATERIAL", "Metals", "SUPPLIER_SOURCED", "kg", None, 0, 0, None, None, None, None, 2.9, None, 0, "CAL", "ACTIVE"),
    "AFE-IC": ("Battery AFE IC", "MATERIAL", "Semiconductors", "SUPPLIER_SOURCED", "EA", None, 0, 0, None, None, None, None, 4.10, None, 0, "MVS", "ACTIVE"),
    "WFR-300": ("300mm wafer", "MATERIAL", "Semiconductors", "SUPPLIER_SOURCED", "EA", None, 0, 0, None, None, None, None, 2400.0, None, 0, "HFD", "ACTIVE"),
}

VEHICLE_SKUS = [c[0] for c in COLORS]
PACK_SKUS = ["PK-STD", "PK-LRG"]
# Sellable finished goods: every color x pack size, kitted at the 3PL with a charger
KITS = {f"{v}-{p[3]}": (v, p) for v in VEHICLE_SKUS for p in PACK_SKUS}
for _kit, (_veh, _pk) in KITS.items():
    ITEMS[_kit] = (f"{ITEMS[_veh][0]} · {ITEMS[_pk][0].split(' · ')[1]} pack", "KIT", "Sellable kit", "KITTED", "EA", "A",
                   0, 0, 1, None, None, None, round(ITEMS[_veh][12] + ITEMS[_pk][12] + 24.0, 2), None, 1, None, "ACTIVE")
CELLS_PER_PACK = {"PK-STD": 40, "PK-LRG": 60}

STATIONS_CM = [
    # code, seq, name, kind, std cycle min, parallel fixtures
    ("S10", 10, "Frame prep & serial marry", "ASSEMBLY", 12.0, 1),
    ("S20", 20, "Drive unit install", "ASSEMBLY", 11.0, 1),
    ("S30", 30, "Pedal unit install", "ASSEMBLY", 10.0, 1),
    ("S40", 40, "Harness & HMI install", "ASSEMBLY", 14.0, 1),
    ("S50", 50, "Wheels, tires & brakes", "ASSEMBLY", 15.5, 1),
    ("S60", 60, "End-of-line test", "TEST", 13.0, 1),
    ("S70", 70, "Water ingress test", "TEST", 9.0, 1),
    ("S80", 80, "Final QA & pack-out", "PACK", 12.0, 1),
]
STATIONS_PACK = [
    ("P10", 10, "Cell sort · OCV/IR", "INSPECTION", 7.0, 1),
    ("P20", 20, "Stack & weld", "ASSEMBLY", 8.2, 1),
    ("P30", 30, "BMS install & flash", "ASSEMBLY", 6.0, 1),
    ("P40", 40, "Enclose & seal", "ASSEMBLY", 7.8, 1),
    ("P50", 50, "End-of-line test · charge/discharge, HiPot", "TEST", 92.0, 12),
    ("P60", 60, "Label & pack-out", "PACK", 5.0, 1),
]
CARRIERS = [
    ("PLL", "Pacific Link Lines", "OCEAN", "PLLU", "EDI315"),
    ("BDR", "Bayside Drayage", "DRAYAGE", "BYDR", "EMAIL"),
    ("SDG", "Sierra DG Freight", "TRUCK", "SDGF", "API"),
    ("CWF", "Crossway Freight", "WHITE_GLOVE", "CRWF", "API"),
    ("PPG", "ParcelPro Ground", "PARCEL", "PPGD", "API"),
    ("SKA", "SkyAxis Air Cargo", "AIR", "SKAX", "EMAIL"),
]
CARRIER_BY_NAME = {c[1]: c[0] for c in CARRIERS}

DEFECT_CODES = [
    ("DU-CONN", "Drive unit connector not seated", "WORKMANSHIP", "CM", None),
    ("HMI-PIXEL", "HMI dead pixels / line defect", "COMPONENT", "SUPPLIER", "HMI-1"),
    ("HRN-PINCH", "Harness pinch or chafe", "WORKMANSHIP", "CM", None),
    ("BRK-BLEED", "Brake bleed / soft lever", "WORKMANSHIP", "CM", None),
    ("EOL-MOTOR", "Motor noise above limit", "COMPONENT", "SUPPLIER", None),
    ("EOL-FW", "Firmware flash failure", "TEST_EQUIPMENT", "CM", None),
    ("EOL-BRK", "Brake torque below spec", "WORKMANSHIP", "CM", None),
    ("WTR-LEAK", "Water ingress at HMI seal", "WORKMANSHIP", "CM", None),
    ("COS-SCR", "Cosmetic scratch", "COSMETIC", "CM", None),
    ("CELL-SORT", "Cell OCV/IR outside window at sort", "COMPONENT", "SUPPLIER", "CEL-21700"),
    ("WELD-RES", "Weld resistance out of spec", "WORKMANSHIP", "OEM", None),
    ("BMS-COMM", "BMS communication failure", "COMPONENT", "SUPPLIER", "BMS-B"),
    ("SEAL-LEAK", "Enclosure seal leak-test failure", "WORKMANSHIP", "OEM", None),
    ("EOL-CAP", "Pack capacity below spec", "COMPONENT", "SUPPLIER", "CEL-21700"),
    ("EOL-HIPOT", "HiPot / insulation failure", "WORKMANSHIP", "OEM", None),
    ("LBL-MIS", "Label / serial mismatch", "WORKMANSHIP", "OEM", None),
    ("IQC-FLAT", "Sealing-face flatness out of tolerance", "COMPONENT", "SUPPLIER", "ENC-STD"),
    ("IQC-OCV", "Cell OCV/IR outliers in sample", "COMPONENT", "SUPPLIER", "CEL-21700"),
    ("IQC-COS", "Cosmetic: sidewall print offset", "COSMETIC", "SUPPLIER", "TIR-1"),
    ("IQC-DIM", "Dimensional nonconformance", "COMPONENT", "SUPPLIER", None),
    ("FLD-CAPFADE", "Pack capacity fade / won't hold charge", "FIELD", "SUPPLIER", "CEL-21700"),
    ("FLD-PIXEL", "Display dead pixels", "FIELD", "SUPPLIER", "HMI-1"),
    ("FLD-SQUEAL", "Brake squeal / rotor rub", "FIELD", "CM", "BRK-1"),
    ("FLD-NOISE", "Drive unit noise", "FIELD", "SUPPLIER", None),
    ("FLD-FW", "Firmware / connectivity fault", "FIELD", "OEM", None),
    ("FLD-CHG", "Pack not charging (BMS)", "FIELD", "SUPPLIER", "BMS-B"),
]

GL_ACCOUNTS = [
    ("1310", "Inventory: raw materials", "ASSET"),
    ("1320", "Inventory: finished goods", "ASSET"),
    ("2000", "Accounts payable", "LIABILITY"),
    ("5100", "Cost of quality: scrap, sort & rework", "EXPENSE"),
    ("5110", "Quality cost recovery: suppliers", "CONTRA_EXPENSE"),
    ("5200", "Freight: expedite", "EXPENSE"),
    ("5400", "Warranty expense", "EXPENSE"),
    ("5410", "Warranty recovery: suppliers", "CONTRA_EXPENSE"),
]

REGIONS = {
    # state: (region, weight, cities)
    "CA": ("WEST", 34, ["Palo Alto", "San Francisco", "Oakland", "San Jose", "Los Angeles", "San Diego", "Sacramento", "Santa Monica", "Berkeley", "Irvine"]),
    "WA": ("WEST", 7, ["Seattle", "Bellevue", "Tacoma"]),
    "OR": ("WEST", 5, ["Portland", "Eugene", "Bend"]),
    "NV": ("WEST", 2, ["Reno", "Las Vegas"]),
    "AZ": ("WEST", 3, ["Phoenix", "Tucson", "Tempe"]),
    "CO": ("MOUNTAIN", 5, ["Denver", "Boulder", "Fort Collins"]),
    "UT": ("MOUNTAIN", 3, ["Salt Lake City", "Park City"]),
    "ID": ("MOUNTAIN", 1, ["Boise"]),
    "NM": ("MOUNTAIN", 1, ["Santa Fe", "Albuquerque"]),
    "TX": ("CENTRAL", 8, ["Austin", "Houston", "Dallas", "San Antonio"]),
    "IL": ("CENTRAL", 4, ["Chicago", "Evanston"]),
    "MN": ("CENTRAL", 2, ["Minneapolis", "St. Paul"]),
    "NY": ("EAST", 8, ["Brooklyn", "New York", "Rochester"]),
    "NJ": ("EAST", 3, ["Jersey City", "Hoboken", "Princeton"]),
    "MA": ("EAST", 4, ["Boston", "Cambridge", "Somerville"]),
    "PA": ("EAST", 2, ["Philadelphia", "Pittsburgh"]),
    "VA": ("EAST", 2, ["Arlington", "Richmond"]),
    "FL": ("EAST", 3, ["Miami", "Tampa", "Orlando"]),
    "NC": ("EAST", 2, ["Raleigh", "Durham", "Charlotte"]),
}
TRANSIT_DAYS = {"WEST": (2, 3), "MOUNTAIN": (3, 4), "CENTRAL": (4, 5), "EAST": (5, 6)}


def write_master(w):
    """w is the World (anchors + conn). Writes every master-data table."""
    c = w.conn
    c.executemany("INSERT INTO site VALUES (?,?,?,?,?,?,?,?,?,?)", SITES)
    c.executemany(
        "INSERT INTO supplier VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8], json.dumps(s[9]) if s[9] else None)
         for s in SUPPLIERS])
    c.executemany(
        "INSERT INTO item VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(k, v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[10], v[11], v[12], v[13], v[14], v[15], v[16])
         for k, v in ITEMS.items()])

    avl = [
        ("KES", "CEL-21700", "PRIMARY", 100, 70), ("PNC", "BMS-A", "PRIMARY", 100, 42),
        ("PNC", "BMS-B", "PRIMARY", 100, 42), ("LMB", "BMS-B", "QUALIFYING", 0, 56),
        ("KBS", "BMS-B", "QUALIFYING", 0, 49), ("SMT", "ENC-STD", "PRIMARY", 100, 28),
        ("SMT", "ENC-LRG", "PRIMARY", 100, 28), ("PCX", "ENC-STD", "QUALIFYING", 0, 21),
        ("PCX", "ENC-LRG", "QUALIFYING", 0, 21), ("CSP", "BUS-S", "PRIMARY", 100, 21),
        ("CSP", "BUS-L", "PRIMARY", 100, 21), ("NRT", "HRN-PK", "PRIMARY", 100, 21),
        ("BAY", "GSK-A", "PRIMARY", 100, 14), ("BAY", "GSK-B", "PRIMARY", 100, 14),
        ("TNM", "DU-B", "PRIMARY", 100, 35), ("TNM", "DU-C", "PRIMARY", 100, 35),
        ("TNM", "PU-1", "PRIMARY", 100, 35), ("HDS", "HMI-1", "PRIMARY", 100, 49),
        ("VPC", "CHG-1", "PRIMARY", 100, 45),
    ] + [("FAP", sku, "PRIMARY", 100, 42) for sku in VEHICLE_SKUS]
    c.executemany("INSERT INTO supplier_item VALUES (?,?,?,?,?)", avl)

    a = w.as_of
    ecos = [
        ("ECO-0031", "BMS rev B: add reverse-polarity protection FET",
         "Two field returns of BMS rev A with reverse-polarity damage from third-party chargers",
         "QUALITY", "IMPLEMENTED", "DATE", dstr(w.eco31), None, None, "BMS-A", "BMS-B", "USE_UP", 3.50,
         "Electrical eng.", dstr(add_days(w.eco31, -30)), dstr(add_days(w.eco31, -14)), dstr(w.eco31)),
        ("ECO-0036", "Enclosure gasket: EPDM to silicone for IP67 margin at low temperature",
         "NCR-0019: seal leak-test failures clustered on cold mornings at P40",
         "QUALITY", "IMPLEMENTED", "DATE", dstr(w.eco36), None, None, "GSK-A", "GSK-B", "USE_UP", 0.55,
         "Mechanical eng.", dstr(add_days(w.eco36, -21)), dstr(add_days(w.eco36, -9)), dstr(w.eco36)),
        ("ECO-0042", "Drive unit rev C: field-updatable controller bootloader + thermal pad",
         "Enables OTA controller updates; lowers controller temperature 6°C at sustained climb",
         "FORM_FIT_FUNCTION", "IMPLEMENTED", "SERIAL", dstr(w.eco42), None, None, "DU-B", "DU-C",
         "USE_UP", 6.40, "Propulsion eng.", dstr(add_days(w.eco42, -35)), dstr(add_days(w.eco42, -16)), dstr(w.eco42)),
        ("ECO-0047", "Pack enclosure: add pressure-equalization vent membrane",
         "Thermal-event venting path; certification pre-scan recommendation",
         "COMPLIANCE", "IN_REVIEW", "DATE", dstr(add_days(a, 30)), None, "PK-LRG", None, None, None, 1.20,
         "Battery eng.", dstr(add_days(a, -9)), None, None),
        ("ECO-0049", "HMI cover glass: second-source glass supplier",
         "De-risk single-source cover glass ahead of Q1 volume", "SUPPLY", "DRAFT", "LOT", None, None,
         "HMI-1", None, None, None, -0.80, "Sourcing", dstr(add_days(a, -3)), None, None),
    ]
    c.executemany("INSERT INTO eco VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ecos)

    early = dstr(add_days(w.cm_sop, -120))
    bom = []
    for kit, (veh, pk) in KITS.items():
        bom += [(kit, veh, "VEHICLE", 1, "OEM", early, None, None),
                (kit, pk, "PACK", 1, "OEM", early, None, None),
                (kit, "CHG-1", "CHARGER", 1, "OEM", early, None, None)]
    for pk, cells, enc, bus in (("PK-STD", 40, "ENC-STD", "BUS-S"), ("PK-LRG", 60, "ENC-LRG", "BUS-L")):
        bom += [
            (pk, "CEL-21700", "CELLS", cells, "OEM", early, None, None),
            (pk, "BMS-A", "BMS", 1, "OEM", early, dstr(w.eco31), None),
            (pk, "BMS-B", "BMS", 1, "OEM", dstr(w.eco31), None, "ECO-0031"),
            (pk, enc, "ENCLOSURE", 1, "OEM", early, None, None),
            (pk, bus, "BUSBAR", 1, "OEM", early, None, None),
            (pk, "HRN-PK", "HARNESS", 1, "OEM", early, None, None),
            (pk, "GSK-A", "GASKET", 1, "OEM", early, dstr(w.eco36), None),
            (pk, "GSK-B", "GASKET", 1, "OEM", dstr(w.eco36), None, "ECO-0036"),
        ]
    for sku in VEHICLE_SKUS:
        bom += [
            (sku, "DU-B", "DRIVE_UNIT", 1, "OEM", early, dstr(w.eco42), None),
            (sku, "DU-C", "DRIVE_UNIT", 1, "OEM", dstr(w.eco42), None, "ECO-0042"),
            (sku, "PU-1", "PEDAL_UNIT", 1, "OEM", early, None, None),
            (sku, "HMI-1", "HMI", 1, "OEM", early, None, None),
            (sku, "FRM-1", "FRAME", 1, "CM", early, None, None),
            (sku, "WHL-F", "WHEEL_F", 1, "CM", early, None, None),
            (sku, "WHL-R", "WHEEL_R", 1, "CM", early, None, None),
            (sku, "TIR-1", "TIRES", 2, "CM", early, None, None),
            (sku, "BRK-1", "BRAKES", 1, "CM", early, None, None),
            (sku, "HRN-VH", "HARNESS", 1, "CM", early, None, None),
        ]
    bom += [
        ("DU-B", "MTR-1", "MOTOR", 1, "SUPPLIER", early, None, None),
        ("DU-B", "CTL-B", "CONTROLLER", 1, "SUPPLIER", early, None, None),
        ("DU-C", "MTR-1", "MOTOR", 1, "SUPPLIER", early, None, None),
        ("DU-C", "CTL-C", "CONTROLLER", 1, "SUPPLIER", early, None, None),
        ("MTR-1", "MAG-NDFEB", "MAGNETS", 0.42, "SUPPLIER", early, None, None),
        ("MAG-NDFEB", "REO-NDPR", "OXIDE", 0.31, "SUPPLIER", early, None, None),
        ("CEL-21700", "CAM-NMC", "CATHODE", 0.021, "SUPPLIER", early, None, None),
        ("CAM-NMC", "LI2CO3", "LITHIUM", 0.39, "SUPPLIER", early, None, None),
        ("ENC-STD", "AL-A380", "ALLOY", 1.15, "SUPPLIER", early, None, None),
        ("ENC-LRG", "AL-A380", "ALLOY", 1.55, "SUPPLIER", early, None, None),
        ("BMS-A", "AFE-IC", "AFE", 2, "SUPPLIER", early, None, None),
        ("BMS-B", "AFE-IC", "AFE", 2, "SUPPLIER", early, None, None),
        ("AFE-IC", "WFR-300", "WAFER", 0.0016, "SUPPLIER", early, None, None),
    ]
    c.executemany(
        "INSERT INTO bom_line(parent_item_id, child_item_id, position, qty_per, bom_level, eff_from, eff_to, eco_id)"
        " VALUES (?,?,?,?,?,?,?,?)", bom)

    p0 = dstr(add_days(w.cm_sop, -90))
    prices = [
        ("CEL-21700", "KES", 0, 3.10, p0, dstr(w.cell_price_step), "CONTRACT", "KES-2026-01"),
        ("CEL-21700", "KES", 0, 2.95, dstr(w.cell_price_step), dstr(add_days(a, 35)), "CONTRACT", "KES-2026-02"),
        ("CEL-21700", "KES", 100000, 2.90, dstr(w.cell_price_step), dstr(add_days(a, 35)), "CONTRACT", "KES-2026-02"),
        ("CEL-21700", "KES", 0, 2.88, dstr(add_days(a, 35)), None, "CONTRACT", "KES-2026-03"),
        ("BMS-A", "PNC", 0, 38.00, p0, None, "CONTRACT", "PNC-2026-01"),
        ("BMS-B", "PNC", 0, 41.50, dstr(add_days(w.eco31, -14)), None, "ECO", "ECO-0031"),
        ("BMS-B", "LMB", 0, 39.20, dstr(add_days(a, -6)), None, "QUOTE", "RFQ-0012"),
        ("BMS-B", "KBS", 0, 40.10, dstr(add_days(a, -4)), None, "QUOTE", "RFQ-0012"),
        ("ENC-STD", "SMT", 0, 22.40, p0, None, "CONTRACT", "SMT-2026-01"),
        ("ENC-LRG", "SMT", 0, 27.90, p0, None, "CONTRACT", "SMT-2026-01"),
        ("BUS-S", "CSP", 0, 6.10, p0, None, "CONTRACT", "CSP-2026-01"),
        ("BUS-L", "CSP", 0, 8.40, p0, None, "CONTRACT", "CSP-2026-01"),
        ("HRN-PK", "NRT", 0, 9.75, p0, None, "CONTRACT", "NRT-2026-01"),
        ("GSK-A", "BAY", 0, 1.80, p0, None, "CONTRACT", "BAY-2026-01"),
        ("GSK-B", "BAY", 0, 2.35, dstr(add_days(w.eco36, -9)), None, "ECO", "ECO-0036"),
        ("DU-B", "TNM", 0, 182.00, p0, None, "CONTRACT", "TNM-2026-01"),
        ("DU-C", "TNM", 0, 188.40, dstr(add_days(w.eco42, -16)), None, "ECO", "ECO-0042"),
        ("PU-1", "TNM", 0, 58.00, p0, None, "CONTRACT", "TNM-2026-01"),
        ("HMI-1", "HDS", 0, 64.00, p0, dstr(add_days(a, -20)), "CONTRACT", "HDS-2026-01"),
        ("HMI-1", "HDS", 0, 61.50, dstr(add_days(a, -20)), None, "CONTRACT", "HDS-2026-02"),
        ("CHG-1", "VPC", 0, 24.00, p0, None, "CONTRACT", "VPC-2026-01"),
    ]
    for sku in VEHICLE_SKUS:
        prices += [(sku, "FAP", 0, 1265.00, p0, dstr(w.cm_price_step), "CONTRACT", "FAP-MSA-2026"),
                   (sku, "FAP", 0, 1248.00, dstr(w.cm_price_step), None, "CONTRACT", "FAP-MSA-2026-A1")]
    c.executemany(
        "INSERT INTO price(item_id, supplier_id, min_qty, unit_price, eff_from, eff_to, basis, source_ref)"
        " VALUES (?,?,?,?,?,?,?,?)", prices)

    stations = []
    for line in ("L1", "L2"):
        for code, seq, name, kind, cyc, fx in STATIONS_CM:
            stations.append((f"TXG-{line}-{code}", "CM-TXG", line, code, seq, name, kind, cyc, fx))
    for code, seq, name, kind, cyc, fx in STATIONS_PACK:
        stations.append((f"FRE-P1-{code}", "OEM-FRE", "P1", code, seq, name, kind, cyc, fx))
    c.executemany("INSERT INTO station VALUES (?,?,?,?,?,?,?,?,?)", stations)
    c.executemany("INSERT INTO carrier VALUES (?,?,?,?,?)", CARRIERS)

    # line capacity, time fences
    c.executemany("INSERT INTO line_capacity VALUES (?,?,?,?,?,?,?,?)", [
        ("CM-TXG", "L1", dstr(w.cm_sop), 1, 570, 17.8, 32, 0.85),
        ("CM-TXG", "L2", dstr(w.line2_start), 1, 570, 21.9, 26, 0.85),
        ("CM-TXG", "L2", dstr(add_days(a, 35)), 1, 570, 19.0, 30, 0.85),
        ("OEM-FRE", "P1", dstr(w.pack_sop), 1, 630, 9.5, 66, 0.82),
    ])
    c.executemany("INSERT INTO time_fence VALUES (?,?,?,?)", [
        ("CM-TXG", 21, 42, "CM commit is frozen 3 weeks out (material staged at the CM); slushy to 6 weeks"),
        ("OEM-FRE", 5, 15, "Pack MPS frozen one week; cells are the long pole beyond that"),
        ("3PL-RNO", 1, 3, "Kitting is planned daily against allocations"),
    ])

    # FX for suppliers that report or invoice in local currency (mock daily rates)
    fx = []
    base = {"TWD": 1 / 32.4, "KRW": 1 / 1382.0, "MXN": 1 / 18.6, "JPY": 1 / 147.0}
    d0 = add_days(a, -200)
    for k in range(0, 201):
        day = add_days(d0, k)
        for cur, v in base.items():
            wobble = 1 + 0.012 * __import__("math").sin(k / 17.0 + len(cur))
            fx.append((cur, dstr(day), round(v * wobble, 8)))
    c.executemany("INSERT INTO fx_rate VALUES (?,?,?)", fx)

    # quality control plan: what is measured, where, against which limits
    c.executemany("INSERT INTO control_plan VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("CP-S60-NOISE", "CM-TXG", "TXG-L1-S60", "DU-C", "motor_db", "dB(A)", None, 42.0, 38.0,
         "Dyno run-up, microphone at 0.5 m", "100%", "Fail -> swap drive unit at rework; 3 fails/shift -> stop & call OEM SQE"),
        ("CP-S60-BRAKE", "CM-TXG", "TXG-L1-S60", "BRK-1", "brake_nm", "N·m", 25.0, None, 28.5,
         "Roller brake tester", "100%", "Fail -> re-bleed and re-test; 2 consecutive -> check S50 torque tool"),
        ("CP-S70-LEAK", "CM-TXG", "TXG-L1-S70", None, "leak_ccm", "cc/min", None, 1.0, 0.3,
         "Pressure-decay leak test", "100%", "Fail -> reseat HMI seal and re-test"),
        ("CP-P50-CAP-S", "OEM-FRE", "FRE-P1-P50", "PK-STD", "capacity_ah", "Ah", 39.0, None, 40.4,
         "Full charge/discharge at C/3", "100%", "Fail -> quarantine pack, open NCR, check cell lot"),
        ("CP-P50-CAP-L", "OEM-FRE", "FRE-P1-P50", "PK-LRG", "capacity_ah", "Ah", 58.5, None, 60.6,
         "Full charge/discharge at C/3", "100%", "Fail -> quarantine pack, open NCR, check cell lot"),
        ("CP-P50-IR-S", "OEM-FRE", "FRE-P1-P50", "PK-STD", "ir_mohm", "mΩ", None, 64.0, 58.0,
         "AC internal resistance at 1 kHz", "100%", "Above USL -> teardown sample of 2 per lot"),
        ("CP-P50-IR-L", "OEM-FRE", "FRE-P1-P50", "PK-LRG", "ir_mohm", "mΩ", None, 44.0, 39.0,
         "AC internal resistance at 1 kHz", "100%", "Above USL -> teardown sample of 2 per lot"),
        ("CP-IQC-CELL", "OEM-FRE", None, "CEL-21700", "ocv_ir_window", "pass/fail", None, None, None,
         "OCV + IR on c=0 sample", "Every lot", "Any outlier -> 100% sort; > 0.5% -> reject lot"),
        ("CP-IQC-ENC", "OEM-FRE", None, "ENC-STD", "sealing_flatness", "mm", None, 0.10, 0.04,
         "CMM on sealing face, c=0 sample", "Every lot", "Any reject -> RTV lot, chargeback, 100% check next 3 lots"),
    ])
    c.executemany("INSERT INTO defect_code VALUES (?,?,?,?,?)", DEFECT_CODES)
    c.executemany("INSERT INTO gl_account VALUES (?,?,?)", GL_ACCOUNTS)


def price_on(conn, item_id, supplier_id, day, qty=0):
    """Effective unit price for an item/supplier on a date (respects price breaks)."""
    row = conn.execute(
        "SELECT price_id, unit_price FROM price WHERE item_id=? AND supplier_id=? AND eff_from<=?"
        " AND (eff_to IS NULL OR eff_to>?) AND min_qty<=? ORDER BY min_qty DESC, eff_from DESC LIMIT 1",
        (item_id, supplier_id, day, day, qty)).fetchone()
    return row
