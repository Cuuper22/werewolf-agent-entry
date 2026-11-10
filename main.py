#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A rule-based Werewolf entry: no model calls, standard library only, deterministic.

Protocol: POST /observe {} lists every decision waiting on us; each is answered with POST /act.
Every answer is a function of the game's view (plus the trial seed, used only to vary phrasing),
and of this process's record of its own earlier answers in that game, so a replay of the trial
makes exactly the same requests.
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENV_URL = (os.environ.get("MAKEFUN_ENV_URL") or "http://store:8080").rstrip("/")
SEED = os.environ.get("MAKEFUN_TRIAL_SEED") or "0"
MAX_CHARS = 500
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Per-game memory of our own night actions that the view does not keep (the doctor sees only its
# latest protection). Filled only from what we sent, so it is as deterministic as the views.
PROTECTS = {}


def log(msg):
    try:
        sys.stderr.write(str(msg) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def http_post(path, body, timeout=60.0):
    """POST one JSON object; returns (status, parsed body or None). status None on a transport error."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(ENV_URL + path, data=data, method="POST",
                                 headers={"content-type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as err:
        status = err.code
        try:
            raw = err.read()
        except Exception:
            raw = b""
    except Exception as err:  # connection refused, timeout, reset ...
        log(f"POST {path} failed: {err!r}")
        return None, None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        obj = None
    return status, obj


def h(*parts):
    """A deterministic 64-bit hash of the trial seed and the parts."""
    text = SEED + "|" + "|".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def choose(options, *key):
    return options[h(*key) % len(options)]


def clip(text, limit=MAX_CHARS):
    """Fold to one line and keep it within the limit, cutting at a sentence end when possible."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut >= limit // 2:
        return head[:cut + 1].strip()
    sp = head.rfind(" ", 0, limit - 3)
    return (head[:sp] if sp > 0 else head[:limit - 3]).rstrip(" ,;") + "..."


# ---------------------------------------------------------------- reading statements

RX_SEAT = re.compile(r"\bseat\s*#?\s*([1-8])\b", re.I)
RX_SEER_CLAIM = re.compile(
    r"\b(?:i am|i'm|im)\s+(?:actually\s+|really\s+|indeed\s+|also\s+)?(?:the\s+|a\s+)?(?:real\s+|true\s+|actual\s+|village\s+)?seer\b"
    r"|\bas the seer\b|(?:^|[.!?]\s+)seer here\b|\bclaim(?:ing)?\s+(?:the\s+)?seer\b"
    r"|\bmy (?:inspections?|visions?)\b|\bmy (?:checks?|results?) (?:on|of|for|show|shows|showed|say|says|came)\b", re.I)
# "I checked Bram" / "I inspected seat 3" (a name or seat must follow, so "I checked the votes" is no claim)
RX_CHECKED = re.compile(r"\b[Ii]\s+(?:checked|inspected|investigated|scanned)\s+(?:on\s+)?(?:[Ss]eat\s*#?\s*[1-8]|[A-Z][a-z]+)")
RX_DOC_CLAIM = re.compile(
    r"\b(?:i am|i'm|im)\s+(?:actually\s+|really\s+)?(?:the\s+|a\s+)?doctor\b|\bas the doctor\b|\bdoctor here\b"
    r"|\bclaim(?:ing)?\s+(?:the\s+)?doctor\b|\bi\s+(?:protected|saved|healed)\b", re.I)
RX_WOLF = re.compile(r"\b(?:were)?wol(?:f|ves)\b|\bscum\b", re.I)
RX_CLEAR = re.compile(
    r"\bnot\s+(?:an?\s+)?(?:were)?wol(?:f|ves)\b|\bisn't\s+(?:an?\s+)?(?:were)?wolf\b|\binnocent\b|\bclean\b"
    r"|\bclear(?:ed)?\b|\bis\s+(?:a\s+)?villager\b|\bconfirmed\s+(?:good|village|villager|town)\b|\bgood guy\b", re.I)
RX_HEDGE = re.compile(r"\b(?:might|maybe|could|possibly|probably|perhaps|think|feel|if|wonder|whether|seems?|likely|or)\b", re.I)
RX_SUS = re.compile(
    r"suspicious|\bsus\b|suspect|sketchy|shady|\blying\b|\bliar\b|deflect|\bodd\b|weird|fishy|evasive|\bvot(?:e|ing)\b"
    r"|\bexile|eliminat|lynch|push(?:ing)? (?:on|for)|\b(?:were)?wol(?:f|ves)\b|\bscum\b|dodg|\bfake\b|\bfalse(?:ly)?\b"
    r"|\bweak\b|inconsistent|contradict|flip-?flop|\bshift(?:ed|ing)? (?:their|his|her) |\bbussing\b|\bred flag", re.I)
RX_TRUST = re.compile(r"\btrust|\binnocent\b|\bclear(?:ed)?\b|\bbelieve\b|\btown\b|vouch|\bdefend", re.I)
RX_NEG = re.compile(r"\b(?:not|isn't|never|no|nothing|don't|doesn't|wasn't|aren't|won't|shouldn't|nobody)\b", re.I)
RX_CLAUSE = re.compile(r"(?<=[.!?;])\s+|,\s+(?:and|but|while|whereas|though)\s+|\s+but\s+", re.I)


def is_seer_claim(text):
    return bool(RX_SEER_CLAIM.search(text) or RX_CHECKED.search(text))


def norm(text):
    return (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


class Ev:
    """One reading of a statement clause: speaker pushes (w > 0) or vouches for (w < 0) target."""
    __slots__ = ("day", "rnd", "speaker", "target", "w", "idx")

    def __init__(self, day, rnd, speaker, target, w, idx):
        self.day, self.rnd, self.speaker, self.target, self.w, self.idx = day, rnd, speaker, target, w, idx


class Info:
    """Everything one view says, parsed."""

    def __init__(self, game, view):
        self.game = game
        you = view.get("you") or {}
        self.me = int(you.get("seat") or 0)
        self.role = you.get("role") or "villager"
        partner = you.get("partner")
        self.partner = partner.get("seat") if isinstance(partner, dict) else None
        self.day = int(view.get("day") or 1)
        self.phase = view.get("phase") or ""
        self.names, self.alive, self.revealed = {}, set(), {}
        players = view.get("players")
        for p in players if isinstance(players, list) else []:
            if not isinstance(p, dict):
                continue
            s = p.get("seat")
            if not isinstance(s, int):
                continue
            self.names[s] = str(p.get("name") or f"Player {s}")
            if p.get("alive"):
                self.alive.add(s)
            elif p.get("role"):
                self.revealed[s] = p.get("role")
        self.wolves = {self.me, self.partner} - {None} if self.role == "werewolf" else set()
        self.seer_results = {}
        for r in view.get("seerResults") or []:
            if isinstance(r.get("seat"), int) and r.get("werewolf") is not None:
                self.seer_results[r["seat"]] = (bool(r["werewolf"]), r.get("night") or 0)
        self.last_protect = view.get("lastProtect")
        self.wolf_votes = [v for v in (view.get("wolfVotes") or []) if isinstance(v, dict)]
        self.name_rx = {s: re.compile(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])") for s, n in self.names.items()}
        self._read_log(view.get("log") or [])
        self._read_speech()

    # ---------------------------------------------------------- the log
    def _read_log(self, log):
        self.speeches = []           # (day, round, seat, text, idx)
        self.passes = {}
        self.votes = {}              # day -> [(voter, target)]
        self.exiles = {}             # day -> (seat, role)
        self.night_deaths = {}       # night -> (seat or None, role)
        self.vote_order = {}
        self.debate_order = {}
        for i, e in enumerate(log if isinstance(log, list) else []):
            if not isinstance(e, dict):
                continue
            k, d = e.get("kind"), e.get("day") or 0
            if k == "speech" and isinstance(e.get("seat"), int):
                self.speeches.append((d, e.get("round") or 1, e["seat"], norm(e.get("text")), i))
            elif k == "pass":
                self.passes[e.get("seat")] = self.passes.get(e.get("seat"), 0) + 1
            elif k == "vote":
                self.votes.setdefault(d, []).append((e.get("seat"), e.get("target")))
            elif k == "exile":
                self.exiles[d] = (e.get("seat"), e.get("role"))
            elif k == "dawn":
                self.night_deaths[d] = (e.get("died"), e.get("role"))
            elif k == "voting":
                self.vote_order[d] = list(e.get("order") or [])
            elif k == "debate":
                self.debate_order[d] = list(e.get("order") or [])
        self.night_killed = {s for (s, _r) in self.night_deaths.values() if s is not None}

    def mentions(self, clause, speaker):
        out = [(int(m.group(1)), m.start()) for m in RX_SEAT.finditer(clause)]
        for s, rx in self.name_rx.items():
            out.extend((s, m.start()) for m in rx.finditer(clause))
        return sorted((x for x in out if x[0] != speaker and x[0] in self.names), key=lambda x: x[1])

    @staticmethod
    def nearest(ments, pos):
        return min(ments, key=lambda m: abs(m[1] - pos))[0]

    def _read_speech(self):
        self.seer_claims = []        # claimant seats in order of first claim
        self.seer_claim_day = {}
        self.seer_claim_idx = {}
        self.doc_claims = []
        self.claimed = {}            # claimant -> {target: is_wolf}
        self.events = []
        self.talk = {}               # seat -> statements made
        for (d, rnd, s, text, idx) in self.speeches:
            self.talk[s] = self.talk.get(s, 0) + 1
            if is_seer_claim(text) and s not in self.seer_claims:
                self.seer_claims.append(s)
                self.seer_claim_day[s] = d
                self.seer_claim_idx[s] = idx
            if RX_DOC_CLAIM.search(text) and s not in self.doc_claims:
                self.doc_claims.append(s)
            carry = None
            for clause in RX_CLAUSE.split(text):
                if not clause:
                    continue
                ments = self.mentions(clause, s)
                if not ments and carry is not None and re.search(r"\b(?:they|he|she|them|him|her)\b", clause, re.I):
                    ments = [(carry, 0)]
                if not ments:
                    continue
                carry = ments[-1][0]
                clear = RX_CLEAR.search(clause)
                wolf = RX_WOLF.search(clause)
                neg = RX_NEG.search(clause)
                if s in self.seer_claims:
                    if clear:
                        self.claimed.setdefault(s, {})[self.nearest(ments, clear.start())] = False
                    elif wolf and not neg and not RX_HEDGE.search(clause):
                        self.claimed.setdefault(s, {})[self.nearest(ments, wolf.start())] = True
                if clear:
                    self.events.append(Ev(d, rnd, s, self.nearest(ments, clear.start()), -1.5, idx))
                    continue
                if wolf and neg and neg.start() < wolf.start():  # "I don't think Ana is a wolf"
                    self.events.append(Ev(d, rnd, s, self.nearest(ments, wolf.start()), -1.0, idx))
                    continue
                sus = RX_SUS.search(clause)
                if sus and not neg:
                    w = 1.5 if wolf else 1.0
                    self.events.append(Ev(d, rnd, s, self.nearest(ments, sus.start()), w, idx))
                    continue
                tr = RX_TRUST.search(clause)
                if tr and not neg:
                    self.events.append(Ev(d, rnd, s, self.nearest(ments, tr.start()), -1.0, idx))

    # ---------------------------------------------------------- helpers
    def nm(self, s):
        return f"{self.names.get(s, '?')} (seat {s})"

    def short(self, s):
        return self.names.get(s, f"seat {s}")

    def my_speeches(self):
        return [sp for sp in self.speeches if sp[2] == self.me]

    def i_claimed_seer(self):
        return any(is_seer_claim(t) for (_d, _r, s, t, _i) in self.speeches if s == self.me)

    def i_claimed_doctor(self):
        return any(RX_DOC_CLAIM.search(t) for (_d, _r, s, t, _i) in self.speeches if s == self.me)

    def today_votes(self):
        return self.votes.get(self.day, [])

    def tally(self):
        t = {}
        for _v, tg in self.today_votes():
            if tg is not None:
                t[tg] = t.get(tg, 0) + 1
        return t

    def remaining_voters_after_me(self):
        order = self.vote_order.get(self.day) or []
        if self.me in order:
            return len(order) - order.index(self.me) - 1
        return 0

    def momentum(self):
        """How much the table is leaning on each seat today: votes cast plus today's pushes by others."""
        m = {}
        for _v, tg in self.today_votes():
            if tg is not None:
                m[tg] = m.get(tg, 0.0) + 1.0
        seen = set()
        for ev in reversed(self.events):
            if ev.day != self.day or ev.speaker == self.me or (ev.speaker, ev.target) in seen:
                continue
            seen.add((ev.speaker, ev.target))
            m[ev.target] = m.get(ev.target, 0.0) + (0.5 if ev.w > 0 else -0.3)
        return m

    def pressure_on_me(self):
        """Distinct others pushing on me today (and votes against me today)."""
        pushers = {ev.speaker for ev in self.events if ev.day == self.day and ev.target == self.me and ev.w > 0}
        return len(pushers) + sum(1 for _v, t in self.today_votes() if t == self.me)

    def influence(self, s):
        return sum(1 for ev in self.events if ev.speaker == s) + 0.5 * self.talk.get(s, 0)

    # ---------------------------------------------------------- belief
    def seer_credibility(self, known):
        """claimant -> 0..1, given private knowledge known {seat: is_wolf}."""
        revealed_seer = [s for s, r in self.revealed.items() if r == "seer"]
        n = len(self.seer_claims)
        cred = {}
        for i, c in enumerate(self.seer_claims):
            r = 0.75 if n == 1 else (0.55 if i == 0 else 0.4)
            rv = self.revealed.get(c)
            if rv == "seer":
                r = 1.0
            elif rv is not None or revealed_seer:
                r = 0.0
            if known.get(c):
                r = 0.0
            elif c in known and c != self.me and r > 0:
                r = max(r, 0.95)  # privately known not to be a wolf (e.g. the doctor saved them)
            if self.role == "seer" and c != self.me:
                r = 0.0
            for t, isw in self.claimed.get(c, {}).items():
                truth = None
                if t in self.revealed:
                    truth = self.revealed[t] == "werewolf"
                elif t in known:
                    truth = known[t]
                if truth is None:
                    continue
                if truth != isw:
                    r = 0.0
                    break
                if isw and r > 0:
                    r = max(r, 0.95)
            cred[c] = r
        if any(v >= 0.95 for v in cred.values()):
            top = max(cred.values())
            for c in cred:
                if cred[c] < top:
                    cred[c] = min(cred[c], 0.05)
        return cred

    def scores(self, known):
        """Village-side suspicion of every living other seat, with the reasons behind it."""
        me = self.me
        S = {s: 0.0 for s in self.alive if s != me}
        R = {s: [] for s in S}

        def add(s, w, why=None, ref=None):
            if s in S:
                S[s] += w
                if why:
                    R[s].append((w, why, ref))

        for s, isw in known.items():
            if s != me:
                add(s, 100.0 if isw else -100.0)
        cred = self.seer_credibility(known)
        multi = len(cred) > 1
        for c, r in cred.items():
            if c == me:
                continue
            if r <= 0.1:
                why = "are making a seer claim that does not hold up"
                if self.claimed.get(c, {}).get(me) and not known.get(me):
                    why = "called me a wolf with a fake seer claim"
                add(c, 9.0 if (multi or r == 0.0) else 4.0, why)
            else:
                add(c, -6.0 * r)
            for t, isw in self.claimed.get(c, {}).items():
                if t == c:
                    continue
                if isw:
                    add(t, 9.0 * r, f"were named a werewolf by {self.short(c)}'s seer check" if r >= 0.3 else None, c)
                else:
                    add(t, -5.0 * r)
        real_doc = [s for s, rl in self.revealed.items() if rl == "doctor"]
        for c in self.doc_claims:
            if c != me and real_doc and c not in real_doc:
                add(c, 8.0, "claimed doctor, but the real doctor is dead")
        for d, (ex, role) in self.exiles.items():
            if ex is None:
                continue
            for v, t in self.votes.get(d, []):
                if v == me:
                    continue
                if role == "werewolf":
                    if t == ex:
                        add(v, -0.8)
                    elif t is not None and self.revealed.get(t) != "werewolf":
                        add(v, 1.0, f"voted for {self.short(t)} on the day we caught the wolf {self.short(ex)}", ex)
                elif t == ex:
                    add(v, 1.0 + (1.0 if role == "seer" else 0.0), f"voted to exile {self.short(ex)}, who was a {role}", ex)
        for _d, vs in self.votes.items():
            for v, t in vs:
                if self.revealed.get(v) == "werewolf" and t is not None:
                    add(t, -1.2)
        pairs = set()
        for ev in self.events:
            sp, t = ev.speaker, ev.target
            key = (ev.day, sp, t, ev.w > 0)
            if key in pairs or sp == me:
                continue
            pairs.add(key)
            if self.revealed.get(sp) == "werewolf":
                if ev.w > 0:
                    add(t, -0.8)
                else:
                    add(t, 1.5, f"were vouched for by {self.short(sp)}, who turned out to be a wolf", sp)
                continue
            if self.revealed.get(t) == "werewolf":
                if ev.w < 0:
                    add(sp, 2.0, f"defended {self.short(t)}, who turned out to be a wolf", t)
                else:
                    add(sp, -0.7)
                continue
            if t == me and ev.w > 0 and not known.get(me):
                add(sp, 0.9, "kept pushing on me, and I know I am not a wolf", me)
                continue
            if (ev.w > 0 and t in cred and cred[t] >= 0.5 and t != sp
                    and ev.idx > self.seer_claim_idx.get(t, 10 ** 9)):
                add(sp, 1.5, f"went after {self.short(t)} right after {self.short(t)} claimed seer", t)
                continue
            if ev.w > 0 and self.revealed.get(t) in ("seer", "doctor", "villager") and t not in self.night_killed:
                add(sp, 0.5, f"pushed on {self.short(t)}, who was a {self.revealed[t]}", t)
            if sp in self.night_killed and ev.w > 0:
                add(t, 0.8, f"were suspected by {self.short(sp)} right before the wolves killed them", sp)
            elif ev.w > 0:
                add(t, 0.25)
        for s in S:
            p = self.passes.get(s, 0)
            if p:
                add(s, 0.3 * p, "keep passing instead of helping" if p >= 2 else None)
            S[s] += (h(self.game, "tb", s) % 1000) / 100000.0
        return S, R, cred

    def known_village(self):
        """What a village-side seat privately knows: its own innocence, its checks, its saves."""
        known = {self.me: False}
        for s, (w, _n) in self.seer_results.items():
            known[s] = w
        for s in self.doctor_saved():
            known.setdefault(s, False)
        return known

    def doctor_saved(self):
        """Seats our protection saved (the night they were protected, no one died)."""
        out = []
        for night, seat in (PROTECTS.get(self.game) or {}).items():
            dn = self.night_deaths.get(night)
            if seat is not None and dn is not None and dn[0] is None:
                out.append(seat)
        return out

    def reasons_text(self, R, s, n=2):
        """The strongest distinct reasons, at most one per person they refer to."""
        seen, refs, out = set(), set(), []
        for _w, why, ref in sorted(R.get(s, []), key=lambda x: -x[0]):
            if why in seen or (ref is not None and ref in refs):
                continue
            seen.add(why)
            if ref is not None:
                refs.add(ref)
            out.append(why)
            if len(out) >= n:
                break
        return out

    def said_today(self, s):
        """Whether I already named seat s in a statement today."""
        rx = self.name_rx.get(s)
        for (d, _r, sp, text, _i) in self.speeches:
            if d == self.day and sp == self.me and (f"seat {s})" in text or (rx is not None and rx.search(text))):
                return True
        return False


# ---------------------------------------------------------------- phrasing

def join_reasons(rs):
    if not rs:
        return ""
    if len(rs) == 1:
        return f"They {rs[0]}."
    return f"They {rs[0]}, and they {rs[1]}."


def village_push(info, t, rs, key):
    T = info.nm(t)
    again = info.said_today(t)
    if rs:
        if again:
            lead = choose([f"I'm sticking with {T}.", f"Nothing I've heard changes my mind on {T}.",
                           f"Still {T} for me."], *key, "again")
        else:
            lead = choose([f"My vote is going to {T}.", f"I'm putting my vote on {T}.", f"{T} is my top suspect right now.",
                           f"I want {T} out today."], *key, "lead")
        call = choose(["Let's stay together on this and not split the vote.",
                       "One target, please - a split vote only helps the wolves.",
                       f"If nobody has a stronger case, let's all land on {info.short(t)}.",
                       "Give me a better name with real evidence, otherwise let's consolidate."], *key, "call")
        return f"{lead} {join_reasons(rs)} {call}"
    lead = choose([f"I'm leaning toward {T}.", f"{T} is where I'm looking.", f"Right now my read is {T}."], *key, "soft")
    why = choose(["Their talk has been vague and they haven't pinned down anyone.",
                  "They've been light on reasons, which is what a wolf hiding would do.",
                  "Nothing they've said commits them to a read; that's how wolves stay safe."], *key, "why")
    call = choose(["Happy to switch if someone has harder evidence, but let's not scatter our votes.",
                   "Whatever we pick, let's vote together.", "Tell me if you see something I don't."], *key, "call2")
    return f"{lead} {why} {call}"


def opener(key):
    return choose([
        "Morning, everyone. There's nothing to read from yet, so I'm watching who pushes hard without giving reasons.",
        "Hi all. Day one is thin, so let's each name the one person we're unsure about and why, and then vote together.",
        "Good morning. I'm a plain villager with nothing to hide. Let's share reads honestly and not scatter our votes.",
        "Hello all. Let's keep this simple: real reasons, one bandwagon, and anyone with hard information should speak up.",
    ], *key, "open")


# ---------------------------------------------------------------- village roles

def village_target(info, options, voting):
    known = info.known_village()
    S, R, cred = info.scores(known)
    cand = [o for o in options if o in S]
    if not cand:
        return None, S, R
    hard = [c for c in cand if S[c] >= 50]
    if hard:
        t = max(hard, key=lambda c: S[c])
        return t, S, R
    best = max(cand, key=lambda c: S[c])
    mom = info.momentum()
    lead = max(cand, key=lambda c: (mom.get(c, 0.0), S[c]))
    need = 1.5 if voting else 1.0
    if lead != best and mom.get(lead, 0.0) >= need and S[lead] >= S[best] - 2.5 and S[lead] > -2.0:
        return lead, S, R
    return best, S, R


def village_speak(info, rnd):
    key = (info.game, info.day, rnd, info.me)
    others = [s for s in info.alive if s != info.me]
    if info.role == "seer" and seer_should_claim(info):
        return seer_claim_text(info, rnd)
    if info.role == "doctor" and (info.i_claimed_doctor() or info.pressure_on_me() >= 2 or doctor_settles_dispute(info)):
        return doctor_claim_text(info, rnd)
    t, S, R = village_target(info, others, False)
    if t is None:
        return opener(key)
    rs = info.reasons_text(R, t)
    if info.role == "seer":  # unclaimed seer: steer away from my cleared players
        rs = [r for r in rs if "seer check" not in r] or rs
    defence = ""
    if info.pressure_on_me() >= 2:
        defence = choose(["I know the heat is on me, but I'm a villager and you'd be wasting a vote.",
                          "To those pushing me: I'm village, and exiling me gives the wolves a free day.",
                          "I get why people look at me, but I'm not a wolf."], *key, "def") + " "
    defence += seer_defence(info, key)
    if info.day == 1 and rnd == 1 and S.get(t, 0) < 1.0 and not rs:
        return defence + opener(key)
    return defence + village_push(info, t, rs, key)


def seer_defence(info, key):
    """Speak up for a believable lone seer claimant whom the table is turning on."""
    if info.role == "seer":
        return ""
    _S, _R, cred = info.scores(info.known_village())
    live = [c for c in info.seer_claims if c in info.alive and c != info.me and cred.get(c, 0) >= 0.5]
    if not live:
        return ""
    c = max(live, key=lambda x: cred[x])
    if info.momentum().get(c, 0.0) < 0.5 and info.said_today(c):
        return ""
    C = info.short(c)
    if len(info.seer_claims) == 1:
        return choose([f"{C}'s seer claim is the best information we have, and nobody has counter-claimed, so {C} is not our exile. ",
                       f"I believe {C}'s seer claim. Wolves would love us to exile our own seer. ",
                       f"Let's protect {C}: an uncontested seer claim is worth more than any hunch. "], *key, "sd")
    return f"Between the seer claims I find {C} more believable. "


def village_vote(info, options):
    t, _S, _R = village_target(info, options, True)
    return t


# -- seer

def seer_known_wolves(info):
    return [s for s, (w, _n) in info.seer_results.items() if w]


def seer_should_claim(info):
    if info.i_claimed_seer():
        return True
    if any(s in info.alive for s in seer_known_wolves(info)):
        return True
    if any(c != info.me and c in info.alive for c in info.seer_claims):
        return True
    if info.pressure_on_me() >= 2:
        return True
    return info.day >= 3 and len(info.seer_results) >= 2


def seer_claim_text(info, rnd):
    key = (info.game, info.day, rnd, info.me)
    res = sorted(info.seer_results.items(), key=lambda kv: kv[1][1])
    parts = []
    for s, (w, n) in res:
        parts.append(f"night {n}, {info.nm(s)}: {'WEREWOLF' if w else 'not a wolf'}")
    head = choose(["I am the seer.", "I'm the seer, and I'm claiming it now.", "Seer claim: I am the seer."], *key, "h")
    if info.i_claimed_seer() and rnd == 2:
        head = choose(["Repeating my seer results so nobody loses track.", "Once more, as the seer:"], *key, "h2")
    body = ("My checks - " + "; ".join(parts) + ".") if parts else "I have no result to share yet."
    fakes = [c for c in info.seer_claims if c != info.me and c in info.alive]
    extra = ""
    if fakes:
        extra = " " + " ".join(f"{info.nm(f)} is lying about being the seer, so they are a wolf." for f in fakes)
    live_wolves = [s for s in seer_known_wolves(info) if s in info.alive]
    if live_wolves:
        w = live_wolves[0]
        act = choose([f"Everyone vote {info.nm(w)} today.", f"Vote {info.short(w)} out - that is a confirmed wolf.",
                      f"Our vote today is {info.nm(w)}, all of us together."], *key, "a")
    elif fakes:
        act = f"Vote {info.nm(fakes[0])} today."
    else:
        cleared = [s for s, (w, _n) in info.seer_results.items() if not w and s in info.alive]
        unk = [s for s in info.alive if s != info.me and s not in info.seer_results]
        act = "Trust the players I cleared"
        if cleared:
            act += " (" + ", ".join(info.short(s) for s in cleared) + ")"
        if unk:
            act += "; the wolves are among " + ", ".join(info.short(s) for s in unk)
        act += "."
        t = village_vote(info, [s for s in info.alive if s != info.me])
        if t is not None:
            act += f" My vote is {info.nm(t)}."
    return f"{head} {body}{extra} {act}"


def seer_inspect(info, options):
    known = info.known_village()
    S, _R, _cred = info.scores(known)
    fresh = [o for o in options if o not in info.seer_results] or list(options)
    def score(o):
        v = S.get(o, 0.0) + 0.4 * info.influence(o)
        if o in info.seer_claims:
            v -= 8.0  # a rival claimant is already exposed; check someone new
        return v + (h(info.game, "insp", info.day, o) % 1000) / 2000.0
    return max(fresh, key=score)


# -- doctor

def doctor_settles_dispute(info):
    """A seer claimant I saved (so the wolves attacked them) while another seer claimant is alive."""
    live = [c for c in info.seer_claims if c in info.alive and c != info.me]
    return len(live) >= 2 and any(s in live for s in info.doctor_saved())


def doctor_claim_text(info, rnd):
    key = (info.game, info.day, rnd, info.me)
    saved = [s for s in info.doctor_saved() if s in info.names]
    hist = PROTECTS.get(info.game) or {}
    t, _S, R = village_target(info, [s for s in info.alive if s != info.me], False)
    msg = choose(["I'm the doctor.", "I have to claim: I am the doctor.", "I'm the doctor, so please hear me out."], *key, "d")
    real = [s for s in saved if s in info.seer_claims and s in info.alive]
    if real:
        x = real[-1]
        fakes = [c for c in info.seer_claims if c != x and c in info.alive and c != info.me]
        msg += (f" I protected {info.nm(x)} and nobody died, so the wolves attacked {info.short(x)}:"
                f" {info.short(x)} is the real seer.")
        if fakes:
            msg += f" That makes {info.nm(fakes[0])} a fake seer and a wolf. Vote {info.short(fakes[0])}."
        return msg
    if saved:
        msg += " I saved " + ", ".join(info.nm(s) for s in saved) + " - that's why nobody died."
    elif hist:
        msg += " I protected " + ", ".join(f"{info.short(s)} on night {n}" for n, s in sorted(hist.items()) if s) + "."
    msg += " Exiling me only helps the wolves."
    if t is not None:
        msg += f" Look at {info.nm(t)} instead."
        rs = info.reasons_text(R, t, 1)
        if rs:
            msg += f" They {rs[0]}."
    return msg


def doctor_protect(info, options):
    known = info.known_village()
    S, _R, cred = info.scores(known)
    opts = list(options)
    seers = [c for c in info.seer_claims if c in opts and cred.get(c, 0) >= 0.5]
    if seers:
        return max(seers, key=lambda c: cred[c])
    saved = [s for s in info.doctor_saved() if s in opts]
    if saved:
        return saved[-1]
    cleared = [t for c, r in cred.items() if r >= 0.5 for t, w in info.claimed.get(c, {}).items() if not w and t in opts]
    if cleared:
        return max(cleared, key=lambda t: info.influence(t))
    def value(o):
        return info.influence(o) - 0.5 * S.get(o, 0.0) + (h(info.game, "prot", info.day, o) % 1000) / 1000.0
    return max(opts, key=value)


# ---------------------------------------------------------------- werewolf

def wolf_threat(info, s):
    """How dangerous a non-wolf is to us: pushes and votes on wolves, wolf calls on us."""
    t = 0.0
    for ev in info.events:
        if ev.speaker == s and ev.target in info.wolves:
            t += 1.0 if ev.w > 0 else -0.5
    for _d, vs in info.votes.items():
        for v, tg in vs:
            if v == s and tg in info.wolves:
                t += 1.0
    if any(info.claimed.get(s, {}).get(w) for w in info.wolves):
        t += 5.0
    return t


def wolf_counterclaim_target(info):
    """The seer claimant to counter-claim against, or None.

    Once I have claimed, it is the one I named a wolf (so the story stays the same every day).
    Otherwise a live claimant who named me a wolf, or whose clears leave me one of the last two
    unchecked players: the village is about to find me, so a counter-claim is the only play left.
    """
    if info.i_claimed_seer():
        for s, w in info.claimed.get(info.me, {}).items():
            if w and s not in info.wolves:
                return s
    for c in info.seer_claims:
        if c in info.wolves or c == info.me or c not in info.alive:
            continue
        cl = info.claimed.get(c, {})
        if cl.get(info.me):
            return c
        unchecked = [s for s in info.alive if s != c and s not in cl]
        if info.me in unchecked and len(unchecked) <= 2 and len(cl) >= 2:
            return c
    return None


def partner_claimed_seer(info):
    return info.partner is not None and info.partner in info.seer_claims and info.partner in info.alive


def wolf_target(info, options):
    """Who we wolves want exiled: the non-wolf the table is already leaning on, else a plausible pick."""
    cand = [o for o in options if o not in info.wolves]
    if not cand:
        return None
    cc = wolf_counterclaim_target(info)
    if (info.i_claimed_seer() or cc) and cc in cand:
        return cc
    if partner_claimed_seer(info):
        rivals = [c for c in info.seer_claims if c not in info.wolves and c in cand]
        if rivals:
            return rivals[0]
    S, _R, cred = info.scores({info.me: False})
    mom = info.momentum()
    def score(c):
        v = 2.0 * mom.get(c, 0.0) + S.get(c, 0.0) + 0.8 * wolf_threat(info, c)
        if cred.get(c, 0) >= 0.5:
            v -= 3.0  # a believed seer is hard to vote out; the night is for them
        return v
    return max(cand, key=score)


def wolf_vote(info, options):
    cand = [o for o in options if o not in info.wolves]
    if not cand:
        return options[0] if options else None
    tally = info.tally()
    p = info.partner
    if p is not None and p in info.alive and p in options:
        vp = tally.get(p, 0)
        others = [tally.get(o, 0) for o in options if o != p]
        vo = max(others) if others else 0
        if vp > vo + 1 + info.remaining_voters_after_me():
            return p  # partner is gone whatever we do; look clean
        if vp >= 1 and vp >= vo:
            rival = max(cand, key=lambda c: (tally.get(c, 0), info.momentum().get(c, 0.0)))
            if tally.get(rival, 0) + 1 >= vp:
                return rival  # make it a tie or overtake
    return wolf_target(info, options)


def wolf_fake_results(info, cc):
    """A stable fake seer record: the rival claimant is a wolf on the night before my first claim
    (so I claimed as soon as I "found" them), and every other night clears a villager."""
    first = info.seer_claim_day.get(info.me, info.day)
    pool = sorted((s for s in info.names if s not in info.wolves and s != cc and s != info.me
                   and s not in info.seer_claims), key=lambda s: h(info.game, "fake", s))
    out = []
    for n in range(1, info.day + 1):
        if n == first:
            out.append((n, cc, True))
        elif pool:
            out.append((n, pool.pop(0), False))
    return out


def wolf_speak(info, rnd):
    key = (info.game, info.day, rnd, info.me)
    others = [s for s in info.alive if s != info.me]
    cc = wolf_counterclaim_target(info)
    if cc is not None and (info.i_claimed_seer() or cc in info.alive):
        res = wolf_fake_results(info, cc)
        txt = "; ".join(f"night {n}, {info.nm(s)}: {'WEREWOLF' if w else 'not a wolf'}" for n, s, w in res)
        head = choose(["I am the real seer.", "I'm the seer, and that claim is false.", "I have to claim: I am the seer."], *key, "cc")
        return (f"{head} My checks - {txt}. {info.short(cc)} is a wolf who claimed seer to get a villager killed. "
                f"Vote {info.nm(cc)} today.") if cc in info.alive else f"{head} My checks - {txt}."
    t = wolf_target(info, others)
    if t is None:
        return choose(["Let's vote together and finish this.", "I'm with the group on this one."], *key, "x")
    S, R, _cred = info.scores({info.me: False})
    rs = info.reasons_text(R, t)
    pre = ""
    if info.partner is not None and (info.exiles.get(info.day - 1) or (None,))[0] == info.partner:
        pre = choose([f"Good riddance to {info.short(info.partner)}. ",
                      f"Glad we caught {info.short(info.partner)}. "], *key, "pd")
    if info.pressure_on_me() >= 2:
        pre += choose(["I'm a villager, and pushing me just burns a day for the wolves.",
                       "I hear the suspicion on me, but I'm village and I've been hunting like everyone else.",
                       "I'm not a wolf. Exiling me would be exactly what the real wolves want."], *key, "wd") + " "
    if partner_claimed_seer(info):
        rivals = [c for c in info.seer_claims if c not in info.wolves]
        if rivals:
            pre += f"I believe {info.short(info.partner)}'s seer claim over {info.short(rivals[0])}'s. "
    if info.day == 1 and rnd == 1 and not rs and info.momentum().get(t, 0) <= 0:
        return pre + opener(key) + " " + choose([
            f"So far {info.nm(t)} is the one I'd like to hear more from.",
            f"I'll be watching {info.nm(t)}; I haven't got a feel for them yet."], *key, "w1")
    return pre + village_push(info, t, rs, key)


def wolf_kill(info, options):
    opts = [o for o in options if o not in info.wolves]
    if not opts:
        return None
    night = info.day
    mine = [v for v in info.wolf_votes if v.get("night") == night and v.get("seat") != info.me]
    if mine and mine[-1].get("target") in opts:
        return mine[-1]["target"]  # agree with the partner's pick: a split kills nobody
    prev = [v.get("target") for v in info.wolf_votes if v.get("night") == night - 1 and v.get("target") is not None]
    dn = info.night_deaths.get(night - 1)
    if prev and len(set(prev)) == 1 and dn is not None and dn[0] is None and prev[0] in opts:
        return prev[0]  # protected last night, so the doctor cannot protect them again tonight
    protect_rival = (info.i_claimed_seer() or partner_claimed_seer(info))
    S, _R, cred = info.scores({info.me: False})
    seers = [c for c in info.seer_claims if c in opts and not protect_rival]
    if seers:
        return max(seers, key=lambda c: (wolf_threat(info, c), -info.seer_claims.index(c)))
    docs = [c for c in info.doc_claims if c in opts]
    if docs:
        return docs[0]
    def value(c):
        v = 1.0 * wolf_threat(info, c) + 0.3 * info.influence(c) - 0.5 * S.get(c, 0.0)
        if protect_rival and c in info.seer_claims:
            v -= 50
        return v + (h(info.game, "kill", night, c) % 1000) / 1000.0
    return max(opts, key=value)


# ---------------------------------------------------------------- dispatch

def fallback(p, strict=False):
    """A safe legal answer: a neutral statement (or a pass when strict), or no target."""
    if p.get("decision") == "speak":
        if strict:
            return {"pass": True}
        return {"text": "I'm still weighing what I've heard. Let's pick one name with real reasons and vote together."}
    return {"target": None}


def decide(p):
    """The answer body (without game/decision) for one pending decision."""
    kind = p.get("decision")
    game = p.get("game")
    view = p.get("view") or {}
    info = Info(game, view)
    options = [o for o in (p.get("options") or []) if isinstance(o, int)]
    if kind == "speak":
        rnd = 1 + sum(1 for sp in info.speeches if sp[0] == info.day and sp[2] == info.me)
        rnd = min(rnd, 2)
        text = wolf_speak(info, rnd) if info.role == "werewolf" else village_speak(info, rnd)
        text = clip(text, int(p.get("max_chars") or MAX_CHARS))
        return {"text": text} if text else {"pass": True}
    if not options:
        return {"target": None}
    if kind == "vote":
        t = wolf_vote(info, options) if info.role == "werewolf" else village_vote(info, options)
    elif kind == "kill":
        t = wolf_kill(info, options)
    elif kind == "protect":
        t = doctor_protect(info, options)
    elif kind == "inspect":
        t = seer_inspect(info, options)
    else:
        t = None
    if t not in options:
        t = options[0]
    return {"target": t}


def answer(p):
    game, kind = p.get("game"), p.get("decision")
    try:
        body = decide(p)
    except Exception as err:
        log(f"game {game} {kind}: strategy error {err!r}")
        body = fallback(p)
    status, res = http_post("/act", dict(body, game=game, decision=kind))
    if status != 200:
        log(f"game {game} {kind}: /act answered {status} {res}; sending the fallback")
        if status not in (400, None):
            return  # 409 not_pending: nothing waits any more
        body = fallback(p, strict=("text" in body))
        status, res = http_post("/act", dict(body, game=game, decision=kind))
        if status == 400:
            body = fallback(p, strict=True)
            status, res = http_post("/act", dict(body, game=game, decision=kind))
    if status == 200 and kind == "protect":
        try:
            night = int((p.get("view") or {}).get("day") or 0)
            PROTECTS.setdefault(game, {})[night] = body.get("target")
        except Exception:
            pass


def main():
    failures = 0
    while True:
        status, obs = http_post("/observe", {}, timeout=60)
        if status != 200 or not isinstance(obs, dict):
            failures += 1
            if failures > 150:  # about 12 minutes without an answer
                log("the env stopped answering; exiting")
                return
            time.sleep(min(5.0, 0.25 * failures))
            continue
        failures = 0
        if obs.get("done"):
            log(f"done: {obs.get('finished')} games finished")
            return
        pending = [p for p in (obs.get("pending") or []) if isinstance(p, dict)]
        pending.sort(key=lambda p: (p.get("game") or 0))
        for p in pending:
            answer(p)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
