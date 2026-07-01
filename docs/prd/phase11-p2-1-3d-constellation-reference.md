# Seam 3D Constellation Explorer — Modeling Reference

*Derived from a deep read of codebase-memory-mcp/graph-ui. Intended as the seed document for Seam's implementation plan.*

---

## 1. Executive Summary

**What graph-ui is:** A standalone React Three Fiber (R3F) 3D graph explorer that renders a pre-positioned node cloud of up to 2,000–10,000 nodes from a codebase-memory Neo4j/SQLite backend. The entire layout is computed server-side in C (Barnes-Hut ForceAtlas2), shipped to the client as pre-baked `x/y/z` floats, and rendered in a single WebGL frame. The client is a pure renderer — zero layout math.

**Stack:** React 18 + Vite + TypeScript, `@react-three/fiber`, `@react-three/drei`, `@react-three/postprocessing`, `three`, Tailwind CSS v3, no state manager (local `useState` + react-query).

**The 3-5 highest-value techniques Seam should model:**

1. **InstancedMesh node cloud** — all ~5,500 nodes in one GPU draw call, per-instance color and scale via `Float32Array`, no per-node JSX. This is the only viable approach at this node count.

2. **Color-boost-above-1.0 → Bloom glow** — nodes use `meshBasicMaterial` with `toneMapped=false`; their RGB values are multiplied to exceed 1.0, which the post-processing Bloom pass picks up as a corona. No emissive material, no extra pass per node. The boost factor is `1.2 + brightness * 0.8` (1.2× for dim/red nodes, 2.0× for white/blue nodes).

3. **Server-side layout** — all ForceAtlas2 physics runs in the backend. The client receives `{x, y, z, size, color}` per node and draws them. Seam must add a new `/api/graph/layout` FastAPI endpoint that computes and caches positions.

4. **Additive-blended `LineSegments`** — edges use `THREE.AdditiveBlending + depthWrite=false + toneMapped=false`. Overlapping edges accumulate brightness, producing nebula filaments on the dark background with zero explicit alpha management.

5. **Glassmorphic panel shell** — `bg-[#0b1920]/80–95 + backdrop-blur-md/xl` over the canvas. The three-column resizable layout with `localStorage`-persisted widths is copy-paste ready.

---

## 2. Rendering Architecture

### Canvas Setup

```
<Canvas
  camera={{ position: [0, 0, 800], fov: 50, near: 0.1, far: 100000 }}
  gl={{ antialias: false, alpha: false, powerPreference: 'high-performance' }}
  dpr={[1, 1.5]}         // ← CRITICAL: stays below MSAA failure range on Apple Silicon
  style={{ background: '#06090f' }}
>
```

`antialias: false` + `dpr` max 1.5 are load-bearing together. The dark background + additive blending masks aliasing; the DPR cap avoids the MSAA compositor failure mode on Retina/Apple Silicon displays. Do not raise dpr above 1.5.

### Lighting

```
<ambientLight intensity={0.5} />
<pointLight position={[500, 500, 500]} intensity={0.6} />
<pointLight position={[-300, -200, -300]} intensity={0.4} color="#6040ff" />
```

The purple fill light at `[-300,-200,-300]` is cosmetic depth-cuing for any future non-node geometry. It does NOT illuminate nodes — `meshBasicMaterial` is unlit. Omit or keep; it does not affect performance.

### Node Cloud — `InstancedMesh`

**Primitive:**
```jsx
<instancedMesh
  args={[undefined, undefined, nodes.length]}
  frustumCulled={false}          // no pop-in during orbit
  onPointerOver={handleHover}
  onPointerOut={() => setHovered(null)}
  onClick={handleClick}
>
  <sphereGeometry args={[1, 32, 24]} />    // radius=1, 768 tri/node
  <meshBasicMaterial vertexColors toneMapped={false} />
</instancedMesh>
```

**Per-frame in `useFrame`:**
- Write position + scale matrix for every instance: highlighted → `size * 0.5`, dimmed → `size * 0.2`
- Upload color `Float32Array` (nodes.length × 3) via `instancedBufferAttribute` on `geometry.attributes.color`

**Color computation (in `useMemo` on `highlightedIds` change):**
```
highlighted:  r *= (1.2 + brightness*0.8)   // ← values exceed 1.0; Bloom fires
              g *= boost
              b *= boost
dimmed:       r *= 0.15   g *= 0.15   b *= 0.15
```

`brightness` of a node's color = `(r + g + b) / 3` in [0,1]. Red nodes (brightness ≈ 0.56) get 1.65× boost; blue-white nodes get 2.0× boost.

**Scale ceiling:** 5,500 nodes × 768 triangles = 4.2M triangles/frame in a single draw call. Acceptable. If Seam's node count grows to 10k+, add a secondary (lower-segment) LOD sphere for far-camera nodes.

### Edge Lines — `LineSegments`

