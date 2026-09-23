/**
 * https://developer.mozilla.org/en-US/docs/Web/HTML/Element/a#target
 */
export const TARGET_NEW_WINDOW = '_blank'

/**
 * The `noopener` prevents linked sites (theirs) having control over originating sites (ours)
 * via JavaScript https://mathiasbynens.github.io/rel-noopener/
 *
 * there's another commonly used property `noreferrer` which we've intentionally excluded from this string.
 * referrers are ok.
 **/
export const EXTERNAL_LINK_REL = 'noopener'

const htmlEscapeMapping = {
  '<': '&lt;',
  '>': '&gt;',
  "'": '&apos;',
  '"': '&quot;',
  '&': '&amp;'
}

/**
 * Escapes HTML to text html with character entities, ie exposing <>'"& chars
 * Useful for <noscript> HTML string generation
 */
export const htmlEscapeToText = (html: string): string =>
  html.replace(/([<>'"&])/g, (match, key) => {
    if (key in htmlEscapeMapping) {
      return htmlEscapeMapping[key as keyof typeof htmlEscapeMapping]
    }
    return match
  })
