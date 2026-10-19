import { ref, onScopeDispose } from 'vue'
import { api } from '../api/index.js'

export function useTask() {
  const task = ref(null),
    workflow = ref(null),
    contents = ref([]),
    loading = ref(false),
    error = ref('')
  let currentId,
    generation = 0,
    timer,
    closed = false
  async function refresh(id = currentId) {
    if (!id || closed) return
    currentId = Number(id)
    const turn = ++generation
    clearTimeout(timer)
    loading.value = true
    try {
      const [nextTask, nextContents, nextWorkflow] = await Promise.all([
        api.tasks.get(id),
        api.content.list({ task_id: id, limit: 100 }),
        api.workflow.get(id).catch((e) => {
          if (e.status === 404) return null
          throw e
        }),
      ])
      if (turn !== generation || closed) return
      task.value = nextTask
      contents.value = nextContents.sort((a, b) => b.version - a.version)
      workflow.value = nextWorkflow
      error.value = ''
    } catch (e) {
      if (turn === generation && !closed) error.value = e.message
    } finally {
      if (turn === generation && !closed) {
        loading.value = false
        if (
          task.value &&
          (['running', 'publishing'].includes(task.value.status) ||
            workflow.value?.status === 'running' ||
            workflow.value?.status === 'generating_images')
        )
          timer = setTimeout(() => refresh(), 3500)
      }
    }
  }
  function reset() {
    ++generation
    currentId = null
    clearTimeout(timer)
    task.value = null
    workflow.value = null
    contents.value = []
    error.value = ''
    loading.value = false
  }
  onScopeDispose(() => {
    closed = true
    reset()
  })
  return { task, workflow, contents, loading, error, refresh, reset }
}
