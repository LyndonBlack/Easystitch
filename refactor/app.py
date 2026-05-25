#!/usr/bin/env python3
"""EasyStitch refactored app — imports from easystitch_core modules."""
import argparse
import base64
import os
import sys
import webbrowser
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from PIL import Image

from easystitch_core.utils import NeedSecondCutError, safe_stem, image_to_data_uri
from easystitch_core.image_prep import run_image_prep
from easystitch_core.trace import trace_prepared_png, parse_traced_svg_for_structure, extract_stroke_candidates
from easystitch_core.geometry import manual_split_object, split_fill_object_by_junction
from easystitch_core.stitch_plan import build_stitch_preview_svg, build_stitch_plan
from easystitch_core.export_dst import export_stitch_plan_to_dst
from easystitch_core.road_marker import (
    collect_satin_objects,
    render_satin_mask,
    clean_binary_mask,
    run_autotrace_centerline,
    parse_centerline_svg_to_polylines,
    clean_centerline_polylines,
    split_polylines_at_object_boundaries,
    tag_split_boundary_nodes,
    normalize_graph_topology,
    build_centerline_graph,
    build_road_graph_overlay_svg,
)
from easystitch_core.export_pyembroidery import export_stitch_plan_to_jef, export_stitch_plan_to_vp3



# ─────────────────────────────────────────────────────────────────────────────
# Flask App
# ─────────────────────────────────────────────────────────────────────────────

