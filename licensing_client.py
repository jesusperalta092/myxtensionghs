# SPDX-License-Identifier: GPL-3.0-or-later
"""
licensing_client.py
══════════════════════════════════════════════════════════════════════════════
GHS HARDWARE-ID & REMOTE LICENSE SENTINEL
Generación de huella digital de máquina y guardia de verificación remota.
══════════════════════════════════════════════════════════════════════════════
"""
import os
import sys
import time
import json
import hashlib
import threading
import subprocess
from typing import Tuple, Optional

CLOUD_VERIFY_URL = os.environ.get("GHS_LICENSE_SERVER", "https://ghs-cloud-backend.onrender.com")
HWID_CACHE_FILE = os.path.join(os.path.expanduser("~"), ".ghs_hwid")


def get_hardware_id() -> str:
    """
    Obtiene una huella digital única, inmutable y anónima de la computadora física.
    Utiliza el MachineGuid de Windows o el UUID de la placa base.
    """
    if os.path.exists(HWID_CACHE_FILE):
        try:
            with open(HWID_CACHE_FILE, "r", encoding="utf-8") as f:
                hwid = f.read().strip()
                if hwid.startswith("HWID-") and len(hwid) > 10:
                    return hwid
        except Exception:
            pass

    raw_tokens = []
    
    # 1. MachineGuid de Windows (Estándar de la industria)
    if sys.platform == "win32":
        try:
            cmd = 'reg query "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography" /v MachineGuid'
            out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if "MachineGuid" in line:
                    parts = line.split()
                    raw_tokens.append(parts[-1].strip())
        except Exception:
            pass

        # 2. BIOS / Motherboard UUID
        try:
            cmd_uuid = 'wmic csproduct get uuid'
            out_uuid = subprocess.check_output(cmd_uuid, shell=True, text=True, stderr=subprocess.DEVNULL)
            lines = [l.strip() for l in out_uuid.splitlines() if l.strip() and "UUID" not in l]
            if lines:
                raw_tokens.append(lines[0])
        except Exception:
            pass

    # 3. Fallback: Nombre de usuario + procesador
    raw_tokens.append(os.environ.get("USERNAME", "user"))
    raw_tokens.append(os.environ.get("COMPUTERNAME", "pc"))
    raw_tokens.append(os.environ.get("PROCESSOR_IDENTIFIER", "cpu"))

    combined = ":".join(raw_tokens).encode("utf-8")
    h = hashlib.sha256(combined).hexdigest().upper()
    hwid = f"HWID-{h[0:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"

    try:
        with open(HWID_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(hwid)
    except Exception:
        pass

    return hwid


def verify_license_remote(server_url: str = CLOUD_VERIFY_URL, timeout: int = 8) -> Tuple[bool, str]:
    """
    Consulta en tiempo real al servidor en Render si esta máquina está autorizada.
    Retorna (es_valido: bool, motivo: str).
    """
    # Si el desarrollador activó modo bypass local explícito
    if os.environ.get("GHS_DEV_BYPASS") == "1":
        return True, "Modo Desarrollador Local Activo"

    hwid = get_hardware_id()
    client_name = os.environ.get("USERNAME", "Usuario")
    
    verify_endpoint = f"{server_url.rstrip('/')}/api/v1/license/verify?hwid={hwid}&name={client_name}"

    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(verify_endpoint, headers={"User-Agent": "GHS-Client/1.6.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                body = response.read().decode("utf-8")
                data = json.loads(body)
                if data.get("allowed") is True:
                    return True, "Licencia Activa"
                else:
                    msg = data.get("message", "Acceso suspendido por el administrador.")
                    return False, msg
    except urllib.error.HTTPError as e:
        # Si el servidor responde 403 o 401
        return False, f"Servidor rechazó autorización (HTTP {e.code})"
    except Exception as e:
        # Si el servidor no responde o no hay internet temporalmente:
        # Modo tolerancia: permitimos continuar si el servidor de Render está arrancando o sin conexión
        return True, f"Modo Offline temporal ({e})"

    return True, "Verificación completada"


def start_license_sentinel(on_blocked_callback, interval: int = 600, server_url: str = CLOUD_VERIFY_URL):
    """
    Inicia un guardia en segundo plano que vigila el estado de la licencia periódicamente.
    Si el administrador presiona 'Bloquear' en Render, el callback se dispara para apagar el puente.
    """
    def _worker():
        while True:
            time.sleep(interval)
            try:
                allowed, msg = verify_license_remote(server_url=server_url, timeout=10)
                if not allowed:
                    if on_blocked_callback:
                        on_blocked_callback(msg)
                    break
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True, name="ghs_license_sentinel")
    t.start()
