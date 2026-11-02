"""把某一天报告 HTML 里的图，导出成适合发社交媒体的 PNG。

用法:
    python scripts/export_figures.py                    # 最新一期，出 4:5 竖版 + 宽图备份
    python scripts/export_figures.py --date 20261030     # 指定日期
    python scripts/export_figures.py --preset social     # 只出竖版（默认 both）
    python scripts/export_figures.py --preset raw        # 只出原始宽图
    python scripts/export_figures.py --size 1080x1350    # 换画布（4:5 默认；1:1 用 1080x1080）
    python scripts/export_figures.py --scale 3           # 像素密度（默认 2x）

输出（exports/<date>/）:
    00_今日温度.png / 01..NN_*.png   4:5 竖版卡片：品牌+日期 / 章节+大标题 / 图 / 读数三连 / 页脚
    variants/v*.png                  切 chip 才看到的备选图（同样是 4:5）
    raw/**.png                       原始宽图（2188px 宽，做头图或精读时用）
    manifest.json                    清单：文件、标题、章节、尺寸

实现要点:
    - Playwright 打开真页面，样式/字体和网页一致，不重画；
    - 竖版卡片是「重新排版」：图内的标题行搬到 HTML 上放大，SVG 的 viewBox 按剩余内容收紧，
      裁掉图内上下留白，所以图在小屏上也更大一点；
    - 每张图克隆到独立画布再截，chip 按钮、吸顶栏不会渗进来，源 HTML 不动；
    - 4:5 卡片统一底部读数三连（温度 / 名单中位 / 超额），成套发布时像连载。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

BRAND = "AI 主题每日复盘"
PAGE_BG = (14, 17, 22)  # --bg
MONO = "ui-monospace,Menlo,Consolas,monospace"

FONT_SCALE = 1.12  # 图内字号放大倍数（网页按 1120px 宽设计，手机上要更大才好读）

FIG_SUFFIX = {"5": "近5日", "20": "近20日", "all": "全部", "d": "今日"}

INJECT_CSS = """
.bar,.shell{visibility:hidden}          /* 只截独立画布 */
#__stage{position:absolute;left:0;top:0;background:#0e1116;z-index:2147483647}
#__stage .chart{margin:0}
#__stage .comp h2{margin:0 0 12px}
#__stage .comp .cards{margin:12px 0 0}
.xfoot{margin-top:9px;padding-top:8px;border-top:1px solid #232b36;
  display:flex;justify-content:space-between;align-items:baseline;gap:14px;
  font:500 11.5px/1.4 @MONO@;color:#6e7681;letter-spacing:.03em}

/* ---------- 4:5 竖版卡片（贴满画幅：窄边距 + 大字号 + 间距均分） ---------- */
#__stage.s-card{display:flex;flex-direction:column;padding:40px 34px 26px;
  color:#e6edf3;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;overflow:hidden}
.s-card .s-top{display:flex;justify-content:space-between;align-items:center;
  padding-bottom:14px;border-bottom:1px solid #232b36;
  font:500 14px/1 @MONO@;letter-spacing:.1em;color:#8b949e;white-space:nowrap}
.s-card .s-top .s-brand{display:flex;align-items:center;gap:9px;color:#c9d5e1}
.s-card .s-top .s-brand::before{content:'';width:8px;height:8px;border-radius:99px;
  background:#58a6ff;box-shadow:0 0 0 3px #58a6ff26}
.s-card .s-meta{color:#6e7681;letter-spacing:.04em;font-size:13.2px}
.s-card .s-head{margin-top:26px}
.s-card .s-sect{font:600 13.6px/1 @MONO@;letter-spacing:.18em;color:#58a6ff}
.s-card .s-title{margin:14px 0 0;font-size:64px;line-height:1.14;font-weight:680;
  letter-spacing:-.03em;color:#eef4fa;text-wrap:balance}
.s-card .s-sub{margin:14px 0 0;font-size:18px;line-height:1.55;color:#8b949e}
/* 中段=标题+图，整块垂直居中；剩余空间在图上均分，不留一整块黑洞 */
.s-card .s-main{flex:1;display:flex;flex-direction:column;justify-content:center;
  min-height:0;padding:4px 0 0}
.s-card .s-body{display:flex;flex-direction:column;justify-content:flex-start;gap:26px;
  margin:26px 0 0;padding:22px 0;border-top:1px solid #232b36;
  border-bottom:1px solid #232b36;flex:1}
.s-card.s-frame .s-body{justify-content:space-between}
.s-card.s-frame .s-main{flex:1;justify-content:center}
.s-card .s-body .chart{background:none;border:0;border-radius:0;padding:0;
  margin:0;overflow:visible}
.s-card .s-fig{flex:0 0 auto}
.s-card .s-kpi{padding-top:24px;border-top:1px solid #232b36}
.s-card .s-kpi .kick{font:600 12px/1 @MONO@;letter-spacing:.22em;color:#6e7681;
  margin-bottom:18px}
.s-card .s-kpi .row{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.s-card .s-kpi .v{font:600 58px/1.04 @MONO@;letter-spacing:-.02em;color:#e6edf3;
  font-variant-numeric:tabular-nums;display:flex;align-items:baseline;gap:5px}
.s-card .s-kpi .v i{font-style:normal;font-size:17px;color:#8b949e;font-weight:500}
.s-card .s-kpi .k{margin-top:12px;font-size:15px;color:#8b949e}
.s-card .s-kpi .n{margin-top:3px;font:500 13px/1.4 @MONO@;color:#6e7681}
.s-card .s-foot{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
  margin-top:24px;padding-top:16px;border-top:1px solid #232b36;
  font:500 12.4px/1.4 @MONO@;color:#6e7681;letter-spacing:.04em}
/* 封面：温度计 + 2×2 指标卡（卡片纵向撑满，字放大），底部同样收读数 */
.s-card.s-cover .s-main{justify-content:flex-start}
.s-card.s-cover .comp{flex:1;display:flex;flex-direction:column;min-height:0}
.s-card.s-cover .s-body{justify-content:flex-start;gap:20px}
.s-card.s-cover .s-body .chart{flex:0 0 auto;background:var(--panel);
  border:1px solid var(--line);border-radius:14px;padding:20px 18px 14px}
.s-card.s-cover .comp{flex:0 0 auto}
.s-card.s-cover .cards{display:grid!important;grid-template-columns:repeat(4,1fr)!important;
  gap:10px;margin:14px 0 0}
.s-card.s-cover .cards .card{padding:16px 16px 14px;border-radius:12px}
.s-card.s-cover .cards .card .k{font-size:13.4px!important;line-height:1.4;min-height:2.8em}
.s-card.s-cover .cards .card .v{font-size:26px!important;line-height:1.15;margin-top:6px}
.s-card.s-cover .cards .trio{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:10px}
.s-card.s-cover .cards .trio b{display:block;font:600 20px/1 @MONO@;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums}
.s-card.s-cover .cards .trio i{display:block;font:500 11.5px/1 @MONO@;color:#6e7681;
  font-style:normal;margin-top:6px;letter-spacing:.06em}
.s-card.s-cover .cards .card .x{font-size:12.4px!important;margin-top:6px;line-height:1.45}
/* 封面主角：巨型读数 + 冰点判定 */
.s-card.s-cover .hero{display:flex;align-items:baseline;gap:22px;margin:6px 0 20px}
.s-card.s-cover .hero .hn{font:700 172px/0.84 @MONO@;letter-spacing:-.05em;color:#eef4fa;
  font-variant-numeric:tabular-nums;display:flex;align-items:baseline;gap:10px}
.s-card.s-cover .hero .hn i{font-style:normal;font:600 30px/1 @MONO@;color:#8b949e}
.s-card.s-cover .hero .hw{font-size:40px;font-weight:680;letter-spacing:-.02em;
  color:#58a6ff;padding-bottom:14px}
/* 温度计放大成色带（CSS 画的，直接把条加粗、刻度加大，读数交给上面的巨型数字） */
.s-card.s-cover .gauge{padding:24px 24px 22px;border-radius:14px}
.s-card.s-cover .gauge .gwrap{padding:26px 2px 0}
.s-card.s-cover .gauge .gk{height:22px}
.s-card.s-cover .gauge .gk span{font-size:15.4px}
.s-card.s-cover .gbar{height:38px;border-radius:19px}
.s-card.s-cover .gbar i{top:-9px;height:56px;width:5px;border-radius:3px}
.s-card.s-cover .gval{display:none}
/* 今日各层涨跌（中位）：5 条横杠，一眼看出谁最弱 */
.s-card .s-bars{flex:1;display:flex;flex-direction:column;justify-content:space-between;
  gap:9px;padding-bottom:6px}
.s-card .s-bars .bt{font:600 12px/1 @MONO@;letter-spacing:.22em;color:#6e7681}
.s-card .s-bars .row{display:flex;align-items:center;gap:16px}
.s-card .s-bars .nm{flex:0 0 112px;text-align:right;font-size:17px;color:#c9d5e1}
.s-card .s-bars .tr{flex:1;height:34px;background:#151b23;border-radius:9px;overflow:hidden}
.s-card .s-bars .fi{height:100%;border-radius:9px}
.s-card .s-bars .vv{flex:0 0 92px;text-align:right;font:600 22px/1 @MONO@;
  font-variant-numeric:tabular-nums}
""".replace("@MONO@", MONO)

JS_COLLECT = """
() => {
  const h2s = [...document.querySelectorAll('h2')];
  const main = document.querySelector('main') || document.body;
  const txt = el => el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
  const sideRows = [...document.querySelectorAll('.side-card .side-row')].map(r => {
    const b = r.querySelector('b');
    return {k: txt(r.querySelector('span')), v: txt(b), cls: b ? b.className : ''};
  });
  const gauge = document.querySelector('div.chart.gauge');
  return {
    width: Math.round(main.getBoundingClientRect().width) || 1094,
    h1: txt(document.querySelector('header.page h1')),
    sideRows,
    gauge: gauge ? gauge.outerHTML : null,
    s1cards: gauge && gauge.nextElementSibling ? gauge.nextElementSibling.outerHTML : null,
    layers: (() => {
      const heat = [...document.querySelectorAll('div.chart svg')]
        .find(sv => (sv.textContent || '').includes('5日累计'));
      if (!heat) return [];
      const pct = /^[-+]?\\d+(\\.\\d+)?%$/;
      const rows = {};
      [...heat.querySelectorAll('text')].forEach(t => {
        const y = Math.round(parseFloat(t.getAttribute('y') || '0') / 4) * 4;
        (rows[y] || (rows[y] = [])).push(t);
      });
      const out = [];
      Object.keys(rows).map(Number).sort((a, b) => a - b).forEach(y => {
        const cells = rows[y].map(t => ({x: parseFloat(t.getAttribute('x') || '0'), s: txt(t)}));
        const vals = cells.filter(c => pct.test(c.s)).sort((a, b) => a.x - b.x);
        const lab = cells.filter(c => !pct.test(c.s) && !/^\\d+$/.test(c.s) && c.s)
                         .sort((a, b) => a.x - b.x)[0];
        if (vals.length && lab) out.push({name: lab.s, v: parseFloat(vals[0].s)});
      });
      return out;
    })(),
    charts: [...document.querySelectorAll('div.chart')].map(el => {
      const svg = el.querySelector('svg');
      const figEl = el.closest('[data-fig]');
      const box = el.closest('.figbox');
      const chips = box ? [...box.querySelectorAll('.chips')].map(c => {
        const on = [...c.querySelectorAll('.chip.on')].map(x => txt(x)).join('/');
        return txt(c.querySelector('.lbl')) + ':' + on;
      }).join(' · ') : '';
      const h2 = h2s.filter(h => h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).pop();
      const num = h2 && h2.querySelector('.n') ? txt(h2.querySelector('.n')) : '';
      const name = h2 ? txt(h2).replace(num, '').trim() : '';
      return {
        title: svg ? txt(svg.querySelector('text')) : '',
        section: name ? `§${num} ${name}` : '',
        fig: figEl ? figEl.getAttribute('data-fig') : null,
        chips,
        gauge: el.classList.contains('gauge'),
        visible: el.getClientRects().length > 0,
        html: el.outerHTML,
      };
    }),
  };
}
"""

# 竖版卡片：重新排版 + 收紧 SVG 的空白
JS_CARD = """
(A) => {
  document.querySelectorAll('#__stage').forEach(n => n.remove());
  const st = document.createElement('div');
  st.id = '__stage';
  const frame = A.fitMode === 'frame';
  st.className = 's-card' + (A.cover ? ' s-cover' : '') + (frame ? ' s-frame' : '');
  st.style.width = A.width + 'px';
  st.style.height = (frame ? A.height : 'auto') + (frame ? 'px' : '');
  st.innerHTML =
    '<div class="s-top"><span class="s-brand">' + A.brand + '</span>' +
    '<span class="s-meta">' + A.meta + '</span></div>' +
    '<div class="s-main"><div class="s-head"><div class="s-sect">' + A.sect + '</div>' +
    '<h1 class="s-title"></h1><p class="s-sub"></p></div>' +
    '<div class="s-body">' + A.html + '</div></div>' +
    (A.kpis ? '<div class="s-kpi">' + A.kpis + '</div>' : '') +
    '<div class="s-foot"><span>' + A.footL + '</span><span>' + A.footR + '</span></div>';
  document.body.appendChild(st);

  const svg = st.querySelector('svg');
  let title = A.title || '', sub = A.sub || '';
  if (svg && !A.keepTitles) {
    const kids = [...svg.children];
    const isText = c => c.tagName.toLowerCase() === 'text';
    let minX = Infinity, plotTop = Infinity;
    kids.filter(c => !isText(c)).forEach(c => {
      try {
        const b = c.getBBox();
        if (b.width || b.height) { minX = Math.min(minX, b.x); plotTop = Math.min(plotTop, b.y); }
      } catch (e) {}
    });
    // 认标题的规则：贴着画布上沿（y ≤ 58）、左对齐（x 在图区左边界附近）、不是轴标签那种 end 锚点。
    // 只按「在所有图形上方」判断会误杀热力图的列头 / 象限图的角标，所以这里多加两个约束。
    // 图内字号整体放大一档：网页是按 1120px 宽看的，压到手机宽度就太小
    const fs = A.fontScale || 1;
    if (fs !== 1) {
      svg.querySelectorAll('text').forEach(t => {
        const cur = parseFloat(t.getAttribute('font-size') || '');
        if (cur > 0) t.setAttribute('font-size', (cur * fs).toFixed(1));
      });
    }
    const headTexts = kids.filter(isText).filter(t => {
      let b; try { b = t.getBBox(); } catch (e) { return false; }
      const anchor = t.getAttribute('text-anchor') || 'start';
      return b.y <= 58 && anchor !== 'end' && b.x <= minX + 10;
    }).slice(0, 4);
    const picked = headTexts.map(t => (t.textContent || '').trim()).filter(Boolean);
    if (!title && picked.length) title = picked[0];
    if (!sub && picked.length > 1) {
      const join = (a, b) => !a ? b : (/[，、：；,]$/.test(a) ? a + b : a + ' · ' + b);
      sub = picked.slice(1).reduce(join, '');
    }
    headTexts.forEach(t => t.remove());

    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    [...svg.children].forEach(c => {
      let b; try { b = c.getBBox(); } catch (e) { return; }
      if (!b.width && !b.height) return;
      x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y);
      x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height);
    });
    const vb = svg.viewBox.baseVal;
    if (isFinite(x0) && vb && vb.width > 0) {
      const pad = 4;
      const nx = Math.max(vb.x, x0 - pad), ny = Math.max(vb.y, y0 - pad);
      const nw = Math.min(vb.x + vb.width, x1 + pad) - nx;
      const nh = Math.min(vb.y + vb.height, y1 + pad) - ny;
      if (nw > 120 && nh > 40) {
        svg.setAttribute('viewBox',
          nx.toFixed(1) + ' ' + ny.toFixed(1) + ' ' + nw.toFixed(1) + ' ' + nh.toFixed(1));
      }
    }
  }
  const titleEl = st.querySelector('.s-title');
  titleEl.textContent = title || A.sect;
  // 长标题自动降字号，别撑成两行
  const tl = (title || A.sect || '').length;
  titleEl.style.fontSize = (tl <= 8 ? 68 : tl <= 12 ? 58 : tl <= 18 ? 48 : 40) + 'px';
  const subEl = st.querySelector('.s-sub');
  if (sub) subEl.textContent = sub; else subEl.remove();

  // 内容全部落位后再量高度：超过画幅上限（默认 4:5）就退回固定画幅，并把图区缩一点塞进去
  const over = () => st.scrollHeight > st.clientHeight + 1;
  if (!frame && st.offsetHeight > A.height + 1) {
    st.style.height = A.height + 'px';
    st.classList.add('s-frame');
  }
  if (A.fit) {
    const body = st.querySelector('.s-body');
    const cs = getComputedStyle(body);
    const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    const avail = body.clientHeight - padY;
    const need = body.scrollHeight - padY;
    if (need > avail + 1 && need > 0) {
      body.style.transform = 'scale(' + (avail / need).toFixed(4) + ')';
      body.style.transformOrigin = 'center center';
    }
  }
  return {title, sub, h: Math.round(st.offsetHeight), frame: st.classList.contains('s-frame')};
}
"""

# 原始宽图：把节点克隆到独立画布，只补一行页脚
JS_RAW = """
(A) => {
  document.querySelectorAll('#__stage').forEach(n => n.remove());
  const stage = document.createElement('div');
  stage.id = '__stage';
  stage.style.width = A.width + 'px';
  stage.innerHTML = A.html;
  const chart = stage.querySelector('.chart');
  if (A.foot && chart) {
    const d = document.createElement('div');
    d.className = 'xfoot';
    d.innerHTML = A.foot;
    chart.appendChild(d);
  }
  document.body.appendChild(stage);
  return true;
}
"""

JS_CLEAR = "() => document.querySelector('#__stage')?.remove()"
JS_GO_TOP = "() => window.scrollTo(0, 0)"


def clean_title(title: str, section: str) -> str:
    t = title or (section.split(" ", 1)[-1] if section else "")
    t = re.sub(r"[（(][^）)]*[）)]", "", t)
    t = t.replace("×", "x").replace("：", "_").replace(":", "_")
    t = re.sub(r"\s*([x+_])\s*", r"\1", t)
    t = re.sub(r"[\\/:*?\"<>|]+", "_", t)
    t = re.sub(r"\s+", "_", t.strip())
    return t.strip("_")[:48] or "figure"


def display_name(stem: str, suffix: str) -> str:
    if not suffix or suffix in stem:
        return stem
    if suffix == "今日" and ("今天" in stem or "今日" in stem):
        return stem
    return f"{stem}_{suffix}"


def fig_suffix(fig: str | None) -> str:
    if not fig:
        return ""
    return FIG_SUFFIX.get(fig.split("_")[-1], fig)


def settle(page, ms: int = 60) -> None:
    page.evaluate("""(ms) => new Promise(r =>
        requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, ms))))""", ms)


def flatten(png: bytes) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    bg = Image.new("RGBA", img.size, PAGE_BG + (255,))
    bg.alpha_composite(img)
    out = io.BytesIO()
    bg.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def shoot(page, target: Path, scale: int) -> dict:
    settle(page)
    handle = page.query_selector("#__stage")
    if handle is None:
        raise RuntimeError(f"画布没建起来：{target.name}")
    box = handle.bounding_box()
    if not box:
        raise RuntimeError(f"取不到画布尺寸：{target.name}")
    handle.scroll_into_view_if_needed()
    settle(page, 40)
    target.write_bytes(flatten(handle.screenshot(type="png")))
    return {"png": f"{box['width'] * scale:.0f}x{box['height'] * scale:.0f}",
            "bytes": target.stat().st_size}


def kpi_strip(rows: list[dict]) -> str:
    """底部读数三连：温度 / 名单中位 / 超额。全部取侧栏真值。"""
    by = {r["k"]: r for r in rows}

    def cell(value: str, unit: str, label: str, note: str, color: str) -> str:
        u = f"<i>{unit}</i>" if unit else ""
        n = f'<div class="n">{note}</div>' if note else ""
        style = f' style="color:{color}"' if color else ""
        return (f'<div><div class="v"{style}>{value}{u}</div>'
                f'<div class="k">{label}</div>{n}</div>')

    cells = []
    t = by.get("温度计指数", {}).get("v", "")
    if t:
        num, _, word = t.partition("·")
        cells.append(cell(num.strip(), "分", "温度", word.strip(), "#e6edf3"))
    m = by.get("名单中位", {})
    if m:
        cells.append(cell(m["v"], "", "名单中位", "41 家 A + 13 家 H",
                          "#3fb950" if "down" in m.get("cls", "") else
                          "#f85149" if "up" in m.get("cls", "") else ""))
    e = by.get("超额 vs 沪深300", {})
    if e:
        cells.append(cell(e["v"], "", "超额 vs 沪深300", "宽基 beta 口径",
                          "#3fb950" if "down" in e.get("cls", "") else
                          "#f85149" if "up" in e.get("cls", "") else ""))
    if not cells:
        return ""
    return (f'<div class="kick">本期读数</div>'
            f'<div class="row">{"".join(cells)}</div>')


def cover_cards(cards_html: str) -> str:
    """首张指标卡把三个窗口的读数摊成三列，别在窄卡里折成两行。"""
    m = re.search(r'<div class="card"><div class="k">名单中位涨跌[^<]*</div>\s*'
                  r'<div class="v"[^>]*>(.*?)</div>\s*</div>', cards_html, re.S)
    if not m:
        return cards_html
    vals = re.findall(r"[-+]?\d+\.\d+%", m.group(1))
    if len(vals) != 3:
        return cards_html
    cells = "".join(
        f'<div><b style="color:{"#3fb950" if v.startswith("-") else "#f85149"}">{v}</b>'
        f'<i>{cap}</i></div>'
        for v, cap in zip(vals, ("今", "5日", "20日")))
    block = ('<div class="card"><div class="k">名单中位涨跌</div>'
             f'<div class="trio">{cells}</div></div>')
    return cards_html[:m.start()] + block + cards_html[m.end():]


def layer_bars(layers: list[dict]) -> str:
    """今日各层中位涨跌的横条图：从强到弱排，条长按最大绝对值归一。"""
    # JSON 会把 −1.0 变成 −1（int），别用 isinstance(float) 过滤
    rows = [{"name": x["name"], "v": float(x["v"])} for x in layers
            if isinstance(x.get("v"), (int, float))]
    if len(rows) < 2:
        return ""
    rows.sort(key=lambda x: -x["v"])
    mx = max(abs(x["v"]) for x in rows) or 1.0
    out = []
    for x in rows:
        v = x["v"]
        color = "#3fb950" if v < 0 else "#f85149"       # A 股习惯：绿跌红涨
        edge = "#2ea043" if v < 0 else "#da3633"
        pct = min(100.0, abs(v) / mx * 100.0)
        out.append(
            f'<div class="row"><div class="nm">{x["name"]}</div>'
            f'<div class="tr"><div class="fi" style="width:{pct:.1f}%;'
            f'background:linear-gradient(90deg,{edge}00,{color})"></div></div>'
            f'<div class="vv" style="color:{color}">{v:+.1f}%</div></div>')
    return ('<div class="s-bars"><div class="bt">今日各层涨跌（中位）</div>'
            + "".join(out) + "</div>")


README_TMPL = """# {iso} 图片导出（发社交媒体用）

源头：`docs/{date}.html`
生成：`python scripts/export_figures.py --date {date}`（需要 Playwright，约 1 分钟）
重跑会覆盖同名图片并重写本文件。

画布宽度固定 {w}px，**高度跟着内容走**（上限 {maxh}），出图 {scale}x。
每张图都是满的：既没有「一张横图飘在黑底中间」的留白，也没有重复的读数条。
代价是各图高度不一，X、微博、公众号、小红书正文都正常显示；要等高的 4:5 就加 `--fit frame`。

## 主推：一条多图帖

| 顺序 | 文件 | 内容 | 像素 |
|---|---|---|---|
{rows}

一张图 = 报告的一章。横图在竖版画幅里最多只占满宽度，一章的图叠起来才填满，
阅读顺序也正好是报告的章节顺序。

- 封面是海报版式：巨型温度读数 + 加粗色带 + 4 张指标卡 + 「今日各层涨跌」横条图，不写正文；
- 章节卡把图内小标题搬成 HTML 大标题，viewBox 按内容收紧，图内字号 ×1.12；
- `variants/` = 切 chip 才看得到的备选窗口，同样贴满排版，可直接替换主图；
- `raw/` = 2188px 宽的原版（图内自带标题），做头图 / 横图 / 精读用；
- `singles/` 默认不出，加 `--singles` 才把每张图单独出一份；
- 读数三连（温度 / 名单中位 / 超额）默认不加，`--kpi` 可补回。

## 换比例 / 更清晰

```bash
python scripts/export_figures.py --size 1080x1080   # 方图
python scripts/export_figures.py --size 1080x1440   # 3:4 更高
python scripts/export_figures.py --fit frame        # 强制 4:5 画幅
python scripts/export_figures.py --scale 3          # 3x（3240px 宽）
```

## 配文草稿

> {caption}

`manifest.json` 里有每个文件的标题、章节、变体（chapter/variant/raw）与像素尺寸。
"""


def readme_caption(rows: list[dict]) -> str:
    by = {r["k"]: r["v"] for r in rows}
    t = by.get("温度计指数", "")
    num, _, word = t.partition("·")
    m, e = by.get("名单中位", ""), by.get("超额 vs 沪深300", "")
    if not (num and m and e):
        return "（这里写配文）"
    return (f"AI 主题温度 {num.strip()} 分（{word.strip()}）。名单中位 {m}，"
            f"超额 vs 沪深300 {e}。分层看谁最弱、谁最抗跌 → 见最后一张。")


def write_readme(outdir: Path, made: list[dict], date: str, iso: str,
                 cw: int, ch: int, scale: int, rows_src: list[dict] | None = None) -> None:
    """顺手把发布说明书写出来，省得每次手写、也不会被清目录搞丢。"""
    cap = readme_caption(rows_src or [])
    order = [f for f in made if f.get("variant") in ("social", "chapter")]
    rows = []
    for i, f in enumerate(order, 1):
        px = f["png"].replace("x", "×")
        rows.append(f"| {i} | `{f['file']}` | {f['title']} | {px} |")
    text = README_TMPL.format(
        iso=iso, date=date, w=cw, maxh=f"{ch}px（4:5）", scale=scale, caption=cap,
        rows="\n".join(rows) or "| — | — | — | — |")
    (outdir / "README.md").write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="报告日期 yyyymmdd，默认取 docs 下最新一天")
    ap.add_argument("--preset", choices=["both", "social", "raw"], default="both")
    ap.add_argument("--size", default="1080x1350",
                    help="画布宽度 + 高度上限，WxH，默认 1080x1350（高度 ≤ 4:5）")
    ap.add_argument("--fit", choices=["content", "frame"], default="content",
                    help="content=画布高度随内容（贴满，默认）；frame=固定 4:5 画幅")
    ap.add_argument("--scale", type=int, default=2, help="像素密度倍数，默认 2")
    ap.add_argument("--out", default="exports", help="输出根目录，默认 exports/")
    ap.add_argument("--only-visible", action="store_true", help="只出默认展示的图，跳过 chip 备选图")
    ap.add_argument("--kpi", action="store_true",
                    help="在图底加「本期读数」三连（温度/名单中位/超额），默认不加")
    ap.add_argument("--singles", action="store_true",
                    help="额外出一张图一帖的单图版（放 singles/，横图在 4:5 里偏空，默认不出）")
    args = ap.parse_args()

    date = args.date
    if not date:
        days = sorted(p.stem for p in DOCS.glob("2*.html") if p.stem.isdigit())
        if not days:
            print("docs 下没找到日报 HTML", file=sys.stderr)
            return 1
        date = days[-1]
    src = DOCS / f"{date}.html"
    if not src.exists():
        print(f"找不到 {src}", file=sys.stderr)
        return 1

    cw, ch = (int(x) for x in args.size.lower().split("x"))
    outdir = ROOT / args.out / date
    vardir = outdir / "variants"
    rawdir = outdir / "raw"
    rawwdir = rawdir / "variants"
    iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    social = args.preset in ("both", "social")
    raw = args.preset in ("both", "raw")
    singlesdir = outdir / "singles"
    dirs = [outdir, vardir, rawdir, rawwdir] + ([singlesdir] if args.singles else [])
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    made: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1300},
                                device_scale_factor=args.scale)
        page.goto(src.as_uri(), wait_until="load")
        page.add_style_tag(content=INJECT_CSS)
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(600)

        data = page.evaluate(JS_COLLECT)
        width, charts = data["width"], data["charts"]
        page.evaluate(JS_GO_TOP)
        kpis = kpi_strip(data["sideRows"])
        meta = f"{iso} · {next((r['v'] for r in data['sideRows'] if r['k'] == '期次'), '')} 期"
        issue = meta.split(" · ")[-1]
        foot_l = "gs50_dashboard · 54 家 AI 探针"

        def social_shot(it, target: Path, **extra):
            payload = {"width": cw, "height": ch, "fitMode": args.fit, "brand": BRAND, "meta": meta,
                       "sect": it["section"], "html": it["html"],
                       "kpis": kpis if args.kpi else "",
                       "footL": foot_l, "footR": issue,
                       "title": it.get("title", ""), "sub": "",
                       "fontScale": FONT_SCALE}
            payload.update(extra)
            r = page.evaluate(JS_CARD, payload)
            if os.environ.get("EF_DEBUG"):
                print("   debug", target.name, r)
            settle(page)
            info = shoot(page, target, args.scale)
            page.evaluate(JS_CLEAR)
            return info

        # ---------- 封面：温度计 + 当日指标卡 ----------
        if social and data["gauge"] and data["s1cards"]:
            t = next((r["v"] for r in data["sideRows"] if r["k"] == "温度计指数"), "")
            score, _, word = t.partition("·")
            hero = (f"<div class='hero'><div class='hn'>{score.strip()}"
                    f"<i>分</i></div><div class='hw'>{word.strip()}</div></div>")
            html = (hero + data["gauge"] + cover_cards(data["s1cards"])
                    + layer_bars(data.get("layers") or []))
            target = outdir / "00_今日温度.png"
            info = social_shot(
                {"section": "§1 温度计", "html": html, "title": "今日温度"},
                target,
                cover=True,
                sub="",
                footR="温度计 0–100 分",
            )
            made.append({"file": target.name, "title": "今日温度（巨型读数 + 色带 + 指标卡 + 层涨跌条）",
                         "section": "§1 温度计", "variant": "social", **info})
            print(f"  {str(target.relative_to(ROOT)):64s} {info['png']}")

        # ---------- 章节卡：一整章的图叠放，撑满 4:5（一张横图单独放会空掉一半） ----------
        if social:
            groups: dict[str, list] = {}
            for it in charts:
                if it["gauge"] or not it["visible"] or not it["section"]:
                    continue
                groups.setdefault(it["section"], []).append(it)
            gno = 0
            for sect, items in groups.items():
                if len(items) < 2:
                    continue
                gno += 1
                name = sect.split(" ", 1)[-1]
                html = "".join(f"<div class='s-fig'>{x['html']}</div>" for x in items)
                target = outdir / f"{gno:02d}_{clean_title(name, sect)}.png"
                info = social_shot({"section": sect, "html": html, "title": name},
                                   target, keepTitles=True, fit=True,
                                   footR=f"{issue} · {len(items)} 张图")
                made.append({"file": str(target.relative_to(outdir)).replace("\\", "/"),
                             "title": name, "section": sect, "variant": "chapter", **info})
                print(f"  {str(target.relative_to(ROOT)):64s} {info['png']}")

        main_no = var_no = 0
        for it in charts:
            if args.only_visible and not it["visible"]:
                continue
            stem = display_name(clean_title(it["title"], it["section"]), fig_suffix(it["fig"]))
            if it["visible"]:
                main_no += 1
                base = f"{main_no:02d}_{stem}"
            else:
                var_no += 1
                base = f"v{var_no:02d}_{stem}"

            # 竖版单图卡（默认不出：横图单独放进 4:5 会空掉一半，--singles 时才生成）
            if social and not it["gauge"] and it["title"] and (args.singles or not it["visible"]):
                target = (singlesdir if it["visible"] else vardir) / f"{base}.png"
                info = social_shot(it, target)
                made.append({"file": str(target.relative_to(outdir)).replace("\\", "/"),
                             "title": it["title"] or it["section"], "section": it["section"],
                             "variant": "single" if it["visible"] else "variant", **info})
                print(f"  {str(target.relative_to(ROOT)):64s} {info['png']}")

            # 原始宽图
            if raw:
                target = (rawdir if it["visible"] else rawwdir) / f"{base}.png"
                page.evaluate(JS_RAW, {"html": it["html"], "width": width,
                                       "foot": f"<span>{BRAND} · {iso}</span>"
                                               f"<span>{it['section']}</span>"})
                info = shoot(page, target, args.scale)
                page.evaluate(JS_CLEAR)
                made.append({"file": str(target.relative_to(outdir)).replace("\\", "/"),
                             "title": it["title"] or it["section"], "section": it["section"],
                             "variant": "raw", **info})
                print(f"  {str(target.relative_to(ROOT)):64s} {info['png']}")

        browser.close()

    (outdir / "manifest.json").write_text(
        json.dumps({"date": date, "source": f"docs/{date}.html", "scale": args.scale,
                    "social_canvas": f"{cw}x{ch}", "count": len(made), "figures": made},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_readme(outdir, made, date, iso, cw, ch, args.scale, data["sideRows"])
    print(f"\n共 {len(made)} 个文件 → {outdir}（README.md 已重写）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
