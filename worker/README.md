# reef-frontend

CF Worker for `surveys.{env}.rfc-editor.org`. Serves `public/` as static assets,
which is a single page at `/` pointing a visitor at www.rfc-editor.org, and for
the paths listed in `wrangler.jsonc`'s `assets.run_worker_first` runs this
worker's code instead:

- `/s` -- a 301 to `/survey/` on Red's origin (`RED_ORIGIN`), query string intact.
  Red hosts the survey runner; links to Reef's old one are still out there.
- `/api/v1/*` -- Reef's precomputed API responses, read straight out of the
  `REEF_BUCKET` R2 bucket, falling back to Django (rewritten to the
  equivalent `/api/reef/*` path) on a bucket miss.
- everything else in that list (`/api/reef/*`, `/admin*` -- which includes the
  survey builder at `/admin/survey-builder/*` -- `/oidc/*`, `/static/*`,
  `/health/*`) -- proxied straight to Django.

Any other path is a 404.

Modelled on Red's `worker/` (rfced-worker), stripped down to the one thing
Reef needs beyond that proxy: no request filtering, no edge caching -- just a
blob read per `/api/v1/*` route and a plain `fetch()` fallback for everything
else.

`/api/v1/*` routes are matched by hand against `precomputer/registry.py`'s
task keys; see `src/index.ts`. Django itself only serves these under
`/api/reef/*`, not `/api/v1/*`, so each route (bar the unrouted
`subjects/*` ones) carries an `originPath` that a bucket miss is rewritten to
before falling back.

`ALLOWED_ORIGINS` (per environment, in `wrangler.jsonc`) must track Django's
`REEF_CORS_ALLOWED_ORIGINS`, since a bucket hit here never reaches Django's
own CORS middleware.

The deploy jobs in `build.yml` and `release.yml` run `npm ci` here before
`wrangler deploy`, since `itty-router` has to be resolvable at bundle time.
