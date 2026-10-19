<script setup>
import { computed } from 'vue'
import { taskLabels, publishLabels, documentLabels, ruleLabels } from '../utils/domain.js'
const props = defineProps({ status: String, type: { type: String, default: 'task' } })
const text = computed(
  () =>
    ({ task: taskLabels, publish: publishLabels, document: documentLabels, rule: ruleLabels })[
      props.type
    ]?.[props.status] ||
    props.status ||
    '未知',
)
</script>
<template>
  <span class="badge" :class="status">{{ text }}</span>
</template>
