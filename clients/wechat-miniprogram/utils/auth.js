// 客人登录会话：集中处理后端地址、微信登录、恢复、退出与本机存储。
// 页面只调用这里导出的函数，不直接读写 token；将来一键入住等功能直接调用 ensureLogin()。

const API_BASE_URL = 'https://akros.icu'
const SESSION_STORAGE_KEY = 'yumi_guest_session'
const REQUEST_TIMEOUT_MS = 10000

// 后端错误码到客人可读提示；未列出的情况统一给出可重试提示，不展示服务器原文。
const LOGIN_ERROR_MESSAGES = {
  code_invalid: '登录凭证已失效，请重新登录',
  login_blocked: '当前微信账号暂时无法登录，请联系管家',
  rate_limited: '登录人数较多，请稍后重试',
  upstream_busy: '微信登录服务繁忙，请稍后重试',
  not_configured: '小程序登录暂未开放',
}
const DEFAULT_LOGIN_ERROR = '登录暂时不可用，请稍后重试'

// 带分类的错误：页面据 message 展示，据 retryable 决定是否提示重试。
class AuthError extends Error {
  constructor(code, message, retryable = true) {
    super(message)
    this.code = code
    this.retryable = retryable
  }
}

// 把 wx.request 包成 Promise；网络失败与超时以 AuthError 拒绝，HTTP 错误码照常返回给调用方判断。
function request({ path, method, data, token }) {
  return new Promise((resolve, reject) => {
    const header = { 'content-type': 'application/json' }
    if (token) header.Authorization = `Bearer ${token}`
    wx.request({
      url: `${API_BASE_URL}${path}`,
      method,
      data,
      header,
      timeout: REQUEST_TIMEOUT_MS,
      success: (res) => resolve(res),
      fail: (error) => {
        const timedOut = ((error && error.errMsg) || '').includes('timeout')
        reject(new AuthError(
          timedOut ? 'timeout' : 'network',
          timedOut ? '连接超时，请检查网络后重试' : '网络异常，请检查网络后重试',
        ))
      },
    })
  })
}

// 取微信临时登录凭证；code 只能用一次，每次登录都重新获取。
function wxLoginCode() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (res) => {
        if (res && res.code) resolve(res.code)
        else reject(new AuthError('wx_login', '微信登录失败，请重试'))
      },
      fail: () => reject(new AuthError('wx_login', '微信登录失败，请重试')),
    })
  })
}

// 读取本机会话；存储读取失败或内容不完整时按未登录处理。
function readStoredSession() {
  try {
    const stored = wx.getStorageSync(SESSION_STORAGE_KEY)
    if (stored && typeof stored.token === 'string' && typeof stored.expiresAt === 'string') {
      return stored
    }
  } catch (error) {
    // 读取失败不能当作已登录；交由调用方展示登录入口。
  }
  return null
}

// 清除本机会话，返回是否成功，供调用方如实反馈。
function clearStoredSession() {
  try {
    wx.removeStorageSync(SESSION_STORAGE_KEY)
    return true
  } catch (error) {
    return false
  }
}

// 本地时钟只用于跳过明显过期的会话，是否有效始终以服务端核验为准。
function isLocallyExpired(session) {
  const expiresAt = Date.parse(session.expiresAt)
  return Number.isNaN(expiresAt) || expiresAt <= Date.now()
}

// 登录：取 code → 后端校验并签发 token → 存入本机。存储失败时撤销刚签发的会话，避免留下不可恢复的登录。
async function login() {
  const code = await wxLoginCode()
  const res = await request({ path: '/api/miniprogram/login', method: 'POST', data: { code } })
  const body = res.data || {}
  if (res.statusCode !== 200 || typeof body.token !== 'string') {
    const errorCode = body.error || 'unavailable'
    throw new AuthError(
      errorCode,
      LOGIN_ERROR_MESSAGES[errorCode] || DEFAULT_LOGIN_ERROR,
      errorCode !== 'login_blocked',
    )
  }
  const session = { token: body.token, expiresAt: body.expires_at, displayName: body.display_name }
  try {
    wx.setStorageSync(SESSION_STORAGE_KEY, session)
  } catch (error) {
    // 撤销失败也只能如实提示；服务端会话会在 24 小时后自然过期。
    await request({ path: '/api/miniprogram/logout', method: 'POST', token: body.token })
      .catch(() => null)
    throw new AuthError('storage', '本机存储失败，登录未保存，请清理微信存储后重试')
  }
  return { displayName: body.display_name, expiresAt: body.expires_at }
}

// 按需登录：本机已有未过期会话直接返回，否则发起登录；返回值不含 token，页面无需接触凭证。
async function ensureLogin() {
  const stored = readStoredSession()
  if (stored && !isLocallyExpired(stored)) {
    return { displayName: stored.displayName, expiresAt: stored.expiresAt }
  }
  return login()
}

// 恢复：没有本机会话时不发请求；401 清除本机状态；网络等故障保留本机会话，等待重试。
async function restore() {
  const stored = readStoredSession()
  if (!stored) return { status: 'logged_out' }
  if (isLocallyExpired(stored)) {
    clearStoredSession()
    return { status: 'logged_out', message: '登录已过期，请重新登录' }
  }
  let res
  try {
    res = await request({ path: '/api/miniprogram/me', method: 'GET', token: stored.token })
  } catch (error) {
    return { status: 'unknown', message: error.message }
  }
  if (res.statusCode === 200 && res.data) {
    return { status: 'logged_in', displayName: res.data.display_name, expiresAt: res.data.expires_at }
  }
  if (res.statusCode === 401) {
    clearStoredSession()
    return { status: 'logged_out', message: '登录已失效，请重新登录' }
  }
  return { status: 'unknown', message: '暂时无法确认登录状态，请稍后重试' }
}

// 退出：服务端确认撤销（204）或会话本已失效（401）后才清除本机；否则保留并提示重试。
async function logout() {
  const stored = readStoredSession()
  if (!stored) return
  const res = await request({ path: '/api/miniprogram/logout', method: 'POST', token: stored.token })
  if (res.statusCode !== 204 && res.statusCode !== 401) {
    throw new AuthError('revoke_failed', '退出未完成，请重试')
  }
  if (!clearStoredSession()) {
    throw new AuthError('storage', '服务端已退出，但本机记录清除失败，请重试')
  }
}

module.exports = {
  API_BASE_URL,
  SESSION_STORAGE_KEY,
  AuthError,
  ensureLogin,
  restore,
  logout,
}
