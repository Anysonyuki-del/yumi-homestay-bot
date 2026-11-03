// 房间详情：照片轮播、基本信息、简介与设施；不显示价格房态，预订咨询转管家（Spec F5）。
const { fetchRoom, loadPageData, discardPendingLoads } = require('../../utils/content')
const { specLine } = require('../../utils/format')

Page({
  data: {
    // gone：房间已下架或不存在，不提供重试。
    status: 'loading',
    message: '',
    room: null,
    skeletonRows: [{ width: '56%', height: '48rpx' }, { width: '40%' }, 1, 1, { width: '70%' }],
    // 多张照片时右下角显示「1/5」，让客人知道还能往后翻。
    swiperNavigation: { type: 'fraction' },
  },
  onLoad(options) {
    this.roomId = options.id
    this.load()
  },
  onUnload() {
    discardPendingLoads(this)
  },
  load() {
    return loadPageData(this, () => fetchRoom(this.roomId), (room) => {
      wx.setNavigationBarTitle({ title: room.name })
      return { status: 'ready', room: { ...room, specLine: specLine(room) } }
    })
  },
  retry() {
    this.load()
  },
  // 点轮播图看大图，可左右滑动浏览全部照片。
  previewPhoto(event) {
    const photos = this.data.room ? this.data.room.photos : []
    const current = photos[event.detail.index]
    if (current) wx.previewImage({ current, urls: photos })
  },
  // 状态块按钮：下架时回列表，出错时重试。
  onStateAction() {
    if (this.data.status === 'gone') this.backToList()
    else this.retry()
  },
  backToList() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/home/index' }) })
  },
})
