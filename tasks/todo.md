# 当前任务：无进行中工作单，以下为挂起事项

2026-09-08 当天完成并上线的工作单已全部归档，见文末「已完成工作单」。
本文件现在记录**已确认存在、但当前不处理**的事项，供后续会话直接接手。

## 挂起 · 百居易 Webhook 从未接通

**用户 2026-09-08 决定：先留存，不修。**

### 已确认的事实

| 观察 | 值 | 取证方式 |
| --- | --- | --- |
| 健康项 `hostex_webhook` | `never_received` | 生产 `/employee/health`（v1.15.0 起才有这一项） |
| 回调密钥 | **已配置** | 报 `never_received` 而非 `not_configured`，判据见 `RuntimeClientStatus.hostex_webhook_configured` |
| `hostex_webhook_events` 表 | **0 行** | 生产库只读查询 |
| `stay_orders` | 153 笔，全部来自对账轮询 | 同上；`updated_at` 随每轮对账刷新 |
| 端点挂载状态 | 已挂载，只收 POST | `GET https://akros.icu/webhooks/hostex` → 405 |
| 生产整体 `status` | **`degraded`** | v1.15.0 部署后如实反映上述事实 |

结论：**回调配了密钥，但一次都没打通过。** 订单一直靠对账轮询进来，
`services/hostex_sync.py::HostexSyncService.reconcile` 因此是唯一的数据通道，
它的完整性比原审查报告估计的重要得多（见已归档的 F-05）。

### 为什么现在才暴露

v1.15.0 之前健康项叫 `hostex_webhook_sync`，实际读的是
`app.state.hostex_sync_last_success`——一个由**对账轮询**刷新、且在进程启动时
被直接赋成当前时间的心跳。回调从没通过，它照样报 `ok`。正名并接上真实心跳后，
事实浮现。`degraded` 是准确的，不是新故障。

### 三个可能成因，都需要百居易后台或其投递日志才能分辨

1. 百居易侧根本没配置回调地址
2. 配了但打不到 `https://akros.icu/webhooks/hostex`（网络、证书、路径）
3. 打到了但密钥不匹配，被 `HostexWebhookService.verify_secret` 拒绝

### 诊断页现在怎么显示它（v1.15.1 起）

页面会显示「百居易回调接收 · 从未收到」，标注「由这一项引起」降级，并给出
核对回调地址与密钥的处理说明。v1.15.0 时这一项因标签表未同步改名而完全不渲染，
页面只显示降级却看不到原因；该回归已修复。

### 处理时的边界

- **不要向该端点发 POST 做探测**。任何 POST 都是写入尝试，可能造出垃圾事件行；
  `GET` 得到 405 已足以证明路径已挂载。
- 若确定不使用回调，把密钥清空即可：健康项变回 `not_configured` 且不参与降级，
  与 `wecom_contact_sync` 的既有约定一致。那是诚实的表达，不是掩盖。
- 修复后应能看到 `hostex_webhook_events` 出现行，且健康项转为 `ok`。

## 其他挂起事项

### 验证缺口（用户已明确接受）

- **隔离 PostgreSQL 验证未做**。代码审查报告要求「修改了迁移、锁或事务时，另用
  隔离 PostgreSQL 验证事务竞争和迁移，SQLite 结果不能替代」。本轮改了迁移
  （`0025`）、事务（F-01、F-09）和行锁（`with_for_update`），只在 SQLite 上验过。
  本机无 `psql`、无 docker。生产迁移已成功执行，但那证明 DDL 能跑通，不是并发行为正确。

### 受阻于生产数据状态的验收

（2026-09-08 复核：三项条件均未变化，仍然受阻。）

- 永久删除的确认文案：生产 `business_tasks` 无 ARCHIVED 记录（CANCELLED 120、
  EXPIRED 9、PENDING_ASSIGNMENT 2、PENDING_CONFIRMATION 3），删除按钮不渲染。
- 同步过期时的「无记录」标记：需要 `source_stale` 为真才能观察。
- 稳定房间展开内容、维修/可入住色调：`room_operational_states` 仅 2 条且均为
  NOT_STARTED，无稳定、维修或可入住状态的房间。

### 数据与运营

- ~~403 条历史人工跟进提醒未标记已处理~~ **已完成（用户操作，2026-09-08 核实）**。
  生产 `lifecycle_reminders`：RESOLVED 400、CANCELLED 82、SCHEDULED 43、MANUAL_FOLLOWUP 19。
  「待我关注」现为 3 项，全部是真实的业务任务待确认，无提醒项。剩余 19 条
  MANUAL_FOLLOWUP 因派生任务仍在而被正确排除，不是遗留污染。
- ~~历史数据只读盘点~~ **已完成（2026-09-09，只读）**。报告见
  `docs/reviews/2026-09-09_historical-data-audit.md`。五类异常零命中，引用完整性
  干净；唯一信号是 5 条企业微信出站投递失败（wecom_async_13），系统已重试+重写、
  会话已恢复，未静默丢弃。轻微遗留：3 条 `delivery_retry_pending=true` 陈旧未清，
  不影响送达，不需紧急处理。
- ~~多次部署均未做生产备份~~ **已完成（v1.16.0）**。`.stage/remote.sh` 新增部署前
  备份，失败即中止；已在生产实跑并恢复进独立临时库逐表比对验证。

### 本次核实中新发现

- **「待处理任务 49」会被读成积压，实际不是**。生产 49 条 PENDING 全是
  `lifecycle_send`，`available_at` 最早 2026-09-09 01:00、最晚 2026-09-22，
  **0 条已到期、0 条被锁、attempts 全为 0**——是正常的预约队列。
  但诊断页用告警色徽标显示这个数字，和「联网信息查询待确认」是同一类认知问题：
  数字准确，却没有说明它意味着什么。可考虑区分「已到期未处理」与「排期待发」。
- **`external_requests` 已有 69 行**，确认 v1.15.0 的外部调用记录在生产真实写入，
  那张「永远为空的表」已经不再为空。
- `purged_task_marks` 为 0 行：迁移 0025 建表后生产未发生过清除，符合预期。

### 仓库与本机

- ~~`docs/reviews/` 未纳入版本库~~ **已完成（v1.16.0）**。三份报告与证据脚本已入库，
  附 `README.md` 说明它们是某一时刻的观察而非当前事实。
