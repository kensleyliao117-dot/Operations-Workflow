import { reactive } from 'vue'

export const messages = reactive([])
export const busy = reactive(new Set())
export function notify(text, type = 'success') {
  const id = crypto.randomUUID()
  messages.push({ id, text, type })
  setTimeout(() => dismiss(id), type === 'error' ? 14000 : 5000)
}
export function dismiss(id) {
  const at = messages.findIndex((item) => item.id === id)
  if (at >= 0) messages.splice(at, 1)
}
export async function action(key, operation) {
  if (busy.has(key)) return
  busy.add(key)
  try {
    return await operation()
  } catch (error) {
    notify(error.message || '操作失败，请刷新状态后重试', 'error')
    return undefined
  } finally {
    busy.delete(key)
  }
}
