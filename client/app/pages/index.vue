<template>
  <div class="mx-auto max-w-2xl p-6">
    <h1 class="text-2xl font-semibold leading-[1.2]">Surveys</h1>
    <p v-if="error" class="mt-4 text-gray-600 dark:text-gray-300">Could not load surveys.</p>
    <ul v-else class="mt-4 space-y-2">
      <li v-for="s in surveys" :key="s.slug">
        <NuxtLink
          :to="{ path: '/s', query: { slug: s.slug } }"
          class="flex lg:flex-row justify-between rounded border border-gray-500 dark:border-gray-700 p-4 hover:bg-gray-50 dark:hover:bg-blue-900 no-underline">
          <span class="font-medium underline">{{ s.title }}</span>
          <span class="flex flex-row items-center gap-2 mt-1 text-sm ml-2">
            <span
              v-if="s.answered"
              class="flex flex-row items-center bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-100 text-sm px-2 py-1 rounded-xl">
              <GraphicsCheckmark class="mr-1" />you've already responded
            </span>
            <span class="bg-gray-300 text-black text-sm px-2 py-1 rounded-xl">
              <span v-if="s.visibility === 'authenticated'" class="flex flex-row items-center">
                <GraphicsBustInSilhouette class="mr-1" />account required survey
              </span>
              <span v-else>
                public survey
              </span>
            </span>
          </span>
         </NuxtLink>
         <span v-if="s.description" class="block text-sm text-gray-600 dark:text-gray-300">
           {{ s.description }}
         </span>
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
