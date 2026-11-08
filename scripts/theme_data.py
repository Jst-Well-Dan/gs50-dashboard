"""主题复盘数据层 —— 只用「不封 IP」的源：通达信官网盘后包 + 腾讯行情 / K 线。

    python scripts/theme_data.py pkg 20261029        # 抓某日全市场盘后包 → data/mkt/
    python scripts/theme_data.py backfill 30         # 往回补到 30 个交易日（已有跳过）
    python scripts/theme_data.py quotes              # 54 家 A 股快照（市值/PE/换手/量比）
    python scripts/theme_data.py hk                  # 港股 13 家（快照+日线+前瞻 PE）→ data/hk/
    python scripts/theme_data.py kline               # 54 家 A 股日线 250 根 + 基准指数
    python scripts/theme_data.py all                 # backfill 30 + quotes + kline

产出：
    data/mkt/YYYYMMDD.csv        全市场截面（含成交额、涨跌停标记）
    data/quote/YYYYMMDD.csv      名单快照
    data/kline/daily.csv         个股/指数长表日线（前复权）
"""
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))

import pandas as pd

TDX_PACKAGE_URL = "http://www.tdx.com.cn/products/data/data/g4day/{ymd}.zip"   # 只有 80 端口通（443 握手失败）
TDX_BJ_FIRST_DAY = "20220506"
TDX_MIN_PRICED = {"sh": 10000, "sz": 3000, "bj": 50}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BENCH = {"sh000300": "沪深300", "sz399006": "创业板指", "sh000688": "科创50",
         "sh000001": "上证指数", "sz399905": "中证500",
         "hkHSTECH": "恒生科技"}   # 港股 13 家的同市场对照


# ----------------------------------------------------------------- 通达信盘后包
def _parse_package(content, ymd):
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())
    rows = []
    for market in ("sh", "sz", "bj"):
        cod_name, md1_name = f"{market}{ymd[2:]}.cod", f"{market}{ymd[2:]}.md1"
        if (market == "bj" and ymd < TDX_BJ_FIRST_DAY
                and cod_name not in names and md1_name not in names):
            continue
        if cod_name not in names or md1_name not in names:
            raise RuntimeError(f"盘后包缺少 {cod_name}/{md1_name}，格式可能已变")
        cod, md1 = archive.read(cod_name), archive.read(md1_name)
        if len(cod) % 150 or len(md1) % 512:
            raise RuntimeError(f"{market} 代码表/行情块长度不是整块，文件可能被截断")
        if len(cod) // 150 != len(md1) // 512:
            raise RuntimeError(f"{market} 代码表与行情块条数对不上")
        before, codes, seqs = len(rows), set(), set()
        for offset in range(0, len(cod), 150):
            record = cod[offset:offset + 150]
            code = record[0:6].rstrip(b"\x00 ").decode("ascii", "replace")
            seq = struct.unpack("<H", record[32:34])[0]
            if not re.fullmatch(r"[0-9]{6}", code):
                raise RuntimeError(f"{market} 代码表出现非 6 位代码 {code!r}")
            if code in codes or seq in seqs:
                raise RuntimeError(f"{market} 代码表重复（{code!r}, seq={seq}）")
            codes.add(code)
            seqs.add(seq)
            block = md1[seq * 512:(seq + 1) * 512]
            if len(block) != 512:
                raise RuntimeError(f"{market}{code} 行情块越界")
            prev_close = struct.unpack("<d", block[4:12])[0]
            open_, high, low, close = struct.unpack("<4d", block[12:44])
            amount = struct.unpack("<d", block[72:80])[0]
            if not all(math.isfinite(v) for v in (prev_close, open_, high, low, close, amount)):
                raise RuntimeError(f"{market}{code} 出现非有限数值")
            if close <= 0:
                continue
            volume = struct.unpack("<Q", block[56:64])[0]
            raw_name = record[40:72].split(b"\x00")[0]
            try:
                name = raw_name.decode("gbk").strip()
            except UnicodeDecodeError as exc:
                raise RuntimeError(f"{market}{code} 名称不是 GBK") from exc
            rows.append({"date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "market": market,
                         "code": code, "name": name, "prev_close": round(prev_close, 4),
                         "open": round(open_, 4), "high": round(high, 4), "low": round(low, 4),
                         "close": round(close, 4), "volume": volume, "amount": round(amount, 2)})
        if len(rows) - before < TDX_MIN_PRICED[market]:
            raise RuntimeError(f"{market} 只有 {len(rows) - before} 条有价记录，文件可能残缺")
    return rows


