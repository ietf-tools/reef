// Copyright The IETF Trust 2026, All Rights Reserved
// Mounts the SurveyJS Analytics dashboard over the Reef results API. Config is
// read from the #reef-config JSON block (no inline script, for the CSP).
(function () {
  "use strict";

  var cfgEl = document.getElementById("reef-config");
  if (!cfgEl) {
    return;
  }
  var cfg = JSON.parse(cfgEl.textContent);
  var panelEl = document.getElementById("surveyVizPanel");
  var countEl = document.getElementById("responseCount");

  if (cfg.licenseKey && window.Survey && typeof Survey.setLicenseKey === "function") {
    Survey.setLicenseKey(cfg.licenseKey);
  }

  fetch(cfg.resultsUrl, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("results request failed: " + response.status);
      }
      return response.json();
    })
    .then(function (data) {
      if (countEl) {
        countEl.textContent = data.count;
      }
      var survey = new Survey.Model(data.survey.definition || {});
      var panel = new SurveyAnalytics.VisualizationPanel(
        survey.getAllQuestions(),
        data.results || [],
        { labelTruncateLength: 27, allowHideQuestions: false }
      );
      panel.render(panelEl);
    })
    .catch(function () {
      if (panelEl) {
        panelEl.textContent = "Failed to load results.";
      }
    });
})();
