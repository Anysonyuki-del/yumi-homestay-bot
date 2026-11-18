# 1.73.3 发版、部署与生产数据库口令轮换 · Claude 交接 Codex 复审

**2026-11-18。** 接续 [Claude 复审回复](2026-11-18_claude-to-codex-audit-repairs-review.md)。复审之后，用户授权修完复审意见并发版部署，随后又授权轮换生产数据库口令。本文交代这两段工作改了什么、依据是什么、证据到哪一步，请 Codex 复审。

完整的运维过程和数字以 [1.73.3 发布变更记录](../releases/1.73.3.md) 为准，本文不重复，只列复审要点。

## 一、授权与范围

| 阶段 | 用户原话 | 实际执行 |
|---|---|---|
| 修复与发版 | 「你修完所有问题发布，直到部署完成为止向我报告」 | 补修 L1、L2，L3 写进发布记录，M1 作为部署前置处理；提交、推送、打标签 `v1.73.3`，部署到生产 |
| 口令轮换 | 先同意在本会话处理「轮换生产数据库口令（维护窗口）」，看过维护方案后回复「现在执行」 | 轮换 `homestay` 角色口令，更新生产 `.env`，只重启 API |

没有做的事：没有调用真实 DeepSeek、Hostex、企业微信，没有发送客人或员工消息，没有改订单，没有跑数据库迁移，没有重建 PostgreSQL 容器。

## 二、审查对象

| 提交 | 内容 |
|---|---|
| `1003de9105b05a7796c541aceca1b63201ff1bc4` | Codex 审计修复候选，加上 Claude 补修的 L1、L2，以及 Spec、交接、复审三份文档 |
| `a4e1f4c872ec9aa9c73a95f056ac3619601188a6` | 版本号 1.73.3、CHANGELOG、`docs/releases/1.73.3.md`、AOCI 资产。标签 `v1.73.3` 指向它 |
| `5465a0b21f511c1973a1d4eec1f9a17b65973470` | 补录部署结果和独立核对 |
| `7a3c1f6f118c9dcb5215855f98f54d9119d11d28` | 补录口令轮换；手册部署清单改为只重建 API；发布记录改动表里的旧口令原文改为文字描述 |

基线是 `6f16d5e`，`origin/main` 等于 `7a3c1f6`。工作区里其余未跟踪文件（JEV 离线评测等）不属于本轮。

`1003de9` 把 Codex 候选和补修合成了一个提交。这里只单独列出 Claude 新增的部分，其余部分应和 Codex 交接时的候选一致。

## 三、复审意见的处理

### L1 · 素材上传阶段的传输故障改走有界重试

- **位置**：`src/homestay_bot/application.py`，`_build_guest_image_handler` 内的 `send_guest_image`，最外层 `except Exception` 分支。
- **改动**：`send_attempted` 为 False 时，如果异常是 `httpx.TransportError`，就抛 `RetrySafeJobError("回复配图素材尚未上传")`。其他非传输异常（HTTP 状态错误、回执残缺等）仍原样抛出，进入终态。`send_attempted` 为 True 的分支（`UnconfirmedImageDelivery`）没有改。
- **测试**：`tests/integration/test_reply_images.py`
  - `test_connect_error_on_image_is_retried_not_failed` 增加参数 `"upload-timeout"`：把 `upload_temporary_image` 替换成抛 `httpx.ReadTimeout` 的函数，断言图片作业仍是 PENDING。撤掉补修时这个用例失败，恢复后通过。
  - `test_failed_image_stops_the_rest_and_alerts_staff` 去掉了 `upload_failed` 参数。上传失败现在会重试，不再适合放在「终态并通知员工」这个用例里。剩下的发送后 `ReadTimeout` 分支增加断言：`unknown_image_job_id` 等于失败作业的编号。
- **和复审建议的差别**：复审写的验收方法是「三次后才进入终态并发出通知」，补修只断言了第一次失败后保持 PENDING，没有覆盖重试耗尽之后的路径。重试耗尽后走 `worker.Worker.run_once` 通用的 `max_attempts` 判定，这次没有改动。

### L2 · 单张图片的核查通知不提「后续段」

- **位置**：`application.py::_notify_undelivered_reply_chain` 的 `unknown_image_job_id` 分支。
- **改动**：
  - 载荷有 `index` 时：「回复配图（第 i/n 段）的发送结果未知……后续段已停止发送，请管家核实……」
  - 没有 `index` 时：「一张图片的发送结果未知，作业编号：…。请管家核实客人是否收到，再决定是否补发。」
- **测试**：`test_interrupted_image_is_reviewed_once_without_replay` 的 `legacy=True` 分支断言正文里没有 `None`，也没有「后续段」。

### L3 · `alembic.ini` 的 `path_separator = os`

只在 `docs/releases/1.73.3.md` 的改动清单里单列了一行，没有改代码。

### M1 · 生产缺少 `POSTGRES_PASSWORD`

没有改代码，部署前在服务器上处理：

1. 备份生产 `.env`，追加 `POSTGRES_PASSWORD`。值从 `DATABASE_URL` 的密码段解码得到，全程没有输出。
2. 核对：用旧 `compose.yaml` 和新 `compose.yaml` 分别计算 `postgres` 的配置哈希，都和运行中容器的标签一致；新配置解析出的口令也和容器初始化时的值一致。
3. 部署脚本使用 `docker compose build api` 加 `up -d --no-deps api`，PostgreSQL 容器没有被调和。

复审里「值不一致会重建 PostgreSQL」是推断。这次没有实际触发，所以仍然没有得到验证。

## 四、发版与部署

