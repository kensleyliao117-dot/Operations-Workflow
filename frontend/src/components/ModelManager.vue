<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import BaseDialog from './BaseDialog.vue'
import ResourceModelManager from './ResourceModelManager.vue'
import { api } from '../api/index.js'
import { analysisModels, loadModels } from '../stores/models.js'
import { action, busy, notify } from '../stores/ui.js'
const props = defineProps({ open: Boolean, inline: Boolean })
const emit = defineEmits(['close'])
const kind = ref('text')
watch(kind, () => {
  form.api_key = ''
})
const editing = ref(null),
  error = ref(''),
  deleting = ref(null)
const originalUrl = ref(null)
const form = reactive({
  name: '',
  provider: '',
  model: '',
  base_url: '',
  api_key: '',
  is_active: true,
  is_default: true,
})
const needsKey = computed(
  () =>
    !editing.value ||
    !originalUrl.value ||
    form.base_url.trim().replace(/\/+$/, '') !== originalUrl.value,
)
function edit(item = null) {
  editing.value = item?.config_id || null
  deleting.value = null
  error.value = ''
  originalUrl.value = item?.base_url || null
  Object.assign(form, {
    name: item?.name || '',
    provider: item?.provider || '',
    model: item?.model || '',
    is_active: item?.is_active ?? true,
    base_url: item?.base_url || '',
    api_key: '',
    is_default: item?.is_default ?? !analysisModels.value.some((model) => model.is_default),
  })
}
async function save() {
  error.value = ''
  const id = editing.value
  const payload = { ...form, api_key: form.api_key.trim() || null }
  await action('save-model', async () => {
    try {
      const value = id ? await api.models.update(id, payload) : await api.models.create(payload)
      form.api_key = ''
      await loadModels()
      edit(value)
      notify('模型配置已保存，API Key 已加密；历史内容保持原版本')
    } catch (e) {
      error.value = e.message
      throw e
    }
  })
}
async function testConnection(item) {
  error.value = ''
  await action('save-model', async () => {
    try {
      const result = await api.models.test(item.config_id)
      notify(`${item.name}：${result.message}`)
    } catch (e) {
      error.value = e.message
      throw e
    }
  })
}
async function remove() {
  const item = deleting.value
  if (!item) return
  await action('save-model', async () => {
    if (item.source === 'system') await api.models.removeSystem(item.id)
    else await api.models.remove(item.config_id)
    await loadModels()
    edit()
    notify('模型已从当前账号移除，历史文案和分析结果仍保留')
  })
}
async function restoreSystem() {
  await action('save-model', async () => {
    await api.models.restoreSystem()
    await loadModels()
    notify('已恢复当前账号的内置系统模型')
  })
}
watch(
  () => props.open,
  (open) => {
    if (open) {
      edit()
      action('load-models', loadModels)
    } else form.api_key = ''
  },
  { immediate: true },
)
onUnmounted(() => {
  form.api_key = ''
})
</script>
<template>
  <component
    :is="inline ? 'section' : BaseDialog"
    :class="{ 'settings-surface': inline }"
    :open="open"
    title="模型连接设置"
    :busy="busy.has('save-model') || busy.has('resource-model')"
    @close="emit('close')"
  >
    <nav class="section-tabs" aria-label="模型类型">
      <button
        v-for="value in [
          { id: 'text', name: '文本模型' },
          { id: 'embedding', name: 'Embedding' },
          { id: 'image', name: '生图模型' },
        ]"
        :key="value.id"
        class="button small"
        :class="{ selected: kind === value.id }"
        :disabled="busy.has('save-model') || busy.has('resource-model')"
        @click="kind = value.id"
      >
        {{ value.name }}
      </button>
    </nav>
    <ResourceModelManager v-if="kind !== 'text'" :key="kind" :kind="kind" />
    <button v-if="kind !== 'text' && !inline" class="button section-gap" @click="emit('close')">
      完成
    </button>
    <template v-if="kind === 'text'">
      <p class="muted">
        填写供应商名称、API URL、API Key 和模型 ID。配置仅属于当前账号，支持 Chat Completions
        兼容接口。模型支持删除，历史文案和分析记录保留。
      </p>
      <div class="model-list">
        <div v-for="item in analysisModels" :key="item.id" class="model-row">
          <div class="grow">
            <strong>{{ item.name || item.model }}</strong>
            <p class="muted">
              {{ item.provider }} · {{ item.model }} ·
              {{
                item.is_active === false ? '已停用' : item.ready ? '凭据已配置' : '缺少供应商凭据'
              }}
              {{ item.is_default ? ' · 默认模型' : '' }}
              {{ item.credential_source === 'system' ? ' · 使用系统凭据' : '' }}
            </p>
            <p v-if="item.base_url" class="muted wrap">{{ item.base_url }}</p>
          </div>
          <button
            v-if="item.source === 'user'"
            type="button"
            class="button small"
            :disabled="busy.has('save-model') || !item.ready"
            @click="testConnection(item)"
          >
            测试连接
          </button>
          <button
            v-if="item.source === 'user'"
            class="button small"
            :disabled="busy.has('save-model')"
            @click="edit(item)"
          >
            编辑模型
          </button>
          <button
            class="button ghost small danger"
            :disabled="busy.has('save-model')"
            @click="deleting = item"
          >
            删除
          </button>
          <span v-if="item.source === 'system'" class="chip">系统配置</span>
        </div>
      </div>
      <div v-if="deleting" class="notice section-gap" role="alert">
        确认删除“{{ deleting.name }}”的模型配置？已保存的文案和分析版本不会删除。
        <p v-if="deleting.source === 'system'" class="muted">
          系统模型仅从当前账号移除，不影响其他账号，可通过“恢复内置模型”重新添加。
        </p>
        <p v-else-if="deleting.is_default" class="muted">
          删除后请重新选择默认模型，后续生成不会使用已删除配置。
        </p>
        <div class="row section-gap">
          <button class="button danger" :disabled="busy.has('save-model')" @click="remove">
            确认删除模型
          </button>
          <button class="button" @click="deleting = null">取消</button>
        </div>
      </div>
      <p v-if="!analysisModels.length" class="muted section-gap">
        暂无模型，请添加模型连接或恢复内置模型后再生成内容。
      </p>
      <button
        class="button ghost small section-gap"
        :disabled="busy.has('save-model')"
        @click="restoreSystem"
      >
        恢复内置模型
      </button>
      <hr class="section-divider" />
      <div class="row between">
        <h3>{{ editing ? '编辑模型' : '新增模型' }}</h3>
        <button v-if="editing" class="button small" @click="edit()">＋ 新增模型</button>
      </div>
      <form @submit.prevent="save">
        <label class="field">
          <span class="field-label">显示名称</span>
          <input
            v-model="form.name"
            required
            maxlength="100"
            placeholder="例如：日常文案模型"
            :disabled="busy.has('save-model')"
          />
        </label>
        <label class="field">
          <span class="field-label">模型供应商</span>
          <input
            v-model="form.provider"
            aria-label="模型供应商"
            required
            maxlength="100"
            placeholder="填写供应商名称，可自定义"
            :disabled="busy.has('save-model')"
          />
        </label>
        <label class="field">
          <span class="field-label">API URL</span>
          <input
            v-model="form.base_url"
            aria-label="API URL"
            type="url"
            required
            maxlength="2048"
            placeholder="https://api.example.com/v1"
            :disabled="busy.has('save-model')"
          />
          <small class="muted">
            填写供应商的公网 HTTPS 接口根地址；系统自动追加 /chat/completions。
          </small>
        </label>
        <label class="field">
          <span class="field-label">API Key</span>
          <input
            v-model="form.api_key"
            aria-label="API Key"
            type="password"
            autocomplete="new-password"
            :required="needsKey"
            maxlength="4096"
            :disabled="busy.has('save-model')"
            :placeholder="needsKey ? '填写供应商提供的 API Key' : '已加密保存；留空保留原密钥'"
          />
          <small class="muted">API Key 是接口密钥。编辑时不回显；更换 API URL 后需重新填写。</small>
        </label>
        <label class="field">
          <span class="field-label">模型 ID</span>
          <input
            v-model="form.model"
            aria-label="模型 ID"
            required
            maxlength="100"
            placeholder="填写供应商提供的聊天模型 ID"
            :disabled="busy.has('save-model')"
          />
          <small class="muted">
            模型 ID 必须由供应商支持；Embedding 和生图模型不能用于文本分析。
          </small>
        </label>
        <p class="notice info">
          保存配置不调用模型；“测试连接”会向所填接口发送一条简短请求，可能产生少量费用。
        </p>
        <label class="check-row">
          <input
            v-model="form.is_active"
            type="checkbox"
            :disabled="busy.has('save-model')"
            @change="!form.is_active && (form.is_default = false)"
          />
          启用此模型
        </label>
        <label class="check-row">
          <input
            v-model="form.is_default"
            type="checkbox"
            :disabled="!form.is_active || busy.has('save-model')"
          />
          设为默认文本模型
        </label>
        <p class="muted">
          默认模型用于案例分析、文案生成、规则辅助检查、图片提示词和运营经验提炼。Embedding
          和实际生图接口可在上方对应分类中配置。
        </p>
        <p v-if="error" class="error-text" role="alert">{{ error }}</p>
        <div class="row section-gap">
          <button class="button primary" :disabled="busy.has('save-model')">保存模型</button>
          <button
            v-if="!inline"
            class="button"
            type="button"
            :disabled="busy.has('save-model')"
            @click="emit('close')"
          >
            完成
          </button>
        </div>
      </form>
    </template>
  </component>
</template>
