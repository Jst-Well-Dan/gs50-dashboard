#!/usr/bin/env bash
# GS50 每日复盘 · 服务器端定时跑（替代 GitHub Actions 的 cron）
#
# 与 .github/workflows/daily.yml 同一套逻辑：
#   1) gate：往前看 5 个日历日，挑「盘后包已发布(HTTP 200)、docs/data/<d>.json 还没出」的最晚一天
#   2) 有活才跑流水线（约 20 分钟）；没活 ~15 秒退出
#   3) docs/ 有变化就 commit + push → GitHub Pages 自动发布
#
# 用法：
#   /opt/gs50-dashboard/deploy/run_daily.sh            # 走 gate（timer 用这个）
#   /opt/gs50-dashboard/deploy/run_daily.sh 20261109   # 强制某一天
set -uo pipefail

APP=/opt/gs50-dashboard
cd "$APP" || exit 1
export TZ=Asia/Shanghai
export PATH="$APP/.venv/bin:/usr/local/bin:/usr/bin:/bin"
export HOME=/home/deploy
export GIT_SSH_COMMAND="ssh -i /home/deploy/.ssh/gs50_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30"

say() { echo "[$(date '+%F %T')] $*"; }

# 一轮约 20 分钟，timer 每 20 分钟一次 → 同一时刻只允许一个实例
exec 9>/tmp/gs50-daily.lock
flock -n 9 || { say "上一轮还在跑 → 本次跳过"; exit 0; }

# ---------- 0. 追平远端（本地 deploy.bat / 手工 dispatch 也会往 main 推） ----------
if git pull --rebase --autostash -q origin main; then
  say "已追平 origin/main（$(git log --oneline -1)）"
else
  say "!! pull --rebase 失败，继续用当前工作树"
  git rebase --abort 2>/dev/null
fi

# ---------- 1. gate ----------
day="${1:-}"
if [ -n "$day" ]; then
  say "手工指定数据日 $day"
else
  for k in 0 1 2 3 4; do
    d=$(date -d "-$k day" +%Y%m%d)
    if [ -f "docs/data/$d.json" ]; then
      say "$d：页面已存在"
      continue
    fi
    # 通达信不存在的数据日返回 301 而不是 404，只有 200 才算「盘后包已发布」
    code=$(curl -s -o /dev/null -m 25 -w '%{http_code}' \
      "http://www.tdx.com.cn/products/data/data/g4day/$d.zip" || echo ERR)
    say "$d.zip → $code"
    if [ "$code" = "200" ]; then day=$d; break; fi
  done
  if [ -z "$day" ]; then
    say "最近 5 天都没有待出的页面（非交易日 / 盘后包未发）→ 跳过"
    exit 0
  fi
  say "→ 要出 $day 的页面"
fi

# ---------- 2. 跑流水线 ----------
# tushare 的 key 由 scripts/tushare_client.py 直接读 $APP/.env，不用在这里 export
.venv/bin/python scripts/run_daily.py "$day" --require-today
rc=$?
say "run_daily exit=$rc"
if [ "$rc" -ne 0 ] || [ ! -f "docs/data/$day.json" ]; then
  say "流水线失败或缺页 → 不提交，等下一次心跳重试"
  exit 1
fi

# ---------- 3. commit + push ----------
if [ -z "$(git status --porcelain docs)" ]; then
  say "docs/ 无变化 → 不提交"
  exit 0
fi
git add -A docs
git -c user.name="gs50-server" -c user.email="gs50@zwtj.site" \
    commit -q -m "dashboard: 云端更新 $(date '+%Y-%m-%d %H:%M') CST"

if git push -q origin main; then
  say "已 push → Pages 自动发布"
  exit 0
fi
say "push 被拒 → pull --rebase 后重试一次"
git pull --rebase --autostash -q origin main || { say "!! rebase 失败"; exit 1; }
if git push -q origin main; then say "已 push（重试成功）"; exit 0; fi
say "!! push 两次都失败（deploy key / 网络）"
exit 1
