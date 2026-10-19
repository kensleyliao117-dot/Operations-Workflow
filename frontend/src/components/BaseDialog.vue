<script setup>
import { ref, watch, onUnmounted, nextTick, useId } from 'vue'
const props = defineProps({ open: Boolean, title: String, busy: Boolean })
const emit = defineEmits(['close'])
const dialog = ref(null)
const titleId = useId()
let before
watch(
  () => props.open,
  async (value) => {
    await nextTick()
    if (value && !dialog.value?.open) {
      before = document.activeElement
      dialog.value?.showModal()
    }
    if (!value && dialog.value?.open) {
      dialog.value.close()
      before?.focus?.()
    }
  },
)
onUnmounted(() => {
  if (dialog.value?.open) dialog.value.close()
})
</script>
<template>
  <dialog
    ref="dialog"
    class="dialog"
    :aria-labelledby="titleId"
    @cancel.prevent="!busy && emit('close')"
  >
    <header class="panel-header">
      <h2 :id="titleId">{{ title }}</h2>
      <button
        type="button"
        class="icon-button"
        :disabled="busy"
        aria-label="关闭弹窗"
        @click="emit('close')"
      >
        ×
      </button>
    </header>
    <div class="dialog-content"><slot /></div>
  </dialog>
</template>
