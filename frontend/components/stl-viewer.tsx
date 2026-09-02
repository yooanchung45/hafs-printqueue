"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

export interface ModelTransform {
  scale: number;
  rotationX: number;
  rotationY: number;
  rotationZ: number;
}

const DEFAULT_TRANSFORM: ModelTransform = { scale: 1, rotationX: 0, rotationY: 0, rotationZ: 0 };

// Parsed, normal-resolved, bed-centred geometry keyed by source URL. STL files
// are immutable and the viewer never mutates the geometry (scale/rotation live
// on the mesh), so entries are shared across mounts and file-tab switches
// instead of refetching + reparsing every time. The cache owns these — they are
// disposed on eviction, never on viewer unmount.
const geometryCache = new Map<string, THREE.BufferGeometry>();
const GEOMETRY_CACHE_LIMIT = 8;

function cacheGeometry(url: string, geometry: THREE.BufferGeometry) {
  geometryCache.set(url, geometry);
  while (geometryCache.size > GEOMETRY_CACHE_LIMIT) {
    const oldest = geometryCache.keys().next().value as string | undefined;
    if (oldest === undefined || oldest === url) break;
    geometryCache.get(oldest)?.dispose();
    geometryCache.delete(oldest);
  }
}

// STLLoader copies the per-facet normals straight from the file for both binary
// and ASCII STL, so most models already have usable normals and the extra
// computeVertexNormals() pass (which also smooths away the faceting a print
// preview should show) is pure cost. Only recompute when the file left them
// zeroed. Sampling the first slice is enough — STL normals are per triangle.
function hasUsableNormals(geometry: THREE.BufferGeometry): boolean {
  const normal = geometry.getAttribute("normal");
  if (!normal) return false;
  const array = normal.array as ArrayLike<number>;
  const sample = Math.min(array.length, 1200);
  for (let i = 0; i < sample; i += 1) {
    if (array[i] !== 0) return true;
  }
  return false;
}

// Bed axes as the printer sees them (X/Y horizontal, Z = height) — not
// three.js's own axis naming. The scene is Y-up for rendering (see the
// rotationX - 90 below), so printer-Z maps to scene +Y, printer-Y maps to
// scene +Z, printer-X maps to scene +X.
const AXIS_SPECS: { dir: [number, number, number]; color: string; label: string }[] = [
  { dir: [1, 0, 0], color: "#ef4444", label: "X" },
  { dir: [0, 0, 1], color: "#22c55e", label: "Y" },
  { dir: [0, 1, 0], color: "#3b82f6", label: "Z" },
];

function makeAxisLabel(text: string, color: string, size: number): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.font = "bold 44px sans-serif";
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 32, 34);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true }));
  sprite.scale.set(size, size, 1);
  return sprite;
}

function buildAxisIndicators(length: number): THREE.Group {
  const group = new THREE.Group();
  const labelGap = Math.max(length * 0.1, 8);
  for (const { dir, color, label } of AXIS_SPECS) {
    const end = new THREE.Vector3(dir[0] * length, dir[1] * length, dir[2] * length);
    const start = new THREE.Vector3(0, 0, 0);
    if (dir[1] === 0) { start.y = 0.3; end.y = 0.3; } // lift horizontal axes just off the grid
    const geometry = new THREE.BufferGeometry().setFromPoints([start, end]);
    const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color }));
    group.add(line);
    const sprite = makeAxisLabel(label, color, Math.max(length * 0.13, 10));
    sprite.position.copy(end).addScaledVector(new THREE.Vector3(...dir), labelGap);
    group.add(sprite);
  }
  return group;
}

function disposeGroup(group: THREE.Group) {
  group.traverse((object) => {
    if (object instanceof THREE.Line) {
      object.geometry.dispose();
      (object.material as THREE.Material).dispose();
    } else if (object instanceof THREE.Sprite) {
      const material = object.material as THREE.SpriteMaterial;
      material.map?.dispose();
      material.dispose();
    }
  });
}

/** DB colour_hex sometimes carries an alpha pair; THREE.Color.set only takes
 * #rgb / #rrggbb. Returns null for an empty / unusable value. */
function toColorHex(hex: string | undefined | null): string | null {
  if (!hex) return null;
  const s = hex.trim();
  if (!s) return null;
  return s.length === 9 ? s.slice(0, 7) : s;
}

