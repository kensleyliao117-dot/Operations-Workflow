export function userKey(user, kind, id = '') {
  return `operations.user.${user}.${kind}.${id}`
}
export function readLocal(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || 'null')
  } catch {
    return null
  }
}
export function writeLocal(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}
export function publishKey(user, task, content, account, visibility) {
  const name = userKey(user, 'publish-request', `${task}.${content}.${account}.${visibility}`)
  const current = readLocal(name)
  if (typeof current === 'string' && /^[\w-]{8,100}$/.test(current)) return current
  const key = `publish-${crypto.randomUUID()}`
  writeLocal(name, key)
  return key
}
export function download(blob, filename) {
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}
