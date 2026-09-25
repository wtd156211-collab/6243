"""把全部样例脚本的模拟结果内联成一个自包含 HTML 报告。

页面数据全部来自引擎输出的 Record（轨迹本身），JS 只负责渲染，不重新推导。
"""
from __future__ import annotations

import json
from pathlib import Path

from .engine import SUMMARY_KEYS, load_script, run

SCRIPTS_DIR = Path("samples") / "scripts"


def script_model(name, records):
    """从引擎记录构建页面模型：请求、真执行、汇总、原始轨迹。"""
    requests = []
    req_by_id = {}
    execs = []
    exec_by_id = {}
    summary = {}
    max_t = 0
    for rec in records:
        max_t = rec.t
        kind = rec.kind
        detail = dict(rec.detail)
        if kind == "ARRIVE":
            req = {"id": rec.req, "key": rec.key, "arrive": rec.t,
                   "end": None, "outcome": "-", "exec": "-"}
            req_by_id[rec.req] = req
            requests.append(req)
        elif kind == "START":
            initiator = rec.data["req"]
            ex = {"id": rec.exec_id, "key": rec.key, "start": rec.t,
                  "end": None, "outcome": "-", "duration": int(detail["duration"]),
                  "members": [initiator]}
            exec_by_id[rec.exec_id] = ex
            execs.append(ex)
            req_by_id[initiator]["exec"] = rec.exec_id
        elif kind == "MERGE":
            req_by_id[rec.req]["exec"] = rec.exec_id
            exec_by_id[rec.exec_id]["members"].append(rec.req)
        elif kind == "REJECT":
            req_by_id[rec.req].update(end=rec.t, outcome=detail["reason"])
        elif kind == "XEND":
            exec_by_id[rec.exec_id].update(end=rec.t, outcome=detail["result"])
        elif kind == "XCANCEL":
            exec_by_id[rec.exec_id].update(end=rec.t, outcome="cancelled")
        elif kind == "RETURN":
            req_by_id[rec.req].update(end=rec.t, outcome=detail["result"])
        elif kind == "CANCEL":
            req_by_id[rec.req].update(end=rec.t, outcome=detail["reason"])
        elif kind == "SUMMARY":
            summary = {k: int(v) for k, v in rec.detail}
    return {
        "name": name,
        "summary": summary,
        "execs": execs,
        "requests": requests,
        "max_t": max_t,
        "trace": [rec.line() for rec in records],
    }