- ~~本机 8010 旧实例~~ **已停止（2026-09-08）**。它由 launchd 托管
  （`com.rin.homestay-bot`，`KeepAlive`+`RunAtLoad`），直接 kill 会被拉起，
  因此已 `launchctl bootout` 并把 plist 改名为 `.plist.disabled`。
  恢复：`mv ~/Library/LaunchAgents/com.rin.homestay-bot.plist{.disabled,}`
  后 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rin.homestay-bot.plist`。
  该目录下的数据与 `.env` 未做任何改动。

### 部署工具链：门禁已修，但只活在本机

2026-09-08 修复的两处（改动在 `.stage/remote.sh`，**该文件被 .gitignore 排除，
不在版本库里**，故此处留档）：

1. **就绪探测曾长期形同虚设**。判据写的是 `[ "$r" = '{"status":"ok"}' ]`，
   而 v1.15.0 之后生产健康是 `degraded`（回调从未接通，如实反映），这个条件
   再也满足不了：循环跑满 30 轮、白等 90 秒，然后一声不响地继续往下走。
   现改为判「应用在应答」——HTTP 码 200 或 503 且响应体含 `"status"` 字段；
   连接被拒、超时、反代 502、抛栈 500 都不算应答。等不到就 `exit 1`。
   修复后实测第 3 次探测即通过（9 秒，此前 90 秒）。
2. **重建之后没有任何门禁**。整条链路的 `exit 1` 原本全在容器重建之前，
   之后的健康、状态码、错误计数全是 `echo`，一个都不拦——容器起来就崩，
   脚本照样打印 DEPLOY_OK。新增「部署后门禁」：容器 running、RestartCount 为 0、
   容器包版本等于本次 tag、公网 /health 有应答、公网登录页 200，任一不过即
   `exit 1`。整体降级**不拦**（那是当前合法稳态，拦了就永远发不出版本），
   但会打印一行说明，免得降级被误当成本次部署引入的问题。

两处都用假服务器和桩做过判别性验证：就绪判据对「200 健康 / 503 降级 / 502 /
500 抛栈 / 200 但不是本应用 / 连接被拒」六种情形全部判对；门禁在「容器已重启 /
不在 running / 版本是旧的 / 公网无应答 / 登录页 502」五种情形下都返回 1。

**耐久性问题（待用户决定）**：`.gitignore:29-30` 排除了 `run-deploy.command`
和 `.stage/`。敏感值只在 `run-deploy.command:12-13`（生产主机 IP、root、
SSH 私钥路径）；`.stage/remote.sh` 经核查**不含**主机、账号、密钥或任何凭据，
只有服务器端路径、容器名和公开域名 akros.icu。因此可以只把 `remote.sh` 纳入
版本库而继续排除 `run-deploy.command`，一行 `!.stage/remote.sh` 即可。
不改是安全优先，改则备份与门禁逻辑不再只存在于一台机器上。

### ~~第三份审查报告~~ 已全部实施（v1.16.0 + v1.17.0）

`docs/reviews/2026-09-08_deployed-usability-audit-report.md` 的 UX-01～11 全部完成：
UX-07 在 v1.16.0，其余十条在 v1.17.0。四项范围决策由用户一次性确认，实施细节与
证据见 CHANGELOG 的 1.17.0 条目。

## 生产验收结果（2026-09-09，v1.17.0 已部署）

用户授权「开始生产验收」，范围限定为**可逆且无外部副作用**的项。

### 生产数据现状（只读取证，限制验收范围）

- 员工只有 1 个 ADMIN，无 STAFF；任务无 assigned/in_progress/pending_inspection，
  无已归档任务；审批表为空；房间仅 2 间且均 NOT_STARTED。
- **无手动建任务的 UI 路由**——任务只从百居易/生命周期派生，因此没有可丢弃的
  合成对象；写路径要么动真实记录，要么裸写 DB（后者不做）。

### 已完成（只读，真实站点字段核对）

| 项 | 生产实证 |
| --- | --- |
| UX-02 | 任务 #554 详情 2 个表单均带 return_to，值含 status_filter 与 page |
| UX-05 | 客户详情默认无记忆/摘要区，五标签在 |
| UX-06 | 账号页有「返回工作台」→ /employee/admin |
| UX-10 | 知识页表单 return_to 跟随当前视图（page、enabled）|
| UX-11 | 凭证密码 maxlength=128，无 256 |
| UX-07/08/徽标 | 清空开关、外部调用表、「排期待发」均已生效 |

### 已完成（写路径，真实提交且已还原）

- **UX-05**：客户 #1 add→remove 标签「测试专用号」。提交后落到
  `/employee/customers/1?tab=overview`（对应标签页，非裸 URL），标签保存生效；
  取消后 DB 直查确认客户 #1 仍为零标签，全链净变化为零。企业微信客户同步未配置，
  无对外副作用。

## v1.18.0 手动建任务 + 连带生产验收（2026-09-09，已部署）

新增管理员手动建任务后，UX-01 与归档/永久删除流不再阻塞——用它自造可丢弃对象验完即销毁。

- **手动建单**：生产建成任务 #561（maintenance，指派给管理员→已分派），跳转新任务
  详情、描述可见。端到端通过。（注：`data-confirm` 的 `window.confirm` 在自动化
  浏览器默认取消会拦下提交，需覆盖后再提交——真实用户点确认即可。）
- **UX-01 第一部分（具体错误文案）**：待检查态直接 POST /561/ready（无证据）→ PRG
  后页面显示「保洁检查清单尚未全部完成」，通用文案「任务操作未完成」不出现。
- **UX-01 第二部分（隐藏必然被拒的按钮）**：待检查且缺证据时，ready 表单不渲染，
  改显「补齐后才能确认可入住」+「去补齐」锚点。
- **归档/恢复/永久删除详情流**：取消→归档（出现「从归档恢复」+ 永久删除表单）→
  永久删除。DB 直查：task 561 已消失、MANUAL 来源任务 0、验收描述任务 0，净残留
  为零；建单审计 1 条按设计保留（purge 保留审计）。

以上三项此前列为「阻塞于生产数据状态」，现已解除。

### 阻塞于生产数据状态（非拒绝，是条件不具备）

- UX-01（缺证据被拒）：无 pending_inspection 任务，且只有 ADMIN 无 STAFF。
- UX-03（零任务房间）：无到店/离店房间。
- UX-09（审批降级）：审批表为空，且需注入 Hostex 失败——不在生产做。
- 归档/永久删除详情流：无已归档任务。
- UX-02/04/10 的**提交回环**：会改真实任务/房态/知识记录且难干净还原，用户选择
  不动真实数据；以集成测试（POST 回环 303→Location 精确匹配）+ 上述只读字段
  实证为验收依据。

### 明确不做（需单独授权，本轮不在范围）

- UX-09 真实确认下单：创建真实百居易订单与金钱承诺。
- UX-06 真实改密：生产仅此一个 ADMIN，改错会锁死账户。
- 真实客人/员工消息发送。

**未覆盖的验收**：报告中标为「生产 U」的项（真实下单、真实凭证替换、真实照片
存储）仍未在生产验证，本轮一律用合成数据与本地复现，未代做生产写操作。

## v1.19.0 房态与本地流程分清（2026-09-09，已部署 + 生产核对）

实施 `docs/reviews/2026-09-09_room-status-clarity-review.md` 的展示层，并纳入用户
补充的营业节奏与规则。生产真实数据核对：标题「入住安排」、7 间房一屏无折叠、
今日到店/离店房显示客人名+计划条、住宿期内无计划条、无订单房无名、辅助统一
「本系统准备情况：未确认」。

- 报告 §1 七个问题全部处理（主状态做订单事实、辅助记录降为未确认、近期空置改口径、
  首页当前房态改名、房态下拉改「调整本系统记录」、零任务不再说未生成）。
- 用户规则：到店即算上一位退房、今日仅退房过15点默认已退房进入下一轮、同一客人
  连续订单为续住、checkout_observed_on 才算已核验退房、同步过期一律待核实。
- 客人名显示在主状态与日期旁（投影扩客户号/名/实际退房日期）。真名只在生产后台，
  提交/截图一律合成名。
- 计划节奏条 12/15 点高亮当前时段，焦点重获且跨边界时只重新 GET 只读页。
- 展示层，无新增数据库字段/枚举。

**用户决定（2026-09-09，收尾）：延迟退房由管理员操作判断，暂不建「实际事实」层。**
不新增延迟退房到具体时刻、真实到店/退房事实的写回入口与 Hostex 事件跟踪。
诚实边界：因系统不跟踪延迟事实，某房实际获批延迟到晚于 15:00 时，页面在 15:00 后
仍按默认显示「按计划已退房」——这是已接受的取舍；管理员用「调整本系统记录」控件或
自行判断处理。若将来要让页面反映真实延迟/到店，再单独定事实来源后重启该层。

## v1.20.0/1.20.1 住宿条时间轴与订单绑定倒计时（2026-09-09，已部署+生产核对）

实施 `docs/specs/2026-09-09_room-stay-timeline-countdown-spec.md`。只读展示，无迁移/
依赖/外部调用/状态机改动。生产真实数据核对：入住安排标题、11 条倒计时实时计算、
11 条住宿条、22 个客户链接、周转间隔 3 小时、旧孤立姓名已移除、375px 无整页溢出。

### A01–A20 覆盖

- 自动化覆盖：A01/A02/A03/A04/A07/A09/A10/A12/A13（unit `test_room_events`/
  `test_admin_operations_service`）；A04/A05/A06/A07 倒计时格式（browser
  `test_admin_interactions` countdown 组）；A17 无溢出（本地 390 + 生产 375）；
  A20 全量 1523 passed。
- 结构性保证（未各写独立断言）：A11 客户链接三态（stay_guest 宏）、A15 不解析
  LATE_CHECK_OUT（根本不读任务正文）、A18 无 JS 保留绝对时间/链接/区间、
  A19 Jinja 自动转义。
- A14 同步过期事件注记：v1.20.0 遗漏，v1.20.1 补上并加集成断言。
- 未自动化（逻辑已实现，靠代码审查）：A08 跨午夜/长开/恢复单次刷新不叠定时器、
  A16 未保存表单跨节点只提示不刷新——均由单个页面级 setInterval + visibilitychange +
  dirtyForms/showStaleHint 实现，未写端到端计时测试。

### 明确未做（Spec 划定，非漏项）

延迟退房到具体时刻、客人 ETA、真实到店/退房事实写回入口——无可信数据来源，本次
不显示推断值、不新增审批/录入。真实姓名未进入任何提交文件或截图。

## v1.21.0 房间日期网格视觉优化（2026-09-09，已部署 + 生产核对）

实施 `docs/specs/2026-09-09_room-calendar-visual-optimization-spec.md`。日期标题下堆
圆胶囊 → 连续可对齐房间日历。只读展示，无迁移/依赖/API/拖拽/审批。

- 几何（§6）：住宿条按计划 15:00/12:00 半天边界映射为相对内容宽度百分比，日期头与
  轨道共用同一坐标系精确对齐；非重叠订单复用轨道（稳定扫描分配），不再逐单换行成
  楼梯；真实重叠才分轨 + 「时间重叠，需核对」；异常区间不伪造几何。
- 视觉（§5）：白底细边框、双行日期头（跨月首日显月）、今日整列底、日期竖线、6px 圆角
  矩形条、按计划态语义配色（对比度实测 8.0/8.5/6.9/9.9/4.55/6.05:1 均≥4.5）、整条 44px
  原生客户链接、单一横向滚动容器、「查看本房间行程列表」折叠。
- V01–V16：几何/轨道/异常有单测（`test_timeline_geometry.py`），模板状态/空态/键盘有
  集成与浏览器测试，对比度实测记录；全量 1531 passed。
- 生产真实数据核对：7 个日历模块、语义齐全、无旧标记残留、整页无横溢。
- 过程教训：改 CSS 曾用过宽删除区间误删无关规则，已还原并改为逐块精准删除。

## 已完成工作单（2026-09-08）

| 版本 | 内容 |
| --- | --- |
| 1.10.1 | 批量操作按用户看见的范围执行；分派表单回填；手机端表格不再整块消失 |
| 1.11.0 | 任务队列与附加筛选分层；房态时间轴宽度与可信度；稳定房间展开；徽标语义统一 |
| 1.11.1 | 后台视觉打磨：中性色阶归一、语义色 token、消除白卡套白卡 |
| 1.12.0 | 「待我关注」每一项都可处置；任务每个状态都有可落下去的动作；计数收敛单一来源 |
| 1.13.0 | 代码审查九项发现 F-01～F-09 全部修复（含迁移 `0025_purged_task_marks`） |
| 1.14.0 | 前端运营精简三阶段：确认文案跟随动作、保留工作上下文、工作台整合、下一步带去向 |
| 1.14.1 | 工作台与房间视图对来源可信度给出一致结论 |
| 1.15.0 | 外部调用留下可查记录；健康检查不再用对账心跳替 Webhook 作证 |
| 1.15.1 | 修复降级项在诊断页被静默丢弃；每项异常给出含义、处理方法与去处 |

两份审查报告的实施决策与证据记录在各自版本的 CHANGELOG 条目中，不在此重复。

## 房间卡片重设计（2026-09-10，用户已回复开始）

- [x] 核对模板、日期几何及倒计时；补入取消横向滚动要求。
- [x] 实施紧凑卡片、独立分轨、纵向日期分段及原生展开。
- [x] 验证日期几何、安全回归及桌面／手机布局；不提交、不部署。
  4 个相关测试文件合计 76 passed；Ruff、JS 语法与 diff 检查通过。合成数据本地页面核对桌面与 390px 手机，日期模块无横向溢出。未做生产验收。

## v1.23.0 接手部署（2026-09-10）

Codex 只跑了 4 个相关测试文件，全量套件里 `test_admin_assets.py` 两条守护是红的：
两条守的都是 v1.22.0 的旧机制（日期列 `minmax(104px)` + 横向滚动、`--cal-axis`
变量），机制已被纵向分段取代，不是真回归。守护改绑新机制而非删除：

- [x] `test_narrow_room_cards_switch_to_the_short_date_segment`：守住
      `container-type` → 容器查询 → 宽/窄段长这条链路。掉任一环，7 列会静默挤进
      窄卡片——正是「格子完全不可读」的原始根因。
- [x] 网格线对比度守护不再锁定变量名，改为「凡是 `--cal-*` 线条变量都要 ≥1.2:1」。
- [x] 三条回归（去掉 container-type / 段长扩到 18 / 线条调成 #F1F5F9）逐一验证能被抓住。
- [x] 全量 1534 passed、15 skipped；Ruff、mypy 干净。
- [x] 合成数据实测 1440 与 390 两档：`scrollWidth == innerWidth`，日期列最窄
      190px / 107px，无横向溢出。

## v1.23.1 三项收尾（2026-09-10，用户授权解开设计令牌）

- [x] ① 令牌：房间卡片段内 27 处字面色归位。#596579→--muted、#e1e5ed→--line、
      #f5f6fa→--surface-sunk 等属邻近漂移，直接替换；#3448a5 是另起的第二套强调
      色，统一收回 --primary。日历四档条色改用 color-mix 挂主色，仅保留 4 个
      回退值作为唯一声明缝。日期头与轨道之间恢复 --line-strong 一档轴线。
- [x] ② 跨段延续：段边界切开的同一笔订单，两段标注「同一笔，接上段／续下段」；
      title 与 aria-label 共用同一句，超窗（更早开始／延续更晚）措辞与跨段分开。
- [x] ③ 去掉 .cal__scroll 的 tabindex 与随之失效的 :focus-visible；role 与
      aria-label 保留。集成测试里那条「时间轴会横向滚动」的断言同步改绑新事实。
- [x] 六条回归逐一先红后绿；全量 1538 passed，ruff、mypy 干净。
- [x] 桌面 1440 与手机 390 重新实测：无横向溢出，日期列最窄 190px / 107px。

## v1.23.2 日期段等高（2026-09-10）

- [x] 实测定位：日期栏本身三段都是 45px，不一致的是轨道高（176/176/88），
      根因是 --rows 取该段可见条数。
- [x] --rows 下限改为 3；--preview-rows 从模板 style 移回 .cal__segment（它是
      设计常量而非每段数据），值固定为 3；折叠阈值 4 → 3。
- [x] 新增等高守护，先用真实旧行为确认能抓住（264/88/88，各段笔数 3/1/1）。
- [x] 全量 1539 passed；ruff、mypy 干净；手机三段轨道高实测 264/264/264。

## v1.24.0 陈旧「重试在途」标记收敛（2026-09-10）

- [x] 更正：诊断页「待处理任务」徽标拆分在 v1.17.0 就做完了（`pending_due_count`
      拆成「已到期待处理」告警与「排期待发」中性），此前列成待办是照抄了 9/8 的
      旧笔记未核代码。本轮只做 delivery_retry_pending 一项。
- [x] 生产只读取证（不取正文）：三条为消息 52、55、105。55/105 的改写已被受理；
      52 的改写失败，但那次失败已在改写消息 53 上通知过员工（notified=true）。
      三条都是簿记问题，不存在漏通知。`guest_delivery_failure_compensate` 从未入队。
- [x] 根因：闩锁只在 force=True 的终态补偿里清，两条正常出口都没清。
- [x] 修复 `_settle_retry_origin`，接在「重试被受理」与「失败已通知」两处。
- [x] 迁移 0026 收敛存量；离线 SQL 模式跳过（数据迁移需读现有行，生产走在线
      `alembic upgrade head`，见 deploy/start.sh）。
- [x] 五条新测试全部先红后绿；全量 1544 passed，ruff、mypy 干净。

## v1.25.0 未送达看板（2026-09-10）

- [x] 定位边界：诊断页看状态、待关注要人做事。看板放诊断页，唯一要人接手的
      「无人知晓」一档在那里直接告警并给处理方法，不另开页面。
- [x] 按投递链计数而非消息行——生产 5 条失败消息实为 4 次未送达。
- [x] 仓储 `delivery_failure_rollup`：JSON 取值用 SQLAlchemy 下标语法，
      PostgreSQL 走 `->>`、SQLite 走 `json_extract`，一份代码两个方言；
      limit 护栏防表增长后全表扫描，取满置 truncated 并在页面如实说明。
- [x] 投影只含编号/阶段/次数/错误码/时间，正文与客户身份不进视图模型，
      并用一条断言锁住 DeliveryChain 的字段集合，新增字段必须重审边界。
- [x] 四条守护逐一先红后绿。其中「已了结也用告警色」第一次没红——原断言只
      守住了 alert 没守住徽标，补成按投递段落判 badge--danger 后才红。

## v1.26.0 投递失败可诊断性（2026-09-10，真实微信号验收驱动）

- [x] 验收发现：天气问答被 wecom_async_13 拦截 → 改写用兜底 → 重发受理 → 通知管家。
      客人只收到一条（手机截图确认），无重复。看板从 4 条链变 5 条，实时记录到。
- [x] 改写兜底原因落库（此前 25 种拒绝理由被 except 整个吞掉）。
- [x] 被拦正文结构快照，绕开 7 天保留期的证据销毁；只记形态不记正文。
- [x] DeepSeek 传输层记账，含异常路径；台账不取查询串。
- [x] 实现中踩到并修复：提前把 metadata 赋回 message_metadata 会让二者变成同一
      对象，JSON 列不跟踪原地修改，本函数后续元数据全部静默不落库。已由既有两条
      测试抓住，并补了一条端到端落库断言。
- [ ] 未决：wecom_async_13 的根因（格式 vs 联网）仍需一次区分性测试。本次三项
      正是为了让下一次失败能被直接查清，而不必依赖现场复现。

## v1.27.0 联网来源名（2026-09-10）

- [x] 官方文档确认 fail_type 对照：4=会话超48小时、5=会话已关闭、10=用户拒收、
      11=无成员登录、12=消息类型被禁、13=安全限制。代码里 13 触发安全改写的假设
      成立，此前全仓无任何依据记录。
- [x] 根因：_source_display_name 顺序反了，机构名表只在标题为空时才查。
- [x] 域名优先 + 标题需通过「能否当机构名念」判据；长度上限按语种分开。
- [x] 实现中一次自我纠正：12 字上限是中文标定，误杀了英文机构名，被既有测试抓住。
- [ ] 未决：第 1 项（wecom_async_13）根因仍未证实。证据是两次被拦的回复都带来源
      句、两次通过的都没有，但「行数」与「来源句」在天气路径上仍然绑定。下一步用
      一次真实天气提问验证：若通过则闭环，若仍被拦则来源句被排除。

## v1.28.0 去掉来源列举（2026-09-10，用户选 A）

- [x] 六条样本完美分离：110/117/120 带来源句全被拦，113/115/121 不带全送达。
      120 与 121 是同一条消息的严格前缀关系，接近受控对照。
- [x] 页脚只保留查询日期与时效提醒；仍要求确有搜索来源。
- [x] 删除机构名映射与标题判据（v1.27.0 引入，随本次改动成为死代码）。
- [x] 自查抓到：页脚句读变化导致 split_tourism_reply 拆不出收尾，改写器会把收尾
      当正文。模式改为来源子句连同前置逗号可选，兼容存量正文。
- [x] 补陈旧来源过滤的行为级测试——它原先唯一的覆盖是页脚点名，本改动会让它归零。
- [x] 已验证闭环（消息 123，2026-09-10 22:00）：372 字、无来源句、accepted，
      无改写无兜底无管家通知。七条样本判据完美分离——带来源句 3/3 全拦，
      不带 4/4 全过。wecom_async_13 根因确认为该固定句式触发企业微信安全限制。
- [x] 判据精确化：123 正文含「据武汉市气象台预报」仍通过，说明被拦的是
      「主要参考了X等公开信息」这个固定句式，而非「提及来源」本身。

## 待办：机器人编造民宿设施（2026-09-10 真实问答发现）

消息 123 出现「民宿这边：大堂备有薄外套和雨伞，需要随时说；房间已换秋被，觉得凉
可再加一床。」——知识库里没有这些事实，是模型自行编造的**具体设施断言**，客人可能
据此下楼索要。消息 115 也有类似苗头（「我看看当天能不能先寄存」「我好安排钥匙或
门锁信息」），但那还只是含糊承诺，这条是事实性断言。

**已修复（v1.29.0）**。机制一直在链路上（deepseek_client.py 的联网正文清洗），漏的是
判据形态：只认自称词。新增「场所 + 供应/状态动词」判据。真实数据对照：四条生产回复
中三条一字未删，问题那条只删去编造的一句。

## 待决：模型无视已注入的订单上下文（2026-09-10 真实问答发现）

消息 126「我明天下午三点左右到」→ 127「您好，明天下午三点左右到没有问题的。想问
一下您预订的是哪套房源、入住日期和大概几位呢？我这边先帮您确认一下安排。」

**先更正一个曾经的误判**：最初怀疑是订单上下文没注入。实测复现
`load_model_context` 的订单查询后确认：订单 64（《春和景明》8/14–16、status
`crm_test`）确实在上下文里——`crm_test` 不在排除列表。**上下文没丢，是模型没用。**

三个问题：

1. **模型无视已注入的订单**，仍追问「哪套房源、入住日期」，而这些正在它手里。
2. **拿着事实不做核对**：订单是 8/14–16，客人说「明天」（9/11），相差近一个月，
   模型没发现矛盾，反而先答「没有问题的」。
3. **空头承诺**：「我这边先帮您确认一下安排」——任务 0、审批 0、提醒 0、无管家
   通知，没有任何人知道要确认什么。

补充事实：该客户 `customer_context_summaries` 的短／长摘要各仅 5 字符（近乎为空），
`customer_memory_items` 0 条。可用上下文只有那 1 条订单，但它足以发现日期矛盾。

1、2 是模型行为问题，过滤器补不了。可选方向：提示词要求先核对已知订单（无确定性
保证，难验收）；或由系统解析客人提到的日期与订单比对后给出硬提示（可靠，但需要
日期解析，是个真功能）。3 可确定性处理：承诺了动作而系统零任务，要么改文案要么
真建任务。

**结论（2026-09-10 复现验证后）**：

- 问题 1「模型无视订单上下文」**不成立，撤销**。唯一那条订单是 8/14–16、已经过去，
  客人说「明天到」与之矛盾；模型不能假设客人指的就是那条旧单，追问「您这次是几号
  入住」恰恰是正确处理，消息 129 还明说了「我先跟您核对一下」。此前四次判断
  （上下文丢了／模型不知道日期／该加提示词／无视上下文）全部被证伪，根子相同：
  看到现象就推断原因，没有走完链路。要验证「会不会无视**有效**订单」需要一条日期
  覆盖明天的订单，属生产写入；为验证一个尚未观察到的问题去造生产数据不划算，暂不做。
- 问题 2「空头承诺」**已修（v1.31.0）**，127 与 129 两条复现消息均正确处理。
- 问题 3 B 组升级分支仍待验收。

## B 组升级分支验收完成（2026-09-10）

| 场景 | 路径 | 值班人通知 | 调模型 | 任务/客诉 |
| --- | --- | --- | --- | --- |
| 转人工 | 高风险固定文案 | 文本 | 0 | 0 |
| 图片 | 同上，非文本未被忽略 | 文本 | 0 | 0 |
| 客诉 | 安抚 + 分析 | **卡片** | 1 | 客诉记录 3 |
| 紧急 | 安全指令 | 文本 | 0 | 0 |

三个正面结论：高危场景完全不调模型（4–7 秒响应，且不会因模型异常答错安全消息）；
值班人通知按场景分级（客诉是可操作卡片）；客诉草稿必须人工点发送（审计
`complaint.send`），AI 不会自动回复投诉客人。

发现并修复的安全缺陷见 v1.32.0。遗留痕迹：客诉记录 3 只能 cancel 不能删；
会话 1 仍为 HUMAN_ACTIVE（单向闩锁，无回退路径）。

## 2026-09-11 Codex 审查修复（用户已回复“开始修复”）

实施依据：本轮审查发现请求级 CustomerAdminService 实例无法保存跨请求冷却；用户确认修复并执行列出的局部精简。

- [x] 冷却回归：tests/integration/test_customer_repository.py 经 SessionCustomerAdminService 的独立事务复现重复入队；覆盖按客户隔离、十分钟边界及回滚。
- [x] 冷却实现：repositories/customers.py 锁定未合并客户并读取最近 customer_context_refresh 作业创建时间；services/customer_admin_service.py 移除实例字典，在同一事务校验后沿既有队列入队。复用现有表，不增加迁移；生产 PostgreSQL 客户行锁串行化同客户请求。
- [x] 等价收敛：repositories/context.py::_save_memory_candidates 合并重复冲突状态写入；customer_memory_policy.py 去除恒真条件；templates/customers/detail.html 去除恒真包装、纠正审核说明；integrations/tourism.py 删除未使用 logger；emergency_service.py 与上述函数缩短历史叙述注释，保留职责及安全约束。
- [x] 最终验证：相关单元/集成测试、Ruff、mypy、差异自审；不调用真实模型/业务接口，不提交、不部署。SQLite 验证持久化与事务，不作为 PostgreSQL 并发行锁验收。

验证结果：跨请求红测确认旧实现不会拒绝重复请求，修复后通过；记忆冲突四种组合在提交前实现及当前实现均通过。最终相关测试 357 passed（1 条既有 Starlette 弃用警告），修改文件 Ruff 通过，mypy 133 个源码文件通过，git diff --check 通过。业务源码净减少 47 行。未跑全量及 PostgreSQL 并发/生产验收：本次按受影响路径验证，SQLite 只证明跨事务持久化和入队失败后的重试行为。

## v1.35.1 发布（2026-09-11，用户授权提交、推送、部署）

- [x] 更新 pyproject.toml 与 CHANGELOG.md，范围为本轮修复。
- [x] 发布验证：1600 passed、15 skipped、17 warnings；真实外部契约未启用；Ruff 与 mypy（133 个源码文件）通过。
- [x] 在隔离 PostgreSQL 数据库中验证客户行锁串行化、重复拒绝及事务回滚后可重试；临时数据库已删除，未运行业务 worker。
- [x] 核验生产基线：源码 dd2297a，版本 1.35.0，迁移 0026_settle_retry_latch；诊断 revision 2，百居易回调从未收到导致既有 degraded。
- [x] 完整受限权限备份：源码、.env、私有上传及 PostgreSQL custom dump；356 个转储条目，备份文件权限 600。

发布顺序：最终 Ponytail 审查 → 提交并推送 main / v1.35.1 → 仅替换 API → 核对版本、迁移、PostgreSQL 连续运行、健康与登录页面。提交后操作及验收结果记录在本轮会话与受忽略的 .stage 发布日志，避免为记录发布结果再产生一个未部署提交。

## YuMi Windows / Android 客户端（2026-09-12，用户已回复“开始”）

实施依据：`docs/specs/2026-09-12_windows-android-client-spec.md`（R2）。源码基线 `ec74610`。
用户决策：授权在本机安装全套构建工具链；Windows 侧标为未验证，先推进 Android 与共享代码。

### 阶段 A：工具链和双端技术验证

- [x] 复核工作区、AGENTS.md 与 Spec 作用域；核对构建机器条件。
      结论：主机 macOS arm64，Rust、JDK、Android SDK/NDK 全部缺失，无 Windows 构建环境。
- [x] `clients/yumi/tests/webview_fixture.py`：标准库 HTTP fixture，覆盖登录重定向、一次性 CSRF、
      带计数的 POST 与 PRG、inline 私有图片、失效会话返回 200 登录 HTML、403、外域重定向、
      中断传输、中文文件名上传、取消选择、格式拒绝、自动外跳、target=_blank、慢响应、业务 500。
      验证：22 项场景全部通过（scratchpad/verify_fixture.py）。
- [x] `clients/yumi/src-tauri/src/lib.rs`：导航策略 `classify_navigation`、`classify_url`、
      `is_exact_local_error_page` 及 14 个单元测试。**尚未编译，等工具链就绪后运行。**
- [x] Rust 侧工具链：rustup 1.29.1（Homebrew）+ Rust stable **1.98.1**，tauri-cli **2.11.4**。
      `rust-toolchain.toml` 已锁定 1.98.1 与 aarch64-linux-android target。
- [x] JDK 17 与 Android SDK/NDK 全部就位并锁定版本：
      JDK **Temurin 17.0.20.1**、cmdline-tools 12.0、platform-tools 37.0.1、
      platforms;android-36、build-tools **35.0.0**（AGP 8.11 实际要求）与 36.0.0、
      NDK **27.3.13750724**(r27d)、Gradle **8.14.3**、AGP **8.11.0**、Kotlin 1.9.25。
      每个产物都比对官方校验和后才使用（JDK 对 Adoptium API SHA256；
      SDK/NDK 对 Google repository2-3.xml SHA1；Gradle 对官方 .sha256）。
- [x] 最小 Tauri 共享工程建立并编译通过。实测 **tauri 2.11.5**（与 Spec 7.1 假设一致）、
      tauri-build 2.6.3、wry 0.55.1、tao 0.35.3、url 2.5.8。
      `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`、`cargo test` 三项通过，22 个单元测试。
- [x] 导航策略按 Spec 5 重构为「允许来源 = 编译期入口 URL 的来源」，不再硬编码主机名：
      `classify_url_against` 为可测核心，先拒用户信息、再判同源、最后按外部链接处理。
      测试地址只经 `test-backend` 特性 + 编译期 `YUMI_TEST_ENTRY` 注入；
      已实测缺该变量时编译失败，正式构建因此不可能含测试地址（A14 的实现手段）。
- [x] **运行验证（macOS/WKWebView，开发态）**：测试构建加载隔离 fixture，
      入口指向 `/autoredirect`，fixture 日志确认收到 `GET /autoredirect -> 302`，
      客户端日志确认 `http://127.0.0.1 -> AllowInApp`、`https://example.com -> ConfirmExternal`。
      证明 `on_navigation` 对首次加载与重定向都会触发，且无用户手势的自动外跳被拦住。
      **此项不构成 Windows(WebView2) 或 Android(Android WebView) 验收**，macOS 不是交付平台。
