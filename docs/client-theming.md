# Client theming: shared with Red

`client/` (the Nuxt survey runner) now shares its visual chrome, coding
conventions and component patterns with Red (`ietf-tools/red`, the public RFC
website), since both sit under the rfc-editor.org site family. This documents
what was ported, what was adapted, and why, so the two don't quietly drift
apart or get "fixed" back out of sync.

## Ported wholesale

- Tailwind v4 (`@tailwindcss/vite`, CSS-first `@theme`), Red's color palette,
  Inter font (same `static.ietf.org` source), and the Reka UI transition
  keyframes/animations, in `app/assets/css/tailwind.css`.
- The color-mode Nuxt module (`modules/color-mode/`), copied verbatim from
  Red's fork of `@nuxtjs/color-mode`. `script.min.js` is a symlink to
  `script.js`, as it is in Red — keep it that way rather than a real file, or
  the two copies will drift.
- `.oxfmtrc.json` / `.oxlintrc.json`: single quotes, no semicolons, no
  trailing commas, 120-column width — copied from Red's monorepo root
  (`~/Code/red/.oxfmtrc.json`), not oxfmt's own defaults (which are double
  quotes + semicolons + trailing commas). Running `oxfmt .` without this
  config silently reformats away from Red's style.
- `Header.vue`, `HeaderNavData.ts`, `HeaderNavDesktop.vue`,
  `HeaderNavMobile.vue` and their supporting components: the Reka UI
  `NavigationMenu`/`Dialog`/`Accordion`/`RadioGroup` structure, keyboard
  navigation, and no-JS `<noscript>` fallback are all Red's, unmodified in
  mechanism.
- Pinia (`@pinia/nuxt`, `pinia-plugin-persistedstate`) and the feature-flags
  system (`utilities/feature-flags.ts`, `FeatureFlagsModal.vue`,
  `FeatureFlagsToast.vue`, `FeatureFlagItem.vue`): same localStorage schema,
  same provide/inject keys, same toast/modal UI.

## Adapted, and why

- **RFC-editor content nav dropped.** Red's real `HeaderNavData.ts` carries
  ~30 links (Browse RFCs, Errata, Document Queue, Subjects, search, …) that
  point at pages Reef doesn't serve. Reef's menu keeps only what applies:
  a theme (light/dark/system) radio group and the account menu.
- **Search dropped entirely**, per explicit instruction — no `GraphicsSearch`
  icon, no `SEARCH_PATH`, no string-icon resolution in `HeaderNavIcon.vue`
  (which only ever renders function-icon menu items now).
- **One OIDC client, not two.** Red's account menu is backed by its own
  `stores/auth.ts` + a 346-line `utilities/oidc.ts` (separate `UserManager`,
  separate Authentik redirect URIs, an enrollment-flow URL Reef's runtime
  config doesn't have). Reef already runs its own OIDC client
  (`composables/useOidc.ts`, oidc-client-ts, PKCE) to gate protected surveys —
  a second independent client tracking sign-in state would risk the header
  showing one auth state while survey access checks another. `stores/auth.ts`
  is the same generic Pinia store Red uses, but it's populated by
  `composables/useOidcSession.ts`, a small composable reading Reef's existing
  `useOidc()` singleton rather than a second `UserManager`. `useOidc.ts`
  gained a `getUser()` method (returning name/picture/email claims) to
  support this; login/logout still go through the same composable.
- **No `oidc` feature flag.** In Red, personalisation is still an opt-in
  experiment (`DEFAULT_FEATURE_FLAGS.oidc`); in Reef, OIDC is load-bearing —
  `s.vue` already triggers login unconditionally for authenticated-visibility
  surveys — so `HeaderNavData.ts` shows the account menu unconditionally too,
  with no feature flag involved. `utilities/feature-flags.ts` keeps the
  mechanism (schema, localStorage persistence, provide/inject keys, the
  toast/modal UI) with zero flags currently defined, ready for a real one
  later.
- **Reef's own OIDC application, not Red's.** `nuxt.config.ts`'s default
  `oidcAuthority`/`oidcClientId` point at Reef's dedicated Authentik
  application (`account.ietf.org/application/o/reef-staging/`), not Red's
  `rfc-editor` one — same identity provider, separate registered app.
  `scope` includes `offline_access`, matching Red, so Authentik issues a
  refresh token alongside the access token.
- **"Create an account" dropped** from the signed-out menu: it calls Red's
  `oidcRegister()`, an Authentik enrollment-flow URL that isn't in Reef's
  runtime config. Only "Sign in" remains; Authentik's own login page offers
  registration.
- **Advanced Settings modal, "Surveys" list modal, and their Pinia stores
  dropped.** `stores/ui-settings.ts` and `AdvancedUISettings.vue` are pure
  RFC-content settings (obsoleted/updated-by display, subject density,
  word-break mode) with no Reef equivalent. The "Surveys" modal
  (`stores/reef-surveys.ts`, `SurveysModal.vue`) duplicates what Reef's own
  home page (`/`) already lists, and its data source
  (`utilities/reef-precomputed.ts`) is Red's blob-store-fixture mechanism for
  reading Reef's *own* precomputed output from outside — redundant from
  inside Reef, which can just call its live API. Dropping both modal-button
  consumers meant the `modal-button` role, `NavModals.vue`, and the checkbox
  group branches in the nav components could come out too — nothing in
  Reef's menu data uses them.
- **Footer trimmed** to the IETF branding row, System Status, and Report a Bug
  (pointed at `ietf-tools/reef`, not `ietf-tools/red`). The footer nav
  columns (`FooterNavData`) are Red's own sitemap.
- Brand mark: same logo SVGs as Red (Reef is `surveys.rfc-editor.org`, same
  family), with alt text describing Reef rather than "RFC Editor" directly.

## Not part of this pass

Red's `ScrollToTop`, `Heading.vue` (anchor-linkable headings with
copy-to-clipboard), and its Matomo/OpenTelemetry instrumentation weren't
brought over — none of the ported chrome needed them, and pulling them in
would mean porting their own dependency chains for no current caller.
