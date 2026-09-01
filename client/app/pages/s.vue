<script setup lang="ts">
import type { FetchError } from "ofetch";

// Remount when the slug changes. SurveyJS builds its model once from the
// definition, so reusing this component across surveys would keep the old one.
definePageMeta({ key: (route) => route.fullPath });

const route = useRoute();
const slug = Array.isArray(route.query.slug)
  ? route.query.slug[0]
  : route.query.slug;
const api = useSurveyApi();
const oidc = useOidc();

const submitted = ref(false);

const { data: survey, error } = await useAsyncData(`survey-${slug}`, () =>
  slug ? api.getDefinition(slug) : Promise.resolve(null),
);

// The OpenAPI schema types both of these as free-form JSON, which is all the
// server promises; SurveyJS is the thing that validates their shape.
const definition = computed(
  () => (survey.value?.definition ?? null) as Record<string, unknown> | null,
);
const theme = computed(
  () => (survey.value?.theme ?? null) as Record<string, unknown> | null,
);

// A 403 means the survey requires authentication; send the visitor to log in.
if (error.value && (error.value as FetchError).statusCode === 403) {
  await oidc.login(route.fullPath);
}

async function onComplete(data: Record<string, unknown>) {
  await api.submitResponse(slug as string, data);
  submitted.value = true;
}
</script>

<template>
  <main class="mx-auto max-w-3xl p-6">
    <div v-if="submitted" class="rounded bg-green-50 p-6 text-green-800">
      Thank you. Your response has been recorded.
    </div>
    <SurveyRunner
      v-else-if="definition"
      :definition="definition"
      :theme="theme"
      @complete="onComplete"
    />
    <div v-else class="text-gray-600">This survey is not available.</div>
  </main>
</template>
