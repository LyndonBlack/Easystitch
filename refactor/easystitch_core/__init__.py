#!/usr/bin/env python3
"""
EasyStitch Core — extracted backend modules.
"""

from .image_prep import (
    normalise_image,
    apply_simplify_filter,
    quantize_image,
    load_image_from_path,
    run_image_prep,
)

from .utils import (
    NeedSecondCutError,
    rgb2lab,
    lab2rgb,
    safe_stem,
    mm_to_px,
    _hex_color_to_rgb,
    color_luminance,
    _neighbors8,
    _polyline_length,
    _simplify_points,
    _points_to_svg_path,
    _rotate_xy,
    _rotate_geom,
    _arc_coords_between,
    _svg_polyline,
    _svg_debug_polyline,
    _svg_debug_dot,
    _svg_debug_text,
    image_to_data_uri,
)

from .trace import (
    find_vtracer_cli,
    count_svg_paths,
    trace_prepared_png,
    parse_traced_svg_for_structure,
    build_structure_payload_from_trace,
    extract_stroke_candidates,
    stroke_preview_svg,
    connected_components_bool,
    zhang_suen_thin,
    _skeleton_segments,
    split_source_path_object,
)

from .geometry import (
    object_fill_geometry,
    geometry_to_svg_d,
    split_fill_object_by_junction,
    split_fill_object_by_line,
    split_stroke_object_by_line,
    manual_split_object,
    generate_edge_walk_preview,
    _sample_subpath_points,
    _close_ring,
    extend_cut_line_local,
    _cut_line_guide_rungs_for_part,
    _skeleton_world_segments_for_geom,
    _sample_linestring,
    _rasterize_geom_mask,
    _sample_line_by_length_preview,
    _sample_line_by_spacing_preview,
    _nearest_point_on_line_preview,
    _normal_crossbar_inside_geom,
    _geometry_polygons,
)

from .fill import (
    generate_fill_preview_lines,
    fill_angle_for_geometry,
    sorted_design_colors,
    objects_for_pass,
)

from .satin import (
    generate_satin_preview_lines,
    generate_guided_satin_preview_lines,
    generate_satin_preview_lines_with_guides,
    build_satin_debug_overlay_svg,
    clip_manual_rung_to_geometry,
)

from .underlay import (
    generate_satin_underlay_preview_lines,
    lighter_object_blocker_geometry_for_underlay,
    foreground_blocker_geometry_for_object,
    subtract_blockers_for_top_fill,
    combined_satin_guide_rungs_for_object,
)

from .stitch_plan import (
    build_stitch_plan,
    build_stitch_preview_svg,
)

from .export_dst import (
    export_stitch_plan_to_dst,
)

from .road_marker import (
    RoadMarkedPath,
    Rung,
    Node,
    Edge,
    build_initial_graph,
)

from .export_pyembroidery import (
    export_stitch_plan_to_jef,
    export_stitch_plan_to_vp3,
)

__all__ = [
    "NeedSecondCutError",
    "rgb2lab",
    "lab2rgb",
    "safe_stem",
    "mm_to_px",
    "_hex_color_to_rgb",
    "color_luminance",
    "_neighbors8",
    "_polyline_length",
    "_simplify_points",
    "_points_to_svg_path",
    "_rotate_xy",
    "_rotate_geom",
    "_arc_coords_between",
    "_svg_polyline",
    "_svg_debug_polyline",
    "_svg_debug_dot",
    "_svg_debug_text",
    "image_to_data_uri",
    "normalise_image",
    "apply_simplify_filter",
    "quantize_image",
    "load_image_from_path",
    "run_image_prep",
    "find_vtracer_cli",
    "count_svg_paths",
    "trace_prepared_png",
    "parse_traced_svg_for_structure",
    "build_structure_payload_from_trace",
    "extract_stroke_candidates",
    "stroke_preview_svg",
    "connected_components_bool",
    "zhang_suen_thin",
    "_skeleton_segments",
    "split_source_path_object",
    "object_fill_geometry",
    "geometry_to_svg_d",
    "split_fill_object_by_junction",
    "split_fill_object_by_line",
    "split_stroke_object_by_line",
    "manual_split_object",
    "generate_edge_walk_preview",
    "_sample_subpath_points",
    "_close_ring",
    "extend_cut_line_local",
    "_cut_line_guide_rungs_for_part",
    "_skeleton_world_segments_for_geom",
    "_sample_linestring",
    "_rasterize_geom_mask",
    "_sample_line_by_length_preview",
    "_sample_line_by_spacing_preview",
    "_nearest_point_on_line_preview",
    "_normal_crossbar_inside_geom",
    "_geometry_polygons",
    "generate_fill_preview_lines",
    "fill_angle_for_geometry",
    "sorted_design_colors",
    "objects_for_pass",
    "generate_satin_preview_lines",
    "generate_guided_satin_preview_lines",
    "generate_satin_preview_lines_with_guides",
    "build_satin_debug_overlay_svg",
    "clip_manual_rung_to_geometry",
    "generate_satin_underlay_preview_lines",
    "lighter_object_blocker_geometry_for_underlay",
    "foreground_blocker_geometry_for_object",
    "subtract_blockers_for_top_fill",
    "combined_satin_guide_rungs_for_object",
    "build_stitch_plan",
    "build_stitch_preview_svg",
    "export_stitch_plan_to_dst",
    "export_stitch_plan_to_jef",
    "export_stitch_plan_to_vp3",
    "RoadMarkedPath",
    "Rung",
    "Node",
    "Edge",
    "build_initial_graph",
]
