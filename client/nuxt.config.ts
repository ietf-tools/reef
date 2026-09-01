// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-07-01",
  // No server rendering: `npm run generate` emits a static bundle that talks to
  // the Reef API from the browser and nothing else. There is no Node runtime in
  // front of it, so every route below has to be a real file NGINX can serve,
  // which is why the runner takes its slug as a query parameter rather than as
  // a path segment: /s?slug=<slug> is one prerendered page, /s/<slug> would be
  // an unbounded set of them.
  ssr: false,
  devtools: { enabled: true },
  devServer: {
    // 3001, not the Nuxt default of 3000, so the dev server does not collide
    // with Red's Nuxt server when both projects are running.
    port: 3001,
  },
  nitro: {
    prerender: {
      routes: ["/", "/s", "/auth/callback"],
    },
  },
  modules: ["@nuxtjs/tailwindcss", "reka-ui/nuxt"],
  css: ["~/assets/css/tailwind.css"],
  runtimeConfig: {
    public: {
      // The API shares this origin behind NGINX, so a relative base works and
      // requests carry the session cookie.
      apiBase: "", // NUXT_PUBLIC_API_BASE
      // Authentik OIDC application issuer (discovery is fetched from here).
      oidcAuthority: "https://account.ietf.org/application/o/reef/", // NUXT_PUBLIC_OIDC_AUTHORITY
      oidcClientId: "", // NUXT_PUBLIC_OIDC_CLIENT_ID
    },
  },
  app: {
    head: {
      title: "Reef Surveys",
      meta: [{ name: "viewport", content: "width=device-width, initial-scale=1" }],
    },
  },
});
