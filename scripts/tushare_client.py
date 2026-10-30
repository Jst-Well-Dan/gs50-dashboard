import os as _os, sys as _sys
_os.chdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "data"))

"""Tushare 代理客户端：限速 + 重试（该代理有每分钟额度，超限返回 rate_limited）

   .env 查找顺序：本服务根目录 → 上级投资目录（老位置）→ 环境变量 TUSHARE_KEY。
   只认第一行形如 tushare_key=xxx 的键值。
"""
import os, time, functools
import tushare as ts

URL = "https://tl.kaixin8.top/tushare/sdk"
HERE = os.path.dirname(os.path.abspath(__file__))
ENV_CANDIDATES = [os.path.join(HERE, "..", ".env"),
                  os.path.join(HERE, "..", "..", ".env"),        # 从原 20261026 AI 目录搬来时的老位置
                  "E:/Python_Doc/Investment/.env"]


def _token():
    for p in ENV_CANDIDATES:
        if not os.path.exists(p):
            continue
        raw = open(p, encoding="utf-8").read().replace("\r", "")
        for line in raw.split("\n"):
            if line.lower().startswith("tushare_key"):
                return line.split("=", 1)[1].strip()
    if os.environ.get("TUSHARE_KEY"):
        return os.environ["TUSHARE_KEY"]
    raise RuntimeError("找不到 tushare key：在本目录建 .env（tushare_key=xxx）或设环境变量 TUSHARE_KEY")

_pro = None
def pro():
    global _pro
    if _pro is None:
        _pro = ts.pro_api(_token())
        _pro._DataApi__http_url = URL
    return _pro

MIN_GAP = 2.5      # 秒/次，保守值
_last = [0.0]
def call(fn, *a, tries=8, **kw):
    """调任意 pro.xxx，自动限速与退避"""
    for i in range(tries):
        gap = time.time() - _last[0]
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        _last[0] = time.time()
        try:
            return fn(*a, **kw)
        except Exception as e:
            msg = repr(e)
            if "rate_limited" in msg or "额度" in msg:
                time.sleep(31)
            elif "minute_upstream_busy" in msg or "Timeout" in msg:
                time.sleep(8)
            else:
                raise
    raise RuntimeError("tushare 重试耗尽")
