"""Buy vs build, settled by evidence: weight each capability by what the team
assumed before shipping the thin app, then by how people actually used it, and
score every candidate platform both ways.

Pure functions:
    capabilities  [{capability_id, domain, name, assumed_weight, feature_key}]
    usage         {feature_key: events observed in the thin-app telemetry}
    vendors       [{vendor_id, domain, name, annual_usd, impl_weeks, notes}]
    fit           {(vendor_id, capability_id): 0..3}
"""
from collections import defaultdict

MAX_FIT = 3.0


def observed_weights(capabilities, usage):
    """Share of each domain's observed usage that went to each capability."""
    totals = defaultdict(float)
    for c in capabilities:
        totals[c["domain"]] += usage.get(c["feature_key"], 0)
    return {c["capability_id"]: (usage.get(c["feature_key"], 0) / totals[c["domain"]] if totals[c["domain"]] else 0.0)
            for c in capabilities}


def normalize(weights):
    s = sum(weights.values())
    return {k: (v / s if s else 0.0) for k, v in weights.items()}


def score(vendor_id, caps, weights, fit):
    """Weighted fit on a 0..1 scale (1 = native support for everything that matters)."""
    return sum(weights[c] * fit.get((vendor_id, c), 0) for c in caps) / MAX_FIT


def ranks(scores):
    order = sorted(scores, key=lambda k: -scores[k])
    return {k: i + 1 for i, k in enumerate(order)}


def evaluate(capabilities, usage, vendors, fit):
    obs = observed_weights(capabilities, usage)
    out = []
    for domain in sorted({c["domain"] for c in capabilities}):
        caps = [c for c in capabilities if c["domain"] == domain]
        ids = [c["capability_id"] for c in caps]
        assumed = normalize({c["capability_id"]: c["assumed_weight"] for c in caps})
        observed = {i: obs[i] for i in ids}
        vs = [v for v in vendors if v["domain"] == domain]
        a_scores = {v["vendor_id"]: score(v["vendor_id"], ids, assumed, fit) for v in vs}
        o_scores = {v["vendor_id"]: score(v["vendor_id"], ids, observed, fit) for v in vs}
        a_rank, o_rank = ranks(a_scores), ranks(o_scores)
        best = min(vs, key=lambda v: (-round(o_scores[v["vendor_id"]], 6), v["annual_usd"]))
        assumed_best = min(vs, key=lambda v: (-round(a_scores[v["vendor_id"]], 6), v["annual_usd"]))
        cap_rows = [{"capability_id": c["capability_id"], "name": c["name"], "description": c.get("description"),
                     "feature_key": c["feature_key"], "usage": usage.get(c["feature_key"], 0),
                     "assumed": assumed[c["capability_id"]], "observed": observed[c["capability_id"]],
                     "delta": observed[c["capability_id"]] - assumed[c["capability_id"]],
                     "fit": {v["vendor_id"]: fit.get((v["vendor_id"], c["capability_id"]), 0) for v in vs}}
                    for c in caps]
        over = max(cap_rows, key=lambda r: r["assumed"] - r["observed"])
        under = max(cap_rows, key=lambda r: r["observed"] - r["assumed"])
        evidence = (f"{over['name']} was assumed {over['assumed']:.0%} of the value; people used it "
                    f"{over['observed']:.1%} of the time. {under['name']} was {under['assumed']:.0%} assumed, "
                    f"{under['observed']:.0%} observed.")
        vendor_rows = []
        for v in vs:
            vid = v["vendor_id"]
            vendor_rows.append({**v, "assumed_score": a_scores[vid], "observed_score": o_scores[vid],
                                "assumed_rank": a_rank[vid], "observed_rank": o_rank[vid],
                                "usd_per_point": (v["annual_usd"] / (o_scores[vid] * 100)) if o_scores[vid] else None})
        vendor_rows.sort(key=lambda r: r["observed_rank"])
        out.append({"domain": domain, "capabilities": cap_rows, "vendors": vendor_rows,
                    "recommended": best["vendor_id"], "assumed_winner": assumed_best["vendor_id"],
                    "flipped": best["vendor_id"] != assumed_best["vendor_id"], "evidence": evidence,
                    "total_usage": sum(r["usage"] for r in cap_rows)})
    return out
