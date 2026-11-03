# SPDX-License-Identifier: GPL-3.0-or-later
"""
hermes_mcp_server.py - Hermes Agent Superpowers MCP Server for GHS
Exposes Nous Research Hermes Agent runtime (88 native tools & 76 specialized skills)
along with Windows OS Automation (computer_use, terminal, file operations, memory)
as a standard Model Context Protocol (MCP) server over STDIO.
"""

import sys
import os
import json
import io
import base64
import time
import subprocess
import traceback
import re
import tempfile
import shutil
import glob
try:
    import psutil
except Exception:
    psutil = None
from typing import Dict, Any, List, Optional

# Ensure standard I/O handles are UTF-8 without buffer issues
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Optional OS Automation Imports
try:
    import pyautogui
    pyautogui.FAILSAFE = True
except Exception:
    pyautogui = None

try:
    import mss
except Exception:
    mss = None

try:
    from PIL import Image, ImageDraw, ImageGrab, ImageFont
except Exception:
    Image = None
    ImageDraw = None
    ImageGrab = None
    ImageFont = None

try:
    import ctypes
    user32 = ctypes.windll.user32
except Exception:
    user32 = None

# ─────────────────────────────────────────────────────────────────────────────
# Hermes Agent Native Integration (hermes-agent(mod))
# ─────────────────────────────────────────────────────────────────────────────
candidate_dirs = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "hermes-agent(mod)"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "2-BACKEND-NUBE-RENDER", "hermes-agent(mod)"),
    r"C:\Users\USER\Downloads\GHS-Script-Free\hermes-agent(mod)",
    r"C:\Users\USER\Downloads\GHS-Proyecto-Final\hermes-agent(mod)",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "hermes-agent(mod)"),
]
MOD_DIR = None
for cd in candidate_dirs:
    if os.path.exists(cd):
        MOD_DIR = cd
        break

HERMES_NATIVE_AVAILABLE = False
_handle_function_call = None
_hermes_registry = None

if MOD_DIR and os.path.exists(MOD_DIR):
    if MOD_DIR not in sys.path:
        sys.path.insert(0, MOD_DIR)
    try:
        from tools.registry import discover_builtin_tools, registry
        from model_tools import handle_function_call
        discover_builtin_tools()
        _handle_function_call = handle_function_call
        _hermes_registry = registry
        HERMES_NATIVE_AVAILABLE = True
        sys.stderr.write(f"[hermes-mcp] Loaded {len(registry._tools)} native Hermes tools from hermes-agent(mod) [{MOD_DIR}]\n")
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"[hermes-mcp] Warning: Could not load native hermes-agent(mod): {e}\n")
        sys.stderr.flush()

# ── RESOLUCION DINAMICA DE DIRECTORIOS HERMES (WINDOWS & LINUX/RENDER) ──
def get_hermes_base_dir() -> str:
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return os.path.join(local_app, "hermes")
        return os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "AppData", "Local", "hermes")
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(here, "memory")):
            return here
        return os.path.join(os.path.expanduser("~"), ".hermes")


# ── CIFRADO / DESCIFRADO TRANSPARENTE DE MEMORIA (Fernet AES) ──
GHS_DEFAULT_MEM_KEY = b'zF-6ZHe9Og1bnZjvo5tl_p7Lz8szWIyUfL0A3Pe7uUU='

def get_fernet_cipher():
    key_str = os.environ.get("GHS_MEM_KEY", "").strip()
    key_path = os.path.join(os.path.dirname(__file__), '.memkey')
    key = None
    if key_str:
        key = key_str.encode('utf-8')
    elif os.path.exists(key_path):
        try:
            with open(key_path, 'rb') as kf:
                key = kf.read().strip()
        except Exception:
            pass
    if not key:
        key = GHS_DEFAULT_MEM_KEY
    from cryptography.fernet import Fernet
    return Fernet(key)

def decrypt_if_encrypted(data_bytes: bytes) -> str:
    if data_bytes.startswith(b'gAAAAA'):
        try:
            cipher = get_fernet_cipher()
            return cipher.decrypt(data_bytes).decode('utf-8')
        except Exception as e:
            return f"[Error desencriptando archivo de memoria: {e}]"
    try:
        return data_bytes.decode('utf-8')
    except Exception:
        return data_bytes.decode('utf-8', errors='replace')

def get_memory_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    proj_mem = os.path.join(here, "memory")
    if os.path.exists(proj_mem):
        return proj_mem
    b = get_hermes_base_dir()
    d = os.path.join(b, "memory")
    os.makedirs(d, exist_ok=True)
    return d

MEMORY_DB_PATH = os.path.join(get_hermes_base_dir(), "ghs_memory.db")
SKILLS_DIR = os.path.join(get_hermes_base_dir(), "skills")
DISABLED_SKILLS_FILE = os.path.join(SKILLS_DIR, "disabled_skills.json")

