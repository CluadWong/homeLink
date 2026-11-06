<script setup lang="ts">
defineProps<{
  title: string
  note?: string
}>()
</script>

<template>
  <div class="empty-block">
    <span class="empty-mark">∅</span>
    <strong>{{ title }}</strong>
    <p v-if="note">{{ note }}</p>
    <slot />
  </div>
</template>
