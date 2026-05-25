from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "templates" / "index.html").read_text()
JS = (ROOT / "web" / "static" / "js" / "road_marker.js").read_text()
CSS = (ROOT / "web" / "static" / "css" / "app.css").read_text()


def test_phase_c_modal_shell_exists_and_open_button_wired():
    assert "RoadMarker.openSegmentEditor()" in HTML
    assert 'id="road-segment-editor-modal"' in HTML
    assert 'id="road-editor-svg"' in HTML
    assert 'id="road-bg-layer"' in HTML
    assert 'id="road-edge-layer"' in HTML
    assert 'id="road-node-layer"' in HTML
    assert 'id="road-highlight-layer"' in HTML


def test_phase_c_uses_single_shared_svg_viewbox_and_viewbox_zoom_pan():
    assert "function openSegmentEditor" in JS
    assert "function renderSegmentEditor" in JS
    assert "function setRoadEditorViewBox" in JS
    assert "function zoomRoadEditor" in JS
    assert "function resetRoadEditorView" in JS
    assert "viewBox" in JS
    assert "roadEditorViewBox" in JS
    assert "getScreenCTM()" in JS
    assert ".inverse()" in JS
    assert "style.transform" not in JS


def test_phase_c_background_outlines_are_current_satin_objects_only():
    assert "function renderRoadEditorBackground" in JS
    assert "structureObjects" in JS
    assert "stitchAssignments" in JS
    assert "defaultStitchType" in JS
    assert "road-bg-path" in JS
    assert "pointer-events: none" in CSS


def test_phase_c_edges_have_visible_and_hit_paths_for_reliable_selection():
    assert "road-edge-hit" in JS
    assert "road-edge-visible" in JS
    assert "data-segment-id" in JS
    assert "selectRoadSegmentFromEditor" in JS
    assert ".road-edge-hit" in CSS
    assert "stroke:transparent" in CSS
    assert "pointer-events:stroke" in CSS


def test_phase_c_selected_edge_uses_yellow_highlight_without_hiding_role_colour():
    assert "road-edge-selected-highlight" in JS
    assert "#ffeb3b" in JS
    assert "setRoadPathStroke(visible, roleColor(seg.role))" in JS
    assert "updateRoadEditorSelectionHighlight" in JS
    assert ".road-edge-selected-highlight" in CSS


def test_secondary_role_is_orange_for_halo_readability():
    assert "case 'secondary': return '#ff9800';" in JS
    assert "#4caf50" not in JS


def test_role_repaints_use_inline_style_to_override_css_stroke_defaults():
    assert "function setRoadPathStroke" in JS
    assert "path.style.setProperty('stroke', color)" in JS
    assert "setRoadPathStroke(visible, roleColor(seg.role))" in JS


def test_small_centerline_toolbar_overlay_has_separate_5px_hit_paths():
    assert "road-small-edge-visible" in JS
    assert "road-small-edge-hit" in JS
    assert "hit.setAttribute('stroke-width', '5')" in JS
    assert "hit.setAttribute('pointer-events', 'stroke')" in JS
    assert "overlayVisibleEdgeElement" in JS
    assert ".road-edge-hit{fill:none;stroke:transparent;stroke-width:12" in CSS


def test_small_centerline_toolbar_selection_highlight_does_not_hide_role_colour():
    assert "updateSmallOverlaySelectionHighlight" in JS
    assert "road-small-edge-selected-highlight" in JS
    assert "setRoadPathStroke(el, roleColor(seg.role))" in JS
    assert "el.setAttribute('stroke', seg.selected ? '#ffeb3b'" not in JS


def test_phase_c_marking_and_reopen_state_are_supported_without_stitch_scope():
    assert "markSelectedSegment('primary')" in HTML
    assert "markSelectedSegment('secondary')" in HTML
    assert "markSelectedSegment('ignore')" in HTML
    assert "clearSelectedSegmentMark()" in HTML
    assert "closeSegmentEditor" in JS
    assert "roadSegmentsBuilt" in JS
    assert "segment editor" in JS.lower()
    forbidden = ["generate_segment_rungs", "satin_v2", "build_stitch_plan", "previewStitches()"]
    for token in forbidden:
        assert token not in JS
