/** The HTTP status an ofetch error carries, or undefined when no response arrived at all. */
export const statusOf = (error: unknown): number | undefined => {
  if (typeof error === 'object' && error !== null && 'statusCode' in error && typeof error.statusCode === 'number') {
    return error.statusCode
  }
  return undefined
}
