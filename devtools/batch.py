# SPDX-License-Identifier: MIT
"""Run run_local.py over several seeds x the 3 practice trials in parallel and print the mean score.

usage: python devtools/batch.py [--seeds 4] [--base 19000]
"""

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def one(args):
    trial, seed, port = args
    out = subprocess.run([sys.executable, os.path.join(HERE, "run_local.py"), "--python", sys.executable, "--trial", str(trial),
                          "--seed", seed, "--port", str(port), "--quiet"], capture_output=True, text=True, timeout=900).stdout
    m = re.search(r"SCORE (\d+)  \(wolf ([\d.]+), village ([\d.]+)\)", out)
    issues = re.search(r"stderr issues: (\d+)", out)
    fin = "None " not in out
    return trial, seed, (int(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None, int(issues.group(1)) if issues else -1, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--base", type=int, default=19000)
    a = ap.parse_args()
    jobs = [(t, f"s{k}", a.base + 10 * (3 * k + t)) for k in range(a.seeds) for t in range(3)]
    res = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for trial, seed, sc, iss, out in ex.map(one, jobs):
            print(f"trial {trial} seed {seed}: {sc} issues {iss}")
            if sc is None:
                print(out[-2000:])
            else:
                res.append(sc)
    if res:
        n = len(res)
        print(f"MEAN over {n}: score {sum(r[0] for r in res) / n:.1f}  wolf {sum(r[1] for r in res) / n:.3f}  village {sum(r[2] for r in res) / n:.3f}")


if __name__ == "__main__":
    main()
