// 到店动作（到店体验 Spec A2）：首页快捷导航第一格与关于页位置行共用同一判断，
// 同一份门店资料在两页给出同一个动作，不再出现「首页写导航、点进去却什么都没有」。

const ABOUT_PAGE = '/pages/me/about'
const COPY_FAILED = '复制没有成功，可在「关于本店」长按地址复制'

// store 为 fetchHome 整理后的门店信息，可能为 null（后台没填）。
// map：有成对坐标，打开微信地图（地址可空）；copy：只有文字地址，复制地址；
// ask：坐标和地址都没有，由页面渲染原生客服按钮问管家怎么到店——即使还填了简介，简介也不能带客人到门口。
function arrivalAction(store) {
  if (store && store.location) return 'map'
  if (store && store.address) return 'copy'
  return 'ask'
}

// 把文字地址放进剪贴板并给出反馈。setClipboardData 属于微信隐私接口，
// 小程序隐私指引里没有声明「剪切板」时会失败，失败时告诉客人改用长按复制，而不是静默无反应。
function copyStoreAddress(address) {
  wx.setClipboardData({
    data: address,
    success: () => wx.showToast({ title: '地址已复制', icon: 'none' }),
    fail: () => wx.showToast({ title: COPY_FAILED, icon: 'none' }),
  })
}

// 弹框给出完整地址，由客人选择「复制地址」或「关闭」；只在客人确认后才写剪贴板，
// 不替换客人原先复制的内容（实施复审 F1）。首页只有文字地址时与地图打不开时共用。
function offerAddressCopy(address, title = '门店地址') {
  wx.showModal({
    title,
    content: address,
    confirmText: '复制地址',
    cancelText: '关闭',
    success: (res) => { if (res.confirm) copyStoreAddress(address) },
  })
}

// 只在 arrivalAction 为 map 时调用。地图打不开且有文字地址时弹框给出地址、由客人决定是否复制；
// 没有地址时只能提示。
function openStoreMap(store) {
  wx.openLocation({
    latitude: store.location.latitude,
    longitude: store.location.longitude,
    name: store.name,
    address: store.address,
    fail: () => {
      if (store.address) offerAddressCopy(store.address, '地图暂时打不开')
      else wx.showToast({ title: '暂时无法打开地图', icon: 'none' })
    },
  })
}

module.exports = { arrivalAction, openStoreMap, offerAddressCopy, copyStoreAddress, ABOUT_PAGE }
