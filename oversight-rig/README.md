# oversight-rig

A drop-in Gas City pack that **disaggregates project-level work scoping out of the mayor**. It adds one always-on, rig-scoped `project-lead` role per rig, so the mayor stops being the bottleneck for every project's day-to-day triage and dispatch.

## The problem it solves

In a single-mayor city, the mayor ends up in the minutiae of every project's work scoping — what to dispatch, what's blocked, what to escalate — across every rig at once. That doesn't scale.

## The model

```
mayor (city, unchanged) ──── plans cross-cutting work, handles escalations

  rig: foo                      rig: bar                  …
  project-lead-foo              project-lead-bar
  • bounded to foo's beads      • bounded to bar's beads
  • reads foo/.gc/              • reads bar/.gc/
    project-brief.md              project-brief.md
  • dispatches its own          • dispatches its own
    ready work directly           ready work directly
```

- **`project-lead`** (rig scope, always-on, one per rig)
  - Bounded to a single rig's beads. Reads its persona, current focus, and escalation triggers from `<rig>/.gc/project-brief.md` — each project owns its own scoping context, so it never piles onto the mayor.
  - Triages its rig every tick and **dispatches ready, in-scope work in its own rig directly** — it does not route every dispatch through the mayor.
  - Judges severity (info vs escalate) and writes structured rollup beads.
- **The mayor stays unchanged** and shrinks to what is genuinely cross-cutting: planning work that spans rigs, and reacting to escalations.

## Deterministic escalation (no relay agent)

The project-lead writes a rollup bead labeled `severity:escalate`. A scheduled order (`escalate-rollups`) uses a mechanical condition trigger that checks for undelivered escalate-severity rollups and delivers them via extmsg — **no second agent decides "is this escalation-worthy."** The judgment is made once, by the agent with the right context, and the rollup bead is the audit trail. Human replies route straight back to the bound project-lead.

## What's in the pack

- `agents/project-lead/` — the role (agent config, prompt template, and a `project-brief.template.md` to copy per rig)
- `orders/` — `patrol-project-leads` (triage cadence), `escalate-rollups` (deterministic delivery) and `dispatched-close-watch` (wakes the lead when its work closes)
- `assets/scripts/` — the delivery script, a rig→channel resolver and the dispatched-close watcher
- `skills/city-executive-status/` — an optional workflow for maintaining an Obsidian-compatible portfolio brief from project-owner updates

## Optional executive status brief

The `city-executive-status` skill packages the shareable workflow for requesting
structured project-owner updates, validating them, and writing one concise
portfolio brief. It can optionally publish a content-hash-deduplicated summary
through a deployment-specific adapter.

Importing this pack exposes the skill but does not activate its example
schedules or write to a vault. The only active pack orders remain
`patrol-project-leads`, `escalate-rollups` and `dispatched-close-watch`. To enable the workflow, follow the
skill's `SKILL.md`, copy its environment and order examples into the consuming
city, configure local paths, and verify dry-run output before scheduling writes.

## Dispatched-close watch

A project-lead that dispatches work and then waits can sit idle with finished
work: nothing tells it that a bead it created has closed. The
`dispatched-close-watch` order fixes that without changing gc. It runs
`assets/scripts/dispatched-close-watch.py` on every `bead.closed` event and,
when a bead the lead created closes, mails `<rig>/oversight-rig.project-lead`
with `--notify` so the lead gets a turn. It changes nothing else: the lead
decides what to do next.

**Which rigs.** The order is rig-scoped: each rig that imports the pack gets
its own instance, run with `$GC_RIG` set, and that rig's lead is told. A lead
that also dispatches into child rigs lists them in `GC_OVERSIGHT_CHILD_RIGS`
(comma-separated rig names, set in the city's or the order's environment).
Bead prefixes come from `gc rig list`. Because only beads the lead itself
created are mailed, a city-wide `GC_OVERSIGHT_CHILD_RIGS` never mails one lead
about another lead's work. The city-level instance, when the pack is also
imported by the city, has no rig and exits 0 without reading anything.

