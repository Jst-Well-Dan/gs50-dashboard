#!/usr/bin/env bash
# A 股卖方共识周刷（原 .github/workflows/weekly-consensus.yml）
#
# 为什么单独一条：镜像端点（tl.kaixin8.top）盘后高峰会 upstream_pool_exhausted，而官方端点对
# report_rc 硬限 1 次/分钟 → 41 家 ≈ 41 分钟。日常跑吃这份长任务不划算，所以挪到周六
# （北京 18:00，非交易日、非高峰），一次把 .cache/consensus/ 刷干净并重出最近一期页面。
#
# 需要 $APP/.env 里的 Tushare_token（56 位官方 token，不是 47 位 tsr_ 镜像 key）。
set -uo pipefail

APP=/opt/gs50-dashboard
cd "$APP" || exit 1
export TZ=Asia/Shanghai
export PATH="$APP/.venv/bin:/usr/local/bin:/usr/bin:/bin"
export HOME=/home/deploy
export GIT_SSH_COMMAND="ssh -i /home/deploy/.ssh/gs50_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30"

say() { echo "[$(date '+%F %T')] $*"; }

exec 9>/tmp/gs50-weekly.lock
flock -n 9 || { say "周刷还在跑 → 本次跳过"; exit 0; }

git pull --rebase --autostash -q origin main || { say "!! pull --rebase 失败，继续"; git rebase --abort 2>/dev/null; }

DAY=$(ls data/mkt 2>/dev/null | sed 's/\.csv$//' | sort | tail -1)
[ -z "$DAY" ] && { say "data/mkt 里没有盘后包 → 先跑一次 run_daily.sh"; exit 1; }
say "周刷数据日 $DAY（官方端点，41 家 ≈ 41 分钟）"

.venv/bin/python -u scripts/fetch_consensus.py "$DAY" --force --official
rc=$?
if [ "$rc" -ne 0 ]; then say "!! fetch_consensus exit=$rc → 不重出页面"; exit "$rc"; fi

.venv/bin/python -u scripts/build_gs50_dashboard.py "$DAY" \
  --out "docs/$DAY.html" --summary "docs/data/$DAY.json" || exit 1
.venv/bin/python -u scripts/build_archive.py || exit 1

if [ -z "$(git status --porcelain docs)" ]; then
  say "共识没有变化 → 不提交"
  exit 0
fi
git add -A docs
git -c user.name="gs50-server" -c user.email="gs50@zwtj.site" \
    commit -q -m "consensus: 周刷 $(date '+%Y-%m-%d %H:%M') CST"
if git push -q origin main; then say "已 push → Pages 自动发布"; exit 0; fi
git pull --rebase --autostash -q origin main || { say "!! rebase 失败"; exit 1; }
git push -q origin main && say "已 push（重试成功）"
