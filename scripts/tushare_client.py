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


def _official_token():
    """官方端点的 token（56 位个人账号）。与镜像的 47 位 tsr_… 不是同一个，别串。"""
    for p in ENV_CANDIDATES:
        if not os.path.exists(p):
            continue
        raw = open(p, encoding="utf-8").read().replace("\r", "")
        for line in raw.split("\n"):
            if line.lower().startswith("tushare_token"):
                return line.split("=", 1)[1].strip()
    for k in ("TUSHARE_TOKEN", "TUSHARE_OFFICIAL_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    raise RuntimeError("找不到官方 token：设环境变量 TUSHARE_TOKEN（56 位个人账号 token）")


_pro = None
def pro():
    """镜像端点（tl.kaixin8.top）：快，但共享池会 upstream_pool_exhausted"""
    global _pro
    if _pro is None:
        _pro = ts.pro_api(_token())
        _pro._DataApi__http_url = URL
    return _pro


_pro_o = None
def pro_official():
    """官方端点（api.waditu.com 默认）：稳，但 report_rc 硬限 1 次/分钟"""
    global _pro_o
    if _pro_o is None:
        _pro_o = ts.pro_api(_official_token())
    return _pro_o


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


OFFICIAL_GAP = 62   # 官方 report_rc 硬限 1 次/分钟，留 2s 余量
_o_last = [0.0]
def call_official(fn, *a, tries=3, **kw):
    """官方端点：按分钟限速串行。慢（41 家 ≈ 41 分钟）但不会被丢股。"""
    for i in range(tries):
        gap = time.time() - _o_last[0]
        if gap < OFFICIAL_GAP:
            time.sleep(OFFICIAL_GAP - gap)
        _o_last[0] = time.time()
        try:
            return fn(*a, **kw)
        except Exception as e:
            msg = repr(e)
            if "频率超限" in msg or "rate_limited" in msg:
                time.sleep(61)
            elif "Timeout" in msg:
                time.sleep(10)
            else:
                raise
    raise RuntimeError("官方端点重试耗尽")
