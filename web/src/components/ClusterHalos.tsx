/**
 * ClusterHalos — faint translucent sphere halos around each cluster centroid.
 *
 * One <mesh> per cluster at the cluster's centroid with a radius matching the
 * cluster's computed spatial spread. The material is intentionally very faint
 * (opacity 0.04) so the halos read as subtle region markers without occluding
 * the star field.
 *
 * Material settings (reference §2 "Cluster Halos"):
 *   meshBasicMaterial — unlit; no depth write (always behind stars)
 *   transparent       — required for any opacity < 1
 *   opacity           — 0.04 (very faint, just visible against CANVAS_BG)
 *   depthWrite        — false (halos never mask nodes or edges)
 *   toneMapped        — false (additive-blending environment; bypass tone mapping)
 *
 * No pure helpers — the centroid and radius come pre-computed from the backend
 * layout endpoint (/api/graph/layout) and stored in ClusterSummary.
 */

import type { ClusterSummary } from "../lib/layoutTypes";

interface ClusterHalosProps {
  clusters: ClusterSummary[];
}

/**
 * Render one translucent sphere halo per cluster.
 *
 * Each sphere is positioned at the cluster's centroid (XYZ) and sized to its
 * radius (computed by the backend as 1.2× the max distance of member nodes from
 * the centroid). The cluster color is inherited from the layout endpoint.
 */
export function ClusterHalos({ clusters }: ClusterHalosProps) {
  if (clusters.length === 0) return null;

  return (
    <>
      {clusters.map((cluster) => (
        <mesh
          key={cluster.cluster_id}
          position={cluster.centroid}
        >
          <sphereGeometry args={[cluster.radius, 16, 16]} />
          <meshBasicMaterial
            color={cluster.color}
            transparent
            opacity={0.04}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
      ))}
    </>
  );
}
