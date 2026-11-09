# 服务器端运维命令（gs50-dashboard）

调度已经从 GitHub Actions 搬到自有服务器。**日常你什么都不用敲** —— 这个文件是「需要动手时照抄」的命令清单。

```
仓库      github.com/Jst-Well-Dan/gs50-dashboard   （Pages 源 = main /docs，push 即上线）
服务器    Ubuntu 24.04 / 2 vCPU / 3.9 GB，用户 deploy（不是 root）
代码      /opt/gs50-dashboard      （= 本仓库的 clone）
环境      /opt/gs50-dashboard/.venv（python 3.12.3 + pandas 3.0.5 + tushare 1.4.29）
密钥      /opt/gs50-dashboard/.env（600，Tushare_key 47位镜像 / Tushare_token 56位官方）
单元      gs50-daily.timer  Mon-Fri 17:00–21:40 每 20 分钟
          gs50-weekly.timer Sat 18:00（A 股共识周刷）
日志      journalctl -u gs50-daily / -u gs50-weekly
```

---

## 0. 本机先设两个变量（后面所有命令都用它们）

Windows 的 Git Bash / PowerShell 里：

```bash
KEY=C:/Users/A/Documents/KEYS/jd_qing_liang_yun.pem
SRV=deploy@117.72.111.211
ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=40 "$SRV" 'whoami && hostname'
```

预期输出 `deploy` + 主机名。这台机器偶尔会在 banner 阶段抽风（`Connection timed out during banner exchange`），**等几秒重试即可**，不要去动密钥。

<details>
<summary>如果报 <code>UNPROTECTED PRIVATE KEY FILE</code></summary>

```bash
icacls "$KEY" /inheritance:r /remove:g Everyone "BUILTIN\Users" "BUILTIN\Administrators" "NT AUTHORITY\SYSTEM" /grant:r "$USERNAME:R"
```
</details>

---

## 1. ✅ 已配置：56 位官方 TUSHARE_TOKEN（2026-11-09 完成）

服务器 `.env` 现在两个键都在（`Tushare_key` 47 位镜像 + `Tushare_token` 56 位官方），600 权限。
已验证 `trade_cal` 和 `report_rc`（周刷真正用的那个接口）都能返回数据。

**以后换 token 只需要重跑这一对命令** —— 本机改 `.env`，然后整份覆盖上去（别在服务器上手改，会被下次覆盖）：

```bash
# 本机：确认两个键的长度（47 / 56），不打印值
cd "E:/Python_Doc/Investment/20261029 gs50_dashboard"
python -c "[print(k,len(v.strip())) for k,v in (l.split('=',1) for l in open('.env',encoding='utf-8').read().replace(chr(13),'').split(chr(10)) if '=' in l)]"

# 上传并收紧权限（scp 会把本地的 644 带上去，必须再 chmod）
scp -i "$KEY" .env "$SRV":/opt/gs50-dashboard/.env
ssh -i "$KEY" "$SRV" 'chmod 600 /opt/gs50-dashboard/.env'
```

不用担手上传会把密钥带进仓库：`.env` 在 `.gitignore` 第 2 行（`git check-ignore -v .env` 可确认）。
本机 `.env` 是 CRLF 换行也没关系，`scripts/tushare_client.py` 读的时候会 `replace("\\r", "")`。

<details>
<summary>等价的手工写入方式（不经过本机文件）</summary>

```bash
ssh -i "$KEY" "$SRV"
read -rs -p '粘贴 56 位 token（不回显）: ' TS; echo
printf 'Tushare_token=%s\n' "$TS" >> /opt/gs50-dashboard/.env
chmod 600 /opt/gs50-dashboard/.env; unset TS
```
</details>

---

## 2. 验证官方端点真的能用

```bash
ssh -i "$KEY" "$SRV" '/opt/gs50-dashboard/.venv/bin/python -c "
import sys; sys.path.insert(0, \"/opt/gs50-dashboard/scripts\")
import tushare_client as tc
print(tc.pro_official().trade_cal(exchange=\"SSE\", start_date=\"20261101\", end_date=\"20261110\").head(3))
"'
```

- 打出 `cal_date / is_open / pretrade_date` 三行表 → **通了**（已验过：20261110/20261109 是交易日，20261107/08 休市）
- `RuntimeError 找不到官方 token` → `.env` 里没有 `Tushare_token=` 这一行（脚本认 `tushare_token` 开头，大小写不敏感）
- `抱歉，您没有访问该接口的权限` → 位数不对，或错把 47 位镜像 key 填进了 `Tushare_token`

