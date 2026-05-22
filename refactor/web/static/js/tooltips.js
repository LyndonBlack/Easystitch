let tooltipTimer = null;
let tooltipEl = null;
let tooltipTarget = null;

function hideHoverTooltip() {
  if (tooltipTimer) {
    clearTimeout(tooltipTimer);
    tooltipTimer = null;
  }
  tooltipTarget = null;
  if (tooltipEl) {
    tooltipEl.classList.remove('show');
    tooltipEl.remove();
    tooltipEl = null;
  }
}

function showHoverTooltip(target, ev) {
  const msg = target?.getAttribute('data-tooltip');
  if (!msg) return;

  hideHoverTooltip();
  tooltipTarget = target;
  tooltipTimer = setTimeout(() => {
    if (tooltipTarget !== target) return;
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'hover-tooltip';
    tooltipEl.textContent = msg;
    document.body.appendChild(tooltipEl);

    const margin = 14;
    const rect = tooltipEl.getBoundingClientRect();
    let x = (ev?.clientX || window.innerWidth / 2) + 14;
    let y = (ev?.clientY || window.innerHeight / 2) + 14;
    if (x + rect.width + margin > window.innerWidth) x = window.innerWidth - rect.width - margin;
    if (y + rect.height + margin > window.innerHeight) y = window.innerHeight - rect.height - margin;
    if (x < margin) x = margin;
    if (y < margin) y = margin;

    tooltipEl.style.left = x + 'px';
    tooltipEl.style.top = y + 'px';
    requestAnimationFrame(() => tooltipEl && tooltipEl.classList.add('show'));
  }, 1500);
}

function initHoverTooltips() {
  document.addEventListener('mouseover', ev => {
    const target = ev.target.closest?.('[data-tooltip]');
    if (target) showHoverTooltip(target, ev);
  });
  document.addEventListener('mouseout', ev => {
    if (ev.target.closest?.('[data-tooltip]')) hideHoverTooltip();
  });
  document.addEventListener('click', hideHoverTooltip, true);
  document.addEventListener('keydown', hideHoverTooltip, true);
}
