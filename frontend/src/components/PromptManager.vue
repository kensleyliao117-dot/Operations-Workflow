<script setup>
import { reactive, ref, watch } from 'vue'
import BaseDialog from './BaseDialog.vue'
import { api, allPages } from '../api/index.js'
import { action, busy, notify } from '../stores/ui.js'
const props = defineProps({
  open: Boolean,
  inline: Boolean,
  platform: { type: String, default: 'rednote' },
})
const emit = defineEmits(['close', 'saved'])
const prompts = ref([]),
  selected = ref(''),
  form = reactive({ name: '', prompt_content: '', is_active: true })
async function remove() {
  const id = Number(selected.value)
  if (!id || !window.confirm('确认删除此 Prompt？历史内容保留，后续生成不再使用该配置。')) return
  await action('save-prompt', async () => {
    await api.prompts.remove(id)
    selected.value = ''
    select()
    await load()
    emit('saved', null)
    notify('Prompt 已删除')
  })
}
async function load() {
  await action('load-prompts', async () => {
    const platform = props.platform
    const values = await allPages(api.prompts.list, { platform })
    if (platform === props.platform) prompts.value = values
  })
}
function select() {
  const value = prompts.value.find((p) => p.id === Number(selected.value))
  Object.assign(form, {
    name: value?.name || '',
    prompt_content: value?.prompt_content || '',
    is_active: value?.is_active ?? true,
  })
}
async function save() {
  await action('save-prompt', async () => {
    const data = { ...form, platform: props.platform }
    const value = selected.value
      ? await api.prompts.update(Number(selected.value), data)
      : await api.prompts.create(data)
    selected.value = value.id
    await load()
    emit('saved', value)
    notify(props.inline ? 'Prompt 模板已保存' : 'Prompt 已保存并应用到当前配置')
    emit('close')
  })
}
watch(
  () => props.platform,
  () => {
    selected.value = ''
    prompts.value = []
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
    title="Prompt 模板"
    :busy="busy.has('save-prompt')"
    @close="emit('close')"
  >
    <p class="muted">手工保存后应用到生成任务，运营经验不会覆盖这里的指令。</p>
    <label class="field">
      <span class="field-label">选择模板</span>
      <select v-model="selected" @change="select">
        <option value="">＋ 新建模板</option>
        <option v-for="prompt in prompts" :key="prompt.id" :value="prompt.id">
          {{ prompt.name }} · v{{ prompt.version }}{{ prompt.is_active ? '' : '（已停用）' }}
        </option>
      </select>
    </label>
    <form @submit.prevent="save">
      <label class="field">
        <span class="field-label">模板名称</span>
        <input v-model="form.name" maxlength="100" required />
      </label>
      <label class="field">
        <span class="field-label">Prompt 内容</span>
        <textarea
          v-model="form.prompt_content"
          rows="9"
          required
          placeholder="例如：面向通勤人群，语气自然，先写场景再介绍产品，避免虚构第一人称体验。"
        />
      </label>
      <label class="check-row">
        <input v-model="form.is_active" type="checkbox" />
        启用此模板
      </label>
      <div class="row section-gap">
        <button class="button primary" :disabled="busy.has('save-prompt')">
          {{ inline ? '保存模板' : '保存并应用' }}
        </button>
        <button
          v-if="selected"
          type="button"
          class="button danger"
          :disabled="busy.has('save-prompt')"
          @click="remove"
        >
          删除模板
        </button>
        <button
          v-if="!inline"
          class="button"
          type="button"
          :disabled="busy.has('save-prompt')"
          @click="emit('close')"
        >
          取消
        </button>
      </div>
    </form>
  </component>
</template>
