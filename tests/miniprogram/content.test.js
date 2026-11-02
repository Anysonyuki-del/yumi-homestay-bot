// 住客自助内容客户端与页面行为测试（小程序住客自助 Spec F5）：wx 与 Page 都用替身。
// 运行：node --test tests/miniprogram/（由 tests/unit/test_miniprogram_client_js.py 调用）
const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const CLIENT_ROOT = path.resolve(__dirname, '../../clients/wechat-miniprogram')
const content = require(path.join(CLIENT_ROOT, 'utils/content.js'))
const { API_BASE_URL } = require(path.join(CLIENT_ROOT, 'utils/auth.js'))

const ROOM = {
  id: 101, name: '山海大床房', bed_type: '1.8 米大床', max_guests: 2, area_sqm: 35,
  cover_url: '/api/miniprogram/rooms/101/photos/7',
}

// routes 按路径返回响应、网络失败，或挂起等测试手动放行；记录请求与客服、提示调用。
function installWx(routes = {}) {
  const calls = []
  const pending = []
  globalThis.wx = {
    request(options) {
      const key = options.url.replace(API_BASE_URL, '')
      calls.push(key)
      const route = routes[key]
      const respond = () => {
        if (!route || route.fail) options.fail({ errMsg: route && route.timeout ? 'request:fail timeout' : 'request:fail' })
        else options.success({ statusCode: route.statusCode, data: route.data })
      }
      if (route && route.hold) pending.push(respond)
      else setImmediate(respond)
    },
    showToast(options) { calls.push(`toast:${options.title}`) },
    stopPullDownRefresh() { calls.push('stopPullDownRefresh') },
    setNavigationBarTitle(options) { calls.push(`title:${options.title}`) },
  }
  return { calls, pending }
}

// 加载页面：用 Page 替身拿到页面配置，setData 合并到 data。
function loadPage(relativePath) {
  let config
  globalThis.Page = (options) => { config = options }
  const pagePath = path.join(CLIENT_ROOT, relativePath)
  delete require.cache[require.resolve(pagePath)]
  require(pagePath)
  const page = Object.create(config)
  page.data = JSON.parse(JSON.stringify(config.data))
  page.setData = (patch) => { Object.assign(page.data, patch) }
  return page
}

test('房间列表把站内照片路径拼成完整地址，并整理字段名', async () => {
  installWx({ '/api/miniprogram/rooms': { statusCode: 200, data: { rooms: [ROOM] } } })
  assert.deepEqual(await content.fetchRooms(), [{
    id: 101, name: '山海大床房', bedType: '1.8 米大床', maxGuests: 2, areaSqm: 35,
    cover: `${API_BASE_URL}/api/miniprogram/rooms/101/photos/7`,
  }])
})

test('结构不符、非 200 与网络失败都报可读错误，不透出服务器原文', async () => {
  const cases = [
    [{ statusCode: 200, data: { rooms: [{ ...ROOM, id: '101' }] } }, '加载失败，请稍后重试'],
    [{ statusCode: 200, data: { error: 'SELECT secret' } }, '加载失败，请稍后重试'],
    [{ statusCode: 503, data: { message: 'internal detail' } }, '加载失败，请稍后重试'],
    [{ fail: true }, '网络异常，请检查网络后重试'],
    [{ fail: true, timeout: true }, '连接超时，请检查网络后重试'],
  ]
  for (const [route, message] of cases) {
    installWx({ '/api/miniprogram/rooms': route })
    await assert.rejects(content.fetchRooms(), (error) => error.message === message)
  }
})

test('房间 404 报已下架；非数字编号不发请求', async () => {
  const { calls } = installWx({ '/api/miniprogram/rooms/9': { statusCode: 404, data: {} } })
  await assert.rejects(content.fetchRoom(9), (error) => error.statusCode === 404 && error.message === '房间不存在或已下架')
  await assert.rejects(content.fetchRoom('../guide'), (error) => error.statusCode === 404)
  assert.deepEqual(calls, ['/api/miniprogram/rooms/9'])
})

test('入住指南校验每条问答都是文字', async () => {
  installWx({ '/api/miniprogram/guide': { statusCode: 200, data: { sections: [{ category: '入住', items: [{ question: '几点？', answer: null }] }] } } })
  await assert.rejects(content.fetchGuide())
})

test('房间列表页：加载成功、空列表、失败后重试', async () => {
  const routes = { '/api/miniprogram/rooms': { fail: true } }
  installWx(routes)
  const page = loadPage('pages/rooms/index.js')
  await page.load()
  assert.equal(page.data.status, 'error')
  assert.equal(page.data.message, '网络异常，请检查网络后重试')

  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [] } }
  await page.load()
  assert.equal(page.data.status, 'empty')

  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [ROOM] } }
  await page.load()
  assert.equal(page.data.status, 'ready')
  assert.equal(page.data.rooms[0].name, '山海大床房')
})

test('页面卸载或重新加载后，迟到的旧结果被丢弃', async () => {
  const { pending } = installWx({ '/api/miniprogram/guide': { hold: true, statusCode: 200, data: { sections: [] } } })
  const page = loadPage('pages/guide/index.js')
  const first = page.load()
  page.onUnload()
  pending.shift()()
  await first
  assert.equal(page.data.status, 'loading', '卸载后的结果不能写回页面')
})

test('下拉刷新等加载结束后才收起动画', async () => {
  const { calls, pending } = installWx({ '/api/miniprogram/rooms': { hold: true, statusCode: 200, data: { rooms: [] } } })
  const page = loadPage('pages/rooms/index.js')
  const refreshing = page.onPullDownRefresh()
  await new Promise(setImmediate)
  assert.ok(!calls.includes('stopPullDownRefresh'))
  pending.shift()()
  await refreshing
  assert.ok(calls.includes('stopPullDownRefresh'))
})

test('详情页：下架显示 gone，不提供重试；加载成功设置标题', async () => {
  const routes = { '/api/miniprogram/rooms/101': { statusCode: 404, data: {} } }
  const { calls } = installWx(routes)
  const page = loadPage('pages/rooms/detail.js')
  page.roomId = '101'
  await page.load()
  assert.equal(page.data.status, 'gone')

  routes['/api/miniprogram/rooms/101'] = {
    statusCode: 200,
    data: { ...ROOM, intro: '朝南', facilities: ['洗衣机'], photos: ['/api/miniprogram/rooms/101/photos/7'] },
  }
  await page.load()
  assert.equal(page.data.status, 'ready')
  assert.deepEqual(page.data.room.photos, [`${API_BASE_URL}/api/miniprogram/rooms/101/photos/7`])
  assert.ok(calls.includes('title:山海大床房'))
})

test('客服未配置时提示暂未开通，不调用微信接口', () => {
  const { calls } = installWx()
  const { openServiceChat } = require(path.join(CLIENT_ROOT, 'utils/service-chat.js'))
  openServiceChat()
  assert.deepEqual(calls, ['toast:客服暂未开通'])
})
