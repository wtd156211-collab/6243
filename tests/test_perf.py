import time
import tracemalloc
import unittest

from oneflight.engine import run

ARRIVALS = 50000
KEYS = 10000


def big_script():
    events = []
    for i in range(ARRIVALS):
        # 每 4 个连续到达共用一个键：前几个合并，键随时间滚动复用
        events.append({"t": i, "arrive": "r%d" % (i + 1), "key": "k%d" % (i // 4 % KEYS)})
    return {
        "name": "big",
        "max_waiters": 4,
        "max_inflight": 64,
        "default_duration": 3,
        "default_timeout": 50,
        "events": events,
    }


class PerfTest(unittest.TestCase):
    def test_trace_time_and_memory_budget(self):
        script = big_script()
        tracemalloc.start()
        baseline, _ = tracemalloc.get_traced_memory()
        start = time.perf_counter()
        records = run(script)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(elapsed, 2.0, "trace 超过 2 秒预算: %.2fs" % elapsed)
        self.assertLess(peak - baseline, 64 * 1024 * 1024,
                        "trace 超过 64 MiB 内存预算: %d B" % (peak - baseline))
        self.assertEqual(records[-1].kind, "SUMMARY")


if __name__ == "__main__":
    unittest.main()
