# 审批人工核验第三轮审查交接 · Codex → Claude

日期：2026-10-02。审查对象：Claude 针对 AR5–AR7 的第三轮修复报告、相关工作区改动与验证证据。

**结论：AR5 的原子性修复、AR6 的状态与轮次保护在独立本地验证中成立，AR7 原来的区号测试短路问题已修复。本轮未发现新的 P1，但还有两项 P2 测试判别力缺口：AR8、AR9。** 当前实现能够通过补充边界探针；需要把这些约束固定为可持续运行的回归，而不是据测试缺口认定业务实现仍有已复现错误。

本文只新增交接文档。Codex 没有修改业务代码、测试、Spec 或既有报告，没有提交、推送、部署，没有调用真实 Hostex、DeepSeek 或企业微信。下面的反向验证仅在独立 Python 进程中临时替换函数，没有写入源码；数据与上游均为合成。Claude 后续实施沿用用户当前明确授权，本文不授予新的外部操作权限。

## 1. 阅读入口、基线与证据复用

1. [Claude 第三轮审查请求](../../docs/reviews/2026-10-02_claude-to-codex-approval-review-round3-handoff.md)：直接审查对象，已明确说明本地修复与验证完成。
2. [Codex 第二轮交接](../../docs/reviews/2026-10-02_codex-to-claude-approval-review-round2-handoff.md)：AR5–AR7 的原问题与 E5、E6。
3. [人工核验 Spec 第 6 节](../../docs/specs/2026-10-01_approval-review-backfill-spec.md:109)：本轮有效修订，尤其 R3／R5 的恢复阈值和迟到写回契约。
4. [Claude 第二轮报告更正](../../docs/reviews/2026-10-01_claude-to-codex-approval-review-round2-handoff.md:74)：与第三轮报告一起理解，不再要求恢复已被替换的保存点实现。

写作时 Git 快照：

- 分支 main，HEAD 为 f3d03cecdc684de4449fca59ae04a8398c8f26e7。
- 已跟踪工作区 diff 为 21 个文件、+915／−57；包含前端实施与审批修复，不能整体称为一个已提交的修复。
- 写作前另有 8 个未跟踪文件：五份审批交接报告、审批 Spec、两份人工核验测试。本文是新增的第六份审批交接报告。
- 本次保留全部既有工作区修改；没有修改评审目录 README、发布记录、任务文档或旧报告。
- 与刚完成的第三轮审查相比，9 个相关源码、测试、报告及 Spec 输入的 SHA-256 均未变化；复用该审查的验证证据。本次写文档不重复运行业务测试。

依据：[开发手册的环境边界](../../YuMi民宿AI开发经验与防回归手册.md:483)要求分别验证数据库、运行态与真实外部结果。测试结果仅证明各自实际覆盖的路径；本文没有核查当前生产审批数量、部署副本或生产健康。

## 2. 原问题与当前修复的核对

| 项目 | 结论 | 文件、符号与证据 |
| --- | --- | --- |
| AR5：回填保存点释放后审计失败，状态无法回滚 | 修复成立，本地 SQLite 已验证 | [ApprovalPageService.backfill_reservation](../../src/homestay_bot/services/approval_page_service.py:314)把状态、订单号与审计放入同一次 flush；[SessionApprovalPageService.backfill_reservation](../../src/homestay_bot/application.py:1826)成功后才提交。审计失败、调用方主动回滚、正常提交、唯一键竞争均有相关回归 |
| AR6①：原创建未结束时，重复确认立即恢复并放开下一次创建 | 修复成立，本地已验证 | [BookingService.confirm_and_create](../../src/homestay_bot/services/booking_service.py:108)在 CREATING 不满 CREATING_STALE_AFTER 时原样返回；[后台恢复](../../src/homestay_bot/application.py:3088)引用同一阈值。现有交错用例验证重复确认和 A2 均不会造成再次建单 |
| AR6②：旧结果覆盖新轮次或终态 | 当前实现成立，持续回归仍缺一条关键边界 | [_reconcile_or_mark_review](../../src/homestay_bot/services/booking_service.py:257)和 [_mark_needs_review](../../src/homestay_bot/services/booking_service.py:339)共用 _same_round；[get_for_update](../../src/homestay_bot/repositories/approvals.py:46)刷新数据库现值。六组补充探针验证新 CREATING、BOOKED、REJECTED 的字段都受到保护；见 AR8 |
| AR7：区号用例被同名分支掩盖 | 原缺口已修复；新增长度保护负例判别力不足 | [test_reopen_blocks_any_order_that_may_be_the_same_guest](../../tests/integration/test_approval_review_actions.py:396)中 +86、0086、86 三组已用不同或缺失姓名，只能依赖手机匹配。405–406 行的 11 位号码负例仍不能捕获错误去前缀；见 AR9 |

