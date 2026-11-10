# SPDX-License-Identifier: MIT
"""Run main.py against the kit's real env (program/env/server.mjs) with the scripted fake house lane.

usage: python devtools/run_local.py [--kit PATH] [--trial 0] [--port 18080] [--seed S]

Prints the per-role results and the trial score computed as RULES.txt defines it, plus a digest
of every game's final view (equal digests on two runs = the same games, action for action).
"""

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fake_house  # noqa: E402


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", default=os.path.normpath(os.path.join(ROOT, "..", "..", "werewolf-agent-01-kit")))
    ap.add_argument("--trial", type=int, default=0)
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--seed", default="local-seed")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--dump", type=int, nargs="*", default=[], help="print these games' logs")
    a = ap.parse_args()

    house_port = a.port + 1
    house = fake_house.serve(house_port)
    state = os.path.join(HERE, ".state", f"t{a.trial}-{a.port}")
    os.makedirs(state, exist_ok=True)
    env = dict(os.environ,
               MAKEFUN_STATE_DIR=state,
               MAKEFUN_ENV_DATA_DIR=os.path.join(a.kit, "practice", f"trial-{a.trial}", "input", "env"),
               MAKEFUN_ENV_PORT=str(a.port),
               MAKEFUN_HOUSE_LLM_URL=f"http://127.0.0.1:{house_port}",
               MAKEFUN_HOUSE_TOKEN="local")
    ready = os.path.join(state, "ready")
    if os.path.exists(ready):
        os.remove(ready)
    srv = subprocess.Popen(["node", os.path.join(a.kit, "program", "env", "server.mjs")], env=env,
                           stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if os.path.exists(ready):
                break
            time.sleep(0.1)
        url = f"http://127.0.0.1:{a.port}"
        t0 = time.time()
        bot_env = dict(os.environ, MAKEFUN_ENV_URL=url, MAKEFUN_TRIAL_SEED=a.seed,
                       MAKEFUN_PROMPT_FILE=os.path.join(a.kit, "practice", f"trial-{a.trial}", "input", "prompt.txt"))
        bot = subprocess.run([a.python, "-P", "-s", os.path.join(ROOT, "main.py")], env=bot_env,
                             stderr=subprocess.PIPE, text=True, timeout=1700)
        took = time.time() - t0
        errs = [l for l in bot.stderr.splitlines() if "error" in l.lower() or "refus" in l.lower() or "answered 4" in l]
        wins = collections.Counter()
        plays = collections.Counter()
        digest = hashlib.sha256()
        days = []
        for n in range(1, 31):
            v = post(url + "/game", {"game": n})
            digest.update(json.dumps(v, sort_keys=True).encode())
            role = v["you"]["role"]
            team = "werewolves" if role == "werewolf" else "village"
            out = v.get("outcome") or {}
            plays[role] += 1
            won = out.get("winner") == team
            wins[role] += won
            days.append(out.get("day"))
            if n in a.dump:
                names = {p["seat"]: p["name"] for p in v["players"]}
                print(f"===== game {n}: me seat {v['you']['seat']} {role}; roles:",
                      {p: r for p, r in enumerate(json.loads(open(os.path.join(a.kit, "practice", f"trial-{a.trial}", "input", "env", "table.json")).read())["games"][n - 1]["roles"], 1)})
                for e in v["log"]:
                    k = e["kind"]
                    if k == "speech":
                        print(f"  d{e['day']} {'*' if e['seat'] == v['you']['seat'] else ' '}{e['seat']} {names[e['seat']]}: {e['text']}")
                    elif k == "vote":
                        print(f"  d{e['day']}  vote {e['seat']} -> {e['target']}")
                    elif k in ("dawn", "exile", "end"):
                        print(f"  d{e['day']}  {k}: {({x: y for x, y in e.items() if x not in ('seq', 'day', 'kind')})}")
            if not a.quiet:
                print(f"game {n:2d} seat {v['you']['seat']} {role:8s} -> {out.get('winner')!s:10s} {out.get('reason')} day {out.get('day')}{'  WIN' if won else ''}")
        wolf = wins["werewolf"] / 15
        vil = (wins["seer"] + wins["doctor"] + wins["villager"]) / 15
        print(f"exit {bot.returncode} in {took:.1f}s; stderr issues: {len(errs)}")
        for l in errs[:10]:
            print("   ", l)
        print("wins:", {r: f"{wins[r]}/{plays[r]}" for r in ("werewolf", "seer", "doctor", "villager")})
        print(f"SCORE {round(1000 * (wolf + vil) / 2)}  (wolf {wolf:.3f}, village {vil:.3f}); digest {digest.hexdigest()[:16]}")
    finally:
        srv.kill()
        house.shutdown()


if __name__ == "__main__":
    main()
