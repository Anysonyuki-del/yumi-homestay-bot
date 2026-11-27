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

// routes 按路径返回响应、网络失败，或挂起等测试手动放行；记录请求、跳转与提示调用。
function installWx(routes = {}) {
  const calls = []
  const pending = []
  const modal = { confirm: true }
  globalThis.wx = {
    request(options) {
      const key = options.url.replace(API_BASE_URL, '')
      calls.push(key)
      const route = routes[key]
      const respond = () => {
        if (!route || route.fail) options.fail({ errMsg: route && route.timeout ? 'request:fail timeout' : 'request:fail' })
        else options.success({ statusCode: route.statusCode, data: route.data })
      }
      // 挂起的响应记下路径，测试可按路径挑出要放行的那一个。
      if (route && route.hold) pending.push(Object.assign(respond, { key }))
      else setImmediate(respond)
    },
    showToast(options) { calls.push(`toast:${options.title}`) },
    stopPullDownRefresh() { calls.push('stopPullDownRefresh') },
    setNavigationBarTitle(options) { calls.push(`title:${options.title}`) },
    navigateTo(options) { calls.push(`navigate:${options.url}`) },
    navigateBack() { calls.push('back') },
    switchTab(options) { calls.push(`tab:${options.url}`) },
    openLocation(options) { calls.push(`map:${options.latitude},${options.longitude}`) },
    setClipboardData(options) { calls.push(`copy:${options.data}`); options.success() },
    // 弹框替身：记录标题、内容与确认文字；默认模拟客人点确认，modal.confirm 设为 false 模拟点关闭。
    showModal(options) { calls.push(`modal:${options.title}|${options.content}|${options.confirmText}`); options.success({ confirm: modal.confirm }) },
    makePhoneCall(options) { calls.push(`call:${options.phoneNumber}`) },
  }
  return { calls, pending, modal }
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

test('首页：房间失败只影响房间区，门店与快捷导航照常；房间区重试只重取房间', async () => {
  // 复现（设计审查 R1）：门店 200 带坐标、房间 503，旧版整页只剩「重试」，导航和客服一起消失。
  const routes = {
    '/api/miniprogram/rooms': { statusCode: 503, data: {} },
    '/api/miniprogram/home': { statusCode: 200, data: { store: STORE, banners: [] } },
  }
  const { calls } = installWx(routes)
  const page = loadPage('pages/home/index.js')
  await page.load()
  assert.equal(page.data.homeStatus, 'ready')
  assert.equal(page.data.roomsStatus, 'error')
  assert.equal(page.data.roomsMessage, '加载失败，请稍后重试')
  assert.ok(calls.includes('title:山野小住'))
  // 已取得的坐标仍可导航。
  calls.length = 0
  page.openQuickNav({ currentTarget: { dataset: { key: 'map' } } })
  assert.deepEqual(calls, ['map:30.25,120.15'])

  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [] } }
  calls.length = 0
  await page.onStateAction()
  assert.deepEqual(calls, ['/api/miniprogram/rooms'], '房间区重试不重取门店资料')
  assert.equal(page.data.roomsStatus, 'empty')

  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [ROOM] } }
  await page.onStateAction()
  assert.equal(page.data.roomsStatus, 'ready')
  assert.equal(page.data.rooms[0].name, '山海大床房')
  assert.equal(page.data.homeStatus, 'ready')
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

test('规格行跳过缺失项，三项都缺时为空串', () => {
  const { specLine } = require(path.join(CLIENT_ROOT, 'utils/format.js'))
  assert.equal(specLine({ bedType: '1.8 米大床', maxGuests: 2, areaSqm: 35 }), '1.8 米大床 · 可住 2 人 · 35 ㎡')
  assert.equal(specLine({ bedType: '', maxGuests: null, areaSqm: 20 }), '20 ㎡')
  assert.equal(specLine({ bedType: '', maxGuests: null, areaSqm: null }), '')
})

