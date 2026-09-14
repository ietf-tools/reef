import type { IRequest } from 'itty-router'

const CONTENT_TYPE = 'application/json;charset=utf-8'

/** The opaque part of an ETag, ie the value with any `W/` weakness prefix removed. */
const opaqueTag = (etag: string) => etag.trim().replace(/^W\//, '')

/**
 * Does an `If-None-Match` value match the ETag we'd serve? Comparison is weak
 * (RFC 9110 §8.8.3.2), which is what `If-None-Match` requires.
 */
export const etagMatches = (ifNoneMatch: string | null, etag: string): boolean => {
  if (!ifNoneMatch) {
    return false
  }
  const candidates = ifNoneMatch.split(',').map((candidate) => candidate.trim())
  return candidates.includes('*') || candidates.map(opaqueTag).includes(opaqueTag(etag))
}

/**
 * Mirrors Django's CORS policy for `/api/reef/*` (reef/settings/base.py,
 * CORS_URLS_REGEX): a fixed allow-list, no credentials, never a wildcard.
 * Needed here because a bucket hit never reaches Django's own CORS middleware.
 */
export const corsOrigin = (req: IRequest, env: Env): string | undefined => {
  const origin = req.headers.get('origin')
  if (!origin) {
    return undefined
  }
  const allowed = env.ALLOWED_ORIGINS.split(',').map((entry) => entry.trim())
  return allowed.includes(origin) ? origin : undefined
}

/**
 * Serve one precomputed JSON object out of R2.
 *
 * On a bucket miss -- the precomputer might not have run yet, or (for a key
 * with no originPath) there is no live endpoint that could ever answer it --
 * originPath is where Django actually serves this content: this worker's own
 * `/api/v1/*` is not a path Django knows, so the fallback request is rewritten
 * to it rather than forwarded verbatim. Without an originPath the request
 * falls through (undefined) to the router's catch-all, which forwards it
 * unrewritten and lets Django's own 404 stand.
 */
export async function serveBlob(
  req: IRequest,
  env: Env,
  key: string,
  originPath?: string
): Promise<Response | undefined> {
  const object = await env.REEF_BUCKET.get(key)
  if (!object) {
    if (!originPath) {
      return undefined
    }
    const url = new URL(req.url)
    url.pathname = originPath
    return fetch(new Request(url, req))
  }

  const headers = new Headers()
  object.writeHttpMetadata(headers)
  headers.set('etag', object.httpEtag)
  headers.set('Content-Type', CONTENT_TYPE)

  const allowedOrigin = corsOrigin(req, env)
  if (allowedOrigin) {
    headers.set('Access-Control-Allow-Origin', allowedOrigin)
    headers.append('Vary', 'Origin')
  }

  if (etagMatches(req.headers.get('if-none-match'), object.httpEtag)) {
    return new Response(null, { status: 304, headers })
  }

  return new Response(object.body, { headers })
}
