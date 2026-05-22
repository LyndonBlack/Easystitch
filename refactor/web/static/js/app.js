// EasyStitch frontend app — extracted from index.html
let currentImageLoaded = false;
let lastPrep = null;
let lastTrace = null;
let structureLoaded = false;
let structureSvgW = 500;
let structureSvgH = 500;
let structureSourcePaths = [];
let structureObjects = [];
let structureSelectedId = null;
let structureCheckedIds = new Set();
let structureGroupCounter = 1;
let structureSplitMode = false;
let structureJunctionMode = false;
let structureActiveTool = 'select';
let structureObjectListCollapsed = true;
let structureSplitTargetReady = false;
let structureCutPoints = [];
let structureJunctionPoints = [];
let structureHoverPoint = null;
let structureNeedSecondCut = false;
let stitchLoaded = false;
let stitchObjects = [];
let stitchAssignments = {};
let stitchSelectedId = null;
let stitchCheckedIds = new Set();
let structureCollapsedGroups = new Set();
let stitchCollapsedGroups = new Set();
let stitchSortMode = 'number';
let stitchObjectListCollapsed = true;
let stitchManualRungs = {};
let currentStitchPlan = null;
let currentExportDebug = null;
let currentStitchPreview = null;
let stitchPlanPlayIndex = 0;
let stitchPlanPlayTimer = null;
let previewLayerMode = 'both';
let manualRungMode = false;
let pendingManualRungPoint = null;
let draggingManualRung = null;
let workZoom = 1.0;
let designTargetLongestMm = null;
let traceAutoRunOnce = false;


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


async function init() {
  initHoverTooltips();
  const res = await fetch('/api/state');
  const data = await res.json();
  if (data.has_image) {
    currentImageLoaded = true;
    document.getElementById('status').textContent = data.input_name;
    toast('Image loaded from command line');
  }
}

function showPane(n) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('pane' + n).classList.add('active');
  document.querySelectorAll('.step').forEach((s, i) => s.classList.toggle('active', i === n - 1));

  if (n === 2 && lastPrep && !lastTrace && !traceAutoRunOnce) {
    traceAutoRunOnce = true;
    setTimeout(() => runTrace(), 80);
  }
  if (n === 3 && !structureLoaded && lastTrace) {
    loadStructure();
  }
  if (n === 4 && !stitchLoaded && structureObjects.length) {
    loadStitchPane();
  }
  setWorkZoom(workZoom);
}
function showDisabled() {
  toast('This pane is a placeholder in this build');
}

function syncVal(inputId, valId, suffix) {
  const v = document.getElementById(inputId).value;
  document.getElementById(valId).textContent = v + suffix;
}

function syncFloatVal(inputId, valId, suffix) {
  const v = parseFloat(document.getElementById(inputId).value);
  document.getElementById(valId).textContent = v.toFixed(2).replace(/0$/, '').replace(/\.0$/, '.0') + suffix;
}

function syncPosterizeVal() {
  const v = parseInt(document.getElementById('posterize-bits').value, 10);
  document.getElementById('posterize-val').textContent = v === 0 ? 'off' : v + ' bit';
}

let previewDarkBackgrounds = new Set();

function applyPreviewBgState(id) {
  const el = document.getElementById(id);
  if (!el) return;

  const dark = previewDarkBackgrounds.has(id);
  el.classList.toggle('preview-dark', dark);
  el.classList.toggle('preview-light', !dark);

  // Force inline styles too because some preview SVGs and checkerboard
  // backgrounds are created dynamically and can override the class.
  const bg = dark ? '#111827' : '';
  el.style.backgroundColor = bg;

  const svgs = el.querySelectorAll('svg');
  svgs.forEach(svg => {
    svg.style.backgroundColor = dark ? '#111827' : '';
    svg.style.backgroundImage = dark
      ? 'linear-gradient(45deg,#1f2937 25%,transparent 25%),linear-gradient(-45deg,#1f2937 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#1f2937 75%),linear-gradient(-45deg,transparent 75%,#1f2937 75%)'
      : '';
    svg.style.backgroundSize = dark ? '24px 24px' : '';
    svg.style.backgroundPosition = dark ? '0 0,0 12px,12px -12px,-12px 0' : '';
  });

  const parent = el.parentElement;
  if (parent) parent.style.position = 'relative';

  const btn = parent ? parent.querySelector(`.preview-bg-toggle[data-target="${id}"]`) : null;
  if (btn) btn.textContent = dark ? 'Light BG' : 'Dark BG';
}

function togglePreviewBg(id) {
  const el = document.getElementById(id);
  if (!el) return;
  if (previewDarkBackgrounds.has(id)) previewDarkBackgrounds.delete(id);
  else previewDarkBackgrounds.add(id);
  applyPreviewBgState(id);
}

function restorePreviewBg(id) {
  applyPreviewBgState(id);
}

function ensurePreviewBgButtons() {
  const ids = ['prep-preview', 'trace-preview', 'structure-preview', 'stitch-preview'];
  ids.forEach((id) => {
    const preview = document.getElementById(id);
    if (!preview) return;

    const parent = preview.parentElement || preview;
    parent.classList.add('work-card');
    parent.style.position = 'relative';

    const existing = parent.querySelector(`.preview-bg-toggle[data-target="${id}"]`);
    if (existing) return;

    const btn = document.createElement('button');
    btn.className = 'preview-bg-toggle';
    btn.dataset.target = id;
    btn.type = 'button';
    btn.textContent = previewDarkBackgrounds.has(id) ? 'Light BG' : 'Dark BG';
    btn.title = 'Toggle preview background';
    btn.style.top = '48px';
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      togglePreviewBg(id);
    });

    parent.appendChild(btn);
    restorePreviewBg(id);
  });
}



function setWorkZoom(z) {
  workZoom = Math.max(0.25, Math.min(5.0, z));
  document.querySelectorAll('.preview-img-wrap img, .preview-img-wrap svg').forEach(el => {
    el.style.transform = 'scale(' + workZoom + ')';
    el.style.transformOrigin = 'center center';
  });
  const msg = 'Work-area zoom: ' + Math.round(workZoom * 100) + '%. Shift + scroll over the preview to zoom.';
  const f1 = document.getElementById('footer-msg');
  if (f1) f1.textContent = msg;
  const f2 = document.getElementById('trace-footer-msg');
  if (f2 && lastTrace) f2.textContent = 'Trace complete. ' + msg;
  const f3 = document.getElementById('structure-footer-msg');
  if (f3) f3.textContent = 'Tool workflow: Select cycles assignment on second click; Fill/Satin/Skip tools paint paths; Manual split stays active until another tool is selected. ' + msg;
  const f4 = document.getElementById('stitch-footer-msg');
  if (f4) f4.textContent = 'Fill = normal colour region, Satin = highlighted column candidate, Skip = faded object. ' + msg;
  updateDesignSizeInfo();
}

async function uploadImage() {
  const input = document.getElementById('file-input');
  if (!input.files.length) {
    toast('Choose an image first');
    return;
  }
  const fd = new FormData();
  fd.append('image', input.files[0]);
  const res = await fetch('/api/upload', {method:'POST', body:fd});
  const data = await res.json();
  if (data.ok) {
    currentImageLoaded = true;
    document.getElementById('status').textContent = data.name;
    document.getElementById('meta').innerHTML = 'Loaded: <b>' + data.name + '</b><br>Now run Image Prep.';
    document.getElementById('palette').innerHTML = '';
    document.getElementById('orig-preview').innerHTML = '<span style="color:#555">Run prep to preview</span>';
    document.getElementById('prep-preview').innerHTML = '<span style="color:#555">Run prep to preview</span>';
    toast('Image uploaded');
  } else {
    toast('Upload failed: ' + (data.error || 'unknown'), 5000);
  }
}

