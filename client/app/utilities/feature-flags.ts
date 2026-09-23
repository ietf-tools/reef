import { z } from 'zod'
import type { Ref } from 'vue'

export const FeatureFlagsSchema = z.object({
  // Ensure all top-level fields are optional so that browsers
  // with old versions saved in localStorage values can still validate
})

export type FeatureFlags = z.infer<typeof FeatureFlagsSchema>

export const featureFlagsKey = Symbol() as InjectionKey<Ref<FeatureFlags>>

export const hasFeatureFlagsLoadedKey = Symbol() as InjectionKey<Ref<boolean>>

export type FeatureFlagUIRow = {
  title: string
  description?: string
  storageType: 'boolean' | string[]
}

const featureFlagsUI: Record<keyof FeatureFlags, FeatureFlagUIRow> = {}

export const DEFAULT_FEATURE_FLAGS: Required<FeatureFlags> = {}

export const featureFlagsUIRows = Object.entries(featureFlagsUI)

export const LOCALSTORAGE_KEY = 'feature-flag-experiments'

export const loadFeatureFlagsFromLocalStorage = (
  hasFeatureFlagsLoadedKey: Ref<boolean>,
  featureFlagsRef: Ref<FeatureFlags>
) => {
  hasFeatureFlagsLoadedKey.value = true
  try {
    const valString = window.localStorage.getItem(LOCALSTORAGE_KEY)
    if (!valString) {
      // no value in local storage
      return
    }
    const val = JSON.parse(valString)
    const { data, error } = FeatureFlagsSchema.safeParse(val)
    if (error || !data) {
      const errorTitle = 'Unable to validate feature flag JSON. Resetting localStorage config.'
      console.log(errorTitle, valString)
      window.localStorage.removeItem(LOCALSTORAGE_KEY)
      throw Error(errorTitle)
    }
    featureFlagsRef.value = {
      // merge current value as default data so that all keys will be present
      ...featureFlagsRef.value,
      ...data
    }
  } catch {
    // Reading throws when localStorage is disabled, which is expected
  }
}

const ENABLE_FEATURE_FLAGS_INPUT_VALUE = '//feature-flag-experiments'

export const watchInputForFeatureFlagExperiments = ({
  inputValueRef,
  isFeatureFlagsModalVisibleRef
}: {
  inputValueRef: Ref<string>
  isFeatureFlagsModalVisibleRef: Ref<boolean>
}): void => {
  watch(inputValueRef, () => {
    const { value } = inputValueRef
    if (value.trim() === ENABLE_FEATURE_FLAGS_INPUT_VALUE) {
      isFeatureFlagsModalVisibleRef.value = true
    }
  })
}

export const isFeatureFlagsModalVisibleKey = Symbol() as InjectionKey<Ref<boolean>>

// Owned by app.vue rather than the toast component: the toast is rendered inside <NuxtLayout>'s
// slot content, which is re-parented into a fresh layout instance (and so unmounted/remounted)
// whenever navigation switches layouts. Keeping "has this been dismissed" here instead of as
// component-local state means it survives that remount.
export const hasFeatureFlagsToastBeenDismissedKey = Symbol() as InjectionKey<Ref<boolean>>

export const useFeatureFlags = () => {
  const featureFlagsRef = inject(featureFlagsKey)

  if (!featureFlagsRef) {
    throw Error('Expected provide(featureFlagsKey) above in component tree.')
  }

  watch(
    () => featureFlagsRef?.value ?? undefined,
    () => {
      if (!featureFlagsRef) {
        throw Error('Expected inject(featureFlagsKey) to be available')
      }
      try {
        // localStorage APIs can throw Errors if browser storage is disabled or storage is full etc
        window.localStorage.setItem(LOCALSTORAGE_KEY, JSON.stringify(featureFlagsRef.value))
      } catch (e: unknown) {
        console.log(
          `[feature-flag-experiments]  Error saving config to localStorage (this is expected behaviour if browser localStorage is disabled or full)`,
          e
        )
      }
    }
  )

  return featureFlagsRef
}

export const useHasFeatureFlagsLoaded = (): Ref<boolean> => {
  const hasFeatureFlagsLoadedRef = inject(hasFeatureFlagsLoadedKey)

  if (!hasFeatureFlagsLoadedRef) {
    throw Error('Expected provide(hasFeatureFlagsLoadedKey) above in component tree.')
  }

  return hasFeatureFlagsLoadedRef
}

export const calculateIfFeatureFlagsAreEnabled = (featureFlags: FeatureFlags): boolean => {
  const entries = Object.entries(featureFlags)
  return entries.reduce((acc, [_key, value]) => (acc ? acc : Boolean(value)), false)
}

export const useAreFeatureFlagsEnabled = () => {
  const featureFlagsRef = inject(featureFlagsKey)
  const isMounted = ref(false)
  onMounted(() => {
    isMounted.value = true
  })
  onUnmounted(() => {
    isMounted.value = false
  })

  if (!featureFlagsRef) {
    console.warn('Expected provide(featureFlagsKey) above in component tree.')
  }

  const areFeatureFlagsEnabled = computed(() => {
    const featureFlags = featureFlagsRef?.value
    if (!featureFlags) {
      console.warn('Expected provide(featureFlagsKey) above in component tree.')
      return false
    }
    if (isMounted.value === false) {
      return false
    }

    return calculateIfFeatureFlagsAreEnabled(featureFlags)
  })

  return areFeatureFlagsEnabled
}
