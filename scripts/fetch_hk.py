"""港股 13 家：快照 + 日线（动量/量能/走势）+ 卖方共识 NTM PE

   三个源，各管一段：
     1. 腾讯快照 qt.gtimg.cn/q=hk00700
          港股字段跟 A 股不一样（78 列）：f[3]=现价 f[32]=涨跌幅% f[37]=成交额(元)
          f[39]=PE(TTM) f[45]=总市值(亿) f[75]=币种
         （tushare 没现成的港股快照可用：hk_dailybasic 在这个代理 unknown_api）
     2. 日线：主源 tushare hk_daily（逐股拉，交易所口径，13 家 × 1~3 段 ≈ 1 分钟）
          · 这个代理单次区间上限约 1 年，要 400 根就分段往回拉（每段 360 天，最多 3 段）
          · hk_daily 本身是未复权价，但 pre_close / pct_chg 已经是除净口径
            （腾讯 2025-05-16 除净日 pre_close 515.5 ≠ 前一日 close 520.0），
            所以复权收益 = pct_chg 累乘，不需要 hk_daily_adj（这个代理 unknown_api）
        兜底：腾讯 hkfqkline，一次 curl 拿 400 根前复权（代理抽风时用）
          → ret5/10/20/all、量能（当日 / 前 20 日均）、距 60 日高、250 日低、60 日 σ、40 日走势
     3. Yahoo Finance（yfinance）earnings_estimate
          FY0/FY1 共识 EPS + 覆盖机构数 + 记价币种
          PE_NTM = 现价 / (w×FY0 + (1−w)×FY1)，w = 距本财年结束剩余天数 / 365
          —— 与 A 股 scripts/fetch_consensus.py 完全同口径（那边是自然年，这边按各公司财年）
          EPS 与股价币种可能不同（腾讯/小米 EPS 是 CNY，联想/禾赛是 USD），
          用 CNYHKD / USDHKD 折成 HKD 再算 PE；Yahoo 自己的 forwardPE（FY1 口径）另存一列对账
     汇率：yfinance 取 CNYHKD=X、USDHKD=X，取不到退回常量

   输出 data/hk/YYYYMMDD.csv：
     原列 date,hk_code,name,price,pct_chg,amount_yi,total_mv_yi,pe_ttm,ccy
     新增 hist_src,hkd_cny,amt_20d,amt_mult,ret5,ret10,ret20,ret_all,ret_all_days,
          dist_hi60,dist_lo250,sigma60,spark,
          pe_ntm,fy0_eps,fy1_eps,ntm_yi,eps_ccy,n_brokers,fy0_end,yahoo_fwd_pe

   用法：python scripts/fetch_hk.py [YYYYMMDD]     （--no-consensus 只跑行情与历史）
"""
import os as _os, sys as _sys
_os.chdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "data"))
import datetime as dt
import json
import os
import subprocess
import threading
import time

import pandas as pd

OUTDIR = "hk"
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk{},day,,,400,qfq"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
FALLBACK_FX = {"CNY": 1.18, "HKD": 1.0, "USD": 7.78}      # 1 单位外币 → HKD（取不到实时汇率时用）
FRESH_DAYS = 3                                             # 共识缓存多久刷新一次（研报按季度更新）


def _curl(url, timeout=25, referer="https://gu.qq.com/"):
    r = subprocess.run(["curl", "-s", "-m", str(timeout), url,
                        "-H", f"User-Agent: {UA}", "-H", f"Referer: {referer}"],
                       capture_output=True)
    return r.stdout.decode("utf-8", errors="ignore")


def quote(codes):
    """腾讯快照 → dict[hk_code] ，金额单位统一成「亿」"""
    out = {}
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        raw = subprocess.run(["curl", "-s", "-m", "25", f"http://qt.gtimg.cn/q="
                              + ",".join("hk" + c for c in chunk)],
                             capture_output=True).stdout.decode("gbk", errors="ignore")
        for line in raw.split(";"):
            if '="' not in line:
                continue
            f = line.split('="', 1)[1].rstrip('"\n').split("~")
            if len(f) < 72:
                continue

            def g(idx, scale=1.0):
                try:
                    return float(f[idx]) / scale if f[idx] else 0.0
                except (ValueError, IndexError):
                    return 0.0
            out[f[2]] = dict(name=f[1], price=g(3), pct_chg=g(32),
                             amount_yi=round(g(37, 1e8), 3),         # 港股 37 是元
                             total_mv_yi=round(g(45), 1), pe_ttm=g(39),
                             ccy=f[75] if len(f) > 75 and f[75] else "HKD")
    return out


