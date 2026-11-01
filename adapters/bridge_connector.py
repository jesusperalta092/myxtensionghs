# SPDX-License-Identifier: GPL-3.0-or-later
"""
adapters/bridge_connector.py - Async Client Connector to GHS Bridge WebSocket (ws://127.0.0.1:17613)
Handles real-time message exchange between external agents (like Hermes) and the GHS browser bridge.
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional

try:
    import websockets
except ImportError:
    websockets = None


class BridgeConnector:
    def __init__(self, host: str = "127.0.0.1", port: int = 17613):
        self.uri = f"ws://{host}:{port}"
        self.ws = None
        self.is_connected = False
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def connect(self, timeout: float = 5.0) -> bool:
        """Establish WebSocket connection to the local bridge."""
        if websockets is None:
            return False
        try:
            self.ws = await asyncio.wait_for(websockets.connect(self.uri, max_size=16 * 1024 * 1024), timeout=timeout)
            self.is_connected = True
            asyncio.create_task(self._listen_loop())
            return True
        except Exception:
            self.is_connected = False
            return False

    async def _listen_loop(self):
        """Continuously listen for responses and broadcasts from bridge.py."""
        try:
            async for raw in self.ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue

                rid = data.get("id")
                if rid and rid in self._pending_requests:
                    fut = self._pending_requests.pop(rid)
                    if not fut.done():
                        fut.set_result(data)
        except Exception:
            pass
        finally:
            self.is_connected = False

    async def call_tool(self, name: str, arguments: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        """Execute a tool via bridge.py."""
        if not self.is_connected:
            ok = await self.connect()
            if not ok:
                return {"ok": False, "error": "Could not connect to GHS bridge (ws://127.0.0.1:17613)"}

        rid = f"req_{int(time.time() * 1000)}"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending_requests[rid] = fut

        payload = {
            "type": "call_tool",
            "id": rid,
            "name": name,
            "arguments": arguments,
            "timeout": timeout
        }

        try:
            await self.ws.send(json.dumps(payload))
            res = await asyncio.wait_for(fut, timeout=timeout + 2.0)
            return res
        except asyncio.TimeoutError:
            self._pending_requests.pop(rid, None)
            return {"ok": False, "error": f"Tool '{name}' timed out after {timeout}s"}
        except Exception as e:
            self._pending_requests.pop(rid, None)
            return {"ok": False, "error": str(e)}

    async def close(self):
        if self.ws:
            await self.ws.close()
            self.is_connected = False


# Singleton helper
connector = BridgeConnector()
