<script setup>
import { computed, ref, watch } from 'vue'
import { api, allPages } from '../api/index.js'
import { action, notify } from '../stores/ui.js'
import BaseDialog from './BaseDialog.vue'
const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const open = ref(false),
  cases = ref([]),
  analyses = ref([]),
  query = ref(''),
  loading = ref(false)
const filtered = computed(() =>
  cases.value.filter((c) => c.name.toLowerCase().includes(query.value.toLowerCase())),
)
const selected = computed(() =>
  props.modelValue.map((id) => analyses.value.find((a) => a.id === id) || { id }),
)
async function load() {
  loading.value = true
  await action('reference-library', async () => {
    const values = await Promise.all([allPages(api.cases.search), allPages(api.cases.analyses)])
    cases.value = values[0]
    analyses.value = values[1]
  })
  loading.value = false
}
function choose(id, checked) {
  if (props.disabled) return
  if (checked && props.modelValue.length >= 5) {
    notify('最多选择 5 个分析版本')
    return
  }
  emit(
    'update:modelValue',
    checked ? [...new Set([...props.modelValue, id])] : props.modelValue.filter((v) => v !== id),
  )
}
watch(open, (value) => {
  if (value) load()
})
</script>
<template>
  <div class="reference-choice">
    <div class="row between">
      <div>
        <span class="field-label">参考案例</span>
        <span class="muted small-text">· 可选，最多 5 个分析版本</span>
      </div>
      <button class="button small" type="button" :disabled="disabled" @click="open = true">
        选择参考案例
      </button>
    </div>
    <div v-if="modelValue.length" class="row section-gap">
      <button
        v-for="item in selected"
        :key="item.id"
        class="chip reference-chip"
        type="button"
        :disabled="disabled"
        @click="choose(item.id, false)"
      >
        已选分析 #{{ item.id }}{{ item.version ? ` · v${item.version}` : '' }} ×
      </button>
    </div>
    <p v-else class="muted small-text">
      使用已有分析作为创作参考。采集、重新分析和版本对比在案例库中完成。
    </p>
    <Teleport to="body">
      <BaseDialog :open="open" title="选择参考案例" @close="open = false">
        <p class="muted">仅引用分析的结构和表达思路，产品事实仍以当前产品资料为准。</p>
        <input
          v-model="query"
          aria-label="搜索参考案例"
          placeholder="搜索案例标题"
          class="search"
          @keydown.enter.prevent
        />
        <p v-if="loading" class="loading-line">读取已保存分析…</p>
        <article v-for="item in filtered" :key="item.id" class="reference-item">
          <h3>{{ item.name }}</h3>
          <p v-if="!analyses.some((a) => a.cases_id === item.id && a.result)" class="muted">
            尚无结构化分析，请先到案例库完成分析。
          </p>
          <label
            v-for="analysis in analyses
              .filter((a) => a.cases_id === item.id && a.result)
              .sort((a, b) => b.version - a.version)"
            :key="analysis.id"
            class="check-row reference-version"
          >
            <input
              type="checkbox"
              :aria-label="`引用案例：${item.name} v${analysis.version}`"
              :checked="modelValue.includes(analysis.id)"
              :disabled="disabled"
              @change="choose(analysis.id, $event.target.checked)"
            />
            <span>
              v{{ analysis.version }} · {{ analysis.model }}
              <small class="muted">{{ analysis.result.summary }}</small>
            </span>
          </label>
        </article>
        <p v-if="!loading && !filtered.length" class="muted">暂无可选案例。</p>
        <div class="dialog-actions">
          <RouterLink class="button ghost" to="/cases" @click="open = false">前往案例库</RouterLink>
          <button type="button" class="button primary" @click="open = false">
            完成选择 · {{ modelValue.length }}/5
          </button>
        </div>
      </BaseDialog>
    </Teleport>
  </div>
</template>
