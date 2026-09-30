#!/usr/bin/env python3
"""Read and write MC3 pedestrian navigation graphs (<city>.graph, text;
mcBaGraph, used by mcCreatureNavFeederByGraph).

    MinExtents x y z / MaxExtents x y z / Graphs 1 { Graph <name> {
        VertexArray N { x y z ... }
        PlaneArray?  (normals: x y z 1)
        ConnectionArray N { Connection i { Vertex1 a Vertex2 b GraphNode n } }
            one per side of a shared edge: the node on that side
        GraphNodeArray N { GraphNode i { Vertices k {..} Connections m {..}
                                         Normal j Type sidewalk|corner } }
        NodeUserDataArray N { UserData i { ... } } } }
"""
import re


def read(path):
    t = open(path, encoding='latin1').read().replace('\r', '')
    g = {'name': re.search(r'Graph (\S+)', t).group(1)}
    va = re.search(r'VertexArray (\d+)\s*\{(.*?)\}', t, re.S)
    nums = [float(x) for x in va.group(2).split()]
    g['verts'] = [tuple(nums[i:i + 3]) for i in range(0, len(nums), 3)]
    g['conns'] = [tuple(int(x) for x in m) for m in re.findall(
        r'Connection \d+\s*\{\s*Vertex1 (\d+)\s*Vertex2 (\d+)\s*GraphNode (\d+)', t)]
    nodes = []
    for m in re.finditer(r'GraphNode \d+\s*\{\s*Vertices \d+\s*\{([^}]*)\}\s*Connections \d+\s*\{([^}]*)\}\s*Normal (\d+)\s*Type (\S+)', t):
        nodes.append({'verts': [int(x) for x in m.group(1).split()],
                      'conns': [int(x) for x in m.group(2).split()],
                      'normal': int(m.group(3)), 'type': m.group(4)})
    g['nodes'] = nodes
    ud = re.search(r'NodeUserDataArray \d+\s*\{(.*)\}\s*\}\s*\}\s*$', t, re.S)
    g['userdata_text'] = ud.group(1) if ud else ''
    return g
