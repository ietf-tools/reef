// Typed access to the Reef API. Types come from openapi-typescript, generated
// from ../reef_api.yaml into app/types/reef-api.ts (see the gen:api script).
import type { components } from "~/types/reef-api";

type SurveyDefinition = components["schemas"]["SurveyDefinition"];
type OpenSurvey = components["schemas"]["OpenSurvey"];

export function useSurveyApi() {
  const config = useRuntimeConfig();
  const oidc = useOidc();

  // The bundle is static and runs only in the browser. The base is empty (and
  // so relative) wherever the API is proxied onto the site's own origin, as the
  // dev NGINX does; where the API answers on a different host, set
  // NUXT_PUBLIC_API_BASE to its absolute URL at build time. Auth travels as a
  // bearer token either way, so nothing here depends on a cookie.
  const baseURL = config.public.apiBase;

  async function authHeaders(): Promise<Record<string, string>> {
    const token = await oidc.getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  return {
    async getDefinition(slug: string): Promise<SurveyDefinition> {
      return $fetch<SurveyDefinition>(`/api/reef/surveys/${slug}/definition/`, {
        baseURL,
        headers: await authHeaders(),
      });
    },

    async openSurveys(): Promise<OpenSurvey[]> {
      return $fetch<OpenSurvey[]>(`/api/reef/surveys/open/`, {
        baseURL,
        headers: await authHeaders(),
      });
    },

    async submitResponse(slug: string, data: unknown): Promise<void> {
      await $fetch(`/api/reef/surveys/${slug}/responses/`, {
        method: "POST",
        baseURL,
        headers: await authHeaders(),
        body: { data },
      });
    },
  };
}
