(() => {
  const formatKrw = (value) => {
    const amount = Number.isFinite(value) ? Math.round(value) : 0;
    const sign = amount < 0 ? "-" : "";
    return `${sign}${new Intl.NumberFormat("ko-KR").format(Math.abs(amount))}원`;
  };

  const renderFinal = (node) => {
    const after = Number(node.dataset.odAfter || 0);
    node.textContent = formatKrw(after);
  };

  const animateNode = (node) => {
    const before = Number(node.dataset.odBefore || 0);
    const after = Number(node.dataset.odAfter || 0);
    const delta = Number(node.dataset.odDelta || 0);
    const shouldAnimate = node.dataset.odAnimate === "1" && delta !== 0;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (!shouldAnimate || reduceMotion) {
      renderFinal(node);
      return;
    }

    const duration = 720;
    const startAt = performance.now();
    const easeOut = (t) => 1 - Math.pow(1 - t, 3);

    const tick = (now) => {
      const progress = Math.min(1, (now - startAt) / duration);
      const eased = easeOut(progress);
      const current = before + (after - before) * eased;
      node.textContent = formatKrw(current);
      if (progress < 1) {
        window.requestAnimationFrame(tick);
      } else {
        renderFinal(node);
      }
    };

    window.requestAnimationFrame(tick);
  };

  const boot = () => {
    document.querySelectorAll("[data-od-animate]").forEach(animateNode);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
