# EasyStitch Refactor Plan

## Overview

Convert the 10,399-line monolithic Flask web app (`easystitch_unified_app_v116_continuous_satin_zigzag.py`) into a modular, maintainable application with separated backend modules, frontend assets, and regression testing.

## Golden Reference

**`reference/easystitch_unified_app_v116_continuous_satin_zigzag.py`** is the single authoritative golden source.

- **Status:** Known-good working prototype — all features validated
- **Location:** `reference/` — **do not edit**
- **Root copy:** A convenience copy also exists at repo root for quick launch. The `reference/` copy is the source of truth for all behavioural comparisons.

## Tools at Our Disposal

| Tool | Purpose |
|---|---|
| **Sub-agent parallel work** (DeepSeek-v4 Flash) | Spawn independent sub-agents for module extraction, one per module, each writes their own tests |
| **Subagent-Driven-Development skill** | 2-stage review workflow per task: spec compliance then code quality |
| **Writing-Plans skill** | Structured plan authoring |
| **Ruff** | Python linting (505 issues found in monolith, mostly E501 line-too-long) |
| **Vulture** | Dead-code detection (~20 flagged, mostly Flask route false positives) |
| **Pytest** | Test harness for each extracted module |
| **Browser tool (headless)** | Screenshot comparison between golden & refactored app |
| **Local Qwen3.6 vision model** | Compare screenshot outputs without VNC (spin up on demand) |
| **Hero script** | Python AST-based import/export mapping to trace code flow |

## Directory Structure (Target)

```
Easystitch/
├── reference/
│   └── easystitch_unified_app_v116_continuous_satin_zigzag.py  (golden)
├── refactor/
│   ├── app.py
│   ├── requirements.txt
│   ├── README.md
│   ├── easystitch_core/
│   │   ├── __init__.py
│   │   ├── image_prep.py
│   │   ├── trace.py
│   │   ├── structure.py
│   │   ├── geometry.py
│   │   ├── fill.py
│   │   ├── satin.py
│   │   ├── underlay.py
│   │   ├── stitch_plan.py
│   │   ├── export_dst.py
│   │   ├── export_pyembroidery.py
│   │   ├── export_debug.py
│   │   └── utils.py
│   └── web/
│       ├── templates/
│       │   └── index.html
│       └── static/
│           ├── css/
│           │   └── app.css
│           └── js/
│               ├── state.js
│               ├── api.js
│               ├── panes.js
│               ├── pane1_prep.js
│               ├── pane2_trace.js
│               ├── pane3_structure.js
│               ├── pane4_stitch.js
│               ├── preview.js
│               ├── stitch_preview.js
│               ├── export.js
│               └── tooltips.js
├── test_assets/
│   ├── happysun.png
│   ├── house.png
│   ├── puppy.png
│   └── sun1.png
├── test_outputs/
│   ├── golden/     (baseline screenshots from golden monolith)
│   └── refactor/   (comparison screenshots from refactored app)
├── oldstuff/       (archived versions — preserved for reference)
├── PLAN.md         (this file)
└── .gitignore
```

## Refactor Phases

### Phase 0 — Create Protected Checkpoint ✅ (DONE)
- Copy v116 to `reference/` ✅
- Set up directory structure ✅
- Install lint/analysis tools ✅
- Push to GitHub ✅

### Phase 0.1 — Housekeeping (DONE)
- Populate `test_assets/` with happysun.png, house.png, puppy.png, sun1.png ✅
- Create `refactor/requirements.txt` with core dependencies ✅
- Update PLAN.md to mark `reference/` as authoritative golden source ✅
- Reference copy at repo root retained as convenience launcher

### Phase 1 — Backend Extraction Only
Move Python code into modules while keeping HTML/JS embedded if needed.

**Recommended order:**
1. `utils.py` — shared helpers, colour conversion, math utilities
2. `image_prep.py` — image loading, quantization, colour reduction
3. `trace.py` — SVG tracing (potrace/vtracer wrapper)
4. `geometry.py` — path geometry, Shapely operations, splitting
5. `fill.py` — fill stitch generation, serpentine, object avoidance
6. `underlay.py` — underlay stitch generation
7. `satin.py` — satin stitch generation, rail/rung logic
8. `stitch_plan.py` — combining fill + satin + underlay into a stitch plan
9. `export_dst.py` — DST file format export
10. `export_pyembroidery.py` — JEF and VP3 export via pyembroidery
11. `export_debug.py` — debug JSON export

After each extraction: syntax check → run app → browser screenshot compare.

### Phase 2 — Frontend Extraction
Only after backend extraction is stable. Extract:
- CSS → `web/static/css/app.css`
- HTML → `web/templates/index.html`
- JS → split into focused modules

### Phase 3 — Feature Development
Only after full modularization:
- Auto colour count
- Auto cutting / satin segmentation
- Better auto assignment
- Real VP3 fix or replacement

## Refactor Principles

1. **Extract, test, compare. Do not rewrite.**
2. **One module at a time.** Small commits after each.
3. **Behavioural equivalence** is the first goal — not improvement.
4. **Screenshot compare** after every module extraction.
5. **Never combine refactor with feature work** in one step.
6. **Use golden monolith as reference** when uncertain.
7. **Preserve behaviour before improving it.**

## Critical Traps to Avoid

- Assignment reset bug — do not reintroduce auto-initialisation on Pane 3 revisit
- Trace segment length below 3.5 px crashes potrace
- Satin side-step regression — must stay continuous zigzag
- Scaling density trap — scale geometry before stitch generation, not stitch coordinates
- Stitch dots default to off
- VP3 is Beta — don't treat as production-ready
- Hoop orientation: Vertical × Horizontal (260×200 means 200 wide, 260 tall)

## Test Images

- `happysun.png` — fill + satin + underlay + scaling test
- `house.png` — complex craft image, auto-assignment limits
- `puppy.png` — junction-heavy, manual cuts, satin segmentation
