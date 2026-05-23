# Satin V2 Engine — Final Plan: Road-Marking Architecture

## Core Architecture

```
Pane 3 — Path Structuring (road-marking)      Pane 4 — Stitch Tuning (execution)
═══════════════════════════════════════       ═══════════════════════════════════
Complex SVG path → graph of edges+nodes        Simple segment [start → end]
  │                                              │
  ├─ Build graph from geometry                   ├─ Known two-rail column
  │  (nodes = junctions/endpoints/corners,        ├─ Known start rung
  │   edges = path sections between them)         ├─ Known end rung
  │                                              │
  ├─ Assign road levels (manual or auto)          └─ Deterministic zigzag:
  ├─ Split edges at junctions                       no ordering, no bar conversion,
  ├─ Place yield/start rungs at branches              no patches
  │                                              (note: rung generation between
  │                                               start/end is a small but real
  │                                               geometry problem — not trivial)
  │
  └─ Output: stitch-ready segments
     with rung anchors + routing order
```

## Key Design Decisions

### 1. Primary roads ARE internally segmented at junctions

For stitching purposes, even the primary road is split into stitchable segments
at junction zones. The difference from secondary roads:

| Aspect | Primary at junction | Secondary at junction |
|--------|-------------------|----------------------|
| Visual continuity | Uninterrupted | Terminates at yield, restarts |
| Internal segments | Split at junction with rung metadata | Separate segment with own start/end rungs |
| Stitch priority | Stitched first | Stitched after primary |
| Rung at junction | Passing rung (shared) | Start rung (yields to primary) |

So the puppy face circle is NOT one giant segment. It's split at each ear junction
into sections: face_right, face_top, face_left, face_bottom. Each section has
its own start/end rungs. The visual result is continuous because each section's
end rung matches the next section's start rung.

### 2. Graph is built from geometry, not SVG path order

The source SVG path order is unreliable. Instead:

```
1. Walk the polygon boundary to find:
   - Endpoints (where a stroke naturally terminates)
   - Junctions (narrow waists where two regions meet)
   - Sharp corners (direction change > 90°)

2. Build a graph:
   nodes = {endpoints, junctions, sharp_corners, manual_cuts}
   edges = path sections between adjacent nodes along the boundary

3. Assign priority to edges based on:
   - Length (longer → higher priority)
   - Enclosure (outer contour → higher priority)
   - Continuity (fewer sharp turns → higher priority)
   - Visual dominance (larger area → higher priority)
```

### 3. Output is stitch-ready, not just labels

Pane 3 MUST produce real structural data. If the output is just "this is secondary,"
Pane 4 still has to guess. Every yield point becomes:

```python
{
    "segment_id": "ear_left",
    "parent_id": "face_outline_section_2",
    "priority": 2,  # 0=primary, 1=secondary, 2=tertiary
    "start_rung_id": "rung_right_ear_junction",
    "end_rung_id": "rung_ear_tip_left",
    "yield_to": "face_outline_section_2"
}
```

and separately, a shared rung registry:

```python
{
    "rungs": {
        "rung_right_ear_junction": {"p1": (423, 225), "p2": (423, 245)},
        "rung_ear_tip_left":       {"p1": (380, 130), "p2": (370, 130)},
        "rung_face_shoulder":      {"p1": (500, 150), "p2": (500, 340)}
    }
}
```

Coordinates can be copied inline for convenience, but IDs prevent drift. If two
segments reference `"rung_right_ear_junction"`, they are guaranteed to use the
same exact coordinates — no "almost the same rung" gaps.

### 4. Shared rungs are first-class objects, not coordinate blobs

Shared junction rungs MUST be the same object referenced by ID, not independently
computed near-duplicates. Rules:

- Each rung has a unique ID
- Adjacent primary sections share the same junction rung ID
- A secondary road's start_rung shares the same ID as the primary road's
  junction rung (they yield to the same physical cross-section)
- satin_v2 looks up rung coordinates by ID from the registry, never recomputes
- If coordinates are recomputed, they must match the registered coordinates
  to within 0.01px tolerance, or the system must warn

