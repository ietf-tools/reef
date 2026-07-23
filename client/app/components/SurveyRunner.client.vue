<script setup lang="ts">
import { Model } from "survey-core";
import "survey-core/survey-core.css";
import { SurveyComponent } from "survey-vue3-ui";

const props = defineProps<{
  definition: Record<string, unknown>;
  theme?: Record<string, unknown> | null;
}>();

const emit = defineEmits<{ complete: [data: Record<string, unknown>] }>();

const model = new Model(props.definition);
if (props.theme) {
  model.applyTheme(props.theme as never);
}
model.onComplete.add((sender) => {
  emit("complete", sender.data as Record<string, unknown>);
});
</script>

<template>
  <SurveyComponent :model="model" />
</template>
