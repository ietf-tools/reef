import { useAuthStore } from '~/stores/auth'

// Restores the OIDC session on the client into the auth store, so Header.vue's account
// menu can render signed-in state without every page having to ask oidc-client-ts itself.
export const useOidcSession = () => {
  const authStore = useAuthStore()
  const oidc = useOidc()

  // Exposed so auth/callback.vue can re-run it once the redirect callback has
  // stored the new session, since it navigates onward client-side rather than
  // reloading, and Header.vue (where this otherwise only runs on mount) stays
  // mounted across that navigation instead of asking again on its own.
  const refresh = async () => {
    try {
      const user = await oidc.getUser()
      if (user) {
        authStore.setUser(user)
      } else {
        authStore.clearUser()
      }
    } finally {
      authStore.hasCheckedAuth = true
    }
  }

  onMounted(refresh)

  return { refresh }
}
