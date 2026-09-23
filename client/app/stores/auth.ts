import type { OidcUser } from '~/composables/useOidc'

// Reactive auth state for the UI layer. Populated client-side after mount (see
// Header.vue) from the OIDC session — defaults to logged-out so first paint renders
// the anonymous view, then enhances once the session is restored.
export const useAuthStore = defineStore('auth', () => {
  const isAuthenticatedRef = ref(false)
  const hasCheckedAuthRef = ref(false)
  const userRef = ref<OidcUser>()

  const setUser = (user: OidcUser) => {
    isAuthenticatedRef.value = true
    userRef.value = user
  }

  const clearUser = () => {
    isAuthenticatedRef.value = false
    userRef.value = undefined
  }

  return {
    hasCheckedAuth: hasCheckedAuthRef,
    isAuthenticated: isAuthenticatedRef,
    user: userRef,
    setUser,
    clearUser
  }
})
