#!/usr/bin/env python3
"""Generate debug SVGs showing the road-marking graph overlaid on source SVGs."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xml.etree.ElementTree as ET
from easystitch_core.trace import parse_traced_svg_for_structure
from easystitch_core.geometry import object_fill_geometry
from easystitch_core.road_marker import build_initial_graph
from shapely.geometry import Polygon

def render_debug_svg(source_svg_path, output_path):
    """Render road-marking nodes+edges overlaid on the source SVG."""
    svg_w, svg_h, fill_objects, stroke_objects = parse_traced_svg_for_structure(source_svg_path)
    
    # Read the source SVG for the background
    tree = ET.parse(source_svg_path)
    root = tree.getroot()
    ns = 'http://www.w3.org/2000/svg'
    
    # Build overlay elements
    overlay_elements = []
    
    for obj in stroke_objects:
        geom = object_fill_geometry(obj)
        if geom is None:
            continue
        
        graph = build_initial_graph(geom)
        if not graph.nodes:
            continue
        
        # Draw edges
        for eid, edge in graph.edges.items():
            sn = graph.nodes[edge.start_node_id]
            en = graph.nodes[edge.end_node_id]
            overlay_elements.append(
                f'<line x1="{sn.position[0]:.1f}" y1="{sn.position[1]:.1f}" '
                f'x2="{en.position[0]:.1f}" y2="{en.position[1]:.1f}" '
                f'stroke="magenta" stroke-width="2" opacity="0.8"/>'
            )
        
        # Draw nodes
        for nid, node in graph.nodes.items():
            color = {'sharp_corner': '#00ff00', 'endpoint': '#ff4444'}.get(node.type, '#8888ff')
            overlay_elements.append(
                f'<circle cx="{node.position[0]:.1f}" cy="{node.position[1]:.1f}" '
                f'r="5" fill="{color}" stroke="white" stroke-width="1.5"/>'
            )
            overlay_elements.append(
                f'<text x="{node.position[0]:.1f}" y="{node.position[1]:.0f}" '
                f'dy="-8" fill="white" font-size="10" text-anchor="middle" '
                f'stroke="black" stroke-width="3" paint-order="stroke">{nid}</text>'
            )
            overlay_elements.append(
                f'<text x="{node.position[0]:.1f}" y="{node.position[1]:.0f}" '
                f'dy="-8" fill="yellow" font-size="10" text-anchor="middle">{nid}</text>'
            )
        
        # Draw object outline in cyan
        if hasattr(geom, 'exterior'):
            pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in geom.exterior.coords)
            overlay_elements.append(
                f'<polyline points="{pts}" fill="none" stroke="cyan" '
                f'stroke-width="1" opacity="0.4"/>'
            )
    
    # Create output SVG
    overlay_xml = '\n    '.join(overlay_elements)
    output_svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}"
     style="background:#1a1a2e">
  <g transform="scale(1,-1) translate(0,-{svg_h})">
    {overlay_xml}
  </g>
  <g>
    <!-- Original paths as reference (flipped back) -->
    {_extract_paths(root, ns, svg_h)}
  </g>
  <!-- Legend -->
  <g transform="translate(10,{svg_h - 60})">
    <text x="0" y="0" fill="white" font-size="14" font-weight="bold">Road-Marking Debug</text>
    <circle cx="20" cy="16" r="5" fill="#00ff00"/><text x="30" y="21" fill="white" font-size="12">Sharp Corner</text>
    <line x1="100" y1="16" x2="140" y2="16" stroke="magenta" stroke-width="2"/><text x="145" y="21" fill="white" font-size="12">Edge</text>
    <circle cx="280" cy="16" r="5" fill="#ff4444"/><text x="290" y="21" fill="white" font-size="12">Endpoint</text>
    <line x1="380" y1="16" x2="420" y2="16" stroke="cyan" stroke-width="1"/><text x="425" y="21" fill="white" font-size="12">Boundary</text>
  </g>
</svg>'''
    
    with open(output_path, 'w') as f:
        f.write(output_svg)
    print(f'Wrote {output_path}')

def _extract_paths(root, ns, svg_h):
    """Extract path elements, flipping Y for SVG coordinate space."""
    paths = []
    for path_el in root.findall(f'.//{{{ns}}}path'):
        d = path_el.get('d', '')
        fill = path_el.get('fill', 'none')
        stroke = path_el.get('stroke', 'none')
        opacity = '0.3' if fill != 'none' else '0.5'
        paths.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
            f'opacity="{opacity}" stroke-width="1"/>'
        )
    return '\n    '.join(paths)

if __name__ == '__main__':
    base = os.path.dirname(os.path.abspath(__file__))
    render_debug_svg(os.path.join(base, 'puppy_traced.svg'), os.path.join(base, 'debug_puppy_graph.svg'))
    render_debug_svg(os.path.join(base, 'house_traced.svg'), os.path.join(base, 'debug_house_graph.svg'))
