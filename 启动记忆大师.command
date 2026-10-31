#!/bin/zsh

set -euo pipefail

PROJECT_DIR="/Users/dxm/Desktop/度小满/记忆大师"
RUST_BIN="$HOME/.cargo/bin"
FALLBACK_PNPM="/Users/dxm/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm"
FALLBACK_PNPM_DIR="/Users/dxm/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback"

export PATH="$RUST_BIN:$FALLBACK_PNPM_DIR:$PATH"

cd "$PROJECT_DIR"

if [ -f ".env.local" ]; then
  set -a
  source ".env.local"
  set +a
fi

if command -v pnpm >/dev/null 2>&1; then
  PNPM_CMD=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
  PNPM_CMD=(corepack pnpm)
elif [ -x "$FALLBACK_PNPM" ]; then
  PNPM_CMD=("$FALLBACK_PNPM")
else
  echo "没找到 pnpm，无法启动记忆大师。"
  echo "请先安装 pnpm，或让我帮你补一个更稳定的启动方式。"
  read -r "?按回车关闭..."
  exit 1
fi

"${PNPM_CMD[@]}" desktop:dev