test('首页：重试或刷新后，迟到的旧房间结果被丢弃；房间区重试不作废门店区在途的请求', async () => {
  const routes = {
    '/api/miniprogram/rooms': { hold: true, statusCode: 200, data: { rooms: [{ ...ROOM, name: '旧结果' }] } },
    '/api/miniprogram/home': { hold: true, statusCode: 200, data: { store: STORE, banners: [] } },
  }
  const { pending } = installWx(routes)
  const page = loadPage('pages/home/index.js')
  const first = page.load()
  const take = (key) => pending.splice(pending.findIndex((respond) => respond.key === key), 1)[0]
  const respondHome = take('/api/miniprogram/home')
  const respondOldRooms = take('/api/miniprogram/rooms')
  routes['/api/miniprogram/rooms'] = { hold: true, statusCode: 200, data: { rooms: [ROOM] } }
  const retried = page.onStateAction()
  take('/api/miniprogram/rooms')()
  await retried
  assert.equal(page.data.rooms[0].name, '山海大床房')
  respondOldRooms()
  respondHome()
  await first
  assert.equal(page.data.rooms[0].name, '山海大床房', '旧的房间结果不能覆盖重试结果')
  assert.equal(page.data.homeStatus, 'ready', '房间区重试不能作废门店区的请求')

  // 卸载后两个区块的在途结果都作废。
  routes['/api/miniprogram/rooms'] = { hold: true, statusCode: 503, data: {} }
  routes['/api/miniprogram/home'] = { hold: true, statusCode: 503, data: {} }
  const again = page.load()
  page.onUnload()
  pending.splice(0).forEach((respond) => respond())
  await again
  assert.equal(page.data.roomsStatus, 'loading')
  assert.equal(page.data.homeStatus, 'loading')
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

test('首页：门店失败时房间照常可开、只在门店区重试；两区都失败时快捷导航仍有原生客服格；没有房间时门店与横幅照常显示', async () => {
  const routes = {
    '/api/miniprogram/rooms': { statusCode: 200, data: { rooms: [ROOM] } },
    '/api/miniprogram/home': { statusCode: 503, data: {} },
  }
  const { calls } = installWx(routes)
  const page = loadPage('pages/home/index.js')
  await page.load()
  assert.equal(page.data.homeStatus, 'error')
  assert.equal(page.data.roomsStatus, 'ready')
  page.openRoom({ currentTarget: { dataset: { id: 101 } } })
  assert.ok(calls.includes('navigate:/pages/rooms/detail?id=101'))
  // 门店资料没取到：到店导航进关于页，由关于页自己加载和重试。
  calls.length = 0
  page.openQuickNav({ currentTarget: { dataset: { key: 'map' } } })
  assert.deepEqual(calls, ['navigate:/pages/me/about'])

  routes['/api/miniprogram/rooms'] = { fail: true }
  await page.load()
  assert.equal(page.data.homeStatus, 'error')
  assert.equal(page.data.roomsStatus, 'error')
  assert.ok(page.data.quickNav.some((item) => item.key === 'host' && item.contact), '两区都失败也要能发起原生客服')

  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [] } }
  routes['/api/miniprogram/home'] = { statusCode: 200, data: { store: STORE, banners: ['/api/miniprogram/banners/3'] } }
  calls.length = 0
  page.retryHome()
  await new Promise(setImmediate)
  await new Promise(setImmediate)
  assert.deepEqual(calls.filter((call) => call.startsWith('/api')), ['/api/miniprogram/home'], '门店区重试不重取房间')
  await page.load()
  assert.equal(page.data.homeStatus, 'ready')
  assert.equal(page.data.roomsStatus, 'empty')
  assert.equal(page.data.store.name, '山野小住')
  assert.deepEqual(page.data.banners, [`${API_BASE_URL}/api/miniprogram/banners/3`])
  assert.ok(calls.includes('title:山野小住'))

  // 刷新时门店区失败：到店动作退回 unknown，不沿用上一次的坐标。
  routes['/api/miniprogram/home'] = { statusCode: 503, data: {} }
  await page.load()
  assert.equal(page.data.arrival, 'unknown')
})