const _boxVertex = new THREE.Vector3();

/** Tight AABB of a mesh's vertices under its own local matrix (rotation + scale,
 * position zeroed). Vertex-accurate, unlike boundingBox.applyMatrix4 which
 * returns the AABB of the *rotated* AABB — far looser, and what left rotated
 * parts hovering above the bed. Sampled on huge meshes so the rotation dial
 * stays smooth; a skipped extreme vertex shifts the drop by <1mm. */
function tightLocalBox(mesh: THREE.Mesh): THREE.Box3 {
  const position = mesh.geometry.getAttribute("position");
  const box = new THREE.Box3();
  const stride = position.count > 60000 ? Math.ceil(position.count / 60000) : 1;
  for (let i = 0; i < position.count; i += stride) {
    _boxVertex.fromBufferAttribute(position, i).applyMatrix4(mesh.matrix);
    box.expandByPoint(_boxVertex);
  }
  return box;
}

/** Rotate + scale the already-loaded model in place, then re-seat it on the
 * bed (rotating changes which point is lowest). Doesn't touch the camera
 * or controls, so the current view is preserved. */
function applyTransform(model: THREE.Mesh, transform: ModelTransform) {
  model.scale.setScalar(transform.scale);
  model.rotation.set(
    THREE.MathUtils.degToRad(transform.rotationX - 90),
    THREE.MathUtils.degToRad(transform.rotationY),
    THREE.MathUtils.degToRad(transform.rotationZ),
  );
  model.position.set(0, 0, 0);
  model.updateMatrix();
  model.position.y = -tightLocalBox(model).min.y;
}

function StlLoadingOverlay() {
  return (
    <div className="stl-viewer-overlay stl-viewer-loading" role="status">
      <span className="spinner stl-viewer-spinner" aria-hidden="true" />
      <strong>3D 미리보기를 준비하고 있습니다</strong>
      <span>큰 파일은 시간이 조금 걸릴 수 있습니다.</span>
    </div>
  );
}

