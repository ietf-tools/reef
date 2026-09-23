import { useAuthStore } from '~/stores/auth'

// Restores the OIDC session on the client into the auth store, so Header.vue's account
// menu can render signed-in state without every page having to ask oidc-client-ts itself.
export const useOidcSession = () => {
  const authStore = useAuthStore()
  const oidc = useOidc()

  onMounted(async () => {
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
  })
}
