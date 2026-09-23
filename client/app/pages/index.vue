<template>
  <div class="mx-auto max-w-2xl p-6">
    <h1 class="text-2xl font-semibold leading-[1.2]">Surveys</h1>
    <p v-if="error" class="mt-4 text-gray-600 dark:text-gray-300">Could not load surveys.</p>
    <ul v-else class="mt-4 space-y-2">
      <li v-for="s in surveys" :key="s.slug">
        <NuxtLink
          :to="{ path: '/s', query: { slug: s.slug } }"
          class="block rounded border border-gray-200 dark:border-gray-700 p-4 hover:bg-gray-50 dark:hover:bg-blue-900">
          <span class="font-medium">{{ s.title }}</span>
          <span v-if="s.description" class="block text-sm text-gray-600 dark:text-gray-300">
            {{ s.description }}
          </span>
        </NuxtLink>
      </li>
      <li v-if="surveys && surveys.length === 0" class="text-gray-600 dark:text-gray-300">
        No open surveys right now.
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
const api = useSurveyApi()
const { data: surveys, error } = await useAsyncData('open-surveys', () => api.openSurveys())
</script>
