"""归档页 + 门厅：把每天 build_gs50_dashboard.py 写出的 docs/data/YYYYMMDD.json 汇总成
  docs/index.html     门厅（最新一期结论 + 读数 + 温度史 + 最近五期），不再自动跳转
  docs/archive.html   目录（温度/中位的迷你趋势 + 逐日表 + 到当日页面的链接）

URL 只有一个角色，别再加别名：
  /                 门厅（永远指向最新一期，稳定可收藏）
  /YYYYMMDD.html    那一天的快照，永不覆盖
  /archive.html     全部日期目录

    python scripts/build_archive.py              # 扫 docs/data/*.json → index.html + archive.html
    python scripts/build_archive.py --docs DIR   # 指定 docs 目录

页面完全静态：不 fetch、不联网，双击也能看。每新增一天重跑一次即可（run_daily.py 会自动跑）。

口径（页面上也写了一遍，因为这里最容易读错）：
    · 温度 / A 股中位 / 新高 / 成交额占比 = 41 家 A 股，分母是 A 股全市场，不含港股
    · 港股中位 = 13 家港股，对照恒生科技（同市场）
    · 沪深300 只是「A 股宽基 beta」参考；名单 44% 是科创板/创业板成长股，结构错配，
      所以并列创业板指作为同风格对照
"""
import csv
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 时间一律用北京时间：runner 是 UTC，datetime.now() 会比页面上写的 CST 差 8 小时。
CST = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))


def now_cst():
    return __import__("datetime").datetime.now(CST)
# ---------- 图表内排版 ----------
# 图表 SVG 的坐标写在 1120 宽的虚拟画布上，落盘后再缩到容器宽度（桌面 ≈0.92×、手机 ≈0.93×）。
# 所以写进 SVG 的字号要比「想看到的 CSS px」略大；这里两档就够，别再加第三档。
FT_T = 15.6   # 图内标题
FT_S = 13.6   # 其余全部：副标 / 说明 / 标签 / 轴标 / 单元值

UP, DOWN, FLAT = "#f85149", "#3fb950", "#8b949e"
ACCENT, DIM, PURPLE = "#58a6ff", "#8b949e", "#bc8cff"

# 仓库地址：页脚那个 GitHub 图标指这里。只有图标 + title，不占版面、不拉外部图片。
REPO_URL = "https://github.com/Jst-Well-Dan/gs50-dashboard"
GH_ICON = (f'<a class="gh" href="{REPO_URL}" target="_blank" rel="noopener"'
           f' title="源码仓库：{REPO_URL}" aria-label="GitHub 源码仓库">'
           '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 '
           '5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94'
           '-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87'
           '.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21'
           ' 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08'
           ' 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01'
           ' 2.2 0 .21.15.46.55.38A8.012 8.012 0 0016 8c0-4.42-3.58-8-8-8z"/></svg></a>')

