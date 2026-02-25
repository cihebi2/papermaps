from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def export_graph_json(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodes": nodes,
        "edges": edges,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def export_graph_gexf(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    gexf = ET.Element(
        "gexf",
        {
            "xmlns": "http://www.gexf.net/1.2draft",
            "version": "1.2",
        },
    )
    graph = ET.SubElement(gexf, "graph", {"mode": "static", "defaultedgetype": "directed"})
    nodes_el = ET.SubElement(graph, "nodes")
    edges_el = ET.SubElement(graph, "edges")

    for node in nodes:
        ET.SubElement(
            nodes_el,
            "node",
            {
                "id": str(node["id"]),
                "label": str(node.get("label") or node["id"]),
            },
        )

    for idx, edge in enumerate(edges):
        ET.SubElement(
            edges_el,
            "edge",
            {
                "id": str(idx),
                "source": str(edge["source"]),
                "target": str(edge["target"]),
                "label": str(edge.get("relation") or ""),
            },
        )

    xml_text = ET.tostring(gexf, encoding="unicode")
    out_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + xml_text, encoding="utf-8")
    return out_path


def export_graph_html(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>papermaps graph</title>
  <style>
    html, body, #network {{
      margin: 0;
      width: 100%;
      height: 100%;
      font-family: Arial, sans-serif;
      background: #f3f5f7;
    }}
    .bar {{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      background: #ffffff;
      border-bottom: 1px solid #d4dae1;
      padding: 8px 12px;
      z-index: 10;
      font-size: 14px;
    }}
    #network {{
      position: fixed;
      top: 44px;
      bottom: 0;
      left: 0;
      right: 0;
    }}
  </style>
</head>
<body>
  <div class="bar">papermaps graph viewer (drag/zoom/select nodes)</div>
  <div id="network"></div>
  <script src="https://unpkg.com/vis-network@9.1.2/dist/vis-network.min.js"></script>
  <script>
    const payload = {payload};
    const nodes = new vis.DataSet(payload.nodes.map(n => {{
      return {{
        id: n.id,
        label: n.label || n.id,
        title: `${{n.label || n.id}}\\nDOI: ${{n.doi || '-'}}\\nDate: ${{n.published_date || '-'}}`,
      }};
    }}));
    const edges = new vis.DataSet(payload.edges.map(e => {{
      return {{
        from: e.source,
        to: e.target,
        label: e.relation || '',
        arrows: 'to',
      }};
    }}));
    const network = new vis.Network(
      document.getElementById('network'),
      {{ nodes, edges }},
      {{
        interaction: {{ hover: true }},
        nodes: {{
          shape: 'dot',
          size: 10,
          font: {{ size: 12 }},
        }},
        edges: {{
          color: '#6b7a8b',
          font: {{ align: 'middle', size: 10 }},
          smooth: true,
        }},
        physics: {{
          stabilization: true,
          barnesHut: {{ springLength: 120 }}
        }}
      }}
    );
  </script>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    return out_path
