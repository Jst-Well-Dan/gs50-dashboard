"""卖方共识 NTM 快照（口径取自 report-consensus skill）

   NTM   = w × FY1共识 + (1 − w) × FY2共识          w = 1 − 当年已过天数 / 365
   FY1/2 = 每家机构取窗口内最新一份年报口径（quarter 以 Q4 结尾）预测 → 剔除 180 天无更新 → 中位数
   PE_NTM = 总市值 ÷ NTM 共识净利

   为什么用 NTM 而不是 pe_ttm：GS 报告用的是前瞻口径；TTM 会被最近一两个季度的异常放大或缩小，
   而卖方共识是"未来 12 个月"的市场定价基础。两者都存，图上标清哪个是哪个。

   数据源：tushare report_rc（逐股，单次约 5–40 秒，代理会间歇 upstream_pool_exhausted → 长退避重试）
   缓存：.cache/consensus/<code>.csv（原样存，改窗口不用重抓）
   断点续跑：data/consensus/YYYYMMDD.csv 里已有的 code6 直接跳过

   用法：python scripts/fetch_consensus.py [YYYYMMDD] [--window 190] [--force]
"""
import os, sys
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
import datetime as dt
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tushare_client as tc

FIELDS = "ts_code,name,report_date,org_name,quarter,np,report_title"
CACHE = os.path.join("..", ".cache", "consensus")
OUTDIR = "consensus"
FRESH_DAYS = 180


def fetch_one(pro, ts_code, start, end, force=False):
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, f"{ts_code}.csv")
    if os.path.exists(f) and not force:
        return pd.read_csv(f, dtype=str)
    for i in range(6):
        try:
            df = tc.call(pro.report_rc, tries=1, ts_code=ts_code,
                         start_date=start, end_date=end, fields=FIELDS)
            df.to_csv(f, index=False, encoding="utf-8-sig")
            return df
        except Exception as e:                                    # noqa: BLE001
            msg = repr(e)
            if "pool_exhausted" in msg or "rate_limited" in msg or "busy" in msg:
                time.sleep(35 + i * 15)
            else:
                print(f"    ✗ {msg[:90]}")
                return pd.DataFrame()
    return pd.DataFrame()


def ntm_of(rc, today):
    if rc is None or not len(rc): return {}
    df = rc.copy()
    df = df[df.quarter.astype(str).str.endswith("Q4")]
    df["v"] = pd.to_numeric(df.np, errors="coerce") / 1e4          # 万元 → 亿元
    df["rd"] = pd.to_datetime(df.report_date, format="%Y%m%d", errors="coerce")
    now = pd.Timestamp(today)
    df["fy"] = df.quarter.astype(str).str[:4].astype(int)
    df = df.dropna(subset=["v", "rd"])
    df = df[((now - df.rd).dt.days <= FRESH_DAYS) & (df.rd <= now)]
    if df.empty: return {}
    # 每家机构每个财年只留最新一份
    df = df.sort_values("rd").groupby(["org_name", "fy"], as_index=False).last()

    fy1 = today.year
    a = df[df.fy == fy1].v
    b = df[df.fy == fy1 + 1].v
    if a.empty or b.empty: return {}
    w = 1 - (today - dt.date(fy1, 1, 1)).days / 365
    ntm = w * a.median() + (1 - w) * b.median()
    return dict(fy1_med=round(a.median(), 1), fy2_med=round(b.median(), 1),
                n_brokers=int(df.org_name.nunique()), w=round(w, 3),
                ntm_yi=round(ntm, 1), p25=round(b.quantile(.25), 1), p75=round(b.quantile(.75), 1))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    win = 190
    for a in sys.argv[1:]:
        if a.startswith("--window"): win = int(a.split("=")[-1])
    force = "--force" in sys.argv
    today = dt.date.today()
    day = args[0] if args else today.strftime("%Y%m%d")

    g = pd.read_csv("gs50.csv", encoding="utf-8-sig", dtype=str)
    codes = [c for c in g.a_code6.dropna() if c and c != "nan"]
    out = f"{OUTDIR}/{day}.csv"
    os.makedirs(OUTDIR, exist_ok=True)
    done = set()
    if os.path.exists(out) and not force:
        done = set(pd.read_csv(out, encoding="utf-8-sig", dtype={"code6": str}).code6)
        print(f"已有 {len(done)} 家，续跑剩余 {len(codes)-len(done)} 家")

    pro = tc.pro()
    start = (today - dt.timedelta(days=win)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    for i, code in enumerate(codes, 1):
        if code in done: continue
        ts_code = code + (".SH" if code[0] in "6" else ".BJ" if code[0] in "48" else ".SZ")
        t0 = time.time()
        rc = fetch_one(pro, ts_code, start, end, force)
        r = ntm_of(rc, today)
        if not r:
            print(f"[{i}/{len(codes)}] {ts_code} 无可用共识"); continue
        r.update(code6=code, ts_code=ts_code, date=day)
        pd.DataFrame([r]).to_csv(out, mode="a", header=not os.path.exists(out),
                                 index=False, encoding="utf-8-sig")
        print(f"[{i}/{len(codes)}] {ts_code} NTM {r['ntm_yi']:.0f}亿 "
              f"(FY1 {r['fy1_med']:.0f} / FY2 {r['fy2_med']:.0f}, {r['n_brokers']}家) {time.time()-t0:.0f}s")

    if os.path.exists(out):
        print(f"\n写出 {out}")


if __name__ == "__main__":
    main()
