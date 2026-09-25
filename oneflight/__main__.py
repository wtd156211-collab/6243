"""命令行：

    python -m oneflight trace  <脚本> <轨迹文件>
    python -m oneflight report <报告文件>
"""

import os
import sys
from pathlib import Path

from .engine import load_script, simulate

_SCRIPTS_DIR = Path("samples") / "scripts"

_USAGE = "用法: python -m oneflight trace <脚本> <轨迹文件> | report <报告文件>"


def _write(path, text, encoding):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding=encoding, newline="\n") as fh:
        fh.write(text)


def _cmd_trace(script_path, trace_path):
    result = simulate(load_script(script_path))
    _write(trace_path, result.trace_text, "ascii")
    print("trace: %s -> %s (%d 行)" % (script_path, trace_path, len(result.records)),
          file=sys.stderr)
    return 0


def _cmd_report(report_path):
    from .report import build_report

    paths = sorted(_SCRIPTS_DIR.glob("*.json"), key=lambda p: p.name)
    if not paths:
        print("report: %s 下没有脚本" % _SCRIPTS_DIR, file=sys.stderr)
        return 1
    results = [simulate(load_script(path)) for path in paths]
    _write(report_path, build_report(results), "utf-8")
    print("report: %d 个脚本 -> %s" % (len(results), report_path), file=sys.stderr)
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "trace" and len(args) == 3:
        return _cmd_trace(args[1], args[2])
    if args and args[0] == "report" and len(args) == 2:
        return _cmd_report(args[1])
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