**Primitive:**
```jsx
<lineSegments geometry={geo}>
  <lineBasicMaterial
    vertexColors
    transparent
    opacity={opacity}
    blending={THREE.AdditiveBlending}
    depthWrite={false}
    toneMapped={false}
  />
</lineSegments>
```

**Geometry (in `useMemo`):**
- `positions` Float32Array: 6 floats per edge (src.xyz + tgt.xyz)
- `colors` Float32Array: 6 floats per edge (both endpoints same color × intensity)

**Intensity levels:**
| Condition | Intensity |
|---|---|
| Both endpoints highlighted | 0.50 |
| Same cluster, no highlight | 0.25 |
| Partial highlight (one endpoint) | 0.04 |
| Cross-cluster, no highlight | 0.06 |
| Both endpoints outside highlight set | *edge skipped entirely* |

Cluster key = first 2 slash-separated path components of `file_path`.

**Geometry rebuild:** The `LineSegments` geometry is rebuilt in `useMemo` on every `highlightedIds` change. On 28,649 edges this is a significant Float32Array allocation + GPU upload per click. Acceptable for Seam's interaction cadence; do not call this on every mouse-move.

### Node Labels — Canvas Sprite

No `@react-three/drei` `Html` or `Text`. Pure Three.js sprites:
1. Draw text to an offscreen `<canvas>` at `64px Inter 600`, device pixel ratio capped at 2
2. 24px horizontal / 14px vertical padding
3. 8px round-join black stroke (`rgba(0,0,0,0.9)`) before fill in `node.color`
4. Wrap in `THREE.CanvasTexture` (SRGBColorSpace, LinearFilter both min+mag, `generateMipmaps=false`)
5. Attach to `THREE.Sprite` at position `[x, y + size*0.7 + worldHeight/2, z]`, `renderOrder=20`, `frustumCulled=false`

**Cap:** 80 labels max, sorted descending by `node.size`. When a highlight set is active, only highlighted nodes get labels (also capped at 80).

Dispose `CanvasTexture` in `useEffect` cleanup. Do not rebuild sprites on every render tick.

### Post-Processing — Bloom

```jsx
<EffectComposer multisampling={0}>
  <Bloom
    luminanceThreshold={0.3}
    luminanceSmoothing={0.7}
    intensity={1.2}
    mipmapBlur={true}
    radius={0.6}
  />
</EffectComposer>
```

`multisampling={0}` is required (combined with `antialias=false`). The `mipmapBlur=true` + `radius=0.6` produces a tight bright ring around the node plus a soft wider corona. At high cluster density all nodes can merge into one bright blob — this is a known gotcha for Seam's 5,500 node count, especially dense clusters.

### Camera / OrbitControls / Auto-Rotate

```jsx
<OrbitControls
  enableDamping
  dampingFactor={0.08}
  rotateSpeed={0.5}
  zoomSpeed={1.5}
  minDistance={10}
  maxDistance={50000}
  autoRotateSpeed={0.4}
/>
```

**Idle auto-rotation:** After `IDLE_TIMEOUT_MS = 60_000` ms with no `pointerdown` or `wheel`, set `controlsRef.current.autoRotate = true`. Implemented as a `lastInteraction` ref checked in `useFrame`, not `setInterval`.

**Camera fly-to (`CameraAnimator`):**
```
useFrame: {
  progress.current += 0.02   // 50 frames to complete at 60fps
  t = 1 - Math.pow(1 - progress.current, 3)   // ease-out cubic
  camera.position.lerp(target.position, t * 0.08)
  camera.lookAt(target.lookAt)
}
```

The inner `0.08` lerp factor means the camera asymptotically approaches the target — it never exactly arrives. After `progress >= 1.0` the `useFrame` work stops and `OrbitControls` resumes full control. Note: `camera.lookAt()` called per-frame while `OrbitControls` is active can cause a conflict on the next user drag; this is accepted as-is since the animation is short.

**Camera target computation:**
```
centroid = average position of all highlightedIds
maxSpread = max distance from centroid among highlighted nodes
distance = max(minDist, maxSpread * 3)
minDist = (highlightedIds.size <= 5) ? 300 : 200
cameraPosition = [cx + d*0.2, cy + d*0.15, cz + d]
lookAt = centroid
```

---

## 3. The Layout Algorithm

### Why Server-Side

The client receives pre-positioned nodes. Zero layout math in the browser. This is not a design choice — it is a performance requirement. For 2,000–5,500 nodes, 40 iterations of force-directed layout with Barnes-Hut O(n log n) repulsion takes 200–500ms in optimized C. In Python/JS on the main thread it would block rendering for seconds.

### The 8-Step Pipeline (from `layout3d.c`)

**Step 1: Node selection.** Query up to `max_nodes` from the store (default 2,000, hard max 10,000). For Seam, order by degree DESC so the most-connected symbols are shown first when capped — the C code takes first-N in store order, which is arbitrary.

**Step 2: Sort node-ID → index map** (qsort) for O(log n) binary-search edge filtering.

