# GS50 每日复盘看板（独立服务）

GS 三张 AI 表去重出的 **54 家**（A 41 / HK 13）当「探针」，每天读一遍 AI 这条线在中国的
温度、广度、轮动与背离，出一张离线的单文件 HTML，并按日归档。

- 产出：`docs/YYYYMMDD.html`（每日一份，永不覆盖）+ `docs/index.html`（首页门厅：最新一期结论 + 入口）+ `docs/archive.html`（历史归档页）
- 部署：`docs/` 直接当 GitHub Pages 的源目录，`deploy.bat` 一键 commit + push；云端则由 GitHub Actions 每个交易日自动跑并发布
- 输入：`data/` 下的日频快照与历史（本地 CSV，可追溯、可重算）
- 这不是组合，没有权重和归因；54 家是采样点，不是持仓。

## 一天怎么跑

```bash
python scripts/run_daily.py                 # 抓今天 → 出页面 → 归档（今天没盘后包就出最近一个交易日）
python scripts/run_daily.py 20261029        # 指定数据日
python scripts/run_daily.py --rebuild       # 不抓数，只用已有 data/ 重建页面 + 归档
python scripts/run_daily.py --no-consensus  # 跳过 A 股共识（最慢的一步，页面复用最近一版）
python scripts/run_daily.py --no-hk         # 跳过港股
python scripts/run_daily.py --no-backfill   # 只出今天这一页，不补历史
```

流水线四步，每一步都能单独跑：

| 步骤 | 命令 | 干什么 | 耗时 |
|---|---|---|---|
| 1 | `theme_data.py all <day>` | 通达信盘后包 + 回补历史 + A 股快照 + 日线 + 港股 | 1–2 分钟 |
| 2 | `fetch_consensus.py <day>` | A 股卖方共识 → PE_NTM（tushare `report_rc`，逐股） | 1–20 分钟（首跑慢，有缓存） |
| 3 | `build_gs50_dashboard.py <day>` | 出 `docs/<day>.html` + `docs/data/<day>.json` | 2 秒 |
| 4 | `build_archive.py` | 出 `docs/archive.html`（趋势图 + 逐日表）+ 重写 `docs/index.html` | 1 秒 |

看页面（本地起个静态服务，或直接双击 HTML）：

```bash
python -m http.server 8000 --directory docs
# 或直接双击 docs/20261029.html（单文件内嵌 SVG，离线可看）
```

## 历史归档（为什么每天留一份）

- **每日一份固定文件** `docs/20261029.html`：当时生成的页面原样留着，以后重跑任何东西都不会改它，
  链接永久可引用。归档页 `docs/archive.html` 只抽三个数（温度分 / 名单中位 / 沪深300）画趋势。
- **补历史页是时点正确的**：重建旧日子时，盘后包、K 线、共识都切到「≤ 该日」，
  所以 5/20 日动量、60 日高、量能倍数都不会偷看未来。
- **唯一例外是腾讯快照**（市值 / PE / 换手）：接口只给当日值。老日子退到最早一份快照，
  页面顶部会挂「回溯归档页」提示，那一页只看估值结构、别比数值。
- 缺页自愈：
  `run_daily.py` 每次会检查 `docs/<day>.html` 是否齐全，缺的就补（首次部署 36 天约 3 分钟）。
  改了页面模板要统一刷一遍旧日期页：`python scripts/run_daily.py --rebuild --all`（约 7 分钟）。

## 部署到 GitHub Pages

**线上：<https://jst-well-dan.github.io/gs50-dashboard/>**（仓库 <https://github.com/Jst-Well-Dan/gs50-dashboard>，public）

Pages 的源目录就是 `docs/`，不用改任何构建配置：

1. Settings → Pages → Source: *Deploy from a branch* → `main` / `/docs`
2. 每天跑完 `python scripts/run_daily.py`，再双击 `deploy.bat`（commit + push）→ Pages 自动重建
3. 或者根本不用管本机：见下面「云端每天自动跑」

### 云端每天自动跑（GitHub Actions）

`.github/workflows/daily.yml`：每个交易日 **17:30 北京时间**跑一遍完整流水线（19:30 再补跑一次，
盘后包发得晚也不会漏），出完页面直接把 `docs/` 提交回 `main`，Pages 随之自动发布。

- 需要 `TUSHARE_KEY` secret：`gh secret set TUSHARE_KEY`（值就是 `.env` 里的 tushare_key）
- 手动触发：`gh workflow run daily`，或 Actions 页面点 *Run workflow*（可指定 `day=YYYYMMDD`）
- `data/` 与 `.cache/` 不入库，靠 Actions cache 在多次运行间接力，不必每次重新回补历史
- 非交易日 / 盘后包还没发布：`--require-today` 让整次运行 1 秒内干净退出，**不会**拿今天的快照去重出昨天的页面
- 云端跑完是新的 commit；本机 `git pull` 就能对齐（页面内容一致，`data/` 各在各的）