export function StlViewer({ url, transform, className = "", onDimensions, onStats, color }: { url: string; transform?: ModelTransform; className?: string; onDimensions?: (size: { x: number; y: number; z: number }) => void; onStats?: (stats: { triangles: number }) => void; color?: string }) {
  const mountRef = useRef<HTMLDivElement>(null);
  const modelRef = useRef<THREE.Mesh | null>(null);
  const transformRef = useRef<ModelTransform>(transform ?? DEFAULT_TRANSFORM);
  const onDimensionsRef = useRef(onDimensions);
  onDimensionsRef.current = onDimensions;
  const onStatsRef = useRef(onStats);
  onStatsRef.current = onStats;
  const colorRef = useRef(color);
  colorRef.current = color;
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  // Scene/camera/renderer/model setup — only ever tears down and rebuilds
  // when the file itself changes, not on every scale/rotation tweak.
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 10000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);

    const styles = getComputedStyle(document.documentElement);
    const tokenColor = (name: string) => new THREE.Color(styles.getPropertyValue(name).trim());
    const modelColor = tokenColor("--color-model");
    const fillColor = tokenColor("--color-model-fill");
    const gridColor = tokenColor("--color-model-grid");

    // Plain white lights + a Phong material with a real specular term — the
    // original viewer's rig. Deliberately not driven by a theme token: the
    // dark-mode "lamp colour" token is a near-black slate, which is what
    // flattened the model to a shadowed silhouette and triggered the matcap
    // workaround. White lights read correctly in both themes.
    scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 2.0));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
    keyLight.position.set(3, 5, 4);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0xdfe6ff, 0.6);
    fillLight.position.set(-4, -2, -3);
    scene.add(fillLight);

    const grid = new THREE.GridHelper(256, 16, fillColor, gridColor);
    scene.add(grid);
    let axisIndicators: THREE.Group | null = null;
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.zoomToCursor = true; // wheel zooms toward the point under the cursor

    let frame = 0;
    let disposed = false;
    setStatus("loading");

    const buildModel = (geometry: THREE.BufferGeometry) => {
      if (disposed) return;
      geometry.computeBoundingBox();
      const originalSize = geometry.boundingBox?.getSize(new THREE.Vector3());
      if (originalSize) onDimensionsRef.current?.({ x: originalSize.x, y: originalSize.y, z: originalSize.z });
      onStatsRef.current?.({ triangles: geometry.getAttribute("position").count / 3 });
      const model = new THREE.Mesh(
        geometry,
        new THREE.MeshPhongMaterial({
          color: toColorHex(colorRef.current) ?? modelColor,
          specular: 0x2b2b2b,
          shininess: 40,
          side: THREE.DoubleSide,
        }),
      );
      applyTransform(model, transformRef.current);
      scene.add(model);
      modelRef.current = model;

      const box = new THREE.Box3().setFromObject(model);
      const size = box.getSize(new THREE.Vector3());
      const span = Math.max(size.x, size.y, size.z, 10);
      camera.position.set(span * 1.25, span, span * 1.55);
      controls.target.set(0, size.y / 3, 0);
      controls.update();

      // Sized relative to the model (and thus to how tightly the camera
      // above is framed) rather than a fixed constant — a fixed-length axis
      // easily lands outside the visible frustum for a small model, which
      // is why the Z label was getting clipped at the top of the viewport.
      axisIndicators = buildAxisIndicators(THREE.MathUtils.clamp(span * 1.1, 30, 120));
      scene.add(axisIndicators);
      setStatus("ready");
    };

    const cached = geometryCache.get(url);
    if (cached) {
      buildModel(cached);
    } else {
      const loader = new STLLoader();
      loader.load(
        url,
        (geometry) => {
          if (!hasUsableNormals(geometry)) geometry.computeVertexNormals();
          geometry.computeBoundingBox();
          geometry.center();
          cacheGeometry(url, geometry);
          buildModel(geometry);
        },
        undefined,
        (error) => {
          if (disposed) return;
          console.warn("STL 미리보기를 불러오지 못했습니다", error);
          setStatus("error");
        },
      );
    }

    const resize = () => {
      const width = Math.max(mount.clientWidth, 1);
      const height = Math.max(mount.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();
    const render = () => {
      frame = requestAnimationFrame(render);
      controls.update();
      renderer.render(scene, camera);
    };
    render();
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      const model = modelRef.current;
      if (model) {
        // geometry is owned by geometryCache — don't dispose it here
        (model.material as THREE.Material).dispose();
      }
      modelRef.current = null;
      if (axisIndicators) disposeGroup(axisIndicators);
      renderer.dispose();
      renderer.domElement.remove();
    };
    // url only — see the effect below for applying transform changes to the
    // already-loaded model without rebuilding the scene/camera.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  // Re-applies scale/rotation to the existing model when transform changes.
  // Deliberately does not touch camera/controls, so the current view holds.
  useEffect(() => {
    transformRef.current = transform ?? DEFAULT_TRANSFORM;
    const model = modelRef.current;
    if (!model) return; // not loaded yet — the setup effect applies transformRef.current once it is
    applyTransform(model, transformRef.current);
  }, [transform]);

  // Recolour the loaded model to the selected filament (or back to the token).
  useEffect(() => {
    const model = modelRef.current;
    if (!model) return;
    const fallback = getComputedStyle(document.documentElement).getPropertyValue("--color-model").trim();
    (model.material as THREE.MeshPhongMaterial).color.set(toColorHex(color) ?? fallback);
  }, [color]);

  return (
    <div className={`stl-viewer ${className}`} role="group" aria-label="3D 모델 미리보기" aria-busy={status === "loading"}>
      <div ref={mountRef} className="stl-viewer-mount" />
      {status === "loading" ? <StlLoadingOverlay /> : null}
      {status === "error" ? <div className="stl-viewer-overlay is-error" role="alert">모델을 불러오지 못했습니다</div> : null}
    </div>
  );
}

// ── Multi-part bed editor ────────────────────────────────────────────────────

export const BED_MM = 256;

export interface PartTransform {
  x: number;          // mm offset of the footprint centre from the bed centre
  y: number;
  scale: number;
  rotationX: number;  // degrees
  rotationY: number;
  rotationZ: number;
}

export interface PartMetrics {
  size: { x: number; y: number; z: number };  // transformed bounding-box dimensions, mm
  triangles: number;
}

