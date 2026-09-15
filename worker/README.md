# reef-worker

CF Worker for `surveys.{env}.rfc-editor.org`. Serves the built Nuxt SPA
(`../client/dist`) as static assets, and for the paths listed in
`wrangler.jsonc`'s `assets.run_worker_first` runs this worker's code instead:

- `/api/v1/*` -- Reef's precomputed API responses, read straight out of the
  `REEF_BUCKET` R2 bucket, falling back to Django (rewritten to the
  equivalent `/api/reef/*` path) on a bucket miss.
- everything else in that list (`/api/reef/*`, `/admin*`, `/oidc/*`,
  `/manage*`, `/static/*`, `/health/*`) -- proxied straight to Django, same as
  the old `client/worker.js` did before the two workers were merged into
  this one.

Modelled on Red's `worker/` (rfced-worker), stripped down to the one thing
Reef needs beyond that proxy: no redirects, no request filtering, no edge
caching -- just a blob read per `/api/v1/*` route and a plain `fetch()`
fallback for everything else.

`/api/v1/*` routes are matched by hand against `precomputer/registry.py`'s
task keys; see `src/index.ts`. Django itself only serves these under
`/api/reef/*`, not `/api/v1/*`, so each route (bar the unrouted
`precomputed/subjects/*` ones) carries an `originPath` that a bucket miss is
rewritten to before falling back.

`ALLOWED_ORIGINS` (per environment, in `wrangler.jsonc`) must track Django's
`REEF_CORS_ALLOWED_ORIGINS`, since a bucket hit here never reaches Django's
own CORS middleware.

`reusable-build.yml` packages this directory and `client/dist` as siblings
(matching `assets.directory: ../client/dist`) for the deploy job in
`build.yml`, which runs `npm ci` here before `wrangler deploy` -- unlike the
old dependency-free `worker.js`, this one needs `itty-router` resolvable at
bundle time.