- [x] capabilities 隔离：`src-tauri/capabilities/` 目录不存在，配置 `"capabilities": []`，
      未注册任何 invoke 命令，`withGlobalTauri: false`。Android 生成工程建立后需复查是否被模板重新引入。
- [x] **Android release APK 构建成功**，产物校验通过：
      applicationId `icu.akros.yumi`、versionName `0.1.0`、versionCode 1000、
      `minSdkVersion=29`、`targetSdk=36`、`native-code` 仅 `arm64-v8a`、
      `usesCleartextTraffic=false`、**无 debuggable**、
      权限仅 INTERNET（无存储/相册权限，符合 F-04 第 4 条）、
      包内检索不到测试地址而正式入口在 so 内（A14 双重保证成立）。
- [x] 按 Spec 修正生成工程中两处不符（都落在可维护、非生成的文件）：
      `app/build.gradle.kts` minSdk 24 → **29**（Spec 第 1 节 Android 10+）；
      `AndroidManifest.xml` 补 `allowBackup="false"` 并新增
      `res/xml/data_extraction_rules.xml`、`res/xml/backup_rules.xml`
      排除云备份与换机直传（Spec 第 5 节禁止备份迁移认证数据）。已重建 APK 复验生效。
- [ ] 测试构建验证登录、首次改密与普通 GET 导航（需真机或模拟器；当前两者都没有）。
- [ ] 实测带会话的私有图片保存、上传取消、确认对话框与 Android 返回事件。
- [ ] 核对 Spec 第 7.1 节各能力在锁定版本的公开接入点，记录实际符号与平台代码路径。