/** R = Rz·Ry·Rx applied to a column vector — matches backend merge_stls so the
 * preview and the sliced plate agree even for multi-axis rotations. */
function eulerFromDegrees(rx: number, ry: number, rz: number): THREE.Euler {
  const m = new THREE.Matrix4().makeRotationZ(THREE.MathUtils.degToRad(rz));
  m.multiply(new THREE.Matrix4().makeRotationY(THREE.MathUtils.degToRad(ry)));
  m.multiply(new THREE.Matrix4().makeRotationX(THREE.MathUtils.degToRad(rx)));
  return new THREE.Euler().setFromRotationMatrix(m);
}

interface PlateScene {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  bedGroup: THREE.Group;
  meshes: (THREE.Mesh | null)[];
  boxHelper: THREE.Box3Helper | null;
  boxIndex: number | null;
}

// X/Y/Z gnomon anchored at a bed corner, drawn in bedGroup-local (slicer)
// coordinates: +X to the right, +Y toward the back of the bed, +Z up.
function buildBedAxes(length: number): THREE.Group {
  const group = new THREE.Group();
  group.position.set(-BED_MM / 2, -BED_MM / 2, 0);
  const specs: { dir: [number, number, number]; color: string; label: string }[] = [
    { dir: [1, 0, 0], color: "#ef4444", label: "X" },
    { dir: [0, 1, 0], color: "#22c55e", label: "Y" },
    { dir: [0, 0, 1], color: "#3b82f6", label: "Z" },
  ];
  for (const { dir, color, label } of specs) {
    const end = new THREE.Vector3(...dir).multiplyScalar(length);
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), end]),
      new THREE.LineBasicMaterial({ color }),
    );
    group.add(line);
    const sprite = makeAxisLabel(label, color, Math.max(length * 0.28, 14));
    sprite.position.copy(end).addScaledVector(new THREE.Vector3(...dir), length * 0.12);
    group.add(sprite);
  }
  return group;
}