def kline_tushare(code, end_day, bars=400, max_seg=3):
    """主源：tushare hk_daily，返回 DataFrame[date, close(复权), amount_yi]

    代理单次区间 ≈1 年上限 → 分段往回拉。未复权价 + 除净口径的 pct_chg →
    复权序列 = pct_chg 累乘，最后对齐到最新收盘价（前复权）。
    """
    try:
        import tushare_client as tc
        pro = tc.pro()
    except Exception as e:                                         # noqa: BLE001
        print(f"    ✗ tushare 不可用 {repr(e)[:60]}")
        return pd.DataFrame()
    end = dt.datetime.strptime(end_day, "%Y%m%d").date()
    frames = []
    for _ in range(max_seg):
        start = end - dt.timedelta(days=360)
        try:
            df = tc.call(pro.hk_daily, ts_code=f"{code}.HK",
                         start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
        except Exception as e:                                     # noqa: BLE001
            print(f"    ✗ hk_daily {code} {repr(e)[:60]}")
            df = None
        if df is not None and len(df):
            frames.append(df)
            # 段内最早日期贴着段起点 = 这一段被填满；明显晚于起点 = 已经到上市日，别再往回拉了
            if df.trade_date.min() > (start + dt.timedelta(days=10)).strftime("%Y%m%d"):
                break
        if sum(len(f) for f in frames) >= bars:
            break
        end = start - dt.timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames).drop_duplicates("trade_date").sort_values("trade_date")
    d = d[d.trade_date <= end_day]
    chg = pd.to_numeric(d.pct_chg, errors="coerce").fillna(0) / 100
    adj = (1 + chg).cumprod()
    close = pd.to_numeric(d.close, errors="coerce")
    if not len(adj) or not adj.iloc[-1]:
        return pd.DataFrame()
    adj = adj * close.iloc[-1] / adj.iloc[-1]                      # 对齐到最新收盘 = 前复权
    return pd.DataFrame({"date": d.trade_date.astype(str).str.replace(
                             r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", regex=True),
                         "close": adj.round(3).to_numpy(),
                         "amount_yi": (pd.to_numeric(d.amount, errors="coerce") / 1e8).round(2).to_numpy()})


def kline_tencent(code):
    """兜底：腾讯前复权日线 → DataFrame[date, close, amount_yi]（amount 万 → 亿）"""
    try:
        d = json.loads(_curl(KLINE_URL.format(code)))["data"]["hk" + code]
    except Exception:                                              # noqa: BLE001
        return pd.DataFrame()
    rows = d.get("qfqday") or d.get("day") or []
    recs = []
    for r in rows:
        try:
            close, vol = float(r[2]), float(r[5])
        except (ValueError, IndexError):
            continue
        amt = None
        if len(r) > 8:
            try:
                amt = float(r[8]) / 1e4                            # 万 → 亿
            except ValueError:
                amt = None
        recs.append(dict(date=r[0], close=close, vol=vol,
                         amount_yi=amt if amt else round(vol * close / 1e8, 2)))
    return pd.DataFrame(recs)


def _call_timeout(fn, secs):
    """给可能卡死的 HTTP 调用套个硬超时（yfinance 卡住时不会自己返回）"""
    box = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as e:                                 # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(secs)
    if "e" in box:
        raise box["e"]
    if "v" not in box:
        raise TimeoutError(f"{secs}s 超时")
    return box["v"]


def fx_rates():
    """1 单位外币 → HKD，以及 1 HKD → CNY（取不到就用常量，对 PE 影响 <1%）"""
    rate = dict(FALLBACK_FX)
    for cur in ("CNY", "USD"):
        try:
            p = _call_timeout(lambda c=cur: __import__("yfinance").Ticker(f"{c}HKD=X")
                              .info.get("regularMarketPrice"), 30)
            if p and p > 0:
                rate[cur] = float(p)
        except Exception:                                          # noqa: BLE001
            pass
    return rate, round(1 / rate["CNY"], 4)


def consensus(code, today):
    """卖方共识 → FY0/FY1 EPS + 覆盖机构数（NTM / PE 由调用方算）

    共识是慢变量（研报按季度更新），所以：
      · 按股票缓存到 .cache/hk_consensus/{code}.json，{FRESH_DAYS} 天内不重拉
      · 过期才打 Yahoo；Yahoo 限速/卡死/无网时直接用旧缓存（PE 里价格还是当日的），
        比留空更有用，cons_date 记下这份共识是哪天的
    """
    cache = os.path.join("..", ".cache", "hk_consensus", f"{code}.json")
    old = {}
    if os.path.exists(cache):
        try:
            old = json.load(open(cache, encoding="utf-8"))
        except (ValueError, OSError):
            old = {}
    age = 999
    try:
        age = (today - dt.date.fromisoformat(old["cons_date"])).days
    except (KeyError, ValueError):
        pass
    if age <= FRESH_DAYS:
        return old
    for attempt in range(2):
        try:
            out = _call_timeout(lambda: _consensus_now(code, today), 60)
        except Exception as e:                                     # noqa: BLE001
            msg = repr(e)
            if attempt == 0 and ("RateLimit" in msg or "Too Many Requests" in msg):
                time.sleep(25)
                continue
            print(f"    ✗ 共识 {code} {msg[:60]} → 用 {old.get('cons_date', '无缓存')}")
            return old
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump(out, open(cache, "w", encoding="utf-8"))
        time.sleep(2)                                              # Yahoo 连着打会 429
        return out
    return old


def _consensus_now(code, today):
    import yfinance as yf
    tk = yf.Ticker(code[1:] + ".HK")
    est = tk.earnings_estimate               # 无覆盖/限速都会抛，交给 consensus() 判
    info = tk.info
    fy0 = float(est.loc["0y", "avg"])
    fy1 = float(est.loc["+1y", "avg"])
    cur = est.loc["0y", "currency"] if isinstance(est.loc["0y", "currency"], str) else "CNY"
    n = int(est.loc["0y", "numberOfAnalysts"] or 0)
    nxt = info.get("nextFiscalYearEnd")
    try:
        fy_end = dt.datetime.fromtimestamp(nxt, dt.timezone.utc).date()
    except (TypeError, ValueError, OSError):
        fy_end = dt.date(today.year, 12, 31)
        if fy_end < today:
            fy_end = dt.date(today.year + 1, 12, 31)
    return dict(cons_date=today.isoformat(), fy0_eps=round(fy0, 4), fy1_eps=round(fy1, 4),
                eps_ccy=cur, n_brokers=n, fy0_end=fy_end.strftime("%Y%m%d"),
                _fy_end=fy_end.isoformat(),
                _yahoo_fwd=info.get("forwardPE"))


def history(row, kl):
    """历史派生指标，写回 row"""
    if kl.empty or len(kl) < 2:
        return row
    s = kl.sort_values("date")
    close = s.close.reset_index(drop=True)

    def ret(n):
        n = min(n, len(close) - 1)
        return round((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100, 2)
    row["ret5"], row["ret10"], row["ret20"] = ret(5), ret(10), ret(20)
    row["ret_all"] = ret(999)
    row["ret_all_days"] = len(close) - 1
    amt20 = s.amount_yi.tail(21).head(20).mean()
    row["amt_20d"] = round(float(amt20), 2) if amt20 == amt20 and amt20 else float("nan")
    row["amt_mult"] = (round(row.get("amount_yi", float("nan")) / amt20, 2)
                       if row["amt_20d"] == row["amt_20d"] else float("nan"))
    row["dist_hi60"] = round((close.iloc[-1] / close.tail(60).max() - 1) * 100, 2)
    row["dist_lo250"] = round((close.iloc[-1] / close.tail(250).min() - 1) * 100, 2)
    row["sigma60"] = round(close.pct_change().tail(60).std() * 100, 2)
    row["spark"] = json.dumps([round(v, 3) for v in close.tail(40)])
    return row


def run(day=None, with_consensus=True):
    day = day or dt.date.today().strftime("%Y%m%d")
    today = dt.datetime.strptime(day, "%Y%m%d").date()
    g = pd.read_csv("gs50.csv", encoding="utf-8-sig", dtype=str)
    # 只看 market=HK 的 13 家；A/H 两地上市的名字走 A 股口径，不在这里重复取
    codes = [c for c in g[g.market == "HK"].hk_code if c]

    q = quote(codes)
    rate, hkd_cny = fx_rates()
    rows = []
    for c in codes:
        if c not in q:
            print(f"  ✗ 无快照 {c}")
            continue
        row = dict(date=day, hk_code=c, **q[c], hkd_cny=hkd_cny)
        kl, src = kline_tushare(c, day), "tushare hk_daily"
        if kl.empty:                                   # 代理抽风时退回腾讯前复权
            kl, src = kline_tencent(c), "腾讯 hkfqkline"
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        if not kl.empty and kl.date.iloc[-1] < iso and row["price"]:
            # tushare 港股当天要 T+1 才出，腾讯日线也可能停在昨天 → 用快照补今天的收尾一根，
            # 否则 ret5/10/20 算到「昨天收盘」而当日涨跌是「今天」，口径对不上
            kl = pd.concat([kl, pd.DataFrame([dict(date=iso, close=row["price"],
                                                   amount_yi=row.get("amount_yi"))])],
                           ignore_index=True)
            src += " + 当日快照"
        row = history(row, kl)
        row["hist_src"] = src
        if with_consensus:
            cs = consensus(c, today)
            if cs:
                px, mv = row["price"], row.get("total_mv_yi", float("nan"))
                # w = 本财年还剩多少比例（财年结束日来自共识缓存，没有就按自然年）
                try:
                    fy_end = dt.date.fromisoformat(cs["_fy_end"])
                except (KeyError, ValueError):
                    fy_end = dt.date(today.year, 12, 31)
                w = min(max((fy_end - today).days / 365, 0.0), 1.0)
                eps_hkd = (w * cs["fy0_eps"] + (1 - w) * cs["fy1_eps"]) * rate.get(cs["eps_ccy"], 1.0)
                cs["pe_ntm"] = round(px / eps_hkd, 2) if eps_hkd else float("nan")
                # NTM 归母净利（亿 HKD）= 每股 × 股本 = eps × 市值 / 股价
                cs["ntm_yi"] = round(eps_hkd * mv / px, 1) if eps_hkd and px else float("nan")
                cs["yahoo_fwd_pe"] = (round(float(cs["_yahoo_fwd"]), 2)
                                      if isinstance(cs["_yahoo_fwd"], (int, float)) else float("nan"))
                row.update({k: v for k, v in cs.items() if not k.startswith("_")})
        rows.append(row)

    df = pd.DataFrame(rows)
    order = ["date", "hk_code", "name", "price", "pct_chg", "amount_yi", "total_mv_yi",
             "pe_ttm", "pe_ntm", "n_brokers", "cons_date", "ntm_yi", "fy0_eps", "fy1_eps",
             "eps_ccy", "fy0_end", "yahoo_fwd_pe", "ccy", "hkd_cny", "hist_src", "amt_20d", "amt_mult",
             "ret5", "ret10", "ret20", "ret_all", "ret_all_days",
             "dist_hi60", "dist_lo250", "sigma60", "spark"]
    df = df.reindex(columns=[c for c in order if c in df.columns])
    os.makedirs(OUTDIR, exist_ok=True)
    df.to_csv(os.path.join(OUTDIR, f"{day}.csv"), index=False, encoding="utf-8-sig")
    print(f"港股 {len(df)}/{len(codes)} 家 → hk/{day}.csv　HKD/CNY {hkd_cny}　CNYHKD {rate['CNY']}")
    cols = [c for c in ("name", "price", "pct_chg", "ret20", "amt_mult", "hist_src",
                        "pe_ttm", "pe_ntm", "yahoo_fwd_pe", "n_brokers", "cons_date")
            if c in df.columns]
    print(df[cols].to_string(index=False))
    return df


def main():
    args = [a for a in _sys.argv[1:] if not a.startswith("--")]
    run(args[0] if args else None, with_consensus="--no-consensus" not in _sys.argv)


if __name__ == "__main__":
    main()