CSS = """
:root{--bg:#0e1116;--panel:#161b22;--line:#232b36;--ink:#e6edf3;--dim:#8b949e;
--up:#f85149;--down:#3fb950;--accent:#58a6ff;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:40px 26px 90px}
.kicker{font:600 12.4px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
.kicker a{color:var(--accent);text-decoration:none;border-bottom:1px solid #58a6ff44}
h1{font-size:27px;margin:0 0 8px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13.9px;margin:0;max-width:86ch}
header{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:11px;margin:20px 0 6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
.card .v{font:600 24px/1.2 var(--mono)}
.card .k{color:var(--dim);font-size:12.6px;margin-top:5px}
.card .x{color:var(--dim);font-size:12.6px;margin-top:2px;font-family:var(--mono)}
h2{font-size:18px;margin:38px 0 10px;display:flex;gap:9px;align-items:baseline}
h2 .n{font:600 12.4px/1 var(--mono);color:var(--dim);border:1px solid var(--line);border-radius:5px;padding:4px 7px}
.chart{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:16px 14px 10px;margin:14px 0 6px;overflow:hidden}
.chart svg{width:100%;height:auto;display:block}
/* 图内文字不接指针：名字压到点上也抢不走悬停 */
.chart svg text{pointer-events:none}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13.4px}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
thead th{background:#12171e;color:var(--dim);font-size:12.6px;letter-spacing:.04em;text-transform:uppercase;font-weight:600;
  position:sticky;top:0;z-index:4}
tbody tr:hover{background:#171e28}
td a{color:var(--accent);text-decoration:none;font-family:var(--mono)}
td a:hover{color:#a5ceff;text-decoration:underline}
.note{color:var(--dim);font-size:12.8px;margin:9px 0 0}
.na{color:#4d5866}
/* ---------- 逐日一览：按月折叠 + 月份跳转条 ---------- */
.months{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0 12px}
.months a{font:500 12.8px/1 var(--mono);padding:8px 12px;border:1px solid var(--line);border-radius:9px;
  background:#12171e;color:#c9d5e1;text-decoration:none;transition:background .14s,border-color .14s,color .14s}
.months a:hover{background:#1c2430;border-color:#3d4a5c;color:#fff}
.months a.on{border-color:#1f6feb88;background:#1f6feb1f;color:#9ccbff}
details.mon{border-bottom:1px solid var(--line);scroll-margin-top:12px}
details.mon>summary{display:flex;align-items:baseline;gap:11px;padding:12px 2px;cursor:pointer;
  list-style:none;font-size:13.6px;color:#dbe6f0;transition:color .14s}
details.mon>summary::-webkit-details-marker{display:none}
details.mon>summary::before{content:'';width:5px;height:5px;flex:none;margin-top:7px;
  border-right:1.6px solid #6e7681;border-bottom:1.6px solid #6e7681;
  transform:rotate(-45deg);transition:transform .16s}
details.mon[open]>summary::before{transform:rotate(45deg)}
details.mon>summary:hover{color:#fff}
details.mon>summary .m{font:600 13.4px/1 var(--mono);color:#e6edf3;letter-spacing:-.01em}
details.mon>summary .s{font:500 12.6px/1 var(--mono);color:var(--dim)}
details.mon[open]>summary{border-bottom:1px solid var(--line)}
details.mon table{margin:0}
.scroll{overflow-x:auto}
.scroll table{min-width:1120px}
code{font-family:var(--mono);font-size:12.6px;background:#1c2430;border:1px solid var(--line);border-radius:4px;padding:1px 5px}
#tip{position:fixed;left:0;top:0;z-index:99;pointer-events:none;opacity:0;transition:opacity .1s;
background:#1b2230f5;border:1px solid #33414f;border-radius:7px;padding:6px 9px;color:#e6edf3;
font:12.6px/1.55 inherit;box-shadow:0 6px 14px #00000080;white-space:pre-line;max-width:300px}
[data-tip]{cursor:crosshair}
[data-tip]:hover{filter:brightness(1.4)}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);color:var(--dim);font-size:12.8px}
footer a{color:var(--accent);text-decoration:none;border-bottom:1px solid #58a6ff44}
footer a.gh{display:inline-flex;align-items:center;border-bottom:none;vertical-align:-3px}
footer a.gh svg{width:15px;height:15px;fill:currentColor}
/* 表头吸顶：只有在 .scroll 不自己当滚动容器时才真正贴着窗口（所以就放在 .scroll 之后） */
@media (min-width:1100px){.scroll{overflow:visible}}
/* 两张趋势图都是 1120 宽的虚拟画布：窗口窄到 <1240 就横向滑动，保住轴标字号 */
@media (max-width:1239px){
  .chart{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .chart svg{min-width:1030px}
}
"""