def create_app(initial_input: str | None, output_dir: str) -> Flask:
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.config["CURRENT_INPUT"] = os.path.abspath(initial_input) if initial_input else None
    app.config["OUTPUT_DIR"] = os.path.abspath(output_dir)
    app.config["UPLOAD_DIR"] = os.path.join(app.config["OUTPUT_DIR"], "_uploads")
    app.config["LAST_PREP"] = None
    app.config["LAST_TRACE"] = None
    app.config["LAST_STRUCTURE"] = None
    app.config["_ROAD_STATE"] = {}  # path_id -> RoadMarkedPath (session cache)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.route("/api/state")
    def api_state():
        current = app.config.get("CURRENT_INPUT")
        return jsonify({
            "has_image": bool(current),
            "input_path": current,
            "input_name": os.path.basename(current) if current else None,
            "output_dir": app.config["OUTPUT_DIR"],
        })

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        try:
            if "image" not in request.files:
                return jsonify({"ok": False, "error": "No image file uploaded"})
            f = request.files["image"]
            if not f.filename:
                return jsonify({"ok": False, "error": "Empty filename"})
            name = safe_stem(f.filename) + Path(f.filename).suffix.lower()
            save_path = os.path.join(app.config["UPLOAD_DIR"], name)
            f.save(save_path)

            # Validate that PIL can open it.
            with Image.open(save_path) as img:
                img.verify()

            app.config["CURRENT_INPUT"] = os.path.abspath(save_path)
            return jsonify({"ok": True, "path": app.config["CURRENT_INPUT"], "name": os.path.basename(save_path)})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})

    @app.route("/api/prep", methods=["POST"])
    def api_prep():
        try:
            current = app.config.get("CURRENT_INPUT")
            if not current:
                return jsonify({"ok": False, "error": "No image loaded"})
            body = request.get_json() or {}
            colors = int(body.get("colors", 12))
            max_size = int(body.get("max_size", 1000))
            result = run_image_prep(
                current,
                app.config["OUTPUT_DIR"],
                max_size=max_size,
                colors=colors,
                simplify_preset=str(body.get("simplify_preset", "none")),
                smoothing=int(body.get("smoothing", 0)),
                posterize_bits=int(body.get("posterize_bits", 0)),
                color_boost=float(body.get("color_boost", 1.0)),
                contrast_boost=float(body.get("contrast_boost", 1.0)),
            )
            app.config["LAST_PREP"] = result
            app.config["LAST_TRACE"] = None
            app.config["LAST_STRUCTURE"] = None
            return jsonify(result)
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})

    @app.route("/api/trace", methods=["POST"])
    def api_trace():
        try:
            prep = app.config.get("LAST_PREP")
            if not prep or not prep.get("output_path"):
                return jsonify({"ok": False, "error": "No prepared PNG available. Run Image Prep first."})

            body = request.get_json() or {}
            result = trace_prepared_png(
                prep["output_path"],
                app.config["OUTPUT_DIR"],
                stem=prep.get("stem", "image"),
                speckle=int(body.get("speckle", 8)),
                mode=str(body.get("mode", "spline")),
                hierarchical=str(body.get("hierarchical", "cutout")),
                color_precision=int(body.get("color_precision", 6)),
                gradient_step=int(body.get("gradient_step", 16)),
                corner_threshold=int(body.get("corner_threshold", 60)),
                segment_length=float(body.get("segment_length", 4.0)),
                splice_threshold=int(body.get("splice_threshold", 45)),
                path_precision=int(body.get("path_precision", 3)),
            )

            extraction_enabled = False
            if extraction_enabled:
                strokes = extract_stroke_candidates(
                    prep["output_path"],
                    min_component_area=int(body.get("stroke_min_area", 24)),
                    max_fill_ratio=float(body.get("stroke_max_fill_ratio", 0.42)),
                    min_aspect_ratio=float(body.get("stroke_min_aspect", 1.6)),
                    min_path_length=float(body.get("stroke_min_length", 14.0)),
                    ignore_near_white=bool(body.get("stroke_ignore_white", True)),
                )
                result.update(strokes)
            else:
                result.update({
                    "svg_w": prep.get("processed_width"),
                    "svg_h": prep.get("processed_height"),
                    "stroke_objects": [],
                    "stroke_count": 0,
                    "component_count": 0,
                    "stroke_preview_svg": "",
                })
            result["extraction_enabled"] = extraction_enabled

            app.config["LAST_TRACE"] = result
            app.config["LAST_STRUCTURE"] = None
            return jsonify(result)
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/structure/load")
    def api_structure_load():
        try:
            trace = app.config.get("LAST_TRACE")
            if not trace or not trace.get("output_path"):
                return jsonify({"ok": False, "error": "No traced SVG available. Run Trace first."})
            svg_w, svg_h, source_paths, objects = parse_traced_svg_for_structure(trace["output_path"])
            payload = {
                "ok": True,
                "svg_w": svg_w,
                "svg_h": svg_h,
                "source_paths": source_paths,
                "objects": objects,
            }
            app.config["LAST_STRUCTURE"] = payload
            return jsonify(payload)
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/structure/manual_split", methods=["POST"])
    def api_structure_manual_split():
        try:
            body = request.get_json() or {}
            obj = body.get("object")
            cut_points = body.get("cut_points") or []
            if not obj:
                return jsonify({"ok": False, "error": "No structure object supplied."})
            if len(cut_points) < 2:
                return jsonify({"ok": False, "error": "Two cut points are required."})
            out_objects = manual_split_object(obj, cut_points)
            cut_rung_count = sum(len(o.get("cut_guide_rungs") or []) for o in out_objects)
            return jsonify({"ok": True, "objects": out_objects, "cut_guide_rungs": cut_rung_count})
        except NeedSecondCutError as e:
            return jsonify({"ok": False, "needs_second_cut": True, "error": str(e)})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/structure/junction_split", methods=["POST"])
    def api_structure_junction_split():
        try:
            body = request.get_json() or {}
            obj = body.get("object")
            center = body.get("center")
            branch_points = body.get("branch_points") or []
            if not obj:
                return jsonify({"ok": False, "error": "No structure object supplied."})
            if not center or len(branch_points) < 3:
                return jsonify({"ok": False, "error": "Junction split needs a centre and at least three branch points."})
            if (obj.get("render_mode") or "fill") == "stroke":
                return jsonify({"ok": False, "error": "Junction split currently works on fill/column shapes, not stroke paths."})
            out_objects = split_fill_object_by_junction(obj, center, branch_points)
            cut_rung_count = sum(len(o.get("cut_guide_rungs") or []) for o in out_objects)
            return jsonify({"ok": True, "objects": out_objects, "cut_guide_rungs": cut_rung_count})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/preview", methods=["POST"])
    def api_stitches_preview():
        try:
            payload = request.get_json() or {}
            result = build_stitch_preview_svg(payload)
            return jsonify({
                "ok": True,
                "svg": result["svg"],
                "counts": result["counts"],
                "layers": result.get("layers", {}),
                "debug_svg": result.get("debug_svg", "")
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/plan", methods=["POST"])
    def api_stitches_plan():
        try:
            payload = request.get_json() or {}
            plan = build_stitch_plan(payload)
            return jsonify({"ok": True, "plan": plan})
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/export_dst", methods=["POST"])
    def api_stitches_export_dst():
        try:
            body = request.get_json() or {}
            plan = body.get("plan")
            filename = body.get("filename") or "easystitch.dst"
            settings = body.get("settings") or {}
            dst_bytes, stats, debug = export_stitch_plan_to_dst(plan, filename=filename, settings=settings)
            return jsonify({
                "ok": True,
                "filename": filename if str(filename).lower().endswith(".dst") else str(filename) + ".dst",
                "dst_base64": base64.b64encode(dst_bytes).decode("ascii"),
                "stats": stats,
                "debug": debug
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/export_jef", methods=["POST"])
    def api_stitches_export_jef():
        try:
            body = request.get_json() or {}
            plan = body.get("plan")
            filename = body.get("filename") or "easystitch.jef"
            settings = body.get("settings") or {}
            jef_bytes, stats, debug = export_stitch_plan_to_jef(plan, filename=filename, settings=settings)
            return jsonify({
                "ok": True,
                "filename": filename if str(filename).lower().endswith(".jef") else str(filename) + ".jef",
                "jef_base64": base64.b64encode(jef_bytes).decode("ascii"),
                "stats": stats,
                "debug": debug
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/stitches/export_vp3", methods=["POST"])
    def api_stitches_export_vp3():
        try:
            body = request.get_json() or {}
            plan = body.get("plan")
            filename = body.get("filename") or "easystitch.vp3"
            settings = body.get("settings") or {}
            vp3_bytes, stats, debug = export_stitch_plan_to_vp3(plan, filename=filename, settings=settings)
            return jsonify({
                "ok": True,
                "filename": filename if str(filename).lower().endswith(".vp3") else str(filename) + ".vp3",
                "vp3_base64": base64.b64encode(vp3_bytes).decode("ascii"),
                "stats": stats,
                "debug": debug
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


    @app.route("/api/roads/mask_only", methods=["POST"])
    def api_roads_mask_only():
        try:
            body = request.get_json() or {}
            settings = body.get("settings") or {}

            objects = body.get("objects") or []
            assignments = body.get("assignments") or {}
            svg_w = body.get("svg_w")
            svg_h = body.get("svg_h")

            if svg_w is None or svg_h is None:
                return jsonify({"ok": False, "error": "Missing svg_w or svg_h"})
            if not isinstance(objects, list):
                return jsonify({"ok": False, "error": "objects must be a list"})
            if not isinstance(assignments, dict):
                return jsonify({"ok": False, "error": "assignments must be an object"})

            scale = int(settings.get("mask_scale", 4))
            threshold = int(settings.get("threshold", 128))
            median_filter = bool(settings.get("median_filter", True))

            mask_result = render_satin_mask(
                objects,
                assignments,
                float(svg_w),
                float(svg_h),
                scale=scale,
                antialias=False,
            )
            clean_image = clean_binary_mask(
                mask_result["image"],
                median_filter=median_filter,
                threshold=threshold,
            )
            mask_result["image"] = clean_image

            png_buffer = BytesIO()
            clean_image.save(png_buffer, format="PNG")
            mask_png_base64 = base64.b64encode(png_buffer.getvalue()).decode("ascii")

            satin_objects = collect_satin_objects(objects, assignments)
            return jsonify({
                "ok": True,
                "mask": {
                    "width_px": mask_result["width_px"],
                    "height_px": mask_result["height_px"],
                    "scale": mask_result["scale"],
                    "svg_w": float(svg_w),
                    "svg_h": float(svg_h),
                    "satin_object_ids": mask_result["satin_object_ids"],
                    "excluded_object_ids": mask_result["excluded_object_ids"],
                },
                "debug": {
                    "mask_png_base64": mask_png_base64,
                    "satin_object_count": len(satin_objects),
                    "excluded_object_count": len(mask_result["excluded_object_ids"]),
                },
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})

    @app.route("/api/roads/centerline", methods=["POST"])
    def api_roads_centerline():
        try:
            body = request.get_json() or {}
            settings = body.get("settings") or {}

            objects = body.get("objects") or []
            assignments = body.get("assignments") or {}
            svg_w = body.get("svg_w")
            svg_h = body.get("svg_h")

            if svg_w is None or svg_h is None:
                return jsonify({"ok": False, "error": "Missing svg_w or svg_h"})
            if not isinstance(objects, list):
                return jsonify({"ok": False, "error": "objects must be a list"})
            if not isinstance(assignments, dict):
                return jsonify({"ok": False, "error": "assignments must be an object"})

            svg_w_f = float(svg_w)
            svg_h_f = float(svg_h)
            scale = int(settings.get("mask_scale", 4))
            threshold = int(settings.get("threshold", 128))
            median_filter = bool(settings.get("median_filter", True))
            min_length_px = float(settings.get("min_length_px", 5.0))
            simplify_tolerance = float(settings.get("simplify_tolerance", 1.0))
            snap_distance = float(settings.get("snap_distance", 3.0))
            despeckle_level = int(settings.get("despeckle_level", 8))
            filter_iterations = int(settings.get("filter_iterations", 4))
            error_threshold = float(settings.get("error_threshold", 2.0))
            autotrace_path = str(settings.get("autotrace_path") or "autotrace")

            mask_result = render_satin_mask(
                objects,
                assignments,
                svg_w_f,
                svg_h_f,
                scale=scale,
                antialias=False,
            )
            clean_image = clean_binary_mask(
                mask_result["image"],
                median_filter=median_filter,
                threshold=threshold,
            )

            autotrace_svg = run_autotrace_centerline(
                clean_image,
                autotrace_path=autotrace_path,
                despeckle_level=despeckle_level,
                filter_iterations=filter_iterations,
                error_threshold=error_threshold,
            )
            raw_polylines = parse_centerline_svg_to_polylines(autotrace_svg, scale=scale)
            clean_polylines = clean_centerline_polylines(
                raw_polylines,
                min_length_px=min_length_px,
                simplify_tolerance=simplify_tolerance,
            )
            # Phase B.3: split polylines at manual split boundaries between Satin objects
            split_polylines = split_polylines_at_object_boundaries(
                clean_polylines,
                objects,
                assignments,
                svg_w_f,
                svg_h_f,
                scale=scale,
            )
            graph = build_centerline_graph(split_polylines, snap_distance=snap_distance)
            # Phase B.3: tag nodes that sit between different Satin objects
            graph = tag_split_boundary_nodes(graph)
            # Phase C.9: normalize graph topology before the frontend builds roadSegments.
            # Existing visible nodes become hard breakpoints on nearby underlying edges.
            graph = normalize_graph_topology(graph, snap_tolerance=float(settings.get("topology_snap_tolerance", 8.0)))
            graph = tag_split_boundary_nodes(graph)
            satin_objects = collect_satin_objects(objects, assignments)
            overlay_svg = build_road_graph_overlay_svg(svg_w_f, svg_h_f, satin_objects, graph)

            png_buffer = BytesIO()
            clean_image.save(png_buffer, format="PNG")
            mask_png_base64 = base64.b64encode(png_buffer.getvalue()).decode("ascii")

            stats = {
                "satin_object_count": len(satin_objects),
                "excluded_object_count": len(mask_result["excluded_object_ids"]),
                "raw_polyline_count": len(raw_polylines),
                "clean_polyline_count": len(clean_polylines),
                "graph_node_count": len(graph.get("nodes", [])),
                "graph_edge_count": len(graph.get("edges", [])),
                "mask_width_px": mask_result["width_px"],
                "mask_height_px": mask_result["height_px"],
                "mask_scale": mask_result["scale"],
            }

            return jsonify({
                "ok": True,
                "graph": graph,
                "mask": {
                    "width_px": mask_result["width_px"],
                    "height_px": mask_result["height_px"],
                    "scale": mask_result["scale"],
                    "svg_w": svg_w_f,
                    "svg_h": svg_h_f,
                    "satin_object_ids": mask_result["satin_object_ids"],
                    "excluded_object_ids": mask_result["excluded_object_ids"],
                },
                "stats": stats,
                "debug": {
                    "mask_png_base64": mask_png_base64,
                    "autotrace_svg": autotrace_svg,
                    "overlay_svg": overlay_svg,
                },
            })
        except Exception as e:
            import traceback
            return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})

    return app


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="EasyStitch unified app prototype")
    p.add_argument("input", nargs="?", help="Optional image to load on startup")
    p.add_argument("--output-dir", default=None, help="Output directory, default: input folder or cwd")
    p.add_argument("--port", type=int, default=5001)
    p.add_argument("--no-browser", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    if args.input and not os.path.isfile(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
    elif args.input:
        output_dir = os.path.dirname(os.path.abspath(args.input))
    else:
        output_dir = os.getcwd()

    app = create_app(args.input, output_dir)

    url = f"http://127.0.0.1:{args.port}"
    print("\n" + "=" * 58)
    print("  EasyStitch Unified App — Phase 19.0a")
    print("=" * 58)
    print(f"  URL       : {url}")
    print(f"  Output dir: {output_dir}")
    if args.input:
        print(f"  Input     : {os.path.abspath(args.input)}")
    else:
        print("  Input     : use browser upload")
    print("=" * 58 + "\n")

    if not args.no_browser:
        webbrowser.open(url)

    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
