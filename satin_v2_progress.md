# Satin V2 — Implementation Progress

**Date**: 2026-05-23  
**Commit**: `be9fc4e` (latest)  
**Plan reference**: `satin_v2_plan_final.md`

---

## Completed Stages

### Stage 0A — Data Model + Graph Builder ✅

**Files**: `easystitch_core/road_marker.py`, `geometry.py`, `app.py`, `__init__.py`

- Rung, Node, Edge, RoadMarkedPath dataclasses with JSON serialization
- `build_initial_graph(polygon)` — detects sharp corners, builds topological graph
- `detect_sharp_corners()` — rewritten: Shapely simplify + deviation-based threshold + spatial dedup
- Ring polygon handling: uses interior hole boundary instead of canvas-bbox exterior
- Hole selection: picks boundary with most detected corners (not just vertex count)
- API: `POST /api/roads/build_graph` returns boundary_coords, has_holes, nodes, edges

**Known limitations (by design for Stage 0A)**:
- Only produces a SINGLE loop of nodes+edges — no branch/junction detection
- Ring polygons only use one hole boundary (the one with most corners)
- Puppy: 10 nodes around face+ears as one continuous loop
- House: 15 nodes around whichever hole has most detail
- Window: 4 nodes (limited by ring boundary selection)

---

### Stage 0B — Manual Road-Marking Tools ✅ (with bugs)

**Backend**: `road_marker.py`, `app.py`  
**Frontend**: `road_marker.js`, `index.html`, `app.css`

**Operations implemented**:
| Tool | Status | Notes |
|------|--------|-------|
| Mark Primary/Secondary/Tertiary | ✅ Working | Sets priority 0/1/2 on edges |
| Cycle (Promote/Demote) | ✅ Working | Cycles priority 0→1→2→0 |
| Split | ⚠️ Buggy | Places splits on wrong lines, not where clicked |
| Yield | ❓ Untestable | Requires ≥2 edges to test (single loop only) |
| Merge | ❌ Broken | Stopped working; was fixed in `b3b01dc` but regressed |
| Select | ⚠️ Unclear | Never visibly highlights a line or node |
| Reorder | ❌ Broken | Reorder panel (stitch-order-panel) never appears |

**Modal overlay**: Full-screen dialog with toolbar inside the modal. Renders boundary polyline + nodes (green circles) + edges (colored lines by priority). 

**Major issues encountered during implementation**:
1. **HierarchyRequestError**: `container.appendChild(svg)` tried to append SVG to itself when container IS the SVG element. Fixed with `container !== svg` guard.
2. **Clone-replace breaking SVG**: `attachClickHandlers` cloned+replaced SVG, which broke the modal layout. Replaced with direct listener attachment.
3. **Visible edge lines stealing clicks**: Visible lines rendered on top of invisible hit areas, blocking click events. Fixed with `pointer-events: none` on visible lines.
4. **boundary_coords not surviving operations**: Initial build_graph included boundary coords, but split/yield/merge returned new state WITHOUT boundary. Fixed by adding `boundary_coords` to `RoadMarkedPath` dataclass + `to_dict()`.
5. **Hole selection picking wrong boundary**: Originally picked hole with most vertices. House picked roof interior (8 corners) instead of full outline. Changed to pick hole with most detected corners (house now gets 15).
6. **Modal overlay not showing full graph**: Sidebar panel was too small (300px). Moved to full-screen modal, then toolbar duplication, then multiple rendering fixes.

---

## Remaining Bugs (Current Session)

### 1. Split tool places splits on wrong lines
- **Symptom**: Clicking on edge A places split on edge B
- **Likely cause**: Click coordinate extraction or edge identification is wrong in the modal SVG context. The `getScreenCTM()` transform might not match the modal SVG's coordinate system, or the click position is being mapped incorrectly.

### 2. Merge no longer works
- **Symptom**: Was working after `b3b01dc` fix (34 tests pass). No longer works in the modal UI.
- **Likely cause**: API call may be failing, or the two-click workflow (select first edge, then second) may be broken in the modal — click handlers may not be properly tracking `selectedEdgeId` state.

### 3. Select tool doesn't highlight
- **Symptom**: Clicking edge/node with Select tool active does nothing visible
- **Likely cause**: `highlightEdge()` or `highlightNode()` functions may reference old DOM containers or the highlight rendering may be broken after the modal migration.

### 4. Reorder panel never appears
- **Symptom**: Clicking Reorder tool doesn't show the stitch-order panel
- **Likely cause**: `stitch-order-panel` is in the SIDEBAR (not the modal). The `rebuildStitchOrderPanel()` function references `stitch-order-list` in the sidebar HTML, which may not be visible when modal is open. Panel needs to be moved into the modal or made to work from the modal.

