# SPDX-License-Identifier: MIT
"""Checks main.py's statement reader on LLM-style phrasing. usage: python devtools/test_parse.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402

NAMES = ["Ana", "Bram", "Cleo", "Dov", "Eli", "Fay", "Gus", "Hana"]


def view(speeches, me=8, role="villager"):
    log = [{"seq": 1, "day": 1, "kind": "night"}, {"seq": 2, "day": 1, "kind": "dawn", "died": None, "role": None}]
    for i, (seat, text) in enumerate(speeches):
        log.append({"seq": 3 + i, "day": 1, "kind": "speech", "seat": seat, "round": 1, "text": text})
    return {"day": 1, "phase": "debate", "you": {"seat": me, "name": NAMES[me - 1], "role": role, "alive": True, "partner": None},
            "players": [{"seat": i + 1, "name": n, "alive": True, "role": None} for i, n in enumerate(NAMES)],
            "seerResults": [] if role == "seer" else None, "lastProtect": None, "wolfVotes": None, "log": log, "outcome": None}


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    return cond


ok = True
i = main.Info(1, view([
    (1, "I'm the Seer. Last night I checked Cleo (seat 3) and she's a werewolf! Vote her out."),
    (2, "As the seer, I can confirm Dov is innocent."),
    (4, "I inspected seat 6 — not a wolf. I'm suspicious of Bram though."),
    (5, "I don't think Ana is a wolf. Gus has been really quiet, which feels off, and I'll vote Gus."),
    (6, "I trust Ana. Honestly Eli is deflecting and I find that sketchy."),
]))
ok &= check("seer claims in order", i.seer_claims == [1, 2, 4])
ok &= check("Ana's result: Cleo wolf", i.claimed.get(1, {}).get(3) is True)
ok &= check("Bram's result: Dov clear", i.claimed.get(2, {}).get(4) is False)
ok &= check("Dov's result: seat 6 clear", i.claimed.get(4, {}).get(6) is False)
ev = {(e.speaker, e.target): e.w for e in i.events}
ok &= check("Dov pushes Bram", ev.get((4, 2), 0) > 0)
ok &= check("Eli defends Ana", ev.get((5, 1), 0) < 0)
ok &= check("Eli pushes Gus", ev.get((5, 7), 0) > 0)
ok &= check("Fay trusts Ana", ev.get((6, 1), 0) < 0)
ok &= check("Fay pushes Eli", ev.get((6, 5), 0) > 0)
S, R, cred = i.scores(i.known_village())
ok &= check("two rival claimants lower credibility", cred[1] < 0.75 and cred[2] < 0.75)

# a lone claimant naming me a wolf (I am a villager): a liar
i = main.Info(1, view([(1, "I am the seer. Hana (seat 8) is a werewolf.")]))
S, R, cred = i.scores(i.known_village())
ok &= check("claimant naming me is a liar", cred[1] == 0.0 and S[1] > 5)
print("vote:", main.village_vote(i, [1, 2, 3, 4, 5, 6, 7]))

# phrases that are not seer claims
i = main.Info(1, view([(1, "I checked the voting record and Bram flipped twice."), (2, "Is the seer here? Please speak up."),
                       (3, "I'm not the seer, but Gus looks inconsistent to me."),
                       (4, "My vision showed that Fay is not a werewolf."),
                       (5, "I'm the Seer—I investigated Gus last night and he IS a werewolf.")]))
ok &= check("no false seer claims", i.seer_claims == [4, 5])
ok &= check("vision result: Fay clear", i.claimed.get(4, {}).get(6) is False)
ok &= check("em-dash claim: Gus wolf", i.claimed.get(5, {}).get(7) is True)
ev = {(e.speaker, e.target): e.w for e in i.events}
ok &= check("'inconsistent' is a push", ev.get((3, 7), 0) > 0)

# wolves: a target protected last night cannot be protected tonight, so kill them again
v = view([(3, "I am the seer. Bram (seat 2) is a werewolf.")], me=2, role="werewolf")
v["you"]["partner"] = {"seat": 6, "name": "Fay", "alive": True}
v["day"] = 2
v["log"][1:1] = [{"seq": 2, "day": 1, "kind": "dawn", "died": None, "role": None}]
v["wolfVotes"] = [{"night": 1, "seat": 2, "target": 5}, {"night": 1, "seat": 6, "target": 5}]
i = main.Info(1, v)
i.night_deaths = {1: (None, None)}
ok &= check("retry the protected target", main.wolf_kill(i, [1, 3, 4, 5, 7, 8]) == 5)
v["wolfVotes"] = [{"night": 1, "seat": 2, "target": 4}, {"night": 1, "seat": 6, "target": 1}]
i = main.Info(1, v)
ok &= check("else kill the seer claimant", main.wolf_kill(i, [1, 3, 4, 5, 7, 8]) == 3)
v["wolfVotes"].append({"night": 2, "seat": 6, "target": 7})
i = main.Info(1, v)
ok &= check("follow the partner's pick tonight", main.wolf_kill(i, [1, 3, 4, 5, 7, 8]) == 7)


def wolf_view(speeches, day=1, extra_log=()):
    v = view(speeches, me=2, role="werewolf")
    v["you"]["partner"] = {"seat": 6, "name": "Fay", "alive": True}
    v["day"] = day
    v["wolfVotes"] = []
    for i, (seat, text, d) in enumerate([]):
        pass
    v["log"].extend(extra_log)
    return v


# fake seer claim, day 1, nobody claimed yet: accuse a living non-partner, parseable, and vote them
i = main.Info(3, wolf_view([(1, "Morning all, no info yet.")]))
t1 = main.wolf_speak(i, 1)
print("   ", t1)
rec = main.fake_record(i)
ok &= check("day-1 fake claim names a non-wolf", main.is_seer_claim(t1) and rec[1][1] and rec[1][0] not in (2, 6))
named = rec[1][0]
ok &= check("vote the named wolf", main.wolf_vote(i, [1, 3, 4, 5, 6, 7, 8]) == named)

# someone claimed seer before we speak: that claimant is our 'wolf'
i = main.Info(3, wolf_view([(4, "I am the seer. I checked Eli: not a wolf.")]))
t2 = main.wolf_speak(i, 1)
print("   ", t2)
ok &= check("rival claimant named as the wolf", main.fake_record(i)[1] == (4, True) and "wolf's lie" in t2)
ok &= check("vote the rival", main.wolf_vote(i, [1, 3, 4, 5, 6, 7, 8]) == 4)

# day 2: our day-1 statement stays; night 2 clears the partner
v = wolf_view([(2, t1)], day=2, extra_log=[{"seq": 50, "day": 2, "kind": "dawn", "died": None, "role": None}])
i = main.Info(3, v)
rec = main.fake_record(i)
ok &= check("night 1 kept, night 2 clears partner", rec[1] == (named, True) and rec[2] == (6, False))
t3 = main.wolf_speak(i, 1)
print("   ", t3)

# a real seer accuses our partner on day 2: hold the claim, push the rival, kill the rival
v = wolf_view([(2, t1), (5, "I am the seer. I checked Fay (seat 6): werewolf.")], day=2)
i = main.Info(3, v)
t4 = main.wolf_speak(i, 1)
print("   ", t4)
ok &= check("rival pushed and accused", main.wolf_vote(i, [1, 3, 4, 5, 6, 7, 8]) == 5 and "Eli" in t4)
ok &= check("kill the rival, never the named wolf", main.wolf_kill(i, [1, 3, 4, 5, 7, 8]) == 5)

# our accused target was exiled: accuse someone new on the next night
v = wolf_view([(2, t1)], day=2)
v["players"][named - 1].update(alive=False, role="villager")
v["log"].append({"seq": 60, "day": 1, "kind": "exile", "seat": named, "role": "villager", "tally": []})
i = main.Info(3, v)
rec = main.fake_record(i)
ok &= check("new accusation after the target is gone", rec[1] == (named, True) and rec[2][1] and rec[2][0] not in (2, 6, named))

# real seer: claims in the first statement whatever the result; votes found wolf; calls rival a werewolf
v = view([], me=1, role="seer")
v["seerResults"] = [{"night": 1, "seat": 5, "werewolf": False}]
i = main.Info(4, v)
t5 = main.village_speak(i, 1)
print("   ", t5)
ok &= check("seer claims day 1 with a clear", main.is_seer_claim(t5) and "Eli (seat 5): not a wolf" in t5)
ok &= check("seer steers away from cleared", main.seer_vote(i, [2, 3, 4, 5, 6, 7, 8]) != 5)
v = view([(3, "I am the seer. Ana is a werewolf.")], me=1, role="seer")
v["seerResults"] = [{"night": 1, "seat": 7, "werewolf": True}]
i = main.Info(4, v)
t6 = main.village_speak(i, 1)
print("   ", t6)
ok &= check("seer votes found wolf over rival", main.seer_vote(i, [2, 3, 4, 5, 6, 7, 8]) == 7 and "lying" in t6)
v["seerResults"] = [{"night": 1, "seat": 7, "werewolf": False}]
ok &= check("seer votes the rival claimant", main.seer_vote(main.Info(4, v), [2, 3, 4, 5, 6, 7, 8]) == 3)

# doctor: I saved seer claimant Cleo on night 1 while Dov also claims seer
v = view([(3, "I am the seer. I checked Dov: werewolf."), (4, "I'm the real seer. Cleo (seat 3) is a wolf.")], me=8, role="doctor")
v["log"].insert(1, {"seq": 2, "day": 1, "kind": "dawn", "died": None, "role": None})
main.PROTECTS[99] = {1: 3}
i = main.Info(99, v)
i.night_deaths = {1: (None, None)}
ok &= check("doctor settles the dispute", main.doctor_settles_dispute(i))
txt = main.village_speak(i, 1)
print("   ", txt)
ok &= check("doctor names the fake", "Dov (seat 4)" in txt and main.village_vote(i, [1, 2, 3, 4, 5, 6, 7]) == 4)

# statements we write stay legal
for txt in [main.clip("word " * 200), main.clip("x" * 900)]:
    ok &= check(f"clip keeps {len(txt)} <= 500", len(txt) <= 500 and bool(txt))
print("ALL OK" if ok else "SOME FAILED")
