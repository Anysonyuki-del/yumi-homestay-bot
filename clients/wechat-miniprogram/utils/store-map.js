// 到店导航（首页快捷导航 Spec N1）：首页「到店导航」与关于页地址行共用。
// 后台填了经纬度才打开微信地图；没有坐标时进入关于页，那里能看到并复制地址。

const ABOUT_PAGE = '/pages/me/about'

// store 为 fetchHome 整理后的门店信息，可能为 null。
// fromAbout 为真表示已经在关于页：没有坐标时什么都不做，避免再打开一个关于页。
function openStoreMap(store, { fromAbout = false } = {}) {
  if (!store || !store.location) {
    if (!fromAbout) wx.navigateTo({ url: ABOUT_PAGE })
    return
  }
  wx.openLocation({
    latitude: store.location.latitude,
    longitude: store.location.longitude,
    name: store.name,
    address: store.address,
    fail: () => wx.showToast({ title: '暂时无法打开地图', icon: 'none' }),
  })
}

module.exports = { openStoreMap, ABOUT_PAGE }
