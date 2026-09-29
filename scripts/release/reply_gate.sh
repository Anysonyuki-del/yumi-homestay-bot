#!/usr/bin/env bash
# 部署前真实模型回归门禁：用标签对应的候选源码，在生产 API 容器的临时目录里跑共用虚构资料，
# 按「只进不退」基线判定。本次没有改回复链路时直接跳过。
#
# 用法：DEPLOY_HOST=… DEPLOY_KEY=… scripts/release/reply_gate.sh <标签>
#   DEPLOY_HOST、DEPLOY_KEY 由本机部署脚本传入，本脚本不写死、不入库任何地址或密钥。
#   可选：REPLY_GATE_PREVIOUS_TAG（默认取标签之前最近的 v* 标签）、
#         REPLY_GATE_CONTAINER（默认 yumi-homestay-bot-api-1）。
#   范围（2026-09-30 用户要求：跑任何测试前先判断有没有必要）：
#         REPLY_GATE_SCOPE=all（默认，全量）
#                         | tourism（联网类场景 + 上次门禁里实际走过联网查询的场景）
#                         | skip（不跑）
#                         | 逗号分隔的场景编号或类别；
#         非全量必须同时给 REPLY_GATE_SCOPE_REASON，写进摘要，发布记录照抄。
#         问题分类、知识检索、主模型提示或公共流程有改动时一律全量。
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

# 回复链路：门禁运行器实际加载、能改变回复正文的模块，加上共用资料与依赖。
# 2026-09-29 按用户要求收窄：原来整个 services/ 与 application.py 都算，改一个后台
# 链接也要跑 20 分钟。清单由 reply_regression 的导入闭包减去基础设施得出，
# tests/unit/test_release_scripts.py 会核对闭包里新增的模块有没有漏列。
REPLY_PATHS=(
  src/homestay_bot/integrations/deepseek_client.py
  src/homestay_bot/integrations/deepseek_delivery_rewriter.py
  src/homestay_bot/integrations/deepseek_tourism.py
  src/homestay_bot/integrations/tourism.py
  src/homestay_bot/integrations/hostex_client.py
  src/homestay_bot/services/answer_policy.py
  src/homestay_bot/services/complaint_service.py
  src/homestay_bot/services/context_retention.py
  src/homestay_bot/services/conversation_service.py
  src/homestay_bot/services/emergency_service.py
  src/homestay_bot/services/fact_policy.py
  src/homestay_bot/services/faq_candidate_context.py
  src/homestay_bot/services/guest_reply_policy.py
  src/homestay_bot/services/guest_verification.py
  src/homestay_bot/services/knowledge_evidence_policy.py
  src/homestay_bot/services/knowledge_service.py
  src/homestay_bot/services/message_service.py
  src/homestay_bot/services/model_budget.py
  src/homestay_bot/services/reply_plan.py
  src/homestay_bot/services/stay_date_range.py
  src/homestay_bot/domain/stay_status.py
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

SCOPE="${REPLY_GATE_SCOPE:-all}"
ONLY=""
if [ "$SCOPE" != "all" ]; then
  : "${REPLY_GATE_SCOPE_REASON:?按范围运行门禁必须写明 REPLY_GATE_SCOPE_REASON}"
  echo "门禁范围：$SCOPE（理由：$REPLY_GATE_SCOPE_REASON）"
fi
case "$SCOPE" in
  all) ;;
  skip)
    echo "REPLY_GATE_SKIPPED_BY_JUDGMENT：$REPLY_GATE_SCOPE_REASON"
    printf -- '- 门禁：按判断跳过（%s）；理由：%s\n' "$TAG" "$REPLY_GATE_SCOPE_REASON" \
      > "$STAGE/reply-gate-$TAG.md"
    exit 0
    ;;
  tourism)
    # 联网问题不只在两个联网类别里：问距离、问位置的知识题也会走联网查询，
    # 所以再并上一次门禁结果里真实调用过联网查询的场景。
    PREV_RESULT="$(ls -t "$STAGE"/reply-gate-v*.json 2>/dev/null \
      | grep -v -e '\.baseline\.json$' -e "reply-gate-$TAG\.json$" | head -1 || true)"
    ONLY="$(git show "$TAG:tests/fixtures/guest_reply_scenarios.json" \
      | python3 -c '
import json, sys
fixture = json.load(sys.stdin)
ids = {s["id"] for s in fixture["scenarios"] if s.get("category") in ("live_search", "stable_tourism")}
if len(sys.argv) > 1 and sys.argv[1]:
    prev = json.load(open(sys.argv[1], encoding="utf-8"))
    for sid, recs in prev.get("records", {}).items():
        # 联网查询记在 traces（如 ["tourism_search"]），tools 只记业务工具。
        if any("tourism_search" in (r.get("traces") or []) for r in recs):
            ids.add(sid)
known = {s["id"] for s in fixture["scenarios"]}
print(",".join(sorted(ids & known)))
' "$PREV_RESULT")"
    echo "联网相关场景（依据：场景类别${PREV_RESULT:+ + $(basename "$PREV_RESULT")}）：$ONLY"
    ;;
  *) ONLY="$SCOPE" ;;
esac

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

echo "开始真实模型回归（全量约 15 至 25 分钟）……"
set +e
ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "set -e; D='$REMOTE_DIR'; C='$CONTAINER'
  mkdir \"\$D/cand\" && tar -xzf \"\$D/cand.tgz\" -C \"\$D/cand\" 2>/dev/null && rm \"\$D/cand.tgz\"
  docker exec \"\$C\" mkdir -p \"\$D\"
  docker cp \"\$D/.\" \"\$C:\$D/\"
  docker exec -u 0 \"\$C\" chown -R 10001 \"\$D\"
  docker exec -w \"\$D\" -e PYTHONPATH=\"\$D/cand\" \"\$C\" \
    python -m homestay_bot.tools.reply_regression gate \
    --fixture fixture.json --baseline baseline.json --out result.json --only '$ONLY'"
status=$?
set -e

RESULT="$STAGE/reply-gate-$TAG.json"
if ! ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "docker exec '$CONTAINER' cat '$REMOTE_DIR/result.json'" \
    > "$RESULT" 2>/dev/null; then
  echo "✗ 取不到回归结果（运行退出码 $status），按不通过处理"
  exit 2
fi

# 摘要与新基线在本机生成；只用标准库，兼容系统自带的 Python 3.9。
python3 - "$RESULT" "$TAG" "$STAGE" "$SCOPE" "${REPLY_GATE_SCOPE_REASON:-}" <<'PY'
import json, sys
path, tag, stage, scope, reason = sys.argv[1:6]
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
if counts.get("scoped"):
    lines.insert(1, f"- 范围：{scope}，只跑 {counts['scenarios']} 个（共 {counts['total_scenarios']} 个）；"
                    f"理由：{reason}")
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
