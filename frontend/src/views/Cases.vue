<script setup>
import { currentPlatform, selectedPlatform } from '../stores/platforms.js'
import { platformAdapters } from '../config/platform_adapters.js'
</script>
<template>
  <header class="page-heading">
    <div>
      <p class="eyebrow">REFERENCE LIBRARY</p>
      <h1>案例库</h1>
      <p>收集值得研究的表达，把观察积累成创作参考。</p>
    </div>
    <RouterLink class="button" to="/generate">前往内容创作 →</RouterLink>
  </header>
  <component
    :is="platformAdapters[selectedPlatform].cases"
    v-if="currentPlatform.collection && platformAdapters[selectedPlatform]?.cases"
    :key="selectedPlatform"
    :selectable="false"
  />
  <section v-else class="panel empty-page">
    <h2>{{ currentPlatform.name }} 案例库待接入</h2>
    <p class="muted">切换到已接入的平台后，可以采集案例并查看分析版本。</p>
  </section>
</template>
