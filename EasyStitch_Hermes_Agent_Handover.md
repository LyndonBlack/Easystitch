# EasyStitch Refactor Handover for Hermes Agent

## 1. Purpose of this handover

This document is intended to hand the EasyStitch project over to the Hermes API-based Agent system for controlled refactoring and future feature development.

The current application is a successful prototype but has grown into a large single-file monolith after many iterative changes across multiple AI systems. The next major work should be done with direct file access, terminal execution, browser automation, screenshots, and regression testing.

The key instruction is:

**Treat the current golden version as the working reference. Do not casually rewrite behaviour from memory. Compare against the golden file whenever uncertain.**

---

## 2. Golden reference version

Use this as the known-good checkpoint:

```text
easystitch_unified_app_v116_continuous_satin_zigzag.py
```

This version is the reference for:

- image preparation
- tracing
- path structure editing
- manual cuts
- cut-guide rungs
- stitch type assignment
- fill stitch generation
- satin stitch generation
- hoop/design scaling
- DST export
- JEF export
- VP3 Beta export
- stitch preview and stitch plan playback

Before refactoring, copy this file into a protected reference folder, for example:

```text
reference/
  easystitch_unified_app_v116_continuous_satin_zigzag.py
```

Do not edit the reference copy.

---

## 3. Golden-reference comparison workflow

The Hermes Agent should use the golden version as an executable oracle.

Recommended setup:

```text
worktree/
  reference/
    easystitch_unified_app_v116_continuous_satin_zigzag.py
  refactor/
    app.py
    easystitch_core/
    web/
  test_assets/
    HappySun.png
    House.png
    Puppy_or_ear_junction_test.png
  test_outputs/
    golden/
    refactor/
```

Whenever a refactor step changes behaviour, run both:

```bash
python reference/easystitch_unified_app_v116_continuous_satin_zigzag.py --port 5001
python refactor/app.py --port 5002
```

Then use browser automation to perform the same workflow in both apps and capture screenshots.

Required comparison screenshots:

```text
Pane 1 prepared image
Pane 2 trace preview
Pane 3 path structure preview
Pane 4 stitch preview
Pane 4 generated stitch plan view
Export stats panel after DST export
```

Keep screenshot names consistent:

```text
test_outputs/golden/happysun_pane4_stitch_preview.png
test_outputs/refactor/happysun_pane4_stitch_preview.png
```

The first goal of refactoring is **behavioural equivalence**, not improvement.

---

## 4. Current working capabilities

### 4.1 Image preparation

Pane 1 currently supports:

- image upload
- colour reduction
- smoothing/simplification controls
- background handling
- output preview

Recent UI cleanups:

- Posterize Bits slider removed
- colour count range reduced to 2–20

Future work planned:

- automatic colour count selection
- better flattening of tiny shading differences

Do not add auto colour count during the first refactor. Preserve current manual behaviour first.

---

### 4.2 Trace stage

Pane 2 currently supports tracing with several modes.

Current important setting:

```text
Trace mode default: Polygon / simpler edges
Segment length default: 3.5 px
```

Important trap:

```text
Potrace segment length must be within 3.5–10.
```

We previously tried 3.0 px to get 25% more polygons, but the tracer panicked:

```text
Segment length is invalid at 3. it must be within 3.5,10
```

So 3.5 px is the safe lower limit.

Pane 2 now auto-runs **Run Trace** the first time it opens after image prep.

QA check:

- Prepare an image in Pane 1.
- Open Pane 2.
- Confirm Run Trace automatically starts once.
- Confirm it does not repeatedly auto-run after returning from later panes.
- Confirm trace output appears without error in all trace modes.

---

### 4.3 Path structure editing

Pane 3 is now the main manual editing and stitch assignment stage.

Current behaviours:

- Load current trace into path structure.
- Paths can be assigned:
  - Fill
  - Satin
  - Skip
- Assignment tools behave like persistent tools.
- Manual split behaves like a persistent tool.
- First manual-split click selects target path.
- Second click starts cut.
- Third click ends cut.
- Failed/secondary split mode can still follow.

Important resolved issue:

A hidden function was repeatedly reinitialising stitch assignments after cuts. This caused assignments to reset while using manual split tools. That was finally resolved by reverting to the correct earlier version and removing the problematic assignment initialiser.

