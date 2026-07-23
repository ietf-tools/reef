// Typed access to the Pink API. Types come from openapi-typescript, generated
// from ../pink_api.yaml into app/types/pink-api.ts (see the gen:api script).
import type { components } from "~/types/pink-api";

type SurveyDefinition = components["schemas"]["SurveyDefinition"];
type OpenSurvey = components["schemas"]["OpenSurvey"];

export function useSurveyApi() {
  const { public: pub } = useRuntimeConfig();
  const oidc = useOidc();

  async function authHeaders(): Promise<Record<string, string>> {
    const token = await oidc.getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  return {
    async getDefinition(slug: string): Promise<SurveyDefinition> {
      return $fetch<SurveyDefinition>(`/api/pink/surveys/${slug}/definition/`, {
        baseURL: pub.apiBase,
        headers: await authHeaders(),
      });
    },

    async openSurveys(): Promise<OpenSurvey[]> {
      return $fetch<OpenSurvey[]>(`/api/pink/surveys/open/`, {
        baseURL: pub.apiBase,
        headers: await authHeaders(),
      });
    },

    async submitResponse(slug: string, data: unknown): Promise<void> {
      await $fetch(`/api/pink/surveys/${slug}/responses/`, {
        method: "POST",
        baseURL: pub.apiBase,
        headers: await authHeaders(),
        body: { data },
      });
    },
  };
}