实测（2026-10-31）：通达信盘后包、腾讯行情/K 线、tushare 代理在 GitHub runner 上均可达，
整轮流水线约 20 分钟（大头是 41 家 A 股共识 + 13 家港股日线）。

线上路径：`/` → 首页门厅（最新一期的结论 + 读数 + 趋势 + 入口），`/<YYYYMMDD>.html` → 某一天，
`/archive.html` → 全部日期。三个 URL 三个角色，没有别名：**页内永远能跳回首页，也永远能往前后一天走**
（日期页顶栏自带 `‹ 前一天 / 后一天 › / 全部日期 / 最新`）。旧的 `/gs50_dashboard.html` 已退役，只剩一个跳到 `/` 的壳。

`docs/` 体积参考：一页 ~220–250 KB（gzip 后 ~46 KB，git 内部同样压缩），
250 个交易日 ≈ 11 MB/年，两年内不用担心仓库变大。

`.gitignore` 已经排掉了 `.env`（tushare key）、`.cache/` 和 `data/mkt|quote|kline|consensus|hk`
（日频原始数据，一天 600 KB+，不进仓库；页面里已经把当日结论固化了）。
这意味着**在新机器 clone 下来只能看页面，不能重算历史**——要重算得把 `data/` 也带上。

## 目录

```
scripts/
  run_daily.py              唯一入口，串起下面四步
  theme_data.py             行情数据层（通达信官网盘后包 / 腾讯行情 / 腾讯 K 线 / 港股）
  fetch_hk.py               港股 13 家：快照 + 日线 + 共识 NTM PE
  fetch_consensus.py        A 股卖方共识 NTM（tushare report_rc）
  build_hk_kline.py         港股 13 家日线收益长表 → data/hk/kline.csv（归档页的港股中位靠它）
  build_gs50_dashboard.py   出单日页面（--out / --summary 指定输出）
  build_archive.py          历史归档页（读 docs/data/*.json）
  tushare_client.py         tushare 代理客户端（限速 + 重试）
data/
  gs50.csv                  名单（54 家 = A 41 + HK 13，含 GS 三张表的归属）
  mkt/YYYYMMDD.csv          全市场盘后包（5,500+ 只，涨幅/成交额/涨跌停）
  quote/YYYYMMDD.csv        名单 + 基准指数快照（市值/PE_TTM/换手/量比/PB）
  kline/daily.csv           名单 + 基准日线（前复权，长表）
  consensus/YYYYMMDD.csv    A 股 NTM 共识（FY1/FY2 中位、覆盖机构数）
  hk/YYYYMMDD.csv           港股 13 家（快照 + 日线派生指标 + NTM 共识）
  hk/kline.csv              港股 13 家日收益长表（hk_code,date,pct,amount_yi；最近 260 个交易日）
  em_cons.csv               东财 AI 概念板块成分（77 板块 / 12,729 条，静态基线）
  em_board_stats.csv        板块 → AI 层/细分 的映射与规模（静态）
.cache/
  hk_consensus/{code}.json  港股共识缓存（3 天刷新，Yahoo 限速时沿用旧值）
  consensus/{ts_code}.csv   A 股研报原文缓存（改窗口不用重抓）
docs/
  YYYYMMDD.html             每天一份，永不覆盖
  index.html                首页门厅：最新一期结论 + 读数 + 温度史 + 入口（每次运行重写，不自动跳）
  archive.html              归档页（趋势 + 逐日表）
  gs50_dashboard.html       已退役：只留一个跳到 index.html 的壳（兼容老链接 / 本地双击）
  data/YYYYMMDD.json        归档页用的每日摘要（<1 KB）
run_daily.bat               双击跑当天
deploy.bat                  双击 commit + push 到 GitHub
```

## 数据源与口径

