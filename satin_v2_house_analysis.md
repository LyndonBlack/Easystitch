# Stage 0A — House SVG Analysis Report

## SVG Structure

- **File**: `house_traced.svg` (576×519)
- **18 fill objects, 20 stroke objects** (tracer marks all as both fill + stroke)
- **5 black stroke objects** are satin candidates (#352C28):
  - `s3p1` — main house outline (ring polygon, 6 holes)
  - `s3p2` — roof/window detail (simple polygon)
  - `s3p3` — door (simple polygon)
  - `s6` — window frame (ring polygon, 5 holes)
  - `s16` — door handle detail (ring polygon, 1 hole)

---

## Results Per Object

### s3p1 — Main House Outline

| Metric | Value |
|--------|-------|
| Area | 45,000 px² |
| Type | Ring polygon (6 holes) |
| Boundary used | Hole 1 (301 verts, area 33,261) |
| Nodes detected | 8 |
| Edges | 8 |

**Nodes**: (71,231), (278,97), (494,232), (537,224), (534,193), (273,39), (30,194), (28,225)

**Assessment**: GOOD. The 8 nodes capture the key structural corners of the house:
- Roof peak area: (273,39) and (278,97)
- Eaves: (30,194) and (534,193)  
- Bottom corners: (28,225) and (537,224)
- Mid-wall corners: (71,231) and (494,232)

**Issue — Hole selection ambiguity**: Two holes have identical vertex counts (301 each):
- Hole 1: area 33,261 — the actual house interior boundary ✓
- Hole 2: area 95,854 — exceeds polygon area (likely self-intersecting geometry) ✗

The code picks the first hole with max vertices, which happens to be correct here, but
this is fragile. If Hole 2 had more vertices, we'd get the wrong boundary.

**Issue — Missing window/door cut-outs**: Holes 3-5 represent windows and the door
area cut out of the thick wall. The current graph treats the polygon as one continuous
ring. In the road-marking model, these cut-outs should create road branches where
the primary road (wall outline) yields to secondary roads (window frames).

### s3p2 — Roof/Window Detail

| Metric | Value |
|--------|-------|
| Area | 2,243 px² |
| Type | Simple polygon (no holes) |
| Boundary used | Exterior (50 verts) |
| Nodes detected | 4 |
| Edges | 4 |

**Nodes**: (378,76), (428,107), (427,52), (377,48)

**Assessment**: GOOD. Simple rectangular-ish shape with 4 corners. No issues.

### s3p3 — Door

| Metric | Value |
|--------|-------|
| Area | 14,951 px² |
| Type | Simple polygon (no holes) |
| Boundary used | Exterior (122 verts) |
| Nodes detected | 6 |
| Edges | 6 |

**Nodes**: (327,302), (310,454), (406,456), (405,319), (390,301), (348,292)

**Assessment**: GOOD. Door outline with 6 corners (arched top + rectangular body).
Corner detection captures the arch curve vertices and bottom corners.

### s6 — Window Frame

| Metric | Value |
|--------|-------|
| Area | 5,630 px² |
| Type | Ring polygon (5 holes) |
| Boundary used | Hole 4 (75 verts, area 2,742) |
| Nodes detected | 3 |
| Edges | 3 |

**Nodes**: (126,351), (252,338), (120,336)

**Assessment**: POOR. Only 3 nodes for a complex window frame with 5 internal panes
(holes). The window is a thick frame ring with individual glass panes as holes.
Using the largest hole boundary (one of the panes) gives only 3 nodes — this doesn't
capture the window's structure at all.

The window frame should have corners on the exterior boundary (the rectangular
frame) AND the interior pane boundaries should define the road branches.

### s16 — Door Handle

| Metric | Value |
|--------|-------|
| Area | 596 px² |
| Type | Ring polygon (1 hole) |
| Boundary used | Hole (17 verts, area 199) |
| Nodes detected | 3 |
| Edges | 3 |

**Nodes**: (395,396), (385,384), (379,395)

**Assessment**: POOR. Tiny detail. 3 nodes on the hole boundary is wrong — the
exterior of this ring should give the handle outline. The hole represents the
handle's interior cut-out. Using the hole gives an inverted result.

---

## Systemic Issues Found

### 1. Hole Selection Heuristic Is Fragile

The code picks `max(geom.interiors, key=lambda h: len(h.coords))`. Problems:
- Multiple holes can have the same vertex count (s3p1: H1 and H2 both 301)
- The "largest by vertex count" hole may not be the shape outline (s6: picks a pane hole)
- No area-based validation: s3p1 Hole 2 has area > polygon area (invalid geometry)

**Fix needed**: Use the hole that best represents the shape outline. Better
heuristics: largest perimeter, or hole with area closest to the polygon area,
or walk all holes and pick the one with the most distinct corners.

### 2. Ring Polygons Get Only One Boundary

Current approach: pick one boundary (exterior or largest hole) and detect corners on it.
For ring polygons with meaningful internal structure (windows within a frame), BOTH
boundaries matter:
- Exterior: the outer frame corners
- Interior holes: the pane/window boundaries

The graph should have nodes from ALL boundaries, not just one.

### 3. No Branch Detection for Cut-Outs

s3p1 has windows (Holes 3-4) and door cut-out (Hole 5) in the thick wall. These
should create road branches:
- Primary road: wall outline around the house
- Secondary roads: window frame outlines within the cut-outs

The current graph has no concept of branches — it's a single ring of nodes.

### 4. Small Rings Give Wrong Boundary

s16 (door handle) is a small ring (exterior area 596, hole area 199). Using the
hole for corner detection gives nodes on the interior cut-out, not the handle
outline. For small rings, the exterior is likely the better boundary.

---

## What Works

- Simple polygons (s3p2, s3p3) — ✅ clean corner detection
- Large ring with well-defined interior boundary (s3p1) — ✅ reasonable corners
- JSON serialization and structural validation — ✅ all edges reference valid nodes
- 45° deviation threshold catches moderate turns — ✅ ear junctions, roof peaks

## What Doesn't Work

- Complex ring polygons with multiple equally-sized holes — ⚠️ fragile selection
- Windows/doors as cut-outs in a thick wall — ❌ no branch concept
- Small ring polygons — ❌ wrong boundary selected
- Multi-pane windows — ❌ interior pane structure is lost

## Recommendation

For Stage 0A, the house SVG reveals that corner detection on a single boundary
is insufficient for complex ring polygons. The issues are structural, not tuning
problems. They will be addressed in:

- **Stage 0B**: Manual tools let the user place splits at window/door boundaries
- **Stage 2**: Auto junction detection will find window/door cut-outs as natural
  branch points
- **Stage 3**: satin_v2 consumes per-segment geometry, so window frames become
  separate stitchable segments

No code changes needed now — these findings validate the staged approach in the plan.