async function runPrep() {
  if (!currentImageLoaded) {
    toast('Load an image first');
    return;
  }
  const btn = document.getElementById('prep-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Preparing…';

  const body = {
    colors: parseInt(document.getElementById('colors').value, 10),
    max_size: parseInt(document.getElementById('max-size').value, 10),
    simplify_preset: document.getElementById('simplify-preset').value,
    smoothing: parseInt(document.getElementById('smoothing').value, 10),
    posterize_bits: parseInt(document.getElementById('posterize-bits')?.value || '0', 10),
    color_boost: parseFloat(document.getElementById('color-boost').value),
    contrast_boost: parseFloat(document.getElementById('contrast-boost').value)
  };

  try {
    const res = await fetch('/api/prep', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Prep failed: ' + (data.error || 'unknown'), 6000);
      console.error(data.trace);
    } else {
      lastPrep = data;
      lastTrace = null;
      traceAutoRunOnce = false;
      renderPrep(data);
      toast('Prepared image saved');
    }
  } catch(e) {
    toast('Prep error: ' + e, 6000);
  }
  btn.disabled = false;
  btn.textContent = 'Run Image Prep';
}

function renderPrep(data) {
  document.getElementById('status').textContent = data.stem + '_prepared.png';
  document.getElementById('orig-size').textContent = data.original_width + '×' + data.original_height;
  document.getElementById('prep-size').textContent = data.processed_width + '×' + data.processed_height;

  document.getElementById('orig-preview').innerHTML =
    '<img src="' + data.original_preview + '" alt="original preview">';
  document.getElementById('prep-preview').innerHTML =
    '<img src="' + data.prepared_preview + '" alt="prepared preview">';

  document.getElementById('meta').innerHTML = `
    Source: <b>${data.input_path}</b><br>
    Prepared: <b>${data.output_path}</b><br>
    Original: <b>${data.original_width}×${data.original_height}</b> ${data.original_mode}<br>
    Processed: <b>${data.processed_width}×${data.processed_height}</b> ${data.resized ? '(resized)' : '(not resized)'}<br>
    Colours: <b>${data.colors_requested}</b><br>
    Simplify: <b>${data.simplify_preset}</b>, smoothing <b>${data.smoothing}</b>, posterize <b>${data.posterize_bits || 'off'}</b><br>
    Colour/contrast: <b>${data.color_boost}×</b> / <b>${data.contrast_boost}×</b><br>
    Time: <b>${data.time_sec}s</b>
  `;

  const pal = document.getElementById('palette');
  pal.innerHTML = '';
  data.palette.forEach((p, i) => {
    const row = document.createElement('div');
    row.className = 'swatch-row';
    row.innerHTML = `
      <div class="swatch" style="background:${p.hex}"></div>
      <div>${i + 1}. ${p.hex}</div>
      <div class="bar"><div style="width:${Math.max(2, p.percent)}%"></div></div>
      <div>${p.percent.toFixed(1)}%</div>
    `;
    pal.appendChild(row);
  });

  document.getElementById('footer-msg').textContent =
    'Prepared PNG is ready. Move to Pane 2 — Trace.';
  document.getElementById('trace-meta').innerHTML = 'Prepared source: <b>' + data.output_path + '</b><br>Ready to trace into fill regions and stroke candidates.';
}

async function runTrace() {
  if (!lastPrep || !lastPrep.output_path) {
    toast('Run Image Prep first');
    showPane(1);
    return;
  }

  const btn = document.getElementById('trace-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Tracing…';

  const body = {
    speckle: parseInt(document.getElementById('trace-speckle').value, 10),
    mode: document.getElementById('trace-mode').value,
    hierarchical: document.getElementById('trace-hierarchical').value,
    gradient_step: parseInt(document.getElementById('trace-gradient-step').value, 10),
    segment_length: parseFloat(document.getElementById('trace-segment-length').value),
    color_precision: parseInt(document.getElementById('trace-color-precision').value, 10),
    corner_threshold: parseInt(document.getElementById('trace-corner-threshold').value, 10),
    splice_threshold: parseInt(document.getElementById('trace-splice-threshold').value, 10),
    path_precision: parseInt(document.getElementById('trace-path-precision').value, 10)
  };

  try {
    const res = await fetch('/api/trace', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Trace failed: ' + (data.error || 'unknown'), 7000);
      console.error(data.trace);
    } else {
      lastTrace = data;
      renderTrace(data);
      toast('SVG trace generated');
    }
  } catch(e) {
    toast('Trace error: ' + e, 7000);
  }

  btn.disabled = false;
  btn.textContent = 'Run Trace';
}

function renderTrace(data) {
  const preview = document.getElementById('trace-preview');
  preview.innerHTML = data.svg_text;
  const svg = preview.querySelector('svg');
  if (svg) {
    svg.style.maxWidth = '100%';
    svg.style.maxHeight = '75vh';
    svg.style.width = 'auto';
    svg.style.height = 'auto';
    svg.style.background = 'transparent';
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.style.transformOrigin = 'center center';
    svg.querySelectorAll('path').forEach(p => {
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('clip-rule', 'evenodd');
    });
  }

  setWorkZoom(workZoom);

  document.getElementById('trace-count').textContent = data.path_count + ' fill paths';
  document.getElementById('trace-meta').innerHTML = `
    SVG: <b>${data.output_path}</b><br>
    Fill-region paths: <b>${data.path_count}</b><br>
    SVG size: <b>${data.svg_kb} KB</b><br>
    Trace time: <b>${data.time_sec}s</b><br>
    Mode: <b>${data.settings.mode}</b>, layering <b>${data.settings.hierarchical}</b><br>
    Speckle: <b>${data.settings.speckle}px</b>, segment length <b>${data.settings.segment_length}px</b><br>
    vtracer: <b>${data.vtracer}</b>
  `;
  document.getElementById('trace-footer-msg').textContent =
    'Trace complete. Pane 2 created fill-region SVG paths ready for Pane 3.';
}

function invalidateStitchPane() {
  stitchLoaded = false;
  stitchObjects = [];
  // Pane 3 now owns stitchAssignments. Do not clear them when structure changes;
  // this function only invalidates Pane 4's derived object list/preview/plan.
  stitchSelectedId = null;
  stitchCheckedIds = new Set();
  stitchCollapsedGroups = new Set();
  stitchManualRungs = {};
  currentStitchPlan = null;
  currentStitchPreview = null;
  stitchPlanPlayIndex = 0;
  if (stitchPlanPlayTimer) { clearInterval(stitchPlanPlayTimer); stitchPlanPlayTimer = null; }
  previewLayerMode = 'both';
  manualRungMode = false;
  pendingManualRungPoint = null;
  draggingManualRung = null;
  const count = document.getElementById('stitch-count');
  const list = document.getElementById('stitch-list');
  const prev = document.getElementById('stitch-preview');
  const detail = document.getElementById('stitch-detail');
  const summary = document.getElementById('stitch-summary');
  if (count) count.textContent = '0';
  if (list) list.innerHTML = '';
  if (prev) prev.innerHTML = '<span style="color:#555">Prepared structure changed; reload Pane 4</span>';
  if (detail) detail.innerHTML = 'Load prepared structure to begin.';
  if (summary) summary.innerHTML = 'No assignments yet.';
}


function initStructureAssignments(reset=false) {
  if (!structureObjects.length) return;
  if (reset) stitchAssignments = {};
  structureObjects.forEach(o => {
    if (!stitchAssignments[o.id]) stitchAssignments[o.id] = defaultStitchType(o);
  });
  Object.keys(stitchAssignments).forEach(id => {
    if (!structureObjects.some(o => o.id === id) && !stitchObjects.some(o => o.id === id)) {
      delete stitchAssignments[id];
    }
  });
  updateStructureStitchSummary();
}

function currentAssignmentObjects() {
  const pane3 = document.getElementById('pane3')?.classList.contains('active');
  if (pane3 && structureLoaded) return structureSelectedObjects();
  if (stitchLoaded) return stitchSelectedObjects();
  if (structureLoaded) return structureSelectedObjects();
  return [];
}

function currentAssignmentObjectList() {
  const pane3 = document.getElementById('pane3')?.classList.contains('active');
  if (pane3 && structureLoaded) return structureObjects;
  if (stitchLoaded) return stitchObjects;
  if (structureLoaded) return structureObjects;
  return [];
}

function currentAssignmentSelectedColour() {
  const objs = currentAssignmentObjects();
  return objs.length ? objs[0].color : null;
}

function refreshAssignmentViews() {
  currentStitchPlan = null;
  currentStitchPreview = null;
  currentExportDebug = null;
  if (structureLoaded) {
    renderStructureList();
    renderStructurePreview();
    updateStructureDetail();
    updateStructureStitchSummary();
  }
  if (stitchLoaded) {
    renderStitchList();
    renderStitchPreview();
    updateStitchDetail();
    updateStitchSummary();
  }
}

function updateStructureStitchSummary() {
  const summary = document.getElementById('structure-stitch-summary');
  if (!summary) return;
  if (!structureObjects.length) {
    summary.innerHTML = 'No stitch assignments yet.';
    return;
  }
  const counts = {fill:0, satin:0, skip:0};
  structureObjects.forEach(o => {
    const st = stitchAssignments[o.id] || defaultStitchType(o);
    counts[st] = (counts[st] || 0) + 1;
  });
  summary.innerHTML = `Fill: <b>${counts.fill}</b> · Satin: <b>${counts.satin}</b> · Skip: <b>${counts.skip}</b><br>Total objects: <b>${structureObjects.length}</b>`;
}

function assignmentLuminance(hex) {
  const h = (hex || '#000000').replace('#', '');
  if (h.length !== 6) return 0;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function appendAssignmentDefs(svg) {
  if (!svg || svg.querySelector('#structureFillHatchDark')) return;
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML = `
    <pattern id="structureFillHatchDark" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(255,255,255,0.65)" stroke-width="1"/>
    </pattern>
    <pattern id="structureFillHatchLight" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(0,0,0,0.55)" stroke-width="1"/>
    </pattern>
  `;
  svg.appendChild(defs);
}

function appendStructureAssignmentHatch(svg, obj, st) {
  if (st !== 'fill') return;
  const hatch = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  hatch.setAttribute('d', obj.d);
  if (obj.tx || obj.ty) hatch.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
  hatch.setAttribute('fill-rule', 'evenodd');
  hatch.setAttribute('clip-rule', 'evenodd');
  hatch.setAttribute('fill', assignmentLuminance(obj.color) > 160 ? 'url(#structureFillHatchLight)' : 'url(#structureFillHatchDark)');
  hatch.setAttribute('pointer-events', 'none');
  svg.appendChild(hatch);
}


function applyStructureObjectPanelState() {
  const panel = document.getElementById('structure-object-panel');
  if (!panel) return;
  const expanded = !structureObjectListCollapsed;
  panel.style.width = expanded ? '340px' : '42px';
  document.querySelectorAll('.structure-list-expanded-only').forEach(el => {
    el.style.display = expanded ? '' : 'none';
  });
  const btn = document.getElementById('structure-object-panel-toggle');
  if (btn) {
    btn.textContent = expanded ? '×' : '☰';
    btn.title = expanded ? 'Collapse embroidery object list' : 'Expand embroidery object list';
  }
}

function toggleStructureObjectPanel() {
  structureObjectListCollapsed = !structureObjectListCollapsed;
  applyStructureObjectPanelState();
  setWorkZoom(workZoom);
}

function setToolButtonActive(id, on) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.classList.toggle('primary', !!on);
}

function updateStructureToolButtons() {
  setToolButtonActive('tool-select-btn', structureActiveTool === 'select');
  setToolButtonActive('manual-split-btn', structureActiveTool === 'manual_split');
  setToolButtonActive('junction-cut-btn', structureActiveTool === 'junction');
  setToolButtonActive('assign-fill-tool-btn', structureActiveTool === 'assign_fill');
  setToolButtonActive('assign-satin-tool-btn', structureActiveTool === 'assign_satin');
  setToolButtonActive('assign-skip-tool-btn', structureActiveTool === 'assign_skip');

  const splitBtn = document.getElementById('manual-split-btn');
  if (splitBtn) splitBtn.textContent = structureActiveTool === 'manual_split' ? 'Manual split: active' : 'Manual split tool';
  const junctionBtn = document.getElementById('junction-cut-btn');
  if (junctionBtn) junctionBtn.textContent = structureActiveTool === 'junction' ? 'Junction cut: active' : 'Junction cut tool';
}

function setStructureTool(tool) {
  if (!structureLoaded && tool !== 'select') {
    toast('Load a traced SVG first');
    return;
  }
  if ((tool === 'manual_split' || tool === 'junction') && structureSelectedObjects().length > 1) {
    toast('Cut tools work on one object at a time. Clear multi-selection/checkmarks first.');
    return;
  }

  structureActiveTool = tool || 'select';
  structureSplitMode = structureActiveTool === 'manual_split';
  structureJunctionMode = structureActiveTool === 'junction';
  structureSplitTargetReady = false;
  structureCutPoints = [];
  structureJunctionPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;

  updateStructureToolButtons();
  renderStructurePreview();
  updateStructureDetail();

  const labels = {
    select: 'Select tool: click an already selected object again to cycle Fill → Satin → Skip',
    manual_split: 'Manual split tool active: first click chooses the path, next two clicks place the cut. Tool stays active after each cut.',
    junction: 'Junction cut tool active: click centre, click each branch, then double-click or Apply.',
    assign_fill: 'Fill tool active: click paths to mark Fill',
    assign_satin: 'Satin tool active: click paths to mark Satin',
    assign_skip: 'Skip tool active: click paths to mark Skip'
  };
  toast(labels[structureActiveTool] || 'Tool selected');
}

function currentAssignToolType() {
  if (structureActiveTool === 'assign_fill') return 'fill';
  if (structureActiveTool === 'assign_satin') return 'satin';
  if (structureActiveTool === 'assign_skip') return 'skip';
  return null;
}

function applyStructureAssignmentClick(obj, shiftKey=false) {
  if (!obj) return;
  if (shiftKey) {
    toggleStructureChecked(obj.id);
    structureSelectedId = obj.id;
    return;
  }

  const toolType = currentAssignToolType();
  if (toolType) {
    stitchAssignments[obj.id] = toolType;
    structureSelectedId = obj.id;
    structureCheckedIds.clear();
    refreshAssignmentViews();
    toast('Assigned ' + toolType + ' to ' + (obj.label || obj.id));
    return;
  }

  const noMulti = structureCheckedIds.size === 0;
  if (structureActiveTool === 'select' && obj.id === structureSelectedId && noMulti) {
    stitchAssignments[obj.id] = cycleStitchType(stitchAssignments[obj.id] || defaultStitchType(obj));
    refreshAssignmentViews();
    toast('Changed selected object to ' + (stitchAssignments[obj.id] || 'fill'));
    return;
  }

  selectStructureObject(obj.id);
}

async function loadStructure() {
  if (!lastTrace || !lastTrace.output_path) {
    toast('Run Trace first');
    showPane(2);
    return;
  }
  try {
    const res = await fetch('/api/structure/load');
    const data = await res.json();
    if (!data.ok) {
      toast('Structure load failed: ' + (data.error || 'unknown'), 7000);
      console.error(data.trace);
      return;
    }
    structureLoaded = true;
    structureSvgW = data.svg_w;
    structureSvgH = data.svg_h;
    structureSourcePaths = data.source_paths || [];
    structureObjects = (data.objects || []).sort((a,b) => (a.order||0) - (b.order||0));
    structureSelectedId = structureObjects.length ? structureObjects[0].id : null;
    structureCheckedIds = new Set();

    // Pane 3 owns Fill/Satin/Skip assignment. On the first load/current-trace
    // load, run Auto Guess once. Returning from Pane 4 does not reload Pane 3,
    // so manual edits are preserved.
    stitchAssignments = {};
    const structureAutoResult = autoAssignImproved(structureObjects);
    Object.keys(structureAutoResult.assignments || {}).forEach(id => {
      stitchAssignments[id] = structureAutoResult.assignments[id];
    });

    updateStructureToolButtons();
    renderStructureList();
    renderStructurePreview();
  ensurePreviewBgButtons();
    updateStructureDetail();
    invalidateStitchPane();
    toast(
      'Path structure loaded' +
      (structureAutoResult.edgeSkipped ? ` · edge/background skipped ${structureAutoResult.edgeSkipped}` : '') +
      (structureAutoResult.columnsConverted ? ` · columns to satin ${structureAutoResult.columnsConverted}` : '') +
      (structureAutoResult.enclosedLargeRegionsForcedFill ? ` · enclosed large regions to fill ${structureAutoResult.enclosedLargeRegionsForcedFill} @ ${Math.round(autoAssignEnclosedFillThreshold() * 100)}%` : '')
    );
  } catch (e) {
    toast('Structure error: ' + e, 7000);
  }
}


function objectGroupKey(obj) {
  return obj.group_id || ('src_' + obj.source_id);
}

function buildGroupedObjectRows(objects) {
  const groups = new Map();
  objects.slice().sort((a,b) => (a.order||0) - (b.order||0)).forEach(obj => {
    const key = objectGroupKey(obj);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(obj);
  });
  return Array.from(groups.entries()).map(([groupId, members]) => {
    members.sort((a,b) => (a.order||0) - (b.order||0));
    return {groupId, members, first: members[0]};
  }).sort((a,b) => (a.first.order||0) - (b.first.order||0));
}

function groupParentLabel(members) {
  if (!members.length) return 'Group';
  const sourceSet = [...new Set(members.map(o => o.display_index))].sort((a,b) => a - b);
  if (sourceSet.length === 1) return 'Path ' + sourceSet[0];
  return 'Group: Paths ' + sourceSet.join(', ');
}

function childLetter(index) {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz';
  if (index < alphabet.length) return alphabet[index];
  return 'p' + (index + 1);
}

function childLabel(parentLabel, index) {
  return parentLabel + childLetter(index);
}

function groupCheckedState(members, checkedSet) {
  const checked = members.filter(o => checkedSet.has(o.id)).length;
  if (checked === 0) return '';
  if (checked === members.length) return 'checked';
  return 'indeterminate';
}

function setCheckboxIndeterminate(input, state) {
  if (state === 'indeterminate') input.indeterminate = true;
}

function structureSelectedObjects() {
  const ids = structureCheckedIds.size ? Array.from(structureCheckedIds) : (structureSelectedId ? [structureSelectedId] : []);
  return ids.map(id => structureObjects.find(o => o.id === id)).filter(Boolean);
}

function renderStructureList() {
  const list = document.getElementById('structure-list');
  const meta = document.getElementById('structure-count');
  applyStructureObjectPanelState();
  list.innerHTML = '';
  meta.textContent = structureObjects.length + ' object' + (structureObjects.length === 1 ? '' : 's');

  buildGroupedObjectRows(structureObjects).forEach(group => {
    const members = group.members;
    const parentLabel = groupParentLabel(members);
    const isGrouped = members.length > 1;
    const groupState = groupCheckedState(members, structureCheckedIds);
    const groupSelected = members.some(o => o.id === structureSelectedId || structureCheckedIds.has(o.id));
    const collapsed = structureCollapsedGroups.has(group.groupId);

    if (isGrouped) {
      const parent = document.createElement('div');
      parent.className = 'obj-row obj-parent' + (groupSelected ? ' sel' : '');
      parent.onclick = (ev) => {
        if (ev.shiftKey) toggleStructureGroup(group.groupId);
        else selectStructureGroup(group.groupId);
      };
      parent.innerHTML = `
        <button class="collapse-toggle" title="${collapsed ? 'Expand group' : 'Collapse group'}" onclick="event.stopPropagation();toggleStructureGroupCollapse('${group.groupId}')">${collapsed ? '+' : '−'}</button>
        <input type="checkbox" ${groupState === 'checked' ? 'checked' : ''} onclick="event.stopPropagation();toggleStructureGroup('${group.groupId}')">
        <div class="obj-swatch" style="background:${members[0].color}"></div>
        <div class="obj-info">
          <div class="obj-name">${parentLabel}</div>
          <div class="obj-meta">${members.length} grouped child paths · group ${group.groupId}</div>
        </div>
        <span class="obj-group-count">${members.length} parts</span>
      `;
      const cb = parent.querySelector('input[type=checkbox]');
      setCheckboxIndeterminate(cb, groupState);
      list.appendChild(parent);
    }

    members.forEach((obj, idx) => {
      const row = document.createElement('div');
      row.className = 'obj-row' + (isGrouped ? ' obj-child' : '') + (obj.id === structureSelectedId ? ' sel' : '') + ((isGrouped && collapsed) ? ' hidden-child' : '');
      row.onclick = (ev) => {
        if (ev.shiftKey) {
          toggleStructureChecked(obj.id);
          structureSelectedId = obj.id;
        } else {
          selectStructureObject(obj.id);
        }
      };
      const displayName = isGrouped ? childLabel(parentLabel, idx) : obj.label;
      row.innerHTML = `
        ${!isGrouped ? '<span style="width:22px;display:inline-block"></span>' : ''}
        <input type="checkbox" ${structureCheckedIds.has(obj.id) ? 'checked' : ''} onclick="event.stopPropagation();toggleStructureChecked('${obj.id}')">
        <div class="obj-swatch" style="background:${obj.color}"></div>
        <div class="obj-info">
          <div class="obj-name">${displayName} <span style="color:#8f96b3;font-size:.72rem">${obj.color}</span></div>
          <div class="obj-meta">source path ${obj.display_index} · ${obj.source_kind || 'object'} · group ${obj.group_id} · ratio ${obj.elongation}<br>${obj.prep_note || 'working object'}</div>
        </div>
        <span class="obj-badge">${isGrouped ? childLetter(idx) : obj.group_id}</span>
      `;
      list.appendChild(row);
    });
  });
}

function selectStructureObject(id) {
  structureSelectedId = id;
  structureCheckedIds.clear();
  structureSplitTargetReady = structureSplitMode;
  structureCutPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  if (structureSplitMode) toast('Manual split target selected: now place first cut point');
}

function toggleStructureChecked(id) {
  if (structureCheckedIds.has(id)) structureCheckedIds.delete(id);
  else structureCheckedIds.add(id);
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
}

function selectStructureGroup(groupId) {
  const members = structureObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  structureSelectedId = members[0].id;
  structureCheckedIds = new Set(members.map(o => o.id));
  structureCutPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
}

function toggleStructureGroup(groupId) {
  const members = structureObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  const allChecked = members.every(o => structureCheckedIds.has(o.id));
  if (allChecked) members.forEach(o => structureCheckedIds.delete(o.id));
  else members.forEach(o => structureCheckedIds.add(o.id));
  structureSelectedId = members[0].id;
  structureCutPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
}

function toggleStructureGroupCollapse(groupId) {
  if (structureCollapsedGroups.has(groupId)) structureCollapsedGroups.delete(groupId);
  else structureCollapsedGroups.add(groupId);
  renderStructureList();
}

function structureSvgMarkup() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function renderStructurePreview() {
  const wrap = document.getElementById('structure-preview');
  if (!structureObjects.length) {
    wrap.innerHTML = '<span style="color:#555">No traced SVG loaded</span>';
    return;
  }
  wrap.innerHTML = structureSvgMarkup();
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  bg.setAttribute('x', '0');
  bg.setAttribute('y', '0');
  bg.setAttribute('width', String(structureSvgW));
  bg.setAttribute('height', String(structureSvgH));
  bg.setAttribute('fill', 'transparent');
  svg.appendChild(bg);
  appendAssignmentDefs(svg);

  const selectedGroupIds = new Set();
  if (structureSelectedId) {
    const s = structureObjects.find(o => o.id === structureSelectedId);
    if (s && s.group_id) selectedGroupIds.add(s.group_id);
  }
  structureCheckedIds.forEach(id => {
    const o = structureObjects.find(x => x.id === id);
    if (o && o.group_id) selectedGroupIds.add(o.group_id);
  });

  const clickHandler = (ev) => {
    if (!structureSplitMode && !structureJunctionMode) return;
    ev.stopPropagation();
    const p = svgPointFromMouse(svg, ev);

    if (structureSplitMode && !structureSplitTargetReady) {
      toast('Manual split: click the path you want to cut first');
      return;
    }

    if (structureJunctionMode) {
      structureJunctionPoints.push([p.x, p.y]);
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();

      if (structureJunctionPoints.length === 1) {
        toast('Junction centre set. Click each branch direction, then double-click or Apply junction cut.');
      } else {
        toast((structureJunctionPoints.length - 1) + ' branch point(s). Need at least 3; double-click or Apply to finish.');
      }
      return;
    }

    structureCutPoints.push([p.x, p.y]);

    if (structureCutPoints.length === 1) {
      structureHoverPoint = [p.x, p.y];
      renderStructurePreview();
      updateStructureDetail();
      toast('Pick second point for the first cut');
    } else if (structureCutPoints.length === 2) {
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();
      applyManualSplit();
    } else if (structureCutPoints.length === 3) {
      structureHoverPoint = [p.x, p.y];
      renderStructurePreview();
      updateStructureDetail();
      toast('Pick fourth point for the second cut');
    } else if (structureCutPoints.length === 4) {
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();
      applyManualSplit();
    } else {
      structureCutPoints = [];
      structureHoverPoint = null;
      structureNeedSecondCut = false;
      renderStructurePreview();
    }
  };

  const moveHandler = (ev) => {
    if (!structureSplitMode && !structureJunctionMode) return;
    if (structureSplitMode && (!structureSplitTargetReady || ![1, 3].includes(structureCutPoints.length))) return;
    if (structureJunctionMode && structureJunctionPoints.length < 1) return;
    const p = svgPointFromMouse(svg, ev);
    structureHoverPoint = [p.x, p.y];
    renderStructurePreview();
  };

  bg.addEventListener('click', clickHandler);
  bg.addEventListener('dblclick', (ev) => {
    if (structureJunctionMode) {
      ev.stopPropagation();
      ev.preventDefault();
      applyJunctionCut();
    }
  });
  bg.addEventListener('mousemove', moveHandler);

  structureObjects.sort((a,b) => (a.order||0) - (b.order||0)).forEach(obj => {
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    if (obj.tx || obj.ty) p.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
    p.style.cursor = (structureSplitMode || structureJunctionMode || currentAssignToolType()) ? 'crosshair' : 'pointer';

    const st = stitchAssignments[obj.id] || defaultStitchType(obj);
    if ((obj.render_mode || 'fill') === 'stroke') {
      p.setAttribute('fill', 'none');
      p.setAttribute('stroke', obj.color);
      p.setAttribute('stroke-width', String(obj.stroke_width || 1.6));
      p.setAttribute('stroke-linecap', 'round');
      p.setAttribute('stroke-linejoin', 'round');
      if (st === 'skip') p.setAttribute('stroke-opacity', '0.22');
    } else {
      p.setAttribute('fill', obj.color);
      p.setAttribute('fill-rule', 'evenodd');
      p.setAttribute('clip-rule', 'evenodd');
      if (st === 'skip') {
        p.setAttribute('fill-opacity', '0.18');
        p.setAttribute('stroke', obj.color);
        p.setAttribute('stroke-opacity', '0.25');
        p.setAttribute('stroke-width', '0.8');
      } else if (st === 'satin') {
        p.setAttribute('fill-opacity', '1');
        p.setAttribute('stroke', obj.color);
        p.setAttribute('stroke-width', '0.6');
        p.setAttribute('stroke-opacity', '0.9');
      }
    }

    if (selectedGroupIds.has(obj.group_id)) {
      if ((obj.render_mode || 'fill') === 'stroke') {
        p.setAttribute('stroke', '#ff5f7c');
        p.setAttribute('stroke-width', String((obj.stroke_width || 1.6) + (obj.id === structureSelectedId ? 1.8 : 1.0)));
        p.setAttribute('stroke-opacity', obj.id === structureSelectedId ? '0.95' : '0.72');
      } else {
        p.setAttribute('stroke', '#e94560');
        p.setAttribute('stroke-width', obj.id === structureSelectedId ? '2' : '1.4');
        p.setAttribute('stroke-opacity', obj.id === structureSelectedId ? '0.95' : '0.65');
      }
    } else if (structureCheckedIds.has(obj.id)) {
      p.setAttribute('stroke', '#78a6ff');
      p.setAttribute('stroke-width', (obj.render_mode || 'fill') === 'stroke' ? String((obj.stroke_width || 1.6) + 0.8) : '1');
      p.setAttribute('stroke-opacity', '0.7');
    }

    p.addEventListener('mousemove', moveHandler);
    p.addEventListener('dblclick', (ev) => {
      if (structureJunctionMode) {
        ev.stopPropagation();
        ev.preventDefault();
        applyJunctionCut();
      }
    });
    p.addEventListener('click', (ev) => {
      if (structureSplitMode) {
        ev.stopPropagation();

        // First click after choosing Manual Split selects the target only.
        // After that, every click anywhere in the preview, including over an
        // adjacent path or inside the target path, is a cut point.
        if (!structureSplitTargetReady) {
          structureSelectedId = obj.id;
          structureCheckedIds.clear();
          structureSplitTargetReady = true;
          structureCutPoints = [];
          structureHoverPoint = null;
          structureNeedSecondCut = false;
          renderStructureList();
          renderStructurePreview();
          updateStructureDetail();
          toast('Manual split target selected: now place first cut point');
          return;
        }

        clickHandler(ev);
        return;
      }

      if (structureJunctionMode) {
        if (structureJunctionPoints.length === 0) {
          structureSelectedId = obj.id;
          structureCheckedIds.clear();
        }
        clickHandler(ev);
        return;
      }

      ev.stopPropagation();
      applyStructureAssignmentClick(obj, ev.shiftKey);
    });
    svg.appendChild(p);
    appendStructureAssignmentHatch(svg, obj, st);
  });

  const drawPoint = (pt, fill) => {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', String(pt[0]));
    c.setAttribute('cy', String(pt[1]));
    c.setAttribute('r', structureJunctionMode ? '3.2' : '2');
    c.setAttribute('fill', fill);
    c.setAttribute('stroke', '#111');
    c.setAttribute('stroke-width', '0.6');
    svg.appendChild(c);
  };

  const drawLine = (a, b, color) => {
    const ln = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    ln.setAttribute('x1', String(a[0]));
    ln.setAttribute('y1', String(a[1]));
    ln.setAttribute('x2', String(b[0]));
    ln.setAttribute('y2', String(b[1]));
    ln.setAttribute('stroke', color);
    ln.setAttribute('stroke-width', '2');
    ln.setAttribute('stroke-dasharray', '6 4');
    svg.appendChild(ln);
  };

  if (structureCutPoints.length >= 1) drawPoint(structureCutPoints[0], '#ffd166');
  if (structureCutPoints.length >= 2) {
    drawPoint(structureCutPoints[1], '#06d6a0');
    drawLine(structureCutPoints[0], structureCutPoints[1], '#ffd166');
  }
  if (structureCutPoints.length >= 3) drawPoint(structureCutPoints[2], '#7bdff2');
  if (structureCutPoints.length >= 4) {
    drawPoint(structureCutPoints[3], '#f15bb5');
    drawLine(structureCutPoints[2], structureCutPoints[3], '#7bdff2');
  }

  if (structureJunctionPoints.length >= 1) {
    const centre = structureJunctionPoints[0];
    drawPoint(centre, '#ff00ff');
    for (let i = 1; i < structureJunctionPoints.length; i++) {
      drawPoint(structureJunctionPoints[i], '#00e5ff');
      drawLine(centre, structureJunctionPoints[i], '#00e5ff');
    }
    if (structureJunctionMode && structureHoverPoint) {
      drawLine(centre, structureHoverPoint, '#ff00ff');
    }
  }

  if (structureSplitMode && structureHoverPoint) {
    if (structureCutPoints.length === 1) {
      drawLine(structureCutPoints[0], structureHoverPoint, '#ffd166');
    } else if (structureCutPoints.length === 3) {
      drawLine(structureCutPoints[2], structureHoverPoint, '#7bdff2');
    }
  }

  setWorkZoom(workZoom);
  document.getElementById('structure-preview-meta').textContent =
    structureObjects.length + ' objects' +
    (structureJunctionMode ? (' · junction points ' + structureJunctionPoints.length) : '');
}
function updateStructureDetail() {
  const detail = document.getElementById('structure-detail');
  const objs = structureSelectedObjects();
  if (!objs.length) {
    detail.innerHTML = 'Load a traced SVG to begin.';
    return;
  }
  if (objs.length > 1) {
    detail.innerHTML = `
      Selected objects: <b>${objs.length}</b><br>
      Sources: <b>${[...new Set(objs.map(o => o.display_index))].join(', ')}</b><br>
      Assignments: <b>${[...new Set(objs.map(o => stitchAssignments[o.id] || defaultStitchType(o)))].join(', ')}</b><br>
      Groups: <b>${[...new Set(objs.map(o => o.group_id))].join(', ')}</b>
    `;
    return;
  }
  const o = objs[0];
  detail.innerHTML = `
    Object: <b>${o.label}</b><br>
    Source path: <b>${o.display_index}</b><br>
    Colour: <b>${o.color}</b><br>
    Assignment: <b>${stitchAssignments[o.id] || defaultStitchType(o)}</b><br>
    Group: <b>${o.group_id}</b><br>
    Ratio: <b>${o.elongation}</b><br>
    Kind: <b>${o.source_kind || 'object'}</b><br>
    Mode: <b>${o.render_mode || 'fill'}</b><br>
    Note: <b>${o.prep_note}</b><br>
    ${structureSplitMode ? `Manual split active · ${structureSplitTargetReady ? 'cut target selected' : 'choose target path'} · points placed: <b>${structureCutPoints.length}</b>${structureNeedSecondCut ? '/4' : '/2'}<br>Selected object: <b>${o.label}</b><br>${!structureSplitTargetReady ? 'Click the path once to choose it as the cut target.' : (structureNeedSecondCut ? 'First cut is registered. Place two more points for the second cut.' : 'Place both cut points outside this selected shape on opposite sides.')}` : ''}
  `;
}

function replaceSourceObjects(sourceId, replacementObjects) {
  structureObjects = structureObjects.filter(o => o.source_id !== sourceId);
  replacementObjects.forEach(o => {
    if (!o.group_id) o.group_id = 'src_' + sourceId;
  });
  structureObjects = structureObjects.concat(JSON.parse(JSON.stringify(replacementObjects)));
  structureObjects.sort((a,b) => (a.order||0) - (b.order||0));
}

function splitSelectedSource() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (!selected.length) { toast('Select an object first'); return; }

  const sourceIds = [...new Set(selected.map(o => o.source_id))];
  let changed = 0;
  sourceIds.forEach(sid => {
    const src = structureSourcePaths.find(s => s.source_id === sid);
    if (src && src.split_parts && src.split_parts.length > 1) {
      replaceSourceObjects(sid, src.split_parts);
      changed++;
    }
  });

  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  if (changed) {
    toast('Best-guess source split applied');
  } else {
    toast('No safe split found. This is likely one fused path or a preserved ring/hole compound path.');
  }
}

function restoreSelectedSource() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (!selected.length) { toast('Select an object first'); return; }

  const sourceIds = [...new Set(selected.map(o => o.source_id))];
  sourceIds.forEach(sid => {
    const src = structureSourcePaths.find(s => s.source_id === sid);
    if (src) {
      const restoreObj = {
        id: 's' + src.source_id,
        source_id: src.source_id,
        display_index: src.display_index,
        label: 'Path ' + src.display_index,
        d: src.d,
        tx: src.tx,
        ty: src.ty,
        color: src.color,
        group_id: 'src_' + src.source_id,
        part_index: 0,
        part_count: 1,
        prep_note: src.prep_note || 'original source path',
        elongation: src.elongation,
        order: src.order || src.source_id,
        hidden: false,
        render_mode: src.render_mode || 'fill',
        stroke_width: src.stroke_width || 1.6,
        source_kind: src.source_kind || 'fill_region'
      };
      replaceSourceObjects(sid, [restoreObj]);
    }
  });

  structureCheckedIds.clear();
  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  invalidateStitchPane();
  toast('Selected source restored');
}

