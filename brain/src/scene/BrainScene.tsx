"use client";
import * as THREE from "three";
import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Stars, Text, Billboard, Sparkles, Grid } from "@react-three/drei";
import { EffectComposer, Bloom, Vignette, Noise, ChromaticAberration } from "@react-three/postprocessing";
import { KIND_COLORS, FIRE_COLORS, type GraphNode } from "../contract";
import { engine, useStore } from "../store";
import { LiveEventSource } from "../eventSource/live";
import { computeLayout } from "../layout3d";

const SIZE: Record<string, number> = {
  model: 1.6, layer: 1.2, agent: 1.0, service: 0.82, tool: 0.64, datastore: 0.95, connector: 1.15,
};
const EDGE_SEGS = 12;
const boot = { t: 0 };

// live connection now runs at DOM level via useLive() in app/brain/page.tsx

const tmpO = new THREE.Object3D();
const tmpC = new THREE.Color();
const vA = new THREE.Vector3(), vB = new THREE.Vector3(), vMid = new THREE.Vector3();
const Q = new THREE.Quaternion();
const UP = new THREE.Vector3(0, 1, 0);
const bootK = () => Math.min(1, (performance.now() - boot.t) / 1500);

// ---------- nodes + coronas ----------
function NodeInstances({ nodes }: { nodes: GraphNode[] }) {
  const positions = useStore((s) => s.positions);
  const select = useStore((s) => s.select);
  const setHover = useStore((s) => s.setHover);
  const hidden = useStore((s) => s.groupHidden);
  const sphereRef = useRef<THREE.InstancedMesh>(null!);
  const boxRef = useRef<THREE.InstancedMesh>(null!);
  const haloRef = useRef<THREE.InstancedMesh>(null!);
  const shellRef = useRef<THREE.InstancedMesh>(null!);

  const spheres = useMemo(() => nodes.filter((n) => n.kind !== "datastore"), [nodes]);
  const boxes = useMemo(() => nodes.filter((n) => n.kind === "datastore"), [nodes]);
  const all = useMemo(() => [...spheres, ...boxes], [spheres, boxes]);

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const bk = bootK();
    const draw = (ref: THREE.InstancedMesh, list: GraphNode[]) => {
      if (!ref) return;
      for (let i = 0; i < list.length; i++) {
        const n = list[i];
        const p = positions[n.id] || [0, 0, 0];
        const act = (engine.activation[n.id] = (engine.activation[n.id] || 0) * 0.92);
        const breathe = 1 + 0.05 * Math.sin(t * 1.3 + i);
        // activation-driven pulse: a sharp shimmer envelope that fires when the
        // node lights up and quickly settles -> cinematic "thinking" heartbeat
        const pulse = act * (0.55 + 0.45 * Math.sin(t * 9 + i * 1.7));
        const intro = Math.max(0, Math.min(1, (bk * list.length - i) / 6)); // staggered boot
        const vis = hidden[n.group || ""] ? 0 : 1;
        const s = (SIZE[n.kind] || 0.8) * breathe * (1 + act * 0.85 + pulse * 0.5) * intro * vis;
        tmpO.position.set(p[0], p[1], p[2]);
        tmpO.scale.setScalar(s);
        tmpO.rotation.set(0, t * 0.2 + i, 0);
        tmpO.updateMatrix();
        ref.setMatrixAt(i, tmpO.matrix);
        const off = n.kind === "connector" && (n.meta as any)?.status === "offline";
        ref.setColorAt(i, tmpC.set(off ? "#ef4444" : KIND_COLORS[n.kind])
          .multiplyScalar(0.45 + act * 2.6 + 0.12 * Math.sin(t * 6 + i)));
      }
      ref.instanceMatrix.needsUpdate = true;
      if (ref.instanceColor) ref.instanceColor.needsUpdate = true;
    };
    draw(sphereRef.current, spheres);
    draw(boxRef.current, boxes);
    // additive coronas (halo grows with activation)
    const halo = haloRef.current;
    if (halo) {
      for (let i = 0; i < all.length; i++) {
        const n = all[i];
        const p = positions[n.id] || [0, 0, 0];
        const act = engine.activation[n.id] || 0;
        const s = (SIZE[n.kind] || 0.8) * (1.7 + act * 2.4) * bootK() * (hidden[n.group || ""] ? 0 : 1);
        tmpO.position.set(p[0], p[1], p[2]);
        tmpO.scale.setScalar(s);
        tmpO.updateMatrix();
        halo.setMatrixAt(i, tmpO.matrix);
        halo.setColorAt(i, tmpC.set(KIND_COLORS[n.kind]).multiplyScalar(0.05 + act * 0.5));
      }
      halo.instanceMatrix.needsUpdate = true;
      if (halo.instanceColor) halo.instanceColor.needsUpdate = true;
    }
    // holographic wireframe shells (Stark): counter-rotating lattice over each
    // node, brightening with activation
    const shell = shellRef.current;
    if (shell) {
      for (let i = 0; i < all.length; i++) {
        const n = all[i];
        const p = positions[n.id] || [0, 0, 0];
        const act = engine.activation[n.id] || 0;
        const vis = hidden[n.group || ""] ? 0 : 1;
        const s = (SIZE[n.kind] || 0.8) * (1.22 + 0.1 * Math.sin(t * 2 + i) + act * 0.7) * bk * vis;
        tmpO.position.set(p[0], p[1], p[2]);
        tmpO.scale.setScalar(s);
        tmpO.rotation.set(-t * 0.3 + i, t * 0.18, t * 0.12);
        tmpO.updateMatrix();
        shell.setMatrixAt(i, tmpO.matrix);
        shell.setColorAt(i, tmpC.set(KIND_COLORS[n.kind]).multiplyScalar(0.22 + act * 1.7));
      }
      shell.instanceMatrix.needsUpdate = true;
      if (shell.instanceColor) shell.instanceColor.needsUpdate = true;
    }
  });

  const pick = (list: GraphNode[]) => (e: any) => { e.stopPropagation(); const n = list[e.instanceId]; if (n) select(n.id); };
  const hov = (list: GraphNode[]) => (e: any) => { e.stopPropagation(); const n = list[e.instanceId]; if (n) setHover(n.id); };

  return (
    <group>
      <instancedMesh ref={haloRef} args={[undefined as any, undefined as any, all.length]} frustumCulled={false}>
        <sphereGeometry args={[1, 16, 16]} />
        <meshBasicMaterial transparent opacity={0.55} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
      </instancedMesh>
      <instancedMesh ref={shellRef} args={[undefined as any, undefined as any, all.length]} frustumCulled={false}>
        <icosahedronGeometry args={[1, 1]} />
        <meshBasicMaterial wireframe transparent opacity={0.6} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
      </instancedMesh>
      <instancedMesh ref={sphereRef} args={[undefined as any, undefined as any, spheres.length]}
        onClick={pick(spheres)} onPointerOver={hov(spheres)} onPointerOut={() => setHover(null)}>
        <icosahedronGeometry args={[1, 2]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>
      <instancedMesh ref={boxRef} args={[undefined as any, undefined as any, boxes.length]}
        onClick={pick(boxes)} onPointerOver={hov(boxes)} onPointerOut={() => setHover(null)}>
        <octahedronGeometry args={[1.25, 0]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>
    </group>
  );
}

// ---------- curved dendrite edges that heat up ----------
function Edges() {
  const edges = useStore((s) => s.edges);
  const positions = useStore((s) => s.positions);
  const byId = useStore((s) => s.byId);
  const hidden = useStore((s) => s.groupHidden);

  const { geom, keys, egroups } = useMemo(() => {
    const n = edges.length;
    const pos = new Float32Array(n * (EDGE_SEGS) * 2 * 3);
    const col = new Float32Array(n * (EDGE_SEGS) * 2 * 3);
    const keys: string[] = [];
    const egroups: [string, string][] = [];
    let o = 0;
    edges.forEach((e) => {
      const a = positions[e.source] || [0, 0, 0];
      const b = positions[e.target] || [0, 0, 0];
      vA.set(a[0], a[1], a[2]); vB.set(b[0], b[1], b[2]);
      vMid.addVectors(vA, vB).multiplyScalar(0.5);
      const bow = vMid.length() * 0.16 + 1.2;
      const ctrl = new THREE.Vector3().addVectors(vA, vB).multiplyScalar(0.5)
        .addScaledVector(new THREE.Vector3().addVectors(vA, vB).multiplyScalar(0.5).normalize(), bow);
      const pts: THREE.Vector3[] = [];
      for (let s = 0; s <= EDGE_SEGS; s++) {
        const u = s / EDGE_SEGS;
        const x = (1 - u) * (1 - u) * vA.x + 2 * (1 - u) * u * ctrl.x + u * u * vB.x;
        const y = (1 - u) * (1 - u) * vA.y + 2 * (1 - u) * u * ctrl.y + u * u * vB.y;
        const z = (1 - u) * (1 - u) * vA.z + 2 * (1 - u) * u * ctrl.z + u * u * vB.z;
        pts.push(new THREE.Vector3(x, y, z));
      }
      for (let s = 0; s < EDGE_SEGS; s++) {
        pos.set([pts[s].x, pts[s].y, pts[s].z, pts[s + 1].x, pts[s + 1].y, pts[s + 1].z], o * 6);
        o++;
      }
      keys.push(`${e.source}->${e.target}`);
      egroups.push([byId[e.source]?.group || "", byId[e.target]?.group || ""]);
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    return { geom: g, keys, egroups };
  }, [edges, positions, byId]);

  useFrame(() => {
    const col = geom.getAttribute("color") as THREE.BufferAttribute;
    const bk = bootK();
    for (let i = 0; i < keys.length; i++) {
      const h = (engine.edgeHeat[keys[i]] = (engine.edgeHeat[keys[i]] || 0) * 0.9);
      const base = 0.05 * bk;
      const eg = egroups[i];
      const vis = eg && (hidden[eg[0]] || hidden[eg[1]]) ? 0 : 1;
      const r = (0.12 + h * 0.9) * (base + h) * 4 * vis;
      const g = (0.45 + h * 0.7) * (base + h) * 4 * vis;
      const b = (0.85 + h * 0.4) * (base + h) * 4 * vis;
      const start = i * EDGE_SEGS * 2;
      for (let v = 0; v < EDGE_SEGS * 2; v++) col.setXYZ(start + v, r, g, b);
    }
    col.needsUpdate = true;
  });

  return (
    <lineSegments geometry={geom} frustumCulled={false}>
      <lineBasicMaterial vertexColors transparent opacity={0.9} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
    </lineSegments>
  );
}
const tmpC2 = new THREE.Vector3();

// ---------- comet synapses ----------
function Pulses() {
  const positions = useStore((s) => s.positions);
  const ref = useRef<THREE.InstancedMesh>(null!);
  const MAX = engine.MAX_PULSES;
  useFrame(() => {
    const mesh = ref.current; if (!mesh) return;
    const now = performance.now();
    const ps = engine.pulses;
    for (let i = ps.length - 1; i >= 0; i--) {
      if ((now - ps[i].start) / ps[i].dur >= 1) {
        engine.activation[ps[i].to] = 1;
        ps[i] = ps[ps.length - 1]; ps.pop();
      }
    }
    let i = 0;
    for (; i < ps.length && i < MAX; i++) {
      const p = ps[i];
      const a = positions[p.from] || [0, 0, 0];
      const b = positions[p.to] || [0, 0, 0];
      vA.set(a[0], a[1], a[2]); vB.set(b[0], b[1], b[2]);
      const t = Math.min(1, (now - p.start) / p.dur);
      const ease = t * t * (3 - 2 * t);
      vMid.lerpVectors(vA, vB, ease);
      const dir = tmpC2.subVectors(vB, vA).normalize();
      Q.setFromUnitVectors(UP, dir);
      const head = Math.sin(t * Math.PI);
      tmpO.position.copy(vMid);
      tmpO.quaternion.copy(Q);
      tmpO.scale.set(0.22 + head * 0.18, 1.1 + head * 1.6, 0.22 + head * 0.18); // comet streak
      tmpO.updateMatrix();
      mesh.setMatrixAt(i, tmpO.matrix);
      mesh.setColorAt(i, tmpC.set(p.status === "error" ? FIRE_COLORS.error : FIRE_COLORS[p.kind]).multiplyScalar(2.6));
    }
    for (; i < MAX; i++) { tmpO.position.set(99999, 0, 0); tmpO.scale.setScalar(0.0001); tmpO.updateMatrix(); mesh.setMatrixAt(i, tmpO.matrix); }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  });
  return (
    <instancedMesh ref={ref} args={[undefined as any, undefined as any, MAX]} frustumCulled={false}>
      <sphereGeometry args={[1, 8, 8]} />
      <meshBasicMaterial toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} />
    </instancedMesh>
  );
}

// ---------- co-activation edges (nodes firing together light up a link) ----------
function CoActivation({ nodes }: { nodes: GraphNode[] }) {
  const positions = useStore((s) => s.positions);
  const hidden = useStore((s) => s.groupHidden);
  const MAX = 28;
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(MAX * 2 * 3), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(MAX * 2 * 3), 3));
    return g;
  }, []);
  useFrame(() => {
    const pos = geom.getAttribute("position") as THREE.BufferAttribute;
    const col = geom.getAttribute("color") as THREE.BufferAttribute;
    const act: { id: string; a: number }[] = [];
    for (const n of nodes) {
      if (hidden[n.group || ""]) continue;
      const a = engine.activation[n.id] || 0;
      if (a > 0.22) act.push({ id: n.id, a });
    }
    act.sort((x, y) => y.a - x.a);
    const top = act.slice(0, 8);
    let e = 0;
    for (let i = 0; i < top.length && e < MAX; i++) {
      for (let j = i + 1; j < top.length && e < MAX; j++) {
        const pa = positions[top[i].id], pb = positions[top[j].id];
        if (!pa || !pb) continue;
        const co = Math.min(top[i].a, top[j].a);           // joint co-activation
        pos.setXYZ(e * 2, pa[0], pa[1], pa[2]); pos.setXYZ(e * 2 + 1, pb[0], pb[1], pb[2]);
        const r = co * 2.0, g = co * 1.35, b = co * 0.3;   // amber, like mesh temporal edges
        col.setXYZ(e * 2, r, g, b); col.setXYZ(e * 2 + 1, r, g, b);
        e++;
      }
    }
    for (; e < MAX; e++) {
      pos.setXYZ(e * 2, 0, 0, 0); pos.setXYZ(e * 2 + 1, 0, 0, 0);
      col.setXYZ(e * 2, 0, 0, 0); col.setXYZ(e * 2 + 1, 0, 0, 0);
    }
    pos.needsUpdate = true; col.needsUpdate = true;
  });
  return (
    <lineSegments geometry={geom} frustumCulled={false}>
      <lineBasicMaterial vertexColors transparent opacity={0.7} blending={THREE.AdditiveBlending}
        depthWrite={false} toneMapped={false} />
    </lineSegments>
  );
}

