# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_hermes_mcp.py - Automated Test for hermes_mcp_server.py
Verifies stdio JSON-RPC MCP initialization, tool discovery, and tool execution.
"""

import sys
import os
import json
import subprocess
import time

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_SCRIPT = os.path.join(ROOT, "hermes_mcp_server.py")


def run_tests():
    print("=" * 60)
    print("  🧪 PRUEBA AUTOMATIZADA: hermes_mcp_server.py (MCP Stdio)")
    print("=" * 60)

    proc = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    req_id = 1

    def send_recv(method, params=None):
        nonlocal req_id
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {}
        }
        req_id += 1
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        if not resp_line:
            raise RuntimeError("Server closed pipe unexpectedly")
        return json.loads(resp_line)

    try:
        # 1. Initialize
        print("\n[1/4] Probando 'initialize'...")
        init_res = send_recv("initialize")
        assert "result" in init_res, f"Fallo initialize: {init_res}"
        server_info = init_res["result"]["serverInfo"]
        print(f"  ✅ Inicialización correcta: {server_info['name']} v{server_info['version']}")

        # 2. Tools List
        print("\n[2/4] Probando 'tools/list'...")
        tools_res = send_recv("tools/list")
        assert "result" in tools_res, f"Fallo tools/list: {tools_res}"
        tools = tools_res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        print(f"  ✅ Herramientas detectadas ({len(tools)}): {', '.join(tool_names)}")
        assert "hermes_computer_use" in tool_names
        assert "hermes_terminal" in tool_names
        assert "hermes_memory" in tool_names

        # 3. Call hermes_terminal
        print("\n[3/4] Probando ejecución de comando con 'hermes_terminal'...")
        term_res = send_recv("tools/call", {
            "name": "hermes_terminal",
            "arguments": {"command": "echo HERMES_MCP_OK"}
        })
        assert "result" in term_res, f"Fallo terminal: {term_res}"
        text = term_res["result"]["content"][0]["text"]
        print(f"  ✅ Salida del comando:\n{text}")
        assert "HERMES_MCP_OK" in text

        # 4. Call hermes_computer_use (list_windows)
        print("\n[4/4] Probando 'hermes_computer_use' (list_windows)...")
        win_res = send_recv("tools/call", {
            "name": "hermes_computer_use",
            "arguments": {"action": "list_windows"}
        })
        assert "result" in win_res, f"Fallo list_windows: {win_res}"
        win_text = win_res["result"]["content"][0]["text"]
        print(f"  ✅ Ventanas detectadas:\n{win_text[:300]}...")

        # 5. Call hermes_skills_list
        print("\n[5/6] Probando 'hermes_skills_list' (76 Habilidades Nativas de Hermes)...")
        skills_res = send_recv("tools/call", {
            "name": "hermes_skills_list",
            "arguments": {}
        })
        assert "result" in skills_res, f"Fallo hermes_skills_list: {skills_res}"
        skills_raw = skills_res["result"]["content"][0]["text"]
        assert "skills" in skills_raw or "count" in skills_raw
        print(f"  ✅ Skills cargadas exitosamente (longitud: {len(skills_raw)} chars)")

        # 6. Call hermes_skill_view
        print("\n[6/6] Probando 'hermes_skill_view' (systematic-debugging)...")
        view_res = send_recv("tools/call", {
            "name": "hermes_skill_view",
            "arguments": {"name": "systematic-debugging"}
        })
        assert "result" in view_res, f"Fallo hermes_skill_view: {view_res}"
        view_raw = view_res["result"]["content"][0]["text"]
        assert "systematic-debugging" in view_raw
        print(f"  ✅ Procedimiento de Skill cargado con éxito!")

        print("\n" + "=" * 60)
        print("  🎉 TODAS LAS PRUEBAS DE HERMES MCP SERVER PASARON EXITOSAMENTE (100%)")
        print("=" * 60)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    run_tests()
