// oidc-client-ts, the same library Red uses.
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'

// The claims Header.vue's account menu shows. A subset of User['profile'],
// named so the Pinia auth store doesn't import oidc-client-ts directly.
export type OidcUser = {
  name?: string
  preferredUsername?: string
  email?: string
  picture?: string
}

const toOidcUser = (user: User): OidcUser => ({
  name: typeof user.profile.name === 'string' ? user.profile.name : undefined,
  preferredUsername: typeof user.profile.preferred_username === 'string' ? user.profile.preferred_username : undefined,
  email: typeof user.profile.email === 'string' ? user.profile.email : undefined,
  picture: typeof user.profile.picture === 'string' ? user.profile.picture : undefined
})

// The API tolerates this much clock skew too, so a token the browser still counts as
// live is one the API will accept.
const renewBeforeExpirySeconds = 30

let manager: UserManager | null = null

function getManager(): UserManager | null {
  if (!import.meta.client) {
    return null
  }
  if (!manager) {
    const { public: pub } = useRuntimeConfig()
    manager = new UserManager({
      authority: pub.oidcAuthority,
      client_id: pub.oidcClientId,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: 'code',
      // offline_access, same as Red, so Authentik issues a refresh token alongside the access token.
      scope: 'openid profile email offline_access',
      userStore: new WebStorageStateStore({ store: window.localStorage })
    })
  }
  return manager
}

export function useOidc() {
  return {
    async login(returnTo?: string) {
      await getManager()?.signinRedirect({
        // pathname alone would drop ?slug=... — every survey's identity lives in the query string.
        state: { returnTo: returnTo ?? `${window.location.pathname}${window.location.search}` }
      })
    },
    async completeLogin() {
      return getManager()?.signinRedirectCallback()
    },
    // Renews rather than giving up on a lapsed token: the background renewal misses
    // whenever its timer does, as on a laptop that slept through it, and a survey can
    // take longer to fill in than the token lasts. Null when there is no session, or
    // renewal failed; hasSession() tells those two apart.
    async getAccessToken(): Promise<string | null> {
      const userManager = getManager()
      const user = await userManager?.getUser()
      if (!userManager || !user) {
        return null
      }
      const { expires_in: expiresIn } = user
      if (expiresIn === undefined || expiresIn > renewBeforeExpirySeconds) {
        return user.access_token
      }
      try {
        const renewed = await userManager.signinSilent()
        return renewed?.access_token ?? null
      } catch {
        return null
      }
    },
    // Whether anyone signed in on this browser, even if their token has since lapsed.
    async hasSession(): Promise<boolean> {
      return Boolean(await getManager()?.getUser())
    },
    // Header.vue's account menu: null when there is no session to restore, distinct
    // from "not checked yet" (see useOidcSession, which owns that distinction).
    async getUser(): Promise<OidcUser | null> {
      const user = await getManager()?.getUser()
      return user && !user.expired ? toOidcUser(user) : null
    },
    async logout() {
      await getManager()?.signoutRedirect()
    }
  }
}
