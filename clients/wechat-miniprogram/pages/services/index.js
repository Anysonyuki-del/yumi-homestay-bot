// 服务：两列知识分类卡片，点进分类看问答；卡片下方是联系管家（三标签 Spec F3，质感 Spec T5）。
const { fetchGuide, loadPageData, discardPendingLoads } = require('../../utils/content')
const { openServiceChat } = require('../../utils/service-chat')
const { serviceCards } = require('../../utils/format')

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
    return loadPageData(this, fetchGuide, (sections) => {
      const categories = serviceCards(sections)
      return { status: categories.length ? 'ready' : 'empty', categories }
    })
  },
  retry() {
    this.load()
  },
  // 状态块按钮：出错时重试，没有分类时问管家。
  onStateAction() {
    if (this.data.status === 'error') return this.retry()
    return this.askHost()
  },
  // 分类名是后台自由文字，作为参数传递前必须编码。
  openCategory(event) {
    const name = event.currentTarget.dataset.name
    wx.navigateTo({ url: `/pages/services/category?name=${encodeURIComponent(name)}` })
  },
  askHost() {
    return openServiceChat()
  },
})
