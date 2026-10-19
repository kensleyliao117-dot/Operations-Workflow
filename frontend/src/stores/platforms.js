import { computed, ref, watch } from 'vue'
import { api } from '../api/index.js'
import { session } from './session.js'
import { readLocal, writeLocal, userKey } from '../utils/storage.js'

export const platforms = ref([])
export const selectedPlatform = ref('rednote')
export const currentPlatform = computed(
  () =>
    platforms.value.find((p) => p.id === selectedPlatform.value) || {
      id: selectedPlatform.value,
      name: selectedPlatform.value,
      workflow: false,
    },
)
let turn = 0
watch(
  () => session.user?.id,
  () => {
    ++turn
    platforms.value = []
    selectedPlatform.value = readLocal(userKey(session.user?.id, 'platform')) || 'rednote'
  },
  { immediate: true },
)

export async function loadPlatforms() {
  const request = ++turn
  const rows = await api.platforms()
  if (request !== turn) return
  platforms.value = rows
  if (!rows.some((p) => p.id === selectedPlatform.value)) selectPlatform(rows[0]?.id || 'rednote')
}
export function selectPlatform(id) {
  if (!platforms.value.some((p) => p.id === id)) return false
  selectedPlatform.value = id
  writeLocal(userKey(session.user?.id, 'platform'), id)
  return true
}
