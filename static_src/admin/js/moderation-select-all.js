// Copyright The IETF Trust 2026, All Rights Reserved

// The select-all box is added here rather than written into the page, so that a
// browser without scripting has no control that does nothing.
const addSelectAll = (cell) => {
  const table = cell.closest("table");
  const rows = () => [...table.querySelectorAll("tbody input[type=checkbox][name]")];
  const all = document.createElement("input");
  all.type = "checkbox";
  all.setAttribute("aria-label", "Select all rows in this table");

  // Some rows ticked is neither "all" nor "none", and the box has to say so.
  const syncWithRows = () => {
    const boxes = rows();
    const ticked = boxes.filter((box) => box.checked).length;
    all.checked = ticked > 0 && ticked === boxes.length;
    all.indeterminate = ticked > 0 && ticked < boxes.length;
  };

  all.addEventListener("change", () => {
    rows().forEach((box) => {
      box.checked = all.checked;
    });
    all.indeterminate = false;
  });
  table.addEventListener("change", (event) => {
    if (event.target !== all) syncWithRows();
  });
  cell.appendChild(all);
  // A browser restoring the page keeps the rows' ticks, so start from them.
  syncWithRows();
};

document.querySelectorAll("th[data-select-all]").forEach(addSelectAll);