def _limit_pct(code, name):
    """涨跌停幅度（%）。ST 5%，创业板/科创板 20%，北交所 30%，其余 10%。"""
    if "ST" in (name or "").upper():
        return 5.0
    if code.startswith(("300", "301", "302", "688", "689")):
        return 20.0
    if code.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def _download(url, out_path, max_time=180):
    """用 curl 下载（python urllib 对这台机器的 tdx 站点会卡在 TLS / 502，curl 稳定）。
    返回 HTTP 状态码。"""
    cmd = ["curl", "-sS", "-L", "-o", out_path, "-w", "%{http_code}", "--max-time", str(max_time),
           "-A", UA, url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl {proc.returncode}: {proc.stderr.strip()[:160]}")
    return proc.stdout.strip()


def fetch_package(day, retries=3):
    """day: 'YYYYMMDD'。非交易日 / 包未发布 → None（404）。"""
    ymd = day.replace("-", "")
    url = TDX_PACKAGE_URL.format(ymd=ymd)
    last = None
    for attempt in range(retries):
        tmp = os.path.join(tempfile.gettempdir(), f"tdx_{ymd}.zip")
        try:
            code = _download(url, tmp)
            if code == "404":
                return None
            if code != "200":
                raise RuntimeError(f"HTTP {code}")
            with open(tmp, "rb") as fh:
                content = fh.read()
            if not content.startswith(b"PK"):
                raise RuntimeError("不是 zip 文件，可能是错误页")
            return _build_frame(content, ymd)
        except (RuntimeError, OSError, zlib.error, zipfile.BadZipFile, ValueError) as exc:
            last = exc
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        time.sleep(2 + attempt * 3)
    raise RuntimeError(f"{day} 盘后包抓取失败: {type(last).__name__}: {last}")


A_SHARE = r"^(?:00[0-3]|30[0-2]|60[0135]|68[89]|4[3-9]|8[3-9]|920)\d{3}$"
A_BY_MARKET = {"sz": r"^(?:00[0-3]|30[0-2])", "sh": r"^(?:60[0135]|68[89])",
               "bj": r"^(?:4[3-9]|8[3-9]|920)"}


def _build_frame(content, ymd):
    df = pd.DataFrame(_parse_package(content, ymd))
    # 只保留 A 股个股：代码规则 + 市场一致性（sh 的 000xxx 是指数，sz 的 000xxx 才是股票）
    df = df[df.code.str.match(A_SHARE)]
    mask = pd.Series(False, index=df.index)
    for market, pattern in A_BY_MARKET.items():
        mask |= (df.market == market) & df.code.str.match(pattern)
    df = df[mask]
    df["pct_chg"] = ((df.close / df.prev_close - 1) * 100).round(2)
    df["amount_yi"] = (df.amount / 1e8).round(3)
    df["limit_pct"] = [_limit_pct(c, n) for c, n in zip(df.code, df.name)]
    df["limit_up"] = df.close >= (df.prev_close * (1 + df.limit_pct / 100)).round(2) - 0.005
    df["limit_down"] = df.close <= (df.prev_close * (1 - df.limit_pct / 100)).round(2) + 0.005
    return df.reset_index(drop=True)


def save_package(df, day):
    os.makedirs("mkt", exist_ok=True)
    df.to_csv(f"mkt/{day}.csv", index=False, encoding="utf-8-sig")


def market_days():
    return sorted(f[:-4] for f in os.listdir("mkt")) if os.path.isdir("mkt") else []


def load_market(day):
    p = f"mkt/{day}.csv"
    if not os.path.exists(p):
        return None
    return pd.read_csv(p, encoding="utf-8-sig", dtype={"code": str})


def backfill(n=30, start=None, jobs=4, max_calendar=100):
    """从 start（默认今天）往回补 n 个交易日的盘后包。已有文件跳过；缺失（404 = 非交易日）继续往前。"""
    have = set(market_days())
    stop = datetime.strptime(start, "%Y%m%d").date() if start else date.today()
    cands, cursor = [], stop
    while len(cands) < n + 25 and (stop - cursor).days < max_calendar:
        ymd = cursor.strftime("%Y%m%d")
        if cursor.weekday() < 5 and ymd not in have:
            cands.append(ymd)
        cursor -= timedelta(days=1)

    got, swept = 0, 0
    for i in range(0, len(cands), jobs):
        if len(market_days()) >= n:
            break
        batch = cands[i:i + jobs]
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = {pool.submit(fetch_package, d): d for d in batch}
            for fut in as_completed(futures):
                day = futures[fut]
                try:
                    df = fut.result()
                except Exception as exc:
                    print(f"  ! {day} {type(exc).__name__}: {str(exc)[:90]}")
                    continue
                if df is None:
                    swept += 1
                else:
                    save_package(df, day)
                    got += 1
                    print(f"  + {day}  {len(df)} 只")
    days = market_days()
    print(f"盘后包：本次新增 {got} 个 → 共 {len(days)} 个交易日（{days[0]} ~ {days[-1]}，跳过 {swept} 个非交易日）")


# ----------------------------------------------------------------- 腾讯行情
def _prefix(code):
    if code[:2].lower() in ("sh", "sz", "bj", "hk"):   # hk 指数（hkHSTECH）后缀要大写，原样透传
        return code
    if code in ("000001", "000300", "000688", "000905", "000016") or code.startswith(("5", "6", "9")):
        return "sh" + code if not code.startswith("92") else "bj" + code
    return "sz" + code


def tencent_quotes(codes):
    """批量实时/收盘快照。返回 DataFrame：code,name,price,pct_chg,amount_yi,turnover,pe_ttm,pb,
    mcap_yi,float_mcap_yi,limit_up,limit_down,vol_ratio,pe_static,stale"""
    out = {}
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        url = "https://qt.gtimg.cn/q=" + ",".join(_prefix(c) for c in chunk)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        text = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "replace")
        for line in text.strip().split(";"):
            if "=" not in line or '"' not in line:
                continue
            vals = line.split('"')[1].split("~")
            if len(vals) < 53:
                continue
            key = line.split("=")[0].split("_")[-1]
            code = key[2:]

            def f(i):
                try:
                    return float(vals[i]) if vals[i] else 0.0
                except ValueError:
                    return 0.0
            out[code] = {"code": code, "name": vals[1], "price": f(3), "pct_chg": f(32),
                         "amount_yi": round(f(37) / 1e4, 3), "turnover": f(38), "pe_ttm": f(39),
                         "amplitude": f(43), "float_mcap_yi": f(44), "mcap_yi": f(45), "pb": f(46),
                         "limit_up": f(47), "limit_down": f(48), "vol_ratio": f(49), "pe_static": f(52),
                         # 快照自己的数据日期（30 号字段，形如 20261106150003）——历史上它是哪天的收盘靠它认
                         "snap_day": ("".join(ch for ch in vals[30] if ch.isdigit()) or "")[:8],
                         "stale": f(37) == 0 and f(3) == f(4) and f(3) > 0}
        time.sleep(0.15)
    return pd.DataFrame(list(out.values()))


