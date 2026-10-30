@echo off
rem 把 docs/ 推到 GitHub（GitHub Pages 源 = main 分支 /docs 目录）
cd /d "%~dp0"

git rev-parse --git-dir >nul 2>&1
if errorlevel 1 (
  echo [!] 这里还不是 git 仓库。先做一次初始化：
  echo     git init -b main
  echo     git remote add origin https://github.com/^<你的账号^>/^<仓库名^>.git
  echo     deploy.bat
  pause
  exit /b 1
)

git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "dashboard: refresh docs %DATE% %TIME%"
) else (
  echo [=] 没有改动，跳过 commit
)

git push
if errorlevel 1 (
  echo [!] push 失败：检查 remote / 网络 / 是否已登录（gh auth login 或 git config）
) else (
  echo [√] 已推送。线上地址：Settings -^> Pages 里看到的那个 URL（首次需选 Deploy from a branch / main / /docs）
)
pause
