<script setup>
defineProps({ name: String, size: { type: Number, default: 20 } })
const paths = {
  feedback: ['M4 3v17h17', 'M8 15l4-5 4 2 5-7'],
  products: ['M4 7h16v14H4z', 'M3 3h18v4H3z', 'M10 11h4'],
  cases: ['M4 4h16v16H4z', 'M8 8h8M8 12h8M8 16h5'],
  create: ['M12 20H4v-8L15 1l8 8-11 11z', 'M12 4l8 8M4 20l5-1-4-4-1 5z'],
  review: ['M9 3H5v18h14V3h-4', 'M9 1h6v4H9z', 'M8 13l3 3 5-6'],
  settings: ['M4 7h16M4 17h16', 'M8 4v6M16 14v6'],
  sidebar: ['M3 4h18v16H3z', 'M9 4v16'],
  plus: ['M12 5v14M5 12h14'],
  arrow: ['M5 12h14M13 6l6 6-6 6'],
}
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(path, index) in paths[name] || paths.cases" :key="index" :d="path" />
  </svg>
</template>
