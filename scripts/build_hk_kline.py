"""港股 13 家日线收益长表 → data/hk/kline.csv（hk_code,date,pct,amount_yi）

为什么要这张表：`hk/YYYYMMDD.csv` 只是「当天快照」，每天一份，回看历史日时手里没有港股的历史截面，
于是「港股 13 家中位涨跌」这条线只有从建库那天才开始有。这里用 tushare `hk_daily` 把 13 家的日线收益
补回来，历史页就能和 A 股侧对称地算「港股中位 vs 恒生科技」。

为什么只存 pct 不存价格：`kline_tushare` 的前复权是「对齐到最新收盘」，每次重跑整体重新标定，
价格并存会在增量合并的接缝处跳一下；日收益与标定无关，可以安全地增量合并。

    python scripts/build_hk_kline.py              # 增量更新 13 家（约 1 分钟，逐股拉；5 天内不重复拉）
    python scripts/build_hk_kline.py --bars 300   # 往回拉更多
    python scripts/build_hk_kline.py --only 09988 --force   # 只补某一只
    python scripts/build_hk_kline.py --rewrite    # 丢掉旧的、整表重建
"""
import os
import sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))

import pandas as pd

import fetch_hk

PATH = "hk/kline.csv"
KEEP_DAYS = 260
STALE_DAYS = 5          # 表的最新日期比目标日旧超过这么多天就先跳过（日常靠 hk/<day>.csv，不用每次重拉）


def fetch_one(code, day, bars):
    """单只港股 → DataFrame[hk_code,date,pct,amount_yi]（pct 是除净调整后的日收益%）

    tushare 主源、腾讯兜底；tushare 代理偶尔会吐「上游池耗尽」或只回来半段，
    所以拉完先看尾部日期，跟不上就再试腾讯。
    """
    import datetime as dt
    want = dt.datetime.strptime(day, "%Y%m%d").date() - dt.timedelta(days=7)
    kl = fetch_hk.kline_tushare(code, day, bars=bars)
    if kl.empty or len(kl) < 2 or pd.to_datetime(kl.date.max()).date() < want:
        k2 = fetch_hk.kline_tencent(code)
        k2 = k2[k2.date.astype(str).str.replace("-", "", regex=False) <= day] if not k2.empty else k2
        if not k2.empty and (kl.empty or k2.date.max() > kl.date.max()):
            kl = k2
    if kl.empty or len(kl) < 2:
        return pd.DataFrame()
    kl = kl.sort_values("date").reset_index(drop=True)
    kl["pct"] = (kl.close.pct_change() * 100).round(2)
    kl = kl.iloc[1:]                                   # 第一根没有前收
    out = kl[["date", "pct", "amount_yi"]].copy()
    out.insert(0, "hk_code", code)
    out["raw"] = (kl["raw"] if "raw" in kl.columns else float("nan"))   # 未复权收盘（市值/PE 用）
    return out


def main():
    argv = sys.argv[1:]
    bars = int(argv[argv.index("--bars") + 1]) if "--bars" in argv else 80
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else None
    day = argv[0] if argv and argv[0].isdigit() else __import__("datetime").date.today().strftime("%Y%m%d")

    if os.path.exists(PATH) and "--force" not in argv and only is None:
        old_max = str(pd.read_csv(PATH, encoding="utf-8-sig", usecols=["date"]).date.max())
        age = (pd.Timestamp(day) - pd.Timestamp(old_max)).days
        if age < STALE_DAYS:
            print(f"{PATH} 最新 {old_max}（比 {day} 旧 {age} 天 < {STALE_DAYS}），跳过；"
                  f"要强制刷新加 --force")
            return

    gs = pd.read_csv("gs50.csv", encoding="utf-8-sig", dtype=str).fillna("")
    codes = [c for c in gs[gs.market == "HK"].hk_code if c]
    if only:
        codes = [c for c in codes if c in only]
    if not codes:
        raise SystemExit("gs50.csv 里没有港股")

    frames = []
    for i, c in enumerate(codes, 1):
        try:
            f = fetch_one(c, day, bars)
        except Exception as exc:                       # noqa: BLE001
            print(f"  ✗ {c} {type(exc).__name__}: {str(exc)[:70]}")
            continue
        if f.empty:
            print(f"  ! {c} 没有拿到日线")
            continue
        frames.append(f)
        print(f"  + {c} {len(f):4d} 根  {f.date.iloc[0]} ~ {f.date.iloc[-1]}")
    if not frames:
        raise SystemExit("一只都没拉到，检查 tushare key / 网络")
    new = pd.concat(frames, ignore_index=True)

    os.makedirs("hk", exist_ok=True)
    if os.path.exists(PATH) and "--rewrite" not in argv:
        old = pd.read_csv(PATH, encoding="utf-8-sig", dtype={"hk_code": str, "date": str})
        # 新拉的覆盖旧的（同 hk_code+date 以新值为准），避免历史修正留在表里
        new = pd.concat([old, new], ignore_index=True).drop_duplicates(["hk_code", "date"], keep="last")
    new = new.sort_values(["hk_code", "date"]).reset_index(drop=True)
    # 每股只留最近 KEEP_DAYS 个交易日（表会随时间变长，但没有意义地变长）
    new = new.groupby("hk_code", group_keys=False).tail(KEEP_DAYS)
    new.to_csv(PATH, index=False, encoding="utf-8-sig")

    days = new.date.nunique()
    print(f"写出 {PATH}：{new.hk_code.nunique()} 只 / {len(new)} 行 / {days} 个交易日 "
          f"（{new.date.min()} ~ {new.date.max()}）")


if __name__ == "__main__":
    main()
