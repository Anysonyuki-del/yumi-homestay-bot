// 「问管家」：打开企业微信的微信客服会话，AI 与人工仍在那边回复（小程序住客自助 Spec F5）。
// 企业 ID 与客服链接都是公开值；需在微信客服后台把本小程序接入该客服账号，且小程序与企业微信同一主体。
const { fetchHome } = require('./content')
const { dialNumber } = require('./format')

// 用户 2026-11-03 提供：企业微信企业 ID 与机器人所用微信客服账号的客服链接。
const CORP_ID = 'ww6412ad55e27f12dd'
const SERVICE_URL = 'https://work.weixin.qq.com/kfid/kfc6727f14ed9ac0252'

const UNAVAILABLE = '暂时无法打开客服，请稍后再试'

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

// 客服打不开时的兜底（质感 Spec T1）：有门店电话就确认后拨打，没有就提示稍后再试。
// 每次都重新读取，管理员改了电话能立刻生效（/home 不缓存）。
async function callStoreInstead() {
  if (fallbackPending) return
  fallbackPending = true
  try {
    const phone = await storePhone()
    const number = dialNumber(phone)
    if (!number) {
      wx.showToast({ title: UNAVAILABLE, icon: 'none' })
      return
    }
    wx.showModal({
      title: '暂时打不开在线客服',
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

// 打开客服会话；微信返回失败（如未接入、主体不一致、基础库过旧）时退回拨打门店电话。
// 返回的 Promise 在会话打开或兜底处理完后完成，供页面与测试等待。
function openServiceChat() {
  return new Promise((resolve) => {
    wx.openCustomerServiceChat({
      extInfo: { url: SERVICE_URL },
      corpId: CORP_ID,
      success: () => resolve(),
      fail: () => callStoreInstead().then(resolve),
    })
  })
}

module.exports = { openServiceChat }