test('分类图标：按关键词挑图标，Wi-Fi 不分大小写，未命中用书本', () => {
  const { categoryIcon } = require(path.join(CLIENT_ROOT, 'utils/format.js'))
  const cases = {
    入住须知: 'key', 退房: 'key', 'Wi-Fi': 'wifi', WIFI密码: 'wifi', 网络: 'wifi',
    停车: 'vehicle', 周边美食: 'location', 交通: 'location', 其他: 'book', '': 'book',
  }
  for (const [category, icon] of Object.entries(cases)) assert.equal(categoryIcon(category), icon, category)
})

test('服务页：每个分类一张卡片，带图标、条数和第一个问题；空分类不出卡片；点卡片以编码后的分类名进入分类页', async () => {
  const { calls } = installWx({ '/api/miniprogram/guide': { statusCode: 200, data: { sections: [
    { category: '入住&退房', items: [
      { question: '几点入住？', answer: '下午 3 点' }, { question: '几点退房？', answer: '中午 12 点' },
    ] },
    { category: '空分类', items: [] },
    { category: '其他', items: [{ question: '能带宠物吗？', answer: '不能' }] },
  ] } } })
  const page = loadPage('pages/services/index.js')
  await page.load()
  assert.equal(page.data.status, 'ready')
  assert.deepEqual(page.data.categories, [
    { name: '入住&退房', icon: 'key', count: 2, preview: '几点入住？' },
    { name: '其他', icon: 'book', count: 1, preview: '能带宠物吗？' },
  ])
  page.openCategory({ currentTarget: { dataset: { name: '入住&退房' } } })
  assert.ok(calls.includes('navigate:/pages/services/category?name=%E5%85%A5%E4%BD%8F%26%E9%80%80%E6%88%BF'))
})

