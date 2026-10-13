import { describe, expect, it } from 'vitest'
import { redirectToRunner } from './runner'

describe('redirectToRunner', () => {
  const env = { RED_ORIGIN: 'https://www.rfc-editor.org' }

  it("sends /s to Red's /survey/ with the query intact", () => {
    const response = redirectToRunner(new Request('https://surveys.rfc-editor.org/s?slug=feedback-2026&x=1'), env)
    expect(response.status).toBe(301)
    expect(response.headers.get('location')).toBe('https://www.rfc-editor.org/survey/?slug=feedback-2026&x=1')
  })

  it('keeps a missing query missing', () => {
    const response = redirectToRunner(new Request('https://surveys.rfc-editor.org/s'), env)
    expect(response.headers.get('location')).toBe('https://www.rfc-editor.org/survey/')
  })
})
