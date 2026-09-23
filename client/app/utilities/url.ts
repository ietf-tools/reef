export const IETF_URL_ORIGIN = 'https://www.ietf.org'

export const IETF_ACCOUNT_URL_ORIGIN = 'https://account.ietf.org'

const httpRegex = /^https?:\/\//

/** Anything not starting `http(s)://` is one of ours (a Nuxt route), everything else is external. */
export const isExternalLink = (href?: string): boolean => {
  if (href === undefined) {
    return true
  }
  return httpRegex.test(href)
}

export const isInternalLink = (href?: string): boolean => !isExternalLink(href)
