# GHS AUTONOMOUS CORE & HERMES OS
## Registro Oficial de Propiedad Intelectual, Arquitectura e Innovaciones Propias

> **Autor & Creador Principal:** Jesus Peralta (jesusperalta092)  
> **Sistema:** GHS (Greatest Hermes Suite) + Hermes Autonomous Agent Runtime  
> **Fecha de Consolidación:** Noviembre 2026  
> **Repositorio Oficial:** `myxtensionghs`  
> **Entorno de Despliegue:** Local Windows OS (`127.0.0.1:17613`) & Cloud Render Backend  

---

## 1. Declaración de Visión y Propósito

Este documento certifica, cataloga y describe todas las tecnologías, herramientas, protocolos de auto-recuperación y módulos de inteligencia autónoma diseñados e implementados a medida en este sistema. 

A diferencia de los chatbots convencionales o extensiones pasivas, **GHS** es una plataforma de ejecución agéntica de circuito cerrado (*Closed-Loop Autonomous Agent*) capaz de operar directamente sobre el sistema operativo Windows, auditar visualmente las aplicaciones que crea, auto-corregir sus propios errores de compilación, trabajar en segundo plano sin dormirse y preservar la soberanía y privacidad del usuario.

---

## 2. El Núcleo de Memoria Criptográfica Tripartita

Diseñado para proporcionar memoria persistente, identidad inmutable y protección contra fugas de información privada (PII).

### 2.1 Capa 1: Contexto Inmediato Sanitizado (`SOUL`, `USER`, `MEMORY`)
* **`SOUL.md` (Identidad y Principios):** Define el ethos de Hermes como operador autónomo proactivo. Establece el principio fundamental: *"Primero la acción, después las disculpas; cero alucinación; acceso total autorizado al espacio de trabajo"*.
* **`USER.md` (Perfil del Operador):** Almacena las preferencias del usuario, el estilo de comunicación y las directivas de stack full-stack obligatorio (React/Vite/TypeScript/Tailwind).
* **`MEMORY.md` (Lecciones Técnicas Aprendidas):** Base de conocimientos operativa donde el agente registra bugs superados y mejores prácticas de compilación.

### 2.2 Cifrado Militar en Reposo (AES-128 Fernet)
* Todos los archivos centrales de memoria se almacenan cifrados en disco local mediante tokens Fernet derivados de claves seguras de máquina. Cualquier intento de lectura no autorizada desde fuera del runtime solo obtiene bytes cifrados ininteligibles.

### 2.3 Filtro de Redacción en Tiempo Real (Anti-PII & Anti-Filtro)
* Implementado en `hermes_mcp_server.py`. Intercepta en tiempo real cualquier ruta absoluta de Windows que contenga nombres de usuario reales (`C:\Users\<Usuario>\...`) o puertos internos, enmascarándolos como rutas genéricas (`~/Downloads/...`).
* **Propósito:** Evita que plataformas externas (como DeepSeek, OpenAI o Claude) activen filtros de seguridad (`<ds_safety>`) por supuesta exposición de datos sensibles.

---

## 3. Sistema de Auditoría Visual y Auto-Recuperación (`hermes_ui_inspect`)

El avance más crítico en el control de calidad autónomo. Permite que el agente tenga "ojos" propios sobre el navegador.

* **Telemetría Headless Chrome:** Levanta instancias de Chrome Headless a resoluciones calibradas (1440x900px, 1440x1400px o 390x1400px móvil) para auditar páginas web vivas en `http://localhost:5173` o archivos locales `file:///`.
* **Detección Automática de Pantallas Rojas (Vite HTTP 500):** Analiza el DOM resultante y los mensajes de error de compilación (PostCSS, Tailwind `@apply`, syntax errors, TypeScript).
* **Bucle Cerrado de Reparación:** Si la web falla con HTTP 500, `hermes_ui_inspect` extrae el mensaje de error exacto del compilador y se lo devuelve a la IA, obligándola a parchar el archivo defectuoso hasta que el servidor devuelva HTTP 200 en verde.
* **Sandbox de Privacidad:** Se eliminó deliberadamente la captura de monitores físicos (`mss.grab`) para garantizar que el agente jamás capture la pantalla del escritorio del usuario ni exponga ventanas personales.

---

## 4. Vigilancia de Servidores y Procesos en Vivo (`hermes_dev_server`)

Módulo de gestión de procesos en segundo plano (*Daemons*):

* **Rastreo de PIDs Activos:** Detecta servidores web iniciados por `npm run dev`, `vite`, `python -m http.server`, Next.js, etc.
* **Telemetría de Puertos TCP:** Mantiene un registro en tiempo real de qué proceso es dueño de cada puerto (ej. `PID 24332 -> :5173`), evitando conflictos de puertos duplicados y permitiendo reinicios limpios.
* **Visualización en Mission Control:** Expone los servidores web activos en el encabezado y en la pestaña dedicada del Dashboard de GHS.