退出条件：Android 侧关键平台能力有运行证据；Windows 能力标为未验证，不进入“两端技术验证通过”状态。

### Spec 7.1 平台接入点核对结果（Tauri 2.11.5 实测，读代码所得，尚未运行验证）

生成的 Kotlin 位于 `gen/android/app/src/main/java/icu/akros/yumi/generated/`，
该目录被 `app/.gitignore` 的 `/src/main/**/generated` 忽略，每次构建重新生成，
因此不能修改其中任何文件；可维护的适配点只有 `MainActivity.kt`、
`app/build.gradle.kts`、`AndroidManifest.xml` 与 `res/`。

| 能力 | 实测结论 |
| --- | --- |
| 导航策略 | **两端共享**。`RustWebViewClient.shouldOverrideUrlLoading` 与 `RustWebView.loadUrl` 都调用 `Rust.shouldOverride(id, url)`，即 Rust 侧 `on_navigation`。已写好的 `classify_url` 在 Android 上同样生效，无需另写一套。Spec 7.1 把 Android 导航当作独立未知项，实际比预期好。 |
| 新窗口 | `RustWebView` 只设了 `javaScriptCanOpenWindowsAutomatically = true`，**未启用 `setSupportMultipleWindows`**，wry 也**未实现 `onCreateWindow`**。在该配置下 Android WebView 会把 `target=_blank` 当作同一视图内的普通导航处理，因而落入上面的共享策略。Spec 7.1 担心的「`on_new_window` 不支持 Android」不构成缺口，但**此结论来自读代码，必须真机/模拟器复验**。 |
| 返回键 | `WryActivity.handleBackNavigation` 是 `open val`、`onWebViewCreate(webView)` 是 `open fun`，`MainActivity` 可直接覆盖，**不需要 fork 框架**。但默认实现是 `canGoBack() → goBack()` 无条件回退历史，与 Spec F-03「不得走会重放 POST 的回退路径」冲突，**必须覆盖为 false 并自行实现**。 |
| 文件选择 | `RustWebChromeClient.onShowFileChooser` 已完整实现（含权限请求与 ActivityResult 生命周期），F-04 上传侧可直接复用。 |
| 网络错误 | `RustWebViewClient.onReceivedError` 存在且区分 `isForMainFrame`，可用于 F-05 的整页错误判断，不会被子资源错误误触发。 |
| 下载 / 长按保存 | wry **未设置 `DownloadListener`、未占用长按与命中测试**，这三个接入点在 `onWebViewCreate` 中完全空闲，F-04 的「保存图片」可在此实现且不与框架冲突。平台 `CookieManager` 可用（wry 自身也在用），满足 F-04 第 2 条「只从平台 Cookie 管理器读取」。 |

**一个必须记住的约束**：`RustWebChromeClient` 与 `RustWebViewClient` 都是 Kotlin 默认的 `final` 类，
**无法继承**。若将来确需改写其行为，唯一受支持的路径是在 `onWebViewCreate` 里用委托包装，
不能靠继承，也不能改生成文件。

### 阶段 A 已发现的事实（影响实现，需并入后续 Spec 修订）

