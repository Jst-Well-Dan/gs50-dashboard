"""港股历史快照回补 → data/hk/YYYYMMDD.csv（按日）

为什么需要：`hk/<day>.csv` 只有「当天跑过」才有（腾讯快照只给当下；tushare 港股**没有**每日指标层，
所谓 `hk_dailybasic` 这个接口根本不存在）。历史日的港股价格 / 市值 / PE 因此是空的。

两条腿，按可用性逐家降级：

  basis=raw（默认）
      price    = `hk/kline.csv` 的 `raw`（tushare hk_daily 未复权收盘，真价，不是前复权）
      股本     = 锚点快照隐含的总股本（锚点市值 ÷ 锚点价；真值，只在锚点那天确认过）
      市值     = price × 股本
      PE_TTM / PE_NTM = 锚点快照的 PE × (当日市值 ÷ 锚点市值)　← 净利/共识只给当下，这两列是折算

  basis=chain（raw 缺失时的兜底）
      price    = 锚点快照价 × Π(1 + 日收益/100)（hk/kline.csv 的 pct 链）
      其余同上

  basis=snapshot（当天本来就有真实快照，只补空列，真值一律不覆盖）

  ret5/10/20、dist_hi60/lo250、σ60、amt_mult、spark 全部由本地序列重算；cons_date 留空。

    python scripts/backfill_hk.py                 # 补所有 kline 里有、hk/ 里缺的交易日
    python scripts/backfill_hk.py 20260915        # 只补某一天
    python scripts/backfill_hk.py --all           # 已有真实快照的日子也补（只填空列）
    python scripts/backfill_hk.py --dry           # 只看会补哪些天
"""
import os
import sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))

import glob
import math

import pandas as pd

OUTDIR = "hk"
KLINE = os.path.join(OUTDIR, "kline.csv")
ORDER = ["date", "hk_code", "name", "price", "pct_chg", "amount_yi", "total_mv_yi",
         "pe_ttm", "pe_ntm", "n_brokers", "cons_date", "ntm_yi", "fy0_eps", "fy1_eps",
         "eps_ccy", "fy0_end", "yahoo_fwd_pe", "ccy", "hkd_cny", "hist_src", "amt_20d", "amt_mult",
         "ret5", "ret10", "ret20", "ret_all", "ret_all_days", "dist_hi60", "dist_lo250",
         "sigma60", "turnover", "float_mcap_yi", "basis", "approx", "spark"]


