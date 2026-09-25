"""把引擎输出内联成单文件 HTML 报告。

页面数据全部来自 simulate() 的结果（与 trace 同一份记录），
不在页面侧重新推导；无 fetch、无 CDN、无 JS 依赖，file:// 双击即开。
同一份输入跑两遍，输出逐字节相同。
"""

from html import escape

from .engine import SUMMARY_KEYS

_CSS = """
body{font:14px/1.5 -apple-system,"Segoe UI","Helvetica Neue",Arial,sans-serif;margin:24px;color:#222}
h1{font-size:20px} h2{font-size:16px;margin:28px 0 6px} h3{font-size:13px;margin:14px 0 4px;color:#444}
table{border-collapse:collapse;font-size:12px;font-family:ui-monospace,Menlo,Consolas,monospace}
th,td{border:1px solid #ccc;padding:3px 10px;text-align:right}
th{background:#f2f2f2} td.l,th.l{text-align:left}
tr.total td{font-weight:bold;background:#f8f8f8}
svg{display:block;margin:6px 0;background:#fff;border:1px solid #ddd}
.note{color:#666;font-size:12px;margin:2px 0 0}
""".strip()


def _nice_step(max_t):
    pow10 = 1
    while True:
        for base in (1, 2, 5, 10):
            step = base * pow10
            if max_t / step <= 12:
                return step
        pow10 *= 10


def _hue(eid):
    return (eid * 67) % 360


def _timeline_svg(res):
    """横轴时间；每个请求一行，同一真执行的请求同色并连竖线；真执行单独一行深色标出。"""
    max_t = max(res.last_t, 1)
    left, top, row_h, plot_w = 76, 26, 16, 860
    scale = plot_w / max_t

    def X(t):
        return left + t * scale

    n_req, n_ex = len(res.requests), len(res.execs)
    exec_top = top + n_req * row_h + 14
    width = left + plot_w + 16
    height = exec_top + n_ex * row_h + 8

    out = ['<svg width="%d" height="%d" viewBox="0 0 %d %d" '
           'xmlns="http://www.w3.org/2000/svg" font-family="ui-monospace,Menlo,monospace" '
           'font-size="9" role="img" aria-label="%s 时间轴">'
           % (width, height, width, height, escape(res.name))]

    step = _nice_step(max_t)
    t = 0
    while t <= max_t:
        x = X(t)
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#eee"/>'
                   % (x, top - 6, x, height - 4))
        out.append('<text x="%.1f" y="%d" text-anchor="middle" fill="#777">%d</text>'
                   % (x, top - 10, t))
        t += step

    req_y = {}
    for i, req in enumerate(res.requests):
        y = top + i * row_h
        req_y[req.rid] = y
        out.append('<text x="%d" y="%d" text-anchor="end" fill="#333">%s</text>'
                   % (left - 6, y + row_h - 6, escape(req.rid)))
    for j, ex in enumerate(res.execs):
        y = exec_top + j * row_h
        out.append('<text x="%d" y="%d" text-anchor="end" fill="#333">e%d</text>'
                   % (left - 6, y + row_h - 6, ex.eid))

    # 合并到同一次真执行的请求：同色竖线连起来
    for ex in res.execs:
        ys = [req_y[m.rid] + row_h / 2 for m in ex.members]
        x = X(ex.start_t)
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                   'stroke-width="1.5" opacity="0.35"/>'
                   % (x, min(ys), x, max(ys), "hsl(%d,60%%,55%%)" % _hue(ex.eid)))

    for i, req in enumerate(res.requests):
        y = top + i * row_h + 3
        x1 = X(req.arrive_t)
        x2 = max(X(req.end_t), x1 + 2.0)
        if req.state == "rejected":
            color = "#bbb"
        else:
            color = "hsl(%d,60%%,72%%)" % _hue(req.exec_id)
        tip = "%s %s %d→%d %s (%s)" % (
            req.rid, req.key, req.arrive_t, req.end_t, req.outcome,
            "e%d" % req.exec_id if req.exec_id else "-")
        out.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s">'
                   '<title>%s</title></rect>'
                   % (x1, y, x2 - x1, row_h - 6, color, escape(tip)))

    for j, ex in enumerate(res.execs):
        y = exec_top + j * row_h + 2
        x1 = X(ex.start_t)
        x2 = max(X(ex.end_t), x1 + 2.0)
        color = "hsl(%d,70%%,42%%)" % _hue(ex.eid)
        outcome = ex.result if ex.state == "done" else "no_waiters"
        tip = "e%d %s [%d,%d) %s 挂载 %d" % (
            ex.eid, ex.key, ex.start_t, ex.end_t, outcome, len(ex.members))
        out.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s" '
                   'stroke="#222" stroke-width="0.8"><title>%s</title></rect>'
                   % (x1, y, x2 - x1, row_h - 5, color, escape(tip)))

    out.append("</svg>")
    return "".join(out)


