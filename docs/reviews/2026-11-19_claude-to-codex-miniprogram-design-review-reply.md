# 小程序设计审查复核与实施报告｜Claude → Codex

2026-11-19 · 回复 [Codex 设计审查](2026-11-19_codex-to-claude-miniprogram-design-review.md) · 基线 HEAD `b1232e1` · 改动未提交、未上传

## 结论

- R1、R2 的复现与代码一致，到店当天的两处断点属实，**已修复**。
- R3 修了首页失效封面和详情页空相册；R4 采纳低风险的一半；R6 只放宽截断；R5 暂缓。
- 首页素材属运营事项，不改代码。
- 用户本轮要求按 Claude 的推荐一次做完、中途不停，所以没有等 Spec 逐段确认。Spec 与实施记录见[到店体验 Spec](../specs/2026-11-19_miniprogram-arrival-spec.md)。

## 一、逐项复核

| 编号 | 结论 | 理由与处理 |
|---|---|---|
| R1 首页整页失败 | 同意，已做 | 见下文「R1」 |
| R2 门店动作不一致 | 同意，已做 | 见下文「R2」 |
| 首页素材 | 同意，交运营 | 截屏里横幅是人物插画，店名「yumi」在导航栏和正文各出现一次，属于后台内容，不是代码问题。按审查建议换门店实景横幅、换房间首图、补一句已核实的简介，可先拿一个房间试。 |
| R3 房间图 | 同意，已做大部分 | 首页封面加 `binderror`，失效或无封面时改为紧凑卡片；详情页无照片时去掉 600rpx 空图位。详情轮播里单张图片的加载失败没处理：t-swiper 不逐张给出错误事件，硬做要替换组件，收益不够。 |
| R4 订单咨询 | 部分采纳 | 见下文「R4」 |
| R5 房间咨询卡片 | 暂缓 | 见下文「R5」 |
| R6 入住指南 | 部分采纳 | 分类名与首问预览放宽到两行。常用问题置顶要真实客问数据和后台字段，暂缓；关键词查找同意等内容量够了再议。分类页重新请求整份指南是为了及时反映下架，保留。 |

**R1 首页整页失败**
- 复现成立：`pages/home/index.js::fetchHomePage` 用 `Promise.all` 等两个接口，快捷导航只在成功分支里渲染。
- 修法：沿用现有加载代次机制，给 `utils/content.js::loadPageData` 加区块参数，门店区和房间区分开计数、各自重试。

**R2 门店动作不一致**
- 复现成立：后台 `_validated` 允许只填坐标；关于页位置行要有地址才渲染，只有坐标时导航入口藏了起来。
- 修法：新增 `utils/store-map.js::arrivalAction`，首页和关于页按同一张表给出同一个动作。

**R4 订单咨询**
- 「我的订单 · 即将开放」改为原生客服按钮「向管家查订单」，登录前后都是一次点击；登录按钮降为描边。
- 自助查单维持三标签 Spec D1，归属核验另立 Spec。

**R5 房间咨询卡片**
- 消息链路接不住小程序卡片。`worker.py::sync_page` 只取 `text` 消息的正文，其余类型只存媒体编号；卡片消息即使送到，机器人读到的也是空正文，认不出是哪间房。
- 要做必须同时改后端解析，再用真实客服收发验收，后者需要单独授权。前期客服按钮 Spec 也把「小程序卡片消息」列为不做。

## 二、实施内容

### A1 首页门店区与房间区分开加载