test('服务页：分类全是空的时显示空状态，不出空白宫格', async () => {
  installWx({ '/api/miniprogram/guide': { statusCode: 200, data: { sections: [{ category: '空分类', items: [] }] } } })
  const page = loadPage('pages/services/index.js')
  await page.load()
  assert.equal(page.data.status, 'empty')
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

test('分类页：分类参数编码损坏时不崩溃，按分类不存在处理', async () => {
  // 复现（前端审查 F-08）：外部链接带残缺的 %E4% 时，decodeURIComponent 抛错，onLoad 直接中断，页面停在加载态。
  installWx({ '/api/miniprogram/guide': { statusCode: 200, data: { sections: [{ category: '入住', items: [{ question: '几点？', answer: '3 点' }] }] } } })
  const page = loadPage('pages/services/category.js')
  page.onLoad({ name: '%E4%' })
  await new Promise(setImmediate)
  await new Promise(setImmediate)
  assert.equal(page.data.status, 'gone')
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

test('我的页：离开页面后迟到的登录核验结果被丢弃', async () => {
  // 前端审查 F-19：页面卸载后不应再用旧请求的结果改写页面状态。
  installWx()
  let release
  const page = loadMePage({ restore: () => new Promise((resolve) => { release = resolve }) })
  page.onLoad()
  page.onUnload()
  release({ status: 'logged_in' })
  await new Promise(setImmediate)
  assert.equal(page.data.authStatus, 'checking')
})

test('关于页：位置行与首页同源——有坐标即使没地址也导航，只有地址复制，都没有问管家；拨号只留数字和加号', async () => {
  // 复现（设计审查 R2）：门店只填成对坐标、文字全空时，旧模板要求有地址才渲染入口，坐标藏了起来。
  const routes = { '/api/miniprogram/home': { statusCode: 200, data: {
    store: { name: null, intro: null, address: null, phone: null, latitude: 30.25, longitude: 120.15 }, banners: [],
  } } }
  const { calls } = installWx(routes)
  const page = loadPage('pages/me/about.js')
  await page.load()
  assert.equal(page.data.status, 'ready')
  assert.equal(page.data.arrival, 'map')
  page.openArrival()
  assert.ok(calls.includes('map:30.25,120.15'))

  routes['/api/miniprogram/home'] = { statusCode: 200, data: {
    store: { ...STORE, latitude: null, longitude: null }, banners: [],
  } }
  await page.load()
  assert.equal(page.data.arrival, 'copy')
  calls.length = 0
  page.openArrival()
  assert.deepEqual(calls, ['copy:合成地址 1 号', 'toast:地址已复制'])
  page.callStore()
  assert.ok(calls.includes('call:057112345678'))

  // 只填简介：没有到店信息，位置行改为问管家（模板渲染原生客服按钮，openArrival 不做事）。
  routes['/api/miniprogram/home'] = { statusCode: 200, data: {
    store: { name: null, intro: '推窗见山。', address: null, phone: null, latitude: null, longitude: null }, banners: [],
  } }
  await page.load()
  assert.equal(page.data.arrival, 'ask')
  calls.length = 0
  page.openArrival()
  assert.deepEqual(calls, [])

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
  const home = loadPage('pages/home/index.js')
  await home.load()
  assert.equal(home.data.homeStatus, 'error')
  assert.equal(home.data.homeMessage, '加载失败，请稍后重试')
  const about = loadPage('pages/me/about.js')
  await about.load()
  assert.equal(about.data.status, 'error')
  assert.equal(about.data.message, '加载失败，请稍后重试')
})

test('到店动作：有坐标导航（地址可空），只有地址复制，都没有问管家；地图打不开时弹框由客人决定是否复制', () => {
  const { arrivalAction, openStoreMap, copyStoreAddress } = require(path.join(CLIENT_ROOT, 'utils/store-map.js'))
  const location = { latitude: 30.25, longitude: 120.15 }
  assert.equal(arrivalAction({ name: '', intro: '', address: '', phone: '', location }), 'map')
  assert.equal(arrivalAction({ name: '', intro: '', address: '合成地址 1 号', phone: '', location: null }), 'copy')
  assert.equal(arrivalAction({ name: '山野小住', intro: '推窗见山。', address: '', phone: '0571', location: null }), 'ask')
  assert.equal(arrivalAction(null), 'ask')

  let opened
  let { calls, modal } = installWx()
  globalThis.wx.openLocation = (options) => { opened = options; calls.push('map') }
  openStoreMap({ name: '山野小住', address: '合成地址 1 号', location })
  assert.deepEqual(calls, ['map'])
  assert.deepEqual(
    { latitude: opened.latitude, longitude: opened.longitude, name: opened.name, address: opened.address },
    { latitude: 30.25, longitude: 120.15, name: '山野小住', address: '合成地址 1 号' },
  )

  // 实施复审 F1：地图失败时不能未经客人确认就改写剪贴板。点关闭零写入，点复制只写一次，没有地址不尝试复制。
  const failingMap = (options) => options.fail({ errMsg: 'openLocation:fail' })
  ;({ calls, modal } = installWx())
  globalThis.wx.openLocation = failingMap
  modal.confirm = false
  openStoreMap({ name: '山野小住', address: '合成地址 1 号', location })
  assert.deepEqual(calls, ['modal:地图暂时打不开|合成地址 1 号|复制地址'])
  modal.confirm = true
  calls.length = 0
  openStoreMap({ name: '山野小住', address: '合成地址 1 号', location })
  assert.deepEqual(calls, ['modal:地图暂时打不开|合成地址 1 号|复制地址', 'copy:合成地址 1 号', 'toast:地址已复制'])
  calls.length = 0
  openStoreMap({ name: '', address: '', location })
  assert.deepEqual(calls, ['toast:暂时无法打开地图'])

  // 隐私指引没声明剪切板等原因导致复制失败时，告诉客人改用长按复制。
  ;({ calls } = installWx())
  globalThis.wx.setClipboardData = (options) => options.fail({ errMsg: 'setClipboardData:fail api scope is not declared in the privacy agreement' })
  copyStoreAddress('合成地址 1 号')
  assert.deepEqual(calls, ['toast:复制没有成功，可在「关于本店」长按地址复制'])
})

test('首页快捷导航：第一格随门店资料变化，与关于页同源；其余三格固定且各自去对的地方', async () => {
  const routes = {
    '/api/miniprogram/rooms': { statusCode: 200, data: { rooms: [ROOM] } },
    '/api/miniprogram/home': { statusCode: 200, data: { store: STORE, banners: [] } },
  }
  const { calls } = installWx(routes)
  const page = loadPage('pages/home/index.js')
  // 首屏起就有四格，不等接口。
  assert.deepEqual(page.data.quickNav.map((item) => item.label), ['到店导航', '入住指南', '联系管家', '关于本店'])
  await page.load()
  const tap = (key) => page.openQuickNav({ currentTarget: { dataset: { key } } })

  calls.length = 0
  tap('map')
  tap('guide')
  tap('about')
  assert.deepEqual(calls, ['map:30.25,120.15', 'tab:/pages/services/index', 'navigate:/pages/me/about'])

  // 只有文字地址：弹框给出完整地址，确认后复制。
  routes['/api/miniprogram/home'] = { statusCode: 200, data: { store: { ...STORE, latitude: null, longitude: null }, banners: [] } }
  await page.load()
  assert.equal(page.data.quickNav[0].label, '门店地址')
  calls.length = 0
  tap('map')
  assert.deepEqual(calls, ['modal:门店地址|合成地址 1 号|复制地址', 'copy:合成地址 1 号', 'toast:地址已复制'])

  // 只填简介：第一格变为原生客服按钮「问怎么到店」，不再写「到店导航」。
  routes['/api/miniprogram/home'] = { statusCode: 200, data: {
    store: { name: null, intro: '推窗见山。', address: null, phone: null, latitude: null, longitude: null }, banners: [],
  } }
  await page.load()
  assert.deepEqual(page.data.quickNav[0], { key: 'map', label: '问怎么到店', icon: 'location', contact: true })
})

test('首页封面事件按房间与封面核对：列表刷新后迟到的旧事件不改新卡片，当前封面失败仍切紧凑卡', async () => {
  // 实施复审 F2：旧版按下标回写，刷新后旧第 0 项的错误事件会把新第 0 项的正常封面藏掉。
  const OTHER = { ...ROOM, id: 202, name: '溪边双床房', cover_url: '/api/miniprogram/rooms/202/photos/9' }
  const routes = {
    '/api/miniprogram/rooms': { statusCode: 200, data: { rooms: [ROOM, OTHER] } },
    '/api/miniprogram/home': HOME_EMPTY,
  }
  installWx(routes)
  const page = loadPage('pages/home/index.js')
  // setData 替身支持 rooms[i].field 路径写法。
  page.setData = (patch) => {
    for (const [key, value] of Object.entries(patch)) {
      const match = key.match(/^rooms\[(\d+)\]\.(\w+)$/)
      if (match) page.data.rooms[Number(match[1])][match[2]] = value
      else page.data[key] = value
    }
  }
  await page.load()
  const oldFirst = { id: page.data.rooms[0].id, cover: page.data.rooms[0].cover }

  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [OTHER, ROOM] } }
  await page.load()
  // 旧第 0 项（ROOM）的错误事件迟到：只能落到 ROOM 现在的位置，不能落到新第 0 项。
  page.onCoverError({ currentTarget: { dataset: oldFirst } })
  assert.equal(page.data.rooms[0].coverFailed, false, '旧事件不能改到换到同一下标的另一间房上')

  // 同房换图：旧封面的事件不改新封面。
  routes['/api/miniprogram/rooms'] = { statusCode: 200, data: { rooms: [{ ...OTHER, cover_url: '/api/miniprogram/rooms/202/photos/10' }, ROOM] } }
  await page.load()
  page.onCoverLoad({ currentTarget: { dataset: { id: OTHER.id, cover: `${API_BASE_URL}/api/miniprogram/rooms/202/photos/9` } } })
  assert.equal(page.data.rooms[0].coverLoaded, false)

  // 正向对照：当前那张封面失败，就切成紧凑卡。
  page.onCoverError({ currentTarget: { dataset: { id: ROOM.id, cover: page.data.rooms[1].cover } } })
  assert.equal(page.data.rooms[1].coverFailed, true)
  assert.equal(page.data.rooms[0].coverFailed, false)
})

test('详情轮播项带读屏文字（房名、图序、放大提示），放大预览仍用原地址列表', async () => {
  const photos = ['/api/miniprogram/rooms/101/photos/7', '/api/miniprogram/rooms/101/photos/8']
  const { calls } = installWx({ '/api/miniprogram/rooms/101': { statusCode: 200, data: { ...ROOM, intro: null, facilities: [], photos } } })
  globalThis.wx.previewImage = (options) => calls.push(`preview:${options.current}|${options.urls.length}`)
  const page = loadPage('pages/rooms/detail.js')
  page.roomId = '101'
  await page.load()
  assert.deepEqual(page.data.room.photoItems.map((item) => item.ariaLabel), [
    '山海大床房，第 1 张，共 2 张，点按放大', '山海大床房，第 2 张，共 2 张，点按放大',
  ])
  assert.equal(page.data.room.photoItems[1].value, `${API_BASE_URL}${photos[1]}`)
  page.previewPhoto({ detail: { index: 1 } })
  assert.ok(calls.includes(`preview:${API_BASE_URL}${photos[1]}|2`))
})

// ---- 双态光学视觉（2026-11-28 光学重设计 Spec F5）：theme.json、标签栏图标、WXML 零硬编码色、TDesign 暗色基座 ----
const fs = require('node:fs')
const zlib = require('node:zlib')

// 解码 8 位 RGBA、非隔行 PNG，返回宽高与逐像素 [r, g, b, a]；只为核对标签栏图标，不支持其他格式。
function decodePng(file) {
  const data = fs.readFileSync(file)
  let offset = 8
  let header = null
  const idat = []
  while (offset < data.length) {
    const length = data.readUInt32BE(offset)
    const type = data.toString('latin1', offset + 4, offset + 8)
    const body = data.subarray(offset + 8, offset + 8 + length)
    if (type === 'IHDR') header = { width: body.readUInt32BE(0), height: body.readUInt32BE(4), depth: body[8], colorType: body[9], interlace: body[12] }
    if (type === 'IDAT') idat.push(body)
    offset += 12 + length
  }
  assert.deepEqual([header.depth, header.colorType, header.interlace], [8, 6, 0], `${file} 不是 8 位 RGBA 非隔行 PNG`)
  const raw = zlib.inflateSync(Buffer.concat(idat))
  const stride = header.width * 4
  const pixels = []
  let previous = Buffer.alloc(stride)
  for (let y = 0; y < header.height; y += 1) {
    const filter = raw[y * (stride + 1)]
    const line = Buffer.from(raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)))
    for (let x = 0; x < stride; x += 1) {
      const left = x >= 4 ? line[x - 4] : 0
      const up = previous[x]
      const corner = x >= 4 ? previous[x - 4] : 0
      let predictor = 0
      if (filter === 1) predictor = left
      else if (filter === 2) predictor = up
      else if (filter === 3) predictor = (left + up) >> 1
      else if (filter === 4) {
        const p = left + up - corner
        const [pa, pb, pc] = [Math.abs(p - left), Math.abs(p - up), Math.abs(p - corner)]
        predictor = pa <= pb && pa <= pc ? left : pb <= pc ? up : corner
      }
      line[x] = (line[x] + predictor) & 255
    }
    for (let x = 0; x < stride; x += 4) pixels.push([line[x], line[x + 1], line[x + 2], line[x + 3]])
    previous = line
  }
  return { width: header.width, height: header.height, pixels }
}

