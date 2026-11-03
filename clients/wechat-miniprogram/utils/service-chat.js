// 「问管家」：打开企业微信的微信客服会话，AI 与人工仍在那边回复（小程序住客自助 Spec F5）。
// 企业 ID 与客服链接都是公开值，需在微信客服后台绑定本小程序后才能打开。
const { fetchHome } = require('./content')
const { dialNumber } = require('./format')

// 由民宿方提供后填写；为空时退回拨打门店电话（质感 Spec T1），填好后自动改走在线客服。
const CORP_ID = ''
const SERVICE_URL = ''

const UNAVAILABLE = '暂时无法打开客服，请稍后再试'
const NOT_OPENED = '客服暂未开通'

// 连点时只处理第一次，避免读取门店电话期间叠出多个确认框。
let fallbackPending = false

// 读取门店公开电话；读取失败或没填都返回空串，由调用方退回原提示。
async function storePhone() {
  try {
    const home = await fetchHome()
    return (home.store && home.store.phone) || ''
  } catch (error) {
    return ''
  }
}

// 客服未开通时的兜底：有门店电话就确认后拨打，没有就提示暂未开通。
// 每次点击都重新读取，管理员改了电话能立刻生效（/home 不缓存）。
async function callStoreInstead() {
  if (fallbackPending) return
  fallbackPending = true
  try {
    const phone = await storePhone()
    const number = dialNumber(phone)
    if (!number) {
      wx.showToast({ title: NOT_OPENED, icon: 'none' })
      return
    }
    wx.showModal({
      title: '在线客服暂未开通',
      content: `拨打门店电话 ${phone}？`,
      confirmText: '拨打',
      success: (res) => {
        // 客人取消拨号也会进 fail，因此失败时不提示。
        if (res.confirm) wx.makePhoneCall({ phoneNumber: number, fail: () => {} })
      },
    })
  } finally {
    fallbackPending = false
  }
}

// 打开客服会话；未配置时退回拨打门店电话，微信返回失败时只给出固定提示。
function openServiceChat() {
  if (!CORP_ID || !SERVICE_URL) return callStoreInstead()
  wx.openCustomerServiceChat({
    extInfo: { url: SERVICE_URL },
    corpId: CORP_ID,
    fail: () => wx.showToast({ title: UNAVAILABLE, icon: 'none' }),
  })
  return Promise.resolve()
}

module.exports = { openServiceChat }
