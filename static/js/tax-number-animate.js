(function () {
  function prefersReducedMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (_) {
      return false;
    }
  }

  function formatCurrency(value, prefix) {
    const safeValue = Number.isFinite(value) ? Math.round(value) : 0;
    const safePrefix = String(prefix || "");
    return `${safePrefix}${safeValue.toLocaleString("ko-KR")}원`;
  }

  function highlightNode(node) {
    if (!(node instanceof HTMLElement)) return;
    node.style.transition = "color .28s ease, transform .28s ease, text-shadow .28s ease";
    node.style.textShadow = "0 0 0 rgba(37,99,235,0)";
    requestAnimationFrame(() => {
      node.style.color = "#1d4ed8";
      node.style.transform = "translateY(-1px)";
      node.style.textShadow = "0 0 18px rgba(37,99,235,.18)";
    });
    window.setTimeout(() => {
      node.style.color = "";
      node.style.transform = "";
      node.style.textShadow = "";
    }, 950);
  }

  function animateNumber(node) {
    if (!(node instanceof HTMLElement)) return;
    const kind = String(node.dataset.taxAnimate || "currency");
    if (kind !== "currency") return;

    const current = Number(node.dataset.taxCurrentValue || 0);
    const previous = Number(node.dataset.taxPreviousValue || current);
    const changed = String(node.dataset.taxChanged || "0") === "1";
    const prefix = String(node.dataset.taxPrefix || "");

    if (!Number.isFinite(current) || !Number.isFinite(previous)) {
      node.textContent = formatCurrency(current, prefix);
      return;
    }

    if (!changed || current === previous || prefersReducedMotion()) {
      node.textContent = formatCurrency(current, prefix);
      return;
    }

    highlightNode(node);

    const start = performance.now();
    const duration = 820;
    const delta = current - previous;

    function step(now) {
      const elapsed = now - start;
      const progress = Math.min(1, elapsed / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      const value = previous + delta * eased;
      node.textContent = formatCurrency(value, prefix);
      if (progress < 1) {
        window.requestAnimationFrame(step);
      } else {
        node.textContent = formatCurrency(current, prefix);
      }
    }

    window.requestAnimationFrame(step);
  }

  function run() {
    document.querySelectorAll("[data-tax-animate]").forEach(animateNumber);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run, { once: true });
  } else {
    run();
  }
})();
