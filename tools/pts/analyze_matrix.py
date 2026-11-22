"""Phase 1 analysis of a mutation -> failing-test matrix (offline research).

    python3 analyze_matrix.py <mutations.json> <tests.json> <out_prefix> <gt_dir> [<gt_dir> ...]

Writes <out_prefix>_matrix.json (sparse: mutation id -> failing test numbers
plus metadata) and <out_prefix>_matrix.csv, and prints coverage statistics,
a greedy runtime-weighted cover preview, and leave-one-group-out checks under
the RWX miss rule (a commit is missed if ANY failing test is not run).
"""
import collections, csv, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from analyzer.scheduler import schedule_tests, duration_milliseconds as dm

mut_path, tests_path, out, *gts = sys.argv[1:]
muts = {m["id"]: m for m in json.load(open(mut_path))["mutants"]}
tests = json.load(open(tests_path))["tests"]
num = {t["id"]: t["number"] for t in tests}
ms = {t["number"]: dm(t["seconds"]) for t in tests}
N = len(tests)
rows = {}
for g in gts:
    for f in os.listdir(g):
        if f.endswith(".json") and not f.startswith("_") and f[:-5] in muts:
            r = json.load(open(os.path.join(g, f)))
            rows[r["id"]] = {"failing": sorted(num[t] for t in r["failing"] if t in num),
                             "collect_errors": r["collect_errors"], "seconds": r["seconds"]}
def cost(sel):
    if not sel: return 0
    w = schedule_tests(set(sel), tests)
    return (max(sum(ms[x] for x in q) for q in w) + 2000 * len(w)) / 1000
FULL = cost(set(ms))
matrix = {i: {**{k: muts[i][k] for k in ("category", "location", "path", "qualname", "line", "node")}, **rows[i]}
          for i in sorted(rows)}
json.dump(matrix, open(out + "_matrix.json", "w"), separators=(",", ":"))
with open(out + "_matrix.csv", "w", newline="") as h:
    w = csv.writer(h); w.writerow(["mutation", "category", "location", "path", "line", "n_failing", "failing"])
    for i, r in matrix.items():
        w.writerow([i, r["category"], r["location"], r["path"], r["line"], len(r["failing"]), " ".join(map(str, r["failing"]))])
BROAD = 0.5 * N
lab = list(matrix)
surv = [i for i in lab if not matrix[i]["failing"]]
broad = [i for i in lab if len(matrix[i]["failing"]) >= BROAD]
killed = [i for i in lab if matrix[i]["failing"]]
narrow = [i for i in killed if len(matrix[i]["failing"]) < BROAD]
print(f"generated={len(muts)} labelled={len(lab)} survived(0 failing)={len(surv)} ({len(surv)/len(lab):.0%}) "
      f"broad(>=50% suite, import-level)={len(broad)} narrow-killed={len(narrow)}")
secs = [matrix[i]['seconds'] for i in lab]
print(f"label wall seconds per mutation: median={sorted(secs)[len(secs)//2]} mean={sum(secs)/len(secs):.1f}")
for key in ("category", "location"):
    c = collections.defaultdict(lambda: [0, 0, 0])
    for i in lab:
        c[matrix[i][key]][0] += 1; c[matrix[i][key]][1] += bool(matrix[i]["failing"]); c[matrix[i][key]][2] += len(matrix[i]["failing"]) >= BROAD
    print(key, {k: f"{v[1]}/{v[0]} killed, {v[2]} broad" for k, v in sorted(c.items())})
sizes = sorted(len(matrix[i]["failing"]) for i in killed)
print("failing-tests per killed mutation: min/median/max", sizes[0], sizes[len(sizes)//2], sizes[-1])
detect = collections.Counter(t for i in killed for t in matrix[i]["failing"])
print(f"tests detecting >=1 mutation: {len(detect)} of {N}; detecting >=1 narrow mutation: "
      f"{len({t for i in narrow for t in matrix[i]['failing']})}")
# oracle per-mutation cost (exactly failing tests) and failing-files cost
byfile = collections.defaultdict(set)
for t in tests: byfile[t["file"]].add(t["number"])
fileof = {t["number"]: t["file"] for t in tests}
oracle = sum(cost(matrix[i]["failing"]) for i in killed)
ofiles = sum(cost(set().union(*(byfile[fileof[t]] for t in matrix[i]["failing"]))) for i in killed)
print(f"per-mutation cost over killed mutations: oracle-tests={oracle:.1f}s files={ofiles:.1f}s full={FULL*len(killed):.1f}s "
      f"(full per commit {FULL}s)")
def greedy_detect_any(ids):
    left = {i for i in ids if matrix[i]["failing"]}; chosen = set()
    while left:
        best = max({t for i in left for t in matrix[i]["failing"]},
                   key=lambda t: (sum(t in matrix[i]["failing"] for i in left) / max(ms[t], 1), -ms[t]))
        chosen.add(best); left = {i for i in left if best not in matrix[i]["failing"]}
    return chosen
def rwx_miss(port, ids):  # official rule: every failing test must be run
    return [i for i in ids if matrix[i]["failing"] and not set(matrix[i]["failing"]) <= port]
def any_miss(port, ids):
    return [i for i in ids if matrix[i]["failing"] and not set(matrix[i]["failing"]) & port]
P = greedy_detect_any(lab)
print(f"PREVIEW greedy detect-any portfolio: {len(P)} tests, RWX cost {cost(P)}s vs full {FULL}s; "
      f"in-sample commits missed under official all-failing rule: {len(rwx_miss(P, lab))}/{len(killed)}")
U = {t for i in lab for t in matrix[i]["failing"]}
print(f"union of all failing tests (needed for zero in-sample misses): {len(U)} tests, cost {cost(U)}s")
Un = {t for i in narrow for t in matrix[i]["failing"]}
print(f"union over narrow-killed only: {len(Un)} tests, cost {cost(Un)}s")
for key in ("category", "path", "location"):
    groups = collections.defaultdict(list)
    for i in lab: groups[matrix[i][key]].append(i)
    tot = mr = ma = cu = 0; costs = []
    for g, ids in groups.items():
        train = [i for i in lab if matrix[i][key] != g]
        port = {t for i in train for t in matrix[i]["failing"]}
        held = [i for i in ids if matrix[i]["failing"]]
        tot += len(held); mr += len(rwx_miss(port, held)); costs.append(cost(port))
        portg = greedy_detect_any(train); ma += len(any_miss(portg, held)); cu += len(rwx_miss(portg, held))
    print(f"leave-one-{key}-out ({len(groups)} groups, {tot} held-out killed): union-portfolio RWX misses {mr} "
          f"({mr/max(tot,1):.0%}), mean portfolio cost {sum(costs)/len(costs):.1f}s; greedy detect-any portfolio: "
          f"detect-any misses {ma} ({ma/max(tot,1):.0%}), RWX misses {cu} ({cu/max(tot,1):.0%})")