// ---------- labels (important nodes + hovered/selected) ----------
function Labels({ nodes }: { nodes: GraphNode[] }) {
  const positions = useStore((s) => s.positions);
  const hovered = useStore((s) => s.hovered);
  const selected = useStore((s) => s.selected);
  const hidden = useStore((s) => s.groupHidden);
  const show = useMemo(() => {
    const ids = new Set(nodes.filter((n) => n.kind === "model" || n.kind === "layer" || n.kind === "connector").map((n) => n.id));
    if (hovered) ids.add(hovered);
    if (selected) ids.add(selected);
    return nodes.filter((n) => ids.has(n.id) && (!hidden[n.group || ""] || n.id === selected));
  }, [nodes, hovered, selected, hidden]);
  return (
    <>
      {show.map((n) => {
        const p = positions[n.id] || [0, 0, 0];
        const big = n.id === hovered || n.id === selected;
        return (
          <Billboard key={n.id} position={[p[0], p[1] + (SIZE[n.kind] || 1) + 0.9, p[2]]}>
            <Text fontSize={big ? 0.95 : 0.62} color={big ? "#ffffff" : KIND_COLORS[n.kind]}
              anchorX="center" anchorY="middle" outlineWidth={0.04} outlineColor="#04060c"
              maxWidth={14} fillOpacity={big ? 1 : 0.8}>
              {n.label}
            </Text>
          </Billboard>
        );
      })}
    </>
  );
}

