"""Statistical process control: p-charts for attribute data, capability (Cp/Cpk/Ppk)
and individuals (I-MR) limits for variables data. Pure functions, no database.

Conventions follow the standard shop-floor definitions:
  p-chart   center p-bar = total defectives / total inspected; per-sample limits
            p-bar +/- 3 * sqrt(p-bar * (1 - p-bar) / n_i), clamped to [0, 1]
  I-MR      sigma_within = MR-bar / d2 (d2 = 1.128 for n = 2); limits x-bar +/- 2.66 * MR-bar
  Cpk       min(USL - mean, mean - LSL) / (3 * sigma_within) over the sides that exist
  Ppk       same with the overall (sample) standard deviation
"""
import math

D2 = 1.128
E2 = 2.66


def pchart(samples, sigma=3.0, revise=True):
    """samples: [{"key": ..., "n": int, "fails": int, ...}]. Returns center, per-point limits and flags.

    With revise=True, points beyond the first-pass limits are treated as assignable causes:
    they are removed, the center line is recomputed once, and every point is re-judged against
    the revised limits (the usual practice when setting control limits from history).
    """
    pts = [dict(s) for s in samples if s.get("n")]
    for p in pts:
        p["p"] = p["fails"] / p["n"]

    def judge(pbar):
        for p in pts:
            half = sigma * math.sqrt(pbar * (1 - pbar) / p["n"]) if 0 < pbar < 1 else 0.0
            p["ucl"] = min(1.0, pbar + half)
            p["lcl"] = max(0.0, pbar - half)
            p["ooc"] = p["p"] > p["ucl"] + 1e-12 or p["p"] < p["lcl"] - 1e-12
        return [p for p in pts if p["ooc"]]

    total_n = sum(p["n"] for p in pts)
    pbar_all = sum(p["fails"] for p in pts) / total_n if total_n else 0.0
    flagged = judge(pbar_all)
    pbar = pbar_all
    revised = False
    if revise and flagged:
        keep = [p for p in pts if not p["ooc"]]
        kn = sum(p["n"] for p in keep)
        if kn:
            pbar = sum(p["fails"] for p in keep) / kn
            revised = True
            flagged = judge(pbar)
    return {"pbar": pbar, "pbar_initial": pbar_all, "revised": revised, "points": pts,
            "ooc": [p["key"] for p in flagged], "total_n": total_n,
            "total_fails": sum(p["fails"] for p in pts)}


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs):
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def mr_bar(xs):
    if len(xs) < 2:
        return float("nan")
    return sum(abs(xs[i] - xs[i - 1]) for i in range(1, len(xs))) / (len(xs) - 1)


def capability(values, lsl=None, usl=None):
    """values in time order. Returns n, mean, sigma (within/overall), Cp, Cpk, Pp, Ppk, % out of spec."""
    xs = [float(v) for v in values if v is not None]
    n = len(xs)
    out = {"n": n, "mean": None, "sd_within": None, "sd_overall": None, "cp": None, "cpk": None,
           "pp": None, "ppk": None, "pct_out": None, "out": 0, "min": None, "max": None}
    if n < 2:
        return out
    m, so, mr = mean(xs), stdev(xs), mr_bar(xs)
    sw = mr / D2 if mr and mr > 0 else so
    out.update(mean=m, sd_within=sw, sd_overall=so, mr_bar=mr, min=min(xs), max=max(xs))

    def index(sd):
        sides = []
        if usl is not None:
            sides.append((usl - m) / (3 * sd))
        if lsl is not None:
            sides.append((m - lsl) / (3 * sd))
        return min(sides) if sides and sd > 0 else None

    out["cpk"] = index(sw)
    out["ppk"] = index(so)
    if lsl is not None and usl is not None:
        out["cp"] = (usl - lsl) / (6 * sw) if sw > 0 else None
        out["pp"] = (usl - lsl) / (6 * so) if so > 0 else None
    bad = sum(1 for x in xs if (lsl is not None and x < lsl) or (usl is not None and x > usl))
    out["out"] = bad
    out["pct_out"] = bad / n
    return out


def capability_status(cpk):
    """Shop-floor reading of Cpk: >= 1.33 capable, 1.0-1.33 marginal, < 1.0 not capable."""
    if cpk is None:
        return "NONE"
    if cpk >= 1.33:
        return "CAPABLE"
    if cpk >= 1.0:
        return "MARGINAL"
    return "NOT_CAPABLE"


def imr_limits(values):
    xs = [float(v) for v in values if v is not None]
    if len(xs) < 2:
        return None
    m, mr = mean(xs), mr_bar(xs)
    return {"center": m, "ucl": m + E2 * mr, "lcl": m - E2 * mr, "mr_bar": mr}


def histogram(values, lo, hi, bins=24):
    xs = [float(v) for v in values if v is not None]
    if not xs or hi <= lo:
        return []
    width = (hi - lo) / bins
    counts = [0] * bins
    for x in xs:
        i = int((x - lo) / width)
        counts[min(max(i, 0), bins - 1)] += 1
    return [{"x0": lo + i * width, "x1": lo + (i + 1) * width, "count": c} for i, c in enumerate(counts)]
