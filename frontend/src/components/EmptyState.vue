<script setup>
import { ui } from '../config/ui.js'
defineProps({
  title: { type: String, default: '暂无内容' },
  description: String,
  image: { type: String, default: 'content' },
})
</script>
<template>
  <div class="empty-state">
    <img v-if="ui.assets[image]" :src="ui.assets[image]" alt="" />
    <h3>{{ title }}</h3>
    <p>{{ description }}</p>
    <slot />
  </div>
</template>
