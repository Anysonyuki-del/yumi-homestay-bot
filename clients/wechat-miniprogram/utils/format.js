// 页面展示用的文字拼装（暖色界面优化）：只做格式化，不改数据。

// 把床型、可住人数、面积拼成一行，缺的项跳过，例如「1.8 米大床 · 可住 2 人 · 35 ㎡」。
function specLine(room) {
  const parts = []
  if (room.bedType) parts.push(room.bedType)
  if (room.maxGuests) parts.push(`可住 ${room.maxGuests} 人`)
  if (room.areaSqm) parts.push(`${room.areaSqm} ㎡`)
  return parts.join(' · ')
}

// 服务宫格的分类图标：按分类名里的关键词挑 TDesign 图标，先命中先用；都不命中用书本。
// 分类名是后台自由填写的文字，关键词表放在客户端，后台不用设置（三标签 Spec F3）。
const CATEGORY_ICONS = [
  [/入住|退房/, 'key'],
  [/wi-?fi|网络/i, 'wifi'],
  [/停车/, 'vehicle'],
  [/周边|美食|交通/, 'location'],
]
const DEFAULT_CATEGORY_ICON = 'book'

function categoryIcon(category) {
  const matched = CATEGORY_ICONS.find(([pattern]) => pattern.test(category || ''))
  return matched ? matched[1] : DEFAULT_CATEGORY_ICON
}

// 拨号前只保留数字和加号；后台允许填空格、短横线和括号方便阅读。
function dialNumber(phone) {
  return String(phone || '').replace(/[^\d+]/g, '')
}

module.exports = { specLine, categoryIcon, dialNumber }
