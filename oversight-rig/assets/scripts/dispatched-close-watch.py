#!/usr/bin/env python3
"""Mail the rig's project-lead when a bead it dispatched closes.

Run by orders/dispatched-close-watch.toml on every bead.closed event. Read-only apart from the
mail: it never reopens, closes or edits a bead. See the "Dispatched-close watch" section of the
pack README.

Ported from rematu/gascity#13 (a8bb33c) by rematu's project-lead, generalized to any rig.

For each bead.closed event of the last --since (default 10m) in a watched rig:
  1. `gc bd show <id> --json`: keep it if `created_by` is the lead and it is still closed.
     Convoys, mail, workflow steps and order tracking beads are dropped from the event payload
     before that read. A close by the lead's own session is not reported back to it.
  2. `gc mail send <lead> --dedup dispatched-close:<id>:<closed_at> --notify`.

Watched rigs are the importing rig ($GC_RIG) plus the child rigs in $GC_OVERSIGHT_CHILD_RIGS
(comma-separated rig names); bead prefixes come from `gc rig list`.

Prints one JSON summary line on stdout. Exits 2 when gc cannot be read or a mail fails: a watcher
that cannot see must not look green. With no rig at all (the city-level instance of the order)
there is no lead to tell: it prints a skip line and exits 0.

Environment (all optional):
  GC_RIG                   importing rig; its lead is told (or --to)
  GC_OVERSIGHT_CHILD_RIGS  extra rigs whose closes are watched (or --child-rigs)
  GC_BIN                   gc executable (default `gc`)
  GC_PACK_STATE_DIR        where handled closes are kept between runs
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ORDER = "dispatched-close-watch"
REASON_CHARS = 500
RETENTION = timedelta(hours=2)  # must exceed --since: a close is mailed once per run window
SKIP_TYPES = {"convoy", "message", "molecule", "gate", "event", "agent", "session"}


class Unavailable(Exception):
    """gc could not be read."""


def utcnow():
    return datetime.now(timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(argv, what):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unavailable(f"{what}: {e}")
    if p.returncode != 0:
        raise Unavailable(f"{what}: exit {p.returncode}: {p.stderr.strip()[:300]}")
    return p.stdout


def load_json(text, what):
    try:
        return json.loads(text)
    except ValueError:
        raise Unavailable(f"{what}: output is not JSON")


def one(data):
    """`gc bd show --json` returns an object or a one-element list."""
    if isinstance(data, list):
        return data[0] if len(data) == 1 and isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def split(value):
    return [r.strip() for r in (value or "").split(",") if r.strip()]


def watched_rigs(gc, names):
    """prefix -> {name, path} for the watched, non-suspended rigs."""
    data = load_json(run([gc, "rig", "list", "--json"], "gc rig list"), "gc rig list")
    rigs = data.get("rigs") if isinstance(data, dict) else None
    if not isinstance(rigs, list):
        raise Unavailable("gc rig list: no rigs array")
    found = {r.get("name"): r for r in rigs if isinstance(r, dict) and not r.get("hq")}
    missing = [n for n in names if n not in found]
    if missing:
        raise Unavailable("gc rig list: unknown rig " + ", ".join(missing))
    return {found[n]["prefix"]: found[n] for n in names if not found[n].get("suspended")}


def closed_events(gc, since):
    """bead id -> {"bead": payload bead, "actors": [...]}, in event order."""
    out = run([gc, "events", "--type", "bead.closed", "--since", since], "gc events")
    events = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        e = load_json(line, "gc events")
        bead = ((e.get("payload") or {}).get("bead") or {}) if isinstance(e, dict) else {}
        if not bead.get("id"):
            continue
        entry = events.setdefault(bead["id"], {"bead": bead, "actors": []})
        if e.get("actor"):
            entry["actors"].append(e["actor"])
    return events


def lead_identities(gc, to):
    """Every name the lead creates and closes beads under: its address, session ids and aliases."""
    ids = {to}
    try:
        data = load_json(run([gc, "session", "list", "--json"], "gc session list"), "gc session list")
        for s in data.get("sessions", []):
            if s.get("template") == to:
                ids.update(v for v in (s.get("id"), s.get("name"), s.get("alias")) if v)
    except (Unavailable, AttributeError):
        pass  # cannot list sessions: match on the address alone and tell the lead anyway
    return ids


def noise(bead):
    """Closes the lead never asked to hear about: convoys, mail, workflow plumbing."""
    meta = bead.get("metadata") if isinstance(bead.get("metadata"), dict) else {}
    return (bead.get("issue_type") in SKIP_TYPES or meta.get("gc.synthetic") == "true"
            or "gc.kind" in meta or "gc.step_id" in meta or "order-tracking" in (bead.get("labels") or []))


def closer(actors):
    """The session that closed the bead. The controller's cache re-emits the close as cache-reconcile."""
    real = [a for a in actors if a != "cache-reconcile"]
    return real[0] if real else (actors[0] if actors else "")


