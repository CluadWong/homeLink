<script setup lang="ts">
defineProps<{
  index: string
  title: string
  description?: string
}>()
</script>

<template>
  <header class="page-header">
    <span class="page-kicker">{{ index }} / {{ title }}</span>
    <h2 class="page-title">{{ title }}</h2>
    <p v-if="description" class="page-desc">{{ description }}</p>
    <slot />
  </header>
</template>
