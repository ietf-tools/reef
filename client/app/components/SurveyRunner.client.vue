<template>
  <SurveyComponent :model="model" />
</template>

<script setup lang="ts">
import { Model } from 'survey-core'
import * as surveyThemes from 'survey-core/themes'
import 'survey-core/survey-core.css'
import { SurveyComponent } from 'survey-vue3-ui'

const props = defineProps<{
  definition: Record<string, unknown>
  theme?: Record<string, unknown> | null
}>()

const emit = defineEmits<{ complete: [data: Record<string, unknown>] }>()

const config = useRuntimeConfig()
const colorMode = useColorMode()
const model = new Model(props.definition)

// A survey's own theme (from the SurveyJS Theme Editor) only carries the colors a
// designer picked, not a light/dark pair, so we still need a base that follows
// Reef's own colour mode for the chrome the custom theme doesn't touch — otherwise
// a survey with no theme (or one built before dark mode) renders with SurveyJS's
// light-only defaults regardless of the page around it.
function themeForMode(mode: string): Record<string, unknown> {
  const themeMap = surveyThemes as unknown as Record<string, Record<string, unknown>>
  const name = `${config.public.surveyThemeFamily}${mode === 'dark' ? 'Dark' : 'Light'}`
  const base = themeMap[name] ?? (mode === 'dark' ? surveyThemes.DefaultDark : surveyThemes.DefaultLight)
  // Same navy the header/footer bars use (bg-blue-900 dark:bg-blue-950), referenced
  // via the Tailwind CSS variable rather than a copied hex so the two stay in sync.
  const primary = `var(--color-blue-${mode === 'dark' ? '950' : '900'})`
  return {
    ...base,
    cssVariables: {
      ...(base.cssVariables as Record<string, string>),
      '--sjs-primary-backcolor': primary,
      '--sjs-primary-backcolor-light': `color-mix(in srgb, ${primary} 10%, transparent)`,
      '--sjs-primary-backcolor-dark': `color-mix(in srgb, ${primary} 85%, black)`,
      '--sjs-primary-forecolor': 'var(--color-white)',
      '--sjs-primary-forecolor-light': 'color-mix(in srgb, var(--color-white) 25%, transparent)'
    }
  }
}

function applyTheme(mode: string) {
  const base = themeForMode(mode)
  model.applyTheme({
    ...base,
    ...props.theme,
    cssVariables: {
      ...(base.cssVariables as Record<string, string>),
      ...(props.theme?.cssVariables as Record<string, string> | undefined)
    }
  } as never)
}

applyTheme(colorMode.value)
watch(
  () => colorMode.value,
  (mode) => applyTheme(mode)
)

model.onComplete.add((sender) => {
  emit('complete', sender.data as Record<string, unknown>)
})
</script>
