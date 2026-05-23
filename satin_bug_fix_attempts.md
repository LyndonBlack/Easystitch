# EasyStitch Satin Bug — Fix Attempt History

## Baseline: Commit 0824326 (Phase 2.5 clean)

**State**: Refactoring complete, bug pre-existing from golden v116.

### Original code in fill.py:

**`_order_satin_bars_zigzag()`** — tries 4 candidates (original/reversed × entry_side 0/1):
```python
def _order_satin_bars_zigzag(lines, start_pos=None):
    ...
    def orient_sequence(seq, entry_side=None):
        ...  # aligns bar[-1] to nearest previous bar[-1]
    
    candidates = []
    candidates.append(orient_sequence(bars, entry_side=0))
    candidates.append(orient_sequence(bars, entry_side=1))
    rbars = list(reversed([list(bar) for bar in bars]))
    candidates.append(orient_sequence(rbars, entry_side=0))
    candidates.append(orient_sequence(rbars, entry_side=1))
    return min(candidates, key=candidate_score)
```

**`_satin_bars_to_continuous_zigzag()`** — only appends bar[-1] for bars[1:]:
```python
def _satin_bars_to_continuous_zigzag(ordered_bars):
    ...
    path = [bars[0][0], bars[0][-1]]
    for bar in bars[1:]:
        path.append(bar[-1])
    # dedup consecutive duplicates
    clean = []
    for p in path:
        if not clean or math.hypot(...) > 1e-6:
            clean.append(p)
    return clean if len(clean) >= 2 else []
```

**`stitch_plan.py`** — top_satin uses `connector_geom=geom`:
```python
current_pos, stitches, jumps = _append_polyline_stitches(
    events, satin_zigzag_path, stitch_len_px, current_pos, jump_threshold_px,
    "top_satin", obj_id, color,
    min_gap_px=small_gap_px,
    connector_geom=geom        # ← allows hidden connector stitches across design
)
```

**Symptom**: Satin zigzag works correctly through body bars, then marches along one rail
for 14 stitches, reverses direction, marches back 14 stitches, then actual zigzag
resumes. Events 8183-8196 march forward (x=397→627, y~553.5), events 8197-8210
march backward (x=609→373, y~554→558), events 8211+ proper zigzag.

**Root cause**: `orient_sequence` processes bars in generated list order. After manual
cuts, cap/end bars are interleaved with body bars. The 4-candidate approach can't fix
interleaved bars — it only reverses the entire sequence.

---

## Attempt 1: Greedy Nearest-Neighbor Ordering (Patch 1)

**File**: `fill.py` — `_order_satin_bars_zigzag()`

**Change**: Replace 4-candidate orient_sequence with greedy algorithm that always
picks the physically nearest remaining bar.

**New code**:
```python
def _order_satin_bars_zigzag(lines, start_pos=None):
    """Greedily chooses the next physical-nearest bar. Each bar is oriented so:
      - bar[0] is the endpoint nearest the current needle position
      - bar[-1] is the opposite rail endpoint"""
    bars = [list(line) for line in lines if line and len(line) >= 2]
    if not bars:
        return []
    
    remaining = bars[:]
    ordered = []
    
    if start_pos is None:
        first = remaining.pop(0)
        ordered.append(first)
        current = first[-1]
    else:
        # Find bar with endpoint nearest to start_pos
        best_idx = 0; best_rev = False; best_d = float("inf")
        for i, bar in enumerate(remaining):
            d0 = point_dist(bar[0], start_pos)
            d1 = point_dist(bar[-1], start_pos)
            if d0 < best_d: best_idx = i; best_rev = False; best_d = d0
            if d1 < best_d: best_idx = i; best_rev = True; best_d = d1
        first = remaining.pop(best_idx)
        if best_rev: first = list(reversed(first))
        ordered.append(first)
        current = first[-1]
    
    # Greedily continue
    while remaining:
        best_idx = 0; best_rev = False; best_d = float("inf")
        for i, bar in enumerate(remaining):
            d0 = point_dist(bar[0], current)
            d1 = point_dist(bar[-1], current)
            if d0 < best_d: best_idx = i; best_rev = False; best_d = d0
            if d1 < best_d: best_idx = i; best_rev = True; best_d = d1
        bar = remaining.pop(best_idx)
        if best_rev: bar = list(reversed(bar))
        ordered.append(bar)
        current = bar[-1]
    
    return ordered
```

