#!/usr/bin/env bash
# 部署前真实模型回归门禁：用标签对应的候选源码，在生产 API 容器的临时目录里跑共用虚构资料，
# 按「只进不退」基线判定。本次没有改回复链路时直接跳过。
#
# 用法：DEPLOY_HOST=… DEPLOY_KEY=… scripts/release/reply_gate.sh <标签>
#   DEPLOY_HOST、DEPLOY_KEY 由本机部署脚本传入，本脚本不写死、不入库任何地址或密钥。
#   可选：REPLY_GATE_PREVIOUS_TAG（默认取标签之前最近的 v* 标签）、
#         REPLY_GATE_CONTAINER（默认 yumi-homestay-bot-api-1）。
# 退出码：0 通过或无需运行；1 不通过；其他 无法运行，按不通过处理。
# 产出：.stage/reply-gate-<标签>.json（完整结果）、.md（发版记录摘要）、
#       .baseline.json（纳入新通过场景后的基线，发布后随现场记录提交）。
#
# 只读解密运行配置、不写库、不发消息、不访问百居易；结束时无论成败都清理临时目录。
# 见 docs/specs/2026-09-25_reply-regression-gate-spec.md F4。
set -euo pipefail

TAG="${1:?用法：reply_gate.sh <标签>}"
: "${DEPLOY_HOST:?缺少 DEPLOY_HOST}"
: "${DEPLOY_KEY:?缺少 DEPLOY_KEY}"
CONTAINER="${REPLY_GATE_CONTAINER:-yumi-homestay-bot-api-1}"
REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"
STAGE="$REPO/.stage"
mkdir -p "$STAGE"

# 回复链路：模型调用、回复策略、会话编排与共用资料；依赖变化也算。
REPLY_PATHS=(
  src/homestay_bot/integrations
  src/homestay_bot/services
  src/homestay_bot/application.py
  src/homestay_bot/tools/reply_regression.py
  tests/fixtures/guest_reply_scenarios.json
  tests/fixtures/guest_reply_regression_baseline.json
  requirements.lock
)

PREV="${REPLY_GATE_PREVIOUS_TAG:-$(git describe --tags --abbrev=0 --match 'v*' "$TAG^" 2>/dev/null || true)}"
if [ -n "$PREV" ]; then
  changed="$(git diff --name-only "$PREV" "$TAG" -- "${REPLY_PATHS[@]}")"
  # pyproject.toml 每次发版都改版本号，只把依赖相关的改动算进来。
  if git diff -U0 "$PREV" "$TAG" -- pyproject.toml | grep -E '^[+-][^+-]' \
      | grep -vqE '^[+-]version = '; then
    changed="${changed}"$'\n'"pyproject.toml"
  fi
  if [ -z "$(printf '%s' "$changed" | tr -d '[:space:]')" ]; then
    echo "REPLY_GATE_SKIPPED：$PREV..$TAG 未改回复链路"
    exit 0
  fi
  echo "回复链路有改动（$PREV..$TAG）："
  printf '%s\n' "$changed" | sed '/^$/d; s/^/  /'
else
  echo "找不到上一个版本标签，按改了回复链路处理"
fi

SSH_OPTS=(-i "$DEPLOY_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new
  -o SendEnv=none -o ServerAliveInterval=30)
REMOTE_DIR="/tmp/yumi-reply-gate-${TAG}-$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"

cleanup() {
  # 失败、中断同样清理：容器内由 root 删除（文件属主是容器用户），再删宿主机目录。
  ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" \
    "docker exec -u 0 '$CONTAINER' rm -rf '$REMOTE_DIR' >/dev/null 2>&1; rm -rf '$REMOTE_DIR'" \
    >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# 候选源码与资料一律取自标签，不取工作区，保证测的就是要部署的内容。
git archive "$TAG" src/homestay_bot | tar -x -C "$WORK"
COPYFILE_DISABLE=1 tar -czf "$WORK/cand.tgz" -C "$WORK/src" homestay_bot
git show "$TAG:tests/fixtures/guest_reply_scenarios.json" > "$WORK/fixture.json"
git show "$TAG:tests/fixtures/guest_reply_regression_baseline.json" > "$WORK/baseline.json"

ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "mkdir -m 700 '$REMOTE_DIR'"
for file in cand.tgz fixture.json baseline.json; do
  scp -q "${SSH_OPTS[@]}" "$WORK/$file" "$DEPLOY_HOST:$REMOTE_DIR/$file"
done

echo "开始真实模型回归（约 15 至 25 分钟）……"
set +e
ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "set -e; D='$REMOTE_DIR'; C='$CONTAINER'
  mkdir \"\$D/cand\" && tar -xzf \"\$D/cand.tgz\" -C \"\$D/cand\" 2>/dev/null && rm \"\$D/cand.tgz\"
  docker exec \"\$C\" mkdir -p \"\$D\"
  docker cp \"\$D/.\" \"\$C:\$D/\"
  docker exec -u 0 \"\$C\" chown -R 10001 \"\$D\"
  docker exec -w \"\$D\" -e PYTHONPATH=\"\$D/cand\" \"\$C\" \
    python -m homestay_bot.tools.reply_regression gate \
    --fixture fixture.json --baseline baseline.json --out result.json"
status=$?
set -e

RESULT="$STAGE/reply-gate-$TAG.json"
if ! ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "docker exec '$CONTAINER' cat '$REMOTE_DIR/result.json'" \
    > "$RESULT" 2>/dev/null; then
  echo "✗ 取不到回归结果（运行退出码 $status），按不通过处理"
  exit 2
fi

# 摘要与新基线在本机生成；只用标准库，兼容系统自带的 Python 3.9。
python3 - "$RESULT" "$TAG" "$STAGE" <<'PY'
import json, sys
path, tag, stage = sys.argv[1:4]
data = json.load(open(path, encoding="utf-8"))
verdict = data["verdict"]
counts = verdict["counts"]
lines = [
    f"- 门禁结论：{'通过' if verdict['passed'] else '不通过'}（{tag}）",
    f"- 代码来源：{data['code']['package_path']}；资料哈希 {data['fixture_sha256'][:16]}",
    f"- 场景 {counts['scenarios']} 个，首轮通过 {counts['first_run_passes']} 个；"
    f"不退步集合 {counts['must_pass']} 个，已知未通过 {counts['known_failures']} 个",
    f"- 退步：{'、'.join(verdict['regressions']) or '无'}",
    f"- 新纳入不退步集合：{'、'.join(verdict['newly_stable']) or '无'}",
    f"- 仍未通过的安全类场景：{'、'.join(verdict['safety_known_failures']) or '无'}",
]
open(f"{stage}/reply-gate-{tag}.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
json.dump(verdict["proposed_baseline"], open(f"{stage}/reply-gate-{tag}.baseline.json", "w",
          encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n".join(lines))
PY

if [ "$status" -eq 0 ]; then
  echo "REPLY_GATE_PASSED"
  exit 0
fi
echo "REPLY_GATE_FAILED"
exit 1