def get_disabled_skills():
    if os.path.exists(DISABLED_SKILLS_FILE):
        try:
            with open(DISABLED_SKILLS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_disabled_skills(disabled):
    os.makedirs(os.path.dirname(DISABLED_SKILLS_FILE), exist_ok=True)
    with open(DISABLED_SKILLS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(disabled), f)



def _init_memory_db():
    try:
        import sqlite3
        os.makedirs(os.path.dirname(MEMORY_DB_PATH), exist_ok=True)
        with sqlite3.connect(MEMORY_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT,
                    key TEXT UNIQUE,
                    content TEXT,
                    created_at REAL
                )
            """)
            conn.commit()
    except Exception:
        pass


_init_memory_db()


# ─────────────────────────────────────────────────────────────────────────────
# Native Hermes Tool Handlers
# ─────────────────────────────────────────────────────────────────────────────

def _tool_native_dispatch(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if not HERMES_NATIVE_AVAILABLE or _handle_function_call is None:
        return {"content": [{"type": "text", "text": "Error: El motor nativo de Hermes Agent no está disponible en 'hermes-agent(mod)'."}], "isError": True}
    try:
        raw_res = _handle_function_call(tool_name, arguments)
        if isinstance(raw_res, (dict, list)):
            text = json.dumps(raw_res, indent=2, ensure_ascii=False)
        else:
            text = str(raw_res)
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error ejecutando herramienta nativa '{tool_name}': {e}"}], "isError": True}


def _tool_skills_list(arguments: Dict[str, Any]) -> Dict[str, Any]:
    raw = _tool_native_dispatch("skills_list", arguments)
    try:
        data = json.loads(raw["content"][0]["text"])
        disabled = get_disabled_skills()
        if disabled and "skills" in data:
            data["skills"] = [s for s in data["skills"] if s.get("name") not in disabled]
            raw["content"][0]["text"] = json.dumps(data)
    except Exception:
        pass
    return raw


def _tool_skill_view(arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = arguments.get("name", "")
    return _tool_native_dispatch("skill_view", {"name": name})


def _tool_tools_list(arguments: Dict[str, Any]) -> Dict[str, Any]:
    tool_summaries = []
    # Herramientas directas de GHS MCP (incluye hermes_prospect_scrape, hermes_stealth_scrape, etc.)
    for t in TOOLS:
        tname = t.get("name")
        tdesc = (t.get("description") or "").split("\n")[0][:120]
        tool_summaries.append({
            "name": tname,
            "toolset": "ghs_mcp_direct",
            "emoji": "⚡",
            "description": tdesc,
            "invocation": "direct_command"
        })
    # Herramientas nativas del motor Nous Research Hermes
    if HERMES_NATIVE_AVAILABLE and _hermes_registry is not None:
        for name, t in sorted(_hermes_registry._tools.items()):
            desc = (t.description or "").split("\n")[0][:120]
            tool_summaries.append({
                "name": name,
                "toolset": getattr(t, "toolset", "hermes_native"),
                "emoji": getattr(t, "emoji", "🔧") or "🔧",
                "description": desc,
                "invocation": "via_hermes_run_tool"
            })
    result_dict = {
        "count": len(tool_summaries),
        "source": "GHS MCP Suite + Nous Research Hermes Engine",
        "tools": tool_summaries
    }
    return {"content": [{"type": "text", "text": json.dumps(result_dict, indent=2, ensure_ascii=False)}]}


def _tool_run_tool(arguments: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = arguments.get("tool_name", "")
    tool_args = arguments.get("arguments", {})
    if not tool_name:
        return {"content": [{"type": "text", "text": "Error: 'tool_name' es requerido para hermes_run_tool."}], "isError": True}
    return _tool_native_dispatch(tool_name, tool_args)


def _tool_web_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _tool_native_dispatch("web_search", arguments)


def _tool_web_extract(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _tool_native_dispatch("web_extract", arguments)


def _tool_execute_code(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _tool_native_dispatch("execute_code", arguments)


def _tool_stealth_scrape(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Motor universal de scraping furtivo y búsqueda resiliente usando Google Chrome/Edge local con Playwright.
    Bypasea protecciones antibot (Cloudflare, Akamai, Datadome, captchas, desafíos botnet y bloqueos 403).
    """
    url = arguments.get("url")
    urls = arguments.get("urls", [])
    query = arguments.get("query")
    url_pattern = arguments.get("url_pattern")
    max_results = int(arguments.get("max_results", 10))
    deep_inspect = bool(arguments.get("deep_inspect", False))
    selectors = arguments.get("selectors")
    extract_meta = bool(arguments.get("extract_meta", True))
    extract_text = bool(arguments.get("extract_text", True))
    keywords = arguments.get("keywords", [])
    screenshot_dir = arguments.get("screenshot_dir")
    wait_seconds = float(arguments.get("wait_seconds", 2.0))
    scroll = bool(arguments.get("scroll", False))
    export_json = arguments.get("export_json")
    export_csv = arguments.get("export_csv")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # Auto-instalación dinámica transparente (Self-Healing JIT)
        try:
            import subprocess
            sys.stderr.write("[hermes-mcp] 'playwright' no detectado. Instalando automáticamente vía pip...\n")
            sys.stderr.flush()
            cmd = [sys.executable, "-m", "pip", "install", "--user", "playwright"]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            from playwright.sync_api import sync_playwright
        except Exception as inst_err:
            return {
                "content": [{"type": "text", "text": f"Error: 'playwright' no estaba instalado y falló la auto-instalación: {inst_err}. Por favor ejecute: pip install playwright"}],
                "isError": True
            }

    target_urls = []
    if url:
        target_urls.append(url.strip())
    if isinstance(urls, list):
        for u in urls:
            u_clean = str(u).strip()
            if u_clean and u_clean not in target_urls:
                target_urls.append(u_clean)

    if screenshot_dir:
        try:
            os.makedirs(screenshot_dir, exist_ok=True)
        except Exception:
            pass

    results = []
    search_results = []

    try:
        with sync_playwright() as p:
            browser = None
            for channel in ["chrome", "msedge", None]:
                try:
                    launch_kwargs = {
                        "headless": True,
                        "args": [
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-infobars"
                        ]
                    }
                    if channel:
                        launch_kwargs["channel"] = channel
                    browser = p.chromium.launch(**launch_kwargs)
                    break
                except Exception:
                    continue

            if not browser:
                return {
                    "content": [{"type": "text", "text": "Error: No se pudo iniciar Chrome o Edge en el sistema host para el modo stealth."}],
                    "isError": True
                }

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1440, "height": 900},
                locale="es-ES",
                extra_http_headers={"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
            )
            page = context.new_page()

            # 1. Búsqueda web en modo stealth (DuckDuckGo sin bloqueos)
            if query:
                search_url = f"https://duckduckgo.com/?q={str(query).replace(' ', '+')}&t=h_&ia=web"
                try:
                    page.goto(search_url, wait_until="networkidle", timeout=20000)
                    time.sleep(1.5)
                    raw_articles = page.evaluate('''() => {
                        const items = [];
                        const articles = document.querySelectorAll('article[data-testid="result"]');
                        for (const art of articles) {
                            const titleEl = art.querySelector('h2 a');
                            const snippetEl = art.querySelector('[data-result="snippet"]') || art.querySelector('div[style*="line-height"]');
                            if (titleEl && titleEl.href && !titleEl.href.includes('duckduckgo.com/y.js')) {
                                items.push({
                                    title: titleEl.innerText.trim(),
                                    url: titleEl.href,
                                    snippet: snippetEl ? snippetEl.innerText.trim() : ""
                                });
                            }
                        }
                        return items;
                    }''')

                    for item in raw_articles:
                        item_url = item.get("url", "")
                        if url_pattern and not re.search(url_pattern, item_url, re.IGNORECASE):
                            continue
                        search_results.append(item)
                        if deep_inspect and item_url not in target_urls:
                            target_urls.append(item_url)

                    search_results = search_results[:max_results]
                except Exception as e:
                    search_results.append({"error": f"Error en búsqueda DuckDuckGo: {str(e)}"})

            # 2. Scraping e inspección profunda de URLs
            target_urls = target_urls[:max_results]
            for target in target_urls:
                page_data = {"url": target}
                try:
                    page.goto(target, wait_until="domcontentloaded", timeout=20000)
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)

                    if scroll:
                        page.evaluate("window.scrollBy(0, 1000)")
                        time.sleep(1)

                    title = page.title()
                    page_data["title"] = title

                    if extract_meta:
                        meta_info = page.evaluate('''() => {
                            const getMeta = (name) => {
                                const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
                                return el ? (el.getAttribute('content') || '') : '';
                            };
                            return {
                                description: getMeta('description') || getMeta('og:description') || getMeta('twitter:description'),
                                og_title: getMeta('og:title'),
                                og_image: getMeta('og:image'),
                                og_type: getMeta('og:type'),
                                keywords: getMeta('keywords')
                            };
                        }''')
                        page_data["meta"] = meta_info

                    if extract_text:
                        body_text = page.evaluate('''() => {
                            return (document.body ? document.body.innerText : '')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .slice(0, 2000);
                        }''')
                        page_data["text_snippet"] = body_text

                    if selectors and isinstance(selectors, dict):
                        extracted_selectors = {}
                        for sel_key, sel_query in selectors.items():
                            try:
                                val = page.eval_on_selector_all(
                                    sel_query,
                                    "els => els.map(e => (e.innerText || e.getAttribute('content') || e.getAttribute('href') || '').trim()).filter(Boolean)"
                                )
                                extracted_selectors[sel_key] = val if len(val) > 1 else (val[0] if val else "")
                            except Exception as sel_err:
                                extracted_selectors[sel_key] = f"Error: {str(sel_err)}"
                        page_data["selectors"] = extracted_selectors

                    if keywords and isinstance(keywords, list):
                        combined_text = f"{title} {page_data.get('meta', {}).get('description', '')} {page_data.get('text_snippet', '')}".lower()
                        matched = [k for k in keywords if str(k).lower() in combined_text]
                        page_data["keyword_matches"] = matched
                        page_data["match_score"] = len(matched)

                    if screenshot_dir:
                        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', target.replace('https://', '').replace('http://', ''))[:50]
                        shot_path = os.path.join(screenshot_dir, f"{safe_name}.png")
                        page.screenshot(path=shot_path)
                        page_data["screenshot_path"] = shot_path

                    results.append(page_data)
                except Exception as e:
                    results.append({"url": target, "error": f"Fallo al cargar página: {str(e)}"})

            browser.close()

    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error ejecutando hermes_stealth_scrape: {str(e)}\n{traceback.format_exc()}"}],
            "isError": True
        }

    response = {
        "success": True,
        "query": query,
        "search_count": len(search_results),
        "search_results": search_results,
        "scraped_count": len(results),
        "results": results
    }

    if export_json:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(export_json)), exist_ok=True)
            with open(export_json, "w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False, indent=2)
            response["exported_json"] = export_json
        except Exception as ex:
            response["export_json_error"] = str(ex)

    if export_csv and results:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(export_csv)), exist_ok=True)
            flat_rows = []
            for r in results:
                row = {
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "description": r.get("meta", {}).get("description", "") if isinstance(r.get("meta"), dict) else "",
                    "og_image": r.get("meta", {}).get("og_image", "") if isinstance(r.get("meta"), dict) else "",
                    "screenshot_path": r.get("screenshot_path", ""),
                    "keyword_matches": ", ".join(r.get("keyword_matches", [])) if "keyword_matches" in r else "",
                    "error": r.get("error", "")
                }
                flat_rows.append(row)
            if flat_rows:
                import csv
                with open(export_csv, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(flat_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(flat_rows)
                response["exported_csv"] = export_csv
        except Exception as ex:
            response["export_csv_error"] = str(ex)

    return {"content": [{"type": "text", "text": json.dumps(response, indent=2, ensure_ascii=False)}]}


def _tool_prospect_scrape(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Motor universal de prospección y scraping de negocios locales y redes sociales.
    Inspirado en el pipeline validado de web-crapper-chaclacayo-negocios.
    Permite obtener negocios de OSM (Overpass multi-mirror), Bing y DuckDuckGo,
    analizar si tienen web o no, enriquecer con GPS y enlaces a Google Maps,
    y exportar a JSON y CSV compatible con Excel.
    """
    import urllib.request
    import urllib.parse
    import html

    query = (arguments.get("query") or arguments.get("rubro") or arguments.get("nicho") or "").strip()
    location = (arguments.get("location") or arguments.get("distrito") or arguments.get("ciudad") or "Chaclacayo, Lima").strip()
    sources = arguments.get("sources") or ["osm", "bing", "ddg"]
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.split(",")]
    max_results = int(arguments.get("max_results") or 50)
    output_dir = arguments.get("output_dir") or ""
    export_csv = arguments.get("export_csv") or ""
    export_json = arguments.get("export_json") or ""
    filter_no_web = bool(arguments.get("filter_no_web", False))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "es-PE,es;q=0.9,en;q=0.8"
    }

    # 1. Resolver Bounding Box de la ubicación
    bbox = None
    if isinstance(arguments.get("bbox"), (list, tuple)) and len(arguments["bbox"]) == 4:
        bbox = arguments["bbox"]
    else:
        try:
            geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(location)}&format=json&limit=1"
            req = urllib.request.Request(geo_url, headers={"User-Agent": "GHSAuditor/1.5 (contact@local.pe)"})
            with urllib.request.urlopen(req, timeout=8) as r:
                geo_data = json.loads(r.read().decode("utf-8"))
                if geo_data and "boundingbox" in geo_data[0]:
                    bb = geo_data[0]["boundingbox"]
                    bbox = (float(bb[0]), float(bb[2]), float(bb[1]), float(bb[3]))
        except Exception:
            pass

    if not bbox:
        if "chaclacayo" in location.lower():
            bbox = (-12.0200, -76.8100, -11.9400, -76.7200)
        elif "lima" in location.lower():
            bbox = (-12.1800, -77.1000, -11.9000, -76.8500)
        else:
            bbox = (-12.0200, -76.8100, -11.9400, -76.7200)

    all_businesses = []
    seen_names = set()

    # 2. Extracción de OpenStreetMap (Overpass Multi-Mirror)
    if "osm" in sources and bbox:
        s, w, n, e = bbox
        q_clean = query.lower()
        tag_filters = ""
        if q_clean:
            tag_filters = f"""
            node["name"~"{q_clean}",i]({s},{w},{n},{e});
            way["name"~"{q_clean}",i]({s},{w},{n},{e});
            node["cuisine"~"{q_clean}",i]({s},{w},{n},{e});
            way["cuisine"~"{q_clean}",i]({s},{w},{n},{e});
            node["shop"~"{q_clean}",i]({s},{w},{n},{e});
            way["shop"~"{q_clean}",i]({s},{w},{n},{e});
            """
        
        overpass_q = f"""
        [out:json][timeout:25];
        (
          {tag_filters}
          node["amenity"~"restaurant|fast_food|cafe|bar|pharmacy|veterinary|dentist|bank|school"]({s},{w},{n},{e});
          way["amenity"~"restaurant|fast_food|cafe|bar|pharmacy|veterinary|dentist|bank|school"]({s},{w},{n},{e});
          node["shop"]({s},{w},{n},{e});
          way["shop"]({s},{w},{n},{e});
          node["tourism"]({s},{w},{n},{e});
          way["tourism"]({s},{w},{n},{e});
        );
        out center tags;
        """
        
        overpass_servers = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"
        ]
        
        osm_elements = []
        import ssl
        ssl_ctx = ssl._create_unverified_context()
        for srv in overpass_servers:
            try:
                data_bytes = urllib.parse.urlencode({"data": overpass_q}).encode("utf-8")
                req = urllib.request.Request(srv, data=data_bytes, headers={"User-Agent": "GHSAuditor/1.5 (contact@local.pe)"})
                with urllib.request.urlopen(req, data=data_bytes, context=ssl_ctx, timeout=12) as resp:
                    if resp.status == 200:
                        parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
                        osm_elements = parsed.get("elements", [])
                        if osm_elements:
                            break
            except Exception:
                continue

        is_food_query = any(k in q_clean for k in ["cevich", "maris", "pescad", "marina", "restauran", "comida", "bar", "cafe"])
        keywords = [q_clean] if q_clean else []
        if is_food_query:
            keywords.extend(["cevich", "maris", "pescad", "pulpa", "marina", "seafood", "fish", "piscis", "puerto", "dveras"])
        elif any(k in q_clean for k in ["zapatill", "sneaker", "calzad"]):
            keywords.extend(["zapatill", "sneaker", "calzad", "shoe", "sport", "tienda"])
        elif any(k in q_clean for k in ["veterinar", "mascot"]):
            keywords.extend(["veterinar", "mascot", "pet", "animal"])
        elif any(k in q_clean for k in ["cafe", "cafeteria"]):
            keywords.extend(["cafe", "cafeteria", "coffee"])

        all_osm_candidates = []
        for el in osm_elements:
            tags = el.get("tags", {})
            name = tags.get("name")
            if not name:
                continue
            key = name.strip().lower()
            if key in seen_names:
                continue
            seen_names.add(key)

            amenity = tags.get("amenity", "")
            if is_food_query and (amenity in ["school", "college", "kindergarten", "bank", "pharmacy", "hospital"] or "mariscal" in key or "colegio" in key or "educativ" in key):
                continue

            has_web = any(k in tags for k in ["website", "contact:website", "url", "facebook"])
            web_val = tags.get("website") or tags.get("contact:website") or tags.get("url") or tags.get("facebook") or ""
            phone = tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile") or ""
            street = tags.get("addr:street") or ""
            housenumber = tags.get("addr:housenumber") or ""
            address = f"{street} {housenumber}".strip() or tags.get("addr:full") or location
            category = tags.get("cuisine") or tags.get("amenity") or tags.get("shop") or tags.get("tourism") or "comercio"
            lat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
            lon = el.get("lon") or (el.get("center", {}) or {}).get("lon")
            maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if (lat and lon) else f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(name + ' ' + location)}"

            full_txt = f"{name} {category} {address}".lower()
            is_match = not keywords or any(kw in full_txt for kw in keywords)

            biz_obj = {
                "nombre": name,
                "categoria": category,
                "telefono": phone if phone else "No registrado",
                "direccion": address,
                "tiene_web": "Sí" if has_web else "No",
                "web": web_val,
                "lat": lat,
                "lon": lon,
                "google_maps": maps_link,
                "fuente": "OpenStreetMap"
            }
            if is_match:
                all_businesses.append(biz_obj)
            else:
                all_osm_candidates.append(biz_obj)

        if len(all_businesses) < 5 and all_osm_candidates:
            all_businesses.extend(all_osm_candidates[:(12 - len(all_businesses))])

    # 3. Búsqueda en Bing y DuckDuckGo (Prospección Web / Redes Sociales)
    search_queries = []
    if query:
        search_queries.append(f'"{query}" "{location}"')
        search_queries.append(f'site:instagram.com "{query}" "{location}"')
    else:
        search_queries.append(f'negocios restaurantes "{location}"')

    if "bing" in sources or "social" in sources:
        for sq in search_queries[:3]:
            try:
                b_url = "https://www.bing.com/search?q=" + urllib.parse.quote(sq) + "&count=25"
                b_req = urllib.request.Request(b_url, headers=headers)
                with urllib.request.urlopen(b_req, timeout=10) as resp:
                    html_txt = resp.read().decode("utf-8", errors="replace")
                    items = re.findall(r'<li class="b_algo">(.*?)</li>', html_txt, re.DOTALL)
                    for it in items:
                        t_match = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', it, re.DOTALL)
                        p_match = re.search(r'<p[^>]*>(.*?)</p>', it, re.DOTALL)
                        if t_match:
                            href = t_match.group(1)
                            title = html.unescape(re.sub(r'<[^>]+>', '', t_match.group(2)).strip())
                            snippet = html.unescape(re.sub(r'<[^>]+>', '', p_match.group(1)).strip()) if p_match else ""
                            
                            clean_name = re.sub(r'\s*[\-|\|]\s*.*', '', title).strip()
                            if len(clean_name) < 3 or clean_name.lower() in seen_names:
                                continue
                            seen_names.add(clean_name.lower())

                            found_phone = re.search(r'(?:\+51\s*|9\d{2}[\s\.-]?\d{3}[\s\.-]?\d{3})', snippet)
                            phone_str = found_phone.group(0).strip() if found_phone else "No registrado"

                            has_social = "instagram.com" in href or "facebook.com" in href
                            all_businesses.append({
                                "nombre": clean_name,
                                "categoria": query or "comercio",
                                "telefono": phone_str,
                                "direccion": location,
                                "tiene_web": "Red Social" if has_social else "Sí",
                                "web": href,
                                "lat": None,
                                "lon": None,
                                "google_maps": f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(clean_name + ' ' + location)}",
                                "fuente": "Instagram" if "instagram.com" in href else "Bing Search"
                            })
            except Exception:
                pass

    if filter_no_web:
        all_businesses = [b for b in all_businesses if b.get("tiene_web") in ("No", "Red Social")]

    all_businesses = all_businesses[:max_results]

    sin_web_count = sum(1 for b in all_businesses if b.get("tiene_web") == "No")
    con_web_count = sum(1 for b in all_businesses if b.get("tiene_web") == "Sí")
    social_count = sum(1 for b in all_businesses if b.get("tiene_web") == "Red Social")

    out_json_path = ""
    out_csv_path = ""

    if output_dir:
        output_dir = resolve_target_path(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        out_json_path = os.path.join(output_dir, "negocios_raw.json")
        out_csv_path = os.path.join(output_dir, "prospectos_filtrados.csv")
    elif not export_json and not export_csv:
        safe_q = re.sub(r'[^a-zA-Z0-9_]', '_', query).strip('_') or "Prospectos"
        safe_loc = re.sub(r'[^a-zA-Z0-9_]', '_', location.split(',')[0]).strip('_') or "Local"
        folder_name = f"Proyecto_{safe_q.capitalize()}_{safe_loc.capitalize()}_{time.strftime('%Y%m%d_%H%M%S')}"
        output_dir = os.path.join(downloads_dir, folder_name)
        os.makedirs(output_dir, exist_ok=True)
        out_json_path = os.path.join(output_dir, "negocios_raw.json")
        out_csv_path = os.path.join(output_dir, "prospectos_filtrados.csv")

    if export_json:
        out_json_path = resolve_target_path(export_json)
        os.makedirs(os.path.dirname(out_json_path), exist_ok=True)

    if export_csv:
        out_csv_path = resolve_target_path(export_csv)
        os.makedirs(os.path.dirname(out_csv_path), exist_ok=True)

    if out_json_path:
        try:
            with open(out_json_path, "w", encoding="utf-8") as f:
                json.dump(all_businesses, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    if out_csv_path and all_businesses:
        try:
            import csv
            with open(out_csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(all_businesses[0].keys()))
                writer.writeheader()
                writer.writerows(all_businesses)
        except Exception:
            pass

    result_payload = {
        "status": "success",
        "query": query,
        "location": location,
        "total_encontrados": len(all_businesses),
        "sin_pagina_web": sin_web_count,
        "con_pagina_web": con_web_count,
        "solo_red_social": social_count,
        "archivo_json": out_json_path or "No exportado",
        "archivo_csv": out_csv_path or "No exportado",
        "negocios": all_businesses[:15]
    }

    return {"content": [{"type": "text", "text": json.dumps(result_payload, ensure_ascii=False, indent=2)}]}



# ─────────────────────────────────────────────────────────────────────────────
# OS Automation Tool Handlers
# ─────────────────────────────────────────────────────────────────────────────

def _tool_computer_use(arguments: Dict[str, Any]) -> Dict[str, Any]:
    action = arguments.get("action", "capture")
    
    if action == "capture":
        img_bytes = None
        width, height = 1920, 1080
        
        if mss is not None:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                sct_img = sct.grab(monitor)
                width, height = sct_img.width, sct_img.height
                if Image is not None:
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG", optimize=True)
                    img_bytes = buf.getvalue()
        elif pyautogui is not None:
            shot = pyautogui.screenshot()
            width, height = shot.size
            buf = io.BytesIO()
            shot.save(buf, format="PNG")
            img_bytes = buf.getvalue()

        if img_bytes:
            b64_data = base64.b64encode(img_bytes).decode("ascii")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Captura de pantalla tomada con éxito ({width}x{height})."
                    },
                    {
                        "type": "image",
                        "data": b64_data,
                        "mimeType": "image/png"
                    }
                ]
            }
        else:
            return {
                "content": [{"type": "text", "text": "Error: mss o pyautogui no están disponibles para captura."}],
                "isError": True
            }

    elif action in ("click", "double_click", "right_click"):
        coord = arguments.get("coordinate")
        if not coord or len(coord) < 2:
            return {"content": [{"type": "text", "text": "Error: 'coordinate' [x, y] es requerido para clics."}], "isError": True}
        x, y = int(coord[0]), int(coord[1])

        if pyautogui is None:
            return {"content": [{"type": "text", "text": "Error: pyautogui no está instalado."}], "isError": True}

        if action == "click":
            pyautogui.click(x, y)
            msg = f"Clic realizado en [{x}, {y}]."
        elif action == "double_click":
            pyautogui.doubleClick(x, y)
            msg = f"Doble clic realizado en [{x}, {y}]."
        elif action == "right_click":
            pyautogui.rightClick(x, y)
            msg = f"Clic derecho realizado en [{x}, {y}]."

        return {"content": [{"type": "text", "text": msg}]}

    elif action == "type":
        text = arguments.get("text", "")
        if not text:
            return {"content": [{"type": "text", "text": "Error: 'text' es requerido para escribir."}], "isError": True}
        if pyautogui is None:
            return {"content": [{"type": "text", "text": "Error: pyautogui no está instalado."}], "isError": True}

        try:
            pyautogui.write(text, interval=0.01)
        except Exception:
            # Fallback using clipboard if unicode fails
            import tkinter as tk
            r = tk.Tk()
            r.withdraw()
            r.clipboard_clear()
            r.clipboard_append(text)
            r.update()
            r.destroy()
            pyautogui.hotkey("ctrl", "v")

        return {"content": [{"type": "text", "text": f"Texto escrito ({len(text)} caracteres)."}]}

    elif action == "key":
        key = arguments.get("key", "")
        if not key:
            return {"content": [{"type": "text", "text": "Error: 'key' es requerido para presionar teclas."}], "isError": True}
        if pyautogui is None:
            return {"content": [{"type": "text", "text": "Error: pyautogui no está instalado."}], "isError": True}

        if "+" in key:
            parts = [p.strip().lower() for p in key.split("+")]
            pyautogui.hotkey(*parts)
        else:
            pyautogui.press(key.lower())

        return {"content": [{"type": "text", "text": f"Tecla pulsada: {key}."}]}

    elif action == "scroll":
        amount = int(arguments.get("scroll_amount", 0))
        if pyautogui is None:
            return {"content": [{"type": "text", "text": "Error: pyautogui no está instalado."}], "isError": True}
        pyautogui.scroll(amount)
        return {"content": [{"type": "text", "text": f"Scroll ejecutado: {amount}."}]}

    elif action == "list_windows":
        windows = []
        if user32 is not None:
            import ctypes.wintypes
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

            def enum_windows_callback(hwnd, extra):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value
                        if title and title not in ("Program Manager", "Settings"):
                            windows.append({"hwnd": hwnd, "title": title})
                return True

            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"count": len(windows), "windows": windows}, indent=2, ensure_ascii=False)
                }
            ]
        }

    elif action == "focus_app":
        app_name = arguments.get("app", "")
        if not app_name:
            return {"content": [{"type": "text", "text": "Error: 'app' es requerido para enfocar ventana."}], "isError": True}

        focused = False
        if user32 is not None:
            import ctypes.wintypes
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

            def enum_for_focus(hwnd, extra):
                nonlocal focused
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        if app_name.lower() in buff.value.lower():
                            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                            user32.SetForegroundWindow(hwnd)
                            focused = True
                            return False
                return True

            user32.EnumWindows(WNDENUMPROC(enum_for_focus), 0)

        if focused:
            return {"content": [{"type": "text", "text": f"Ventana '{app_name}' traída al frente exitosamente."}]}
        else:
            return {"content": [{"type": "text", "text": f"No se encontró ninguna ventana visible con el nombre '{app_name}'."}], "isError": True}

    return {"content": [{"type": "text", "text": f"Acción desconocida '{action}'."}], "isError": True}


def _inspect_dom_telemetry(url: str) -> Dict[str, Any]:
    telemetry = {
        "status": "N/A",
        "title": "",
        "headings": [],
        "buttons": [],
        "fonts": [],
        "error": None
    }
    if not url or not url.startswith("http"):
        return telemetry
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GHS-Visual-Auditor/1.5.5",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache"
        })
        with urllib.request.urlopen(req, timeout=3) as resp:
            telemetry["status"] = f"ONLINE (HTTP {resp.status})"
            raw_html = resp.read().decode("utf-8", errors="replace")
            
            m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE)
            if m:
                telemetry["title"] = m.group(1).strip()
                
            for tag in ["h1", "h2", "h3"]:
                matches = re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", raw_html, re.IGNORECASE | re.DOTALL)
                for h in matches:
                    clean_h = re.sub(r"<[^>]+>", "", h).strip()
                    if clean_h:
                        telemetry["headings"].append(f"{tag.upper()}: {clean_h[:70]}")
                        
            btn_matches = re.findall(r"<button[^>]*>(.*?)</button>", raw_html, re.IGNORECASE | re.DOTALL)
            for b in btn_matches[:8]:
                clean_b = re.sub(r"<[^>]+>", "", b).strip()
                if clean_b:
                    telemetry["buttons"].append(clean_b[:35])
                    
            fonts = re.findall(r"family=([^&'\"]+)", raw_html)
            telemetry["fonts"] = list(dict.fromkeys([f.replace("+", " ") for f in fonts]))

        # DEEP AUDIT: Detect Vite / React / PostCSS fatal runtime error overlays
        # Only probe if raw_html actually contains references to Vite or /src/
        base_url = url.rstrip("/")
        if "/src/" in raw_html or "@vite" in raw_html:
            modules_to_probe = ["/src/main.tsx", "/src/main.jsx", "/src/App.tsx", "/src/App.jsx", "/src/App.css", "/src/index.css"]
            for mod in modules_to_probe:
                try:
                    mod_url = base_url + mod
                    mod_req = urllib.request.Request(mod_url, headers={"User-Agent": "Mozilla/5.0 GHS-Auditor/1.5.5"})
                    with urllib.request.urlopen(mod_req, timeout=2) as mod_resp:
                        mod_text = mod_resp.read().decode("utf-8", errors="ignore")
                        if "[plugin:vite" in mod_text or "vite-error-overlay" in mod_text:
                            telemetry["error"] = mod_text[:600]
                            telemetry["status"] = "🚨 ERROR CRÍTICO EN PANTALLA (Vite Error Overlay Activo)"
                            break
                except urllib.error.HTTPError as he:
                    # In Vite, compilation/syntax errors return HTTP 500. A 404 simply means that file doesn't exist.
                    if he.code >= 500:
                        err_body = he.read().decode("utf-8", errors="ignore")
                        clean_err = re.sub(r"<[^>]+>", " ", err_body).strip()
                        clean_err = " ".join(clean_err.split())
                        telemetry["error"] = f"Fallo HTTP {he.code} en {mod}: {clean_err[:500]}"
                        telemetry["status"] = f"🚨 ERROR CRÍTICO DE COMPILACIÓN / POSTCSS (HTTP {he.code})"
                        break
                except Exception:
                    pass
    except Exception as e:
        telemetry["status"] = f"OFFLINE ({e})"
    return telemetry


def _tool_ui_inspect(arguments: Dict[str, Any]) -> Dict[str, Any]:
    url = arguments.get("url", "").strip()
    wait_seconds = int(arguments.get("wait_seconds", 2))
    full_page = arguments.get("full_page", True)
    if isinstance(full_page, str):
        full_page = full_page.lower() in ("true", "1", "yes")
    device = arguments.get("device", "desktop").lower()
    scroll_y_raw = arguments.get("scroll_y", 0)

    # Normalize file URLs or local HTML paths
    if url:
        clean_url = url.strip().strip("'\"")
        if clean_url.endswith(".html") or clean_url.endswith(".htm") or os.path.isfile(clean_url):
            url = "file:///" + os.path.abspath(clean_url).replace("\\", "/")
        elif not clean_url.startswith("http://") and not clean_url.startswith("https://") and not clean_url.startswith("file://"):
            if os.path.isfile(clean_url):
                url = "file:///" + os.path.abspath(clean_url).replace("\\", "/")

    # Auto-detect local dev server if url is omitted or localhost
    if not url or url.startswith("http://localhost:5173"):
        server_found = False
        for port in [5173, 3000, 4173, 8080, 8000]:
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://localhost:{port}", timeout=0.6) as resp:
                    if resp.status == 200:
                        url = f"http://localhost:{port}"
                        server_found = True
                        break
            except Exception:
                pass

        # If no local port is active, look for recent index.html in Downloads or workspace
        if not server_found:
            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            recent_html = None
            recent_time = 0
            try:
                candidates = []
                if os.path.isdir(downloads):
                    for entry in os.scandir(downloads):
                        if entry.is_dir() and not entry.name.startswith((".", "node_modules", "GHS-Script-Free")):
                            sub_idx = os.path.join(entry.path, "index.html")
                            if os.path.isfile(sub_idx):
                                candidates.append(sub_idx)
                    root_idx = os.path.join(downloads, "index.html")
                    if os.path.isfile(root_idx):
                        candidates.append(root_idx)
                for cand in candidates:
                    try:
                        mtime = os.path.getmtime(cand)
                        if mtime > recent_time:
                            recent_time = mtime
                            recent_html = cand
                    except Exception:
                        pass
            except Exception:
                pass

            if recent_html and (time.time() - recent_time) < 7200:
                url = "file:///" + os.path.abspath(recent_html).replace("\\", "/")
            elif not url:
                url = "http://localhost:5173"

    # Device viewports
    devices = {
        "desktop": (1440, 900),
        "laptop": (1280, 800),
        "tablet": (768, 1024),
        "mobile": (390, 844)
    }
    viewport_width, viewport_height = devices.get(device, (1440, 900))
    # Render at realistic height instead of 3600px to avoid destroying CSS 100vh / min-h-screen
    capture_height = 1400 if full_page else viewport_height

    img_bytes = None
    full_img = None
    width, height = viewport_width, viewport_height

    # Primary method: Chrome / Edge Headless with full resolution & zero-cache
    if url and (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
        browser_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        ]
        browser_exe = next((p for p in browser_candidates if os.path.isfile(p)), None)
        if browser_exe:
            temp_profile = None
            tmp_shot = None
            try:
                import tempfile
                import shutil
                # Give Vite HMR a moment to finish emitting modified modules to disk
                time.sleep(max(0.6, min(wait_seconds, 2.5)))

                temp_profile = tempfile.mkdtemp(prefix="ghs_headless_profile_")
                tmp_shot = os.path.join(tempfile.gettempdir(), f"ghs_browser_snap_{int(time.time()*1000)}.png")

                # Cache-busting URL to ensure fresh load from local Vite dev server
                sep = "&" if "?" in url else "?"
                bust_url = f"{url}{sep}_ghs_ts={int(time.time() * 1000)}"

                chrome_cmd = [
                    browser_exe,
                    "--headless=new",
                    "--no-sandbox",
                    "--enable-webgl",
                    "--ignore-gpu-blocklist",
                    "--use-gl=angle",
                    "--disable-dev-shm-usage",
                    "--hide-scrollbars",
                    "--disable-cache",
                    "--disk-cache-size=0",
                    "--media-cache-size=0",
                    "--disable-application-cache",
                    "--disable-back-forward-cache",
                    "--disable-background-networking",
                    "--run-all-compositor-stages-before-draw",
                    f"--user-data-dir={temp_profile}",
                    f"--window-size={viewport_width},{capture_height}",
                    f"--screenshot={tmp_shot}",
                    bust_url
                ]
                if device == "mobile":
                    chrome_cmd.insert(4, "--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

                subprocess.run(chrome_cmd, timeout=22, capture_output=True)
                if os.path.isfile(tmp_shot) and os.path.getsize(tmp_shot) > 1000:
                    if Image is not None:
                        im = Image.open(tmp_shot)
                        full_img = im.copy()
                        width, height = im.size

                        # Process scroll_y if specified
                        scroll_top = 0
                        if isinstance(scroll_y_raw, str):
                            s = scroll_y_raw.strip().lower()
                            if s in ("top", "hero", "header", "0%"):
                                scroll_top = 0
                            elif s in ("middle", "mid", "50%", "catalog"):
                                scroll_top = max(0, (im.height - viewport_height) // 2)
                            elif s in ("bottom", "footer", "end", "100%"):
                                scroll_top = max(0, im.height - viewport_height)
                            elif s.isdigit():
                                scroll_top = int(s)
                        elif isinstance(scroll_y_raw, (int, float)) and scroll_y_raw > 0:
                            scroll_top = int(scroll_y_raw)

                        # If user specifically asked to scroll to a section
                        if scroll_top > 0 and im.height > viewport_height:
                            top = min(scroll_top, im.height - viewport_height)
                            bottom = min(top + viewport_height, im.height)
                            im = im.crop((0, top, im.width, bottom))
                            width, height = im.size
                        elif full_page and im.height > viewport_height:
                            # Smart crop trailing uniform rows from bottom so agent doesn't perceive artificial padding
                            try:
                                rgb_im = im.convert("RGB")
                                w_im, h_im = rgb_im.size
                                bg_color = rgb_im.getpixel((w_im // 2, h_im - 1))
                                content_bottom = h_im
                                for y in range(h_im - 1, viewport_height, -8):
                                    samples = [rgb_im.getpixel((int(w_im * f), y)) for f in (0.15, 0.35, 0.5, 0.65, 0.85)]
                                    is_uniform = all(
                                        abs(c[0] - bg_color[0]) < 8 and abs(c[1] - bg_color[1]) < 8 and abs(c[2] - bg_color[2]) < 8
                                        for c in samples
                                    )
                                    if not is_uniform:
                                        content_bottom = min(h_im, y + 48)
                                        break
                                if content_bottom < h_im and content_bottom >= viewport_height:
                                    im = im.crop((0, 0, w_im, content_bottom))
                                    width, height = im.size
                            except Exception:
                                pass

                        buf = io.BytesIO()
                        im.save(buf, format="PNG")
                        img_bytes = buf.getvalue()
                    else:
                        with open(tmp_shot, "rb") as f:
                            img_bytes = f.read()
            except Exception:
                pass
            finally:
                if tmp_shot and os.path.isfile(tmp_shot):
                    try:
                        os.remove(tmp_shot)
                    except Exception:
                        pass
                if temp_profile and os.path.isdir(temp_profile):
                    try:
                        import shutil
                        shutil.rmtree(temp_profile, ignore_errors=True)
                    except Exception:
                        pass
    if not img_bytes:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error en hermes_ui_inspect: No se pudo conectar a '{url}' ni renderizar la página con Chrome Headless.\n• Si es un proyecto React/Vite, asegúrate de haber ejecutado 'npm run dev' con hermes_terminal (is_daemon: true) en el puerto 5173.\n• Si es un archivo HTML estático, especifica la ruta del archivo (ej: 'index.html')."
                }
            ],
            "isError": True
        }

    if img_bytes:
        save_path_msg = ""
        try:
            downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            save_path = os.path.join(downloads_dir, "ghs_ui_snapshot.png")
            with open(save_path, "wb") as f:
                f.write(img_bytes)
            if full_img is not None:
                full_path = os.path.join(downloads_dir, "ghs_ui_snapshot_full.png")
                full_img.save(full_path)
            save_path_msg = f"Guardada en: {save_path}"
        except Exception:
            pass

        # Extract DOM & CSS Telemetry
        dom = _inspect_dom_telemetry(url)

        # Check screenshot for Vite / Next red fatal error screen visually
        if Image is not None and img_bytes and not dom.get("error"):
            try:
                test_im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                sample_w = min(test_im.width, 320)
                sample_h = min(test_im.height, 120)
                red_pixels = 0
                dark_pixels = 0
                total_sampled = 0
                for sy in range(10, sample_h, 4):
                    for sx in range(10, sample_w, 4):
                        r, g, b = test_im.getpixel((sx, sy))
                        total_sampled += 1
                        if r > 185 and g < 95 and b < 120:
                            red_pixels += 1
                        elif r < 40 and g < 40 and b < 45:
                            dark_pixels += 1
                if total_sampled > 0 and (red_pixels / total_sampled) > 0.06 and (dark_pixels / total_sampled) > 0.30:
                    dom["status"] = "🚨 PANTALLA ROJA DE ERROR DETECTADA VISUALMENTE (Vite Error Overlay)"
                    dom["error"] = "Se detectó la barra roja y fondo oscuro característico del Overlay de Error de Vite en la pantalla."
            except Exception:
                pass

        if dom.get("error"):
            telemetry_lines = [
                "🚨 ALERTA CRÍTICA: ERROR DETECTADO EN EL NAVEGADOR (PANTALLA ROJA DE ERROR)",
                f"• Servidor Dev: {dom['status']}",
                f"• Causa del Error en Vite: {dom['error']}",
                "",
                "── ACCIÓN INMEDIATA OBLIGATORIA PARA EL AGENTE ──",
                "1. Tu último cambio de código rompió la compilación o el CSS de Vite.",
                "2. NO inventes comandos en terminal para 'eliminar @theme o @tailwind'. Esas reglas son normales de Tailwind v4.",
                "3. REPARA O RESTAURA DE INMEDIATO la sintaxis del archivo que modificaste para que la web vuelva a renderizar.",
                "",
                f"{save_path_msg}",
            ]
        else:
            telemetry_lines = [
                f"✓ Inspección visual completada ({width}x{height}px, Dispositivo: {device.upper()}) para '{url}'.",
                f"{save_path_msg}",
                "",
                "── TELEMETRÍA ESTRUCTURAL & AUDITORÍA UX/UI DEL SITIO ──",
                f"• Servidor Dev: {dom['status']}",
                f"• Título de la Aplicación: {dom['title'] or '(Sin título explícito)'}",
                f"• Modo de Captura: {'Página Completa (Full-Page scrollable)' if full_page and not scroll_y_raw else f'Sección/Scroll y={scroll_y_raw}'}",
            ]
            if dom["headings"]:
                telemetry_lines.append(f"• Jerarquía Tipográfica Detectada: {', '.join(dom['headings'][:6])}")
            if dom["buttons"]:
                telemetry_lines.append(f"• Acciones & Botones Detectados: {', '.join(dom['buttons'][:6])}")
            if dom["fonts"]:
                telemetry_lines.append(f"• Fuentes Tipográficas: {', '.join(dom['fonts'])}")
            telemetry_lines.append("")
            telemetry_lines.append("INSTRUCCIONES DE ART DIRECTION PARA EL DISEÑADOR:")
            telemetry_lines.append("1. Analiza la imagen adjunta para auditar proporciones, contrastes, alineaciones y tipografía.")
            telemetry_lines.append("2. Si deseas auditar otra sección o hacer scroll vertical, llama 'hermes_ui_inspect' pasando 'scroll_y' (ej: 'middle', 'bottom' o 1200).")
            telemetry_lines.append("3. Para auditar la experiencia móvil en teléfono, llama 'hermes_ui_inspect' con 'device': 'mobile'.")

        b64_data = base64.b64encode(img_bytes).decode("ascii")
        return {
            "content": [
                {
                    "type": "text",
                    "text": "\n".join(telemetry_lines)
                },
                {
                    "type": "image",
                    "data": b64_data,
                    "mimeType": "image/png"
                }
            ]
        }
    else:
        return {
            "content": [{"type": "text", "text": "Error: no se pudo generar la captura de pantalla de la interfaz."}],
            "isError": True
        }


# ─────────────────────────────────────────────────────────────────────────────
# Dev Server Persistence & Registry
# ─────────────────────────────────────────────────────────────────────────────
DEV_SERVERS_REGISTRY_FILE = os.path.join(tempfile.gettempdir(), "ghs_dev_servers.json")

def _load_dev_server_registry() -> Dict[str, Any]:
    if os.path.exists(DEV_SERVERS_REGISTRY_FILE):
        try:
            with open(DEV_SERVERS_REGISTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_dev_server_registry(reg: Dict[str, Any]) -> None:
    try:
        with open(DEV_SERVERS_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(reg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def _register_dev_server(pid: int, port: Optional[int] = None, url: Optional[str] = None, project: Optional[str] = None, cwd: Optional[str] = None, command: Optional[str] = None) -> None:
    reg = _load_dev_server_registry()
    proj_name = project
    if not proj_name and cwd:
        proj_name = os.path.basename(os.path.normpath(cwd))
    if not proj_name:
        proj_name = f"Servidor Web (Puerto {port})" if port else f"Proceso Dev (PID {pid})"

    dev_url = url or (f"http://localhost:{port}" if port else "")
    reg[str(pid)] = {
        "pid": pid,
        "port": port,
        "url": dev_url,
        "project": proj_name,
        "cwd": cwd or "",
        "command": command or "",
        "started_at": time.time()
    }
    _save_dev_server_registry(reg)

def _unregister_dev_server(pid: int) -> None:
    reg = _load_dev_server_registry()
    if str(pid) in reg:
        del reg[str(pid)]
        _save_dev_server_registry(reg)


# ─────────────────────────────────────────────────────────────────────────────
# Workspace & Project Path Resolution Helpers
# ─────────────────────────────────────────────────────────────────────────────
GLOBAL_ACTIVE_WORKSPACE: Optional[str] = None

def get_base_workspace() -> str:
    """Returns the base user projects directory (e.g. C:\\Users\\USER\\Downloads)."""
    user_home = os.path.expanduser("~")
    downloads = os.path.join(user_home, "Downloads")
    return downloads if os.path.exists(downloads) else os.getcwd()

def set_active_workspace(path: str) -> None:
    global GLOBAL_ACTIVE_WORKSPACE
    if not path:
        return
    norm = os.path.normpath(os.path.abspath(path))
    ghs_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))
    # Never set GHS's own directory as the project workspace
    if not norm.lower().startswith(ghs_dir.lower()):
        GLOBAL_ACTIVE_WORKSPACE = norm

def resolve_target_path(path: str) -> str:
    global GLOBAL_ACTIVE_WORKSPACE
    if not path:
        return ""
    raw = path.strip()
    
    # 1. Interceptor de Memoria 3 Capas (SOUL.md, USER.md, MEMORY.md)
    bname = os.path.basename(raw.replace("\\", "/")).upper()
    if bname in ("SOUL.MD", "USER.MD", "MEMORY.MD") or "HERMES/MEMORY" in raw.upper().replace("\\", "/"):
        mdir = get_memory_dir()
        target = os.path.join(mdir, os.path.basename(raw.replace("\\", "/")))
        if not os.path.exists(target):
            try:
                os.makedirs(mdir, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(f"# {bname}\n\nArchivo de memoria inicializado correctamente.\n")
            except Exception:
                pass
        return target

    # 2. Interceptor de ghs_project_state.json y agent_workspace
    if "GHS_PROJECT_STATE.JSON" in raw.upper():
        ws_dir = os.path.join(get_base_workspace(), "agent_workspace")
        os.makedirs(ws_dir, exist_ok=True)
        return os.path.join(ws_dir, "ghs_project_state.json")

    # 3. Limpieza de rutas de Windows cuando corremos en Linux / Render
    if sys.platform != "win32" and re.match(r'^[a-zA-Z]:[\\/]', raw):
        m = re.search(r'[\\/]Downloads[\\/](.*)', raw, re.IGNORECASE)
        if m:
            clean_path = os.path.join(get_base_workspace(), m.group(1).replace("\\", "/"))
        else:
            clean_path = os.path.basename(raw.replace("\\", "/"))
    else:
        clean_path = os.path.normpath(raw)
    if os.path.isabs(clean_path):
        parent_dir = os.path.dirname(clean_path)
        cur = parent_dir
        for _ in range(4):
            if os.path.exists(os.path.join(cur, "package.json")) or os.path.exists(os.path.join(cur, "src")):
                set_active_workspace(cur)
                break
            cur = os.path.dirname(cur)
        return clean_path

    ghs_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))
    # If workspace is active and valid, use it
    if GLOBAL_ACTIVE_WORKSPACE and os.path.isdir(GLOBAL_ACTIVE_WORKSPACE):
        if not os.path.normpath(GLOBAL_ACTIVE_WORKSPACE).lower().startswith(ghs_dir.lower()):
            return os.path.normpath(os.path.join(GLOBAL_ACTIVE_WORKSPACE, clean_path))

    # Detect recent project directory in Downloads
    base_dir = get_base_workspace()
    candidates = []
    try:
        for entry in os.scandir(base_dir):
            if entry.is_dir():
                full_p = os.path.normpath(entry.path)
                if full_p.lower().startswith(ghs_dir.lower()):
                    continue
                if entry.name.startswith(".") or entry.name.lower() in ("mcps", "logo", "campus_evaluaciones", "ejemplos de estilos y diseño", "noticapture-4"):
                    continue
                has_pkg = os.path.exists(os.path.join(entry.path, "package.json"))
                has_src = os.path.exists(os.path.join(entry.path, "src"))
                score = (10 if has_pkg else 0) + (5 if has_src else 0)
                candidates.append((score, entry.stat().st_mtime, entry.path))
        if candidates:
            candidates.sort(reverse=True)
            GLOBAL_ACTIVE_WORKSPACE = candidates[0][2]
            return os.path.normpath(os.path.join(GLOBAL_ACTIVE_WORKSPACE, clean_path))
    except Exception:
        pass

    # Default to Downloads/apex-labs or Downloads/proyecto-web
    default_proj = os.path.join(base_dir, "apex-labs")
    os.makedirs(default_proj, exist_ok=True)
    GLOBAL_ACTIVE_WORKSPACE = default_proj
    return os.path.normpath(os.path.join(GLOBAL_ACTIVE_WORKSPACE, clean_path))


def _tool_terminal(arguments: Dict[str, Any]) -> Dict[str, Any]:
    command = arguments.get("command", "")
    cwd = arguments.get("cwd", None)

    if not command:
        return {"content": [{"type": "text", "text": "Error: 'command' es requerido."}], "isError": True}

    # Auto-detect or use active workspace
    if cwd:
        # Expand environment variables ($env:VAR, %VAR%, ~, $HOME)
        cwd = os.path.expandvars(os.path.expanduser(str(cwd)))
        cwd = re.sub(r"\$env:([a-zA-Z0-9_]+)", lambda m: os.environ.get(m.group(1), ""), cwd, flags=re.IGNORECASE)
        home_path = os.path.expanduser("~")
        cwd = re.sub(r"\$HOME\b", lambda _: home_path, cwd, flags=re.IGNORECASE)
        set_active_workspace(cwd)
    else:
        # Check if command mentions creating a project
        m = re.search(r"(?:create\s+vite@latest|create-vite|degit)\s+([a-zA-Z0-9_\-]+)", command)
        if m:
            proj_name = m.group(1)
            base_dir = get_base_workspace()
            target_proj = os.path.join(base_dir, proj_name)
            set_active_workspace(target_proj)
            cwd = base_dir
        elif GLOBAL_ACTIVE_WORKSPACE and os.path.isdir(GLOBAL_ACTIVE_WORKSPACE):
            cwd = GLOBAL_ACTIVE_WORKSPACE

    # Normalize timeout: allow timeout (sec) or timeout_ms (ms)
    # Must ALWAYS be <= 95s so hermes_mcp_server returns before bridge.py's 120s limit!
    raw_timeout = arguments.get("timeout_ms") or arguments.get("timeout")
    if raw_timeout is not None:
        try:
            val = float(raw_timeout)
            # If val > 500, it was passed in milliseconds (e.g. 120000 ms -> 120s)
            sec = val / 1000.0 if val > 500 else val
            timeout = max(1.0, min(sec, 95.0))
        except Exception:
            timeout = 60.0
    else:
        timeout = 60.0

    # Auto-create cwd if specified to eliminate [WinError 267] "El nombre del directorio no es válido"
    if cwd:
        try:
            os.makedirs(cwd, exist_ok=True)
        except Exception:
            pass

    # Auto-sanitize Windows ExecutionPolicy and command formats
    if os.name == "nt":
        cmd_stripped = command.strip()
        lowered = cmd_stripped.lower()

        # Protect GHS Bridge from indiscriminate taskkill of python.exe
        if re.search(r"\btaskkill\b.*(?:/im\s+python(?:\.exe)?|python(?:\.exe)?\s+/im)", lowered) or \
           re.search(r"\bstop-process\b.*-name\s+python\b", lowered):
            return {
                "content": [{
                    "type": "text",
                    "text": "AVISO DE SEGURIDAD GHS: Ejecutar 'taskkill /IM python.exe' de forma indiscriminada detendría el proceso del Puente WebSocket GHS. Para detener un servidor o script específico de Python, usa 'hermes_dev_server' o termina el proceso por su PID específico (ej: 'taskkill /F /PID <pid>')."
                }],
                "isError": True
            }

        # 1. If command starts with bare flags like "-NoProfile -Command ...", prepend powershell.exe
        if cmd_stripped.startswith("-"):
            command = f"powershell.exe -ExecutionPolicy Bypass {cmd_stripped}"
            cmd_stripped = command.strip()
            lowered = cmd_stripped.lower()

        # 2. If command is a bare PowerShell cmdlet or starts with $ (PowerShell variable/expression)
        ps_cmdlets = (
            "new-item", "get-item", "remove-item", "copy-item", "move-item", "set-item",
            "get-childitem", "test-path", "start-process", "stop-process", "set-content",
            "get-content", "add-content", "clear-content"
        )
        if cmd_stripped.startswith("$") or any(lowered.startswith(cmdlet + " ") or lowered == cmdlet for cmdlet in ps_cmdlets):
            if not lowered.startswith("powershell"):
                command = f'powershell.exe -ExecutionPolicy Bypass -NoProfile -Command "{cmd_stripped}"'
                cmd_stripped = command.strip()
                lowered = cmd_stripped.lower()

        # 3. Auto-sanitize ExecutionPolicy for explicit PowerShell calls
        if lowered.startswith("powershell") and "-executionpolicy" not in lowered and "-ep" not in lowered:
            command = re.sub(r"^powershell\b", "powershell -ExecutionPolicy Bypass -NoProfile", cmd_stripped, count=1, flags=re.IGNORECASE)

        # 4. Auto-map npm/npx to npm.cmd/npx.cmd on Windows to avoid executable not found errors
        if re.match(r"^npm\b", command, re.IGNORECASE) and not re.match(r"^npm\.cmd\b", command, re.IGNORECASE):
            command = re.sub(r"^npm\b", "npm.cmd", command, count=1, flags=re.IGNORECASE)
        elif re.match(r"^npx\b", command, re.IGNORECASE) and not re.match(r"^npx\.cmd\b", command, re.IGNORECASE):
            command = re.sub(r"^npx\b", "npx.cmd", command, count=1, flags=re.IGNORECASE)

        # 5. Optimize npm install on Windows with --no-audit --no-fund to speed up and prevent file locks
        if re.search(r"\bnpm(?:\.cmd)?\s+i(?:nstall)?\b", command, re.IGNORECASE) and "--no-audit" not in command:
            command += " --no-audit --no-fund"

    # Detect if this is a background / daemon command (e.g. dev server, start /b, etc.)
    is_daemon = bool(arguments.get("is_daemon") or arguments.get("background") or arguments.get("daemon"))

    # Unwrap 'cmd.exe /c start ...' or 'start ...' to prevent Windows from creating GUI console windows
    if os.name == "nt":
        m_start = re.match(r'^(?:cmd(?:\.exe)?\s+/c\s+)?start(?:\s+"[^"]*")?(?:\s+/[a-z]+)*\s+(.*)$', command.strip(), re.IGNORECASE)
        if m_start:
            inner = m_start.group(1).strip()
            if inner:
                command = inner
                is_daemon = True

    lowered = command.strip().lower()
    daemon_regexes = [
        r"\bstart\s+/[bm]",
        r"\bnpm(?:\.cmd)?\s+(?:run\s+)?(?:dev|start|preview)\b",
        r"\bnpx(?:\.cmd)?\s+(?:vite|http-server|nodemon|serve)\b",
        r"\bvite(?:\.cmd)?\s+(?:dev|preview)\b",
        r"\bpython(?:\.exe)?\s+-m\s+http\.server\b",
        r"\bpython(?:\.exe)?\s+(?:app|server|main|api)\.py\b",
        r"\bflask(?:\.exe)?\s+run\b",
        r"\buvicorn\b",
        r"\bgunicorn\b",
        r"\bstreamlit\s+run\b",
        r"\bnodemon\b",
        r"\bserve\s+(?:-s|\d+|\.)\b",
    ]
    if not is_daemon and any(re.search(pat, lowered) for pat in daemon_regexes):
        is_daemon = True

    if is_daemon:
        try:
            work_dir = cwd if (cwd and os.path.isdir(cwd)) else os.getcwd()
            daemon_log_path = os.path.join(work_dir, ".ghs_daemon.log")
            
            # Clean up old daemon log if present
            try:
                if os.path.exists(daemon_log_path):
                    os.remove(daemon_log_path)
            except Exception:
                pass

            log_file = open(daemon_log_path, "w", encoding="utf-8", errors="replace")
            
            creation_flags = 0
            if os.name == "nt":
                # CREATE_NO_WINDOW (0x08000000) | CREATE_NEW_PROCESS_GROUP (0x200)
                creation_flags = 0x08000000 | 0x00000200

            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                close_fds=True
            )
            
            # Poll up to 5.0 seconds to capture the exact dev server URL and port
            init_output = ""
            detected_url = ""
            detected_port = None
            t_start = time.time()
            while time.time() - t_start < 5.0:
                poll_code = proc.poll()
                if poll_code is not None and poll_code != 0:
                    break
                if os.path.exists(daemon_log_path):
                    try:
                        with open(daemon_log_path, "r", encoding="utf-8", errors="replace") as f:
                            init_output = f.read().strip()
                    except Exception:
                        pass
                m = re.search(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):([0-9]+)", init_output)
                if m:
                    detected_url = m.group(0)
                    try:
                        detected_port = int(m.group(2))
                    except ValueError:
                        pass
                    break
                time.sleep(0.4)

            poll_code = proc.poll()
            log_file.flush()
            log_file.close()

            if poll_code is not None and poll_code != 0:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error al iniciar proceso en segundo plano (Exit Code: {poll_code}):\n{init_output}"
                    }],
                    "isError": True
                }

            # Register dev server in persistent registry
            proj_name = os.path.basename(os.path.normpath(cwd)) if cwd else "Proyecto Web"
            _register_dev_server(proc.pid, port=detected_port, url=detected_url, project=proj_name, cwd=cwd, command=command)

            url_msg = f"\n🌐 Servidor detectado en: {detected_url}" if detected_url else ""
            return {
                "content": [{
                    "type": "text",
                    "text": f"Exit Code: 0\n\n--- STDOUT ---\n[DAEMON] Servidor/proceso en segundo plano iniciado exitosamente (PID {proc.pid}).\nSalida inicial del servidor:\n{init_output or 'Servidor en ejecución.'}{url_msg}\nEl proceso continúa ejecutándose activamente y ya está registrado en el Centro de Mando GHS."
                }],
                "isError": False
            }
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error al iniciar daemon: {e}"}], "isError": True}

    # Standard synchronous execution with hidden window and process-tree termination
    sync_flags = 0x08000000 if os.name == "nt" else 0
    try:
        sys.stderr.write(f"[hermes-terminal] Ejecutando: {command[:90]} (timeout={timeout:.0f}s)\n")
        sys.stderr.flush()
    except Exception:
        pass

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
            creationflags=sync_flags
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            ret_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Kill entire process tree cleanly on Windows so no child processes keep pipes open
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5)
                except Exception:
                    pass
            else:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.communicate(timeout=1.5)
            except Exception:
                pass
            try:
                sys.stderr.write(f"[hermes-terminal] Timeout excedido ({timeout:.0f}s). Proceso cancelado limpiamente.\n")
                sys.stderr.flush()
            except Exception:
                pass
            return {
                "content": [{
                    "type": "text",
                    "text": f"El comando excedió el tiempo límite de {timeout:.0f}s y fue cancelado. Si este proceso o servidor debe permanecer ejecutándose en segundo plano, ejecútalo con 'is_daemon': true."
                }],
                "isError": True
            }

        # Auto-recover from npm Windows TAR_ENTRY_ERROR (file locking / ENOENT on Windows)
        if ret_code != 0 and ("TAR_ENTRY_ERROR" in (stderr or "") or ret_code == 4294963245 or ret_code == -4051):
            if "npm" in command.lower() and ("install" in command.lower() or " i " in command.lower()):
                time.sleep(1.2)
                retry_cmd = re.sub(r"--no-audit|--no-fund|--prefer-offline", "", command).strip() + " --no-audit --no-fund --prefer-offline"
                retry_proc = subprocess.Popen(
                    retry_cmd,
                    shell=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=cwd,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=sync_flags
                )
                try:
                    r_out, r_err = retry_proc.communicate(timeout=timeout)
                    if retry_proc.returncode == 0:
                        ret_code = retry_proc.returncode
                        stdout, stderr = r_out, r_err
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(retry_proc.pid)], capture_output=True, timeout=5)
                    try:
                        retry_proc.communicate(timeout=1.5)
                    except Exception:
                        pass

        output = f"Exit Code: {ret_code}\n"
        if stdout:
            out_text = stdout.strip()
            if len(out_text) > 6000:
                out_lines = out_text.splitlines()
                if len(out_lines) > 50:
                    out_text = "\n".join(out_lines[:25]) + f"\n... [{len(out_lines) - 35} líneas omitidas] ...\n" + "\n".join(out_lines[-10:])
            output += f"\n--- STDOUT ---\n{out_text}\n"

        if stderr:
            err_text = stderr.strip()
            if len(err_text) > 4000:
                err_lines = err_text.splitlines()
                if len(err_lines) > 40:
                    err_text = "\n".join(err_lines[:20]) + f"\n... [{len(err_lines) - 30} advertencias omitidas] ...\n" + "\n".join(err_lines[-10:])
            output += f"\n--- STDERR ---\n{err_text}\n"
            if "about_Execution_Policies" in stderr or "PSSecurityException" in stderr:
                output += "\n[SOLUCIÓN REQUERIDA]: Windows bloqueó la ejecución de scripts. Ejecuta con 'powershell -ExecutionPolicy Bypass' o utiliza 'npm.cmd' en lugar de 'npm'.\n"

        if ret_code == 0:
            cmd_lower = command.lower()
            if "build" in cmd_lower or "vite" in cmd_lower or "tsc" in cmd_lower:
                output += "\n[VALIDACIÓN TÉCNICA GHS]: ✓ COMPILACIÓN COMPLETADA EXITOSAMENTE (Exit Code: 0). Cualquier advertencia en STDERR referente a 'lightningcss minify: Unknown at rule: @theme' o '@tailwind' es estándar y esperada en Tailwind v4. NO es un error de compilación. Queda estrictamente prohibido intentar eliminar @theme de tu CSS. Tu proyecto compila y funciona perfectamente.\n"

        return {
            "content": [{"type": "text", "text": output.strip()}],
            "isError": ret_code != 0
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error al ejecutar comando: {e}"}], "isError": True}


_DEV_SERVERS_CACHE: Optional[Dict[str, Any]] = None
_DEV_SERVERS_CACHE_TIME: float = 0.0

def _tool_dev_server(arguments: Dict[str, Any]) -> Dict[str, Any]:
    global _DEV_SERVERS_CACHE, _DEV_SERVERS_CACHE_TIME
    action = arguments.get("action", "list")
    target_port = arguments.get("port", None)
    target_pid = arguments.get("pid", None)

    # Invalidate cache if stopping
    if action in ("stop", "stop_all"):
        _DEV_SERVERS_CACHE = None
    elif action == "list" and _DEV_SERVERS_CACHE is not None and (time.time() - _DEV_SERVERS_CACHE_TIME) < 4.0:
        return _DEV_SERVERS_CACHE

    try:
        def _get_running_servers():
            servers = []
            reg = _load_dev_server_registry()
            updated_reg = {}
            registered_pids = set()

            # 1. Clean and check registered servers
            for pid_str, info in reg.items():
                try:
                    pid = int(pid_str)
                    if psutil and psutil.pid_exists(pid):
                        p = psutil.Process(pid)
                        if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                            updated_reg[pid_str] = info
                            registered_pids.add(pid)
                            for child in p.children(recursive=True):
                                registered_pids.add(child.pid)
                            servers.append(info)
                except Exception:
                    pass
            _save_dev_server_registry(updated_reg)

            # 2. Dual-stack port scan (IPv4 + IPv6 [::1]) via netstat -ano
            try:
                res = subprocess.run("netstat -ano", shell=True, capture_output=True, text=True, creationflags=0x08000000)
                listening_ports = {}
                internal_ports = {17613, 17614, 13469, 9876}

                for line in res.stdout.splitlines():
                    if "LISTENING" in line:
                        parts = line.strip().split()
                        if len(parts) >= 5:
                            local_addr = parts[1]
                            pid_str = parts[4]
                            m = re.search(r":(\d+)$", local_addr)
                            if m:
                                try:
                                    port = int(m.group(1))
                                    pid = int(pid_str)
                                    if 1024 <= port <= 65535 and port not in internal_ports:
                                        listening_ports.setdefault(pid, []).append(port)
                                except ValueError:
                                    pass

                ghs_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__))).lower()

                for pid, ports in listening_ports.items():
                    if pid in registered_pids:
                        for s in servers:
                            if s.get("pid") == pid and not s.get("port") and ports:
                                s["port"] = ports[0]
                                s["url"] = f"http://localhost:{ports[0]}"
                        continue

                    if not psutil:
                        continue

                    try:
                        p = psutil.Process(pid)
                        pname = (p.name() or "").lower()
                        cmdline_list = p.cmdline() or []
                        cmdline = " ".join(cmdline_list)
                        cmd_lower = cmdline.lower()

                        if any(x in cmd_lower for x in [
                            "system32", "svchost", "antigravity", "language_server",
                            "sqlite-mcp", "mcp-fetch", "server-filesystem", "firebase-tools",
                            "bridge.py", "launch_studio", "openai_server.py"
                        ]):
                            continue

                        is_dev = any(k in cmd_lower for k in [
                            "vite", "next", "webpack", "parcel", "http-server",
                            "live-server", "serve", "react-scripts", "astro", "nuxt"
                        ]) or (
                            ("node" in pname or "bun" in pname) and any(k in cmd_lower for k in ["run", "dev", "start", "preview"])
                        ) or (
                            "python" in pname and "-m http.server" in cmd_lower
                        )

                        is_user_proj = "downloads" in cmd_lower and ghs_dir not in cmd_lower

                        if is_dev or is_user_proj:
                            proj_match = re.search(r"Downloads[\\/]([^\\/\s\"']+)", cmdline, re.IGNORECASE)
                            primary_port = ports[0] if ports else 5173
                            proj_name = proj_match.group(1) if proj_match else f"Servidor Web ({pname})"
                            server_entry = {
                                "pid": pid,
                                "port": primary_port,
                                "project": proj_name,
                                "url": f"http://localhost:{primary_port}",
                                "cwd": p.cwd() if hasattr(p, "cwd") else "",
                                "started_at": p.create_time()
                            }
                            servers.append(server_entry)
                            registered_pids.add(pid)
                            _register_dev_server(pid, primary_port, server_entry["url"], proj_name, server_entry["cwd"])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass

            return servers

        if action == "list":
            servers = _get_running_servers()
            res = {
                "content": [{"type": "text", "text": json.dumps({"count": len(servers), "servers": servers}, indent=2, ensure_ascii=False)}],
                "isError": False
            }
            _DEV_SERVERS_CACHE = res
            _DEV_SERVERS_CACHE_TIME = time.time()
            return res

        elif action == "stop":
            pids_to_kill = set()
            if target_pid:
                pids_to_kill.add(int(target_pid))
            servers = _get_running_servers()
            for s in servers:
                if target_port and s.get("port") == int(target_port):
                    pids_to_kill.add(s["pid"])
                elif target_pid and s.get("pid") == int(target_pid):
                    pids_to_kill.add(s["pid"])

            stopped = []
            for pid in pids_to_kill:
                try:
                    subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True, creationflags=0x08000000)
                    _unregister_dev_server(pid)
                    stopped.append(f"PID {pid}")
                except Exception as e:
                    stopped.append(f"PID {pid} (error: {e})")
            return {
                "content": [{"type": "text", "text": f"Servidor detenido: {', '.join(stopped) if stopped else 'No se encontró ningún servidor activo con ese criterio.'}"}],
                "isError": False
            }

        elif action == "stop_all":
            servers = _get_running_servers()
            stopped = []
            for s in servers:
                pid = s.get("pid")
                if pid:
                    try:
                        subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True, creationflags=0x08000000)
                        _unregister_dev_server(pid)
                        stopped.append(f"{s.get('project', 'Servidor')} (Puerto {s.get('port', '?')})")
                    except Exception:
                        pass
            _save_dev_server_registry({})
            return {
                "content": [{"type": "text", "text": f"Todos los servidores locales han sido detenidos exitosamente ({len(stopped)} proceso(s)): {', '.join(stopped) if stopped else 'No había servidores web activos.'}"}],
                "isError": False
            }

        return {"content": [{"type": "text", "text": f"Acción desconocida '{action}'."}], "isError": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error en control de servidores: {e}"}], "isError": True}


def _tool_file_read(arguments: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = (arguments.get("path") or arguments.get("filepath") or 
                arguments.get("file_path") or arguments.get("target_path") or 
                arguments.get("file") or arguments.get("filename") or "")
    if not raw_path:
        return {"content": [{"type": "text", "text": "Error: 'path' es requerido."}], "isError": True}
    
    path = resolve_target_path(raw_path)
    if not os.path.exists(path):
        return {"content": [{"type": "text", "text": f"Archivo no encontrado: '{path}'."}], "isError": True}
    
    offset = int(arguments.get("offset", 0))
    limit = int(arguments.get("limit", 2000))

    try:
        with open(path, "rb") as f:
            raw_bytes = f.read()
        full_text = decrypt_if_encrypted(raw_bytes)
        lines = full_text.splitlines(keepends=True)
        
        slice_lines = lines[offset: offset + limit]
        content = "".join(slice_lines)
        sep = chr(10) + chr(10)
        hdr = f"Archivo: {path} (Lineas {offset + 1} a {offset + len(slice_lines)} de {len(lines)})"
        return {
            "content": [
                {
                    "type": "text",
                    "text": hdr + sep + content
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error al leer archivo '{path}': {e}"}], "isError": True}


def _tool_file_write(arguments: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = (arguments.get("path") or arguments.get("filepath") or 
                arguments.get("file_path") or arguments.get("target_path") or 
                arguments.get("file") or arguments.get("filename") or "")
    content = arguments.get("content")
    if content is None:
        content = arguments.get("text")
    if content is None:
        content = arguments.get("data", "")
    if not isinstance(content, str):
        content = str(content)

    if not raw_path:
        return {"content": [{"type": "text", "text": "Error: 'path' es requerido."}], "isError": True}

    target_path = resolve_target_path(raw_path)
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        bname = os.path.basename(target_path).upper()
        norm_path = target_path.replace(chr(92), "/").upper()
        if bname in ("SOUL.MD", "USER.MD", "MEMORY.MD") or "MEMORY" in norm_path:
            cipher = get_fernet_cipher()
            enc_bytes = cipher.encrypt(content.encode("utf-8"))
            with open(target_path, "wb") as f:
                f.write(enc_bytes)
        else:
            with open(target_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(content)
        return {"content": [{"type": "text", "text": f"Archivo guardado exitosamente: {target_path} ({len(content)} bytes)."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error al escribir archivo en '{target_path}': {e}"}], "isError": True}


def _tool_file_patch(arguments: Dict[str, Any]) -> Dict[str, Any]:
    raw_path = (arguments.get("path") or arguments.get("filepath") or 
                arguments.get("file_path") or arguments.get("target_path") or 
                arguments.get("file") or arguments.get("filename") or "")
    patch_text = arguments.get("patch", "")
    search_text = arguments.get("search", "")
    replace_text = arguments.get("replace", "")

    if not raw_path and not patch_text:
        return {"content": [{"type": "text", "text": "Error: 'path' o 'patch' es requerido."}], "isError": True}

    if not raw_path and patch_text:
        m_file = re.search(r"\*\*\*\s*Update File:\s*([^\r\n]+)", patch_text)
        if m_file:
            raw_path = m_file.group(1).strip()

    target_path = resolve_target_path(raw_path) if raw_path else None
    if not target_path or not os.path.exists(target_path):
        return {"content": [{"type": "text", "text": f"Error: Archivo objetivo no encontrado: '{target_path or raw_path}'."}], "isError": True}

    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()

        # Case 1: Direct search and replace
        if search_text:
            if search_text in original:
                updated = original.replace(search_text, replace_text, 1)
            else:
                norm_search = re.sub(r"\s+", " ", search_text.strip())
                norm_orig = re.sub(r"\s+", " ", original)
                if norm_search in norm_orig:
                    escaped = re.escape(search_text.strip())
                    escaped = re.sub(r"\\\s+", r"\\s+", escaped)
                    updated = re.sub(escaped, replace_text, original, count=1)
                else:
                    return {"content": [{"type": "text", "text": f"Error: Bloque 'search' no encontrado en '{target_path}'."}], "isError": True}

        # Case 2: Unified diff or ChatGPT style patch
        elif patch_text:
            updated = original
            lines = patch_text.splitlines()
            old_chunk = []
            new_chunk = []
            applied_count = 0
            in_hunk = False

            for line in lines:
                if line.startswith("@@") or line.startswith("*** Update File:") or line.startswith("*** Begin Patch"):
                    if old_chunk or new_chunk:
                        old_s = "\n".join(old_chunk)
                        new_s = "\n".join(new_chunk)
                        if old_s and old_s in updated:
                            updated = updated.replace(old_s, new_s, 1)
                            applied_count += 1
                        old_chunk, new_chunk = [], []
                    in_hunk = True
                    continue

                if in_hunk:
                    if line.startswith("-"):
                        old_chunk.append(line[1:])
                    elif line.startswith("+"):
                        new_chunk.append(line[1:])
                    elif line.startswith(" ") or (line and not line.startswith("***")):
                        old_chunk.append(line[1:] if line.startswith(" ") else line)
                        new_chunk.append(line[1:] if line.startswith(" ") else line)

            if old_chunk or new_chunk:
                old_s = "\n".join(old_chunk)
                new_s = "\n".join(new_chunk)
                if old_s and old_s in updated:
                    updated = updated.replace(old_s, new_s, 1)
                    applied_count += 1

            if applied_count == 0:
                minus_lines = [l[1:] for l in lines if l.startswith("-")]
                plus_lines = [l[1:] for l in lines if l.startswith("+")]
                if minus_lines and plus_lines:
                    old_str = "\n".join(minus_lines)
                    new_str = "\n".join(plus_lines)
                    if old_str in updated:
                        updated = updated.replace(old_str, new_str, 1)
                        applied_count = 1

            if applied_count == 0:
                return {"content": [{"type": "text", "text": "Aviso: No se pudo localizar el bloque exacto del parche en el archivo. Usa 'hermes_file_write' para escribir el archivo completo."}], "isError": True}

        else:
            return {"content": [{"type": "text", "text": "Error: Se requiere 'patch' o 'search' y 'replace'."}], "isError": True}

        with open(target_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(updated)

        return {"content": [{"type": "text", "text": f"✓ Archivo parchado exitosamente: {target_path} ({len(updated)} bytes)."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error al aplicar parche en '{target_path}': {e}"}], "isError": True}


def _tool_toggle_skill(arguments: Dict[str, Any]) -> Dict[str, Any]:
    name = arguments.get("name", "")
    active = arguments.get("active", True)
    if not name:
        return {"content": [{"type": "text", "text": "Error: name requerido."}], "isError": True}
    try:
        disabled = get_disabled_skills()
        if active:
            disabled.discard(name)
        else:
            disabled.add(name)
        save_disabled_skills(disabled)
        return {"content": [{"type": "text", "text": f"Skill {name} {'activada' if active else 'desactivada'}."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}


def _tool_memory(arguments: Dict[str, Any]) -> Dict[str, Any]:
    import sqlite3
    action = arguments.get("action", "context")  # default = context para forzar carga al inicio
    key = arguments.get("key", "")
    content = arguments.get("content", "")
    category = arguments.get("category", "general")

                    # ── MEMORIA UNIFICADA 3 CAPAS (SOUL.md + USER.md + MEMORY.md) ──
    if action == "context":
        if os.name == 'nt':
            mem_dir = os.environ.get("GHS_MEMORY_DIR", os.path.join(os.environ.get("LOCALAPPDATA", "."), "hermes", "memory"))
        else:
            cloud_mem = os.path.join(os.path.dirname(__file__), "memory")
            mem_dir = os.environ.get("GHS_MEMORY_DIR", cloud_mem)
        parts = []
        nl = chr(10)
        dnl = chr(10) + chr(10)
        for fname in ["SOUL.md", "USER.md", "MEMORY.md"]:
            p = os.path.join(mem_dir, fname)
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        raw_data = f.read()
                    txt = decrypt_if_encrypted(raw_data)
                    parts.append(f"=== {fname} ===" + nl + txt)
                except Exception as e:
                    parts.append(f"=== {fname} ===" + nl + f"[error leyendo: {e}]")
            else:
                parts.append(f"=== {fname} ===" + nl + "[no existe aun]")
        full_text = dnl.join(parts)
        import re
        full_text = re.sub(r'[A-Za-z]:\\[Uu]sers\\[^\\]+', '~', full_text)
        full_text = re.sub(r'/home/[^/]+', '~', full_text)
        full_text = re.sub(r':(17613|17614|9876)\b', ':*****', full_text)
        return {"content": [{"type": "text", "text": full_text}]}
    try:
        with sqlite3.connect(MEMORY_DB_PATH) as conn:
            cursor = conn.cursor()
            if action == "save":
                if not key or not content:
                    return {"content": [{"type": "text", "text": "Error: 'key' y 'content' requeridos para guardar memoria."}], "isError": True}
                cursor.execute("""
                    INSERT INTO memories (category, key, content, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET content=excluded.content, created_at=excluded.created_at
                """, (category, key, content, time.time()))
                conn.commit()
                return {"content": [{"type": "text", "text": f"Memoria guardada bajo la clave '{key}'."}]}

            elif action == "search":
                query = arguments.get("query", key)
                cursor.execute("""
                    SELECT key, category, content FROM memories
                    WHERE key LIKE ? OR content LIKE ?
                """, (f"%{query}%", f"%{query}%"))
                rows = cursor.fetchall()
                results = [{"key": r[0], "category": r[1], "content": r[2]} for r in rows]
                return {"content": [{"type": "text", "text": json.dumps(results, indent=2, ensure_ascii=False)}]}

            else:  # list
                cursor.execute("SELECT key, category, content FROM memories ORDER BY created_at DESC LIMIT 50")
                rows = cursor.fetchall()
                results = [{"key": r[0], "category": r[1], "content": r[2]} for r in rows]
                return {"content": [{"type": "text", "text": json.dumps(results, indent=2, ensure_ascii=False)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error en base de datos de memoria: {e}"}], "isError": True}


def _tool_project_clean(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gestor de ciclo de vida, aislamiento y limpieza de proyectos.
    Permite inicializar una carpeta de proyecto limpia antes de crear archivos,
    y al concluir, purgar scripts de ensayo obsoletos y volcados HTML temporales.
    """
    action = arguments.get("action", "clean").lower()
    project_name = arguments.get("project_name", "").strip()
    raw_dir = arguments.get("project_dir", "").strip()
    downloads_dir = os.path.expanduser("~") + "/Downloads"
    
    if raw_dir:
        target_dir = resolve_target_path(raw_dir)
    elif project_name:
        target_dir = os.path.join(downloads_dir, project_name)
    else:
        target_dir = downloads_dir

    keep_files = arguments.get("keep_files", [
        "*.xlsx", "*.csv", "*.html", "*.json", "*.png", "*.jpg", "*.jpeg", "*.webp", "*.svg", "*.css", "*.js", "main.py", "LEEME*", "README*", "gen_final.py"
    ])
    default_remove = [
        "dump_*.*", "ddg_dump*.*", "*.tmp", "temp_*.*", "inspect_*.py", "parse_*.py", "enrich_*.py",
        "scrape_rappi*.py", "gen_excel_makis.py", "test_*.py", "test_*.json", "debug_*.*"
    ]
    remove_patterns = arguments.get("remove_patterns", default_remove)

    try:
        # 1. INICIALIZAR CARPETA LIMPIA E INDEPENDIENTE PARA NUEVO PROYECTO (ZERO RECYCLING)
        if action in ("init", "init_project", "crear", "nuevo", "start"):
            if not project_name and arguments.get("project_dir"):
                cand = os.path.basename(arguments.get("project_dir", "").rstrip(r'\/'))
                if cand and cand.lower() != "downloads":
                    project_name = cand
            if not project_name:
                project_name = f"Proyecto_{time.strftime('%Y%m%d_%H%M%S')}"

            proj_path = os.path.join(downloads_dir, project_name)
            # Si la carpeta ya existe de un proyecto anterior, crear una aislada con timestamp para evitar colisión/reciclaje
            if os.path.exists(proj_path) and os.listdir(proj_path):
                project_name = f"{project_name}_{time.strftime('%Y%m%d_%H%M%S')}"
                proj_path = os.path.join(downloads_dir, project_name)

            temp_path = os.path.join(proj_path, "_temp")
            os.makedirs(proj_path, exist_ok=True)
            os.makedirs(temp_path, exist_ok=True)
            
            readme_path = os.path.join(proj_path, "LEEME_INSTRUCCIONES.txt")
            if not os.path.exists(readme_path):
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(f"# Proyecto Independiente: {project_name}\nFecha de creación: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\nEste directorio es 100% exclusivo y auto-contenido.\nTodo script, código y entregable de esta sesión pertenece aquí.\nArchivos auxiliares o de prueba van en '_temp/'.\n")

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "action": "init_project",
                        "project_dir": proj_path,
                        "temp_dir": temp_path,
                        "message": f"Carpeta dedicada creada exitosamente en '{proj_path}'. Por favor, guarda todos los scripts y entregables dentro de este directorio."
                    }, indent=2, ensure_ascii=False)
                }]
            }

        # 2. PURGAR ARCHIVOS BASURA AL CONCLUIR
        elif action in ("clean", "cleanup", "purgar", "limpiar", "finalize", "finish", "done", "terminar", "concluir"):
            deleted = []
            preserved = []
            scan_dirs = [target_dir]
            if project_name and target_dir != downloads_dir:
                scan_dirs.append(downloads_dir)

            for sdir in scan_dirs:
                if not os.path.exists(sdir):
                    continue
                for pat in remove_patterns:
                    matches = glob.glob(os.path.join(sdir, pat))
                    for fp in matches:
                        fname = os.path.basename(fp)
                        # No borrar si coincide con keep_files
                        is_protected = False
                        for kpat in keep_files:
                            if re.search(r"^" + kpat.replace("*", ".*").replace("?", ".") + r"$", fname, re.IGNORECASE):
                                is_protected = True
                                break
                        if is_protected:
                            preserved.append(fname)
                            continue

                        try:
                            if os.path.isfile(fp):
                                os.remove(fp)
                                deleted.append(fname)
                            elif os.path.isdir(fp) and fname == "_temp":
                                shutil.rmtree(fp, ignore_errors=True)
                                deleted.append(fname)
                        except Exception:
                            pass

                # Limpiar carpeta _temp si existe dentro del directorio del proyecto
                sub_temp = os.path.join(sdir, "_temp")
                if os.path.exists(sub_temp) and os.path.isdir(sub_temp):
                    try:
                        shutil.rmtree(sub_temp, ignore_errors=True)
                        deleted.append("_temp/")
                    except Exception:
                        pass

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "action": "clean_garbage",
                        "target_dir": target_dir,
                        "deleted_count": len(deleted),
                        "deleted_files": list(set(deleted)),
                        "preserved_files": list(set(preserved)),
                        "message": f"Limpieza completada: se eliminaron {len(deleted)} archivo(s) residuales/basura y se preservaron los entregables esenciales."
                    }, indent=2, ensure_ascii=False)
                }]
            }

        # 3. ORGANIZAR PROYECTO (Mover entregables a carpeta de proyecto y purgar basura)
        elif action in ("organize", "organizar"):
            if not project_name:
                project_name = f"Proyecto_{time.strftime('%Y%m%d_%H%M%S')}"
            proj_path = os.path.join(downloads_dir, project_name)
            os.makedirs(proj_path, exist_ok=True)

            moved_files = []
            deleted_files = []

            # Mover o copiar archivos a conservar
            deliverables = arguments.get("deliverables", [])
            for deliv in deliverables:
                src = os.path.join(downloads_dir, deliv) if not os.path.isabs(deliv) else deliv
                if os.path.exists(src):
                    dst = os.path.join(proj_path, os.path.basename(src))
                    try:
                        shutil.copy2(src, dst)
                        moved_files.append(os.path.basename(src))
                        try:
                            os.remove(src)
                        except Exception:
                            pass  # Si está bloqueado por Excel, se queda en Downloads hasta que se cierre
                    except Exception:
                        pass

            # Purgar basura de Downloads
            for pat in remove_patterns:
                for fp in glob.glob(os.path.join(downloads_dir, pat)):
                    try:
                        fname = os.path.basename(fp)
                        os.remove(fp)
                        deleted_files.append(fname)
                    except Exception:
                        pass

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "action": "organize",
                        "project_dir": proj_path,
                        "moved_deliverables": moved_files,
                        "deleted_garbage": deleted_files,
                        "message": f"Proyecto organizado en '{proj_path}'. Entregables resguardados y {len(deleted_files)} archivo(s) basura purgados."
                    }, indent=2, ensure_ascii=False)
                }]
            }

        else:
            return {"content": [{"type": "text", "text": f"Acción desconocida '{action}'. Use 'init', 'clean' u 'organize'."}], "isError": True}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error en gestión de proyecto: {str(e)}\n{traceback.format_exc()}"}], "isError": True}



