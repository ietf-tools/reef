<template>
  <div class="mx-auto max-w-3xl p-6">
    <div v-if="submitted" class="rounded font-bold bg-green-50 dark:bg-green-950 p-6 text-green-800 dark:text-green-100">
      Thank you. Your response has been recorded.
    </div>
    <div v-else-if="pending" class="rounded bg-gray-50 dark:bg-blue-900 p-6">
      <p v-if="resumeError" class="font-bold text-red-800 dark:text-red-200">{{ resumeError }}</p>
      <p v-else class="text-gray-700 dark:text-gray-200">Submitting the answers you gave before signing in…</p>
      <button
        v-if="resumeError"
        type="button"
        class="mt-4 rounded bg-blue-900 dark:bg-blue-950 px-4 py-2 font-medium text-white hover:bg-blue-800 disabled:opacity-60"
        :disabled="resuming"
        @click="resume">
        Try again
      </button>
    </div>
    <SurveyRunner v-else-if="definition" :definition="definition" :theme="theme" :save="save" />
    <div v-else class="text-gray-600 dark:text-gray-300">{{ unavailableMessage }}</div>
  </div>
</template>

<script setup lang="ts">
import { statusOf } from '~/utilities/fetch-error'

// Remount when the slug changes. SurveyJS builds its model once from the
// definition, so reusing this component across surveys would keep the old one.
definePageMeta({ key: (route) => route.fullPath })

const route = useRoute()
const slug = Array.isArray(route.query.slug) ? route.query.slug[0] : route.query.slug
const api = useSurveyApi()
const oidc = useOidc()
const { submitted, pending, resuming, resumeError, save, resume } = useSurveySubmission(slug ?? '')

const { data: survey, error } = await useAsyncData(`survey-${slug}`, () =>
  slug ? api.getDefinition(slug) : Promise.resolve(null)
)

// The OpenAPI schema types both of these as free-form JSON, which is all the
// server promises; SurveyJS is the thing that validates their shape.
const definition = computed(() => (survey.value?.definition ?? null) as Record<string, unknown> | null)
const theme = computed(() => (survey.value?.theme ?? null) as Record<string, unknown> | null)

// 401 is a token the API refused rather than a missing one, so signing in again
// would only come back to the same answer.
const unavailableMessage = computed(() => {
  const status = statusOf(error.value)
  if (status === 401) {
    return "Your sign-in wasn't accepted, so this survey couldn't be loaded. Try signing out and back in."
  }
  if (!error.value || status === 404) {
    return 'This survey is not available.'
  }
  return "This survey couldn't be loaded. Please try again shortly."
})

// The definition endpoint answers 403 for an authenticated-visibility survey.
if (statusOf(error.value) === 403) {
  await oidc.login(route.fullPath)
}

if (pending.value) {
  void resume()
}
</script>