function groupSelectedObjects() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (selected.length < 2) { toast('Select at least 2 objects to group'); return; }

  const gid = 'grp_' + (structureGroupCounter++);
  const ids = new Set(selected.map(o => o.id));
  structureObjects.forEach(o => { if (ids.has(o.id)) o.group_id = gid; });

  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  invalidateStitchPane();
  toast('Grouped ' + selected.length + ' objects');
}

function ungroupSelectedObjects() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const selected = structureSelectedObjects();
  if (!selected.length) { toast('Select object(s) first'); return; }

  const ids = new Set(selected.map(o => o.id));
  structureObjects.forEach(o => { if (ids.has(o.id)) o.group_id = 'src_' + o.source_id; });

  renderStructureList();
  renderStructurePreview();
  updateStructureDetail();
  invalidateStitchPane();
  toast('Ungrouped selected objects');
}


function toggleManualSplitMode() {
  setStructureTool(structureActiveTool === 'manual_split' ? 'select' : 'manual_split');
}

function toggleJunctionCutMode() {
  setStructureTool(structureActiveTool === 'junction' ? 'select' : 'junction');
}

async function applyJunctionCut() {
  const objs = structureSelectedObjects();
  if (objs.length !== 1) {
    toast('Select exactly one object for junction cut');
    cancelManualSplit();
    return;
  }
  if (structureJunctionPoints.length < 4) {
    toast('Junction cut needs a centre point plus at least three branch points');
    return;
  }

  try {
    const center = structureJunctionPoints[0];
    const branch_points = structureJunctionPoints.slice(1);
    const res = await fetch('/api/structure/junction_split', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({object: objs[0], center, branch_points})
    });
    const data = await res.json();

    if (!data.ok) {
      toast('Junction cut failed: ' + (data.error || 'unknown'), 9000);
      console.error(data.trace);
      return;
    }

    const selected = objs[0];
    const selectedAssignment = stitchAssignments[selected.id] || defaultStitchType(selected);
    structureObjects = structureObjects.filter(o => o.id !== selected.id);
    delete stitchAssignments[selected.id];
    data.objects.forEach((o, i) => {
      if (!o.id) o.id = selected.id + '_jcut' + (i + 1);
      o.group_id = selected.group_id || ('src_' + selected.source_id);
      o.source_id = selected.source_id;
      o.display_index = selected.display_index;
      o.color = o.color || selected.color;
      o.render_mode = o.render_mode || selected.render_mode || 'fill';
      o.stroke_width = o.stroke_width || selected.stroke_width || 1.6;
      o.source_kind = o.source_kind || selected.source_kind || 'fill_region';
      o.cut_guide_rungs = o.cut_guide_rungs || [];
      stitchAssignments[o.id] = selectedAssignment;
    });

    structureObjects = structureObjects.concat(data.objects).sort((a,b) => (a.order||0) - (b.order||0));
    structureSelectedId = data.objects[0].id;
    structureCheckedIds.clear();
    structureJunctionPoints = [];
    structureHoverPoint = null;
    updateStructureToolButtons();
    renderStructureList();
    renderStructurePreview();
    updateStructureDetail();
    invalidateStitchPane();
    toast('Junction cut created ' + data.objects.length + ' object(s)' +
      ((data.cut_guide_rungs || 0) ? (' with ' + data.cut_guide_rungs + ' cut guide rung(s)') : ''));
  } catch (e) {
    toast('Junction cut error: ' + e, 9000);
  }
}

function cancelManualSplit() {
  structureSplitTargetReady = false;
  structureCutPoints = [];
  structureJunctionPoints = [];
  structureHoverPoint = null;
  structureNeedSecondCut = false;
  updateStructureToolButtons();
  renderStructurePreview();
  updateStructureDetail();
}
function svgPointFromMouse(svg, evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  return {x: p.x, y: p.y};
}

