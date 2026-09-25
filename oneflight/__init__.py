"""oneflight 请求合并与并发去重：确定性模拟引擎 + 单文件 HTML 报告。"""

from .engine import SimResult, load_script, simulate

__all__ = ["SimResult", "load_script", "simulate"]
