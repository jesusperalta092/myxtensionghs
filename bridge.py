# # SPDX-License-Identifier: GPL-3.0-or-later
# bridge.py
# ──────────────────────────────────────────────────────────────────────────
#  GHS Bridge
#  Local WebSocket <-> Roblox Studio MCP server.
#  The browser extension talks to this over ws://127.0.0.1:<PORT>.
#
#  What this bridge exposes to Kimi (aggregated into one tools/list):
#    - Every MCP server declared in config.json (by default: roblox), each
#      spawned as a stdio child and routed by tool name.
#
#  Design goals (robustness first):
#   - Each MCP stdio process is read by ONE dedicated thread; responses are
#     matched by JSON-RPC id (no "read the next line and hope" races).
#   - stderr is drained so a child never blocks on a full pipe.
#   - A dead server is auto-restarted and the failing call retried once.
#   - Tool calls are locked PER SERVER, so a slow server never blocks another.
#   - Every call ALWAYS produces a reply: a result OR a structured error.
#     Nothing ever hangs the agentic loop silently.
# ──────────────────────────────────────────────────────────────────────────
import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time

try:
    # Sibling script (same folder as bridge.py, which Python puts on sys.path
    # automatically) - reused here purely to detect a Studio version bump
    # (see _current_studio_exe below), not to launch anything.
    import launch_studio_mcp as _studio_scan
except Exception:
    _studio_scan = None

try:
    import websockets
except ImportError:
    print("[bridge] Missing dependency. Run:  pip install websockets")
    sys.exit(1)

# Ensure sys.stdout and sys.stderr are never None when running headless
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

# Windows consoles often default to a legacy codepage (cp1252): printing
# non-ASCII text then raises UnicodeEncodeError INSIDE the WS handler, which
# kills the connection. Force UTF-8 (best effort). We also keep all console
# output strictly ASCII (no arrows / dots) so nothing garbles on a console that
# stayed on a legacy codepage anyway.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _enable_ansi_colors():
    """On Windows, turn on ANSI escape processing so color codes render instead
    of printing as literal gibberish like "<ESC>[92m". Returns True on success."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


HOST = os.environ.get("ZS_BRIDGE_HOST", "0.0.0.0")
# Keep in sync with ghs-extension/manifest.json "version" - printed at
# startup so a user's terminal output alone tells us which build they're on.
BRIDGE_VERSION = "1.5.5"
PORT = int(os.environ.get("ZS_BRIDGE_PORT", "17613"))
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
os.environ["GHS_BRIDGE_PID"] = str(os.getpid())
os.environ["GHS_ROOT_DIR"] = HERE

# The primary server. It is always present, added by the installer, and can
# never be edited/removed through the extension (it is what GHS is FOR).

# ── SUPERPODERES MCP INTEGRADAS (HERMES NATIVE SUITE) ──
MCP_BUILTIN_PRESETS = {"filesystem", "fetch", "sqlite", "memory", "blender", "rea"}
disabled_presets = set()

PRIMARY_SERVER_ID = "roblox"

if _enable_ansi_colors():
    C = {
        "reset": "\033[0m", "dim": "\033[2m", "gr": "\033[92m",
        "yl": "\033[93m", "rd": "\033[91m", "cy": "\033[96m",
        # Bold white-on-red: for a non-technical user, an "ACTION NEEDED" step
        # must look nothing like the routine cyan/yellow status noise around
        # it, or it gets scrolled past unread (seen live 2026-07-13 - the
        # toggle instruction and the boot banner's own yellow re-explanation
        # of the SAME step were visually indistinguishable). Bright-yellow-bg
        # with black text was tried first but reads as low-contrast/washed
        # out on several real terminal color schemes (also seen live) - white
        # on red is the universal high-contrast "act now" pairing.
        "act": "\033[1m\033[97m\033[41m",
    }
else:
    C = {k: "" for k in ("reset", "dim", "gr", "yl", "rd", "cy", "act")}

# Every run appends here (never truncated), so a whole test session - across
# multiple restarts - stays in one file the user can just send us. Each
# process start writes a banner (see main()) so restarts are easy to spot.
LOGS_DIR = os.path.join(HERE, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOGS_DIR, "bridge_debug.log")
try:
    _log_file = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
except Exception:
    _log_file = None


class _Spinner:
    """Terminal-only progress indicator for waits that can run several seconds
    (server launch/handshake, Studio attach grace period) so the console never
    just sits there looking dead - the #1 thing that makes a user assume the
    bridge hung and close the window. Purely cosmetic: writes over its own line
    with \\r, never touches bridge_debug.log, and is skipped entirely when
    stdout isn't a real console (redirected to a file, no ANSI)."""
    FRAMES = "|/-\\"
    # Only ONE spinner may animate at a time: server launches now run in
    # PARALLEL (see MCPManager.start_all), and several spinners fighting over
    # the same console line with \r produced interleaved garbage. Whoever
    # acquires this lock animates; the others silently skip (the log lines
    # around them still tell the story).
    _active = threading.Lock()

    def __init__(self, label):
        self.label = label
        self._stop = threading.Event()
        self._thread = None
        self._owns_lock = False

    def __enter__(self):
        try:
            if sys.stdout and getattr(sys.stdout, "isatty", None) and sys.stdout.isatty() and _Spinner._active.acquire(blocking=False):
                self._owns_lock = True
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            # Wipe the spinner line so the next log() line doesn't get glued
            # onto trailing spinner characters.
            try:
                print("\r" + " " * (len(self.label) + 4) + "\r", end="", flush=True)
            except Exception:
                pass
        if self._owns_lock:
            _Spinner._active.release()

    def _run(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            try:
                print(f"\r{C['dim']}{self.label} {frame}{C['reset']}", end="", flush=True)
            except Exception:
                break
            i += 1
            self._stop.wait(0.15)


def _clear_spinner_line():
    """Wipe whatever a live _Spinner (running on its own thread, mid-frame) left
    on the current console line via bare \\r writes, so the next print() below
    doesn't get glued onto its trailing characters - seen live 2026-07-14: an
    action_banner() fired while '[roblox] starting... -' was still mid-line and
    the red box rendered smashed onto it instead of starting on a fresh line.
    \\033[K (clear to end of line) doesn't depend on knowing the spinner's label
    length the way Spinner.__exit__'s own wipe does."""
    try:
        if sys.stdout and getattr(sys.stdout, "isatty", None) and sys.stdout.isatty():
            print("\r\033[K", end="", flush=True)
    except Exception:
        pass


def log(msg, color="dim", terminal=True):
    """terminal=False: written to bridge_debug.log only, not the console. Use
    for noisy/technical detail (raw stderr from child MCP servers, per-call
    traces) that would bury the handful of lines a non-technical user actually
    needs to read. Nothing is ever lost - it all still lands in the file."""
    if terminal:
        _clear_spinner_line()
        ts = time.strftime("%H:%M:%S")
        print(f"{C['dim']}{ts}{C['reset']} {C.get(color,'')}{msg}{C['reset']}", flush=True)
    if _log_file:
        try:
            _log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
            _log_file.flush()
        except Exception:
            pass


def action_banner(lines):
    """Print a step the USER must physically go do, styled so it cannot be
    mistaken for routine status/warning noise (see the 'act' color above).
    Framed with blank lines so it visually stands alone in a scrolling
    terminal - a non-technical user should be able to glance at the window
    and immediately spot this without reading everything above it.

    Every line (header, content, footer) is padded to the SAME width so the
    yellow block renders as one clean rectangle - an earlier version padded
    each line to a fixed guess independently, which produced a ragged block
    with mismatched edges on a real console (seen live 2026-07-13)."""
    header = "ACTION NEEDED"
    width = max([len(header) + 8] + [len(ln) for ln in lines]) + 2
    top = f">>> {header} " + ">" * max(0, width - len(header) - 5)
    _clear_spinner_line()
    print()
    print(f"{C['act']}  {top.ljust(width)}{C['reset']}")
    for ln in lines:
        print(f"{C['act']}  {ln.ljust(width)}{C['reset']}")
    print(f"{C['act']}  {'>' * width}{C['reset']}")
    print()
    if _log_file:
        try:
            _log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ACTION NEEDED: "
                             f"{' | '.join(lines)}\n")
            _log_file.flush()
        except Exception:
            pass


# Roblox Studio exposes its built-in MCP server on this loopback port. StudioMCP
# (and our bridge, via it) reaches Studio through it.
STUDIO_MCP_PORT = 13469


def _port_owner(port):
    """(pid, name, path) of the process LISTENING on `port`, or None. Win32 only."""
    if sys.platform != "win32":
        return None
    # BOTH stacks: "-p TCP" alone is IPv4-only, and a squatter listening on
    # [::1]:<port> (IPv6 loopback) was then completely invisible to this probe
    # even while Get-NetTCPConnection showed it plainly (the likely reason the
    # boot-time squatter check stayed silent on a machine where ropilot
    # provably held the port - see the 2026-07-13 live report).
    out = ""
    for proto in ("TCP", "TCPv6"):
        try:
            out += subprocess.run(
                ["netstat", "-ano", "-p", proto],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=8,
            ).stdout
        except Exception:
            pass
    if not out:
        return None
    pid = None
    # v4 lines end the local address in ":<port>", v6 in "]:<port>" - matching
    # on the ":<port> " suffix (with the column gap) covers both shapes.
    needle = f":{port} "
    for line in out.splitlines():
        if "LISTENING" in line and needle in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = parts[-1]
                break
    if not pid:
        return None
    name, path = "?", ""
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
             f"if($p){{$p.Name; $p.Path}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8,
        ).stdout.splitlines()
        ps = [l.strip() for l in ps if l.strip()]
        if ps:
            name = ps[0]
            path = ps[1] if len(ps) > 1 else ""
    except Exception:
        pass
    return (pid, name, path)


def _roblox_studio_app_running():
    """True/False whether a real Roblox Studio window process exists, or None
    if this can't be determined (non-Windows, or the check itself failed)."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq RobloxStudioBeta.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8,
        ).stdout
    except Exception:
        return None
    return "RobloxStudioBeta.exe" in out


def _kill_orphan_studio_mcp():
    """Kill leftover StudioMCP.exe processes from a PREVIOUS session/crash.

    StudioMCP.exe is Roblox's own MCP proxy; launch_studio_mcp.py spawns one
    as a direct child every time the bridge starts. If an earlier restart's
    tree-kill missed the grandchild (a reparenting race), or Studio itself
    crashed and left its own StudioMCP.exe running (seen live 2026-07-11:
    RobloxStudioBeta.exe zombied after two RobloxCrashHandler.exe events),
    the orphan keeps LISTENING on Studio's MCP port. Every StudioMCP.exe we
    launch afterward - even a freshly restarted one - just connects to that
    zombie instead of a real Studio, so the bridge reports "Studio connected"
    forever even with Studio fully closed. studio_watch's auto-restart cannot
    fix this on its own: restarting our proxy still lands on the same zombie.

    Only acts when NO real Studio app is running at all - in that state any
    existing StudioMCP.exe is unambiguously orphaned (a legitimate one only
    exists to serve a live Studio), so it is safe to auto-kill without asking.
    If Studio IS running (or this can't be determined), this is a no-op: a
    live StudioMCP.exe might be legitimately serving it, so nothing is
    touched - this must never risk killing a working connection.
    """
    if sys.platform != "win32":
        return
    if _roblox_studio_app_running() is not False:
        return
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq StudioMCP.exe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8,
        ).stdout
    except Exception:
        return
    if "StudioMCP.exe" not in out:
        return
    log("Found leftover StudioMCP.exe process(es) with no Roblox Studio running - "
        "cleaning them up (known cause of a phantom 'Studio connected' state).", "yl")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "StudioMCP.exe"],
                       capture_output=True, text=True, timeout=8)
    except Exception as e:
        log(f"could not clean up orphaned StudioMCP.exe: {e}", "rd")


def _descendant_pids(root_pid):
    """Set of PIDs = root_pid + every descendant, or None if the process tree
    could not be read (in which case callers must NOT make kill decisions)."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object "
             "{ \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        ).stdout
    except Exception:
        return None
    children = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    if not children:
        return None
    pids = {int(root_pid)}
    stack = [int(root_pid)]
    while stack:
        for c in children.get(stack.pop(), []):
            if c not in pids:
                pids.add(c)
                stack.append(c)
    return pids


def _reclaim_studio_port(client):
    """Kill a StudioMCP.exe that owns Studio's MCP port but is NOT our own child.

    The deadlock this breaks (reported live, survives every restart combo):
    a zombie StudioMCP.exe from a crashed session keeps LISTENING on 13469.
    The user reopens Studio -> its MCP plugin does its ONE-SHOT registration
    against the ZOMBIE (wasted). The user restarts the bridge -> Studio is now
    running, so _kill_orphan_studio_mcp's safety guard skips the cleanup, and
    check_studio_port waves the zombie through too (its path IS under Roblox).
    Our fresh StudioMCP can't own the port, Studio never re-registers on its
    own -> 0 tools forever, no restart order can fix it by hand.

    Ownership is decided by PID, not heuristics: we know the PID of the
    launcher we spawned (client.proc), so a StudioMCP.exe holding the port
    outside that process tree is a leftover by definition - Studio open or
    not. If the process tree can't be read, we do nothing (never risk killing
    our own healthy child on bad data). Returns True if a zombie was killed;
    the caller must then restart the roblox proxy (safe here even with Studio
    open: the plugin's single registration already went to the zombie, so
    there is no attempt left for a restart to collide with) AND tell the user
    to open Assistant Settings > MCP Servers so the plugin re-registers.
    """
    owner = _port_owner(STUDIO_MCP_PORT)
    if not owner:
        return False
    pid, name, path = owner
    # Only ever kill a StudioMCP.exe. Studio itself holding the port is fine;
    # a non-Roblox squatter is check_studio_port's (interactive) job.
    if "studiomcp" not in (name or "").lower():
        return False
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    if client is not None and client.proc is not None and client.is_alive():
        tree = _descendant_pids(client.proc.pid)
        if tree is None or pid_i in tree:
            return False  # ours, or unknowable - leave it alone
    log(f"port {STUDIO_MCP_PORT} is held by a StudioMCP.exe (pid {pid_i}) that this "
        "bridge did NOT launch - a leftover from a previous session. Studio "
        "registered to it, so our proxy sees 0 tools.", "yl")
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid_i)],
                       capture_output=True, text=True, timeout=8)
    except Exception as e:
        log(f"could not kill the leftover StudioMCP.exe: {e}", "rd")
        return False
    log(f"killed the leftover StudioMCP.exe (pid {pid_i}) to free Studio's MCP port.", "cy")
    return True


