<script setup lang="ts">
import type { FetchError } from "ofetch";

const route = useRoute();
const slug = route.params.slug as string;
const api = useSurveyApi();
const oidc = useOidc();

const submitted = ref(false);

// Fetch client-side only: the definition may require the user's bearer token
// (held in the browser), and the SurveyJS widget renders client-side regardless.
const { data: survey, error } = await useAsyncData(
  `survey-${slug}`,
  () => api.getDefinition(slug),
  { server: false },
);

// A 403 means the survey requires authentication; send the visitor to log in.
if (error.value && (error.value as FetchError).statusCode === 403) {
  await oidc.login(route.fullPath);
}

async function onComplete(data: Record<string, unknown>) {
  await api.submitResponse(slug, data);
  submitted.value = true;
}
</script>

<template>
  <main class="mx-auto max-w-3xl p-6">
    <div v-if="error" class="text-gray-600">This survey is not available.</div>
    <div v-else-if="submitted" class="rounded bg-green-50 p-6 text-green-800">
      Thank you. Your response has been recorded.
    </div>
    <SurveyRunner
      v-else-if="survey"
      :definition="survey.definition"
      :theme="survey.theme"
      @complete="onComplete"
    />
  </main>
</template>
