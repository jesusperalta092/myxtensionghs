# SPDX-License-Identifier: GPL-3.0-or-later
"""
licensing_manager.py
══════════════════════════════════════════════════════════════════════════════
GHS CLOUD COMMAND & LICENSING CENTER (v1.6.0)
Control centralizado de licencias, hardware IDs y kill-switch remoto.
══════════════════════════════════════════════════════════════════════════════
"""
import os
import json
import time
import hmac
import hashlib
import secrets
import urllib.parse
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "licenses_db.json")

# Variables de entorno en Render
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ghs_admin_secret_2026")
SECRET_KEY = os.environ.get("GHS_SECRET_KEY", "ghs_master_cryptokey_9841").encode("utf-8")

# ══════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS LOCAL Y PERSISTENTE
# ══════════════════════════════════════════════════════════════════════════
def _load_db() -> Dict[str, Any]:
    if not os.path.exists(DB_PATH):
        init_data = {
            "version": "1.6.0",
            "users": {},
            "audit_logs": []
        }
        _save_db(init_data)
        return init_data
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": "1.6.0", "users": {}, "audit_logs": []}


def _save_db(data: Dict[str, Any]):
    tmp_path = DB_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, DB_PATH)


def _log_audit(action: str, target: str, details: str = ""):
    db = _load_db()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "target": target,
        "details": details
    }
    db.setdefault("audit_logs", []).append(entry)
    if len(db["audit_logs"]) > 200:
        db["audit_logs"] = db["audit_logs"][-200:]
    _save_db(db)


# ══════════════════════════════════════════════════════════════════════════
#  TOKENS DE SESIÓN ADMIN Y FIRMAS CRIPTOGRÁFICAS
# ══════════════════════════════════════════════════════════════════════════
def create_admin_token() -> str:
    """Genera un token de sesión firmado para el administrador (validez 7 días)."""
    expiry = int(time.time()) + (7 * 86400)
    msg = f"admin:{expiry}".encode("utf-8")
    sig = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()
    return f"admin:{expiry}:{sig}"


