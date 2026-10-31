"""卖方共识 NTM 快照（口径取自 report-consensus skill）

   NTM   = w × FY1共识 + (1 − w) × FY2共识          w = 1 − 当年已过天数 / 365
   FY1/2 = 每家机构取窗口内最新一份年报口径（quarter 以 Q4 结尾）预测 → 剔除 180 天无更新 → 中位数
   PE_NTM = 总市值 ÷ NTM 共识净利

   为什么用 NTM 而不是 pe_ttm：GS 报告用的是前瞻口径；TTM 会被最近一两个季度的异常放大或缩小，
   而卖方共识是"未来 12 个月"的市场定价基础。两者都存，图上标清哪个是哪个。

   数据源：tushare report_rc（逐股，单次约 5–40 秒，代理会间歇 upstream_pool_exhausted → 长退避重试）
   缓存：.cache/consensus/<code>.csv（原样存，改窗口不用重抓）
   断点续跑：data/consensus/YYYYMMDD.csv 里已有的 code6 直接跳过

   用法：python scripts/fetch_consensus.py [YYYYMMDD] [--window 190] [--force] [--offline]

   ⚠️ 口径是「YYYYMMDD 那天的视角」：窗口起止与 180 天新鲜度都按这个日子算。
      补历史日就靠这个 —— 先把原始研报表拉宽窗口一次，再用 --offline 按日重算：
        python scripts/fetch_consensus.py 20261030 --force --window=420   # 联网一次，缓存拉宽
        for d in $(ls data/mkt | sed 's/.csv//'); do \
            python scripts/fetch_consensus.py $d --offline --force; done   # 断网按日重算
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


def fetch_one(pro, ts_code, start, end, force=False, seg_days=170):
    """拉原始研报表（每机构每期的净利预测）→ .cache/consensus/<ts_code>.csv

    代理对单次区间有限制（超了报 date_range_too_large），所以按 seg_days 分段拉再合并，
    这样想补多久的历史就能补多久（补历史日靠它把缓存拉宽）。
    """
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, f"{ts_code}.csv")
    if os.path.exists(f) and not force:
        return pd.read_csv(f, dtype=str)
    s = dt.datetime.strptime(start, "%Y%m%d").date()
    e = dt.datetime.strptime(end, "%Y%m%d").date()
    frames, cursor = [], s
    while cursor <= e:
        seg_end = min(cursor + dt.timedelta(days=seg_days - 1), e)
        for i in range(4):
            try:
                frames.append(tc.call(pro.report_rc, tries=1, ts_code=ts_code,
                                      start_date=cursor.strftime("%Y%m%d"),
                                      end_date=seg_end.strftime("%Y%m%d"), fields=FIELDS))
                break
            except Exception as e_:                             # noqa: BLE001
                msg = repr(e_)
                if "pool_exhausted" in msg or "rate_limited" in msg or "busy" in msg:
                    time.sleep(35 + i * 15)
                else:
                    print(f"    ✗ {msg[:90]}")
                    break
        cursor = seg_end + dt.timedelta(days=1)
    frames = [x for x in frames if x is not None and len(x)]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    key = [c for c in ("ts_code", "report_date", "org_name", "quarter") if c in df.columns]
    df = df.drop_duplicates(key if key else None)
    df.to_csv(f, index=False, encoding="utf-8-sig")
    return df


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
    offline = "--offline" in sys.argv          # 只用 .cache/ 里的原始研报表，不联网（补历史日专用）
    today = dt.date.today()
    day = args[0] if args else today.strftime("%Y%m%d")
    # 口径必须是「那天的视角」：窗口与 180 天新鲜度都按 day 算，不用真正的 today（否则补历史会前视）
    as_of = dt.datetime.strptime(day, "%Y%m%d").date()
    if as_of > today:
        raise SystemExit(f"{day} 在将来（今天 {today}），不跑")
    today = as_of

    g = pd.read_csv("gs50.csv", encoding="utf-8-sig", dtype=str)
    codes = [c for c in g.a_code6.dropna() if c and c != "nan"]
    out = f"{OUTDIR}/{day}.csv"
    os.makedirs(OUTDIR, exist_ok=True)
    done = set()
    if os.path.exists(out) and not force:
        done = set(pd.read_csv(out, encoding="utf-8-sig", dtype={"code6": str}).code6)
        print(f"已有 {len(done)} 家，续跑剩余 {len(codes)-len(done)} 家")
    elif force and os.path.exists(out):
        os.remove(out)                     # --force 是「整份重算」，不然后面 append 会把旧行叠进去

    pro = None if offline else tc.pro()
    start = (today - dt.timedelta(days=win)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    if force and not offline:
        print(f"重拉原始研报表：{start} ~ {end}（覆盖 .cache/consensus/）")
    for i, code in enumerate(codes, 1):
        if code in done: continue
        ts_code = code + (".SH" if code[0] in "6" else ".BJ" if code[0] in "48" else ".SZ")
        t0 = time.time()
        rc = (fetch_one(pro, ts_code, start, end, force) if pro is not None
              else pd.read_csv(os.path.join(CACHE, f"{ts_code}.csv"), dtype=str)
              if os.path.exists(os.path.join(CACHE, f"{ts_code}.csv")) else pd.DataFrame())
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