// ---------- selection + hover rings ----------
function Rings() {
  const positions = useStore((s) => s.positions);
  const selected = useStore((s) => s.selected);
  const hovered = useStore((s) => s.hovered);
  const byId = useStore((s) => s.byId);
  const selRef = useRef<THREE.Mesh>(null!);
  const hovRef = useRef<THREE.Mesh>(null!);
  useFrame((st) => {
    const t = st.clock.elapsedTime;
    const place = (ref: THREE.Mesh, id: string | null, spin: number) => {
      if (!ref) return;
      if (id && positions[id]) {
        const p = positions[id]; const r = (SIZE[byId[id]?.kind || "agent"] || 1) + 0.7;
        ref.visible = true;
        ref.position.set(p[0], p[1], p[2]);
        ref.scale.setScalar(r);
        ref.rotation.set(Math.PI / 2.4, 0, t * spin);
      } else ref.visible = false;
    };
    place(selRef.current, selected, 0.9);
    place(hovRef.current, hovered && hovered !== selected ? hovered : null, -1.4);
  });
  return (
    <>
      <mesh ref={selRef} visible={false}>
        <torusGeometry args={[1, 0.045, 8, 48]} />
        <meshBasicMaterial color="#5cc8ff" toneMapped={false} transparent opacity={0.95} />
      </mesh>
      <mesh ref={hovRef} visible={false}>
        <torusGeometry args={[1, 0.03, 8, 40]} />
        <meshBasicMaterial color="#ffffff" toneMapped={false} transparent opacity={0.5} />
      </mesh>
    </>
  );
}

