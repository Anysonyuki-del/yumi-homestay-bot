// 首页：横幅轮播、门店标题区与左图右文的房间列表，点房间进详情（三标签 Spec F2）。
const { fetchHome, fetchRooms, loadPageData, discardPendingLoads } = require('../../utils/content')
const { openServiceChat } = require('../../utils/service-chat')
const { specLine } = require('../../utils/format')

// 首页同时要门店信息和房间；任一失败整页按出错处理，避免只显示一半内容。
function fetchHomePage() {
  return Promise.all([fetchHome(), fetchRooms()])
}

Page({
  data: {
    // loading：加载中；ready：有房间；empty：没有展示的房间（横幅与门店信息照常显示）；error：加载失败可重试。
    status: 'loading',
    message: '',
    store: null,
    banners: [],
    rooms: [],
    swiperNavigation: { type: 'dots' },
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
    return loadPageData(this, fetchHomePage, ([home, rooms]) => ({
      status: rooms.length ? 'ready' : 'empty',
      store: home.store,
      banners: home.banners,
      rooms: rooms.map((room) => ({ ...room, specLine: specLine(room) })),
    }))
  },
  retry() {
    this.load()
  },
  // 状态块按钮：出错时重试，空列表时问管家。
  onStateAction() {
    if (this.data.status === 'error') this.retry()
    else this.askHost()
  },
  openRoom(event) {
    wx.navigateTo({ url: `/pages/rooms/detail?id=${event.currentTarget.dataset.id}` })
  },
  askHost() {
    openServiceChat()
  },
})
