// 房间列表（默认首页）：展示后台勾选的房间，点卡片进入详情（小程序住客自助 Spec F5）。
const { fetchRooms, loadPageData, discardPendingLoads } = require('../../utils/content')
const { openServiceChat } = require('../../utils/service-chat')

Page({
  data: {
    // loading：加载中；ready：有内容；empty：没有展示的房间；error：加载失败可重试。
    status: 'loading',
    message: '',
    rooms: [],
    skeletonRows: [{ width: '100%', height: '360rpx' }, 1, { width: '60%' }],
  },
  onLoad() {
    this.load()
  },
  onUnload() {
    discardPendingLoads(this)
  },
  // 下拉刷新与重试共用加载流程；刷新结束后收起下拉动画。
  async onPullDownRefresh() {
    await this.load()
    wx.stopPullDownRefresh()
  },
  load() {
    return loadPageData(this, fetchRooms, (rooms) => ({
      status: rooms.length ? 'ready' : 'empty',
      rooms,
    }))
  },
  retry() {
    this.load()
  },
  openRoom(event) {
    wx.navigateTo({ url: `/pages/rooms/detail?id=${event.currentTarget.dataset.id}` })
  },
  askHost() {
    openServiceChat()
  },
})
