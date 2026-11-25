# Gemini → 审查方：知识库页面整体重构 Spec（阶段 1–3）递交审查

- **日期**：2026-11-25
- **基线提交**：`main` = `origin/main` = `fa0ac3872e5dd5a9bf1e7b52102a5e1ded4c0022`（`v1.76.1`）
- **送审 Spec**：[docs/specs/2026-11-25_knowledge-base-refactor-spec.md](file:///Volumes/02/code/homestay-bot/docs/specs/2026-11-25_knowledge-base-refactor-spec.md)
- **当前状态**：
  - 用户已完成 **Spec 阶段 1（现状证据与边界）** 与 **Spec 阶段 2（功能点、信息架构与交互细节）** 的全部问答拍板，并在阶段 3 提出「spec1-3 整理好报告，准备递交审查」。
  - **纯只读设计阶段**：未修改任何业务代码、模板、静态资源或测试文件，未执行提交/推送/部署，未调用外部服务。
  - **尚未收到用户“开始”编码指令**。
- **审查边界**：审查意见仅作为评审建议，不代替用户确认；若审查结论要求修订 Spec，修订后须请用户重新确认受影响段落并明确回复“开始”后再进入编码。

---

## 一、背景与用户已确认决策链

用户提出的核心痛点：当前知识库页面中**全局知识库和房间知识库管理混乱、识别不清晰**，希望整体改进并取消阻碍管理的既有旧限制。

经过阶段 1 与阶段 2 的多轮结构化确认，用户已明确以下 7 项核心边界与产品裁决：

| 阶段 | 编号 | 议题 | 用户已确认结论 |
| :--- | :--- | :--- | :--- |
| 阶段 1 | U1 | 重构允许改动的层级 | **仅重构前端页面与后台管理路由/服务**：保持现有数据库表结构与机器人检索契约完全不变，不触发 `REPLY_PATHS` 真实模型回归门禁。 |
| 阶段 1 | U2 | 全局与房间的管理结构 | **双工作区顶层切分**：一级页签明确拆分为「全店通用 / 按房间管理 / 店外周边」，另保留「待审核候选」与「资料核实」。 |
| 阶段 1 | U3 | 明确要放开的 5 项限制 | ① 取消新建默认 `unreviewed` 陷阱；② 取消英文问答强制手填；③ 解除详情页不能启停、分类必须手打精确匹配等后台操作限制，支持详情页直接启停与跨房复制；④ 支持一条知识一次选定多个房间；⑤ 放宽配图张数限制（仍保留 JPG/PNG 兼容企业微信）。 |
| 阶段 1 | U4 | 安全与防错底线 | **全部保留**：防串房、拦截 `【待管理员确认】` 占位符、管理员写/员工只读、服务端一次性 CSRF、`EMPLOYEE_CSP` 无内联脚本、审计不落正文。 |
| 阶段 2 | U5 | 新建知识交互形态 | 采用**独立新建页 `/employee/knowledge/new`**（支持带当前工作区/房间预设，与编辑页共用分层表单组件，无 JS 亦流畅）。 |
| 阶段 2 | U6 | 英文选填的零迁移兜底 | 认可**留空自动以中文填充落库**，并在编辑页通过「英=中判定」回显为空 + 提示「跟随中文」，防止改中文后英文列残留漂移。 |
| 阶段 2 | U7 | 配图上限与复制策略 | 配图上限放宽至 **9 张**；「复制到其他房间」默认提供**同时复制配图（物理拷贝为独立 `file_id`）**选项。 |

---

## 二、Spec 阶段 1–3 核心要点总览

| 阶段 | Spec 章节 | 核心内容 |
| :--- | :--- | :--- |
| **阶段 1：现状证据** | §1 | 6 项经源码核验的混乱与受限根因：① `KnowledgeAdminService.list_all()` 混排四类范围且 `room=shared` 混合 `global`/`public`/`unreviewed`；② 列表与卡片丢失 `room_number`；③ 房源常驻卡片（`property_card_snippet()`）与房间知识割裂；④ `entry_state.html` 四类徽章全部撞色为 `info` 蓝；⑤ 新建默认 `unreviewed` 陷阱 + 双下拉未联动；⑥ 13 字段平铺折叠新建、英文必填、精确手打分类、详情页禁启停、配图限 3 张、`referrals.html` 导航位置错位。 |
| **阶段 2：功能与信息架构** | §2 (F1–F4) | **F1** 顶层 5 页签统一归入 `page_actions` + 存量 `unreviewed` 仅在非零时顶部告警并可集中定界 + 旧 URL 参数兼容；**F2**「按房间管理」桌面双栏/移动切换工作台（左侧带条数房间目录 + 右侧本房只读常驻房源卡 + 统一 `房号 · 房名`）；**F3** 独立语义色系区分全局（青绿）/房间（深靛）/店外（暖灰）/待审（琥珀），不借用 `--primary` 陶红，补齐配图数与英文沿用角标；**F4** 独立新建页、三选一归属联动、多房间同事务批量创建、英文选填防漂移、详情页直接启停、分类下拉+`<datalist>`、快捷定界 `POST /{id}/scope`、配图放宽至 9 张、跨房复制 `POST /{id}/copy`（物理拷贝独立配图文件）。 |
| **阶段 3：决策、风险与验证** | §3–§5 | **D1–D7** 明确零迁移/零 `REPLY_PATHS` 边界、多房独立记录模型、英文防漂移规则、配图物理拷贝隔离、路由注册顺序护栏、一次性 CSRF + PRG 与安全底线；列出 **5 处因解除旧限制而需同步调整的既有测试断言**、复制并发/容错限额、精确到文件与符号的 14 项改动计划，以及涵盖静态检查、定向集成测试、真实服务 Playwright 浏览器测试和本地全量 `pytest`（因触及 `application.py`）的验证方案。 |

---

## 三、请审查方重点核查的 8 个问题

请按严重度（P1 阻断/功能错误、P2 边界缺口或维护风险、P3 建议）给出可定位的审查意见，并区分「方案缺口」「现有代码事实」「需用户重新裁决」：

### 1. 英文选填中文兜底（D3）对检索、证据门与向量的隐性影响
- **方案现状**：因遵守 U1（不改 `models.py`、`knowledge_service.py`、`knowledge_evidence_policy.py`、`knowledge_embeddings.py`），留空 `question_en`/`answer_en` 时由后台服务自动填入 `question_zh`/`answer_zh`。
- **请核查**：
  - 当一条知识在英文列存了中文原文，英文客人提问（`Language.EN`）时：
    - `KnowledgeService._score()` 与 `retrieve_detailed()`（`services/knowledge_service.py` L642–L656、L788–L799）是否能正常召回？
    - `knowledge_evidence_policy.py` 的属性/限定词正则（如 `fee`/`time`/`late_checkout` 等）遇到「英文问句 + 中文审核答案」时，是否可能出现**证据门判为 `insufficient` 拒答**，或反过来**把纯中文答案原文直接发给英文客人**？
    - `knowledge_embeddings.py::embedding_text()`（L95–L101）对 `Language.EN` 计算中文文本向量，开启语义检索时是否存在意料之外的排序偏差或副作用？
  - 如果存在真实业务缺陷，是否应在页面明确告知“留空英文仅保障中文问答与不存空串，英文客人提问仍建议补写英文”，还是需要向用户提示调整 U1 边界？

### 2. 裸 `/employee/knowledge` 默认落点改为 `section=global` 的发现性风险
- **方案现状**：默认进入 `/employee/knowledge` 落到「全店通用（`section=global`）」，房间知识在「按房间管理（`section=rooms`）」，店外在「店外周边（`section=public`）」。
- **请核查**：
  - 如果管理员在首页搜索栏输入一个关键词（例如某间房专属的「投影仪」），但当前停留在 `section=global`，只搜全店通用会搜不到房间知识，是否会造成误判“库里没有这条知识”？
  - 是否需要在搜索/工作区设计上补充「**全部知识**（`section=all`）」切换或「跨区搜索命中提示（例如提示“房间知识中还有 N 条匹配”）」？
  - 对既有测试 `tests/integration/test_knowledge_routes.py` 中直接请求 `/employee/knowledge` 的多条用例（如翻页、筛选、待审核提示）是否会引入不必要的兼容负担？

### 3. 跨房间复制配图的并发窗口与物理存储安全（D4）
- **方案现状**：`PrivateFileStorage.copy_image(file_id)` 物理拷贝生成全新随机 `file_id`，失败回滚时删除本次拷贝出的全部新文件。
- **请核查**：
  - 在 `SessionKnowledgeAdminService.copy_to_rooms()` 中，如果先查源配图列表 → 事务外拷贝磁盘文件 → 再开事务插入新条目与新配图，期间若并发发生源条目被删或源配图被删/调整顺序，是否会产生不一致？
  - 建议最优并发方案是什么（例如：单事务中先 `FOR UPDATE` 锁源条目并读出配图列表，还是接受“先拷贝、入库前锁源条目复核版本/存在性、不符则中止并清文件”避免跨磁盘 I/O 长持锁）？
  - SQLite 与 PostgreSQL 下拷贝大量图片（如 10 间房 × 9 张 = 90 次文件拷贝）是否有超时、单请求耗时或磁盘空间风险？是否应给单次「带配图复制」设置更合理的房间数上限（例如 20 间或按总文件数上限如 50 个文件）？

### 4. 多房间批量创建（D2）的校验契约与边界情况
- **方案现状**：`POST /employee/knowledge` 同时兼容 `property_id`（单值）与 `property_ids`（复选列表），选多间房时同一事务内分别创建多条 `scope="property"` 独立记录。
- **请核查**：
  - 同时传入 `property_id` 与 `property_ids`、`property_ids` 含重复 ID / 空字符串 / 非数字 / 已停用房源 / 不存在房源，以及 `scope != "property"` 时仍提交了非空 `property_ids`（例如无 JS 浏览器勾选了房间又把范围改回全店通用），应如何精确判定（拒绝 422 还是在 `scope != "property"` 时校验拦截）？
  - 批量创建多条房间知识后，若其中某条的 `return_to` 是具体单房链接，回跳去哪更符合直觉？

### 5. 详情页直接启停（D6）与 `data-unsaved-warning` / 一次性 CSRF 的交互边界
- **方案现状**：详情页左侧编辑表单带 `data-unsaved-warning`，右侧新增「立即停用 / 立即启用」与「复制到其他房间」表单，共用页面签发的同一个 `csrf_token`，提交后 303 重定向回详情页刷新 token。
- **请核查**：
  - 管理员修改了编辑区内容但尚未点击保存，直接点右侧「立即停用」或「复制」时：`admin.js` 的 `data-unsaved-warning` 目前只监听 `beforeunload` 还是也会拦截同页其他表单提交？是否存在“点了右侧启停导致左侧未保存修改静默丢失”的体验坑？
  - 管理员在详情页点「上传配图」或「启停」后若按浏览器后退键再点「保存」，因旧 token 已消费会得到 409，这是否与现有详情页配图/删除行为一致且可接受？
  - `POST /{entry_id}/enable|disable` 在 [routes/knowledge.py](file:///Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py#L1546-L1549) 目前会自动给不含 `#` 的 `return_to` 追加 `#knowledge-entries` 锚点；若回跳目标是 `/employee/knowledge/{entry_id}`，这个锚点在详情页不存在，是否应在 Spec 中明确调整该锚点逻辑？

### 6. 快捷定界接口 `POST /{entry_id}/scope`（F4）的字段一致性约束
- **方案现状**：供 `section=unreviewed` 卡片一键设为 `global` 或 `public`。
- **请核查**：
  - 如果该路由仅允许 `unreviewed -> global/public`，需在服务层显式校验原状态为 `unreviewed`、执行占位符校验（避免含 `【待管理员确认】` 的已启用未审草稿一键上线生效！——**重点核查这一点**），并确保 `property_id=None`；
  - 若以后从 `global` 改为 `property` 或 `public` 时，已勾选的 `show_in_miniprogram=True` 是否保留原布尔值（由 `miniprogram_content.py` 自动过滤掉）还是自动清零？请确认与现有编辑行为是否一致。

### 7. 配图上限放宽至 9 张（F4）与现有回复测试/体验预期
- **方案现状**：`MAX_KNOWLEDGE_IMAGES` 由 `3` 改为 `9`，不改动 `conversation_service.py::MAX_REPLY_MESSAGES = 5`。
- **请核查**：
  - 全仓是否有任何测试（除 `tests/integration/test_knowledge_image_admin.py` 外）硬编码假设单条知识最多 3 张配图？
  - 在详情页配图列表中，对第 5 张及以后的图片是否值得增加视觉标注（如“超出单轮回复额度，调整前移后发送”），减少管理员困惑？

### 8. 既有测试变更与验证策略充分性
- **方案现状**：Spec §3.2 列出 5 处因解除旧限制而需同步更新的既有测试，且计划新增真实服务 + SQLite 的 `tests/browser/test_knowledge_workbench.py`，提交前因改动 `application.py` 跑一次本地全量 `pytest`，并验证 `git diff` 与 `REPLY_PATHS` 零交集。
- **请核查**：
  - 5 处测试改动清单是否完整、有无遗漏（如 `tests/browser/test_knowledge_referrals_page.py`、`tests/unit/test_admin_assets.py` 等）？
  - 是否存在必须覆盖的 PostgreSQL 专项并发用例（例如跨房间复制与删除竞争、多房间批量创建事务回滚）？

---

## 四、审查快照（SHA-256）

以下文件摘要绑定本次送审版本，基于 Git `HEAD = fa0ac3872e5dd5a9bf1e7b52102a5e1ded4c0022` 加当前工作区：

| 文件 | 工作区状态 | SHA-256 |
| :--- | :--- | :--- |
| `docs/specs/2026-11-25_knowledge-base-refactor-spec.md` | 未跟踪（本次送审 Spec） | `a4575c69a22bee96da16b948f8dbebcb40cb6c18511924d91801d8855046f299` |
| `src/homestay_bot/routes/knowledge.py` | 与 HEAD 相同 | `d4ad14a5f7f0e86cdf9d446bfd3c00a5835b91a5fe23f0a42d26fe1c98e1e6d4` |
| `src/homestay_bot/routes/knowledge_referrals.py` | 与 HEAD 相同 | `576b218ceb39695bdf802cf7f4af15ee95b18d3f0faa7bcc6bf21d299643e4ac` |
| `src/homestay_bot/application.py` | 与 HEAD 相同 | `7d074cd4d16989e38acd417d12340fe36dd5eb7cb476c5c93a3c8e520041a8c0` |
| `src/homestay_bot/services/knowledge_service.py` | 与 HEAD 相同 | `113dcd6fc1311126236f4d1f6f3baa54376e8646686ccd689098d154fe4d4bb0` |
| `src/homestay_bot/services/knowledge_embeddings.py` | 与 HEAD 相同 | `55f517e84de0d3d241601ad747887c7af3bc9c200b30733b854fd0335d68f321` |
| `src/homestay_bot/services/miniprogram_content.py` | 与 HEAD 相同 | `3db8c6861924adf21877d75d4c23097f132924cbe3c2fda8cb3f705bcc2c47cd` |
| `src/homestay_bot/services/private_file_storage.py` | 与 HEAD 相同 | `35a3cb2530c43f41e185ef4aceba00d58d9e0376b1058cc2086cfb6ce5f72fe5` |
| `src/homestay_bot/repositories/knowledge.py` | 与 HEAD 相同 | `27334eb55a896819a30961caddf7a406460aee90a96d71b0ce98d5dcc3c4aafd` |
| `src/homestay_bot/domain/models.py` | 与 HEAD 相同 | `a22955b397e4853540ee919c80739b0535bb61c64a6e7a72c5edba2e730676e9` |
| `src/homestay_bot/templates/knowledge/index.html` | 与 HEAD 相同 | `0d671759a6ba726127bbcac6991567e7d1baf4e769249c94d342612d8b9a3a6c` |
| `src/homestay_bot/templates/knowledge/detail.html` | 与 HEAD 相同 | `3d6cc2f083d3aebf6e9b05cea14ef273fd8f1ca892a2ffe7aa991939d9243e0c` |
| `src/homestay_bot/templates/knowledge/scope_fields.html` | 与 HEAD 相同 | `c19e1ce322a09812dcf79dbffafe7013687d378afdcd8aa3466a2403cfcec207` |
| `src/homestay_bot/templates/knowledge/entry_state.html` | 与 HEAD 相同 | `29e24c797616ee2b7cbecfd4776c0e805775d5675b2adcb9116cc8370d280b55` |
| `src/homestay_bot/templates/knowledge/referrals.html` | 与 HEAD 相同 | `e52a27b46883beb3fcb24546883cecd5e5753ccd45287a1427ea232475f933f6` |
| `src/homestay_bot/templates/knowledge/referral_detail.html` | 与 HEAD 相同 | `f220094407d875df78e4175096d208ec3c97a0ab8bf8befd` |
| `src/homestay_bot/static/app.css` | 与 HEAD 相同 | `94e7018b9b5b7f67025a1b3d034ea54ed1fae2896206c2e2115e0cb27d2d41e8` |
| `src/homestay_bot/static/admin.js` | 与 HEAD 相同 | `a2754143defac007242bbddb19c23ca39956a3535f1418408c251a17d48f5803` |
| `scripts/release/reply_gate.sh` | 与 HEAD 相同 | `f3e11913d62af74d66e3fa7418284e6aa66152581bb3d90119b48354c72f8764` |
| `tests/integration/test_knowledge_routes.py` | 与 HEAD 相同 | `4fc044d9d1dc73af23530048a47e08f6ff91c1992d2ac349e226b35fecac293d` |
| `tests/integration/test_knowledge_image_admin.py` | 与 HEAD 相同 | `397d247701410ad84a4849e767221d2c557f0b860f89369183062599374f16a7` |

> **工作区归属说明**：工作区中另有 3 处早先遗留的本地设计配置改动（`M .impeccable/config.json`、`?? .impeccable/surfaces/`、`?? PRODUCT.md`），不属于本次知识库重构 Spec，本轮未触碰。

---

## 五、本轮未执行事项

- 未修改任何业务代码、模板、样式、脚本或测试文件；
- 未运行业务测试（仅文档与 Spec 编写）；
- 未调用真实 DeepSeek / Hostex / 企业微信，未接触生产环境；
- 未执行 `aoci_maintain`（本次仅产出待审 Spec 与审查交接草案，待审查收敛或实施达最终稳定态时统一维护 AOCI 认知索引）。
