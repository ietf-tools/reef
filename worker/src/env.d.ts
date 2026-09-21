interface Env {
  REEF_BUCKET: R2Bucket
  /**
   * Comma-separated. Tracks Django's REEF_CORS_ALLOWED_ORIGINS for this
   * environment, because a bucket read never reaches Django's CORS middleware.
   * Staging carries one origin Django does not, for Red's dev server; see
   * wrangler.jsonc.
   */
  ALLOWED_ORIGINS: string
}