周刷真正依赖的是 `report_rc`（权限门槛比 `trade_cal` 高），单独验它：

```bash
ssh -i "$KEY" "$SRV" '/opt/gs50-dashboard/.venv/bin/python -c "
import sys; sys.path.insert(0, \"/opt/gs50-dashboard/scripts\")
import tushare_client as tc
df = tc.pro_official().report_rc(ts_code=\"688256.SH\", start_date=\"20260401\", end_date=\"20261109\",
                                 fields=\"ts_code,report_date,quarter,op_rt,op_pr,eps,np,pe\")
print(len(df)); print(df.head(2))
"'
# 已验：rows=116
```

---

## 3. 日常：确认它自己在跑（不需要动手）

```bash
# 两个 timer 的下次触发时间
ssh -i "$KEY" "$SRV" "systemctl list-timers 'gs50-*' --no-pager"

# 今晚这一轮干了什么（gate 判定 + 结果）
ssh -i "$KEY" "$SRV" 'journalctl -u gs50-daily --since today --no-pager -o cat | tail -30'

# 只看关键行
ssh -i "$KEY" "$SRV" "journalctl -u gs50-daily --since today --no-pager -o cat | grep -E '要出|已 push|跳过|!!'"

# 周刷
ssh -i "$KEY" "$SRV" 'journalctl -u gs50-weekly --since -7d --no-pager -o cat | tail -20'
```

健康的一天长这样（15 次心跳里 14 次 1 秒退出，1 次真跑 20 分钟）：

```
[2026-11-10 17:00:01] 20261110：页面已存在
[2026-11-10 17:00:01] 20261109：页面已存在
[2026-11-10 17:00:02] 最近 5 天都没有待出的页面（非交易日 / 盘后包未发）→ 跳过
...
[2026-11-10 17:20:03] 20261110.zip → 200
[2026-11-10 17:20:03] → 要出 20261110 的页面
[2026-11-10 17:41:55] run_daily exit=0
[2026-11-10 17:42:01] 已 push → Pages 自动发布
```

---

## 4. 手动操作

```bash
# 立刻跑一轮（走 gate：有缺页才动真格）——  测试/救急都用它
ssh -i "$KEY" "$SRV" 'sudo systemctl start gs50-daily && sleep 3 && systemctl is-active gs50-daily'

# 指定某一天重出并发布（补漏页、验证模板）
ssh -i "$KEY" "$SRV" '/opt/gs50-dashboard/deploy/run_daily.sh 20261030'

# 改了页面模板，要把所有历史页面按新模板重刷（约 7 分钟）→ 然后发布
ssh -i "$KEY" "$SRV" 'cd /opt/gs50-dashboard && .venv/bin/python scripts/run_daily.py --rebuild --all'
ssh -i "$KEY" "$SRV" 'cd /opt/gs50-dashboard && export GIT_SSH_COMMAND="ssh -i /home/deploy/.ssh/gs50_deploy -o IdentitiesOnly=yes" && git add -A docs && git -c user.name=gs50-server -c user.email=gs50@zwtj.site commit -m "dashboard: 全量重出 $(date +%F)" && git push'

# 周刷现在手动跑一次（要 41 分钟，别在 17:00-21:40 之外的时间外跑也行，只是慢）
ssh -i "$KEY" "$SRV" 'sudo systemctl start gs50-weekly'
```

**本机改了代码/模板之后**：`git push` 就行。服务器每次跑之前都会 `git pull --rebase --autostash`，下一轮心跳自动用上新代码（`docs/` 里的历史页面不受影响）。

---

## 5. 应急：服务器不可用时，用 GitHub 手动跑

GitHub 侧的定时任务已经全撤（只留手动），逻辑和服务器脚本一致。secrets（`TUSHARE_KEY` / `TUSHARE_TOKEN`）仍在仓库里。

```bash
gh workflow run daily.yml -f day=20261109      # 指定数据日
gh workflow run daily.yml                       # 不指定 → 走 gate
gh run list --workflow=daily.yml --limit 3
gh run watch                                    # 跟踪最近一次
```

周刷同理：`gh workflow run weekly-consensus.yml`（必须在 secrets 里有 `TUSHARE_TOKEN`）。