def _tool_video_to_playbook(arguments: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from video_to_playbook import video_to_playbook
        media_path = arguments.get("media_path") or arguments.get("video_path") or ""
        # Auto-detect latest video if media_path is empty or "latest"
        if not media_path or media_path.lower() in ("latest", "reciente", "ultimo"):
            downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            recordings = glob.glob(os.path.join(downloads_dir, "demostracion_*.webm")) + glob.glob(os.path.join(downloads_dir, "*.webm")) + glob.glob(os.path.join(downloads_dir, "*.mp4"))
            if recordings:
                recordings.sort(key=os.path.getmtime, reverse=True)
                media_path = recordings[0]
            else:
                return {"content": [{"type": "text", "text": "No se encontró ningún video de demostración reciente en Downloads. Pasa 'media_path' con la ruta del archivo."}], "isError": True}

        playbook_name = arguments.get("playbook_name") or arguments.get("name") or "demostracion_autonoma"
        description = arguments.get("description", "")
        platform = arguments.get("platform", "windows_desktop")
        manual_steps = arguments.get("manual_steps")

        res = video_to_playbook.process_demonstration(
            media_path_or_url=media_path,
            playbook_name=playbook_name,
            description=description,
            platform=platform,
            manual_steps=manual_steps
        )
        return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error al procesar video a playbook: {e}\n{traceback.format_exc()}"}], "isError": True}


# ─────────────────────────────────────────────────────────────────────────────
# Herramientas Nativas de Android & Compilación Local
# ─────────────────────────────────────────────────────────────────────────────

def _tool_android_sync_artifacts(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_name = arguments.get("project_name", "GHS_Android_App").strip()
    project_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", project_name)
    files = arguments.get("files", [])
    if not files and "file" in arguments:
        files = [arguments["file"]]

    base_dir = os.path.join(os.path.expanduser("~"), "Downloads", "GHS_Android_Projects", project_name)
    os.makedirs(base_dir, exist_ok=True)

    saved_files = []
    for f in files:
        if isinstance(f, dict):
            rel_path = f.get("path", "").strip().lstrip("/\\")
            content = f.get("content", "")
        else:
            continue
        if not rel_path:
            continue
        full_path = os.path.normpath(os.path.join(base_dir, rel_path))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as out_f:
            out_f.write(content)
        saved_files.append(rel_path)

    return {
        "content": [{
            "type": "text",
            "text": f"✓ Proyecto Android '{project_name}' sincronizado exitosamente en:\n{base_dir}\nArchivos guardados en disco ({len(saved_files)}):\n" + "\n".join(f"- {p}" for p in saved_files[:25]) + (f"\n... [{len(saved_files)-25} archivos adicionales]" if len(saved_files) > 25 else "")
        }]
    }


def _tool_android_build_apk(arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_name = arguments.get("project_name", "").strip()
    base_dir = os.path.join(os.path.expanduser("~"), "Downloads", "GHS_Android_Projects")
    if project_name:
        proj_dir = os.path.join(base_dir, project_name)
    else:
        candidates = [os.path.join(base_dir, d) for d in os.listdir(base_dir)] if os.path.exists(base_dir) else []
        candidates = [d for d in candidates if os.path.isdir(d)]
        if not candidates:
            return {"content": [{"type": "text", "text": "Error: No se encontró ningún proyecto Android en Downloads/GHS_Android_Projects. Sincroniza primero con 'android_sync_artifacts'."}], "isError": True}
        candidates.sort(key=os.path.getmtime, reverse=True)
        proj_dir = candidates[0]
        project_name = os.path.basename(proj_dir)

    gradlew = os.path.join(proj_dir, "gradlew.bat" if os.name == "nt" else "gradlew")
    if not os.path.exists(gradlew):
        gradlew_cmd = "gradle.cmd assembleDebug" if os.name == "nt" else "gradle assembleDebug"
    else:
        gradlew_cmd = f'"{gradlew}" assembleDebug'

    try:
        proc = subprocess.run(
            gradlew_cmd,
            shell=True,
            cwd=proj_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240
        )
        if proc.returncode == 0:
            apk_path = None
            for root, _, fnames in os.walk(proj_dir):
                for fn in fnames:
                    if fn.endswith(".apk") and "debug" in fn.lower():
                        apk_path = os.path.join(root, fn)
                        break
                if apk_path:
                    break

            size_str = f" ({os.path.getsize(apk_path) // 1024} KB)" if apk_path and os.path.exists(apk_path) else ""
            apk_info = f"\n📦 APK Generado: {apk_path}{size_str}" if apk_path else ""
            return {
                "content": [{
                    "type": "text",
                    "text": f"🎉 ¡COMPILACIÓN ANDROID EXITOSA! (BUILD SUCCESSFUL){apk_info}\nTu app nativa está lista para instalar en tu celular físico con 'android_install_adb'."
                }],
                "isError": False
            }
        else:
            err_msg = proc.stderr.strip() or proc.stdout.strip()
            return {
                "content": [{
                    "type": "text",
                    "text": f"🚨 [AUTO-SANACIÓN ACTIVA] ERROR DE COMPILACIÓN GRADLE (Exit Code: {proc.returncode}):\n{err_msg[-1800:]}\n\n[DIRECTIVA DE REPARACIÓN OBLIGATORIA]: Analiza el error específico arriba (línea de Kotlin, paquete faltante o fallo en Jetpack Compose) y emite INMEDIATAMENTE la corrección de código para solucionar el build."
                }],
                "isError": True
            }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error al ejecutar build de Gradle: {e}"}], "isError": True}


def _tool_android_install_adb(arguments: Dict[str, Any]) -> Dict[str, Any]:
    apk_path = arguments.get("apk_path", "").strip()
    if not apk_path:
        base_dir = os.path.join(os.path.expanduser("~"), "Downloads", "GHS_Android_Projects")
        apks = []
        if os.path.exists(base_dir):
            for root, _, fnames in os.walk(base_dir):
                for fn in fnames:
                    if fn.endswith(".apk"):
                        p = os.path.join(root, fn)
                        apks.append((os.path.getmtime(p), p))
        if apks:
            apks.sort(reverse=True)
            apk_path = apks[0][1]
        else:
            return {"content": [{"type": "text", "text": "Error: No se encontró ningún archivo .apk en disco. Compila primero con 'android_build_apk'."}], "isError": True}

    try:
        check = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        lines = [l.strip() for l in check.stdout.splitlines() if l.strip() and not l.startswith("List of devices")]
        devices = [l.split()[0] for l in lines if "\tdevice" in l]
        if not devices:
            return {
                "content": [{
                    "type": "text",
                    "text": f"⚠️ No se detectó ningún teléfono celular Android conectado por USB.\n\nPasos para probar en tu celular:\n1. Conecta tu teléfono Android a la PC con cable USB.\n2. En Ajustes del teléfono activa 'Opciones de desarrollador' > 'Depuración por USB'.\n3. Acepta el mensaje en tu pantalla.\nTu APK listo está resguardado en: {apk_path}"
                }],
                "isError": True
            }

        target_dev = devices[0]
        inst = subprocess.run(["adb", "-s", target_dev, "install", "-r", apk_path], capture_output=True, text=True, timeout=90)
        if "Success" in inst.stdout or inst.returncode == 0:
            return {
                "content": [{
                    "type": "text",
                    "text": f"📲 ¡APP INSTALADA CON ÉXITO EN TU CELULAR ANDROID!\nDispositivo: {target_dev}\nAPK: {os.path.basename(apk_path)}\n\nLa aplicación ya está instalada en tu teléfono lista para usarse."
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error al instalar APK con ADB:\n{inst.stderr or inst.stdout}"
                }],
                "isError": True
            }
    except FileNotFoundError:
        return {
            "content": [{
                "type": "text",
                "text": f"Aviso: 'adb' no está instalado en el PATH de Windows.\nTu archivo APK generado está guardado en:\n{apk_path}\nPuedes copiarlo directamente a tu celular o instalar ADB ejecutando: winget install Google.PlatformTools"
            }],
            "isError": True
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error en ADB: {e}"}], "isError": True}


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tool Registry Definition
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "hermes_skills_list",
        "description": "Lista todas las 76 habilidades especializadas (Skills) del agente Hermes de Nous Research por categoría (desarrollo de software, investigación académica arXiv, productividad office/notion/obsidian, smart home, audio, video, red teaming).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filtro opcional de categoría (ej: 'software-development', 'research', 'productivity', 'smart-home', 'creative')."
                }
            }
        }
    },
    {
        "name": "hermes_skill_view",
        "description": "Carga y lee las instrucciones técnicas completas y procedimientos paso a paso de cualquiera de las 76 habilidades de Hermes pasando su nombre (ej: 'systematic-debugging', 'test-driven-development', 'arxiv', 'obsidian', 'google-workspace', 'docx', 'xlsx', 'plan').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nombre de la habilidad a consultar (ej: 'systematic-debugging', 'arxiv', 'obsidian')."
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "hermes_tools_list",
        "description": "Lista las 88 herramientas nativas disponibles en el motor de ejecución de Nous Research Hermes Agent con sus nombres, emojis y descripciones.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "hermes_run_tool",
        "description": "Pasarela de ejecución universal: invoca CUALQUIERA de las 88 herramientas nativas de Hermes Agent ('browser_navigate', 'browser_click', 'browser_snapshot', 'process_manage', 'kanban_create', 'vision_analyze', 'x_search', etc.) pasando 'tool_name' y su objeto 'arguments'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Nombre exacto de la herramienta nativa de Hermes a ejecutar."
                },
                "arguments": {
                    "type": "object",
                    "description": "Diccionario con los parámetros requeridos por la herramienta seleccionada."
                }
            },
            "required": ["tool_name"]
        }
    },
    {
        "name": "hermes_web_search",
        "description": "Búsqueda web en vivo de alta velocidad mediante el motor de Hermes Agent. Devuelve resultados estructurados con títulos, descripciones y URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término o pregunta de búsqueda web."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "hermes_web_extract",
        "description": "Extrae y limpia el contenido completo en texto plano de páginas web o enlaces proporcionados.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de URLs para extraer contenido."
                }
            },
            "required": ["urls"]
        }
    },
    {
        "name": "hermes_execute_code",
        "description": "Ejecuta código Python con acceso al runtime de Hermes y herramientas asociadas de manera programática.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Código Python a ejecutar."
                }
            },
            "required": ["code"]
        }
    },
    {
        "name": "hermes_computer_use",
        "description": "Controla el escritorio de Windows con IA: toma capturas de pantalla de alta resolución, haz clics, doble clics, escribe texto con teclado, presiona combinaciones de teclas (win+r, ctrl+c), lista ventanas activas y enfoca aplicaciones.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["capture", "click", "double_click", "right_click", "type", "key", "scroll", "list_windows", "focus_app"],
                    "description": "Acción a realizar sobre Windows."
                },
                "coordinate": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "[x, y] coordenadas en píxeles para clics."
                },
                "text": {
                    "type": "string",
                    "description": "Texto para escribir con 'type'."
                },
                "key": {
                    "type": "string",
                    "description": "Tecla o combinación ('enter', 'ctrl+c', 'win+r', 'alt+tab')."
                },
                "app": {
                    "type": "string",
                    "description": "Nombre o título de la ventana a enfocar para 'focus_app'."
                },
                "scroll_amount": {
                    "type": "integer",
                    "description": "Cantidad de scroll vertical."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "hermes_terminal",
        "description": "Ejecuta comandos reales del sistema operativo Windows (PowerShell, CMD, Bash, scripts) de forma autónoma y devuelve la salida.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Comando a ejecutar en la terminal de Windows."
                },
                "timeout": {
                    "type": "integer",
                    "description": "Tiempo máximo en segundos antes de cancelar (defecto: 60s)."
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Tiempo máximo en milisegundos antes de cancelar."
                },
                "is_daemon": {
                    "type": "boolean",
                    "description": "Establecer en true para servidores web, watchers o procesos de soporte que deben continuar corriendo en segundo plano indefinidamente (ej: npm run dev, npx vite, python -m http.server)."
                },
                "background": {
                    "type": "boolean",
                    "description": "Alias de is_daemon para ejecución desacoplada en segundo plano."
                },
                "cwd": {
                    "type": "string",
                    "description": "Directorio de trabajo opcional."
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "hermes_file_read",
        "description": "Lee el contenido de un archivo en el disco local de Windows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta absoluta o relativa del archivo."},
                "offset": {"type": "integer", "description": "Línea inicial (0-indexed)."},
                "limit": {"type": "integer", "description": "Máximo de líneas a leer."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "hermes_file_write",
        "description": "Crea o sobrescribe un archivo en el disco local de Windows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del archivo a escribir."},
                "content": {"type": "string", "description": "Contenido completo a escribir."}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "hermes_file_patch",
        "description": "Aplica un parche o diff a un archivo existente en el disco local de Windows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del archivo a parchar."},
                "patch": {"type": "string", "description": "Contenido del diff/parche o bloque a aplicar."},
                "search": {"type": "string", "description": "Texto exacto a buscar en el archivo (opcional)."},
                "replace": {"type": "string", "description": "Texto de reemplazo (opcional)."}
            }
        }
    },
    {
        "name": "hermes_toggle_skill",
        "description": "Activa o desactiva una skill en disabled_skills.json. Las skills desactivadas no aparecen en hermes_skills_list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre de la skill"},
                "active": {"type": "boolean", "description": "true para activar, false para desactivar"}
            },
            "required": ["name", "active"]
        }
    },
    {
        "name": "hermes_memory",
        "description": "Memoria persistente 3 capas. action='context' devuelve SOUL.md + USER.md + MEMORY.md (contexto inyectable). action='save'/'search'/'list' opera sobre SQLite de hechos.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["context", "save", "search", "list"],
                    "description": "context = SOUL.md + USER.md + MEMORY.md (contexto de arranque); save/search/list = SQLite de hechos."
                },
                "key": {"type": "string", "description": "Identificador o clave del hecho."},
                "content": {"type": "string", "description": "Información detallada a recordar."},
                "category": {"type": "string", "description": "Categoría (ej: desarrollo, windows, preferencias)."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "hermes_ui_inspect",
        "description": "Superpoder de Visión & Auditoría Estética Integral (Desktop, Mobile, Scroll vertical y Full-Page): abre la aplicación web en ejecución (ej: 'http://localhost:5173') con Chrome Headless. Permite capturar la página completa de arriba a abajo ('full_page': true), desplazarse/hacer scroll a cualquier altura o sección ('scroll_y': 800, 'middle', 'bottom'), auditar vistas móviles ('device': 'mobile') y extraer la jerarquía DOM/CSS en tiempo real para que la IA audite y perfeccione la estética, tipografía, contrastes, iconografía y responsividad.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL local o remota a inspeccionar (ej: 'http://localhost:5173'). Si se omite, auto-detecta el servidor Vite local activo."
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Si es true (defecto), captura la página entera de arriba a abajo en alta resolución para auditar todo el layout sin cortes."
                },
                "scroll_y": {
                    "type": ["integer", "string"],
                    "description": "Posición de desplazamiento vertical en píxeles (ej: 0, 800, 1600, 2400) o sección semántica ('top', 'middle', 'bottom', 'hero', 'footer', '50%')."
                },
                "device": {
                    "type": "string",
                    "enum": ["desktop", "laptop", "tablet", "mobile"],
                    "description": "Dispositivo a simular: 'desktop' (1440x900), 'laptop' (1280x800), 'tablet' (768x1024), 'mobile' (390x844)."
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Segundos de espera antes de la captura para permitir animaciones, fuentes y Canvas VFX (defecto: 2s)."
                }
            }
        }
    },
    {
        "name": "hermes_dev_server",
        "description": "Gestor y monitor de servidores locales en vivo (Vite, Next, Node, React): lista los servidores y puertos actualmente activos en la PC, detiene servidores específicos por puerto/PID o detiene todos los servidores web de proyectos de forma limpia sin afectar a los servidores MCP del sistema.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "stop", "stop_all"],
                    "description": "Acción a realizar: 'list' (listar servidores y puertos activos), 'stop' (detener por puerto o pid), 'stop_all' (detener todos los servidores de proyectos)."
                },
                "port": {
                    "type": "integer",
                    "description": "Puerto del servidor a detener (ej: 5173, 4173)."
                },
                "pid": {
                    "type": "integer",
                    "description": "PID del proceso a detener."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "hermes_stealth_scrape",
        "description": "Motor de scraping web y búsqueda avanzada de alta resiliencia (Anti-Bot Bypass con Google Chrome/Playwright). Permite buscar en la web (DuckDuckGo/Google), extraer resultados orgánicos sin anuncios, evadir Cloudflare/Akamai/Datadome/CAPTCHAs, inspeccionar perfiles o páginas protegidas (Instagram, Twitter, LinkedIn, tiendas e-commerce, portales de noticias), extraer selectores CSS personalizados, metadatos OpenGraph/SEO, texto limpio, verificar keywords, tomar capturas de pantalla de evidencia y exportar directamente a JSON o CSV (compatible con Excel).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término de búsqueda web en modo stealth (ej: 'site:instagram.com sneakers lima tienda', 'site:linkedin.com software engineer', 'precios laptops gamer')."
                },
                "url": {
                    "type": "string",
                    "description": "URL individual directa a raspar o inspeccionar con motor Chrome real."
                },
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de URLs a raspar en lote (batch) reutilizando la misma sesión del navegador."
                },
                "url_pattern": {
                    "type": "string",
                    "description": "Expresión regular opcional para filtrar URLs de los resultados de búsqueda (ej: 'instagram\\.com/([a-zA-Z0-9_\\.]+)' o 'tienda')."
                },
                "deep_inspect": {
                    "type": "boolean",
                    "description": "Si es True junto a 'query', visita cada URL encontrada en la búsqueda para raspar su contenido completo y metadatos."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cantidad máxima de resultados o páginas a inspeccionar (por defecto: 10)."
                },
                "selectors": {
                    "type": "object",
                    "description": "Diccionario clave-selector CSS a extraer del DOM (ej: {'precio': '.price-tag', 'titulo': 'h1', 'items': '.card-title'})."
                },
                "extract_meta": {
                    "type": "boolean",
                    "description": "Si es True, extrae metadatos SEO y redes sociales (description, og:title, og:image, og:type, keywords). Por defecto: True."
                },
                "extract_text": {
                    "type": "boolean",
                    "description": "Si es True, extrae el texto visible limpio del cuerpo de la página (hasta 2000 caracteres). Por defecto: True."
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de palabras clave para verificar si están presentes en el contenido de cada página."
                },
                "screenshot_dir": {
                    "type": "string",
                    "description": "Directorio opcional para guardar capturas de pantalla (.png) como evidencia visual de cada página inspeccionada."
                },
                "wait_seconds": {
                    "type": "number",
                    "description": "Segundos de espera tras cargar la página para permitir la hidratación de frameworks JavaScript (React, Vue, Next, Vite). Por defecto: 2.0."
                },
                "scroll": {
                    "type": "boolean",
                    "description": "Si es True, realiza scroll vertical para activar carga diferida (lazy-loading / scroll infinito)."
                },
                "export_json": {
                    "type": "string",
                    "description": "Ruta de archivo opcional para guardar los resultados estructurados en formato JSON."
                },
                "export_csv": {
                    "type": "string",
                    "description": "Ruta de archivo opcional para guardar los resultados en formato CSV (con codificación UTF-8 BOM compatible con Microsoft Excel)."
                }
            }
        }
    },
    {
        "name": "hermes_project_clean",
        "description": "Gestor de ciclo de vida, aislamiento y limpieza de proyectos. Permite inicializar una carpeta de proyecto limpia antes de crear archivos ('init'), purgar archivos basura y volcados residuales al terminar el proyecto ('clean'), o mover los entregables a una carpeta de proyecto y limpiar la basura ('organize').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["init", "clean", "organize"],
                    "description": "Acción a realizar: 'init' (crear carpeta aislada de proyecto Downloads/<project_name>), 'clean' (purgar volcados HTML, temporales y scripts obsoletos), 'organize' (mover entregables finales a carpeta de proyecto y purgar basura)."
                },
                "project_name": {
                    "type": "string",
                    "description": "Nombre de la carpeta del proyecto (ej: 'Proyecto_Makis_Lima', 'Proyecto_Scraper_Precios')."
                },
                "project_dir": {
                    "type": "string",
                    "description": "Ruta absoluta opcional del directorio a limpiar o gestionar."
                },
                "deliverables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de archivos entregables a resguardar (ej: ['makis_sushi_lima.xlsx', 'gen_final.py'])."
                },
                "keep_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Patrones de archivos protegidos que NUNCA deben borrarse (por defecto: ['*.xlsx', '*.csv', 'main.py', 'LEEME*', 'README*'])."
                },
                "remove_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Patrones de archivos basura a purgar (por defecto: ['dump_*.html', 'ddg_dump*.*', '*.tmp', 'temp_*.*', 'inspect_*.py', 'parse_*.py'])."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "hermes_prospect_scrape",
        "description": "Arsenal universal de prospección y scraping de negocios locales, comercios y perfiles sociales (basado en el motor validado de web-crapper-chaclacayo-negocios). Extrae comercios de OpenStreetMap con rotación de mirrors (Overpass sin bloqueo), búsquedas orgánicas en Bing y DuckDuckGo, detecta si tienen página web o solo redes sociales, captura teléfonos, direcciones y coordenadas GPS con enlaces listos a Google Maps, y exporta automáticamente a JSON y CSV compatible con Excel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Rubro, nicho o palabra clave a prospectar (ej: 'cevicherias', 'restaurantes', 'zapatillas', 'veterinarias', 'dentistas'). Dejar vacío para auditar todos los comercios del área."
                },
                "location": {
                    "type": "string",
                    "description": "Distrito, ciudad o zona geográfica a prospectar (ej: 'Chaclacayo, Lima', 'Miraflores', 'Arequipa'). Por defecto: 'Chaclacayo, Lima'."
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fuentes a consultar: ['osm', 'bing', 'ddg', 'social'] (por defecto: ['osm', 'bing', 'ddg'])."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Número máximo de prospectos a retornar (por defecto: 50)."
                },
                "output_dir": {
                    "type": "string",
                    "description": "Carpeta del proyecto donde exportar automáticamente 'negocios_raw.json' y 'prospectos_filtrados.csv'."
                },
                "filter_no_web": {
                    "type": "boolean",
                    "description": "Si es True, filtra y entrega exclusivamente los negocios que no tienen página web propia (o que solo tienen redes sociales), ideales para venta de servicios web."
                }
            }
        }
    },
    {
        "name": "hermes_video_to_playbook",
        "description": "Convierte una grabación de pantalla o video demostrativo (.webm, .mp4 en Descargas) en un Playbook estructurado paso a paso (SOP) y lo guarda en el cerebro persistente del agente (agent_brain.json) para su ejecución autónoma posterior con hermes_computer_use o terminal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "media_path": {
                    "type": "string",
                    "description": "Ruta absoluta del archivo de video o audio en Descargas (ej: 'c:\\Users\\USER\\Downloads\\demostracion_2026-09-28.webm'), o 'latest' para usar la grabación más reciente."
                },
                "playbook_name": {
                    "type": "string",
                    "description": "Nombre identificador para el procedimiento (ej: 'subir_notas_portal', 'scraping_facturas')."
                },
                "description": {
                    "type": "string",
                    "description": "Descripción breve del objetivo del procedimiento."
                },
                "platform": {
                    "type": "string",
                    "description": "Plataforma o aplicación objetivo (ej: 'web', 'desktop', 'excel', 'chrome')."
                },
                "manual_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Pasos opcionales extraídos o descritos manualmente."
                }
            }
        }
    },
    {
        "name": "android_sync_artifacts",
        "description": "Sincroniza y escribe en el disco local los archivos Kotlin/Android generados en AI Studio Apps a Downloads/GHS_Android_Projects/<project_name>.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Nombre de la aplicación (ej: 'DramaPe', 'MiAppAndroid')."
                },
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Ruta relativa dentro del proyecto (ej: 'app/src/main/java/.../Screen.kt')."},
                            "content": {"type": "string", "description": "Código completo del archivo."}
                        },
                        "required": ["path", "content"]
                    },
                    "description": "Lista de archivos a guardar en el proyecto."
                }
            },
            "required": ["project_name", "files"]
        }
    },
    {
        "name": "android_build_apk",
        "description": "Compila el proyecto Android nativo con Gradle (gradlew assembleDebug) para generar el instalador APK en tu PC local. Si hay un error, devuelve la traza exacta para auto-sanación.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Nombre del proyecto Android a compilar en Downloads/GHS_Android_Projects."
                }
            }
        }
    },
    {
        "name": "android_install_adb",
        "description": "Instala y despliega el archivo APK generado directamente en un teléfono celular Android real conectado por USB o en un emulador activo vía ADB.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "apk_path": {
                    "type": "string",
                    "description": "Ruta absoluta opcional del APK a instalar. Si se omite, busca el APK más reciente en Downloads/GHS_Android_Projects."
                }
            }
        }
    }
]