export function StlPlateEditor({
  parts,
  selected,
  invalid,
  onSelect,
  onMove,
  onPartMetrics,
  color,
  className = "",
}: {
  parts: { url: string; transform: PartTransform }[];
  selected: number | null;
  invalid: Set<number>;
  onSelect: (index: number | null) => void;
  onMove: (index: number, pos: { x: number; y: number }) => void;
  onPartMetrics: (index: number, metrics: PartMetrics) => void;
  color?: string;
  className?: string;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<PlateScene | null>(null);
  const partsRef = useRef(parts);
  partsRef.current = parts;
  const colorRef = useRef(color);
  colorRef.current = color;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const invalidRef = useRef(invalid);
  invalidRef.current = invalid;
  const cbRef = useRef({ onSelect, onMove, onPartMetrics });
  cbRef.current = { onSelect, onMove, onPartMetrics };
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  // Per-part { footprint centre, drop height, size } for the current scale +
  // rotation. Recomputed from the actual vertices (a rotated bounding box is
  // far looser than the real hull, which is what left parts floating), and
  // cached so a plain XY drag doesn't re-walk the mesh.
  const shapeRef = useRef<Record<number, { key: string; cx: number; cy: number; dropZ: number; size: THREE.Vector3 }>>({});

  const urlsKey = parts.map((part) => part.url).join("|");

  // Position/rotation/scale each mesh, drop it onto Z=0, report its footprint.
  const applyTransforms = () => {
    const s = sceneRef.current;
    if (!s) return;
    partsRef.current.forEach((part, index) => {
      const mesh = s.meshes[index];
      if (!mesh) return;
      const t = part.transform;
      mesh.scale.setScalar(t.scale);
      mesh.rotation.copy(eulerFromDegrees(t.rotationX, t.rotationY, t.rotationZ));
      const key = `${t.scale}|${t.rotationX}|${t.rotationY}|${t.rotationZ}`;
      let shape = shapeRef.current[index];
      if (!shape || shape.key !== key) {
        mesh.position.set(0, 0, 0);
        mesh.updateMatrix();
        const box = tightLocalBox(mesh);
        shape = {
          key,
          cx: (box.min.x + box.max.x) / 2,
          cy: (box.min.y + box.max.y) / 2,
          dropZ: -box.min.z,
          size: box.getSize(new THREE.Vector3()),
        };
        shapeRef.current[index] = shape;
      }
      mesh.position.set(t.x - shape.cx, t.y - shape.cy, shape.dropZ);
      mesh.updateMatrix();
      cbRef.current.onPartMetrics(index, {
        size: { x: shape.size.x, y: shape.size.y, z: shape.size.z },
        triangles: mesh.geometry.getAttribute("position").count / 3,
      });
    });
  };

  const updateHighlights = () => {
    const s = sceneRef.current;
    if (!s) return;
    const sel = selectedRef.current;
    const bad = invalidRef.current;
    s.meshes.forEach((mesh, index) => {
      if (!mesh) return;
      const material = mesh.material as THREE.MeshPhongMaterial;
      if (bad.has(index)) material.emissive.setHex(0x7f1d1d);
      else if (index === sel) material.emissive.setHex(0x1e3a5f);
      else material.emissive.setHex(0x000000);
    });
    if (s.boxIndex !== sel) {
      if (s.boxHelper) {
        s.scene.remove(s.boxHelper);
        s.boxHelper.geometry.dispose();
        s.boxHelper = null;
      }
      const target = sel != null ? s.meshes[sel] : null;
      if (target) {
        s.boxHelper = new THREE.Box3Helper(
          new THREE.Box3().setFromObject(target),
          new THREE.Color(0x3b82f6),
        );
        s.scene.add(s.boxHelper);
      }
      s.boxIndex = sel;
    }
  };

  // Scene + meshes: rebuilt only when the SET of parts changes (add / remove).
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    setStatus("loading");

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 20000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);

    const styles = getComputedStyle(document.documentElement);
    const tokenColor = (name: string) => new THREE.Color(styles.getPropertyValue(name).trim());
    const modelColor = tokenColor("--color-model");
    const fillColor = tokenColor("--color-model-fill");
    const gridColor = tokenColor("--color-model-grid");

    scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 2.0));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
    keyLight.position.set(3, 5, 4);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0xdfe6ff, 0.6);
    fillLight.position.set(-4, -2, -3);
    scene.add(fillLight);

    // Everything lives in a group rotated so its local XY is the bed plane and
    // local +Z is up — i.e. slicer coordinates. Part positions and the merge
    // maths on the server are then the same numbers.
    const bedGroup = new THREE.Group();
    bedGroup.rotation.x = -Math.PI / 2;
    scene.add(bedGroup);
    const grid = new THREE.GridHelper(BED_MM, 16, fillColor, gridColor);
    grid.rotation.x = Math.PI / 2; // lay it in the group's local XY plane
    bedGroup.add(grid);
    const bedAxes = buildBedAxes(44);
    bedGroup.add(bedAxes);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.zoomToCursor = true;
    camera.position.set(BED_MM * 0.9, BED_MM * 1.0, BED_MM * 1.3);
    controls.target.set(0, 0, 0);
    controls.update();

    const state: PlateScene = {
      scene, camera, renderer, controls, bedGroup, meshes: [], boxHelper: null, boxIndex: null,
    };
    sceneRef.current = state;

    let disposed = false;
    const currentParts = partsRef.current;
    state.meshes = currentParts.map(() => null);
    shapeRef.current = {};
    let loaded = 0;

    const placeMesh = (index: number, geometry: THREE.BufferGeometry) => {
      if (disposed) return;
      const mesh = new THREE.Mesh(
        geometry,
        new THREE.MeshPhongMaterial({
          color: toColorHex(colorRef.current) ?? modelColor,
          specular: 0x2b2b2b,
          shininess: 40,
          side: THREE.DoubleSide,
        }),
      );
      mesh.matrixAutoUpdate = false;
      state.meshes[index] = mesh;
      bedGroup.add(mesh);
      loaded += 1;
      if (loaded === currentParts.length) {
        applyTransforms();
        updateHighlights();
        setStatus("ready");
      }
    };

    currentParts.forEach((part, index) => {
      const cached = geometryCache.get(part.url);
      if (cached) {
        placeMesh(index, cached);
        return;
      }
      new STLLoader().load(
        part.url,
        (geometry) => {
          if (!hasUsableNormals(geometry)) geometry.computeVertexNormals();
          geometry.computeBoundingBox();
          geometry.center();
          cacheGeometry(part.url, geometry);
          placeMesh(index, geometry);
        },
        undefined,
        (error) => {
          if (!disposed) {
            console.warn("STL 미리보기를 불러오지 못했습니다", error);
            setStatus("error");
          }
        },
      );
    });

    // ── Pointer: click to select, drag a selected part across the bed ────────
    const raycaster = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const hitPoint = new THREE.Vector3();
    let drag: { index: number; offsetX: number; offsetY: number } | null = null;
    let downX = 0;
    let downY = 0;

    const toNdc = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      ndc.set(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
    };
    const bedPointFromPointer = (event: PointerEvent): THREE.Vector3 | null => {
      toNdc(event);
      raycaster.setFromCamera(ndc, camera);
      if (!raycaster.ray.intersectPlane(groundPlane, hitPoint)) return null;
      return bedGroup.worldToLocal(hitPoint.clone());
    };

    const onPointerDown = (event: PointerEvent) => {
      downX = event.clientX;
      downY = event.clientY;
      toNdc(event);
      raycaster.setFromCamera(ndc, camera);
      const meshes = state.meshes.filter(Boolean) as THREE.Mesh[];
      const hit = raycaster.intersectObjects(meshes, false)[0];
      if (!hit) return;
      const index = state.meshes.indexOf(hit.object as THREE.Mesh);
      cbRef.current.onSelect(index);
      const local = bedPointFromPointer(event);
      const t = partsRef.current[index]?.transform;
      if (local && t) {
        drag = { index, offsetX: local.x - t.x, offsetY: local.y - t.y };
        controls.enabled = false;
        renderer.domElement.setPointerCapture(event.pointerId);
      }
    };
    const onPointerMove = (event: PointerEvent) => {
      if (!drag) return;
      const local = bedPointFromPointer(event);
      if (!local) return;
      const half = BED_MM / 2;
      cbRef.current.onMove(drag.index, {
        x: THREE.MathUtils.clamp(local.x - drag.offsetX, -half, half),
        y: THREE.MathUtils.clamp(local.y - drag.offsetY, -half, half),
      });
    };
    const onPointerUp = (event: PointerEvent) => {
      if (drag) {
        drag = null;
        controls.enabled = true;
        try { renderer.domElement.releasePointerCapture(event.pointerId); } catch { /* ignore */ }
        return;
      }
      if (Math.hypot(event.clientX - downX, event.clientY - downY) < 4) cbRef.current.onSelect(null);
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);

    const resize = () => {
      const width = Math.max(mount.clientWidth, 1);
      const height = Math.max(mount.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();

    let frame = 0;
    const render = () => {
      frame = requestAnimationFrame(render);
      controls.update();
      const helper = state.boxHelper;
      if (helper && selectedRef.current != null) {
        const mesh = state.meshes[selectedRef.current];
        if (mesh) helper.box.setFromObject(mesh);
      }
      renderer.render(scene, camera);
    };
    render();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      controls.dispose();
      state.meshes.forEach((mesh) => {
        if (mesh) (mesh.material as THREE.Material).dispose(); // geometry owned by the cache
      });
      disposeGroup(bedAxes);
      grid.geometry.dispose();
      (grid.material as THREE.Material).dispose();
      if (state.boxHelper) state.boxHelper.geometry.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlsKey]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { applyTransforms(); }, [parts]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { updateHighlights(); }, [selected, invalid]);

  // Recolour every part to the selected filament (or back to the token).
  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;
    const fallback = getComputedStyle(document.documentElement).getPropertyValue("--color-model").trim();
    const next = toColorHex(color) ?? fallback;
    s.meshes.forEach((mesh: THREE.Mesh | null) => {
      if (mesh) (mesh.material as THREE.MeshPhongMaterial).color.set(next);
    });
  }, [color, urlsKey]);

  return (
    <div className={`stl-viewer ${className}`} role="group" aria-label="3D 배치 편집기" aria-busy={status === "loading"}>
      <div ref={mountRef} className="stl-viewer-mount" />
      {status === "loading" ? <StlLoadingOverlay /> : null}
      {status === "error" ? <div className="stl-viewer-overlay is-error" role="alert">모델을 불러오지 못했습니다</div> : null}
    </div>
  );
}