Without this, segments will develop gaps and overlaps at junctions because each
one independently computes "almost the same" rung.

### 5. Closed loops need explicit seam rules

For any segment that forms a closed loop with no natural start/end (e.g., a simple
ring polygon, or the primary face circle split into linked sections), satin_v2
needs a deterministic seam — a point where the zigzag starts and stops.

Seam placement rules (in priority order):

1. **User-placed seam** — manual "Place Seam" tool in Pane 3
2. **Nearest current needle position** — choose the rung closest to where the
   needle ended the previous segment (minimizes travel)
3. **Lowest visual priority point** — if no needle context, choose the rung at
   the least visually prominent location (e.g., bottom of circle, inside of curve)
4. **First rung in stitch order** — fallback: use the first rung encountered
   when walking the boundary clockwise

For primary sections that form a continuous loop (face_right → face_top →
face_left → face_bottom → back to face_right), the seam is naturally at the
section boundaries — each section has explicit start_rung/end_rung. The closed-loop
rule applies mainly to undivided ring-like segments.

### 6. Staged: manual first, auto later

Do NOT try to solve auto-detection first. Build the manual tools, validate the
concept, then add auto-suggestion. Each stage builds on the previous.

The graph builder starts with only sharp corners and user-created nodes.
Narrow-waist junction detection is pushed to Stage 2 as auto-suggestion logic.

---

## Stage 0A: Data Model + Debug Overlay

**Goal:** Define the road-marking data structures and build a visual overlay
in Pane 3 before any manual tools exist. Validates that the data model works
with the puppy SVG.

### 0A.1 — Data Structures

```python
# Rung registry (first-class objects)
Rung = {
    "id": "rung_right_ear_junction",  # unique string ID
    "p1": (423.0, 225.0),             # first endpoint
    "p2": (423.0, 245.0)              # second endpoint
}

# Node (graph vertex)
Node = {
    "id": "node_ear_right",
    "type": "sharp_corner" | "user_cut" | "manual_yield" | "endpoint",
    "position": (423.0, 235.0)
}

# Edge (graph edge = road segment)
Edge = {
    "id": "edge_face_right",
    "priority": 0,                    # 0=primary, 1=secondary, 2=tertiary
    "start_node_id": "node_shoulder_right",
    "end_node_id": "node_ear_right",
    "start_rung_id": "rung_shoulder_right",   # reference into rung registry
    "end_rung_id": "rung_right_ear_junction",
    "yield_to_edge_id": None,                  # if this edge yields to another
    "geom": <Shapely polygon>                  # the geometry of just this segment
}

# Full output
RoadMarkedPath = {
    "path_id": "path_2",
    "rungs": {id: Rung, ...},          # shared rung registry
    "nodes": {id: Node, ...},          # graph nodes
    "edges": {id: Edge, ...},          # graph edges
    "stitch_order": ["edge_face_right", "edge_face_top", ...]
}
```

### 0A.2 — Debug Overlay

In Pane 3, for a selected satin path:
- Nodes shown as circles (color by type: blue=corner, green=user_cut, orange=yield)
- Edges shown as colored lines (blue=primary, green=secondary, orange=tertiary)
- Rungs shown as dashed lines crossing edges at node positions
- Segment IDs displayed as labels on edges

### 0A.3 — Initial Graph (No Auto-Detection)

On initial load, the graph has ONLY:
- **Sharp corners** — direction change > 90° (simple geometry, reliable)
- **Endpoints** — where the polygon boundary naturally terminates
- **User-created nodes** — placed via Stage 0B tools

NO narrow-waist detection, NO junction auto-detection. Those are Stage 2.

### Files touched — 0A
- `refactor/easystitch_core/road_marker.py` (data structures, initial graph builder)
- `refactor/easystitch_core/geometry.py` (sharp_corner_detect helper)
- `refactor/web/templates/index.html` (debug overlay canvas in Pane 3)
- `refactor/web/static/js/road_marker.js` (render nodes/edges/rungs)
- `refactor/app.py` (API: `/api/roads/build_graph`)

---

## Stage 0B: Manual Split/Yield/Priority Tools

