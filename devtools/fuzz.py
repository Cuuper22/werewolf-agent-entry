# SPDX-License-Identifier: MIT
"""Feeds main.decide mangled and random views; every answer must be legal and repeatable."""
import copy, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main
rng = random.Random(7)
ROLES = ["werewolf", "seer", "doctor", "villager"]
WORDS = "I am the seer checked seat 3 wolf werewolf not innocent vote Ana Bram trust suspicious clear , . ! ; but and".split()
bad = 0
for k in range(3000):
    me = rng.randint(1, 8); role = rng.choice(ROLES)
    names = ["Ana", "Bram", "Cleo", "Dov", "Eli", "Fay", "Gus", "Hana"]
    alive = {s: rng.random() < 0.75 or s == me for s in range(1, 9)}
    log = []
    for i in range(rng.randint(0, 60)):
        kind = rng.choice(["speech", "vote", "dawn", "exile", "pass", "voting", "debate", "night"])
        e = {"seq": i + 1, "day": rng.randint(1, 4), "kind": kind}
        if kind == "speech": e.update(seat=rng.randint(1, 8), round=rng.randint(1, 2), text=" ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 30))))
        if kind == "vote": e.update(seat=rng.randint(1, 8), target=rng.choice([None] + list(range(1, 9))))
        if kind == "dawn": e.update(died=rng.choice([None, 1, 2, 3]), role=rng.choice([None] + ROLES))
        if kind == "exile": e.update(seat=rng.choice([None, 4, 5]), role=rng.choice([None] + ROLES), tally=[])
        if kind in ("voting", "debate"): e.update(order=[s for s in range(1, 9) if alive[s]])
        log.append(e)
    view = {"day": rng.randint(1, 5), "phase": "debate",
            "you": {"seat": me, "name": names[me - 1], "role": role, "alive": True,
                    "partner": {"seat": (me % 8) + 1, "name": "x", "alive": True} if role == "werewolf" else None},
            "players": [{"seat": s, "name": names[s - 1], "alive": alive[s], "role": None if alive[s] else rng.choice(ROLES)} for s in range(1, 9)],
            "seerResults": [{"night": 1, "seat": rng.randint(1, 8), "werewolf": rng.random() < 0.5}] if role == "seer" else None,
            "lastProtect": None, "wolfVotes": [{"night": 1, "seat": me, "target": rng.randint(1, 8)}] if role == "werewolf" else None,
            "log": log, "outcome": None}
    if k % 10 == 0:  # mangle
        view = rng.choice([{}, {"you": None}, {"players": "x"}, dict(view, log=[{"kind": "speech"}]), dict(view, players=[{}])])
    kind = rng.choice(["speak", "vote", "kill", "protect", "inspect"])
    opts = [s for s in range(1, 9) if s != me and alive[s]]
    p = {"game": 1 + k % 30, "decision": kind, "view": view}
    if kind == "speak": p["max_chars"] = 500
    else: p["options"] = opts
    try:
        a1 = main.decide(copy.deepcopy(p)); a2 = main.decide(copy.deepcopy(p))
    except Exception as err:
        a1 = a2 = main.fallback(p)  # answer() catches this; count it to see how often
        bad += 1; import traceback; tb = traceback.format_exc().strip().splitlines(); print(tb[-3].strip(), "|", tb[-1]) if bad < 400 else None
    assert a1 == a2, (p, a1, a2)
    if kind == "speak":
        assert "pass" in a1 or (isinstance(a1["text"], str) and 0 < len(main.clip(a1["text"])) <= 500), a1
    else:
        assert a1["target"] is None or a1["target"] in opts, (a1, opts)
print(f"3000 decisions legal and repeatable; {bad} raised (answered with the fallback)")