async function applyManualSplit() {
  const objs = structureSelectedObjects();
  if (objs.length !== 1) {
    toast('Select exactly one object for manual split');
    cancelManualSplit();
    return;
  }
  if (![2, 4].includes(structureCutPoints.length)) {
    toast(structureNeedSecondCut ? 'Pick two more cut points for the second cut' : 'Pick two cut points');
    return;
  }
  try {
    const res = await fetch('/api/structure/manual_split', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({object: objs[0], cut_points: structureCutPoints})
    });
    const data = await res.json();

    if (!data.ok && data.needs_second_cut) {
      structureSplitTargetReady = true;
      structureNeedSecondCut = true;
      structureHoverPoint = null;
      renderStructurePreview();
      updateStructureDetail();
      toast('First cut registered. This shape needs a second cut: place two more points for the second cut.', 9000);
      return;
    }

    if (!data.ok) {
      structureSplitTargetReady = false;
      structureCutPoints = [];
      structureHoverPoint = null;
      structureNeedSecondCut = false;
      renderStructurePreview();
      updateStructureDetail();
      toast('Manual split failed: ' + (data.error || 'unknown') + '  Try again on the selected object.', 9000);
      console.error(data.trace);
      return;
    }

    const selected = objs[0];
    const selectedAssignment = stitchAssignments[selected.id] || defaultStitchType(selected);
    structureObjects = structureObjects.filter(o => o.id !== selected.id);
    delete stitchAssignments[selected.id];
    data.objects.forEach((o, i) => {
      if (!o.id) o.id = selected.id + '_cut' + (i + 1);
      o.group_id = selected.group_id || ('src_' + selected.source_id);
      o.source_id = selected.source_id;
      o.display_index = selected.display_index;
      o.color = o.color || selected.color;
      o.render_mode = o.render_mode || selected.render_mode || 'fill';
      o.stroke_width = o.stroke_width || selected.stroke_width || 1.6;
      o.source_kind = o.source_kind || selected.source_kind || 'fill_region';
      o.cut_guide_rungs = o.cut_guide_rungs || [];
      stitchAssignments[o.id] = selectedAssignment;
    });
    structureObjects = structureObjects.concat(data.objects).sort((a,b) => (a.order||0) - (b.order||0));
    structureSelectedId = data.objects[0].id;
    structureCheckedIds.clear();
    structureSplitTargetReady = false;
    structureCutPoints = [];
    structureHoverPoint = null;
    structureNeedSecondCut = false;
    updateStructureToolButtons();
    renderStructureList();
    renderStructurePreview();
    updateStructureDetail();
    invalidateStitchPane();
    toast('Manual split created ' + data.objects.length + ' object(s)' +
      ((data.cut_guide_rungs || 0) ? (' with ' + data.cut_guide_rungs + ' cut guide rung(s)') : '') +
      '. Choose the next path to cut.');
  } catch (e) {
    structureSplitTargetReady = false;
    structureCutPoints = [];
    structureHoverPoint = null;
    structureNeedSecondCut = false;
    renderStructurePreview();
    updateStructureDetail();
    toast('Manual split error: ' + e + '  Cut points cleared; try again.', 9000);
  }
}
function saveStructureJson() {
  if (!structureLoaded) { toast('Load a traced SVG first'); return; }
  const payload = {
    version: 1,
    source_svg: lastTrace ? lastTrace.output_path : null,
    svg_w: structureSvgW,
    svg_h: structureSvgH,
    source_paths: structureSourcePaths,
    objects: structureObjects
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'easystitch_structure.json';
  a.click();
  URL.revokeObjectURL(a.href);
}




let autoAssignBBoxSvg = null;

function autoAssignObjectBBox(obj) {
  if (!obj || !obj.d) return null;
  try {
    if (!autoAssignBBoxSvg) {
      autoAssignBBoxSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      autoAssignBBoxSvg.setAttribute('width', '0');
      autoAssignBBoxSvg.setAttribute('height', '0');
      autoAssignBBoxSvg.style.position = 'fixed';
      autoAssignBBoxSvg.style.left = '-10000px';
      autoAssignBBoxSvg.style.top = '-10000px';
      autoAssignBBoxSvg.style.visibility = 'hidden';
      document.body.appendChild(autoAssignBBoxSvg);
    }

    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    autoAssignBBoxSvg.appendChild(p);
    const b = p.getBBox();

    let len = 0;
    try { len = p.getTotalLength(); } catch (e) { len = 0; }
    p.remove();

    const tx = Number(obj.tx || 0);
    const ty = Number(obj.ty || 0);
    const area = Math.max(0, b.width * b.height);
    const minDim = Math.min(Math.max(0, b.width), Math.max(0, b.height));
    const maxDim = Math.max(Math.max(0, b.width), Math.max(0, b.height));
    const areaPerLength = len > 0 ? area / len : area;

    return {
      x1: b.x + tx,
      y1: b.y + ty,
      x2: b.x + b.width + tx,
      y2: b.y + b.height + ty,
      w: b.width,
      h: b.height,
      area,
      minDim,
      maxDim,
      aspect: minDim > 0 ? maxDim / minDim : 999,
      length: len,
      areaPerLength
    };
  } catch (e) {
    return null;
  }
}

function autoAssignTouchesImageEdge(b, eps=1.5) {
  if (!b) return false;
  return (
    b.x1 <= eps ||
    b.y1 <= eps ||
    b.x2 >= (structureSvgW - eps) ||
    b.y2 >= (structureSvgH - eps)
  );
}

function autoAssignBBoxContains(outer, inner, margin=1.0) {
  if (!outer || !inner) return false;
  return (
    outer.x1 <= inner.x1 + margin &&
    outer.y1 <= inner.y1 + margin &&
    outer.x2 >= inner.x2 - margin &&
    outer.y2 >= inner.y2 - margin
  );
}

function autoAssignIsLikelyColumn(obj, bbox) {
  if (!obj || !bbox) return false;
  if ((obj.render_mode || 'fill') === 'stroke') return true;

  // Use the earlier working elongation signal again, but let the full-list
  // pass below correct large enclosed fills such as the HappySun face back to
  // Fill when they contain satin details.
  if (bbox.area < 12) return false;

  const ratio = parseFloat(obj.elongation || 0);
  const oldElongationColumn = ratio >= 3.0;
  const thinByWidth = bbox.minDim <= 16 && bbox.aspect >= 1.8;
  const thinByAreaLength = bbox.areaPerLength <= 9.0 && bbox.aspect >= 1.2;
  const veryLongNarrow = bbox.aspect >= 3.5 && bbox.minDim <= 24;

  return oldElongationColumn || thinByWidth || thinByAreaLength || veryLongNarrow;
}

function autoAssignBaseType(obj, bbox) {
  // Default is Fill. Then only explicit rules override it.
  if (autoAssignTouchesImageEdge(bbox)) return 'skip';
  if (obj.hidden) return 'skip';
  if (autoAssignIsLikelyColumn(obj, bbox)) return 'satin';
  return 'fill';
}

function autoAssignEnclosedFillThreshold() {
  const el = document.getElementById('auto-enclosed-fill-pct');
  const pct = parseFloat(el?.value || '70');
  return Math.max(0.50, Math.min(0.90, pct / 100.0));
}

let autoAssignAreaCanvas = null;
let autoAssignAreaCtx = null;

function autoAssignEnsureAreaCanvas() {
  if (!autoAssignAreaCanvas) {
    autoAssignAreaCanvas = document.createElement('canvas');
    autoAssignAreaCanvas.width = 128;
    autoAssignAreaCanvas.height = 128;
    autoAssignAreaCtx = autoAssignAreaCanvas.getContext('2d', { willReadFrequently: true });
  }
  return autoAssignAreaCtx;
}

function autoAssignCountFilledPixelsForObject(obj, bounds) {
  if (!obj || !obj.d || !bounds) return 0;
  const ctx = autoAssignEnsureAreaCanvas();
  if (!ctx) return 0;

  const canvas = ctx.canvas;
  const margin = 2;
  const bw = Math.max(1, bounds.x2 - bounds.x1);
  const bh = Math.max(1, bounds.y2 - bounds.y1);
  const usable = Math.max(1, Math.min(canvas.width, canvas.height) - margin * 2);
  const scale = usable / Math.max(bw, bh);

  ctx.save();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#ffffff';

  try {
    const path = new Path2D(obj.d);
    ctx.translate(margin + ((usable - bw * scale) * 0.5), margin + ((usable - bh * scale) * 0.5));
    ctx.scale(scale, scale);
    ctx.translate(-bounds.x1, -bounds.y1);
    ctx.translate(Number(obj.tx || 0), Number(obj.ty || 0));
    ctx.fill(path, 'nonzero');
  } catch (e) {
    ctx.restore();
    return 0;
  }

  ctx.restore();

  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let count = 0;
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] > 0) count += 1;
  }
  return count;
}

function autoAssignRasterAreaRatio(innerObj, outerObj, innerBBox, outerBBox) {
  if (!innerObj || !outerObj || !innerBBox || !outerBBox) return 0;
  const bounds = {
    x1: Math.min(innerBBox.x1, outerBBox.x1),
    y1: Math.min(innerBBox.y1, outerBBox.y1),
    x2: Math.max(innerBBox.x2, outerBBox.x2),
    y2: Math.max(innerBBox.y2, outerBBox.y2),
  };
  const outerPixels = autoAssignCountFilledPixelsForObject(outerObj, bounds);
  if (!outerPixels) return 0;
  const innerPixels = autoAssignCountFilledPixelsForObject(innerObj, bounds);
  return innerPixels / outerPixels;
}

function autoAssignImproved(list) {
  const boxes = new Map();
  list.forEach(o => boxes.set(o.id, autoAssignObjectBBox(o)));

  const assignments = {};
  let edgeSkipped = 0;
  let columnsConverted = 0;
  let enclosedLargeRegionsForcedFill = 0;

  // Pass 1: everything starts as Fill, then hard overrides run.
  list.forEach(o => {
    const b = boxes.get(o.id);
    if (autoAssignTouchesImageEdge(b)) {
      assignments[o.id] = 'skip';
      edgeSkipped += 1;
    } else if (autoAssignIsLikelyColumn(o, b)) {
      assignments[o.id] = 'satin';
      columnsConverted += 1;
    } else {
      assignments[o.id] = 'fill';
    }
  });

  // Pass 2: if an object was changed to Satin by the column detector, but it
  // substantially fills the inside of another satin outline/column, switch it
  // back to Fill.
  //
  // This uses a simple "caveman stone counting" raster estimate:
  // rasterise the enclosing satin object and the inner candidate into a small
  // offscreen canvas (longest side 128 px), count filled pixels for each, then
  // compare the ratio. This behaves much better than bbox percentage on angled
  // shapes such as the HappySun spikes.
  const satinContainers = list.filter(o => assignments[o.id] === 'satin')
    .map(o => ({obj: o, bbox: boxes.get(o.id)}))
    .filter(x => x.bbox && x.bbox.area > 0);

  list.forEach(o => {
    const b = boxes.get(o.id);
    if (!b || assignments[o.id] !== 'satin') return;
    if (autoAssignTouchesImageEdge(b)) return;

    const enclosedAndLarge = satinContainers.some(c => {
      if (!c.obj || c.obj.id === o.id) return false;
      if (!autoAssignBBoxContains(c.bbox, b, 2.0)) return false;
      if (b.area < 16) return false;

      const fillRatio = autoAssignRasterAreaRatio(o, c.obj, b, c.bbox);
      return fillRatio >= autoAssignEnclosedFillThreshold() && fillRatio < 0.985;
    });

    if (enclosedAndLarge) {
      assignments[o.id] = 'fill';
      columnsConverted -= 1;
      enclosedLargeRegionsForcedFill += 1;
    }
  });

  return {assignments, edgeSkipped, columnsConverted, enclosedLargeRegionsForcedFill};
}


function defaultStitchType(obj) {
  return autoAssignBaseType(obj, autoAssignObjectBBox(obj));
}

function loadStitchPane() {
  if (!structureObjects.length) {
    toast('Prepare paths in Pane 3 first');
    showPane(3);
    return;
  }
  stitchObjects = JSON.parse(JSON.stringify(structureObjects)).sort((a,b) => (a.order||0) - (b.order||0));
  // Pane 4 is read-only for Fill/Satin/Skip assignments. It never auto-assigns
  // or changes stitchAssignments; it only previews, tunes, and exports.
  stitchSelectedId = stitchObjects.length ? stitchObjects[0].id : null;
  stitchCheckedIds = new Set();
  stitchCollapsedGroups = new Set();
  stitchManualRungs = {};
  currentStitchPlan = null;
  currentStitchPreview = null;
  stitchPlanPlayIndex = 0;
  if (stitchPlanPlayTimer) { clearInterval(stitchPlanPlayTimer); stitchPlanPlayTimer = null; }
  previewLayerMode = 'both';
  manualRungMode = false;
  pendingManualRungPoint = null;
  draggingManualRung = null;
  stitchSortMode = 'number';
  stitchLoaded = true;
  renderStitchList();
  renderStitchPreview();
  ensurePreviewBgButtons();
  updateStitchDetail();
  updateStitchSummary();
  updateDesignSizeInfo();
  toast('Prepared stitch map loaded from Pane 3 assignments');
  setTimeout(() => previewStitches(), 80);
}

function stitchSelectedObjects() {
  const ids = stitchCheckedIds.size ? Array.from(stitchCheckedIds) : (stitchSelectedId ? [stitchSelectedId] : []);
  return ids.map(id => stitchObjects.find(o => o.id === id)).filter(Boolean);
}

function selectStitchObject(id) {
  stitchSelectedId = id;
  stitchCheckedIds.clear();
  pendingManualRungPoint = null;
  draggingManualRung = null;
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
  updateManualRungStatus();
}

function toggleStitchChecked(id) {
  if (stitchCheckedIds.has(id)) stitchCheckedIds.delete(id);
  else stitchCheckedIds.add(id);
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
}

function selectStitchGroup(groupId) {
  const members = stitchObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  stitchSelectedId = members[0].id;
  stitchCheckedIds = new Set(members.map(o => o.id));
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
}

function toggleStitchGroup(groupId) {
  const members = stitchObjects.filter(o => objectGroupKey(o) === groupId);
  if (!members.length) return;
  const allChecked = members.every(o => stitchCheckedIds.has(o.id));
  if (allChecked) members.forEach(o => stitchCheckedIds.delete(o.id));
  else members.forEach(o => stitchCheckedIds.add(o.id));
  stitchSelectedId = members[0].id;
  renderStitchList();
  renderStitchPreview();
  updateStitchDetail();
}

function toggleStitchGroupCollapse(groupId) {
  if (stitchCollapsedGroups.has(groupId)) stitchCollapsedGroups.delete(groupId);
  else stitchCollapsedGroups.add(groupId);
  renderStitchList();
}

function groupAssignmentSummary(members) {
  const vals = [...new Set(members.map(o => stitchAssignments[o.id] || 'fill'))];
  return vals.length === 1 ? vals[0] : 'mixed';
}


function cycleStitchType(type) {
  if (type === 'fill') return 'satin';
  if (type === 'satin') return 'skip';
  return 'fill';
}

function applyStitchSelectionAction(id, shiftKey) {
  if (shiftKey) {
    toggleStitchChecked(id);
    stitchSelectedId = id;
    renderStitchList();
    renderStitchPreview();
    updateStitchDetail();
    return;
  }

  const noMulti = stitchCheckedIds.size === 0;
  if (id === stitchSelectedId && noMulti) {
    stitchAssignments[id] = cycleStitchType(stitchAssignments[id] || 'fill');
    refreshAssignmentViews();
    toast('Changed selected object to ' + (stitchAssignments[id] || 'fill'));
    return;
  }

  selectStitchObject(id);
}

function selectedStitchColour() {
  return currentAssignmentSelectedColour();
}

function assignColourStitch(type) {
  const colour = currentAssignmentSelectedColour();
  const list = currentAssignmentObjectList();
  if (!colour || !list.length) {
    toast('Select an object first');
    return;
  }
  let changed = 0;
  list.forEach(o => {
    if (o.color === colour) {
      stitchAssignments[o.id] = type;
      changed += 1;
    }
  });
  refreshAssignmentViews();
  toast('Assigned ' + type + ' to ' + changed + ' object(s) of colour ' + colour);
}

