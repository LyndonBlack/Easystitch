# EasyStitch — Hermes Agent Handover

## Project Overview

EasyStitch converts simplified PNG/JPG artwork into machine-embroidery files (DST, JEF, VP3). It's a Flask web app with a 4-pane workflow: Image Prep → Trace → Structure Editing → Stitch & Export. The prototype was built as a single 10,399-line monolith (`easystitch_unified_app_v116_continuous_satin_zigzag.py`) and has been refactored into a modular structure.

## Refactor Status — COMPLETE ✅

The monolith was split into a modular Flask app using the golden-reference strategy (extract verbatim, compare, never rewrite). All code was copied byte-for-byte from the golden v116 monolith.

### Refactor Structure

```
Easystitch/
├── reference/
│   └── easystitch_unified_app_v116_continuous_satin_zigzag.py  (10,399 lines, UNTOUCHED)
├── refactor/
│   ├── app.py                        (359 lines — Flask shell, imports easystitch_core)
│   ├── requirements.txt
│   ├── easystitch_core/
│   │   ├── __init__.py               (re-exports 73 public symbols)
│   │   ├── utils.py                  (284 lines — colour, math, SVG helpers)
│   │   ├── image_prep.py             (210 lines — load, quantize, simplify)
│   │   ├── trace.py                  (787 lines — vtracer CLI, SVG parsing)
│   │   ├── geometry.py               (850 lines — Shapely ops, splitting)
│   │   ├── fill.py                   (653 lines — fill generation, ordering)
│   │   ├── satin.py                  (1,416 lines — rails, rungs, zigzag)
│   │   ├── underlay.py               (256 lines — underlay, blockers)
│   │   ├── stitch_plan.py            (722 lines — plan builder, preview SVG)
│   │   ├── export_dst.py             (382 lines — DST binary export)
│   │   └── export_pyembroidery.py    (583 lines — JEF/VP3 via pyembroidery)
│   ├── tests/
│   │   ├── test_manual_split.py      (12 regression checks)
│   │   └── test_satin_handoff.py     (9 regression checks)
│   └── web/
│       ├── templates/index.html      (558 lines — extracted HTML)
│       └── static/
│           ├── css/app.css           (124 lines — extracted CSS)
│           └── js/
│               ├── state.js          (43 lines — global state vars)
│               ├── tooltips.js       (56 lines — tooltip helpers)
│               └── app.js            (3,543 lines — main app logic)
├── test_assets/                      (happysun, house, puppy, sun1, smile1)
├── test_outputs/                     (golden/ + refactor/ screenshot dirs)
├── PLAN.md                           (refactor plan doc)
└── .gitignore
```

### Refactor Phases Completed

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Copy golden, set up structure | ✅ |
| 0.1 | Test assets, requirements.txt | ✅ |
| 1.1–1.10 | 10 backend modules extracted | ✅ |
| 2.1 | app.py created with easystitch_core imports | ✅ |
| 2.2 | HTML → web/templates/index.html | ✅ |
| 2.3 | CSS → web/static/css/app.css | ✅ |
| 2.4 | JS → web/static/js/app.js (single file) | ✅ |
| 2.5 | JS split into state.js + tooltips.js + app.js | ✅ |

Total: **18 files, ~11,000 lines** across backend + frontend.

### How to Run

```bash
cd ~/AI/Easystitch/refactor
python3 app.py --port 5002
# or with an image:
python3 app.py ../happysun.png --no-browser --port 5002
```

### Key Dependencies

```
flask, pillow, numpy, scikit-learn, svgpathtools, shapely, pyembroidery
# vtracer CLI binary (not Python binding): cargo install vtracer --locked
```

### Critical Traps (from golden monolith)

| Trap | Detail |
|------|--------|
| Trace segment length | Minimum 3.5px — lower crashes potrace |
| Satin zigzag | Must be continuous (no side-step bars) |
| Stitch dots | Default OFF |
| Hoop orientation | Vertical × Horizontal (260×200 = 200 wide, 260 tall) |
| Scaling | Scale geometry → effective DPI, not stitch coordinates |
| Assignment reset | Pane 3 only — must not auto-reset on Pane 4 load |
| VP3 | Beta status — trim handling fragile |
| Manual split | Cuts must pass cleanly across the geometry |
| Connector lines | `connector_geom=geom` can hide wrong-end starts |

## Known Issue: Satin Zigzag Side-Step Pattern (NOT FIXED)

### The Bug

After refactoring, the satin top stitch shows a straight line along one rail instead of a zigzag across the column. This is a **pre-existing bug in v116** (the golden monolith), not introduced by the refactor. The refactored code matches v116 byte-for-byte in this area.

### Symptoms

In the stitch plan events (viewable in exported JSON), the satin top stitches for a column do not alternate rails. Instead they step along one side of the column:

```
Expected: L→R→L→R→L→R (true zigzag)
Actual:   L→R→R→R→R→R (first cross, then all on same rail)
```

This creates a visible straight line along the satin column in the DST/JEF output.

### The Trigger

The bug is most visible when manual cuts are applied to the HappySun mouth. Splitting the mouth into 2-3 pieces (via the manual split tool) creates `cut_guide_rungs` that change how satin bars are generated. The bars with cut guide rungs are well-formed, but the continuous zigzag converter doesn't handle them properly.

