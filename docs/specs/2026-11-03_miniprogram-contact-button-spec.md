# 小程序「问管家」改用原生客服按钮 Spec

状态：待用户审阅。用户回复「开始」前不写代码。

范围来源（用户 2026-11-03）：
- 用户反馈「暂时无法打开客服」。
- 真机日志：`openCustomerServiceChat:fail not bind`。
- 在两条路里，用户选了第 1 条：改用小程序原生客服按钮。

## 一、现状证据

- **现在的实现**
  - `utils/service-chat.js::openServiceChat` 调用 `wx.openCustomerServiceChat`，传入企业 ID 和客服链接。
  - 打开失败时，`callStoreInstead` 读取门店电话：有电话就弹框确认后拨打，没有电话就提示「暂时无法打开客服」。
  - 这个文件当前有一处未提交改动：失败时把 errMsg 写进调试日志。
- **为什么打不开**
  - 真机报错是 `openCustomerServiceChat:fail not bind`。
  - 在小程序后台「客服 → 微信客服 → 接入」填企业 ID `ww6412ad55e27f12dd` 时，页面提示「企业ID与实体名称不一致」。也就是说，小程序和企业微信（武汉秉善品牌管理有限公司）不是同一主体，这个接口绑不上。
- **已经接好的另一条路**
  - 企业微信后台「在小程序中接入」已显示 Yumi小管家 接入了客服账号 YuMi小管家。这一步走的是「免开发快速授权」，由小程序管理员扫码完成，不要求同主体。
  - 企业微信页面对这条路的说明是：客人进入小程序原有的客服会话发消息，就会转到微信客服。小程序原有的客服会话，就是 `button open-type="contact"` 打开的那个。
- **组件支持**
  - 项目里的 TDesign 1.17.0 `t-button`（`miniprogram_npm/tdesign-miniprogram/button/button.wxml`）已透传 `open-type`、`session-from`、`show-message-card` 和 `bind:contact`。

### 全部「问管家」入口

| # | 位置 | 现在的写法 |
|---|---|---|
| E1 | 首页快捷导航「联系管家」（`pages/home/index.wxml`，`QUICK_NAV` 的 `host`） | `view` + `openQuickNav` → `askHost` |
| E2 | 首页页尾「还有问题？直接问管家」 | `view.contact` + `askHost` |
| E3 | 首页房间为空时的状态块按钮 | `templates/state.wxml` → `onStateAction` → `askHost` |
| E4 | 服务页「联系管家」条（`pages/services/index.wxml`） | `view.contact` + `askHost` |
| E5 | 服务页没有分类时的状态块按钮 | `onStateAction` → `askHost` |
| E6 | 分类页右下角「问管家」（`pages/services/category.wxml`） | `t-button` + `askHost` |
| E7 | 房间详情底栏「预订或咨询，问管家」（`pages/rooms/detail.wxml`） | `t-button` + `askHost` |
| E8 | 我的 →「联系管家」（`pages/me/index.wxml`） | `view.menu__row` + `askHost` |
| E9 | 关于本店没有门店信息时的状态块按钮（`pages/me/about.js`） | `onStateAction` → `openServiceChat` |

## 二、功能点

### C1 所有入口改成原生客服按钮

- 原生客服按钮必须由客人直接点按钮触发，JS 里不能调起。所以 9 个入口都要在模板里直接写成按钮。
- **已经是 `t-button` 的（E6、E7）**：去掉 `bind:tap`，加上 `open-type="contact"`。样式不变。
- **状态块（E3、E5、E9）**
  - `templates/state.wxml` 新增参数 `actionContact`。为真时，按钮带 `open-type="contact"`，不绑定 `onStateAction`。
  - 三个页面的空状态都传 `actionContact: true`。出错状态的「重试」不变。
  - `onStateAction` 只剩重试一种情况。
- **自己画的行和条（E1、E2、E4、E8）**
  - 外层从 `view` 换成原生 `button open-type="contact"`，内部结构和 class 不变。
  - `app.wxss` 新增一个通用类 `.contact-reset`，去掉原生按钮的默认外观：边框、背景、内边距、默认宽度、行高和文字居中。这样看起来和改前一样。按压反馈保留，仍用现有的 `hover-class`。
  - 快捷导航的四格仍由 `QUICK_NAV` 循环生成。`host` 那格用 `wx:if` 渲染成按钮，其余三格不变。
- **会话来源**：加 `session-from`，区分客人从哪一页进来（如 `home`、`room`），方便在微信客服后台查看来源。不发小程序卡片（`show-message-card` 不开）。

### C2 删除旧实现

- 删除 `utils/service-chat.js`，以及各页的 `askHost` 和 `openServiceChat` 引用。
- `openQuickNav` 去掉 `host` 分支。
- 本次未提交的 errMsg 日志改动随文件一起删除。
- 同时去掉「客服打不开时改拨门店电话」的兜底（质感 Spec T1）。原生按钮打开后，微信不会把成功或失败告诉小程序，没有触发兜底的时机。门店电话在「关于本店」页仍可拨打（`pages/me/about.js::callStore`，不改）。

### C3 测试（只写有判别力的）

