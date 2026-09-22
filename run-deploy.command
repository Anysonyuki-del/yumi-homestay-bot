#!/bin/bash
# 双击运行：把当前 HEAD 打包送到生产服务器，再执行 .stage/remote.sh。
#
# 版本、目标提交和校验和全部由仓库现状推导，不再手写。此前这三个值写死在脚本里，
# 发版后若忘记同步就会拿旧包去部署，把生产静默回退到上一个版本 —— 而旧包的 SHA
# 与 TARGET 彼此自洽，服务器端校验不会拦下来。
#
# 加 --check 只跑起飞前检查，不连服务器、不改任何东西。
set -euo pipefail

REPO="/Volumes/02/obsidian codex/homestay-bot"
HOST="root@117.72.14.15"
KEY="$HOME/.ssh/yumi_codex"
cd "$REPO"

DRY=0
[ "${1:-}" = "--check" ] && DRY=1

fail() { echo "✗ $*" >&2; exit 1; }

# ── 起飞前检查：任何一项不过就不部署 ───────────────────────────────
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)
[ -n "$VERSION" ] || fail "无法从 pyproject.toml 读出版本号"
TAG="v$VERSION"
HEAD_SHA=$(git rev-parse HEAD)

# 工作区必须干净，否则部署内容与仓库对不上，事后无从复现
[ -z "$(git status --porcelain --untracked-files=no)" ] \
  || { git status --short; fail "工作区有未提交改动"; }

# HEAD 必须正好带着本版本的标签，杜绝「版本号没跟着代码走」
TAG_SHA=$(git rev-parse "$TAG^{commit}" 2>/dev/null || echo "")
[ "$TAG_SHA" = "$HEAD_SHA" ] \
  || fail "HEAD 不是 $TAG（pyproject=$VERSION，HEAD=$(git rev-parse --short HEAD)）。先打标签或切到正确提交。"

# 发布包携带的是 refs/heads/main，而 TARGET 取自 HEAD；分离头指针下两者可能不是
# 同一个提交，会导致服务器快进到 main 的位置后与 TARGET 不符而中止。
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || fail "当前不在 main 分支，请先 git checkout main"
[ "$(git rev-parse main)" = "$HEAD_SHA" ] || fail "HEAD 与 main 不一致"

echo "✓ 版本 $TAG"
echo "✓ 目标提交 $HEAD_SHA"
echo "✓ 工作区干净"

# 未推送只提醒，不阻断：部署走 bundle，不依赖 GitHub
git fetch -q origin 2>/dev/null || true
if [ -n "$(git log origin/main..HEAD --oneline 2>/dev/null)" ]; then
  echo "! 本地领先 origin/main，本次部署的提交尚未推送到 GitHub"
fi

# ── 每次现打发布包，不复用 .stage 里的旧文件 ──────────────────────
BUNDLE="$REPO/.stage/$TAG.bundle"
mkdir -p "$REPO/.stage"
rm -f "$BUNDLE"
git bundle create "$BUNDLE" main "$TAG" >/dev/null 2>&1
git bundle verify "$BUNDLE" >/dev/null || fail "发布包自检未通过"
SHA=$(shasum -a 256 "$BUNDLE" | awk '{print $1}')
echo "✓ 发布包 $(du -h "$BUNDLE" | cut -f1)  sha256=${SHA:0:16}…"

if [ "$DRY" = 1 ]; then
  echo
  echo "--check 模式：检查全部通过，未连接服务器。"
  exit 0
fi

# ── 连接方式：有密钥走免密，否则回落到密码提示 ────────────────────
if [ -f "$KEY" ]; then
  SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
  echo "✓ 使用密钥 ${KEY}（免密）"
else
  SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
  echo "! 未找到密钥，ssh/scp 会各要一次密码"
fi

LOG="$REPO/.stage/deploy.log"
: > "$LOG"

echo
echo "1/2 上传 $TAG.bundle 到 $HOST ……"
scp "${SSH_OPTS[@]}" "$BUNDLE" "$HOST:/tmp/$TAG.bundle" 2>&1 | tee -a "$LOG"

echo "2/2 执行部署 ……"
ssh "${SSH_OPTS[@]}" \
  -o SendEnv=none \
  "$HOST" \
  "DEPLOY_TAG='$TAG' DEPLOY_TARGET='$HEAD_SHA' DEPLOY_SHA='$SHA' bash -s" \
  < "$REPO/.stage/remote.sh" 2>&1 | tee -a "$LOG"

echo "SSH_EXIT=${PIPESTATUS[0]}" >> "$LOG"
echo
if grep -q "DEPLOY_OK" "$LOG"; then
  echo "✓ 部署完成（$TAG）。完整输出见 .stage/deploy.log"
else
  echo "✗ 未见 DEPLOY_OK，请检查 .stage/deploy.log"
  exit 1
fi
