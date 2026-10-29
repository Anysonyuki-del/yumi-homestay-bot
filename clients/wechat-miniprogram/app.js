// app.js
App({
  onLaunch() {
    // 展示本地存储能力
    const logs = wx.getStorageSync('logs') || []
    logs.unshift(Date.now())
    wx.setStorageSync('logs', logs)

    // 登录由首页按钮经 utils/auth.js 的 ensureLogin() 按需发起，这里不再自动调用 wx.login。
  },
  globalData: {
    userInfo: null
  }
})