TIP_JS = """<script>
/* 月份跳转：目标是折叠的月份时把它展开（纯渐进增强，没 JS 也能看到分组标题） */
function openHash(){
  var t = location.hash && document.getElementById(location.hash.slice(1));
  if (t && t.tagName === 'DETAILS') t.open = true;
}
addEventListener('hashchange', openHash);
openHash();
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
    pad_l, pad_r, pad_t, pad_b = 46, 16, 18, 30
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
                     f"<text x='{pad_l - 8}' y='{Y(v) + 4:.1f}' fill='{DIM}' font-size='{FT_S}' "
                     f"text-anchor='end' font-family='ui-monospace'>{v}</text>")
    pts = " ".join(f"{X(i):.1f},{Y(r['score']):.1f}" for i, r in enumerate(rows) if ok(r.get("score")))
    parts.append(f"<polyline points='{pts}' fill='none' stroke='{ACCENT}' stroke-width='1.8' "
                 f"stroke-linejoin='round'/>")
    for i, r in enumerate(rows):
        if not ok(r.get("score")):
            continue
        tip = (f"{esc(dlabel(r['day']))}　温度 {r['score']:.0f} {esc(r.get('mood') or '')}"
               f"&#10;A 股中位 {r['median']:+.2f}%　{r.get('up')}/{r.get('down')} 涨跌")
        parts.append(
            f"<circle cx='{X(i):.1f}' cy='{Y(r['score']):.1f}' r='11' fill='transparent' data-tip='{tip}'/>"
            f"<circle{' class=' + chr(39) + 'pk' + chr(39) if i == n - 1 else ''} "
            f"cx='{X(i):.1f}' cy='{Y(r['score']):.1f}' r='4.4' fill='{ACCENT}' data-tip='{tip}'/>")
    every = max(1, n // 8)
    for i, r in enumerate(rows):
        if i % every == 0 or i == n - 1:
            parts.append(f"<text x='{min(X(i), W - 26):.1f}' y='{H - 8}' fill='{DIM}' font-size='{FT_S}' "
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
    pad_l, pad_r, pad_t, pad_b = 56, 16, 38, 30
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
             f"<text x='{pad_l - 8}' y='{Y(span):.1f}' fill='{DIM}' font-size='{FT_S}' text-anchor='end' "
             f"font-family='ui-monospace'>{span:.1f}%</text>",
             f"<text x='{pad_l - 8}' y='{Y(-span):.1f}' fill='{DIM}' font-size='{FT_S}' text-anchor='end' "
             f"font-family='ui-monospace'>-{span:.1f}%</text>"]
    # 图例（左上角，横排）
    lx = pad_l + 6
    for f, col, nm in lines:
        parts.append(f"<line x1='{lx}' y1='12' x2='{lx + 16}' y2='12' stroke='{col}' stroke-width='1.8' "
                     f"stroke-dasharray='4 3'/>"
                     f"<text x='{lx + 21}' y='16' fill='{col}' font-size='{FT_S}' font-family='ui-monospace'>{esc(nm)}</text>")
        lx += 40 + len(nm) * 13.8
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
            parts.append(f"<text x='{min(X(i), W - 26):.1f}' y='{H - 8}' fill='{DIM}' font-size='{FT_S}' "
                         f"text-anchor='middle' font-family='ui-monospace'>{r['day'][4:6]}/{r['day'][6:]}'</text>")
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ 页面
TH_ROW = ("<tr><th>日期</th><th style='text-align:right'>温度</th>"
          "<th style='text-align:right'>A 股中位</th><th style='text-align:right'>涨/跌</th>"
          "<th style='text-align:right'>新高</th><th style='text-align:right'>成交(亿)</th>"
          "<th style='text-align:right'>占 A 股市场</th><th style='text-align:right'>超额300</th>"
          "<th style='text-align:right'>超额创50</th><th style='text-align:right'>港股中位</th>"
          "<th style='text-align:right'>超额 HSTECH</th>"
          "<th style='text-align:right'>5日</th><th style='text-align:right'>20日</th>"
          "<th style='text-align:right'>异动</th></tr>")


def day_row(r):
    """逐日表的一行（日期只用 MM-DD：年份在月份分组标题里）"""
    sc, e = r.get("score"), r.get("excess")
    sc_col = DOWN if ok(sc) and sc < 45 else ("#d29922" if not ok(sc) or sc < 62 else UP)
    return (f"<tr><td><a href='{r['day']}.html' title='{dlabel(r['day'])}'>{r['day'][4:6]}-{r['day'][6:]}</a></td>"
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


def month_sections(rows):
    """逐日一览：按月分组，最新月默认展开。返回（月份跳转条, 分组 HTML）。

    期数只增不减，一整张表两年后会变得没法读。按月折叠后页面高度只跟「一个月有多少天」有关，
    跟历史长短无关。折叠用 <details>：不依赖 JS，浏览器 Ctrl+F 命中被折叠的行时会自动展开。
    """
    groups = {}
    for r in reversed(rows):                      # 新的在前
        groups.setdefault(r["day"][:6], []).append(r)
    chips, blocks = [], []
    for i, (ym, rs) in enumerate(groups.items()):
        label = f"{ym[:4]}-{ym[4:]}"
        scores = [r["score"] for r in rs if ok(r.get("score"))]
        judged = [r for r in rs if ok(r.get("excess"))]
        wins = sum(1 for r in judged if r["excess"] > 0)
        avg = f"平均温度 {sum(scores) / len(scores):.0f}" if scores else "平均温度 —"
        sub = f"{len(rs)} 期　·　{avg}　·　跑赢 {wins}/{len(judged) or '—'}"
        on = ' class="on"' if i == 0 else ''
        chips.append(f"<a href='#m{ym}'{on}>{label}</a>")
        open_ = " open" if i == 0 else ""
        blocks.append(
            f"<details class='mon' id='m{ym}'{open_}>"
            f"<summary><span class='m'>{label}</span><span class='s'>{sub}</span></summary>"
            f"<div class='scroll'><table><thead>{TH_ROW}</thead>"
            f"<tbody>{''.join(day_row(r) for r in rs)}</tbody></table></div></details>")
    return "".join(chips), "".join(blocks)


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

    chips, mblocks = month_sections(rows)

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 主题每日复盘 · 历史归档（{len(rows)} 期）</title><style>{CSS}</style></head>
<body><div class="wrap">
<header>
<div class="kicker">AI 主题每日复盘 · 历史归档　·　<a href="index.html">← 首页</a></div>
<h1>{len(rows)} 期日报：温度、广度、超额，一天一行</h1>
<p class="sub">每一行都是那一天生成的完整页面（<code>docs/YYYYMMDD.html</code>）——当时的结论、当时的图表，不会因为后面重跑而改变。
下面三张图只抽出五个数：<b>温度分</b>（0–100 加权分）、<b>A 股中位涨跌</b>、<b>创业板指</b>、<b>港股中位</b>、<b>恒生科技</b>。</p>
</header>

<div class="cards">
<div class="card"><div class="v" style="font-size:18px">{dlabel(last['day'])}</div>
<div class="k">最新一期 · {esc(last.get('mood') or '—')} {f"{last['score']:.0f}" if ok(last.get('score')) else '—'}</div>
<div class="x">A 股中位 {f"{last['median']:+.2f}%" if ok(last.get('median')) else '—'}　超额 {pct(last.get('excess'))}</div></div>
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
<p class="note" style="margin:10px 0 0">按月折叠，最新月默认展开；点月份跳到那一组。行里的日期只写月-日，年份看分组标题。</p>
<div class="months">{chips}</div>
{mblocks}
<p class="note"><b>市场口径：</b>「温度 / A 股中位 / 涨跌 / 新高 / 成交额 / 占 A 股市场」都是 <b>41 家 A 股</b>口径，
分母是 A 股全市场（通达信盘后包），<b>不含港股</b>；「港股中位 / 超额 HSTECH」是 13 家港股口径。
两边分开算，是因为港股没有涨停、没有流通盘数据，混在一起的中位会被两套制度搅乱。<br>
「超额300 / 超额创50」= A 股中位 − 沪深300 / 创业板指；「超额 HSTECH」= 港股中位 − 恒生科技；
「5日 / 20日」是名单 A 股的窗口中位涨跌；「异动」= 触发阈值的个股条数。点日期进入当天的完整页面。</p>

<footer>AI 主题每日复盘 · 归档页生成于 {now_cst():%Y-%m-%d %H:%M}　·　
{len(rows)} 期　·　重新生成：<code>python scripts/build_archive.py</code>　·　
<a href="index.html">首页</a>　·　<a href="manual.html">项目说明</a>　·　{GH_ICON}</footer>
</div>
<div id="tip"></div>
{TIP_JS}
</body></html>"""

    os.makedirs(docs_dir, exist_ok=True)
    out = os.path.join(docs_dir, "archive.html")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write(html)
    print(f"写出 {out}  {len(html) / 1024:.0f} KB　{len(rows)} 期　{dlabel(rows[0]['day'])} ~ {dlabel(last['day'])}")
    hub_path = os.path.join(docs_dir, "index.html")
    with open(hub_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(hub(rows, last))
    print(f"写出 {hub_path} → 门厅（{dlabel(last['day'])}，{len(rows)} 期）")
    # 根目录放一个跳转壳：docs/ 是发布目录（GH Pages），但本地点开时根目录更顺手
    stub = os.path.join(os.path.dirname(docs_dir), "index.html")
    rel = os.path.relpath(hub_path, os.path.dirname(docs_dir)).replace(os.sep, "/")
    with open(stub, "w", encoding="utf-8", newline="") as fh:
        fh.write(PORTAL.replace("$target", rel))
    print(f"写出 {stub} → 跳转壳（→ {rel}）")
    return out


# 根目录 index.html：只做跳转，指针永远指向 docs/index.html（唯一真源在那儿，不复制内容）
PORTAL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>AI 主题每日复盘</title>
<meta http-equiv="refresh" content="0; url=$target">
<style>body{background:#0d1117;color:#c9d5e1;font:15px/1.7 ui-monospace,Menlo,Consolas,monospace;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
a{color:#58a6ff}small{color:#8b949e}</style></head>
<body><div>
<p>正在打开看板…</p>
<p><a href="$target">如果没有自动跳转，点这里</a></p>
<p><small>$target</small></p>
</div>
<script>location.replace("$target");</script>
</body></html>
"""

HUB_CSS = """
/* ---------- 门厅 index.html：入口页，不是跳转壳 ---------- */
.bar{position:sticky;top:0;z-index:60;padding:7px 0;background:#0e1116e6;
  backdrop-filter:blur(14px) saturate(1.25);border-bottom:1px solid var(--line)}
.bar-in{max-width:1046px;margin:0 auto;height:44px;display:flex;align-items:center;gap:9px;padding:0 25px}
.brand{display:flex;align-items:center;gap:9px;font-size:14px;font-weight:600;letter-spacing:.01em;
  white-space:nowrap;color:var(--ink);text-decoration:none}
.brand .dot{width:8px;height:8px;border-radius:99px;background:var(--accent);outline:1px solid #58a6ff59;outline-offset:2px}
.bar .sp{flex:1}
.btn{display:inline-flex;align-items:center;gap:7px;min-height:36px;padding:9px 14px;border:1px solid #2b3644;
  border-radius:9px;background:#161b22;color:#c9d5e1;font:500 13.2px/1 inherit;text-decoration:none;
  white-space:nowrap;transition:background .16s,border-color .16s,color .16s}
.btn:hover{background:#1c2430;border-color:#3d4a5c;color:#fff}
.btn svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.hwrap{max-width:1046px;margin:0 auto;padding:0 25px 84px}
.hhead{padding:54px 0 30px;border-bottom:1px solid var(--line)}
.hdat{margin:0 0 20px;font:500 12.8px/1.6 var(--mono);color:var(--dim);
  letter-spacing:.02em;font-variant-numeric:tabular-nums}
.hdat b{color:#c9d5e1;font-weight:600}
.hdat .div{padding:0 .55em;color:#39434f}
h1.h1{margin:0 0 14px;font-size:clamp(27px,4.2vw,44px);line-height:1.16;letter-spacing:-.028em;
  font-weight:600;text-wrap:balance}
h1.h1 .sep{color:#39434f;font-weight:400;padding:0 .1em}
h1.h1 .cl{display:inline-block}
h1.h1 .pt{color:var(--down);font-variant-numeric:tabular-nums}
h1.h1 .pt.up{color:var(--up)}
.nw{white-space:nowrap}
h1.h1 .u{font-style:normal;font-size:.42em;font-weight:500;color:var(--dim);margin-left:.12em;letter-spacing:.02em}
.hsub{margin:0;max-width:60ch;color:var(--dim);font-size:14.6px;line-height:1.72}
.hact{display:flex;align-items:center;gap:20px;flex-wrap:wrap;margin-top:26px}
.go{display:inline-flex;align-items:center;gap:10px;height:44px;padding:0 19px;border-radius:11px;
  background:#123050;border:1px solid #1f6feb7a;color:#cfe6ff;font:600 14px/1 inherit;text-decoration:none;
  transition:background .16s,border-color .16s,color .16s}
.go:hover{background:#173d63;border-color:#58a6ffaa;color:#fff}
.go svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:1.8;
  stroke-linecap:round;stroke-linejoin:round;transition:transform .18s cubic-bezier(.22,1,.36,1)}
.go:hover svg{transform:translateX(3px)}
.read{display:grid;grid-template-columns:repeat(4,1fr);margin:0;border-bottom:1px solid var(--line)}
.read>div{padding:19px 20px 21px 0}
.read>div+div{padding-left:20px;border-left:1px solid var(--line)}
.read dt{font:600 12.4px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--dim)}
.read dd{margin:10px 0 0;font:600 24px/1.1 var(--mono);color:#e6edf3;
  letter-spacing:-.02em;font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
.read dd i{display:block;margin-top:6px;font:500 12.6px/1.45 var(--mono);font-style:normal;
  letter-spacing:.03em;color:var(--dim)}
.read dd.up{color:var(--up)}
.read dd.down{color:var(--down)}
.hsec{padding-top:46px}
.hsec h2{margin:0 0 15px;font-size:18px;font-weight:600;letter-spacing:-.005em;
  display:flex;align-items:baseline;justify-content:space-between;gap:14px;color:#e6edf3}
.hsec h2 a{font:500 12.8px/1 var(--mono);color:var(--accent);text-decoration:none;
  border-bottom:1px solid #58a6ff44;letter-spacing:0}
.hsec h2 a:hover{color:#a5ceff}
.recent{list-style:none;margin:0;padding:0}
.recent li{border-bottom:1px solid var(--line)}
.recent li+li{border-top:1px solid var(--line)}
.recent a{display:grid;grid-template-columns:92px minmax(0,1fr) 84px 20px;align-items:center;gap:14px;
  padding:13px 4px 13px 0;text-decoration:none;color:inherit;transition:background .14s}
.recent a:hover{background:#161b22}
.recent .d{font:500 13.4px/1 var(--mono);color:#c9d5e1;font-variant-numeric:tabular-nums}
.recent .m{font-size:14px;font-weight:600;display:flex;align-items:baseline;gap:8px;min-width:0}
.recent .m small{font:500 12.6px/1 var(--mono);color:var(--dim);font-weight:500;white-space:nowrap}
.recent .v{font:600 13.6px/1 var(--mono);text-align:right;font-variant-numeric:tabular-nums}
.recent .arw{color:#39434f;text-align:right;font:400 15px/1 var(--mono);transition:color .14s,transform .14s}
.recent a:hover .arw{color:var(--accent);transform:translateX(3px)}
/* 名单来历：数据全从 data/gs50.csv 数出来，别在文案里写死家数 */
.why{margin:2px 0 0;max-width:84ch}
.why>div{display:grid;grid-template-columns:104px minmax(0,1fr);gap:16px;padding:7px 0;align-items:baseline}
.why dt{font:600 12.2px/1.65 var(--mono);letter-spacing:.08em;color:var(--dim);white-space:nowrap}
.why dd{margin:0;font-size:14.3px;line-height:1.74;color:#c9d5e1;min-width:0;text-wrap:pretty}
.why dd b{color:#fff;font-weight:600}
.why + .hsub{margin-top:12px;max-width:84ch;font-size:13.6px;line-height:1.7}
html{scroll-behavior:smooth}
.chart{animation:hubin .6s cubic-bezier(.22,1,.36,1) both}
@keyframes hubin{from{transform:translateY(8px)}to{transform:none}}
.chart svg circle.pk{animation:hubpulse 1.6s cubic-bezier(.22,1,.36,1) .7s 2}
@keyframes hubpulse{0%,100%{r:4.4}48%{r:7.4}}
@media (prefers-reduced-motion:reduce){
  .chart,.chart svg circle.pk{animation:none}
  .go svg,.recent .arw{transition:none}
}
@media (max-width:620px){
  /* 窄屏：两个分句各自成行，绝不在「名单跑输」和「沪深300」之间断开 */
  h1.h1 .cl{display:block}
  h1.h1 .sep{display:none}
}
/* 温度史是 1120 宽的虚拟画布：窗口窄到 <1240 就横向滑动，保住轴标字号 */
@media (max-width:1239px){
  .chart{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .chart svg{min-width:1030px}
}
@media (max-width:760px){
  .hhead{padding:34px 0 24px}
  .read{grid-template-columns:1fr 1fr}
  .read>div{padding:15px 14px 17px 0;border-left:0}
  .read>div+div{padding-left:0}
  .read>div:nth-child(even){padding-left:16px;border-left:1px solid var(--line)}
  .read>div:nth-child(n+3){border-top:1px solid var(--line)}
  .read dd{font-size:21px}
  .chart{overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}
  .recent a{grid-template-columns:74px minmax(0,1fr) 68px;gap:10px;padding:12px 2px 12px 0}
  .recent .exc,.recent .arw{display:none}  .hact{gap:16px}
  .why>div{grid-template-columns:1fr;gap:3px;padding:6px 0}
  .why dt{font-size:11.6px}
  .why dd{font-size:13.8px}
}
"""


def gs_facts():
    """从 data/gs50.csv 数出名单构成（总数 / A / HK / 六层家数）。
    门厅上的“54 家”全从这儿取，名单变了页面自己跟上，不会和文案对不上。"""
    layers = [("Semiconductors", "半导体", "#58a6ff"),
              ("Infrastructure", "基础设施", "#39c5cf"),
              ("Application (Physical)", "应用·物理", "#bc8cff"),
              ("Power", "电力", "#d29922"),
              ("Application (AI+)", "应用·AI+", "#f778ba"),
              ("AI Model", "模型", "#a5d6ff")]
    try:
        with open(os.path.join(ROOT, "data", "gs50.csv"), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return None
    if not rows:
        return None
    cnt = {}
    for r in rows:
        cnt[r.get("ai_layer", "")] = cnt.get(r.get("ai_layer", ""), 0) + 1
    return {"n": len(rows),
            "a": sum(1 for r in rows if r.get("market") == "A"),
            "hk": sum(1 for r in rows if r.get("market") == "HK"),
            "layers": [(lab, col, cnt.get(key, 0)) for key, lab, col in layers if cnt.get(key)]}


def hub(rows, last):
    """门厅：不自动跳转。答案（今天几度、跑输多少）在最上面，入口（今天 / 目录）紧随，
    趋势和最近五期给出「现在处于什么位置」，其余全部交给 /archive.html。"""
    day = last["day"]
    n = len(rows)
    pos = [r["day"] for r in rows].index(day) + 1
    sc = last.get("score")
    mood = esc(last.get("mood") or "")
    med, exc, share = last.get("median"), last.get("excess"), last.get("amount_share")
    gs = gs_facts()
    # 名单构成一句话（家数现算，描述性词是固定的）：半导体 19（设计/设备/材料…）、基础设施 12（…）
    gs_mix = " · ".join(f"{lab} <b>{cnt}</b>"
                        for lab, _col, cnt in (gs["layers"] if gs else []))
    gs_n = gs["n"] if gs else 54

    # H1 = 当天页自己的结论，不从别处另起一套话术；两个分句各自成块，换行只发生在 ·
    h1 = (f"<span class='cl'>{mood} {sc:.0f}<i class='u'>分</i></span>" if (ok(sc) and mood)
          else f"<span class='cl'>温度 {sc:.0f}<i class='u'>分</i></span>")
    if ok(exc):
        cls = "pt up" if exc > 0 else "pt"
        h1 = (f"{h1}<span class='sep'>·</span><span class='cl'>"
              f"名单{'跑赢' if exc > 0 else '跑输'}沪深300 "
              f"<span class='{cls}'>{abs(exc):.2f}pt</span></span>")

    med_cls = "up" if ok(med) and med > 0 else ("down" if ok(med) else "")
    exc_cls = "up" if ok(exc) and exc > 0 else ("down" if ok(exc) else "")
    read = f"""<dl class="read">
<div><dt>温度计</dt><dd>{f"{sc:.0f}" if ok(sc) else '—'}<i>{mood or '—'} · 0–100 加权分</i></dd></div>
<div><dt>名单 A 股中位</dt><dd class="{med_cls}">{f"{med:+.2f}%" if ok(med) else '—'}
<i>{last.get('up', '—')} 涨 / {last.get('down', '—')} 跌</i></dd></div>
<div><dt>超额 vs 沪深300</dt><dd class="{exc_cls}">{f"{exc:+.2f}pt" if ok(exc) else '—'}
<i>沪深300 {f"{last['bench300']:+.2f}%" if ok(last.get('bench300')) else '—'}</i></dd></div>
<div><dt>占 A 股成交额</dt><dd>{f"{share:.1f}%" if ok(share) else '—'}
<i>{num(last.get('amount'))} 亿 · 量能 {f"{last['amt_ratio']:.0f}%" if ok(last.get('amt_ratio')) else '—'}</i></dd></div>
</dl>"""

    def row(r):
        s, m = r.get("score"), r.get("median")
        e = r.get("excess")
        s_col = DOWN if ok(s) and s < 45 else ("#d29922" if not ok(s) or s < 62 else UP)
        return (f"<li><a href='{r['day']}.html'>"
                f"<span class='d'>{dlabel(r['day'])[5:]}</span>"
                f"<span class='m'>{esc(r.get('mood') or '—')}"
                f"<small style='color:{s_col}'>{f'{s:.0f}' if ok(s) else '—'}</small>"
                f"<small>{pct(m)}</small></span>"
                f"<span class='v' style='color:{DOWN if ok(e) and e < 0 else UP if ok(e) else DIM}'>"
                f"{f'{e:+.2f}pt' if ok(e) else '—'}</span>"
                f"<span class='arw'>→</span></a></li>")

    recent = "".join(row(r) for r in reversed(rows[-5:]))
    chart = score_chart(rows, W=1000)
    built = esc(last.get("built") or "")

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 主题每日复盘 · 首页（最新 {dlabel(day)}）</title>
<style>{CSS}{HUB_CSS}</style></head><body>
<div class="bar"><div class="bar-in">
<a class="brand" href="index.html"><span class="dot"></span>AI 主题复盘</a>
<span class="sp"></span>
<a class="btn" href="archive.html" title="全部 {n} 期历史归档 · 温度与中位的逐日总览"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h11"/></svg>历史总览</a>
<a class="btn" href="manual.html" title="数据从哪来、谁在用哪个接口、哪些拿不到"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 100 18 9 9 0 000-18zm0 4v1m0 3v6"/></svg>项目说明</a>
</div></div>
<div class="hwrap">
<header class="hhead">
<p class="hdat">数据日 <b>{dlabel(day)}</b><span class="div">·</span>第 <b>{pos}</b> / {n} 期
<span class="div">·</span>{'今日' if pos == n else '最新已归档'}</p>
<h1 class="h1">{h1}</h1>
<dl class="why">
<div><dt>名单从哪来</dt><dd>GS《China Strategy: AI Changes The Game, China is coming》<span class="nw">（2026-10-11）</span>公开的三张名单表，去重后 <b>{gs_n} 家</b>：A 股 {gs['a'] if gs else 41}、港股 {gs['hk'] if gs else 13}。</dd></div>
<div><dt>都包括什么</dt><dd>{gs_mix}——从芯片、设备、数据中心一路排到模型与落地应用。</dd></div>
<div><dt>为什么能当探针</dt><dd>这 {gs_n} 家串起整条 AI 链，读它们的价格、成交额、估值与分化，就是读市场对 AI 这条线的定价。</dd></div>
</dl>
<p class="hsub">每天盘后出一次，<b class="nw">{dlabel(day)}.html</b> 就是那天的快照，后面重跑也不会改它。</p>
<div class="hact">
<a class="go" href="{day}.html">进入本期复盘
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h14M12 6l6 6-6 6"/></svg></a>
</div>
</header>

{read}

<section class="hsec">
<h2>温度史</h2>
<div class="chart">{chart}</div>
<p class="note">线 = 温度分（0–100），三条虚线是刻度 25 / 62 / 80。
点每一期，悬停可看当天的中位涨跌、涨跌家数和温度。越往右越新。</p>
</section>

<section class="hsec">
<h2>最近五期<a href="archive.html">全部 {n} 期</a></h2>
<ul class="recent">{recent}</ul>
</section>

<footer>AI 主题每日复盘 · 首页生成于 {now_cst():%Y-%m-%d %H:%M}　·　
数据日 {dlabel(day)}{f'（页面生成于 {built}）' if built else ''}　·　
<a href="archive.html">历史归档</a>　·　{GH_ICON}</footer>
</div>
<div id="tip"></div>
{TIP_JS}
</body></html>"""
    return html


def main():
    docs = os.path.join(ROOT, "docs")
    argv = sys.argv[1:]
    if "--docs" in argv:
        docs = os.path.abspath(argv[argv.index("--docs") + 1])
    build(docs)


if __name__ == "__main__":
    main()
