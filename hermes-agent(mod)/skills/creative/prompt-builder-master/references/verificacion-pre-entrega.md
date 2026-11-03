# Verificación Pre-Entrega (Checklist Extendida)

Antes de entregar el prompt final a AI Studio, valida TODO. Si algo falla, refactoriza el prompt, no la checklist.

## A. Paleta y Color
- [ ] Todos los hex del prompt están declarados en la sección "Dirección de Arte"
- [ ] Hay exactamente 1 color de acento principal (no 3, no 0)
- [ ] Contraste WCAG AAA en texto principal (7:1 mínimo)
- [ ] Contraste WCAG AA en texto secundario (4.5:1 mínimo)
- [ ] No hay neones fuera de paleta

## B. Tipografía
- [ ] Display font declarada (nombre + peso + fallback)
- [ ] Body font declarada (nombre + peso + fallback)
- [ ] Escala tipográfica explícita (ej. 12/14/16/20/24/32/48/64/96)
- [ ] `letter-spacing` definido (no por defecto)
- [ ] `clamp()` o `sp` para responsive, no media queries para font-size

## C. Espaciado y Geometría
- [ ] Spacing scale declarada (múltiplos de 4 u 8)
- [ ] Radius declarado (rango, no valor único)
- [ ] Botones en rango 36–44px web / 40–48dp Android
- [ ] Padding horizontal botones 16–20px
- [ ] Cards: `border-radius` ≤ 24px
- [ ] Grid explícito (12-col web, 4-col mobile)

## D. Componentes
- [ ] Badges/tags con glassmorphism refinado + borde ultrafino + indicador luminoso
- [ ] Íconos: TODOS son SVG custom con `path` propio (verificar que no dice "Lucide", "FontAwesome", "Heroicons")
- [ ] Hero es interactivo (canvas, carrusel 3D, partículas, lookbook) — NO imagen estática
- [ ] Empty States tienen ilustración SVG propia (no placeholder gris)
- [ ] Modales/drawers aíslan visualmente el fondo en estado cerrado

## E. Motion
- [ ] Motion curve declarado (`cubic-bezier(...)`)
- [ ] Duraciones en rango correcto según preset (150–900ms)
- [ ] Animaciones 60fps (transform/opacity, no width/height)
- [ ] Scroll triggers o Framer Motion declarados

## F. Contenido
- [ ] CERO "Lorem ipsum"
- [ ] CERO "Texto aquí" / "[Placeholder]"
- [ ] CERO `<div class="placeholder">`
- [ ] Todo el copy es real, coherente con el nicho
- [ ] CTAs tienen verbo de acción específico (no "Enviar", sí "Reservar mi lugar")

## G. Responsive
- [ ] Validado conceptualmente a 360px (móvil)
- [ ] Validado conceptualmente a 768px (tablet)
- [ ] Validado conceptualmente a 1440px (desktop)
- [ ] Validado conceptualmente a 1920px (ultra-wide)
- [ ] Container max-width declarado

## H. Arquitectura y Código
- [ ] Estructura de carpetas explícita (no "típica de React")
- [ ] Cada sección = componente/archivo propio
- [ ] TypeScript estricto (no `any`)
- [ ] Stack con versiones exactas (React 18.3+, Next 14.2+, etc.)
- [ ] Android: AGP 8.5+, Kotlin 2.0+, Compose BOM 2024.09+

## I. Anti-genericidad
- [ ] NO es Cyberpunk por defecto
- [ ] NO es Dark Mode por defecto (salvo justificación)
- [ ] NO usa "gradient purple to blue" (el cliché de IA)
- [ ] NO usa Inter por defecto (a menos que el preset lo exija)
- [ ] NO usa bordes redondeados exagerados en todo
- [ ] Al menos UN detalle artesanal no pedido (una animación única, un patrón custom, un microdetalle)

## J. Anti-Hallucination
- [ ] Todas las librerías mencionadas existen y tienen esa API
- [ ] Todos los endpoints/APIs mencionados son reales o marcados como "mock"
- [ ] No hay features de AI Studio inventadas

---

## Scoring

Cada sección A–I vale 10 pts. Total 100.
- 90–100: Listo para entregar
- 70–89: Refactorizar las secciones falladas
- <70: Volver a Fase 1
