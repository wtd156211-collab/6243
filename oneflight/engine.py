"""oneflight 请求合并：确定性模拟引擎。

逻辑时钟只由脚本事件与 duration 推进；不读系统时钟、不用线程。
同刻优先级固定：到期超时 0 -> 脚本取消 1 -> 到点完成 2 -> 到达 3。
"""

import heapq
import json

_PRIO_TIMEOUT = 0
_PRIO_CANCEL = 1
_PRIO_COMPLETE = 2
_PRIO_ARRIVE = 3

SUMMARY_KEYS = (
    "arrive", "cancel", "err", "exec", "merge", "ok", "reject", "return", "xcancel",
)


class Request:
    __slots__ = ("rid", "key", "arrive_t", "deadline", "exec_id",
                 "state", "end_t", "outcome")

    def __init__(self, rid, key, arrive_t, deadline):
        self.rid = rid
        self.key = key
        self.arrive_t = arrive_t
        self.deadline = deadline
        self.exec_id = None       # 并入/发起的真执行编号；被拒为 None
        self.state = "arrived"    # arrived/waiting/returned/cancelled/rejected
        self.end_t = None
        self.outcome = None       # ok/err/user/timeout/waiter_limit/inflight_limit


class Exec:
    __slots__ = ("eid", "key", "start_t", "duration", "result",
                 "members", "attached", "state", "end_t")

    def __init__(self, eid, key, start_t, duration, result):
        self.eid = eid
        self.key = key
        self.start_t = start_t
        self.duration = duration
        self.result = result
        self.members = []         # 挂载顺序（发起者最前），含已离开的
        self.attached = 0         # 当前还挂着的请求数
        self.state = "running"    # running/done/cancelled
        self.end_t = None


class SimResult:
    def __init__(self, name, records, requests, execs, counts, last_t):
        self.name = name
        self.records = records
        self.requests = requests  # 到达顺序
        self.execs = execs        # 编号顺序
        self.counts = counts
        self.last_t = last_t

    @property
    def trace_text(self):
        return "\n".join(self.records) + "\n"


def load_script(path):
    with open(path, "r", encoding="ascii") as fh:
        return json.load(fh)


def simulate(script):
    """跑一份时序脚本，返回 SimResult。同一份脚本结果逐字节确定。"""
    max_waiters = script["max_waiters"]
    max_inflight = script["max_inflight"]
    default_duration = script["default_duration"]
    default_timeout = script["default_timeout"]

    heap = []
    seq = 0
    for ev in script["events"]:
        prio = _PRIO_ARRIVE if "arrive" in ev else _PRIO_CANCEL
        heapq.heappush(heap, (ev["t"], prio, seq, ev))
        seq += 1

    records = []
    requests = []
    requests_by_id = {}
    execs = []
    inflight = {}   # key -> 正在跑的 Exec
    running = 0
    last_t = 0
    counts = {key: 0 for key in SUMMARY_KEYS}

    def rec(t, kind, rid, key, eid, detail):
        nonlocal last_t
        last_t = t
        records.append("%04d|%s|%s|%s|%s|%s" % (t, kind, rid, key, eid, detail))

    def release(ex):
        nonlocal running
        del inflight[ex.key]
        running -= 1

    def detach(req, t, reason):
        # 请求脱离等待；挂载清零时真执行当场 XCANCEL 并释放键与配额
        req.state = "cancelled"
        req.end_t = t
        req.outcome = reason
        ex = execs[req.exec_id - 1]
        ex.attached -= 1
        eid = "e%d" % ex.eid
        rec(t, "CANCEL", req.rid, req.key, eid, "reason=" + reason)
        counts["cancel"] += 1
        if ex.attached == 0:
            ex.state = "cancelled"
            ex.end_t = t
            rec(t, "XCANCEL", "-", ex.key, eid, "reason=no_waiters")
            counts["xcancel"] += 1
            release(ex)

    def complete(ex, t):
        if ex.state != "running":
            return  # 已 XCANCEL，惰性作废
        ex.state = "done"
        ex.end_t = t
        eid = "e%d" % ex.eid
        rec(t, "XEND", "-", ex.key, eid, "result=" + ex.result)
        counts[ex.result] += 1
        for req in ex.members:  # 挂载顺序，发起者最前
            if req.state == "waiting":
                req.state = "returned"
                req.end_t = t
                req.outcome = ex.result
                rec(t, "RETURN", req.rid, req.key, eid, "result=" + ex.result)
                counts["return"] += 1
        release(ex)

    def arrive(ev, t):
        nonlocal running, seq
        rid = ev["arrive"]
        key = ev["key"]
        req = Request(rid, key, t, t + ev.get("timeout", default_timeout))
        requests.append(req)
        requests_by_id[rid] = req
        rec(t, "ARRIVE", rid, key, "-", "-")
        counts["arrive"] += 1

        ex = inflight.get(key)
        if ex is not None:
            if ex.attached >= max_waiters:
                req.state = "rejected"
                req.end_t = t
                req.outcome = "waiter_limit"
                rec(t, "REJECT", rid, key, "-", "reason=waiter_limit")
                counts["reject"] += 1
                return
            ex.members.append(req)
            ex.attached += 1
            req.state = "waiting"
            req.exec_id = ex.eid
            rec(t, "MERGE", rid, key, "e%d" % ex.eid, "pos=%d" % ex.attached)
            counts["merge"] += 1
        else:
            if running >= max_inflight:
                req.state = "rejected"
                req.end_t = t
                req.outcome = "inflight_limit"
                rec(t, "REJECT", rid, key, "-", "reason=inflight_limit")
                counts["reject"] += 1
                return
            ex = Exec(len(execs) + 1, key, t,
                      ev.get("duration", default_duration),
                      ev.get("result", "ok"))
            execs.append(ex)
            inflight[key] = ex
            running += 1
            ex.members.append(req)
            ex.attached = 1
            req.state = "waiting"
            req.exec_id = ex.eid
            rec(t, "START", "-", key, "e%d" % ex.eid, "duration=%d" % ex.duration)
            counts["exec"] += 1
            heapq.heappush(heap, (t + ex.duration, _PRIO_COMPLETE, seq, ex))
            seq += 1
        heapq.heappush(heap, (req.deadline, _PRIO_TIMEOUT, seq, req))
        seq += 1

    while heap:
        t, prio, _, ev = heapq.heappop(heap)
        if prio == _PRIO_ARRIVE:
            arrive(ev, t)
        elif prio == _PRIO_CANCEL:
            req = requests_by_id[ev["cancel"]]
            if req.state == "waiting":
                detach(req, t, "user")
        elif prio == _PRIO_TIMEOUT:
            if ev.state == "waiting":
                detach(ev, t, "timeout")
        else:
            complete(ev, t)

    detail = " ".join("%s=%d" % (key, counts[key]) for key in SUMMARY_KEYS)
    rec(last_t, "SUMMARY", "-", "-", "-", detail)
    return SimResult(script["name"], records, requests, execs, counts, last_t)