| 数据 | 来源 | 口径 / 注意 |
|---|---|---|
| A 股收盘、成交额、涨跌停 | 通达信官网盘后包（`theme_data.py`） | 与交易所一致；T 日盘后发布 |
| A 股市值、PE_TTM、换手、量比 | 腾讯行情 `qt.gtimg.cn` | 只有当日快照 → 回看历史日时用「不晚于该日的最近一份」并在页头标注快照日 |
| A 股日线（60/250 日高低、40 日走势） | 腾讯 `ifzq` 前复权日线 | 每只保留最近 260 根，页面取 60/250/40 窗口 |
| A 股 PE_NTM | tushare `report_rc` 研报盈利预测 | `NTM = w×FY1 + (1−w)×FY2`，`w = 1 − 当年已过天数/365`；每家机构取窗口内最新年报口径，剔除 180 天无更新，取中位数 |
| 基准（沪深300 / 创业板指 / 恒生科技） | 腾讯指数（当日快照）+ 腾讯 K 线（历史） | **超额口径**：A 股 41 家用沪深300（宽基 beta）+ 创业板指（同风格）；港股 13 家用恒生科技（同市场）。历史日一律用 K 线切到当日，只有「当天自己那份 `quote/`」才用快照——否则回溯旧日时沪深300 会变成一条常量线 |
| 港股中位涨跌（历史） | `data/hk/kline.csv`（tushare `hk_daily` 逐股，腾讯兑底） | 只存日收益 `pct`，不存价格：前复权是「对齐最新收盘」，价格会在增量合并的接缝处跳；有 `hk/YYYYMMDD.csv` 当日快照时以快照为准 |
| 港股快照（价格/成交额/市值/PE_TTM） | 腾讯 `qt.gtimg.cn`（`hk` 前缀） | 字段位与 A 股不同 |
| 港股日线 | **tushare `hk_daily`**（主）/ 腾讯 `hkfqkline`（兜底） | 代理单次区间上限约 1 年 → 分段往回拉；`hk_daily` 未复权但 `pre_close` 已按除净调整，故复权收益 = `pct_chg` 累乘；tushare 港股 T+1 才出当天，用快照补最后一根 |
| 港股 PE_NTM | Yahoo Finance `earnings_estimate` | 与 A 股同公式、按各公司财年（阿里/联想 3 月年结）；港股 EPS 多为人民币，按当日 `CNYHKD` 折港元；Yahoo 自己的 `forwardPE`（FY1 口径）存 `yahoo_fwd_pe` 对账 |
| 板块中位/量能 | 东财概念板块（`em_cons.csv`）+ 盘后包 | 板块中位 = 成分等权中位；成分表是静态基线，板块调整需重新抓 |

## 已知缺口（按影响排序）

1. **港股前瞻 PE 的机构覆盖薄**：13 家都能算，但小票只有 1–4 家机构（天岳先进 1 家），
   腾讯 32 家、阿里 19 家。图 7 里港股是空心点，口径相同、机构少的别逐格比。
2. **港股历史只有从建库那天起累积的快照**：`hk/20261028.csv` 只有当日列；
   5/10/20 日、量能、走势靠日线算得出，但历史日的市值/PE 快照无法回补。
   补历史页时如果那天没有 `hk/<day>.csv`，页面会挂「港股缺失」提示，港股 13 家留空而不是拿别的日期充数。
3. **Yahoo 会间歇限速（429）**：共识按股票缓存、3 天刷新；限速或超时时沿用上一份并记 `cons_date`。
   首次建库若撞上限速，该家当天退回 PE_TTM，页面会标 `TTM`。
4. **A 股共识只覆盖 40/41 家**（`report_rc` 无更新的剔除），且有 180 天新鲜度要求。
5. **板块成分是静态基线**（12,729 条 / 77 板块，其中 5 个板块可能被东财截断）；
   更新需要重抓东财概念成分。
6. `quote/` 快照按「最近一份 ≤ 数据日」取用，回看很久以前的日期时市值/PE 会略偏（股本变化慢，影响小）。

## 字段速查

- `data/mkt/*.csv`：`date, market, code, name, prev_close, open, high, low, close, volume, amount,
  pct_chg, amount_yi, limit_pct, limit_up, limit_down`
- `data/quote/*.csv`：`code, name, price, pct_chg, amount_yi, turnover, pe_ttm, amplitude,
  float_mcap_yi, mcap_yi, pb, limit_up, limit_down, vol_ratio, pe_static, stale`
- `data/consensus/*.csv`：`code6, ts_code, date, fy1_med, fy2_med, n_brokers, w, ntm_yi, p25, p75`
- `data/hk/*.csv`：`hk_code, name, price, pct_chg, amount_yi, total_mv_yi, pe_ttm, pe_ntm,
  n_brokers, cons_date, fy0_eps, fy1_eps, eps_ccy, fy0_end, yahoo_fwd_pe, hkd_cny, hist_src,
  amt_20d, amt_mult, ret5, ret10, ret20, ret_all, dist_hi60, dist_lo250, sigma60, spark`
- `data/kline/daily.csv`：`code, date, open, close, high, low, volume`（前复权长表）
- `data/gs50.csv`：`name_cn, name_en, gs_ticker, lists, ai_layer, ai_sector, market, a_code6,
  hk_code, trackable`

