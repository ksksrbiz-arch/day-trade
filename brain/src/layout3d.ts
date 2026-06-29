// Compute static 3D positions for the graph via d3-force-3d (run once on load).
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCenter,
  forceX,
  forceY,
  forceZ,
} from "d3-force-3d";
import type { GraphEdge, GraphNode } from "./contract";

// gentle per-group separation so layers visually cluster
const GROUP_ANCHORS: Record<string, [number, number, number]> = {
  "Reasoning Council": [-22, 14, 0],
  Cognition: [0, 0, 0],
  Desk: [22, 10, 0],
  Tools: [22, -14, 8],
  Runtime: [-22, -14, -8],
  Memory: [0, -20, -12],
};

export function computeLayout(
  nodes: GraphNode[],
  edges: GraphEdge[]
): Record<string, [number, number, number]> {
  const simNodes = nodes.map((n) => ({ id: n.id, group: n.group || "Cognition" }));
  const simLinks = edges.map((e) => ({ source: e.source, target: e.target }));

  const sim = forceSimulation(simNodes, 3)
    .force("charge", forceManyBody().strength(-26))
    .force(
      "link",
      forceLink(simLinks)
        .id((d: any) => d.id)
        .distance(7)
        .strength(0.5)
    )
    .force("center", forceCenter(0, 0, 0))
    .force("x", forceX((d: any) => (GROUP_ANCHORS[d.group] || [0, 0, 0])[0]).strength(0.08))
    .force("y", forceY((d: any) => (GROUP_ANCHORS[d.group] || [0, 0, 0])[1]).strength(0.08))
    .force("z", forceZ((d: any) => (GROUP_ANCHORS[d.group] || [0, 0, 0])[2]).strength(0.08))
    .stop();

  for (let i = 0; i < 320; i++) sim.tick();

  const out: Record<string, [number, number, number]> = {};
  for (const n of simNodes as any[]) out[n.id] = [n.x, n.y, n.z];
  return out;
}
