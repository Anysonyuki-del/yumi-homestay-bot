// 首页：横幅轮播、门店标题区、快捷导航与房间大图卡片，点房间进详情（三标签 Spec F2，快捷导航 Spec N1、N2）。
const { fetchHome, fetchRooms, loadPageData, discardPendingLoads } = require('../../utils/content')
const { openServiceChat } = require('../../utils/service-chat')
const { specLine } = require('../../utils/format')
const { openStoreMap, ABOUT_PAGE } = require('../../utils/store-map')

// 后台没填门店名时的品牌名，与 app.json 的默认导航栏标题一致（质感 Spec D3）。
const BRAND_NAME = 'YuMi 民宿'

// 快捷导航的四个固定入口（快捷导航 Spec D1）：顺序即显示顺序，key 决定点击后做什么。
const QUICK_NAV = [
  { key: 'map', label: '到店导航', icon: 'location' },
  { key: 'guide', label: '入住指南', icon: 'book' },
  { key: 'host', label: '联系管家', icon: 'chat' },
  { key: 'about', label: '关于本店', icon: 'shop' },
]

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
    brandName: BRAND_NAME,
    quickNav: QUICK_NAV,
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
    return loadPageData(this, fetchHomePage, ([home, rooms]) => {
      // 导航栏显示门店名，没填时显示品牌名（质感 Spec T2）。
      wx.setNavigationBarTitle({ title: (home.store && home.store.name) || BRAND_NAME })
      return {
        status: rooms.length ? 'ready' : 'empty',
        store: home.store,
        banners: home.banners,
        // coverLoaded 控制封面淡入，图片加载完成前保持透明。
        rooms: rooms.map((room) => ({ ...room, specLine: specLine(room), coverLoaded: false })),
      }
    })
  },
  retry() {
    this.load()
  },
  // 状态块按钮：出错时重试，空列表时问管家。
  onStateAction() {
    if (this.data.status === 'error') return this.retry()
    return this.askHost()
  },
  openRoom(event) {
    wx.navigateTo({ url: `/pages/rooms/detail?id=${event.currentTarget.dataset.id}` })
  },
  // 快捷导航：到店导航按有无坐标决定开地图还是进关于页；入住指南是底部标签页，只能 switchTab。
  openQuickNav(event) {
    const { key } = event.currentTarget.dataset
    if (key === 'map') return openStoreMap(this.data.store)
    if (key === 'guide') return wx.switchTab({ url: '/pages/services/index' })
    if (key === 'about') return wx.navigateTo({ url: ABOUT_PAGE })
    return this.askHost()
  },
  // 封面加载完成后才显示，配合样式里的淡入过渡。
  onCoverLoad(event) {
    this.setData({ [`rooms[${event.currentTarget.dataset.index}].coverLoaded`]: true })
  },
  askHost() {
    return openServiceChat()
  },
})
