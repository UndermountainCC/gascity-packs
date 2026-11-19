#!/usr/bin/env python3
"""Tests for assets/scripts/dispatched-close-watch.py (stdlib unittest). Fake gc, no network, no city.

  python3 -m pytest oversight-rig/tests/test_dispatched_close_watch.py -q   (from the repo root)

Ported from rematu/gascity#13 (a8bb33c). The rgc-ba8 fixtures are its real close on 2026-11-19
(bead and both bead.closed events), with paths made generic: a task rematu's project-lead
dispatched into its child rig rematu-gascity, closed by a worker with gc.outcome=pass.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

try:
    import tomllib  # Python 3.11+
except ImportError:
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(HERE)
SCRIPT = os.path.join(PACK, "assets", "scripts", "dispatched-close-watch.py")
FIX = os.path.join(HERE, "fixtures", "dispatched-close")
LEAD = "rematu/oversight-rig.project-lead"
PREFIX = "[order dispatched-close-watch] Closed: "
# The only calls the watcher may make. It must never edit a bead or reach the cluster.
ALLOWED = [["gc", "events"], ["gc", "rig", "list"], ["gc", "bd", "show"],
           ["gc", "session", "list"], ["gc", "mail", "send"]]


def body(mail):
    return mail["args"][mail["args"].index("-m") + 1]


def subject(mail):
    return mail["args"][mail["args"].index("-s") + 1]


def event(bead_id, actor="pi-wisp-worker", **bead):
    b = dict({"id": bead_id, "title": "t " + bead_id, "status": "closed", "issue_type": "task",
              "updated_at": "2026-11-19T05:00:00Z", "metadata": {}}, **bead)
    return {"actor": actor, "payload": {"bead": b}, "subject": bead_id, "type": "bead.closed",
            "ts": "2026-11-19T05:00:00.1Z"}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.worktree = os.path.join(self.dir, "worktrees", "rgc-ba8")
        self.rigs = os.path.join(self.dir, "rigs")
        for name in ("bead-rgc-ba8.json", "rigs.json"):
            self.copy(name, name)
        self.copy("events-rgc-ba8.jsonl", "events.jsonl")
        self.write("sessions.json", {"sessions": [
            {"id": "pi-wisp-l29hq", "name": LEAD, "alias": "rematu-lead", "template": LEAD,
             "state": "active"},
            {"id": "pi-wisp-ii1jc7", "template": "rematu-gascity/gc.run-operator", "state": "active"}]})
        # The importing rig is rematu; rematu-gascity is the child rig its lead dispatches into.
        self.env = {
            "PATH": os.environ.get("PATH", ""), "HOME": self.dir, "FAKE_DIR": self.dir,
            "GC_BIN": os.path.join(FIX, "fake-gc"), "GC_RIG": "rematu",
            "GC_OVERSIGHT_CHILD_RIGS": "rematu-gascity",
            "GC_PACK_STATE_DIR": os.path.join(self.dir, "state"),
        }

    def copy(self, src, dst):
        with open(os.path.join(FIX, src)) as f:
            text = f.read()
        text = text.replace("$WORKTREE", self.worktree).replace("$RIG", os.path.join(self.rigs, "rematu-gascity"))
        text = text.replace('"/rigs/', '"' + self.rigs + "/")
        with open(os.path.join(self.dir, dst), "w") as f:
            f.write(text)

    def write(self, name, doc):
        with open(os.path.join(self.dir, name), "w") as f:
            json.dump(doc, f)

    def read(self, name):
        with open(os.path.join(self.dir, name)) as f:
            return json.load(f)

    def edit(self, name, fn):
        doc = self.read(name)
        fn(doc)
        self.write(name, doc)

    def events(self, *evs):
        with open(os.path.join(self.dir, "events.jsonl"), "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in evs))

    def bead(self, bead_id, **fields):
        b = dict({"id": bead_id, "title": "t " + bead_id, "status": "closed", "issue_type": "task",
                  "created_by": LEAD, "closed_at": "2026-11-19T05:00:00Z", "metadata": {}}, **fields)
        self.write("bead-%s.json" % bead_id, [b])

    def run_watch(self, *args, **env):
        p = subprocess.run([sys.executable, "-B", SCRIPT] + list(args),
                           capture_output=True, text=True, env=dict(self.env, **env), timeout=60)
        return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]), p.stderr

    def jsonl(self, name):
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(line) for line in f]

    def delivered(self):
        return self.jsonl("delivered.jsonl")

    def calls(self, *prefix):
        return [c for c in self.jsonl("calls.jsonl") if c[:len(prefix)] == list(prefix)]


class TestRgcBa8(Base):
    def test_rgc_ba8_close_is_mailed_to_the_lead(self):
        rc, out, err = self.run_watch()
        self.assertEqual(rc, 0, err)
        mails = self.delivered()
        self.assertEqual(len(mails), 1, mails)
        m = mails[0]
        self.assertEqual(m["to"], LEAD)
        self.assertIn("--notify", m["args"])
        self.assertEqual(m["dedup"], "dispatched-close:rgc-ba8:2026-11-19T04:21:03Z")
        self.assertTrue(subject(m).startswith(PREFIX + "rgc-ba8 Repoint Claude's statusLine"), subject(m))
        text = body(m)
        self.assertIn("Bead: rgc-ba8 (rematu-gascity)", text)
        self.assertIn("Closed: 2026-11-19T04:21:03Z by pi-wisp-ii1jc7", text,
                      "the closing session, not the cache re-emit")
        self.assertIn("Outcome: pass", text)
        self.assertIn("Close reason: Implementation verified: commit c6d1616", text)
        self.assertIn("gc bd show rgc-ba8", text)
        self.assertEqual(out["notified"], ["rgc-ba8"])
        self.assertEqual(out["rigs"], ["rematu", "rematu-gascity"])
        self.assertEqual(out["closes"], 1, "both bead.closed events are one close")
        self.assertEqual(self.calls("gc", "bd", "show"), [["gc", "bd", "show", "rgc-ba8", "--json"]])

    def test_the_mail_says_an_order_sent_it(self):
        self.run_watch()
        text = body(self.delivered()[0])
        self.assertIn("Automated notice from the oversight-rig dispatched-close-watch order, not from a person", text)
        self.assertIn("sender shows as human", text)


class TestWhichRigs(Base):
    def test_child_rig_is_not_watched_without_the_var(self):
        rc, out, _ = self.run_watch(GC_OVERSIGHT_CHILD_RIGS="")
        self.assertEqual(rc, 0)
        self.assertEqual(out["rigs"], ["rematu"])
        self.assertEqual(self.calls("gc", "bd"), [])
        self.assertEqual(self.delivered(), [])

    def test_child_rigs_flag(self):
        rc, out, _ = self.run_watch("--child-rigs", "rematu-gascity", GC_OVERSIGHT_CHILD_RIGS="")
        self.assertEqual(rc, 0)
        self.assertEqual(out["notified"], ["rgc-ba8"])

    def test_lead_address_follows_gc_rig(self):
        self.edit("bead-rgc-ba8.json", lambda b: b[0].update({"created_by": "feedlot/oversight-rig.project-lead"}))
        rc, out, _ = self.run_watch(GC_RIG="feedlot", GC_OVERSIGHT_CHILD_RIGS="rematu-gascity")
        self.assertEqual(rc, 0)
        self.assertEqual(out["to"], "feedlot/oversight-rig.project-lead")
        self.assertEqual(self.delivered()[0]["to"], "feedlot/oversight-rig.project-lead")

    def test_city_level_instance_skips(self):
        rc, out, _ = self.run_watch(GC_RIG="", GC_OVERSIGHT_CHILD_RIGS="")
        self.assertEqual(rc, 0)
        self.assertTrue(out["ok"])
        self.assertIn("skipped", out)
        self.assertEqual(self.jsonl("calls.jsonl"), [])

    def test_unknown_child_rig_exits_2(self):
        rc, out, _ = self.run_watch(GC_OVERSIGHT_CHILD_RIGS="rematu-gascity,nope")
        self.assertEqual(rc, 2)
        self.assertIn("unknown rig nope", out["error"])


class TestWhoIsTold(Base):
    def test_a_close_is_mailed_once(self):
        self.run_watch()
        rc, out, _ = self.run_watch()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(len(self.calls("gc", "mail", "send")), 1, "the second run knows it was handled")
        self.assertEqual(out["notified"], [])

    def test_without_state_the_mail_dedup_still_holds(self):
        self.run_watch(GC_PACK_STATE_DIR="")
        self.run_watch(GC_PACK_STATE_DIR="")
        keys = [c[c.index("--dedup") + 1] for c in self.calls("gc", "mail", "send")]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(self.delivered()), 1)

    def test_plain_task_in_the_importing_rig_is_mailed(self):
        self.events(event("gr-h0qo.5"))
        self.bead("gr-h0qo.5", close_reason="done")
        rc, out, _ = self.run_watch()
        self.assertEqual(rc, 0)
        m = self.delivered()[0]
        self.assertTrue(subject(m).startswith(PREFIX + "gr-h0qo.5 "))
        self.assertIn("Bead: gr-h0qo.5 (rematu)", body(m))
        self.assertIn("Close reason: done", body(m))

    def test_a_bead_created_under_the_lead_alias_is_mailed(self):
        self.events(event("gr-al1"))
        self.bead("gr-al1", created_by="rematu-lead")
        self.run_watch()
        self.assertEqual([m["to"] for m in self.delivered()], [LEAD])

    def test_beads_the_lead_did_not_create_are_ignored(self):
        self.edit("bead-rgc-ba8.json", lambda b: b[0].update({"created_by": "rematu/gc.mayor"}))
        rc, out, _ = self.run_watch()
        self.assertEqual(rc, 0)
        self.assertEqual(self.delivered(), [])

    def test_a_close_by_the_lead_itself_is_not_mailed_back(self):
        self.events(event("gr-ug4c.1", actor="pi-wisp-l29hq"))
        self.bead("gr-ug4c.1")
        self.run_watch()
        self.assertEqual(self.delivered(), [])

    def test_a_close_under_the_lead_name_is_not_mailed_back(self):
        self.events(event("gr-tnq6", actor=LEAD))
        self.bead("gr-tnq6")
        self.run_watch()
        self.assertEqual(self.delivered(), [])

    def test_a_reopened_bead_is_not_mailed(self):
        self.edit("bead-rgc-ba8.json", lambda b: b[0].update({"status": "open"}))
        self.run_watch()
        self.assertEqual(self.delivered(), [])

    def test_other_rigs_and_plumbing_are_not_even_read(self):
        self.events(event("fl-abc"), event("pi-wisp-x1"),
                    event("rgc-cv1", issue_type="convoy"),
                    event("rgc-st1", metadata={"gc.step_id": "do-work.implement"}),
                    event("gr-wf1", metadata={"gc.kind": "workflow"}),
                    event("gr-syn", metadata={"gc.synthetic": "true"}))
        rc, out, _ = self.run_watch()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls("gc", "bd"), [])
        self.assertEqual(self.delivered(), [])

    def test_recipient_can_be_set(self):
        self.run_watch("--to", "other/lead")
        self.assertEqual(self.delivered(), [])  # rgc-ba8 was created by rematu's lead, not other/lead


class TestFailsLoud(Base):
    def test_unreadable_events_exit_2(self):
        open(os.path.join(self.dir, "FAIL-events"), "w").close()
        rc, out, err = self.run_watch()
        self.assertEqual(rc, 2)
        self.assertFalse(out["ok"])
        self.assertIn("cannot read closes", err)

    def test_unknown_rig_exits_2(self):
        rc, out, err = self.run_watch("--rigs", "rematu,nope")
        self.assertEqual(rc, 2)
        self.assertIn("unknown rig nope", out["error"])

    def test_unreadable_bead_exits_2(self):
        os.remove(os.path.join(self.dir, "bead-rgc-ba8.json"))
        rc, out, err = self.run_watch()
        self.assertEqual(rc, 2)
        self.assertIn("rgc-ba8: gc bd show", err)

    def test_failed_mail_exits_2_and_is_retried(self):
        open(os.path.join(self.dir, "FAIL-mail"), "w").close()
        rc, out, _ = self.run_watch()
        self.assertEqual(rc, 2)
        os.remove(os.path.join(self.dir, "FAIL-mail"))
        rc, out, _ = self.run_watch()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.delivered()), 1)

    def test_dry_run_sends_nothing(self):
        rc, out, err = self.run_watch("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls("gc", "mail"), [])
        self.assertIn(PREFIX + "rgc-ba8", err)


class TestReadOnly(Base):
    def test_only_reads_and_mails(self):
        self.run_watch()
        self.run_watch(GC_PACK_STATE_DIR="")
        for c in self.jsonl("calls.jsonl"):
            words = [x for x in c if not x.startswith("-")]
            self.assertTrue(any(words[:len(a)] == a for a in ALLOWED), c)


@unittest.skipUnless(tomllib, "tomllib needs Python 3.11")
class TestOrder(unittest.TestCase):
    def test_order_fires_on_every_close_for_the_importing_rig(self):
        with open(os.path.join(PACK, "orders", "dispatched-close-watch.toml"), "rb") as f:
            order = tomllib.load(f)["order"]
        self.assertEqual(order["trigger"], "event")
        self.assertEqual(order["on"], "bead.closed")
        self.assertNotIn("scope", order, "rig-scoped: each importing rig tells its own lead")
        self.assertIn("$GC_PACK_DIR/assets/scripts/dispatched-close-watch.py", order["exec"])
        self.assertNotIn("--rigs", order["exec"], "rigs come from $GC_RIG, not a hardcoded list")


if __name__ == "__main__":
    unittest.main()