- 私有附件不带 `Content-Disposition`：`routes/private_files.py::download_private_file` 与
  `routes/properties.py::download_property_qr` 均以 `filename=None` 构造 FileResponse，
  只设置 `media_type`、`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。
  因此原生“保存图片”拿不到服务器给定文件名，必须自行从 URL 末段与 Content-Type 推导，
  并按 Spec F-04 第 5 条清理路径成分与危险字符。Spec 第 2 节未记录这一点。
- HTTP 头只能是 latin-1，中文文件名无法直接放进 `Content-Disposition`；
  真实后台回避该问题的方式正是不发该头，客户端不应假定能从响应头取到中文名。
- 构建环境事实（需写进 INSTALL/交接说明）：本机默认源极慢（adoptium 约 60 KB/s、
  dl.google.com 约 34–546 KB/s、ghcr.io 直接 `HTTP/2 PROTOCOL_ERROR`），
  国内镜像快 45–120 倍。Gradle 的 JVM **不读 `HTTP_PROXY` 环境变量**，
  必须用 `GRADLE_OPTS` 传 `-Dhttp(s).proxyHost/Port`，否则在依赖解析处静默挂死。
  本次用会话级 `GRADLE_USER_HOME` + init 脚本换镜像，
  未修改用户全局 Gradle 配置，也未把镜像地址写进生成工程，可复现性不受影响。
- 安装 Android SDK 必须接受 Google SDK 许可协议，已在用户授权「安装 Android SDK + NDK」
  的范围内代为接受，许可文件位于 `$ANDROID_HOME/licenses`。

### 阶段 B / C

按 Spec 第 8 节执行，阶段 A 退出条件满足后展开。

## Android 实测问题修复（2026-09-13，用户已回复「开始实施修复方案」）

实施依据：`docs/specs/2026-09-13_android-field-test-fixes-spec.md`。

### 先核对 Spec 对交接文档的三处纠正——全部成立

- [x] **返回键：我的交接结论是错的。** `generated/TauriActivity.kt:35` 有
      `override val handleBackNavigation: Boolean = false`，Wry 默认的无条件 `goBack()`
      并未生效。此前只读了 `WryActivity` 就下结论，漏了中间这层。相关推断作废。
- [x] `.topbar` 确为 `rgb(255 255 255 / 82%)` + `backdrop-filter: blur(12px)`，
      顶部内容透出由半透明背景解释，不是 z-index 错误。
- [x] `_calendar_segments` 确实把 `--rows` 取成 `max(条目数, 3)`；宏只有 2 个调用方
      （`room_timeline` 内的 wide/compact 两处）。

### R-01 系统栏 / 刘海 / 键盘（已改，待真机验证）

- [x] `MainActivity.onWebViewCreate`：安全边界改为 systemBars 与 displayCutout
      **逐边取最大值**，IME 与底部栏同样取大值而非相加（避免双重补偿）；
      每次由 inset 重新赋值不累加；补 `ViewCompat.requestApplyInsets` 解决首次派发时序。
- [x] **真机验证：padding 方案被证伪，已改为 margin 并验证通过。**
      设备 vivo V2502DA / Android 16 (API 36) / WebView 151 / density 3.5 / 手势导航。
      - 证伪证据：`setPadding(上 140)` 后诊断页 `innerHeight` 仍是 800 CSS px
        （= 2800 物理 px ÷ 3.5，整屏高度），`visualViewport.offsetTop` 为 0，
        顶栏仍被状态栏压住。padding 没有缩小网页视口。
      - 改 margin 后：`WebView 屏幕坐标 0,140`、`宽高 1260×2660`、
        `innerHeight 760`、`避让方式 margin`、`父容器 ContentFrameLayout`，顶栏完整可见。
      - 键盘避让：弹出时 `WebView 高 1614`、`边距 上 140 / 下 1046`，
        验算 2800−140−1046=1614 吻合，且为单次补偿，无三重补偿。
      - `env(safe-area-inset-*)` 四项均为 0px：页面未声明 viewport-fit=cover，
        不能依赖 CSS 安全区，必须由原生层负责。
- [ ] V01/V02 剩余项：键盘十次显隐后尺寸恢复、横竖屏、刘海横屏、三键导航。
      投屏可读数值但无法可靠驱动软键盘与返回手势，这几项需在手机上直接操作。

### R-03 日历空轨道（已修，已验证）

- [x] **发现与 Spec 冲突并解决**：既有 `test_every_date_segment_is_the_same_height`
      要求各段高度一致（7 天切成 3+3+1 时末段不能矮一截），最少三行正是为此引入。
      Spec 要求「两笔订单不留第三条空轨道」，直接改会打破它。
      两者仅在各段笔数不同时冲突。改为 **`--rows` 取各段可见笔数的最大值**（下限 1），
      笔数都少时一起收紧、笔数不齐时一起对齐，两个要求同时满足。
- [x] `app.css` 折叠态由固定 `--preview-rows` 改为 `min(--rows, --preview-rows)`，
      否则只改模板不生效（Spec 已点明这一点）。
- [x] 新增 5 条 browser 回归（1/2 笔精确占行、>3 笔折叠封顶且展开铺开、空日历保留文案高度、
      桌面宽屏同规则）。先复现再修：红测显示 1 笔和 2 笔都占 264px（3×88）。
- [x] 验证：browser 31 passed、资源单测 15 passed、相关 69 passed；
      `src/` 净改动 19 行。mypy 回到改动前的 2 个既有错误，未新增。

### 诊断基线设施（Spec 第 3 节）

- [x] `clients/yumi/ui/diagnostics.html`：自包含测量页，不联网、无客户数据。
      含视口/DPR/visualViewport、根元素字号与 10rem 实测标尺、`env()` 安全区、
      与 `.topbar` 同构的 sticky 探针、键盘测试输入框、可回传的数值汇总。
- [x] `lib.rs` 新增 `diagnostics` 特性：入口改为打包本地页；启动时用
      `asset_resolver` 自检页面是否真的打进产物（缺页会直接报错而不是显示空白）。
- [x] `MainActivity` 注入原生测量（机型/API/WebView 版本/density/**fontScale**/
      导航方式/WebView 几何/各类 inset/可见窗口边界）。单向原生→网页，未注册任何
      invoke 命令，capabilities 仍为空。
- [x] 门控改为「当前页面就是那个打包诊断页」而非 debuggable 标志：业务页面来自 https
      远程来源，永不命中本地路径，因此即使进正式包也不会向业务页面注入。
- [x] Rust：fmt / clippy(-D warnings，正式与诊断两种配置) / 25 个单元测试全过。

### 本轮新增的工具链事实

- **`cargo tauri android build` 会主动剥掉 `app/build.gradle.kts` 里的
  `applicationIdSuffix`**（实测：加入后构建，文件哈希变化且该行消失），
  但保留 `versionNameSuffix`、`minSdk` 与注释。因此测试包身份隔离不能走这条路，
  需改用 Tauri 侧配置；而改 Tauri identifier 又会牵动 Kotlin namespace 与 JNI 入口
  （原 Spec R2 第 5 节已警告），此项留待专门处理。
- 日历的宽/紧凑切换用的是 **`@container (max-width: 620px)`**（容器为
  `.room-operation-card` 的 `container-type: inline-size`），不是媒体查询。
  测试夹具不套这层容器时紧凑布局恒为 `display:none`，量到的高度恒为 0。
- **Tauri 把 `frontendDist` 资源压缩后嵌入二进制**：用 `strings` 搜文件名会搜到
  代码里的字符串字面量而非资源键，据此判断「资源没打包」是错的。
  可靠判据是运行期 `asset_resolver`。

### 待办

- [x] **R-02 显示比例：实测无缩放异常，按 Spec 第 5 节第一条不改 WebView 缩放。**
      `系统 fontScale = 1`（默认未放大）、`visualViewport.scale = 1`、
      根元素 `font-size 16px`（浏览器默认）、`innerWidth 360 CSS px`、无整页横向溢出。
      1260 物理 px ÷ 3.5 = 360，属小屏手机的正常视口宽度。用户感知的「偏大」
      来自 3.5 倍 DPR 下的物理字号，不是缩放故障；应作为版式密度问题处理。
- [x] **R-02 顶部遮盖：已修并验证。** `.topbar` 由 `rgb(255 255 255 / 82%)` +
      `backdrop-filter: saturate(180%) blur(12px)` 改为实色 `var(--surface)`（即 #fff，同色不变观感），
      并移除两条 backdrop-filter。sticky、z-index: 20、栅格布局、padding 全部保留。
      新增 2 条回归：顶栏背景 alpha 必须为 1；抽屉(40) > 遮罩(30) > 顶栏(20) 层级不被破坏。
      按 Spec 第 8 节只断言计算样式与层级数值，不做截图像素断言。
      红测先复现 alpha = 0.82。改后 browser 33 passed、资源单测 15 passed、
      相关面 350 passed / 1 skipped（跳过的是需显式启用的 DeepSeek 契约测试）。
      全仓 app.css 已无其他 `82%` 或 `backdrop-filter` 残留，改动闭合。

  **注意**：网页改动要经后端单独发布才会到达生产 APK，重新打 APK 不会让线上样式更新
  （Spec 第 8 节第 3 条）。当前改动仅在本地验证，未部署。
- [ ] 原阶段 A 未完成项（返回、target=_blank、外链确认、本地错误页接线、图片保存、
      测试身份、图标、Windows）按 Spec 第 7 节处置，不与界面修复混报完成。

## 阶段 A 剩余能力实现（2026-09-14，用户回复「全部做完」）

Spec 依据：`docs/specs/2026-09-12_windows-android-client-spec.md` 第 7 节表格。
本轮只实现与本机验证，**未部署、未提交、未发包给员工**。

- [x] **图标统一**：`cargo tauri icon` 重生成，Android mipmap 不再是 Tauri 模板默认图，
      与桌面侧同源（当前仍为占位图，待用户提供正式品牌图后重跑同一条命令）。
- [x] **返回键（F-03）**：`MainActivity.installBackHandling` 用 OnBackPressedDispatcher 接管，
      顺序为 键盘 → 错误界面 → 可回退历史 → 根页面确认退出。
      `TauriActivity.handleBackNavigation = false` 已关掉 Wry 默认回退，不存在双重处理。
      POST 不重放由 `SafeWebViewClient.onFormResubmission` 显式拒绝（不依赖 Android 默认值）。
      网页抽屉交由页面自己关闭，原生层不猜 DOM 状态。
- [x] **连接错误页（F-05）**：做成**原生覆盖层**而非导航到打包 HTML。
      理由：导航到本地资源就要为本地协议在导航策略上开口子，与 Spec 第 5 节
      「来自远程页的任意本地协议导航一律拒绝」冲突；原生绘制完全不产生导航。
      重试只 GET 固定工作台入口，地址存于 strings.xml，由 Rust 单测
      `android资源里的入口地址与常量一致` 锁定与 `WORKSTATION_URL` 不分叉（已做反向验证）。
- [x] **外链确认（F-03）**：`lib.rs` 接入 tauri-plugin-dialog / opener，
      `ConfirmExternal` 先拦下再弹含目标域名的原生确认，用户不操作则不外跳。
      自动重定向重复提示由 `should_prompt_external` 抑制（5 秒窗口，2 条单测覆盖跨目标与过期）。
      两个插件只在 Rust 侧调用，capabilities 仍为空，远程网页无法经 IPC 触达。
- [x] **图片保存（F-04）**：`ImageSaver` + 长按命中测试。wry 未占用长按与 DownloadListener，
      属空闲扩展点，无需包装框架对象。
      顺序为**先下载校验、再让用户选位置**：反过来会在用户选中的位置留下空文件或登录页 HTML。
      逐跳同源、Cookie 只取自平台 CookieManager、非 2xx 与非图片类型一律拒绝、
      文件名为中性时间戳（不沿用私有附件标识）、只创建新文档不覆盖、失败只删本次临时文件。
- [x] **测试身份隔离（A14）**：实测 `cargo tauri android build` **会重写
      `app/build.gradle.kts` 并精准删掉 `applicationIdSuffix`**（同文件里的 minSdk、
      versionNameSuffix 和注释都保留）。改为放进独立的 `app/identity.gradle` 再 apply 进来，
      该写法在重写后留存。验证：debug 包 applicationId = `icu.akros.yumi.test`，
      而 Activity 仍为 `icu.akros.yumi.MainActivity`（namespace 未动，JNI 符号完好）。
- [x] **诊断产物与正式产物分离**：新增 `ui-diagnostics/` 与
      `src-tauri/tauri.diagnostics.conf.json`，诊断构建换 frontendDist。
      复查正式包：诊断页正文（「顶栏探针」）在 dex 与 so 中均已不存在。
- [x] **Kotlin 安全边界单测**：新增 `app/src/test/.../SecurityBoundaryTest.kt`，
      5 项通过（诊断注入门控 3 项 + 图片保存同源判定 2 项）。
      已做反向验证：把门控改成前缀匹配后「业务页面与伪造地址一律不注入」立即失败。

**遗留与已知偏差（不得当作已解决）**：

- 正式包 dex 仍含 `diagnostics.html` 与 `__yumiNative` 字符串：Kotlin 侧是运行时门控而非构建期剔除。
  该页面在正式构建中既不在打包资源里、也被导航策略拒绝，因此不可达；
  上述 Kotlin 单测证明门控不会命中任何业务地址。但「字符串不在包里」这一条并未达成。
- 本轮所有 Android 行为（返回键、错误界面、图片保存、外链确认）**只有编译与单测证据，
  尚无真机运行证据**。必须真机验证后才能计入 Spec 第 9 节验收。
- 测试签名密钥因临时目录被清理而丢失，已重新生成并改放
  `clients/yumi/.local/`（已 gitignore）。**新签名与此前发出的 b1–b5 不同，
  安装 b6 前必须先卸载旧测试包**。这不是 Spec 6.2 的正式发布密钥。
- Windows 侧一切仍为未验证。

## 阶段 A 收尾 Spec 实施（2026-09-15，用户回复「开整直接做完」）

依据 `docs/specs/2026-09-15_android-phase-a-closure-spec.md`。C01–C09 九项全部先核实再修。
本轮未部署、未提交、未发包给员工。

### 逐项处置

- [x] **C07 日历分段（先核实，结论成立）**：`--rows` 实为 `peak.rows`（跨段取最大），
      与我 09-14 报告里写的「真实行数」不符。根因是我为「分段视觉等高」在实现注释里
      自行做了产品取舍。已删除 peak 预扫描，改为各段自己的 `visible.bars | length`（下限 1）。
      旧测试 `test_every_date_segment_is_the_same_height` 把「等高」写成通过标准，
      已改判据为「每段各自吻合」并保留其真实 7 天分段夹具；另加 3 条不均匀分段红测
      （[1,3] / [1,4,0] / 展开态）。先红后绿：1 笔的段原为 264px，修后 88px。
- [x] **C04 来源分叉**：`build.rs` 现按构建模式算出唯一入口，经 `cargo:rustc-env`
      注入 Rust，并生成 `values/generated_entry.xml` 给 Android。两侧同源，构建期即不可能分叉。
      跨语言测试改为**无条件**校验（旧版 `cfg(not(test-backend))` 恰好漏掉真正会分叉的场景）。
- [x] **C05 身份隔离**：`identity.gradle` 改读 `build-mode.properties`，
      非 production 模式（含 release）一律加 `.test`。实测 diagnostics-release 与
      isolated-test-release 的 applicationId 均为 `icu.akros.yumi.test`，
      Activity 仍为 `icu.akros.yumi.MainActivity`（namespace 未动，JNI 完好）。
- [x] **C09 诊断隔离**：诊断采集与注入移入 `src/diagnostics/java`，
      非诊断模式编译 `src/nodiagnostics/java` 的同接口空实现。
      正式包复查：`diagnostics.html`、`__yumiNative`、`DiagnosticsGate`、`避让方式`、
      `顶栏探针` **在 dex 中全部消失**，只剩空实现。09-14 遗留的那条缺口已关闭。
- [x] **C01 保存串单**：新增 `SaveSession`，单一在途操作 + 自增 ID 匹配回调，
      忙碌时第二次长按只提示不排队；`onDestroy` 作废在途操作并清理临时文件。
- [x] **C02 结果处理**：`parseResult` 现要求 `RESULT_OK`；`commit` 失败时
      用 `DocumentsContract.deleteDocument` 清理本次新建的目标，删不掉则如实提示
      「可能留有不完整文件」，不谎称未写入。
- [x] **C03 内容校验**：核对 `Content-Length` 与实收字节；用 `BitmapFactory`
      的 `inJustDecodeBounds` 验证真实可解码（不分配位图，不自写格式解析器）。
      上限取服务端**允许配置的最大值 25 MiB**（`config.py::private_upload_max_bytes`
      的 `le` 约束），不是默认 10 MiB——按默认值设限会让调高配置后的合法附件存不下。
      **我最初凭空写了 12 MiB，是读了真实配置后改正的。**
- [x] **C06 返回保护**：可回退时不再直接 `goBack`。原生层看不到历史条目的 HTTP 方法，
      `WebBackForwardList` 只给 URL，无法证明上一页是安全 GET。改为原生确认离开
      并提示未保存内容会丢失，确认后 GET 当前构建的工作台入口。
- [x] **C08 外链在途互斥**：`ExternalPromptState` 把「在途互斥」与「五秒去重」分开。
      新增测试证明去重窗口拦不住不同 URL 的连续跳转，必须靠在途互斥。
- [x] **跨语言策略差异**：Kotlin `isSaveable` 补齐 userInfo 拒绝，与 Rust 一致；
      测试新增 3 个 userInfo 用例。

### 验证结果

| 项 | 结果 |
| --- | --- |
| Rust fmt / clippy / test | 通过 / 0 问题 / **29 项** |
| Kotlin（production 模式） | 2 项（诊断测试按设计被排除） |
| Kotlin（diagnostics 模式） | **5 项**（条件源码集生效） |
| 浏览器回归 | **36 项** |
| 资源单测 | 15 项 |
| 构建组合 | production-release / isolated-test-release / diagnostics-release 身份与来源均正确 |

构建期防线做了反向验证，三种错误组合均被拒绝：缺 `YUMI_TEST_ENTRY`、
`test-backend` 与 `diagnostics` 同时启用、测试入口填成生产地址。

### 仍未完成

- **B5 真实业务联调与真机验收整体未做**：A04/A07 需接真实路由、`AdminAuthService`、
  真实 CSRF 服务与隔离数据库，并让设备访问隔离实例。本轮全部改动只有编译与单测证据，
  **没有任何真机运行证据**，不得计入 Spec 第 9 节验收。
- 返回键、错误界面、图片保存、外链确认、`target=_blank` 的真机表现仍未验证。
- 网页侧改动需后端单独发布才会到生产，当前仅本地验证。
- Windows 侧保持未验证。
- 正式发布 keystore 仍未生成；当前为测试签名。

## 数据库清理可靠性与 RAG 检索（2026-09-21，用户回复「英文先不管，a，一次跑完」）

依据 `docs/specs/2026-09-21_database-optimization-spec.md`（R4）。PG 验收走路径（c）：
本机没有 Docker 或 Postgres，不安装、不推分支，PG 用例和 CI 步骤只写好待审。
本轮不提交、不推送、不部署。

### A：清理可靠性（含 D1/D2）

- [x] A1 有界去重键：`task_page_service.py::build_attachment_cleanup_dedupe_key`，
      自动清理和人工批量删除共用；单条删除的 `task-purge:<id>` 不变。
- [x] A2 有界清理：`retention.py::purge/purge_archived_tasks` 每次只处理一批，不自行提交；
      归档清理用 `FOR UPDATE SKIP LOCKED` + `DELETE … RETURNING`；
      `application.py::_run_retention_round/_run_retention_phase` 每批一个短事务，
      两阶段互不影响，每阶段每轮最多 20 批，整轮固定一个 now；
      失败日志不带 exc_info，避免把 SQL 参数写进日志。
- [x] 删除 `delete_file` 与 `_run_retention_loop` 的 `storage` 参数。
- [x] D1 人工批量删除：`operations.py::require_purgeable` 按编号升序 `FOR UPDATE` 并用
      `populate_existing`；`purge_selected` 带归档条件 `RETURNING`，删除集合不一致则整批失败。
- [x] D2 墓碑：`operations.py::mark_purged_many` 原生 upsert（时间只前移），三个删除入口共用；
      `_purged_mark` 过期清理改为带条件的 DELETE。
- [x] A3 SQLite 回归与变异验证：12 处关键保护逐一删除后都有测试失败。
- [x] A3 PG 用例 `tests/integration/test_retention_postgresql.py` 与 CI 隔离 PG 步骤已写好。
      **PostgreSQL 未验收**：T1/T9/T12/T17 未执行。
- [ ] A4 线上基线：没有生产只读授权，未采集。

### C：RAG 检索（C0/C1；C2 未启动）

- [x] C0 评估集：校准集 40 条由 Claude 编写，`tests/fixtures/knowledge_retrieval_cases.json`；
      留出集 40 条由 Codex（gpt-5.6-sol）独立编写，`knowledge_retrieval_holdout.json`，
      sha256 `1605a5e9…3dc8b`，在 C1 动手前冻结。用户默认模型 gpt-6-astra 需要更新的
      Codex CLI（本机 0.152.1），这次运行单独指定 `-m gpt-5.6-sol`，没有改用户配置。
      留出集单独成一个文件（Spec 原写同一文件），这样作者边界清楚。
- [x] 基线：`knowledge_retrieval_baseline.json`，在改动前的源码上记录；放行按生产口径
      计算（被判为专属问题且证据门通过）。
- [x] C1 `knowledge_service.py`：共享主题别名 `PROPERTY_TOPICS`（15 个主题，多义词只用于
      检索排序）；问题虚词过滤；整条问答入证，放不进预算就整条跳过并计数（`retrieve_detailed`）。
- [x] C1 `deepseek_client.py`：证据门按主题逐一找本店范围内的支撑句，多主题缺一个就不算已覆盖，
      分类和关键词不作证；回复里的免费说法和数字要有证据，否则退回「尚未确认」，且不生成 FAQ 候选。
- [x] C1 `answer_policy.py::is_property_specific` 复用主题别名。
- [x] 变异验证：10 处关键保护逐一删除后都有测试失败。
- 兜底文案仍然只有中文（用户：英文先不管）。

| 指标 | 校准 基线→C1 | 留出 基线→C1（只跑一次） |
| --- | --- | --- |
| Recall@3（目标 ≥0.90） | 0.828 → 1.000 | 0.935 → 0.935 |
| 证据完整 | 0.833 → 1.000 | 0.828 → 1.000 |
| 证据门判断准确率 | 0.375 → 0.925 | 0.400 → 0.550 |
| 错误放行（目标 0） | 1 → 0 | 2 → 2 |
| 模拟回复判定准确率 | 0.778 → 1.000 | 0.333 → 0.833 |
| 隔离（目标 100%） | 1.000 → 1.000 | 0.950 → 0.950 |
| 边界：知识不放行实时问题 | 1.000 → 1.000 | 1.000 → 1.000 |
| 检索耗时 P50/P95（含 SQLite 读取，合成规模） | 0.46/0.64 ms | 0.43/0.72 ms |

留出集未达标，按 strict xfail 记录，门槛不变：
- hold-iso-31、hold-iso-35（错误放行）：「路口」「桥边」「商户」不在周边措辞词表里，
  周边商户的句子被当成本店早餐证据。真缺陷，未修。
- hold-iso-35、hold-bound-39（隔离）：Codex 把已启用的周边商户信息和静态价格标为禁止入证，
  本评估的隔离口径是「停用、旧答案、候选草稿」。口径不一致，需要用户确认。
- 保守漏判（共 16 条，全部退回「尚未确认」，方向安全）：同义说法不在别名表
  （车搁哪儿、甩洗、爬楼、抽两口、降温设备、Can I park、lift）；主题不在表里
  （晚到入住、儿童用品、空调、屋顶露台）。这满足 C2 的准入条件「C1 后仍有成组同义漏检」。

范围外发现（未修）：「今晚还有空房吗」不被交易分类或房态强制规则识别（词表里没有「空房」），
属于交易策略，C 阶段不改。

### 验收状态

| 项 | 状态 |
| --- | --- |
| T2–T8、T10、T11、T13–T16、T18 | SQLite 通过 |
| T1、T9、T12、T17 | PG 用例已写好，**未执行（PostgreSQL 未验收）** |
| K1–K9 | 本地通过；K2 只验证了证据门，英文兜底文案未改 |
| K10–K12（C2） | 未启动 |
| 真实模型、生产 | 未验证、未采集 |
| 全量 pytest | 1685 通过 / 24 跳过（15 项真实契约 + 9 项 PG）/ 1 xfail |

### Codex 交接审查补修：C1 证据来源（2026-09-21）

用户已明确“开始”；依据 Spec 9.4.1，只修问题标题作证、否定免费与问句数字作证三条路径及直接相关的范围校验。上表是 Claude 历史交付记录，K1～K9 不能视为全部验收通过：公开留出集仍有已知失败，PG/真实模型亦未验收。

- [x] 核对三条缺陷与所有调用方，确认现有 `respond` 测试可复用。
- [x] 写端到端回归并确认红测：6 个拦截场景修复前全部失败；最终共 12 个回归场景。
- [x] 修复共享证据判断，保留真实答案、周边问答和明确否定的正常回复。
- [x] 运行受影响验证集与静态检查：248 passed / 1 xfailed；Ruff 通过，mypy 133 个源码文件通过。公开留出集错误放行 2 → 0，隔离仍为 0.950。
- [x] 更新交接第 12 节；未提交、未推送、未部署。独立留出/PG/真实模型仍未验收，保留隔离失败标记。

### 复核 Codex §9.4.1 补修并修复放行回归（2026-09-21，用户回复「开始修」）

- [x] 复核：§9.4.1 的问句数字、周边标题修复成立；答案判据与否定窗口过宽，引入 4 个错误放行（入住字眼、数字+单位当距离、远端否定、被子量词）。
- [x] Spec §9.4.2 补记范围；`deepseek_client.py::_ANSWER_TOPIC_PATTERNS/_CLOCK_TIME/_FREE_NEGATION_PATTERN` 收窄。
- [x] 4 条回归测试：换回旧判据时失败原因是「被放行」，修复后通过。
- [x] 全量 1701 通过 / 24 跳过 / 1 xfail；ruff、mypy、diff-check 通过。
- 保留：距离证据不核对目的地（交接前已有）；中文距离问题走联网旅游搜索，不经知识证据门。

### 收尾补齐（2026-09-22，用户回复「全部做完」）

- [x] A4 只读基线：无积压，旧缺陷未触发，生产知识库为 0 条（Spec §7.2）。
- [x] 隔离剔除、距离核对目的地、「空房」识别、英文兜底（Spec §9.4.3）。
- [x] PostgreSQL 验收：CI 隔离 PG 18 通过 / 0 跳过。
- [x] 第二套留出集（Codex）冻结并记录 C2 前基线（只看汇总）。
- [ ] 1.37.0 合并 main、打标签、推送、部署与线上核对。
- [ ] C2：硅基流动 bge-m3，普通表加 Python 精确计算，默认关闭。
- 跳过：真实模型验收（用户决定）。
- [x] 1.37.0 已部署并线上核对（见交接 §15.1）。
- [x] C2 代码与测试完成（1.38.0，默认关闭）；待 key 后调参、验收、开启。

### 2026-09-22 Codex C2 审查补修

- [x] 核对代码与第二套失败，更新本轮 Spec 边界。
- [x] 补向量响应、安全门、评估回归并确认红测：7 个异常向量批次、2 个安全场景及评估缓存计数。
- [x] 修共享边界与评估缓存/超时/回退计数；第二套公开回归错误放行 1 → 0，隔离 0.975 → 1.000。
- [x] 共用交易分类触及广泛调用，最终全量离线验证 1751 passed / 24 skipped / 12 warnings；15 项真实契约与 9 项本机 PG 跳过。Ruff 通过、mypy 134 个源码文件通过。交接第 10 节记录最新边界。
- [x] 全量连接清理警告专项核对：本轮相关 4 个测试文件以线程异常/SAWarning 视为错误运行，149 passed、无警告；未重跑无关全量。
- 外部门禁：真实向量与延迟、新独立留出、生产页面/开启未执行；未授权提交推送部署。

### 1.38.1：复核 Codex 补修并校正、准备 C2 验收（2026-09-22，用户回复「全部做完，生产也做」）

- [x] 复核 Codex §9.5.2：工作区、Spec 与交接一致，验证可复现；改动全为收紧。
- [x] 校正（Spec §9.5.3）：收费证据允许同一问答的另一句、认 yuan/¥，费用句点名他题不作证；英文问房价收窄，排除 room service。
- [x] Spec 结构整理：9.5.1 / 9.5.2 / 9.5.3。
- [x] 新增语义检索校准集 calibration_v2（30 条，关键词模式 Recall@3 0.682）。
- [x] 第三套独立留出集（Codex 编写，40 条，已冻结，未跑过评估）。
- [x] 1.38.1 发布与线上核对（备份复验、版本、迁移、日志、公网健康）。
- 阻塞：真实向量调参与延迟需要 key；开启需要管理员密码；生产知识库需要真实内容。

### 知识证据门属性校验（2026-09-23，用户回复「一次性做完」）

- [x] 四项产品决策确认：审核原文优先、跨语言缺失回未确认、同话题最多反问一次、第四套留出集由新会话编写。
- [x] 红测先行：三条已复现的错误输出（10℃、6 p.m. 安静时段、无麸质早餐）在真实 respond 链路上失败。
- [x] 新增 `knowledge_evidence_policy.py`：主题 + 所问属性的证据计划，最少完整审核答案，指令夹带与冲突答案保守处理。
- [x] 主题清单补齐 13 项；英文 check in/out 归入入住退房。
- [x] 静态分支跳过精炼；交易金额缺实时依据转员工确认。
- [x] 评估改用真实 respond 出口；受支持回答被审核原文取代不计为失败。
- [x] 五项变异验证全部被测试抓到。
- [x] 最终验证：全量 1780 passed / 24 skipped；Ruff、mypy 135 文件、diff-check 通过。
- [x] 第四套独立留出集 `holdout_v4`（独立会话编写，40 条，未读证据门实现与 Spec §4/§11）：已在 `CASE_FILES` 登记，`--only holdout_v4` 可出报告；sha256 `dc5af4c3f1f1a26d5e430ea0fcd7e6e64b7bdbf18a995801e7544bbbc17dc715`（`tests/fixtures/knowledge_retrieval_holdout_v4.json`，103720 字节）。
- [ ] 第四套留出集的验收：在冻结版本上跑纯关键词评估并按 Spec §9 比对安全项与回答率（本会话未跑 `--semantic`）。
- [ ] 版本号、CHANGELOG、提交、发布与生产验收：需用户单独授权。

### holdout_v4 编写与一次性评估（2026-09-23）

- [x] 新会话按说明书独立编写 40 条，结构、分布、去重核对通过；评估脚本只新增登记。
- [x] 三条与已确认决策冲突的标注在跑评估前修正并记入 notes；冻结 sha256 bbe0ec58…8767。
- [x] 一次性评估：纯关键词 Recall@3 0.611，关键词 + 语义 0.944，隔离 1.000，查询 P95 304 ms。
- [x] 修复周边范围漏洞（巷口、路口等指路说法与「与本店无关」声明），新增回归测试；隔离 0.975 → 1.000。
- [ ] 未达标：错误放行 2（烘干能力、代洗服务，属主题对上、对象没对上的已知上限）。语义检索继续保持关闭。
- [ ] holdout_v4 已转为回归集；再证明泛化需要第五套。
- [x] 修复 holdout_v4 暴露的四处失败（周边指路说法、烘干、代洗、夜间供应），六套集错误放行与隔离全部达标。
- [x] 修复后验证：全量 1784 passed / 24 skipped；Ruff、mypy 135 文件、diff-check、五项变异验证通过。
- [x] 1.39.0 发布并部署：CI 通过、备份复验、容器包版本 1.39.0、迁移仍为 0027、重启 0 次、日志异常 0、公网登录页 200。
- [ ] `/health` 的 degraded 组件明细需管理员登录后台核对（既有稳态，原因记为百居易回调未接通）。

### 2026-09-23 证据门审查补修（已授权开始）

- [x] 更新补修 Spec，复现五项偏差；新增 7 项回归先红后绿。
- [x] 最小修改共享规则及评估，保留正常回答。
- [x] 最终离线验证与交接更新；全量 1790 passed / 1 failed / 24 skipped，Ruff、mypy 122 文件、diff-check 通过。无提交推送部署、无生产访问。
- [x] 严格评估暴露的 cal-long-01 延迟退房规则错配已按用户“直接修复”授权补修；明确延迟退房规则才可作证，普通退房时间不再替代，冻结样本及门禁保持不变。
- [x] 补修新增 3 项回归先红后绿，相关 45 项测试通过；Ruff、mypy 122 文件通过。
- [x] 补修最终全量离线验证与交接结果更新：1794 passed / 24 skipped，58.62 秒；无失败。六套关键词负样本未获确认数均为 0，calibration 回复断言正确率 1.000；未运行真实外部服务、PostgreSQL 或生产验收。

### 知识草稿停用导入（2026-09-23，用户回复「直接开始」「执行」）

- [x] 核实交接对现有代码的描述；新增一次性导入工具与 18 项测试，真实 40 条草稿本地验证通过。
- [x] 1.39.6 发布：全量 1813 passed / 24 skipped，CI 通过，部署前备份可读性复验通过。
- [x] 生产导入：预览新建 40 / 冲突 0 后执行；共 40 条、启用 0、审计 40、客人检索可见 0。
- [ ] 管理员登录后台逐条审核：补全 17 条「待填写」，确认后逐条启用。

### 登录「表单令牌无效或已使用」修复（2026-09-23，用户回复「直接修」）

- [x] 生产日志与令牌表只读排查：多次刷新后提交均 409，40 分钟内只签发 1 个令牌且未被消费；新会话下发的 Cookie 正常（311 字节）。登录令牌代码自 09-08 起未改，非本次部署引入。
- [x] 修复：复用会话缓存令牌前先向服务端核对，失效则重签；令牌不匹配时返回带新令牌的登录页（仍 409、不做认证、与登录页共用限速）。
- [x] 新增 4 项测试，原有登录安全用例全部保持；全量 1817 passed / 24 skipped，CI 通过。
- [x] 1.39.7 部署：备份复验通过；生产以伪造令牌验证，返回 409 与带新令牌的提示页。
- [x] 用户用真实账号登录成功（2026-09-23）。

### 2026-09-24 回复链审查补修（用户「直接修」）

- [x] 核对真实接管、失败通知与开场白调用链；在分段 Spec §6 记录补修范围。
- [x] 补最小失败回归，修复通知定位、新接管续发及独立标题开场白；先红后绿。
- [x] 最终离线全量 1875 passed / 24 skipped / 9 warnings（61.33 秒）；Ruff、mypy（2 个修改源码文件）、diff-check 通过。PostgreSQL 与真实外部服务未验收；未提交推送部署。

### 2026-09-24 发布 1.39.11（用户「提交推送部署」）

- [x] 核实线上基线、补版本号、CHANGELOG 与发布变更记录。
- [x] 提交/推送前 Ponytail 审查通过；92cfc65 / v1.39.11 已推送，两个 CI success（含 PostgreSQL 18 passed）。
- [x] 备份解析 366 项；首次权限错误已修复并重建 API，独立运行态验收通过；迁移 0027，PostgreSQL 未重启，健康仍为原有 degraded；完整记录见 docs/releases/1.39.11.md。

### 提示词审计修复（2026-09-24，用户回复「开始」「授权」「2」）

依据 `/claude-api prompt-audit` 的审计报告（目标模型 deepseek-v4-flash）。分支 `fix/prompt-audit`，基于 1.39.11。

- [x] 主提示词：去掉要求调用不存在的旅游联网搜索工具；工具路由改为「本轮有查询工具时先调用再回答，以工具结果为准」，时效信息无查询结果时不给具体数值。
- [x] 房态判读规则（stay_available、days 逐晚不含退房日）移入 `search_availability` 描述；三个工具描述补足返回内容与边界，参考价工具注明「客人询问房价时调用」。
- [x] 精炼与旅游提示词去掉天气开场白要求，开场白只由 `_warm_weather_reply` 负责。
- [x] 客诉草稿删除三句由代码覆盖的字段类型要求。
- [x] 项目 AGENTS.md 去掉本机绝对路径；文档例外补上 `docs/releases/` 发布变更记录。
- [x] **撤回**：客诉与 FAQ 草稿改由 pydantic schema 生成字段清单。真实 DeepSeek 下客诉草稿缺 5 个必填字段、校验失败，恢复手写字段清单。
- [x] 真实 DeepSeek 前后对比（生产 API 容器临时目录，只读配置，工具用假数据，不写库不发消息，结束后已清理）：
  - 时效未分流问题：修改前 6 次中 2 次给出无依据的具体用时或安排；修改后 9 次中 1 次（汉口站用时）。
  - 房态：前后均正确按 stay_available 区分可住房型。
  - 参考价：首版弱化后 2 次中 1 次未调用工具；补强后 3/3 调用。最终回复均为转人工模板。
  - 天气开场白：前后均恰好一次。
  - 客诉、FAQ 草稿：最终版本各 3/3 通过校验。
  - 既有问题（前后都有）：天气联网回复会编造民宿设施（修改前 7 次中 1 次，修改后 5 次中 2 次，样本不足以判断差异），`remove_ungrounded_property_claims` 未拦住；「你们有哪些房型」前后都不调用 `list_properties`。均不在本次范围，单列。
- [x] 联网回复编造设施、房型问题不查房源：用户回复「一起修了再发」「开始」，按 `docs/specs/2026-09-24_amenity-claims-and-room-catalog-spec.md` 实施并在生产容器复测通过。
- [x] 1.39.12 发布与线上核对、测试号收件验收（用户确认三条都收到）。

### 编造服务与时效分流（2026-09-24，用户回复「23修复」「开始」）

- [x] 按 `docs/specs/2026-09-24_service-claims-and-live-routing-spec.md` 实施；全量 1929 passed / 24 skipped；生产容器真实 DeepSeek 复测通过。
- [x] 1.39.13 发布与线上核对、测试号收件验收（用户确认收到）。

### 待办：联网结果缓存按意图共享（用户 2026-09-24 提出设计，未立项）

- 结论：不写入知识库。知识库是人工审核事实，联网内容不可信且可能出错，写进去会被当作审核事实发给所有客人。
- 可做的小改动：天气类缓存键从「问题原文」改为「类型 + 地点 + 目标日期」，缓存结构化事实而不是整段回复，失效时间按信息类型设置，与入住天气提醒（`lifecycle_reminders.py::TourismReminderWeatherProvider`）共用。
- 前提：1.39.13 上线运行一两周后，只读统计生产日志里的联网搜索次数和 `cache_hit` 命中率，重复率确实高才立项写 Spec。

### 联网回复等待时间优化（2026-09-24，用户回复「按顺序全部做完」）

- [x] 第 1 项：联网问题先发固定安抚。
- [x] 第 2、3 项：生产容器实测，采用搜索 1 次、关闭思考、推荐正文 400 至 600 字（搜索中位数 14.6 → 3.4 秒）；跳过精炼未达标准，不采用。
- [x] 1.39.14 发布与线上核对、测试号收件验收（用户确认先收到安抚、再收到答案）。

### 「不得编造事实」全局底层规则（2026-09-24，用户回复「开始」）

- [x] 按 `docs/specs/2026-09-24_fact-source-rule-spec.md` 实施；全量 1968 passed / 24 skipped；固定话术改动前后逐字相同；生产历史只读评估与真实 DeepSeek 复测通过。
- [x] 1.39.15 发布与线上核对、测试号收件验收（用户确认收到）。
- [ ] 待决：回复中出现「（来源：历史查询）」这类内部说法，是否处理由用户决定。

### 安全类回复送达与两处回归修复（2026-09-25，用户回复「直接做完」）

- [x] 按 `docs/specs/2026-09-25_safety-delivery-and-regressions-spec.md` 实施 F1 至 F5；29 条新增修复用例在 main 上失败、修改后通过；全量 2012 passed / 24 skipped。
- [x] 部署前真实模型回归发现房态工具未开放：住宿意图收成 `answer_policy.asks_stay_availability` 一处定义。
- [x] 部署前真实模型回归：四轮，发现并修复住宿意图五处词表不一致、人数误判为房态、英文与带日期追问不开放工具；结果见 `docs/releases/1.39.16.md`。
- [x] 1.39.16 发布与线上核对（DEPLOY_OK）。
- [x] 测试号收件验收（用户确认收到）。
- [ ] 待决：店外信息的常识性推测（如「这几天武汉早晚偏凉」）是否收紧，由用户决定。
- [ ] 第 ② 块：P9 起的边界条目（Q4/Q13、Q5、Q6/Q14、Q8/Q15、Q18），由用户转交边界 Spec。
- [ ] 第 ③ 块：1.39.17 真实模型回归门禁（Q16、Q20）与共用虚构资料（Q17）。

### 真实模型回归门禁、共用虚构资料与店外状态不推测（2026-09-26，用户回复「开始，保留开关，顺手修」）

- [x] F1 共用虚构资料 `tests/fixtures/guest_reply_scenarios.json` 与离线校验（7 个房态场景补同义说法，禁令未放宽）。
- [x] F2、F3 回归运行器 `tools/reply_regression.py` 与基线文件；线上 1.39.16 首次基线 146 × 3：稳定通过 71、已知未通过 75（安全类 11）。
- [x] F5 店外状态不推测；F6 删空后的中性兜底。
- [x] F4 门禁脚本 `scripts/release/reply_gate.sh`，本机部署脚本接入与跳过开关；自测：故意退步判为不通过（退出码 1），未改回复链路时跳过，临时目录均清理。
- [x] 1.39.17 发布：部署中门禁首次执行并通过（无退步，新纳入 3 个），DEPLOY_OK。
- [x] 测试号收件验收（用户确认收到，天气部分不再推测）。

### 紧急情况后续固定答复、问价流程（对比版）与回归工具补强 1.40.0（2026-09-26，用户回复「按推荐来」）

- [x] 按 `docs/specs/2026-09-26_emergency-follow-up-and-price-spec.md` 实施 F1 至 F3；36 条新增或改动的用例在 main 上失败、修改后通过；全量 2080 passed / 24 skipped。
- [x] 1.40.0：部署中回归门禁判 `AV-备注` 退步，拦截未部署；核查为字面判定问题（首个请求哈希一致）。
- [x] 1.40.1 发布：门禁通过（退步 0，新纳入 10 个），DEPLOY_OK。
- [x] 1.40.1 测试号收件验收（问价、紧急后续均收到）；测试号会话经授权交还机器人（审计 conversation_release）。
- [ ] 待做：1.41.0「经验沉淀」（管家答复沉淀、事件复盘、每日汇总），先写 Spec。
- [ ] 待做：Codex 重构完成后，两版问价实现用同一门禁对比择优。

### 主链分阶段耗时日志 1.40.2 与客人数据保留放开 1.41.0（2026-09-26，用户回复「开始，3补回直接授权」）

- [x] 1.40.2：按 `docs/specs/2026-09-25_main-chain-stage-timing-spec.md` 实施；新增 7 条用例；全量 2087 passed / 24 skipped。
- [x] 1.40.2 发布：门禁通过（退步 0，新纳入 2 个），DEPLOY_OK；线上耗时日志待真实消息验证。
- [x] 1.41.0：按 `docs/specs/2026-09-26_guest-data-retention-spec.md` 实施 G1 至 G6；全量 2092 passed / 24 skipped。
- [ ] 1.41.0 发布、迁移核对、回填存量加密数据（用户已授权）。