**Step 3: Edge filtering.** For each edge type, keep only edges where both endpoints are in the rendered node set (binary search). Build per-node degree array (undirected: both endpoints incremented).

**Step 4: BFS call-depth.** Seed at depth 0: nodes with label `Route`, `File`, `Module`, or `Package` (or in-degree-0 nodes as fallback). BFS propagates `depth + 1` along directed edges. Unvisited nodes get depth 0. **For Seam**: use `seam/analysis/processes.py` `list_entry_points()` as depth-0 seeds, then BFS along `call` edges from the `edges` table.

**Step 5: Seed ring positions.**
```
cluster_key = first 3 slash-separated components of file_path
             e.g. "src/indexer/graph.py" → "src/indexer/graph"
             e.g. "main.py" → "" (root files all share the same cluster angle)

// FNV-1a hash (32-bit, UNSIGNED arithmetic — simulate with & 0xFFFFFFFF in Python)
hash = FNV1a(cluster_key)
  basis = 2166136261
  prime = 16777619
  for each byte: hash = (hash XOR byte) * prime & 0xFFFFFFFF

angle = (hash & 0xFFFF) / 65535 * 2π
radius = 500 + ((hash >> 16) & 0xFF) / 255 * 250   // range 500–750

// Per-node jitter (LCG seeded from FNV1a of qualified_name)
jitter_seed = FNV1a(qualified_name)
jitter_seed = (jitter_seed * 1103515245 + 12345) & 0xFFFFFFFF
jitter_x = ((jitter_seed >> 16) & 0x7FFF) / 32768.0 - 0.5  // ±0.5, multiplied by 40 → ±20 units
jitter_seed = (jitter_seed * 1103515245 + 12345) & 0xFFFFFFFF
jitter_y = ((jitter_seed >> 16) & 0x7FFF) / 32768.0 - 0.5

seed_x = cos(angle) * radius + jitter_x * 40
seed_y = sin(angle) * radius + jitter_y * 40
seed_z = -call_depth * 50.0    // entry points at z=0, callees descend
```

**Step 6: Barnes-Hut ForceAtlas2 — 40 iterations.**
```
BH_THETA = 1.2
kr (repulsion) = 8.0
ka (attraction) = 1.0
k_anchor = 0.25 * mass   // high-degree nodes anchor more strongly

Each iteration:
  build octree from current positions
  for each node: compute repulsion via BH approximation (F = kr*m1*m2/d, direction away)
  for each edge: add bi-directional attraction (F = ka * delta_xyz, pulls endpoints)
  for each node: add anchor spring to seed position (F = k_anchor * (pos - seed))
  displacement cap: if |F * speed| > 8 units, scale down speed

```

**Step 7:** Copy optimized `x/y/z` to result nodes.

**Step 8:** Output edges with original node IDs.

### Seam Backend Endpoint Required

Seam must add a new route to `seam/server/web.py`:

```
GET /api/graph/layout?max_nodes=2000
```

**Returns:**
```json
{
  "nodes": [{"id": 42, "x": 312.4, "y": -88.2, "z": -150.0,
              "label": "function", "name": "parse_file",
              "file_path": "seam/indexer/parser.py",
              "size": 4.0, "color": "#ffe080"}],
  "edges": [{"source": 42, "target": 107, "type": "call"}],
  "total_nodes": 5488
}
```

**Implementation strategy for Seam (Python, no C):**
- Use `numpy` for force computation (vectorized repulsion/attraction per iteration)
- Use `scipy.spatial.cKDTree` for approximate O(n log n) repulsion (k-nearest approximation instead of true BH octree — acceptable for n ≤ 5,500)
- Or implement a simple Python BH octree class (~100 lines)
- Cache the result in memory keyed by `(db_mtime, max_nodes)` with TTL matching `SEAM_STALENESS_TTL_SECONDS`
- A full layout compute for 2,000 nodes in Python/numpy should run in 1–3 seconds; acceptable as a one-time cost per session

---

## 4. Interaction & Selection

### Raycasting — Free via R3F

R3F handles instanced raycasting natively. Register pointer events on the `<instancedMesh>` element, not on individual nodes:

```jsx
<instancedMesh
  onPointerOver={(e) => { e.stopPropagation(); onHover(nodes[e.instanceId]); }}
  onPointerOut={() => onHover(null)}
  onClick={(e) => { e.stopPropagation(); onClick(nodes[e.instanceId]); }}
>
```

`e.instanceId` is injected by R3F. No custom raycasting code required. Raycasts are throttled to the render loop by R3F internally — no debounce needed.

### Hover → Tooltip

`hovered` state lives in `GraphScene` as `useState<GraphNode | null>`. While non-null, mount `<NodeTooltip>`:

```jsx
// NodeTooltip.tsx
<Html
  position={[node.x, node.y + node.size * 0.7, node.z]}
  center
>
  <div style={{ pointerEvents: 'none' }} className="bg-[#1a1a2e]/95 backdrop-blur ...">
    <span style={{ background: colorForKind(node.label) }} />  {/* color dot */}
    <span>{node.name}</span>
    <span>{node.file_path}</span>
  </div>
</Html>
```

