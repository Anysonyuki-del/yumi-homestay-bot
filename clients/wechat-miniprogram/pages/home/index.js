// 首页：横幅轮播、门店标题区、快捷导航与房间大图卡片，点房间进详情（三标签 Spec F2，快捷导航 Spec N1、N2）。
// 门店区与房间区分别加载、分别出错（到店体验 Spec A1）：房间接口故障时，导航、指南和联系管家仍然可用。
const { fetchHome, fetchRooms, loadPageData, discardPendingLoads } = require('../../utils/content')
const { specLine } = require('../../utils/format')
const { arrivalAction, openStoreMap, offerAddressCopy, ABOUT_PAGE } = require('../../utils/store-map')

// 后台没填门店名时的品牌名，与 app.json 的默认导航栏标题一致（质感 Spec D3）。
const BRAND_NAME = 'YuMi 民宿'

// 快捷导航第一格随到店动作变化（到店体验 Spec A2），其余三格固定（快捷导航 Spec D1）。
// unknown 表示门店资料还没取到（加载中或失败）：点了进关于页，由关于页自己加载和重试。
// contact 为真的格子渲染成原生客服按钮，只能由客人点按钮打开会话，不经 openQuickNav（客服按钮 Spec C1）。
const ARRIVAL_ITEMS = {
  unknown: { key: 'map', label: '到店导航', icon: 'location' },
  map: { key: 'map', label: '到店导航', icon: 'location' },
  copy: { key: 'map', label: '门店地址', icon: 'location' },
  ask: { key: 'map', label: '问怎么到店', icon: 'location', contact: true },
}
const FIXED_ITEMS = [
  { key: 'guide', label: '入住指南', icon: 'book' },
  { key: 'host', label: '联系管家', icon: 'chat', contact: true },
  { key: 'about', label: '关于本店', icon: 'shop' },
]

// 按到店动作生成四格快捷导航。
function quickNavFor(arrival) {
  return [ARRIVAL_ITEMS[arrival], ...FIXED_ITEMS]
}

const HOME_SECTION = { statusKey: 'homeStatus', messageKey: 'homeMessage' }
const ROOMS_SECTION = { statusKey: 'roomsStatus', messageKey: 'roomsMessage' }

Page({
  data: {
    // 门店区 homeStatus：loading / ready / error；房间区 roomsStatus：loading / ready / empty / error。
    homeStatus: 'loading',
    homeMessage: '',
    roomsStatus: 'loading',
    roomsMessage: '',
    store: null,
    banners: [],
    rooms: [],
    arrival: 'unknown',
    swiperNavigation: { type: 'dots' },
    brandName: BRAND_NAME,
    quickNav: quickNavFor('unknown'),
  },
  onLoad() {
    this.load()
  },
  onUnload() {
    discardPendingLoads(this)
  },
  // 下拉刷新与首次加载同时取两个区块；两个区块都落定后才收起下拉动画。
  async onPullDownRefresh() {
    await this.load()
    wx.stopPullDownRefresh()
  },
  load() {
    return Promise.all([this.loadHome(), this.loadRooms()])
  },
  // 门店区：成功时决定导航栏标题和到店动作。开始加载时先把到店动作退回 unknown，
  // 刷新失败就不会沿用上一次的门店资料（失败时整区按出错显示）。
  loadHome() {
    this.setData({ arrival: 'unknown', quickNav: quickNavFor('unknown') })
    return loadPageData(this, fetchHome, (home) => {
      // 导航栏显示门店名，没填时显示品牌名（质感 Spec T2）。
      wx.setNavigationBarTitle({ title: (home.store && home.store.name) || BRAND_NAME })
      const arrival = arrivalAction(home.store)
      return {
        homeStatus: 'ready',
        store: home.store,
        banners: home.banners,
        arrival,
        quickNav: quickNavFor(arrival),
      }
    }, HOME_SECTION)
  },
  loadRooms() {
    return loadPageData(this, fetchRooms, (rooms) => ({
      roomsStatus: rooms.length ? 'ready' : 'empty',
      // coverLoaded 控制封面淡入；coverFailed 表示封面地址有但图片没加载出来，卡片改用紧凑布局（到店体验 Spec R3）。
      rooms: rooms.map((room) => ({ ...room, specLine: specLine(room), coverLoaded: false, coverFailed: false })),
    }), ROOMS_SECTION)
  },
  retryHome() {
    this.loadHome()
  },
  // 状态块按钮只剩房间区出错时的重试，只重取房间；空列表时的「问管家」是原生客服按钮，不经这里（客服按钮 Spec C1）。
  onStateAction() {
    return this.loadRooms()
  },
  openRoom(event) {
    wx.navigateTo({ url: `/pages/rooms/detail?id=${event.currentTarget.dataset.id}` })
  },
  // 快捷导航：第一格按到店动作开地图、看并复制地址或进关于页；入住指南是底部标签页，只能 switchTab。
  openQuickNav(event) {
    const { key } = event.currentTarget.dataset
    if (key === 'map') return this.openArrival()
    if (key === 'guide') return wx.switchTab({ url: '/pages/services/index' })
    if (key === 'about') return wx.navigateTo({ url: ABOUT_PAGE })
  },
  openArrival() {
    const { arrival, store } = this.data
    if (arrival === 'map') return openStoreMap(store)
    // 只有文字地址时：弹框给出完整地址，客人读完一次点击即可复制（到店体验 Spec A2）。
    if (arrival === 'copy') return offerAddressCopy(store.address)
    return wx.navigateTo({ url: ABOUT_PAGE })
  },
  // 封面事件按房间编号与封面地址找回当前卡片（实施复审 F2）：列表刷新或换序后，旧图片迟到的
  // load/error 事件对不上当前那一项，直接忽略，不能按下标改到另一间房或另一张封面上。
  markCover(event, field) {
    const { id, cover } = event.currentTarget.dataset
    // 编号按字符串比：dataset 取值类型随基础库可能不同，比错会让封面永远不淡入。
    const index = this.data.rooms.findIndex((room) => String(room.id) === String(id) && room.cover === cover)
    if (index >= 0) this.setData({ [`rooms[${index}].${field}`]: true })
  },
  // 封面加载完成后才显示，配合样式里的淡入过渡。
  onCoverLoad(event) {
    this.markCover(event, 'coverLoaded')
  },
  // 封面地址失效时改用紧凑卡片，房名和规格直接露出来，不留一块空白图位。
  onCoverError(event) {
    this.markCover(event, 'coverFailed')
  },
})
