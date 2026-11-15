# ruff: noqa: UP031, SIM115  (exploratory analysis scripts: %-format output, one-shot file reads, immediately-called lambdas)
"""Rolling out-of-sample comparison of PV calibration schemes on SunKnee slots.json."""

import json
import sys

d = json.load(open(sys.argv[1]))["days"]
dates = sorted(d)
DAY = range(14, 34)  # 07:00-17:00 UTC slots


def fc(day, s, k="pv_estimate"):
    return d[day]["fc"].get(str(s), {}).get(k, 0.0)


def act(day, s):
    return d[day]["act"].get(str(s), 0.0)


def history_pairs(i, n):
    """(slot, raw P50, actual, recency weight) for the n days before day i."""
    out = []
    for j in range(max(0, i - n), i):
        w = max(1.0 - 0.1 * (i - j - 1), 0.3)
        for s in DAY:
            p = fc(dates[j], s)
            if p > 0.01:
                out.append((s, p, act(dates[j], s), w))
    return out


def weighted_ratio(pairs):
    num = sum(a * w for _, p, a, w in pairs)
    den = sum(p * w for _, p, a, w in pairs)
    return num / den if den > 0 else 1.0


def ratio_quantile(pairs, q):
    """Energy-weighted quantile of slot ratio actual/P50."""
    rs = sorted(((a / p), p * w) for _, p, a, w in pairs)
    total = sum(x[1] for x in rs)
    acc = 0
    for r, wt in rs:
        acc += wt
        if acc >= q * total:
            return r
    return rs[-1][0] if rs else 1.0


def scheme_predbat(i, n=7):
    """Per-slot ratio of sums over n days (Predbat-like). P10/P90 = Predbat's published calibrated band."""
    pairs = history_pairs(i, n)
    by = {}
    for s in DAY:
        sp = [x for x in pairs if x[0] == s]
        by[s] = min(max(weighted_ratio(sp), 0.2), 4.0) if sp else 1.0
    return lambda s: (
        fc(dates[i], s, "pv_estimate10"),
        fc(dates[i], s) * by[s],
        fc(dates[i], s, "pv_estimate90"),
    )


def scheme_quantile(i, n=14, lo=0.1, hi=0.9):
    """Pooled slot-ratio quantiles: P10/P90 from the 10th/90th percentile ratio, P50 from the middle 50% ratio of sums."""
    pairs = history_pairs(i, n)
    if not pairs:
        return None
    q25, q75 = ratio_quantile(pairs, 0.25), ratio_quantile(pairs, 0.75)
    mid = [x for x in pairs if q25 <= x[2] / x[1] <= q75]
    r50 = weighted_ratio(mid) if mid else weighted_ratio(pairs)
    r10, r90 = ratio_quantile(pairs, lo), ratio_quantile(pairs, hi)
    return lambda s: (
        fc(dates[i], s) * r10,
        fc(dates[i], s) * r50,
        fc(dates[i], s) * r90,
    )


def scheme_quantile_by_slot(i, n=14):
    """As quantile, but the central ratio stays per time-of-day (keeps shading pattern); band from pooled quantiles relative to the pooled median."""
    pairs = history_pairs(i, n)
    if not pairs:
        return None
    med = ratio_quantile(pairs, 0.5)
    r10, r90 = ratio_quantile(pairs, 0.1) / med, ratio_quantile(pairs, 0.9) / med
    base = scheme_predbat(i, 7)
    return lambda s: (base(s)[1] * r10, base(s)[1], base(s)[1] * r90)


def evaluate(name, scheme, start=14):
    e_abs = e_bias = e_act = 0
    over90 = under10 = slots = 0
    e90 = 0
    for i in range(start, len(dates)):
        f = scheme(i)
        if f is None:
            continue
        dp = da = 0
        for s in DAY:
            p10, p50, p90 = f(s)
            a = act(dates[i], s)
            if p50 <= 0.01 and a <= 0.01:
                continue
            slots += 1
            over90 += a > p90
            under10 += a < p10
            e90 += max(0, a - p90)
            dp += p50
            da += a
        e_abs += abs(dp - da)
        e_bias += dp - da
        e_act += da
    print(
        "%-26s daily P50 error %4.0f%%  bias %+4.0f%%  actual>P90 %4.1f%% of slots (energy above %4.1f%%)  actual<P10 %4.1f%%"
        % (
            name,
            100 * e_abs / e_act,
            100 * e_bias / e_act,
            100 * over90 / slots,
            100 * e90 / e_act,
            100 * under10 / slots,
        )
    )


print(
    "Out of sample, days %s..%s (each day calibrated from earlier days only); target actual>P90 ~10%%, actual<P10 ~10%%"
    % (dates[14], dates[-1])
)
evaluate(
    "raw Solcast P50 / pub band",
    lambda i: (
        lambda s: (
            fc(dates[i], s, "pv_estimate10"),
            fc(dates[i], s),
            fc(dates[i], s, "pv_estimate90"),
        )
    ),
)
evaluate(
    "Predbat published (CL)",
    lambda i: (
        lambda s: (
            fc(dates[i], s, "pv_estimate10"),
            fc(dates[i], s, "pv_estimateCL"),
            fc(dates[i], s, "pv_estimate90"),
        )
    ),
)
evaluate("per-slot 7d (Predbat-like)", scheme_predbat)
evaluate("pooled quantiles 14d", scheme_quantile)
evaluate("pooled quantiles 28d", lambda i: scheme_quantile(i, 28))
evaluate("per-slot P50 + quantile band", scheme_quantile_by_slot)