### 5. Yield untestable
- **Symptom**: Requires at least two edges to create a yield between, but Stage 0A only produces a single loop
- **Mitigation**: This is expected until Stage 2 auto-detection provides multiple road segments. User could manually Split to create multiple edges, but Split is buggy.

---

## What Worked Well

- **Primary/Secondary/Tertiary marking**: Reliable, instant visual feedback (color change)
- **Cycle tool**: Works correctly, cycles through all three priority levels
- **Boundary rendering**: Full polygon outline visible in modal (faint blue polyline)
- **Hole selection**: Picks the most geometrically detailed boundary
- **API parameter sync**: Frontend/backend parameter names now consistent
- **Test suite**: 36 integration tests + 34 bug-fix tests, all passing

---

## Files Modified/Created

| File | Status | Lines |
|------|--------|-------|
| `easystitch_core/road_marker.py` | Created (Stage 0A) + heavily modified (0B) | ~820 |
| `easystitch_core/geometry.py` | Modified — detect_sharp_corners rewritten | ~990 |
| `easystitch_core/__init__.py` | Modified — added exports | +7 |
| `easystitch_core/stitch_plan.py` | Modified — satin_engine branch (unused) | +3 |
| `easystitch_core/fill.py` | Modified (earlier, reverted) | — |
| `app.py` | Modified — 6 API endpoints + road state cache | +211 |
| `web/templates/index.html` | Modified — toolbar, modal, overlay, panel swap | +50 |
| `web/static/js/road_marker.js` | Created + heavily modified | ~980 |
| `web/static/js/app.js` | Modified — _maybeShowRoadGraph hook | +15 |
| `web/static/css/app.css` | Modified — road-tool-btn styles | +8 |
| `tests/test_road_marker.py` | Created — 36 integration tests | ~280 |
| `tests/test_merge_and_reorder_fixes.py` | Created — 34 focused tests | ~200 |

**Untouched**: `satin.py`, `underlay.py`, `export_dst.py`, `trace.py`, `image_prep.py`, `utils.py`

---

## Architecture Decisions Made

1. **Road graph as modal overlay** (not sidebar panel or inline preview) — gives users full-screen view of the geometry
2. **Toolbar duplicated in modal** — users don't need to switch focus to sidebar
3. **Single-boundary corner detection** — Stage 0A intentional limitation; Stage 2 adds multi-boundary junction detection
4. **RoadMarkedPath stores boundary_coords** — survives all operations, always available for rendering
5. **Deviation = 180° - interior_angle** — lower deviation = sharper corner; threshold 150° catches moderate turns

---

## Next Steps (from Plan)

### Stage 0C: Export Structured Segments JSON
- Serialize road-marked graph into stitch-ready segment format
- Validation: all rung IDs exist, no orphan nodes, shared junction rungs

### Stage 0D: Validate with Puppy Head Outline
- Manual road-marking acceptance test
- Requires Split and Yield tools to be working first

### Stage 1: Auto-Suggest Road Priorities
- Auto-detect primary/secondary based on length, enclosure, continuity, area
- User accepts or adjusts

### Stage 2: Auto Junction Detection + Splitting
- Narrow-waist detection for branch points
- Multi-boundary handling for ring polygons
- Auto-split at junctions, auto-place yield rungs

### Stage 3: Satin V2 Stitching Engine
- Given pre-structured segments with start/end rungs, produce zigzag paths
- Rung generation between start/end (small but real geometry problem)

---

## Git History

```
be9fc4e Fix: HierarchyRequestError — don't appendChild SVG to itself
e4c58e7 Debug: remove clone-replace, add console.log + toast on edge/node clicks
7956ffb Fix: visible edge lines now pointer-events:none so clicks reach hit area
f771831 Fix road graph modal: toolbar updates, refresh targets modal SVG
3362deb Road graph modal: toolbar inside modal + full-screen flex layout
dc0083a Fix road graph modal: fetch data on toggle if not cached
d2c7601 Road graph: replace inline overlay with full-screen modal dialog
04538f5 Road graph moved to main preview overlay + improved hole selection
e19c7f9 Fix: boundary_coords now stored in RoadMarkedPath
12f1f2b Add polygon boundary rendering to road graph overlay
b3b01dc Fix 4 Stage 0B bugs (merge, reorder, zoom, ring note)
c86e9e8 Revert exclusive tool selection
32b8728 Fix: Road Graph panel above Object Detail + exclusive tool selection
7ba5bea Stage 0B: Manual road-marking tools
ddfd329 Stage 0A: Road-marking data model + initial graph builder
cf246ab Fix hardcoded /tmp/Easystitch path in test_manual_split.py
```

---

## Session Token Usage

~60M tokens consumed across this session (research sub-agents + implementation sub-agents + debugging). The repeated context-bloat from debugging the modal overlay was the largest contributor.
