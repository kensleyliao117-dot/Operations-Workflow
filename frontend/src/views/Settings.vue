<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { selectedPlatform, currentPlatform } from '../stores/platforms.js'
import ModelManager from '../components/ModelManager.vue'
import PromptManager from '../components/PromptManager.vue'
import RuleManager from '../components/RuleManager.vue'
const route = useRoute()
const sections = [
  {
    id: 'models',
    name: '模型连接',
    description: '管理独立的供应商接口和密钥，为当前账号选择默认文本模型。',
  },
  { id: 'prompts', name: 'Prompt 模板', description: '为不同平台保存常用的内容风格与表达要求。' },
  { id: 'rules', name: '平台规则', description: '维护当前平台的检查规则，辅助人工判断风险。' },
]
const active = computed(() => sections.find((s) => s.id === route.query.section) || sections[0])
</script>
<template>
  <header class="page-heading">
    <div>
      <p class="eyebrow">PREFERENCES</p>
      <h1>设置</h1>
      <p>把常用偏好配置好，让每次创作更专注。</p>
    </div>
  </header>
  <nav class="section-tabs" aria-label="设置分类">
    <RouterLink
      v-for="section in sections"
      :key="section.id"
      :to="{ path: '/settings', query: { section: section.id } }"
      :class="{ selected: active.id === section.id }"
    >
      {{ section.name }}
    </RouterLink>
  </nav>
  <section class="settings-content">
    <header class="section-heading">
      <p class="eyebrow">{{ active.id === 'models' ? '账号设置' : currentPlatform.name }}</p>
      <h2>{{ active.name }}</h2>
      <p class="muted">{{ active.description }}</p>
    </header>
    <ModelManager v-if="active.id === 'models'" :open="true" inline />
    <PromptManager
      v-else-if="active.id === 'prompts'"
      :key="selectedPlatform"
      :open="true"
      inline
      :platform="selectedPlatform"
    />
    <RuleManager v-else :key="selectedPlatform" :open="true" inline :platform="selectedPlatform" />
  </section>
</template>
