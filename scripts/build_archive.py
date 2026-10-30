"""历史归档页：把每天 build_gs50_dashboard.py 写出的 docs/data/YYYYMMDD.json 汇总成
docs/archive.html（温度/中位的迷你趋势 + 逐日表 + 到当日页面的链接）。

    python scripts/build_archive.py              # 扫 docs/data/*.json → docs/archive.html
    python scripts/build_archive.py --docs DIR   # 指定 docs 目录

页面完全静态：不 fetch、不联网，双击也能看。每新增一天重跑一次即可（run_daily.py 会自动跑）。

口径（页面上也写了一遍，因为这里最容易读错）：
    · 温度 / A 股中位 / 新高 / 成交额占比 = 41 家 A 股，分母是 A 股全市场，不含港股
    · 港股中位 = 13 家港股，对照恒生科技（同市场）
    · 沪深300 只是「A 股宽基 beta」参考；名单 44% 是科创板/创业板成长股，结构错配，
      所以并列创业板指作为同风格对照
"""
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UP, DOWN, FLAT = "#f85149", "#3fb950", "#8b949e"
ACCENT, DIM, PURPLE = "#58a6ff", "#8b949e", "#bc8cff"

CSS = """
:root{--bg:#0e1116;--panel:#161b22;--line:#232b36;--ink:#e6edf3;--dim:#8b949e;
--up:#f85149;--down:#3fb950;--accent:#58a6ff;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:40px 26px 90px}
.kicker{font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
.kicker a{color:var(--accent);text-decoration:none;border-bottom:1px solid #58a6ff44}
h1{font-size:27px;margin:0 0 8px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13.5px;margin:0;max-width:86ch}
header{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:11px;margin:20px 0 6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
.card .v{font:600 23px/1.2 var(--mono)}
.card .k{color:var(--dim);font-size:11.8px;margin-top:5px}
.card .x{color:var(--dim);font-size:11px;margin-top:2px;font-family:var(--mono)}
h2{font-size:18px;margin:38px 0 10px;display:flex;gap:9px;align-items:baseline}
h2 .n{font:600 11.5px/1 var(--mono);color:var(--dim);border:1px solid var(--line);border-radius:5px;padding:4px 7px}
.chart{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 12px 8px;margin:14px 0 6px;overflow:hidden}
.chart svg{width:100%;height:auto;display:block}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
thead th{background:#12171e;color:var(--dim);font-size:11px;letter-spacing:.05em;text-transform:uppercase;font-weight:600}
tbody tr:hover{background:#171e28}
td a{color:var(--accent);text-decoration:none;font-family:var(--mono)}
td a:hover{color:#a5ceff;text-decoration:underline}
.note{color:var(--dim);font-size:12.3px;margin:9px 0 0}
.na{color:#4d5866}
.scroll{overflow-x:auto}
.scroll table{min-width:1120px}
code{font-family:var(--mono);font-size:12.2px;background:#1c2430;border:1px solid var(--line);border-radius:4px;padding:1px 5px}
#tip{position:fixed;left:0;top:0;z-index:99;pointer-events:none;opacity:0;transition:opacity .1s;
background:#1b2230f5;border:1px solid #33414f;border-radius:7px;padding:6px 9px;color:#e6edf3;
font:12px/1.55 inherit;box-shadow:0 8px 24px #00000066;white-space:pre-line;max-width:300px}
[data-tip]{cursor:crosshair}
[data-tip]:hover{filter:brightness(1.4)}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);color:var(--dim);font-size:12.4px}
footer a{color:var(--accent);text-decoration:none;border-bottom:1px solid #58a6ff44}
"""