Key: `pointerEvents: none` on the tooltip div so it never blocks subsequent raycasts. `@react-three/drei` `Html` reprojects the 3D anchor to screen space every frame automatically.

### Click → State Machine (lives in `GraphTab`)

```
handleNodeClick(node):
  1. setSelectedNode(node)
  2. connectedIds = {node.id} ∪ {all direct neighbor IDs from filteredData.edges}
     (single linear O(edges) scan — acceptable)
  3. setHighlightedIds(connectedIds)
  4. setCameraTarget(computeCameraTarget(filteredData.nodes, connectedIds))

onClose:
  setSelectedNode(null)
  setHighlightedIds(null)
  setCameraTarget(null)

onNavigate(newNode):   // from NodeDetailPanel connection list
  handleNodeClick(newNode)   // re-runs the same state machine
```

The `CameraAnimator` component watches `cameraTarget` via `useEffect`, resets `progress.current = 0` when it changes, then runs the lerp in `useFrame`.

### NodeDetailPanel

- Scans `allEdges` in `useMemo` for `source === node.id` (outbound) and `target === node.id` (inbound)
- Groups by `edgeType`, sorted descending by count
- Renders up to 25 entries per group with "+N more" label (no virtualization — hard cap)
- Each entry is a `<button>` calling `onNavigate(connectedNode)` for panel-to-panel navigation

For Seam, the detail panel content maps to `seam context <symbol>` output: callers, callees, cluster peers, why comments. The panel can be populated directly from the `/api/symbol/{name}` route already in `seam/server/web.py`.

---

## 5. Filters, HUD & Panels

### Three-Column Layout

```
┌─────────────────────────────────────────────────────────┐
│ [FilterPanel] [border-b]                                │  ← fixed-height flush
│ [Sidebar / file tree]  ║  [3D Canvas]  ║  [DetailPanel] │
│ style={{width: leftW}} ║  flex-1       ║  style={{width: rightW}}
└─────────────────────────────────────────────────────────┘
```

Left column: `shrink-0 bg-[#0b1920]/90 backdrop-blur-md`. Right column: only mounts when `selectedNode !== null`. Canvas: `flex-1 relative overflow-hidden`.

### Panel Width Persistence

```ts
// Load
const loadWidth = (key: string, fallback: number): number => {
  const stored = localStorage.getItem(key)
  if (!stored) return fallback
  const n = parseInt(stored, 10)
  return Math.min(Math.max(n, 150), 600)
}

// Save (called inside onResize callback during drag)
const saveWidth = (key: string, value: number) =>
  localStorage.setItem(key, String(Math.round(value)))

// Keys
'cbm-left-w'   // default 260, clamp [150, 500] during drag
'cbm-right-w'  // default 280, clamp [200, 500] during drag
```

Note: load-time upper clamp is 600; drag-time upper clamp is 500. This is a minor inconsistency in the source — pick one value for Seam (500 is correct).

### ResizeHandle

```tsx
// w-1 cursor-col-resize hover:bg-primary/30 active:bg-primary/50
// useRef: dragging, lastX
// onPointerDown: setPointerCapture, dragging=true
// onPointerMove: delta = e.clientX - lastX; onResize(side==='left' ? delta : -delta)
// onPointerUp: releasePointerCapture, dragging=false
```

No external library. The parent (not the handle) persists to `localStorage`.

### FilterPanel Model

- Receives **raw unfiltered `data`**, not `filteredData` — counts never change as filters are applied
- `labelCounts` and `edgeTypeCounts` derived in `useMemo`, sorted descending by count
- Each filter chip: `<button>` with color dot + label + count; on = `border-white/[0.08] bg-white/[0.04]`; off = `border-transparent opacity-25`
- All / None controls call parent callbacks that reset the enabled `Set` to full vocabulary or `new Set()`
- No shadcn Checkbox — hand-rolled `w-3.5 h-3.5 rounded border` with `✓` glyph at `text-[9px]`

**For Seam's filter vocabulary:**
- Node kinds: `function | class | method | interface | type | field` (6 kinds)
- Edge kinds: `call | import | extends | implements | instantiates | holds | reads | writes | uses` (9 kinds)

### HUD Stats Overlay

```jsx
// absolute top-4 left-4 pointer-events-none font-mono text-[11px]
<p className="text-foreground/50">{visibleNodes} nodes / {visibleEdges} edges</p>
{filtered && <p className="text-white/25">filtered from {totalNodes}</p>}
{limitNotice && <p className="text-amber-400/70">{limitNotice}</p>}
{highlightedIds?.size > 0 && <p className="text-cyan-400/50">{highlightedIds.size} selected</p>}
```

Limit notice text: `"Showing 2,000 of 5,488 nodes. Use filters to narrow."` (shown when `data.total_nodes > data.nodes.length`).

