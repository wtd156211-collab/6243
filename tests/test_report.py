import json
import re
import unittest
from pathlib import Path

from oneflight.report import build_report

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "samples" / "scripts"
EXPECTED = ROOT / "samples" / "expected"

DATA_RE = re.compile(
    r'<script type="application/json" id="oneflight-data">(.*?)</script>', re.S)


def expected_summary(name):
    last = (EXPECTED / (name + ".txt")).read_text(encoding="ascii").splitlines()[-1]
    detail = last.split("|", 5)[5]
    return {k: int(v) for k, v in (pair.split("=") for pair in detail.split(" "))}


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = build_report(SCRIPTS)
        cls.payload = json.loads(DATA_RE.search(cls.html).group(1))

    def test_deterministic_byte_identical(self):
        self.assertEqual(build_report(SCRIPTS), build_report(SCRIPTS))

    def test_self_contained_no_network(self):
        self.assertNotIn("fetch(", self.html)
        self.assertNotIn("src=", self.html)
        self.assertNotIn("href=", self.html)
        self.assertNotIn("@import", self.html)
        self.assertNotIn("url(", self.html)
        # 唯一的 http 串应是 SVG 命名空间标识符，不是网络引用
        self.assertEqual(self.html.count("http"), self.html.count("http://www.w3.org/2000/svg"))

    def test_summary_matches_trace(self):
        scripts = {s["name"]: s for s in self.payload["scripts"]}
        self.assertEqual(len(scripts), 6)
        for name, model in scripts.items():
            with self.subTest(script=name):
                self.assertEqual(model["summary"], expected_summary(name))

    def test_model_counts_consistent_with_trace(self):
        for model in self.payload["scripts"]:
            with self.subTest(script=model["name"]):
                trace = model["trace"]
                self.assertEqual(len(model["requests"]), model["summary"]["arrive"])
                self.assertEqual(len(model["execs"]), model["summary"]["exec"])
                self.assertEqual(trace[-1].split("|")[1], "SUMMARY")
                self.assertEqual(int(trace[-1].split("|")[0]), model["max_t"])

    def test_page_contains_required_sections(self):
        for marker in ("请求", "真执行", "汇总", "oneflight-data"):
            self.assertIn(marker, self.html)


if __name__ == "__main__":
    unittest.main()
