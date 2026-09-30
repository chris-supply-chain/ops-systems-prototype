"""Process mining over process_event: cases, variants, the directly-follows graph,
glue analysis and the "delete these steps" projection.

Pure functions over plain rows, so they test without a database:
    rows        iterable of dicts with case_id, activity, ts (ISO-8601), actor_role
    categories  {activity: 'VALUE' | 'CONTROL' | 'GLUE' | 'WAIT'}

Time between two consecutive events is charged to the later activity: that is
the wait plus work it took for that step to happen. Deleting a step deletes the
time charged to it.
"""
import datetime as dt
from collections import Counter, defaultdict
from statistics import median

START, END = "▶ start", "■ end"

# The path the process was designed to follow (what the SOP says)
DESIGNED = {
    "PO_CONFIRMATION": ["Create PO", "Transmit PO (EDI 850)", "Supplier acknowledges (EDI 855)",
                        "Promise loaded to ERP", "MRP consumes promise"],
    "DEVIATION_APPROVAL": ["Deviation requested by email", "Route for signatures", "Quality approves",
                           "Engineering approves", "MES updated to allow part"],
    "INBOUND_RECEIPT": ["Container unloaded", "Serials scanned", "Put away", "Available to allocate"],
}
TITLES = {
    "PO_CONFIRMATION": "PO confirmation → promise in the plan",
    "DEVIATION_APPROVAL": "Deviation approval",
    "INBOUND_RECEIPT": "Inbound receipt at the 3PL",
}
NON_HUMAN = {"System", "Supplier", "Carrier"}
CONFIRM_EVENTS = ("Supplier acknowledges (EDI 855)", "Supplier confirms in portal", "Supplier replies by email",
                  "Supplier sends open-order Excel")


def parse_ts(s):
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=dt.timezone.utc)
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def hours(a, b):
    return (b - a).total_seconds() / 3600.0


def build_cases(rows):
    """{case_id: [{'activity', 'ts', 'role'}...]} sorted by time (stable on ties)."""
    cases = defaultdict(list)
    for i, r in enumerate(rows):
        cases[r["case_id"]].append({"activity": r["activity"], "ts": parse_ts(r["ts"]), "role": r.get("actor_role"), "i": i})
    for evs in cases.values():
        evs.sort(key=lambda e: (e["ts"], e["i"]))
    return dict(cases)


def cycle_hours(events):
    return hours(events[0]["ts"], events[-1]["ts"]) if len(events) > 1 else 0.0


def _q(values, p):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def variants(cases):
    """Distinct activity sequences, most frequent first."""
    groups = defaultdict(list)
    for cid, evs in cases.items():
        groups[tuple(e["activity"] for e in evs)].append(cid)
    total = max(1, len(cases))
    out = []
    for seq, cids in groups.items():
        cyc = [cycle_hours(cases[c]) for c in cids]
        out.append({"sequence": list(seq), "cases": len(cids), "share": len(cids) / total,
                    "median_cycle_h": median(cyc), "p90_cycle_h": _q(cyc, 0.9), "example": sorted(cids)[0],
                    "case_ids": sorted(cids)})
    out.sort(key=lambda v: (-v["cases"], v["median_cycle_h"], v["sequence"]))
    for i, v in enumerate(out):
        v["rank"] = i + 1
    return out


def dfg(cases):
    """Directly-follows graph with artificial start/end nodes.
    nodes: {activity: {'count', 'cases'}}; edges: {(a, b): {'count', 'median_h'}}"""
    node_count = Counter()
    node_cases = defaultdict(set)
    edge_count = Counter()
    edge_dur = defaultdict(list)
    for cid, evs in cases.items():
        prev, prev_t = START, None
        node_count[START] += 1
        node_cases[START].add(cid)
        for e in evs:
            node_count[e["activity"]] += 1
            node_cases[e["activity"]].add(cid)
            edge_count[(prev, e["activity"])] += 1
            if prev_t is not None:
                edge_dur[(prev, e["activity"])].append(hours(prev_t, e["ts"]))
            prev, prev_t = e["activity"], e["ts"]
        edge_count[(prev, END)] += 1
        node_count[END] += 1
        node_cases[END].add(cid)
    nodes = {a: {"count": node_count[a], "cases": len(node_cases[a])} for a in node_count}
    edges = {k: {"count": n, "median_h": median(edge_dur[k]) if edge_dur[k] else 0.0} for k, n in edge_count.items()}
    return nodes, edges


