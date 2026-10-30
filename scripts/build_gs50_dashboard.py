"""AI 主题每日复盘看板 —— 54 家 GS 名单当「探针」，读主题的温度、广度、轮动与背离。

    python scripts/build_gs50_dashboard.py [YYYYMMDD]

数据（全部落在 data/，跑之前先 python scripts/theme_data.py all）：
    data/mkt/YYYYMMDD.csv      全市场截面（通达信盘后包，含成交额、涨跌停）
    data/quote/YYYYMMDD.csv    名单 + 基准指数快照（腾讯）
    data/kline/daily.csv       个股前复权日线（腾讯）
    data/consensus/YYYYMMDD.csv  卖方共识 → PE_NTM（A 股，tushare report_rc）
    data/hk/YYYYMMDD.csv       港股 13 家（快照 + 前复权日线 + 共识 NTM PE）
    data/em_cons.csv           板块-个股关系（77 个 AI 相关概念板块）
输出：
    docs/gs50_dashboard.html   离线单文件，内嵌 SVG

页面结构（复盘顺序）：
    1 温度计   2 广度与分化   3 结构轮动   4 名单 vs 板块   5 异动榜
    6 全表     7 估值（贵不贵 × 有没有人追）        8 口径与积累
"""
import glob
import json
import math
import os
import sys

_CWD0 = os.getcwd()   # chdir 前记住调用处的工作目录，--out/--summary 的相对路径按它解析
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))

import pandas as pd


def _abs(p):
    return os.path.normpath(p) if os.path.isabs(p) else os.path.normpath(os.path.join(_CWD0, p))


def parse_args(argv):
    """位置参数 = 数据日；--out / --summary 指定输出页与摘要 JSON（默认沿用老路径）"""
    day, out, summary, i = None, None, None, 0
    while i < len(argv):
        a = argv[i]
        if a in ("--out", "--summary"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} 需要一个路径")
            if a == "--out":
                out = _abs(argv[i + 1])
            else:
                summary = _abs(argv[i + 1])
            i += 2
            continue
        if not a.startswith("--"):
            day = day or a
        i += 1
    return day, out, _abs(summary) if summary else None

HKD_CNY = 0.91
LAYERS = ["Semiconductors", "Infrastructure", "Power",
          "Application (Physical)", "Application (AI+)", "AI Model"]
COLOR = {"Semiconductors": "#58a6ff", "Infrastructure": "#39c5cf", "Power": "#d29922",
         "Application (Physical)": "#bc8cff", "Application (AI+)": "#f778ba", "AI Model": "#a5d6ff"}
SHORT = {"Semiconductors": "半导体", "Infrastructure": "基础设施", "Power": "电力",
         "Application (Physical)": "应用·物理", "Application (AI+)": "应用·AI+", "AI Model": "模型"}
BENCH = {"000300": "沪深300", "399006": "创业板指", "000688": "科创50", "000001": "上证指数"}


# ------------------------------------------------------------------ 工具
UP, DOWN, FLAT = "#f85149", "#3fb950", "#8b949e"   # A 股习惯：涨红、跌绿


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pct(v, digits=1, sign=True):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "<span class='na'>—</span>"
    col = UP if v > 0 else DOWN if v < 0 else FLAT
    s = f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"
    return f"<span style='color:{col}'>{s}</span>"


