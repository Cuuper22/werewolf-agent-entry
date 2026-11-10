# SPDX-License-Identifier: MIT
"""A scripted stand-in for the house LLM lane (OpenAI chat-completions shape), for local testing only.

It reads the exact prompt the env builds for a house seat and answers a JSON reply with simple,
somewhat credulous heuristics: it believes a lone seer claim, follows bandwagons, and wolves
counter-claim now and then. Randomness is seeded from the prompt text, so runs are reproducible.
"""

import hashlib
import json
import random
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RX_ME = re.compile(r"You are (.+?) \(seat (\d)\) in game (\d+)\. Your role is (a werewolf|the seer|the doctor|a villager)")
RX_PLAYER = re.compile(r"^\[moderator\] seat (\d) \((.+?)\): (alive|dead, was a (\w+))", re.M)
RX_PARTNER = re.compile(r"Your partner is seat (\d)")
RX_INSPECT = re.compile(r"you inspected seat (\d) \(.+?\): (a werewolf|not a werewolf)")
RX_PROTECT = re.compile(r"Night (\d+): you protected seat (\d)")
RX_WOLFVOTE = re.compile(r"Night (\d+): seat (\d) \(.+?\) named seat (\d)")
RX_SPEECH = re.compile(r'^seat (\d) \((.+?)\) said: (".*")$', re.M)
RX_VOTE = re.compile(r"seat (\d) \(.+?\) voted to exile seat (\d)")
RX_DAYHDR = re.compile(r"\[moderator\] Day (\d+) (?:debate|voting|dawns)")
RX_SEATREF = re.compile(r"seat (\d)", re.I)
ROLE = {"a werewolf": "werewolf", "the seer": "seer", "the doctor": "doctor", "a villager": "villager"}


def parse(system, user):
    m = RX_ME.search(system)
    me, role = int(m.group(2)), ROLE[m.group(4)]
    players, names = {}, {}
    for pm in RX_PLAYER.finditer(user):
        s = int(pm.group(1))
        names[s] = pm.group(2)
        players[s] = pm.group(4) if pm.group(3) != "alive" else None  # None = alive
    alive = [s for s, r in players.items() if r is None]
    partner = int(RX_PARTNER.search(user).group(1)) if RX_PARTNER.search(user) else None
    inspected = {int(a): b == "a werewolf" for a, b in RX_INSPECT.findall(user)}
    protects = [(int(n), int(s)) for n, s in RX_PROTECT.findall(user)]
    wolfvotes = [(int(n), int(a), int(b)) for n, a, b in RX_WOLFVOTE.findall(user)]
    decision = user.split("YOUR DECISION", 1)[1]
    log = user.split("GAME LOG", 1)[1].split("YOUR DECISION", 1)[0]
    speeches, votes_today = [], {}
    day = 1
    for line in log.splitlines():
        dm = RX_DAYHDR.search(line)
        if dm:
            d = int(dm.group(1))
            if d != day:
                day = d
                votes_today = {}
        sm = RX_SPEECH.match(line)
        if sm:
            try:
                txt = json.loads(sm.group(3))
            except Exception:
                txt = sm.group(3)
            speeches.append((day, int(sm.group(1)), txt))
        vm = RX_VOTE.search(line)
        if vm:
            votes_today[int(vm.group(2))] = votes_today.get(int(vm.group(2)), 0) + 1
    return dict(me=me, role=role, names=names, players=players, alive=alive, partner=partner,
                inspected=inspected, protects=protects, wolfvotes=wolfvotes, decision=decision,
                speeches=speeches, votes_today=votes_today, day=day)


def refs(g, text):
    """Seats a statement points at: 'seat N' or a player's name."""
    out = [int(x) for x in RX_SEATREF.findall(text)]
    for s, n in g["names"].items():
        if re.search(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])", text):
            out.append(s)
    return out


def read_claims(g):
    """seer claimants in order, their wolf calls; accusation counts."""
    claim_order, calls, clears, acc = [], {}, {}, {}
    for day, s, txt in g["speeches"]:
        low = txt.lower()
        if re.search(r"\b(i am|i'm) (the |the real )?seer\b", low):
            if s not in claim_order:
                claim_order.append(s)
        for sent in re.split(r"(?<=[.!?;])\s+", txt):
            sl = sent.lower()
            rs = [r for r in refs(g, sent) if r != s]
            if not rs:
                continue
            t = rs[0]
            if s in claim_order and ("werewolf" in sl or "wolf" in sl) and "not" not in sl:
                calls.setdefault(s, set()).add(t)
            elif s in claim_order and ("not a wolf" in sl or "not a werewolf" in sl or "cleared" in sl or "clean" in sl):
                clears.setdefault(s, set()).add(t)
            elif any(k in sl for k in ("suspicious", "vote", "wolf", "exile", "lying", "suspect")) and "not" not in sl:
                acc[t] = acc.get(t, 0) + (1 + (day == g["day"]))
    return claim_order, calls, clears, acc


def options_of(g):
    return [int(x) for x in re.findall(r"seat (\d) \(", g["decision"])]


