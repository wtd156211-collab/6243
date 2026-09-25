import hashlib
import unittest
from pathlib import Path

from oneflight.engine import SUMMARY_KEYS, load_script, run

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "samples" / "scripts"
EXPECTED = ROOT / "samples" / "expected"


def trace_text(script_path):
    records = run(load_script(script_path))
    return "".join(rec.line() + "\n" for rec in records)


class TraceMatchesExpectedTest(unittest.TestCase):
    def test_all_samples_byte_identical(self):
        script_paths = sorted(SCRIPTS.glob("*.json"))
        self.assertEqual(len(script_paths), 6)
        for sp in script_paths:
            with self.subTest(script=sp.name):
                expected = (EXPECTED / (sp.stem + ".txt")).read_bytes()
                self.assertEqual(trace_text(sp).encode("ascii"), expected)

    def test_trace_is_deterministic(self):
        for sp in sorted(SCRIPTS.glob("*.json")):
            with self.subTest(script=sp.name):
                first = hashlib.sha256(trace_text(sp).encode("ascii")).hexdigest()
                second = hashlib.sha256(trace_text(sp).encode("ascii")).hexdigest()
                self.assertEqual(first, second)


class SummarySemanticsTest(unittest.TestCase):
    def _summary(self, name):
        records = run(load_script(SCRIPTS / (name + ".json")))
        last = records[-1]
        self.assertEqual(last.kind, "SUMMARY")
        return dict(last.detail)

    def test_summary_keys_alphabetical(self):
        records = run(load_script(SCRIPTS / "limits.json"))
        keys = [k for k, _ in records[-1].detail]
        self.assertEqual(keys, sorted(SUMMARY_KEYS))

    def test_waiter_cancel_xcancel_releases_key(self):
        summary = self._summary("waiter_cancel")
        self.assertEqual(summary["xcancel"], 1)
        self.assertEqual(summary["exec"], 2)  # XCANCEL 已计入真执行次数
        self.assertEqual(summary["ok"], 1)    # 中止的那次不算 ok

    def test_timeout_beats_completion_at_same_instant(self):
        summary = self._summary("timeout")
        self.assertEqual(summary["cancel"], 4)
        self.assertEqual(summary["return"], 1)

    def test_rejects_hold_no_quota(self):
        summary = self._summary("limits")
        self.assertEqual(summary["reject"], 3)
        self.assertEqual(summary["exec"], 3)


if __name__ == "__main__":
    unittest.main()
