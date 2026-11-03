// 我的：微信登录与退出、联系管家、关于本店、我的订单入口（三标签 Spec F5）。
// 登录状态只经 utils/auth.js 读写；页面不接触 token，也不显示头像昵称。
const { ensureLogin, restore, logout } = require('../../utils/auth')

const ORDERS_COMING = '订单查询即将开放，现在可以问管家'

Page({
  data: {
    // checking：确认中；logged_out：未登录；logged_in：已登录；unknown：网络等原因暂时无法确认。
    authStatus: 'checking',
    authBusy: false,
    authMessage: '',
  },
  onLoad() {
    this.authGeneration = 0
    this.restoreLogin()
  },
  // 每次登录相关操作领取新代次；结果返回时代次已变说明有更新的操作，丢弃旧结果，
  // 避免退出后迟到的恢复响应把页面改回已登录。
  beginAuthOperation(message) {
    this.authGeneration += 1
    this.setData({ authBusy: true, authMessage: message })
    return this.authGeneration
  },
  // 打开页面时只核验本机已有会话；没有会话时 restore 不发请求。
  async restoreLogin() {
    if (this.data.authBusy) return
    const generation = this.beginAuthOperation('')
    let result
    try {
      result = await restore()
    } catch (error) {
      result = { status: 'unknown', message: '暂时无法确认登录状态，请稍后重试' }
    }
    if (generation !== this.authGeneration) return
    this.setData({ authBusy: false, authStatus: result.status, authMessage: result.message || '' })
  },
  // 登录按钮统一走 ensureLogin()：已有有效会话就复用，不重复签发。
  async handleLogin() {
    if (this.data.authBusy) return
    const generation = this.beginAuthOperation('')
    try {
      await ensureLogin()
      if (generation !== this.authGeneration) return
      this.setData({ authBusy: false, authStatus: 'logged_in', authMessage: '' })
    } catch (error) {
      if (generation !== this.authGeneration) return
      // 已有会话但暂时无法核验时本机记录仍保留，显示待确认，而不是误显示未登录。
      this.setData({
        authBusy: false,
        authStatus: error.code === 'verify_unknown' ? 'unknown' : 'logged_out',
        authMessage: error.message || '登录暂时不可用，请稍后重试',
      })
    }
  },
  // 退出只在服务端确认后才显示已退出；失败时保持原状态并提示重试。
  async handleLogout() {
    if (this.data.authBusy) return
    const generation = this.beginAuthOperation('')
    try {
      await logout()
      if (generation !== this.authGeneration) return
      this.setData({ authBusy: false, authStatus: 'logged_out', authMessage: '已退出登录' })
    } catch (error) {
      if (generation !== this.authGeneration) return
      this.setData({ authBusy: false, authMessage: error.message || '退出未完成，请重试' })
    }
  },
  openAbout() {
    wx.navigateTo({ url: '/pages/me/about' })
  },
  // 订单需要先解决「订单归属」核对，第二期接入；本期只给入口和去向（Spec D1）。
  // 入口排在菜单最后并标「即将开放」（质感 Spec T6），点了仍给出去向。
  openOrders() {
    wx.showToast({ title: ORDERS_COMING, icon: 'none' })
  },
})