def db_quotes(day):
    """A 股快照的「按日期版本」——腾讯快照只有当日值，tushare daily_basic 有历史。

    市值 / 流通市值 / 换手 / 量比 / PE / PB 取自 daily_basic(trade_date=day)，
    价格 / 涨跌 / 成交额 / 涨跌停取自当日盘后包（mkt/<day>.csv），两边按 6 位代码对齐，
    产出 schema 与腾讯快照完全一致的 quote/<day>.csv（页面不用改）。
    回溯补历史就用这个：`python scripts/theme_data.py dbquotes 20260915`
    """
    import tushare_client as tc
    p = f"mkt/{day}.csv"
    if not os.path.exists(p):
        raise SystemExit(f"{day} 没有盘后包 mkt/{day}.csv，先跑 pkg {day}")
    m = pd.read_csv(p, encoding="utf-8-sig", dtype={"code": str})
    g = pd.read_csv("gs50.csv", encoding="utf-8-sig", dtype=str).fillna("")
    codes = [c for c in g.a_code6 if c]
    pro = tc.pro()
    fields = "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pe_ttm,pb,total_mv,circ_mv"
    try:
        db = tc.call(pro.daily_basic, tries=6, trade_date=day, fields=fields)
    except Exception as exc:
        # 镜像端点（tl.kaixin8.top）会整段 SSL EOF / 池子耗尽；官方端点一次就能拉全市场，切过去只多一分钟
        print(f"! 镜像端点拿不到 {day} 的 daily_basic（{type(exc).__name__}: {exc}）→ 改走官方端点", flush=True)
        db = tc.call_official(tc.pro_official().daily_basic, tries=3, trade_date=day, fields=fields)
    if db is None or not len(db):
        raise SystemExit(f"daily_basic 没有 {day} 的数据")
    db = db.copy()
    db["code"] = db.ts_code.astype(str).str[:6]
    d = db.drop_duplicates("code").set_index("code")
    rows, miss = [], []
    for _, r in m[m.code.isin(set(codes))].iterrows():
        if r.code not in d.index:
            miss.append(r.code)
            continue
        b = d.loc[r.code]
        amp = ((r.high - r.low) / r.prev_close * 100) if r.prev_close else float("nan")
        rows.append({
            "code": r.code, "name": r.name, "price": r.close, "pct_chg": r.pct_chg,
            "amount_yi": r.amount_yi, "turnover": b.turnover_rate, "pe_ttm": b.pe_ttm,
            "amplitude": round(amp, 2) if amp == amp else float("nan"),
            "float_mcap_yi": round(b.circ_mv / 1e4, 2), "mcap_yi": round(b.total_mv / 1e4, 2),
            "pb": b.pb, "limit_up": r.limit_up, "limit_down": r.limit_down,
            "vol_ratio": b.volume_ratio, "pe_static": b.pe, "stale": False})
    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit(f"{day} 一份都对不上（daily_basic 当天没有这 54 家）")
    os.makedirs("quote", exist_ok=True)
    out.to_csv(f"quote/{day}.csv", index=False, encoding="utf-8-sig")
    print(f"行情（daily_basic）：{len(out)}/{len(codes)} 条 → quote/{day}.csv"
          + (f"　缺 {miss}" if miss else ""))
    return out