## 3. AR8 · P2：当前迟到用例没有单独验证确认时间比较

**位置与原因**

- 测试：[test_late_result_of_an_old_round_cannot_overwrite_the_new_round](../../tests/integration/test_approval_review_actions.py:624)。
- 639–641 行先完成新一轮确认，明确断言其已经是 BOOKED；之后旧一轮成功或业务失败才返回。
- 实现：[BookingService._same_round](../../src/homestay_bot/services/booking_service.py:272)同时要求当前状态为 CREATING、确认时间等于旧调用捕获的 attempt。

当前测试验证了终态保护。但是，即使去掉 approved_at 比较，保留“只有 CREATING 才写入”，同样能挡住已经 BOOKED 的审批。因此，测试不能区分“只看状态”与“正确检查轮次”。

**已经执行的反向验证**

仅在本进程把 _same_round 改为“当前状态是 CREATING 即允许写入”，保留生产源码不变，运行上述两组迟到用例：

~~~text
合成反向验证：仅在内存中移除确认时间比较，保留 CREATING 状态检查。
2 passed in 3.82s
~~~

Claude 第三轮报告中“让 _same_round 恒为真，两组失败”的证据有效，但该实验同时移除了状态保护与时间保护，不能单独证明时间比较的判别力。

**最小补测与验收**

补充旧调用返回时，新一轮仍处于 CREATING、但 approved_at 已不同的场景。复用现有项目 SQLite 引擎、真实 SQLAlchemy 仓储和合成上游；保留旧会话的缓存对象，使锁后刷新也实际参与验证。无需新增状态框架或业务字段。

1. 旧轮次分别通过 _reconcile_or_mark_review 与 _mark_needs_review 返回。
2. 新轮次状态保持 CREATING，房间、金额、订单号、请求编号、失败信息与确认时间不被旧结果改变。
3. 新轮次自己的后续成功结果仍能正常写入。
4. 在进程内仅移除时间比较时，新增场景必须失败；恢复实际实现后通过。

可采用真实门面交错，也可通过独立会话建立新 CREATING 状态后调用真实写回出口；后者需覆盖真实锁后读取与最终数据库断言，不能只对 _same_round 写一个返回值镜像测试。若在现有 PostgreSQL 组复用该场景，应明确它是新增覆盖，旧“9 passed”不包含这一场景。

**当前实现的独立控制证据**

Codex 已在真实 SQLite 仓储中保留旧会话，另用独立会话设定新确认时间、房间 102、金额 499 与当前请求编号，再执行两个旧写回出口。下表的六种组合均通过最终数据库字段断言：

| 数据库当前状态 | 旧成功结果出口 | 旧失败结果出口 |
| --- | --- | --- |
| 新一轮 CREATING，确认时间不同 | 保持当前状态与字段 | 保持当前状态与字段 |
| BOOKED | 保持当前状态与字段 | 保持当前状态与字段 |
| REJECTED | 保持当前状态与字段 | 保持当前状态与字段 |

该探针证明当前实现能挡住这类写回；它没有作为仓库回归测试保存，也没有证明真实外部订单不会额外创建。

## 4. AR9 · P2：11 位号码负例无法捕获错误去前缀

**位置与原因**

- 测试：[test_reopen_blocks_any_order_that_may_be_the_same_guest 的号码负例](../../tests/integration/test_approval_review_actions.py:405)：上游手机为 86013800138，与夹具客人手机 13800138000 比较，预期允许回退。
- 实现：[_normalize_phone](../../src/homestay_bot/services/approval_page_service.py:99)仅在去掉国家码后剩 11 位时去前缀；[_maybe_same_guest](../../src/homestay_bot/services/approval_page_service.py:108)比较双方归一化手机。

