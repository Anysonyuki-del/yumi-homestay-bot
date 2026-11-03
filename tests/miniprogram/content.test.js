// 住客自助内容客户端与页面行为测试（小程序住客自助 Spec F5，三标签 Spec F1–F5）：wx 与 Page 都用替身。
// 运行：node --test tests/miniprogram/（由 tests/unit/test_miniprogram_client_js.py 调用）
const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const CLIENT_ROOT = path.resolve(__dirname, '../../clients/wechat-miniprogram')
const content = require(path.join(CLIENT_ROOT, 'utils/content.js'))
const { API_BASE_URL } = require(path.join(CLIENT_ROOT, 'utils/auth.js'))

const ROOM = {
  id: 101, name: '山海大床房', bed_type: '1.8 米大床', max_guests: 2, area_sqm: 35,
  cover_url: '/api/miniprogram/rooms/101/photos/7', summary: '朝南，有阳台。',
}
const STORE = {
  name: '山野小住', intro: '推窗见山。', address: '合成地址 1 号',
  latitude: 30.25, longitude: 120.15, phone: '0571-1234 5678',
}
const HOME_EMPTY = { statusCode: 200, data: { store: null, banners: [] } }

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
    navigateTo(options) { calls.push(`navigate:${options.url}`) },
    navigateBack() { calls.push('back') },
    openLocation(options) { calls.push(`map:${options.latitude},${options.longitude}`) },
    makePhoneCall(options) { calls.push(`call:${options.phoneNumber}`) },
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
    cover: `${API_BASE_URL}/api/miniprogram/rooms/101/photos/7`, summary: '朝南，有阳台。',
  }])
})

