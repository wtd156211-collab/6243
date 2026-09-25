"""命令行：

    python -m oneflight trace  <脚本> <轨迹文件>
    python -m oneflight report <报告文件>
"""
import sys
from pathlib import Path

from .engine import load_script, run
from .report import SCRIPTS_DIR, build_report

USAGE = "usage: python -m oneflight trace <脚本> <轨迹文件> | python -m oneflight report <报告文件>"


def _write(path, text):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] == "trace":
        records = run(load_script(args[1]))
        _write(args[2], "".join(rec.line() + "\n" for rec in records))
        print("trace: %s -> %s (%d 行)" % (args[1], args[2], len(records)), file=sys.stderr)
        return 0
    if len(args) == 2 and args[0] == "report":
        _write(args[1], build_report(SCRIPTS_DIR))
        print("report: %s/scripts -> %s" % (SCRIPTS_DIR, args[1]), file=sys.stderr)
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
