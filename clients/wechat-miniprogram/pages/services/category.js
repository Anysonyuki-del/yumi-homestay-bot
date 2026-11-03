// 服务分类：一个知识分类下的问答折叠列表，导航栏标题就是分类名（三标签 Spec F3）。
const { ContentError, fetchGuide, loadPageData, discardPendingLoads } = require('../../utils/content')

const CATEGORY_GONE = '这个分类暂时没有内容'

Page({
  data: {
    status: 'loading',
    message: '',
    items: [],
    skeletonRows: [1, 1, 1, 1],
  },
  onLoad(options) {
    this.category = decodeURIComponent((options && options.name) || '')
    if (this.category) wx.setNavigationBarTitle({ title: this.category })
    this.load()
  },
  onUnload() {
    discardPendingLoads(this)
  },
  async onPullDownRefresh() {
    await this.load()
    wx.stopPullDownRefresh()
  },
  // 每次重新读取整份指南再按分类筛选：分类可能已被后台取消展示，此时按「已下架」处理。
  load() {
    return loadPageData(this, async () => {
      const section = (await fetchGuide()).find((item) => item.category === this.category)
      if (!section) throw new ContentError(CATEGORY_GONE, 404, true)
      return section.items
    }, (items) => ({ status: 'ready', items }))
  },
  retry() {
    this.load()
  },
  // 状态块按钮：出错时重试，分类不存在时回到服务页。
  onStateAction() {
    if (this.data.status === 'error') this.retry()
    else wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/services/index' }) })
  },
})