function FocusRig({ controls }: { controls: React.MutableRefObject<any> }) {
  const selected = useStore((s) => s.selected);
  const positions = useStore((s) => s.positions);
  const want = useRef<THREE.Vector3 | null>(null);
  useEffect(() => {
    if (selected && positions[selected]) {
      const p = positions[selected];
      want.current = new THREE.Vector3(p[0], p[1], p[2]);
    }
  }, [selected, positions]);
  useFrame(() => {
    if (want.current && controls.current) {
      controls.current.target.lerp(want.current, 0.07);
      controls.current.update();
    }
  });
  return null;
}

// ---------- central energy core: a pulsing collective-mind reactor ----------
function _avgActivation(): number {
  let tot = 0, n = 0;
  for (const k in engine.activation) { tot += engine.activation[k] || 0; n++; }
  return n ? tot / n : 0;
}

function EnergyCore() {
  const core = useRef<THREE.Mesh>(null!);
  const r1 = useRef<THREE.Mesh>(null!);
  const r2 = useRef<THREE.Mesh>(null!);
  const r3 = useRef<THREE.Mesh>(null!);
  useFrame((st) => {
    const t = st.clock.elapsedTime;
    const act = _avgActivation();
    const bk = bootK();
    const pulse = (1 + 0.12 * Math.sin(t * 2.0) + act * 1.4) * bk;
    if (core.current) {
      core.current.scale.setScalar(2.0 * pulse);
      core.current.rotation.set(t * 0.12, t * 0.17, 0);
      (core.current.material as THREE.MeshBasicMaterial).opacity = (0.08 + act * 0.3) * bk;
    }
    if (r1.current) { r1.current.rotation.set(Math.PI / 2, t * 0.45, 0); r1.current.scale.setScalar((4.0 + act * 1.6) * bk); }
    if (r2.current) { r2.current.rotation.set(t * 0.33, Math.PI / 3, t * 0.2); r2.current.scale.setScalar((5.4 + act * 2.2) * bk); }
    if (r3.current) { r3.current.rotation.set(t * 0.2, -t * 0.25, Math.PI / 5); r3.current.scale.setScalar((6.8 + act * 2.6) * bk); }
  });
  return (
    <group>
      <mesh ref={core}>
        <icosahedronGeometry args={[1, 1]} />
        <meshBasicMaterial color="#36e2ff" wireframe transparent opacity={0.12}
          blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh ref={r1}><torusGeometry args={[1, 0.012, 8, 90]} />
        <meshBasicMaterial color="#5cc8ff" transparent opacity={0.42} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} /></mesh>
      <mesh ref={r2}><torusGeometry args={[1, 0.009, 8, 90]} />
        <meshBasicMaterial color="#a06bff" transparent opacity={0.32} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} /></mesh>
      <mesh ref={r3}><torusGeometry args={[1, 0.006, 8, 90]} />
        <meshBasicMaterial color="#36e2ff" transparent opacity={0.22} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} /></mesh>
    </group>
  );
}