const hex = ([r, g, b]) => '#' + [r, g, b].map((value) => value.toString(16).padStart(2, '0')).join('')
const readClient = (relative) => fs.readFileSync(path.join(CLIENT_ROOT, relative), 'utf8')

// app.wxss 里 @media (prefers-color-scheme: dark) { page { ... } } 那一块（注释里提到的同名文字不算）。
function darkPageBlock() {
  const css = readClient('app.wxss')
  const start = css.indexOf('@media (prefers-color-scheme: dark) {')
  assert.ok(start >= 0, 'app.wxss 缺少暗色块')
  return css.slice(start, css.indexOf('\n}\n', start))
}

test('app.json 开启 darkmode，窗口与标签栏的颜色和图标全部引用 theme.json 中浅暗两态都定义的变量', () => {
  const app = JSON.parse(readClient('app.json'))
  const theme = JSON.parse(readClient('theme.json'))
  assert.equal(app.darkmode, true)
  assert.equal(app.themeLocation, 'theme.json')
  const values = [
    ...Object.values(app.window).filter((value) => value !== app.window.navigationBarTitleText),
    app.tabBar.color, app.tabBar.selectedColor, app.tabBar.backgroundColor, app.tabBar.borderStyle,
    ...app.tabBar.list.flatMap((item) => [item.iconPath, item.selectedIconPath]),
  ]
  assert.equal(values.length, 14)
  for (const value of values) {
    assert.match(value, /^@\w+$/, `${value} 仍是写死的字面量`)
    for (const mode of ['light', 'dark']) assert.ok(theme[mode][value.slice(1)], `${mode} 缺少 ${value}`)
  }
  assert.deepEqual(Object.keys(theme.light).sort(), Object.keys(theme.dark).sort())
  assert.deepEqual(
    [theme.light.navBgColor, theme.light.tabBgColor, theme.light.tabSelectedColor],
    ['#f6f4ee', '#ffffff', '#c24322'],
  )
  assert.deepEqual(
    [theme.dark.navBgColor, theme.dark.tabBgColor, theme.dark.tabSelectedColor, theme.dark.navTxtStyle],
    ['#151413', '#100f0e', '#f09a5e', 'white'],
  )
})

