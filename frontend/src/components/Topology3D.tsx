import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import {
  RotateCcw,
  Sparkles,
  Server,
  Zap,
  Maximize2,
  Minimize2,
  ExternalLink,
} from 'lucide-react';
import type { Device } from '../api';

interface Topology3DProps {
  devices: Device[];
  onSelectDevice: (deviceId: number) => void;
  onRunDryRun: (deviceIds: number[]) => void;
  isJobRunning?: boolean;
}

interface Node3DData {
  id: number;
  hostname: string;
  role: 'spine' | 'leaf' | 'border';
  platform: string;
  ip: string;
  status: string;
  position: THREE.Vector3;
  meshGroup: THREE.Group;
  glowMesh: THREE.Mesh;
}

export const Topology3D: React.FC<Topology3DProps> = ({
  devices,
  onSelectDevice,
  onRunDryRun,
  isJobRunning = false,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredNode, setHoveredNode] = useState<Node3DData | null>(null);
  const [selectedNode, setSelectedNode] = useState<Node3DData | null>(null);
  const [autoRotate, setAutoRotate] = useState(true);
  const [burstMode, setBurstMode] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Mutable refs so the animation loop reads live values without re-creating the scene.
  const autoRotateRef = useRef(autoRotate);
  const burstModeRef = useRef(burstMode);
  const isJobRunningRef = useRef(isJobRunning);
  useEffect(() => { autoRotateRef.current = autoRotate; }, [autoRotate]);
  useEffect(() => { burstModeRef.current = burstMode; }, [burstMode]);
  useEffect(() => { isJobRunningRef.current = isJobRunning; }, [isJobRunning]);

  // References for Three.js animation loop
  const sceneRef = useRef<THREE.Scene | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const nodesMapRef = useRef<Map<number, Node3DData>>(new Map());
  const packetParticlesRef = useRef<{ particle: THREE.Mesh; start: THREE.Vector3; end: THREE.Vector3; progress: number; speed: number }[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight || 540;

    // 1. Scene & Camera
    const scene = new THREE.Scene();
    sceneRef.current = scene;
    scene.fog = new THREE.FogExp2(0x020617, 0.04);

    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 100);
    camera.position.set(0, 0, 11);
    cameraRef.current = camera;

    // 2. Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    container.replaceChildren(renderer.domElement);
    rendererRef.current = renderer;

    // 3. Ambient & Point Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);

    const primaryLight = new THREE.PointLight(0x6366f1, 3, 25);
    primaryLight.position.set(0, 5, 8);
    scene.add(primaryLight);

    const secondaryLight = new THREE.PointLight(0x06b6d4, 2, 25);
    secondaryLight.position.set(0, -5, 8);
    scene.add(secondaryLight);

    // 4. Background Starfield / Cyber Grid
    const starCount = 300;
    const starGeometry = new THREE.BufferGeometry();
    const starPositions = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount * 3; i += 3) {
      starPositions[i] = (Math.random() - 0.5) * 35;
      starPositions[i + 1] = (Math.random() - 0.5) * 35;
      starPositions[i + 2] = (Math.random() - 0.5) * 20 - 5;
    }
    starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
    const starMaterial = new THREE.PointsMaterial({
      color: 0x475569,
      size: 0.12,
      transparent: true,
      opacity: 0.7,
    });
    const starPoints = new THREE.Points(starGeometry, starMaterial);
    scene.add(starPoints);

    // Ground Grid Helper
    const grid = new THREE.GridHelper(24, 24, 0x1e293b, 0x0f172a);
    grid.position.y = -4.5;
    scene.add(grid);

    // 5. Node Positions (2 Spine at top, 2 Leaf at bottom)
    const positions: Record<string, THREE.Vector3> = {
      'spine-1.croc.lab': new THREE.Vector3(-3.2, 2.2, 0),
      'spine-2.croc.lab': new THREE.Vector3(3.2, 2.2, 0),
      'leaf-1.croc.lab': new THREE.Vector3(-3.2, -2.2, 0),
      'leaf-2.croc.lab': new THREE.Vector3(3.2, -2.2, 0),
    };

    const nodesMap = new Map<number, Node3DData>();
    nodesMapRef.current = nodesMap;

    devices.forEach((dev) => {
      const pos = positions[dev.hostname] || new THREE.Vector3(0, 0, 0);
      const isSpine = dev.role === 'spine';
      const nodeColor = isSpine ? 0x8b5cf6 : 0x06b6d4; // purple for spine, cyan for leaf

      const group = new THREE.Group();
      group.position.copy(pos);

      // Core Mesh
      const geometry = isSpine
        ? new THREE.OctahedronGeometry(0.85, 0)
        : new THREE.BoxGeometry(1.1, 1.1, 1.1);

      const material = new THREE.MeshStandardMaterial({
        color: nodeColor,
        roughness: 0.2,
        metalness: 0.8,
        emissive: nodeColor,
        emissiveIntensity: 0.35,
      });
      const coreMesh = new THREE.Mesh(geometry, material);
      (coreMesh as any).deviceId = dev.id;
      group.add(coreMesh);

      // Wireframe overlay
      const wireGeo = new THREE.WireframeGeometry(geometry);
      const wireMat = new THREE.LineBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.4,
      });
      const wireLine = new THREE.LineSegments(wireGeo, wireMat);
      group.add(wireLine);

      // Outer Revolving Ring
      const ringGeo = new THREE.TorusGeometry(1.35, 0.03, 16, 50);
      const ringMat = new THREE.MeshBasicMaterial({
        color: nodeColor,
        transparent: true,
        opacity: 0.6,
      });
      const ringMesh = new THREE.Mesh(ringGeo, ringMat);
      ringMesh.rotation.x = Math.PI / 2;
      group.add(ringMesh);

      // Status Aura Glow Mesh
      const auraColor =
        dev.status === 'DRIFT_DETECTED'
          ? 0xf59e0b
          : dev.status === 'UNREACHABLE'
          ? 0xef4444
          : dev.status === 'IN_SYNC'
          ? 0x10b981
          : 0x64748b;

      const auraGeo = new THREE.SphereGeometry(1.5, 16, 16);
      const auraMat = new THREE.MeshBasicMaterial({
        color: auraColor,
        transparent: true,
        opacity: 0.18,
        wireframe: true,
      });
      const auraMesh = new THREE.Mesh(auraGeo, auraMat);
      group.add(auraMesh);

      scene.add(group);

      nodesMap.set(dev.id, {
        id: dev.id,
        hostname: dev.hostname,
        role: dev.role,
        platform: dev.platform,
        ip: dev.management_ip,
        status: dev.status,
        position: pos,
        meshGroup: group,
        glowMesh: auraMesh,
      });
    });

    // 6. CLOS Fiber Optical Links (Every Leaf connects to every Spine)
    const spines = devices.filter((d) => d.role === 'spine');
    const leaves = devices.filter((d) => d.role === 'leaf');
    const links: { start: THREE.Vector3; end: THREE.Vector3 }[] = [];

    leaves.forEach((leaf) => {
      spines.forEach((spine) => {
        const start = positions[leaf.hostname];
        const end = positions[spine.hostname];
        if (start && end) {
          links.push({ start, end });

          // Draw Glowing Fiber Link
          const curve = new THREE.LineCurve3(start, end);
          const tubeGeo = new THREE.TubeGeometry(curve, 20, 0.04, 8, false);
          const tubeMat = new THREE.MeshBasicMaterial({
            color: 0x38bdf8,
            transparent: true,
            opacity: 0.35,
          });
          const tube = new THREE.Mesh(tubeGeo, tubeMat);
          scene.add(tube);
        }
      });
    });

    // 7. Dynamic Data Packets (Photons traveling along links)
    const packets: { particle: THREE.Mesh; start: THREE.Vector3; end: THREE.Vector3; progress: number; speed: number }[] = [];
    const packetGeo = new THREE.SphereGeometry(0.1, 8, 8);
    const packetMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });

    links.forEach((link, idx) => {
      // 2 packets per link traveling in opposite directions
      for (let p = 0; p < 2; p++) {
        const mesh = new THREE.Mesh(packetGeo, packetMat.clone());
        scene.add(mesh);
        packets.push({
          particle: mesh,
          start: p === 0 ? link.start : link.end,
          end: p === 0 ? link.end : link.start,
          progress: (idx * 0.25 + p * 0.5) % 1,
          speed: 0.006 + Math.random() * 0.004,
        });
      }
    });
    packetParticlesRef.current = packets;

    // 8. Mouse & Interaction (Raycaster & Orbit Control)
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2(-100, -100);
    let isDragging = false;
    let previousMousePosition = { x: 0, y: 0 };
    const sceneRotation = { x: 0.1, y: 0 };

    const onMouseDown = (e: MouseEvent) => {
      isDragging = true;
      previousMousePosition = { x: e.clientX, y: e.clientY };
    };

    const onMouseMove = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      if (isDragging) {
        const deltaX = e.clientX - previousMousePosition.x;
        const deltaY = e.clientY - previousMousePosition.y;
        sceneRotation.y += deltaX * 0.005;
        sceneRotation.x += deltaY * 0.005;
        previousMousePosition = { x: e.clientX, y: e.clientY };
      }

      // Check hover
      raycaster.setFromCamera(mouse, camera);
      const meshes: THREE.Object3D[] = [];
      nodesMap.forEach((n) => meshes.push(n.meshGroup.children[0]));
      const intersects = raycaster.intersectObjects(meshes);

      if (intersects.length > 0) {
        const devId = (intersects[0].object as any).deviceId;
        const node = nodesMap.get(devId);
        setHoveredNode(node || null);
        container.style.cursor = 'pointer';
      } else {
        setHoveredNode(null);
        container.style.cursor = isDragging ? 'grabbing' : 'grab';
      }
    };

    const onMouseUp = () => {
      isDragging = false;
    };

    const onClick = () => {
      raycaster.setFromCamera(mouse, camera);
      const meshes: THREE.Object3D[] = [];
      nodesMap.forEach((n) => meshes.push(n.meshGroup.children[0]));
      const intersects = raycaster.intersectObjects(meshes);

      if (intersects.length > 0) {
        const devId = (intersects[0].object as any).deviceId;
        const node = nodesMap.get(devId);
        if (node) {
          setSelectedNode(node);
          onSelectDevice(node.id);
        }
      }
    };

    const onWheel = (e: WheelEvent) => {
      camera.position.z = THREE.MathUtils.clamp(camera.position.z + e.deltaY * 0.01, 6, 18);
    };

    container.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    container.addEventListener('click', onClick);
    container.addEventListener('wheel', onWheel);

    // 9. Render Loop
    let animationFrameId: number;
    const clock = new THREE.Clock();

    const animate = () => {
      animationFrameId = requestAnimationFrame(animate);
      const delta = clock.getDelta();
      const elapsedTime = clock.getElapsedTime();

      // Auto rotation
      if (autoRotateRef.current && !isDragging) {
        sceneRotation.y += delta * 0.15;
      }
      scene.rotation.y = sceneRotation.y;
      scene.rotation.x = sceneRotation.x;

      // Rotate nodes and rings
      nodesMap.forEach((node) => {
        const core = node.meshGroup.children[0];
        const ring = node.meshGroup.children[2];
        const aura = node.glowMesh;

        core.rotation.y += delta * 0.8;
        core.rotation.x += delta * 0.4;
        ring.rotation.z += delta * 1.2;

        // Aura pulse
        const pulse = 1 + Math.sin(elapsedTime * 3) * 0.08;
        aura.scale.set(pulse, pulse, pulse);
      });

      // Animate packet particles along links
      const speedMultiplier = isJobRunningRef.current || burstModeRef.current ? 4.5 : 1.0;
      packets.forEach((p) => {
        p.progress += p.speed * speedMultiplier;
        if (p.progress >= 1) p.progress = 0;
        p.particle.position.lerpVectors(p.start, p.end, p.progress);

        // Highlight packet in burst/job
        if (isJobRunningRef.current || burstModeRef.current) {
          (p.particle.material as THREE.MeshBasicMaterial).color.setHex(0xf59e0b);
        } else {
          (p.particle.material as THREE.MeshBasicMaterial).color.setHex(0x38bdf8);
        }
      });

      renderer.render(scene, camera);
    };

    animate();

    // 10. Resize Observer
    const resizeObserver = new ResizeObserver(() => {
      if (!container || !renderer || !camera) return;
      const newWidth = container.clientWidth;
      const newHeight = container.clientHeight || 540;
      camera.aspect = newWidth / newHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(newWidth, newHeight);
    });
    resizeObserver.observe(container);

    return () => {
      cancelAnimationFrame(animationFrameId);
      container.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      container.removeEventListener('click', onClick);
      container.removeEventListener('wheel', onWheel);
      resizeObserver.disconnect();
      renderer.dispose();
    };
  }, [devices]);

  return (
    <div
      className={`relative bg-slate-950 border border-slate-800 rounded-3xl overflow-hidden shadow-2xl transition-all duration-300 ${
        isFullscreen ? 'fixed inset-0 z-50 rounded-none' : 'h-[580px]'
      }`}
    >
      {/* 3D WebGL Canvas */}
      <div ref={containerRef} className="w-full h-full cursor-grab active:cursor-grabbing" />

      {/* Floating Header Overlay */}
      <div className="absolute top-4 left-4 z-10 pointer-events-none flex items-center space-x-3">
        <div className="h-9 w-9 rounded-xl bg-indigo-500/20 border border-indigo-500/30 backdrop-blur flex items-center justify-center text-indigo-400 shadow-lg">
          <Sparkles className="w-5 h-5 animate-pulse" />
        </div>
        <div>
          <h3 className="text-sm font-extrabold text-white tracking-wide flex items-center space-x-2">
            <span>3D CLOS Topology Visualizer</span>
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
              Live WebGL
            </span>
          </h3>
          <p className="text-[11px] text-slate-400">
            2x Spine (Arista EOS) • 2x Leaf (Cisco IOS-XE) • eBGP Fabric
          </p>
        </div>
      </div>

      {/* Control Buttons (Top Right) */}
      <div className="absolute top-4 right-4 z-10 flex items-center space-x-2">
        <button
          onClick={() => setBurstMode(!burstMode)}
          className={`px-3 py-1.5 rounded-xl text-xs font-semibold backdrop-blur border transition flex items-center space-x-1.5 shadow-sm ${
            burstMode
              ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 shadow-amber-500/20'
              : 'bg-slate-900/80 text-slate-300 border-slate-700 hover:bg-slate-800'
          }`}
          title="Сымитировать всплеск трафика телеметрии"
        >
          <Zap className="w-3.5 h-3.5" />
          <span>{burstMode ? 'Traffic Burst ON' : 'Burst Test'}</span>
        </button>

        <button
          onClick={() => setAutoRotate(!autoRotate)}
          className={`p-2 rounded-xl backdrop-blur border text-xs transition ${
            autoRotate
              ? 'bg-indigo-600/30 text-indigo-300 border-indigo-500/40'
              : 'bg-slate-900/80 text-slate-400 border-slate-700 hover:text-white'
          }`}
          title="Автоповорот 3D камеры"
        >
          <RotateCcw className="w-4 h-4" />
        </button>

        <button
          onClick={() => setIsFullscreen(!isFullscreen)}
          className="p-2 rounded-xl bg-slate-900/80 text-slate-400 hover:text-white border border-slate-700 backdrop-blur transition"
          title={isFullscreen ? 'Свернуть' : 'Развернуть во весь экран'}
        >
          {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
        </button>
      </div>

      {/* Hovered Node Quick Tooltip */}
      {hoveredNode && (
        <div className="absolute top-16 left-4 z-10 bg-slate-900/90 border border-slate-700 backdrop-blur-md p-3.5 rounded-2xl shadow-xl pointer-events-none text-xs space-y-1.5 min-w-[220px]">
          <div className="flex items-center justify-between">
            <span className="font-bold text-white font-mono">{hoveredNode.hostname}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono">
              {hoveredNode.ip}
            </span>
          </div>
          <div className="text-[11px] text-slate-400">
            ОС: <span className="text-slate-200 font-mono uppercase">{hoveredNode.platform}</span> • Роль:{' '}
            <span className="text-slate-200 capitalize">{hoveredNode.role}</span>
          </div>
          <div className="flex items-center space-x-1.5 pt-1 border-t border-slate-800 text-[11px]">
            <span className="text-slate-400">Статус:</span>
            <span
              className={`font-semibold ${
                hoveredNode.status === 'IN_SYNC'
                  ? 'text-emerald-400'
                  : hoveredNode.status === 'DRIFT_DETECTED'
                  ? 'text-amber-400'
                  : 'text-rose-400'
              }`}
            >
              {hoveredNode.status}
            </span>
          </div>
        </div>
      )}

      {/* Selected Node Action Card (Bottom Left) */}
      {selectedNode && (
        <div className="absolute bottom-4 left-4 z-10 bg-slate-900/95 border border-indigo-500/40 backdrop-blur-md p-4 rounded-2xl shadow-2xl text-xs space-y-3 min-w-[300px]">
          <div className="flex items-center justify-between pb-2 border-b border-slate-800">
            <div className="flex items-center space-x-2">
              <Server className="w-4 h-4 text-indigo-400" />
              <span className="font-bold text-white font-mono">{selectedNode.hostname}</span>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              className="text-slate-500 hover:text-slate-300 text-xs"
            >
              ✕
            </button>
          </div>

          <div className="space-y-1 text-slate-300 text-[11px]">
            <div>IP: <span className="font-mono text-slate-200">{selectedNode.ip}</span></div>
            <div>Платформа: <span className="font-mono text-slate-200 uppercase">{selectedNode.platform}</span></div>
            <div>Статус: <span className="font-semibold text-emerald-400">{selectedNode.status}</span></div>
          </div>

          <div className="flex items-center space-x-2 pt-1">
            <button
              onClick={() => onRunDryRun([selectedNode.id])}
              className="flex-1 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold transition"
            >
              Dry-Run
            </button>
            <button
              onClick={() => onSelectDevice(selectedNode.id)}
              className="py-1.5 px-3 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition flex items-center space-x-1"
            >
              <span>Параметры</span>
              <ExternalLink className="w-3 h-3 text-slate-400" />
            </button>
          </div>
        </div>
      )}

      {/* Legend & Help Indicator (Bottom Right) */}
      <div className="absolute bottom-4 right-4 z-10 flex items-center space-x-3 bg-slate-900/80 border border-slate-800/80 backdrop-blur px-3 py-1.5 rounded-xl text-[11px] text-slate-400 pointer-events-none">
        <div className="flex items-center space-x-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-purple-500" />
          <span>Spine (Arista)</span>
        </div>
        <div className="flex items-center space-x-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-cyan-400" />
          <span>Leaf (Cisco)</span>
        </div>
        <div className="flex items-center space-x-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
          <span>In Sync</span>
        </div>
        <div className="flex items-center space-x-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
          <span>Drift</span>
        </div>
      </div>
    </div>
  );
};