---

## 5. Terminal Auto-Cura y Normalizador Multi-Shell (`hermes_terminal` Blindada)

Capa de abstracción inteligente sobre el sistema operativo Windows para erradicar los errores comunes de sintaxis de la IA:

* **Auto-Cura de Rutas con Comillas Simples:** Convierte automáticamente comandos fallidos como `cd 'C:\ruta'` en la sintaxis válida de Windows: `cd /d "C:\ruta"`.
* **Normalización de Separadores:** Convierte barras diagonales inclinadas (`/`) a barras invertidas de Windows (`\`) dentro de las instrucciones de cambio de directorio.
* **Enrutamiento Inteligente de Cmdlets PowerShell:** Si el modelo envía instrucciones de PowerShell (`Set-Location`, `Get-ChildItem`, `Select-Object`, `Where-Object`, `Format-List`, variables `$env:`), el puente las detecta y las envuelve automáticamente en:
  ```cmd
  powershell.exe -ExecutionPolicy Bypass -NoProfile -Command "..."
  ```
* **Escudo Anti-Suicidio del Puente:** Intercepta llamadas destructivas como `taskkill /IM python.exe` para evitar que un agente detenga por accidente el propio servidor WebSocket de GHS.

---

## 6. Motor de Andamiaje Ultrarrápido (`hermes_batch_scaffold`)

Superpoder creado para eliminar el "impuesto de tokens" y el lag extremo en chats largos:

* **Problema que Resuelve:** Antes, crear un proyecto requería 25 turnos separados (uno por cada archivo de configuración), acumulando 80.000 tokens en el chat y provocando congelamiento (`empty.end`).
* **Mecanismo:** Permite que el agente envíe en **un solo turno** un paquete completo con los archivos de andamiaje (`package.json`, `tsconfig.json`, `vite.config.ts`, `tailwind.config.js`, `index.html`, etc.).
* **Pre-Flight Validation (Validación Pre-Vuelo):** Antes de escribir un solo byte en disco, el backend analiza la sintaxis JSON de todos los archivos de configuración. Si alguno tiene una llave rota o está truncado, aborta la operación y exige la corrección inmediata, evitando ensuciar el disco.
* **Impacto:** Reduce el tiempo de inicialización de un proyecto de 10 minutos a **30 milisegundos**, ahorrando el 75% de los tokens de contexto.

---

## 7. Modo Segundo Plano Infinito (Anti-Sleep Keep-Alive)

Mecanismo que otorga autonomía real al agente mientras el usuario realiza otras tareas:

* **El Problema de Chrome:** Por políticas de ahorro de energía (*Background Tab Throttling* y *Memory Saver*), Chrome suspende los temporizadores de las pestañas en segundo plano y congela el bucle del agente.
* **La Solución Propietaria:**
  1. **Interruptor Dinámico en `#zs-bar`:** Botón `⚡ 2do Plano` (Púrpura Neón cuando está activo).
  2. **Micro-Stream de Audio Inaudible:** Inicializa un `AudioContext` con una ganancia de `0.00001` (completamente inaudible). Esto clasifica la pestaña ante Chrome como un "Proceso Multimedia Activo", impidiendo que el navegador reduzca la prioridad de los temporizadores.
  3. **Bypass de `waitVisible()`:** La extensión desactiva el freno de mano de `document.hidden`, permitiendo que el agente ejecute herramientas, instale paquetes npm y audite webs mientras la ventana está minimizada.

---

## 8. Skill Oficial: `threejs-worldclass-offline`

Manual de directivas técnicas para renderizado 3D de alta fidelidad:

* **Principio Anti-Suspense:** Prohibición estricta de importar `<Environment preset="...">` de Drei que dependa de activos `.hdr` remotos en GitHub/CDN.
* **Iluminación Procedural Dinámica:**
  * **Arquitectura:** `hemisphereLight` cielo/tierra + sol direccional cálido (sombras 2048px) + `ContactShadows`.
  * **Configurador de Producto:** Sistema de 3 puntos (Key, Fill y Rim light de contorno).
  * **Cyberpunk / Sci-Fi:** Niebla exponencial, fondo profundo y luces puntuales con atenuación física y materiales emisivos.
* **Resultado:** Escenas 3D que cargan instantáneamente en 0ms, con soporte para `prefers-reduced-motion` y rendimiento optimizado a 60 FPS.

---

## 9. Dashboard Mission Control (Atelier Theme)

La interfaz centralizada para el control de la suite (`dashboard.html` / `dashboard.js`):

* **Panel de Superpoderes:** Catálogo en tiempo real de las 164 herramientas y 82 skills cargadas dinámicamente desde el puente local.
* **Monitor de Estado en Vivo:** Badges de conexión WebSocket, conteo de Servidores Web activos y selector de Modo Segundo Plano.
* **Limpieza Ergonómica:** Eliminación definitiva de controles obsoletos (`#zs-folder-btn`), permitiendo una barra de navegación esbelta y moderna sobre cualquier plataforma de IA soportada (DeepSeek, ChatGPT, Gemini, Z.ai, Arena, Claude).

---

## 10. Grafo de Conocimiento y Compresión Arquitectónica (`hermes_code_graph`)

Motor de análisis topológico de código implementado en Python puro:

* **Extracción AST Multilenguaje:** Parsea estructuras sintácticas en Python (`ast.parse`) y JavaScript/TypeScript (regex AST de alta fidelidad), catalogando clases, funciones, métodos e imports.
* **Detección de "God Nodes":** Calcula el grado de acoplamiento de dependencias (in-degree / out-degree) para alertar al agente sobre archivos neurálgicos cuya modificación requiere precaución extrema.
* **Compresión Topológica 70x:** En lugar de leer decenas de archivos con `file_read` saturando el contexto del LLM, genera un mapa de arquitectura completo en menos de 400 tokens.

---

## 11. Planificación Persistente Anti-Amnesia (`hermes_task_plan`)

Módulo de persistencia agéntica en disco local:

* **Arquitectura de 3 Archivos:** Mantiene `task_plan.md` (fases y pasos pendientes/completados), `findings.md` (restricciones arquitectónicas descubiertas) y `progress.md` (bitácora de hitos alcanzados).
* **Resiliencia ante Desconexiones:** Si el navegador se cierra, la red falla o el chat se limpia, el agente lee el estado del plan en disco en 200 tokens y retoma la ejecución sin amnesia ni desvíos.

---

## 12. Automatización Nativa de Escritorio Windows (`hermes_computer_use` UIA)

Evolución del control de escritorio hacia Windows UI Automation:

* **Inspección de Jerarquía de Accesibilidad:** `action: "inspect_tree"` extrae el árbol de controles de ventanas nativas (.exe) asignando referencias estables (`@ref:0`, `@ref:1`).
* **Interacción en Segundo Plano Sin Visión:** Permite clics e inputs directos (`uia_click`, `uia_set_value`) calculando coordenadas geométricas automáticamente sin necesidad de tomar capturas de pantalla ni saturar tokens de visión.

---

## 13. Matriz Resumen de Superpoderes Propios

| Innovación / Superpoder | Archivo / Componente Principal | Rol en el Ecosistema |
| :--- | :--- | :--- |
| **Memoria Tripartita Cifrada** | `%LOCALAPPDATA%\hermes\memory\` | Identidad inmutable, lecciones y hechos persistentes. |
| **Filtro Anti-PII en Vivo** | `hermes_mcp_server.py` | Enmascaramiento dinámico de rutas de usuario. |
| **Auditoría Visual Headless** | `hermes_ui_inspect` | Ojos de la IA, captura 1440px y auto-reparación HTTP 500. |
| **Tracker y Lanzador Web** | `hermes_dev_server` | Inicio instantáneo HTTP y monitoreo de servidores en 2° plano. |
| **Terminal Auto-Cura** | `_tool_terminal` (`hermes_mcp_server.py`) | Corrección de comillas en `cd`, PowerShell y rutas Windows. |
| **Scaffolding Atómico por Lote**| `hermes_batch_scaffold` | Creación de andamiaje en 30ms con pre-flight JSON check. |
| **Knowledge Graph AST 70x** | `hermes_code_graph` | Grafo de dependencias, God Nodes y compresión 70x de contexto. |
| **Planificador Anti-Amnesia** | `hermes_task_plan` | Persistencia en disco de planes, findings y progreso. |
| **Windows UIA Desktop** | `hermes_computer_use` | Árbol UIA con `@ref:N`, clics deterministas sin captura de pantalla. |
| **Pre-Flight Guardian** | `hermes_preflight` | Validación sintáctica previa (Python, JS, JSON) antes de entregar. |
| **Rollback Quirúrgico** | `hermes_checkpoint` | Puntos de restauración rápidos por archivo sin Git ni overhead. |
| **Modo Segundo Plano Infinito** | `core/main.js` + Audio Keep-Alive | Ejecución autónoma sin dormirse al minimizar la ventana. |
| **Skill Three.js Offline** | `skills/creative/threejs-worldclass-offline/` | Escenas 3D inmediatas sin bloqueo de Suspense ni CDN. |
| **Mission Control Dashboard** | `dashboard.html` + `overlay.css` | Panel Atelier de control total, servidores y enjambre. |

---
*Este paper representa el estado del arte y la propiedad intelectual del sistema desarrollado por Jesus Peralta. Todos los derechos de arquitectura y diseño pertenecen al autor.*