test('12 枚标签栏图标都是 81×81 单色剪影：浅色沿用原色，暗色与 theme.json 同色且轮廓与浅色逐像素一致', () => {
  const theme = JSON.parse(readClient('theme.json'))
  const lightColor = { Icon: '#58524a', ActiveIcon: '#c24322' }
  const darkColor = { Icon: theme.dark.tabColor, ActiveIcon: theme.dark.tabSelectedColor }
  let checked = 0
  for (const name of ['home', 'services', 'me']) {
    for (const kind of ['Icon', 'ActiveIcon']) {
      const light = decodePng(path.join(CLIENT_ROOT, theme.light[`${name}${kind}`]))
      const dark = decodePng(path.join(CLIENT_ROOT, theme.dark[`${name}${kind}`]))
      for (const [image, color] of [[light, lightColor[kind]], [dark, darkColor[kind]]]) {
        assert.deepEqual([image.width, image.height], [81, 81])
        const colors = new Set(image.pixels.filter((pixel) => pixel[3]).map(hex))
        assert.deepEqual([...colors], [color], `${name}${kind} 图标颜色应为 ${color}`)
        checked += 1
      }
      assert.deepEqual(dark.pixels.map((pixel) => pixel[3]), light.pixels.map((pixel) => pixel[3]), `${name}${kind} 暗色轮廓走样`)
    }
  }
  assert.equal(checked, 12)
})

