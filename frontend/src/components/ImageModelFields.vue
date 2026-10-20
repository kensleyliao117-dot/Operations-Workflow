<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/index.js'
import { action } from '../stores/ui.js'
import { imageRatioOptions, normalizeImageSize } from '../utils/image_sizes.js'
const props = defineProps({ modelValue: [String, Number], size: String, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'update:size'])
const models = ref([])
const selected = computed(() =>
  props.modelValue
    ? models.value.find((row) => row.id === Number(props.modelValue))
    : models.value.find((row) => row.is_default),
)
const ratios = computed(() => imageRatioOptions(selected.value))
const available = computed(() => ratios.value.some((option) => !option.disabled))
const dimensions = computed(() => (available.value ? props.size?.replace('*', ' × ') : ''))
watch(
  [ratios, () => props.size],
  ([values, size]) => {
    const normalized = normalizeImageSize(size, values)
    if (normalized !== size) emit('update:size', normalized)
  },
  { immediate: true },
)
onMounted(() =>
  action('image-models-load', async () => {
    models.value = (await api.resourceModels.list('image')).filter((row) => row.is_active)
    if (props.modelValue && !models.value.some((row) => row.id === Number(props.modelValue)))
      emit('update:modelValue', null)
  }),
)
</script>
<template>
  <label class="field">
    <span class="field-label">生图模型</span>
    <select
      :value="modelValue || ''"
      :disabled="disabled"
      aria-label="生图模型"
      @change="emit('update:modelValue', $event.target.value ? Number($event.target.value) : null)"
    >
      <option value="">使用账号默认生图模型</option>
      <option v-for="row in models" :key="row.id" :value="row.id">
        {{ row.name }} · {{ row.provider }}{{ row.is_default ? ' · 默认' : '' }}
      </option>
    </select>
  </label>
  <label class="field">
    <span class="field-label">图片比例</span>
    <select
      :value="size"
      :disabled="disabled || !available"
      aria-label="图片比例"
      translate="no"
      class="notranslate"
      required
      @change="emit('update:size', $event.target.value)"
    >
      <option
        v-for="option in ratios"
        :key="option.ratio"
        :value="option.size"
        :disabled="option.disabled"
        translate="no"
      >
        {{ option.ratio }}
      </option>
    </select>
    <small v-if="dimensions" class="muted notranslate" translate="no">{{ dimensions }} px</small>
    <small v-if="selected && !available" class="muted">
      所选模型未配置这些比例，请在模型连接中补充支持的尺寸或更换模型。
    </small>
  </label>
</template>
