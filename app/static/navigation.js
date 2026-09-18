(function () {
  "use strict";

  const header = document.querySelector(".global-nav");
  if (!header) return;
  const nav = header.querySelector(".subnav");
  const toggle = header.querySelector(".nav-toggle");
  const menus = Array.from(header.querySelectorAll("details.nav-menu"));
  const profile = header.querySelector("details.profile-menu");
  let scheduled = false;

  function closeNavigation() {
    header.classList.remove("nav-open");
    toggle.setAttribute("aria-expanded", "false");
    menus.forEach((menu) => {
      menu.open = false;
    });
  }

  function positionMenu(menu) {
    if (!menu.open) return;
    const panel = menu.querySelector(".nav-menu__panel");
    if (header.classList.contains("nav-collapsed")) {
      panel.style.removeProperty("top");
      panel.style.removeProperty("left");
      panel.style.removeProperty("max-height");
      return;
    }
    const summary = menu.querySelector("summary").getBoundingClientRect();
    const container = nav.querySelector(".subnav__inner").getBoundingClientRect();
    const panelWidth = panel.getBoundingClientRect().width;
    const left = Math.max(12, Math.min(summary.left, window.innerWidth - panelWidth - 12));
    panel.style.top = `${summary.bottom - container.top + 8}px`;
    panel.style.left = `${left - container.left}px`;
    panel.style.maxHeight = `${Math.max(100, window.innerHeight - summary.bottom - 24)}px`;
  }

  function updateLayout() {
    scheduled = false;
    header.classList.add("nav-ready");
    header.classList.remove("nav-collapsed", "nav-stacked");
    const crowded = nav.scrollWidth > nav.clientWidth + 1;
    const collapsed = window.innerWidth < 768 || (crowded && window.innerWidth < 1024);
    header.classList.toggle("nav-collapsed", collapsed);
    header.classList.toggle("nav-stacked", crowded && !collapsed);
    if (!collapsed) closeNavigation();
    menus.forEach(positionMenu);
  }

  function scheduleLayout() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(updateLayout);
  }

  toggle.addEventListener("click", () => {
    const open = header.classList.toggle("nav-open");
    toggle.setAttribute("aria-expanded", String(open));
    if (profile) profile.open = false;
    if (!open)
      menus.forEach((menu) => {
        menu.open = false;
      });
  });
  menus.forEach((menu) =>
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      if (profile) profile.open = false;
      menus.forEach((other) => {
        if (other !== menu) other.open = false;
      });
      requestAnimationFrame(() => positionMenu(menu));
    }),
  );
  profile?.addEventListener("toggle", () => {
    if (profile.open) closeNavigation();
  });
  document.addEventListener("click", (event) => {
    menus.forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
    if (profile && !profile.contains(event.target)) profile.open = false;
    if (!header.contains(event.target)) closeNavigation();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openMenu = menus.find((menu) => menu.open) || (profile?.open ? profile : null);
    if (openMenu) {
      openMenu.open = false;
      openMenu.querySelector("summary").focus();
    } else if (header.classList.contains("nav-open")) {
      closeNavigation();
      toggle.focus();
    }
  });
  nav.querySelectorAll("a.active").forEach((link) => link.setAttribute("aria-current", "page"));
  window.addEventListener("resize", scheduleLayout);
  window.addEventListener("scroll", () => menus.forEach(positionMenu), true);
  document.fonts?.ready.then(scheduleLayout);
  updateLayout();
})();
