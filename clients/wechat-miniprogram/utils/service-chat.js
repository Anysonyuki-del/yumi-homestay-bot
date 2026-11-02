// 「问管家」：打开企业微信的微信客服会话，AI 与人工仍在那边回复（小程序住客自助 Spec F5）。
// 企业 ID 与客服链接都是公开值，需在微信客服后台绑定本小程序后才能打开。

// 由民宿方提供后填写；为空时按钮提示暂未开通，不报错。
const CORP_ID = ''
const SERVICE_URL = ''

const UNAVAILABLE = '暂时无法打开客服，请稍后再试'

// 打开客服会话；配置缺失或微信返回失败时只给出固定提示。
function openServiceChat() {
  if (!CORP_ID || !SERVICE_URL) {
    wx.showToast({ title: '客服暂未开通', icon: 'none' })
    return
  }
  wx.openCustomerServiceChat({
    extInfo: { url: SERVICE_URL },
    corpId: CORP_ID,
    fail: () => wx.showToast({ title: UNAVAILABLE, icon: 'none' }),
  })
}

module.exports = { openServiceChat }
