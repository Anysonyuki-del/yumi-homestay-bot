// index.js
const { API_BASE_URL, ensureLogin, restore, logout } = require('../../utils/auth')

// 把服务端返回的 ISO 到期时间转成「月-日 时:分」，解析失败时不显示。
function formatExpiry(isoText) {
  const date = new Date(isoText)
  if (!isoText || Number.isNaN(date.getTime())) return ''
  const pad = (value) => String(value).padStart(2, '0')
  return `登录有效期至 ${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

Page({
  data: {
    motto: 'Hello World',
    connectionLoading: false,
    connectionResult: '尚未测试后端连接',
    // checking：确认中；logged_out：未登录；logged_in：已登录；unknown：网络等原因暂时无法确认。
    authStatus: 'checking',
    authBusy: false,
    authMessage: '',
    displayName: '',
    expiresText: '',
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
  // 重新打开时只核验本机已有 token；没有 token 时 restore 不发请求。
  async restoreLogin() {
    if (this.data.authBusy) return
    const generation = this.beginAuthOperation('正在确认登录状态…')
    let result
    try {
      result = await restore()
    } catch (error) {
      result = { status: 'unknown', message: '暂时无法确认登录状态，请稍后重试' }
    }
    if (generation !== this.authGeneration) return
    this.setData({
      authBusy: false,
      authStatus: result.status,
      authMessage: result.message || '',
      displayName: result.displayName || '',
      expiresText: formatExpiry(result.expiresAt),
    })
  },
  // 登录按钮：本期由客人主动发起，统一走 ensureLogin()，以后的按需登录复用同一入口。
  async handleLogin() {
    if (this.data.authBusy) return
    const generation = this.beginAuthOperation('正在登录…')
    try {
      const session = await ensureLogin()
      if (generation !== this.authGeneration) return
      this.setData({
        authBusy: false,
        authStatus: 'logged_in',
        authMessage: '登录成功',
        displayName: session.displayName || '',
        expiresText: formatExpiry(session.expiresAt),
      })
    } catch (error) {
      if (generation !== this.authGeneration) return
      this.setData({
        authBusy: false,
        authStatus: 'logged_out',
        authMessage: error.message || '登录暂时不可用，请稍后重试',
      })
    }
  },
  // 退出只在服务端确认后才显示已退出；失败时保持原状态并提示重试。
  async handleLogout() {
    if (this.data.authBusy) return
    const generation = this.beginAuthOperation('正在退出…')
    try {
      await logout()
      if (generation !== this.authGeneration) return
      this.setData({
        authBusy: false,
        authStatus: 'logged_out',
        authMessage: '已退出登录',
        displayName: '',
        expiresText: '',
      })
    } catch (error) {
      if (generation !== this.authGeneration) return
      this.setData({
        authBusy: false,
        authMessage: error.message || '退出未完成，请重试',
      })
    }
  },
  // 只读取公开健康接口；同时核对状态码和响应内容，避免把降级或错误页面当作服务正常。
  testConnection() {
    if (this.data.connectionLoading) return
    this.setData({ connectionLoading: true, connectionResult: '正在连接 YuMi 后端…' })
    wx.request({
      url: `${API_BASE_URL}/health`,
      method: 'GET',
      timeout: 10000,
      // HTTP 503 也会进入 success，表示收到响应，仍须独立判断健康状态。
      success: (res) => {
        const status = res.data && res.data.status
        let result = `服务器已响应，但健康检查结果异常（HTTP ${res.statusCode}）`
        if (res.statusCode === 200 && status === 'ok') {
          result = '连接成功，后端服务正常（HTTP 200）'
        } else if (res.statusCode === 503 && status === 'degraded') {
          result = '已连到 YuMi 后端，服务当前处于降级状态（HTTP 503）'
        }
        this.setData({ connectionResult: result })
      },
      // 请求失败只显示排查方向，不把底层异常或服务器正文直接呈现给用户。
      fail: (error) => {
        const timedOut = (error.errMsg || '').includes('timeout')
        this.setData({
          connectionResult: timedOut
            ? '连接超时，请检查网络后重试'
            : '请求失败，请检查网络和 request 合法域名后重试',
        })
      },
      // 无论成功还是失败都恢复按钮，让用户能够主动重试。
      complete: () => this.setData({ connectionLoading: false }),
    })
  },
})
