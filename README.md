# Rule-based Werewolf agent

An entry for the make.fun bounty "Build the best Werewolf agent". `main.py` plays one seat in each of
the trial's 30 games through the env at `MAKEFUN_ENV_URL`, and makes **no model calls**. Every statement,
vote and night action comes from hand-written rules. It runs on Python 3.13 with the standard library
only, and it is deterministic: answers depend only on the game's view, the trial seed (used only to vary
phrasing) and the agent's own earlier answers.

## How it plays

The agent rebuilds a full picture from each view's public log: role claims, claimed seer results,
accusations and defences (read clause by clause), each day's votes, deaths, exiles and revealed roles.
A shared suspicion model scores every living player from that evidence. The evidence includes seer
claims weighted by credibility, votes for exiled villagers, defending a player later revealed as a wolf,
going after a believable seer claimant, and fake claims caught out by revealed roles.

- **Villager**: names one suspect with concrete reasons, defends a believable lone seer claimant, and
  joins the forming bandwagon when its target is nearly as suspicious as its own pick, so the vote is
  never split for nothing.
- **Seer**: inspects the most suspicious and most influential unchecked players. Once it finds a wolf
  (or is pressured, or meets a rival claim) it claims seer and lists every result each day.
- **Doctor**: protects a believable seer claimant, then players it previously saved, then cleared or
  vocal villagers, and never the same seat two nights running. When a save proves which of two seer
  claimants is real, it says so.
- **Werewolf**: talks like a villager, reusing the village model's reasons, and pushes the non-wolf the
  table already leans on. It never votes its partner unless that exile is already certain, and it tries
  to force a tie when the partner leads. At night it follows the partner's pick, re-targets a player
  protected the night before (the doctor cannot repeat), then goes for seer claimants, doctor claimants
  and the biggest threats. It counter-claims seer only when a claimant has named it or narrowed the
  unchecked players down to it.

## Local testing

`devtools/` drives the kit's real env (`program/env/server.mjs`) with a scripted stand-in for the house
LLM lane. Nothing in it is used by `main.py`.

```
python devtools/test_parse.py            # statement-reader and night-rule checks
python devtools/run_local.py --trial 0   # one trial against the fake house; prints the score
python devtools/batch.py --seeds 4       # 3 practice trials x 4 seeds, mean score
```

`run_local.py` expects the kit at `../../werewolf-agent-01-kit` (override with `--kit`) and Node 22+.