**Result (stich plan 5.json)**: 
- Mouth: dropped from 2 bad lines to 1. ✅ Partial fix for manual cuts.
- Mouth: still walks back at events 8282-8296. ❌
- Sunspike 1 (s5): walk-back at 8636-8643. ❌ NEW (wasn't visible before)
- Sunspike 2 (s18): walk-back at 9913-9919. ❌ NEW

**Why**: Greedy ordering picks ALL bars including reverse-pass ones. After exhausting
forward-direction bars at the column endpoint, reverse-direction bars are physically
nearest and get picked up, producing a walk-back along one rail.

---

## Attempt 2: Contract-Based Converter (Patch 2)

**File**: `fill.py` — `_satin_bars_to_continuous_zigzag()`

**Change**: Simplified dedup (inline instead of post-process loop), updated docstring
to make the contract explicit: `_order_satin_bars_zigzag` handles ordering, this
function just converts.

**New code**:
```python
def _satin_bars_to_continuous_zigzag(ordered_bars):
    """Convert already ordered/oriented rail-to-rail satin bars into one true
    continuous satin zigzag path.

    Precondition: _order_satin_bars_zigzag() has already oriented each bar so
    bar[0] is the approach-side endpoint and bar[-1] is the opposite rail endpoint.

    This function must not re-sort bars."""
    bars = [list(bar) for bar in (ordered_bars or []) if bar and len(bar) >= 2]
    if not bars:
        return []
    path = [bars[0][0], bars[0][-1]]
    for bar in bars[1:]:
        next_point = bar[-1]
        if math.hypot(next_point[0] - path[-1][0], next_point[1] - path[-1][1]) > 1e-6:
            path.append(next_point)
    return path if len(path) >= 2 else []
```

**Result**: No behavioural change — mostly a docs/cleanup patch. Walk-back still present.

---

## Attempt 3: Remove connector_geom for Satin (Patch 3)

**File**: `stitch_plan.py` — `stitch_satin_object()`

**Change**: Changed `connector_geom=geom` to `connector_geom=None` in the top_satin
append call. This prevents `_append_polyline_stitches` from converting long travel
moves into hidden connector stitches within the satin geometry.

**Before**:
```python
current_pos, stitches, jumps = _append_polyline_stitches(
    events, satin_zigzag_path, stitch_len_px, current_pos, jump_threshold_px,
    "top_satin", obj_id, color,
    min_gap_px=small_gap_px,
    connector_geom=geom      # ← allows hidden stitching across design
)
```

**After**:
```python
current_pos, stitches, jumps = _append_polyline_stitches(
    events, satin_zigzag_path, stitch_len_px, current_pos, jump_threshold_px,
    "top_satin", obj_id, color,
    min_gap_px=small_gap_px,
    connector_geom=None       # ← trim/jump instead of hidden stitch
)
```

**Result**: Safety belt — if ordering ever fails, produces a trim/jump instead of
hidden stitch line. Does not fix the root cause. Walk-back still present.

---

## Attempt 4: Truncation at Column Endpoint (v1)

**File**: `fill.py` — added to end of `_order_satin_bars_zigzag()`, after greedy loop

**Change**: After greedy ordering, detect where bar centres reverse direction along
the primary column axis and truncate. Used projection onto axis from first 1/3 of bars.

**New code**:
```python
    # Truncate at the physical column endpoint...
    if len(ordered) >= 4:
        centres = [((b[0][0] + b[-1][0]) * 0.5, (b[0][1] + b[-1][1]) * 0.5)
                   for b in ordered]
        n_sample = max(2, len(ordered) // 3)
        dx = centres[n_sample - 1][0] - centres[0][0]
        dy = centres[n_sample - 1][1] - centres[0][1]
        axis_len = math.hypot(dx, dy)
        if axis_len > 1e-6:
            proj = [(c[0] * dx + c[1] * dy) / axis_len for c in centres]
            half = len(ordered) // 2
            spacings = [proj[i + 1] - proj[i] for i in range(half - 1)
                        if proj[i + 1] > proj[i]]
            avg_spacing = sum(spacings) / len(spacings) if spacings else axis_len
            cutoff = len(ordered)
            for i in range(1, len(proj)):
                if proj[i] < proj[i - 1] - avg_spacing * 0.5:
                    cutoff = i
                    break
            if cutoff < len(ordered):
                ordered = ordered[:cutoff]
```

**Result**: Bug — projection direction wasn't normalized. If centres decrease (e.g.
750→670), proj values are negative and increasing. The reversal check
`proj[i] < proj[i-1] - threshold` never triggers because proj increases. Walk-back
still present. Also: when forward/reverse bars share stations, greedy picks both
at each station before moving on, keeping centres monotonic — no reversal to detect.

