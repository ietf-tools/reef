# Vendored SurveyJS bundles

The Django-hosted survey builder and analytics pages load the SurveyJS Creator
and Analytics libraries from self-hosted static files (no CDN), so a strict
Content Security Policy can allow `script-src 'self'` only.

`package.json` pins the SurveyJS packages. `sync.sh` copies the needed `.min.js`
and `.min.css` bundles out of `node_modules` into `static/vendor/surveyjs/`,
which is on `STATICFILES_DIRS` and is picked up by `collectstatic`.

## Populate the bundles

```
cd vendor
npm install
npm run sync
```

This runs automatically during the dev container init (`docker/scripts/app-init.sh`)
and during the production statics image build. The `node_modules/` directory and
the synced bundles under `static/vendor/surveyjs/` are generated and are not
committed.
