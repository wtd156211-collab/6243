"""oneflight 验收测试：只读 samples/，标准库 unittest。"""

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "samples" / "scripts"
EXPECTED = ROOT / "samples" / "expected"

sys.path.insert(0, str(ROOT))

from oneflight import simulate  # noqa: E402


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "oneflight", *args],
                          cwd=ROOT, capture_output=True, text=True)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TraceSamplesTest(unittest.TestCase):
    """六个样例脚本：trace 与 samples/expected 逐字节相同。"""

    def test_trace_matches_expected(self):
        for script_path in sorted(SCRIPTS.glob("*.json")):
            name = script_path.stem
            with self.subTest(script=name):
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "out.txt"
                    proc = run_cli("trace", str(script_path), str(out))
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(out.read_bytes(),
                                     (EXPECTED / (name + ".txt")).read_bytes())

    def test_trace_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            outs = []
            for i in (1, 2):
                out = Path(tmp) / ("t%d.txt" % i)
                proc = run_cli("trace", str(SCRIPTS / "limits.json"), str(out))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                outs.append(sha256(out))
            self.assertEqual(outs[0], outs[1])


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.report = Path(cls._tmp.name) / "report.html"
        proc = run_cli("report", str(cls.report))
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        cls.html = cls.report.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_report_deterministic(self):
        other = Path(self._tmp.name) / "report2.html"
        proc = run_cli("report", str(other))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.report.read_bytes(), other.read_bytes())

    def test_report_self_contained(self):
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))
        # xmlns 是命名空间标识符不是网络请求；除此之外不得有任何外部引用
        body = self.html.replace('xmlns="http://www.w3.org/2000/svg"', "")
        for banned in ("fetch(", "http://", "https://", "<script", "<link",
                       "@import", "src="):
            self.assertNotIn(banned, body)

    def test_report_summary_matches_trace(self):
        # 页面汇总数字必须与每个脚本的 SUMMARY 行一一对应
        for expected_path in sorted(EXPECTED.glob("*.txt")):
            summary = expected_path.read_text(encoding="ascii").splitlines()[-1]
            detail = summary.split("|", 5)[5]
            pairs = dict(kv.split("=") for kv in detail.split(" "))
            with self.subTest(script=expected_path.stem):
                row = "<td class=\"l\">%s</td>" % expected_path.stem
                pos = self.html.find(row)
                self.assertGreaterEqual(pos, 0)
                for key in ("arrive", "cancel", "err", "exec", "merge",
                            "ok", "reject", "return", "xcancel"):
                    pos = self.html.find("<td>%s</td>" % pairs[key], pos)
                    self.assertGreaterEqual(pos, 0)

    def test_report_sections(self):
        # 4.4 三项：请求行、真执行行、汇总；每个脚本一节
        for script_path in sorted(SCRIPTS.glob("*.json")):
            name = script_path.stem
            with self.subTest(script=name):
                self.assertIn("<h2>%s</h2>" % name, self.html)
        self.assertIn("<th>到达</th><th>结束</th><th>结局</th><th>真执行</th>",
                      self.html)
        self.assertIn("<th>挂载数</th>", self.html)
        self.assertIn('<tr class="total"><td class="l">合计</td>', self.html)
        self.assertIn("<svg", self.html)


def _big_script(n_arrivals=50000, n_keys=2000):
    """确定性放大脚本（不用随机数）：多键交错、含 err 与取消。"""
    events = []
    rid = 0
    for i in range(n_arrivals):
        rid += 1
        ev = {"t": i * 2, "arrive": "r%d" % rid, "key": "k%d" % (i % n_keys)}
        if i % n_keys == 0:  # 每把键的第一个请求当发起者
            ev["duration"] = 30 + (i % 7)
            if i % 11 == 0:
                ev["result"] = "err"
        events.append(ev)
    # 给还在等待的请求补一些用户取消（t 单调不减）
    t = n_arrivals * 2
    for i in range(1, n_arrivals, 997):
        t += 1
        events.append({"t": t, "cancel": "r%d" % i, "reason": "user"})
    return {"name": "big", "max_waiters": 8, "max_inflight": 64,
            "default_duration": 25, "default_timeout": 400, "events": events}


class ScaleTest(unittest.TestCase):
    """规模：5×10^4 到达；trace 时间、峰值内存与确定性。"""

    def test_big_script(self):
        script = _big_script()
        tracemalloc.start()
        base, _ = tracemalloc.get_traced_memory()
        start = time.perf_counter()
        result = simulate(script)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        extra_mib = (peak - base) / 2 ** 20
        print("\nbig: %.2fs, peak +%.1f MiB, %d 行"
              % (elapsed, extra_mib, len(result.records)), file=sys.stderr)
        self.assertLess(elapsed, 2.0)
        self.assertLess(extra_mib, 64.0)
        # 同脚本两遍轨迹逐字节相同
        again = simulate(json.loads(json.dumps(script)))
        self.assertEqual(result.trace_text, again.trace_text)
        # 每个到达的请求都有终态
        self.assertTrue(all(r.end_t is not None for r in result.requests))


if __name__ == "__main__":
    unittest.main()
