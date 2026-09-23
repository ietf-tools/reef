import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2025-07-01',
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
    port: 3001
  },
  nitro: {
    prerender: {
      routes: ['/', '/s', '/auth/callback']
    }
  },
  modules: ['reka-ui/nuxt', '@pinia/nuxt', 'pinia-plugin-persistedstate/nuxt', './modules/color-mode/module.ts'],
  // Same defaults as Red, so the resolved theme (and the .dark class it toggles)
  // agrees between the two sites for anyone moving between them.
  colorMode: {
    classSuffix: '',
    preference: 'system',
    fallback: 'light'
  },
  css: ['~/assets/css/tailwind.css'],
  vite: {
    plugins: [tailwindcss()]
  },
  runtimeConfig: {
    public: {
      // Empty means relative: the dev NGINX proxies the API onto this origin.
      // Set NUXT_PUBLIC_API_BASE where it answers elsewhere.
      apiBase: '', // NUXT_PUBLIC_API_BASE
      // Authentik OIDC application issuer (discovery is fetched from here).
      // Reef's own "reef-staging" application (a public/PKCE client), not Red's
      // "rfc-editor" one: the identity is still the same account.ietf.org user,
      // but Reef authenticates against its own registered app — Reef's server
      // only ever sees the resulting access token as an API caller
      // (REEF_API_OIDC_* in reef/settings/base.py).
      oidcAuthority: 'https://account.ietf.org/application/o/reef-staging/', // NUXT_PUBLIC_OIDC_AUTHORITY
      oidcClientId: 'dAytIOu6rzN2Za3kyeFlo3FhHh3K0al0w0k1N649', // NUXT_PUBLIC_OIDC_CLIENT_ID
      // Name of a paired *Light/*Dark theme family from survey-core/themes (e.g.
      // "Default", "Flat", "Sharp"), applied as the runner's base so it follows
      // this site's colour mode. Set NUXT_PUBLIC_SURVEY_THEME_FAMILY to restyle
      // every survey's chrome without a code change.
      surveyThemeFamily: 'Default' // NUXT_PUBLIC_SURVEY_THEME_FAMILY
    }
  },
  app: {
    head: {
      title: 'RFC-Editor.org Surveys',
      htmlAttrs: {
        lang: 'en'
      },
      meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }],
      // Same font source as Red, so text renders identically across the two sites.
      link: [
        { rel: 'preconnect', href: 'https://static.ietf.org' },
        { rel: 'stylesheet', href: 'https://static.ietf.org/fonts/inter/import.css' }
      ]
    }
  }
})
