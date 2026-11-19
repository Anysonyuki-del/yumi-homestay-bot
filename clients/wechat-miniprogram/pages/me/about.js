// 关于本店：民宿名、简介、到店方式与电话；有电话才提供拨号（三标签 Spec F5）。
// 到店方式与首页快捷导航第一格同源（到店体验 Spec A2）：有坐标导航、只有地址复制、都没有问管家。
const { fetchHome, loadPageData, discardPendingLoads } = require('../../utils/content')
const { dialNumber } = require('../../utils/format')
const { arrivalAction, openStoreMap, copyStoreAddress } = require('../../utils/store-map')

Page({
  data: {
    // ready：有门店信息；empty：后台还没填；error：加载失败可重试。
    status: 'loading',
    message: '',
    store: null,
    // 到店动作：map / copy / ask，决定位置行显示什么、点了做什么。
    arrival: 'ask',
    skeletonRows: [{ width: '48%', height: '48rpx' }, 1, 1, { width: '70%' }],
  },
  onLoad() {
    this.load()
  },
  onUnload() {
    discardPendingLoads(this)
  },
  load() {
    return loadPageData(this, fetchHome, (home) => ({
      status: home.store ? 'ready' : 'empty',
      store: home.store,
      arrival: arrivalAction(home.store),
    }))
  },
  retry() {
    this.load()
  },
  // 状态块按钮只剩出错时的重试；没有门店信息时的「问管家」是原生客服按钮，不经这里（客服按钮 Spec C1）。
  onStateAction() {
    this.retry()
  },
  // 位置行：有坐标打开地图，只有地址时复制地址；ask 时那一行是原生客服按钮，不经这里。
  openArrival() {
    const { arrival, store } = this.data
    if (arrival === 'map') openStoreMap(store)
    else if (arrival === 'copy') copyStoreAddress(store.address)
  },
  // 客人取消拨号也会进 fail，因此失败时不提示。
  callStore() {
    const number = dialNumber(this.data.store && this.data.store.phone)
    if (number) wx.makePhoneCall({ phoneNumber: number, fail: () => {} })
  },
})
