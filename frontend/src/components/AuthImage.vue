<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { api } from '../api/index.js'
import { ui } from '../config/ui.js'
const props = defineProps({ contentId: Number, index: Number })
const src = ref(''),
  failed = ref(false),
  loading = ref(false)
let current = 0
function release() {
  if (src.value) URL.revokeObjectURL(src.value)
  src.value = ''
}
async function load() {
  const turn = ++current
  release()
  failed.value = false
  loading.value = true
  try {
    const blob = await api.content.image(props.contentId, props.index)
    if (turn === current) src.value = URL.createObjectURL(blob)
  } catch {
    if (turn === current) failed.value = true
  } finally {
    if (turn === current) loading.value = false
  }
}
watch(() => [props.contentId, props.index], load, { immediate: true })
onUnmounted(() => {
  ++current
  release()
})
</script>
<template>
  <figure class="content-image">
    <a v-if="src" :href="src" target="_blank" rel="noopener" aria-label="查看原图">
      <img :src="src" :alt="`配图 ${index + 1}`" />
    </a>
    <div v-else class="image-placeholder">
      <img :src="ui.assets.imageFallback" alt="" />
      <span>{{ loading ? '配图加载中…' : '图片加载失败' }}</span>
      <button v-if="failed" class="button small" @click="load">重新加载</button>
    </div>
    <figcaption>配图 {{ index + 1 }} · 点击查看原图</figcaption>
    <a
      v-if="src"
      class="button small"
      :href="src"
      :download="`content-${contentId}-image-${index + 1}.png`"
    >
      下载图片
    </a>
  </figure>
</template>
