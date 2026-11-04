# 🛡️ PROTOCOLOS DE SEGURIDAD Y ESTABILIDAD — DRAMAPE

> **Objetivo primordial:** Garantizar disponibilidad ininterrumpida (99.9% uptime), cero rupturas en producción, reproducción fluida de más de 42,000 títulos y protección estricta contra regresiones o caídas de servidor.

Este documento establece las **reglas operativas y directrices de ingeniería obligatorias** que deben cumplirse antes, durante y después de cualquier modificación, optimización o despliegue en la plataforma DramaPe.

---

## 📌 ÍNDICE
1. [Regla de Oro: Verificación Pre-Flight Local](#1-regla-de-oro-verificación-pre-flight-local)
2. [Protocolo de Integridad de Código y Arquitectura](#2-protocolo-de-integridad-de-código-y-arquitectura)
3. [Protocolo Upstream y Prevención de Timeouts (Fail-Fast)](#3-protocolo-upstream-y-prevención-de-timeouts-fail-fast)
4. [Protocolo de Detección y Cuarentena de Títulos Caídos](#4-protocolo-de-detección-y-cuarentena-de-títulos-caídos)
5. [Protocolo de Monitoreo e Intercepción en Panel Admin](#5-protocolo-de-monitoreo-e-intercepción-en-panel-admin)
6. [Protocolo de Despliegues y Rollback Instantáneo](#6-protocolo-de-despliegues-y-rollback-instantáneo)
7. [Protocolo de Experiencia de Usuario (UX) y Publicidad](#7-protocolo-de-experiencia-de-usuario-ux-y-publicidad)
8. [Protocolo de Ingesta, Deduplicación y Expansión de Catálogos](#8-protocolo-de-ingesta-deduplicación-y-expansión-de-catálogos)

---

## 1. Regla de Oro: Verificación Pre-Flight Local

**Bajo ninguna circunstancia se sube código a `main` sin haber superado las dos pruebas locales de compilación:**

### A. Backend Python (`server.py`)
Antes de cada commit, verificar que la sintaxis y tipos del servidor estén impecables:
```bash
python -m py_compile server.py
```
*El código de salida debe ser estrictamente `0` (sin advertencias ni errores de sintaxis).*

### B. Frontend Next.js (`web-next`)
Ejecutar la compilación de producción en local para validar que no haya referencias rotas, errores de JSX, llamadas inválidas a hooks o incompatibilidades de paquetes:
```bash
cd web-next
npm run build
```
*Todas las rutas (`/`, `/buscar`, `/seccion/[key]`, `/titulo/[slug]`, `/ver/[slug]/[ep]`, `/admin`) deben compilar al 100% con estatus verde.*

### C. Paridad de Dependencias y Entorno de Producción (Docker / Railway)
Cualquier módulo, librería o `import` nuevo en `server.py` o en el frontend DEBE estar explícitamente aprovisionado para el contenedor de producción:
1. **Python (`requirements.txt` y `Dockerfile`):**
   - Verificar que figure en `requirements.txt`.
   - Si requiere dependencias nativas del sistema operativo C/C++, incluir el paquete binario de Debian en el `apt-get install` de `Dockerfile` (ejemplo: `python3-cryptography`).
2. **Programación Defensiva en Backend (`server.py`):**
   - Todo import de librerías externas o no-estándar debe estar encapsulado en bloques `try/except` para garantizar que un módulo faltante NUNCA impida el arranque del servidor en el puerto 8090 ni tumbe la plataforma.
3. **Node.js (`package.json`):**
   - Cualquier dependencia de Node.js debe estar en `web-next/package.json` y compilar sin `--force`.

---

## 2. Protocolo de Integridad de Código y Arquitectura

1. **Variables y Constantes Globales Críticas:**
   - Jamás eliminar ni redefinir fuera de su contexto variables como `ROOT`, `PORT`, `BUILD_ID`, `SESSION`, `CATALOG` o `UPSTREAM`.
   - Si se edita una sección de código adyacente a una función crítica, validar que las sentencias `return`, `try/except` y los cierres de bloque permanezcan intactos.

2. **Edición Segura de Archivos:**
   - Siempre revisar el `git diff` antes de confirmar cualquier modificación para verificar que únicamente se cambiaron las líneas previstas.
   - En archivos grandes (`server.py`, `Player.js`), usar reemplazos acotados por rango de líneas y validar el contexto previo y posterior.

3. **Compatibilidad Server-Side y Client-Side:**
   - En Next.js (`lib/api.js`), las llamadas `fetch` deben contemplar tanto ejecución en servidor (Node) como en navegador:
     ```javascript
     const base = typeof window === 'undefined' ? PROXY : '';
     const res = await fetch(`${base}/api/...`);
     ```
   - Evitar rutas absolutas con `localhost` en el navegador del usuario final.

---

## 3. Protocolo Upstream y Prevención de Timeouts (Fail-Fast)

El incidente reportado con el error `PLAYBACK_TIMEOUT` (HTTP 504) se originó porque el servidor upstream tardaba más de 12 segundos en responder, lo que disparaba el temporizador de seguridad del reproductor.

### Reglas para Requests a Servidores Proveedores:
1. **Timeouts Estrictos (Fail-Fast):**
   - Ninguna llamada HTTP saliente a un servidor upstream debe superar los **2.5 segundos** de timeout:
     ```python
     requests.get(url, headers=hdrs, timeout=2.0)
     ```
   - Si un servidor no responde en 2 segundos, se descarta de inmediato para no congelar el hilo ni hacer esperar al usuario.

2. **Prioridad de Dominios Canónicos:**
   - Usar siempre el dominio directo y más rápido como primario (`https://esdramia.com`) antes que subdominios secundarios que puedan presentar latencia (`https://free.esdramia.com`).

3. **Sin Reintentos Infinitos ante 404:**
   - Si un upstream devuelve `404 Not Found`, significa que el video fue eliminado en el origen. No se reintenta: el backend responde de inmediato con `404` en menos de 2 segundos.
   - En `Player.js`, ante un código `404`, el reproductor cancela el temporizador de timeout de inmediato y muestra un mensaje limpio al usuario en lugar de dejar el spinner dando vueltas 12 segundos.

---

## 4. Protocolo de Detección y Cuarentena de Títulos Caídos

Cuando un proveedor externo elimina un video o drama de sus servidores (como ocurrió con `juega-al-fuego-con-el-jefe-de-la-mafia`):

1. **Cuarentena Inmediata en `BROKEN_SLUGS`:**
   - Se registra el slug en la lista negra preventiva de `server.py`:
     ```python
     BROKEN_SLUGS = {
         'juega-al-fuego-con-el-jefe-de-la-mafia',
     }
     ```
   - Al estar en `BROKEN_SLUGS`, el título queda automáticamente excluido de:
     - El catálogo general (`CATALOG`).
     - El índice de búsqueda global (`get_search_index()`).
     - Las recomendaciones y estantes.

2. **Limpieza de Archivos Estáticos:**
   - Remover el título de `catalog_full.json` y de `data/dramas_shelves.json` para que no se muestre en los carruseles visuales de la plataforma.

---

## 5. Protocolo de Monitoreo e Intercepción en Panel Admin

DramaPe cuenta con un sistema centinela automático conectado a Supabase:

1. **Captura Silenciosa de Errores:**
   - Todo incidente de reproducción (HTTP 404, 500, 502, timeouts o fallas HLS) es interceptado por `Player.js` y transmitido vía `navigator.sendBeacon` al endpoint `/api/analytics/error-report`.
   - Se guarda con metadatos completos: `slug`, `dorama`, `episodio`, `error_type`, `http_status`, `dispositivo` y `user_agent`.

2. **Revisión en el Panel Admin (`/admin`):**
   - La pestaña **"Errores Reportados"** muestra los incidentes en tiempo real con un botón **"Probar ▶"** para reproducir directamente y comprobar si la falla persiste.
   - Una vez corregida la causa raíz o puesto el drama en cuarentena, marcar como **"Resuelto"** para mantener limpio el panel.

---

## 6. Protocolo de Despliegues y Rollback Instantáneo

1. **Commits Claros y Atómicos:**
   - Cada commit debe resolver una tarea específica y tener un mensaje descriptivo siguiendo convención convencional commits (`feat:`, `fix:`, `refactor:`).
   - Ejemplo: `fix: optimize stream resolution timeouts and exclude deleted upstream titles`.

2. **Revisión de `git status` y Archivos Residuales:**
   - Antes de commitear, eliminar scripts temporales (`scratch/test_*.py`) para no ensuciar el repositorio.

3. **Protocolo de Rollback de Emergencia:**
   - Si tras un despliegue en Railway o Vercel se detecta una anomalía crítica imprevista:
     ```bash
     # Ver el último commit estable
     git log -n 3 --oneline
     # Revertir de inmediato al commit funcional
     git revert HEAD --no-edit
     git push origin main
     ```
   - Este procedimiento toma menos de 60 segundos y restablece la plataforma a su estado operativo anterior.

---

## 7. Protocolo de Experiencia de Usuario (UX) y Publicidad

1. **Búsqueda sin Redirecciones Molestas:**
   - La búsqueda cruzada entre secciones debe mantener al usuario en su sección actual.
   - Al pulsar cualquier resultado de otra sección, se abre el modal de episodios directamente en la página activa.

2. **Políticas de Publicidad no Intrusiva:**
   - Banners y anuncios deben respetar las dimensiones adaptadas para móviles (máximo 320x50 en celulares, banners 728x90 solo en escritorio).
   - En la página de inicio (`/`) se restringe la publicidad invasiva para mantener una primera impresión impecable y rápida.
   - Los popunders deben respetar el intervalo de frecuencia configurado (1 vez cada 24 horas por usuario).

---

## 8. Protocolo de Ingesta, Deduplicación y Expansión de Catálogos

Para garantizar la estabilidad del sistema al scrapear o incorporar nuevos proveedores masivos (como HotPlot, DramaTube o DramaVibe):

1. **Regla de Pertenencia de Sección Única (Cero Secciones Fantasma):**
   - Todo título nuevo debe integrarse y etiquetarse exclusivamente bajo una de las secciones oficiales de DramaPe (`hotdrama`, `dramatube`, `dramavibe`, `dramashorts`, `drama`).
   - Queda terminantemente prohibido crear secciones satélite, archivos paralelos no coordinados (`hotplot.json`) o insignias que confundan al usuario en el frontend o en las pestañas del buscador.

2. **Filtro Estricto de Calidad ($\ge 9$ Episodios):**
   - Todo título con menos de 9 capítulos queda descartado de forma estricta e inmediata durante la ingesta.
   - Solo se indexan series completas en español (dobladas o subtituladas), con metadatos completos (título, sinopsis, póster HD y mapa de capítulos con duraciones).

3. **Deduplicación Preventiva Obligatoria:**
   - Antes de escribir en `data/hotdrama.json`, validar contra el conjunto de slugs y títulos normalizados ya existentes:
     ```python
     def _norm(t):
         raw = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode('utf-8').lower()
         nt = re.sub(r'\(dubbed\)|\[dubbed\]|\(espanol\)|\[espanol\]|\(es\)|\(doblado\)|\[doblado\]', '', raw)
         return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', nt)).strip()
     ```
   - Si un título ya existe en el catálogo, se prioriza la versión con mayor resolución (ej. stream 1080p verificado sobre fuentes estándar) y se descarta el duplicado para evitar tarjetas gemelas en la búsqueda.

4. **Compatibilidad de Slugs y Enrutamiento Frontend:**
   - Cada prefijo de proveedor nuevo (ej. `hp-`) debe estar explícitamente contemplado en la cadena completa de resolución:
     - `web-next/app/api/title/[slug]/route.js`
     - `web-next/lib/api.js` (`getTitle`)
     - Endpoints del backend en `server.py` (`/api/hotdrama/item`)

5. **Rate-Limiting de Cortesía y Resumibilidad en Ingestas Masivas:**
   - En extracciones de 100 o más títulos, intercalar pausas preventivas (0.2s - 0.35s) entre solicitudes a APIs de origen para no disparar mitigaciones de Cloudflare ni saturar la sesión autenticada.
   - Guardar checkpoints de respaldo cada 50 o 100 títulos para garantizar persistencia y evitar pérdidas en caso de interrupciones de red.

6. **Pre-Flight Post-Ingesta:**
   - Inmediatamente después de una ingesta masiva:
     1. Compilar `python -m py_compile server.py`
     2. Ejecutar `npm run build` en `web-next`
     3. Validar con prueba de reproducción HTTP directa en al menos 3 episodios aleatorios de la nueva remesa.

7. **Validación Obligatoria de Episodios Reproducibles (Playable & Duration Check):**
   - Jamás indexar series basándose únicamente en el número nominal reportado en sitemaps o metadatos (`numberOfEpisodes`).
   - Todo capítulo indexado debe tener `duration > 0` real y el proveedor debe tener el medio disponible (`"playable": true` / `duration_ms > 0`).
   - El Episodio 1 debe ser estrictamente reproducible. Si el origen solo aloja un trailer/teaser (ej. solo el cap 24 como muestra y los caps 1 al 23 en duración 0), la serie queda vetada de inmediato para evitar errores 404 al usuario.

8. **Enrutamiento Obligatorio de Segmentos por Proxy y Resiliencia en Bitrate Alto (1080p):**
   - Todos los segmentos de video (incluidos CDNs externos como HotPlot / BunnyCDN) deben enrutarse a través de `/proxy/seg?t=` para aprovechar el pool de conexiones del servidor y la caché en disco `_segcache` (reduciendo la latencia de 25s a 80ms).
   - En el cliente web (`Player.js`), `fragLoadingMaxRetryTimeout` y `fragLoadingTimeOut` deben ser de al menos 30 segundos (30,000 ms) para evitar que Hls.js cancele en bucle fragmentos pesados de 1080p (2.75 Mbps / 3 MB).
   - El watchdog de reproducción debe aplazarse (`deferWatchdog`) con cada evento `FRAG_LOADING` y `FRAG_LOADED`, y cancelarse de inmediato con `FRAG_BUFFERED`.

9. **Deprecación Upstream Inmediata y Cero Metadatos Basura (Anti-Adcash / 410 Protocol):**
   - Si un título es eliminado en origen (retorna 404 definitivo como `la-esposa-del-padrino-un-precio-que-no-puedes-pagar`), debe incluirse inmediatamente en `BROKEN_SLUGS`, purgarse de `data/dramas_shelves.json` y `catalog_full.json`, y responder con código HTTP 410 Gone para que aplicaciones y clientes limpien su historial local.
   - Ningún metadato publicitario o de prueba (ej. `adcash`) debe ingresar a los listados de carruseles ni bases de datos activas.
   - **Métricas Reales del Catálogo DramaPe:**
     - El ecosistema consolidado cuenta con **más de 8,160 dramas activos** y más de 100,000 capítulos en producción:
       - Catálogo Central (Esdramia / Suikora): 1,577 títulos (51,025 episodios)
       - DramaVibe: 4,342 títulos
       - DramaTube: 1,128 títulos
       - HotDrama / HotPlot: 1,115 títulos
       - DramaShorts: 119 títulos
     - El pie de página y los contadores públicos deben reportar la métrica global consolidada (`8,160+ Dramas Activos`), no solo el subconjunto de la página de inicio.

---

*Última actualización: 27 de Octubre 2026 — Antigravity Engineering & DramaPe Core Team.*