---

## 6. Color & Data Model

### TypeScript Types

```ts
// Mirrors the server layout output
type GraphNode = {
  id: number          // SQLite rowid
  x: number           // pre-computed float
  y: number
  z: number
  label: string       // symbol kind: 'function' | 'class' | 'method' | 'interface' | 'type' | 'field'
  name: string        // symbol name (unqualified)
  file_path?: string
  size: number        // float; base by kind + degree boost
  color: string       // '#rrggbb' — server-assigned stellar color
}

type GraphEdge = {
  source: number      // node id
  target: number      // node id
  type: string        // Seam edge kind
}

type GraphData = {
  nodes: GraphNode[]
  edges: GraphEdge[]
  total_nodes: number    // true count in DB (may exceed nodes.length)
}
```

### Stellar Color Encoding (degree → color)

These are the **10-band thresholds from the C source** (not the 7-band legend in `colors.ts` — use these):

| Degree | Color | Classification |
|---|---|---|
| ≤ 1 | `#ff6050` | M red dwarf |
| ≤ 3 | `#ff8855` | late K |
| ≤ 5 | `#ffa060` | K orange |
| ≤ 8 | `#ffc070` | early K |
| ≤ 12 | `#ffe080` | G yellow (Sun-like) |
| ≤ 18 | `#fff0c0` | F yellow-white |
| ≤ 25 | `#fff8e8` | late A warm white |
| ≤ 35 | `#e8e8ff` | A white-blue |
| ≤ 50 | `#c0d0ff` | B blue-white |
| > 50 | `#80a0ff` | O blue giant |

Assign these in the layout endpoint in Python:
```python
def stellar_color(degree: int) -> str:
    thresholds = [(1,'#ff6050'),(3,'#ff8855'),(5,'#ffa060'),(8,'#ffc070'),
                  (12,'#ffe080'),(18,'#fff0c0'),(25,'#fff8e8'),(35,'#e8e8ff'),
                  (50,'#c0d0ff')]
    for d, c in thresholds:
        if degree <= d: return c
    return '#80a0ff'
```

### Node Size Formula

```python
SIZE_FOR_KIND = {'function': 4, 'method': 4, 'type': 4, 'field': 4,
                 'class': 6, 'interface': 6,
                 # Seam has no File/Module/Project labels — adjust as needed
                }

def node_size(kind: str, degree: int) -> float:
    base = SIZE_FOR_KIND.get(kind, 4)
    boost = min(degree * 0.3, 10.0) if degree > 5 else 0.0
    return base + boost
```

### Seam Edge Type → Color Map (9 kinds)

Proposed semantic palette staying in the teal-dark spirit but differentiated for Seam:

| Edge kind | Color | Rationale |
|---|---|---|
| `call` | `#1DA27E` | Seam primary seafoam teal |
| `import` | `#3b82f6` | blue (dependency) |
| `extends` | `#a855f7` | purple (inheritance) |
| `implements` | `#8b5cf6` | violet (interface contract) |
| `instantiates` | `#f97316` | orange (construction) |
| `holds` | `#06b6d4` | cyan (composition/storage) |
| `reads` | `#22c55e` | green (data read) |
| `writes` | `#ef4444` | red (data write — destructive) |
| `uses` | `#eab308` | yellow (parameter coupling) |

Default (unknown kind): `#1C8585`.

### Node Kind → Color Map (sidebar/tooltip only, not node sphere color)

```ts
const KIND_COLORS: Record<string, string> = {
  'class':     '#a855f7',   // purple
  'interface': '#8b5cf6',   // violet
  'function':  '#06b6d4',   // cyan
  'method':    '#1DA27E',   // teal
  'type':      '#f97316',   // orange
  'field':     '#64748b',   // slate
}
const DEFAULT_KIND_COLOR = '#94a3b8'
```

Node sphere color comes from the stellar degree scale (assigned server-side). Kind colors appear only in the detail panel badge (`colorForKind(kind) + '18'` for bg alpha, + `'bb'` for text alpha), the sidebar color dot, and the tooltip dot.

---

## 7. Aesthetic Spec

### graph-ui Reference Aesthetic (exact values)

**Background layers:**
- Body HTML: `#0a0a10` (cold blue-black, before React mounts)
- CSS `--color-background`: `#0a161a` (teal-shifted dark)
- Canvas 3D: `#06090f` (near-void, colder than the shell)
- Sidebar: `#0c1a20`
- Panels / header: `#0b1920`
- Cards / modals: `#0e2028`

**Primary brand:** `#1DA27E` (seafoam teal) — appears only at `/10–/25` opacity in chrome; full-opacity only on text and the 7px logo dot.

**Borders:** `#1a3a4030` (nearly transparent teal at ~19% alpha). Interactive surface hover: `bg-white/[0.04]`.

**Typography:** Inter everywhere; JetBrains Mono for file paths and code values. Size discipline: 9px metadata, 10px counts, 11px HUD, 12px tree labels, 14–15px headings, 18–22px big numbers. Section labels: `uppercase tracking-widest text-[9px]`.