**Which lead.** By default the lead is `<rig>/oversight-rig.project-lead`. A
rig whose lead is another agent sets `GC_OVERSIGHT_LEAD` to that agent's
address (`--to` still wins when run by hand). `GC_OVERSIGHT_LEAD_ALIASES`
(comma-separated, or `--lead-aliases`) lists other names the lead created or
closed beads under, such as its address before a rename. Aliases count as the
lead both for `created_by` and for "the lead closed it itself", and so do the
sessions whose template is one of them. Mail always goes to the one lead
address.

```bash
# The feedlot rig's lead is feedlot/feedlot-lead, formerly feedlot-lead.
GC_OVERSIGHT_LEAD=feedlot/feedlot-lead
GC_OVERSIGHT_LEAD_ALIASES=feedlot-lead
```

**What it does.**

1. `gc events --type bead.closed --since 10m`. Event orders run on the
   controller tick, so a close is mailed within a tick or two; the 10 minutes
   are a margin. A close reaches the bus twice (from the closing session, then
   as `cache-reconcile`), so events are grouped by bead.
2. From the event payload it drops what is not a task: other rigs, convoys,
   mail, `gc.synthetic` beads, workflow steps (`gc.kind`, `gc.step_id`) and
   order tracking beads. Those are never read.
3. `gc bd show <id> --json`: keeps the bead if `created_by` is the lead (its
   address or one of its aliases, or a session id, name or alias of a session
   whose template is one of those) and it is still closed. A close by the lead itself is not mailed
   back to it.
4. `gc mail send <lead> --dedup dispatched-close:<id>:<closed_at> --notify`.
   The subject is `[order dispatched-close-watch] Closed: <id> <title>`; the
   body has the bead, who closed it, `gc.outcome` and the first 500 characters
   of the close reason.

**Sender.** The mail shows as sent by `human`: `gc mail send --from` only
accepts the calling session's own identity, and an order has none. The
`[order dispatched-close-watch]` subject prefix and a line in the body say
that it is an automated notice from this order, not a person.

A mailed close is kept for 2 hours in
`$GC_PACK_STATE_DIR/dispatched-close-watch.json`, so later runs inside the
10-minute window do not send it again; the mail's dedup key covers a run
without that file. A bead that is reopened and closed again has a new
`closed_at` and is mailed again.

**Fails loud.** If `gc rig list` or `gc events` cannot be read, a watched rig
is unknown, a bead cannot be read, or a mail fails, the script exits 2 and
`gc order history` records a failed run. A failed mail is retried on the next
run.

**Read-only.** It only runs `gc events`, `gc rig list`, `gc bd show`,
`gc session list` and `gc mail send`; the tests check that it makes no other
call.

```bash
# Dry run by hand: prints the mails to stderr and sends none.
GC_RIG=<rig> GC_OVERSIGHT_CHILD_RIGS=<child,...> \
  python3 oversight-rig/assets/scripts/dispatched-close-watch.py --since 2h --dry-run
python3 -m pytest oversight-rig/tests/test_dispatched_close_watch.py -q
```

The tests use a fake `gc` (`tests/fixtures/dispatched-close/`) and the real
close of `rgc-ba8` from 2026-11-19, with paths made generic.

Limits: it does not say when a worker went idle without closing (only closes
are events), and "dispatched by the lead" means `created_by` is the lead — a
bead someone else created and the lead only slung is not mailed.

Ported from rematu's project-lead work in rematu/gascity#13 (a8bb33c).

## Requirements

- An extmsg/slack adapter in the city for outbound delivery and inbound replies — **compose this pack with your slack pack** (e.g. `slack-full`, `slack-channel`, or `slack-mini`). This pack ships only the oversight role and its escalation machinery, not a slack bridge.
- Optional: the project-lead's rig-scoped dispatch examples use convoy formulas (`mol-decompose`, `mol-pr-from-issue`) supplied by a workflow pack (e.g. `gastown`). The role works without them.
- Optional: the executive-status skill needs Python 3.11 or newer. Obsidian and a publishing adapter are not required; without them it writes ordinary Markdown to a configured path.

## Install

1. Add the pack to your `city.toml` and stamp a `project-lead` per rig.
2. For each rig under oversight, author its brief:
   ```bash
   cp agents/project-lead/project-brief.template.md <rig-root>/.gc/project-brief.md
   # then edit the brief to fit the project
   ```
3. Bind each rig's `project-lead` session to that rig's channel via your slack pack so escalations deliver and replies route back.
4. `gc supervisor reload` — picks up the new agents and orders.