保留 86013800138，或错误删掉开头 86 得到 013800138，两者都不等于夹具手机号。这个 blocked=False 断言在正确与错误实现下都会成立。

**已经执行的反向验证**

仅在本进程移除国家码去除的长度条件，运行九组身份参数：

~~~text
合成反向验证：仅在内存中移除手机号的前缀长度保护。
9 passed in 0.47s
~~~

独立约束断言 _normalize_phone("86013800138") == "86013800138" 在当前真实实现下通过。问题是已有参数用例没有固定这一约束。

**最小补测与验收**

在已有测试文件中增加一条有判别力的长度保护断言，或调整场景，构造错误去前缀才会产生误匹配的号码对照。直接断言这个 11 位输入归一后仍等于原值即可验证已明确的业务约束，无需给所有辅助函数各写一套测试。

- 当前真实实现通过。
- 仅移除前缀长度条件时，该约束必须失败。
- 保留现有 +86、0086、86 三组使用不同／缺失姓名的场景，确保手机分支仍被实际验证。

## 5. 可运行的反向验证

在仓库根目录运行。以下命令只临时替换进程内函数，进程退出即消失；使用既有隔离测试，不读取生产配置或调用真实外部服务。这两段故意移除保护，所以“通过”表示测试没有发现这项退化，不表示被替换的实现正确。

### E8 · 移除时间比较，保留状态检查

~~~sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import pytest
from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.services.booking_service import BookingService

def accept_any_creating_round(locked, attempt):
    """仅在本进程移除时间比较，保留状态检查，核对迟到用例判别力。"""
    return locked.status is ApprovalStatus.CREATING

BookingService._same_round = staticmethod(accept_any_creating_round)
print("合成反向验证：仅在内存中移除确认时间比较，保留 CREATING 状态检查。")
raise SystemExit(pytest.main([
    "-q", "-p", "no:cacheprovider",
    "tests/integration/test_approval_review_actions.py::test_late_result_of_an_old_round_cannot_overwrite_the_new_round",
]))
PY
~~~

### E9 · 移除手机前缀长度条件

~~~sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import re
import pytest
from homestay_bot.services import approval_page_service as page

def strip_prefix_without_length_guard(value):
    """仅在本进程移除长度条件，检查已有号码参数能否发现错误。"""
    digits = re.sub(r"\D", "", value or "")
    for prefix in ("0086", "86"):
        if digits.startswith(prefix):
            return digits[len(prefix):]
    return digits

page._normalize_phone = strip_prefix_without_length_guard
print("合成反向验证：仅在内存中移除手机号的前缀长度保护。")
raise SystemExit(pytest.main([
    "-q", "-p", "no:cacheprovider",
    "tests/integration/test_approval_review_actions.py::test_reopen_blocks_any_order_that_may_be_the_same_guest",
]))
PY
~~~

以上是已执行命令的注释整理版；本次文档写作只做脚本语法检查，没有再次执行变异验证。

## 6. 第三轮报告第 4 节的其他问题

| 问题 | 审查答复与边界 |
| --- | --- |
| 五分钟阈值是否阻碍处理 | 源码与修订 Spec 一致；重复确认在阈值内保持 CREATING，后台沿用同一常量。五分钟是恢复判断阈值，实际处理仍依赖 worker 运行与轮询；本文没有验证生产等待时间 |
| 建单是否重试、整条流程是否必定短于五分钟 | [HostexClient.create_reservation](../../src/homestay_bot/integrations/hostex_client.py:432)显式使用 retry_safe=False，创建不在客户端自动重放；写后核验的只读查询有分页与重试，不能用单次 HTTP 默认超时推断完整流程一定结束 |
| approved_at 与时区比较 | 当前 SQLite 下 _as_utc 和锁后刷新通过真实仓储探针；PostgreSQL 的九条用例属于 Claude 提供的证据，本次未独立重跑。持续回归需要 AR8 补充两轮都处于 CREATING 的覆盖 |
| A1 去保存点后，共享会话会不会继续使用失败事务 | 已追踪当前生产调用方为独立 SessionApprovalPageService 门面，异常时不提交，退出会话；直接测试调用方关闭或回滚。未发现当前生产路径在此错误后继续用同一会话处理其他写操作 |
| populate_existing 是否破坏调用方 | 当前实际加锁调用集中于 BookingService 的入口及两个写回出口；入口加锁前没有依赖待保存审批修改的路径。ApprovalService 的创建／来源消息查询没有调用此方法；本轮未发现依赖旧缓存行为的生产调用方 |

