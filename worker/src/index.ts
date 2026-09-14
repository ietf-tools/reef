import { IttyRouter } from 'itty-router'
import type { IRequest } from 'itty-router'
import { serveBlob } from './blobs'

const router = IttyRouter<IRequest, [Env, ExecutionContext]>()

router
  .get('/api/v1/stats/', (req, env) => serveBlob(req, env, 'stats.json', '/api/reef/stats/'))
  .get('/api/v1/popularity/', (req, env) => serveBlob(req, env, 'popularity.json', '/api/reef/popularity/'))
  // Not routed in Django at all (see precomputer/registry.py), so no originPath: a
  // bucket miss here has nowhere else to be served from.
  .get('/api/v1/precomputed/subjects/', (req, env) => serveBlob(req, env, 'subjects.json'))
  .get('/api/v1/precomputed/subjects/:slug/', (req, env) => serveBlob(req, env, `subjects/${req.params.slug}.json`))
  .get('/api/v1/surveys/open/', (req, env) => serveBlob(req, env, 'surveys/open.json', '/api/reef/surveys/open/'))
  .get('/api/v1/surveys/:slug/definition/', (req, env) =>
    serveBlob(
      req,
      env,
      `surveys/${req.params.slug}/definition.json`,
      `/api/reef/surveys/${req.params.slug}/definition/`
    )
  )
  .get('/api/v1/ratings/:rfc/', (req, env) =>
    serveBlob(req, env, `ratings/${req.params.rfc}.json`, `/api/reef/ratings/${req.params.rfc}/`)
  )
  /**
   * Everything else -- and any of the routes above on a bucket miss, since
   * `serveBlob` returns undefined rather than a 404 -- goes to Django, the
   * only place that can render it live.
   */
  .all('*', (request: IRequest) => fetch(request))

export default {
  fetch: (request: Request, env: Env, ctx: ExecutionContext): Promise<Response> => router.fetch(request, env, ctx)
}
