# 03 — Plan de Secciones + Tokens

## Dirección de arte (confirmada)
- **Preset**: [nombre]
- **Paleta**: `#hex` · `#hex` · `#hex` · `#hex` (acento)
- **Tipografía**: Display `[nombre]` [peso] / Body `[nombre]` [peso]
- **Spacing scale**: [valores]
- **Radius**: [valores]
- **Motion curve**: `cubic-bezier(...)`

## Stack técnico
- **Web**: React 18.3+ / Next.js 14.2+ / TypeScript 5.4+ / TailwindCSS 3.4+ / Framer Motion 11+
- **Android**: Kotlin 2.0+ / Compose BOM 2024.09+ / AGP 8.5+ / Hilt / StateFlow
- **Iconografía**: SVG custom, cero librería
- **VFX**: Canvas API / WebGL / partículas

## Estructura de carpetas

```
proyecto/
  src/
    components/
      hero/
      sections/
      ui/
    icons/           ← SVG custom paths, NO librería
    styles/
    lib/
```

## Secciones y componentes

| # | Sección | Componente | Interacciones | Estados |
|---|---|---|---|---|
| 1 | Hero | [nombre] | [lista] | loading/ready/error |
| 2 | ... | ... | ... | ... |

## Tokens CSS (extracto)

```css
:root {
  --color-bg: #...;
  --color-fg: #...;
  --color-accent: #...;
  --font-display: '...', serif;
  --font-body: '...', sans-serif;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 16px;
  --radius-sm: 6px;
  --radius-md: 12px;
  --motion-base: cubic-bezier(...);
}
```
