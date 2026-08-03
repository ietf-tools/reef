// Copyright The IETF Trust 2026, All Rights Reserved
// Mounts the SurveyJS Creator and saves the definition and theme back to the
// Reef API. Configuration is read from the #reef-config JSON block rendered by
// the template (no inline script, to satisfy the Content Security Policy).
(function () {
  "use strict";

  var cfgEl = document.getElementById("reef-config");
  if (!cfgEl) {
    return;
  }
  var cfg = JSON.parse(cfgEl.textContent);

  if (cfg.licenseKey && window.Survey && typeof Survey.setLicenseKey === "function") {
    Survey.setLicenseKey(cfg.licenseKey);
  }

  var creator = new SurveyCreator.SurveyCreator({
    showThemeTab: true,
    autoSaveEnabled: false,
  });

  if (cfg.definition && Object.keys(cfg.definition).length > 0) {
    creator.JSON = cfg.definition;
  }
  if (cfg.theme) {
    try {
      creator.theme = cfg.theme;
    } catch (e) {
      // Older/newer Creator versions may expose the theme differently; ignore.
    }
  }

  creator.saveSurveyFunc = function (saveNo, callback) {
    var body = { definition: creator.JSON };
    try {
      if (creator.theme) {
        body.theme = creator.theme;
      }
    } catch (e) {
      // no theme available
    }
    fetch(cfg.apiUrl, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": cfg.csrfToken,
      },
      credentials: "same-origin",
      body: JSON.stringify(body),
    })
      .then(function (response) {
        callback(saveNo, response.ok);
      })
      .catch(function () {
        callback(saveNo, false);
      });
  };

  creator.render(document.getElementById("surveyCreator"));
})();