function orderedStitchGroups() {
  const groups = buildGroupedObjectRows(stitchObjects);
  if (stitchSortMode === 'colour') {
    groups.sort((a, b) => {
      const ac = (a.first.color || '').toLowerCase();
      const bc = (b.first.color || '').toLowerCase();
      if (ac < bc) return -1;
      if (ac > bc) return 1;
      return (a.first.order || 0) - (b.first.order || 0);
    });
  } else {
    groups.sort((a, b) => (a.first.order || 0) - (b.first.order || 0));
  }
  return groups;
}

function toggleStitchSortMode() {
  stitchSortMode = stitchSortMode === 'number' ? 'colour' : 'number';
  const meta = document.getElementById('stitch-sort-meta');
  if (meta) meta.textContent = 'Sort mode: ' + stitchSortMode;
  renderStitchList();
  toast('Stitch list sort: ' + stitchSortMode);
}

function stitchBadgeClass(st) {
  if (st === 'satin') return 'stitch-badge stitch-satin';
  if (st === 'skip') return 'stitch-badge stitch-skip';
  return 'stitch-badge stitch-fill';
}


function applyStitchObjectPanelState() {
  const panel = document.getElementById('stitch-object-panel');
  if (!panel) return;
  const expanded = !stitchObjectListCollapsed;
  panel.style.width = expanded ? '360px' : '42px';
  document.querySelectorAll('.stitch-list-expanded-only').forEach(el => {
    el.style.display = expanded ? '' : 'none';
  });
  const btn = document.getElementById('stitch-object-panel-toggle');
  if (btn) {
    btn.textContent = expanded ? '×' : '☰';
    btn.title = expanded ? 'Collapse stitch object list' : 'Expand stitch object list';
  }
}

function toggleStitchObjectPanel() {
  stitchObjectListCollapsed = !stitchObjectListCollapsed;
  applyStitchObjectPanelState();
  setWorkZoom(workZoom);
}

function renderStitchList() {
  const list = document.getElementById('stitch-list');
  const meta = document.getElementById('stitch-count');
  const sortMeta = document.getElementById('stitch-sort-meta');
  if (!list || !meta) return;
  list.innerHTML = '';
  meta.textContent = stitchObjects.length + ' object' + (stitchObjects.length === 1 ? '' : 's');
  if (sortMeta) sortMeta.textContent = 'Sort mode: ' + stitchSortMode;
  applyStitchObjectPanelState();

  orderedStitchGroups().forEach(group => {
    const members = group.members;
    const parentLabel = groupParentLabel(members);
    const isGrouped = members.length > 1;
    const groupState = groupCheckedState(members, stitchCheckedIds);
    const groupSelected = members.some(o => o.id === stitchSelectedId || stitchCheckedIds.has(o.id));
    const collapsed = stitchCollapsedGroups.has(group.groupId);

    if (isGrouped) {
      const stSummary = groupAssignmentSummary(members);
      const parent = document.createElement('div');
      parent.className = 'obj-row obj-parent' + (groupSelected ? ' sel' : '');
      parent.onclick = (ev) => {
        if (ev.shiftKey) toggleStitchGroup(group.groupId);
        else selectStitchGroup(group.groupId);
      };
      parent.innerHTML = `
        <button class="collapse-toggle" title="${collapsed ? 'Expand group' : 'Collapse group'}" onclick="event.stopPropagation();toggleStitchGroupCollapse('${group.groupId}')">${collapsed ? '+' : '−'}</button>
        <input type="checkbox" ${groupState === 'checked' ? 'checked' : ''} onclick="event.stopPropagation();toggleStitchGroup('${group.groupId}')">
        <div class="obj-swatch" style="background:${members[0].color}"></div>
        <div class="obj-info">
          <div class="obj-name">${parentLabel}</div>
          <div class="obj-meta">${members.length} grouped child paths · group ${group.groupId}</div>
        </div>
        <span class="${stSummary === 'mixed' ? 'obj-group-count' : stitchBadgeClass(stSummary)}">${stSummary}</span>
      `;
      const cb = parent.querySelector('input[type=checkbox]');
      setCheckboxIndeterminate(cb, groupState);
      list.appendChild(parent);
    }

    members.forEach((obj, idx) => {
      const st = stitchAssignments[obj.id] || 'fill';
      const row = document.createElement('div');
      row.className = 'obj-row' + (isGrouped ? ' obj-child' : '') + (obj.id === stitchSelectedId ? ' sel' : '') + ((isGrouped && collapsed) ? ' hidden-child' : '');
      row.onclick = (ev) => applyStitchSelectionAction(obj.id, ev.shiftKey);
      const displayName = isGrouped ? childLabel(parentLabel, idx) : obj.label;
      row.innerHTML = `
        ${!isGrouped ? '<span style="width:22px;display:inline-block"></span>' : ''}
        <input type="checkbox" ${stitchCheckedIds.has(obj.id) ? 'checked' : ''} onclick="event.stopPropagation();toggleStitchChecked('${obj.id}')">
        <div class="obj-swatch" style="background:${obj.color}"></div>
        <div class="obj-info">
          <div class="obj-name">${displayName} <span style="color:#8f96b3;font-size:.72rem">${obj.color}</span></div>
          <div class="obj-meta">source path ${obj.display_index} · group ${obj.group_id} · ratio ${obj.elongation}<br>${obj.prep_note || 'working object'}</div>
        </div>
        <span class="${stitchBadgeClass(st)}">${st}</span>
      `;
      list.appendChild(row);
    });
  });
}

