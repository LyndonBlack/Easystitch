import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "web" / "static" / "js" / "road_marker.js"


def run_road_marker_builder(graph):
    script = f"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync({json.dumps(str(JS_PATH))}, 'utf8');
const sandbox = {{
  console,
  roadSegments: [],
  roadSelectedSegmentId: null,
  roadSegmentsBuilt: false,
  document: {{ getElementById: () => null, querySelectorAll: () => [] }},
}};
const RoadMarker = vm.runInNewContext(code + "\\nRoadMarker;", sandbox);
const graphData = {json.dumps(graph)};
RoadMarker.buildRoadSegmentsFromGraph(graphData);
process.stdout.write(JSON.stringify({{
  built: sandbox.roadSegmentsBuilt,
  selected: sandbox.roadSelectedSegmentId,
  segments: sandbox.roadSegments,
  graph: graphData,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_phase_c7_splits_edge_at_intermediate_existing_nodes_and_clips_points():
    graph = {
        "nodes": [
            {"id": "node_1", "x": 0, "y": 0},
            {"id": "node_2", "x": 10, "y": 1.5},
            {"id": "node_3", "x": 20, "y": -1.5},
            {"id": "node_9", "x": 30, "y": 0},
        ],
        "edges": [
            {
                "id": "edge_12",
                "source": "node_1",
                "target": "node_9",
                "points": [[0, 0], [10, 0], [20, 0], [30, 0]],
                "length": 30,
                "source_object_ids": ["obj_a"],
            }
        ],
    }

    out = run_road_marker_builder(graph)

    assert out["built"] is True
    assert [s["source_node"] for s in out["segments"]] == ["node_1", "node_2", "node_3"]
    assert [s["target_node"] for s in out["segments"]] == ["node_2", "node_3", "node_9"]
    assert [s["source_edge_id"] for s in out["segments"]] == ["edge_12", "edge_12", "edge_12"]
    assert [s["edge_id"] for s in out["segments"]] == ["edge_12__seg_0001", "edge_12__seg_0002", "edge_12__seg_0003"]
    assert all(s["role"] == "unmarked" and s["priority"] is None for s in out["segments"])
    assert all(s["source_object_ids"] == ["obj_a"] for s in out["segments"])

    # Child spans are clipped to their own source/target interval, not the whole parent edge.
    assert out["segments"][0]["points"] == [[0, 0], [10, 0]]
    assert out["segments"][1]["points"] == [[10, 0], [20, 0]]
    assert out["segments"][2]["points"] == [[20, 0], [30, 0]]
    assert all(s["points"] != [[0, 0], [10, 0], [20, 0], [30, 0]] for s in out["segments"])


def test_phase_c7_ignores_nodes_outside_four_px_tolerance_and_duplicate_endpoints():
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0, "y": 0},
            {"id": "node_b", "x": 30, "y": 0},
            {"id": "node_near_start", "x": 0.5, "y": 0.5},
            {"id": "node_far", "x": 15, "y": 4.5},
        ],
        "edges": [
            {"id": "edge_a", "source": "node_a", "target": "node_b", "points": [[0, 0], [30, 0]], "length": 30}
        ],
    }

    out = run_road_marker_builder(graph)

    assert len(out["segments"]) == 1
    seg = out["segments"][0]
    assert seg["source_node"] == "node_a"
    assert seg["target_node"] == "node_b"
    assert seg["points"] == [[0, 0], [30, 0]]
    assert seg["source_edge_id"] == "edge_a"


def test_phase_c7_frontend_does_not_create_generated_intersection_nodes():
    graph = {
        "nodes": [
            {"id": "node_l", "x": 0, "y": 10},
            {"id": "node_r", "x": 20, "y": 10},
            {"id": "node_t", "x": 10, "y": 0},
            {"id": "node_b", "x": 10, "y": 20},
        ],
        "edges": [
            {"id": "edge_h", "source": "node_l", "target": "node_r", "points": [[0, 10], [20, 10]], "length": 20},
            {"id": "edge_v", "source": "node_t", "target": "node_b", "points": [[10, 0], [10, 20]], "length": 20},
        ],
    }

    out = run_road_marker_builder(graph)

    assert out["graph"]["nodes"] == graph["nodes"]
    assert len(out["segments"]) == 2
    assert {segment["source_edge_id"] for segment in out["segments"]} == {"edge_h", "edge_v"}


def test_phase_c7_consumes_backend_normalized_edges_without_resplitting_topology():
    graph = {
        "nodes": [
            {"id": "node_a", "x": 0, "y": 0},
            {"id": "node_mid", "x": 10, "y": 0},
            {"id": "node_b", "x": 30, "y": 0},
        ],
        "edges": [
            {
                "id": "edge_long_n1",
                "source_edge_id": "edge_long",
                "source": "node_a",
                "target": "node_mid",
                "points": [[0, 0], [10, 0]],
                "length": 10,
            },
            {
                "id": "edge_long_n2",
                "source_edge_id": "edge_long",
                "source": "node_mid",
                "target": "node_b",
                "points": [[10, 0], [30, 0]],
                "length": 20,
            },
        ],
    }

    out = run_road_marker_builder(graph)

    assert [(s["source_node"], s["target_node"], s["points"]) for s in out["segments"]] == [
        ("node_a", "node_mid", [[0, 0], [10, 0]]),
        ("node_mid", "node_b", [[10, 0], [30, 0]]),
    ]
    assert [s["source_edge_id"] for s in out["segments"]] == ["edge_long", "edge_long"]
