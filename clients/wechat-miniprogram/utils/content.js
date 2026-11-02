// 公开内容请求：房间列表、房间详情、入住指南（小程序住客自助 Spec F5）。
// 这些接口不需要登录；返回结构不符时一律按加载失败处理，不把服务器原文显示给客人。

const { API_BASE_URL } = require('./auth')

const REQUEST_TIMEOUT_MS = 10000
const LOAD_FAILED = '加载失败，请稍后重试'

// 带分类的加载错误：页面只展示 message；statusCode 供调用方区分「已下架」。
class ContentError extends Error {
  constructor(message, statusCode = 0) {
    super(message)
    this.name = 'ContentError'
    this.statusCode = statusCode
  }
}

// GET 一个公开接口；网络失败、超时和非 200 都以 ContentError 拒绝。
function getJson(path) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE_URL}${path}`,
      method: 'GET',
      timeout: REQUEST_TIMEOUT_MS,
      success: (res) => {
        if (res.statusCode === 200 && res.data && typeof res.data === 'object') resolve(res.data)
        else reject(new ContentError(LOAD_FAILED, res.statusCode))
      },
      fail: (error) => {
        const timedOut = ((error && error.errMsg) || '').includes('timeout')
        reject(new ContentError(timedOut ? '连接超时，请检查网络后重试' : '网络异常，请检查网络后重试'))
      },
    })
  })
}

// 后端返回站内路径，这里拼成小程序 image 组件可用的完整地址。
function absoluteUrl(path) {
  return typeof path === 'string' && path.startsWith('/') ? `${API_BASE_URL}${path}` : ''
}

const isString = (value) => typeof value === 'string'
const isOptionalString = (value) => value === null || isString(value)
const isOptionalNumber = (value) => value === null || typeof value === 'number'

// 校验并整理一张房间卡片；不符合约定的字段直接判为结构错误。
function toRoomCard(item) {
  if (
    !item || typeof item.id !== 'number' || !isString(item.name)
    || !isOptionalString(item.bed_type) || !isOptionalNumber(item.max_guests)
    || !isOptionalNumber(item.area_sqm) || !isOptionalString(item.cover_url)
  ) {
    throw new ContentError(LOAD_FAILED)
  }
  return {
    id: item.id,
    name: item.name,
    bedType: item.bed_type || '',
    maxGuests: item.max_guests,
    areaSqm: item.area_sqm,
    cover: item.cover_url ? absoluteUrl(item.cover_url) : '',
  }
}

// 房间列表。
async function fetchRooms() {
  const body = await getJson('/api/miniprogram/rooms')
  if (!Array.isArray(body.rooms)) throw new ContentError(LOAD_FAILED)
  return body.rooms.map(toRoomCard)
}

const ROOM_GONE = '房间不存在或已下架'

// 房间详情；房间下架或不存在（404）时报「已下架」，与可重试的加载失败区分。
async function fetchRoom(id) {
  if (!/^\d+$/.test(String(id))) throw new ContentError(ROOM_GONE, 404)
  let body
  try {
    body = await getJson(`/api/miniprogram/rooms/${id}`)
  } catch (error) {
    if (error.statusCode === 404) throw new ContentError(ROOM_GONE, 404)
    throw error
  }
  const card = toRoomCard(body)
  if (
    !isOptionalString(body.intro) || !Array.isArray(body.facilities) || !body.facilities.every(isString)
    || !Array.isArray(body.photos) || !body.photos.every(isString)
  ) {
    throw new ContentError(LOAD_FAILED)
  }
  return {
    ...card,
    intro: body.intro || '',
    facilities: body.facilities,
    photos: body.photos.map(absoluteUrl).filter(Boolean),
  }
}

// 入住指南：分类分组的问答。
async function fetchGuide() {
  const body = await getJson('/api/miniprogram/guide')
  if (!Array.isArray(body.sections)) throw new ContentError(LOAD_FAILED)
  return body.sections.map((section) => {
    if (
      !section || !isString(section.category) || !Array.isArray(section.items)
      || !section.items.every((item) => item && isString(item.question) && isString(item.answer))
    ) {
      throw new ContentError(LOAD_FAILED)
    }
    return {
      category: section.category,
      items: section.items.map((item) => ({ question: item.question, answer: item.answer })),
    }
  })
}

// 三个页面共用的加载流程：每次加载领取新代次，结果返回时代次已变（重新加载或页面卸载）
// 就丢弃，避免迟到的旧结果覆盖新内容。toReadyData 把结果转成页面数据。
async function loadPageData(page, fetcher, toReadyData) {
  page.loadGeneration = (page.loadGeneration || 0) + 1
  const generation = page.loadGeneration
  page.setData({ status: 'loading', message: '' })
  try {
    const result = await fetcher()
    if (generation !== page.loadGeneration) return
    page.setData(toReadyData(result))
  } catch (error) {
    if (generation !== page.loadGeneration) return
    page.setData({
      status: error && error.statusCode === 404 ? 'gone' : 'error',
      message: (error instanceof ContentError && error.message) || LOAD_FAILED,
    })
  }
}

// 页面卸载时调用：让仍在途的请求结果作废。
function discardPendingLoads(page) {
  page.loadGeneration = (page.loadGeneration || 0) + 1
}

module.exports = {
  ContentError,
  fetchRooms,
  fetchRoom,
  fetchGuide,
  loadPageData,
  discardPendingLoads,
}
