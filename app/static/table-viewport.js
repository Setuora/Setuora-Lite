(function () {
  "use strict";

  // Short and empty tables take only the space they need. Long tables show up
  // to ten real records, with their header and remaining records scrollable.
  const MAX_VISIBLE_RECORDS = 10;
  let scheduled = false;

  function measureTable(table) {
    const wrapper = table.closest(".table-scroll, .access-table-scroll");
    if (!wrapper || !table.getClientRects().length) return;
    const rows = Array.from(table.tBodies).flatMap((body) => Array.from(body.rows));
    const visibleRows = rows.filter((row) => !row.hidden && row.getClientRects().length);
    const bodyHeight = visibleRows
      .slice(0, MAX_VISIBLE_RECORDS)
      .reduce((height, row) => height + row.getBoundingClientRect().height, 0);
    const headerHeight = table.tHead?.getBoundingClientRect().height || 0;
    const captionHeight = table.caption?.getBoundingClientRect().height || 0;
    const footerHeight =
      visibleRows.length <= MAX_VISIBLE_RECORDS
        ? table.tFoot?.getBoundingClientRect().height || 0
        : 0;
    const scrollbarHeight = Math.max(0, wrapper.offsetHeight - wrapper.clientHeight);
    const height = Math.ceil(
      bodyHeight + headerHeight + captionHeight + footerHeight + scrollbarHeight,
    );
    wrapper.classList.add("table-record-viewport");
    wrapper.style.setProperty("--table-record-viewport-height", `${height}px`);

    // Make overflowing tables keyboard-scrollable without first tabbing through
    // every action in the table. Hidden tables are measured when opened.
    const scrolls =
      wrapper.scrollWidth > wrapper.clientWidth + 1 ||
      wrapper.scrollHeight > wrapper.clientHeight + 1;
    wrapper.tabIndex = scrolls ? 0 : -1;
    wrapper.setAttribute("role", "region");
    if (!wrapper.hasAttribute("aria-label") && !wrapper.hasAttribute("aria-labelledby")) {
      const heading = wrapper.closest(".panel")?.querySelector("h2, h3");
      wrapper.setAttribute("aria-label", `${heading?.textContent.trim() || "Data"} table`);
    }
  }

  function update() {
    scheduled = false;
    document.querySelectorAll("table").forEach(measureTable);
  }

  function scheduleUpdate() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(update);
  }

  scheduleUpdate();
  window.addEventListener("resize", scheduleUpdate);
  document.fonts?.ready.then(scheduleUpdate);
  new MutationObserver(scheduleUpdate).observe(document.body, {
    childList: true,
    characterData: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["hidden", "open"],
  });
})();