迟到成功结果的可追溯性作为建议项单列：

- _same_round 的警告只有审批编号和当前状态，没有把旧 attempt、上游请求编号或匹配订单号关联到审批审计。该范围已由 Claude 报告第 4 节第 3 点和第 5 节披露。
- 项目并非完全没有外部调用记录：[application.py::record_external_call](../../src/homestay_bot/application.py:4844)在独立事务中写 ExternalRequest，包含请求编号及调用结果；这不等于记录了“该旧轮次结果为何被丢弃、属于哪个审批”。
- 建议后续明确“旧轮次成功但本地拒绝写回”的最小追溯要求，再决定记录审计或关联日志。通知员工涉及新的流程与外部消息，不能由本文自动扩展授权。
- 这一建议不列为本轮新的 P1，也不要求为它改动当前正确的状态保护。本轮最小修补交付为 AR8、AR9 的可判别测试及对应验证记录。

Claude 报告提及的其他 SQLite 保存点风险仍在本轮范围外；应按具体调用链核查是否已有物理父事务，不能把 A1 的反例自动推广到所有保存点。

## 7. 验证清单与后续交付

| 证据 | 结果 | 证明范围 |
| --- | --- | --- |
| Codex 独立相关回归 | 45 passed，8.69 秒，退出码 0 | 人工核验、确认流程、审批仓储和审批页服务；命令见下方 |
| E8 反向验证 | 2 passed，3.82 秒 | 原迟到两组不能单独验证时间轮次保护 |
| E9 反向验证 | 9 passed，0.47 秒 | 九组身份参数不能捕获错误去掉短号码国家码 |
| 六组写回边界探针 | 六组关键字段断言全部通过 | 当前真实 SQLite 实现保护新 CREATING、BOOKED、REJECTED，覆盖旧成功／失败两个出口 |
| 11 位号码约束探针 | 通过 | 当前归一化实现保留 86013800138 |
| Claude 提供的 PostgreSQL 与全量结果 | PostgreSQL 组 9 passed；全量 2407 passed、2 failed、15 skipped；修订失败的流程测试后局部 9 passed | 作者报告证据；Codex 没有独立重跑全量或 PostgreSQL；不能把局部修复结果改写成一次全量全绿 |
| 本次文档静态检查 | 20 个文件链接及引用行有效；2 段探针 Python 语法、空白及 git diff --check 通过；src／tests 共 333 个文件写作前后内容摘要一致 | 文档质量与修改范围；业务证据复用，不重复测试 |

Codex 45 条相关回归的原命令：

~~~sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/integration/test_approval_review_actions.py \
  tests/integration/test_approval_flow.py \
  tests/integration/test_approval_repository.py \
  tests/unit/test_approval_page_service.py
~~~

未覆盖：真实上游建单耗时、结果可见性与字段契约；迟到成功后多单的实际人工发现流程；生产运行态、部署副本、登录页面和真实消息收件。上述探针未进行生产写入，没有读取受保护的未跟踪项目总结文件。

请 Claude 逐项回复 AR8、AR9“成立／部分成立／不成立”，附文件、测试符号及可判别证据。若当前授权包括补测，优先修改现有人工核验测试、按需复用 PostgreSQL 组，并在新的交接或追加更正中写明：

1. 新场景、最终数据库断言及选择该验证范围的依据。
2. 当前真实实现通过；只移除对应保护的进程内变异必须让新增用例失败。
3. 精确命令、结果、退出码及未执行范围；未变输入的有效证据直接复用，不因交接重复跑全量。
4. 最终相关 diff、Git 状态与“本地修改和验证完成，可以交给 Codex 审查”的明确声明。

这两项是验证覆盖补充，现有业务契约已经在 Spec 第 6 节规定；若发现必须改业务逻辑或扩大到审计、通知、共享引擎，则先说明新的证据与范围，按当前用户授权和适用 Spec 门禁处理。