def truncate(s, n=REASON_CHARS):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def mail_subject(bead_id, title):
    return f"[order {ORDER}] Closed: {bead_id} {(title or '')[:80]}".strip()


def mail_body(bead, rig, by):
    meta = bead.get("metadata") or {}
    return "\n".join([
        f"Bead: {bead.get('id')} ({rig}) {bead.get('title') or ''}".rstrip(),
        f"Closed: {bead.get('closed_at') or bead.get('updated_at') or '?'} by {by or 'unknown'}",
        f"Outcome: {meta.get('gc.outcome') or '(none)'}",
        f"Close reason: {truncate(bead.get('close_reason')) or '(none)'}",
        "",
        f"Automated notice from the oversight-rig {ORDER} order, not from a person. gc mail send",
        "--from only accepts the calling session's own identity and an order has none, so the",
        f"sender shows as human. It changes nothing: review it with gc bd show {bead.get('id')}.",
    ])


class Handled:
    """Closes already mailed, kept between runs so the lookback does not mail them again."""

    def __init__(self, directory):
        self.path = os.path.join(directory, f"{ORDER}.json") if directory else None
        self.seen = {}
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.seen = json.load(f)
            except (OSError, ValueError):
                self.seen = {}

    def __contains__(self, key):
        return key in self.seen

    def add(self, key, t):
        self.seen[key] = iso(t)

    def save(self, t):
        if not self.path:
            return
        cutoff = iso(t - RETENTION)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({k: v for k, v in self.seen.items() if v >= cutoff}, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rigs", default=os.environ.get("GC_RIG"),
                    help="comma-separated rigs whose closes are watched (default: $GC_RIG)")
    ap.add_argument("--child-rigs", default=os.environ.get("GC_OVERSIGHT_CHILD_RIGS"),
                    help="comma-separated extra rigs to watch (default: $GC_OVERSIGHT_CHILD_RIGS)")
    ap.add_argument("--to", help="the lead (default: <$GC_RIG or first rig>/oversight-rig.project-lead)")
    ap.add_argument("--since", default="10m", help="event lookback; must exceed the controller tick")
    ap.add_argument("--dry-run", action="store_true", help="detect and print, send no mail")
    a = ap.parse_args(argv)
    own = split(a.rigs)
    if not own:
        # The city-level instance of a rig order: no rig, so no lead to tell.
        print(json.dumps({"ok": True, "skipped": "no rig: pass --rigs or set GC_RIG"}))
        return 0
    rigs = list(dict.fromkeys(own + split(a.child_rigs)))
    to = a.to or f"{os.environ.get('GC_RIG') or own[0]}/oversight-rig.project-lead"
    gc = os.environ.get("GC_BIN") or "gc"
    t = utcnow()
    try:
        by_prefix = watched_rigs(gc, rigs)
        events = closed_events(gc, a.since)
    except Unavailable as e:
        print(f"{ORDER}: cannot read closes: {e}", file=sys.stderr)
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2

    handled = Handled(os.environ.get("GC_PACK_STATE_DIR"))
    lead = None
    notified, errors = [], []
    for bead_id, ev in events.items():
        rig = by_prefix.get(bead_id.split("-", 1)[0])
        if not rig or noise(ev["bead"]):
            continue
        try:
            bead = one(load_json(run([gc, "bd", "show", bead_id, "--json"], "gc bd show"), "gc bd show"))
        except Unavailable as e:
            errors.append(f"{bead_id}: {e}")
            continue
        if lead is None:
            lead = lead_identities(gc, to)
        if not bead or bead.get("created_by") not in lead or bead.get("status") != "closed" or noise(bead):
            continue
        key = f"dispatched-close:{bead_id}:{bead.get('closed_at') or ev['bead'].get('updated_at') or ''}"
        if key in handled:
            continue
        by = closer(ev["actors"])
        if by in lead:
            handled.add(key, t)
            continue  # the lead closed it itself
        subject = mail_subject(bead_id, bead.get("title"))
        body = mail_body(bead, rig["name"], by)
        if a.dry_run:
            print(subject + "\n" + body + "\n", file=sys.stderr)
            notified.append(bead_id)
            continue
        try:
            run([gc, "mail", "send", to, "-s", subject, "-m", body, "--dedup", key, "--notify"],
                "gc mail send")
        except Unavailable as e:
            errors.append(f"{bead_id}: {e}")
            continue
        handled.add(key, t)
        notified.append(bead_id)
    if not a.dry_run:
        handled.save(t)
    for e in errors:
        print(f"{ORDER}: {e}", file=sys.stderr)
    print(json.dumps({"ok": not errors, "rigs": rigs, "to": to, "closes": len(events),
                      "notified": notified, "errors": errors}))
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
