---
name: cloudflare-tunnel
description: Expone un servidor local (Vite, Next.js, Flask) a internet usando Cloudflare Tunnel. Devuelve una URL pública HTTPS sin abrir puertos.
version: 1.0.0
author: GHS Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [cloudflare, tunnel, red, internet, deploy, url, vite, nextjs]
triggers:
  - cloudflare
  - tunnel
  - tunel
  - exponer
  - url publica
  - compartir
  - deploy
  - abrir puerto
---

# Cloudflare Tunnel

## Cuando usar
- El usuario quiere ver su proyecto local (localhost:5173) desde el celular.
- El usuario quiere compartir una URL pública temporal.
- El usuario quiere probar webhooks desde internet.

## Cómo usar (Windows)
1. El binario de Cloudflare esta en: `C:\Users\USER\Downloads\Cloudflare-heramienta\cloudflared.exe`
2. Para crear un tunel rapido a un puerto local, usa `hermes_terminal` con:
   `C:\Users\USER\Downloads\Cloudflare-heramienta\cloudflared.exe tunnel --url http://localhost:[PUERTO]`
   (Asegurate de usar `is_daemon: true` para que no bloquee la terminal).
3. La salida del comando incluira una URL aleatoria (ej: `https://xxx.trycloudflare.com`).
4. Extrae la URL y entregasela al usuario.

## Reglas
- NUNCA reveles la URL a terceros sin el consentimiento del usuario.
- El tunel se cierra cuando el proceso finaliza o si el usuario ejecuta `4_stop_tunnel.bat`.
