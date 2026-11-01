# 🗺️ Mapa de Arquitectura y Navegación de GHS

Bienvenido al mapa maestro del sistema. Este archivo documenta la estructura modular del proyecto para que cualquier función, herramienta o servidor se localice en 1 segundo.

---

## 📌 Puertos de Red y Servicios

| Puerto | Protocolo | Servicio | Descripción |
| :--- | :---: | :--- | :--- |
| **`17613`** | `ws://` | **GHS Bridge Core** | Hub principal de comunicación WebSocket entre la extensión y las herramientas locales. |
| **`17614`** | `http://` | **OpenAI API Adapter (Hermes)** | Servidor HTTP REST compatible con OpenAI (`/v1/chat/completions`, `/v1/models`). |
| **`9876`** | `tcp://` | **Blender 3D Addon** | Socket de ejecución directa de scripts Python en Blender 5.1+. |

---

## 📂 Estructura de Directorios

```
c:\Users\USER\Downloads\GHS-Free-1.5.5\
│
├── ARCH_MAP.md                     # 🗺️ Este mapa arquitectónico
├── bridge.py                       # ⚡ Núcleo WebSocket del puente (puerto 17613)
├── config.json                     # ⚙️ Configuración de servidores MCP activos
├── hermes_mcp_server.py            # ☤ Servidor MCP de Superpoderes Hermes (OS, terminal, archivos, memoria)
├── start.bat                       # 🚀 Lanzador de arranque en 1 clic (Bridge + OpenAI Server)
├── run_hermes.bat                  # ☤ Lanzador directo de Hermes Agent conectado a GHS
│
├── hermes-agent(mod)/              # ☤ SUITE COMPLETA DE HERMES AGENT (Nous Research)
│   ├── cli.py                      # CLI interactivo de Hermes con soporte de herramientas
│   ├── run_agent.py                # Ejecutor autónomo de Hermes
│   ├── agent/                      # Lógica interna y bucles cognitivos de Hermes
│   └── tools/                      # Herramientas nativas (terminal, archivos, web, cua-driver)
│
├── adapters/                       # 🔌 ADAPTADORES DE INTEGRACIÓN EXTERNA
│   ├── __init__.py
│   ├── openai_server.py            # Servidor API compatible con OpenAI (puerto 17614)
│   └── bridge_connector.py         # Cliente WebSocket hacia bridge.py
│
├── ghs-extension/           # 🧩 EXTENSIÓN DE NAVEGADOR
│   ├── manifest.json               # Manifiesto V3 de Chrome/Edge
│   ├── dashboard.html              # 🎛️ Centro de Mando a pantalla completa
│   ├── dashboard.css               # Estilos oscuros modernos del Centro de Mando
│   ├── dashboard.js                # Controlador interactivo de la suite
│   ├── overlay.css                 # Barra flotante sobre el chat web
│   ├── background.js               # Service worker de fondo
│   ├── core/                       # Lógica agnóstica de proveedor
│   │   ├── config.js               # System prompts, roles y notas de herramientas
│   │   ├── main.js                 # Inyector de comandos y DOM en chats
│   │   └── parser.js               # Analizador de comandos JSON y ###LUA###
│   └── providers/                  # Adaptadores por sitio web de IA
│       ├── deepseek.js             # Integración con chat.deepseek.com
│       ├── chatgpt.js              # Integración con chatgpt.com
│       ├── gemini.js               # Integración con gemini.google.com
│       └── kimi.js                 # Integración con kimi.ai
│
├── blender_master_tools.py         # 🎨 Herramientas 3D PBR y generación de equipo
├── agent_brain.py                  # 🧠 Memoria permanente y lecciones aprendidas
├── video_to_playbook.py            # 📹 Extractor de Procedimientos (SOP) desde video/voz
├── media_transcriber.py            # 🎙️ Transcriptor de audio con Whisper
│
└── tests/                          # 🧪 PRUEBAS Y CLIENTES DE SIMULACIÓN
    ├── __init__.py
    └── test_hermes_call.py         # Cliente de prueba simulando Hermes Agent
```

---

## ⚡ Guía Rápida de Modificación

* **¿Quieres agregar una nueva herramienta o superpoder?**
  Edita `config.json` para declarar el comando o añade la función en `blender_master_tools.py` / `bridge.py`.
* **¿Quieres cambiar lo que la IA sabe al iniciar?**
  Edita la función `buildSystemPrompt()` en `ghs-extension/core/config.js`.
* **¿Quieres ajustar la interfaz del Centro de Mando?**
  Edita `ghs-extension/dashboard.html` y `dashboard.css`.
* **¿Quieres usar Hermes Agent u otro framework con tu IA gratis?**
  Apunta tu script a `http://127.0.0.1:17614/v1` con cualquier API Key.
