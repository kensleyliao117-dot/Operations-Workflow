<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { api } from '../api/index.js'
import { action, busy, notify } from '../stores/ui.js'
import { safeLink, displayDate } from '../utils/domain.js'
import { analysisModels as models, loadModels } from '../stores/models.js'
import { session } from '../stores/session.js'
import { readLocal, writeLocal, userKey } from '../utils/storage.js'
import ModelManager from './ModelManager.vue'
import BaseDialog from './BaseDialog.vue'
const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  disabled: Boolean,
  selectable: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue'])
const caseEditor = ref(false),
  editingCase = ref(null)
const caseForm = reactive({ name: '', link: '', body: '' })
function editCase(item = null) {
  editingCase.value = item?.id || null
  Object.assign(caseForm, {
    name: item?.name || '',
    link: item?.link || '',
    body: item?.body || '',
  })
  caseEditor.value = true
}
async function saveCase() {
  const id = editingCase.value,
    data = { ...caseForm }
  await action('save-case', async () => {
    if (id) await api.cases.update(id, data)
    else await api.cases.create(data)
    caseEditor.value = false
    await search()
    notify('案例已保存；修改原文不会覆盖已有分析，后续可重新分析')
  })
}
async function deleteCase(item) {
  if (
    !window.confirm(
      '确认从案例库删除此案例？已生成内容的历史引用保留，再次采集同一链接可恢复案例。',
    )
  )
    return
  await action(`delete-case:${item.id}`, async () => {
    const analyses = await api.cases.analyses({ cases_id: item.id, limit: 100 })
    await api.cases.remove(item.id)
    setSelection(selectedIds.value.filter((id) => !analyses.some((value) => value.id === id)))
    delete histories[item.id]
    await search()
    notify('案例已从当前账号的案例库删除')
  })
}
const selectedIds = ref([...props.modelValue])
watch(
  () => props.modelValue,
  (ids) => {
    selectedIds.value = [...ids]
    hydrateSelection()
  },
  { deep: true },
)
function setSelection(ids) {
  selectedIds.value = [...new Set(ids)]
  emit('update:modelValue', selectedIds.value)
}
function removeSelection(id) {
  setSelection(selectedIds.value.filter((value) => value !== id))
}
async function hydrateSelection() {
  await Promise.allSettled(
    selectedIds.value.map(async (id) => {
      if (Object.values(histories).some((rows) => rows.some((row) => row.id === id))) return
      const value = await api.cases.analysis(id)
      if (!histories[value.cases_id]?.some((row) => row.id === id))
        (histories[value.cases_id] ||= []).push(value)
      selected[value.cases_id] = id
    }),
  )
}
const cases = ref([]),
  q = ref(''),
  links = ref(''),
  currentTab = ref(false),
  model = ref(readLocal(userKey(session.user.id, 'analysis-model')) || ''),
  showModels = ref(false),
  browser = ref(null),
  offset = ref(0),
  more = ref(false),
  histories = reactive({}),
  expanded = ref(null),
  selected = reactive({}),
  feedback = ref(''),
  comparison = ref(null),
  compareLeft = ref(''),
  compareRight = ref('')