// ---------- expanding shockwave rings on the floor grid ----------
function Shockwaves() {
  const N = 3;
  const refs = useRef<THREE.Mesh[]>([]);
  useFrame((st) => {
    const t = st.clock.elapsedTime;
    const act = _avgActivation();
    for (let i = 0; i < N; i++) {
      const m = refs.current[i]; if (!m) continue;
      const phase = ((t * (0.12 + act * 0.25) + i / N) % 1);
      const s = 2 + phase * 64;
      m.scale.set(s, s, s);
      (m.material as THREE.MeshBasicMaterial).opacity = (0.22 + act * 0.3) * (1 - phase);
    }
  });
  return (
    <group position={[0, -33.6, 0]} rotation={[Math.PI / 2, 0, 0]}>
      {Array.from({ length: N }).map((_, i) => (
        <mesh key={i} ref={(el) => { if (el) refs.current[i] = el as THREE.Mesh; }}>
          <ringGeometry args={[0.985, 1, 80]} />
          <meshBasicMaterial color="#1c6f9c" transparent opacity={0.2} side={THREE.DoubleSide}
            blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
        </mesh>
      ))}
    </group>
  );
}

// ---------- selection burst: a quick expanding ring when a node is picked ----------
function SelectionBurst() {
  const selected = useStore((s) => s.selected);
  const positions = useStore((s) => s.positions);
  const ref = useRef<THREE.Mesh>(null!);
  const start = useRef(0);
  const pos = useRef<number[] | null>(null);
  useEffect(() => {
    if (selected && positions[selected]) { pos.current = positions[selected]; start.current = performance.now(); }
  }, [selected, positions]);
  useFrame((st) => {
    const m = ref.current; if (!m) return;
    if (!pos.current) { m.visible = false; return; }
    const k = (performance.now() - start.current) / 750;
    if (k >= 1) { m.visible = false; return; }
    m.visible = true;
    m.position.set(pos.current[0], pos.current[1], pos.current[2]);
    m.scale.setScalar(0.5 + k * 7);
    m.rotation.set(Math.PI / 2.4, 0, st.clock.elapsedTime);
    (m.material as THREE.MeshBasicMaterial).opacity = 0.7 * (1 - k);
  });
  return (
    <mesh ref={ref} visible={false}>
      <ringGeometry args={[0.88, 1, 56]} />
      <meshBasicMaterial color="#9fe6ff" transparent opacity={0.7} side={THREE.DoubleSide}
        blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
    </mesh>
  );
}