def _process_cmdline(pid):
    """Full command line of `pid`, or "" if it can't be read. Win32 only.

    Used to tell OUR OWN kind of process (a python running bridge.py) apart
    from an unrelated app that merely happens to listen on the same port -
    the process NAME is just "python"/"py"/"pythonw", far too generic to kill
    on. The command line is what proves it is a leftover bridge."""
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" "
             f"-ErrorAction SilentlyContinue).CommandLine"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8,
        ).stdout
    except Exception:
        return ""
    return (out or "").strip()


def _reclaim_bridge_port():
    """Free OUR OWN listen port (17613) from a leftover bridge before we bind.

    The common failure (reported live, WinError 10048 on bind): the user
    relaunches start.bat while an earlier bridge.py is still running - window
    closed with the X instead of Ctrl+C, a previous crash that left a detached
    python, or a double double-click. The old process still holds the port, so
    websockets.serve() dies on bind with a cryptic (localised) OSError and the
    whole bridge exits code 1.

    We reuse _port_owner (already generic over the port) and only ever kill a
    process we can PROVE is another bridge.py - never a same-name innocent
    (some unrelated python listening on 17613): the guard is the command line
    containing "bridge.py", plus an explicit self-exclusion by PID. Anything
    else (a non-python app, or a python whose cmdline we can't read) is left
    alone and surfaced to the user by the caller's friendly bind-error path.
    Returns True if a leftover bridge was killed."""
    owner = _port_owner(PORT)
    if not owner:
        return False
    pid, name, path = owner
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_i == os.getpid():
        return False  # never kill ourselves (defensive; we haven't bound yet)
    # Must look like a python interpreter AND be running bridge.py. Killing on
    # the port alone would murder whatever legitimately owns 17613.
    if "python" not in (name or "").lower() and "py" != (name or "").lower():
        return False
    cmdline = _process_cmdline(pid_i)
    if "bridge.py" not in cmdline.lower():
        log(f"port {PORT} is held by pid {pid_i} ('{name}') but it does not look "
            f"like a GHS bridge - leaving it alone.", "yl")
        return False
    log(f"port {PORT} is held by a leftover GHS bridge (pid {pid_i}) from a "
        "previous session - killing it so this one can start.", "yl")
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid_i)],
                       capture_output=True, text=True, timeout=8)
    except Exception as e:
        log(f"could not kill the leftover bridge (pid {pid_i}): {e}", "rd")
        return False
    log(f"killed the leftover bridge (pid {pid_i}); the port is free now.", "cy")
    return True


def _kill_port_squatter():
    """Kill a NON-Roblox process holding Studio's MCP port, no questions asked.

    Called only when the child's stderr has PROVEN the port is hijacked (see
    MCPClient.saw_foreign_ws_host - StudioMCP connected to a foreign host and
    could not parse its protocol; the ropilot case). At that point there is no
    ambiguity left to justify check_studio_port's interactive prompt, and the
    prompt was itself a trap: many users never answer it, and the one-shot boot
    check often runs a beat before a background helper (ropilot) grabs the
    port. Here we have hard evidence, so kill the squatter outright. Returns
    (killed, name) so the caller can tell the user which app to uninstall /
    remove from startup, since it will otherwise reclaim the port on next boot.
    """
    owner = _port_owner(STUDIO_MCP_PORT)
    if owner:
        pid, name, path = owner
        if "roblox" in (path or "").lower() or "studiomcp" in (name or "").lower():
            return False, None  # legitimate Studio-side owner; not a squatter
        log(f"port {STUDIO_MCP_PORT} is hijacked by '{name}' (pid {pid}, {path}).", "yl")
        log("    StudioMCP connected to it instead of Roblox Studio - that is why "
            "there are 0 tools.", "yl")
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=8)
        except Exception as e:
            log(f"could not kill '{name}': {e}", "rd")
            return False, name
        log(f"killed '{name}' so Studio can use the port.", "cy")
        return True, name
    # We could NOT resolve who owns the port, yet StudioMCP's stderr proved the
    # port is hijacked (this function is only called under that proof). This is
    # the state that used to fail SILENTLY: _port_owner returning None (e.g. a
    # squatter listening on IPv6 loopback that an IPv4-only netstat missed, or
    # any netstat quirk) left the user staring at 0 tools with no explanation.
    # Never be silent here. Try a name-based fallback for the known offender
    # (ropilot ships a background helper that squats this port), then always
    # tell the user what we know.
    log(f"port {STUDIO_MCP_PORT} is hijacked (StudioMCP could not talk to Roblox "
        "Studio on it) but the owning process could not be identified by port.", "yl")
    # ropilot is a multi-process app (validated live 2026-07-13): the port is
    # held by ropilot-infra-helper.exe, supervised by ropilot-infra.exe. Kill
    # both so the supervisor can't just respawn the helper and re-grab the port.
    killed_name = None
    for img in ("ropilot-infra-helper.exe", "ropilot-infra.exe", "ropilot.exe"):
        try:
            res = subprocess.run(["taskkill", "/F", "/IM", img],
                                 capture_output=True, text=True, timeout=8)
        except Exception:
            continue
        if res.returncode == 0:
            killed_name = img
            log(f"killed '{img}' (known port squatter) so Studio can use the port.", "cy")
    if killed_name:
        return True, killed_name
    log("    Could not auto-kill it. Find it manually: run  netstat -ano | "
        f"findstr {STUDIO_MCP_PORT}  then end that PID in Task Manager.", "yl")
    return False, None


def _print_squatter_hint(name):
    """After killing a port squatter (e.g. ropilot), tell the user how to stop
    it coming back - it is a background helper that respawns on the next boot
    and re-grabs the port before Studio, which is why a PC reboot never fixed
    this class of 0-tools report."""
    app = name or "the other app"
    action_banner([
        f"'{app}' fights Roblox Studio for its connection - it will keep",
        "coming back after every restart until you remove it.",
        f"1. Uninstall '{app}' (or remove it from Windows startup).",
        "2. In Roblox Studio: Assistant Settings > MCP Servers,",
        "   turn OFF then back ON 'Enable Studio as MCP server'.",
    ])


def _print_reregister_hint():
    """The one user action that completes a zombie-kill recovery: Studio's MCP
    plugin registers only once per boot and that attempt went to the zombie,
    so after the kill + proxy restart the user must make it register again."""
    # Opening the panel alone is technically enough to re-register, but we tell
    # the user to toggle OFF/ON to be sure - a toggle strictly implies opening
    # the panel, so it can never do less, and it removes any ambiguity about
    # whether "just looking at it" counted. Same wording as the squatter/no-place
    # banners so all three read as one identical instruction, not three variants.
    action_banner([
        "Go to Roblox Studio now.",
        "Turn OFF then back ON: Assistant Settings > MCP Servers",
        "         > 'Enable Studio as MCP server'",
        "Wait about 10 seconds - this window will turn green.",
    ])


