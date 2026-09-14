# reef-worker

CF Worker serving Reef's precomputed API responses out of R2 under
`/api/v1/*`, falling back to Django for a bucket miss or anything it doesn't
recognise.

Modelled on Red's `worker/` (rfced-worker), stripped down to the one thing
Reef needs: no redirects, no request filtering, no edge caching -- just a
blob read per route and a plain `fetch()` fallback.

Routes are matched by hand against `precomputer/registry.py`'s task keys; see
`src/index.ts`. Django itself only serves these under `/api/reef/*`, not
`/api/v1/*`, so each route (bar the unrouted `precomputed/subjects/*` ones)
carries an `originPath` that a bucket miss is rewritten to before falling
back.

`ALLOWED_ORIGINS` (per environment, in `wrangler.jsonc`) must track Django's
`REEF_CORS_ALLOWED_ORIGINS`, since a bucket hit here never reaches Django's
own CORS middleware.
