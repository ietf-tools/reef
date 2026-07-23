// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-07-01",
  ssr: false,
  devtools: { enabled: true },
  devServer: {
    port: 3000,
  },
  modules: ["@nuxtjs/tailwindcss", "reka-ui/nuxt"],
  css: ["~/assets/css/tailwind.css"],
  runtimeConfig: {
    public: {
      // Same NGINX origin as the Django API in dev, so a relative base works.
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