def fetch_hk_quotes(codes, day):
    """港股快照 + 前复权日线 + 共识 NTM PE 统一由 scripts/fetch_hk.py 负责（它写 hk/{day}.csv）

    原来这里只写当日快照，5/10/20 日动量、量能、40 日走势和前瞻 PE 都是空的；
    现在把取数逻辑收在 fetch_hk.py 一处，避免两个脚本写同一份文件、schema 打架。
    """
    import fetch_hk
    return fetch_hk.run(day)


# ----------------------------------------------------------------- 腾讯 K 线
KLINE_HOSTS = ["https://web.ifzq.gtimg.cn", "https://proxy.finance.qq.com/ifzqgtimg", "https://ifzq.gtimg.cn"]
_down_until = {}


def tencent_kline(code, count=260, adjust="qfq", retries=2):
    """日线。返回 DataFrame：date,open,high,low,close,volume"""
    symbol = _prefix(code)
    param = f"{symbol},day,,,{count},{adjust}"
    errors = []
    for _ in range(retries):
        for host in KLINE_HOSTS:
            if _down_until.get(host, 0) > time.time():
                continue
            url = host + "/appstock/app/fqkline/get"
            try:
                req = urllib.request.Request(url + "?param=" + param)
                req.add_header("User-Agent", UA)
                req.add_header("Referer", "https://gu.qq.com/")
                payload = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8"))
            except Exception as exc:
                errors.append(f"{host}:{type(exc).__name__}")
                _down_until[host] = time.time() + 120
                continue
            node = (payload or {}).get("data", {}).get(symbol)
            if not isinstance(node, dict):
                errors.append(f"{host}:no-node")
                _down_until[host] = time.time() + 120
                continue
            items = node.get(adjust + "day") or node.get("day")
            if not items:
                errors.append(f"{host}:empty")
                _down_until[host] = time.time() + 120
                continue
            rows = [{"code": code[-6:], "date": it[0], "open": float(it[1]), "close": float(it[2]),
                     "high": float(it[3]), "low": float(it[4]), "volume": float(it[5])} for it in items]
            return pd.DataFrame(rows)
        time.sleep(3)
    raise RuntimeError(f"K 线 {code} 三入口均不可用: {'; '.join(errors)}")