def fnum(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return v if v == v else float("nan")


def load_kline():
    k = pd.read_csv(KLINE, encoding="utf-8-sig", dtype={"hk_code": str, "date": str})
    k["ymd"] = k.date.str.replace("-", "", regex=False)
    return k.sort_values(["hk_code", "date"]).reset_index(drop=True)


def load_snapshots():
    """所有真实快照（跳过本脚本自己写的回补文件）"""
    out = {}
    for f in sorted(glob.glob(os.path.join(OUTDIR, "2*.csv"))):
        d = os.path.basename(f)[:8]
        if not d.isdigit():
            continue
        s = pd.read_csv(f, encoding="utf-8-sig", dtype={"hk_code": str})
        if "basis" in s.columns and (s.basis.astype(str) != "snapshot").any():
            continue
        if "approx" in s.columns and s.approx.fillna(0).astype(float).max() == 1:
            continue
        if "price" not in s.columns or s.price.notna().sum() == 0:
            continue
        out[d] = s.set_index("hk_code")
    return out


def chain_price(g, anchor_ymd, anchor_px):
    """没有未复权收盘时的兜底：以锚点为起点按日收益往前后推"""
    g = g.sort_values("date").reset_index(drop=True)
    dates = [d.replace("-", "") for d in g.date]
    pct = g.pct.fillna(0.0).tolist()
    price = [float("nan")] * len(g)
    idx = next((i for i, d in enumerate(dates) if d == anchor_ymd), None)
    if idx is None or not anchor_px or anchor_px != anchor_px:
        return price
    price[idx] = anchor_px
    for i in range(idx + 1, len(g)):
        price[i] = price[i - 1] * (1 + pct[i] / 100)
    for i in range(idx - 1, -1, -1):
        denom = 1 + pct[i + 1] / 100
        price[i] = price[i + 1] / denom if denom else float("nan")
    return price


def build_day(day, kline, snaps):
    """某一天的回补截面（DataFrame，index = hk_code）"""
    iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    # 锚点固定用「列最全的最新一份快照」：所有历史日共用同一个口径，
    # 否则每一天各挑各的锚点，会在锚点边界跳一下（PE_TTM 是市值÷净利，净利在两天里被供应商改过口径就会突变）
    rich = [d for d in snaps
            if "pe_ntm" in snaps[d].columns and pd.to_numeric(snaps[d].pe_ntm, errors="coerce").notna().any()]
    anchor_day = max(rich or list(snaps))
    anchor = snaps[anchor_day]
    hk_fx = fnum(anchor.get("hkd_cny").dropna().iloc[0]) if "hkd_cny" in anchor.columns and anchor.hkd_cny.notna().any() else float("nan")
    rows = []
    for code, grp in kline.groupby("hk_code"):
        if code not in anchor.index:
            continue
        a = anchor.loc[code]
        px0, mv0, ntm0, ttm0 = (fnum(a.get("price")), fnum(a.get("total_mv_yi")),
                                fnum(a.get("pe_ntm")), fnum(a.get("pe_ttm")))
        g = grp.sort_values("date").reset_index(drop=True)
        raw = pd.to_numeric(g.get("raw"), errors="coerce") if "raw" in g.columns else pd.Series(dtype=float)
        px_series = raw if len(raw) and raw.notna().any() else pd.Series(chain_price(g, anchor_day, px0))
        s = g.assign(px=px_series.values)
        hit = s[s.date == iso]
        if hit.empty:
            continue
        px = fnum(hit.px.iloc[0])
        if px != px:
            continue

        # ---- 估值：价格用未复权真价，股本用锚点快照隐含的（财报里的 issued_common_shares 不可靠：
        #     新股增发后它明显滞后，实测 09903 天数智芯只有真实股本的 1/3），PE 按市值比折算
        shares = mv0 / px0 if (mv0 == mv0 and px0) else float("nan")
        mcap = px * shares if shares == shares else float("nan")
        pe_ttm = ttm0 * mcap / mv0 if (ttm0 == ttm0 and mv0 == mv0 and mv0) else float("nan")
        pe_ntm = ntm0 * mcap / mv0 if (ntm0 == ntm0 and mv0 == mv0 and mv0) else float("nan")
        basis = "raw" if len(raw) and raw.notna().any() else "chain"

        amt_map = s.set_index("date").amount_yi
        amt = fnum(amt_map.get(iso))
        hist = s[s.date <= iso].dropna(subset=["px"])
        closes = hist.px
        pcts = hist.pct.fillna(0.0)

        def ret(n):
            return (math.prod(1 + p / 100 for p in pcts.tail(n)) - 1) * 100 if len(pcts) >= n else float("nan")

        amt20 = fnum(amt_map[amt_map.index < iso].tail(20).mean())
        hi60, lo250 = closes.tail(60).max(), closes.tail(250).min()
        rows.append(dict(
            hk_code=code, name=a.get("name", ""), price=round(px, 3),
            pct_chg=round(fnum(hist.pct.iloc[-1]), 2), amount_yi=amt,
            total_mv_yi=round(mcap, 1), pe_ttm=round(pe_ttm, 2), pe_ntm=round(pe_ntm, 2),
            ntm_yi=round(mcap / pe_ntm, 1) if mcap == mcap and pe_ntm == pe_ntm and pe_ntm else float("nan"),
            ccy="HKD", hkd_cny=hk_fx,
            hist_src=(f"tushare hk_daily 未复权收盘 × 快照 {anchor_day} 的股本（PE 按市值比折算）"
                      if basis == "raw" else
                      f"近似回补：hk/kline.csv 日收益链 × 快照 {anchor_day}"),
            basis=basis, approx=1 if basis == "chain" else 0,
            amt_20d=round(amt20, 2),
            amt_mult=round(amt / amt20, 2) if amt == amt and amt20 == amt20 and amt20 else float("nan"),
            ret5=round(ret(5), 2), ret10=round(ret(10), 2), ret20=round(ret(20), 2),
            ret_all=round((px / closes.iloc[0] - 1) * 100, 2) if len(closes) > 1 else float("nan"),
            ret_all_days=max(len(closes) - 1, 0),
            dist_hi60=round((px / hi60 - 1) * 100, 2) if hi60 and hi60 == hi60 else float("nan"),
            dist_lo250=round((px / lo250 - 1) * 100, 2) if lo250 and lo250 == lo250 else float("nan"),
            sigma60=round(float(pcts.tail(60).std(ddof=1)), 2) if len(pcts) > 2 else float("nan"),
            turnover=round(amt / mcap * 100, 2) if mcap == mcap and mcap else float("nan"),
            float_mcap_yi=float("nan"),
            spark=[round(p, 3) for p in closes.tail(40)]))
    if not rows:
        return None, anchor_day
    return pd.DataFrame(rows).set_index("hk_code"), anchor_day


def combine(day, approx, real):
    """真实快照优先，空值/缺列由回补值补（真值不覆盖）→ (df, basis 计数)"""
    if real is None:
        df = approx.drop(columns=["date"], errors="ignore").copy()
    else:
        r = real.drop(columns=["date"], errors="ignore")
        df = r.combine_first(approx.drop(columns=["date"], errors="ignore"))
        real_codes = {c for c in r.index if fnum(r.loc[c].get("price")) == fnum(r.loc[c].get("price"))}
        df["basis"] = ["snapshot" if c in real_codes else str(df.loc[c].get("basis", "anchor"))
                       for c in df.index]
        df["approx"] = [0 if c in real_codes else int(df.loc[c].get("approx", 1) or 0) for c in df.index]
    df = df.reset_index()
    df.insert(0, "date", day)
    df["spark"] = [str([round(float(v), 3) for v in sp]) if isinstance(sp, (list, tuple)) else sp
                   for sp in df.get("spark", [""] * len(df))]
    for c in ORDER:
        if c not in df.columns:
            df[c] = float("nan")
    counts = df.basis.astype(str).value_counts().to_dict()
    return df[ORDER], counts


def main():
    argv = sys.argv[1:]
    dry, do_all = "--dry" in argv, "--all" in argv
    days = [a for a in argv if a.isdigit()]
    kline = load_kline()
    snaps = load_snapshots()
    if not snaps:
        sys.exit("hk/ 里没有真实快照，先跑 python scripts/theme_data.py hk <day>")
    if not days:
        days = (sorted(f[:-4] for f in os.listdir("mkt") if f.endswith(".csv"))
                if os.path.isdir("mkt") else sorted(kline.ymd.unique()))
        if do_all:
            days = sorted(kline.ymd.unique())
    written, skipped = 0, 0
    for day in days:
        if day not in set(kline.ymd):
            skipped += 1
            continue
        real = snaps.get(day)
        if real is not None and not do_all:
            continue
        approx, anchor_day = build_day(day, kline, snaps)
        if approx is None:
            print(f"{day} 跳过（kline 里没有这天的价格）")
            continue
        df, counts = combine(day, approx, real)
        tag = " ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        if dry:
            print(f"{day} 将写 {len(df)} 家（{tag}）")
            continue
        df.to_csv(os.path.join(OUTDIR, f"{day}.csv"), index=False, encoding="utf-8-sig")
        written += 1
        print(f"{day} 写出 {len(df)} 家 ← {tag}")
    print(f"\n写出 {written} 个 hk/<day>.csv" + (f"，kline 里没有的日期 {skipped} 个" if skipped else ""))


if __name__ == "__main__":
    main()