test('结构不符、非 200 与网络失败都报可读错误，不透出服务器原文', async () => {
  const cases = [
    [{ statusCode: 200, data: { rooms: [{ ...ROOM, id: '101' }] } }, '加载失败，请稍后重试'],
    [{ statusCode: 200, data: { rooms: [{ ...ROOM, summary: 5 }] } }, '加载失败，请稍后重试'],
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

test('首页：加载成功、空列表、失败后重试', async () => {
  const routes = { '/api/miniprogram/rooms': { fail: true }, '/api/miniprogram/home': HOME_EMPTY }
  installWx(routes)
  const page = loadPage('pages/home/index.js')
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
  const page = loadPage('pages/services/index.js')
  const first = page.load()
  page.onUnload()
  pending.shift()()
  await first
  assert.equal(page.data.status, 'loading', '卸载后的结果不能写回页面')
})

test('下拉刷新等加载结束后才收起动画', async () => {
  const { calls, pending } = installWx({
    '/api/miniprogram/rooms': { hold: true, statusCode: 200, data: { rooms: [] } },
    '/api/miniprogram/home': HOME_EMPTY,
  })
  const page = loadPage('pages/home/index.js')
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

test('规格行跳过缺失项，三项都缺时为空串', () => {
  const { specLine } = require(path.join(CLIENT_ROOT, 'utils/format.js'))
  assert.equal(specLine({ bedType: '1.8 米大床', maxGuests: 2, areaSqm: 35 }), '1.8 米大床 · 可住 2 人 · 35 ㎡')
  assert.equal(specLine({ bedType: '', maxGuests: null, areaSqm: 20 }), '20 ㎡')
  assert.equal(specLine({ bedType: '', maxGuests: null, areaSqm: null }), '')
})

test('状态块按钮：出错时重试，空列表时转问管家', async () => {
  const routes = { '/api/miniprogram/rooms': { fail: true }, '/api/miniprogram/home': HOME_EMPTY }
  const { calls } = installWx(routes)
  const page = loadPage('pages/home/index.js')
  await page.load()
  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [] } }
  page.onStateAction()
  await new Promise(setImmediate)
  await new Promise(setImmediate)
  assert.equal(page.data.status, 'empty')
  page.onStateAction()
  assert.ok(calls.includes('toast:客服暂未开通'))
})

test('首页接口：门店信息为 null 时原样返回；坐标齐全才给出导航位置；结构不符报错', async () => {
  installWx({ '/api/miniprogram/home': HOME_EMPTY })
  assert.deepEqual(await content.fetchHome(), { store: null, banners: [] })

  installWx({ '/api/miniprogram/home': { statusCode: 200, data: {
    store: { ...STORE, latitude: null, longitude: null, phone: null },
    banners: ['/api/miniprogram/banners/3'],
  } } })
  assert.deepEqual(await content.fetchHome(), {
    store: { name: '山野小住', intro: '推窗见山。', address: '合成地址 1 号', phone: '', location: null },
    banners: [`${API_BASE_URL}/api/miniprogram/banners/3`],
  })

  installWx({ '/api/miniprogram/home': { statusCode: 200, data: { store: STORE, banners: [] } } })
  assert.deepEqual((await content.fetchHome()).store.location, { latitude: 30.25, longitude: 120.15 })
  installWx({ '/api/miniprogram/home': { statusCode: 200, data: { store: { ...STORE, longitude: null }, banners: [] } } })
  assert.equal((await content.fetchHome()).store.location, null, '只有一个坐标不能导航')

  for (const data of [
    { store: { ...STORE, latitude: '30.25' }, banners: [] },
    { store: STORE, banners: [3] },
    { store: STORE },
  ]) {
    installWx({ '/api/miniprogram/home': { statusCode: 200, data } })
    await assert.rejects(content.fetchHome(), (error) => error.message === '加载失败，请稍后重试')
  }
})

test('首页：门店信息或房间任一失败整页出错；没有房间时门店与横幅照常显示', async () => {
  const routes = {
    '/api/miniprogram/rooms': { statusCode: 200, data: { rooms: [ROOM] } },
    '/api/miniprogram/home': { statusCode: 503, data: {} },
  }
  installWx(routes)
  const page = loadPage('pages/home/index.js')
  await page.load()
  assert.equal(page.data.status, 'error')

  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [] } }
  routes['/api/miniprogram/home'] = { statusCode: 200, data: { store: STORE, banners: ['/api/miniprogram/banners/3'] } }
  await page.load()
  assert.equal(page.data.status, 'empty')
  assert.equal(page.data.store.name, '山野小住')
  assert.deepEqual(page.data.banners, [`${API_BASE_URL}/api/miniprogram/banners/3`])
})

test('分类图标：按关键词挑图标，Wi-Fi 不分大小写，未命中用书本', () => {
  const { categoryIcon } = require(path.join(CLIENT_ROOT, 'utils/format.js'))
  const cases = {
    入住须知: 'key', 退房: 'key', 'Wi-Fi': 'wifi', WIFI密码: 'wifi', 网络: 'wifi',
    停车: 'vehicle', 周边美食: 'location', 交通: 'location', 其他: 'book', '': 'book',
  }
  for (const [category, icon] of Object.entries(cases)) assert.equal(categoryIcon(category), icon, category)
})

test('服务页：每个分类一格并带图标；点格子以编码后的分类名进入分类页', async () => {
  const { calls } = installWx({ '/api/miniprogram/guide': { statusCode: 200, data: { sections: [
    { category: '入住&退房', items: [{ question: '几点入住？', answer: '下午 3 点' }] },
    { category: '其他', items: [{ question: '能带宠物吗？', answer: '不能' }] },
  ] } } })
  const page = loadPage('pages/services/index.js')
  await page.load()
  assert.deepEqual(page.data.categories, [{ name: '入住&退房', icon: 'key' }, { name: '其他', icon: 'book' }])
  page.openCategory({ currentTarget: { dataset: { name: '入住&退房' } } })
  assert.ok(calls.includes('navigate:/pages/services/category?name=%E5%85%A5%E4%BD%8F%26%E9%80%80%E6%88%BF'))
})

test('分类页：只显示该分类的问答；分类已不展示时显示 gone，按钮回到服务页', async () => {
  const guide = { statusCode: 200, data: { sections: [
    { category: '入住&退房', items: [{ question: '几点入住？', answer: '下午 3 点' }] },
    { category: '其他', items: [{ question: '能带宠物吗？', answer: '不能' }] },
  ] } }
  const { calls } = installWx({ '/api/miniprogram/guide': guide })
  const page = loadPage('pages/services/category.js')
  page.onLoad({ name: encodeURIComponent('入住&退房') })
  await new Promise(setImmediate)
  await new Promise(setImmediate)
  assert.equal(page.data.status, 'ready')
  assert.deepEqual(page.data.items, [{ question: '几点入住？', answer: '下午 3 点' }])
  assert.ok(calls.includes('title:入住&退房'))

  page.category = '已删除的分类'
  await page.load()
  assert.equal(page.data.status, 'gone')
  assert.equal(page.data.message, '这个分类暂时没有内容')
  page.onStateAction()
  assert.ok(calls.includes('back'))
})

// 我的页用 auth 模块替身：只关心页面怎样根据登录结果切换状态。
function loadMePage(auth) {
  const authPath = require.resolve(path.join(CLIENT_ROOT, 'utils/auth.js'))
  const real = require.cache[authPath]
  require.cache[authPath] = { id: authPath, filename: authPath, loaded: true, exports: { ...real.exports, ...auth } }
  try {
    return loadPage('pages/me/index.js')
  } finally {
    require.cache[authPath] = real
  }
}

test('我的页：登录、退出成功与失败、核验待确认，状态如实切换', async () => {
  installWx()
  let logoutError = null
  let loginError = null
  const page = loadMePage({
    restore: async () => ({ status: 'logged_out' }),
    ensureLogin: async () => { if (loginError) throw loginError; return { displayName: '' } },
    logout: async () => { if (logoutError) throw logoutError },
  })
  page.onLoad()
  await new Promise(setImmediate)
  assert.equal(page.data.authStatus, 'logged_out')

  await page.handleLogin()
  assert.equal(page.data.authStatus, 'logged_in')

  logoutError = Object.assign(new Error('退出未完成，请重试'), { code: 'revoke_failed' })
  await page.handleLogout()
  assert.equal(page.data.authStatus, 'logged_in', '服务端没确认退出前不能显示已退出')
  assert.equal(page.data.authMessage, '退出未完成，请重试')

  logoutError = null
  await page.handleLogout()
  assert.equal(page.data.authStatus, 'logged_out')

  loginError = Object.assign(new Error('暂时无法确认登录状态，请稍后重试'), { code: 'verify_unknown' })
  await page.handleLogin()
  assert.equal(page.data.authStatus, 'unknown')
})

test('我的订单本期只提示去问管家', () => {
  const { calls } = installWx()
  const page = loadMePage({ restore: async () => ({ status: 'logged_out' }) })
  page.openOrders()
  assert.deepEqual(calls, ['toast:订单查询即将开放，现在可以问管家'])
})

test('关于页：没有坐标不打开地图；有坐标才导航；拨号只留数字和加号', async () => {
  const routes = { '/api/miniprogram/home': { statusCode: 200, data: {
    store: { ...STORE, latitude: null, longitude: null }, banners: [],
  } } }
  const { calls } = installWx(routes)
  const page = loadPage('pages/me/about.js')
  await page.load()
  assert.equal(page.data.status, 'ready')
  page.openMap()
  assert.ok(!calls.some((call) => call.startsWith('map:')))
  page.callStore()
  assert.ok(calls.includes('call:057112345678'))

  routes['/api/miniprogram/home'] = { statusCode: 200, data: { store: STORE, banners: [] } }
  await page.load()
  page.openMap()
  assert.ok(calls.includes('map:30.25,120.15'))

  routes['/api/miniprogram/home'] = HOME_EMPTY
  await page.load()
  assert.equal(page.data.status, 'empty')
})

test('后端还没有首页接口（404）时，首页与关于页显示可重试的出错，不当作已下架', async () => {
  // 现场：新版小程序先于后端 1.73.0 上线，/home 返回 404，首页曾落到空白内容区。
  const routes = {
    '/api/miniprogram/rooms': { statusCode: 200, data: { rooms: [] } },
    '/api/miniprogram/home': { statusCode: 404, data: { detail: 'Not Found' } },
  }
  installWx(routes)
  for (const pagePath of ['pages/home/index.js', 'pages/me/about.js']) {
    const page = loadPage(pagePath)
    await page.load()
    assert.equal(page.data.status, 'error', pagePath)
    assert.equal(page.data.message, '加载失败，请稍后重试')
  }
})