**Goal:** Add the toolbar tools for manual road marking. User can place splits,
yields, and assign priorities.

### 0B.1 — Tools

| Tool | Action |
|------|--------|
| **Mark Primary** | Click an edge → sets priority 0 (blue highlight) |
| **Mark Secondary** | Click an edge → sets priority 1 (green highlight) |
| **Mark Tertiary** | Click an edge → sets priority 2 (orange highlight) |
| **Place Split** | Click on path → creates a cut node, splits the edge |
| **Place Yield** | Click near a junction → creates a yield rung between two edges |
| **Promote/Demote** | Right-click edge → cycle priority up/down |
| **Merge Edges** | Select two adjacent edges → merge into one |
| **Reorder** | Drag edges in the stitch-order panel to reorder |
| **Place Seam** | Click on a closed-loop edge → sets seam point for stitch start |

### 0B.2 — Rung Creation Rules

When a yield or split is placed:
1. A new rung is added to the shared rung registry with a unique ID
2. The rung is computed at the click point, perpendicular to the local boundary
   tangent, extended to intersect the polygon on both sides
3. All affected edges reference the rung by ID — no coordinate duplication
4. If two edges share a junction, they reference the SAME rung ID

### Files touched — 0B
- `refactor/web/static/js/road_marker.js` (tool handlers, click-to-place logic)
- `refactor/web/templates/index.html` (toolbar HTML)
- `refactor/app.py` (API: `/api/roads/place_split`, `/api/roads/place_yield`,
  `/api/roads/set_priority`)

---

## Stage 0C: Export Structured Segments JSON

**Goal:** Serialize the road-marked graph into the structured segments format
that Pane 4 consumes. Validates the data pipeline end-to-end.

### 0C.1 — Export Format

Same as Section 3 output format — segments with rung IDs, rung registry,
stitch order. The export function converts the internal graph (nodes + edges)
into the external segment format.

### 0C.2 — Validation

- All referenced rung IDs exist in the rung registry
- All segments in stitch_order exist in the edges dict
- Adjacent primary segments share the same junction rung ID
- Secondary segments that yield_to another segment share its start rung
- No orphan nodes (every node is referenced by at least one edge)

### Files touched — 0C
- `refactor/easystitch_core/road_marker.py` (export function, validation)
- `refactor/app.py` (API: `/api/roads/export`)

---

## Stage 0D: Validate with Puppy Head Outline

**Goal:** Manually road-mark the puppy head outline and verify the output is
correct and complete. This is the Stage 0 acceptance test.

### 0D.1 — Validation Steps

```
1. Load puppy SVG → select head outline (Path #2)
2. Graph builder shows initial nodes: 2 sharp corners at shoulders
3. User manually places:
   - 4 split nodes: right ear junction, left ear junction,
     right jaw junction, left jaw junction
   - Yield rungs at each junction
4. User marks:
   - Face circle edges → Primary
   - Ear branches → Secondary
   - Jaw sections → Secondary
5. Click "Export" → structured segments JSON produced
```

### 0D.2 — Acceptance Criteria

