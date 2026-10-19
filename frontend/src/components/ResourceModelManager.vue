<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api/index.js'
import { action, busy, notify } from '../stores/ui.js'
const props = defineProps({ kind: { type: String, required: true } })
const rows = ref([]),
  editing = ref(null),
  deleting = ref(null),
  error = ref('')
const label = computed(() => (props.kind === 'embedding' ? 'Embedding' : '生图'))
const form = reactive({
  name: '',
  provider: '',
  model: '',
  base_url: '',
  api_key: '',
  protocol: 'compatible',
  dimensions: 1024,
  send_dimensions: true,
  sizes: '1024*1024',
  is_active: true,
  is_default: true,
})
let originalUrl = ''
const needsKey = computed(
  () => !editing.value || form.base_url.trim().replace(/\/+$/, '') !== originalUrl,
)
async function load() {
  rows.value = await api.resourceModels.list(props.kind)
}
function edit(row = null) {
  editing.value = row?.id || null
  originalUrl = row?.base_url || ''
  error.value = ''
  Object.assign(form, {
    name: row?.name || '',
    provider: row?.provider || '',
    model: row?.model || '',
    base_url: row?.base_url || '',
    api_key: '',
    protocol: row?.protocol || 'compatible',
    dimensions: row?.dimensions || 1024,
    send_dimensions: row?.send_dimensions ?? true,
    sizes: row?.image_sizes?.join(', ') || '1024*1024',
    is_active: row?.is_active ?? true,
    is_default: row?.is_default ?? !rows.value.some((value) => value.is_default),
  })
}
function payload() {
  return {
    kind: props.kind,
    name: form.name,
    provider: form.provider,
    model: form.model,
    base_url: form.base_url,
    api_key: form.api_key.trim() || null,
    protocol: form.protocol,
    dimensions: props.kind === 'embedding' ? Number(form.dimensions) : null,
    send_dimensions: form.send_dimensions,
    image_sizes:
      props.kind === 'image'
        ? [
            ...new Set(
              form.sizes
                .split(/[,，\s]+/)
                .filter(Boolean)
                .map((size) => size.replace(/[x×]/g, '*')),
            ),
          ]
        : [],
    is_active: form.is_active,
    is_default: form.is_default,
  }
}
async function save() {
  error.value = ''
  await action('resource-model', async () => {
    try {
      const value = editing.value
        ? await api.resourceModels.update(editing.value, payload())
        : await api.resourceModels.create(payload())
      form.api_key = ''
      await load()
      edit(value)
      notify(`${label.value} 连接已保存，密钥已加密`)
    } catch (e) {
      error.value = e.message
      throw e
    }
  })
}
async function remove() {
  const row = deleting.value
  if (!row) return
  await action('resource-model', async () => {
    await api.resourceModels.remove(row.id)
    deleting.value = null
    await load()
    edit()
    notify('模型连接已删除，已保存的资料、向量和图片仍保留')
  })
}
onMounted(() =>
  action('resource-model-load', async () => {
    await load()
    edit()
  }),
)
onUnmounted(() => {
  form.api_key = ''
})
</script>
<template>
  <p class="muted">配置仅属于当前账号。未设置此类默认模型时，沿用部署时的系统配置。</p>
  <p v-if="kind === 'embedding'" class="notice section-gap">
    更换接口地址、模型或维度后，需要在产品资料页面重新执行“入库”。旧向量保留，不会与新模型混用。更换密钥不需要重新入库。
  </p>
  <div class="model-list">
    <div v-for="row in rows" :key="row.id" class="model-row">
      <div class="grow">
        <strong>{{ row.name }}</strong>
        <p class="muted">
          {{ row.provider }} · {{ row.model }}{{ row.is_default ? ' · 默认' : ''
          }}{{ !row.is_active ? ' · 已停用' : '' }}
        </p>
        <p class="muted wrap">
          {{ row.base_url }}{{ row.dimensions ? ` · ${row.dimensions} 维` : '' }}
        </p>
      </div>
      <button
        v-if="kind === 'embedding'"
        class="button small"
        :disabled="busy.has('resource-model') || !row.ready"
        @click="
          action('resource-model', async () => {
            const result = await api.resourceModels.test(row.id)
            notify(result.message)
          })
        "
      >
        测试连接
      </button>
      <button class="button small" :disabled="busy.has('resource-model')" @click="edit(row)">
        编辑模型
      </button>
      <button
        class="button ghost small danger"
        :disabled="busy.has('resource-model')"
        @click="deleting = row"
      >
        删除
      </button>
    </div>
  </div>
  <p v-if="!rows.length" class="muted section-gap">暂无自定义{{ label }}连接。</p>
  <div v-if="deleting" class="notice section-gap" role="alert">
    确认删除“{{
      deleting.name
    }}”？已保存的资料和图片不会删除。删除默认连接后，后续请求沿用系统配置，或需重新添加默认连接。
    <div class="row section-gap">
      <button class="button danger" :disabled="busy.has('resource-model')" @click="remove">
        确认删除模型
      </button>
      <button class="button" @click="deleting = null">取消</button>
    </div>
  </div>
  <hr class="section-divider" />
  <div class="row between">
    <h3>{{ editing ? '编辑' : '新增' }}{{ label }}模型</h3>
    <button v-if="editing" class="button small" @click="edit()">＋ 新增模型</button>
  </div>
  <form @submit.prevent="save">
    <label class="field">
      <span class="field-label">显示名称</span>
      <input v-model="form.name" required maxlength="100" :disabled="busy.has('resource-model')" />
    </label>
    <label class="field">
      <span class="field-label">模型供应商</span>
      <input
        v-model="form.provider"
        required
        maxlength="100"
        placeholder="可自定义供应商名称"
        :disabled="busy.has('resource-model')"
      />
    </label>
    <label v-if="kind === 'image'" class="field">
      <span class="field-label">接口协议</span>
      <select
        v-model="form.protocol"
        @change="
          form.sizes =
            form.protocol === 'qwen_image' ? '1728*2368, 2048*2048, 1536*2688' : '1024*1024'
        "
      >
        <option value="compatible">Images 兼容接口</option>
        <option value="qwen_image">千问同步生图接口</option>
      </select>
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
        :disabled="busy.has('resource-model')"
      />
      <small class="muted">
        {{
          kind === 'embedding'
            ? '填写根地址，自动追加 /embeddings。'
            : form.protocol === 'qwen_image'
              ? '填写百炼 HTTPS 根域名，不含 /api/v1/... 调用路径。'
              : '填写根地址，自动追加 /images/generations。支持同步返回 PNG 图片 URL 或 Base64。'
        }}
      </small>
    </label>
    <label class="field">
      <span class="field-label">API Key</span>
      <input
        v-model="form.api_key"
        type="password"
        autocomplete="new-password"
        :required="needsKey"
        maxlength="4096"
        :placeholder="needsKey ? '填写接口密钥' : '留空保留已加密的密钥'"
        :disabled="busy.has('resource-model')"
      />
    </label>
    <label class="field">
      <span class="field-label">模型 ID</span>
      <input
        v-model="form.model"
        required
        maxlength="100"
        placeholder="供应商提供的模型 ID"
        :disabled="busy.has('resource-model')"
      />
    </label>
    <template v-if="kind === 'embedding'">
      <label class="field">
        <span class="field-label">向量维度</span>
        <input
          v-model="form.dimensions"
          aria-label="向量维度"
          type="number"
          required
          min="1"
          max="4096"
        />
        <small class="muted">填写模型实际输出的维度，当前支持 1–4096 维。</small>
      </label>
      <label class="check-row">
        <input v-model="form.send_dimensions" type="checkbox" />
        请求中发送 dimensions 参数
      </label>
      <p class="muted">接口不接受自定义维度时取消勾选，并填写其固定输出维度。</p>
    </template>
    <label v-else class="field">
      <span class="field-label">支持的图片尺寸</span>
      <input
        v-model="form.sizes"
        aria-label="支持的图片尺寸"
        required
        placeholder="1024*1024, 1024*1536"
      />
      <small class="muted">
        用逗号分隔。可选
        1024*1024、1024*1536、1536*1024、1024*1792、1792*1024，以及现有千问尺寸；请以供应商支持范围为准。
      </small>
    </label>
    <label class="check-row">
      <input
        v-model="form.is_active"
        type="checkbox"
        @change="!form.is_active && (form.is_default = false)"
      />
      启用此模型
    </label>
    <label class="check-row">
      <input v-model="form.is_default" type="checkbox" :disabled="!form.is_active" />
      设为默认{{ label }}模型
    </label>
    <p class="muted">
      {{
        kind === 'embedding'
          ? '保存不调用接口；测试连接会发送一条简短文本，可能产生少量费用。'
          : '保存不调用生图接口；生成图片时按本连接调用，提示词仍由文本模型生成。'
      }}
    </p>
    <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    <button class="button primary" :disabled="busy.has('resource-model')">保存模型</button>
  </form>
</template>