function stitchSvgMarkup() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function renderStitchPreview() {
  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return;
  if (!stitchObjects.length) {
    wrap.innerHTML = '<span style="color:#555">No stitch assignments loaded</span>'; restorePreviewBg('stitch-preview');
    return;
  }

  wrap.innerHTML = stitchSvgMarkup(); restorePreviewBg('stitch-preview');
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  renderHoopRulers(svg);
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  defs.innerHTML = `
    <pattern id="fillHatchDark" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(255,255,255,0.65)" stroke-width="1"/>
    </pattern>
    <pattern id="fillHatchLight" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(0,0,0,0.55)" stroke-width="1"/>
    </pattern>
  `;
  svg.appendChild(defs);

  const selectedGroupIds = new Set();
  if (stitchSelectedId) {
    const s = stitchObjects.find(o => o.id === stitchSelectedId);
    if (s && s.group_id) selectedGroupIds.add(s.group_id);
  }
  stitchCheckedIds.forEach(id => {
    const o = stitchObjects.find(x => x.id === id);
    if (o && o.group_id) selectedGroupIds.add(o.group_id);
  });

  function luminance(hex) {
    const h = (hex || '#000000').replace('#', '');
    if (h.length !== 6) return 0;
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  stitchObjects.slice().sort((a,b) => (a.order||0) - (b.order||0)).forEach(obj => {
    const st = stitchAssignments[obj.id] || 'fill';
    const isHighlighted = selectedGroupIds.has(obj.group_id) || stitchCheckedIds.has(obj.id) || obj.id === stitchSelectedId;

    const base = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    base.setAttribute('d', obj.d);
    if (obj.tx || obj.ty) base.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
    base.style.cursor = 'pointer';
    base.setAttribute('fill-rule', 'evenodd');
    base.setAttribute('clip-rule', 'evenodd');

    if (st === 'skip') {
      base.setAttribute('fill', obj.color);
      base.setAttribute('fill-opacity', '0.18');
      base.setAttribute('stroke', obj.color);
      base.setAttribute('stroke-opacity', '0.25');
      base.setAttribute('stroke-width', '0.8');
    } else if (st === 'satin') {
      base.setAttribute('fill', obj.color);
      base.setAttribute('fill-opacity', '1');
      base.setAttribute('stroke', obj.color);
      base.setAttribute('stroke-width', '0.6');
      base.setAttribute('stroke-opacity', '0.9');
    } else {
      base.setAttribute('fill', obj.color);
      base.setAttribute('fill-opacity', '1');
      base.setAttribute('stroke', 'rgba(255,255,255,0.25)');
      base.setAttribute('stroke-width', '0.4');
    }

    if (isHighlighted) {
      base.setAttribute('stroke', '#e94560');
      base.setAttribute('stroke-width', obj.id === stitchSelectedId ? '2.2' : '1.4');
      base.setAttribute('stroke-opacity', obj.id === stitchSelectedId ? '1' : '0.78');
    }

    base.addEventListener('click', (ev) => {
      ev.stopPropagation();
      applyStitchSelectionAction(obj.id, ev.shiftKey);
    });
    svg.appendChild(base);

    if (st === 'fill') {
      const hatch = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hatch.setAttribute('d', obj.d);
      if (obj.tx || obj.ty) hatch.setAttribute('transform', `translate(${obj.tx},${obj.ty})`);
      hatch.setAttribute('fill-rule', 'evenodd');
      hatch.setAttribute('clip-rule', 'evenodd');
      hatch.setAttribute('fill', luminance(obj.color) > 160 ? 'url(#fillHatchLight)' : 'url(#fillHatchDark)');
      hatch.setAttribute('pointer-events', 'none');
      svg.appendChild(hatch);
    }
  });

  setWorkZoom(workZoom);
  renderManualRungOverlay();
  document.getElementById('stitch-preview-meta').textContent = stitchObjects.length + ' objects';
}

function updateStitchDetail() {
  const detail = document.getElementById('stitch-detail');
  if (!detail) return;
  const objs = stitchSelectedObjects();
  if (!objs.length) {
    detail.innerHTML = 'Load prepared structure to begin.';
    return;
  }
  if (objs.length > 1) {
    detail.innerHTML = `
      Selected objects: <b>${objs.length}</b><br>
      Assignments: <b>${[...new Set(objs.map(o => stitchAssignments[o.id] || 'fill'))].join(', ')}</b><br>
      Groups: <b>${[...new Set(objs.map(o => o.group_id))].join(', ')}</b>
    `;
    return;
  }
  const o = objs[0];
  const st = stitchAssignments[o.id] || 'fill';
  detail.innerHTML = `
    Object: <b>${o.label}</b><br>
    Assignment: <b>${st}</b><br>
    Source path: <b>${o.display_index}</b><br>
    Colour: <b>${o.color}</b><br>
    Group: <b>${o.group_id}</b><br>
    Ratio: <b>${o.elongation}</b><br>
    Note: <b>${o.prep_note}</b>
  `;
}

function updateStitchSummary() {
  const summary = document.getElementById('stitch-summary');
  if (!summary) return;
  const counts = {fill:0, satin:0, skip:0};
  stitchObjects.forEach(o => {
    const st = stitchAssignments[o.id] || 'fill';
    counts[st] = (counts[st] || 0) + 1;
  });
  summary.innerHTML = `
    Fill: <b>${counts.fill}</b><br>
    Satin: <b>${counts.satin}</b><br>
    Skip: <b>${counts.skip}</b><br>
    Total objects: <b>${stitchObjects.length}</b>
  `;
}

function assignSelectedStitch(type) {
  const objs = currentAssignmentObjects();
  if (!objs.length) { toast('Select object(s) first'); return; }
  objs.forEach(o => { stitchAssignments[o.id] = type; });
  refreshAssignmentViews();
  toast('Assigned ' + type + ' to ' + objs.length + ' object(s)');
}

function autoAssignStitches() {
  const list = currentAssignmentObjectList();
  if (!list.length) { toast('Load structure first'); return; }

  const result = autoAssignImproved(list);
  Object.keys(result.assignments).forEach(id => {
    stitchAssignments[id] = result.assignments[id];
  });

  refreshAssignmentViews();
  toast(
    'Auto assignment refreshed' +
    (result.edgeSkipped ? ` · edge/background skipped ${result.edgeSkipped}` : '') +
    (result.columnsConverted ? ` · columns to satin ${result.columnsConverted}` : '') +
    (result.enclosedLargeRegionsForcedFill ? ` · enclosed large regions to fill ${result.enclosedLargeRegionsForcedFill} @ ${Math.round(autoAssignEnclosedFillThreshold() * 100)}%` : '')
  );
}


function setPreviewLayerMode(mode) {
  previewLayerMode = mode || 'both';

  const underlayBox = document.getElementById('underlay-enable');
  if (underlayBox) {
    if (previewLayerMode === 'top') underlayBox.checked = false;
    if (previewLayerMode === 'underlay') underlayBox.checked = true;
  }

  updateUnderlayUi(false);
  renderCachedStitchPreview();
  ensurePreviewBgButtons();
}

function getSelectedHoop() {
  const value = document.getElementById('hoop-size')?.value || '120x120';
  const parts = value.split('x').map(v => parseFloat(v));

  // Hoop options are named as Vertical × Horizontal, matching the machine/hoop
  // convention. Internally width is horizontal and height is vertical.
  const verticalMm = parts[0] || 120;
  const horizontalMm = parts[1] || 120;

  return {
    id: value,
    width_mm: horizontalMm,
    height_mm: verticalMm,
    vertical_mm: verticalMm,
    horizontal_mm: horizontalMm,
    label: `${verticalMm} × ${horizontalMm} mm (V × H)`
  };
}

function designSizeSourceObjects() {
  const source = stitchObjects.length ? stitchObjects : structureObjects;
  if (!source.length) return [];
  const stitched = source.filter(o => (stitchAssignments[o.id] || 'fill') !== 'skip');
  return stitched.length ? stitched : source;
}

let designSizingBBoxSvg = null;

function designSizingObjectBBox(obj) {
  if (!obj || !obj.d) return null;
  try {
    if (!designSizingBBoxSvg) {
      designSizingBBoxSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      designSizingBBoxSvg.setAttribute('width', '0');
      designSizingBBoxSvg.setAttribute('height', '0');
      designSizingBBoxSvg.style.position = 'fixed';
      designSizingBBoxSvg.style.left = '-10000px';
      designSizingBBoxSvg.style.top = '-10000px';
      designSizingBBoxSvg.style.visibility = 'hidden';
      document.body.appendChild(designSizingBBoxSvg);
    }

    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    designSizingBBoxSvg.appendChild(p);
    const b = p.getBBox();
    p.remove();

    const tx = Number(obj.tx || 0);
    const ty = Number(obj.ty || 0);
    return {
      x1: b.x + tx,
      y1: b.y + ty,
      x2: b.x + b.width + tx,
      y2: b.y + b.height + ty,
      w: b.width,
      h: b.height
    };
  } catch (e) {
    return null;
  }
}

function getDesignBoundsSvgUnits() {
  const objects = designSizeSourceObjects();
  let bounds = null;

  objects.forEach(o => {
    const b = designSizingObjectBBox(o);
    if (!b) return;
    if (!bounds) bounds = {x1:b.x1, y1:b.y1, x2:b.x2, y2:b.y2};
    else {
      bounds.x1 = Math.min(bounds.x1, b.x1);
      bounds.y1 = Math.min(bounds.y1, b.y1);
      bounds.x2 = Math.max(bounds.x2, b.x2);
      bounds.y2 = Math.max(bounds.y2, b.y2);
    }
  });

  if (!bounds) return null;
  bounds.w = Math.max(0, bounds.x2 - bounds.x1);
  bounds.h = Math.max(0, bounds.y2 - bounds.y1);
  return bounds;
}

function formatMm(v) {
  if (!Number.isFinite(v)) return '0.0';
  return v.toFixed(1);
}

function getFitLongestSideForHoop(bounds, hoop) {
  if (!bounds || bounds.w <= 0 || bounds.h <= 0) return Math.min(hoop.width_mm, hoop.height_mm);
  const longestSvg = Math.max(bounds.w, bounds.h);
  const widthAtLongestOne = bounds.w / longestSvg;
  const heightAtLongestOne = bounds.h / longestSvg;
  return Math.min(
    hoop.width_mm / Math.max(0.0001, widthAtLongestOne),
    hoop.height_mm / Math.max(0.0001, heightAtLongestOne)
  );
}

function clampDesignTargetLongest(value, bounds=null, hoop=null) {
  hoop = hoop || getSelectedHoop();
  bounds = bounds || getDesignBoundsSvgUnits();
  const fitMax = getFitLongestSideForHoop(bounds, hoop);
  const maxVal = Math.max(5, fitMax);
  const v = Number.isFinite(value) ? value : maxVal;
  return Math.max(5, Math.min(maxVal, v));
}

function syncDesignTargetControls(bounds=null) {
  const hoop = getSelectedHoop();
  bounds = bounds || getDesignBoundsSvgUnits();
  const fitMax = getFitLongestSideForHoop(bounds, hoop);
  const slider = document.getElementById('design-longest-side');
  const input = document.getElementById('design-longest-side-input');

  if (!bounds || bounds.w <= 0 || bounds.h <= 0) {
    if (slider) {
      slider.max = String(Math.round(fitMax));
      slider.value = String(Math.round(fitMax));
      slider.disabled = true;
    }
    if (input) {
      input.max = String(formatMm(fitMax));
      input.value = String(formatMm(fitMax));
      input.disabled = true;
    }
    return;
  }

  if (designTargetLongestMm === null) {
    designTargetLongestMm = fitMax;
  }
  designTargetLongestMm = clampDesignTargetLongest(designTargetLongestMm, bounds, hoop);

  const maxRounded = Math.max(5, Math.ceil(fitMax * 10) / 10);
  if (slider) {
    slider.disabled = false;
    slider.max = String(Math.ceil(maxRounded));
    slider.value = String(Math.round(designTargetLongestMm));
  }
  if (input) {
    input.disabled = false;
    input.max = String(maxRounded.toFixed(1));
    input.value = String(formatMm(designTargetLongestMm));
  }
}

function setDesignTargetToFit() {
  const bounds = getDesignBoundsSvgUnits();
  const hoop = getSelectedHoop();
  designTargetLongestMm = getFitLongestSideForHoop(bounds, hoop);
  syncDesignTargetControls(bounds);
}

function onHoopSizeChanged() {
  // On hoop change, default back to "fit selected hoop" because the available
  // maximum has changed.
  setDesignTargetToFit();
  updateDesignSizeInfo();
  renderStitchPreview();
  renderCachedStitchPreview();
}

function onDesignLongestSideSlider() {
  const slider = document.getElementById('design-longest-side');
  const bounds = getDesignBoundsSvgUnits();
  designTargetLongestMm = clampDesignTargetLongest(parseFloat(slider?.value || '0'), bounds);
  syncDesignTargetControls(bounds);
  updateDesignSizeInfo();
  renderStitchPreview();
  renderCachedStitchPreview();
}

function onDesignLongestSideInput() {
  const input = document.getElementById('design-longest-side-input');
  const bounds = getDesignBoundsSvgUnits();
  designTargetLongestMm = clampDesignTargetLongest(parseFloat(input?.value || '0'), bounds);
  syncDesignTargetControls(bounds);
  updateDesignSizeInfo();
  renderStitchPreview();
  renderCachedStitchPreview();
}

function getDesignScaleInfo() {
  const hoop = getSelectedHoop();
  const bounds = getDesignBoundsSvgUnits();
  if (!bounds || bounds.w <= 0 || bounds.h <= 0) return {hoop, bounds: null};

  const nativeMmPerSvg = 25.4 / 96.0;
  const nativeW = bounds.w * nativeMmPerSvg;
  const nativeH = bounds.h * nativeMmPerSvg;
  const fitLongestMm = getFitLongestSideForHoop(bounds, hoop);

  if (designTargetLongestMm === null) designTargetLongestMm = fitLongestMm;
  designTargetLongestMm = clampDesignTargetLongest(designTargetLongestMm, bounds, hoop);

  const longestSvg = Math.max(bounds.w, bounds.h);
  const designMmPerSvg = designTargetLongestMm / Math.max(0.0001, longestSvg);
  const designW = bounds.w * designMmPerSvg;
  const designH = bounds.h * designMmPerSvg;
  const fitPercentOfNative = (designMmPerSvg / nativeMmPerSvg) * 100.0;
  const exceedsHoop = designW > hoop.width_mm + 0.01 || designH > hoop.height_mm + 0.01;

  return {
    hoop,
    bounds,
    nativeMmPerSvg,
    nativeW,
    nativeH,
    fitMmPerSvg: designMmPerSvg,
    fitW: designW,
    fitH: designH,
    fitPercentOfNative,
    fitLongestMm,
    targetLongestMm: designTargetLongestMm,
    exceedsNative: exceedsHoop,
    exceedsHoop
  };
}

function updateDesignSizeInfo() {
  const el = document.getElementById('design-size-meta');
  if (!el) return;

  const info = getDesignScaleInfo();
  const hoop = info.hoop;
  if (!info.bounds) {
    syncDesignTargetControls(null);
    el.innerHTML = `Selected hoop: <b>${hoop.label}</b><br>Load prepared structure to calculate design size.`;
    return;
  }

  syncDesignTargetControls(info.bounds);

  const warning = info.exceedsHoop
    ? `<br><span style="color:#f6c177">Selected design size exceeds selected hoop.</span>`
    : `<br><span style="color:#9da7c4">Selected design size fits selected hoop. This size is now used for stitch generation and export.</span>`;

  el.innerHTML =
    `Selected hoop: <b>${hoop.label}</b><br>` +
    `Current legacy size: <b>${formatMm(info.nativeW)} × ${formatMm(info.nativeH)} mm</b><br>` +
    `Selected design size: <b>${formatMm(info.fitW)} × ${formatMm(info.fitH)} mm</b><br>` +
    `Fit-to-hoop max longest side: <b>${formatMm(info.fitLongestMm)} mm</b><br>` +
    `Scale estimate: <b>${info.fitPercentOfNative.toFixed(1)}%</b> of current size` +
    warning;
}

function clearHoopRulers(svg) {
  if (!svg) return;
  svg.querySelectorAll('.hoop-ruler').forEach(g => g.remove());
}

function niceRulerStep(maxMm) {
  if (maxMm <= 60) return 5;
  if (maxMm <= 180) return 10;
  if (maxMm <= 300) return 20;
  return 50;
}

function renderHoopRulers(svg) {
  if (!svg) return;
  clearHoopRulers(svg);

  const info = getDesignScaleInfo();
  if (!info.bounds || !info.fitMmPerSvg) return;

  const b = info.bounds;
  const mmPerSvg = info.fitMmPerSvg;
  const widthMm = b.w * mmPerSvg;
  const heightMm = b.h * mmPerSvg;
  const maxMm = Math.max(widthMm, heightMm);
  const stepMm = niceRulerStep(maxMm);
  const stepSvg = stepMm / mmPerSvg;
  const minorStepSvg = stepSvg / 2;

  const hoopWsvg = info.hoop.width_mm / mmPerSvg;
  const hoopHsvg = info.hoop.height_mm / mmPerSvg;

  // For now the design is centred inside the selected hoop. Later, when we add
  // placement controls, this centre can become user-adjustable without changing
  // the scale/ruler maths.
  const cx = b.x1 + b.w / 2;
  const cy = b.y1 + b.h / 2;
  const hoopRect = {
    x: cx - hoopWsvg / 2,
    y: cy - hoopHsvg / 2,
    w: hoopWsvg,
    h: hoopHsvg
  };

  // Expand the SVG viewBox to include the whole hoop frame, otherwise a small
  // design inside a larger hoop would clip the frame at the preview edge.
  const pad = Math.max(8, Math.max(hoopWsvg, hoopHsvg) * 0.035);
  const vbX = Math.min(0, hoopRect.x - pad, b.x1 - pad);
  const vbY = Math.min(0, hoopRect.y - pad, b.y1 - pad);
  const vbX2 = Math.max(structureSvgW, hoopRect.x + hoopRect.w + pad, b.x2 + pad);
  const vbY2 = Math.max(structureSvgH, hoopRect.y + hoopRect.h + pad, b.y2 + pad);
  svg.setAttribute('viewBox', `${vbX.toFixed(2)} ${vbY.toFixed(2)} ${(vbX2 - vbX).toFixed(2)} ${(vbY2 - vbY).toFixed(2)}`);

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('class', 'hoop-ruler');
  g.setAttribute('pointer-events', 'none');

  const sizeBase = Math.max(hoopRect.w, hoopRect.h, b.w, b.h);
  const fontSize = Math.max(7, Math.min(18, sizeBase * 0.018));
  const tickMajor = Math.max(4, Math.min(18, sizeBase * 0.014));
  const tickMinor = tickMajor * 0.55;

  const addLine = (x1, y1, x2, y2, cls='') => {
    const l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    l.setAttribute('x1', x1.toFixed(2));
    l.setAttribute('y1', y1.toFixed(2));
    l.setAttribute('x2', x2.toFixed(2));
    l.setAttribute('y2', y2.toFixed(2));
    if (cls) l.setAttribute('class', cls);
    g.appendChild(l);
  };

  const addText = (x, y, txt, anchor='middle', rotate=false) => {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', x.toFixed(2));
    t.setAttribute('y', y.toFixed(2));
    t.setAttribute('font-size', fontSize.toFixed(2));
    t.setAttribute('text-anchor', anchor);
    if (rotate) t.setAttribute('transform', `rotate(-90 ${x.toFixed(2)} ${y.toFixed(2)})`);
    t.textContent = txt;
    g.appendChild(t);
  };

  // Full selected hoop/frame overlay.
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  bg.setAttribute('class', 'hoop-frame-bg');
  bg.setAttribute('x', hoopRect.x.toFixed(2));
  bg.setAttribute('y', hoopRect.y.toFixed(2));
  bg.setAttribute('width', hoopRect.w.toFixed(2));
  bg.setAttribute('height', hoopRect.h.toFixed(2));
  g.appendChild(bg);

  const frame = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  frame.setAttribute('class', 'hoop-frame');
  frame.setAttribute('x', hoopRect.x.toFixed(2));
  frame.setAttribute('y', hoopRect.y.toFixed(2));
  frame.setAttribute('width', hoopRect.w.toFixed(2));
  frame.setAttribute('height', hoopRect.h.toFixed(2));
  g.appendChild(frame);

  // Rulers remain tied to the selected design size, not to screen zoom.
  addLine(b.x1, b.y1, b.x2, b.y1, 'ruler-base');
  addLine(b.x1, b.y1, b.x1, b.y2, 'ruler-base');

  for (let x = b.x1, mm = 0; x <= b.x2 + 0.001; x += minorStepSvg, mm += stepMm / 2) {
    const isMajor = Math.abs(mm / stepMm - Math.round(mm / stepMm)) < 0.001;
    const tick = isMajor ? tickMajor : tickMinor;
    addLine(x, b.y1, x, b.y1 + tick);
    if (isMajor && mm > 0 && mm <= widthMm + 0.5) {
      addText(x, b.y1 + tick + fontSize + 1, String(Math.round(mm)));
    }
  }

  for (let y = b.y1, mm = 0; y <= b.y2 + 0.001; y += minorStepSvg, mm += stepMm / 2) {
    const isMajor = Math.abs(mm / stepMm - Math.round(mm / stepMm)) < 0.001;
    const tick = isMajor ? tickMajor : tickMinor;
    addLine(b.x1, y, b.x1 + tick, y);
    if (isMajor && mm > 0 && mm <= heightMm + 0.5) {
      addText(b.x1 + tick + fontSize + 1, y + fontSize * 0.35, String(Math.round(mm)), 'middle', true);
    }
  }

  svg.appendChild(g);
}


function previewSvgShell() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function svgAddPolyline(svg, points, color, width, opacity, dash='') {
  if (!points || points.length < 2) return;
  const pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  pl.setAttribute('points', points.map(p => `${Number(p[0]).toFixed(2)},${Number(p[1]).toFixed(2)}`).join(' '));
  pl.setAttribute('fill', 'none');
  pl.setAttribute('stroke', color || '#000000');
  pl.setAttribute('stroke-width', String(width));
  pl.setAttribute('stroke-opacity', String(opacity));
  pl.setAttribute('stroke-linecap', 'round');
  pl.setAttribute('stroke-linejoin', 'round');
  pl.setAttribute('vector-effect', 'non-scaling-stroke');
  if (dash) pl.setAttribute('stroke-dasharray', dash);
  svg.appendChild(pl);
}

function shouldShowStitchDots() {
  return document.getElementById('show-stitch-dots')?.checked === true;
}

function svgDotColorForLine(color) {
  const h = (color || '#000000').replace('#', '');
  if (h.length !== 6) return '#111111';
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum < 70 ? '#f4f4f4' : '#111111';
}

function svgAddStitchDots(svg, points, color, radius=1.15, opacity=0.75, maxDots=25000) {
  if (!svg || !points || !points.length) return 0;
  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('pointer-events', 'none');
  g.setAttribute('class', 'stitch-point-dots');
  const fill = svgDotColorForLine(color);
  let count = 0;

  // The preview polylines already contain generated stitch vertices, so dots
  // at those vertices make the actual stitch length/density visible.
  for (const p of points) {
    if (count >= maxDots) break;
    if (!p || p.length < 2) continue;
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', Number(p[0]).toFixed(2));
    c.setAttribute('cy', Number(p[1]).toFixed(2));
    c.setAttribute('r', String(radius));
    c.setAttribute('fill', fill);
    c.setAttribute('fill-opacity', String(opacity));
    c.setAttribute('stroke', color || '#000000');
    c.setAttribute('stroke-opacity', '0.45');
    c.setAttribute('stroke-width', '0.35');
    g.appendChild(c);
    count += 1;
  }

  if (count) svg.appendChild(g);
  return count;
}

function renderCachedStitchPreview() {
  if (!currentStitchPreview) return false;

  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return false;

  wrap.innerHTML = previewSvgShell(); restorePreviewBg('stitch-preview');
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  renderHoopRulers(svg);
  const mode = previewLayerMode || 'both';
  const showUnderlay = mode === 'both' || mode === 'underlay';
  const showTop = mode === 'both' || mode === 'top';

  const layers = currentStitchPreview.layers || {};
  let count = 0;

  if (showUnderlay) {
    for (const line of layers.underlay || []) {
      svgAddPolyline(svg, line.points, line.color, line.width || 0.75, line.opacity || 0.55, line.dash || '');
      if (shouldShowStitchDots()) svgAddStitchDots(svg, line.points, line.color, 0.95, 0.45);
      count += 1;
    }
  }

  if (showTop) {
    for (const line of layers.top || []) {
      svgAddPolyline(svg, line.points, line.color, Math.max(line.width || 0.85, 1.05), Math.max(line.opacity || 0.9, 0.98), line.dash || '');
      if (shouldShowStitchDots()) svgAddStitchDots(svg, line.points, line.color, 1.15, 0.78);
      count += 1;
    }
    if (currentStitchPreview.debug_svg) {
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.innerHTML = currentStitchPreview.debug_svg;
      svg.appendChild(g);
    }
  }

  setWorkZoom(workZoom);
  renderManualRungOverlay();

  const meta = document.getElementById('stitch-preview-meta');
  if (meta) {
    const c = currentStitchPreview.counts || {};
    meta.textContent =
      `Preview ${mode}: ${count} line(s) · ` +
      `${c.underlay_edge_lines || 0} edge underlay · ` +
      `${c.underlay_fill_lines || 0} underlay fill · ` +
      `${c.top_fill_lines || 0} top fill · ` +
      `${c.satin_bars || 0} satin bars` +
      (c.design_scale_applied ? ` · scaled ${(c.target_width_mm || 0).toFixed(1)}×${(c.target_height_mm || 0).toFixed(1)}mm` : '') +
      ((c.cut_guide_rungs || 0) ? ` · cut rungs ${c.cut_guide_rungs}` : '');
  }

  return true;
}

function updateUnderlayUi(render=true) {
  const on = document.getElementById('underlay-enable')?.checked ?? true;
  const box = document.getElementById('underlay-settings');
  if (box) {
    box.style.opacity = on ? '1' : '0.35';
    box.style.pointerEvents = on ? '' : 'none';
  }

  const layerSelect = document.getElementById('preview-layer-mode');
  if (layerSelect) {
    if (!on && previewLayerMode !== 'top') {
      previewLayerMode = 'top';
      layerSelect.value = 'top';
    } else if (on && previewLayerMode === 'top') {
      previewLayerMode = 'underlay';
      layerSelect.value = 'underlay';
    } else {
      layerSelect.value = previewLayerMode || 'both';
    }
  }

  if (render) renderCachedStitchPreview();
}

function cleanManualRungsPayload() {
  const existing = new Set(stitchObjects.map(o => o.id));
  const out = {};
  Object.entries(stitchManualRungs).forEach(([id, rungs]) => {
    if (!existing.has(id)) return;
    const clean = (rungs || []).filter(r => r && r.a && r.b && r.a.length === 2 && r.b.length === 2);
    if (clean.length) out[id] = clean;
  });
  return out;
}

function currentStitchSettings() {
  const scaleInfo = getDesignScaleInfo();
  const svgToMm = (scaleInfo && scaleInfo.fitMmPerSvg) ? scaleInfo.fitMmPerSvg : (25.4 / 96.0);
  const effectiveDpi = 25.4 / Math.max(0.000001, svgToMm);

  const designScale = scaleInfo && scaleInfo.bounds ? {
    svg_to_mm: svgToMm,
    effective_dpi: effectiveDpi,
    target_width_mm: scaleInfo.fitW,
    target_height_mm: scaleInfo.fitH,
    target_longest_mm: scaleInfo.targetLongestMm,
    hoop_width_mm: scaleInfo.hoop.width_mm,
    hoop_height_mm: scaleInfo.hoop.height_mm,
    hoop_label: scaleInfo.hoop.label,
    bounds_svg: scaleInfo.bounds,
    scaling_applied: true
  } : {
    svg_to_mm: 25.4 / 96.0,
    effective_dpi: 96.0,
    scaling_applied: false
  };

  return {
    fill_angle: parseFloat(document.getElementById('stitch-fill-angle')?.value || '45'),
    auto_fill_direction: document.getElementById('auto-fill-direction-enable')?.checked ?? true,
    auto_fill_threshold: parseFloat(document.getElementById('auto-fill-threshold')?.value || '2.0'),
    stitch_order_mode: document.getElementById('stitch-order-mode')?.value || 'quality',
    avoid_top_fill_overlap: document.getElementById('avoid-top-fill-overlap-enable')?.checked ?? true,
    underlay_protect_lighter: document.getElementById('underlay-protect-lighter-enable')?.checked ?? true,
    underlay_light_threshold: parseFloat(document.getElementById('underlay-light-threshold')?.value || '45'),
    underlay_jump_trim_threshold_mm: parseFloat(document.getElementById('underlay-jump-trim-threshold-mm')?.value || '5'),
    row_spacing_mm: parseFloat(document.getElementById('stitch-row-spacing')?.value || '0.4'),
    stitch_length_mm: parseFloat(document.getElementById('stitch-length-mm')?.value || '2.5'),
    jump_trim_threshold_mm: parseFloat(document.getElementById('jump-trim-threshold-mm')?.value || '3.0'),
    satin_spacing_mm: parseFloat(document.getElementById('satin-spacing-mm')?.value || '0.45'),
    satin_max_width_mm: parseFloat(document.getElementById('satin-max-width-mm')?.value || '7.0'),
    satin_end_extra_rungs: parseInt(document.getElementById('satin-end-extra-rungs')?.value || '2', 10),
    satin_use_guide_helper: document.getElementById('satin-guide-helper-enable')?.checked ?? false,
    satin_debug_rails: document.getElementById('satin-debug-rails-enable')?.checked ?? false,
    enable_underlay: document.getElementById('underlay-enable')?.checked ?? true,
    underlay_inset_mm: parseFloat(document.getElementById('underlay-inset-mm')?.value || '0.8'),
    underlay_row_mm: parseFloat(document.getElementById('underlay-row-mm')?.value || '1.6'),

    // Effective DPI is the bridge between SVG units and real embroidery mm.
    // Geometry remains in SVG coordinates, but spacing and export conversion
    // now use the selected Pane 4 design size.
    dpi: effectiveDpi,
    design_scale: designScale
  };
}


function selectedStitchObject() {
  return stitchObjects.find(o => o.id === stitchSelectedId) || null;
}

function selectedManualRungTarget() {
  const obj = selectedStitchObject();
  if (!obj) return null;
  if ((stitchAssignments[obj.id] || 'fill') !== 'satin') return null;
  return obj;
}

function updateManualRungStatus() {
  const el = document.getElementById('manual-rung-status');
  if (!el) return;
  const obj = selectedManualRungTarget();
  if (!obj) {
    el.textContent = 'Manual rung target: none';
    return;
  }
  const count = (stitchManualRungs[obj.id] || []).length;
  el.textContent = `Manual rung target: ${obj.label || obj.id} · ${count} guide rung(s)`;
}

function toggleManualRungMode() {
  if (!stitchLoaded) {
    loadStitchPane();
    if (!stitchLoaded) return;
  }
  manualRungMode = !manualRungMode;
  pendingManualRungPoint = null;
  draggingManualRung = null;
  const btn = document.getElementById('manual-rung-mode-btn');
  if (btn) btn.textContent = manualRungMode ? 'Manual rung mode: on' : 'Manual rung mode: off';
  renderManualRungOverlay();
  updateManualRungStatus();
  toast(manualRungMode ? 'Manual rung mode on: first click chooses the satin path, second click finishes the guide rung' : 'Manual rung mode off');
}

function clearSelectedManualRungs() {
  const obj = selectedManualRungTarget();
  if (!obj) {
    toast('Select a satin object first');
    return;
  }
  delete stitchManualRungs[obj.id];
  pendingManualRungPoint = null;
  draggingManualRung = null;
  renderManualRungOverlay();
  updateManualRungStatus();
  toast('Cleared manual rungs for selected satin object');
}

function clearAllManualRungs() {
  stitchManualRungs = {};
  pendingManualRungPoint = null;
  draggingManualRung = null;
  renderManualRungOverlay();
  updateManualRungStatus();
  toast('Cleared all manual rungs');
}

function getPreviewSvg() {
  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return null;
  return wrap.querySelector('svg');
}

function stitchPreviewPoint(evt) {
  const svg = getPreviewSvg();
  if (!svg) return null;
  return svgPointFromMouse(svg, evt);
}

function ensureManualRungs(id) {
  if (!stitchManualRungs[id]) stitchManualRungs[id] = [];
  return stitchManualRungs[id];
}

function objectTransformString(obj) {
  if (obj.tx || obj.ty) return `translate(${obj.tx || 0},${obj.ty || 0})`;
  return '';
}

function findSatinObjectAtPoint(pt) {
  const svg = getPreviewSvg();
  if (!svg || !pt) return null;

  const ordered = stitchObjects.slice().sort((a, b) => (b.order || 0) - (a.order || 0));
  const selected = selectedManualRungTarget();
  if (selected) {
    const idx = ordered.findIndex(o => o.id === selected.id);
    if (idx >= 0) {
      ordered.splice(idx, 1);
      ordered.unshift(selected);
    }
  }

  for (const obj of ordered) {
    if ((stitchAssignments[obj.id] || 'fill') !== 'satin') continue;
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', obj.d);
    const tr = objectTransformString(obj);
    if (tr) p.setAttribute('transform', tr);
    p.setAttribute('fill-rule', 'evenodd');
    p.setAttribute('clip-rule', 'evenodd');

    try {
      const local = new DOMPoint(pt.x, pt.y);
      if (p.isPointInFill && p.isPointInFill(local)) return obj;
    } catch (e) {}

    try {
      p.setAttribute('fill', 'transparent');
      p.setAttribute('stroke', 'none');
      svg.appendChild(p);
      const bb = p.getBBox();
      p.remove();
      if (pt.x >= bb.x && pt.x <= bb.x + bb.width && pt.y >= bb.y && pt.y <= bb.y + bb.height) {
        return obj;
      }
    } catch (e) {
      try { p.remove(); } catch (_) {}
    }
  }
  return selected || null;
}

function renderManualRungOverlay() {
  const svg = getPreviewSvg();
  if (!svg) return;

  const existing = svg.querySelector('#manual-rung-overlay');
  if (existing) existing.remove();

  const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('id', 'manual-rung-overlay');
  g.setAttribute('pointer-events', 'all');

  const obj = selectedManualRungTarget();
  const selectedId = obj ? obj.id : null;

  Object.entries(stitchManualRungs).forEach(([objId, rungs]) => {
    const isSel = objId === selectedId;
    const objForLabel = stitchObjects.find(o => o.id === objId);
    rungs.forEach((rung, idx) => {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', rung.a[0]);
      line.setAttribute('y1', rung.a[1]);
      line.setAttribute('x2', rung.b[0]);
      line.setAttribute('y2', rung.b[1]);
      line.setAttribute('stroke', isSel ? '#ff2bd6' : '#b46cff');
      line.setAttribute('stroke-width', isSel ? '2.2' : '1.2');
      line.setAttribute('stroke-opacity', isSel ? '0.95' : '0.45');
      line.setAttribute('stroke-linecap', 'round');
      line.setAttribute('vector-effect', 'non-scaling-stroke');
      g.appendChild(line);

      const midx = (rung.a[0] + rung.b[0]) / 2;
      const midy = (rung.a[1] + rung.b[1]) / 2;
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', midx + 4);
      label.setAttribute('y', midy - 4);
      label.setAttribute('fill', isSel ? '#ff2bd6' : '#b46cff');
      label.setAttribute('font-size', '9');
      label.setAttribute('font-family', 'monospace');
      label.setAttribute('stroke', '#111');
      label.setAttribute('stroke-width', '0.25');
      label.setAttribute('paint-order', 'stroke');
      label.setAttribute('vector-effect', 'non-scaling-stroke');
      label.textContent = objForLabel ? (objForLabel.label || objId) : objId;
      g.appendChild(label);

      ['a', 'b'].forEach((endKey) => {
        const pt = rung[endKey];
        const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        c.setAttribute('cx', pt[0]);
        c.setAttribute('cy', pt[1]);
        c.setAttribute('r', isSel ? '3.2' : '2.2');
        c.setAttribute('fill', endKey === 'a' ? '#ffea00' : '#ff2bd6');
        c.setAttribute('stroke', '#111');
        c.setAttribute('stroke-width', '0.8');
        c.setAttribute('vector-effect', 'non-scaling-stroke');
        c.style.cursor = isSel ? 'grab' : 'default';
        if (isSel) {
          c.addEventListener('mousedown', (ev) => {
            ev.stopPropagation();
            draggingManualRung = {objId, idx, endKey};
            c.style.cursor = 'grabbing';
          });
        }
        g.appendChild(c);
      });
    });
  });

  if (pendingManualRungPoint) {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', pendingManualRungPoint[0]);
    c.setAttribute('cy', pendingManualRungPoint[1]);
    c.setAttribute('r', '3.5');
    c.setAttribute('fill', '#ffea00');
    c.setAttribute('stroke', '#111');
    c.setAttribute('stroke-width', '0.8');
    c.setAttribute('vector-effect', 'non-scaling-stroke');
    g.appendChild(c);
  }

  svg.appendChild(g);

  svg.onmousemove = (ev) => {
    if (!draggingManualRung) return;
    const p = stitchPreviewPoint(ev);
    if (!p) return;
    const rungs = stitchManualRungs[draggingManualRung.objId] || [];
    const rung = rungs[draggingManualRung.idx];
    if (!rung) return;
    rung[draggingManualRung.endKey] = [p.x, p.y];
    renderManualRungOverlay();
  };

  svg.onmouseup = () => {
    draggingManualRung = null;
    renderManualRungOverlay();
  };

  svg.onmouseleave = () => {
    draggingManualRung = null;
    renderManualRungOverlay();
  };

  svg.onclick = (ev) => {
    if (!manualRungMode) return;
    if (draggingManualRung) return;

    const p = stitchPreviewPoint(ev);
    if (!p) return;
    ev.stopPropagation();

    if (!pendingManualRungPoint) {
      const hitObj = findSatinObjectAtPoint(p);
      if (!hitObj) {
        toast('Click inside a satin object to start a manual rung');
        return;
      }
      stitchSelectedId = hitObj.id;
      stitchCheckedIds.clear();
      pendingManualRungPoint = [p.x, p.y];
      renderStitchList();
      updateStitchDetail();
      updateManualRungStatus();
      renderManualRungOverlay();
      toast('Manual rung target: ' + (hitObj.label || hitObj.id) + '. Pick the opposite side.');
      return;
    }

    const objNow = selectedManualRungTarget();
    if (!objNow) {
      pendingManualRungPoint = null;
      toast('Select a satin object first');
      renderManualRungOverlay();
      return;
    }

    const rungs = ensureManualRungs(objNow.id);
    rungs.push({a: pendingManualRungPoint, b: [p.x, p.y]});
    pendingManualRungPoint = null;
    updateManualRungStatus();
    renderManualRungOverlay();
    toast('Manual guide rung added to ' + (objNow.label || objNow.id) + '. Preview stitches to apply.');
  };

  updateManualRungStatus();
}

async function previewStitches() {
  if (!stitchLoaded) {
    loadStitchPane();
    if (!stitchLoaded) return;
  }

  const wrap = document.getElementById('stitch-preview');
  const meta = document.getElementById('stitch-preview-meta');
  if (wrap) wrap.innerHTML = '<span style="color:#555">Generating stitch preview…</span>';

  try {
    const res = await fetch('/api/stitches/preview', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        svg_w: structureSvgW,
        svg_h: structureSvgH,
        objects: stitchObjects,
        assignments: stitchAssignments,
        manual_rungs: cleanManualRungsPayload(),
        settings: currentStitchSettings()
      })
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Stitch preview failed: ' + (data.error || 'unknown'), 9000);
      console.error(data.trace);
      renderStitchPreview();
      return;
    }

    currentStitchPreview = {
      svg: data.svg,
      counts: data.counts || {},
      layers: data.layers || {},
      debug_svg: data.debug_svg || ''
    };
    renderCachedStitchPreview();
    toast('Stitch preview generated');
  } catch (e) {
    toast('Stitch preview error: ' + e, 9000);
    renderStitchPreview();
  }
}


