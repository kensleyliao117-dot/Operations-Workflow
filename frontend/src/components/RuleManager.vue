<script setup>
import { ref, reactive, watch } from 'vue'
import BaseDialog from './BaseDialog.vue'
import { api } from '../api/index.js'
import { action, busy, notify } from '../stores/ui.js'
const props = defineProps({
  open: Boolean,
  inline: Boolean,
  platform: { type: String, default: 'rednote' },
})
const emit = defineEmits(['close'])
const rules = ref([]),
  platform = ref([]),
  selected = ref('')
const form = reactive({
  content: '',
  rule_type: 'ai',
  keywords: '',
  max_length: 20,
  targets: ['title', 'body', 'tags'],
  severity: 'medium',
  is_active: true,
})
async function load() {
  await action('load-rules', async () => {
    const id = props.platform
    const values = await Promise.all([api.rules.custom(id), api.rules.platform(id)])
    if (id === props.platform) [rules.value, platform.value] = values
  })
}
function select() {
  const id = String(selected.value)
  const r = id.startsWith('platform:')
    ? platform.value.find((r) => r.id === Number(id.slice(9)))
    : rules.value.find((r) => r.id === Number(id))
  Object.assign(
    form,
    r
      ? {
          content: r.content || '',
          rule_type: r.rule_type,
          keywords: (r.keywords || []).join('，'),
          max_length: r.max_length || 20,
          targets: [...r.targets],
          severity: r.severity,
          is_active: r.is_active,
        }
      : {
          content: '',
          rule_type: 'ai',
          keywords: '',
          max_length: 20,
          targets: ['title', 'body', 'tags'],
          severity: 'medium',
          is_active: true,
        },
  )
}
async function save() {
  if (!form.targets.length) {
    notify('请选择至少一个检查字段', 'error')
    return
  }
  await action('save-rule', async () => {
    const data = {
      platform_name: props.platform,
      content: form.content.trim() || null,
      rule_type: form.rule_type,
      targets: form.targets,
      severity: form.severity,
      is_active: form.is_active,
      keywords:
        form.rule_type === 'keyword'
          ? [
              ...new Set(
                form.keywords
                  .split(/[,，\n]/)
                  .map((v) => v.trim())
                  .filter(Boolean),
              ),
            ]
          : [],
      max_length: form.rule_type === 'length' ? Number(form.max_length) : null,
    }
    if (String(selected.value).startsWith('platform:'))
      await api.rules.updatePlatform(Number(String(selected.value).slice(9)), data)
    else if (selected.value) await api.rules.update(Number(selected.value), data)
    else await api.rules.create(data)
    notify('规则已保存，后续检查采用新配置；风险不阻止审核和发布')
    await load()
  })
}
async function remove() {
  const id = String(selected.value)
  if (
    !id ||
    !window.confirm('确认移除此规则？公共参考规则的移除仅影响当前账号，历史风险记录保留。')
  )
    return
  await action('save-rule', async () => {
    if (id.startsWith('platform:')) await api.rules.removePlatform(Number(id.slice(9)))
    else await api.rules.remove(Number(id))
    selected.value = ''
    select()
    await load()
    notify('规则已移除')
  })
}
watch(
  () => props.platform,
  () => {
    selected.value = ''
    rules.value = []
    platform.value = []
    select()
    if (props.open) load()
  },
)
watch(
  () => props.open,
  (value) => {
    if (value) load()
  },
  { immediate: true },
)
</script>
<template>
  <component
    :is="inline ? 'section' : BaseDialog"
    :class="{ 'settings-surface': inline }"
    :open="open"
    title="自定义检查规则"
    :busy="busy.has('save-rule')"
    @close="emit('close')"
  >
    <p class="notice">
      规则可新增、修改或移除。公共参考项的修改只影响当前账号；检查仅作提示，不阻止保存或发布，也不保证平台审核通过。
    </p>
    <label class="field">
      <span class="field-label">已有规则</span>
      <select v-model="selected" @change="select">
        <option value="">＋ 新建规则</option>
        <option v-for="rule in rules" :key="rule.id" :value="rule.id">
          #{{ rule.id }} {{ rule.content || rule.rule_type }} · v{{ rule.version
          }}{{ rule.is_active ? '' : '（停用）' }}
        </option>
        <option
          v-for="rule in platform"
          :key="`platform:${rule.id}`"
          :value="`platform:${rule.id}`"
        >
          参考规则 #{{ rule.id }} {{ rule.content || rule.rule_type }} · v{{ rule.version }}
        </option>
      </select>
    </label>
    <form @submit.prevent="save">
      <label class="field">
        <span class="field-label">规则类型</span>
        <select v-model="form.rule_type">
          <option value="ai">AI 辅助检查</option>
          <option value="keyword">关键词匹配</option>
          <option value="length">长度限制</option>
        </select>
      </label>
      <label class="field">
        <span class="field-label">规则说明</span>
        <textarea v-model="form.content" :required="form.rule_type === 'ai'" rows="3" />
      </label>
      <label v-if="form.rule_type === 'keyword'" class="field">
        <span class="field-label">关键词（逗号分隔）</span>
        <input v-model="form.keywords" required />
      </label>
      <label v-if="form.rule_type === 'length'" class="field">
        <span class="field-label">最大字符数</span>
        <input v-model="form.max_length" type="number" min="1" max="50000" required />
      </label>
      <div class="field">
        <span class="field-label">检查字段</span>
        <div class="row">
          <label
            v-for="field in [
              ['title', '标题'],
              ['body', '正文'],
              ['tags', '话题'],
              ['image_prompt', '图片 Prompt'],
            ]"
            :key="field[0]"
            class="check-row"
          >
            <input v-model="form.targets" type="checkbox" :value="field[0]" />
            {{ field[1] }}
          </label>
        </div>
      </div>
      <label class="field">
        <span class="field-label">风险等级</span>
        <select v-model="form.severity">
          <option value="low">低</option>
          <option value="medium">中</option>
          <option value="high">高</option>
        </select>
      </label>
      <label class="check-row">
        <input v-model="form.is_active" type="checkbox" />
        启用规则
      </label>
      <button class="button primary section-gap" :disabled="busy.has('save-rule')">保存规则</button>
      <button
        v-if="selected"
        type="button"
        class="button danger section-gap"
        :disabled="busy.has('save-rule')"
        @click="remove"
      >
        删除规则
      </button>
    </form>
    <details>
      <summary>当前平台规则（{{ platform.length }}）</summary>
      <p v-for="rule in platform" :key="rule.id" class="muted">
        {{ rule.content }} · v{{ rule.version }}
      </p>
    </details>
  </component>
</template>