def check_studio_port():
    """Warn (and optionally kill) a NON-Roblox process squatting Studio's MCP port.

    A third-party tool (e.g. "ropilot") that binds 13469 before Studio does
    hijacks the MCP channel: StudioMCP connects to IT instead of Studio, the
    handshake succeeds but tools/list never answers -> the bridge sees 0 tools.
    This is silent and brutal to diagnose, so we surface it up front.
    """
    owner = _port_owner(STUDIO_MCP_PORT)
    if not owner:
        return False
    pid, name, path = owner
    # The legitimate holder is Studio itself / a Roblox helper: its path lives
    # under a "...\Roblox\..." folder. Anything else is an intruder.
    if "roblox" in (path or "").lower():
        return False
    where = path or name
    log(f"port {STUDIO_MCP_PORT} (Studio's MCP port) is held by a non-Roblox process:", "yl")
    log(f"    {name} (pid {pid})  {where}", "yl")
    log("    This will block Studio's tools (the bridge will see 0 tools).", "yl")
    try:
        ans = input("    Kill this process so Studio can use the port? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans in ("y", "yes", "o", "oui"):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=8)
            log(f"killed {name} (pid {pid}). Studio can use the port now.", "cy")
            # Tell the user the finishing step IMMEDIATELY, here, instead of only
            # after the ~48s server-launch grace loop that follows: killing the
            # squatter frees the port, but Studio's MCP plugin registers only
            # once per boot and that attempt already went to the squatter, so it
            # will NOT re-attach on its own - a toggle is needed. Printing this
            # now (not 48s later, after start_all's grace loop) is what turns a
            # ~1-minute "why is nothing happening" wait into an act-right-away
            # instruction. Uses action_banner (not log) so a non-technical user
            # visually cannot miss it among the surrounding status lines - seen
            # live indistinguishable when both used the same plain color.
            action_banner([
                "Go to Roblox Studio now.",
                "Turn OFF then back ON: Assistant Settings > MCP Servers",
                "         > 'Enable Studio as MCP server'",
                "Wait about 10 seconds - this window will turn green.",
            ])
            return True  # a squatter WAS killed -> Studio must reclaim the port
        except Exception as e:
            log(f"could not kill it: {e}", "rd")
    else:
        log("left it running. Close it yourself, then restart the bridge.", "yl")
    return False


_TRANSIENT_STUDIO_MARKERS = (
    "no roblox studio instance", "no active studio", "studio instance is connect",
    "studio instance connected", "not connected to", "no studio instance",
)


def _looks_like_transient_studio_drop(text):
    low = (text or "").lower()
    return any(m in low for m in _TRANSIENT_STUDIO_MARKERS)


# ── config.json read / write (for extension-driven add/remove) ──────────────
def _read_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                cfg.setdefault("mcpServers", {})
                return cfg
        except Exception as e:
            log(f"config.json unreadable ({e}) - starting from a fresh one", "yl")
    return {"mcpServers": {PRIMARY_SERVER_ID: {"command": "launch_studio_mcp.py", "args": []}}}


def _write_config(cfg):
    """Atomic write so a crash mid-write never leaves a truncated config.json."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def config_add_server(server_id, command, args=None, env=None):
    """Add/replace an addon server in config.json. Refuses to touch the primary
    (roblox) server. Returns (ok, error)."""
    sid = (server_id or "").strip()
    if not sid:
        return False, "server id is required"
    if sid == PRIMARY_SERVER_ID:
        return False, f"'{PRIMARY_SERVER_ID}' is the primary server and cannot be edited"
    if not (command or "").strip():
        return False, "a command is required"
    cfg = _read_config()
    spec = {"command": command.strip(), "args": list(args or [])}
    if env:
        spec["env"] = dict(env)
    cfg["mcpServers"][sid] = spec
    try:
        _write_config(cfg)
    except Exception as e:
        return False, f"could not write config.json: {e}"
    return True, None


def config_remove_server(server_id):
    """Remove an addon server from config.json. Refuses the primary server."""
    sid = (server_id or "").strip()
    if sid in (PRIMARY_SERVER_ID, "system"):
        return False, f"'{sid}' is a built-in server and cannot be removed"
    cfg = _read_config()
    servers = cfg.get("mcpServers", {})
    if sid in servers:
        del servers[sid]
        try:
            _write_config(cfg)
        except Exception as e:
            return False, f"could not write config.json: {e}"
    return True, None


def restart_self():
    """Replace this process with a fresh one so config.json is reloaded from
    scratch. Children are killed first to free their stdio pipes / ports before
    the new instance claims them."""
    log("restarting bridge to load new server config...", "yl")
    try:
        for c in mgr.clients.values():
            c.stop()
    except Exception:
        pass
    if _log_file:
        try:
            _log_file.flush()
        except Exception:
            pass
    argv = list(sys.argv)
    script = os.path.abspath(argv[0]) if argv else os.path.abspath(__file__)
    argv = [script] + argv[1:]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([sys.executable] + argv, cwd=HERE, creationflags=creationflags)
        os._exit(0)
    except Exception as e:
        log(f"spawn failed: {e}, attempting execv...", "rd")
        try:
            os.execv(sys.executable, [sys.executable] + argv)
        except Exception as e2:
            log(f"could not restart bridge: {e2}", "rd")
            os._exit(1)


# ══════════════════════════════════════════════════════════════════════════
#  HARDENED MCP CLIENT  (one per server in config.json)
# ══════════════════════════════════════════════════════════════════════════
class MCPClient:
    def __init__(self, server_id, command, args, env=None):
        self.id = server_id
        self.command = command
        self.args = list(args or [])
        self.env = env or {}
        self.proc = None
        self.req_id = 1
        self.write_lock = threading.Lock()
        self.call_lock = threading.Lock()   # serialize tool calls (single stdio pipe)
        self.pending = {}                    # id -> queue.Queue (one slot)
        self.pend_lock = threading.Lock()
        self.tools_cache = []
        self.start_lock = threading.Lock()
        self._reader_thread = None
        # Crash-loop forensics (read by server_watch). The auto-restart used to
        # hide a server that something else kills over and over: the terminal
        # showed an endless quiet restart cycle with no explanation at all. We
        # keep just enough state to NAME the problem in the terminal instead:
        #  - last_exit: exit code from the final _reader EOF (crash vs kill hint)
        #  - stderr_tail: the last few stderr lines (usually the actual reason -
        #    port bind failure, missing dependency, crash trace)
        #  - restart_times: recent auto-restart timestamps (loop detector input)
        #  - loop_warned_at: throttle so the big red banner prints once per
        #    cooldown, not every 5s poll
        self.last_exit = None
        self.stderr_tail = []
        self.restart_times = []
        self.loop_warned_at = 0.0
        # Set when the configured command itself couldn't be launched at all
        # (e.g. 'uvx' not installed / not on PATH). This is NOT a crash - the
        # process never existed, so last_exit/stderr_tail stay empty and the
        # generic crash-loop banner used to print "the server printed no error
        # output before dying", which is misleading for a config problem the
        # user can fix in seconds. Kept across restarts so the banner can name
        # the real cause instead.
        self.start_error = None
        # Set when StudioMCP's stderr shows it connected to a FOREIGN WS host on
        # Studio's MCP port (not Studio). The unmistakable signature is a parse
        # error on the host's messages ("missing field `type`") - Studio speaks
        # the expected protocol, a squatter like ropilot speaks its own. This is
        # a timing-independent proof that the port is hijacked, unlike the
        # one-shot check_studio_port() boot probe which can miss a squatter that
        # grabs the port a moment after boot (seen live 2026-07-13: ropilot took
        # the port ~1s after the boot check ran, so nothing was flagged).
        self.saw_foreign_ws_host = False

    # ── lifecycle ─────────────────────────────────────────────────────────
    def _resolve(self, s):
        return os.path.expandvars(os.path.expanduser(str(s)))

    def start(self):
        with self.start_lock:
            if self.is_alive():
                return
            cmd = [self._resolve(self.command)] + [self._resolve(a) for a in self.args]
            # A bare .py command (relative paths resolve against the bridge dir)
            # is run with the SAME interpreter the bridge itself uses, so it works
            # even on installs where only the `py` launcher exists (no `python`
            # on PATH). This is how the Studio MCP launcher is wired by default.
            if cmd[0].lower() in ("python", "python3", "python.exe"):
                cmd[0] = sys.executable
                if len(cmd) > 1 and cmd[1].lower().endswith(".py") and not os.path.isabs(cmd[1]):
                    cmd[1] = os.path.join(HERE, cmd[1])
            elif cmd[0].lower().endswith(".py"):
                script = cmd[0]
                if not os.path.isabs(script):
                    script = os.path.join(HERE, script)
                cmd = [sys.executable, script] + cmd[1:]
            # On Windows, npx/npm/yarn/pnpm/bunx are .cmd shims that Popen can't
            # launch directly (WinError 2). Run them through cmd.exe so any
            # node-based MCP server "just works" from config.json.
            if sys.platform == "win32":
                base = os.path.basename(cmd[0]).lower()
                stem = base.split(".")[0]
                if stem in ("npx", "npm", "yarn", "pnpm", "bunx") or base.endswith((".cmd", ".bat")):
                    if cmd[0].lower() not in ("cmd.exe", "cmd"):
                        cmd = ["cmd.exe", "/c"] + cmd
            env = dict(os.environ)
            for k, v in self.env.items():
                env[k] = self._resolve(v)
            log(f"[{self.id}] launching  ({' '.join(cmd)})", "cy")
            with _Spinner(f"    [{self.id}] starting..."):
                try:
                    self.proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                        encoding="utf-8",
                        errors="replace",
                        cwd=HERE,
                        env=env,
                    )
                except FileNotFoundError:
                    # The OS couldn't find cmd[0] at all - this is a config
                    # problem (missing dependency, typo, not on PATH), not a
                    # transient crash. Auto-restart will keep retrying (the
                    # user may install it later), but name the real cause so
                    # it doesn't just look like an endless silent restart loop.
                    self.start_error = (
                        f"command not found: '{cmd[0]}' - is it installed and on PATH? "
                        f"(configured for server '{self.id}' in config.json)"
                    )
                    log(f"[{self.id}] {self.start_error}", "rd")
                    raise
                except OSError as e:
                    self.start_error = f"could not launch '{cmd[0]}': {e}"
                    log(f"[{self.id}] {self.start_error}", "rd")
                    raise
                else:
                    self.start_error = None
                with self.pend_lock:
                    self.pending.clear()
                self.saw_foreign_ws_host = False  # fresh process, fresh verdict
                self._reader_thread = threading.Thread(target=self._reader, args=(self.proc,), daemon=True)
                self._reader_thread.start()
                threading.Thread(target=self._stderr_drain, args=(self.proc,), daemon=True).start()

                # MCP handshake.
                self._request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ghs-bridge", "version": "1.0"},
                }, timeout=30)
                self._notify("notifications/initialized")
                # Some MCP servers (notably Roblox's StudioMCP) advertise 0 tools at
                # the instant initialize returns, because they connect to their
                # backend (the running Studio) a moment AFTER the stdio handshake.
                # A single tools/list then caches an empty list forever. So if we
                # get nothing, retry for a few seconds to let the backend attach.
                # Short per-attempt timeout so the bridge never looks frozen if the
                # server stays silent (e.g. Studio not open yet); ~12s total budget.
                for _ in range(12):
                    if self.refresh_tools(timeout=3):
                        break
                    if not self.is_alive():
                        break
                    time.sleep(1.0)
            log(f"[{self.id}] MCP server up  ({len(self.tools_cache)} tools advertised)", "cy")

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def restart(self):
        log(f"[{self.id}] restarting...", "yl")
        self.stop()
        time.sleep(0.4)
        self.start()

    def stop(self):
        with self.pend_lock:
            for q in self.pending.values():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            self.pending.clear()
        if self.proc:
            try:
                for p in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                    if p and hasattr(p, "close") and not getattr(p, "closed", False):
                        try:
                            p.close()
                        except Exception:
                            pass
            except Exception:
                pass
            # proc.terminate() (TerminateProcess on Windows) only kills THIS
            # pid. Our command is often a wrapper (e.g. launch_studio_mcp.py)
            # that Popen()s a real child (StudioMCP.exe) to own the stdio
            # pipes - terminate() would leave that child orphaned, still bound
            # to Studio's MCP port, fighting the next restart's fresh instance.
            # taskkill /T kills the whole tree.
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        capture_output=True, timeout=8,
                    )
                else:
                    self.proc.terminate()
            except Exception:
                pass
        self.proc = None

    # ── io threads ────────────────────────────────────────────────────────
    def _reader(self, proc):
        stream = proc.stdout
        while True:
            try:
                line = stream.readline()
            except Exception:
                break
            if line == "":  # EOF -> process exited
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue  # stray non-JSON log on stdout
            mid = msg.get("id")
            if mid is None:
                continue  # server notification, nothing waits on it
            with self.pend_lock:
                q = self.pending.get(mid)
            if q is not None:
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass
        code = proc.poll()
        self.last_exit = code  # kept for the crash-loop banner in server_watch
        log(f"[{self.id}] stdout closed (process ended, exit code {code})", "rd")
        with self.pend_lock:
            for q in self.pending.values():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

    def _stderr_drain(self, proc):
        # Surface the child's stderr instead of silently discarding it - this
        # is often the ONLY clue why a server died (crash trace, port bind
        # failure, missing Studio, etc).
        try:
            for line in iter(proc.stderr.readline, ""):
                line = line.rstrip()
                if line:
                    # Ring buffer of the last stderr lines: when the server
                    # enters a crash loop, these are printed in the terminal
                    # banner - they are usually the only real explanation
                    # (port already in use, module not found, crash trace).
                    self.stderr_tail.append(line)
                    if len(self.stderr_tail) > 8:
                        self.stderr_tail.pop(0)
                    # Squatter signature: StudioMCP connected to a non-Studio host
                    # on the MCP port and can't parse its protocol. This is the
                    # ropilot hijack, timing-independent (see saw_foreign_ws_host).
                    low = line.lower()
                    if "failed to parse message from ws host" in low or "missing field `type`" in low:
                        self.saw_foreign_ws_host = True
                    log(f"[{self.id}] stderr: {line}", "yl", terminal=False)
        except Exception:
            pass

    # ── jsonrpc ───────────────────────────────────────────────────────────
    def _next_id(self):
        with self.write_lock:
            rid = self.req_id
            self.req_id += 1
            return rid

    def _notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self.write_lock:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()

    def _request(self, method, params, timeout):
        if not self.is_alive():
            raise RuntimeError(f"server '{self.id}' is not running")
        rid = self._next_id()
        q = queue.Queue(maxsize=1)
        with self.pend_lock:
            self.pending[rid] = q
        try:
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            with self.write_lock:
                self.proc.stdin.write(json.dumps(payload) + "\n")
                self.proc.stdin.flush()
            try:
                return q.get(timeout=timeout)
            except queue.Empty:
                return None
        finally:
            with self.pend_lock:
                self.pending.pop(rid, None)

    # ── high-level ────────────────────────────────────────────────────────
    def refresh_tools(self, timeout=20):
        msg = self._request("tools/list", {}, timeout=timeout)
        if msg and "result" in msg:
            self.tools_cache = msg["result"].get("tools", [])
        return self.tools_cache

    def call_tool(self, name, arguments, timeout):
        """Returns {"text":..., "images":[...]}. Raises on error/timeout."""
        with self.call_lock:
            for attempt in (1, 2):
                if not self.is_alive():
                    self.restart()
                msg = self._request("tools/call",
                                    {"name": name, "arguments": arguments}, timeout)
                if msg is None:
                    # The server process did not reply within `timeout` seconds!
                    # Its stdio loop is unresponsive or stuck in a blocking call.
                    # We MUST restart it to clear the deadlocked stdin/stdout pipe, so
                    # subsequent tool calls are not blocked forever.
                    log(f"[{self.id}] unresponsive after {timeout}s - restarting to recover pipe...", "yl")
                    self.restart()
                    raise TimeoutError(
                        f"No response from server '{self.id}' after {timeout}s.")
                if msg.get("error"):
                    err = msg["error"]
                    err_text = err.get("message", json.dumps(err))
                    if attempt == 1 and _looks_like_transient_studio_drop(err_text):
                        log(f"[{self.id}] {name}: transient Studio drop, retrying once...", "yl")
                        time.sleep(1.5)
                        continue
                    raise RuntimeError(err_text)
                content = msg.get("result", {}).get("content", [])
                text = "\n".join(it.get("text", "") for it in content if it.get("type") == "text")
                images = [{"data": it["data"], "mimeType": it.get("mimeType", "image/jpeg")}
                          for it in content if it.get("type") == "image" and it.get("data")]
                if not text and not images and content:
                    text = json.dumps(content)[:4000]
                # Studio's own MCP proxy briefly loses its binding to the Studio
                # app every few seconds on some machines (seen live: repeated
                # "Bound studio ... disconnected for proxy ..." stderr, self-
                # healing within ~1-4s). A tool call landing in that window
                # fails with a "no Studio instance connected" style message
                # even though Studio is genuinely open - confirmed live via
                # start_stop_play. One short retry rides through it instead of
                # surfacing a spurious error to the user.
                if attempt == 1 and _looks_like_transient_studio_drop(text):
                    log(f"[{self.id}] {name}: transient Studio drop, retrying once...", "yl")
                    time.sleep(1.5)
                    continue
                return {"text": text, "images": images}


# ══════════════════════════════════════════════════════════════════════════
#  NATIVE BUILTIN SUPERPOWERS (direct binary downloads & shell execution)
# ══════════════════════════════════════════════════════════════════════════
#  SECURITY & SANDBOX GUARDRAILS (Protección contra borrado y escape de carpetas)
# ══════════════════════════════════════════════════════════════════════════
import re

try:
    from agent_brain import brain
except Exception as _e_brain:
    brain = None

try:
    from task_pipeline import task_pipeline
except Exception as _e_tp:
    task_pipeline = None

try:
    from media_transcriber import transcriber
except Exception as _e_mt:
    transcriber = None

try:
    from video_to_playbook import video_to_playbook
except Exception as _e_v2p:
    video_to_playbook = None

try:
    from browser_operator import browser_op
except Exception as _e_bo:
    browser_op = None

DEFAULT_SANDBOX_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

def get_security_config():
    """Read security settings from config.json with safe defaults and auto-heal on new PCs."""
    cfg = _read_config()
    sec = cfg.get("security", {})
    configured = sec.get("sandbox_dir")
    if not configured or not os.path.exists(configured):
        configured = DEFAULT_SANDBOX_DIR
    os.makedirs(configured, exist_ok=True)
    return {
        "sandbox_dir": os.path.normpath(configured),
        "allow_read": bool(sec.get("allow_read", True)),
        "allow_write": bool(sec.get("allow_write", True)),
        "allow_delete": bool(sec.get("allow_delete", False)),   # STRICTLY FALSE BY DEFAULT
        "allow_shell": bool(sec.get("allow_shell", True)),
    }

def update_security_config(patch):
    cfg = _read_config()
    sec = cfg.setdefault("security", {})
    sec.update(patch)
    try:
        _write_config(cfg)
    except Exception as e:
        log(f"[security] could not write config.json: {e}", "rd")
    return get_security_config()

def is_safe_path(target_path, sandbox_dir):
    """Ensure path resolves strictly inside the sandbox directory (no traversal / escape)."""
    try:
        resolved_target = os.path.realpath(os.path.abspath(target_path))
        resolved_sandbox = os.path.realpath(os.path.abspath(sandbox_dir))
        return os.path.commonpath([resolved_target, resolved_sandbox]) == resolved_sandbox
    except Exception:
        return False

DESTRUCTIVE_COMMAND_PATTERNS = [
    r"\bRemove-Item\b",
    r"\bRemove-ItemProperty\b",
    r"\bClear-Content\b",
    r"\bClear-Item\b",
    r"\bdel\b",
    r"\berase\b",
    r"\brmdir\b",
    r"\brd\b",
    r"\brm\s+",
    r"\bFormat-Volume\b",
    r"\bformat\b",
    r"\bdiskpart\b",
    r"\bStop-Computer\b",
    r"\bRestart-Computer\b",
    r"\bshutdown\b",
    r"\bDrop-Database\b",
    r"\btakeown\b",
    r"\bicacls\b",
    r"\breg\s+delete\b",
    r"\bSet-ExecutionPolicy\b",
]

FORBIDDEN_TARGET_PATHS = [
    r"c:\\windows",
    r"c:\\program files",
    r"c:\\program files \(x86\)",
    r"c:\\programdata",
    r"system32",
    r"syswow64",
    r"reg\.exe",
]

def check_command_safety(cmd, allow_delete, sandbox_dir):
    """Inspect shell command against destructive patterns and path traversal."""
    cmd_lower = cmd.lower()
    for pat in FORBIDDEN_TARGET_PATHS:
        if re.search(pat, cmd_lower):
            raise PermissionError(f"ACCESO DENEGADO (Guardrail de Seguridad): Acceso bloqueado a rutas protegidas del sistema ('{pat}').")
    
    # ── GHS Immunity Guardrail: Proteger el puente y servidores MCP contra autodestrucción ──
    kill_python_patterns = [
        r"taskkill.*(?:/im|-im)\s+python(?:\.exe)?",
        r"stop-process.*-(?:processname|name)\s+python",
        r"wmic\s+process\s+where.*name=.*python\.exe.*delete",
        r"pkill.*python",
        r"killall.*python",
    ]
    for pat in kill_python_patterns:
        if re.search(pat, cmd_lower):
            raise PermissionError(
                "ACCESO DENEGADO (Escudo de Inmunidad GHS): Está prohibido matar 'python.exe' "
                "de forma masiva porque destruye el puente WebSocket de GHS y apaga la conexión del agente. "
                "Termina tus procesos de usuario específicamente por su PID o puerto de desarrollo."
            )
    
    # Proteger el PID del puente si se intenta matar directamente
    my_pid = str(os.getpid())
    if re.search(rf"\btaskkill\b.*(?:\b/pid\b|\b-pid\b)\s+{my_pid}\b", cmd_lower):
        raise PermissionError(f"ACCESO DENEGADO (Escudo de Inmunidad GHS): El PID {my_pid} pertenece al proceso raíz de GHS Bridge y no puede ser terminado.")

    if not allow_delete:
        for pat in DESTRUCTIVE_COMMAND_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                raise PermissionError("ACCESO DENEGADO (Protección de Borrado Activa): El comando contiene órdenes destructivas o de eliminación, pero el permiso de borrado ('allow_delete') está DESACTIVADO por seguridad.")


class BuiltinClient:
    """Provides native bridge superpowers with strict sandbox and permissions enforcement."""
    def __init__(self):
        self.id = "system"
        self.tools_cache = [
            {
                "name": "download_file",
                "description": "Download any file from the web (images, PNG sprites, audio, zip, etc.) and save it directly to the user's sandbox Downloads folder. Protected by sandbox path confinement.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The direct web URL of the file to download."
                        },
                        "filename": {
                            "type": "string",
                            "description": "Destination filename or relative path (e.g. 'mario_sprites/mariosheet.png'). Automatically saved inside the user's Downloads folder."
                        }
                    },
                    "required": ["url", "filename"]
                }
            },
            {
                "name": "search_and_download_image",
                "description": "Search the web (Wikipedia & Wikimedia Commons API) for high-resolution images of any person, celebrity, animal, car, place, or object and automatically download it to the user's sandbox Downloads folder. Use this when the user asks for photos or images instead of guessing direct URLs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search term or subject name (e.g. 'Lana Del Rey', 'Miley Cyrus', 'Ferrari F40')."
                        },
                        "filename": {
                            "type": "string",
                            "description": "Optional destination filename inside Downloads (e.g. 'lana_del_rey.jpg'). If omitted, automatically derived."
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "delete_file",
                "description": "Delete a file inside the user's Downloads folder. Strictly requires 'allow_delete' permission to be enabled by the user.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "The relative path of the file to delete inside the Downloads folder."
                        }
                    },
                    "required": ["filename"]
                }
            },
            {
                "name": "run_shell_command",
                "description": "Execute a Windows PowerShell command strictly inside the user's Downloads folder. Destructive/delete commands are blocked unless 'allow_delete' permission is active.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The command line string to execute inside the sandbox folder."
                        }
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "get_security_status",
                "description": "Get the current security sandbox directory and permissions status (read, write, delete, shell).",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "capture_screen",
                "description": "Capture a screenshot of the user's primary monitor and return it as a visual image. Useful for visual inspection, verifying UI state, or checking desktop applications.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Optional filename to save the capture in Downloads (e.g. 'capture.png')."
                        }
                    }
                }
            },
            {
                "name": "learn_lesson",
                "description": "Save a learned rule, mistake resolution, or grading preference to the persistent agent brain so the AI never repeats the same mistake.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "category": { "type": "string", "description": "e.g. 'campus_ui', 'grading_criteria', 'video_handling'" },
                        "situation": { "type": "string", "description": "The context or page where the issue happened." },
                        "issue": { "type": "string", "description": "What went wrong or was ambiguous." },
                        "solution": { "type": "string", "description": "How to solve it." },
                        "golden_rule": { "type": "string", "description": "The permanent rule for next time." }
                    },
                    "required": ["category", "situation", "golden_rule"]
                }
            },
            {
                "name": "get_brain_rules",
                "description": "Retrieve all past learned lessons, procedures, and rules stored in the agent's persistent memory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "category": { "type": "string", "description": "Optional category filter." }
                    }
                }
            },
            {
                "name": "save_playbook",
                "description": "Save an autonomous procedural recipe (Playbook/SOP) for ANY workflow (e-commerce, scraping, university, internal portals, desktop apps) learned from user instructions or video demonstration.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": { "type": "string", "description": "Unique identifier of the playbook (e.g. 'descargar_reportes_ventas', 'evaluar_trabajos_web')." },
                        "description": { "type": "string", "description": "What this procedure achieves." },
                        "platform": { "type": "string", "description": "Target website or software (e.g. 'moodle', 'shopify', 'blender', 'general')." },
                        "steps": {
                            "type": "array",
                            "items": { "type": "string" },
                            "description": "Sequential steps to execute."
                        }
                    },
                    "required": ["name", "steps"]
                }
            },
            {
                "name": "get_playbook",
                "description": "Retrieve an autonomous procedural playbook/recipe from the agent brain.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": { "type": "string", "description": "Name of the playbook." }
                    },
                    "required": ["name"]
                }
            },
            {
                "name": "queue_load_batch",
                "description": "Universal Batch Loader. Loads a queue of items of ANY type (students, products, orders, audit targets, URLs, documents) and automatically clusters duplicate/shared targets to avoid redundant AI calls.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "batch_name": { "type": "string", "description": "Name or identifier of the batch (e.g. 'evaluacion_semana_5', 'actualizacion_precios_tienda')." },
                        "batch_type": { "type": "string", "description": "Category of batch (e.g. 'general', 'grading', 'e_commerce', 'scraping')." },
                        "items": {
                            "type": "array",
                            "description": "List of item objects. Each item can have 'label'/'name', 'identifier'/'id', 'target_url'/'url', and 'raw_payload'/'metadata'.",
                            "items": { "type": "object" }
                        }
                    },
                    "required": ["batch_name", "items"]
                }
            },
            {
                "name": "queue_get_status",
                "description": "Get live progress statistics for any batch queue (total items, completed, pending groups, percentage).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "batch_name": { "type": "string", "description": "Optional batch name filter." }
                    }
                }
            },
            {
                "name": "queue_get_next_task",
                "description": "Fetch the next pending task unit from the universal queue, along with all associated item labels and URLs.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "batch_name": { "type": "string", "description": "Optional batch name filter." }
                    }
                }
            },
            {
                "name": "queue_save_result",
                "description": "Save the outcome/grade/audit result for a task unit, automatically replicating the result across all items linked in that group.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "group_id": { "type": "integer", "description": "The task group ID." },
                        "result_data": { "description": "Result (number, grade, dictionary, or text) to persist." },
                        "notes": { "type": "string", "description": "Feedback, explanation, or comments." },
                        "key_evidence": { "type": "string", "description": "Optional timestamps, links, or proof." }
                    },
                    "required": ["group_id", "result_data"]
                }
            },
            {
                "name": "queue_get_resume_prompt",
                "description": "Generate a universal hot-swap session resumption prompt for when changing AI accounts or opening a fresh chat without losing queue progress.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "batch_name": { "type": "string", "description": "Optional batch name filter." }
                    }
                }
            },
            {
                "name": "queue_export_csv",
                "description": "Export all batch results, labels, notes, and targets to a clean CSV report in the workspace directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "batch_name": { "type": "string", "description": "Optional batch name filter." },
                        "filename": { "type": "string", "description": "Output CSV filename (default: 'batch_results.csv')." }
                    }
                }
            },
            {
                "name": "get_media_transcript",
                "description": "Extract full spoken text and timestamped segments from any YouTube video URL or local audio/video file for instant analysis without heavy tokens.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target": { "type": "string", "description": "YouTube video URL or path to a local media file." }
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "convert_demonstration_to_playbook",
                "description": "Convert a user screen recording video, audio demonstration, or manual steps into a persistent, structured Playbook/SOP stored in the agent's brain.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "media_path_or_url": { "type": "string", "description": "Path to local video/audio recording or YouTube demonstration." },
                        "playbook_name": { "type": "string", "description": "Identifier name for the playbook." },
                        "description": { "type": "string", "description": "What this procedure accomplishes." },
                        "platform": { "type": "string", "description": "Target website or application (e.g. 'moodle', 'shopify', 'excel')." },
                        "manual_steps": { "type": "array", "items": { "type": "string" }, "description": "Optional list of steps." }
                    },
                    "required": ["media_path_or_url", "playbook_name"]
                }
            },
            {
                "name": "browser_get_open_tabs",
                "description": "List all active tabs in the user's Chrome/Edge browser via Chrome DevTools Protocol (CDP).",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "browser_open_url",
                "description": "Open a target URL in a new browser tab.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": { "type": "string", "description": "Web URL to navigate to." }
                    },
                    "required": ["url"]
                }
            },
            {
                "name": "browser_click",
                "description": "Click any button, link, or tab in the active browser page using a CSS selector.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "selector": { "type": "string", "description": "CSS selector of the element to click (e.g. '#guardar-btn', '.tab-reportes')." }
                    },
                    "required": ["selector"]
                }
            },
            {
                "name": "browser_fill",
                "description": "Fill a form input, textarea, or search field in the active browser page using a CSS selector.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "selector": { "type": "string", "description": "CSS selector of the input field." },
                        "value": { "type": "string", "description": "Text value to insert." }
                    },
                    "required": ["selector", "value"]
                }
            },
            {
                "name": "browser_extract_table",
                "description": "Extract structured rows and headers from any HTML table on the active browser page into JSON.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "selector": { "type": "string", "description": "Optional CSS selector of the table (default: 'table')." }
                    }
                }
            }
        ]

    def is_alive(self):
        return True

    def start(self):
        pass

    def stop(self):
        pass

    def refresh_tools(self):
        return self.tools_cache

    def call_tool(self, name, arguments, timeout):
        sec = get_security_config()
        sandbox_dir = sec["sandbox_dir"]
        os.makedirs(sandbox_dir, exist_ok=True)

        if name == "get_security_status":
            return {
                "text": f"Security Sandbox Status:\n- Folder: {sandbox_dir}\n- Read: {'Allowed' if sec['allow_read'] else 'Blocked'}\n- Write: {'Allowed' if sec['allow_write'] else 'Blocked'}\n- Delete: {'Allowed' if sec['allow_delete'] else 'BLOCKED (Protected)'}\n- Shell: {'Allowed' if sec['allow_shell'] else 'Blocked'}",
                "images": []
            }

        elif name == "download_file":
            if not sec["allow_write"]:
                raise PermissionError("ACCESO DENEGADO (Guardrail de Seguridad): El permiso de escritura ('allow_write') está desactivado.")
            url = (arguments.get("url") or "").strip()
            filename = (arguments.get("filename") or "").strip()
            if not url or not filename:
                raise ValueError("Both 'url' and 'filename' are required.")

            target_path = os.path.normpath(os.path.join(sandbox_dir, filename))
            if not is_safe_path(target_path, sandbox_dir):
                target_path = os.path.join(sandbox_dir, os.path.basename(filename))
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            import urllib.request
            import urllib.parse
            import re

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            }

            candidate_urls = [url]
            # Wikimedia thumbnail healing: Wikimedia rejects non-standard sizes with HTTP 400
            if "wikimedia.org" in url and "/thumb/" in url:
                orig = re.sub(r'/thumb(/[^/]+/[^/]+/[^/]+)/[^/]+$', r'\1', url)
                if orig != url:
                    candidate_urls.append(orig)
                c1280 = re.sub(r'/\d+px-([^/]+)$', r'/1280px-\1', url)
                if c1280 != url:
                    candidate_urls.append(c1280)
                c800 = re.sub(r'/\d+px-([^/]+)$', r'/800px-\1', url)
                if c800 != url:
                    candidate_urls.append(c800)

            data = None
            last_err = None
            for cand in candidate_urls:
                try:
                    req = urllib.request.Request(cand, headers=headers)
                    with urllib.request.urlopen(req, timeout=timeout or 30) as resp:
                        data = resp.read()
                        break
                except Exception as e:
                    last_err = e
                    continue

            # If still failed (e.g. 404 hallucinated URL), try automatic recovery search
            if not data:
                clean_term = os.path.splitext(os.path.basename(filename))[0].replace("_", " ").replace("-", " ")
                try:
                    search_api = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(clean_term)}&prop=pageimages&format=json&pilicense=any&pithumbsize=1280"
                    sreq = urllib.request.Request(search_api, headers={"User-Agent": "GHS/1.5.5"})
                    with urllib.request.urlopen(sreq, timeout=8) as sr:
                        sdata = json.loads(sr.read())
                        for pid, pdata in sdata.get("query", {}).get("pages", {}).items():
                            if "thumbnail" in pdata:
                                auto_url = pdata["thumbnail"]["source"]
                                req = urllib.request.Request(auto_url, headers=headers)
                                with urllib.request.urlopen(req, timeout=timeout or 30) as resp:
                                    data = resp.read()
                                    break
                except Exception:
                    pass

            if not data:
                raise last_err or RuntimeError(f"Could not download file from {url}")

            with open(target_path, "wb") as f:
                f.write(data)

            kb = len(data) / 1024.0
            log(f"[system] download_file: saved {len(data)} bytes -> {target_path}", "gr")
            return {
                "text": f"Successfully downloaded '{os.path.basename(target_path)}' ({kb:.1f} KB) to {target_path}",
                "images": []
            }

        elif name == "search_and_download_image":
            if not sec["allow_write"]:
                raise PermissionError("ACCESO DENEGADO (Guardrail de Seguridad): El permiso de escritura ('allow_write') está desactivado.")
            query = (arguments.get("query") or "").strip()
            if not query:
                raise ValueError("'query' parameter is required.")

            filename = (arguments.get("filename") or "").strip()
            if not filename:
                safe_q = re.sub(r'[^a-zA-Z0-9_-]', '_', query.lower()).strip('_')
                filename = f"{safe_q}.jpg"

            target_path = os.path.normpath(os.path.join(sandbox_dir, filename))
            if not is_safe_path(target_path, sandbox_dir):
                target_path = os.path.join(sandbox_dir, os.path.basename(filename))
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            import urllib.request
            import urllib.parse

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            }

            img_url = None
            # 1. Wikipedia PageImages API (highest quality official photos)
            try:
                wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(query)}&prop=pageimages&format=json&pilicense=any&pithumbsize=1280"
                req = urllib.request.Request(wiki_url, headers={"User-Agent": "GHS/1.5.5"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    res = json.loads(r.read())
                    pages = res.get("query", {}).get("pages", {})
                    for pid, pdata in pages.items():
                        if "thumbnail" in pdata:
                            img_url = pdata["thumbnail"]["source"]
                            break
            except Exception:
                pass

            # 2. Commons Generator Search API (general fallback for any object, car, etc.)
            if not img_url:
                try:
                    c_url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrnamespace=6&gsrsearch={urllib.parse.quote(query)}&gsrlimit=3&prop=imageinfo&iiprop=url&iiurlwidth=1280&format=json"
                    req = urllib.request.Request(c_url, headers={"User-Agent": "GHS/1.5.5"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        res = json.loads(r.read())
                        pages = res.get("query", {}).get("pages", {})
                        for pid, pdata in pages.items():
                            infos = pdata.get("imageinfo", [])
                            if infos and "thumburl" in infos[0]:
                                img_url = infos[0]["thumburl"]
                                break
                            elif infos and "url" in infos[0]:
                                img_url = infos[0]["url"]
                                break
                except Exception:
                    pass

            if not img_url:
                raise RuntimeError(f"No high-resolution images found online for query: '{query}'")

            req = urllib.request.Request(img_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout or 30) as resp:
                data = resp.read()

            with open(target_path, "wb") as f:
                f.write(data)

            kb = len(data) / 1024.0
            log(f"[system] search_and_download_image: saved {len(data)} bytes -> {target_path}", "gr")
            return {
                "text": f"Successfully searched and downloaded image for '{query}' ({kb:.1f} KB) to {target_path}\nSource URL: {img_url}",
                "images": []
            }

        elif name == "delete_file":
            if not sec["allow_delete"]:
                raise PermissionError("ACCESO DENEGADO (Protección de Borrado Activa): El permiso de borrado ('allow_delete') está DESACTIVADO por seguridad. La IA no puede eliminar archivos en este equipo.")
            filename = (arguments.get("filename") or "").strip()
            if not filename:
                raise ValueError("'filename' parameter is required.")
            target_path = os.path.normpath(os.path.join(sandbox_dir, filename))
            if not is_safe_path(target_path, sandbox_dir):
                raise PermissionError(f"ACCESO DENEGADO: La ruta '{filename}' está fuera de la zona segura ({sandbox_dir}).")
            if not os.path.exists(target_path):
                return {"text": f"El archivo '{filename}' no existe en la zona segura.", "images": []}
            if os.path.isdir(target_path):
                raise PermissionError("ACCESO DENEGADO: Por seguridad no se permite borrar directorios completos con delete_file.")
            os.remove(target_path)
            log(f"[system] delete_file: deleted {target_path}", "yl")
            return {"text": f"Archivo '{os.path.basename(target_path)}' eliminado con éxito de la zona segura.", "images": []}

        elif name == "run_shell_command":
            if not sec["allow_shell"]:
                raise PermissionError("ACCESO DENEGADO (Guardrail de Seguridad): La ejecución de comandos shell está desactivada.")
            cmd = (arguments.get("command") or "").strip()
            if not cmd:
                raise ValueError("'command' parameter is required.")
            check_command_safety(cmd, sec["allow_delete"], sandbox_dir)
            log(f"[system] run_shell_command (sandbox={sandbox_dir}): {cmd}", "cy")
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                cwd=sandbox_dir,
                capture_output=True,
                text=True,
                timeout=timeout or 60
            )
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            result_text = f"Exit code: {res.returncode}\n"
            if out:
                result_text += f"Output:\n{out}\n"
            if err:
                result_text += f"Errors:\n{err}\n"
            return {"text": result_text.strip(), "images": []}

        elif name == "capture_screen":
            filename = (arguments.get("filename") or f"screenshot_{int(time.time())}.png").strip()
            target_path = os.path.join(sandbox_dir, filename)
            b64_data = ""
            try:
                from PIL import ImageGrab
                import io, base64
                im = ImageGrab.grab()
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                b64_data = base64.b64encode(buf.getvalue()).decode("ascii")
                im.save(target_path)
            except Exception as e:
                log(f"[system] capture_screen note: {e}", "yl")
            
            if b64_data:
                log(f"[system] capture_screen: saved to {target_path}", "gr")
                return {
                    "text": f"Screenshot captured and saved to {target_path}",
                    "images": [{"data": b64_data, "mimeType": "image/png"}]
                }
            return {
                "text": f"Screen capture attempted. Note: Interactive desktop capture requires an unlocked desktop session. Path: {target_path}",
                "images": []
            }

        elif name == "learn_lesson":
            if not brain:
                return {"text": "AgentBrain module is not available.", "images": []}
            res = brain.add_lesson(
                category=arguments.get("category", "general"),
                situation=arguments.get("situation", ""),
                issue_or_mistake=arguments.get("issue", ""),
                solution=arguments.get("solution", ""),
                golden_rule=arguments.get("golden_rule", "")
            )
            return {"text": f"Regla aprendida y guardada en el cerebro (ID {res['id']}): {res['golden_rule']}", "images": []}

        elif name == "get_brain_rules":
            if not brain:
                return {"text": "AgentBrain module is not available.", "images": []}
            prompt = brain.generate_brain_prompt()
            return {"text": prompt or "No hay lecciones registradas aún en el cerebro.", "images": []}

        elif name == "save_playbook":
            if not brain:
                return {"text": "AgentBrain module is not available.", "images": []}
            res = brain.save_playbook(
                name=arguments.get("name", "unnamed_playbook"),
                description=arguments.get("description", ""),
                steps=arguments.get("steps", []),
                platform=arguments.get("platform", "general")
            )
            return {"text": f"Playbook guardado en el cerebro con éxito: '{res['name']}' ({len(res['steps'])} pasos).", "images": []}

        elif name == "get_playbook":
            if not brain:
                return {"text": "AgentBrain module is not available.", "images": []}
            p = brain.get_playbook(arguments.get("name", ""))
            if not p:
                return {"text": f"Playbook '{arguments.get('name')}' no encontrado.", "images": []}
            return {"text": json.dumps(p, indent=2, ensure_ascii=False), "images": []}

        elif name in ("queue_load_batch", "load_students_batch"):
            target_engine = task_pipeline or eval_engine
            if not target_engine:
                return {"text": "TaskPipeline engine is not available.", "images": []}
            batch_name = arguments.get("batch_name") or arguments.get("course_name") or "Lote_Principal"
            items = arguments.get("items") or arguments.get("students") or []
            if isinstance(items, str):
                items = json.loads(items)
            b_type = arguments.get("batch_type", "general")
            if hasattr(target_engine, "queue_load_batch"):
                res = target_engine.queue_load_batch(batch_name, items, batch_type=b_type)
            else:
                res = target_engine.load_students_batch(batch_name, items)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        elif name in ("queue_get_status", "get_evaluation_status"):
            target_engine = task_pipeline or eval_engine
            if not target_engine:
                return {"text": "TaskPipeline engine is not available.", "images": []}
            batch_name = arguments.get("batch_name") or arguments.get("course_name")
            if hasattr(target_engine, "queue_get_status"):
                status = target_engine.queue_get_status(batch_name)
            else:
                status = target_engine.get_evaluation_status(batch_name)
            return {"text": json.dumps(status, indent=2, ensure_ascii=False), "images": []}

        elif name in ("queue_get_next_task", "get_next_group_to_evaluate"):
            target_engine = task_pipeline or eval_engine
            if not target_engine:
                return {"text": "TaskPipeline engine is not available.", "images": []}
            batch_name = arguments.get("batch_name") or arguments.get("course_name")
            if hasattr(target_engine, "queue_get_next_task"):
                t = target_engine.queue_get_next_task(batch_name)
            else:
                t = target_engine.get_next_group_to_evaluate(batch_name)
            if not t:
                return {"text": "No hay más unidades pendientes en la cola. ¡Todo el lote ha sido completado!", "images": []}
            return {"text": json.dumps(t, indent=2, ensure_ascii=False), "images": []}

        elif name in ("queue_save_result", "save_group_evaluation"):
            target_engine = task_pipeline or eval_engine
            if not target_engine:
                return {"text": "TaskPipeline engine is not available.", "images": []}
            gid = int(arguments.get("group_id", 0))
            res_val = arguments.get("result_data") if "result_data" in arguments else arguments.get("grade")
            notes_val = arguments.get("notes") or arguments.get("feedback") or ""
            evidence_val = arguments.get("key_evidence") or arguments.get("key_moments") or ""
            if hasattr(target_engine, "queue_save_result"):
                res = target_engine.queue_save_result(gid, res_val, notes=notes_val, key_evidence=evidence_val)
            else:
                res = target_engine.save_group_evaluation(gid, float(res_val or 0), notes_val, evidence_val)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        elif name in ("queue_get_resume_prompt", "get_resume_prompt"):
            target_engine = task_pipeline or eval_engine
            if not target_engine:
                return {"text": "TaskPipeline engine is not available.", "images": []}
            batch_name = arguments.get("batch_name") or arguments.get("course_name")
            if hasattr(target_engine, "queue_get_resume_prompt"):
                prompt = target_engine.queue_get_resume_prompt(batch_name)
            else:
                prompt = target_engine.get_resume_prompt(batch_name)
            return {"text": prompt, "images": []}

        elif name in ("queue_export_csv", "export_report_csv"):
            target_engine = task_pipeline or eval_engine
            if not target_engine:
                return {"text": "TaskPipeline engine is not available.", "images": []}
            batch_name = arguments.get("batch_name") or arguments.get("course_name")
            fname = arguments.get("filename", "batch_results.csv")
            if hasattr(target_engine, "queue_export_csv"):
                target = target_engine.queue_export_csv(batch_name, fname)
            else:
                target = target_engine.export_report_csv(batch_name, fname)
            return {"text": f"Reporte exportado exitosamente a: {target}", "images": []}

        elif name == "get_media_transcript":
            if not transcriber:
                return {"text": "MediaTranscriber module is not available.", "images": []}
            target = (arguments.get("target") or "").strip()
            res = transcriber.get_transcript(target)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        elif name == "convert_demonstration_to_playbook":
            if not video_to_playbook:
                return {"text": "VideoToPlaybook module is not available.", "images": []}
            media = arguments.get("media_path_or_url", "")
            pb_name = arguments.get("playbook_name", "unnamed_playbook")
            desc = arguments.get("description", "")
            plat = arguments.get("platform", "general")
            steps = arguments.get("manual_steps")
            res = video_to_playbook.process_demonstration(media, pb_name, description=desc, platform=plat, manual_steps=steps)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        elif name == "browser_get_open_tabs":
            if not browser_op:
                return {"text": "BrowserOperator module is not available.", "images": []}
            tabs = browser_op.get_open_tabs()
            if not tabs:
                return {"text": "No se encontraron pestañas activas con CDP en el puerto 9222. Abre Chrome con: chrome.exe --remote-debugging-port=9222", "images": []}
            return {"text": json.dumps(tabs, indent=2, ensure_ascii=False), "images": []}

        elif name == "browser_open_url":
            if not browser_op:
                return {"text": "BrowserOperator module is not available.", "images": []}
            url = arguments.get("url", "")
            res = browser_op.open_url(url)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        elif name == "browser_click":
            if not browser_op:
                return {"text": "BrowserOperator module is not available.", "images": []}
            sel = arguments.get("selector", "")
            res = browser_op.click_selector(sel)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        elif name == "browser_fill":
            if not browser_op:
                return {"text": "BrowserOperator module is not available.", "images": []}
            sel = arguments.get("selector", "")
            val = arguments.get("value", "")
            res = browser_op.fill_input(sel, val)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        elif name == "browser_extract_table":
            if not browser_op:
                return {"text": "BrowserOperator module is not available.", "images": []}
            sel = arguments.get("selector", "table")
            res = browser_op.extract_table(sel)
            return {"text": json.dumps(res, indent=2, ensure_ascii=False), "images": []}

        raise RuntimeError(f"Unknown system tool '{name}'")


# ══════════════════════════════════════════════════════════════════════════
#  MANAGER  - aggregates every MCP server, routes by tool name.
# ══════════════════════════════════════════════════════════════════════════
class MCPManager:
    def __init__(self):
        self.clients = {}          # server_id -> MCPClient
        self.index = {}            # advertised_name -> (holder, real_name)
        self.index_lock = threading.Lock()
        self.builtin = BuiltinClient()
        self.clients["system"] = self.builtin

    def load_config(self):
        servers = _read_config().get("mcpServers", {}) or {}
        self.clients = {"system": self.builtin}
        for sid, spec in servers.items():
            self.clients[sid] = MCPClient(
                sid, spec.get("command"), spec.get("args"), spec.get("env"))
        log(f"configured {len(self.clients)} MCP server(s): {', '.join(self.clients) or '(none)'}", "cy")

    def start_all(self):
        # Launch every configured server IN PARALLEL, not one after another.
        # client.start() can block for up to ~12s (its own "wait for Studio's
        # tools to appear" grace loop) - with a sequential for-loop, Roblox
        # being first in config.json meant every OTHER server (Blender, any
        # addon) didn't even begin launching until Roblox's grace loop gave
        # up, even though that addon has nothing to do with Roblox and could
        # have been ready in 1-2s. A thread per client removes that
        # dependency entirely: a slow/absent Roblox Studio no longer holds up
        # an addon server the user actually wants right now.
        threads = []
        for sid, client in self.clients.items():
            def _run(sid=sid, client=client):
                try:
                    client.start()
                except Exception as e:
                    log(f"[{sid}] failed to start: {e}  (other servers continue)", "rd")
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        self.rebuild_index()

    def rebuild_index(self):
        """Aggregate server tools. Collisions get a 'server/' prefix."""
        with self.index_lock:
            self.index = {}
            for sid, client in self.clients.items():
                for t in (client.tools_cache or []):
                    name = t.get("name")
                    if not name:
                        continue
                    advertised = name if name not in self.index else f"{sid}/{name}"
                    self.index[advertised] = (client, name)

    def list_tools(self, refresh=False):
        if refresh:
            for sid, client in self.clients.items():
                try:
                    if not client.is_alive():
                        client.start()
                    else:
                        client.refresh_tools()
                except Exception as e:
                    log(f"[{sid}] refresh failed: {e}", "yl")
            self.rebuild_index()
        out = []
        for sid, client in self.clients.items():
            for t in (client.tools_cache or []):
                name = t.get("name")
                advertised = name
                with self.index_lock:
                    # find the advertised key that maps to this (client, name)
                    for k, (holder, real) in self.index.items():
                        if holder is client and real == name:
                            advertised = k
                            break
                tt = dict(t)
                tt["name"] = advertised
                tt["server"] = sid
                out.append(tt)
        return out

    def call(self, name, arguments, timeout):
        with self.index_lock:
            entry = self.index.get(name)
        if entry is None:
            # Maybe a freshly added tool or servers still starting - retry with brief wait.
            for _ in range(8):
                self.rebuild_index()
                with self.index_lock:
                    entry = self.index.get(name)
                if entry is not None:
                    break
                time.sleep(0.5)
        if entry is None:
            raise RuntimeError(f"unknown tool '{name}'")
        holder, real_name = entry
        return holder.call_tool(real_name, arguments, timeout)

    def restart(self, server_id=None):
        targets = [self.clients[server_id]] if server_id and server_id in self.clients else list(self.clients.values())
        for client in targets:
            try:
                client.restart()
            except Exception as e:
                log(f"[{client.id}] restart failed: {e}", "rd")
        self.rebuild_index()

    def health(self):
        res = [{"id": sid, "alive": c.is_alive(), "tools": len(c.tools_cache)}
                for sid, c in self.clients.items()]
        hermes_alive = bool(self.clients.get("hermes") and self.clients["hermes"].is_alive())
        for pid in ["blender", "filesystem", "fetch", "sqlite", "memory", "rea"]:
            if not any(r["id"] == pid for r in res):
                is_active = hermes_alive and (pid not in disabled_presets)
                res.append({"id": pid, "alive": is_active, "tools": 10 if is_active else 0})
        return res

    def any_alive(self):
        return any(c.is_alive() for c in self.clients.values())

    def add_client(self, sid, command, args=None, env=None):
        """Hot-plug a new MCP server in memory without restarting the bridge."""
        if sid in self.clients:
            try:
                self.clients[sid].stop()
            except Exception:
                pass
        client = MCPClient(sid, command, args or [], env)
        self.clients[sid] = client
        client.start()
        self.rebuild_index()
        return client

    def remove_client(self, sid):
        """Hot-unplug an MCP server in memory without restarting the bridge."""
        if sid == "system":
            return
        client = self.clients.pop(sid, None)
        if client:
            try:
                client.stop()
            except Exception:
                pass
            self.rebuild_index()


# ══════════════════════════════════════════════════════════════════════════
#  WEBSOCKET SERVER
# ══════════════════════════════════════════════════════════════════════════
mgr = MCPManager()
clients = set()

# ── Studio connectivity probe ──────────────────────────────────────────────
# The MCP server process stays alive even when Roblox Studio is closed or its
# MCP option is disabled - tool calls then return instantly with an "Unable to
# find an active Studio instance" text. So "mcp_alive" alone is misleading.
#
# TWO LEVELS (validated live 2026-06):
#  - list_roblox_studios: instant, side-effect-free. studios == [] means NO Studio
#    is connected to the MCP (app closed, OR its "Studio as MCP Server" option is
#    disabled - the two are indistinguishable at this layer). A non-empty list
#    means a Studio app IS connected, BUT note its entry stays present (active:true)
#    even when no place is open - only its "name" goes null. So presence != usable.
#  - get_studio_state: tells whether a PLACE is actually loaded. With a place open
#    it returns "Available DataModels: ..."; with the Studio on the home screen (or
#    the active place closed) it returns "...doesn't have a place opened / previously
#    active Studio has disconnected". That is the authoritative "place loaded" signal
#    (same phrase the call path already recognises in core/main.js).
STUDIO_PROBE_TOOL = "list_roblox_studios"
STUDIO_STATE_TOOL = "get_studio_state"
# Substrings get_studio_state emits when a Studio is connected but no place is open.
NO_PLACE_MARKERS = ("doesn't have a place", "no place opened", "place opened",
                    "has disconnected", "no active studio")


def _probe_tool_text(tool):
    """Call a side-effect-free probe tool with no args; return its text, or None if
    the tool is unavailable / the server is busy / it errored (best-effort)."""
    with mgr.index_lock:
        entry = mgr.index.get(tool)
    if entry is None:
        return None
    holder, real_name = entry
    # Never queue behind a long-running tool call (the probe is best-effort).
    if not holder.call_lock.acquire(blocking=False):
        return None
    try:
        if not holder.is_alive():
            return None
        msg = holder._request("tools/call", {"name": real_name, "arguments": {}}, timeout=8)
        if not msg or msg.get("error"):
            return None
        content = msg.get("result", {}).get("content", [])
        return "\n".join(it.get("text", "") for it in content if it.get("type") == "text")
    except Exception:
        return None
    finally:
        holder.call_lock.release()


def probe_studio():
    """Two-level Studio connectivity. Returns {"app": x, "place": y} where each is
    True / False / None (None = unknown: probe tool missing or server busy).
      app   - a Roblox Studio instance is connected to the MCP server. False = Studio
              closed OR its MCP-server option disabled (indistinguishable here).
      place - a place/datamodel is actually loaded and usable. False = Studio open on
              the home screen, or the active place was closed. Only meaningful when
              app is True (when app is False/None, place mirrors it)."""
    if _roblox_studio_app_running() is False:
        return {"app": False, "place": False}
    roblox = mgr.clients.get(PRIMARY_SERVER_ID)
    if roblox is not None and roblox.is_alive() and not roblox.tools_cache:
        return {"app": False, "place": False}
    text = _probe_tool_text(STUDIO_PROBE_TOOL)
    if text is None:
        return {"app": None, "place": None}
    try:
        studios = json.loads(text).get("studios") or []
    except Exception:
        return {"app": None, "place": None}
    if not studios:
        return {"app": False, "place": False}
    # A Studio app is connected - now check whether a place is actually open.
    state = _probe_tool_text(STUDIO_STATE_TOOL)
    if state is None:
        return {"app": True, "place": None}
    low = state.lower()
    place = not any(m in low for m in NO_PLACE_MARKERS)
    return {"app": True, "place": place}


def safe_call(name, arguments, timeout):
    """Never raises. Always returns a dict the extension can feed back to DeepSeek."""
    try:
        result = mgr.call(name, arguments, timeout)
        return {"ok": True, "text": result["text"], "images": result["images"]}
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "kind": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e), "kind": type(e).__name__}


async def run_tool_task(ws, name, args, timeout, rid):
    """Execute one tool off the socket read loop and send its result back.

    Kept as a standalone task (not awaited inline in handler) so a long tool
    never starves the connection's ability to answer app-level pings - see the
    call_tool branch in handler() for the full rationale."""
    # ── ALIAS GUARD: modelos web a veces mandan action como command ──
    # Ej: {"command": "context"} → {"command": "hermes_memory", "params": {"action": "context"}}
    ALIASES = {"context": ("hermes_memory", {"action": "context"})}
    if isinstance(name, str) and name in ALIASES:
        real_name, defaults = ALIASES[name]
        merged = dict(defaults)
        if isinstance(args, dict):
            merged.update(args)
        name, args = real_name, merged
        log(f"[alias] '{list(ALIASES.keys())[0]}' → {real_name}({merged})", color="yellow", terminal=False)
    t0 = time.monotonic()
    res = await asyncio.to_thread(safe_call, name, args, timeout)
    elapsed = time.monotonic() - t0
    tag = "gr" if res.get("ok") else "rd"
    summary = (res.get("text") or res.get("error") or "")[:80].replace("\n", " ")
    slow = "  [SLOW]" if elapsed > 5 else ""
    # Routine per-call traces are technical noise for a non-dev user watching
    # the console; they still land in bridge_debug.log. A failed/slow call
    # DOES surface on the terminal - that's the signal a user should notice.
    log(f"<- {name} ({elapsed:.1f}s){slow}: {summary}", tag, terminal=not res.get("ok") or elapsed > 5)
    try:
        await ws.send(json.dumps({"type": "tool_result", "id": rid, **res}))
    except websockets.ConnectionClosed:
        pass


def _get_playbooks_map():
    try:
        from agent_brain import brain
        return brain.data.get("playbooks", {})
    except Exception:
        return {}


async def broadcast_status():
    """Push a fresh status snapshot to every currently-connected extension tab.

    Needed because the socket now starts listening (see _boot_and_diagnose in
    main()) before every MCP server has necessarily finished launching in the
    background - an extension that connects in that window gets an early,
    incomplete "connected" snapshot (e.g. an addon server not started yet).
    The extension's own periodic poll only reads a passively cached copy of
    the LAST message it received (background.js never re-probes on its own),
    so without a follow-up push that stale snapshot can persist forever (seen
    live 2026-07-11: Blender not yet alive at connect-time froze the "Start
    Roblox agent" button in its fully-disabled, non-degraded state even long
    after Blender was actually up). background.js already handles a second
    "connected" message arriving at any time (updates its cache and re-renders
    the bar), so re-sending this exact shape once startup truly settles is
    enough to self-correct with zero extension-side changes needed.
    """
    if not clients:
        return
    try:
        _st = await asyncio.to_thread(probe_studio)
        _proc = await asyncio.to_thread(_roblox_studio_app_running)
        _pbs = await asyncio.to_thread(_get_playbooks_map)
        payload = json.dumps({
            "type": "connected",
            "mcp_alive": mgr.any_alive(),
            "studio": _st["place"], "studio_app": _st["app"],
            # Whether a Roblox Studio WINDOW process exists at all - lets the
            # extension word the corrective step correctly ("open the MCP
            # panel in your already-open Studio" vs "launch Studio").
            "studio_proc": _proc,
            "servers": mgr.health(),
            "tools": mgr.list_tools(),
            "security": get_security_config(),
            "playbooks": _pbs,
            "port": PORT,
        })
    except Exception:
        return
    for ws in list(clients):
        try:
            await ws.send(payload)
        except Exception:
            pass


async def handler(ws):
    peer = getattr(ws, "remote_address", ("?",))[0]
    clients.add(ws)
    log(f"extension connected  ({peer})  [{len(clients)} client(s)]", "gr")
    try:
        _st = await asyncio.to_thread(probe_studio)
        _pbs = await asyncio.to_thread(_get_playbooks_map)
        await ws.send(json.dumps({
            "type": "connected",
            "mcp_alive": mgr.any_alive(),
            "studio": _st["place"], "studio_app": _st["app"],
            "studio_proc": await asyncio.to_thread(_roblox_studio_app_running),
            "servers": mgr.health(),
            "tools": mgr.list_tools(),
            "security": get_security_config(),
            "playbooks": _pbs,
            "port": PORT,
        }))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            rid = msg.get("id")

            if mtype == "ping":
                await ws.send(json.dumps({"type": "pong", "id": rid}))

            elif mtype == "studio_status":
                studio = await asyncio.to_thread(probe_studio)
                await ws.send(json.dumps({
                    "type": "studio_status", "id": rid,
                    "studio": studio["place"], "studio_app": studio["app"],
                    "studio_proc": await asyncio.to_thread(_roblox_studio_app_running),
                    "mcp_alive": mgr.any_alive(),
                }))

            elif mtype == "list_tools":
                try:
                    tools = await asyncio.to_thread(mgr.list_tools, True)
                except Exception as e:
                    tools = mgr.list_tools()
                    log(f"list_tools error: {e}", "yl")
                _st = await asyncio.to_thread(probe_studio)
                await ws.send(json.dumps({
                    "type": "tools", "id": rid,
                    "tools": tools, "mcp_alive": mgr.any_alive(),
                    "studio": _st["place"], "studio_app": _st["app"],
                    "studio_proc": await asyncio.to_thread(_roblox_studio_app_running),
                    "servers": mgr.health(),
                }))

            elif mtype == "call_tool":
                name = msg.get("name", "")
                args = msg.get("arguments") or {}
                timeout = float(msg.get("timeout", 120000)) / 1000.0
                log(f"-> tool  {name}({', '.join(args.keys())})", "cy", terminal=False)
                # Run the tool as a BACKGROUND task instead of awaiting it here.
                # Awaiting inline parks this read loop for the WHOLE tool call, so
                # a long tool (e.g. wait_job_finished > 25s) means the client's
                # app-level pings are never read/answered - its half-open-socket
                # watchdog then force-closes the connection and the in-flight call
                # is dropped as "bridge unreachable" (reported live). As a task,
                # the loop stays free to answer pings/status while the tool runs.
                # The extension only ever has ONE call_tool in flight (its agent
                # loop awaits each result before sending the next), so this never
                # overlaps tool executions.
                asyncio.create_task(run_tool_task(ws, name, args, timeout, rid))

            elif mtype in ("add_server", "remove_server"):
                sid = msg.get("server_id")
                log(f"[SUPERPODERES] >>> Solicitud recibida: {mtype.upper()} para '{sid}'", "yl")
                if sid in ("blender", "filesystem", "fetch", "sqlite", "memory", "rea"):
                    if mtype == "add_server":
                        disabled_presets.discard(sid)
                        log(f"[SUPERPODERES] ✅ '{sid}' activado con éxito (motor nativo Hermes).", "gr")
                    else:
                        disabled_presets.add(sid)
                        log(f"[SUPERPODERES] ⚪ '{sid}' desactivado.", "dim")
                    await ws.send(json.dumps({
                        "type": "server_changed", "id": rid,
                        "ok": True, "error": None, "restarting": False,
                    }))
                    await broadcast_status()
                    continue
                if mtype == "add_server":
                    cmd_raw = msg.get("command") or ""
                    # Normalizacion multiplataforma
                    if sys.platform != "win32":
                        if cmd_raw.endswith(".cmd"):
                            cmd_raw = cmd_raw[:-4]
                        elif cmd_raw.endswith(".exe"):
                            cmd_raw = cmd_raw[:-4]
                    elif sys.platform == "win32":
                        if cmd_raw in ("npx", "npm"):
                            cmd_raw = cmd_raw + ".cmd" or ""
                    # Normalización multiplataforma
                    if sys.platform != "win32":
                        if cmd_raw.endswith(".cmd"):
                            cmd_raw = cmd_raw[:-4]
                        elif cmd_raw.endswith(".exe"):
                            cmd_raw = cmd_raw[:-4]
                    elif sys.platform == "win32":
                        if cmd_raw in ("npx", "npm"):
                            cmd_raw = cmd_raw + ".cmd" or ""
                    # Normalización multiplataforma
                    if sys.platform != "win32":
                        if cmd_raw.endswith(".cmd"):
                            cmd_raw = cmd_raw[:-4]
                        elif cmd_raw.endswith(".exe"):
                            cmd_raw = cmd_raw[:-4]
                    elif sys.platform == "win32":
                        if cmd_raw in ("npx", "npm"):
                            cmd_raw = cmd_raw + ".cmd"
                    args = msg.get("args") or []
                    env = msg.get("env")
                    log(f"[SUPERPODERES] 1/3 Guardando '{sid}' en config.json...", "dim")
                    ok, err = await asyncio.to_thread(config_add_server, sid, cmd_raw, args, env)
                    if ok:
                        log(f"[SUPERPODERES] 2/3 Lanzando proceso MCP para '{sid}' ({cmd_raw})...", "cy")
                        try:
                            client = await asyncio.to_thread(mgr.add_client, sid, cmd_raw, args, env)
                            log(f"[SUPERPODERES] 3/3 ✅ '{sid}' activado con éxito ({len(client.tools_cache)} herramientas cargadas).", "gr")
                        except Exception as e:
                            ok = False
                            err = str(e)
                            log(f"[SUPERPODERES] ❌ Error al arrancar '{sid}': {e}", "rd")
                            await asyncio.to_thread(config_remove_server, sid)
                            await asyncio.to_thread(mgr.remove_client, sid)
                else:
                    log(f"[SUPERPODERES] 1/2 Quitando '{sid}' de config.json...", "dim")
                    ok, err = await asyncio.to_thread(config_remove_server, sid)
                    log(f"[SUPERPODERES] 2/2 Deteniendo proceso MCP de '{sid}' en memoria...", "cy")
                    await asyncio.to_thread(mgr.remove_client, sid)
                    log(f"[SUPERPODERES] ⚪ '{sid}' desactivado limpiamente.", "dim")
                
                await ws.send(json.dumps({
                    "type": "server_changed", "id": rid,
                    "ok": ok, "error": err, "restarting": False,
                }))
                if ok:
                    log(f"[SUPERPODERES] Transmitiendo catálogo actualizado al navegador sin desconectar...", "cy")
                    await broadcast_status()

            elif mtype == "restart_mcp":
                sid = msg.get("server")
                try:
                    await asyncio.to_thread(mgr.restart, sid)
                    ok, err = True, None
                except Exception as e:
                    ok, err = False, str(e)
                await ws.send(json.dumps({
                    "type": "mcp_status", "id": rid,
                    "alive": mgr.any_alive(), "ok": ok, "error": err,
                    "servers": mgr.health(), "tools": mgr.list_tools(),
                }))

            elif mtype == "get_security_config":
                sec = await asyncio.to_thread(get_security_config)
                await ws.send(json.dumps({"type": "security_config", "id": rid, "ok": True, "config": sec}))

            elif mtype == "set_security_config":
                patch = msg.get("config") or {}
                sec = await asyncio.to_thread(update_security_config, patch)
                log(f"[SEGURIDAD] Configuración de seguridad actualizada: {sec}", "cy")
            elif mtype in ("get_playbooks", "list_playbooks"):
                pbs = await asyncio.to_thread(_get_playbooks_map)
                await ws.send(json.dumps({"type": "playbooks_list", "id": rid, "playbooks": pbs}))

            elif mtype == "save_playbook":
                try:
                    from agent_brain import brain
                    pb_name = msg.get("name") or msg.get("id") or "unnamed_playbook"
                    pb_desc = msg.get("description", "")
                    pb_steps = msg.get("steps", [])
                    pb_plat = msg.get("platform", "general")
                    saved = brain.save_playbook(name=pb_name, description=pb_desc, steps=pb_steps, platform=pb_plat)
                    await ws.send(json.dumps({"type": "playbook_saved", "id": rid, "ok": True, "playbook": saved}))
                    await broadcast_status()
                except Exception as e:
                    await ws.send(json.dumps({"type": "playbook_saved", "id": rid, "ok": False, "error": str(e)}))

            elif mtype == "delete_playbook":
                try:
                    from agent_brain import brain
                    pb_name = msg.get("name") or msg.get("id")
                    if pb_name and pb_name in brain.data.get("playbooks", {}):
                        del brain.data["playbooks"][pb_name]
                        brain._save()
                    await ws.send(json.dumps({"type": "playbook_deleted", "id": rid, "ok": True, "name": pb_name}))
                    await broadcast_status()
                except Exception as e:
                    await ws.send(json.dumps({"type": "playbook_deleted", "id": rid, "ok": False, "error": str(e)}))

            elif mtype == "toggle_mcp":
                sid = msg.get("server") or msg.get("server_id")
                enabled = bool(msg.get("enabled", True))
                if sid in ("blender", "filesystem", "fetch", "sqlite", "memory", "rea"):
                    if enabled:
                        disabled_presets.discard(sid)
                    else:
                        disabled_presets.add(sid)
                log(f"[SUPERPODERES] Superpoder '{sid}' marcado como {'ACTIVO' if enabled else 'INACTIVO'}.", "cy")
                await ws.send(json.dumps({
                    "type": "toggle_mcp_ack",
                    "id": rid,
                    "server": sid,
                    "enabled": enabled,
                    "ok": True,
                }))
                await broadcast_status()

            else:
                await ws.send(json.dumps({
                    "type": "error", "id": rid,
                    "error": f"unknown message type: {mtype}",
                }))
    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        log(f"handler error: {e}", "rd")
    finally:
        clients.discard(ws)
        log(f"extension disconnected  [{len(clients)} client(s)]", "yl")


async def server_watch():
    """Poll every MCP server and restart any that died unexpectedly (e.g. the
    StudioMCP proxy crashing on its own - see stop()'s taskkill /T fix and the
    stderr logging above for why this used to happen silently). Without this,
    a dead server only got noticed on the NEXT real tool call, which is what
    made "Studio looks connected but nothing responds" possible."""
    # Crash-LOOP detection thresholds: LOOP_N deaths within LOOP_WINDOW seconds
    # means something is killing (or instantly crashing) the server every time
    # we bring it back - the silent restart cycle the auto-restart otherwise
    # hides completely. We still keep restarting (the cause may be transient,
    # e.g. the user is about to start Blender), but the terminal now NAMES the
    # problem: exit code, the child's last stderr lines, and - for a port-bound
    # server - who is squatting the port. Banner re-prints at most every
    # LOOP_WARN_COOLDOWN so the terminal stays readable.
    LOOP_N = 3
    LOOP_WINDOW = 60
    LOOP_WARN_COOLDOWN = 120
    while True:
        await asyncio.sleep(5)
        for sid, client in list(mgr.clients.items()):
            try:
                if not client.is_alive():
                    now = time.time()
                    # restart_times holds RESTART ATTEMPTS (appended just before
                    # each start below), never per-poll sightings - appending on
                    # every 5s poll would keep the window full forever and the
                    # "slow down" branch would then block restarts permanently.
                    client.restart_times = [t for t in client.restart_times if now - t < LOOP_WINDOW]
                    looping = len(client.restart_times) >= LOOP_N
                    if looping and now - client.loop_warned_at > LOOP_WARN_COOLDOWN:
                        client.loop_warned_at = now
                        log(f"[{sid}] CRASH LOOP: died {len(client.restart_times)} times in the last "
                            f"{LOOP_WINDOW}s (last exit code: {client.last_exit}). Something is killing it "
                            f"or it cannot start.", "rd")
                        if client.start_error:
                            log(f"[{sid}] {client.start_error}", "rd")
                        elif client.stderr_tail:
                            log(f"[{sid}] last error output (usually the real reason):", "rd")
                            for ln in client.stderr_tail:
                                log(f"[{sid}]   {ln}", "yl")
                        else:
                            log(f"[{sid}] the server printed no error output before dying.", "yl")
                        # Port forensics: name the process squatting a port this
                        # server needs. For the primary Roblox proxy that is
                        # Studio's MCP port; a squatter there (seen live: a
                        # 'ropilot' app) makes the proxy die/misbehave forever.
                        if sid == "roblox":
                            owner = _port_owner(STUDIO_MCP_PORT)
                            if owner:
                                pid, name, path = owner
                                if "roblox" not in (name or "").lower() and "studio" not in (path or "").lower():
                                    log(f"[{sid}] port {STUDIO_MCP_PORT} is held by '{name}' (pid {pid}, {path}) - "
                                        f"close that program, it is squatting Studio's MCP port.", "rd")
                        log(f"[{sid}] common causes: its app is not running (e.g. Blender + addon), a port "
                            f"conflict, an antivirus killing it, or a bad command in config.json. "
                            f"Auto-restart continues in the background.", "yl")
                    if looping and client.restart_times and now - client.restart_times[-1] < 15:
                        # Clearly hopeless right now: drop to a ~15s cadence so a
                        # broken command isn't hammer-spawned every 5 seconds,
                        # while still retrying forever (the cause may clear, e.g.
                        # the user finally opens Blender).
                        continue
                    client.restart_times.append(now)
                    log(f"[{sid}] found dead - auto-restarting...", "yl")
                    await asyncio.to_thread(client.start)
                    mgr.rebuild_index()
                    await broadcast_status()  # tell any connected extension right away
            except Exception as e:
                log(f"[{sid}] auto-restart failed: {e}", "rd")


def _current_studio_exe():
    """The StudioMCP.exe our launcher would currently pick (newest version
    folder paired with a real RobloxStudioBeta.exe), or None. Reused here only
    to detect a Studio update happening mid-session: Roblox's own bug report
    ("Studio MCP turning off after update") says the toggle resets to OFF
    whenever Studio auto-updates - restarting our proxy can't fix that (Studio
    itself refuses the connection while its toggle is off), so the terminal
    should say "re-enable the toggle" instead of "wait for auto-recovery"
    when a version bump coincides with the disconnect."""
    if _studio_scan is None:
        return None
    try:
        return _studio_scan.find_studio_mcp()
    except Exception:
        return None


async def studio_watch(initial_app, initial_place=None):
    """Poll Studio attachment and log transitions, so the terminal confirms in
    GREEN the moment Studio attaches (e.g. after the user toggles its MCP server)
    and warns again if it later drops. Best-effort; never raises.

    Also auto-recovers from a real disconnect: two bugs reported on the Roblox
    devforum leave StudioMCP.exe alive (our client stays "alive" - the process
    never dies, so server_watch's dead-process restart never fires) but stuck
    talking to nothing - (1) StudioMCP keeps a stale named-pipe handle keyed by
    Studio's old PID after Studio is closed and reopened, and never rediscovers
    the new one; (2) MCP silently disconnects every 5-15 minutes on some
    machines. The documented user workaround for both is "toggle Studio's MCP
    server off/on" / "reopen the MCP panel" - which just forces StudioMCP to
    redo its handshake. Restarting OUR proxy process is the equivalent from
    this side (taskkill + fresh launch_studio_mcp.py), so do it automatically
    once a drop looks real (sustained, not a momentary blip) instead of leaving
    the user to notice and toggle it themselves."""
    prev_app = initial_app
    prev_place = initial_place
    disconnected_since = None
    last_auto_restart = 0.0
    empty_since = None       # when the roblox catalogue was first seen empty
    last_reclaim = 0.0       # cooldown for the zombie-StudioMCP port reclaim
    place_transitions = []
    known_studio_exe = await asyncio.to_thread(_current_studio_exe)
    update_suspected = False
    # Only auto-restart a disconnect that follows a real connection (matches
    # the two known bugs above) - never spam-restart while Studio simply isn't
    # open yet at all (prev_app starting False/None is the common cold-start
    # case and restarting there would just be noise every cooldown).
    ever_connected = initial_app is True
    while True:
        await asyncio.sleep(4)
        # If StudioMCP launched while Studio was closed, its catalogue is EMPTY
        # and stays that way: start()'s 12s retry loop has long given up, and
        # nothing else ever re-asks for tools/list (probe_studio can't - the
        # probe tools themselves are part of the missing catalogue, which is
        # why it short-circuits to "not connected" on an empty cache). So a
        # Studio opened AFTER that window was never detected until the user
        # restarted the whole bridge (seen live 2026-07-11). Re-ask here on
        # every poll while the catalogue is empty; the moment Studio attaches,
        # tools appear, the index rebuilds, and the normal probe below flips
        # the state to connected on this same iteration.
        rc0 = mgr.clients.get("roblox")
        if rc0 is not None and rc0.is_alive() and not rc0.tools_cache:
            got = False
            try:
                got = bool(await asyncio.to_thread(rc0.refresh_tools, 3))
            except Exception:
                got = False
            if got:
                mgr.rebuild_index()
                log(f"Roblox Studio's tools appeared ({len(rc0.tools_cache)}) - Studio attached.", "gr")
                empty_since = None
            else:
                now0 = time.time()
                if empty_since is None:
                    empty_since = now0
                # First: a PROVEN port hijack (stderr showed StudioMCP talking to
                # a foreign host, e.g. ropilot). Hard evidence, so recover fast
                # and unconditionally - no need to wait out the sustained-empty
                # window the ambiguous zombie case below uses.
                if (rc0.saw_foreign_ws_host and now0 - last_reclaim > 180):
                    last_reclaim = now0
                    killed, sname = await asyncio.to_thread(_kill_port_squatter)
                    if killed:
                        try:
                            await asyncio.to_thread(mgr.restart, "roblox")
                        except Exception as e:
                            log(f"roblox proxy restart after squatter kill failed: {e}", "rd")
                        _print_squatter_hint(sname)
                        await broadcast_status()
                # Otherwise: catalogue stuck empty WITH a Studio window open often
                # means a zombie StudioMCP.exe (not ours) still owns port 13469 and
                # swallowed Studio's one-shot registration - a state no manual
                # restart combination can escape (see _reclaim_studio_port).
                # Sustained-empty threshold + cooldown so a Studio that is
                # merely slow to boot never triggers a spurious kill.
                elif (now0 - empty_since > 20 and now0 - last_reclaim > 180
                        and await asyncio.to_thread(_roblox_studio_app_running) is True):
                    last_reclaim = now0
                    if await asyncio.to_thread(_reclaim_studio_port, rc0):
                        try:
                            await asyncio.to_thread(mgr.restart, "roblox")
                        except Exception as e:
                            log(f"roblox proxy restart after zombie kill failed: {e}", "rd")
                        _print_reregister_hint()
                        await broadcast_status()
        else:
            empty_since = None
        try:
            st = await asyncio.to_thread(probe_studio)
        except Exception:
            continue
        app, place = st["app"], st["place"]
        if app is not None and app != prev_app:
            if app is True:
                # Roblox-only count, not mgr.list_tools() (sums every server,
                # e.g. + Blender) - this message is specifically about Roblox
                # attaching, so it must not borrow addon tool counts (same
                # class of bug as the startup banner, see roblox_total above).
                rc = mgr.clients.get("roblox")
                roblox_now = len(rc.tools_cache) if rc else 0
                log(f"Roblox Studio connected - {roblox_now} tools ready.", "gr")
                ever_connected = True
                disconnected_since = None
                update_suspected = False
            else:
                cur_exe = await asyncio.to_thread(_current_studio_exe)
                if cur_exe and known_studio_exe and cur_exe != known_studio_exe:
                    # A newer Studio version folder appeared since we last saw
                    # one - restarting the proxy will NOT fix this (Studio
                    # itself refuses the MCP connection while its own toggle
                    # is off), so tell the user the actual fix instead of
                    # letting the generic auto-recovery below spin uselessly.
                    ver = os.path.basename(os.path.dirname(cur_exe))
                    log(f"Roblox Studio appears to have UPDATED (new version: {ver}). "
                        "Studio often turns its MCP toggle back OFF after an update - open "
                        "Roblox Studio > Assistant Settings > MCP Servers and re-enable "
                        "'Enable Studio as MCP server'.", "yl")
                    update_suspected = True
                else:
                    log("Roblox Studio disconnected - re-enable its MCP server (toggle off/on).", "yl")
                    update_suspected = False
                known_studio_exe = cur_exe or known_studio_exe
                if ever_connected and disconnected_since is None:
                    disconnected_since = time.time()
            prev_app = app
            # studio_watch only used to LOG transitions - an extension sitting
            # on the pre-start standby screen (no tool calls happening, so
            # nothing else round-trips to the bridge) never saw Studio connect
            # or disconnect mid-session until it happened to poll for an
            # unrelated reason. Push it immediately instead of leaving that
            # extension staring at a stale snapshot indefinitely.
            await broadcast_status()
        # Set once per iteration: BOTH the app-drop branch and the place-churn
        # block below use it. It used to be assigned only inside the app-drop
        # branch, so any iteration that skipped that branch crashed the whole
        # watcher with UnboundLocalError on the churn line (seen live: the task
        # died right after a successful reconnect, silently ending ALL Studio
        # monitoring and status broadcasts until the bridge was restarted).
        now = time.time()
        if app is False and ever_connected and disconnected_since is not None and not update_suspected:
            # ~20s sustained (5 polls) before treating it as a real drop, not a
            # momentary blip; 90s cooldown between recovery attempts so a
            # Studio that is genuinely closed for a while doesn't get hammered.
            # Skipped entirely when a version bump was the likely cause (see
            # above) - restarting our proxy cannot flip Studio's own toggle
            # back on, so retrying would just be noise every 90s.
            if now - disconnected_since > 20 and now - last_auto_restart > 90:
                last_auto_restart = now
                # Which recovery applies depends on whether a Studio WINDOW is
                # actually running (validated live 2026-07-11, both directions):
                #  - Studio RUNNING but not attached: Studio's MCP plugin only
                #    registers ONCE, at Studio boot or on a toggle flip. It
                #    never retries by itself, and restarting OUR proxy cannot
                #    reach into Studio to re-register it - worse, a restart
                #    that lands while Studio is booting kills the listener at
                #    the exact moment the plugin makes its single attempt,
                #    which is precisely how this state got created. So: do NOT
                #    touch the proxy; tell the user the one action that works.
                #  - No Studio running: a restart is safe (nothing to collide
                #    with) and clears genuinely stuck/stale proxy state.
                if await asyncio.to_thread(_roblox_studio_app_running) is True:
                    log("Roblox Studio is RUNNING but its MCP plugin has not registered with the "
                        "bridge yet.", "yl")
                    log("If Studio is still STARTING UP, give it a minute (its plugin registers "
                        "late in boot).", "yl")
                    log("If Studio is fully loaded and this stays yellow: in Roblox Studio, simply "
                        "OPEN Assistant Settings > MCP Servers - opening that panel makes the "
                        "plugin re-register (validated twice live). If that's not enough, toggle "
                        "'Enable Studio as MCP server' OFF then ON there.", "yl")
                else:
                    log("Roblox Studio proxy looks stuck (known StudioMCP disconnect bug) - "
                        "restarting it to recover.", "yl")
                    try:
                        await asyncio.to_thread(mgr.restart, "roblox")
                        await broadcast_status()
                    except Exception as e:
                        log(f"auto-restart of roblox proxy failed: {e}", "rd")
        # PLACE-level churn: `app` can stay stuck reporting True the whole time
        # (seen live 2026-07-11 - Studio fully closed, list_roblox_studios kept
        # answering with a leftover studio entry for 4+ minutes, so the app-drop
        # trigger above never fires) while `place` flip-flops "loaded"/"closed"
        # every ~10-20s forever. That is not a user opening/closing places that
        # fast - it is the same class of stuck-proxy bug, just visible at the
        # place layer instead of the app layer. A fresh StudioMCP.exe process
        # cannot carry over stale cached state, so the same restart applies.
        place_transitions[:] = [t for t in place_transitions if now - t < 90]
        if len(place_transitions) >= 4 and now - last_auto_restart > 90:
            # Same running-Studio guard as the app-drop recovery above: with a
            # real Studio window up, a proxy restart can only collide with the
            # plugin's one-shot registration; the churn is Studio-side state.
            if await asyncio.to_thread(_roblox_studio_app_running) is not True:
                last_auto_restart = now
                log(f"Roblox Studio's place status flipped {len(place_transitions)} times in the last "
                    "90s (known StudioMCP stuck-proxy bug) - restarting the proxy to recover.", "yl")
                try:
                    await asyncio.to_thread(mgr.restart, "roblox")
                    await broadcast_status()
                except Exception as e:
                    log(f"auto-restart of roblox proxy failed: {e}", "rd")
                place_transitions.clear()
        if place is not None and place != prev_place:
            # Debounce: StudioMCP's binding to Studio blips every few seconds
            # on some machines (self-healing in ~1-4s - seen live as "Bound
            # studio ... disconnected" stderr). A probe landing in that window
            # can misread EITHER direction - most confusingly, reporting
            # "place loaded" from a stale cached response while the place is
            # actually still closed (seen live). Recheck once before trusting
            # a transition, in either direction.
            await asyncio.sleep(1.2)
            try:
                confirm = (await asyncio.to_thread(probe_studio))["place"]
            except Exception:
                confirm = None
            if confirm is None or confirm != place:
                continue  # didn't hold up on recheck - treat as noise, not a real change
            if place is True:
                log("Place loaded in Studio.", "gr")
            else:
                log("Place closed (Studio app still connected).", "yl")
            prev_place = place
            place_transitions.append(time.time())
            await broadcast_status()


async def _supervised(name, coro_factory):
    """Run a watcher coroutine forever, restarting it if it ever raises.

    Both watchers are designed to never raise, but one line proved that wrong
    in practice (an UnboundLocalError killed studio_watch SILENTLY - asyncio
    only prints 'Task exception was never retrieved' at shutdown, so all
    Studio monitoring and status broadcasts just stopped until the user
    restarted the bridge). A crash in a watcher must never be silent or
    permanent: log it loudly, wait a beat, start a fresh instance.
    """
    while True:
        try:
            await coro_factory()
            return  # normal completion (doesn't happen today, but respect it)
        except Exception as e:
            log(f"{name} crashed: {type(e).__name__}: {e} - restarting it in 5s "
                f"(please report this).", "rd")
            await asyncio.sleep(5)


async def main():
    print(f"\n{C['cy']}  GHS Bridge v{BRIDGE_VERSION}{C['reset']}  {C['dim']}- Agente Autónomo - ws://{HOST}:{PORT}{C['reset']}\n")
    log(f"===== BRIDGE START  v{BRIDGE_VERSION}  pid={os.getpid()}  log={LOG_PATH} =====", "cy")
    cfg_servers = _read_config().get("mcpServers", {})
    if "roblox" in cfg_servers:
        await asyncio.to_thread(_kill_orphan_studio_mcp)
        killed_squatter = await asyncio.to_thread(check_studio_port)
    else:
        killed_squatter = False
    mgr.load_config()

    # Shared "we already told the user the corrective step" flag. Two producers
    # can print the 'toggle Studio's MCP server' action banner: the early
    # _early_studio_guidance task (fast, doesn't wait for start_all's ~48s grace
    # loop) and the post-start_all diagnostic block in _boot_and_diagnose. This
    # flag lets whichever fires first suppress the other, so the user never sees
    # the same instruction twice. Mutable dict so both nested coroutines share it.
    _guidance_shown = {"v": False}

    async def _early_studio_guidance():
        """Print the corrective action banner WITHOUT waiting for start_all()'s
        full ~48s grace loop.

        RobloxClient.start() retries tools/list for up to ~48s to catch a Studio
        that attaches a little late - correct for a Studio that IS coming, but it
        also means a user whose Studio is simply closed or whose MCP toggle is off
        waits ~48s before the terminal tells them what to do (the extension, which
        reads status over the socket, already says it immediately). So after a
        short grace we check independently and, if still not connected, show the
        step now. If Studio then attaches, studio_watch prints the green
        'connected' line - so an early banner is at worst redundant, never wrong
        (the user confirmed a premature toggle hint during boot is harmless).

        Deliberately does NOT try to distinguish 'Studio closed' from 'MCP toggle
        off': probe_studio can't tell them apart (both read app=False, see its
        docstring), and the banner wording already covers both, so there is no
        finer detection to preserve here. A proven port-squatter case is left to
        _boot_and_diagnose / studio_watch, which print their own, more specific
        hint."""
        if PRIMARY_SERVER_ID not in mgr.clients:
            return
        if not await asyncio.to_thread(_roblox_studio_app_running):
            return
        await asyncio.sleep(12)  # give a fast, normal attach the chance to win
        if _guidance_shown["v"]:
            return
        rc = mgr.clients.get(PRIMARY_SERVER_ID)
        if rc is None or getattr(rc, "saw_foreign_ws_host", False):
            return
        if rc.tools_cache:
            st = await asyncio.to_thread(probe_studio)
            if st["app"] is not False:
                return
        _guidance_shown["v"] = True
        action_banner([
            "Open your place in Roblox Studio.",
            "Go to: Assistant Settings > MCP Servers",
            "       > 'Enable Studio as MCP server'",
            "It can take up to ~10s; this window will turn green.",
        ])

    async def _boot_and_diagnose():
        """Launch every configured MCP server and print the boot diagnostic
        banner. Runs as a background task AFTER the socket below is already
        listening, so a slow or absent Roblox Studio never delays the
        extension's ability to connect and use OTHER MCP servers (e.g.
        Blender) right away - only the terminal banner and Roblox's own
        auto-recovery loop wait on this. (mgr.start_all() itself also
        launches every server in parallel now, for the same reason.)"""
        try:
            await asyncio.to_thread(mgr.start_all)
        except Exception as e:
            log(f"server startup error: {e}", "rd")
            log("The bridge will keep running; it retries on the first tool call.", "yl")
        total = len(mgr.list_tools())
        roblox_client = mgr.clients.get("roblox")
        roblox_total = len(roblox_client.tools_cache) if roblox_client else 0

        # Port-hijack check (ropilot etc.), done at boot
        if (roblox_client is not None and roblox_total == 0
                and roblox_client.saw_foreign_ws_host):
            killed, sname = await asyncio.to_thread(_kill_port_squatter)
            if killed:
                try:
                    await asyncio.to_thread(mgr.restart, "roblox")
                    roblox_total = len(roblox_client.tools_cache)
                    total = len(mgr.list_tools())
                except Exception as e:
                    log(f"roblox proxy restart after squatter kill failed: {e}", "rd")
                _print_squatter_hint(sname)

        reclaimed_zombie = False
        if (roblox_client is not None and roblox_total == 0
                and await asyncio.to_thread(_roblox_studio_app_running) is True
                and await asyncio.to_thread(_reclaim_studio_port, roblox_client)):
            try:
                await asyncio.to_thread(mgr.restart, "roblox")
                roblox_total = len(roblox_client.tools_cache)
                total = len(mgr.list_tools())
            except Exception as e:
                log(f"roblox proxy restart after zombie kill failed: {e}", "rd")
            reclaimed_zombie = True
            _print_reregister_hint()

        _st = await asyncio.to_thread(probe_studio)
        is_studio_proc = await asyncio.to_thread(_roblox_studio_app_running)

        if _st["app"] is False and is_studio_proc:
            with _Spinner("    waiting for Roblox Studio to attach..."):
                for _ in range(8):
                    await asyncio.sleep(1)
                    _st = await asyncio.to_thread(probe_studio)
                    if _st["app"] is not False:
                        break

        if _st["app"] is True:
            await asyncio.sleep(1.5)
            confirm = await asyncio.to_thread(probe_studio)
            if confirm["app"] is not True:
                _st = confirm

        if roblox_client is not None and (roblox_total == 0 or _st["app"] is False):
            if not is_studio_proc:
                # Studio is closed: bridge runs 100% cleanly for Hermes and all other tools without nagging
                log(f"ready {total} tools available ({len(mgr.clients)} MCP servers) - Agente Autónomo Listo", "gr")
            elif killed_squatter or reclaimed_zombie or _guidance_shown["v"]:
                log("    still waiting for you to toggle Studio's MCP server "
                    "(see the action box above)...", "yl")
            else:
                _guidance_shown["v"] = True
                if roblox_total > 0:
                    log(f"    {roblox_total} Roblox tools loaded, but NO Roblox Studio is connected yet.", "yl")
                    log("    (This can be a slow attach that clears itself within ~10-15s -", "yl")
                    log("    watch for a green 'Roblox Studio connected' line right after.)", "yl")
                action_banner([
                    "Open your place in Roblox Studio.",
                    "Go to: Assistant Settings > MCP Servers",
                    "       > 'Enable Studio as MCP server'",
                    "It can take up to ~10s; this window will turn green.",
                ])
        elif _st["app"] is True:
            log(f"ready {total} tools available - Roblox Studio connected", "gr")
        else:
            log(f"ready {total} tools available ({len(mgr.clients)} MCP servers) - Agente Autónomo Listo", "gr")
        asyncio.create_task(_supervised(
            "studio_watch", lambda: studio_watch(_st["app"], _st["place"])))

    async def _early_status_pushes():
        """A few follow-up status broadcasts shortly after boot.

        mgr.start_all() still doesn't RETURN until every server's thread has
        joined - including Roblox's, which can take up to ~48s (StudioMCP's
        own internal "waiting for tools" retry loop, seen live). So a single
        broadcast placed after start_all() would be just as slow as the old
        blocking behavior for the exact case this is meant to fix: an addon
        server (e.g. Blender) that's ready in 1-13s while Roblox is still
        slowly timing out. Poll-and-broadcast a few times instead, cheaply,
        so any extension that connected during that window self-corrects
        quickly instead of staying stuck on its first, incomplete snapshot.
        """
        for interval in (2, 2, 4, 6, 6):  # cumulative: 2s, 4s, 8s, 14s, 20s after boot
            await asyncio.sleep(interval)
            await broadcast_status()

    # Free our own port from a leftover bridge (double-launch / X-closed window /
    # prior crash) BEFORE binding, so relaunching start.bat "just works" instead
    # of dying on WinError 10048. Only ever kills a proven bridge.py; anything
    # else falls through to the friendly bind-error below.
    if await asyncio.to_thread(_reclaim_bridge_port):
        await asyncio.sleep(0.6)  # let Windows release the socket before we bind

    try:
        server_ctx = await websockets.serve(
            handler, HOST, PORT, ping_interval=20, ping_timeout=20,
            max_size=16 * 1024 * 1024)
    except OSError as e:
        # errno 10048 (Win) / EADDRINUSE: something we could NOT auto-kill still
        # owns the port - another app, or a python whose cmdline we couldn't read.
        if getattr(e, "errno", None) in (98, 10048) or "10048" in str(e):
            owner = await asyncio.to_thread(_port_owner, PORT)
            who = f" by '{owner[1]}' (pid {owner[0]})" if owner else ""
            log(f"could not start: port {PORT} is already in use{who}.", "rd")
            log(f"    A previous bridge may still be running, or another app took "
                f"the port. Close it, then relaunch. To find it:", "yl")
            log(f"      netstat -ano | findstr {PORT}", "yl")
            log(f"      taskkill /F /PID <the pid from the last column>", "yl")
            log(f"    Or set a different port before start.bat:  set ZS_BRIDGE_PORT=17614", "yl")
            return
        raise

    async with server_ctx:
        log(f"listening on ws://{HOST}:{PORT}  - load the extension and open a supported AI chat", "cy")
        asyncio.create_task(_supervised("server_watch", server_watch))
        asyncio.create_task(_boot_and_diagnose())
        asyncio.create_task(_early_studio_guidance())
        asyncio.create_task(_early_status_pushes())
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("shutting down...", "yl")
        for c in mgr.clients.values():
            c.stop()
    finally:
        log("===== BRIDGE STOP =====", "cy")
