// 小程序登录客户端行为测试：用 wx 替身验证会话存储、恢复、退出和首页状态。
// 运行：node --test tests/miniprogram/auth.test.js（由 tests/unit/test_miniprogram_client_js.py 调用）
const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const CLIENT_ROOT = path.resolve(__dirname, '../../clients/wechat-miniprogram')
const auth = require(path.join(CLIENT_ROOT, 'utils/auth.js'))

const TOKEN = 'a'.repeat(43)
const FUTURE = '2999-01-01T00:00:00+00:00'

// 构造 wx 替身：routes 按「方法 路径」返回响应或网络失败，请求按顺序记录。
function installWx({
  routes = {},
  storage = {},
  failSet = false,
  failGet = false,
  failRemove = false,
  loginCode = 'code-1',
} = {}) {
  const calls = []
  const pending = []
  globalThis.wx = {
    login({ success, fail }) {
      calls.push('wx.login')
      if (loginCode) success({ code: loginCode })
      else fail({ errMsg: 'login:fail' })
    },
    request(options) {
      const key = `${options.method} ${options.url.replace(auth.API_BASE_URL, '')}`
      calls.push({ key, header: options.header, data: options.data })
      const route = routes[key]
      const respond = () => {
        if (!route || route.fail) options.fail({ errMsg: route && route.timeout ? 'request:fail timeout' : 'request:fail' })
        else options.success({ statusCode: route.statusCode, data: route.data })
      }
      if (route && route.hold) pending.push(respond)
      else setImmediate(respond)
    },
    getStorageSync(key) {
      if (failGet) throw new Error('storage unavailable')
      return storage[key]
    },
    setStorageSync(key, value) {
      if (failSet) throw new Error('quota exceeded')
      storage[key] = value
    },
    removeStorageSync(key) {
      if (failRemove) throw new Error('storage unavailable')
      delete storage[key]
    },
  }
  return { calls, storage, pending }
}

// auth.js 在模块内记住已失效的 token，每个用例用不同 token，避免用例之间互相影响。
let tokenSerial = 0
function nextToken() {
  tokenSerial += 1
  return String(tokenSerial).padStart(43, 't')
}

const storedSession = (token = nextToken()) => ({
  [auth.SESSION_STORAGE_KEY]: { token, expiresAt: FUTURE, displayName: '小程序客户' },
})

test('restore 没有本机会话时不发任何请求', async () => {
  const { calls } = installWx()
  assert.deepEqual(await auth.restore(), { status: 'logged_out' })
  assert.deepEqual(calls, [])
})

test('restore 带 Bearer 核验，401 时清除本机会话', async () => {
  const token = nextToken()
  const { calls, storage } = installWx({
    storage: storedSession(token),
    routes: { 'GET /api/miniprogram/me': { statusCode: 401, data: {} } },
  })
  const result = await auth.restore()
  assert.equal(result.status, 'logged_out')
  assert.equal(calls[0].header.Authorization, `Bearer ${token}`)
  assert.equal(storage[auth.SESSION_STORAGE_KEY], undefined)
})

test('restore 网络失败时保留本机会话，状态为待确认', async () => {
  const { storage } = installWx({
    storage: storedSession(),
    routes: { 'GET /api/miniprogram/me': { fail: true } },
  })
  const result = await auth.restore()
  assert.equal(result.status, 'unknown')
  assert.ok(storage[auth.SESSION_STORAGE_KEY])
})

test('restore 本地已过期的会话直接清除，不请求服务端', async () => {
  const storage = {
    [auth.SESSION_STORAGE_KEY]: { token: nextToken(), expiresAt: '2000-01-01T00:00:00+00:00' },
  }
  const { calls } = installWx({ storage })
  assert.equal((await auth.restore()).status, 'logged_out')
  assert.deepEqual(calls, [])
  assert.equal(storage[auth.SESSION_STORAGE_KEY], undefined)
})