test('页面 WXML 不再写死颜色：t-icon 颜色由父容器或 t-class 继承，暗色才能整体切换', () => {
  const files = []
  const walk = (dir) => {
    for (const entry of fs.readdirSync(path.join(CLIENT_ROOT, dir), { withFileTypes: true })) {
      const relative = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(relative)
      else if (entry.name.endsWith('.wxml')) files.push(relative)
    }
  }
  walk('pages')
  walk('templates')
  assert.ok(files.length >= 6)
  for (const file of files) {
    const source = readClient(file)
    assert.doesNotMatch(source, /#[0-9a-fA-F]{3,8}\b|rgba?\(/, `${file} 写死了颜色`)
    assert.doesNotMatch(source, /<t-icon\b[^>]*\scolor=/, `${file} 的 t-icon 仍传 color 属性`)
  }
})

// TDesign 1.17.0 公共样式没有引入 theme/_dark.wxss：这套语义基座必须在 app.wxss 暗色块里整套显式写出。
const TDESIGN_DARK_BASE = [
  ...['page', 'container', 'container-active', 'secondarycontainer', 'secondarycontainer-active', 'component',
    'component-active', 'component-disabled', 'secondarycomponent', 'secondarycomponent-active', 'specialcomponent']
    .map((name) => `--td-bg-color-${name}`),
  ...['primary', 'secondary', 'placeholder', 'disabled', 'anti', 'brand', 'link'].map((name) => `--td-text-color-${name}`),
  ...Array.from({ length: 14 }, (_, index) => `--td-gray-color-${index + 1}`),
  ...[1, 2, 3, 4].flatMap((index) => [`--td-font-white-${index}`, `--td-font-gray-${index}`]),
  '--td-component-stroke', '--td-component-border', '--td-border-level-1-color', '--td-border-level-2-color',
  '--td-mask-active', '--td-mask-disabled',
  ...['', '-active', '-disabled', '-focus', '-light', '-light-active'].map((suffix) => `--td-brand-color${suffix}`),
  ...Array.from({ length: 10 }, (_, index) => `--td-primary-color-${index + 1}`),
  ...['error', 'warning', 'success'].flatMap((tone) => [
    `--td-${tone}-color`,
    ...Array.from({ length: 10 }, (_, index) => `--td-${tone}-color-${index + 1}`),
    ...['focus', 'active', 'disabled', 'light', 'light-active'].map((suffix) => `--td-${tone}-color-${suffix}`),
  ]),
  '--td-button-primary-text-color', '--td-button-primary-outline-color', '--td-button-default-color',
  '--td-collapse-panel-bg-color', '--td-collapse-header-text-color', '--td-skeleton-bg-color',
  '--td-swiper-nav-fraction-bg-color', '--td-loading-color',
]

test('app.wxss 暗色块完整覆写 TDesign 暗色语义基座与在用组件令牌，并同步全部 --yumi-* 令牌', () => {
  const block = darkPageBlock()
  const declared = new Set([...block.matchAll(/(--[\w-]+):/g)].map((match) => match[1]))
  const missing = TDESIGN_DARK_BASE.filter((name) => !declared.has(name))
  assert.deepEqual(missing, [])
  const css = readClient('app.wxss')
  const light = css.slice(css.indexOf('page {'), css.indexOf('\n}\n', css.indexOf('page {')))
  const yumi = [...light.matchAll(/(--yumi-[\w-]+):\s*(#|rgba|inset|0 )/g)].map((match) => match[1])
  assert.ok(yumi.length >= 16)
  assert.deepEqual(yumi.filter((name) => !declared.has(name)), [], '浅色块的颜色/阴影令牌暗色块必须逐一覆写')
})

const TDESIGN_ROOT = path.join(CLIENT_ROOT, 'miniprogram_npm/tdesign-miniprogram')
test('本机装了 miniprogram_npm 时：在用组件里所有带浅色字面兜底的 --td-* 都已在暗色块声明', { skip: !fs.existsSync(TDESIGN_ROOT) && 'miniprogram_npm 未构建（CI 不装），只跑上一条基座清单' }, () => {
  const declared = new Set([...darkPageBlock().matchAll(/(--td-[\w-]+):/g)].map((match) => match[1]))
  const fallback = /var\((--td-[\w-]+),\s*(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|white|black|transparent)\)/g
  const needed = new Set()
  for (const component of ['button', 'swiper', 'swiper-nav', 'icon', 'collapse', 'collapse-panel', 'skeleton', 'loading']) {
    const source = fs.readFileSync(path.join(TDESIGN_ROOT, component, `${component}.wxss`), 'utf8')
    for (const match of source.matchAll(fallback)) needed.add(match[1])
  }
  assert.ok(needed.size >= 10)
  assert.deepEqual([...needed].filter((name) => !declared.has(name)), [])
})
