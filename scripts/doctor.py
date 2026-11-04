"""NetOps Fabric Doctor & Pre-flight Diagnostics Utility.

Usage:
    python scripts/doctor.py [--verbose]
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

# Add backend directory to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ANSI Color codes
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
RESET = "\033[0m"


def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> tuple[bool, float, str]:
    """Test TCP socket connection and measure handshake latency in ms."""
    start = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        latency = (time.perf_counter() - start) * 1000
        # Try non-blocking peek for SSH banner
        sock.settimeout(1.0)
        try:
            banner = sock.recv(64).decode("utf-8", errors="ignore").strip()
        except Exception:
            banner = "(no banner sent)"
        sock.close()
        return True, latency, banner
    except Exception as exc:
        sock.close()
        return False, 0.0, str(exc)


def main() -> int:
    print(f"\n{BOLD}{CYAN}╔════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║     CROC DREAM NetOps Platform — System & Fabric Doctor        ║{RESET}")
    print(f"{BOLD}{CYAN}╚════════════════════════════════════════════════════════════════╝{RESET}\n")

    overall_healthy = True

    # 1. Environment & Python Runtime
    print(f"{BOLD}1. Python Runtime & Dependencies:{RESET}")
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    in_venv = sys.prefix != sys.base_prefix
    print(f"   • Python Version: {GREEN}{py_ver}{RESET} ({'Virtualenv' if in_venv else 'System'})")

    required_pkgs = ["fastapi", "paramiko", "pydantic", "sqlalchemy", "jinja2", "yaml"]
    missing_pkgs = []
    for pkg in required_pkgs:
        try:
            __import__(pkg)
        except ImportError:
            missing_pkgs.append(pkg)

    if not missing_pkgs:
        print(f"   • Dependencies:   {GREEN}✔ All core packages available{RESET} ({', '.join(required_pkgs)})")
    else:
        print(f"   • Dependencies:   {RED}✖ Missing: {', '.join(missing_pkgs)}{RESET}")
        overall_healthy = False

    # 2. Database Connectivity
    print(f"\n{BOLD}2. Database Health & Storage:{RESET}")
    try:
        from netops.db import build_engine, build_session_factory
        from netops.settings import get_settings
        from sqlalchemy import text

        db_path = BACKEND_DIR / "netops_local.db"
        db_url = os.environ.get("NETOPS_DATABASE_URL") or get_settings().database_url or f"sqlite:///{db_path}"
        engine = build_engine(db_url)
        factory = build_session_factory(engine)

        with factory() as session:
            device_count = session.execute(text("SELECT count(*) FROM devices")).scalar() or 0
            job_count = session.execute(text("SELECT count(*) FROM jobs")).scalar() or 0
            user_count = session.execute(text("SELECT count(*) FROM users")).scalar() or 0
        print(f"   • Connection:     {GREEN}✔ DB reachable{RESET} ({engine.url.render_as_string(hide_password=True)})")
        print(f"   • Entities:       {device_count} device(s), {job_count} job(s), {user_count} user(s)")
    except Exception as exc:
        print(f"   • Connection:     {RED}✖ DB connection error: {exc}{RESET}")
        overall_healthy = False

    # 3. Intent Repository (Git SoT)
    print(f"\n{BOLD}3. Git Source-of-Truth (Intent Repository):{RESET}")
    intent_dir = REPO_ROOT / "intent"
    inv_devices = []
    try:
        from netops.intent.repository import IntentRepository
        inv_file = "inventory.yaml"
        repo = IntentRepository(intent_dir, inventory_file=inv_file)
        snapshot = repo.load()
        inv_devices = list(snapshot.inventory.devices)
        print(f"   • Path:           {GREEN}✔ Valid{RESET} ({intent_dir}/{inv_file})")
        print(f"   • Snapshot:       {len(inv_devices)} intended device(s) defined in YAML")
        for dev in inv_devices:
            print(f"     - {dev.hostname} [{dev.platform.value}] @ {dev.management_ip}:{dev.management_port}")
    except Exception as exc:
        print(f"   • Repository:     {RED}✖ Failed to load intent: {exc}{RESET}")
        overall_healthy = False

    # 4. Live Lab Connectivity Probe (from Git SoT Inventory)
    print(f"\n{BOLD}4. Multi-Vendor Lab Network Probe (from Git SoT):{RESET}")
    nodes = [
        (str(d.hostname), str(d.management_ip), int(d.management_port), str(d.platform.value))
        for d in inv_devices
    ]

    print(f"   {'Node':<18} | {'Port':<5} | {'Vendor Platform':<16} | {'Status':<10} | {'Latency':<9} | {'SSH Banner'}")
    print("   " + "-" * 88)

    lab_reachable_count = 0
    for hostname, host_ip, port, vendor in nodes:
        ok, latency, banner = check_tcp_port(host_ip, port, timeout=2.5)
        if ok:
            lab_reachable_count += 1
            status_str = f"{GREEN}OPEN{RESET}"
            lat_str = f"{latency:.1f}ms"
            banner_short = banner[:32] if banner else "(no banner)"
        else:
            status_str = f"{RED}REFUSED{RESET}"
            lat_str = "---"
            banner_short = banner[:32]

        print(f"   {hostname:<18} | {port:<5} | {vendor:<16} | {status_str:<19} | {lat_str:<9} | {banner_short}")

    print("   " + "-" * 88)
    print(f"   Lab Accessibility: {lab_reachable_count}/{len(nodes)} ports open")

    # 5. Diagnostic Advice & Filigree Verdict
    print(f"\n{BOLD}5. Diagnostic Findings & System Status:{RESET}")
    if nodes and lab_reachable_count == len(nodes):
        print(f"   {GREEN}✔ Multi-vendor lab connectivity verified ({lab_reachable_count}/{len(nodes)} ports open){RESET}")
    else:
        print(f"   {YELLOW}⚠ Notice: Lab hosts have limited connectivity ({lab_reachable_count}/{len(nodes)}). Check VPN or containerlab state.{RESET}")

    print(f"\n{BOLD}Doctor Verdict:{RESET} {'[SYSTEM READY FOR PRODUCTION / DEMO]' if overall_healthy else '[ATTENTION REQUIRED]'}\n")
    return 0 if overall_healthy else 1


if __name__ == "__main__":
    sys.exit(main())
