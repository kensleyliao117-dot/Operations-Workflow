<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/index.js'
import { action } from '../stores/ui.js'
const props = defineProps({ modelValue: [String, Number], size: String, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'update:size'])
const models = ref([])
const selected = computed(() =>
  props.modelValue
    ? models.value.find((row) => row.id === Number(props.modelValue))
    : models.value.find((row) => row.is_default),
)
const sizes = computed(() => selected.value?.image_sizes || ['1728*2368', '2048*2048', '1536*2688'])
watch(sizes, (values) => {
  if (values.length && !values.includes(props.size)) emit('update:size', values[0])
})
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
    <span class="field-label">图片尺寸</span>
    <select
      :value="size"
      :disabled="disabled"
      aria-label="图片尺寸"
      @change="emit('update:size', $event.target.value)"
    >
      <option v-for="value in sizes" :key="value" :value="value">
        {{ value.replace('*', ' × ') }}
      </option>
    </select>
  </label>
</template>