def build_report(scripts_dir=SCRIPTS_DIR):
    models = []
    for path in sorted(Path(scripts_dir).glob("*.json"), key=lambda p: p.name):
        script = load_script(path)
        records = run(script)
        models.append(script_model(script.get("name", path.stem), records))
    payload = json.dumps({"keys": list(SUMMARY_KEYS), "scripts": models},
                         ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")
    return _TEMPLATE.replace("__DATA__", payload)


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>oneflight 请求合并轨迹报告</title>
<style>
body{font:14px/1.5 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif;margin:24px;color:#1f2937;background:#fff}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:16px;margin:28px 0 6px;border-top:2px solid #e5e7eb;padding-top:16px}
h3{font-size:13px;margin:14px 0 4px;color:#4b5563}
table{border-collapse:collapse;margin:6px 0;font-variant-numeric:tabular-nums}
th,td{border:1px solid #d1d5db;padding:3px 10px;text-align:left;font-size:13px}
th{background:#f3f4f6}
tr.total td{font-weight:700;background:#f9fafb}
.chart{background:#fcfcfd;border:1px solid #e5e7eb;border-radius:6px;margin:8px 0;display:block}
.axis{stroke:#6b7280;stroke-width:1}
.lane{stroke:#eef0f2;stroke-width:1}
.tick{font-size:10px;fill:#6b7280}
.lane-label{font-size:11px;fill:#374151}
.exec-label{font-weight:700}
.exec-bar{stroke:#111827;stroke-width:1.5}
.req-bar{opacity:.85}
.merge-link{stroke-dasharray:2 3;stroke-width:1;opacity:.55}
.reject-mark{fill:#9ca3af;stroke:#4b5563;stroke-width:1}
.mark{font-size:11px}
.mark.ok{fill:#15803d}.mark.err{fill:#b91c1c}.mark.cancel{fill:#6b7280}.mark.reject{fill:#6b7280}
.kv{font-size:13px;margin:4px 0}
.kv b{display:inline-block;min-width:52px;color:#111827}
.legend{font-size:12px;color:#4b5563;margin:4px 0}
details{margin:8px 0}
summary{cursor:pointer;font-size:13px;color:#4b5563}
pre{background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:8px;overflow:auto;font-size:12px;line-height:1.4}
.meta{font-size:12px;color:#6b7280}
</style>
</head>
<body>
<h1>oneflight 请求合并轨迹报告</h1>
<p class="meta">数据由 oneflight 引擎轨迹直接生成，页面只做渲染；同刻优先级：超时 → 取消 → 完成 → 到达。</p>
<h2>汇总（与 SUMMARY 九口径一致）</h2>
<div id="summary"></div>
<div id="scripts"></div>
<script type="application/json" id="oneflight-data">__DATA__</script>
<script>
(function () {
  "use strict";
  var SVGNS = "http://www.w3.org/2000/svg";
  var data = JSON.parse(document.getElementById("oneflight-data").textContent);
  var KEYS = data.keys;
  var PALETTE = ["#2563eb","#dc2626","#059669","#d97706","#7c3aed","#0891b2","#db2777","#65a30d","#ea580c","#0d9488"];
  var REJECT_COLOR = "#9ca3af";

  function colorFor(eid) {
    if (!eid || eid === "-") { return REJECT_COLOR; }
    return PALETTE[(parseInt(eid.slice(1), 10) - 1) % PALETTE.length];
  }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) { for (var k in attrs) { e.setAttribute(k, attrs[k]); } }
    if (text != null) { e.textContent = text; }
    return e;
  }
  function sel(tag, attrs, text) {
    var e = document.createElementNS(SVGNS, tag);
    if (attrs) { for (var k in attrs) { e.setAttribute(k, attrs[k]); } }
    if (text != null) { e.textContent = text; }
    return e;
  }
  function markClass(outcome) {
    if (outcome === "ok") { return "ok"; }
    if (outcome === "err") { return "err"; }
    return "cancel";
  }
  function markChar(outcome) {
    if (outcome === "ok") { return "✓"; }
    if (outcome === "err") { return "✗"; }
    if (outcome === "user") { return "⊘"; }
    if (outcome === "timeout") { return "⏱"; }
    return "•";
  }

  function summaryTable(scripts) {
    var table = el("table");
    var head = el("tr");
    head.appendChild(el("th", null, "脚本"));
    KEYS.forEach(function (k) { head.appendChild(el("th", null, k)); });
    table.appendChild(head);
    var total = {};
    KEYS.forEach(function (k) { total[k] = 0; });
    scripts.forEach(function (s) {
      var tr = el("tr");
      tr.appendChild(el("td", null, s.name));
      KEYS.forEach(function (k) {
        tr.appendChild(el("td", null, String(s.summary[k])));
        total[k] += s.summary[k];
      });
      table.appendChild(tr);
    });
    var tr = el("tr", { "class": "total" });
    tr.appendChild(el("td", null, "合计"));
    KEYS.forEach(function (k) { tr.appendChild(el("td", null, String(total[k]))); });
    table.appendChild(tr);
    return table;
  }

  function chart(s) {
    var W = 960, labelW = 150, right = 60, top = 34, laneH = 20, bottom = 30;
    var lanes = [];
    s.execs.forEach(function (e) { lanes.push({ kind: "exec", o: e }); });
    s.requests.forEach(function (r) { lanes.push({ kind: "req", o: r }); });
    var H = top + lanes.length * laneH + bottom;
    var maxT = s.max_t > 0 ? s.max_t : 1;
    function X(t) { return labelW + (W - labelW - right) * (t / maxT); }
    var svg = sel("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, width: "100%", role: "img" });
    var axisY = top - 10;
    svg.appendChild(sel("line", { x1: labelW, y1: axisY, x2: W - right, y2: axisY, "class": "axis" }));
    var NT = 6, i, t, tx;
    for (i = 0; i <= NT; i++) {
      t = Math.round(maxT * i / NT);
      tx = X(t);
      svg.appendChild(sel("line", { x1: tx, y1: axisY - 4, x2: tx, y2: axisY, "class": "axis" }));
      svg.appendChild(sel("text", { x: tx, y: axisY - 6, "text-anchor": "middle", "class": "tick" }, t + "ms"));
    }
    var execLaneY = {};
    lanes.forEach(function (ln, idx) {
      var y = top + idx * laneH;
      var mid = y + laneH / 2;
      svg.appendChild(sel("line", { x1: labelW, y1: y + laneH, x2: W - right, y2: y + laneH, "class": "lane" }));
      if (ln.kind === "exec") {
        var e = ln.o;
        execLaneY[e.id] = mid;
        var g = sel("g");
        g.appendChild(sel("text", { x: labelW - 8, y: mid + 4, "text-anchor": "end", "class": "lane-label exec-label" }, "⚙ " + e.id + " " + e.key));
        var end = e.end == null ? e.start : e.end;
        var rect = sel("rect", { x: X(e.start), y: mid - 7, width: Math.max(3, X(end) - X(e.start)), height: 14, "class": "exec-bar", fill: colorFor(e.id) });
        rect.appendChild(sel("title", null, e.id + " " + e.key + " [" + e.start + ", " + end + ") " + e.outcome + " 挂载 " + e.members.length));
        g.appendChild(rect);
        g.appendChild(sel("text", { x: X(end) + 5, y: mid + 4, "class": "mark " + (e.outcome === "cancelled" ? "cancel" : e.outcome) }, e.outcome === "cancelled" ? "XCANCEL" : e.outcome));
        svg.appendChild(g);
      } else {
        var r = ln.o;
        var g2 = sel("g");
        g2.appendChild(sel("text", { x: labelW - 8, y: mid + 4, "text-anchor": "end", "class": "lane-label" }, r.id + " " + r.key));
        if (r.exec === "-") {
          var d = sel("rect", { x: X(r.arrive) - 4, y: mid - 4, width: 8, height: 8, transform: "rotate(45 " + X(r.arrive) + " " + mid + ")", "class": "reject-mark" });
          d.appendChild(sel("title", null, r.id + " " + r.key + " @" + r.arrive + " 被拒 " + r.outcome));
          g2.appendChild(d);
          g2.appendChild(sel("text", { x: X(r.arrive) + 7, y: mid + 4, "class": "mark reject" }, r.outcome));
        } else {
          var c = colorFor(r.exec);
          var endR = r.end == null ? r.arrive : r.end;
          var bar = sel("rect", { x: X(r.arrive), y: mid - 4, width: Math.max(2, X(endR) - X(r.arrive)), height: 8, "class": "req-bar", fill: c });
          bar.appendChild(sel("title", null, r.id + " " + r.key + " [" + r.arrive + ", " + endR + "] " + r.outcome + " → " + r.exec));
          g2.appendChild(bar);
          if (execLaneY[r.exec] != null) {
            g2.appendChild(sel("line", { x1: X(r.arrive), y1: mid - 4, x2: X(r.arrive), y2: execLaneY[r.exec], "class": "merge-link", stroke: c }));
          }
          g2.appendChild(sel("text", { x: X(endR) + 4, y: mid + 4, "class": "mark " + markClass(r.outcome) }, markChar(r.outcome)));
        }
        svg.appendChild(g2);
      }
    });
    return svg;
  }

  function requestsTable(s) {
    var table = el("table");
    var head = el("tr");
    ["请求", "键", "到达", "结束", "结局", "并入执行"].forEach(function (h) { head.appendChild(el("th", null, h)); });
    table.appendChild(head);
    s.requests.forEach(function (r) {
      var tr = el("tr");
      [r.id, r.key, String(r.arrive), r.end == null ? "-" : String(r.end), r.outcome, r.exec].forEach(function (v) {
        tr.appendChild(el("td", null, v));
      });
      table.appendChild(tr);
    });
    return table;
  }

  function execsTable(s) {
    var table = el("table");
    var head = el("tr");
    ["执行", "键", "开始", "结束", "结局", "挂载数", "挂载请求"].forEach(function (h) { head.appendChild(el("th", null, h)); });
    table.appendChild(head);
    s.execs.forEach(function (e) {
      var tr = el("tr");
      [e.id, e.key, String(e.start), e.end == null ? "-" : String(e.end), e.outcome, String(e.members.length), e.members.join(" ")].forEach(function (v) {
        tr.appendChild(el("td", null, v));
      });
      table.appendChild(tr);
    });
    return table;
  }

  document.getElementById("summary").appendChild(summaryTable(data.scripts));
  var root = document.getElementById("scripts");
  data.scripts.forEach(function (s) {
    var sec = el("section");
    sec.appendChild(el("h2", null, s.name));
    sec.appendChild(el("p", { "class": "legend" }, "⚙ 粗框条 = 真执行；同色细条 = 并入该执行的请求（虚线连到所属执行）；灰菱形 = 到达即被拒。结局标记：✓ ok　✗ err　⊘ 用户取消　⏱ 超时。"));
    sec.appendChild(chart(s));
    var kv = el("p", { "class": "kv" });
    kv.appendChild(el("b", null, "exec"));
    kv.appendChild(document.createTextNode(s.summary.exec + " 次真执行　"));
    kv.appendChild(el("b", null, "reject"));
    kv.appendChild(document.createTextNode(s.summary.reject + " 次拒绝"));
    sec.appendChild(kv);
    sec.appendChild(el("h3", null, "汇总"));
    sec.appendChild(summaryTable([s]));
    sec.appendChild(el("h3", null, "请求"));
    sec.appendChild(requestsTable(s));
    sec.appendChild(el("h3", null, "真执行"));
    sec.appendChild(execsTable(s));
    var det = el("details");
    det.appendChild(el("summary", null, "原始轨迹（" + s.trace.length + " 行）"));
    det.appendChild(el("pre", null, s.trace.join("\\n")));
    sec.appendChild(det);
    root.appendChild(sec);
  });
})();
</script>
</body>
</html>
"""