## 页面结构

1 温度计 · 2 广度与分化 · 3 结构轮动 · 4 名单 vs 板块 · 5 异动榜 · 6 全表 · 7 估值（贵不贵 × 有没有人追）· 8 明天盯什么（规则生成）· 9 口径与积累

另有一页 `docs/index.html`（首页门厅）：当天的结论当 H1、四项读数（温度 / A 股中位 / 超额 / 成交占比）、
37 期温度史、最近五期列表，以及「进入本期复盘 / 全部 37 期」两个入口。不自动跳转，可收藏。

另有一页 `docs/archive.html`：最新一期（日期 + 温度 + 中位 + 超额）/ 区间平均温度 / 日均中位 / 跑赢基准天数 / 最新港股中位，
三张图（温度分折线、A 股中位柱 + 沪深300 / 创业板指虚线、港股中位柱 + 恒生科技虚线）和逐日表（14 列：温度、A 股中位、涨跌、新高、成交额、
占 A 股市场、超额300、超额创50、港股中位、超额 HSTECH、5/20 日、异动），每行链到那一天的完整页面。

逐日表**按月份用 `<details>` 分组**（最新月默认展开，其余收起，每组标题带「N 期 · 平均温度 · 跑赢 x/y」），
上方一排月份锚点可跳转（目标是折叠的月时会自动展开）。所以页面高度只跟「一个月多少天」有关，
不会随历史线性变长；不加分页，因为这张表的价值就是「一眼扫到连续多天」，而 `<details>` 里的内容
仍会被浏览器 Ctrl+F 找到。桌面端表头吸顶（手机端 `.scroll` 要横向滑动，所以不吸顶）。

### 排版口径（字号 / 图表缩放）

- 正文 15px；除正文外的所有小字**不低于 12.4px**（卡片说明、表头、侧栏、口径折叠、页脚都在这一层）。
- 图表是写进 HTML 的 inline SVG，坐标画在 **1120 宽的虚拟画布**上，再由 `.chart svg{width:100%}` 缩到容器宽度。
  所以图内字号写在 `FT_T` / `FT_S` 两个常量上（`build_gs50_dashboard.py`、`build_archive.py` 各一份），
  **只有两档**：标题 15.6、其余全部 13.6 —— 落盘后实际渲染约 12.5–13.3px。不要再加第三档，也不要写回 9.5/10。
- 画布放不下时（日报页 vw < 1350、归档页 vw < 1240）**横向滑动**，而不是把字压低：
  `.chart svg{min-width:1030px}` + `.chart{overflow-x:auto}`。要改字号先想清楚缩放倍数。
- 温度计（第 1 节）是例外：它是 CSS 色带（`.gauge/.gbar`），不跟着画布缩放，永远 16px 高、字是真字号。

### 基准怎么选（重要）

- 中位数 / 涨停 / 新高 / 成交额占比都是 **41 家 A 股**口径，分母也是 A 股全市场（通达信盘后包），**不含港股**。
  这不是遗漏：港股没有涨停、没有流通盘数据，混进同一个中位会把两套交易制度搅在一起。
- **沪深300 只是「A 股宽基 beta」参考，不是这份名单的合格基准**：名单 41 家里 11 家科创（688）、
  7 家创业板（300/301）、12 家 002，合计 44% 是成长股，没有银行 / 地产 / 白酒；拿沪深300 算超额，
  长期会混进一大块「成长 vs 价值」的风格差。20261029 就是典型：
  中位 −4.53%，沪深300 −1.03%（超额 −3.50pt）看起来跑输很多，创业板指 −4.45%（超额 −0.08pt）几乎持平。
  所以表格和图上**并列创业板指**作为同风格对照。
- 港股 13 家用**恒生科技**（腾讯、阿里、小米、联想、地平线都是成分），同市场、同风格。
- 为什么不用 MSCI China：腾讯行情不提供该指数（只有 MCHI 等美股 ETF，带汇率和溢价）；
  而且 MSCI China 也是全市场市值加权（含金融地产），换它并不解决结构错配。

## 依赖

```bash
pip install pandas tushare yfinance
```

`curl` 在 PATH 里（腾讯 / 东财接口用它，`requests` 的 TLS 指纹会被拒）。
`.env` 里放 `tushare_key=xxx`（查找顺序：本目录 → 上级目录 → 环境变量 `TUSHARE_KEY`）。

## 来源

从 `E:\Python_Doc\Investment\20261026 AI` 抽出来的独立服务：只搬了这个看板真正需要的数据与脚本，
原来那套「2,446 家 AI 全池 / 30 细分 / 境外收入切分」的分析代码留在原目录，与本服务无关。