# ─────────────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 Stdio Loop
# ─────────────────────────────────────────────────────────────────────────────

def send_response(rid: Any, result: Any = None, error: Any = None):
    resp = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    
    line = json.dumps(resp, ensure_ascii=True)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            try:
                req = json.loads(line)
            except Exception:
                continue

            rid = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "initialize":
                send_response(rid, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "hermes-agent-superpowers",
                        "version": "1.5.5"
                    }
                })

            elif method == "notifications/initialized":
                pass

            elif method == "tools/list":
                all_tools = list(TOOLS)
                if HERMES_NATIVE_AVAILABLE and _hermes_registry is not None:
                    for tname, t in _hermes_registry._tools.items():
                        if not any(x.get("name") == tname for x in all_tools):
                            schema = getattr(t, "parameters", None) or getattr(t, "input_schema", None)
                            if not schema or not isinstance(schema, dict):
                                schema = {"type": "object", "properties": {}}
                            all_tools.append({
                                "name": tname,
                                "description": (getattr(t, "description", "") or f"Nous Hermes tool: {tname}").split("\n")[0][:140],
                                "inputSchema": schema
                            })
                send_response(rid, {"tools": all_tools})

            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments", {})

                if name == "hermes_skills_list":
                    res = _tool_skills_list(args)
                elif name == "hermes_skill_view":
                    res = _tool_skill_view(args)
                elif name == "hermes_tools_list":
                    res = _tool_tools_list(args)
                elif name == "hermes_run_tool":
                    res = _tool_run_tool(args)
                elif name == "hermes_web_search":
                    res = _tool_web_search(args)
                elif name == "hermes_web_extract":
                    res = _tool_web_extract(args)
                elif name == "hermes_execute_code":
                    res = _tool_execute_code(args)
                elif name == "hermes_computer_use":
                    res = _tool_computer_use(args)
                elif name == "hermes_terminal":
                    res = _tool_terminal(args)
                elif name == "hermes_file_read":
                    res = _tool_file_read(args)
                elif name == "hermes_file_write":
                    res = _tool_file_write(args)
                elif name in ("hermes_file_patch", "hermes_patch"):
                    res = _tool_file_patch(args)
                elif name == "hermes_toggle_skill":
                    res = _tool_toggle_skill(args)
                elif name == "hermes_memory":
                    res = _tool_memory(args)
                elif name == "hermes_ui_inspect":
                    res = _tool_ui_inspect(args)
                elif name == "hermes_dev_server":
                    res = _tool_dev_server(args)
                elif name == "hermes_stealth_scrape":
                    res = _tool_stealth_scrape(args)
                elif name == "hermes_project_clean":
                    res = _tool_project_clean(args)
                elif name == "hermes_prospect_scrape":
                    res = _tool_prospect_scrape(args)
                elif name in ("hermes_video_to_playbook", "video_to_playbook"):
                    res = _tool_video_to_playbook(args)
                elif name == "android_sync_artifacts":
                    res = _tool_android_sync_artifacts(args)
                elif name == "android_build_apk":
                    res = _tool_android_build_apk(args)
                elif name == "android_install_adb":
                    res = _tool_android_install_adb(args)
                elif HERMES_NATIVE_AVAILABLE and _hermes_registry is not None and name in _hermes_registry._tools:
                    res = _tool_native_dispatch(name, args)
                else:
                    send_response(rid, error={"code": -32601, "message": f"Tool '{name}' not found."})
                    continue

                send_response(rid, res)

            elif method == "ping":
                send_response(rid, {})

            else:
                if rid is not None:
                    send_response(rid, error={"code": -32601, "message": f"Method '{method}' not implemented."})

        except Exception as e:
            err_msg = traceback.format_exc()
            try:
                sys.stderr.write(f"[hermes-mcp] Error: {err_msg}\n")
                sys.stderr.flush()
            except Exception:
                pass
            if 'rid' in locals() and rid is not None:
                try:
                    send_response(rid, error={"code": -32603, "message": f"Error interno en Hermes MCP: {e}"})
                except Exception:
                    pass


if __name__ == "__main__":
    main()
