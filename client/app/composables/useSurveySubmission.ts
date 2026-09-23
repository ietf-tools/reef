import { statusOf } from '~/utilities/fetch-error'

type SurveyData = Record<string, unknown>

// Answers held across the sign-in redirect a failed submission sends the visitor
// through, which leaves the page and would otherwise take everything they filled in
// with it. sessionStorage, so they stay in the tab that wrote them and no longer.
const draftKey = (slug: string) => `reef-survey-draft:${slug}`

const readDraft = (slug: string): SurveyData | null => {
  try {
    const raw = window.sessionStorage.getItem(draftKey(slug))
    const parsed: unknown = raw ? JSON.parse(raw) : null
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? { ...parsed } : null
  } catch {
    return null
  }
}

const keepDraft = (slug: string, data: SurveyData): boolean => {
  try {
    window.sessionStorage.setItem(draftKey(slug), JSON.stringify(data))
    return true
  } catch {
    return false
  }
}

const clearDraft = (slug: string) => {
  try {
    window.sessionStorage.removeItem(draftKey(slug))
  } catch {
    // Nothing kept, or storage is blocked; either way nothing is left to resubmit.
  }
}

const failureMessage = (status: number | undefined): string => {
  if (status === undefined) {
    return "Your response couldn't be saved because the server couldn't be reached. Check your connection and try again."
  }
  if (status === 404) {
    return "This survey has closed, so your response couldn't be saved."
  }
  if (status === 400) {
    return "Your response was rejected as invalid, so it couldn't be saved."
  }
  return "Your response couldn't be saved. Please try again."
}

const notAcceptedMessage =
  "Your sign-in wasn't accepted, so your response couldn't be saved. Try signing out and back in, then submit again."

/**
 * Submits a survey response, and says why when it could not.
 *
 * save() throws an Error whose message is meant for the visitor. When the failure is
 * the visitor's sign-in (it lapsed, or the API refused it), it instead keeps their
 * answers and sends them to sign in again; coming back, `pending` holds those answers
 * and resume() submits them. That happens once: a second sign-in failure for answers
 * that already went round is reported rather than redirected, since a token the API
 * refuses would otherwise send the visitor round in a loop.
 */
export const useSurveySubmission = (slug: string) => {
  const api = useSurveyApi()
  const oidc = useOidc()
  const route = useRoute()

  const submitted = ref(false)
  const pending = ref<SurveyData | null>(readDraft(slug))
  const resuming = ref(false)
  const resumeError = ref<string | null>(null)
  const returnedFromSignIn = pending.value !== null

  const signInAgain = async (data: SurveyData): Promise<never> => {
    if (returnedFromSignIn || !keepDraft(slug, data)) {
      throw new Error(notAcceptedMessage)
    }
    await oidc.login(route.fullPath)
    throw new Error('Taking you to sign in again. Your answers will be kept.')
  }

  const save = async (data: SurveyData) => {
    // A lapsed sign-in would otherwise go out as an anonymous submission, which an
    // open survey accepts and records against nobody: never counted as theirs.
    if ((await oidc.hasSession()) && !(await oidc.getAccessToken())) {
      await signInAgain(data)
    }
    try {
      await api.submitResponse(slug, data)
    } catch (error) {
      const status = statusOf(error)
      if (status === 401 || status === 403) {
        await signInAgain(data)
      }
      throw new Error(failureMessage(status))
    }
    clearDraft(slug)
    pending.value = null
    submitted.value = true
  }

  const resume = async () => {
    const { value: data } = pending
    if (!data || resuming.value) {
      return
    }
    resuming.value = true
    resumeError.value = null
    try {
      await save(data)
    } catch (error) {
      resumeError.value = error instanceof Error ? error.message : failureMessage(undefined)
    } finally {
      resuming.value = false
    }
  }

  return { submitted, pending, resuming, resumeError, save, resume }
}
