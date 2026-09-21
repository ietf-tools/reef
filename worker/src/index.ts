import { IttyRouter } from 'itty-router'
import type { IRequest } from 'itty-router'
import { serveBlob } from './blobs'

const router = IttyRouter<IRequest, [Env, ExecutionContext]>()

router
  .get('/api/v1/stats.json', (req, env) => serveBlob(req, env, 'stats.json', '/api/reef/stats/'))
  .get('/api/v1/popularity.json', (req, env) => serveBlob(req, env, 'popularity.json', '/api/reef/popularity/'))
  // Not routed in Django at all (see precomputer/registry.py), so no originPath: a
  // bucket miss here has nowhere else to be served from.
  .get('/api/v1/subjects.json', (req, env) => serveBlob(req, env, 'subjects.json'))
  .get('/api/v1/subjects/:slug.json', (req, env) => serveBlob(req, env, `subjects/${req.params.slug}.json`))
  .get('/api/v1/surveys/open.json', (req, env) => serveBlob(req, env, 'surveys/open.json', '/api/reef/surveys/open/'))
  // The superset Red filters by each row's `visibility`, including the surveys
  // only a signed-in reader may be offered. No originPath: no deployment routes
  // it, and the served `/api/reef/surveys/open/` answers a different question.
  .get('/api/v1/surveys/published.json', (req, env) => serveBlob(req, env, 'surveys/published.json'))
  .get('/api/v1/surveys/:slug/definition.json', (req, env) =>
    serveBlob(
      req,
      env,
      `surveys/${req.params.slug}/definition.json`,
      `/api/reef/surveys/${req.params.slug}/definition/`
    )
  )
  .get('/api/v1/ratings/:rfc.json', (req, env) =>
    serveBlob(req, env, `ratings/${req.params.rfc}.json`, `/api/reef/ratings/${req.params.rfc}/`)
  )
  /**
   * Everything else this worker is invoked for -- `/api/reef/*` (the rest of the
   * Reef API: mutations, auth'd reads, schema, docsets, subscriptions...), `/admin*`
   * (Django admin, and the survey builder under `/admin/survey-builder/*`),
   * `/oidc/*`, `/static/*`, `/health/*` (see wrangler.jsonc's run_worker_first) --
   * and any `/api/v1/*` route above on a bucket miss, since
   * `serveBlob` returns undefined rather than a 404, goes to Django, the only place
   * that can render it live.
   *
   * A same-zone fetch() cannot re-enter a Worker route, so this reaches the zone
   * origin (the Cloudflare Tunnel) rather than looping back into this worker. That
   * holds as long as the `global_fetch_strictly_public` compatibility flag stays
   * off, which is the default. Everything not listed in run_worker_first never
   * reaches here at all -- it's served straight from the Nuxt build in `dist/`.
   */
  .all('*', (request: IRequest) => fetch(request))

export default {
  fetch: (request: Request, env: Env, ctx: ExecutionContext): Promise<Response> => router.fetch(request, env, ctx)
}
