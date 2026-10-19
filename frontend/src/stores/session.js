import { reactive } from 'vue'
import { api, configureAuth } from '../api/index.js'

const key = 'operations.session.v1'
function restore() {
  try {
    return JSON.parse(sessionStorage.getItem(key) || 'null')
  } catch {
    return null
  }
}
const saved = restore()
export const session = reactive({ token: saved?.token || '', user: null, restored: false })
let validation
export function clearSession() {
  const owner = session.user?.id
  if (owner) {
    for (const key of Object.keys(localStorage))
      if (key.startsWith(`operations.user.${owner}.`)) localStorage.removeItem(key)
  }
  session.token = ''
  session.user = null
  session.restored = false
  validation = null
  sessionStorage.removeItem(key)
}
configureAuth(
  () => session.token,
  (issuedToken) => {
    if (session.token === issuedToken) {
      clearSession()
      window.dispatchEvent(new Event('session-expired'))
    }
  },
)
export async function validateSession() {
  if (!session.token) return false
  if (session.restored) return true
  if (!validation)
    validation = api.auth
      .me()
      .then((user) => {
        session.user = user
        session.restored = true
        return true
      })
      .finally(() => {
        validation = null
      })
  return validation
}
export async function login(data) {
  const result = await api.auth.login(data)
  clearSession()
  session.token = result.access_token
  try {
    await validateSession()
    sessionStorage.setItem(key, JSON.stringify({ token: session.token }))
  } catch (error) {
    clearSession()
    throw error
  }
}
