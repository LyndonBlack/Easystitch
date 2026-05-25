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
