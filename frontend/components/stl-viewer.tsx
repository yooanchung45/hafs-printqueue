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

export function StlViewer({ url, transform, className = "", onDimensions }: { url: string; transform?: ModelTransform; className?: string; onDimensions?: (size: { x: number; y: number; z: number }) => void }) {
  const mountRef = useRef<HTMLDivElement>(null);

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
    const lightColor = tokenColor("--color-model-light");
    const fillColor = tokenColor("--color-model-fill");
    const gridColor = tokenColor("--color-model-grid");

    scene.add(new THREE.HemisphereLight(lightColor, fillColor, 1.8));
    const light = new THREE.DirectionalLight(lightColor, 2.4);
    light.position.set(3, 5, 4);
    scene.add(light);
    const grid = new THREE.GridHelper(256, 16, fillColor, gridColor);
    scene.add(grid);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    let model: THREE.Mesh | null = null;
    let frame = 0;
    const loader = new STLLoader();
    loader.load(url, (geometry) => {
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      const originalSize = geometry.boundingBox?.getSize(new THREE.Vector3());
      if (originalSize) onDimensions?.({ x: originalSize.x, y: originalSize.y, z: originalSize.z });
      geometry.center();
      model = new THREE.Mesh(
        geometry,
        new THREE.MeshStandardMaterial({ color: modelColor, roughness: 0.62, metalness: 0.02 }),
      );
      const current = transform ?? { scale: 1, rotationX: 0, rotationY: 0, rotationZ: 0 };
      model.scale.setScalar(current.scale);
      model.rotation.set(
        THREE.MathUtils.degToRad(current.rotationX - 90),
        THREE.MathUtils.degToRad(current.rotationY),
        THREE.MathUtils.degToRad(current.rotationZ),
      );
      const box = new THREE.Box3().setFromObject(model);
      const size = box.getSize(new THREE.Vector3());
      model.position.y = size.y / 2;
      scene.add(model);
      const span = Math.max(size.x, size.y, size.z, 10);
      camera.position.set(span * 1.25, span, span * 1.55);
      controls.target.set(0, size.y / 3, 0);
      controls.update();
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
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      if (model) {
        model.geometry.dispose();
        (model.material as THREE.Material).dispose();
      }
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [url, transform, onDimensions]);

  return <div ref={mountRef} className={`stl-viewer ${className}`} role="img" aria-label="3D 모델 미리보기" />;
}
