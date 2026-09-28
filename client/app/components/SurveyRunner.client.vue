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

// The page around the runner shows its own thank-you once save() resolves, so
// the completed page must not say anything of its own meanwhile; unset, it falls
// back to SurveyJS's "Thank you for completing the survey". It stays shown, blank,
// because the saving and save-error notifications render inside it.
model.completedHtml = ''

// A survey's own theme (from the SurveyJS Theme Editor) only carries the colors a
// designer picked, not a light/dark pair, so we still need a base that follows
// Reef's own colour mode for the chrome the custom theme doesn't touch — otherwise
// a survey with no theme (or one built before dark mode) renders with SurveyJS's
// light-only defaults regardless of the page around it.
function themeForMode(mode: string): Record<string, unknown> {
  const themeMap = surveyThemes as unknown as Record<string, Record<string, unknown>>
  const name = `${config.public.surveyThemeFamily}${mode === 'dark' ? 'Dark' : 'Light'}`
  const base = themeMap[name] ?? (mode === 'dark' ? surveyThemes.DefaultDark : surveyThemes.DefaultLight)
  // Light mode uses the same navy as the header/footer bars (bg-blue-900). Dark
  // mode cannot: SurveyJS paints checked boxes, selected ratings, the progress bar,
  // focus borders and the Next button with the primary colour, and the navy is
  // nearly the same darkness as its dark panels (about 1.5:1). A light accent with
  // dark text on it keeps those controls visible. Both are referenced via the
  // Tailwind CSS variables rather than copied hexes so the palette stays in sync.
  const dark = mode === 'dark'
  const primary = `var(--color-blue-${dark ? '100' : '900'})`
  const primaryText = dark ? 'var(--color-blue-975)' : 'var(--color-white)'
  // Both SurveyJS Default themes set secondary text at under half opacity and
  // borders at under a fifth, short of WCAG AA (4.5:1 for text, 3:1 for control
  // outlines) on their own panels; these opacities clear it (about 5.7:1 and
  // 3.4:1). Text inputs, checkbox and radio decorators, the boolean toggle,
  // buttons and rating items have no CSS border at all: their outline is the
  // shadow, which both themes keep as a faint drop shadow. A 1px ring stands in
  // for it. The *-reset variants stay as SurveyJS defines them, since they only
  // clear the shadow while the focus ring is shown.
  const ink = (alpha: number) => `rgba(${dark ? '255, 255, 255' : '0, 0, 0'}, ${alpha})`
  const contrast = {
    '--sjs-general-forecolor-light': ink(dark ? 0.65 : 0.6),
    '--sjs-general-dim-forecolor-light': ink(dark ? 0.65 : 0.6),
    '--sjs-border-default': ink(dark ? 0.4 : 0.45),
    '--sjs-border-light': ink(dark ? 0.3 : 0.35),
    '--sjs-border-inside': ink(dark ? 0.3 : 0.45),
    '--sjs-shadow-small': `0px 0px 0px 1px ${ink(dark ? 0.3 : 0.35)}`,
    '--sjs-shadow-inner': `inset 0px 0px 0px 1px ${ink(dark ? 0.4 : 0.45)}`
  }
  return {
    ...base,
    cssVariables: {
      ...(base.cssVariables as Record<string, string>),
      ...contrast,
      '--sjs-primary-backcolor': primary,
      '--sjs-primary-backcolor-light': `color-mix(in srgb, ${primary} 10%, transparent)`,
      '--sjs-primary-backcolor-dark': `color-mix(in srgb, ${primary} 85%, black)`,
      '--sjs-primary-forecolor': primaryText,
      '--sjs-primary-forecolor-light': `color-mix(in srgb, ${primaryText} 25%, transparent)`
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

<style>
/* The boolean thumb is the selected yes/no answer. SurveyJS gives it the same
   1px --sjs-shadow-small edge as every button and frame and exposes no variable
   of its own, so this draws its edge as a thicker ring in the theme's border
   colour. Read-only and preview thumbs draw their own edge and are left alone. */
.sd-boolean:not(.sd-boolean--readonly, .sd-boolean--preview) .sd-boolean__thumb {
  box-shadow: 0 0 0 2px var(--sjs-border-default);
}
</style>
