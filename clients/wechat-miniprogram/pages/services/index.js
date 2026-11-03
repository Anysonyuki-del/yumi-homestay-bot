// 服务：知识分类的图标宫格，点进分类看问答；宫格下方是联系管家（三标签 Spec F3）。
const { fetchGuide, loadPageData, discardPendingLoads } = require('../../utils/content')
const { openServiceChat } = require('../../utils/service-chat')
const { categoryIcon } = require('../../utils/format')

Page({
  data: {
    status: 'loading',
    message: '',
    categories: [],
  },
  onLoad() {
    this.load()
  },
  onUnload() {
    discardPendingLoads(this)
  },
  async onPullDownRefresh() {
    await this.load()
    wx.stopPullDownRefresh()
  },
  load() {
    return loadPageData(this, fetchGuide, (sections) => ({
      status: sections.length ? 'ready' : 'empty',
      categories: sections.map((section) => ({
        name: section.category,
        icon: categoryIcon(section.category),
      })),
    }))
  },
  retry() {
    this.load()
  },
  // 状态块按钮：出错时重试，没有分类时问管家。
  onStateAction() {
    if (this.data.status === 'error') this.retry()
    else this.askHost()
  },
  // 分类名是后台自由文字，作为参数传递前必须编码。
  openCategory(event) {
    const name = event.currentTarget.dataset.name
    wx.navigateTo({ url: `/pages/services/category?name=${encodeURIComponent(name)}` })
  },
  askHost() {
    openServiceChat()
  },
})
