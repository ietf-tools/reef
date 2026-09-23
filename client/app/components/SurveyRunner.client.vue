<template>
  <SurveyComponent :model="model" />
  <div v-if="retryable" class="mt-4 flex justify-center">
    <button
      type="button"
      class="rounded bg-blue-900 dark:bg-blue-950 px-4 py-2 font-medium text-white hover:bg-blue-800 disabled:opacity-60"
      :disabled="saving"
      @click="attemptSave">
      Try again
    </button>
  </div>
</template>

<script setup lang="ts">
import { Model, type CompleteEvent } from 'survey-core'
import * as surveyThemes from 'survey-core/themes'
import 'survey-core/survey-core.css'
import { SurveyComponent } from 'survey-vue3-ui'

type SurveyData = Record<string, unknown>
type SaveOptions = Pick<CompleteEvent, 'showSaveInProgress' | 'showSaveError' | 'showSaveSuccess'>

const props = defineProps<{
  definition: Record<string, unknown>
  theme?: Record<string, unknown> | null
  // Rejects with an Error whose message is shown to the visitor.
  save: (data: SurveyData) => Promise<void>
}>()

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

// Held once the survey completes, because by then SurveyJS has left the last page
// and a retry has nothing else to resubmit.
let completed: { data: SurveyData; options: SaveOptions } | null = null
const saving = ref(false)
const retryable = ref(false)

const attemptSave = async () => {
  if (!completed || saving.value) {
    return
  }
  const { data, options } = completed
  saving.value = true
  retryable.value = false
  options.showSaveInProgress()
  try {
    await props.save(data)
    options.showSaveSuccess()
  } catch (error) {
    options.showSaveError(error instanceof Error ? error.message : "Your response couldn't be saved. Please try again.")
    retryable.value = true
  } finally {
    saving.value = false
  }
}

// onComplete rather than onCompleting, because only here has SurveyJS already
// cleared the answers to questions its conditions hid, which is the data a response
// stores. attemptSave() reports saving before its first await: SurveyJS follows a
// survey's navigateToUrl straight after this handler unless saving has started,
// which would leave the page before the response reached the server.
model.onComplete.add((sender, options) => {
  completed = { data: sender.data as SurveyData, options }
  void attemptSave()
})
</script>
