# YuMi 住客小程序设计审查｜Codex → Claude

2026-11-19 · 供 Claude 复核与拟定 Spec · 仓库 HEAD `b1232e1`

**优先处理到店当天的两处断点。** 房间接口故障会让首页的导航和客服同时消失；后台只填坐标时，「关于本店」又会藏起一个本来能打开的地图。两项均用合成数据复现。现有三栏、暖色和原生客服入口可保留。以下优先级按客人任务受阻程度确定，未测量线上发生率。

## P1 · 客人到店时能找到地址和管家

### R1　房间列表失败拖垮整个首页

**复现。** 令门店接口返回 200 且带有效坐标，房间列表返回 503。首页进入整页错误态，只剩「重试」。原因是 `fetchHomePage` 用 `Promise.all` 等待两个结果，快捷导航也位于成功分支。现有测试明确要求「任一失败整页出错」，调整这项行为需要同步修改验收约定。[首页取数与映射](../../clients/wechat-miniprogram/pages/home/index.js#L17) · [首页模板](../../clients/wechat-miniprogram/pages/home/index.wxml#L12) · [原行为测试](../../tests/miniprogram/content.test.js#L207)

**建议与验收。** 让门店区和房间区分别显示加载结果，指南和联系客服保留可点。房间失败时，仅房间区提供重试，已取得的坐标仍可导航；门店失败而房间成功时，房间卡仍可打开；两者都失败时，首页仍能发起原生客服。重试、下拉刷新或退出页面后，迟到的旧请求不得覆盖新结果。现有加载代次机制可作为实现依据。[公共加载流程 `loadPageData`](../../clients/wechat-miniprogram/utils/content.js#L153)

### R2　首页和「关于本店」对同一份门店资料给出不同动作

**复现。** 门店只填成对经纬度，名称、简介、地址、电话均空。后台接受该组合；关于页进入 `ready`，四个文字字段均为空。其 `openMap` 能把坐标传给地图，但模板要求文字地址存在才渲染入口。另一种情况是只填简介：首页仍写「到店导航」，点进去后只有简介。[后台校验 `_validated`](../../src/homestay_bot/services/miniprogram_home_admin.py#L85) · [地图分支 `openStoreMap`](../../clients/wechat-miniprogram/utils/store-map.js#L8) · [关于页 `load`](../../clients/wechat-miniprogram/pages/me/about.js#L20) · [位置入口](../../clients/wechat-miniprogram/pages/me/about.wxml#L9)

| 公开资料 | 建议两页提供的动作 | 验收观察 |
|---|---|---|
| 有坐标，地址可空 | 导航到店 | 首页、关于页都能打开同一坐标；若地图失败且有地址，可复制地址 |
| 只有文字地址 | 复制地址 | 客人可读到完整地址，一次点击复制并收到反馈 |
| 坐标和地址都缺失 | 问管家怎么到店 | 即使还有简介，也可直接从当前位置发起原生客服 |

## 首页素材 · 可与 P1 同步处理

微信开发者工具中已打开的首页显示人物插画横幅，导航栏和正文各显示一次「yumi」。第一张房间封面在窗口中呈现为深色文字截图。附件仅保留首页上部，能复核横幅、店名和四个快捷入口，未包含第一张封面。该画面只代表本次模拟器所见，线上素材版本待核实。[查看首页截屏](2026-11-19-miniprogram-design-evidence/home.png)

建议运营先核实素材用途。横幅优先选能识别门店的实景；若插画属于品牌主题，可选能看到主题布置的实景。房间首图选能看出床型和空间的照片，门店简介用一句已核实的特色说明。现有后台能调整横幅排序，房间列表按照片排序取首张作为封面，可先用一个房间验证效果。[横幅排序 `reorder_banners`](../../src/homestay_bot/services/miniprogram_home_admin.py#L184) · [房间封面 `_card`](../../src/homestay_bot/services/miniprogram_content.py#L280)

## P2 · 房间浏览、咨询和入住指南

| 编号 | 当前体验与证据 | 建议与验收 |
|---|---|---|
| R3 房间图 | 首页有封面地址却加载失败时，只有成功事件能解除透明样式，无图提示也不会出现。详情明确无图时仍留出 600rpx，375px 宽设备上约为 300px。[封面与 `onCoverLoad`](../../clients/wechat-miniprogram/pages/home/index.wxml#L45) · [封面样式](../../clients/wechat-miniprogram/pages/home/index.wxss#L15) · [详情无图样式](../../clients/wechat-miniprogram/pages/rooms/detail.wxss#L4) | 加载中保留占位；确认缺图或失败时切成紧凑布局。分别以正常图、空相册、失效封面检查，后两种应能直接看到房名和规格。正常图保留滑动与放大，并在真机读屏核对房名、图序和放大提示。 |
| R4 订单咨询 | 「我的」把登录作为主按钮，「我的订单」在登录前后都只弹提示，客人还要再找客服。真实订单查询在前期设计中列为二期。[账户页按钮与菜单](../../clients/wechat-miniprogram/pages/me/index.wxml#L13) · [`openOrders`](../../clients/wechat-miniprogram/pages/me/index.js#L72) · [三栏 Spec 决策 D1](../specs/2026-11-03_miniprogram-three-tabs-spec.md#L123) | 建议将入口改为可直接操作的「向管家查订单」，降低登录按钮的视觉优先级。验收登录前后都能经一次点击发起咨询。自助查单另议，展示订单前必须核实归属，不能按微信登录或客人自报手机号自动关联。[现有人工核对边界 `GuestVerificationService`](../../src/homestay_bot/services/guest_verification.py#L27) |
| R5 房间咨询 | 房间详情底栏可打开原生客服，但按钮只声明固定来源 `room`，没有提供当前房间的消息卡片。[详情底栏](../../clients/wechat-miniprogram/pages/rooms/detail.wxml#L28) | 建议用「咨询这间房」，允许客人主动附上公开房间卡片。先验证当前微信客服接入链路是否传递并显示房名；从两个不同房间进入，接收侧应能准确区分。端到端收发需要单独授权。 |
| R6 入住指南 | 服务页按分类展示第一问，分类名和预览均单行截断；进分类后再次请求整份指南。想问停车的客人可能要先猜分类。[卡片映射 `serviceCards`](../../clients/wechat-miniprogram/utils/format.js#L29) · [卡片样式](../../clients/wechat-miniprogram/pages/services/index.wxss#L9) · [分类页 `load`](../../clients/wechat-miniprogram/pages/services/category.js#L25) | 先从实际客问中选少量常用问题，让客人在服务首屏直接展开答案，同时保留全部分类。候选问题须来自当前公开指南；下架、过期或房间专属内容不能因置顶重新可见。待内容量确有需要再考虑关键词查找。[公开条件 `MiniProgramContentService.guide`](../../src/homestay_bot/services/miniprogram_content.py#L129) |

## 验证记录与交接请求

- 上一轮审查执行 `node --test tests/miniprogram/auth.test.js tests/miniprogram/content.test.js`，38 项通过。门店与公开内容集成测试 `tests/integration/test_miniprogram_home.py`、`tests/integration/test_miniprogram_content.py` 共 26 项通过。它们验证现有行为；R1 的旧断言正是拟调整的产品行为。
- R1、R2 另以本地合成响应走过页面逻辑。视觉证据覆盖开发者工具中已打开的首页；其余页面依据 WXML、WXSS、页面逻辑和后端契约。线上素材、真机排版与读屏、地图实际跳转、客服实际收发均待核验。
- **请 Claude 先回传复核结论。** 对 R1–R6 分别说明同意、需调整或暂缓及理由。若同意优先级，请把 R1、R2 合并成一份到店体验 Spec，列明涉及的页面、必须更新的旧断言、失败分支和验收矩阵。R4 的订单入口、R5 的客服卡片和 R6 的常用问题需先确认产品取舍；实时价格、房态及在线预订另行评估。按项目流程，完整 Spec 获用户确认并明确回复「开始」后才进入编码。