---

## 6. 出问题先看这三处

```bash
# ① gate 说跳过，但页面确实缺 → 看盘后包到底发没发（200=已发布，301=没有；不存在也是 301）
curl -s -o /dev/null -m 25 -w '%{http_code}\n' http://www.tdx.com.cn/products/data/data/g4day/20261110.zip

# ② push 失败 → deploy key 是否还在/还有写权限（仓库 Settings → Deploy keys，标题 gs50-server-daily）
ssh -i "$KEY" "$SRV" 'ssh -i ~/.ssh/gs50_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -T git@github.com'
# 期望：Hi Jst-Well-Dan/gs50-dashboard! You've successfully authenticated, but GitHub does not provide shell access.

# ③ 流水线失败 → 看带 !! 的行和最后的 exit 码
ssh -i "$KEY" "$SRV" 'journalctl -u gs50-daily -n 120 --no-pager -o cat | tail -40'
```

其它常见现象：

| 现象 | 原因 / 处理 |
|---|---|
| `上一轮还在跑 → 本次跳过` | 正常。`flock` 防重入，一轮 20 分钟，心跳 20 分钟一次 |
| `最近 5 天都没有待出的页面` | 非交易日，或盘后包还没发。等到 21:40 还没出，第二天的心跳会补（gate 往前看 5 天） |
| `!! {day} 的 A 股共识没落盘` | 镜像端点限流。页面照样出，只是 PE_NTM 部分缺失；下轮重试或手动 `systemctl start gs50-weekly` |
| 磁盘 | `df -h /`，目前 41% 用量；`data/` 会随历史回补慢慢涨（现在 20 MB） |

---

## 7. 踩过的坑（别重复踩）

| 坑 | 结论 |
|---|---|
| `pip install` 卡在 `Downloading pandas...` 二十分钟不动 | 这台机器访问 pypi.org **能拿到响应头但 body 传输卡死**。必须走国内镜像：`-i https://mirrors.aliyun.com/pypi/simple` |
| 服务器上 `git clone` 报 `could not open .../tmp_pack_xxx for reading` | 跨境拉 GitHub 会断。用 `--depth 1` 再 `fetch --unshallow`，或干脆多试几次 |
| Windows 上 `chmod +x` git 不认（`core.filemode=false`） | 所以 unit 里写 `ExecStart=/bin/bash /opt/.../run_daily.sh`，**不依赖可执行位** |
| `sudo -u deploy git pull` 报 `dubious ownership` | 不要用 root 代跑 deploy 的 git。要么以 deploy 登录，要么 root 执行 `systemctl` 相关的部分 |
| 服务器 repo `git status` 一直显示 `deploy/*.sh` 被改 | 已设 `git config core.fileMode false`（本地 chmod 不再算改动） |
| `pkill -f "pip install ..."` 把自己的 ssh 会话杀掉 | 用 `pkill -f "[p]ip install ..."`（正则自匹配问题） |

---

## 8. 相关文件位置

| 路径 | 内容 |
|---|---|
| `/opt/gs50-dashboard/deploy/run_daily.sh` | 每日调度脚本（gate → `run_daily.py` → commit/push） |
| `/opt/gs50-dashboard/deploy/run_weekly.sh` | 共识周刷脚本 |
| `/opt/gs50-dashboard/deploy/systemd/*` | 单元文件原件（改这里再 `install` 到 `/etc`） |
| `/etc/systemd/system/gs50-daily.{service,timer}` | 已安装的单元（改完要 `daemon-reload`） |
| `/opt/gs50-dashboard/.env` | 600，两个 tushare 凭据 |
| `/home/deploy/.ssh/gs50_deploy` | 推代码用的 deploy key（私钥，勿动） |
| `/tmp/gs50-daily.lock`、`/tmp/gs50-weekly.lock` | 防重入锁 |

改完单元文件的正确顺序：

```bash
# 本机改 deploy/systemd/*.service → push
ssh -i "$KEY" "$SRV" 'cd /opt/gs50-dashboard && git pull --rebase --autostash -q'
ssh -i "$KEY" root@117.72.111.211 'cd /opt/gs50-dashboard && install -m 644 deploy/systemd/* /etc/systemd/system/ && systemctl daemon-reload && systemctl list-timers "gs50-*" --no-pager'
```