TIP_JS = """<script>
var tip=document.getElementById('tip');
document.addEventListener('mousemove',function(e){
  var t=e.target.closest?e.target.closest('[data-tip]'):null;
  if(!t){tip.style.opacity=0;return;}
  var txt=t.getAttribute('data-tip');
  if(tip.getAttribute('data-k')!==txt){tip.textContent=txt;tip.setAttribute('data-k',txt);}
  var w=tip.offsetWidth,h=tip.offsetHeight,x=e.clientX+16,y=e.clientY+16;
  if(x+w>window.innerWidth-10)x=e.clientX-w-16;
  if(y+h>window.innerHeight-10)y=e.clientY-h-16;
  tip.style.left=x+'px';tip.style.top=y+'px';tip.style.opacity=1;
});
</script>"""


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ok(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def pct(v, digits=2):
    if not ok(v):
        return "<span class='na'>—</span>"
    col = UP if v > 0 else DOWN if v < 0 else FLAT
    return f"<span style='color:{col}'>{v:+.{digits}f}%</span>"


def num(v, digits=0):
    return f"{v:,.{digits}f}" if ok(v) else "<span class='na'>—</span>"


def dlabel(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d


# ------------------------------------------------------------------ SVG
def score_chart(rows, W=1120, H=210):
    """温度分折线（0–100，带冰点/常温/过热参考线）"""
    n = len(rows)
    if n < 1:
        return ""
    pad_l, pad_r, pad_t, pad_b = 44, 14, 14, 26
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
    step = iw / max(n - 1, 1)

    def X(i):
        return pad_l + (iw / 2 if n == 1 else i * step)

    def Y(v):
        return pad_t + ih * (1 - max(0.0, min(100.0, v)) / 100)

    parts = [f"<svg viewBox='0 0 {W} {H}' role='img'>"]
    for v, col in ((25, "#3fb950"), (62, "#d29922"), (80, "#f85149")):
        parts.append(f"<line x1='{pad_l}' y1='{Y(v):.1f}' x2='{W - pad_r}' y2='{Y(v):.1f}' "
                     f"stroke='{col}33' stroke-dasharray='3 4'/>"
                     f"<text x='{pad_l - 8}' y='{Y(v) + 4:.1f}' fill='{DIM}' font-size='10' "
                     f"text-anchor='end' font-family='ui-monospace'>{v}</text>")
    pts = " ".join(f"{X(i):.1f},{Y(r['score']):.1f}" for i, r in enumerate(rows) if ok(r.get("score")))
    parts.append(f"<polyline points='{pts}' fill='none' stroke='{ACCENT}' stroke-width='1.8' "
                 f"stroke-linejoin='round'/>")
    for i, r in enumerate(rows):
        if not ok(r.get("score")):
            continue
        parts.append(
            f"<circle cx='{X(i):.1f}' cy='{Y(r['score']):.1f}' r='3.2' fill='{ACCENT}' "
            f"data-tip='{esc(dlabel(r['day']))}　温度 {r['score']:.0f} {esc(r.get('mood') or '')}"
            f"&#10;A 股中位 {r['median']:+.2f}%　{r.get('up')}/{r.get('down')} 涨跌'/>")
    every = max(1, n // 8)
    for i, r in enumerate(rows):
        if i % every == 0 or i == n - 1:
            parts.append(f"<text x='{X(i):.1f}' y='{H - 8}' fill='{DIM}' font-size='10' "
                         f"text-anchor='middle' font-family='ui-monospace'>{r['day'][4:6]}/{r['day'][6:]}'</text>")
    parts.append("</svg>")
    return "".join(parts)


def bar_chart(rows, key, lines, tag, W=1120, H=210):
    """某一侧（A 股 / 港股）的中位涨跌柱 + 基准折线。lines = [(json 字段, 颜色, 名字)]

    图例固定放在左上角（不跟最后一点走，否则两条线收在同一个价位时文字会叠在一起）。
    """
    n = len(rows)
    if n < 1:
        return ""
    pad_l, pad_r, pad_t, pad_b = 44, 14, 30, 26
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
    vals = [r[key] for r in rows if ok(r.get(key))]
    for f, _, _ in lines:
        vals += [r[f] for r in rows if ok(r.get(f))]
    span = max([abs(v) for v in vals] + [1.0]) * 1.15
    step = iw / max(n, 1)
    bw = max(2.0, min(18.0, step * 0.62))

    def X(i):
        return pad_l + step * (i + 0.5)

    def Y(v):
        return pad_t + ih / 2 - ih / 2 * (max(-span, min(span, v)) / span)

    parts = [f"<svg viewBox='0 0 {W} {H}' role='img'>",
             f"<line x1='{pad_l}' y1='{Y(0):.1f}' x2='{W - pad_r}' y2='{Y(0):.1f}' stroke='#232b36'/>",
             f"<text x='{pad_l - 8}' y='{Y(span):.1f}' fill='{DIM}' font-size='10' text-anchor='end' "
             f"font-family='ui-monospace'>{span:.1f}%</text>",
             f"<text x='{pad_l - 8}' y='{Y(-span):.1f}' fill='{DIM}' font-size='10' text-anchor='end' "
             f"font-family='ui-monospace'>-{span:.1f}%</text>"]
    # 图例（左上角，横排）
    lx = pad_l + 6
    for f, col, nm in lines:
        parts.append(f"<line x1='{lx}' y1='12' x2='{lx + 16}' y2='12' stroke='{col}' stroke-width='1.8' "
                     f"stroke-dasharray='4 3'/>"
                     f"<text x='{lx + 21}' y='16' fill='{col}' font-size='10.5' font-family='ui-monospace'>{esc(nm)}</text>")
        lx += 34 + len(nm) * 10.5
    for i, r in enumerate(rows):
        v = r.get(key)
        if not ok(v):
            continue
        y0, y1 = Y(0), Y(v)
        col = UP if v > 0 else DOWN if v < 0 else FLAT
        tip = f"{esc(dlabel(r['day']))}　{tag}中位 {v:+.2f}%"
        for f, _, nm in lines:
            if ok(r.get(f)):
                tip += f"&#10;{nm} {r[f]:+.2f}%　超额 {v - r[f]:+.2f}pt"
        if ok(r.get("score")):
            tip += f"&#10;温度 {r['score']:.0f}　异动 {r.get('moves', '—')} 条"
        parts.append(f"<rect x='{X(i) - bw / 2:.1f}' y='{min(y0, y1):.1f}' width='{bw:.1f}' "
                     f"height='{abs(y1 - y0):.1f}' fill='{col}' opacity='.78' data-tip='{tip}'/>")
    for f, col, nm in lines:
        pts = [(X(i), Y(r[f])) for i, r in enumerate(rows) if ok(r.get(f))]
        if len(pts) > 1:
            parts.append("<polyline points='%s' fill='none' stroke='%s' stroke-width='1.5' "
                         "stroke-dasharray='4 3'/>" % (" ".join(f"{a:.1f},{b:.1f}" for a, b in pts), col))
    every = max(1, n // 8)
    for i, r in enumerate(rows):
        if i % every == 0 or i == n - 1:
            parts.append(f"<text x='{X(i):.1f}' y='{H - 8}' fill='{DIM}' font-size='10' "
                         f"text-anchor='middle' font-family='ui-monospace'>{r['day'][4:6]}/{r['day'][6:]}'</text>")
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ 页面
def build(docs_dir):
    ddir = os.path.join(docs_dir, "data")
    rows = []
    for f in sorted(glob.glob(os.path.join(ddir, "*.json"))):
        day = os.path.basename(f)[:8]
        try:
            with open(f, encoding="utf-8") as fh:
                r = json.load(fh)
        except Exception:
            continue
        r.setdefault("day", day)
        rows.append(r)
    rows.sort(key=lambda r: r["day"])
    # 只留真的有日期页的（页面被删掉就别在归档里放死链）
    rows = [r for r in rows if os.path.exists(os.path.join(docs_dir, f"{r['day']}.html"))]
    if not rows:
        raise SystemExit(f"{ddir} 里没有可用的摘要 JSON，先跑 python scripts/run_daily.py")

    last = rows[-1]
    scores = [r["score"] for r in rows if ok(r.get("score"))]
    meds = [r["median"] for r in rows if ok(r.get("median"))]
    exc = [r["excess"] for r in rows if ok(r.get("excess"))]
    exc_c50 = [r["excess_c50"] for r in rows if ok(r.get("excess_c50"))]
    win = sum(1 for r in rows if ok(r.get("excess")) and r["excess"] > 0)
    win_c50 = sum(1 for r in rows if ok(r.get("excess_c50")) and r["excess_c50"] > 0)
    bench_n = sum(1 for r in rows if ok(r.get("excess")))
    nav = 1.0
    for m in meds:
        nav *= 1 + m / 100
    avg_exc = f"{sum(exc) / len(exc):+.2f}pt" if exc else "—"
    avg_c50 = f"{sum(exc_c50) / len(exc_c50):+.2f}pt" if exc_c50 else "—"
    hk_last = last.get("hk_median")

    trs = []
    for r in reversed(rows):
        sc = r.get("score")
        sc_col = DOWN if ok(sc) and sc < 45 else ("#d29922" if not ok(sc) or sc < 62 else UP)
        trs.append(
            f"<tr><td><a href='{r['day']}.html'>{dlabel(r['day'])}</a></td>"
            f"<td style='text-align:right'><span style='color:{sc_col};font-family:var(--mono)'>"
            f"{f'{sc:.0f}' if ok(sc) else '—'}</span></td>"
            f"<td style='text-align:right'>{pct(r.get('median'))}</td>"
            f"<td style='text-align:right'>{r.get('up', '—')}/{r.get('down', '—')}</td>"
            f"<td style='text-align:right'>{r.get('hi', '—')}</td>"
            f"<td style='text-align:right'>{num(r.get('amount'))}</td>"
            f"<td style='text-align:right'>{num(r.get('amount_share'), 1)}%</td>"
            f"<td style='text-align:right'>{pct(r.get('excess'))}</td>"
            f"<td style='text-align:right'>{pct(r.get('excess_c50'))}</td>"
            f"<td style='text-align:right'>{pct(r.get('hk_median'))}</td>"
            f"<td style='text-align:right'>{pct(r.get('hk_excess'))}</td>"
            f"<td style='text-align:right'>{pct(r.get('ret5'))}</td>"
            f"<td style='text-align:right'>{pct(r.get('ret20'))}</td>"
            f"<td style='text-align:right'>{r.get('moves', '—')}</td></tr>")

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 主题每日复盘 · 历史归档（{len(rows)} 期）</title><style>{CSS}</style></head>
<body><div class="wrap">
<header>
<div class="kicker">AI 主题每日复盘 · 历史归档　·　<a href="index.html">最新一期 →</a></div>
<h1>{len(rows)} 期日报：温度、广度、超额，一天一行</h1>
<p class="sub">每一行都是那一天生成的完整页面（<code>docs/YYYYMMDD.html</code>）——当时的结论、当时的图表，不会因为后面重跑而改变。
下面三张图只抽出五个数：<b>温度分</b>（0–100 加权分）、<b>A 股中位涨跌</b>、<b>创业板指</b>、<b>港股中位</b>、<b>恒生科技</b>。</p>
</header>

<div class="cards">
<div class="card"><div class="v">{len(rows)}</div><div class="k">已归档期数</div>
<div class="x">{dlabel(rows[0]['day'])} ~ {dlabel(last['day'])}</div></div>
<div class="card"><div class="v">{f"{last['score']:.0f}" if ok(last.get('score')) else '—'}</div>
<div class="k">最新温度 · {esc(last.get('mood') or '—')}</div>
<div class="x">{dlabel(last['day'])}　A 股中位 {f"{last['median']:+.2f}%" if ok(last.get('median')) else '—'}</div></div>
<div class="card"><div class="v">{f"{sum(scores) / len(scores):.0f}" if scores else '—'}</div><div class="k">区间平均温度</div>
<div class="x">{f"最低 {min(scores):.0f} · 最高 {max(scores):.0f}" if scores else '—'}</div></div>
<div class="card"><div class="v">{f"{sum(meds) / len(meds):+.2f}%" if meds else '—'}</div><div class="k">区间日均 A 股中位</div>
<div class="x">等权累乘 {nav:.2f}×（按日中位粗算，非净值）</div></div>
<div class="card"><div class="v">{win}/{bench_n or '—'}</div><div class="k">跑赢沪深300 的天数</div>
<div class="x">创业板指 {win_c50}/{len(exc_c50) or '—'}　均值 300 {avg_exc} · 创50 {avg_c50}</div></div>
<div class="card"><div class="v">{pct(hk_last, 2)}</div><div class="k">最新港股 13 家中位</div>
<div class="x">恒生科技 {f"{last['bench_hst']:+.2f}%" if ok(last.get('bench_hst')) else '—'} → 超额 {pct(last.get('hk_excess'), 2)}</div></div>
</div>

<h2><span class="n">1</span>温度分（0–100）</h2>
<div class="chart">{score_chart(rows)}</div>
<p class="note">温度 = A 股中位涨跌 + 广度 + 量能 + 创新高家数的加权，只用于横向比较和历史对比，不是预测。
虚线是刻度：冰点 &lt;25 · 偏冷 &lt;45 · 常温 &lt;62 · 升温 &lt;80 · 过热 ≥80。</p>

<h2><span class="n">2</span>A 股：名单中位 vs 沪深300 / 创业板指</h2>
<div class="chart">{bar_chart(rows, "median", [("bench300", DIM, "沪深300"), ("bench_c50", PURPLE, "创业板指")], "A 股")}</div>
<p class="note">柱 = <b>41 家 A 股</b>当日中位涨跌（涨红跌绿）；灰虚线 = 沪深300，紫虚线 = 创业板指。
为什么并列两条：这份名单 44% 是科创板/创业板成长股、没有银行地产，<b>跟沪深300 结构错配</b>——
拿沪深300 算超额，长期会混进一大块风格差（成长 vs 价值），不是选股；创业板指才是同风格的对照。
今天（{dlabel(last['day'])}）就是典型：A 股中位 {f"{last['median']:+.2f}%" if ok(last.get('median')) else '—'}、
沪深300 {f"{last['bench300']:+.2f}%" if ok(last.get('bench300')) else '—'}（超额 {pct(last.get('excess'))}）、
创业板指 {f"{last['bench_c50']:+.2f}%" if ok(last.get('bench_c50')) else '—'}（超额 {pct(last.get('excess_c50'))}）。</p>

<h2><span class="n">3</span>港股：13 家中位 vs 恒生科技</h2>
<div class="chart">{bar_chart(rows, "hk_median", [("bench_hst", DIM, "恒生科技")], "港股")}</div>
<p class="note">柱 = <b>13 家港股</b>当日中位涨跌；灰虚线 = 恒生科技（腾讯、阿里、小米、联想、地平线都是其成分，
是这份名单港股侧最贴的同市场对照）。港股中位来自 <code>data/hk/kline.csv</code>（tushare hk_daily 逐股补的历史），
有当日快照 <code>hk/YYYYMMDD.csv</code> 时以快照为准。</p>

<h2><span class="n">4</span>逐日一览</h2>
<div class="scroll"><table><thead><tr><th>日期</th><th style="text-align:right">温度</th>
<th style="text-align:right">A 股中位</th><th style="text-align:right">涨/跌</th>
<th style="text-align:right">新高</th><th style="text-align:right">成交(亿)</th>
<th style="text-align:right">占 A 股市场</th><th style="text-align:right">超额300</th>
<th style="text-align:right">超额创50</th><th style="text-align:right">港股中位</th>
<th style="text-align:right">超额 HSTECH</th>
<th style="text-align:right">5日</th><th style="text-align:right">20日</th>
<th style="text-align:right">异动</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table></div>
<p class="note"><b>市场口径：</b>「温度 / A 股中位 / 涨跌 / 新高 / 成交额 / 占 A 股市场」都是 <b>41 家 A 股</b>口径，
分母是 A 股全市场（通达信盘后包），<b>不含港股</b>；「港股中位 / 超额 HSTECH」是 13 家港股口径。
两边分开算，是因为港股没有涨停、没有流通盘数据，混在一起的中位会被两套制度搅乱。<br>
「超额300 / 超额创50」= A 股中位 − 沪深300 / 创业板指；「超额 HSTECH」= 港股中位 − 恒生科技；
「5日 / 20日」是名单 A 股的窗口中位涨跌；「异动」= 触发阈值的个股条数。点日期进入当天的完整页面。</p>

<footer>AI 主题每日复盘 · 归档页生成于 {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}　·　
{len(rows)} 期　·　重新生成：<code>python scripts/build_archive.py</code>　·　
<a href="index.html">最新一期</a>　·　<a href="gs50_dashboard.html">最新（固定链接）</a></footer>
</div>
<div id="tip"></div>
{TIP_JS}
</body></html>"""

    os.makedirs(docs_dir, exist_ok=True)
    out = os.path.join(docs_dir, "archive.html")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write(html)
    print(f"写出 {out}  {len(html) / 1024:.0f} KB　{len(rows)} 期　{dlabel(rows[0]['day'])} ~ {dlabel(last['day'])}")
    return out


def main():
    docs = os.path.join(ROOT, "docs")
    argv = sys.argv[1:]
    if "--docs" in argv:
        docs = os.path.abspath(argv[argv.index("--docs") + 1])
    build(docs)


if __name__ == "__main__":
    main()