Critical warning:

**Do not reintroduce automatic assignment resets on Pane 3 revisit, manual split, or Pane 4 load.**

Current intended auto assignment behaviour:

- Pane 3 can run Auto Guess manually.
- Pane 3 may initialise assignments when first loaded from current trace.
- Pane 4 must never auto-change Fill/Satin/Skip assignments.

Known limitation:

Auto Guess is not reliable enough for complex images and should not be the basis of future workflow until reworked later.

---

### 4.4 Manual split and cut-guide rungs

Manual split/cuts now also provide cut-guide rung information for satin paths.

Important behaviour:

- A manual cut through a satin column can become the start/end rung for the resulting satin section.
- This works especially well on the HappySun smile and similar curved satin paths.
- Pane 4 preview must display correctly and not error with variables such as `satin_show_rails`.

Previously fixed bug:

```text
Stitch preview failed: name 'satin_show_rails' not defined
```

Current expectation:

- Manual split cut guides must survive from Pane 3 to Pane 4.
- Cut-derived rungs should influence satin rail/rung generation.
- Manual rungs should still work in Pane 4.

---

### 4.5 Junction cut tool

A manual junction cut tool was started.

Status:

```text
Leave it in place, but do not rely on it yet.
```

The tool shows promise but needs more work. It is lower priority than refactoring, auto cutting, and real-world stitch tests.

Future idea:

- A manual multi-branch junction splitter:
  - first click = centre
  - subsequent clicks = branches
  - double click = finish
- Later automation:
  - detect likely Y/T/intersection junctions
  - ask/confirm split count
  - generate branch cuts and guide rungs

Do not develop this further until the app is modular.

---

## 5. Stitch generation state

### 5.1 Fill stitch behaviour

Current fill behaviour includes:

- top fill object avoidance
- serpentine/lane fill to minimise jumps and trims
- top fill avoids different-colour objects
- top fill tries to avoid crossing internal objects
- long reposition moves should trim where appropriate
- underlay remains structurally more linear, but now avoids lighter objects in some cases

Important improvements already made:

- Fill no longer jumps repeatedly across eyes/mouth/eyebrows in the HappySun example.
- Fill uses serpentine segments to reduce unnecessary trims and jumps.
- Small gap/corner fill now uses a fixed minimum of:

```text
0.5 mm
```

This changed from 1.0 mm because small scaled designs needed better corner coverage.

---

### 5.2 Underlay behaviour

Current underlay behaviour:

- fill objects use edge walk plus sparse fill
- satin objects use contour/centre-ish underlay
- underlay can protect lighter-colour objects
- underlay long-jump trim threshold default:

```text
5.0 mm
```

Top-layer jump/trim threshold default:

```text
3.0 mm
```

Important distinction:

Do not confuse the underlay long-jump trim setting with the top-layer jump/trim threshold. We intentionally restored top trim to 3.0 mm and reduced underlay trim from 10.0 mm to 5.0 mm.

---

### 5.3 Satin stitch behaviour

Current satin status is much improved.

Important final fix:

The satin top stitch was changed from side-stepping bars to a continuous zigzag.

Previous incorrect behaviour:

```text
cross column
small same-side step
cross column
small same-side step
```

Corrected behaviour:

```text
left rail → right rail → left rail → right rail
```

Current key version:

```text
v116_continuous_satin_zigzag
```

This is important. Do not regress to the earlier “small side-step” satin.

Satin settings:

- Satin Spacing controls the zigzag density / distance to next opposite rail point.
- Extra rungs default to 0.
- Manual/cut rungs can define start/end rungs.

Known future work:

- automatic cutting / segmentation of large/branched satin paths
- better rail pairing for complex shapes
- satin width warning after real machine testing

---

## 6. Hoop and scaling system

Pane 4 has the current hoop/design scale controls.

Hoop selector options:

```text
120 × 120 mm
260 × 200 mm (V × H)
360 × 200 mm (V × H)
```

Important convention:

The machine convention here is:

```text
Vertical × Horizontal
```

Internally:

```text
260 × 200 mm means 200 mm wide, 260 mm tall
360 × 200 mm means 200 mm wide, 360 mm tall
```