test('ensureLogin 取 code 登录并保存会话，返回值不含 token', async () => {
  const { calls, storage } = installWx({
    routes: {
      'POST /api/miniprogram/login': {
        statusCode: 200,
        data: { token: TOKEN, expires_at: FUTURE, display_name: '小程序客户' },
      },
    },
  })
  const result = await auth.ensureLogin()
  assert.deepEqual(result, { displayName: '小程序客户', expiresAt: FUTURE })
  assert.deepEqual(calls[1].data, { code: 'code-1' })
  assert.equal(storage[auth.SESSION_STORAGE_KEY].token, TOKEN)
})

test('ensureLogin 已有未过期会话时不重复登录', async () => {
  const { calls } = installWx({ storage: storedSession() })
  assert.equal((await auth.ensureLogin()).displayName, '小程序客户')
  assert.deepEqual(calls, [])
})

test('登录后本机存储失败：撤销刚签发的会话并报错，不留下登录状态', async () => {
  const { calls, storage } = installWx({
    failSet: true,
    routes: {
      'POST /api/miniprogram/login': {
        statusCode: 200,
        data: { token: TOKEN, expires_at: FUTURE, display_name: '小程序客户' },
      },
      'POST /api/miniprogram/logout': { statusCode: 204 },
    },
  })
  await assert.rejects(auth.ensureLogin(), (error) => error.code === 'storage')
  const logout = calls.find((call) => call.key === 'POST /api/miniprogram/logout')
  assert.equal(logout.header.Authorization, `Bearer ${TOKEN}`)
  assert.equal(storage[auth.SESSION_STORAGE_KEY], undefined)
})

test('高风险拦截不可重试，code 无效可重试', async () => {
  installWx({ routes: { 'POST /api/miniprogram/login': { statusCode: 403, data: { error: 'login_blocked' } } } })
  await assert.rejects(auth.ensureLogin(), (error) => error.code === 'login_blocked' && error.retryable === false)
  installWx({ routes: { 'POST /api/miniprogram/login': { statusCode: 401, data: { error: 'code_invalid' } } } })
  await assert.rejects(auth.ensureLogin(), (error) => error.code === 'code_invalid' && error.retryable === true)
})

test('logout 服务端确认后才清除本机；失败时保留并报错', async () => {
  const failing = installWx({
    storage: storedSession(),
    routes: { 'POST /api/miniprogram/logout': { statusCode: 503, data: { error: 'revoke_failed' } } },
  })
  await assert.rejects(auth.logout(), (error) => error.code === 'revoke_failed')
  assert.ok(failing.storage[auth.SESSION_STORAGE_KEY])

  const ok = installWx({
    storage: storedSession(),
    routes: { 'POST /api/miniprogram/logout': { statusCode: 204 } },
  })
  await auth.logout()
  assert.equal(ok.storage[auth.SESSION_STORAGE_KEY], undefined)
})

test('登录后存储失败且撤销未确认时，如实提示未能确认撤销', async () => {
  installWx({
    failSet: true,
    routes: {
      'POST /api/miniprogram/login': {
        statusCode: 200,
        data: { token: nextToken(), expires_at: FUTURE, display_name: '小程序客户' },
      },
      'POST /api/miniprogram/logout': { statusCode: 503, data: { error: 'revoke_failed' } },
    },
  })
  await assert.rejects(
    auth.ensureLogin(),
    (error) => error.code === 'storage' && error.message.includes('未能确认撤销'),
  )
})

test('logout 读取本机记录失败时报错，不跳过服务端撤销', async () => {
  const { calls } = installWx({ storage: storedSession(), failGet: true })
  await assert.rejects(auth.logout(), (error) => error.code === 'storage_read')
  assert.deepEqual(calls, [])
})

test('restore 读取本机记录失败时状态待确认，不当作未登录', async () => {
  const { calls } = installWx({ storage: storedSession(), failGet: true })
  const result = await auth.restore()
  assert.equal(result.status, 'unknown')
  assert.deepEqual(calls, [])
})