**Glassmorphism:** `bg-[#0b1920]/80–95 backdrop-blur-md/xl`. Header uses `/80 backdrop-blur-md`; detail panels use `/95 backdrop-blur-xl`.

**Glow effects (3D):** Bloom `luminanceThreshold=0.3 intensity=1.2 radius=0.6 mipmapBlur=true`. Nodes boosted 1.2–2.0× above 1.0. Edges additive-blended at 0.06–0.50 intensity.

---

### Seam's OWN Distinct Aesthetic — "Code Intelligence Observatory"

Seam is a different instrument from codebase-memory-mcp — a local, read-only, single-project code intelligence tool, not a polyglot codebase explorer. Its look should feel like a scientific instrument: precise, dark, slightly clinical. Borrow the *techniques* but replace the *theme*.

**Backdrop:** `#08080e` — a deep ink-black with a slight violet undertone. Not teal. Not blue-black. The distinction signals "different tool."

**CSS tokens (Tailwind v3 config extension):**
```js
// Seam's own palette
background: '#08080e',      // ink-black (violet undertone, vs teal-black of graph-ui)
foreground: '#dde8e8',      // slightly cooler white
sidebar:    '#0c0c14',      // dark violet-black
card:       '#0f0f1a',      // slightly lifted
primary:    '#1DA27E',      // keep Seam's existing seafoam teal
accent:     '#7c3aed',      // violet-purple (vs teal accent) — used for cluster highlights
secondary:  '#12121e',
'muted-fg': '#6a7a8a',
border:     '#1a1a2e40',    // violet-dark at ~25% opacity
```

**3D canvas:** `#04040a` — even darker than the shell. The graph should feel like it's floating in deep space, not on a dark website.

