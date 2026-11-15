# ruff: noqa: UP031, SIM115  (exploratory analysis scripts: %-format output, one-shot file reads, immediately-called lambdas)
import json
import sys

TH = float(sys.argv[2])
d = json.load(open(sys.argv[1]))["days"]
days = [(k, v["fc"], v["act"]) for k, v in sorted(d.items()) if v["fc"]]
B = [("pv_estimate10", 0.10), ("pv_estimate", 0.50), ("pv_estimate90", 0.90)]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def ratios(ds, key):
    r = []
    for k, f, a in ds:
        for i in range(48):
            p = f.get(str(i), {}).get(key, 0)
            if f.get(str(i), {}).get("pv_estimate90", 0) < TH or p <= 0:
                continue
            r.append(a.get(str(i), 0) / p)
    return r


print("whole period, %d days" % len(days))
for key, p in B:
    r = ratios(days, key)
    print("  %-14s scale %.2f  (n=%d)" % (key, q(r, p), len(r)))
print("trailing 14 days, every 7 days:   k10   k50   k90   k90/k50  k10/k50")
for j in range(14, len(days) + 1, 7):
    w = days[j - 14 : j]
    k = [q(ratios(w, key), p) for key, p in B]
    print(
        "  to %s               %.2f  %.2f  %.2f   %.2f     %.2f"
        % (w[-1][0], k[0], k[1], k[2], k[2] / k[1], k[0] / k[1])
    )
# out of sample: scale from previous 14d, score next 7d
hit = {key: [0, 0] for key, _ in B}
for j in range(14, len(days), 7):
    w = days[j - 14 : j]
    nxt = days[j : j + 7]
    for key, p in B:
        k = q(ratios(w, key), p)
        for kk, f, a in nxt:
            for i in range(48):
                pv = f.get(str(i), {}).get(key, 0)
                if f.get(str(i), {}).get("pv_estimate90", 0) < TH or pv <= 0:
                    continue
                hit[key][0] += a.get(str(i), 0) > k * pv
                hit[key][1] += 1
print(
    "out of sample (14d scale, next 7d): fraction of daylight slots actual > scaled band (target 90/50/10%)"
)
for key, _ in B:
    print("  %-14s %.0f%%" % (key, 100 * hit[key][0] / hit[key][1]))