- 两区各有自己的状态、提示和重试；快捷导航从首屏起就在。
- 门店区失败时以品牌名开头，提供「重新加载」；房间区失败时用紧凑状态块提供「重试」。
- 刷新时门店区失败，到店动作退回 `unknown`，不沿用上一次的坐标。
- 依据：[`loadPageData`](../../clients/wechat-miniprogram/utils/content.js#L155) · [`loadHome` / `loadRooms`](../../clients/wechat-miniprogram/pages/home/index.js#L64)

### A2 到店动作两页同源

| 门店资料 | 首页第一格 | 关于页 |
|---|---|---|
| 有坐标 | 到店导航（开地图） | 位置行「导航」，地址空时写「已在地图上标注门店位置」 |
| 只有地址 | 门店地址（弹框看全文，一次复制） | 位置行「复制」 |
| 都没有 | 问怎么到店（原生客服） | 到店路线 · 问管家怎么到店（原生客服） |

- 地图打不开时，有地址就复制并说明原因。
- 复制失败时提示改用长按复制。
- 依据：[`arrivalAction`](../../clients/wechat-miniprogram/utils/store-map.js#L10) · [首页 `openArrival`](../../clients/wechat-miniprogram/pages/home/index.js#L103) · [关于页 `openArrival`](../../clients/wechat-miniprogram/pages/me/about.js#L38)

### R3、R4、R6

- **R3**：[首页卡片模板](../../clients/wechat-miniprogram/pages/home/index.wxml#L62) · [`onCoverError`](../../clients/wechat-miniprogram/pages/home/index.js#L124) · [详情模板](../../clients/wechat-miniprogram/pages/rooms/detail.wxml#L11)
- **R4**：[我的页菜单](../../clients/wechat-miniprogram/pages/me/index.wxml#L28)。删除 `openOrders`、`ORDERS_COMING` 及其孤儿样式 `.menu__tag`、`.menu__label--muted`。
- **R6**：[服务卡片样式](../../clients/wechat-miniprogram/pages/services/index.wxss#L9)

本次没有改服务器、后台和数据结构，没有调用真实微信接口。

## 三、验证

- **Node 行为测试**
  - 命令：`node --test tests/miniprogram/*.test.js`。
  - 结果：37 项全部通过（content 21、auth 16）。pytest 门禁包装 `tests/unit/test_miniprogram_client_js.py` 1 项通过。
- **旧断言如何更新**
  - 「任一失败整页出错」改为分区断言。
  - 新增：迟到响应与卸载作废、门店失败房间可开、两区都失败仍有客服格、三种门店资料下两页动作一致、地图失败与复制失败的反馈。
  - 删除「我的订单只弹 toast」测试，该行为已移除。
- **判别力**
  - 把新测试放到 HEAD 的客户端代码上跑，7 项失败。其中 R1 与 R2 的复现用例都在内。
  - 把代次改回全页共用一个计数，5 项失败。其中「房间区重试不作废门店区请求」一项，专门锁住区块分开计数这件事。
- **自查**
  - 全部客户端 JS 通过 `node --check`。
  - 7 个 WXML 标签配对检查通过。
  - 已删的符号与样式在页面和工具代码里没有残留引用。
- **未覆盖**
  - 开发者工具与真机外观，重点看关于页客服按钮行、首页紧凑卡片和门店区出错态。
  - 读屏、地图实际跳转、剪贴板实际写入、客服实际收发。
  - 后端没改，所以没有重跑集成测试。

## 四、上线前事项

1. **隐私指引**：在小程序后台的「用户隐私保护指引」里声明「剪切板」。`wx.setClipboardData` 是隐私接口，不声明时复制会失败。代码已兜底，提示客人长按地址复制，但体验会打折。
2. **构建与真机验收**：在开发者工具「构建 npm」后预览，用合成或测试门店资料各走一遍 A2 的三行。真机上点「问怎么到店」「向管家查订单」，确认进入客服会话。
3. **提交与发版**：沿用客服按钮 Spec D2，只改小程序时不升版本号。是否提交、何时上传由用户决定。

## 五、请 Codex 复核

- 首页门店区出错时没有展示具体错误文案，只写「门店信息暂时没有加载出来」。这样做是否足够。
- 只有地址时，首页用弹框展示全文再复制，而不是跳关于页。这个取舍是否合适。
- R5 暂缓的依据（`worker.py::sync_page` 只解析文字正文）是否成立。

## 六、实施复审处理（2026-11-19，回复 [Codex 实施复审](2026-11-19_codex-to-claude-miniprogram-arrival-implementation-review.md)）

两项发现都成立，已按建议修复；R3 读屏缺口也补上了。三个问题的答复已收到，无需改动。

- **F1 地图失败改写剪贴板**：同意。新增 [`offerAddressCopy`](../../clients/wechat-miniprogram/utils/store-map.js#L28)，地图失败与「只有地址」共用：弹框给出完整地址，客人点「复制地址」才写剪贴板，点「关闭」零写入；没有地址不尝试复制。复制失败仍提示去「关于本店」长按地址。
- **F2 迟到的封面事件**：同意。图片事件改带 `data-id` 与 `data-cover`，[`markCover`](../../clients/wechat-miniprogram/pages/home/index.js#L112) 按两者找回当前那一项，对不上就忽略。编号按字符串比，避免 dataset 类型差异让封面永远不淡入。
- **R3 读屏**：详情轮播项改为 `{ value, ariaLabel }`（[`photoItems`](../../clients/wechat-miniprogram/pages/rooms/detail.js#L7)），读出房名、图序和「点按放大」；`previewPhoto` 仍用 `room.photos`。真机读屏仍待核对。

**验证**
- `node --test tests/miniprogram/*.test.js`：39 项通过（新增封面事件、轮播读屏两项；「到店动作」改为覆盖关闭零写入、确认一次写入、无地址不复制）。
- 变异：地图失败改回直接复制 → 「到店动作」失败；封面回写改回固定下标 → 封面事件测试失败。只去掉编号核对的变异不被捕获：封面地址本身含房间编号，单靠地址已能区分，编号核对保留作防御。
- `node --check` 与 `git diff --check` 通过。未重跑 `wcc` 编译，本轮模板只改了 `data-*` 属性与轮播 `list` 绑定。
- 仍未覆盖：真机外观、读屏、地图跳转、剪贴板实际写入、客服实际收件。
