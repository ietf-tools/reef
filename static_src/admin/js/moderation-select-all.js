// Copyright The IETF Trust 2026, All Rights Reserved

// The select-all box is added here rather than written into the page, so that a
// browser without scripting has no control that does nothing.
const addSelectAll = (cell) => {
  const table = cell.closest("table");
  const rows = () => table.querySelectorAll("tbody input[type=checkbox][name]");
  const all = document.createElement("input");
  all.type = "checkbox";
  all.setAttribute("aria-label", "Select every row on this page");
  all.addEventListener("change", () => {
    rows().forEach((box) => {
      box.checked = all.checked;
    });
  });
  cell.appendChild(all);
};

document.querySelectorAll("th[data-select-all]").forEach(addSelectAll);