def reply_for(system, user):
    g = parse(system, user)
    rng = random.Random(hashlib.sha256((system + user).encode()).hexdigest())
    me, role = g["me"], g["role"]
    wolves = {me, g["partner"]} if role == "werewolf" else set()
    claim_order, calls, clears, acc = read_claims(g)
    dead_roles = {s: r for s, r in g["players"].items() if r is not None}
    # credulity: believe the first live claimant unless a revealed fact contradicts them
    believed = None
    for c in claim_order:
        if dead_roles.get(c) not in (None, "seer"):
            continue
        bad = any(dead_roles.get(t) not in (None, "werewolf") for t in calls.get(c, ()))
        bad = bad or (role != "werewolf" and me in calls.get(c, ()))
        bad = bad or (role == "seer" and c != me)
        if not bad:
            believed = c
            break
    susp = {s: acc.get(s, 0) + rng.random() * 1.5 for s in g["alive"] if s != me}
    if believed is not None and role != "werewolf":
        for t in calls.get(believed, ()):
            if t in susp:
                susp[t] += 10
        for t in clears.get(believed, ()):
            if t in susp:
                susp[t] -= 5
        if believed in susp:
            susp[believed] -= 6
    for c in claim_order:
        if c != believed and c in susp and role != "werewolf":
            susp[c] += 4
    if role == "seer":
        for t, w in g["inspected"].items():
            if t in susp:
                susp[t] += 50 if w else -50
    for t, n in g["votes_today"].items():
        if t in susp:
            susp[t] += 0.8 * n
    dec = g["decision"]
    opts = options_of(g)

    if "your turn to speak" in dec:
        if role == "seer" and (any(g["inspected"].values()) or me in acc and acc[me] >= 3):
            res = "; ".join(f"seat {t} ({g['names'][t]}) is {'a werewolf' if w else 'not a wolf'}" for t, w in g["inspected"].items())
            return {"say": f"I am the seer. My checks: {res}. Vote out the wolf.", "thought": "claim"}
        if role == "werewolf":
            hit = [c for c in claim_order if c not in wolves and (me in calls.get(c, ()) or g["partner"] in calls.get(c, ()))]
            if hit and rng.random() < 0.45 and dead_roles.get(hit[0]) is None:
                c = hit[0]
                return {"say": f"I am the seer, the real one. I checked seat {c} ({g['names'][c]}) and they are a werewolf.", "thought": "counter"}
            nonw = [s for s in susp if s not in wolves]
            if not nonw:
                return {"say": None, "thought": "x"}
            t = max(nonw, key=lambda s: susp[s])
            return {"say": f"I find seat {t} ({g['names'][t]}) suspicious; their story doesn't add up.", "thought": "push"}
        if rng.random() < 0.15:
            return {"say": "I don't have much yet; I'll listen.", "thought": "quiet"}
        if not susp:
            return {"say": None, "thought": "x"}
        t = max(susp, key=lambda s: susp[s])
        return {"say": f"I think seat {t} ({g['names'][t]}) is suspicious and I'll vote for them.", "thought": "read"}

    if not opts:
        return {"target": None, "thought": "none"}
    if "Name tonight's victim" in dec:
        n = max([v[0] for v in g["wolfvotes"]] + [0])
        tonight = [v for v in g["wolfvotes"] if v[0] == n and v[1] != me]
        if tonight and tonight[0][2] in opts and rng.random() < 0.9:
            return {"target": tonight[0][2], "thought": "follow"}
        claimed = [c for c in claim_order if c in opts]
        if claimed:
            return {"target": claimed[0], "thought": "seer"}
        return {"target": rng.choice(opts), "thought": "kill"}
    if "Protect one of" in dec:
        if believed is not None and believed in opts:
            return {"target": believed, "thought": "seer"}
        return {"target": rng.choice(opts), "thought": "guess"}
    if "Inspect one of" in dec:
        fresh = [o for o in opts if o not in g["inspected"]] or opts
        return {"target": rng.choice(fresh), "thought": "look"}
    # vote
    cand = [o for o in opts if o in susp]
    if role == "werewolf":
        cand = [o for o in cand if o not in wolves] or cand
    if not cand:
        return {"target": None, "thought": "abstain"}
    if g["votes_today"] and rng.random() < 0.5:
        lead = max(g["votes_today"], key=lambda s: g["votes_today"][s])
        if lead in cand:
            return {"target": lead, "thought": "bandwagon"}
    return {"target": max(cand, key=lambda s: susp[s]), "thought": "vote"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        msgs = body.get("messages") or []
        try:
            out = reply_for(msgs[0]["content"], msgs[1]["content"])
            text = json.dumps(out)
        except Exception as err:  # make the bug visible but keep the game going
            print("fake house error:", repr(err), flush=True)
            text = '{"target": null, "say": null, "thought": "error"}'
        data = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    request_queue_size = 256  # the env fires many house calls at once; the default backlog of 5 drops some
    daemon_threads = True


def serve(port):
    srv = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
