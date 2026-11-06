<script setup lang="ts">
defineProps<{ text?: string }>()
</script>

<template>
  <div class="loading-block" role="status">
    <span class="loading-bars"><i></i><i></i><i></i></span>
    <span>{{ text || '正在读取' }}</span>
  </div>
</template>
