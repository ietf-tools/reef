<template>
  <div class="mx-auto max-w-3xl p-6">
    <div class="rounded font-bold p-6 text-black dark:text-white">
      <p>Welcome to surveys, nothing here.</p>
      <p class="mt-6"><a :href="rfceditorBase" class="text-blue-200 dark:text-white underline">Go to RFC-Editor.org</a></p>
    </div>
  </div>
</template>

<script setup lang="ts">
const {
  public: { rfceditorBase }
} = useRuntimeConfig()
</script>