- 删除 `tests/miniprogram/content.test.js` 里针对 `openServiceChat` 的测试：成功、失败拨号、失败提示、连点。
- 首页快捷导航测试里「联系管家走客服兜底」那条改为：`openQuickNav` 只处理其余三格。
- 不为模板里的 `open-type` 写测试，那等于复述 WXML。原生按钮的行为只能在真机上验收。
- 自查：全仓不再出现 `openCustomerServiceChat`、`askHost`、`service-chat`；WXML 结构和 JS 语法自查通过。

## 三、风险与决策

- **D1 去掉拨打门店电话的兜底**（建议去掉）
  - 原生按钮没有失败回调，留着兜底也不会触发。
  - 替代方案是在「联系管家」旁另加一个「拨打电话」入口。这次不加：门店电话现在还没填，加了也是空的。
- **D2 版本与发布**（建议：只提交，不升版本号）
  - 这次只改小程序，服务器不用部署。
  - 改完提交到 main；由你在开发者工具里「构建 npm」、上传，再用真机验收。
  - 如果你要随这次改动正式发版，另外告诉我，我再补版本号、CHANGELOG 和发布记录。
- **风险**
  - 「免开发授权后，原生客服会话转到微信客服」依据的是企业微信页面的说明，我没有在真机上看到。上传后请用真机点任一「问管家」：应进入会话，发一句话后由机器人回复，就算通过。
  - 如果真机上进入的是小程序自带客服（消息出现在小程序后台「客服 → 小程序客服」，没有到企业微信），说明授权没生效，需要回企业微信后台重新授权。代码不用再改。
  - 开发者工具的模拟器里，原生客服按钮只会打开一个模拟页面，不能用来验收。
  - 原生 `button` 在微信里有默认宽度和伪元素边框。之前「我的」页头部被挤歪就是这个原因。`.contact-reset` 要逐一在开发者工具里核对 E1、E2、E4、E8 的外观。
- **不做**
  - 小程序卡片消息、按房间带参数进入会话。
  - 后台改主体、重新做 API 接入。
- **实施顺序**
  1. `app.wxss::.contact-reset`，`templates/state.wxml` 新增 `actionContact`。
  2. 改 E1–E9 的模板，删掉各页的 `askHost` 和 `utils/service-chat.js`。
  3. 改测试，跑 `node --test tests/miniprogram/*.test.js`，做自查。
  4. 在本 Spec 追加实施记录和开发者工具核对清单。

## 四、实施记录（2026-11-03，用户「开始」）

C1–C3 已在本地 main 工作区实现，没有提交，也没有调用真实微信接口。文首的「状态：待用户审阅」是写 Spec 时的状态。D1 去掉了拨号兜底，D2 只改代码、不升版本号。

- **改动**
  - **`app.wxss`**：新增 `.contact-reset`。
    - 单类名权重：清掉内边距、底色、圆角、字号、行高和对齐，并去掉伪元素边框。
    - `button.contact-reset:not([size=mini])`：清掉微信默认的宽度和外边距。
    - 页面自己的类在全局样式之后加载，所以页面写的内边距、底色和圆角仍然生效。
  - **`templates/state.wxml`**：新增参数 `actionContact` 和 `sessionFrom`。为真时渲染原生客服按钮，不绑定 `onStateAction`。
  - **E1–E9**：全部改为 `open-type="contact"`，来源分别是：

    | `session-from` | 入口 |
    |---|---|
    | `home` | E1、E2、E3 |
    | `services` | E4、E5 |
    | `category` | E6 |
    | `room` | E7 |
    | `me` | E8 |
    | `about` | E9 |

  - **快捷导航**：仍由 `QUICK_NAV` 循环生成；`host` 那格用 `wx:if` 渲染成按钮，其余三格不变。
  - **删除的旧代码**
    - `utils/service-chat.js` 已删除，未提交的 errMsg 日志改动随之删除。
    - 删除了各页的 `askHost` 和 `require`，`openQuickNav` 去掉了 `host` 分支。
    - 首页、服务页、关于页的 `onStateAction` 只剩重试。
- **测试与验证**
  - 删除了 4 条 `openServiceChat` 测试。
  - 从测试替身里去掉了 `openCustomerServiceChat` 和 `showModal`。
  - 状态块测试和快捷导航测试中「转问管家」的断言已删除。
  - 小程序 Node 测试 38 项通过。
  - 变异检查：把首页 `onStateAction` 改成空函数，测试能抓到。
  - 自查：
    - 6 个改动页面的 JS 语法检查通过；7 个 WXML 的标签配对检查通过。
    - 主工作区源码、测试和配置里，已不再出现 `openCustomerServiceChat`、`askHost`、`service-chat`。只有历史文档和 `tasks/todo.md` 的旧条目还提到它们，没有改动。
- **开发者工具核对清单**（`.contact-reset` 的外观只能看）
  1. 首页快捷导航四格大小、间距一致，「联系管家」那格没有灰底、边框，也没有居中挤压。
  2. 首页页尾条和服务页「联系管家」条：浅底、圆角 24rpx、文字靠左，右侧有箭头。
  3. 「我的」页：「联系管家」行与「关于本店」「我的订单」行同高，行间有分隔线。
  4. 分类页右下角按钮和详情页底栏按钮外观不变。
  5. 按下时有变淡或变底色的反馈。
- **真机验收**（模拟器不能代替）
  - 任一「问管家」都应进入会话。客人发一句话后，由 YuMi小管家 的机器人回复，才算通过。
  - 如果消息出现在小程序后台「客服 → 小程序客服」，而不在企业微信，说明免开发授权没生效，需要回企业微信后台重新授权。
