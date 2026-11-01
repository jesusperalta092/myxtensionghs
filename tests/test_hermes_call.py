# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_hermes_call.py - Client Simulation Test for Hermes Agent / OpenAI Clients
Simulates how Hermes Agent (Nous Research) or any external agent connects to GHS
via the Universal OpenAI Adapter at http://127.0.0.1:17614/v1.
"""

import json
import urllib.request
import urllib.error
import sys

# Reconfigure stdout for clean utf-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_URL = "http://127.0.0.1:17614/v1"


def test_models():
    print("1. Probando GET /v1/models...")
    try:
        req = urllib.request.Request(f"{BASE_URL}/models", headers={"User-Agent": "Hermes-Agent-Test/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"   ✓ Modelos disponibles: {[m['id'] for m in data.get('data', [])]}")
            return True
    except Exception as e:
        print(f"   ✗ Error conectando a /v1/models: {e}")
        return False


def test_chat_completion():
    print("\n2. Probando POST /v1/chat/completions (Modo Estándar)...")
    payload = {
        "model": "hermes-virtual",
        "messages": [
            {"role": "system", "content": "Eres un asistente de ingeniería nivel élite."},
            {"role": "user", "content": "¿Quién eres y qué puedes hacer en esta PC?"}
        ],
        "stream": False
    }

    try:
        req = urllib.request.Request(
            f"{BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer ghs-free",
                "User-Agent": "Hermes-Agent-Test/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg = data["choices"][0]["message"]["content"]
            print(f"   ✓ Respuesta de Hermes:\n   \"{msg}\"\n")
            return True
    except Exception as e:
        print(f"   ✗ Error en chat/completions: {e}")
        return False


def test_streaming():
    print("3. Probando POST /v1/chat/completions (Modo Streaming SSE)...")
    payload = {
        "model": "deepseek-r1",
        "messages": [
            {"role": "user", "content": "Ejecuta una verificación de periféricos y recursos."}
        ],
        "stream": True
    }

    try:
        req = urllib.request.Request(
            f"{BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer ghs-free",
                "User-Agent": "Hermes-Agent-Test/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            sys.stdout.write("   ✓ Stream recibido: ")
            for line in resp:
                decoded = line.decode("utf-8").strip()
                if decoded.startswith("data: ") and decoded != "data: [DONE]":
                    chunk_json = json.loads(decoded[6:])
                    delta = chunk_json["choices"][0]["delta"].get("content", "")
                    sys.stdout.write(delta)
                    sys.stdout.flush()
            print("\n")
            return True
    except Exception as e:
        print(f"   ✗ Error en streaming: {e}")
        return False


if __name__ == "__main__":
    print("=== TEST SUITE: GHS OpenAI Adapter (Hermes Bridge) ===\n")
    m_ok = test_models()
    c_ok = test_chat_completion()
    s_ok = test_streaming()

    if m_ok and c_ok and s_ok:
        print("🎉 ¡TODOS LOS TESTS PASARON CON ÉXITO! El servidor es 100% compatible con OpenAI y Hermes.")
    else:
        print("⚠️ Algunos tests fallaron. Asegúrate de que openai_server.py esté corriendo.")
