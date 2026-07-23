#!/usr/bin/env bash
#
# Copy the SurveyJS UMD bundles from node_modules into the Django static tree.
# Run from the vendor/ directory after `npm install`. Invoked as `npm run sync`.
#
set -euo pipefail

cd "$(dirname "$0")"

DEST="static/vendor/surveyjs"
mkdir -p "$DEST"

MISSING=0
copy() {
  local src="node_modules/$1"
  if [ -f "$src" ]; then
    cp "$src" "$DEST/"
    echo "  + $(basename "$src")"
  else
    echo "  ! missing: $src" >&2
    MISSING=1
  fi
}

# Form library (runner + core) — dependencies of the Creator and Analytics.
copy survey-core/survey-core.min.css
copy survey-core/survey.core.min.js
copy survey-js-ui/survey-js-ui.min.js

# Survey Creator (builder).
copy survey-creator-core/survey-creator-core.min.css
copy survey-creator-core/survey-creator-core.min.js
copy survey-creator-js/survey-creator-js.min.js

# Analytics dashboard (+ Plotly).
copy survey-analytics/survey.analytics.min.css
copy survey-analytics/survey.analytics.min.js
copy plotly.js-dist-min/plotly.min.js

if [ "$MISSING" != "0" ]; then
  echo "Some bundles were missing. The dist file names may have changed in a" >&2
  echo "newer SurveyJS release; check the installed package versions." >&2
  exit 1
fi

echo "SurveyJS bundles synced to vendor/$DEST"
