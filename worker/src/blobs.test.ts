import { describe, expect, it } from 'vitest'
import { corsOrigin, etagMatches } from './blobs'

describe('etagMatches', () => {
  it('matches an identical strong etag', () => {
    expect(etagMatches('"abc123"', '"abc123"')).toBe(true)
  })

  it('matches a weak If-None-Match against a strong stored etag', () => {
    expect(etagMatches('W/"abc123"', '"abc123"')).toBe(true)
  })

  it('matches any entry in a comma-separated list', () => {
    expect(etagMatches('"nope", "abc123"', '"abc123"')).toBe(true)
  })

  it('matches a wildcard', () => {
    expect(etagMatches('*', '"abc123"')).toBe(true)
  })

  it('rejects a different etag', () => {
    expect(etagMatches('"xyz789"', '"abc123"')).toBe(false)
  })

  it('rejects a missing header', () => {
    expect(etagMatches(null, '"abc123"')).toBe(false)
  })
})

describe('corsOrigin', () => {
  const env = {
    REEF_BUCKET: {} as R2Bucket,
    ALLOWED_ORIGINS: 'https://www.rfc-editor.org, https://foo.example'
  }

  it('echoes back an allowed origin', () => {
    const req = {
      headers: new Headers({ origin: 'https://www.rfc-editor.org' })
    } as unknown as Request
    expect(corsOrigin(req as never, env)).toBe('https://www.rfc-editor.org')
  })

  it('refuses an origin not on the list', () => {
    const req = { headers: new Headers({ origin: 'https://evil.example' }) } as unknown as Request
    expect(corsOrigin(req as never, env)).toBeUndefined()
  })

  it('returns undefined for a same-origin request with no Origin header', () => {
    const req = { headers: new Headers() } as unknown as Request
    expect(corsOrigin(req as never, env)).toBeUndefined()
  })
})
