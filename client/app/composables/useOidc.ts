// OIDC (Authentik) login for the survey runner, using oidc-client-ts (the same
// library Red uses). Authorization Code + PKCE; tokens kept in localStorage.
import { UserManager, WebStorageStateStore } from "oidc-client-ts";

let manager: UserManager | null = null;

function getManager(): UserManager | null {
  if (!import.meta.client) {
    return null;
  }
  if (!manager) {
    const { public: pub } = useRuntimeConfig();
    manager = new UserManager({
      authority: pub.oidcAuthority,
      client_id: pub.oidcClientId,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: "code",
      scope: "openid profile email",
      userStore: new WebStorageStateStore({ store: window.localStorage }),
    });
  }
  return manager;
}

export function useOidc() {
  return {
    async login(returnTo?: string) {
      await getManager()?.signinRedirect({
        state: { returnTo: returnTo ?? window.location.pathname },
      });
    },
    async completeLogin() {
      return getManager()?.signinRedirectCallback();
    },
    async getAccessToken(): Promise<string | null> {
      const user = await getManager()?.getUser();
      return user && !user.expired ? user.access_token : null;
    },
    async logout() {
      await getManager()?.signoutRedirect();
    },
  };
}