Pane 4 includes:

- hoop frame overlay
- mm rulers
- Design longest side slider
- exact numeric input
- scaling display
- selected design size display

Current scaling logic:

- geometry stays in SVG coordinates
- selected design size determines effective DPI
- backend uses effective DPI for:
  - stitch length
  - fill row spacing
  - satin spacing
  - underlay spacing
  - DST/JEF/VP3 coordinate conversion

Important principle:

**Do not scale finished stitches like an image. Scale the geometry relationship before stitch generation by using effective DPI, then regenerate stitches at normal real-world spacing.**

Current behaviour:

- changing slider updates visual scale/rulers
- pressing Enter in the numeric design-size field triggers Preview Stitches
- sliding does not auto-regenerate stitches to avoid overloading

QA check:

- Use HappySun.
- Set 120 × 120 hoop.
- Scale design to 120 mm, preview/export.
- Scale design to 50 mm, press Enter, preview/export.
- Confirm stitch count decreases naturally.
- Confirm fill density remains physically reasonable rather than simply shrinking an old dense stitch plan.

---

## 7. Export status

### 7.1 DST

DST is the known-good baseline.

Current DST behaviour:

- exports correctly
- online viewers show expected stitch structure
- scale works
- trim/jump events appear acceptable in available viewers

Important limitation:

DST has limited rich metadata and trims are encoded as jump hints. Some viewers may show connector lines even if the machine trims.

Still, DST is currently the validation baseline.

---

### 7.2 JEF

JEF support has been added via `pyembroidery`.

Status:

```text
Better than VP3 in viewer tests.
Needs real machine testing.
```

Known viewer oddity:

Some viewers appear to leave off the final stitch near trims, but JEF does not show the erratic VP3 movements seen earlier.

Use JEF as a second real-machine test format.

---

### 7.3 VP3 Beta

VP3 is native/preferred for the target Husqvarna Viking / Pfaff-style workflow, but current export through `pyembroidery` is marked Beta.

Button label:

```text
Export VP3 Beta
```

Observed issue:

- VP3 viewer output had missing stitches around trim boundaries.
- It also showed odd erratic movements.
- Command-position fix and trim-anchor workaround only partly improved the issue.

Conclusion:

Do not treat VP3 as production-ready yet. Keep it for testing only.

Important note:

`pyembroidery` can write VP3, but trim handling appears fragile in this workflow. Avoid piling more hacks into VP3 until real machine behaviour and/or a better VP3 writer strategy is understood.

---

### 7.4 Target machine accepted formats

The target machine accepts:

```text
.VP3  native/preferred
.HUS
.SHV
.SH7
.DST
.EXP
.JEF
.PEC
```

Current app supports:

```text
DST
JEF
VP3 Beta
```

Possible future secondaries:

- EXP may be easy through `pyembroidery`, but may not add much over DST.
- PEC/PES may be possible depending on library support and machine behaviour.
- HUS/SHV/SH7 should not be attempted until a reliable writer/library route is identified.

---

## 8. Known external dependency notes

### 8.1 pyembroidery

JEF and VP3 export use:

```bash
pip install pyembroidery
```

User environment example:

```text
pyembroidery 1.5.1
Python 3.14 user install
```

Important implementation detail:

`pyembroidery 1.5.1` VP3 writer expected a filesystem path, not a `BytesIO`. The exporter was patched to write a temporary file, read it back, and delete it.

Keep that temp-file approach for pyembroidery exports unless confirmed safe otherwise.

---

## 9. Critical sticky issues / traps already encountered

### 9.1 Assignment reset bug

Symptoms:

- manual split second cut reset all assignments
- assignments reinitialised unexpectedly
- debug message showed assignment init firing

Resolution:

- reverted to correct prior version
- removed problematic function
- ensured Pane 4 does not auto-assign stitch types

Trap:

Do not reintroduce assignment initialisation except at intentional load/Auto Guess points.

---

### 9.2 Trace segment length crash

Bad value:

```text
3.0 px
```

Error:

```text
Segment length is invalid at 3. it must be within 3.5,10
```

Safe value:

```text
3.5 px
```

---

### 9.3 Satin side-step regression