function safeBaseFileName() {
  // Prefer the browser-selected input file name.
  try {
    const fileInput = document.getElementById('file-input') || document.querySelector('input[type="file"]');
    if (fileInput && fileInput.files && fileInput.files.length && fileInput.files[0].name) {
      return fileInput.files[0].name.replace(/\.[^.]+$/, '');
    }
  } catch (e) {}

  // Fall back to globals used by some older EasyStitch versions, if present.
  try {
    if (typeof currentFileName !== 'undefined' && currentFileName) {
      return String(currentFileName).replace(/\.[^.]+$/, '');
    }
  } catch (e) {}

  try {
    if (typeof loadedFileName !== 'undefined' && loadedFileName) {
      return String(loadedFileName).replace(/\.[^.]+$/, '');
    }
  } catch (e) {}

  return 'easystitch';
}

function downloadText(filename, text) {
  const blob = new Blob([text], {type: 'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadBase64Binary(filename, b64, mime='application/octet-stream') {
  const raw = atob(b64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  const blob = new Blob([bytes], {type: mime});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function updateExportStats(stats) {
  const el = document.getElementById('export-stats');
  if (!el) return;
  if (!stats) {
    el.innerHTML = 'No machine export yet.';
    return;
  }
  el.innerHTML =
    `<b>${stats.format || 'Export'}</b><br>` +
    `Records: ${stats.records || 0}<br>` +
    `Stitches / jumps / trims: ${stats.stitches || 0} / ${stats.jumps || 0} / ${stats.trims || 0}<br>` +
    `Colour changes: ${stats.color_changes || 0}<br>` +
    `Size: ${stats.width_mm || 0} × ${stats.height_mm || 0} mm<br>` +
    `Bounds 0.1mm: X ${stats.min_x_01mm || 0}..${stats.max_x_01mm || 0}, Y ${stats.min_y_01mm || 0}..${stats.max_y_01mm || 0}<br>` +
    `<span style="color:#9da7c4">${stats.note || ''}</span>`;
}

async function requestDstExport() {
  if (!currentStitchPlan) {
    await generateStitchPlan();
    if (!currentStitchPlan) {
      toast('No stitch plan available to export', 6000);
      return null;
    }
  }

  const filename = safeBaseFileName() + '.dst';
  const res = await fetch('/api/stitches/export_dst', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      filename,
      plan: currentStitchPlan,
      settings: currentStitchSettings()
    })
  });
  const data = await res.json();
  if (!data.ok) {
    toast('DST export failed: ' + (data.error || 'unknown'), 9000);
    console.error(data.trace);
    return null;
  }
  currentExportDebug = data.debug || null;
  updateExportStats(data.stats);
  return data;
}

async function requestJefExport() {
  if (!currentStitchPlan) {
    await generateStitchPlan();
    if (!currentStitchPlan) {
      toast('No stitch plan available to export', 6000);
      return null;
    }
  }

  const filename = safeBaseFileName() + '.jef';
  const res = await fetch('/api/stitches/export_jef', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      filename,
      plan: currentStitchPlan,
      settings: currentStitchSettings()
    })
  });
  const data = await res.json();
  if (!data.ok) {
    toast('JEF export failed: ' + (data.error || 'unknown'), 9000);
    console.error(data.trace || data);
    return null;
  }
  currentExportDebug = data.debug || null;
  updateExportStats(data.stats);
  return data;
}

