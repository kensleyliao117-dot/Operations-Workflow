export const taskLabels = {
  pending: '待生成',
  running: '处理中 / 待修改',
  pending_review: '待人工审核',
  approved: '审核通过',
  rejected: '已退回',
  publishing: '发布中 / 待确认',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}
export const workflowLabels = {
  loading_analyses: '读取案例分析',
  generating: '生成文案',
  checking_rules: '检查平台规则',
  platform_rules: '检查平台规则',
  waiting_review: '等待人工审核',
  waiting_changes: '等待修改',
  waiting_rule_changes: '等待风险修改',
  approved: '审核通过',
  cancelled: '已取消',
  failed: '执行失败',
  image_generation: '生成配图',
  generating_images: '生成配图',
  running: '工作流执行中',
}
export const ruleLabels = {
  no_risk_detected: '未发现风险',
  risk_detected: '发现风险',
  incomplete: '检查不完整',
  error: '检查失败',
}
export const documentLabels = {
  uploaded: '已上传 · 待入库',
  indexed: '已入库',
  index_failed: '入库失败',
  deleting: '删除中',
}
export const publishLabels = {
  pending: '待发布',
  publishing: '发布处理中',
  published: '已发布',
  failed: '发布失败',
  unknown: '结果不明 · 请核实',
}
export function canEdit(task, content, latest) {
  return (
    !!task &&
    !!content &&
    content.id === latest?.id &&
    !['approved', 'publishing', 'completed', 'cancelled'].includes(task.status) &&
    !(task.status === 'failed' && task.failed_from_status === 'publishing')
  )
}
export function canApprove(task, content, latest, check, dirty) {
  return !!content && !dirty && task?.status === 'pending_review' && content.id === latest?.id
}
export function reviewSubmissionAction(task, content, latest, workflow, dirty = false) {
  if (
    !canEdit(task, content, latest) ||
    !['running', 'pending_review', 'rejected'].includes(task.status)
  )
    return null
  if (
    workflow?.status === 'failed' &&
    workflow.next_nodes?.length === 1 &&
    workflow.next_nodes[0] === 'platform_rules' &&
    ['running', 'pending_review'].includes(task.status)
  )
    return 'retry_review'
  if (
    workflow &&
    (workflow.interrupt == null ||
      !['waiting_review', 'waiting_rule_changes', 'waiting_changes'].includes(workflow.status))
  )
    return null
  if (dirty) return workflow ? 'submit_changes' : 'check'
  if (!workflow) return task.status === 'pending_review' ? null : 'check'
  if (content.version > workflow.content_version) return 'submit_changes'
  if (task.status !== 'running') return null
  return content.id === workflow.content_id && content.version === workflow.content_version
    ? 'submit_review'
    : null
}
export function canPublish(task, content, latest, records, dirty, workflow) {
  return (
    !!content &&
    !dirty &&
    task?.status === 'approved' &&
    content.id === latest?.id &&
    (!workflow || workflow.status === 'approved') &&
    !records.some((r) => ['publishing', 'published', 'unknown'].includes(r.publish_status))
  )
}
export function displayDate(value, utc = false) {
  if (!value) return '—'
  const normalized = utc && !/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value + 'Z' : value
  const date = new Date(normalized)
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(date)
}
export function formatSize(bytes) {
  return bytes < 1024 * 1024
    ? `${Math.ceil(bytes / 1024)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
export function safeLink(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : undefined
  } catch {
    return undefined
  }
}
export function splitTags(value) {
  return [
    ...new Set(
      value
        .split(/[\s,，]+/)
        .map((v) => v.replace(/^#+/, '').trim())
        .filter(Boolean),
    ),
  ]
}