- **本地最终全量**（含补修和版本号）：3724 passed、85 skipped、1 warning。Ruff、Mypy（166 个源文件）、`git diff --check` 通过。
- **CI**：main 与标签两次运行都成功。全量 3658 passed、84 skipped；PostgreSQL 七组专项 78 passed；镜像构建成功。本地比 CI 多出的 66 项来自未入库的 JEV 离线评测用例。
- **真实模型门禁**：`REPLY_PATHS` 只命中 `domain/stay_status.py`，用 `REPLY_GATE_SCOPE=skip` 跳过。理由：私有常量改名为公开的 `EXCLUDED_STAY_STATUSES`，集合内容和判定函数都没变。理由已写入门禁摘要和发布记录。
- **测试号真实收发**：没有做。图片链路的改动只影响失败和中断分支，正常发送路径没有变化，真实环境也很难人为制造「请求已发出但回执丢失」。
- **部署**：部署脚本输出 `DEPLOY_OK`。只替换了 API 容器，包版本 1.73.3，Alembic 在 `0038_miniprogram_home (head)`。PostgreSQL 容器启动时间仍是 2026-10-21。
- **独立核对**：容器 running、重启 0，日志中异常行为 0。作业表里 RUNNING、锁超时、到期未领取都是 0；`stale_non_replayable` 图片 0 条，核查登记 0 条。公网 `/health` 返回 503 degraded，这是发布前就有的稳态。

## 五、生产数据库口令轮换

**缘由**：生产 `homestay` 角色一直用旧 `compose.yaml` 里写死的口令，这个值在 Git 历史中公开。

**方式**：整个流程写成一个脚本，上传到服务器后台一次跑完。当时 SSH 被服务器的并发连接限制随机断开，这样可以避免中途断线留下改了一半的状态。脚本本身没有入库。要点如下：

- **停 API 之前**（任何一步失败都直接中止，不改任何东西）：
  1. `pg_dump` 并用 `pg_restore` 复验可读；对象数或字节数异常就中止。
  2. 另存 `.env` 和 `pg_authid` 里的旧 SCRAM 校验值。
  3. 用 `secrets.token_urlsafe(32)` 生成新口令，按 PostgreSQL 的存储格式在本地算出 SCRAM-SHA-256 校验值（4096 次迭代，16 字节盐）。
  4. 生成新 `.env`，保留原有引号风格，并回读校验解析出的口令和生成值一致。
  5. 等 RUNNING 作业清零，最多等 60 秒。
- **停 API 之后**：一旦出错，`ERR` trap 会回滚：用旧校验值恢复角色口令、还原 `.env`、启动 API。
  1. 通过容器内本地套接字（trust）执行 `ALTER ROLE homestay PASSWORD '<校验值>'`。数据库只收到校验值，明文不会出现在数据库日志里。
  2. 写入新 `.env`。
  3. 用 API 镜像起一次性容器，通过容器网络走 SCRAM 认证做探测：新口令（经 `.env`）必须返回 `CONNECT_OK`；旧口令必须返回口令认证失败（`InvalidPasswordError`）。
  4. `up -d --no-deps api`，健康探测有应答，并且容器状态为 `running/0`，才算完成。

**结果**：没有触发回滚，API 停机约 26 秒，PostgreSQL 没有重启。轮换后 API 日志异常为 0，历史清理轮次正常完成；`homestay` 的 TCP 连接有 5 条。数据库日志里的口令认证失败只有 1 条，就是那次故意用旧口令做的探测。

**后果**：`postgres` 服务的 Compose 配置哈希现在和运行中容器的标签不同。不带 `--no-deps` 的 `up` 会重建 PostgreSQL 容器。手册的部署清单（`YuMi民宿AI开发经验与防回归手册.md` 第 466 行）已改成只重建 API，部署脚本本来就用 `--no-deps`。

## 六、请 Codex 重点复审

1. **L1 的分类边界**：`send_attempted = True` 写在调用 `send_image` 之前，所以新分支只覆盖 `upload_temporary_image`（以及它内部取 token）抛出的 `httpx.TransportError`，包括 `ReadTimeout`、`WriteTimeout`、`RemoteProtocolError`。请确认：素材上传阶段的这些异常对客人没有任何副作用；重新上传会多出一个过期前无人引用的临时素材，这是否可以接受？
2. **L1 的测试缺口**：重试耗尽后进入终态，并且只发一条「未送达」通知（不是「结果未知」），这条路径没有用例覆盖。请判断是否需要补用例。
3. **口令轮换的回滚设计**：`ERR` trap 在 `set -e` 下的触发范围，包括命令替换和管道里的失败；回滚函数用 `set +e` 防重入。请判断有没有失败点会导致不回滚，或者只回滚了一半。
4. **配置哈希与容器标签不一致**：这个状态是长期保留，还是另找窗口用 `up -d postgres` 收敛？收敛时会重建数据库容器，需要单独授权，本轮没有做。
5. **README 的轮换说明**：README 写的是交互式 `\password homestay`（psql 也在客户端计算校验值），和这次用脚本的做法等价，但它没有提到轮换之后 `postgres` 配置哈希会变、以后部署必须带 `--no-deps`。是否需要补上这一点，请判断。本轮只改了手册，没有改 README。

## 七、证据缺口

- 部署和轮换之后都没有新的外发作业进来，还没有亲眼看到 worker 在新版本和新口令下处理作业。数据库读写只由历史清理轮次证明。
- 登录后的后台页面和诊断页没有核对。
- 服务器上几份旧 `.env` 备份、一个早期回滚构建目录和 root 的命令历史里还有旧口令。它们都已失效，按用户授权范围没有删除。
- 多实例 worker 并发恢复同一张中断图片，仍然只有 Codex 那次本地实验，没有生产证据。当前生产是单进程。
- M1 第 2 点（口令值不一致时会重建 PostgreSQL）仍是推断，没有实测。
