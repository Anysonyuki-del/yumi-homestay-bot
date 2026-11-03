// 关于本店：民宿名、简介、地址与电话；有坐标才提供导航，有电话才提供拨号（三标签 Spec F5）。
const { fetchHome, loadPageData, discardPendingLoads } = require('../../utils/content')
const { dialNumber } = require('../../utils/format')
const { openStoreMap } = require('../../utils/store-map')

Page({
  data: {
    // ready：有门店信息；empty：后台还没填；error：加载失败可重试。
    status: 'loading',
    message: '',
    store: null,
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
    }))
  },
  retry() {
    this.load()
  },
  // 状态块按钮只剩出错时的重试；没有门店信息时的「问管家」是原生客服按钮，不经这里（客服按钮 Spec C1）。
  onStateAction() {
    this.retry()
  },
  // 只有后台填了经纬度才打开地图；没有坐标时地址只供阅读和复制（与首页「到店导航」共用逻辑）。
  openMap() {
    openStoreMap(this.data.store, { fromAbout: true })
  },
  // 客人取消拨号也会进 fail，因此失败时不提示。
  callStore() {
    const number = dialNumber(this.data.store && this.data.store.phone)
    if (number) wx.makePhoneCall({ phoneNumber: number, fail: () => {} })
  },
})