test('401 后本机记录清除失败，失效记录不会被 ensureLogin 当成有效会话', async () => {
  const { calls, storage } = installWx({
    storage: storedSession(),
    failRemove: true,
    routes: {
      'GET /api/miniprogram/me': { statusCode: 401, data: {} },
      'POST /api/miniprogram/login': {
        statusCode: 200,
        data: { token: nextToken(), expires_at: FUTURE, display_name: '小程序客户' },
      },
    },
  })
  assert.equal((await auth.restore()).status, 'logged_out')
  assert.ok(storage[auth.SESSION_STORAGE_KEY], '替身中清除确实失败，旧记录仍在')
  await auth.ensureLogin()
  assert.ok(calls.includes('wx.login'), '必须重新登录，而不是复用失效记录')
})

test('退出时服务端已撤销但本机清除失败，重试退出即完成', async () => {
  const { calls } = installWx({
    storage: storedSession(),
    failRemove: true,
    routes: { 'POST /api/miniprogram/logout': { statusCode: 204 } },
  })
  await assert.rejects(auth.logout(), (error) => error.code === 'storage')
  await auth.logout()
  assert.equal(calls.filter((call) => call.key === 'POST /api/miniprogram/logout').length, 1)
})

// 加载首页：用 Page 替身拿到页面配置，setData 合并到 data。
function loadIndexPage() {
  let config
  globalThis.Page = (options) => { config = options }
  const pagePath = path.join(CLIENT_ROOT, 'pages/index/index.js')
  delete require.cache[require.resolve(pagePath)]
  require(pagePath)
  const page = Object.create(config)
  page.data = JSON.parse(JSON.stringify(config.data))
  page.setData = (patch) => { Object.assign(page.data, patch) }
  return page
}

test('首页重复点击登录只发起一次 wx.login', async () => {
  const { calls, pending } = installWx({
    routes: {
      'POST /api/miniprogram/login': {
        hold: true,
        statusCode: 200,
        data: { token: TOKEN, expires_at: FUTURE, display_name: '小程序客户' },
      },
    },
  })
  const page = loadIndexPage()
  page.authGeneration = 0
  const first = page.handleLogin()
  const second = page.handleLogin()
  await new Promise((resolve) => setImmediate(resolve))
  pending.forEach((respond) => respond())
  await Promise.all([first, second])
  assert.equal(calls.filter((call) => call === 'wx.login').length, 1)
  assert.equal(page.data.authStatus, 'logged_in')
  assert.equal(page.data.authBusy, false)
})

test('首页退出失败时保持已登录并给出提示', async () => {
  installWx({
    storage: storedSession(),
    routes: { 'POST /api/miniprogram/logout': { fail: true } },
  })
  const page = loadIndexPage()
  page.authGeneration = 0
  page.setData({ authStatus: 'logged_in', displayName: '小程序客户' })
  await page.handleLogout()
  assert.equal(page.data.authStatus, 'logged_in')
  assert.match(page.data.authMessage, /网络/)
})

test('首页恢复时迟到的旧结果被丢弃', async () => {
  const { pending } = installWx({
    storage: storedSession(),
    routes: {
      'GET /api/miniprogram/me': {
        hold: true,
        statusCode: 200,
        data: { expires_at: FUTURE, display_name: '小程序客户' },
      },
    },
  })
  const page = loadIndexPage()
  page.authGeneration = 0
  const restoring = page.restoreLogin()
  await new Promise((resolve) => setImmediate(resolve))
  // 模拟恢复请求在途期间已开始更新的操作（例如页面重新加载后的新一轮确认）。
  page.authGeneration += 1
  page.setData({ authStatus: 'logged_out', authBusy: false })
  pending.forEach((respond) => respond())
  await restoring
  assert.equal(page.data.authStatus, 'logged_out')
})
