import { ref, watch } from 'vue'
import { api } from '../api/index.js'
import { session } from './session.js'

export const analysisModels = ref([])
let turn = 0
watch(
  () => session.user?.id,
  () => {
    ++turn
    analysisModels.value = []
  },
)
export async function loadModels() {
  const request = ++turn
  const values = await api.models.list()
  if (request === turn) analysisModels.value = values
}
