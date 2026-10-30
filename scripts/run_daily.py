"""GS50 每日复盘看板 · 唯一入口

    python scripts/run_daily.py                  # 抓今天 → 出页面（今天没盘后包就出最近一个交易日）
    python scripts/run_daily.py 20261029         # 指定数据日
    python scripts/run_daily.py --rebuild        # 不抓数，只用已有 data/ 重建页面 + 归档
    python scripts/run_daily.py --no-consensus   # 跳过 A 股卖方共识（慢，最多几分钟；页面复用最近一版）
    python scripts/run_daily.py --no-hk          # 跳过港股（港股休市或想快一点）
    python scripts/run_daily.py --no-backfill    # 不补历史日（只出今天这一页 + 归档）

流水线（每一步都是独立脚本，可以单独跑）：
    1 theme_data.py all <day>          盘后包 + 回补历史 + A 股快照 + 日线 + 港股
    1b build_hk_kline.py <day>         港股 13 家日线收益长表（归档页的港股中位靠它，5 天内不重拉）
    2 fetch_consensus.py <day>         A 股卖方共识 → PE_NTM（tushare report_rc，逐股，慢）
    3 build_gs50_dashboard.py <day>    → docs/<day>.html（+ docs/data/<day>.json 摘要）
    4 build_archive.py                 → docs/archive.html（历史归档页）+ docs/index.html（跳最新）

输出（全部静态，GitHub Pages 源目录 = docs/）：
    docs/<day>.html          每天一份，永不覆盖 → 可永久引用某一天
    docs/index.html          跳到最新一期（每次运行重写）
    docs/archive.html        温度/中位趋势 + 逐日表 + 到每天页面的链接
    docs/gs50_dashboard.html 永远是最新一期（本地双击看，也兼容老链接）
    docs/data/<day>.json     归档页用的每日摘要（<1 KB）
"""

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
PY = sys.executable
BUILD = os.path.join("scripts", "build_gs50_dashboard.py")
ARCHIVE = os.path.join("scripts", "build_archive.py")

INDEX_TPL = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 主题每日复盘 · 最新（{day}）</title>
<meta http-equiv="refresh" content="0; url={day}.html">
<link rel="canonical" href="{day}.html">
<style>body{{margin:0;background:#0e1116;color:#e6edf3;
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:640px;margin:18vh auto;padding:0 26px}}a{{color:#58a6ff}}</style>
<script>location.replace("{day}.html");</script></head>
<body><div class="wrap"><p>正在打开最新一期（{day}）……</p>
<p>没跳转的话点这里：<a href="{day}.html">数据日 {day}</a>　·　<a href="archive.html">历史归档</a></p></div></body></html>
"""


def all_days():
    return sorted(os.path.basename(p)[:8] for p in glob.glob(os.path.join(ROOT, "data", "mkt", "*.csv")))


def latest_mkt_day():
    days = all_days()
    return days[-1] if days else None


def run(title, args, allow_fail=True):
    print(f"\n=== {title} ===", flush=True)
    rc = subprocess.run([PY, "-u", *args], cwd=ROOT).returncode
    if rc and not allow_fail:
        sys.exit(f"✗ {title} 失败（exit {rc}）")
    if rc:
        print(f"! {title} 返回 {rc}，继续", flush=True)
    return rc


def build_page(day, allow_fail=False):
    """出一页：docs/<day>.html + docs/data/<day>.json（同一次进程内算完，不重复读盘）"""
    return run(f"生成页面 {day}",
               [BUILD, day, "--out", os.path.join("docs", f"{day}.html"),
                "--summary", os.path.join("docs", "data", f"{day}.json")],
               allow_fail=allow_fail)


def write_index(day):
    path = os.path.join(DOCS, "index.html")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(INDEX_TPL.format(day=day))
    print(f"写出 {path} → {day}.html")


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    day = argv[0] if argv else None
    rebuild = "--rebuild" in flags

    if not rebuild:
        day = day or __import__("datetime").date.today().strftime("%Y%m%d")
        td = os.path.join("scripts", "theme_data.py")
        steps = [("盘后包", [td, "pkg", day]),
                 ("回补历史", [td, "backfill", "30"])]
        if "--no-hk" not in flags:
            steps.append(("港股 13 家", [td, "hk", day]))
            steps.append(("港股日线（归档用）", [os.path.join("scripts", "build_hk_kline.py"), day]))
        steps += [("A 股快照", [td, "quotes", day]),
                  ("A 股日线", [td, "kline"])]
        for i, (title, args) in enumerate(steps, 1):
            run(f"1/4 抓数 · {title}（{i}/{len(steps)}）", args)
        if "--no-consensus" not in flags:
            run(f"2/4 A 股共识 {day}", [os.path.join("scripts", "fetch_consensus.py"), day])
        else:
            print("\n=== 2/4 A 股共识（跳过，复用 consensus/ 里最近一版）===", flush=True)
    else:
        print("\n=== 1-2/4 抓数（--rebuild 跳过）===", flush=True)

    # 抓数失败（非交易日、盘后包还没发）时用最近一个有数据的交易日出页面
    target = day if day and os.path.exists(os.path.join(ROOT, "data", "mkt", f"{day}.csv")) else latest_mkt_day()
    if not target:
        sys.exit("data/mkt 里没有任何盘后包，先跑 python scripts/theme_data.py pkg <YYYYMMDD>")
    if day and target != day:
        print(f"! {day} 没有盘后包（非交易日或还没发布）→ 用 {target} 出页面", flush=True)

    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    build_page(target, allow_fail=False)
    # 老链接 / 本地双击的固定名：内容与 docs/<target>.html 完全一致，git 里是同一个 blob，不额外占空间
    shutil.copyfile(os.path.join(DOCS, f"{target}.html"),
                    os.path.join(DOCS, "gs50_dashboard.html"))

    # 3/4 补齐历史：某天漏跑了（或第一次部署）就按当时口径重算，已存在的不动
    missing = [d for d in all_days()
               if not os.path.exists(os.path.join(DOCS, f"{d}.html"))
               or not os.path.exists(os.path.join(DOCS, "data", f"{d}.json"))]
    if "--no-backfill" in flags:
        if missing:
            print(f"\n=== 3/4 补齐历史（--no-backfill 跳过，缺 {len(missing)} 天）===", flush=True)
    elif not missing:
        print("\n=== 3/4 补齐历史（无缺页）===", flush=True)
    else:
        print(f"\n=== 3/4 补齐历史：{len(missing)} 天（首次较慢，约 {len(missing) * 5} 秒）===", flush=True)
        for i, d in enumerate(missing, 1):
            print(f"--- [{i}/{len(missing)}] {d}", flush=True)
            build_page(d, allow_fail=True)

    # 4/4 归档页 + 最新跳转
    write_index(target)
    run("4/4 历史归档页", [ARCHIVE], allow_fail=False)
    print(f"\n完成 → {os.path.join(DOCS, 'index.html')}（最新）· "
          f"{os.path.join(DOCS, 'archive.html')}（归档）")


if __name__ == "__main__":
    main()
