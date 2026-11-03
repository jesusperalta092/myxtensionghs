---
name: threejs-worldclass-offline
description: Directivas maestras para escenas 3D interactivas (Three.js / React Three Fiber) 100% offline, fluidas y sin bloqueos de red. Regla de Oro Anti-Suspense, iluminación procedural dinámica para cualquier tipo de proyecto (arquitectura, configurador de producto, sci-fi/cyberpunk, videojuegos) y optimización de sombras y render.
tags: [3d, threejs, r3f, webgl, react-three-fiber, offline, performance, shaders]
---

# Three.js World-Class Offline — Manual de Principios 3D Autónomos

> Esta skill se activa cuando el usuario o el proyecto requiere renderizado 3D (Three.js / @react-three/fiber / drei).
> Prohíbe terminantemente depender de activos remotos de internet en tiempo de ejecución y establece fórmulas matemáticas e iluminación procedural pura para garantizar que cualquier escena 3D cargue en 0 milisegundos y nunca se quede en blanco.

---

## 1. La Regla de Oro: Zero-Suspense & Anti-CDN

### El Error Crítico a Evitar
En `@react-three/drei`, componentes como `<Environment preset="sunset" />` o `<Environment preset="city" />` **descargan archivos .hdr de 5 a 15 MB desde servidores remotos en GitHub/jsDelivr**.
* Si la red tiene latencia, o si se realiza una auditoría visual con Chrome Headless (`hermes_ui_inspect`), el árbol de React queda atrapado dentro de `<Suspense fallback={null}>` indefinidamente.
* **Resultado del fallo:** El lienzo 3D se renderiza completamente vacío (solo el color de fondo).

### La Solución de Ingeniería
Todo modelo, textura, cielo e iluminación debe resolverse **localmente y de forma procedural en código**. Cero descargas remotas en el hook de render.

---

## 2. Fórmulas de Iluminación Procedural por Tipo de Escena

La iluminación debe responder al propósito del proyecto. Nunca uses una fórmula fija: selecciona o parametriza la luz según el caso de uso:

### Tipo A: Luz Natural & Arquitectura (Golden Hour / Daylight)
Para viviendas, estructuras, pabellones y exteriores realistas:
```tsx
{/* Cielo y rebote de suelo */}
<hemisphereLight args={['#dce8f5', '#c2b69d', 0.55]} />
<ambientLight intensity={0.25} />

{/* Sol principal con sombras nítidas y suaves */}
<directionalLight
  position={[8, 12, 6]}
  intensity={2.0}
  color="#fff5e0"
  castShadow
  shadow-mapSize={[2048, 2048]}
  shadow-camera-left={-15}
  shadow-camera-right={15}
  shadow-camera-top={15}
  shadow-camera-bottom={-15}
  shadow-bias={-0.0003}
/>

{/* Relleno cálido de atardecer en el lado opuesto */}
<directionalLight position={[-10, 6, -8]} intensity={0.5} color="#e0caa8" />

{/* Sombra de contacto difusa para anclaje al piso */}
<ContactShadows position={[0, 0.01, 0]} opacity={0.35} scale={35} blur={2.4} far={8} color="#2b261f" />
```

### Tipo B: Estudio Fotográfico & Configuradores de Producto
Para calzado, dispositivos tecnológicos, relojes o automóviles:
```tsx
{/* Fondo neutro de estudio */}
<color attach="background" args={['#121316']} />
<ambientLight intensity={0.4} />

{/* Key Light (Luz clave frontal superior a 45 grados) */}
<directionalLight position={[5, 8, 5]} intensity={1.8} color="#ffffff" castShadow />

{/* Fill Light (Luz de relleno suave y fría) */}
<directionalLight position={[-6, 4, 2]} intensity={0.8} color="#90b8f8" />

{/* Rim Light (Luz trasera de contorno para destacar la silueta y bordes metálicos) */}
<directionalLight position={[0, 6, -8]} intensity={2.2} color="#ffd285" />

<ContactShadows position={[0, -0.01, 0]} opacity={0.6} scale={15} blur={1.8} far={4} color="#000000" />
```

### Tipo C: Sci-Fi, Cyberpunk & Dark Mode
Para interfaces oscuras, mundos futuristas, visualizadores de datos o juegos nocturnos:
```tsx
<color attach="background" args={['#07090e']} />
<fogExp2 attach="fog" args={['#07090e', 0.025]} />
<ambientLight intensity={0.15} />

{/* Luces puntuales de color saturado con atenuación física */}
<pointLight position={[-4, 3, 2]} color="#00f0ff" intensity={3.5} distance={12} />
<pointLight position={[4, 2, -2]} color="#ff0077" intensity={3.0} distance={10} />
<pointLight position={[0, -1, 3]} color="#7928ca" intensity={2.0} distance={8} />

{/* Materiales con emisión propia (Glow) */}
// meshStandardMaterial emissive="#00f0ff" emissiveIntensity={0.8}
```

---

## 3. Principios de Rendimiento y Experiencia de Usuario

1. **DPR Adaptable**: Usa `dpr={[1, 1.75]}` en `<Canvas>` para mantener 60 FPS en pantallas Retina y móviles sin saturar la GPU.
2. **Órbita Controlada (`OrbitControls`)**:
   * Limita `minDistance` y `maxDistance` para evitar que la cámara atraviese el suelo o se pierda en el infinito.
   * `maxPolarAngle={Math.PI / 2.05}`: Evita que la cámara mire el modelo desde abajo del suelo.
   * `enableDamping={true}` con `dampingFactor={0.06}` para una sensación de peso y suavidad táctil.
3. **Respeto a `prefers-reduced-motion`**:
   * Si el usuario tiene desactivadas las animaciones en su sistema operativo, apaga `autoRotate` en `OrbitControls` y cualquier oscilación continua en `useFrame`.
4. **Geometría y Materiales Limpios**:
   * Favorece primitivas procedurales (`boxGeometry`, `cylinderGeometry`, `extrudeGeometry`) para construcciones arquitectónicas o de producto sin necesidad de cargar pesados archivos `.gltf` externos si no son estrictamente requeridos.