The user reports this as: "the first row of satin top stitch jumps from right to left" or "the last stitch of the column jumps across to start the other side of the mouth."

### Root Cause

The function `_satin_bars_to_continuous_zigzag()` in `fill.py` (lines 564–585) converts ordered satin bars into a continuous zigzag path. The current code:

```python
def _satin_bars_to_continuous_zigzag(ordered_bars):
    bars = [list(bar) for bar in (ordered_bars or []) if bar and len(bar) >= 2]
    if not bars:
        return []
    path = [bars[0][0], bars[0][-1]]
    for bar in bars[1:]:
        path.append(bar[-1])           # ← ALWAYS appends bar[-1] (same rail!)
    # Remove duplicates
    clean = []
    for p in path:
        if not clean or math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    return clean if len(clean) >= 2 else []
```

After the first bar (which correctly crosses from one rail to the other at `bars[0][0]→bars[0][-1]`), every subsequent bar always appends `bar[-1]` — stepping along the same rail instead of alternating. For 100 bars, this produces 2 cross-column stitches (bar[0]) and 99 same-rail steps.

### Attempted Fixes (ALL FAILED — reverted)

#### Attempt 1: Rail Alternation
Changed `_satin_bars_to_continuous_zigzag` to check distance from `last` to decide whether to pick `bar[0]` or `bar[-1]`:

```python
for bar in bars[1:]:
    a, b = bar[0], bar[-1]
    da = math.hypot(a[0] - last[0], a[1] - last[1])
    db = math.hypot(b[0] - last[0], b[1] - last[1])
    nxt = b if da <= db else a
    ...
```

**Result:** Fixed the side-step for the first half of the column but created a round-trip effect where the path went from the entry point to the far end and then back to the near end. The second half of bars had their "opposite rail" on the wrong side.

#### Attempt 2: Monotonic Trim
Added post-processing to trim the path to the longest monotonic span, preventing doubling-back.

**Result:** Broke all satin paths — the trim was too aggressive on curved columns where the primary axis isn't purely horizontal or vertical.

#### Attempt 3: Preview current_pos
Changed `build_stitch_preview_svg` to track `underlay_end_pos` and pass it to `_order_satin_bars_zigzag` instead of `None`.

**Result:** The preview changed but the underlying issue wasn't fixed. Also caused JS error `cannot access local variable 'ordered_satin_preview_bars'`.

#### Attempt 4: Zigzag Entry Points for Underlay
Changed the stitch plan to compute zigzag entry points from actual candidate paths instead of raw bar endpoints.

**Result:** Shifted the jump to a different position but didn't eliminate it.

#### Attempt 5: Underlay Hint from Previous Stitch
When `current_pos` is None after a trim, walked back to find the last stitch position and used it as a hint for `_order_underlay_to_finish_near`.

**Result:** Didn't fix the fundamental issue; satin still started at the wrong end.

### What the Fix Needs

The correct fix should:

1. Make `_satin_bars_to_continuous_zigzag` alternate rails: after `bars[0][0] → bars[0][-1]`, decide for each subsequent bar whether to pick `bar[0]` or `bar[-1]` based on which endpoint continues the zigzag.

2. Handle degenerate bars at tapered column ends where both endpoints are on the same physical edge (short bars at narrow tips). These need to be detected and either aligned or skipped.

3. NOT use monotonic trimming on the overall path — the zigzag genuinely changes direction as it follows the column, and trimming will break curved shapes.

4. If the bar sequence wraps around (generated from rung A to rung B and back), detect this and only use bars in one direction.

### Key Files

- **`refactor/easystitch_core/fill.py`** — `_satin_bars_to_continuous_zigzag()` at line 564
- **`refactor/easystitch_core/stitch_plan.py`** — `build_stitch_plan()` at line 207 (stitch_satin_object inner function), `build_stitch_preview_svg()` at line 531
- **`refactor/easystitch_core/satin.py`** — `generate_satin_preview_lines()` and `generate_satin_preview_lines_with_guides()`
- **`refactor/tests/test_satin_handoff.py`** — 9 regression checks for satin behavior

### Test Images

- `test_assets/happysun.png` — Full HappySun image (best for reproducing)
- `test_assets/smile1.png` — Simplified smile-only image
- User stitch plan JSONs in `~/Downloads/happysun_stitch_plan (2).json`, `(3).json`

### User Workflow to Reproduce

1. Load HappySun
2. Trace, then load structure
3. Find the mouth/face path (s2, color #11100B)
4. Apply 2 manual cuts near the tapered ends of the smile
5. Assign all 3 pieces as Satin with underlay enabled
6. Export stitch plan JSON
7. Check events around the underlay→satin transitions (should be near events 8180–8300)
8. The first few `top_satin` events after `underlay_satin_contour_center` should zigzag across the column, not step along one rail

### Notes

- The golden monolith (v116) has the same bug — this isn't a refactor regression.
- The issue is ONLY in the continuous zigzag converter, NOT in the bar generation or ordering.
- `_order_satin_bars_zigzag()` correctly orders bars using `current_pos` — it picks the right end to start from.
- The preview (`build_stitch_preview_svg`) also has a separate issue: it calls `_order_satin_bars_zigzag(satin_lines, None)` which ignores the underlay end position. Fixing the converter first is higher priority.
