(() => {
  const grid = document.querySelector(".month-grid");
  if (!grid) return;

  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // --- Popovers: only one open at a time, closeable via Escape or an
  // outside click, and clamped so they never run off the right edge. ---
  const disclosures = () => Array.from(grid.querySelectorAll("details.dot-wrap"));

  const closeAll = (except) => {
    disclosures().forEach((node) => { if (node !== except && node.open) node.open = false; });
  };

  const clampPopover = (details) => {
    const panel = details.querySelector(":scope > .popover, :scope > .extra-dots");
    if (!panel) return;
    panel.classList.remove("align-right");
    const rect = panel.getBoundingClientRect();
    const overflow = rect.right - (window.innerWidth || document.documentElement.clientWidth);
    if (overflow > 0) panel.classList.add("align-right");
  };

  grid.addEventListener("toggle", (event) => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.classList.contains("dot-wrap")) return;
    if (details.open) {
      closeAll(details);
      // Nested "+N" popovers may contain their own dot-wraps; only clamp
      // the disclosure that just opened.
      if (!reducedMotion) requestAnimationFrame(() => clampPopover(details));
      else clampPopover(details);
    }
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && disclosures().some((node) => node.open)) {
      closeAll(null);
    }
  });

  document.addEventListener("click", (event) => {
    const openOnes = disclosures().filter((node) => node.open);
    if (!openOnes.length) return;
    openOnes.forEach((node) => { if (!node.contains(event.target)) node.open = false; });
  });

  // --- Roving-tabindex arrow-key navigation across day cells. ---
  const cells = () => Array.from(grid.querySelectorAll(".day"));
  const COLUMNS = 7;

  const focusCell = (target) => {
    cells().forEach((cell) => cell.setAttribute("tabindex", cell === target ? "0" : "-1"));
    target.focus();
  };

  grid.addEventListener("keydown", (event) => {
    const current = event.target;
    if (!(current instanceof HTMLElement) || !current.classList.contains("day")) return;
    const all = cells();
    const index = all.indexOf(current);
    if (index === -1) return;
    let nextIndex = null;
    switch (event.key) {
      case "ArrowRight": nextIndex = index + 1; break;
      case "ArrowLeft": nextIndex = index - 1; break;
      case "ArrowDown": nextIndex = index + COLUMNS; break;
      case "ArrowUp": nextIndex = index - COLUMNS; break;
      case "Home": nextIndex = index - (index % COLUMNS); break;
      case "End": nextIndex = index - (index % COLUMNS) + (COLUMNS - 1); break;
      default: return;
    }
    if (nextIndex === null || nextIndex < 0 || nextIndex >= all.length) return;
    event.preventDefault();
    focusCell(all[nextIndex]);
  });
})();
