"""oneflight 确定性请求合并模拟引擎。

没有线程与 sleep，不读系统时钟：逻辑时钟只由脚本事件与 duration 推进。
同刻优先级固定：到期超时 0 → 脚本取消 1 → 到点完成 2 → 到达 3。
真执行占用左闭右开区间 [开始, 开始+duration)。
"""
from __future__ import annotations

import heapq
import json
from pathlib import Path

PRIO_TIMEOUT = 0
PRIO_CANCEL = 1
PRIO_COMPLETE = 2
PRIO_ARRIVE = 3

SUMMARY_KEYS = ("arrive", "cancel", "err", "exec", "merge", "ok", "reject", "return", "xcancel")

_DETAIL_WAITER_LIMIT = (("reason", "waiter_limit"),)
_DETAIL_INFLIGHT_LIMIT = (("reason", "inflight_limit"),)
_DETAIL_NO_WAITERS = (("reason", "no_waiters"),)
_DETAIL_REASON = {"user": (("reason", "user"),), "timeout": (("reason", "timeout"),)}
_DETAIL_RESULT = {"ok": (("result", "ok"),), "err": (("result", "err"),)}


class Record:
    """一条轨迹记录；line() 输出 4.2 的 6 段格式，data 承载页面用的结构化字段。"""

    __slots__ = ("t", "kind", "req", "key", "exec_id", "detail", "data")

    def __init__(self, t, kind, req, key, exec_id, detail=(), data=None):
        self.t = t
        self.kind = kind
        self.req = req
        self.key = key
        self.exec_id = exec_id
        self.detail = tuple(detail)
        self.data = data

    def line(self):
        if self.detail:
            detail = " ".join("%s=%s" % (k, v) for k, v in self.detail)
        else:
            detail = "-"
        return "%04d|%s|%s|%s|%s|%s" % (self.t, self.kind, self.req, self.key, self.exec_id, detail)


def load_script(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(script):
    """跑一个脚本，返回 Record 列表（末条为 SUMMARY）。"""
    return Engine(script).run()


class Engine:
    def __init__(self, script):
        self.max_waiters = script["max_waiters"]
        self.max_inflight = script["max_inflight"]
        self.default_duration = script["default_duration"]
        self.default_timeout = script["default_timeout"]
        self.records = []
        self.counts = {k: 0 for k in SUMMARY_KEYS}
        self.heap = []
        self.seq = 0
        self.requests = {}  # rid -> [eid, waiting]
        self.execs = {}     # eid -> {"key", "members", "waiting", "result", "status"}
        self.inflight = {}  # key -> eid（仅 running 的真执行）
        self.running = 0
        self.next_exec = 0
        for ev in script["events"]:
            if "arrive" in ev:
                self._push(ev["t"], PRIO_ARRIVE, ("arrive", ev))
            else:
                self._push(ev["t"], PRIO_CANCEL, ("cancel", ev["cancel"]))

    def run(self):
        heap = self.heap
        while heap:
            t, _prio, _seq, (kind, payload) = heapq.heappop(heap)
            if kind == "arrive":
                self._arrive(t, payload)
            elif kind == "timeout":
                self._detach(t, payload, "timeout")
            elif kind == "cancel":
                self._detach(t, payload, "user")
            else:
                self._complete(t, payload)
        last_t = self.records[-1].t if self.records else 0
        self._emit(last_t, "SUMMARY", "-", "-", "-",
                   tuple((k, self.counts[k]) for k in SUMMARY_KEYS))
        return self.records

    def _push(self, t, prio, payload):
        heapq.heappush(self.heap, (t, prio, self.seq, payload))
        self.seq += 1

    def _emit(self, t, kind, req, key, exec_id, detail=(), data=None):
        self.records.append(Record(t, kind, req, key, exec_id, detail, data))

    def _attach(self, t, rid, eid, ev):
        timeout = ev.get("timeout", self.default_timeout)
        self.requests[rid] = [eid, True]
        self._push(t + timeout, PRIO_TIMEOUT, ("timeout", rid))

    def _arrive(self, t, ev):
        rid = ev["arrive"]
        key = ev["key"]
        self.counts["arrive"] += 1
        self._emit(t, "ARRIVE", rid, key, "-")
        eid = self.inflight.get(key)
        if eid is not None:
            ex = self.execs[eid]
            if ex["waiting"] >= self.max_waiters:
                self.counts["reject"] += 1
                self._emit(t, "REJECT", rid, key, "-", _DETAIL_WAITER_LIMIT)
                return
            ex["members"].append(rid)
            ex["waiting"] += 1
            self._attach(t, rid, eid, ev)
            self.counts["merge"] += 1
            self._emit(t, "MERGE", rid, key, eid, (("pos", ex["waiting"]),))
            return
        if self.running >= self.max_inflight:
            self.counts["reject"] += 1
            self._emit(t, "REJECT", rid, key, "-", _DETAIL_INFLIGHT_LIMIT)
            return
        self.next_exec += 1
        eid = "e%d" % self.next_exec
        duration = ev.get("duration", self.default_duration)
        self.execs[eid] = {
            "key": key,
            "members": [rid],
            "waiting": 1,
            "result": ev.get("result", "ok"),
            "status": "running",
        }
        self.inflight[key] = eid
        self.running += 1
        self.counts["exec"] += 1
        self._attach(t, rid, eid, ev)
        self._push(t + duration, PRIO_COMPLETE, ("complete", eid))
        self._emit(t, "START", "-", key, eid, (("duration", duration),), data={"req": rid})

    def _detach(self, t, rid, reason):
        req = self.requests[rid]
        if not req[1]:
            return
        req[1] = False
        eid = req[0]
        ex = self.execs[eid]
        ex["waiting"] -= 1
        self.counts["cancel"] += 1
        self._emit(t, "CANCEL", rid, ex["key"], eid, _DETAIL_REASON[reason])
        if ex["status"] == "running" and ex["waiting"] == 0:
            ex["status"] = "cancelled"
            del self.inflight[ex["key"]]
            self.running -= 1
            self.counts["xcancel"] += 1
            self._emit(t, "XCANCEL", "-", ex["key"], eid, _DETAIL_NO_WAITERS)

    def _complete(self, t, eid):
        ex = self.execs[eid]
        if ex["status"] != "running":
            return  # 已 XCANCEL 的完成事件直接作废
        ex["status"] = "done"
        del self.inflight[ex["key"]]
        self.running -= 1
        result = ex["result"]
        self.counts[result] += 1
        detail = _DETAIL_RESULT[result]
        self._emit(t, "XEND", "-", ex["key"], eid, detail)
        for rid in ex["members"]:
            req = self.requests[rid]
            if req[1]:
                req[1] = False
                self.counts["return"] += 1
                self._emit(t, "RETURN", rid, ex["key"], eid, detail)