The continuous zigzag fix is crucial.

If satin output shows small rectangular side-steps along a rail, it has regressed.

Expected satin visual:

```text
tight zigzag across column
```

Not:

```text
ladder rung plus side rail travel
```

---

### 9.4 Scaling density trap

Do not scale final stitch coordinates only.

If output becomes too dense or too sparse after resizing, check:

```text
currentStitchSettings()
getDesignScaleInfo()
effective DPI
backend dpi usage
mm_to_px()
DST/JEF coordinate conversion
```

Spacing settings must remain in real mm.

---

### 9.5 Preview dots

Stitch dots are useful at small scale but clutter larger previews.

Current default:

```text
Show stitch dots = off
```

Do not default it on.

---

### 9.6 Viewer versus machine behaviour

Some online viewers show:

- connector lines
- missing final stitch near trim
- different trim interpretation
- colour simplifications

Real machine testing is required before making definitive file-format decisions.

Use DST as baseline, then JEF, then VP3 Beta.

---

## 10. Recommended refactor target structure

Suggested structure:

```text
easystitch/
  app.py
  requirements.txt
  README.md

  easystitch_core/
    __init__.py
    image_prep.py
    trace.py
    structure.py
    geometry.py
    fill.py
    satin.py
    underlay.py
    stitch_plan.py
    export_dst.py
    export_pyembroidery.py
    export_debug.py
    utils.py

  web/
    templates/
      index.html
    static/
      css/
        app.css
      js/
        state.js
        api.js
        panes.js
        pane1_prep.js
        pane2_trace.js
        pane3_structure.js
        pane4_stitch.js
        preview.js
        stitch_preview.js
        export.js
        tooltips.js
```

Refactor principle:

```text
Extract, test, compare. Do not rewrite.
```

---

## 11. Refactor order

### Phase 0 — create protected checkpoint

- Copy v116 to `reference/`.
- Commit it.
- Generate baseline outputs for HappySun, house, puppy if available.
- Store screenshots and exports.

### Phase 1 — backend extraction only

Move Python code into modules while keeping the HTML/JS embedded if needed.

Recommended order:

```text
1. utils.py
2. image_prep.py
3. trace.py
4. geometry.py
5. fill.py
6. underlay.py
7. satin.py
8. stitch_plan.py
9. export_dst.py
10. export_pyembroidery.py
```

After each extraction:

```bash
python -m compileall .
python app.py --no-browser --port 5002
```

Then run the same browser workflow and compare screenshots.

### Phase 2 — frontend extraction

Only after backend extraction is stable.

Move:

```text
CSS → web/static/css/app.css
HTML → web/templates/index.html
JS state/API/helpers first
Pane-specific JS second
Preview/stitch/export JS last
```

Do not mix frontend extraction with feature changes.

### Phase 3 — only then resume feature development

Future features:

```text
auto colour count
auto cutting / satin segmentation
better auto assignment
real VP3 fix or replacement
additional format tests
```

---

## 12. Regression test suite

Minimum manual/browser regression tests after each phase.

### Test A — HappySun

Purpose:

- fill
- satin
- underlay
- scaling
- export

Checklist:

```text
Pane 1: image prep works
Pane 2: trace auto-runs
Pane 3: assignments can be edited
Pane 4: stitch preview works
Scale to 120 mm and preview
Scale to 50 mm and preview
Generate stitch plan
Export DST
Export JEF
Export VP3 Beta if testing only
```

Expected:

- face fill avoids eyes/mouth/eyebrows
- sun spike satin is continuous zigzag
- no huge unexpected top-layer jumps
- DST matches golden viewer output
- JEF does not show VP3-style erratic moves

---

### Test B — House

Purpose:

- complex simple-craft image
- highlights auto assignment limits
- reveals need for auto cutting

Checklist:

```text
Trace works
Manual assignment possible
Satin columns likely need many cuts
Preview does not crash
Scaling works
DST export works
```

Expected:

- auto assignment may not be good
- manual work still possible
- do not treat failures here as stitch engine regressions unless preview/export breaks

---

### Test C — Puppy / ears / junction-heavy image

Purpose:

- manual cuts
- Y/T junctions
- satin segmentation needs

Checklist:

```text
Manual split selects target first
second click starts cut
third click completes cut
cut guide rungs appear
satin output respects cut rungs
```

Expected:

- lots of manual work still needed
- future auto cutter should use this as a primary test

---

### Test D — Small design scale

Purpose:

- scaling and stitch density

Checklist:

```text
Set hoop 120×120
Set design longest side 50 mm
Press Enter in size field
Preview Stitches runs
Generate Stitch Plan
Export DST
```

Expected:

- stitch count decreases
- fill density remains reasonable
- small gap fill covers corners better than older 1.0 mm behaviour
- stitch dots can be toggled on for inspection

---

## 13. QA checks the Hermes Agent should run automatically

After every refactor step:

```bash
python -m compileall .
python app.py --no-browser --port 5002
```

Browser automation:

```text
open app
load HappySun image
run Pane 1 prep
open Pane 2 and confirm auto trace
open Pane 3
load current trace
assign/check stitch types
open Pane 4
load prepared structure
preview stitches
generate stitch plan
export DST
export JEF
```

Screenshot checks:

```text
no blank panels
no JS console errors
no traceback toast
preview SVG appears
stitch plan appears
export stats update
```

File checks:

```text
DST file nonzero
JEF file nonzero
stitch plan JSON nonzero
export debug JSON nonzero
```

Optional format validation:

- open DST and JEF in external viewer if automation allows
- compare screenshot with golden output

---

## 14. Future feature roadmap

### 14.1 Auto colour count selection

Goal:

Most simple images should auto-pick a sensible reduced colour count to flatten minor shading while preserving important design regions.

Possible approach:

```text
analyse colour clusters after prep
measure cluster sizes
merge tiny/nearby clusters
prefer flat poster-like output
choose colour count based on elbow/knee in colour-distance curve
```

Do not implement in monolith. Do after refactor.

---

### 14.2 Auto cutting / satin segmentation

This is probably the most important next feature.

Problem:

House and puppy examples show that even simple craft images may need 20–30 manual cuts to make satin columns behave properly.

Needed research:

- How Ink/Stitch expects satin rails/rungs
- How it handles strokes, rails, and rungs
- Whether it auto-converts stroke-like paths or relies on user-authored paths
- How other digitizers segment outlines into satin columns

Potential approach:

```text
detect long filled outline shapes
find junctions / branches / high-curvature points
split at T/Y/intersections
split at sharp corners
create cut-guide rungs at split boundaries
preserve user edits
allow preview/undo
```

Primary tests:

```text
HappySun smile
puppy ears
house roof/door/window outlines
Y/T junction synthetic test
```

---

### 14.3 Better Auto Guess

Current Auto Guess is not reliable.

Past attempted rules:

- everything defaults Fill
- edge-touching paths Skip
- column detection Satin
- enclosed fill correction
- bbox ratio
- raster area ratio
- raster width estimate

The raster width experiment performed badly and was rolled back.

Do not restart Auto Guess until after modular refactor. It needs isolated geometry tests.

---

### 14.4 File format work

Real-world testing should guide this.

Priority:

```text
DST baseline
JEF real machine test
VP3 Beta real machine test only if desired
EXP optional
```

Avoid more VP3 hacks until we understand real machine behaviour.

---

## 15. Agent behaviour instructions

The Hermes Agent should:

```text
work in small commits
run syntax checks after every code move
run the golden comparison workflow frequently
take screenshots automatically
never combine refactor and new feature work in one step
use the golden monolith as reference when unsure
preserve behaviour before improving it
ask for human review before deleting old code
```

The Hermes Agent should not:

```text
rewrite the entire app at once
change stitch generation while extracting modules
change UI behaviour during backend extraction
trust Auto Guess as correct
treat VP3 as production-ready
default stitch dots to on
lower trace segment length below 3.5 px
change hoop orientation back to Horizontal × Vertical
```

---

## 16. Final note

This project is now at the transition point from successful prototype to maintainable application.

The current monolith is valuable because it encodes many solved edge cases. The refactor should preserve those behaviours first, then create room for the next hard features:

```text
auto colour count
auto cutting
better file formats
real machine feedback
```

The golden version is the truth until the refactored version proves equivalence through screenshots, exports, and stitch plan comparison.
