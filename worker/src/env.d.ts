interface Env {
  REEF_BUCKET: R2Bucket
  /** Comma-separated. Must track Django's REEF_CORS_ALLOWED_ORIGINS for this environment. */
  ALLOWED_ORIGINS: string
}