def num(v, digits=0, unit=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "<span class='na'>—</span>"
    return f"{v:,.{digits}f}{unit}"


def mult(v):
    """倍数：历史太短算不出量能倍数时显示 — 而不是 nan×"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "<span class='na'>—</span>"
    return f"{v:.2f}×"


def heat_color(v, lo=-8, hi=8, mid=0):
    """收益 → 色深：涨红、跌绿（A 股习惯）"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "#1a2028", "#6e7681"
    t = max(-1.0, min(1.0, v / (hi if v > mid else abs(lo) or 1)))
    base = (248, 81, 73) if v > 0 else (63, 185, 80) if v < 0 else (110, 118, 129)
    a = 0.10 + 0.62 * abs(t)
    bg = f"rgba({base[0]},{base[1]},{base[2]},{a:.2f})"
    fg = "#e6edf3" if abs(t) > 0.45 else "#b9c6d4"
    return bg, fg


def zscores(series):
    s = pd.Series(series).astype(float)
    sd = s.std(ddof=0)
    if not sd or math.isnan(sd):
        return s * 0.0
    return (s - s.mean()) / sd


# ------------------------------------------------------------------ 数据装配
def load_history():
    days = sorted(f[:-4] for f in os.listdir("mkt")) if os.path.isdir("mkt") else []
    frames = []
    for d in days:
        df = pd.read_csv(f"mkt/{d}.csv", encoding="utf-8-sig", dtype={"code": str})
        df["day"] = d
        frames.append(df[["day", "code", "name", "market", "close", "pct_chg", "amount_yi",
                          "limit_up", "limit_down", "limit_pct"]])
    hist = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return days, hist


def build():
    gs = pd.read_csv("gs50.csv", encoding="utf-8-sig", dtype=str).fillna("")
    gs = gs.rename(columns={"a_code6": "code"})
    days, hist = load_history()
    day = _DAY_ARG or (days[-1] if days else None)
    if day is None:
        raise SystemExit("data/mkt 里没有盘后包，先跑 python scripts/theme_data.py all")
    if day not in days:
        raise SystemExit(f"{day} 不在 data/mkt 里（已有 {days[0]} ~ {days[-1]}）")
    # 时点切开：归档旧日子时不能看到未来的盘后包 / K 线（否则 5/20 日动量、60 日高、量能倍数全部前视）
    days = days[:days.index(day) + 1]
    hist = hist[hist.day <= day]
    today = hist[hist.day == day].set_index("code")
    px = hist.pivot_table(index="day", columns="code", values="close")
    px = px.reindex(days)

    # ---------------- 名单面板
    a = gs[gs.market == "A"].copy()
    a["close"] = a.code.map(today.close)
    a["pct_chg"] = a.code.map(today.pct_chg)
    a["amount_yi"] = a.code.map(today.amount_yi)
    a["limit_up"] = a.code.map(today.limit_up).fillna(False).astype(bool)
    a["limit_down"] = a.code.map(today.limit_down).fillna(False).astype(bool)
    a["name_src"] = a.code.map(today.name)

    # 快照：市值 / PE / 量比
    # 快照按「最近一份 ≤ 当天的」取：市值/股本变化慢，历史日回看时够用，并在页头标注快照日期
    q_list = [c for c in sorted(glob.glob("quote/*.csv")) if os.path.basename(c)[:8] <= day]
    retro = False
    if not q_list:                       # 回溯归档：当日之前没有快照 → 用最早一份，页面标注为回溯页
        q_list = sorted(glob.glob("quote/*.csv"))[:1]
        retro = bool(q_list)
    qf = q_list[-1] if q_list else ""
    quote_day = os.path.basename(qf)[:8] if qf else "—"
    quote = pd.read_csv(qf, encoding="utf-8-sig", dtype={"code": str}) if qf else pd.DataFrame()
    if not quote.empty:
        qq = quote.set_index("code")
        a["mcap_yi"] = a.code.map(qq.mcap_yi)
        a["pe_ttm_new"] = a.code.map(qq.pe_ttm)
        a["turnover"] = a.code.map(qq.turnover)
        a["vol_ratio_q"] = a.code.map(qq.vol_ratio)
        a["pb"] = a.code.map(qq.pb)
    for col in ("mcap_yi", "pe_ttm_new", "turnover", "vol_ratio_q", "pb"):
        if col not in a.columns:
            a[col] = float("nan")
    # A 股历史均值成交额 → 量能倍数
    amt_hist = hist.pivot_table(index="day", columns="code", values="amount_yi").reindex(days)
    a["amt_20d"] = a.code.map(amt_hist.iloc[:-1].tail(20).mean())
    a["amt_mult"] = (a.amount_yi / a.amt_20d).round(2)

    def ret(code, n):
        s = px[code].dropna() if code in px.columns else pd.Series(dtype=float)
        if len(s) < 2:
            return float("nan")
        n = min(n, len(s) - 1)
        return (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100

    a["ret5"] = [ret(c, 5) for c in a.code]
    a["ret10"] = [ret(c, 10) for c in a.code]
    a["ret20"] = [ret(c, 20) for c in a.code]
    a["ret_all"] = [ret(c, 999) for c in a.code]
    a["ret_all_days"] = min(len(days) - 1, 999)

    # 共识 PE_NTM（当天没有就用最近一版，共识是慢变量）
    cf_list = sorted(glob.glob("consensus/*.csv"))
    cf_list = [c for c in cf_list if os.path.basename(c)[:8] <= day]
    cf = cf_list[-1] if cf_list else ""
    cons_day = os.path.basename(cf)[:8] if cf else "—"
    cons = pd.read_csv(cf, encoding="utf-8-sig", dtype={"code6": str}) if cf else pd.DataFrame()
    if not cons.empty:
        cc = cons.set_index("code6")
        a["ntm_yi"] = a.code.map(cc.ntm_yi)
        a["n_brokers"] = a.code.map(cc.n_brokers)
        if {"fy1_med", "fy2_med"} <= set(cc.columns):
            a["g_fwd"] = a.code.map((cc.fy2_med / cc.fy1_med - 1).replace(
                [float("inf"), -float("inf")], float("nan")) * 100)
    else:
        a["ntm_yi"] = float("nan")
        a["g_fwd"] = float("nan")
    a["pe_ntm"] = (a.mcap_yi / a.ntm_yi).round(1)

    # K 线：距 60 / 250 日高、σ、sparkline
    kpath = "kline/daily.csv"
    kline = pd.read_csv(kpath, encoding="utf-8-sig", dtype={"code": str}) if os.path.exists(kpath) else pd.DataFrame()
    if not kline.empty:
        kline = kline[kline.date.astype(str).str.replace("-", "", regex=False) <= day]
    dist_hi60, dist_lo250, sig60, spark = {}, {}, {}, {}
    if not kline.empty:
        for code, grp in kline.groupby("code"):
            s = grp.sort_values("date")
            close = s.close
            if len(close) < 25:
                continue
            hi = close.tail(60).max()
            lo = close.tail(250).min()
            dist_hi60[code] = (close.iloc[-1] / hi - 1) * 100
            dist_lo250[code] = (close.iloc[-1] / lo - 1) * 100
            sig60[code] = s.close.pct_change().tail(60).std() * 100
            spark[code] = list(close.tail(40))
    a["dist_hi60"] = a.code.map(dist_hi60)
    a["dist_lo250"] = a.code.map(dist_lo250)
    a["sigma60"] = a.code.map(sig60)

    # 港股：快照 + 前复权日线（动量/量能/走势）+ 卖方共识 NTM PE，见 scripts/fetch_hk.py
    hk = gs[gs.market == "HK"].copy()
    hkf = [c for c in sorted(glob.glob("hk/*.csv")) if os.path.basename(c)[:8] <= day]
    hk_fx = HKD_CNY
    hk_cons_day, hk_hist_src = "—", "—"
    if hkf:
        h = pd.read_csv(hkf[-1], encoding="utf-8-sig", dtype={"hk_code": str}).set_index("hk_code")
        for col, src in (("close", "price"), ("pct_chg", "pct_chg"), ("mcap_yi", "total_mv_yi"),
                         ("pe_ttm_new", "pe_ttm"), ("pe_ntm", "pe_ntm"), ("n_brokers", "n_brokers"),
                         ("turnover", "turnover"), ("float_mcap_yi", "float_mcap_yi"),
                         ("amount_yi", "amount_yi"), ("amt_mult", "amt_mult"),
                         ("ret5", "ret5"), ("ret10", "ret10"), ("ret20", "ret20"),
                         ("ret_all", "ret_all"), ("dist_hi60", "dist_hi60"),
                         ("dist_lo250", "dist_lo250"), ("sigma60", "sigma60")):
            hk[col] = hk.hk_code.map(h[src]) if src in h.columns else float("nan")
        if "hkd_cny" in h.columns and h.hkd_cny.notna().any():     # 汇率用当日实时值，别写死 0.91
            hk_fx = float(h.hkd_cny.dropna().iloc[0])
        hk["mcap_yi"] = (hk.mcap_yi * hk_fx).round(1)
        hk["code"] = hk.hk_code                                     # 5 位港股代码，不与 A 股 6 位冲突
        if "cons_date" in h.columns and h.cons_date.notna().any():
            hk_cons_day = str(h.cons_date.dropna().astype(str).max()).replace("-", "")
        if "hist_src" in h.columns and h.hist_src.notna().any():
            hk_hist_src = str(h.hist_src.dropna().iloc[0]).split(" +")[0]
        for c, s in zip(hk.hk_code, h.get("spark", pd.Series(dtype=object)).reindex(hk.hk_code)):
            try:
                spark[c] = json.loads(s)
            except (TypeError, ValueError):
                continue
    else:
        for col in ("close", "pct_chg", "mcap_yi", "pe_ttm_new", "amount_yi"):
            hk[col] = float("nan")

    cols_keep = ["name_cn", "ai_layer", "ai_sector", "market", "code", "close", "pct_chg",
                 "amount_yi", "mcap_yi", "pe_ttm_new", "pe_ntm", "ret5", "ret10", "ret20", "ret_all",
                 "amt_mult", "turnover", "float_mcap_yi", "vol_ratio_q", "pb", "dist_hi60",
                 "dist_lo250", "sigma60", "limit_up", "limit_down", "n_brokers", "g_fwd"]
    for df in (a, hk):
        for c in cols_keep:
            if c not in df.columns:
                df[c] = float("nan")
    panel = pd.concat([a[cols_keep], hk[cols_keep]], ignore_index=True)
    # 换手率：A 股用行情接口的流通换手；港股无流通盘，退化为「成交额 / 总市值」
    panel["turn"] = pd.to_numeric(panel.get("turnover"), errors="coerce")
    panel["turn"] = panel["turn"].fillna((panel.amount_yi / panel.mcap_yi * 100).round(2))
    panel["pe_ttm"] = panel.pe_ttm_new
    panel["label"] = panel.name_cn
    panel["spark"] = panel.code.map(spark)

    # ---------------- 全市场温度
    mkt = today.copy()
    mkt["amount_yi"] = pd.to_numeric(mkt.amount_yi, errors="coerce")
    total_amt = mkt.amount_yi.sum()
    stats = {
        "n": len(mkt),
        "amount": total_amt,
        "amount_prev": (hist[hist.day == days[-2]].amount_yi.sum() if len(days) > 1 else float("nan")),
        "up": int((mkt.pct_chg > 0).sum()),
        "down": int((mkt.pct_chg < 0).sum()),
        "flat": int((mkt.pct_chg == 0).sum()),
        "median": float(mkt.pct_chg.median()),
        "mean": float(mkt.pct_chg.mean()),
        "zt": int(mkt.limit_up.sum()),
        "dt": int(mkt.limit_down.sum()),
    }
    # 全市场 5 日 / 20 日涨跌（等权中位，用历史）
    m_med = hist.groupby("day").pct_chg.median().reindex(days)

    # ---------------- 名单当日温度
    a_pct = panel[panel.market == "A"].pct_chg.dropna()
    theme = {
        "n": int(len(a_pct)),
        "median": float(a_pct.median()),
        "mean": float(a_pct.mean()),
        "up": int((a_pct > 0).sum()),
        "down": int((a_pct < 0).sum()),
        "p10": float(a_pct.quantile(0.1)),
        "p90": float(a_pct.quantile(0.9)),
        "spread": float(a_pct.quantile(0.9) - a_pct.quantile(0.1)),
        "amount": float(panel[panel.market == "A"].amount_yi.sum()),
        "w": (float((panel[panel.market == "A"].pct_chg * panel[panel.market == "A"].mcap_yi).sum()
                    / panel[panel.market == "A"].mcap_yi.sum())
              if panel[panel.market == "A"].mcap_yi.notna().any()
              and panel[panel.market == "A"].mcap_yi.sum() else float("nan")),
        "zt": int(panel.limit_up.fillna(False).sum()),
    }
    if pd.isna(theme["w"]):
        theme["w"] = theme["median"]
    # 基准
    bench = {}
    if not quote.empty:
        qi = quote.set_index("code")
        for c, nm in BENCH.items():
            if c in qi.index:
                bench[nm] = float(qi.loc[c, "pct_chg"])
    bench5, bench20 = {}, {}
    if not kline.empty:
        for c, nm in BENCH.items():
            sig = kline[kline.code == c].sort_values("date")
            if len(sig) > 6:
                bench5[nm] = float((sig.close.iloc[-1] / sig.close.iloc[-6] - 1) * 100)
            if len(sig) > 21:
                bench20[nm] = float((sig.close.iloc[-1] / sig.close.iloc[-21] - 1) * 100)

    # 温度打分（0–100）
    hist_amt = hist.groupby("day").amount_yi.sum().reindex(days)
    amt_ratio = stats["amount"] / hist_amt.iloc[:-1].mean() if len(days) > 1 else 1.0
    hi_n = int((panel.dist_hi60 > -1.5).sum())
    up_ratio = theme["up"] / max(theme["n"], 1)
    score = 50 + 22 * max(-1, min(1, theme["median"] / 4)) + 22 * (up_ratio - 0.5) + \
        8 * max(-1, min(1, amt_ratio - 1)) + 6 * (hi_n / max(theme["n"], 1) - 0.08)
    score = max(2, min(98, score))
    mood = ("冰点" if score < 25 else "偏冷" if score < 45 else "常温" if score < 62 else
            "升温" if score < 80 else "过热")

    # ---------------- 层 × 时间
    lay = panel[panel.market == "A"].groupby("ai_layer")
    layer_today = lay.agg(n=("name_cn", "size"), med=("pct_chg", "median"), mean=("pct_chg", "mean"),
                          p10=("pct_chg", lambda s: s.quantile(0.1)),
                          p90=("pct_chg", lambda s: s.quantile(0.9)),
                          r5=("ret5", "median"), r10=("ret10", "median"), r20=("ret20", "median"),
                          amt=("amount_yi", "sum"), pe=("pe_ntm", "median"),
                          pe_ttm=("pe_ttm", "median"), mv=("mcap_yi", "sum"))
    layer_today = layer_today.reindex([l for l in LAYERS if l in layer_today.index])
    layer_today["disp"] = layer_today.p90 - layer_today.p10
    layer_today["amt_share"] = (layer_today.amt / layer_today.amt.sum() * 100).round(1)

    # 层的时间序列（每日中位涨跌）
    code2layer = dict(zip(panel.code.dropna(), panel.ai_layer))
    hist_a = hist[hist.code.isin(code2layer)].copy()
    hist_a["ai_layer"] = hist_a.code.map(code2layer)
    lay_day = hist_a.pivot_table(index="day", columns="ai_layer", values="pct_chg", aggfunc="median").reindex(days)
    # 等权净值（名单 A 股）
    eq_pct = hist_a.groupby("day").pct_chg.median().reindex(days)
    eq_nav = (1 + eq_pct.fillna(0) / 100).cumprod()
    # 每层 / 名单整体的量能倍数（今日成交额 ÷ 之前各日均值）
    lay_amt = hist_a.pivot_table(index="day", columns="ai_layer", values="amount_yi", aggfunc="sum").reindex(days)
    lay_amt_ratio = {l: float(lay_amt[l].iloc[-1] / lay_amt[l].iloc[:-1].mean())
                     for l in lay_amt.columns if len(days) > 2 and lay_amt[l].iloc[:-1].mean()}
    theme_amt = hist_a.groupby("day").amount_yi.sum().reindex(days)
    theme_amt_ratio = float(theme_amt.iloc[-1] / theme_amt.iloc[:-1].mean()) if len(days) > 2 else float("nan")
    layer_today["amt_ratio"] = [lay_amt_ratio.get(l, float("nan")) for l in layer_today.index]

    # ---------------- 板块
    boards = {}
    cons_map = pd.read_csv("em_cons.csv", encoding="utf-8-sig", dtype=str)
    bstat = pd.read_csv("em_board_stats.csv", encoding="utf-8-sig", dtype={"bk": str})
    bname = dict(zip(bstat.bk, bstat.board)) if not bstat.empty else {}
    bname.update(dict(zip(cons_map.bk, cons_map.board)))
    blayer = dict(zip(bstat.bk, bstat.ai_layer)) if "ai_layer" in bstat else {}

    hist_b = hist.merge(cons_map.rename(columns={"code6": "code"})[["bk", "code"]], on="code", how="inner")
    board_day = hist_b.pivot_table(index="day", columns="bk", values="pct_chg", aggfunc="median").reindex(days)
    board_amt = hist_b.pivot_table(index="day", columns="bk", values="amount_yi", aggfunc="sum").reindex(days)
    board_n = hist_b.groupby("bk").code.nunique()
    board_up = hist_b[hist_b.day == day].assign(up=lambda d: d.pct_chg > 0).groupby("bk").up.mean()

    # 板块 N 日收益 = 成分 N 日收益的中位
    pxb = hist_b.pivot_table(index="day", columns="code", values="close").reindex(days)
    board_r5, board_r20 = {}, {}
    for bk, grp in hist_b[hist_b.day == day].groupby("bk"):
        codes = [c for c in grp.code if c in pxb.columns]
        for lag, box in ((5, board_r5), (20, board_r20)):
            rs = []
            for c in codes:
                ser = pxb[c].dropna()
                if len(ser) > lag:
                    rs.append((ser.iloc[-1] / ser.iloc[-1 - lag] - 1) * 100)
            box[bk] = float(pd.Series(rs).median()) if rs else float("nan")

    name_codes = set(panel[panel.market == "A"].code)
    board_rows = []
    for bk in board_day.columns:
        s = board_day[bk].dropna()
        if len(s) < 2 or bk not in bname:
            continue
        today_members = hist_b[(hist_b.day == day) & (hist_b.bk == bk)]
        listed = today_members[today_members.code.isin(name_codes)]
        board_rows.append({
            "bk": bk, "board": bname.get(bk, bk), "layer": blayer.get(bk, ""),
            "n": int(board_n.get(bk, 0)),
            "med": float(today_members.pct_chg.median()) if len(today_members) else float("nan"),
            "up_ratio": float(board_up.get(bk, float("nan"))),
            "amt": float(board_amt[bk].iloc[-1]) if bk in board_amt else float("nan"),
            "r5": board_r5.get(bk, float("nan")), "r20": board_r20.get(bk, float("nan")),
            "amt_ratio": float(board_amt[bk].iloc[-1] / board_amt[bk].iloc[:-1].mean())
            if bk in board_amt and len(days) > 2 else float("nan"),
            "listed_n": int(len(listed)),
            "listed_med": float(listed.pct_chg.median()) if len(listed) else float("nan"),
            "leader": "", "leader_pct": float("nan"),
        })
    bdf = pd.DataFrame(board_rows)
    # 板块龙头（成交额 ≥ 1 亿的最大涨幅）
    if not bdf.empty:
        todays = hist[hist.day == day]
        for i, r in bdf.iterrows():
            mem = hist_b[(hist_b.day == day) & (hist_b.bk == r.bk)]
            cand = todays[todays.code.isin(set(mem.code)) & (todays.amount_yi >= 1)]
            if cand.empty:
                continue
            top = cand.nlargest(1, "pct_chg").iloc[0]
            bdf.at[i, "leader"] = top["name"]
            bdf.at[i, "leader_pct"] = top["pct_chg"]
        bdf["gap"] = bdf.listed_med - bdf.med          # 名单 − 板块
    # 名单外的强势候选
    cand_rows = []
    if not bdf.empty and not hist.empty:
        todays = hist[hist.day == day]
        for bk, grp in hist_b[hist_b.day == day].groupby("bk"):
            mem = set(grp.code)
            cand = todays[todays.code.isin(mem) & (todays.amount_yi >= 3) & (~todays.code.isin(name_codes))]
            for _, rr in cand.nlargest(2, "pct_chg").iterrows():
                if rr.pct_chg < 4:
                    continue
                cand_rows.append({"board": bname.get(bk, bk), "name": rr["name"], "code": rr.code,
                                  "pct": rr.pct_chg, "amt": rr.amount_yi})
    cands = pd.DataFrame(cand_rows).drop_duplicates(subset=["code"]).nlargest(12, "pct") if cand_rows else pd.DataFrame()

    # ---------------- 异动
    moves = []
    pa = panel[panel.market == "A"].copy()
    for _, r in pa.iterrows():
        tags = []
        if r.limit_up:
            tags.append(("涨停", UP))
        if r.limit_down:
            tags.append(("跌停", DOWN))
        if not pd.isna(r.dist_hi60) and r.dist_hi60 > -1.0:
            tags.append(("60日新高", "#d29922"))
        if not pd.isna(r.dist_lo250) and r.dist_lo250 < 3:
            tags.append(("逼近一年低", "#6e7681"))
        if not pd.isna(r.amt_mult) and r.amt_mult >= 1.8:
            tags.append((f"量能 {r.amt_mult:.1f}×", "#58a6ff"))
        if not pd.isna(r.sigma60) and not pd.isna(r.pct_chg) and r.sigma60 > 0:
            z = r.pct_chg / r.sigma60
            if abs(z) >= 1.8:
                tags.append((f"{z:+.1f}σ", "#bc8cff"))
        if tags:
            moves.append({"name": r.name_cn, "code": r.code, "layer": r.ai_layer,
                          "pct": r.pct_chg, "amt": r.amount_yi, "tags": tags,
                          "amt_mult": r.amt_mult, "ret5": r.ret5, "turn": r.turn})
    moves = sorted(moves, key=lambda m: -(abs(m["pct"]) if not pd.isna(m["pct"]) else 0))
    # 异动个股 → 所属板块当日中位（判断独走 vs 共振）
    code_boards = cons_map.groupby("code6").bk.apply(list).to_dict()
    for m in moves:
        bls = code_boards.get(m["code"], [])
        sub = bdf[bdf.bk.isin(bls)] if not bdf.empty else pd.DataFrame()
        m["board"] = "、".join(sub.nlargest(2, "n").board.tolist()) if not sub.empty else "—"
        m["board_med"] = float(sub.med.mean()) if not sub.empty else float("nan")

    return dict(gs=gs, panel=panel, day=day, days=days, stats=stats, theme=theme, bench=bench,
                bench5=bench5, bench20=bench20, score=score, quote_day=quote_day,
                lay_amt=lay_amt, board_amt=board_amt, mood=mood, layer=layer_today, lay_day=lay_day,
                eq_nav=eq_nav, eq_pct=eq_pct, m_med=m_med, bdf=bdf, cands=cands, moves=moves,
                amt_ratio=amt_ratio, theme_amt_ratio=theme_amt_ratio, hi_n=hi_n,
                boards=bdf, kline=kline, hist=hist, cons_day=cons_day, retro=retro,
                hk_cons_day=hk_cons_day, hk_hist_src=hk_hist_src)


# ------------------------------------------------------------------ 图
def sp_tip(r):
    """全表 40 日走势的悬停提示"""
    v = r.spark if isinstance(r.spark, (list, tuple)) else []
    if len(v) < 2 or not v[0]:
        return ""
    chg = (v[-1] / v[0] - 1) * 100
    return (f"{r.name_cn} · 最近 40 个交易日\n"
            f"{chg:+.1f}%（{min(v):.0f} ~ {max(v):.0f}）\n"
            f"今日 {r.pct_chg:+.2f}%")


def sparkline(vals, w=86, h=22, color="#58a6ff", tip=""):
    if not isinstance(vals, (list, tuple)) or len(vals) < 2:
        return "<span class='na'>—</span>"
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = [(i / (len(vals) - 1) * (w - 2) + 1, h - 2 - (v - lo) / rng * (h - 4)) for i, v in enumerate(vals)]
    d = " ".join(("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    c = UP if vals[-1] >= vals[0] else DOWN
    tt = f" data-tip='{esc(tip)}'" if tip else ""
    return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'{tt}>"
            f"<path d='{d}' fill='none' stroke='{c}' stroke-width='1.4'/></svg>")


def line_chart(days, series, title, sub, W=1120, H=300, fmt="{:.1f}%", zero=True, ylab=""):
    """多序列折线。series: [{name, color, values(list, 可含 nan), dash}]"""
    L, R, T, B = 62, 118, 46, 34
    allv = [v for s in series for v in s["values"] if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not allv:
        return "<div class='note'>无数据</div>"
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.12 or 1
    lo, hi = lo - pad, hi + pad
    # 刻度取整（1/2/2.5/5 × 10^k），避免出现 -26.2% 这种读数
    raw = (hi - lo) / 4
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = min([m * mag for m in (0.5, 1, 2, 2.5, 5, 10, 20)],
               key=lambda c: abs(math.log(c / raw))) if raw > 0 else 1
    lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
    n = len(days)

    def xx(i): return L + (W - L - R) * (i / max(n - 1, 1))
    def yy(v): return T + (H - T - B) * (hi - v) / (hi - lo)

    p = [f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' font-family='ui-monospace,Menlo,Consolas,monospace'>"]
    p.append(f"<text x='{L}' y='20' fill='#e6edf3' font-size='13' font-weight='600'>{esc(title)}</text>")
    p.append(f"<text x='{L}' y='37' fill='#8b949e' font-size='11'>{esc(sub)}</text>")
    ticks = max(2, int(round((hi - lo) / step)))
    for k in range(ticks + 1):
        v = lo + step * k
        y = yy(v)
        p.append(f"<line x1='{L}' y1='{y:.1f}' x2='{W-R}' y2='{y:.1f}' stroke='#232b36'/>")
        p.append(f"<text x='{L-8}' y='{y+4:.1f}' fill='#6e7681' font-size='10' text-anchor='end'>{fmt.format(v)}</text>")
    if zero and lo < 0 < hi:
        p.append(f"<line x1='{L}' y1='{yy(0):.1f}' x2='{W-R}' y2='{yy(0):.1f}' stroke='#3d4a5c' stroke-dasharray='3 3'/>")
    step = max(1, n // 9)
    ticks_x = list(range(0, n, step))
    if ticks_x[-1] != n - 1:
        ticks_x = [i for i in ticks_x if n - 1 - i >= step * 0.6] + [n - 1]
    for i in ticks_x:
        p.append(f"<text x='{xx(i):.1f}' y='{H-B+16}' fill='#6e7681' font-size='9.5' text-anchor='middle'>{days[i][4:6]}-{days[i][6:8]}</text>")
    labels = []
    for s in series:
        seg, started = [], False
        for i, v in enumerate(s["values"]):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                if seg:
                    p.append(f"<path d='{' '.join(seg)}' fill='none' stroke='{s['color']}' stroke-width='1.9'"
                             + (" stroke-dasharray='4 3'" if s.get("dash") else "") + "/>")
                seg, started = [], False
                continue
            seg.append(("M" if not seg else "L") + f"{xx(i):.1f},{yy(v):.1f}")
            started = True
        if seg:
            p.append(f"<path d='{' '.join(seg)}' fill='none' stroke='{s['color']}' stroke-width='1.9'"
                     + (" stroke-dasharray='4 3'" if s.get("dash") else "") + "/>")
        # 右边标签（先收集，再按纵向防重叠排布）
        last = next((v for v in reversed(s["values"]) if v is not None and not (isinstance(v, float) and math.isnan(v))), None)
        if last is not None:
            labels.append([yy(last), s["name"], s["color"]])
        for i, v in enumerate(s["values"]):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            p.append(f"<circle cx='{xx(i):.1f}' cy='{yy(v):.1f}' r='7' fill='transparent' "
                     f"data-tip='{esc(s['name'])} · {days[i][4:6]}-{days[i][6:8]} · {fmt.format(v)}'/>")
    labels.sort()
    for i in range(1, len(labels)):
        if labels[i][0] - labels[i - 1][0] < 14:
            labels[i][0] = labels[i - 1][0] + 14
    for y, name, col in labels:
        p.append(f"<text x='{W-R+8}' y='{min(y, H-B+2):.1f}' fill='{col}' font-size='10.5'>{esc(name)}</text>")
    p.append("</svg>")
    return "".join(p)


def heatgrid(rows, cols, values, title, sub, W=1120, rowh=38):
    """行 × 列 热力矩阵。values[r][c]"""
    L, T = 96, 64
    cw = (W - L - 20) / len(cols)
    H = T + rowh * len(rows) + 26
    p = [f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' font-family='ui-monospace,Menlo,Consolas,monospace'>"]
    p.append(f"<text x='0' y='18' fill='#e6edf3' font-size='13' font-weight='600'>{esc(title)}</text>")
    p.append(f"<text x='0' y='35' fill='#8b949e' font-size='11'>{esc(sub)}</text>")
    for j, c in enumerate(cols):
        p.append(f"<text x='{L+cw*(j+0.5):.1f}' y='{T-10}' fill='#8b949e' font-size='10.5' text-anchor='middle'>{esc(c)}</text>")
    for i, r in enumerate(rows):
        y = T + i * rowh
        p.append(f"<text x='{L-10}' y='{y+rowh/2+4:.1f}' fill='#c9d5e1' font-size='11' text-anchor='end'>{esc(r)}</text>")
        for j in range(len(cols)):
            v = values[i][j]
            bg, fg = heat_color(v)
            tip = "" if v is None or (isinstance(v, float) and math.isnan(v)) else f" data-tip='{esc(r)} · {esc(c)} · {v:+.2f}%'"
            p.append(f"<rect x='{L+cw*j+2:.1f}' y='{y+3:.1f}' width='{cw-4:.1f}' height='{rowh-6:.1f}' rx='4' fill='{bg}'{tip}/>")
            txt = "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1f}%"
            p.append(f"<text x='{L+cw*(j+0.5):.1f}' y='{y+rowh/2+4:.1f}' fill='{fg}' font-size='11' text-anchor='middle'>{txt}</text>")
    p.append("</svg>")
    return "".join(p)


def figbox(groups, figs):
    """多口径切换器。groups=[(dim, [(取值, 标签, 是否默认), ...], 组标题), ...]
    figs=[(key, svg), ...]，key = 各组当前取值用 _ 连接。"""
    out = ['<div class="figbox">']
    default = []
    for dim, opts, glabel in groups:
        out.append(f'<div class="chips" data-dim="{dim}"><span class="lbl">{esc(glabel)}</span>')
        for val, label, on in opts:
            out.append(f'<button class="chip{" on" if on else ""}" data-v="{val}">{esc(label)}</button>')
            if on:
                default.append(val)
        out.append("</div>")
    dk = "_".join(default)
    for key, svg in figs:
        hide = "" if key == dk else " style='display:none'"
        out.append(f'<div data-fig="{key}"{hide}>{svg}</div>')
    out.append("</div>")
    return "".join(out)


def quadrant(rows, title, sub, W=1120, H=420, foot=""):
    """动量 × 量能变化 象限：rows=[{name,color,x(量能倍数),y(当日中位),size}]"""
    L, R, T, B = 62, 30, 50, 42
    xs = [r["x"] for r in rows if not pd.isna(r["x"])]
    ys = [r["y"] for r in rows if not pd.isna(r["y"])]
    if not xs or not ys:
        return "<div class='note'>无数据</div>"
    x0, x1 = min(0.9, min(xs) - .05), max(1.15, max(xs) + .05)
    y0, y1 = min(min(ys) - 1.5, -2.0), max(max(ys) + 1.5, 2.0)

    def xx(v): return L + (W - L - R) * (v - x0) / (x1 - x0)
    def yy(v): return T + (H - T - B) * (y1 - v) / (y1 - y0)

    p = [f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' font-family='ui-monospace,Menlo,Consolas,monospace'>"]
    p.append(f"<text x='{L}' y='20' fill='#e6edf3' font-size='13' font-weight='600'>{esc(title)}</text>")
    p.append(f"<text x='{L}' y='37' fill='#8b949e' font-size='11'>{esc(sub)}</text>")
    p.append(f"<rect x='{L}' y='{T}' width='{W-L-R}' height='{H-T-B}' fill='#12171e' stroke='#232b36' rx='6'/>")
    p.append(f"<line x1='{xx(1):.1f}' y1='{T}' x2='{xx(1):.1f}' y2='{H-B}' stroke='#3d4a5c' stroke-dasharray='4 4'/>")
    p.append(f"<line x1='{L}' y1='{yy(0):.1f}' x2='{W-R}' y2='{yy(0):.1f}' stroke='#3d4a5c' stroke-dasharray='4 4'/>")
    for lbl, tx, ty, col, anch in (("放量上涨", W - R - 14, T + 17, UP, "end"),
                                   ("放量下跌", W - R - 14, H - B - 9, DOWN, "end"),
                                   ("缩量上涨", L + 12, T + 17, "#8b949e", "start"),
                                   ("缩量下跌", L + 12, H - B - 9, "#8b949e", "start")):
        p.append(f"<text x='{tx}' y='{ty}' fill='{col}' font-size='10.5' text-anchor='{anch}'>{lbl}</text>")
    draw = [r for r in rows if not (pd.isna(r["x"]) or pd.isna(r["y"]))]
    draw.sort(key=lambda r: (-r["y"], r["x"]))
    boxes = []
    for r in draw:
        rad = 5 + 9 * math.sqrt(min(r.get("size", 1) / 300, 1))
        cx_, cy_ = xx(r["x"]), yy(r["y"])
        tip = esc(f"{r['name']}\n量能 {r['x']:.2f}× · 动量 {r['y']:+.2f}%")
        if r.get("n"):
            tip += esc(f" · {int(r['n'])} 家")
        p.append(f"<circle cx='{cx_:.1f}' cy='{cy_:.1f}' r='{rad:.1f}' fill='{r['color']}' opacity='.62' "
                 f"stroke='{r['color']}' data-tip='{tip}'/>")
        for dy in (4, -8, 16, -20, 28):
            for dx, anch in ((rad + 3, "start"), (-rad - 3, "end")):
                bx, by = cx_ + dx, cy_ + dy
                est = 10.8 * len(str(r["name"]))
                if anch == "start" and bx + est > W - R - 8:
                    continue
                if anch == "end" and bx - est < L + 4:
                    continue
                if any(abs(bx - b0) < 46 and abs(by - b1) < 11 for b0, b1 in boxes):
                    continue
                boxes.append((bx, by))
                p.append(f"<text x='{bx:.1f}' y='{by:.1f}' fill='#c9d5e1' font-size='10' text-anchor='{anch}'>{esc(r['name'])}</text>")
                dy = None
                break
            if dy is None:
                break
    for v in [x for x in (0.8, 1.0, 1.25, 1.5, 2.0) if x0 <= x <= x1]:
        p.append(f"<text x='{xx(v):.1f}' y='{H-B+16}' fill='#6e7681' font-size='9.5' text-anchor='middle'>{v:.2f}×</text>")
    for v in range(int(y0), int(y1) + 1, 2 if (y1 - y0) < 16 else 5):
        if v == 0:
            continue
        p.append(f"<text x='{L-8}' y='{yy(v)+4:.1f}' fill='#6e7681' font-size='9.5' text-anchor='end'>{v:+d}%</text>")
    p.append(f"<text x='{W-R}' y='{H-8}' fill='#6e7681' font-size='10' text-anchor='end'>{esc(foot or '横轴：成交额 / 20 日均　纵轴：当日中位涨跌　气泡：家数')}</text>")
    p.append("</svg>")
    return "".join(p)


def dotplot(df, W=1120, H=470, ymin=8, ymax=600):
    """分层 PE 打点"""
    L, R, T, B = 74, 34, 46, 84
    layers = [l for l in LAYERS if (df.ai_layer == l).any()]
    bw = (W - L - R) / len(layers)
    ly0, ly1 = math.log10(ymin), math.log10(ymax)

    def yy(v): return T + (H - T - B) * (ly1 - math.log10(max(min(v, ymax), ymin))) / (ly1 - ly0)

    p = [f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' font-family='ui-monospace,Menlo,Consolas,monospace'>"]
    p.append(f"<text x='{L}' y='20' fill='#e6edf3' font-size='13' font-weight='600'>分层 PE（前瞻共识 NTM）</text>")
    p.append(f"<text x='{L}' y='37' fill='#8b949e' font-size='11'>每家一个点，横线为层内中位；纵轴对数。只标 &gt;150x 与 &lt;12x 两头；空心 = 港股（与 A 股同 NTM 口径，只为区分市场）；负 PE 不画</text>")
    for v in (10, 20, 30, 50, 100, 200, 300):
        y = yy(v)
        p.append(f"<line x1='{L}' y1='{y:.1f}' x2='{W-R}' y2='{y:.1f}' stroke='#232b36'/>")
        p.append(f"<text x='{L-8}' y='{y+4:.1f}' fill='#6e7681' font-size='10.5' text-anchor='end'>{v}x</text>")
    for i, lay in enumerate(layers):
        sx = df[df.ai_layer == lay]
        cx = L + bw * (i + .5)
        p.append(f"<line x1='{cx-bw*.44:.1f}' y1='{T}' x2='{cx-bw*.44:.1f}' y2='{H-B}' stroke='#1b2430'/>")
        p.append(f"<text x='{cx}' y='{H-B+22}' fill='{COLOR[lay]}' font-size='11.5' text-anchor='middle'>{SHORT[lay]}</text>")
        p.append(f"<text x='{cx}' y='{H-B+38}' fill='#6e7681' font-size='10' text-anchor='middle'>n={len(sx)}</text>")
        vals = sx.pe_ntm.dropna()
        vals = vals[vals > 0]
        if len(vals):
            med = vals.median()
            p.append(f"<line x1='{cx-bw*.40:.1f}' y1='{yy(med):.1f}' x2='{cx+bw*.40:.1f}' y2='{yy(med):.1f}' stroke='{COLOR[lay]}' stroke-width='2' opacity='.45'/>")
            p.append(f"<text x='{cx+bw*.42:.1f}' y='{yy(med)+13:.1f}' fill='{COLOR[lay]}' font-size='10.5' text-anchor='end'>中位 {med:.0f}x</text>")
        for k, (_, r) in enumerate(sx.iterrows()):
            v = r.pe_ntm
            if pd.isna(v) or v <= 0:
                continue
            x = cx + ((k % 7) - 3) * (min(bw * .62, 78) / 7)
            y = yy(v)
            col = COLOR[lay]
            pe_lbl = "TTM" if r.market == "HK" else "NTM"
            tip = esc(f"{r.name_cn}\nPE {pe_lbl} {v:.1f}x · 今日 {r.pct_chg:+.2f}%")
            if r.market == "HK":
                p.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4.6' fill='none' stroke='{col}' stroke-width='1.6' opacity='.85' data-tip='{tip}'/>")
            else:
                p.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4.6' fill='{col}' opacity='.88' data-tip='{tip}'/>")
            if v > 150 or v < 12:
                p.append(f"<text x='{x+7:.1f}' y='{y+4:.1f}' fill='#9aa7b4' font-size='9.5'>{esc(r.name_cn)}</text>")
    p.append("</svg>")
    return "".join(p)


def scatter(df, W=1120, H=470):
    """PE_NTM × 隐含增速（PEG 视角）。虚线 = PEG 等值线，线下方 = 增速配得上估值。"""
    L, R, T, B = 74, 40, 46, 62
    if "g_fwd" not in df.columns:
        return "<div class='note'>无 FY1/FY2 共识，无法计算增速</div>"
    d = df[(df.pe_ntm > 0) & df.g_fwd.notna() & (df.g_fwd > 0)].copy()
    if d.empty:
        return "<div class='note'>无数据</div>"
    gx0, gx1 = 8.0, 400.0
    py0, py1 = 8.0, 400.0
    lx0, lx1 = math.log10(gx0), math.log10(gx1)
    ly0, ly1 = math.log10(py0), math.log10(py1)

    def xx(g): return L + (W - L - R) * (math.log10(min(max(g, gx0), gx1)) - lx0) / (lx1 - lx0)
    def yy(p): return T + (H - T - B) * (ly1 - math.log10(min(max(p, py0), py1))) / (ly1 - ly0)

    p = [f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' font-family='ui-monospace,Menlo,Consolas,monospace'>"]
    p.append(f"<text x='{L}' y='20' fill='#e6edf3' font-size='13' font-weight='600'>贵不贵 × 增速配不配（PEG 视角）</text>")
    p.append(f"<text x='{L}' y='37' fill='#8b949e' font-size='11'>横轴 FY2/FY1 净利润增速（对数），纵轴 PE_NTM（对数）；灰虚线 = PEG 1 / 2 / 3，线下方 = 增速配得上估值。仅 A 股 {len(d)} 家（港股缺 FY1/FY2 共识）</text>")
    for g in (10, 20, 40, 100, 200, 400):
        x = xx(g)
        p.append(f"<line x1='{x:.1f}' y1='{T}' x2='{x:.1f}' y2='{H-B}' stroke='#232b36'/>")
        p.append(f"<text x='{x:.1f}' y='{H-B+18}' fill='#6e7681' font-size='10.5' text-anchor='middle'>{g}%</text>")
    for v in (10, 20, 30, 50, 100, 200, 300, 500):
        y = yy(v)
        p.append(f"<line x1='{L}' y1='{y:.1f}' x2='{W-R}' y2='{y:.1f}' stroke='#232b36'/>")
        p.append(f"<text x='{L-8}' y='{y+4:.1f}' fill='#6e7681' font-size='10.5' text-anchor='end'>{v}x</text>")
    # PEG 等值线：PE = PEG × 增速
    for k, colr in ((1, "#58a6ff"), (2, "#3d4a5c"), (3, "#3d4a5c")):
        g_lo = max(gx0, py0 / k)
        g_hi = min(gx1, py1 / k)
        if g_hi <= g_lo:
            continue
        p.append(f"<line x1='{xx(g_lo):.1f}' y1='{yy(k*g_lo):.1f}' x2='{xx(g_hi):.1f}' y2='{yy(k*g_hi):.1f}' "
                 f"stroke='{colr}' stroke-dasharray='5 4' opacity='.65'/>")
        lx_, ly_ = xx(g_lo * 1.08), yy(k * g_lo * 1.08)
        p.append(f"<text x='{lx_:.1f}' y='{ly_-6:.1f}' fill='{colr}' font-size='9.5' text-anchor='start' opacity='.9'>PEG {k}</text>")
    p.append(f"<text x='{L+8}' y='{T+16:.0f}' fill='#f85149' font-size='10' opacity='.85'>↑ 贵：增速跟不上估值</text>")
    p.append(f"<text x='{W-R-8}' y='{H-B-10:.0f}' fill='#3fb950' font-size='10' text-anchor='end' opacity='.85'>↓ 便宜：增速配得上</text>")
    lx = L
    for lay in LAYERS:
        if not (d.ai_layer == lay).any():
            continue
        p.append(f"<circle cx='{lx+4:.1f}' cy='{H-16:.1f}' r='3.4' fill='{COLOR[lay]}' opacity='.8'/>")
        p.append(f"<text x='{lx+11:.1f}' y='{H-12:.1f}' fill='#8b949e' font-size='9.5'>{esc(SHORT[lay])}</text>")
        lx += 12 + 11 * len(SHORT[lay])
    mx = max(float(pd.to_numeric(d.mcap_yi, errors="coerce").fillna(0).max()), 1)
    for _, r in d.iterrows():
        rr = 4 + 13 * math.sqrt((r.mcap_yi or 0) / mx)
        col = COLOR.get(r.ai_layer, "#8b949e")
        peg = r.pe_ntm / r.g_fwd if r.g_fwd else float("nan")
        tip = esc(f"{r.name_cn} · {SHORT.get(r.ai_layer, '')}\nPE NTM {r.pe_ntm:.0f}x · 增速 {r.g_fwd:+.0f}%\nPEG {peg:.2f}"
                  + ("" if pd.isna(r.mcap_yi) else f" · 市值 {r.mcap_yi:.0f} 亿"))
        p.append(f"<circle cx='{xx(r.g_fwd):.1f}' cy='{yy(r.pe_ntm):.1f}' r='{rr:.1f}' fill='{col}' opacity='.5' "
                 f"stroke='{col}' data-tip='{tip}'/>")
        if r.g_fwd > gx1:
            p.append(f"<text x='{W-R-2:.1f}' y='{yy(r.pe_ntm)+3.5:.1f}' fill='{col}' font-size='10' opacity='.9'>▸</text>")
    # 标签：离 PEG=1 越远 + 市值越大越优先
    boxes = []
    pri = d.assign(score=(d.pe_ntm / d.g_fwd).apply(lambda v: abs(math.log10(v)) if v > 0 else 0)
                   + d.mcap_yi.fillna(0) / 8000).nlargest(15, "score")
    for _, r in pri.iterrows():
        x, y = xx(r.g_fwd) + 9, yy(r.pe_ntm) + 3.5
        if any(abs(x - bx) < 54 and abs(y - by) < 12 for bx, by in boxes):
            continue
        boxes.append((x, y))
        p.append(f"<text x='{x:.1f}' y='{y:.1f}' fill='#c9d5e1' font-size='9.5'>{esc(r.name_cn)}</text>")
    p.append("</svg>")
    return "".join(p)


def gauge(score, mood, tip=""):
    W, H = 1120, 74
    L, R = 0, 0
    p = [f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' font-family='ui-monospace,Menlo,Consolas,monospace'>"]
    if tip:
        p.append(f"<g data-tip='{esc(tip)}'>")
    stops = [(0, "#2b3947"), (25, "#3d6b8f"), (45, "#7d8894"), (62, "#d29922"), (80, "#f0883e"), (100, "#f85149")]
    def col(s):
        for i in range(len(stops) - 1):
            a, b = stops[i], stops[i + 1]
            if a[0] <= s <= b[0]:
                t = (s - a[0]) / (b[0] - a[0])
                ca = tuple(int(a[1][k:k + 2], 16) for k in (1, 3, 5))
                cb = tuple(int(b[1][k:k + 2], 16) for k in (1, 3, 5))
                return "#%02x%02x%02x" % tuple(int(ca[k] + (cb[k] - ca[k]) * t) for k in range(3))
        return stops[-1][1]
    for i in range(100):
        x = W * i / 100
        p.append(f"<rect x='{x:.1f}' y='26' width='{W/100+0.6:.1f}' height='14' fill='{col(i)}' opacity='.55'/>")
    x = W * score / 100
    p.append(f"<polygon points='{x-7:.1f},22 {x+7:.1f},22 {x:.1f},12' fill='#e6edf3'/>")
    p.append(f"<text x='{x:.1f}' y='60' fill='#e6edf3' font-size='13' font-weight='600' text-anchor='middle'>{mood} {score:.0f}</text>")
    for v, lbl in ((0, "冰点"), (25, "偏冷"), (45, "常温"), (62, "升温"), (80, "过热")):
        p.append(f"<text x='{W*v/100:.1f}' y='14' fill='#6e7681' font-size='9.5' text-anchor='{'start' if v==0 else 'middle'}'>{lbl}</text>")
    if tip:
        p.append("</g>")
    p.append("</svg>")
    return "".join(p)


# ------------------------------------------------------------------ 渲染
SWITCHER_JS = """<script>
document.querySelectorAll('.figbox').forEach(function (box) {
  var groups = [].slice.call(box.querySelectorAll('.chips'));
  function apply() {
    var key = groups.map(function (g) {
      var a = g.querySelector('.chip.on') || g.querySelector('.chip');
      return a ? a.dataset.v : '';
    }).join('_');
    [].slice.call(box.querySelectorAll('[data-fig]')).forEach(function (f) {
      f.style.display = f.dataset.fig === key ? '' : 'none';
    });
  }
  groups.forEach(function (g) {
    g.addEventListener('click', function (e) {
      var b = e.target.closest('.chip');
      if (!b || !g.contains(b)) return;
      [].slice.call(g.querySelectorAll('.chip')).forEach(function (c) { c.classList.toggle('on', c === b); });
      apply();
    });
  });
});

var tip = document.getElementById('tip');
document.addEventListener('mousemove', function (e) {
  var t = e.target.closest ? e.target.closest('[data-tip]') : null;
  if (!t) { tip.style.opacity = 0; return; }
  var txt = t.getAttribute('data-tip');
  if (tip.getAttribute('data-k') !== txt) { tip.textContent = txt; tip.setAttribute('data-k', txt); }
  var w = tip.offsetWidth, h = tip.offsetHeight;
  var x = e.clientX + 16, y = e.clientY + 16;
  if (x + w > window.innerWidth - 10) x = e.clientX - w - 16;
  if (y + h > window.innerHeight - 10) y = e.clientY - h - 16;
  tip.style.left = x + 'px'; tip.style.top = y + 'px'; tip.style.opacity = 1;
});
// 全表：排序 + 筛选
(function () {
  var tbl = document.getElementById('t54');
  if (!tbl) return;
  var body = tbl.tBodies[0];
  var rows = [].slice.call(body.rows);
  var q = document.getElementById('q54');
  var cnt = document.getElementById('cnt54');
  var st = { mkt: '', layer: '', text: '' };
  function apply() {
    var n = 0;
    rows.forEach(function (r) {
      var ok = (!st.mkt || r.dataset.mkt === st.mkt) &&
               (!st.layer || r.dataset.layer === st.layer) &&
               (!st.text || (r.dataset.key || '').indexOf(st.text) >= 0);
      r.style.display = ok ? '' : 'none';
      if (ok) n++;
    });
    if (cnt) cnt.textContent = n + ' / ' + rows.length;
  }
  if (q) q.addEventListener('input', function () { st.text = q.value.trim().toLowerCase(); apply(); });
  [].slice.call(document.querySelectorAll('[data-f]')).forEach(function (g) {
    g.addEventListener('click', function (e) {
      var b = e.target.closest('.chip'); if (!b) return;
      [].slice.call(g.querySelectorAll('.chip')).forEach(function (c) { c.classList.toggle('on', c === b); });
      st[g.dataset.f] = b.dataset.v; apply();
    });
  });
  var dir = {};
  [].slice.call(tbl.tHead.rows[0].cells).forEach(function (th, i) {
    if (th.hasAttribute('data-nosort')) return;
    th.classList.add('sortable');
    th.addEventListener('click', function () {
      var desc = dir[i] = !dir[i];
      [].slice.call(tbl.tHead.rows[0].cells).forEach(function (c) { c.classList.remove('asc', 'desc'); });
      th.classList.add(desc ? 'desc' : 'asc');
      rows.sort(function (a, b) {
        var x = a.cells[i], y = b.cells[i];
        if (!x || !y) return 0;
        var vx = x.dataset.sort, vy = y.dataset.sort;
        if (vx !== undefined && vy !== undefined) return desc ? vy - vx : vx - vy;
        if (vx !== undefined) return -1;
        if (vy !== undefined) return 1;
        return desc ? y.textContent.localeCompare(x.textContent) : x.textContent.localeCompare(y.textContent);
      });
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });
})();
</script>"""

CSS = """
:root{--bg:#0e1116;--panel:#161b22;--line:#232b36;--ink:#e6edf3;--dim:#8b949e;
--up:#f85149;--down:#3fb950;--part:#d29922;--accent:#58a6ff;--accent2:#bc8cff;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:40px 26px 90px}
.kicker{font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
h1{font-size:27px;margin:0 0 8px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13.5px;margin:0;max-width:86ch}
header{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:11px;margin:20px 0 6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
.card .v{font:600 23px/1.2 var(--mono)}
.card .k{color:var(--dim);font-size:11.8px;margin-top:5px}
.card .x{color:var(--dim);font-size:11px;margin-top:2px;font-family:var(--mono)}
h2{font-size:18px;margin:44px 0 10px;display:flex;gap:9px;align-items:baseline}
h2 .n{font:600 11.5px/1 var(--mono);color:var(--dim);border:1px solid var(--line);border-radius:5px;padding:4px 7px}
h3{font-size:14px;margin:22px 0 6px;color:#dbe6f0}
.chart{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 12px 8px;margin:14px 0 6px;overflow:hidden}
.chart svg{width:100%;height:auto;display:block}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
thead th{background:#12171e;color:var(--dim);font-size:11px;letter-spacing:.05em;text-transform:uppercase;font-weight:600;position:sticky;top:0}
tbody tr:hover{background:#171e28}
.note{color:var(--dim);font-size:12.3px;margin:9px 0 0}
.na{color:#4d5866}
.warn{border-left:3px solid var(--part);background:#1a1608;border-radius:6px;padding:11px 15px;margin:14px 0;font-size:12.8px;color:#e8dcc0}
.tag{font:600 10px/1 var(--mono);padding:3px 6px;border-radius:4px;margin-right:4px;white-space:nowrap}
.bullets{margin:10px 0 0;padding-left:18px;color:#c9d5e1;font-size:13.4px}
.bullets li{margin:5px 0}
.scroll{overflow-x:auto}
.chips{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:16px 0 -2px}
.chips .lbl{font:600 10.5px/1 var(--mono);color:var(--dim);letter-spacing:.06em;margin-right:2px}
.chip{font:500 11.5px/1.5 inherit;padding:4px 11px;border:1px solid var(--line);border-radius:999px;background:#12171e;color:var(--dim);cursor:pointer;transition:.12s}
.chip:hover{color:#c9d5e1;border-color:#31404f}
.chip.on{background:#1f6feb1f;border-color:#1f6feb88;color:#79b8ff}
.chip.on:hover{color:#a5ceff}
#tip{position:fixed;left:0;top:0;z-index:99;pointer-events:none;opacity:0;transition:opacity .1s;
background:#1b2230f5;border:1px solid #33414f;border-radius:7px;padding:6px 9px;color:#e6edf3;
font:12px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
box-shadow:0 8px 24px #00000066;white-space:pre-line;max-width:300px}
.tbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:14px 0 2px}
.tbar input{font:12.5px/1.6 inherit;padding:5px 10px;border:1px solid var(--line);border-radius:7px;
background:#12171e;color:var(--ink);outline:none;width:150px}
.tbar input:focus{border-color:#1f6feb88}
.tbar .cnt{font:600 11px/1 var(--mono);color:var(--dim);margin-left:auto}
th.sortable{cursor:pointer;user-select:none}
th.sortable:hover{color:#c9d5e1}
th.sortable::after{content:'⇅';font-size:9px;opacity:.35;margin-left:5px}
th.asc::after{content:'▲';opacity:1;color:#58a6ff}
th.desc::after{content:'▼';opacity:1;color:#58a6ff}
[data-tip]{cursor:crosshair}
[data-tip]:hover{filter:brightness(1.4)}
.figbox [data-fig]+[data-fig] .chart{margin-top:-2px}
code{font-family:var(--mono);font-size:12.2px;background:#1c2430;border:1px solid var(--line);border-radius:4px;padding:1px 5px}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);color:var(--dim);font-size:12.4px}
footer a,.kicker a{color:var(--accent);text-decoration:none;border-bottom:1px solid #58a6ff44}
footer a:hover,.kicker a:hover{color:#a5ceff}
"""

# 命令行参数在 import 时解析一次；OUT_PATH / SUMMARY_PATH 为 None 时沿用老行为
_DAY_ARG, OUT_PATH, SUMMARY_PATH = parse_args(sys.argv[1:])


def main():
    D = build()
    day = D["day"]
    panel, theme, stats, layer = D["panel"], D["theme"], D["stats"], D["layer"]
    days, bdf, cands, moves = D["days"], D["bdf"], D["cands"], D["moves"]
    bench = D["bench"]
    a_panel = panel[panel.market == "A"]

    # ---- 一句话结论
    bench300 = bench.get("沪深300", float("nan"))
    excess = theme["median"] - bench300 if not pd.isna(bench300) else float("nan")
    worst_layer = layer.med.idxmin() if not layer.empty else None
    best_layer = layer.med.idxmax() if not layer.empty else None
    bullets = []
    bullets.append(
        f"名单中位 <b>{theme['median']:+.2f}%</b>（等权 {theme['mean']:+.2f}%），"
        f"沪深300 {bench300:+.2f}% → <b>超额 {excess:+.2f}pt</b>；"
        f"{theme['up']} 涨 / {theme['down']} 跌，涨停 {theme['zt']} 家。")
    if not layer.empty:
        bullets.append(
            f"最弱 <b>{SHORT.get(worst_layer, worst_layer)}</b> {layer.loc[worst_layer, 'med']:+.2f}%，"
            f"最强 <b>{SHORT.get(best_layer, best_layer)}</b> {layer.loc[best_layer, 'med']:+.2f}%；"
            f"层间极差 {layer.med.max() - layer.med.min():.2f}pt，层内分化（P90−P10）"
            f"{layer.disp.median():.1f}pt。")
    b5 = D["bench5"].get("沪深300", float("nan"))
    b20 = D["bench20"].get("沪深300", float("nan"))
    t5, t20 = a_panel.ret5.median(), a_panel.ret20.median()
    if not pd.isna(b5) and not pd.isna(t5):
        bullets.append(
            f"窗口：名单中位 5 日 {t5:+.2f}%（沪深300 {b5:+.2f}%，超额 {t5 - b5:+.2f}pt）、"
            f"20 日 {t20:+.2f}%（沪深300 {b20:+.2f}%，超额 {t20 - b20:+.2f}pt）"
            f"——短窗口在修复还是继续跑输，决定这波是回调还是趋势反转。")
    bullets.append(
        f"量能：名单成交 <b>{theme['amount']:,.0f} 亿</b>，占全市场 {theme['amount'] / stats['amount'] * 100:.1f}%；"
        f"全市场 {stats['amount']:,.0f} 亿（20 日均 {D['amt_ratio'] * 100:.0f}%）。"
        f"全市场 {stats['up']} 涨 / {stats['down']} 跌，涨停 {stats['zt']}、跌停 {stats['dt']}。")
    if not bdf.empty:
        diverged = bdf[(bdf.listed_n >= 2) & (bdf.gap.notna())].sort_values("gap")
        if len(diverged):
            w = diverged.iloc[0]
            bullets.append(
                f"名单 vs 板块背离最大：<b>{w.board}</b>（名单 {w.listed_med:+.1f}% vs 板块 {w.med:+.1f}%，"
                f"差 {w.gap:+.1f}pt，{int(w.listed_n)} 家在名单内）——"
                + ("名单跑输，主题在别处扩散" if w.gap < 0 else "名单领涨，资金抱团龙头") + "。")

    # ---- 表格：层
    layer_rows = "".join(
        f"<tr><td><span style='display:inline-block;width:8px;height:8px;border-radius:99px;background:{COLOR[l]};margin-right:7px'></span>{esc(SHORT[l])}</td>"
        f"<td style='text-align:right'>{int(r.n)}</td>"
        f"<td style='text-align:right'>{pct(r.med, 2)}</td>"
        f"<td style='text-align:right'>{pct(r.r5, 2)}</td>"
        f"<td style='text-align:right'>{pct(r.r10, 2)}</td>"
        f"<td style='text-align:right'>{pct(r.r20, 2)}</td>"
        f"<td style='text-align:right'>{num(r.disp, 1, 'pt')}</td>"
        f"<td style='text-align:right'>{num(r.amt, 0, ' 亿')}</td>"
        f"<td style='text-align:right'>{num(r.amt_ratio, 2, '×')}</td>"
        f"<td style='text-align:right'>{num(r.pe, 1, 'x')}</td>"
        f"<td style='text-align:right'>{num(r.amt_share, 1, '%')}</td></tr>"
        for l, r in layer.iterrows())

    # ---- 名单 vs 所属 AI 板块（一行摘要，替代原来的 77 行表）
    probe_line = ""
    if not bdf.empty:
        sub_b = bdf[(bdf.listed_n >= 3) & bdf.r20.notna()]
        if len(sub_b):
            b20 = float(sub_b.r20.median())
            t20 = float(a_panel.ret20.median())
            diff = t20 - b20
            tag = ("名单跑赢 = 钱在往公认龙头集中" if diff > 3 else
                   "名单跑输 = 主题在别处扩散，龙头不是先锋" if diff < -3 else "两边同步，没有明显扩散或抱团")
            probe_line = (f"名单 20 日中位 <b>{t20:+.1f}%</b>，所属 AI 板块中位（{len(sub_b)} 个、名单内 ≥3 家）"
                          f"<b>{b20:+.1f}%</b>，差 <b>{diff:+.1f}pt</b> — {tag}。")

    # ---- 异动表
    move_rows = ""
    for m in moves:
        tags = "".join(f"<span class='tag' style='background:{c}22;color:{c};border:1px solid {c}55'>{esc(t)}</span>"
                       for t, c in m["tags"])
        lay_c = COLOR.get(m["layer"], "#8b949e")
        bmed = m.get("board_med", float("nan"))
        verdict = ""
        if not pd.isna(bmed) and not pd.isna(m["pct"]):
            verdict = ("独走" if (m["pct"] - bmed) > 2.5 else
                       "背离" if (m["pct"] - bmed) < -2.5 else "共振")
        move_rows += (
            f"<tr><td><b>{esc(m['name'])}</b><div style='font-size:11px;color:#6e7681'>{esc(m['code'])}</div></td>"
            f"<td><span style='color:{lay_c};font-size:12px'>{esc(SHORT.get(m['layer'], m['layer']))}</span></td>"
            f"<td style='text-align:right'>{pct(m['pct'], 2)}</td>"
            f"<td>{esc(m['board'])}</td>"
            f"<td style='text-align:right'>{pct(bmed, 1)} <span style='color:#6e7681;font-size:11px'>{verdict}</span></td>"
            f"<td style='text-align:right'>{num(m['turn'], 2, '%')}</td>"
            f"<td style='text-align:right'>{num(m['amt_mult'], 2, '×')}</td>"
            f"<td>{tags}</td></tr>")

    # ---- 全表
    table_rows = ""
    for _, r in panel.sort_values(["ai_layer", "pct_chg"], ascending=[True, False]).iterrows():
        pe = r.pe_ntm if not pd.isna(r.pe_ntm) else (r.pe_ttm if not pd.isna(r.pe_ttm) else float("nan"))
        pe_lbl = "NTM" if not pd.isna(r.pe_ntm) else "TTM"
        def ds(v, unit=""):
            return "" if pd.isna(v) else f" data-sort='{float(v):.4f}'"
        table_rows += (
            f"<tr data-mkt='{r.market}' data-layer='{esc(r.ai_layer)}' data-key='{esc((str(r.name_cn) + str(r.code)).lower())}'>"
            f"<td><b>{esc(r.name_cn)}</b></td>"
            f"<td><span style='color:{COLOR.get(r.ai_layer, '#8b949e')};font-size:12px'>{esc(SHORT.get(r.ai_layer, r.ai_layer))}</span>"
            f"<div style='font-size:10.5px;color:#6e7681'>{esc(r.ai_sector)}</div></td>"
            f"<td>{r.market}</td>"
            f"<td style='text-align:right'{ds(r.pct_chg)}>{pct(r.pct_chg, 2)}</td>"
            f"<td style='text-align:right'{ds(r.ret5)}>{pct(r.ret5, 1)}</td>"
            f"<td style='text-align:right'{ds(r.ret10)}>{pct(r.ret10, 1)}</td>"
            f"<td style='text-align:right'{ds(r.ret20)}>{pct(r.ret20, 1)}</td>"
            f"<td style='text-align:right'{ds(r.amt_mult)}>{num(r.amt_mult, 2, '×')}</td>"
            f"<td style='text-align:right'{ds(r.dist_hi60)}>{pct(r.dist_hi60, 0, sign=False)}</td>"
            f"<td style='text-align:right'{ds(pe)}>{num(pe, 0, 'x')} <span style='color:#6e7681;font-size:10px'>{pe_lbl}</span></td>"
            f"<td style='text-align:right'{ds(r.mcap_yi)}>{num(r.mcap_yi, 0)}</td>"
            f"<td>{sparkline(r.spark, tip=sp_tip(r))}</td></tr>")

    # ---- 层间强弱：口径（相对/绝对）× 范围（全部/20日/5日）预渲染
    lay_day = D["lay_day"]
    lay_amt = D.get("lay_amt", pd.DataFrame())
    board_amt = D.get("board_amt", pd.DataFrame())
    bench_daily = pd.Series(dtype=float)
    kl = D.get("kline") if isinstance(D.get("kline"), pd.DataFrame) else pd.DataFrame()
    if not kl.empty and "000300" in set(kl.code):
        b = kl[kl.code == "000300"].copy()
        b["d"] = b.date.astype(str).str.replace("-", "", regex=False)
        bser = b.set_index("d").close.reindex(days)
        bench_daily = bser.pct_change() * 100
    bnav = (1 + bench_daily.fillna(0) / 100).cumprod()
    lay_nav = {l: (1 + lay_day[l].fillna(0) / 100).cumprod() for l in LAYERS if l in lay_day.columns}

    def _rebase(s):
        return ((s / s.iloc[0] - 1) * 100) if len(s) and s.iloc[0] else s * 0

    SPAN_LBL = {"all": f"近 {len(days)} 个交易日", "20": "近 20 个交易日", "5": "近 5 个交易日"}
    lay_figs = []
    for span in ("5", "20", "all"):
        s0 = 0 if span == "all" else max(len(days) - (5 if span == "5" else 20) - 1, 0)
        dslice, bsub = days[s0:], bnav.iloc[s0:]
        for base in ("rel", "abs"):
            ser = []
            for l, nav in lay_nav.items():
                nsub = nav.iloc[s0:]
                if base == "rel":
                    vals = list((((nsub / nsub.iloc[0]) / (bsub / bsub.iloc[0])) - 1).mul(100).values)
                else:
                    vals = list(_rebase(nsub).values)
                ser.append({"name": SHORT[l], "color": COLOR[l], "values": vals})
            if base == "abs":
                ser.append({"name": "沪深300", "color": "#6e7681", "values": list(_rebase(bsub).values), "dash": True})
                title = "层间强弱（等权净值，绝对涨跌口径）"
                sub = f"各层内 A 股中位涨跌累乘；灰虚线 = 沪深300。{SPAN_LBL[span]}"
            else:
                title = "层间相对强弱（÷ 沪深300，起点 = 0%）"
                sub = f"层净值 ÷ 沪深300 净值 − 1：0 上方 = 跑赢基准，下方 = 跑输。{SPAN_LBL[span]}"
            lay_figs.append((f"{base}_{span}", f"<div class='chart'>{line_chart(dslice, ser, title, sub, H=300)}</div>"))
    lay_switch = figbox(
        [("base", [("rel", "相对沪深300", True), ("abs", "绝对涨跌", False)], "口径"),
         ("span", [("all", "全部", True), ("20", "近20日", False), ("5", "近5日", False)], "范围")],
        lay_figs)
    mkt_med_vals = list(D["m_med"].values)

    # 层 × 时间热力
    hrows = [SHORT[l] for l in LAYERS if l in D["lay_day"].columns]
    hvals = []
    for l in LAYERS:
        if l not in D["lay_day"].columns:
            continue
        r = layer.loc[l] if l in layer.index else None
        row = [r.med if r is not None else float("nan"), r.r5 if r is not None else float("nan"),
               r.r10 if r is not None else float("nan"), r.r20 if r is not None else float("nan")]
        # 按日
        row += [D["lay_day"][l].iloc[-k] if len(D["lay_day"]) >= k else float("nan") for k in range(1, 4)]
        hvals.append(row)
    hcols = ["今日", "5日累计", "10日累计", "20日累计", "前1日", "前2日"]

    # 象限：今日 / 5 日 / 20 日 三个窗口预渲染（横轴窗口均额，纵轴同窗口累计中位）
    def amt_win(amt_hist, span):
        k = 1 if span == "d" else (5 if span == "5" else 20)
        num = amt_hist.iloc[-k:].mean()
        rest = amt_hist.iloc[:-k] if k < len(amt_hist) else amt_hist.iloc[:-1]
        den = float(rest.mean())
        return float(num) / den if den else float("nan")

    q_spans = [("d", "今日", "今日成交额 / 其余日均", "今日中位涨跌", "动量 × 量能：今天谁在异动"),
               ("5", "近 5 日", "近 5 日均额 / 此前均额", "5 日累计中位", "动量 × 量能：这一周谁在放量启动"),
               ("20", "近 20 日", "近 20 日均额 / 此前均额", "20 日累计中位", "动量 × 量能：这一个月的主线是谁")]
    ycol = {"d": "med", "5": "r5", "20": "r20"}
    top_b = bdf[bdf.listed_n >= 1].nlargest(5, "amt") if not bdf.empty else pd.DataFrame()
    q_figs = []
    for span, lbl, xdesc, ydesc, title in q_spans:
        rows = []
        for l, r in layer.iterrows():
            x = amt_win(lay_amt[l], span) if l in lay_amt.columns else float("nan")
            rows.append({"name": SHORT[l], "color": COLOR[l], "x": float(x),
                         "y": float(r.get(ycol[span], float("nan"))), "size": 60,
                         "n": int(r.get("n", 0))})
        for _, b in top_b.iterrows():
            x = amt_win(board_amt[b.bk], span) if b.bk in board_amt.columns else float("nan")
            rows.append({"name": b.board, "color": "#8b949e", "x": float(x),
                         "y": float(b.get(ycol[span], float("nan"))), "size": 40,
                         "n": int(b.get("n", 0))})
        foot = f"横轴：{xdesc}　纵轴：{ydesc}　气泡：家数"
        sub = f"彩色 = 层，灰点 = 名单相关板块（成交额前 5）；右上 = 放量上涨，右下 = 放量下跌。{lbl}口径"
        q_figs.append((span, f"<div class='chart'>{quadrant(rows, title, sub, foot=foot)}</div>"))
    q_switch = figbox([("win", [(s, l2, s == "d") for s, l2, *_ in q_spans], "窗口")], q_figs)

    # ---- 盯什么整节已移除（规则提示与第 3/5 节的判定重复）
    n_a = len(a_panel)

    lay_chips = "".join(
        f"<button class=\"chip{' on' if i == 0 else ''}\" data-v=\"{'' if i == 0 else esc(l)}\">"
        f"{'全部' if i == 0 else esc(SHORT.get(l, l))}</button>"
        for i, l in enumerate([None] + [x for x in LAYERS if (panel.ai_layer == x).any()]))
    t54_bar = (
        '<div class="tbar">'
        '<input id="q54" type="search" placeholder="搜公司 / 代码">'
        f'<div class="chips" data-f="mkt" style="margin:0">'
        f'<span class="lbl">市场</span>'
        f'<button class="chip on" data-v="">全部</button><button class="chip" data-v="A">A</button>'
        f'<button class="chip" data-v="HK">HK</button></div>'
        f'<div class="chips" data-f="layer" style="margin:0"><span class="lbl">层</span>{lay_chips}</div>'
        f'<span class="cnt" id="cnt54">{len(panel)} / {len(panel)}</span>'
        '</div>')

    gauge_tip = (f"温度 {D['score']:.0f} / 100 · {D['mood']}\n"
                 f"名单中位 {theme['median']:+.2f}%　上涨 {theme['up']}/{theme['n']}\n"
                 f"量能 {mult(D['theme_amt_ratio'])}　新高 {D['hi_n']}/{n_a}\n"
                 "刻度：冰点 <25 · 偏冷 <45 · 常温 <62 · 升温 <80 · 过热 ≥80")
    ntm_n = int(a_panel.pe_ntm.notna().sum())
    ntm_hk = int(panel[panel.market == "HK"].pe_ntm.notna().sum())
    data_days = len(days)
    last_q = panel[panel.market == "HK"]
    retro_warn = ""
    quote_note = "最早一份快照（回溯补算）" if D.get("retro") else "不晚于数据日的最近一份快照"
    prev_amt = stats.get("amount_prev")
    chg_amt = ("<span class='na'>—</span>" if prev_amt is None or pd.isna(prev_amt) or not prev_amt
               else f"{stats['amount'] / prev_amt - 1:+.1%}")
    if D.get("retro"):
        retro_warn = (
            "<div class='warn'><b>回溯归档页：</b>这一页是事后补生成的。"
            f"行情、成交额、涨跌停、5/20 日动量、60 日高、量能倍数都只用到 {day} 及之前的数据（不含未来）；"
            f"但 {day} 当时没有腾讯快照，市值 / PE / 换手退回 {D['quote_day']} 的快照，"
            "所以第 6 节（估值）只看结构、别比数值。完整口径的历史页从有当日快照的那天开始。</div>")
    if D.get("hk_cons_day") == "—" and panel[panel.market == "HK"].close.isna().all():
        retro_warn += (
            f"<div class='warn'><b>港股缺失：</b>{day} 没有港股快照（<code>data/hk/{day}.csv</code>），"
            "本页港股 13 家的价格 / 动量 / PE 均为空，名单中位只算 A 股。</div>")

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 主题每日复盘 · {day}</title><style>{CSS}</style></head><body><div class="wrap">
<header>
<div class="kicker">AI 主题每日复盘 · {day}　（{data_days} 个交易日的盘后包，20 日动量可用）　·　<a href="archive.html">全部日期</a>　·　<a href="index.html">最新</a></div>
<h1>54 家名单当探针：主题今天几度、钱在哪个环节、名单还对不对</h1>
<p class="sub">这 54 家不是组合，是<b>采样点</b>——用它读 AI 这条线在中国的温度、广度、轮动和与概念板块的背离。
数据说明：A 股 41 家用通达信盘后包（收盘价、成交额、涨跌停），市值与 PE 用{quote_note}（{D['quote_day']}）；
港股 13 家用腾讯（快照 + 前复权日线），前瞻 PE 用 Yahoo 卖方共识；基准用腾讯指数；成交额占比 = 名单成交额 / 全市场成交额。</p>
</header>
{retro_warn}

<div class="cards">
<div class="card"><div class="v" style="color:{UP if theme['median'] > 0 else DOWN}">{theme['median']:+.2f}%</div>
<div class="k">名单中位涨跌</div><div class="x">等权 {theme['mean']:+.2f}% · 市值加权 {theme['w']:+.2f}%</div></div>
<div class="card"><div class="v">{theme['up']}/{theme['down']}</div><div class="k">涨 / 跌（{theme['n']} 家 A 股）</div>
<div class="x">涨停 {theme['zt']} · P90−P10 {theme['spread']:.1f}pt</div></div>
<div class="card"><div class="v">{theme['amount']:,.0f}<span style="font-size:13px;color:#8b949e"> 亿</span></div>
<div class="k">名单成交额</div><div class="x">占全市场 {theme['amount'] / stats['amount'] * 100:.1f}%（全市场 {stats['amount']:,.0f} 亿）</div></div>
<div class="card"><div class="v">{pct(theme['median'] - bench300, 2)}</div><div class="k">vs 沪深300</div>
<div class="x">沪深300 {bench300:+.2f}% · 创业板 {bench.get('创业板指', float('nan')):+.2f}%</div></div>
<div class="card"><div class="v">{stats['up'] / stats['n'] * 100:.0f}%</div><div class="k">全市场上涨家数占比</div>
<div class="x">涨停 {stats['zt']} / 跌停 {stats['dt']} · 中位 {stats['median']:+.2f}%</div></div>
</div>

<h2><span class="n">1</span>主题温度计</h2>
<div class="chart">{gauge(D['score'], D['mood'], gauge_tip)}</div>
<div class="cards" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">
<div class="card"><div class="k">名单中位涨跌（今日 / 5日 / 20日）</div>
<div class="v" style="font-size:17px">{theme['median']:+.2f}% <span style="color:#8b949e">/</span>
{pct(a_panel.ret5.median(), 1)} <span style="color:#8b949e">/</span> {pct(a_panel.ret20.median(), 1)}</div></div>
<div class="card"><div class="k">量能倍数（名单 / 均值）</div>
<div class="v" style="font-size:17px">{mult(D['theme_amt_ratio'])}</div>
<div class="x">全市场 {stats['amount']:,.0f} 亿（{mult(D['amt_ratio'])}，环比 {chg_amt}）</div></div>
<div class="card"><div class="k">创新高家数（距 60 日高 &lt;1.5%）</div>
<div class="v" style="font-size:17px">{D['hi_n']} / {n_a}</div><div class="x">名单 A 股口径</div></div>
<div class="card"><div class="k">分位差 P90−P10（分化度）</div>
<div class="v" style="font-size:17px">{theme['spread']:.1f}pt</div>
<div class="x">越大 = 越分歧；普涨时收敛</div></div>
</div>
<ul class="bullets">{"".join(f"<li>{b}</li>" for b in bullets)}</ul>

<h2><span class="n">2</span>广度与分化：均值会骗人</h2>
<div class="chart">{line_chart(days, [
    {"name": "名单中位(日涨跌)", "color": "#58a6ff", "values": [v for v in D['eq_pct'].values]},
    {"name": "全市场中位", "color": "#6e7681", "values": mkt_med_vals, "dash": True}],
    "每日中位涨跌：名单 vs 全市场", "同一天两条线的差 = 主题相对强弱的日度节奏（点越靠上说明主题当天越抗跌）", H=260)}</div>
{lay_switch}

<div class="scroll"><table><thead><tr><th>层</th><th style="text-align:right">家数</th>
<th style="text-align:right">今日中位</th><th style="text-align:right">5日</th><th style="text-align:right">10日</th><th style="text-align:right">20日</th>
<th style="text-align:right">层内P90−P10</th><th style="text-align:right">成交额</th><th style="text-align:right">量能</th>
<th style="text-align:right">PE_NTM中位</th><th style="text-align:right">成交额占比</th></tr></thead>
<tbody>{layer_rows}</tbody></table></div>

<h2><span class="n">3</span>结构轮动</h2>
<div class="chart">{heatgrid(hrows, hcols, hvals, "层 × 时间（中位涨跌）", "左边三列是累计口径，右边三列是逐日；看哪一层在由弱转强、哪一层连续复仇失败", W=1120)}</div>
{q_switch}
<p class="note">{probe_line}</p>
<h2><span class="n">4</span>异动榜（不是排序，是解释）</h2>
<div class="scroll"><table><thead><tr><th>个股</th><th>层</th><th style="text-align:right">涨跌</th><th>相关板块</th>
<th style="text-align:right">板块中位</th><th style="text-align:right">换手率</th><th style="text-align:right">量能</th><th>标签</th></tr></thead>
<tbody>{move_rows or "<tr><td colspan='8' class='note'>今日没有触发异动阈值的个股（|σ|≥1.8、量能≥1.8×、涨停/跌停、新高/新低）</td></tr>"}</tbody></table></div>
<p class="note">「独走 / 共振 / 背离」= 个股涨跌 − 相关板块中位涨跌，超过 ±2.5pt 才算独走或背离。
「换手率」A 股用流通换手（行情接口口径），港股无流通盘数据，退化为成交额 / 总市值（口径偏小，只做同市场内比较）。</p>

<h2><span class="n">5</span>全表（{len(panel)} 家）</h2>
<p class="sub">点表头排序（再点一次反向），上面可以按市场 / 层筛，也可以搜名字或代码。</p>
{t54_bar}
<div class="scroll"><table id="t54"><thead><tr><th>公司</th><th>层 / 细分</th><th>市场</th><th style="text-align:right">今日</th>
<th style="text-align:right">5日</th><th style="text-align:right">10日</th><th style="text-align:right">20日</th><th style="text-align:right">量能</th>
<th style="text-align:right">距60日高</th><th style="text-align:right">PE</th><th style="text-align:right">市值(亿)</th><th data-nosort>40日</th></tr></thead>
<tbody>{table_rows}</tbody></table></div>
<p class="note">PE 优先用卖方共识 PE_NTM（A 股 {ntm_n}/{n_a} 家，共识快照 {D['cons_day']}；港股 {ntm_hk}/13 家，共识快照 {D['hk_cons_day']}），缺的退回 PE_TTM。
NTM = w×FY1 + (1−w)×FY2，w = 距本财年结束剩余天数 / 365；港股按各公司财年（阿里/联想是 3 月年结）算，港股 EPS 多为人民币，
按当日 CNYHKD 折成港元再比价，所以同一家会出现 NTM 与 Yahoo forwardPE（只取 FY1）的小差。
「量能」= 当日成交额 / 前 20 日均；「距60日高」= 收盘 / 60 日最高 − 1（0% 即新高）；
港股历史来自 {D['hk_hist_src']}（约 400 根，新股只到上市日），当日快照补最后一根；港股共识 3 天刷新一次，Yahoo 限速时沿用上一份。</p>

<h2><span class="n">6</span>估值：贵不贵 × 增速配不配</h2>
<div class="chart">{dotplot(panel)}</div>
<div class="chart">{scatter(panel)}</div>
<p class="note">这两张是<b>结构图</b>，不是买卖信号：上图看「同一层里谁被定了高价」，下图看「这个高价有没有增速兜底」。
PEG 射线读法：<b>线下方 = 增速配得上估值，线上方 = 贵</b>；今天贴在最上方的三家（华虹 6.3、中芯 2.8、寒武纪 1.0 上下）就是「贵得有理由但也在透支」的位置。
增速是 FY2/FY1 的预测利润比值，<b>低基数转正会把它放大到几百个百分点</b>（沐曦 2225%、芯原 554%，已贴右边界的 ▸ 标记），那种 PEG 看着便宜并不作数。</p>



<h2><span class="n">7</span>口径与积累</h2>
<div class="warn"><b>口径：</b>① 名单 = GS 三张表去重 54 家（A 41 / HK 13），<b>不构成组合</b>，页面里没有权重和归因；
② A 股涨跌与成交额来自通达信官网盘后包（当日收盘口径，与交易所一致）；港股快照来自腾讯，5/10/20 日、量能与 40 日走势来自腾讯前复权日线；
③ 港股 13 家前瞻 PE 来自 Yahoo 卖方共识（覆盖 1–32 家机构，小票只有 1–4 家，参考度弱于 A 股的 20+ 家）；
分层 PE 打点图里港股是空心点（口径相同、只标记市场），PEG 散点图暂不含港股——Yahoo 那份共识没有 FY1/FY2 明细，算不出增速；
④ 板块口径 = 77 个 AI 相关东财概念板块（data/em_cons.csv，12,729 条成分关系，含截断板块），板块中位是成分等权中位；
⑤ 温度分数 = 中位涨跌 + 广度 + 量能 + 创新高家数的加权，0–100，仅用于横向比较，不是预测。</div>
<p class="note">积累：每跑一次 <code>python scripts/theme_data.py all</code> 就多一天盘后包与 K 线；
动量窗口（5/10/{data_days - 1} 日）随积累自动变长。<br>
生成命令：<code>python scripts/build_gs50_dashboard.py {day}</code>　·　数据：<code>data/mkt/</code>、<code>data/quote/</code>、<code>data/kline/</code></p>
<footer>AI 主题每日复盘 · 生成于 {pd.Timestamp.now():%Y-%m-%d %H:%M} · 数据日 {day}　·　<a href="archive.html">历史归档</a>　·　<a href="index.html">最新一期</a></footer>
</div>
<div id="tip"></div>
{SWITCHER_JS}
</body></html>"""

    os.makedirs("../docs", exist_ok=True)
    out = OUT_PATH or "../docs/gs50_dashboard.html"
    if os.path.dirname(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write(html)
    print(f"写出 {out}  {len(html) / 1024:.0f} KB　数据日 {day}　温度 {D['score']:.0f} {D['mood']}")
    print(f"名单中位 {theme['median']:+.2f}%　涨跌 {theme['up']}/{theme['down']}　异动 {len(moves)} 条　板块 {len(bdf)} 个")

    if SUMMARY_PATH:
        def _j(v):
            return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)
        os.makedirs(os.path.dirname(SUMMARY_PATH) or ".", exist_ok=True)
        payload = {
            "day": day, "score": _j(D["score"]), "mood": D["mood"],
            "median": _j(theme["median"]), "mean": _j(theme["mean"]), "w": _j(theme["w"]),
            "up": int(theme["up"]), "down": int(theme["down"]), "n": int(theme["n"]),
            "zt": int(theme["zt"]), "spread": _j(theme["spread"]),
            "amount": _j(theme["amount"]), "mkt_amount": _j(stats["amount"]),
            "amount_share": _j(theme["amount"] / stats["amount"] * 100) if stats["amount"] else None,
            "amt_ratio": _j(D["amt_ratio"] * 100),
            "bench300": _j(bench300), "excess": _j(excess),
            "ret5": _j(t5), "ret20": _j(t20), "bench5": _j(b5), "bench20": _j(b20),
            "moves": len(moves), "n_boards": int(len(bdf)), "days": len(D["days"]),
            "hi": int(D["hi_n"]),
            "ntm_a": int(a_panel.pe_ntm.notna().sum()),
            "layers": {SHORT.get(l, l): _j(r.med) for l, r in layer.iterrows()},
            "data_day": day, "built": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        }
        with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, allow_nan=False)
        print(f"写出 {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
