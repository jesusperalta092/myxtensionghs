# SPDX-License-Identifier: GPL-3.0-or-later
"""
browser_operator.py - Autonomous Browser & DOM Controller for GHS
Controls live browser sessions (Chrome/Edge) via Chrome DevTools Protocol (CDP)
or web automation to click, fill forms, read tables, and navigate any website
(university portals, e-commerce dashboards, company intranets).
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Optional

CDP_DEFAULT_PORT = 9222
CDP_BASE_URL = f"http://localhost:{CDP_DEFAULT_PORT}"


class BrowserOperator:
    def __init__(self, port: int = CDP_DEFAULT_PORT):
        self.port = port
        self.base_url = f"http://localhost:{port}"

    def is_browser_connected(self) -> bool:
        """Checks if Chrome/Edge is running with remote debugging enabled."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/version", headers={"User-Agent": "GHS/1.5.5"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return bool(data.get("Browser"))
        except Exception:
            return False

    def get_open_tabs(self) -> List[Dict[str, Any]]:
        """Lists all open tabs in the browser."""
        try:
            req = urllib.request.Request(f"{self.base_url}/json/list", headers={"User-Agent": "GHS/1.5.5"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                tabs = json.loads(resp.read().decode("utf-8"))
                return [
                    {
                        "id": t.get("id"),
                        "title": t.get("title"),
                        "url": t.get("url"),
                        "type": t.get("type"),
                        "ws_url": t.get("webSocketDebuggerUrl")
                    }
                    for t in tabs if t.get("type") == "page"
                ]
        except Exception as e:
            return []

    def open_url(self, url: str) -> Dict[str, Any]:
        """Opens a new tab with the target URL."""
        if not self.is_browser_connected():
            return {
                "ok": False,
                "error": "El navegador no está conectado en el puerto CDP (9222).",
                "hint": "Inicia Chrome con: chrome.exe --remote-debugging-port=9222 para permitir control autónomo."
            }
        try:
            encoded_url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=")
            req = urllib.request.Request(f"{self.base_url}/json/new?{encoded_url}", method="PUT")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                tab = json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "tab_id": tab.get("id"), "url": tab.get("url"), "title": tab.get("title")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def execute_js_in_tab(self, js_code: str, tab_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes JavaScript in an open tab (clicks elements, reads text, extracts tables).
        Uses WebSocket connection to CDP Runtime.evaluate.
        """
        import websocket  # type: ignore
        tabs = self.get_open_tabs()
        if not tabs:
            return {"ok": False, "error": "No hay pestañas abiertas accesibles."}

        target_tab = None
        if tab_id:
            target_tab = next((t for t in tabs if t["id"] == tab_id), None)
        if not target_tab:
            target_tab = tabs[0]  # First active page tab

        ws_url = target_tab.get("ws_url")
        if not ws_url:
            return {"ok": False, "error": "La pestaña no expone WebSocket Debugger URL."}

        try:
            ws = websocket.create_connection(ws_url, timeout=5)
            call_id = 101
            payload = json.dumps({
                "id": call_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js_code,
                    "returnByValue": True,
                    "awaitPromise": True
                }
            })
            ws.send(payload)
            resp = json.loads(ws.recv())
            ws.close()

            result = resp.get("result", {}).get("result", {})
            return {
                "ok": True,
                "tab_title": target_tab.get("title"),
                "value": result.get("value"),
                "type": result.get("type"),
                "description": result.get("description")
            }
        except Exception as e:
            return {"ok": False, "error": f"Error comunicando con CDP: {e}"}

    def click_selector(self, selector: str) -> Dict[str, Any]:
        """Clicks an element matching the CSS selector."""
        js = f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return {{ found: false, error: 'Elemento no encontrado: ' + {json.dumps(selector)} }};
            el.scrollIntoView({{ block: 'center' }});
            el.click();
            return {{ found: true, text: (el.innerText || el.value || '').slice(0, 100) }};
        }})()
        """
        return self.execute_js_in_tab(js)

    def fill_input(self, selector: str, value: str) -> Dict[str, Any]:
        """Fills an input field with the specified value."""
        js = f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return {{ found: false, error: 'Campo no encontrado: ' + {json.dumps(selector)} }};
            el.focus();
            el.value = {json.dumps(value)};
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return {{ found: true, current_value: el.value }};
        }})()
        """
        return self.execute_js_in_tab(js)

    def extract_table(self, table_selector: str = "table") -> Dict[str, Any]:
        """Extracts tabular data from any HTML table into a clean JSON structure."""
        js = f"""
        (() => {{
            const table = document.querySelector({json.dumps(table_selector)});
            if (!table) return {{ found: false, error: 'Tabla no encontrada: ' + {json.dumps(table_selector)} }};
            const rows = Array.from(table.querySelectorAll('tr'));
            const data = [];
            let headers = [];
            const ths = table.querySelectorAll('th');
            if (ths.length > 0) {{
                headers = Array.from(ths).map(th => th.innerText.trim());
            }}
            for (const r of rows) {{
                const cells = Array.from(r.querySelectorAll('td')).map(td => td.innerText.trim());
                if (cells.length > 0) data.push(cells);
            }}
            return {{ found: true, headers, row_count: data.length, data: data.slice(0, 100) }};
        }})()
        """
        return self.execute_js_in_tab(js)


# Singleton instance
browser_op = BrowserOperator()