def verify_admin_token(token: str) -> bool:
    """Valida la firma y vigencia del token de sesión de administrador."""
    if not token or ":" not in token:
        return False
    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "admin":
        return False
    try:
        expiry = int(parts[1])
        if time.time() > expiry:
            return False
        expected_msg = f"admin:{expiry}".encode("utf-8")
        expected_sig = hmac.new(SECRET_KEY, expected_msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(parts[2], expected_sig)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════
#  LÓGICA DEL MOTOR DE LICENCIAS (CLIENTES & HEARTBEAT)
# ══════════════════════════════════════════════════════════════════════════
def check_client_license(hwid: str, license_key: str = "", client_name: str = "", ip: str = "") -> Dict[str, Any]:
    """
    Verifica si una máquina / cliente tiene permiso de ejecutar GHS.
    Si es un cliente nuevo y no está bloqueado, se auto-registra como activo.
    """
    if not hwid:
        return {"allowed": False, "status": "invalid_hwid", "message": "Hardware ID no proporcionado."}

    db = _load_db()
    users = db.setdefault("users", {})

    now_iso = datetime.now().isoformat()

    if hwid not in users:
        # Auto-registro inicial
        users[hwid] = {
            "hwid": hwid,
            "name": client_name or f"PC-{hwid[:8]}",
            "license": license_key or f"GHS-AUTO-{secrets.token_hex(4).upper()}",
            "status": "active",
            "first_seen": now_iso,
            "last_seen": now_iso,
            "last_ip": ip or "Desconocida",
            "notes": "Registrado automáticamente al iniciar GHS"
        }
        _save_db(db)
        _log_audit("REGISTER", hwid, f"Nuevo cliente registrado: {users[hwid]['name']}")

    user = users[hwid]
    user["last_seen"] = now_iso
    if ip:
        user["last_ip"] = ip
    # Actualizar con nombre enriquecido si el actual es generico o no ha sido personalizado
    if client_name and (user.get("name") in ("USER", f"PC-{hwid[:8]}", "Usuario") or ("@" in client_name and not user.get("custom_name"))):
        user["name"] = client_name
    _save_db(db)

    if user.get("status") == "blocked":
        return {
            "allowed": False,
            "status": "blocked",
            "message": "⛔ ACCESO SUSPENDIDO: Tu licencia ha sido revocada o suspendida por el administrador."
        }

    # Token firmado que certifica la validez por 1 hora
    token_expiry = int(time.time()) + 3600
    proof_msg = f"{hwid}:{user.get('license')}:{token_expiry}".encode("utf-8")
    proof_sig = hmac.new(SECRET_KEY, proof_msg, hashlib.sha256).hexdigest()

    return {
        "allowed": True,
        "status": "active",
        "name": user.get("name"),
        "license": user.get("license"),
        "expiry": token_expiry,
        "token": f"{token_expiry}:{proof_sig}"
    }


def rename_user(hwid: str, new_name: str) -> Dict[str, Any]:
    db = _load_db()
    users = db.setdefault("users", {})
    if hwid not in users:
        return {"success": False, "error": "Usuario no encontrado"}
    clean_name = new_name.strip()
    users[hwid]["name"] = clean_name
    users[hwid]["custom_name"] = True
    _save_db(db)
    _log_audit("RENAME", hwid, f"Nombre actualizado a: {clean_name}")
    return {"success": True, "hwid": hwid, "new_name": clean_name}


def toggle_user(hwid: str) -> Dict[str, Any]:
    db = _load_db()
    users = db.setdefault("users", {})
    if hwid not in users:
        return {"success": False, "error": "Usuario no encontrado"}

    current = users[hwid].get("status", "active")
    new_status = "blocked" if current == "active" else "active"
    users[hwid]["status"] = new_status
    _save_db(db)
    _log_audit("TOGGLE_STATUS", hwid, f"Cambio de estado a: {new_status}")
    return {"success": True, "new_status": new_status, "hwid": hwid}


def delete_user(hwid: str) -> Dict[str, Any]:
    db = _load_db()
    users = db.setdefault("users", {})
    if hwid in users:
        del users[hwid]
        _save_db(db)
        _log_audit("DELETE", hwid, "Cliente eliminado de la base de datos")
        return {"success": True}
    return {"success": False, "error": "Usuario no encontrado"}


def create_or_update_user(hwid: str, name: str, license_key: str = "", notes: str = "") -> Dict[str, Any]:
    db = _load_db()
    users = db.setdefault("users", {})
    now_iso = datetime.now().isoformat()
    users[hwid] = {
        "hwid": hwid,
        "name": name or f"Cliente-{hwid[:8]}",
        "license": license_key or f"GHS-PRO-{secrets.token_hex(4).upper()}",
        "status": users.get(hwid, {}).get("status", "active"),
        "first_seen": users.get(hwid, {}).get("first_seen", now_iso),
        "last_seen": now_iso,
        "last_ip": users.get(hwid, {}).get("last_ip", "Manual"),
        "notes": notes
    }
    _save_db(db)
    _log_audit("UPSERT", hwid, f"Licencia asignada para {name}")
    return {"success": True, "user": users[hwid]}


# ══════════════════════════════════════════════════════════════════════════
#  INTERFAZ GRÁFICA DEL PANEL ADMIN (/admin)
# ══════════════════════════════════════════════════════════════════════════
def render_admin_page(is_auth: bool = False, login_error: bool = False) -> str:
    db = _load_db() if is_auth else {}
    users = db.get("users", {}) if is_auth else {}
    total_users = len(users)
    active_users = sum(1 for u in users.values() if u.get("status") == "active")
    blocked_users = total_users - active_users

    users_json = json.dumps(list(users.values()), ensure_ascii=False) if is_auth else "[]"

    if not is_auth:
        return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GHS Control Center - Iniciar Sesión</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #090a0f;
      --card-bg: rgba(18, 20, 29, 0.75);
      --border: rgba(255, 255, 255, 0.08);
      --accent: #a855f7;
      --accent-glow: rgba(168, 85, 247, 0.35);
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --danger: #ef4444;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Outfit', sans-serif;
      background: radial-gradient(circle at 50% 20%, #17112c 0%, var(--bg) 70%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .login-card {{
      background: var(--card-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 44px;
      width: 100%;
      max-width: 440px;
      box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.7), 0 0 40px -10px var(--accent-glow);
      text-align: center;
      animation: fadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    @keyframes fadeIn {{
      from {{ opacity: 0; transform: translateY(12px) scale(0.98); }}
      to {{ opacity: 1; transform: translateY(0) scale(1); }}
    }}
    .logo-badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 64px;
      height: 64px;
      background: linear-gradient(135deg, #a855f7, #6366f1);
      border-radius: 20px;
      font-size: 32px;
      margin-bottom: 24px;
      box-shadow: 0 10px 25px -5px rgba(168, 85, 247, 0.5);
    }}
    h1 {{ font-size: 24px; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 8px; }}
    p.subtitle {{ color: var(--text-muted); font-size: 14px; margin-bottom: 30px; line-height: 1.5; }}
    .form-group {{ margin-bottom: 22px; text-align: left; }}
    label {{ display: block; font-size: 13px; font-weight: 600; color: var(--text-muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }}
    input[type="password"] {{
      width: 100%;
      padding: 14px 18px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border);
      border-radius: 14px;
      color: #fff;
      font-size: 15px;
      font-family: 'JetBrains Mono', monospace;
      outline: none;
      transition: all 0.2s ease;
    }}
    input[type="password"]:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(168, 85, 247, 0.25);
      background: rgba(255, 255, 255, 0.07);
    }}
    .btn-submit {{
      width: 100%;
      padding: 14px 20px;
      background: linear-gradient(135deg, #a855f7 0%, #7c3aed 100%);
      color: #fff;
      border: none;
      border-radius: 14px;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s ease;
      box-shadow: 0 10px 25px -5px rgba(168, 85, 247, 0.4);
    }}
    .btn-submit:hover {{
      transform: translateY(-2px);
      box-shadow: 0 15px 30px -5px rgba(168, 85, 247, 0.6);
    }}
    .error-box {{
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
      padding: 12px 16px;
      border-radius: 12px;
      font-size: 13px;
      margin-bottom: 20px;
    }}
    .footer-hint {{
      margin-top: 24px;
      font-size: 12px;
      color: #6b7280;
    }}
  </style>
</head>
<body>
  <div class="login-card">
    <div class="logo-badge">⚡</div>
    <h1>GHS Control Center</h1>
    <p class="subtitle">Panel de Seguridad y Gestión de Licencias Autónomas en la Nube</p>
    
    {"<div class='error-box'>⚠️ Contraseña de Administrador incorrecta.</div>" if login_error else ""}

    <form method="GET" action="/admin/login">
      <div class="form-group">
        <label for="password">Clave Maestra de Administrador</label>
        <input type="password" id="password" name="password" required autofocus placeholder="Ingresa tu clave secreta de Render">
      </div>
      <button type="submit" class="btn-submit">Desbloquear Panel</button>
    </form>
    
    <div class="footer-hint">Protegido por variable de entorno ADMIN_PASSWORD en Render</div>
  </div>
</body>
</html>"""

    # VISTA AUTENTICADA (PANEL ADMIN COMPLETO)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GHS Autonomous Command Center - Panel de Licencias</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #07090e;
      --card-bg: rgba(15, 18, 28, 0.7);
      --border: rgba(255, 255, 255, 0.08);
      --accent: #a855f7;
      --accent-glow: rgba(168, 85, 247, 0.3);
      --success: #10b981;
      --danger: #ef4444;
      --text: #f9fafb;
      --text-muted: #94a3b8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Outfit', sans-serif;
      background: radial-gradient(circle at 50% 0%, #15102a 0%, var(--bg) 60%);
      color: var(--text);
      min-height: 100vh;
      padding: 30px 20px;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 35px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 15px;
    }}
    .brand-icon {{
      width: 44px;
      height: 44px;
      background: linear-gradient(135deg, #a855f7, #6366f1);
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 22px;
      box-shadow: 0 8px 20px -4px var(--accent-glow);
    }}
    .brand-title {{ font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }}
    .brand-subtitle {{ font-size: 13px; color: var(--text-muted); }}
    .header-actions {{
      display: flex;
      align-items: center;
      gap: 15px;
    }}
    .status-pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 14px;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.25);
      border-radius: 20px;
      color: #34d399;
      font-size: 13px;
      font-weight: 600;
    }}
    .status-dot {{ width: 8px; height: 8px; background: #34d399; border-radius: 50%; box-shadow: 0 0 10px #34d399; }}
    .btn-logout {{
      padding: 8px 16px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      border-radius: 10px;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      transition: all 0.2s;
    }}
    .btn-logout:hover {{ background: rgba(239, 68, 68, 0.15); color: #fca5a5; border-color: rgba(239, 68, 68, 0.3); }}

    /* KPI METRICS */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 20px;
      margin-bottom: 35px;
    }}
    .metric-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 22px;
      backdrop-filter: blur(16px);
      box-shadow: 0 15px 35px -10px rgba(0, 0, 0, 0.5);
    }}
    .metric-title {{ font-size: 13px; font-weight: 600; text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px; letter-spacing: 0.5px; }}
    .metric-value {{ font-size: 32px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }}

    /* TOOLBAR */
    .toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 15px;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }}
    .search-box {{
      flex: 1;
      min-width: 280px;
      position: relative;
    }}
    .search-box input {{
      width: 100%;
      padding: 12px 18px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      color: #fff;
      font-size: 14px;
      outline: none;
      transition: border 0.2s;
    }}
    .search-box input:focus {{ border-color: var(--accent); }}
    .btn-action {{
      padding: 12px 20px;
      background: linear-gradient(135deg, #a855f7, #7c3aed);
      border: none;
      border-radius: 12px;
      color: #fff;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 8px 20px -5px rgba(168, 85, 247, 0.4);
      transition: all 0.2s;
    }}
    .btn-action:hover {{ transform: translateY(-2px); }}

    /* TABLE */
    .table-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 20px;
      overflow: hidden;
      backdrop-filter: blur(16px);
      box-shadow: 0 20px 45px -15px rgba(0, 0, 0, 0.6);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 14px;
    }}
    th {{
      background: rgba(255, 255, 255, 0.02);
      padding: 16px 20px;
      font-size: 12px;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-bottom: 1px solid var(--border);
    }}
    td {{
      padding: 18px 20px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}

    .client-name {{ font-weight: 700; font-size: 15px; }}
    .client-note {{ font-size: 12px; color: var(--text-muted); margin-top: 2px; }}
    .code-pill {{
      display: inline-block;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      padding: 4px 8px;
      background: rgba(255, 255, 255, 0.06);
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      cursor: pointer;
      transition: background 0.2s;
    }}
    .code-pill:hover {{ background: rgba(168, 85, 247, 0.2); border-color: var(--accent); }}

    .badge-status {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .badge-active {{ background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }}
    .badge-blocked {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }}

    .btn-toggle {{
      padding: 8px 14px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
      border: none;
    }}
    .btn-block {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.4); }}
    .btn-block:hover {{ background: #ef4444; color: #fff; }}
    .btn-activate {{ background: rgba(16, 185, 129, 0.2); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4); }}
    .btn-activate:hover {{ background: #10b981; color: #fff; }}

    .btn-del {{
      padding: 8px 10px;
      background: transparent;
      border: 1px solid transparent;
      color: #6b7280;
      border-radius: 8px;
      cursor: pointer;
      margin-left: 8px;
      transition: all 0.2s;
    }}
    .btn-del:hover {{ color: #ef4444; background: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.3); }}

    .btn-rename {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 6px;
      color: #94a3b8;
      font-size: 11px;
      padding: 2px 6px;
      cursor: pointer;
      margin-left: 6px;
      transition: all 0.2s;
    }}
    .btn-rename:hover {{
      background: rgba(168, 85, 247, 0.2);
      border-color: var(--accent);
      color: #fff;
    }}

    /* MODAL */
    .modal-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(8px);
      z-index: 100;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .modal-card {{
      background: #111422;
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 30px;
      width: 100%;
      max-width: 480px;
      box-shadow: 0 25px 60px rgba(0,0,0,0.8);
    }}
    .modal-title {{ font-size: 18px; font-weight: 700; margin-bottom: 20px; }}
    .modal-input {{ width: 100%; padding: 12px 14px; background: rgba(255,255,255,0.05); border: 1px solid var(--border); border-radius: 10px; color: #fff; margin-bottom: 15px; font-family: inherit; }}
    .modal-actions {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-icon">⚡</div>
        <div>
          <div class="brand-title">GHS CLOUD COMMAND CENTER</div>
          <div class="brand-subtitle">Gestión Suprema de Licencias & Kill-Switch Remoto</div>
        </div>
      </div>
      <div class="header-actions">
        <div class="status-pill">
          <div class="status-dot"></div>
          Servidor Render Activo
        </div>
        <a href="/admin/logout" class="btn-logout">Cerrar Sesión</a>
      </div>
    </header>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-title">Total de Clientes</div>
        <div class="metric-value" id="val-total" style="color: #60a5fa;">{total_users}</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Clientes Activos</div>
        <div class="metric-value" id="val-active" style="color: #34d399;">{active_users}</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Clientes Bloqueados</div>
        <div class="metric-value" id="val-blocked" style="color: #f87171;">{blocked_users}</div>
      </div>
    </div>

    <div class="toolbar">
      <div class="search-box">
        <input type="text" id="search-input" placeholder="🔍 Buscar por nombre, Hardware ID o Licencia..." onkeyup="filterUsers()">
      </div>
      <button class="btn-action" onclick="openAddModal()">+ Registrar / Asignar Licencia</button>
    </div>

    <div class="table-card">
      <table>
        <thead>
          <tr>
            <th>Cliente</th>
            <th>Hardware ID (Huella PC)</th>
            <th>Licencia</th>
            <th>Última Conexión</th>
            <th>Estado</th>
            <th>Acciones Inmediatas</th>
          </tr>
        </thead>
        <tbody id="users-tbody">
        </tbody>
      </table>
    </div>
  </div>

  <!-- MODAL NUEVO CLIENTE -->
  <div class="modal-overlay" id="add-modal">
    <div class="modal-card">
      <div class="modal-title">Asignar Nueva Licencia de Cliente</div>
      <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:6px;">Nombre del Cliente</label>
      <input type="text" id="modal-name" class="modal-input" placeholder="Ej: Pepito Sánchez">
      
      <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:6px;">Hardware ID (Opcional, se auto-asigna al conectar)</label>
      <input type="text" id="modal-hwid" class="modal-input" placeholder="Ej: HWID-4821-B90F (o deja en blanco)">
      
      <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:6px;">Notas / Plan</label>
      <input type="text" id="modal-notes" class="modal-input" placeholder="Ej: Plan Pro - Pago mensual">

      <div class="modal-actions">
        <button class="btn-logout" onclick="closeAddModal()">Cancelar</button>
        <button class="btn-action" onclick="submitNewUser()">Guardar Licencia</button>
      </div>
    </div>
  </div>

  <script>
    let usersData = {users_json};

    function renderTable() {{
      const tbody = document.getElementById('users-tbody');
      if (!usersData || usersData.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#6b7280;padding:40px;">No hay clientes registrados aún. Se registrarán automáticamente cuando inicien GHS.</td></tr>';
        return;
      }}

      tbody.innerHTML = usersData.map(u => {{
        const isAct = u.status === 'active';
        return `
          <tr id="row-${{u.hwid}}">
            <td>
              <div class="client-name">
                ${{escapeHtml(u.name || 'Sin nombre')}}
                <button class="btn-rename" onclick="renameClient('${{u.hwid}}', '${{escapeHtml(u.name || '')}}')" title="Editar apodo o nombre real">✏️</button>
              </div>
              <div class="client-note">${{escapeHtml(u.notes || 'IP: ' + (u.last_ip || 'Desconocida'))}}</div>
            </td>
            <td>
              <span class="code-pill" onclick="copyText('${{u.hwid}}')" title="Clic para copiar HWID">${{escapeHtml(u.hwid.substring(0, 16))}}...</span>
            </td>
            <td>
              <span class="code-pill" onclick="copyText('${{u.license || ''}}')" title="Clic para copiar Licencia">${{escapeHtml(u.license || 'N/A')}}</span>
            </td>
            <td style="color:#94a3b8;font-size:13px;">
              ${{formatDate(u.last_seen)}}
            </td>
            <td>
              <span class="badge-status ${{isAct ? 'badge-active' : 'badge-blocked'}}">
                ${{isAct ? '🟢 ACTIVO' : '🔴 BLOQUEADO'}}
              </span>
            </td>
            <td>
              <button class="btn-toggle ${{isAct ? 'btn-block' : 'btn-activate'}}" onclick="toggleStatus('${{u.hwid}}')">
                ${{isAct ? '🚫 Bloquear Acceso' : '🟢 Reactivar Acceso'}}
              </button>
              <button class="btn-del" onclick="deleteClient('${{u.hwid}}')" title="Eliminar registro">🗑️</button>
            </td>
          </tr>
        `;
      }}).join('');
    }}

    async function renameClient(hwid, currentName) {{
      const newName = prompt('Editar nombre para este cliente:', currentName);
      if (newName === null || !newName.trim()) return;
      try {{
        const res = await fetch('/api/admin/rename?hwid=' + encodeURIComponent(hwid) + '&name=' + encodeURIComponent(newName.trim()), {{
          method: 'GET'
        }});
        const data = await res.json();
        if (data.success) {{
          const item = usersData.find(u => u.hwid === hwid);
          if (item) item.name = data.new_name;
          renderTable();
        }}
      }} catch (e) {{
        alert('Error al renombrar: ' + e);
      }}
    }}

    async function toggleStatus(hwid) {{
      try {{
        const res = await fetch('/api/admin/toggle?hwid=' + encodeURIComponent(hwid), {{
          method: 'GET'
        }});
        const data = await res.json();
        if (data.success) {{
          const item = usersData.find(u => u.hwid === hwid);
          if (item) item.status = data.new_status;
          updateMetrics();
          renderTable();
        }}
      }} catch (e) {{
        alert('Error al alternar estado: ' + e);
      }}
    }}

    async function deleteClient(hwid) {{
      if (!confirm('¿Seguro que deseas eliminar este cliente?')) return;
      try {{
        const res = await fetch('/api/admin/delete?hwid=' + encodeURIComponent(hwid), {{
          method: 'GET'
        }});
        const data = await res.json();
        if (data.success) {{
          usersData = usersData.filter(u => u.hwid !== hwid);
          updateMetrics();
          renderTable();
        }}
      }} catch (e) {{
        alert('Error al eliminar: ' + e);
      }}
    }}

    async function submitNewUser() {{
      const name = document.getElementById('modal-name').value.trim();
      const hwid = document.getElementById('modal-hwid').value.trim() || ('HWID-' + Math.random().toString(36).substring(2, 10).toUpperCase());
      const notes = document.getElementById('modal-notes').value.trim();
      if (!name) return alert('Ingresa un nombre de cliente.');

      try {{
        const url = '/api/admin/create?hwid=' + encodeURIComponent(hwid) + '&name=' + encodeURIComponent(name) + '&notes=' + encodeURIComponent(notes);
        const res = await fetch(url, {{
          method: 'GET'
        }});
        const data = await res.json();
        if (data.success) {{
          usersData.unshift(data.user);
          updateMetrics();
          renderTable();
          closeAddModal();
        }}
      }} catch (e) {{
        alert('Error al guardar: ' + e);
      }}
    }}

    function updateMetrics() {{
      const total = usersData.length;
      const active = usersData.filter(u => u.status === 'active').length;
      const blocked = total - active;
      document.getElementById('val-total').textContent = total;
      document.getElementById('val-active').textContent = active;
      document.getElementById('val-blocked').textContent = blocked;
    }}

    function filterUsers() {{
      const q = document.getElementById('search-input').value.toLowerCase();
      const rows = document.querySelectorAll('#users-tbody tr');
      rows.forEach(r => {{
        r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
      }});
    }}

    function openAddModal() {{
      document.getElementById('add-modal').style.display = 'flex';
      document.getElementById('modal-name').focus();
    }}
    function closeAddModal() {{
      document.getElementById('add-modal').style.display = 'none';
    }}

    function copyText(txt) {{
      navigator.clipboard.writeText(txt);
      alert('Copiado al portapapeles: ' + txt);
    }}

    function formatDate(iso) {{
      if (!iso) return 'Desconocido';
      const d = new Date(iso);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {{hour: '2-digit', minute:'2-digit'}});
    }}

    function escapeHtml(s) {{
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }}

    renderTable();
  </script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════
#  MANEJADOR HTTP UNIVERSAL PARA LA INTEGRACIÓN CON EL SERVIDOR
# ══════════════════════════════════════════════════════════════════════════
async def handle_licensing_http(path: str, headers: Dict[str, str], body_bytes: bytes = b"", client_ip: str = "") -> Optional[Tuple[int, Dict[str, str], bytes]]:
    """
    Despacha peticiones HTTP relacionadas a /admin y /api/v1/license.
    Retorna (status_code, headers_dict, body_bytes) o None si no corresponde a este módulo.
    """
    clean_path = path.split("?")[0]
    query_str = path.split("?")[1] if "?" in path else ""
    query_params = urllib.parse.parse_qs(query_str)

    # 1. Heartbeat público del cliente: GET /api/v1/license/verify
    if clean_path == "/api/v1/license/verify":
        hwid = query_params.get("hwid", [""])[0]
        license_key = query_params.get("license", [""])[0]
        client_name = query_params.get("name", [""])[0]
        res = check_client_license(hwid, license_key, client_name, client_ip)
        body = json.dumps(res).encode("utf-8")
        return (200, {"Content-Type": "application/json; charset=utf-8"}, body)

    # 2. Rutas del Panel de Administración
    if clean_path.startswith("/admin") or clean_path.startswith("/api/admin"):
        # Extraer cookie de sesión
        cookie_header = headers.get("cookie", "") or headers.get("Cookie", "")
        cookies = {}
        for c in cookie_header.split(";"):
            if "=" in c:
                k, v = c.strip().split("=", 1)
                cookies[k] = v

        session_token = cookies.get("ghs_admin_session", "")
        is_authenticated = verify_admin_token(session_token)

        # GET /admin/logout
        if clean_path == "/admin/logout":
            resp_headers = {
                "Location": "/admin",
                "Set-Cookie": "ghs_admin_session=; Path=/; Max-Age=0; HttpOnly"
            }
            return (302, resp_headers, b"")

        # GET or POST /admin/login
        if clean_path == "/admin/login":
            form_params = urllib.parse.parse_qs(body_bytes.decode("utf-8", errors="ignore")) if body_bytes else {}
            pwd = form_params.get("password", [""])[0] or query_params.get("password", [""])[0]
            if pwd == ADMIN_PASSWORD:
                new_token = create_admin_token()
                resp_headers = {
                    "Location": "/admin",
                    "Set-Cookie": f"ghs_admin_session={new_token}; Path=/; HttpOnly; Max-Age=604800; SameSite=Lax"
                }
                return (302, resp_headers, b"")
            else:
                html = render_admin_page(is_auth=False, login_error=True)
                return (200, {"Content-Type": "text/html; charset=utf-8"}, html.encode("utf-8"))

        # GET /admin
        if clean_path == "/admin":
            html = render_admin_page(is_auth=is_authenticated, login_error=False)
            return (200, {"Content-Type": "text/html; charset=utf-8"}, html.encode("utf-8"))

        # Endpoints protegidos de la API Admin
        if not is_authenticated:
            return (401, {"Content-Type": "application/json"}, json.dumps({"error": "No autorizado"}).encode("utf-8"))

        try:
            req_data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            req_data = {}

        if clean_path == "/api/admin/toggle":
            hwid = req_data.get("hwid") or query_params.get("hwid", [""])[0]
            res = toggle_user(hwid)
            return (200, {"Content-Type": "application/json"}, json.dumps(res).encode("utf-8"))

        if clean_path == "/api/admin/delete":
            hwid = req_data.get("hwid") or query_params.get("hwid", [""])[0]
            res = delete_user(hwid)
            return (200, {"Content-Type": "application/json"}, json.dumps(res).encode("utf-8"))

        if clean_path == "/api/admin/rename":
            hwid = req_data.get("hwid") or query_params.get("hwid", [""])[0]
            new_name = req_data.get("name") or query_params.get("name", [""])[0]
            res = rename_user(hwid, new_name)
            return (200, {"Content-Type": "application/json"}, json.dumps(res).encode("utf-8"))

        if clean_path == "/api/admin/create":
            hwid = req_data.get("hwid") or query_params.get("hwid", [""])[0]
            name = req_data.get("name") or query_params.get("name", [""])[0]
            notes = req_data.get("notes") or query_params.get("notes", [""])[0]
            res = create_or_update_user(hwid, name, notes=notes)
            return (200, {"Content-Type": "application/json"}, json.dumps(res).encode("utf-8"))

    return None
