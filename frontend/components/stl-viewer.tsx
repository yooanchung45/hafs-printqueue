"use client";

import { useEffect, useRef } from "react";
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

/** Rotate + scale the already-loaded model in place, then re-seat it on the
 * bed (rotating changes the bounding box height). Doesn't touch the camera
 * or controls, so the current view is preserved. */
function applyTransform(model: THREE.Mesh, transform: ModelTransform) {
  model.scale.setScalar(transform.scale);
  model.rotation.set(
    THREE.MathUtils.degToRad(transform.rotationX - 90),
    THREE.MathUtils.degToRad(transform.rotationY),
    THREE.MathUtils.degToRad(transform.rotationZ),
  );
  model.position.y = 0;
  const box = new THREE.Box3().setFromObject(model);
  const size = box.getSize(new THREE.Vector3());
  model.position.y = size.y / 2;
}

export function StlViewer({ url, transform, className = "", onDimensions }: { url: string; transform?: ModelTransform; className?: string; onDimensions?: (size: { x: number; y: number; z: number }) => void }) {
  const mountRef = useRef<HTMLDivElement>(null);
  const modelRef = useRef<THREE.Mesh | null>(null);
  const transformRef = useRef<ModelTransform>(transform ?? DEFAULT_TRANSFORM);
  const onDimensionsRef = useRef(onDimensions);
  onDimensionsRef.current = onDimensions;

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

    const buildModel = (geometry: THREE.BufferGeometry) => {
      if (disposed) return;
      geometry.computeBoundingBox();
      const originalSize = geometry.boundingBox?.getSize(new THREE.Vector3());
      if (originalSize) onDimensionsRef.current?.({ x: originalSize.x, y: originalSize.y, z: originalSize.z });
      const model = new THREE.Mesh(
        geometry,
        new THREE.MeshPhongMaterial({
          color: modelColor,
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
        (error) => console.warn("STL 미리보기를 불러오지 못했습니다", error),
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

  return <div ref={mountRef} className={`stl-viewer ${className}`} role="img" aria-label="3D 모델 미리보기" />;
}