- Output contains exactly 8 edges (4 face sections + 2 ears + 2 jaw)
- All 8 edges have valid priority (0 or 1)
- All junction rungs are shared (same ID used by both the primary edge's
  end_rung and the secondary edge's start_rung)
- Rung registry has exactly 8 rungs (4 junctions + 2 ear tips + 2 jaw tips)
- Stitch order lists all 8 edges
- Validation checks pass (no orphan nodes, no missing rung references)
- Pane 3 debug overlay shows correct color coding

### Files touched — 0D
- `refactor/tests/test_road_marker.py` (new tests for export + validation)

---

## Per-Segment Fallback (Cross-Cutting)

From Stage 0 onwards, if any segment fails to produce valid satin (degenerate
geometry, rung generation failure, zero-width rung), fall back to legacy satin
for that specific segment only. The remaining segments still use V2.

This prevents a single bad segment from blocking the entire path.

---

## Stage 1: Auto-Suggest Road Priorities

**Goal:** After manual marking works, add an "Auto Road Mark" button that suggests
priorities based on geometric signals. User can accept or adjust.

### 1.1 — Auto-Priority Signals

For each edge in the graph, compute priority from weighted signals:

| Signal | Weight | How to compute |
|--------|--------|---------------|
| Length | 0.35 | Edge boundary length / longest edge length (normalized) |
| Enclosure | 0.25 | Is this edge on the outer contour? → +1.0. Inner → +0.0 |
| Continuity | 0.25 | 1.0 - (sharp_turns / total_nodes). Smoother = higher |
| Area | 0.15 | Polygon area of the region this edge bounds / largest area |

Score = weighted sum of signals. Higher score → higher priority.

### 1.2 — Junction Resolution

At each junction node where edges meet:

```
1. Find the highest-priority edge connected to this junction.
2. That edge "passes through" — its segment is continuous.
3. All other edges "yield" — they get start_rung at this junction
   and yield_to = the passing edge's segment_id.
4. If two edges have equal priority, the longer one wins.
```

### 1.3 — UI

- "Auto Road Mark" button in Pane 3
- Runs auto-priority and shows results as suggestions (dashed outlines)
- User can accept all, adjust individual edges, or reject
- Manual markings always override auto

### Files touched — Stage 1
- `refactor/easystitch_core/road_marker.py` (add auto-priority scoring, junction resolution)
- `refactor/web/static/js/road_marker.js` (auto-mark UI)
- `refactor/app.py` (new API: `/api/roads/auto_mark`)

---

## Stage 2: Auto Junction Detection + Splitting

**Goal:** Automatically detect Y/T/X junctions in the graph and split edges.
Reduce manual work — the user should only need to adjust priorities, not place
every junction manually.

### 2.1 — Junction Detection

```
For each polygon boundary point p:
  1. Find the closest non-adjacent boundary point q.
  2. If distance(p, q) < 3 × stitch_spacing:
     a. Check: are regions on both sides of the line (p,q) wider than
        5 × stitch_spacing? (Prevents false junctions on uniformly thin strokes)
     b. If yes → junction candidate at midpoint of (p, q)
  3. Cluster nearby junction candidates (within 2 × stitch_spacing)
     into a single junction node.
```

### 2.2 — Auto-Split

```
At each detected junction:
  1. Compute the waist line (perpendicular to the junction direction).
  2. Extend the waist line to intersect the polygon boundary.
  3. Split the polygon at this intersection line.
  4. Create nodes at the split points, add edges between them.
  5. Connect the new edges to the existing graph.
```

### 2.3 — Auto Yield Rung Placement

```
For each junction where auto-priority has resolved which edge passes through:
  1. The yield rung is the waist line itself.
  2. For the passing edge: a rung node is placed. This becomes:
     - End rung of the preceding segment
     - Start rung of the following segment
  3. For yielding edges: the same rung becomes their start_rung.
```

### Files touched — Stage 2
- `refactor/easystitch_core/road_marker.py` (auto-junction detection, auto-split, auto-yield)
- `refactor/easystitch_core/geometry.py` (junction detection algorithm)
- `refactor/web/static/js/road_marker.js` (auto-split UI, junction display)

---

## Stage 3: Satin V2 Stitching Engine

**Goal:** Given pre-structured segments from Pane 3, produce satin zigzag paths.
Deterministic — all the complexity lives in road-marking. But rung generation
between start/end rungs is a small but real geometry problem, not trivial.

### Algorithm

```
Input: segment polygon, start_rung_id (str), end_rung_id (str),
       rung_registry (dict), spacing_px
Output: Flat zigzag polyline [p1, p2, p3, p4, ...]

1. Look up start/end rung coordinates from registry by ID.
   If either rung_id is None (leaf segment with natural endpoint):
     → Cast the narrowest valid cross-section at that end of the polygon.

2. Generate intermediate rungs between start and end:
   a. First rung = start_rung coordinates
   b. Walk along the segment's medial region at spacing_px intervals
      (this is the hard part — requires reliable local centre/axis or
       boundary-pairing method; start with simple offset-curve approach,
       accept fallback to legacy if it fails)
   c. At each step, compute local tangent from the polygon boundary
   d. Rotate 90° → normal direction
   e. Intersect normal with segment polygon boundary → (left, right) rail points
   f. Final rung = end_rung coordinates (exact match, no extrapolation)

3. Build zigzag path:
   path = []
   for rung in rungs:
       path.append(rung.left)
       path.append(rung.right)
   # Deterministic alternation. No bar generation, no ordering, no connector patches.

4. Return flat polyline.

Edge cases:
- start_rung or end_rung is None (leaf segment):
  → Use the narrowest natural cross-section at that end
- Segment width < 3 × spacing_px:
  → Use single-line running stitch instead of satin
- Segment has no valid rungs (degenerate):
  → Return empty path, per-segment fallback to legacy
- Segment is a closed loop (start_rung_id == end_rung_id, or no natural seam):
  → Apply seam rules (Section 5) to determine stitch start point
  → Split the loop at the seam rung, stitch as an open segment
  → The seam rung appears as both start and end of the zigzag (continuous)
```

### Integration

In `stitch_plan.py`, when `satin_engine: "v2"`:

```python
# Legacy: one giant polygon → guess everything
# V2: pre-structured segments → stitch each one
rung_registry = structured_output["rungs"]
for edge in structured_output["stitch_order"]:
    segment = structured_output["edges"][edge]
    try:
        zigzag_path = generate_satin_v2_segment(
            segment["geom"],
            segment["start_rung_id"],
            segment["end_rung_id"],
            rung_registry,
            stitch_len_px
        )
    except RungGenerationError:
        # Fall back to legacy for this segment only
        zigzag_path = generate_satin_legacy(segment["geom"], ...)
    events = _append_polyline_stitches(
        events, zigzag_path, stitch_len_px, current_pos,
        jump_threshold_px, "top_satin", obj_id, color,
        connector_geom=None  # safety belt: never hide travel as satin
    )
```

### Files touched — Stage 3
- `refactor/easystitch_core/satin_v2.py` (new — deterministic rung+zigzag generator)
- `refactor/easystitch_core/stitch_plan.py` (branch on satin_engine, per-segment loop)
- `refactor/easystitch_core/__init__.py` (export satin_v2)
- `refactor/tests/test_satin_v2.py` (new tests)

---

## Stage 4: Integration + Polish

### 4.1 — Full Pipeline

```
User loads SVG
  → Pane 3: graph built, auto-mark suggests priorities
  → User adjusts (promote/demote, manual split, reorder)
  → "Apply" → structured segments output
  → Pane 4: "Generate Preview" → satin_v2 stitches each segment
  → "Export DST" → final embroidery file
```

### 4.2 — Legacy Fallback

Per-path granularity: if road-marking fails on a specific path, that path falls
back to legacy satin. The toggle in Pane 4 applies globally, but individual
paths can override.

### 4.3 — Pull Compensation (Optional)

In satin_v2, apply optional inset/outset to rung endpoints based on local width.
Matches Ink/Stitch's `offset_points` — compensates for thread pull.

### 4.4 — Underlay

For V2 segments:
- Primary roads: center-walk underlay along the segment centreline
- Secondary roads: edge-walk underlay along segment boundary
- Underlay stitched before top satin, per segment, in stitch order

### 4.5 — Default Switch

When V2 matches or exceeds legacy on all test subjects:
- UI default: "V2 Satin"
- Legacy remains as "Legacy Satin (classic)"
- Deprecation after N releases with no legacy usage

---

## Test Subjects (Progressive)

| Stage | Test Subject | What It Validates |
|-------|-------------|-------------------|
| 0 | Puppy head outline, manually marked | Manual road-marking concept, segment output format |
| 0 | Puppy cheeks (simple blobs) | Trivial segments (no junctions) still work |
| 1 | Puppy head outline, auto-marked | Auto-priority signals produce correct primary/secondary |
| 2 | Puppy head outline, auto-detected junctions | Junction detection finds all 4 ear/jaw junctions |
| 3 | Puppy head outline, full pipeline | satin_v2 stitches all segments with correct zigzag |
| 3 | HappySun smile, manually split | Regression: simple satin still works |
| 3 | Sun rays | Simple straight/curved strokes |
| 4 | Full puppy (all paths) | Multi-path routing, trim/jump between segments |
| 4 | Manual edge case: nose/mouth split | User draw-cut in Pane 3, half fill, half satin |

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Rung generation fails on complex segment shapes | Medium | Start with simple segments (Stage 0D validation). Fall back to legacy per-segment. Iterate on medial walk algorithm. |
| Junction detection misses some junctions | Medium | Manual "Place Split" tool as fallback. Iterate on detection thresholds. Auto-detection pushed to Stage 2. |
| Auto-priority gets primary/secondary wrong | Medium | Manual override always available. Start with manual Stage 0 to validate concept before automating. |
| Shared rung IDs drift (near-duplicate coordinates) | Low | Rungs are first-class objects with IDs. Lookup by ID, never recompute. 0.01px tolerance check on write. |
| Performance on complex SVGs | Low | Graph-based approach is O(n) in boundary points. satin_v2 is deterministic per segment. |
| Legacy engine breaks during integration | Very Low | All branching is additive. Legacy path behind feature flag, per-path fallback. |

---

## Files Changed (All Stages)

| File | Stage 0A | Stage 0B | Stage 0C | Stage 0D | Stage 1 | Stage 2 | Stage 3 | Stage 4 |
|------|----------|----------|----------|----------|---------|---------|---------|---------|
| `easystitch_core/road_marker.py` | Data model + graph builder | — | Export + validation | — | Auto-priority | Auto-detect | — | — |
| `easystitch_core/satin_v2.py` | — | — | — | — | — | — | Create | Add pull comp, underlay |
| `easystitch_core/geometry.py` | Sharp corner detect | Rung creation | — | — | — | Junction detect | Rung cast | — |
| `easystitch_core/stitch_plan.py` | — | — | — | — | — | — | Branch + per-segment | — |
| `easystitch_core/__init__.py` | Export road_marker | — | — | — | — | — | Export satin_v2 | — |
| `web/templates/index.html` | Debug overlay | Toolbar | — | — | Auto-mark button | Junction display | — | Full flow polish |
| `web/static/js/road_marker.js` | Render nodes/edges | Tool handlers | — | — | Auto-mark UI | Auto-split UI | — | Integration |
| `web/static/js/app.js` | Wire road-marker | — | — | — | — | — | Wire satin_engine | — |
| `app.py` | build_graph API | place_split/place_yield/set_priority APIs | export API | — | auto_mark API | — | Update preview | Update export |
| `tests/test_road_marker.py` | — | — | — | Create | Extend | Extend | — | — |
| `tests/test_satin_v2.py` | — | — | — | — | — | — | Create | Extend |

**Untouched:** `satin.py` (legacy), `fill.py` (legacy ordering/zigzag),
`export_dst.py`, `export_pyembroidery.py`, `underlay.py`, `trace.py`,
`image_prep.py`, `utils.py`.

---

## Why This Architecture Works

1. **Manual-first staging.** Stage 0 validates the concept with manual tools before
   any automation. The user can road-mark the puppy today and see if the approach
   produces better satin than legacy. No waiting for auto-detection.

2. **Output is stitch-ready.** Every segment that leaves Pane 3 has real start_rung,
   end_rung, and yield_to data. Pane 4 never has to guess.

3. **Primary roads are segmented internally.** Even the face circle is split into
   sections at junctions. No giant un-stitchable objects. Visual continuity is
   maintained by shared rung nodes between adjacent primary sections.

4. **Graph from geometry, not path order.** SVG path sequence is ignored. The
   graph is built from actual polygon geometry — nodes at features, edges between
   them. Priority assigned to edges, not paths.

5. **Deliberately boring satin_v2.** The stitching engine is ~100 lines because
   it receives solved problems. No heuristics, no patches, no special cases.
   Just "stitch this segment from here to here."

6. **Legacy untouched, additive only.** The existing engine stays as-is. V2 is
   a parallel path. Per-path fallback if V2 fails.