def layers(nodes, edges):
    """Longest-path layering after removing back edges (DFS from start, heaviest edges first).
    Returns ({activity: layer}, set_of_back_edges)."""
    out = defaultdict(list)
    for (a, b), e in edges.items():
        if a != b:
            out[a].append((e["count"], b))
    for a in out:
        out[a].sort(key=lambda x: (-x[0], x[1]))
    state, back = {}, set()

    def visit(u):
        state[u] = 1
        for _, v in out.get(u, []):
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                visit(v)
        state[u] = 2

    visit(START)
    for n in sorted(nodes):
        if n not in state:
            visit(n)
    forward = defaultdict(list)
    indeg = Counter()
    for (a, b) in edges:
        if a != b and (a, b) not in back:
            forward[a].append(b)
            indeg[b] += 1
    layer = {n: 0 for n in nodes}
    queue = [n for n in nodes if indeg[n] == 0]
    seen = 0
    while queue:
        u = queue.pop(0)
        seen += 1
        for v in forward[u]:
            layer[v] = max(layer[v], layer[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    # the end node always sits alone in the last column
    last = max((l for n, l in layer.items() if n != END), default=0)
    layer[END] = last + 1
    return layer, back


def glue_analysis(cases, categories):
    """Time and human touches by category and by activity."""
    by_cat = defaultdict(lambda: {"hours": 0.0, "events": 0, "touches": 0})
    by_act = defaultdict(lambda: {"hours": [], "events": 0, "touches": 0})
    for evs in cases.values():
        for i, e in enumerate(evs):
            cat = categories.get(e["activity"], "VALUE")
            gap = hours(evs[i - 1]["ts"], e["ts"]) if i else 0.0
            human = e["role"] not in NON_HUMAN
            by_cat[cat]["hours"] += gap
            by_cat[cat]["events"] += 1
            by_cat[cat]["touches"] += 1 if human else 0
            a = by_act[e["activity"]]
            a["hours"].append(gap)
            a["events"] += 1
            a["touches"] += 1 if human else 0
    total_h = sum(c["hours"] for c in by_cat.values()) or 1.0
    n = max(1, len(cases))
    cats = [{"category": k, "hours": v["hours"], "share": v["hours"] / total_h, "events": v["events"],
             "touches": v["touches"], "touches_per_case": v["touches"] / n}
            for k, v in sorted(by_cat.items())]
    acts = []
    for k, v in by_act.items():
        acts.append({"activity": k, "category": categories.get(k, "VALUE"), "events": v["events"],
                     "touches": v["touches"], "hours_total": sum(v["hours"]),
                     "median_h": median(v["hours"]) if v["hours"] else 0.0,
                     "share": sum(v["hours"]) / total_h})
    acts.sort(key=lambda a: -a["hours_total"])
    return {"categories": cats, "activities": acts, "total_hours": total_h}


def projection(cases, categories, delete=("GLUE", "WAIT")):
    """Cycle time per case if every step in the deleted categories disappeared with its charged time."""
    before, after, touches_before, touches_after = [], [], [], []
    for evs in cases.values():
        before.append(cycle_hours(evs))
        kept = 0.0
        tb = ta = 0
        for i, e in enumerate(evs):
            cat = categories.get(e["activity"], "VALUE")
            human = e["role"] not in NON_HUMAN
            tb += 1 if human else 0
            if cat in delete:
                continue
            ta += 1 if human else 0
            if i:
                kept += hours(evs[i - 1]["ts"], e["ts"])
        after.append(kept)
        touches_before.append(tb)
        touches_after.append(ta)
    mb, ma = (median(before) if before else 0.0), (median(after) if after else 0.0)
    return {"median_before_h": mb, "median_after_h": ma, "reduction": (1 - ma / mb) if mb else 0.0,
            "p90_before_h": _q(before, 0.9), "p90_after_h": _q(after, 0.9),
            "touches_before": sum(touches_before) / max(1, len(cases)),
            "touches_after": sum(touches_after) / max(1, len(cases)), "deleted": list(delete)}


def conformance(cases, designed):
    if not designed or not cases:
        return {"designed": designed or [], "conforming": 0, "share": 0.0}
    ok = sum(1 for evs in cases.values() if [e["activity"] for e in evs] == designed)
    return {"designed": designed, "conforming": ok, "share": ok / len(cases)}


def confirm_to_plan(cases):
    """PO confirmations: hours from the supplier's confirmation to the plan consuming it, by path."""
    paths = defaultdict(list)
    for cid, evs in cases.items():
        acts = [e["activity"] for e in evs]
        conf = next((e for e in evs if e["activity"] in CONFIRM_EVENTS), None)
        mrp = next((e for e in reversed(evs) if e["activity"] == "MRP consumes promise"), None)
        if not conf or not mrp:
            continue
        if conf["activity"] in ("Supplier acknowledges (EDI 855)", "Supplier confirms in portal"):
            path = "EDI 855 / supplier portal"
        elif "Platform parses email/Excel" in acts:
            path = "Email/Excel → platform parser"
        else:
            path = "Email/Excel → re-keyed by hand"
        paths[path].append(hours(conf["ts"], mrp["ts"]))
    order = ["Email/Excel → re-keyed by hand", "Email/Excel → platform parser", "EDI 855 / supplier portal"]
    return [{"path": p, "cases": len(paths[p]), "median_h": median(paths[p]), "p90_h": _q(paths[p], 0.9)}
            for p in order if paths.get(p)]


def rare_variants(vars_, designed, top=None, max_share=0.03):
    """Variants almost nobody planned for: rare, and carrying steps outside the designed path."""
    base = set(designed or []) | set((top or {}).get("sequence", []))
    out = []
    for v in vars_:
        if v["share"] > max_share or v["cases"] > 3:
            continue
        extra = []
        for a in v["sequence"]:
            if a not in base and a not in extra:
                extra.append(a)
        repeats = [a for a, n in Counter(v["sequence"]).items() if n > 1]
        out.append({**{k: v[k] for k in ("rank", "sequence", "cases", "share", "median_cycle_h", "example")},
                    "distinct_steps": extra, "repeated_steps": repeats})
    return out


def analyze(rows, categories, process):
    """Everything the Process Lab page shows for one process."""
    cases = build_cases(rows)
    vars_ = variants(cases)
    nodes, edges = dfg(cases)
    layer, back = layers(nodes, edges)
    designed = DESIGNED.get(process, [])
    cyc = [cycle_hours(e) for e in cases.values()]
    touches = [sum(1 for x in e if x["role"] not in NON_HUMAN) for e in cases.values()]
    glue = glue_analysis(cases, categories)
    return {
        "process": process,
        "title": TITLES.get(process, process),
        "cases": len(cases),
        "events": sum(len(e) for e in cases.values()),
        "variants": vars_,
        "median_cycle_h": median(cyc) if cyc else 0.0,
        "p90_cycle_h": _q(cyc, 0.9),
        "touches_per_case": sum(touches) / max(1, len(touches)),
        "conformance": conformance(cases, designed),
        "nodes": [{"activity": a, **n, "category": categories.get(a, "START" if a in (START, END) else "VALUE"),
                   "layer": layer.get(a, 0)} for a, n in nodes.items()],
        "edges": [{"from": a, "to": b, **e, "back": (a, b) in back or a == b} for (a, b), e in edges.items()],
        "glue": glue,
        "projection": projection(cases, categories),
        "rare": rare_variants(vars_, designed, vars_[0] if vars_ else None),
        "confirm_to_plan": confirm_to_plan(cases) if process == "PO_CONFIRMATION" else [],
    }