watch(model, (id) => {
  if (session.user) writeLocal(userKey(session.user.id, 'analysis-model'), id)
})
watch(models, (rows) => {
  if (!rows.some((m) => m.id === model.value && m.ready && m.is_active !== false))
    model.value =
      (
        rows.find((m) => m.ready && m.is_default && m.is_active !== false) ||
        rows.find((m) => m.ready && m.is_active !== false)
      )?.id || ''
})
async function search(append = false) {
  await action('case-search', async () => {
    const at = append ? offset.value : 0
    const values = await api.cases.search({ q: q.value.trim() || undefined, offset: at, limit: 20 })
    cases.value = append ? [...cases.value, ...values] : values
    offset.value = at + values.length
    more.value = values.length === 20
  })
}
async function history(id) {
  histories[id] = await api.cases.analyses({ cases_id: id, limit: 100 })
  histories[id].sort((a, b) => b.version - a.version)
}
async function collect() {
  const urls = currentTab.value
    ? [null]
    : [
        ...new Set(
          links.value
            .split(/\r?\n/)
            .map((v) => v.trim())
            .filter(Boolean),
        ),
      ]
  if (!urls.length || urls.length > 10) {
    notify('请输入 1–10 个完整帖子链接，每行一个。', 'error')
    return
  }
  await action('collect-cases', async () => {
    let failures = 0
    for (const url of urls) {
      try {
        const value = await api.cases.collect(url ? { url } : {})
        notify(
          value.case_already_saved
            ? '该案例已在案例库中，已复用已有记录'
            : `已采集：${value.case.name}`,
        )
      } catch (e) {
        ++failures
        notify(e.message, 'error')
      }
    }
    if (!failures) links.value = ''
    await search()
  })
}
async function analyze(item) {
  await action(`analyze-case:${item.id}`, async () => {
    if (!model.value) throw new Error('请选择已启用且配置了凭据的分析模型')
    const value = await api.cases.analyze(item.id, model.value ? { model_id: model.value } : {})
    await history(item.id)
    expanded.value = item.id
    selected[item.id] =
      histories[item.id].find((analysis) => selectedIds.value.includes(analysis.id))?.id || value.id
    await search()
    notify(`分析已保存为 v${value.version}`)
  })
}
async function toggle(item, checked) {
  if (!checked) {
    setSelection(
      selectedIds.value.filter(
        (id) => !histories[item.id]?.some((a) => a.id === id) && id !== item.latest_analysis_id,
      ),
    )
    return
  }
  if (selectedIds.value.length >= 5) {
    notify('每次生成最多引用 5 个分析版本', 'error')
    return
  }
  await action(`select-analysis:${item.id}`, async () => {
    await history(item.id)
    const value =
      histories[item.id].find((a) => a.id === Number(selected[item.id])) ||
      histories[item.id].find((a) => a.result)
    if (!value?.result) {
      notify('请先完成 AI 分析，再选择结构化分析结果。', 'error')
      return
    }
    selected[item.id] = value.id
    if (selectedIds.value.length >= 5) throw new Error('每次生成最多引用 5 个分析版本')
    setSelection([...selectedIds.value, value.id])
  })
}
function isSelected(item) {
  return (
    selectedIds.value.includes(item.latest_analysis_id) ||
    histories[item.id]?.some((a) => selectedIds.value.includes(a.id))
  )
}
function changeVersion(item) {
  if (!isSelected(item)) return
  const ids = selectedIds.value.filter((id) => !histories[item.id].some((a) => a.id === id))
  const value = histories[item.id].find((a) => a.id === Number(selected[item.id]))
  if (value?.result) ids.push(value.id)
  setSelection(ids)
}
async function revise(id) {
  const analysis = histories[id]?.find((a) => a.id === Number(selected[id])) || histories[id]?.[0]
  if (!analysis || !feedback.value.trim()) return
  await action('revise-case', async () => {
    if (!model.value) throw new Error('请选择已启用且配置了凭据的分析模型')
    const saved = await api.cases.feedback(analysis.id, feedback.value.trim())
    notify('补充反馈已保存，正在生成修订版本')
    const value = await api.cases.revise(analysis.id, {
      feedback_ids: [saved.id],
      ...(model.value ? { model_id: model.value } : {}),
    })
    await history(id)
    feedback.value = ''
    selected[id] =
      histories[id].find((analysis) => selectedIds.value.includes(analysis.id))?.id || value.id
    notify('修订分析已保存，可在版本列表明确选择修订结果')
    await search()
  })
}
onMounted(() => {
  search()
  hydrateSelection()
  action('analysis-models', async () => {
    await loadModels()
  })
})
</script>
<template>
  <section class="panel">
    <div class="panel-header">
      <h3>{{ selectable ? '爆款案例参考' : '已保存的案例' }}</h3>
      <button class="button small" :disabled="disabled" @click="editCase()">＋ 手动添加案例</button>
      <span v-if="selectable" class="muted">已选 {{ selectedIds.length }}/5</span>
    </div>
    <div v-if="selectable && selectedIds.length" class="row section-gap">
      <button
        v-for="id in selectedIds"
        :key="id"
        class="button small"
        :disabled="disabled"
        @click="removeSelection(id)"
      >
        已选分析 #{{ id }} ×
      </button>
    </div>
    <p class="muted">案例用于借鉴结构与表达，产品事实以当前产品资料为准。</p>
    <details>
      <summary>采集帖子链接</summary>
      <label class="field section-gap">
        <span class="field-label">完整网页链接（每行一个）</span>
        <textarea
          v-model="links"
          rows="3"
          :disabled="currentTab || disabled"
          placeholder="https://www.xiaohongshu.com/explore/..."
        />
      </label>
      <label class="check-row">
        <input v-model="currentTab" type="checkbox" :disabled="disabled" />
        读取远程调试 Chrome 中已打开的单篇帖子
      </label>
      <div class="row section-gap">
        <button class="button" :disabled="disabled || busy.has('collect-cases')" @click="collect">
          {{ busy.has('collect-cases') ? '采集中…' : '采集并保存' }}
        </button>
        <button
          class="button ghost small"
          :disabled="busy.has('browser-status')"
          @click="action('browser-status', async () => (browser = await api.cases.browser()))"
        >
          检测 Chrome
        </button>
      </div>
      <p v-if="browser" :class="browser.connected ? 'muted' : 'error-text'">
        {{ browser.message }}
      </p>
      <p class="muted section-gap">
        请先按 docs/collectors.md 开启远程调试并登录。当前支持链接采集，关键词自动搜索尚未接入。
      </p>
    </details>
    <hr class="section-divider" />
    <div class="row">
      <input
        v-model="q"
        class="grow"
        placeholder="搜索已保存的案例标题、正文"
        aria-label="搜索案例库"
        @keyup.enter="search()"
      />
      <button class="button small" :disabled="busy.has('case-search')" @click="search()">
        搜索
      </button>
    </div>
    <label class="field section-gap">
      <span class="field-label">分析模型</span>
      <select v-model="model" aria-label="分析模型" :disabled="disabled">
        <option value="" disabled>选择已配置凭据的模型</option>
        <option
          v-for="value in models"
          :key="value.id"
          :value="value.id"
          :disabled="!value.ready || value.is_active === false"
        >
          {{ value.name || value.model }} · {{ value.provider
          }}{{ value.is_active === false ? '（已停用）' : value.ready ? '' : '（未配置凭据）' }}
        </option>
      </select>
    </label>
    <button class="button ghost small" @click="showModels = true">＋ 添加或管理分析模型</button>
    <p class="muted small-text">
      切换模型后重新分析会创建新版本，不覆盖已有结果。模型选择会按当前账号保存。
    </p>
    <div class="stack">
      <article v-for="item in cases" :key="item.id" class="case-card">
        <div class="row">
          <input
            v-if="selectable"
            type="checkbox"
            :aria-label="`引用案例：${item.name}`"
            :checked="isSelected(item)"
            :disabled="disabled || busy.has(`select-analysis:${item.id}`)"
            @change="toggle(item, $event.target.checked)"
          />
          <div class="grow">
            <div class="case-title">{{ item.name }}</div>
            <small class="muted">{{ item.history_hint }}</small>
          </div>
          <img
            v-if="safeLink(item.image_urls?.[0])"
            class="case-thumb"
            :src="safeLink(item.image_urls[0])"
            alt="案例缩略图"
            referrerpolicy="no-referrer"
            loading="lazy"
            @error="$event.target.hidden = true"
          />
        </div>
        <p>{{ item.body?.slice(0, 140) || '暂无正文摘要' }}</p>
        <div class="row">
          <span class="chip">赞 {{ item.metrics?.likes ?? '—' }}</span>
          <span class="chip">藏 {{ item.metrics?.favorites ?? '—' }}</span>
          <span class="chip">评 {{ item.metrics?.comments ?? '—' }}</span>
          <a
            v-if="safeLink(item.link)"
            :href="safeLink(item.link)"
            target="_blank"
            rel="noopener noreferrer"
          >
            原帖 ↗
          </a>
        </div>
        <div class="row section-gap">
          <button
            class="button small"
            :disabled="disabled || busy.has(`analyze-case:${item.id}`)"
            @click="analyze(item)"
          >
            {{
              busy.has(`analyze-case:${item.id}`)
                ? '分析中…'
                : item.already_analyzed
                  ? '重新分析 · 新版本'
                  : 'AI 分析'
            }}
          </button>
          <button
            class="button ghost small"
            @click="
              action(`history:${item.id}`, async () => {
                expanded = expanded === item.id ? null : item.id
                if (expanded) await history(item.id)
              })
            "
          >
            分析与版本
          </button>
          <button
            class="button small"
            :disabled="disabled || busy.has('save-case')"
            @click="editCase(item)"
          >
            编辑案例
          </button>
          <button
            class="button danger small"
            :disabled="disabled || busy.has(`delete-case:${item.id}`)"
            @click="deleteCase(item)"
          >
            删除案例
          </button>
        </div>
        <div v-if="expanded === item.id" class="section-gap">
          <label class="field">
            <span class="field-label">分析版本</span>
            <select
              v-model="selected[item.id]"
              aria-label="分析版本"
              :disabled="disabled"
              @change="changeVersion(item)"
            >
              <option
                v-for="analysis in histories[item.id]"
                :key="analysis.id"
                :value="analysis.id"
              >
                v{{ analysis.version }} · {{ analysis.model || '手工分析' }} ·
                {{ displayDate(analysis.created_at) }}
              </option>
            </select>
          </label>
          <template
            v-for="analysis in histories[item.id]?.filter(
              (a) => a.id === Number(selected[item.id] || histories[item.id]?.[0]?.id),
            )"
            :key="analysis.id"
          >
            <p class="preserve">{{ analysis.result?.summary || analysis.content }}</p>
            <details v-if="analysis.result">
              <summary>结构、Hook 与可复用写法</summary>
              <pre>{{ JSON.stringify(analysis.result, null, 2) }}</pre>
            </details>
          </template>
          <label class="field section-gap">
            <span class="field-label">补充反馈</span>
            <textarea
              v-model="feedback"
              rows="3"
              maxlength="5000"
              placeholder="指出分析不足或补充新的创作思路"
            />
          </label>
          <button
            class="button small"
            :disabled="disabled || !feedback.trim() || busy.has('revise-case')"
            @click="revise(item.id)"
          >
            保存反馈并修订
          </button>
          <details v-if="histories[item.id]?.length > 1">
            <summary>比较分析版本</summary>
            <div class="row section-gap">
              <select v-model="compareLeft" class="grow" aria-label="左侧分析版本">
                <option value="">选择左侧版本</option>
                <option v-for="a in histories[item.id]" :key="a.id" :value="a.id">
                  v{{ a.version }} · {{ a.model }}
                </option>
              </select>
              <select v-model="compareRight" class="grow" aria-label="右侧分析版本">
                <option value="">选择右侧版本</option>
                <option v-for="a in histories[item.id]" :key="a.id" :value="a.id">
                  v{{ a.version }} · {{ a.model }}
                </option>
              </select>
              <button
                class="button small"
                :disabled="!compareLeft || !compareRight || compareLeft === compareRight"
                @click="
                  action(
                    'compare',
                    async () => (comparison = await api.cases.compare(compareLeft, compareRight)),
                  )
                "
              >
                比较
              </button>
            </div>
            <template v-if="comparison?.case_id === item.id">
              <p class="muted">{{ comparison.comparison_hint }}</p>
              <div v-for="difference in comparison.differences" :key="difference.field">
                <strong>{{ difference.field }}</strong>
                <pre>
版本 A：{{ difference.left }}
版本 B：{{ difference.right }}</pre>
              </div>
            </template>
          </details>
        </div>
      </article>
    </div>
    <p v-if="!cases.length" class="muted section-gap">案例库暂无匹配内容，可以先采集帖子链接。</p>
    <button
      v-if="more"
      class="button small section-gap"
      :disabled="busy.has('case-search')"
      @click="search(true)"
    >
      加载更多案例
    </button>
    <BaseDialog
      :open="caseEditor"
      title="案例资料"
      :busy="busy.has('save-case')"
      @close="caseEditor = false"
    >
      <form @submit.prevent="saveCase">
        <label class="field">
          <span class="field-label">案例标题</span>
          <input v-model="caseForm.name" required />
        </label>
        <label class="field">
          <span class="field-label">案例链接</span>
          <input v-model="caseForm.link" type="url" required />
        </label>
        <label class="field">
          <span class="field-label">案例正文</span>
          <textarea v-model="caseForm.body" rows="8" />
        </label>
        <button class="button primary" :disabled="busy.has('save-case')">保存案例</button>
      </form>
    </BaseDialog>
    <ModelManager :open="showModels" @close="showModels = false" />
  </section>
</template>
