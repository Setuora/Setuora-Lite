(function () {
  "use strict";

  // Inline event handlers are blocked by the Content Security Policy.
  document.addEventListener("submit", function (event) {
    const form = event.target.closest("form[data-confirm]");
    if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
  });

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-print-page]")) window.print();
  });
})();
