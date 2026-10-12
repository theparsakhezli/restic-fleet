"""Unit tests for restic-fleet-server (the backup server's collect/maintain tasks). restic itself is
replaced by a fake, so nothing here touches a real repository.

Run: python -m unittest discover -s tests/unit -v
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "roles" / "server_tasks" / "files" / "restic-fleet-server"


def load_module():
    loader = importlib.machinery.SourceFileLoader("rfs", str(SRC))
    spec = importlib.util.spec_from_loader("rfs", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def rfc3339(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".123456789Z"


class ServerTasksTest(unittest.TestCase):
    def setUp(self):
        self.rfs = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        self.passes = os.path.join(self.tmp.name, "repos")
        os.makedirs(os.path.join(self.data, "web1", "snapshots"))
        os.makedirs(self.passes)
        pathlib.Path(self.passes, "web1.pass").write_text("x")
        self.cfg = {"data_dir": self.data, "cache_dir": self.tmp.name, "repo_pass_dir": self.passes,
                    "clients": {"web1": {"daily": 7, "weekly": 4}}}
        self.snapshots = []          # (id, claimed time, upload time)
        self.calls = []
        self.posted = []
        self.rfs.restic = self.fake_restic
        self.rfs.post = lambda cfg, payload: self.posted.append(payload)
        self.rfs.dir_size = lambda path: 4096     # st_blocks is Linux-only; not under test here
        self.dumps = []             # snapshot ids streamed by the restore test
        self.dump_result = (0, 12345, "")
        self.rfs.restic_stream = self.fake_stream

    def tearDown(self):
        self.tmp.cleanup()

    def add_snapshot(self, sid: str, claimed: float, uploaded: float):
        path = os.path.join(self.data, "web1", "snapshots", sid)
        pathlib.Path(path).write_text("{}")
        os.utime(path, (uploaded, uploaded))
        self.snapshots.append({"id": sid, "time": rfc3339(claimed)})

    def fake_restic(self, env, *args, timeout=0):
        self.calls.append(args[0])
        out = json.dumps(self.snapshots) if args[0] == "snapshots" else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    def fake_stream(self, env, *args, timeout=0):
        self.dumps.append(args[-2])
        return self.dump_result

    # ------------------------------------------------------------------ parsing & validation
    def test_parse_time_formats(self):
        p = self.rfs.parse_time
        self.assertEqual(p("2020-01-01T00:00:00Z"), 1577836800.0)
        self.assertEqual(p("2020-01-01T01:00:00+01:00"), 1577836800.0)   # no fraction: Go drops ".0"
        self.assertAlmostEqual(p("2020-01-01T00:00:00.5-00:00"), 1577836800.5)
        self.assertAlmostEqual(p("2019-12-31T19:00:00.123456789-05:00"), 1577836800.123456789)
        with self.assertRaises(ValueError):
            p("yesterday")

    def test_keep_args(self):
        self.assertEqual(self.rfs.keep_args({"daily": 7, "hourly": 0, "within": "30d"}),
                         ["--keep-daily", "7", "--keep-within", "30d"])
        for bad in ({"dialy": 7}, {"daily": "7; rm -rf /"}, {"daily": -2}, {"within": "--all"}, {"daily": True}, []):
            with self.assertRaises(ValueError, msg=bad):
                self.rfs.keep_args(bad)

    # ------------------------------------------------------------------ maintenance
    def test_normal_maintenance_forgets_and_checks(self):
        now = time.time()
        self.add_snapshot("a" * 64, now - 7200, now - 3600)        # 1 h backup run
        self.assertEqual(self.rfs.maintain(self.cfg), 0)
        self.assertEqual([c for c in self.calls if c != "snapshots"], ["unlock", "forget", "check"])
        self.assertTrue(self.posted[0]["maintenance"]["web1"]["ok"])

    def test_backdated_snapshot_blocks_retention(self):
        now = time.time()
        self.add_snapshot("a" * 64, now - 7200, now - 3600)
        self.add_snapshot("b" * 64, now - 200 * 86400, now - 60)   # claims 200 days ago, uploaded now
        self.assertEqual(self.rfs.maintain(self.cfg), 1)
        self.assertNotIn("forget", self.calls, "nothing may be forgotten while snapshot times are suspect")
        self.assertIn("check", self.calls, "the integrity check still runs")
        result = self.posted[0]["maintenance"]["web1"]
        self.assertFalse(result["ok"])
        self.assertIn("bbbbbbbb", result["message"])

    def test_future_snapshot_blocks_retention(self):
        now = time.time()
        self.add_snapshot("c" * 64, now + 30 * 86400, now - 60)
        self.assertEqual(self.rfs.maintain(self.cfg), 1)
        self.assertNotIn("forget", self.calls)

    def test_invalid_retention_blocks_retention(self):
        self.cfg["clients"]["web1"] = {"dialy": 7}
        self.assertEqual(self.rfs.maintain(self.cfg), 1)
        self.assertNotIn("forget", self.calls)
        self.assertIn("unknown retention key", self.posted[0]["maintenance"]["web1"]["message"])

    def test_time_check_can_be_disabled(self):
        now = time.time()
        self.add_snapshot("b" * 64, now - 200 * 86400, now - 60)
        self.cfg["verify_snapshot_times"] = False
        self.assertEqual(self.rfs.maintain(self.cfg), 0)
        self.assertIn("forget", self.calls)

    def test_collect_survives_odd_times(self):
        now = time.time()
        self.add_snapshot("a" * 64, now - 3600, now - 3500)
        self.add_snapshot("d" * 64, now + 365 * 86400, now)        # future-dated
        self.rfs.collect(self.cfg)
        info = self.posted[-1]["repos"]["web1"]
        self.assertEqual(info["snapshots"], 2)
        self.assertLess(info["last_snapshot"], now, "a future-dated snapshot must not hide an overdue host")

    # ------------------------------------------------------------------ restore test
    def test_restore_test_runs_then_waits(self):
        now = time.time()
        self.add_snapshot("a" * 64, now - 7200, now - 3600)
        self.assertEqual(self.rfs.maintain(self.cfg), 0)
        self.assertEqual(self.dumps, ["a" * 64])
        self.assertIn("restore test passed", self.posted[0]["maintenance"]["web1"]["message"])
        self.rfs.maintain(self.cfg)                       # a week later: not due again yet
        self.assertEqual(len(self.dumps), 1)

    def test_failed_restore_test_fails_maintenance_and_retries(self):
        now = time.time()
        self.add_snapshot("a" * 64, now - 7200, now - 3600)
        self.dump_result = (1, 0, "Fatal: ciphertext verification failed")
        self.assertEqual(self.rfs.maintain(self.cfg), 1)
        result = self.posted[0]["maintenance"]["web1"]
        self.assertFalse(result["ok"])
        self.assertIn("ciphertext verification failed", result["message"])
        self.dump_result = (0, 10, "")
        self.assertEqual(self.rfs.maintain(self.cfg), 0, "a failed test is retried at the next run")
        self.assertEqual(len(self.dumps), 2)

    def test_empty_restore_of_nonempty_snapshot_fails(self):
        now = time.time()
        self.add_snapshot("a" * 64, now - 7200, now - 3600)
        self.snapshots[0]["summary"] = {"total_bytes_processed": 5000}
        self.dump_result = (0, 0, "")
        self.assertEqual(self.rfs.maintain(self.cfg), 1)

    def test_restore_test_can_be_disabled(self):
        now = time.time()
        self.add_snapshot("a" * 64, now - 7200, now - 3600)
        self.cfg["restore_test_days"] = 0
        self.assertEqual(self.rfs.maintain(self.cfg), 0)
        self.assertEqual(self.dumps, [])

    def test_grouped_snapshot_listing(self):
        grouped = [{"group_key": {}, "snapshots": [{"id": "e" * 64}]}, {"group_key": {}, "snapshots": [{"id": "f" * 64}]}]
        self.rfs.restic = lambda env, *a, timeout=0: subprocess.CompletedProcess(a, 0, json.dumps(grouped), "")
        ok, note = self.rfs.restore_test(self.cfg, {})
        self.assertTrue(ok, note)
        self.assertEqual(self.dumps, ["e" * 64, "f" * 64])


if __name__ == "__main__":
    unittest.main()
