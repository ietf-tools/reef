// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-07-01",
  ssr: true,
  devtools: { enabled: true },
  devServer: {
    port: 3000,
  },
  modules: ["@nuxtjs/tailwindcss", "reka-ui/nuxt"],
  css: ["~/assets/css/tailwind.css"],
  runtimeConfig: {
    // Server-side (render-time) API base. The Nuxt server cannot use a relative
    // URL, so it needs a reachable host: the Django server in the same dev
    // container, or the internal service URL in production.
    apiBaseServer: "http://localhost:8001", // NUXT_API_BASE_SERVER
    public: {
      // Client-side base. Same NGINX origin as the Django API, so relative works.
      apiBase: "", // NUXT_PUBLIC_API_BASE
      // Authentik OIDC application issuer (discovery is fetched from here).
      oidcAuthority: "https://account.ietf.org/application/o/pink/", // NUXT_PUBLIC_OIDC_AUTHORITY
      oidcClientId: "", // NUXT_PUBLIC_OIDC_CLIENT_ID
    },
  },
  app: {
    head: {
      title: "Pink Surveys",
      meta: [{ name: "viewport", content: "width=device-width, initial-scale=1" }],
    },
  },
});