**Node glow:** Keep the 10-band stellar palette exactly (it's physically motivated and color-blind-friendly enough). The RGB boost-above-1.0 → Bloom technique is kept verbatim.

**Edge palette:** The 9-kind semantic color map from section 6. The dominant edge type (`call`) is `#1DA27E` — same as the primary — so the call graph reads as "the main pulse" of the codebase.

**Cluster halos (Seam addition):** Render a subtle, large-radius sphere (radius = cluster spatial extent × 1.2, wireframe or transparent) around each cluster in a desaturated version of the cluster's dominant node color, opacity 0.03–0.05. This visually groups the stars into galaxies without overriding the individual node glow. This is a technique graph-ui does NOT do — it would be a Seam original.

**Panel chrome:** `bg-[#0c0c14]/90 backdrop-blur-xl`. Hover: `bg-white/[0.035]`. Active/selected: `bg-[accent]/10` (violet at 10% opacity) instead of teal. This differentiates Seam's selection color from its edge highlight color.

**Health/status dots:** Not needed — Seam has no multi-project health polling. Replace with an index-freshness indicator: green pulse when index is fresh, amber when stale (maps to `SEAM_STALENESS_CHECK`).

---

## 8. Concrete Seam Adaptation Plan

### New Dependencies

Add to `pyproject.toml [web]` extra:
```toml
# seam/server/web.py already has fastapi + uvicorn
numpy>=1.26        # layout force computation
scipy>=1.12        # cKDTree for approximate BH repulsion
```

Add to frontend (new `seam/_web/` build):
```
@react-three/fiber     ^8.x
@react-three/drei      ^9.x
@react-three/postprocessing  ^2.x
three                  ^0.168.x
```

Keep existing ReactFlow — the 3D view is a new tab, not a replacement.

### New Backend Endpoint

Add to `seam/server/web.py`:

```python
# GET /api/graph/layout?max_nodes=2000
@app.get("/api/graph/layout")
async def graph_layout(max_nodes: int = 2000) -> dict:
    ...
```

Implement layout logic in new module `seam/query/layout.py`:
- Query symbols from SQLite ordered by degree DESC, limit `max_nodes`
- Query edges where both endpoints are in the fetched node set
- BFS call-depth from `list_entry_points()` seeds
- FNV-1a ring positions (first 3 path components as cluster key)
- 40 iterations ForceAtlas2 with numpy vectorized forces + scipy cKDTree repulsion
- Cache result in module-level dict keyed by `(db_path, db_mtime, max_nodes)` with 60s TTL
- Return `{nodes: [...], edges: [...], total_nodes: <true count>}`

Degree computation in Python:
```python
# After fetching nodes and edges
from collections import Counter
degree = Counter()
for e in edges:
    degree[e['source']] += 1
    degree[e['target']] += 1
```

### Frontend Module Structure (new files in `seam/_web/src/`)

```
components/
  ConstellationScene.tsx   ← R3F Canvas, orbit, bloom, satellite-cluster halos (Seam original)
  NodeCloud.tsx            ← InstancedMesh (direct port from graph-ui)
  EdgeLines.tsx            ← LineSegments + AdditiveBlending (direct port, remap edge kinds)
  NodeLabels.tsx           ← Canvas sprite labels (direct port)
  NodeTooltip.tsx          ← Drei Html tooltip (direct port, use KIND_COLORS)
  ConstellationTab.tsx     ← State machine: selectedNode, highlightedIds, cameraTarget, filters
  FilterPanel.tsx          ← Node-kind + edge-kind toggles (port, map to Seam's 6+9 vocab)
  NodeDetailPanel.tsx      ← Symbol detail (port, populate from /api/symbol/{name})
  ResizeHandle.tsx         ← Direct port
hooks/
  useLayoutData.ts         ← fetch /api/graph/layout, constant GRAPH_RENDER_NODE_LIMIT = 2000
lib/
  colors.ts                ← KIND_COLORS, EDGE_TYPE_COLORS, stellar_color display legend
  types.ts                 ← GraphNode, GraphEdge, GraphData (as in section 6)
```

### Slotting into the Existing App Tab Shell

The existing `seam/server/web.py` FastAPI app already serves a SPA. Add a "Constellation" tab alongside the existing Seam Explorer views. The tab adds:

1. `<ConstellationTab>` renders the R3F Canvas and three-column shell
2. The tab is lazy-loaded (the R3F bundle is large; do not include it in the main chunk)
3. On tab mount, fire `useLayoutData` → `GET /api/graph/layout?max_nodes=2000`
4. Pass layout data to `<ConstellationScene>`

The existing `/api/status`, `/api/search`, `/api/symbol/{name}`, `/api/clusters`, `/api/graph/neighborhood` routes remain untouched. The detail panel in `ConstellationTab` calls `/api/symbol/{name}` to populate callers/callees/cluster — same data, new UI surface.

### What to Copy Verbatim

| Technique | Copy as-is? |
|---|---|
| `InstancedMesh` node cloud setup | Yes |
| Color boost 1.2–2.0× above 1.0 → Bloom | Yes |
| `AdditiveBlending + depthWrite=false` edges | Yes |
| Bloom params (threshold 0.3, intensity 1.2, radius 0.6, mipmapBlur) | Yes |
| Canvas sprite labels (no drei Html/Text) | Yes |
| `dpr={[1, 1.5]}` + `antialias=false` | Yes |
| `frustumCulled=false` on both mesh and sprites | Yes |
| OrbitControls params (dampingFactor 0.08 etc.) | Yes |
| Idle auto-rotate after 60s | Yes |
| Camera fly-to: ease-out cubic t*0.08 lerp | Yes |
| `computeCameraTarget`: centroid + `maxSpread*3` | Yes |
| ResizeHandle with `setPointerCapture` | Yes |
| Panel width `localStorage` persistence | Yes |
| FilterPanel raw-data counts (never filteredData) | Yes |
| HUD stats overlay (pointer-events-none absolute) | Yes |
| Label cap 80 sorted by size | Yes |
| Panel-to-panel navigation via `onNavigate` re-calling click handler | Yes |
| R3F native instanceId raycasting | Yes |
| `drei Html pointerEvents:none` tooltip | Yes |

### What to Skip or Replace

| Technique | Why skip / what to use instead |
|---|---|
| Satellite galaxy (LinkedProject cross-repo render) | Seam is single-project per server |
| Server JSON-RPC 2.0 transport | Seam uses plain FastAPI REST; add `/api/graph/layout` |
| C Barnes-Hut octree (layout3d.c) | Port algorithm to Python + numpy + scipy.spatial.cKDTree |
| `fetcDetail` with `center_node` (currently a stub in source) | Seam uses `/api/symbol/{name}` for detail data |
| `HealthDot` polling | Replace with index-freshness indicator from `staleness.py` |
| ControlTab (process manager, log viewer) | Not applicable to Seam's read-only constraint |
| StatsTab (multi-project cards) | Not applicable; Seam's stats come from `/api/status` |
| 21-kind edge color vocabulary | Replace with Seam's 9-kind semantic color map |
| LABEL_COLORS by structural label (Project/Package/File/Route) | Replace with KIND_COLORS by symbol kind |

### Level-of-Detail Strategy for 5,500 Nodes

Seam's count of ~5,488 nodes is above the graph-ui default cap (2,000) but well below its hard max (10,000). Recommended approach:

- **Default render limit:** `GRAPH_RENDER_NODE_LIMIT = 2000` — same as graph-ui. The layout endpoint returns nodes ordered by degree DESC so the most-connected (most important) symbols are always in view.
- **Filter to reduce count:** Each node-kind filter that is toggled off removes all symbols of that kind and their edges. `field` symbols alone can account for 20–30% of total count — toggling off `field` alone may bring the visible set under 2,000.
- **No client-side LOD:** Do not implement separate LOD geometry (low-poly spheres for far nodes). The single-draw-call InstancedMesh already handles 5,500 nodes at acceptable frame rates. Add LOD only if profiling shows frame time > 8ms.
- **Label cap:** Keep at 80, sorted by degree (mapping to `node.size`). At 5,500 nodes, rendering 5,500 canvas textures would saturate GPU texture memory.

### Zero-Network / Read-Only Constraints

- All layout computation runs in the FastAPI process (same machine as the SQLite DB). No external API calls.
- The layout algorithm uses only data already in the SQLite `symbols` and `edges` tables.
- `numpy` and `scipy` are pure-Python/C extensions with no network calls.
- The cache in `seam/query/layout.py` is process-local (dict). If the Seam server restarts, the layout is recomputed on the next request — this is correct behavior.
- `SEAM_STALENESS_CHECK` applies: if the index is stale, the layout endpoint should attach an `index_status` banner (same contract as the 5 graph-traversal tools) and serve the cached layout (stale positions are better than no positions).

---

## 9. Open Design Questions

A human must decide these before implementation begins.

**Q1: Renderer choice — R3F 3D or ReactFlow 2D upgraded?**
The findings assume a new R3F tab. The alternative is upgrading the existing ReactFlow canvas with custom SVG node rendering, CSS drop-shadow glow, and a client-side force layout. R3F is superior for visual quality (true WebGL bloom, additive edge blending). ReactFlow is superior for integration continuity (no new 3D dependency bundle, same interaction model). If Seam ships the 3D tab as a genuinely complementary view (precision-label-clicking in 2D, galaxy-overview in 3D), both have clear roles. If the goal is a single best graph view, make the call now.

**Q2: Server-side layout precomputed vs. client-side force layout?**
The modeling reference argues for server-side. The alternative: use a client-side library like `d3-force` or `graphology-layout-forceatlas2` (WASM). Client-side layout means zero new backend code and the layout adapts to the filtered node set in real time. The cost: 2–5 second layout convergence on the main thread (or a Web Worker). Server-side is one network request + cached. Decision depends on whether Seam's Explorer users will frequently filter and expect the layout to re-stabilize, or whether a stable precomputed layout is preferred.

**Q3: Cluster visual treatment — halos, sub-galaxies, or flat starfield?**
Three options:
- **Flat starfield:** All nodes in one ring layout, clusters co-located by FNV-1a hash of first-3-path-components. No visual cluster boundary. Simple, works immediately.
- **Cluster halos:** Transparent sphere overlays around each cluster centroid (the Seam-original idea from section 7). Visually groups stars into galaxies. Requires computing cluster centroids post-layout.
- **Sub-galaxy layout:** Assign each cluster to a distinct ring in 3D space, then use ForceAtlas2 within each ring. More structure, but requires cluster labels to be computed before layout (they are available in Seam's `clusters` table).

The flat starfield is the MVP. Cluster halos are a Phase 2 polish item.

**Q4: What is the seed/cap strategy when `total_nodes > GRAPH_RENDER_NODE_LIMIT`?**
graph-ui takes the first `max_nodes` from the store (arbitrary order). The seam_notes recommend degree DESC. A third option: prioritize nodes in the currently-viewed cluster (from `/api/clusters`). The choice determines which symbols are invisible by default — this has usability implications for codebase navigation.

**Q5: How does the 3D tab stay in sync with the 2D ReactFlow precision tab?**
If a user selects a node in the 2D tab, should the 3D tab fly to it? If the 3D tab highlights a node, should the 2D tab scroll to it? Two options: (a) independent state (tabs are fully decoupled — simpler), (b) shared selected-symbol state in a React context or URL param. The URL param approach (`?symbol=ClassName.method`) is compatible with Seam's zero-network constraint and enables deep-linking from the CLI.

**Q6: Should the layout endpoint expose `max_nodes` as a user control in the HUD?**
graph-ui has the HUD limit notice but no slider. Adding a `max_nodes` selector (e.g. 500 / 1000 / 2000 / 5000) in the HUD would let users trade visual density for completeness. The layout would need to be recomputed or cached per value. This is a UX feature decision, not a technical one.

**Q7: Edge rendering at full 28,649-edge scale?**
At 2,000 nodes with degree-ordered sampling, the visible edge count will be a subset of 28,649. But if filters are set to show `call` edges only with max_nodes=2000, edge count could still be in the tens of thousands. The `LineSegments` geometry at 28,649 edges is a 28,649 × 6 = 171,894-float `Float32Array` per position and color array. This is ~1.4 MB of typed arrays rebuilt on every `highlightedIds` change. Acceptable once; unacceptable at 60fps. Confirm that geometry rebuilds only on state changes (not on `useFrame`) — the current architecture does this correctly via `useMemo`.

**Q8: Node label strategy when the symbol name is qualified?**
Seam symbol names are qualified: `ClassName.method_name`. graph-ui uses `node.name` directly. For Seam, the best label is the bare name (`method_name`) with the container shown on hover. This means `NodeLabels.tsx` should strip the container prefix from the displayed text but keep it in the sprite's tooltip data. Decide before implementing the canvas sprite drawing code.