function Scene() {
  const nodes = useStore((s) => s.nodes);
  const autoRotate = useStore((s) => s.autoRotate);
  const controls = useRef<any>(null);
  useEffect(() => { if (nodes.length > 0 && boot.t === 0) boot.t = performance.now(); }, [nodes.length]);
  return (
    <>
      <color attach="background" args={["#04060c"]} />
      <fog attach="fog" args={["#04060c", 60, 170]} />
      <ambientLight intensity={0.5} />
      <Stars radius={120} depth={60} count={2600} factor={3.2} saturation={0} fade speed={0.6} />
      <Sparkles count={140} scale={[110, 70, 110]} size={2.2} speed={0.22} opacity={0.45} color="#7fd4ff" noise={1.5} />
      <Sparkles count={70} scale={[60, 40, 60]} size={3.6} speed={0.5} opacity={0.3} color="#a06bff" noise={2.5} />
      <Grid position={[0, -34, 0]} infiniteGrid cellSize={3} cellThickness={0.5}
        sectionSize={15} sectionThickness={1} cellColor="#0e2a3a" sectionColor="#1c6f9c"
        fadeDistance={170} fadeStrength={4} />
      <Shockwaves />
      {nodes.length > 0 && (
        <>
          <EnergyCore />
          <Edges />
          <CoActivation nodes={nodes} />
          <SelectionBurst />
          <NodeInstances nodes={nodes} />
          <Pulses />
          <Rings />
          <Labels nodes={nodes} />
        </>
      )}
      <OrbitControls ref={controls} autoRotate={autoRotate} autoRotateSpeed={0.32}
        enableDamping dampingFactor={0.07} maxDistance={160} minDistance={6} />
      <FocusRig controls={controls} />
      <EffectComposer>
        <Bloom intensity={1.7} luminanceThreshold={0.18} luminanceSmoothing={0.9} mipmapBlur radius={0.9} />
        <ChromaticAberration offset={[0.0006, 0.0009]} radialModulation modulationOffset={0.35} />
        <Vignette eskil={false} offset={0.22} darkness={1.0} />
        <Noise opacity={0.025} />
      </EffectComposer>
    </>
  );
}

export default function BrainScene() {
  return (
    <Canvas camera={{ position: [0, 8, 64], fov: 52, near: 0.1, far: 600 }}
      gl={{ antialias: true, powerPreference: "high-performance" }} dpr={[1, 1.8]}
      style={{ position: "absolute", inset: 0 }}
      onPointerMissed={() => useStore.getState().select(null)}>
      <Scene />
    </Canvas>
  );
}
