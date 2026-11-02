// 入住指南：后台勾选的「本店通用」知识按分类折叠展示（小程序住客自助 Spec F1、F5）。
const { fetchGuide, loadPageData, discardPendingLoads } = require('../../utils/content')
const { openServiceChat } = require('../../utils/service-chat')

Page({
  data: {
    status: 'loading',
    message: '',
    sections: [],
    skeletonRows: [{ width: '40%' }, 1, 1, 1],
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
      sections,
    }))
  },
  retry() {
    this.load()
  },
  askHost() {
    openServiceChat()
  },
})