def refresh_kline(codes, count=260):
    """增量刷新 data/kline/daily.csv（按 code 覆盖最后 260 根）。"""
    os.makedirs("kline", exist_ok=True)
    path = "kline/daily.csv"
    old = pd.read_csv(path, encoding="utf-8-sig", dtype={"code": str}) if os.path.exists(path) else pd.DataFrame()
    frames, fail = [], []
    for i, c in enumerate(codes, 1):
        try:
            frames.append(tencent_kline(c, count=count))
        except Exception as exc:
            fail.append(f"{c}({type(exc).__name__})")
        if i % 20 == 0:
            print(f"  K 线 {i}/{len(codes)}")
        time.sleep(0.12)
    new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not new.empty and not old.empty:
        keep = old[~old.code.isin(set(new.code))]
        new = pd.concat([keep, new], ignore_index=True)
    if not new.empty:
        new = new[new.code.str.fullmatch(r"[0-9]{6}|[A-Z]{4,6}")]   # 除了 A 股 6 位码，还留 HSTECH 这类指数码
        new = new.sort_values(["code", "date"]).reset_index(drop=True)
        new.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"K 线：{new.code.nunique() if not new.empty else 0} 只，失败 {len(fail)} {fail[:5]}")
    return new


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    day = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y%m%d")
    gs = pd.read_csv("gs50.csv", encoding="utf-8-sig", dtype=str).fillna("")
    a_codes = [c for c in gs[gs.market == "A"].a_code6 if c]

    if cmd in ("pkg", "all"):
        df = fetch_package(day)
        if df is None:
            print(f"{day} 没有盘后包（非交易日或还没发布）")
        else:
            save_package(df, day)
            print(f"{day} 盘后包：{len(df)} 只，成交额 {df.amount_yi.sum():.0f} 亿，涨停 {int(df.limit_up.sum())}")
    if cmd in ("backfill", "all"):
        n = int(sys.argv[2]) if cmd == "backfill" and len(sys.argv) > 2 else 30
        backfill(n, start=day if cmd == "all" else None)
    if cmd in ("hk", "all"):
        fetch_hk_quotes([c for c in gs[gs.market == "HK"].hk_code if c], day)
    if cmd in ("quotes", "all"):
        q = tencent_quotes(a_codes + list(BENCH))
        os.makedirs("quote", exist_ok=True)
        q.to_csv(f"quote/{day}.csv", index=False, encoding="utf-8-sig")
        print(f"行情：{len(q)} 条 → quote/{day}.csv")
    if cmd == "snap":
        # 按日期取快照：腾讯实时自带数据日期，对得上 day 就用它（含基准指数）；
        # 对不上说明在给历史日补页，必须换 tushare daily_basic，否则市值/换手/PE 会错位一天。
        q = tencent_quotes(a_codes + list(BENCH))
        got = sorted({v for v in q.get("snap_day", []) if v})
        if got == [day]:
            os.makedirs("quote", exist_ok=True)
            q.to_csv(f"quote/{day}.csv", index=False, encoding="utf-8-sig")
            print(f"行情（腾讯实时，数据日 {day}）：{len(q)} 条 → quote/{day}.csv")
        else:
            print(f"腾讯快照的数据日是 {got or '未知'}，不是 {day} → 改走 tushare daily_basic 回补")
            db_quotes(day)
    if cmd == "dbquotes":
        db_quotes(day)
    if cmd in ("kline", "all"):
        refresh_kline(a_codes + list(BENCH))
    print("done")


if __name__ == "__main__":
    main()
