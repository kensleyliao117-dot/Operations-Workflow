export const metricFields = [
  ['views', '浏览量'],
  ['impressions', '曝光量'],
  ['clicks', '点击'],
  ['likes', '点赞'],
  ['favorites', '收藏'],
  ['comments', '评论'],
  ['conversions', '转化'],
]
export const metricSources = {
  manual: '后台人工录入',
  rednote_public: '公开详情页',
  rednote_creator: '创作中心',
}
export function formatObservationAge(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '发布时间未知，观察窗口待核实'
  let remaining = Math.floor(seconds)
  if (remaining < 60) return `发布后 ${remaining} 秒`
  const parts = []
  for (const [unit, label] of [
    [86400, '天'],
    [3600, '小时'],
    [60, '分钟'],
  ]) {
    const value = Math.floor(remaining / unit)
    if (value) parts.push(`${value} ${label}`)
    remaining %= unit
  }
  return `发布后 ${parts.join(' ')}`
}
export function localDateTime() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 19)
}