---

## Attempt 5: Truncation v2 — Max Projection Tracking

**File**: `fill.py` — replaced truncation logic

**Change**: Use overall direction (centres[0] → centres[-1]), track running maximum
of projection, truncate when we back up > 3 bar spacings from max.

**New code**:
```python
    if len(ordered) >= 4:
        centres = [...]
        dx = centres[-1][0] - centres[0][0]
        dy = centres[-1][1] - centres[0][1]
        overall_len = math.hypot(dx, dy)
        if overall_len > 1e-6:
            proj = [(c[0] * dx + c[1] * dy) / overall_len for c in centres]
            spacings = [abs(proj[i] - proj[i - 1]) for i in range(1, len(proj))]
            avg_spacing = sum(spacings) / len(spacings) if spacings else overall_len
            max_proj = proj[0]
            cutoff = len(ordered)
            tol = avg_spacing * 3.0
            for i in range(1, len(proj)):
                if proj[i] > max_proj:
                    max_proj = proj[i]
                elif max_proj - proj[i] > tol:
                    cutoff = i
                    break
            if cutoff < len(ordered):
                ordered = ordered[:cutoff]
```

**Result**: Still doesn't catch the walk-back. Root cause discovered: bar centres
don't geometrically reverse — reverse-pass bars extend the column in the same
direction (e.g. 750→710→700→690→680→670, all monotonic). No reversal in centres,
so truncation never triggers. The walk-back is a ZIGZAG PATTERN issue, not a
geometric reversal.

---

## Attempt 6: Both Endpoints in Converter

**File**: `fill.py` — `_satin_bars_to_continuous_zigzag()`

**Change**: Append both `bar[0]` AND `bar[-1]` for bars[1:], not just `bar[-1]`.
This ensures the cross-stitch is never lost when consecutive bars' bar[-1] land
on the same rail.

**New code**:
```python
def _satin_bars_to_continuous_zigzag(ordered_bars):
    ...
    path = [bars[0][0], bars[0][-1]]
    for bar in bars[1:]:
        if math.hypot(bar[0][0] - path[-1][0], bar[0][1] - path[-1][1]) > 1e-6:
            path.append(bar[0])
        if math.hypot(bar[-1][0] - path[-1][0], bar[-1][1] - path[-1][1]) > 1e-6:
            path.append(bar[-1])
    return path if len(path) >= 2 else []
```

**Result**: Produces SQUARE satin stitch (alternating approach-side steps + crosses)
instead of continuous zigzag. This pattern was deliberately removed before the
refactor. User rejected. Walk-back still present.

**Why it fails**: The approach-side step (`path[-1] → bar[0]`) is a MINI same-rail
stitch at the station transition. This creates a visible square/ladder pattern
rather than a smooth zigzag. The golden v116 deliberately avoids this by only
taking bar[-1] — the zigzag is supposed to jump directly from one cross to the next
cross on the opposite rail, skipping the mini approach step.

---

## Attempt 7: Full Revert

Reverted both `fill.py` and `stitch_plan.py` to commit `0824326` (clean Phase 2.5).

---

## Current State

**Commit**: 0824326
**Files**: fill.py (original _order_satin_bars_zigzag with 4-candidate approach,
original _satin_bars_to_continuous_zigzag with bar[-1] only),
stitch_plan.py (connector_geom=geom)
**Tests**: All 14 regression checks pass
**Bug**: Still present — same as golden v116

---

## Key Learnings

1. **Greedy ordering fixes manual cut interleaving** (2 lines → 1 line on mouth)
   but exposes walk-back on sunspikes and doesn't eliminate it entirely.

2. **Truncation doesn't work** because bar centres don't reverse geometrically —
   reverse-pass bars extend the column in the same direction.

3. **Both-endpoints approach creates square satin** — was deliberately removed
   pre-refactor, not an acceptable pattern.

4. **connector_geom=None** is a safety belt, not a root-cause fix.

5. The walk-back is a ZIGZAG PATTERN issue: when greedy picks up reverse-pass bars,
   their `bar[-1]` endpoints land on the same physical rail for consecutive bars,
   creating a march instead of alternating crosses.

6. The fundamental tension: the converter takes only `bar[-1]` (to avoid square
   satin), but this loses the cross when consecutive bars share the same rail for
   `bar[-1]`. The fix needs to maintain alternating rails WITHOUT adding approach-side
   mini-stitches.