async function exportStitchPlanJef() {
  try {
    const data = await requestJefExport();
    if (!data) return;
    downloadBase64Binary(data.filename || (safeBaseFileName() + '.jef'), data.jef_base64, 'application/octet-stream');
    toast('JEF exported: ' + (data.stats ? (data.stats.records + ' records') : data.filename));
  } catch (e) {
    console.error(e);
    toast('JEF export error: ' + e, 9000);
  }
}

async function requestVp3Export() {
  if (!currentStitchPlan) {
    await generateStitchPlan();
    if (!currentStitchPlan) {
      toast('No stitch plan available to export', 6000);
      return null;
    }
  }

  const filename = safeBaseFileName() + '.vp3';
  const res = await fetch('/api/stitches/export_vp3', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      filename,
      plan: currentStitchPlan,
      settings: currentStitchSettings()
    })
  });
  const data = await res.json();
  if (!data.ok) {
    toast('VP3 export failed: ' + (data.error || 'unknown'), 9000);
    console.error(data.trace || data);
    return null;
  }
  currentExportDebug = data.debug || null;
  updateExportStats(data.stats);
  return data;
}

async function exportStitchPlanVp3() {
  try {
    const data = await requestVp3Export();
    if (!data) return;
    downloadBase64Binary(data.filename || (safeBaseFileName() + '.vp3'), data.vp3_base64, 'application/octet-stream');
    toast('VP3 exported: ' + (data.stats ? (data.stats.records + ' records') : data.filename));
  } catch (e) {
    console.error(e);
    toast('VP3 export error: ' + e, 9000);
  }
}

async function exportStitchPlanDst() {
  try {
    const data = await requestDstExport();
    if (!data) return;
    downloadBase64Binary(data.filename || (safeBaseFileName() + '.dst'), data.dst_base64, 'application/octet-stream');
    toast('DST exported: ' + (data.stats ? (data.stats.records + ' records') : data.filename));
  } catch (e) {
    console.error(e);
    toast('DST export error: ' + e, 9000);
  }
}

async function saveExportDebugJson() {
  try {
    let debug = currentExportDebug;
    if (!debug) {
      const data = await requestDstExport();
      if (!data) return;
      debug = data.debug;
    }
    if (!debug) {
      toast('No export debug data available', 6000);
      return;
    }
    downloadText(
      safeBaseFileName() + '_export_debug.json',
      JSON.stringify(debug, null, 2)
    );
    toast('Export debug JSON downloaded');
  } catch (e) {
    console.error(e);
    toast('Save export debug failed: ' + e, 9000);
  }
}

function updateStitchPlanStats(stats) {
  const el = document.getElementById('stitch-plan-stats');
  if (!el) return;
  if (!stats) {
    el.textContent = 'No stitch plan generated yet.';
    return;
  }
  el.innerHTML = `
    Objects used: ${stats.objects_used || 0}<br>
    Fill / Satin objects: ${stats.fill_objects || 0} / ${stats.satin_objects || 0}<br>
    Total stitch events: ${stats.stitches || 0}<br>
    Underlay / Top stitches: ${stats.underlay_stitches || 0} / ${stats.top_stitches || 0}<br>
    Jumps / Trims / Colour changes: ${stats.jumps || 0} / ${stats.trims || 0} / ${stats.color_changes || 0}<br>
    Jump/trim threshold: ${(stats.jump_threshold_mm || 0).toFixed(1)}mm<br>
    Underlay long-jump trim: ${(stats.underlay_jump_trim_threshold_mm || 0).toFixed(1)}mm<br>
    Underlay protects lighter: ${stats.underlay_protect_lighter ? 'on' : 'off'} · threshold ${stats.underlay_light_threshold || 0}<br>
    Small gap fill: ${(stats.small_gap_fill_mm || 0).toFixed(1)}mm<br>
    Satin underlay: ${stats.satin_underlay_mode || 'contour_centerline'}<br>
    Satin top order: ${stats.satin_top_order || 'zigzag_ladder'}<br>
    Top fill order: ${stats.top_fill_order || 'lane_serpentine'}<br>
    Long jump connector: ${stats.long_jump_connector_policy || 'hidden-if-safe'}<br>
    Manual/cut guide rungs: ${stats.manual_rungs || 0} · from cuts ${stats.cut_guide_rungs || 0}<br>
    Auto fill direction objects: ${stats.auto_fill_direction_objects || 0}<br>
    Avoid top fill overlap: ${stats.avoid_top_fill_overlap ? 'on' : 'off'}<br>
    Estimated size: ${(stats.estimated_width_mm || 0).toFixed(1)}mm × ${(stats.estimated_height_mm || 0).toFixed(1)}mm<br>
    Scale applied: ${stats.design_scale_applied ? 'yes' : 'no'}${stats.effective_dpi ? ' · effective DPI ' + stats.effective_dpi.toFixed(1) : ''}
  `;
}

function stitchPlanSvgMarkup() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${structureSvgW}" height="${structureSvgH}" viewBox="0 0 ${structureSvgW} ${structureSvgH}"></svg>`;
}

function appendSvgLine(svg, a, b, color, width, opacity, dash='') {
  const ln = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  ln.setAttribute('x1', a.x);
  ln.setAttribute('y1', a.y);
  ln.setAttribute('x2', b.x);
  ln.setAttribute('y2', b.y);
  ln.setAttribute('stroke', color);
  ln.setAttribute('stroke-width', String(width));
  ln.setAttribute('stroke-opacity', String(opacity));
  ln.setAttribute('stroke-linecap', 'round');
  ln.setAttribute('vector-effect', 'non-scaling-stroke');
  if (dash) ln.setAttribute('stroke-dasharray', dash);
  svg.appendChild(ln);
}

function appendSvgDot(svg, p, color, r, label='') {
  const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c.setAttribute('cx', p.x);
  c.setAttribute('cy', p.y);
  c.setAttribute('r', String(r));
  c.setAttribute('fill', color);
  c.setAttribute('stroke', '#111');
  c.setAttribute('stroke-width', '0.6');
  c.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.appendChild(c);
  if (label) {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', p.x + 4);
    t.setAttribute('y', p.y - 4);
    t.setAttribute('fill', color);
    t.setAttribute('font-size', '9');
    t.setAttribute('font-family', 'monospace');
    t.setAttribute('stroke', '#111');
    t.setAttribute('stroke-width', '0.25');
    t.setAttribute('paint-order', 'stroke');
    t.setAttribute('vector-effect', 'non-scaling-stroke');
    t.textContent = label;
    svg.appendChild(t);
  }
}

function eventPoint(ev) {
  if (typeof ev.x !== 'number' || typeof ev.y !== 'number') return null;
  return {x: ev.x, y: ev.y};
}

function setupPlanPlayhead() {
  const slider = document.getElementById('plan-playhead');
  const val = document.getElementById('plan-playhead-val');
  const total = currentStitchPlan ? ((currentStitchPlan.events || []).length) : 0;
  if (slider) {
    slider.max = String(Math.max(0, total - 1));
    slider.value = String(Math.min(stitchPlanPlayIndex, Math.max(0, total - 1)));
  }
  if (val) val.textContent = `${Math.min(stitchPlanPlayIndex, Math.max(0, total - 1))}/${Math.max(0, total - 1)}`;
}

function viewFullStitchPlan() {
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }
  const events = currentStitchPlan.events || [];
  const maxIndex = Math.max(0, events.length - 1);
  stitchPlanPlayIndex = maxIndex;
  viewStitchPlan(maxIndex);
}

function viewStitchPlan(limitIndex=null) {
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }

  const events = currentStitchPlan.events || [];
  const maxEventsControl = parseInt(document.getElementById('plan-max-events')?.value || '15000', 10);
  const playLimit = limitIndex === null ? maxEventsControl : Math.max(0, Math.min(limitIndex, events.length - 1));

  const wrap = document.getElementById('stitch-preview');
  if (!wrap) return;

  wrap.innerHTML = stitchPlanSvgMarkup();
  restorePreviewBg('stitch-preview');
  const svg = wrap.querySelector('svg');
  svg.style.maxWidth = '100%';
  svg.style.maxHeight = '75vh';
  svg.style.width = 'auto';
  svg.style.height = 'auto';

  renderHoopRulers(svg);
  const showStitches = document.getElementById('plan-show-stitches')?.checked ?? true;
  const showJumps = document.getElementById('plan-show-jumps')?.checked ?? true;
  const showTrims = document.getElementById('plan-show-trims')?.checked ?? true;

  let last = null;
  let drawnStitches = 0;
  let drawnJumps = 0;
  let drawnTrims = 0;
  let processed = 0;
  let currentColor = '#000000';

  for (let idx = 0; idx < events.length; idx++) {
    if (idx > playLimit) break;
    const ev = events[idx];
    processed += 1;

    if (ev.type === 'color_change') {
      currentColor = ev.color || currentColor;
      last = null;
      continue;
    }

    if (ev.type === 'move') {
      last = eventPoint(ev);
      continue;
    }

    if (ev.type === 'jump') {
      const p = eventPoint(ev);
      if (p && last && showJumps) {
        appendSvgLine(svg, last, p, '#ff4040', 1.25, 0.9, '5 4');
        drawnJumps += 1;
      }
      if (p) last = p;
      continue;
    }

    if (ev.type === 'trim') {
      if (last && showTrims) {
        appendSvgDot(svg, last, '#ffea00', 3.6, 'trim');
        drawnTrims += 1;
      }
      last = null;
      continue;
    }

    if (ev.type === 'stitch') {
      const p = eventPoint(ev);
      if (p && last && showStitches) {
        const layer = ev.layer || '';
        const width = layer.includes('underlay') ? 0.65 : 1.0;
        const opacity = layer.includes('underlay') ? 0.55 : 0.95;
        appendSvgLine(svg, last, p, ev.color || currentColor, width, opacity);
        if (shouldShowStitchDots()) {
          const dotColor = svgDotColorForLine(ev.color || currentColor);
          appendSvgDot(svg, p, dotColor, layer.includes('underlay') ? 1.05 : 1.25, '');
        }
        drawnStitches += 1;
      }
      if (p) last = p;
      continue;
    }
  }

  // Current needle marker.
  const currentEv = events[Math.min(playLimit, events.length - 1)];
  const currentPt = currentEv ? eventPoint(currentEv) : null;
  if (currentPt) appendSvgDot(svg, currentPt, '#00ff66', 4.2, 'needle');

  const meta = document.getElementById('stitch-preview-meta');
  if (meta) {
    meta.textContent = `Plan view: ${drawnStitches} stitches · ${drawnJumps} jumps · ${drawnTrims} trims · processed ${processed}/${events.length}`;
  }

  stitchPlanPlayIndex = Math.min(playLimit, Math.max(0, events.length - 1));
  setupPlanPlayhead();
  setWorkZoom(workZoom);
}

function seekStitchPlan(index) {
  pauseStitchPlan(false);
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }
  stitchPlanPlayIndex = Math.max(0, Math.min(index || 0, (currentStitchPlan.events || []).length - 1));
  viewStitchPlan(stitchPlanPlayIndex);
}

function playStitchPlan() {
  if (!currentStitchPlan) {
    toast('Generate a stitch plan first');
    return;
  }
  if (stitchPlanPlayTimer) clearInterval(stitchPlanPlayTimer);

  const events = currentStitchPlan.events || [];
  const total = events.length;
  if (!total) return;

  // Step size scales with plan size so playback is usable even for 50k+ events.
  const step = Math.max(25, Math.ceil(total / 700));
  stitchPlanPlayTimer = setInterval(() => {
    stitchPlanPlayIndex += step;
    if (stitchPlanPlayIndex >= total - 1) {
      stitchPlanPlayIndex = total - 1;
      pauseStitchPlan(false);
    }
    viewStitchPlan(stitchPlanPlayIndex);
  }, 80);

  toast('Playing stitch plan');
}

function pauseStitchPlan(showToast=true) {
  if (stitchPlanPlayTimer) {
    clearInterval(stitchPlanPlayTimer);
    stitchPlanPlayTimer = null;
    if (showToast) toast('Playback paused');
  }
}

async function generateStitchPlan() {
  if (!stitchLoaded) {
    loadStitchPane();
    if (!stitchLoaded) return;
  }

  try {
    const res = await fetch('/api/stitches/plan', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        svg_w: structureSvgW,
        svg_h: structureSvgH,
        objects: stitchObjects,
        assignments: stitchAssignments,
        manual_rungs: cleanManualRungsPayload(),
        settings: currentStitchSettings()
      })
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Stitch plan failed: ' + (data.error || 'unknown'), 9000);
      console.error(data.trace);
      return;
    }
    currentStitchPlan = data.plan;
    stitchPlanPlayIndex = 0;
    pauseStitchPlan(false);
    updateStitchPlanStats(currentStitchPlan.stats);
    setupPlanPlayhead();
    viewFullStitchPlan();
    toast('Stitch plan generated: ' + (currentStitchPlan.stats.stitches || 0) + ' stitch events');
  } catch (e) {
    toast('Stitch plan error: ' + e, 9000);
  }
}

async function saveStitchPlanJson() {
  try {
    if (!currentStitchPlan) {
      await generateStitchPlan();
      if (!currentStitchPlan) {
        toast('No stitch plan available to save', 6000);
        return;
      }
    }
    downloadText(
      safeBaseFileName() + '_stitch_plan.json',
      JSON.stringify(currentStitchPlan, null, 2)
    );
    toast('Stitch plan JSON downloaded');
  } catch (e) {
    console.error(e);
    toast('Save stitch plan failed: ' + e, 9000);
  }
}

function saveStitchJson() {
  if (!structureLoaded && !stitchLoaded) { toast('Load prepared structure first'); return; }
  const objectsForSave = stitchLoaded ? stitchObjects : structureObjects;
  const payload = {
    version: 1,
    source_svg: lastTrace ? lastTrace.output_path : null,
    svg_w: structureSvgW,
    svg_h: structureSvgH,
    objects: objectsForSave,
    assignments: stitchAssignments,
    manual_rungs: cleanManualRungsPayload(),
    settings: currentStitchSettings(),
    note: 'EasyStitch stitch assignment map with stitch settings. Final satin/export is the next stage.'
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'easystitch_stitch_assignments.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

function toast(msg, ms=3200) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.preview-area').forEach(area => {
    area.addEventListener('wheel', e => {
      if (e.shiftKey) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.08 : 0.08;
        setWorkZoom(workZoom + delta);
      }
    }, {passive:false});
  });
});

init();

setTimeout(ensurePreviewBgButtons, 50);
setTimeout(updateStructureToolButtons, 60);
setTimeout(updateDesignSizeInfo, 70);

setTimeout(ensurePreviewBgButtons, 100);
setTimeout(() => {
  ['prep-preview','trace-preview','structure-preview','stitch-preview'].forEach(restorePreviewBg);
}, 150);
