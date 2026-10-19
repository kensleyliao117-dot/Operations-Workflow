<script setup>
import StatusBadge from './StatusBadge.vue'
import { workflowLabels } from '../utils/domain.js'
defineProps({ task: Object, workflow: Object, loading: Boolean, error: String })
defineEmits(['refresh'])
</script>
<template>
  <section class="panel">
    <div class="panel-header">
      <h3>工作流状态</h3>
      <button class="button ghost small" :disabled="loading" @click="$emit('refresh')">刷新</button>
    </div>
    <div v-if="task" class="stack">
      <StatusBadge :status="task.status" />
      <p class="stage-text">
        {{
          workflow
            ? workflowLabels[workflow.stage] || workflowLabels[workflow.status] || workflow.stage
            : '工作流尚未启动'
        }}
      </p>
      <p v-if="['running', 'generating_images'].includes(workflow?.status)" class="muted">
        <span class="spinner"></span>
        后端正在处理，可稍后刷新查看
      </p>
      <p v-if="task.error_message || workflow?.error_message" class="error-text">
        {{ task.error_message || workflow?.error_message }}
      </p>
      <small class="muted">
        任务 #{{ task.id }}
        <template v-if="workflow?.content_version">· 内容 v{{ workflow.content_version }}</template>
      </small>
    </div>
    <p v-else class="muted">创建或选择任务后显示实际状态。</p>
    <p v-if="error" class="error-text" role="alert">{{ error }}</p>
  </section>
</template>