def _fmt_t(t):
    return "-" if t is None else "%04d" % t


def _request_table(res):
    rows = ['<table><thead><tr><th class="l">请求</th><th class="l">键</th>'
            "<th>到达</th><th>结束</th><th>结局</th><th>真执行</th></tr></thead><tbody>"]
    for req in res.requests:
        rows.append('<tr><td class="l">%s</td><td class="l">%s</td><td>%04d</td>'
                    "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                        escape(req.rid), escape(req.key), req.arrive_t,
                        _fmt_t(req.end_t), req.outcome,
                        "e%d" % req.exec_id if req.exec_id else "-"))
    rows.append("</tbody></table>")
    return "".join(rows)


def _exec_table(res):
    rows = ['<table><thead><tr><th class="l">真执行</th><th class="l">键</th>'
            "<th>开始</th><th>结束</th><th>结局</th><th>挂载数</th>"
            '<th class="l">挂载请求</th></tr></thead><tbody>']
    for ex in res.execs:
        outcome = ex.result if ex.state == "done" else "no_waiters"
        members = ", ".join(m.rid for m in ex.members)
        rows.append('<tr><td class="l">e%d</td><td class="l">%s</td><td>%04d</td>'
                    "<td>%s</td><td>%s</td><td>%d</td><td class=\"l\">%s</td></tr>" % (
                        ex.eid, escape(ex.key), ex.start_t, _fmt_t(ex.end_t),
                        outcome, len(ex.members), escape(members)))
    rows.append("</tbody></table>")
    return "".join(rows)


def _summary_table(results):
    header = "".join("<th>%s</th>" % key for key in SUMMARY_KEYS)
    rows = ['<table><thead><tr><th class="l">脚本</th>%s</tr></thead><tbody>' % header]
    totals = {key: 0 for key in SUMMARY_KEYS}
    for res in results:
        cells = "".join("<td>%d</td>" % res.counts[key] for key in SUMMARY_KEYS)
        rows.append('<tr><td class="l">%s</td>%s</tr>' % (escape(res.name), cells))
        for key in SUMMARY_KEYS:
            totals[key] += res.counts[key]
    rows.append('<tr class="total"><td class="l">合计</td>%s</tr>'
                % "".join("<td>%d</td>" % totals[key] for key in SUMMARY_KEYS))
    rows.append("</tbody></table>")
    return "".join(rows)


def build_report(results):
    parts = ["<!DOCTYPE html>\n<html lang=\"zh\">\n<head>\n<meta charset=\"utf-8\">\n"
             "<title>oneflight 请求合并报告</title>\n<style>", _CSS,
             "</style>\n</head>\n<body>\n<h1>oneflight 请求合并报告</h1>\n",
             '<p class="note">时间轴：浅色横条 = 请求从到达到终态；深色横条 = 真执行区间；'
             "同一颜色的请求合并进同一次真执行（竖线相连）；灰色 = 到达即被拒。"
             "悬停横条可查看明细。数据与 trace 轨迹同源。</p>\n"]
    for res in results:
        parts.append('<section>\n<h2>%s</h2>\n' % escape(res.name))
        parts.append(_timeline_svg(res))
        parts.append("\n<h3>请求</h3>\n")
        parts.append(_request_table(res))
        parts.append("\n<h3>真执行</h3>\n")
        parts.append(_exec_table(res))
        parts.append("\n</section>\n")
    parts.append("<section>\n<h2>汇总</h2>\n")
    parts.append(_summary_table(results))
    parts.append("\n</section>\n</body>\n</html>\n")
    return "".join(parts)
