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

/** A procedural matcap: a sphere-lit gradient painted in the model colour —
 * bright key highlight upper-left, dark falloff, a soft rim lower-right. Used
 * instead of scene lights because a matte part lit by directional lights has
 * too little luminance range to read as a solid against a dark viewport, so
 * every lighting rig we tried left it looking like a flat silhouette. A
 * matcap bakes in a strong fixed gradient that always shows form. */
function makeMatcap(base: THREE.Color): THREE.CanvasTexture {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const srgb = base.clone().convertLinearToSRGB();
  const css = (c: THREE.Color) => `#${c.getHexString()}`;
  const light = srgb.clone().lerp(new THREE.Color(0xffffff), 0.72);
  const dark = srgb.clone().multiplyScalar(0.26);
  if (ctx) {
    ctx.fillStyle = css(dark);
    ctx.fillRect(0, 0, size, size);
    const key = ctx.createRadialGradient(size * 0.36, size * 0.33, size * 0.04, size * 0.5, size * 0.5, size * 0.6);
    key.addColorStop(0, css(light));
    key.addColorStop(0.45, css(srgb));
    key.addColorStop(1, css(dark));
    ctx.fillStyle = key;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
    ctx.fill();
    const rim = ctx.createRadialGradient(size * 0.72, size * 0.77, size * 0.02, size * 0.72, size * 0.77, size * 0.3);
    rim.addColorStop(0, "rgba(255,255,255,0.4)");
    rim.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = rim;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
    ctx.fill();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
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

type StlViewerProps = { url: string; transform?: ModelTransform; className?: string; onDimensions?: (size: { x: number; y: number; z: number }) => void };

export function StlViewer(props: StlViewerProps) {
  // A different file starts with a fresh loading state, including when revisiting a tab.
  return <StlViewerScene key={props.url} {...props} />;
}

function StlViewerScene({ url, transform, className = "", onDimensions }: StlViewerProps) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const mountRef = useRef<HTMLDivElement>(null);
  const modelRef = useRef<THREE.Mesh | null>(null);
  const transformRef = useRef<ModelTransform>(transform ?? DEFAULT_TRANSFORM);
  const onDimensionsRef = useRef(onDimensions);
  useEffect(() => { onDimensionsRef.current = onDimensions; }, [onDimensions]);

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
    const rootTheme = document.documentElement.getAttribute("data-theme");
    const darkTheme = rootTheme === "dark"
      || (rootTheme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    // Drawn over the mesh so the actual outline and creases are always
    // visible, whatever the matcap does — a light line in dark mode, a dark
    // line in light mode.
    const edgeColor = new THREE.Color(darkTheme ? "#e6eefb" : "#1e293b");

    // No scene lights: the model uses an unlit matcap material (see
    // makeMatcap) for guaranteed, background-independent shading.
    const grid = new THREE.GridHelper(256, 16, fillColor, gridColor);
    scene.add(grid);
    let axisIndicators: THREE.Group | null = null;
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    let frame = 0;
    let disposed = false;
    const loader = new STLLoader();
    loader.load(url, (geometry) => {
      if (disposed) { geometry.dispose(); return; }
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      const originalSize = geometry.boundingBox?.getSize(new THREE.Vector3());
      geometry.center();
      const model = new THREE.Mesh(
        geometry,
        new THREE.MeshMatcapMaterial({ matcap: makeMatcap(modelColor) }),
      );
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry, 24),
        new THREE.LineBasicMaterial({ color: edgeColor, transparent: true, opacity: 0.55 }),
      );
      model.add(edges);
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
      // Keep the overlay until the mesh and its edges have actually been rendered.
      renderer.render(scene, camera);
      if (originalSize) onDimensionsRef.current?.({ x: originalSize.x, y: originalSize.y, z: originalSize.z });
      setStatus("ready");
    }, undefined, () => {
      if (!disposed) setStatus("error");
    });

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
        model.geometry.dispose();
        const material = model.material as THREE.MeshMatcapMaterial;
        material.matcap?.dispose();
        material.dispose();
        model.children.forEach((child) => {
          if (child instanceof THREE.LineSegments) {
            child.geometry.dispose();
            (child.material as THREE.Material).dispose();
          }
        });
      }
      modelRef.current = null;
      if (axisIndicators) disposeGroup(axisIndicators);
      renderer.dispose();
      renderer.domElement.remove();
    };
    // url only — see the effect below for applying transform changes to the
    // already-loaded model without rebuilding the scene/camera.
  }, [url]);

  // Re-applies scale/rotation to the existing model when transform changes.
  // Deliberately does not touch camera/controls, so the current view holds.
  useEffect(() => {
    transformRef.current = transform ?? DEFAULT_TRANSFORM;
    const model = modelRef.current;
    if (!model) return; // not loaded yet — the setup effect applies transformRef.current once it is
    applyTransform(model, transformRef.current);
  }, [transform]);

  return (
    <div className={`stl-viewer ${className}`} role="group" aria-label="3D 모델 미리보기" aria-busy={status === "loading"}>
      <div ref={mountRef} className="stl-viewer-canvas" role="img" aria-label="3D 모델" />
      {status !== "ready" ? (
        <div className={`stl-viewer-overlay ${status === "loading" ? "stl-viewer-loading" : ""}`} role={status === "error" ? "alert" : "status"}>
          {status === "loading" ? <span className="spinner stl-viewer-spinner" aria-hidden="true" /> : null}
          <strong>{status === "loading" ? "3D 미리보기를 준비하고 있습니다" : "미리보기를 불러오지 못했습니다"}</strong>
          <span>{status === "loading" ? "큰 파일은 시간이 조금 걸릴 수 있습니다." : "잠시 후 다시 시도해 주세요."}</span>
        </div>
      ) : null}
    </div>
  );
}
