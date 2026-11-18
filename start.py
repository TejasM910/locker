"""
Locker Project One-Click Launcher
Starts all services (MySQL check, MQTT broker, FastAPI backend, Next.js frontend, Ngrok)
and automatically opens the website in the browser once ready.
"""

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib import request
import json

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
ENV_FILE = ROOT_DIR / ".env"
MOSQUITTO_PATH = Path(r"C:\Program Files\Mosquitto\mosquitto.exe")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, timeout: int = 30, desc: str = "") -> bool:
    """Wait until a specific port is listening or timeout occurs."""
    print(f"Waiting for {desc} (port {port})...", end="", flush=True)
    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_port_in_use(port):
            print(" Ready!")
            return True
        time.sleep(1)
        print(".", end="", flush=True)
    print(" Timed out!")
    return False


def start_detached_console(title: str, command: list[str], cwd: Path):
    """Launch a service in its own independent command prompt window on Windows."""
    # Using 'cmd.exe /k' keeps the window open so logs and errors are visible.
    cmd = ["cmd.exe", "/c", "start", title] + command
    subprocess.Popen(
        cmd,
        cwd=str(cwd),
        shell=True,
    )


def check_mysql():
    """Ensure MySQL is reachable on port 3306."""
    if not is_port_in_use(3306):
        print("[*] Port 3306 not active. Attempting to start MySQL80 Windows Service...")
        try:
            subprocess.run(
                ["powershell", "-Command", "Start-Service -Name MySQL80 -ErrorAction SilentlyContinue"],
                check=False
            )
        except Exception:
            pass
        wait_for_port(3306, timeout=15, desc="MySQL")
    else:
        print("[+] MySQL is running on port 3306.")


def start_mqtt():
    """Start Mosquitto broker if not already running."""
    if is_port_in_use(1883):
        print("[+] Mosquitto MQTT broker is already running on port 1883.")
        return

    if MOSQUITTO_PATH.exists():
        print("[*] Starting Mosquitto MQTT Broker...")
        start_detached_console("Locker - MQTT Broker", [str(MOSQUITTO_PATH), "-v"], ROOT_DIR)
        wait_for_port(1883, timeout=10, desc="MQTT Broker")
    else:
        print(f"[!] Warning: Mosquitto not found at {MOSQUITTO_PATH}. Skipping MQTT launch.")


def start_backend():
    """Start the FastAPI backend with uvicorn."""
    if is_port_in_use(8000):
        print("[+] API backend is already running on port 8000.")
        return

    print("[*] Starting FastAPI Backend on port 8000...")
    start_detached_console(
        "Locker - FastAPI Backend",
        [sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
        BACKEND_DIR
    )
    wait_for_port(8000, timeout=30, desc="API Backend")


def start_frontend():
    """Start the Next.js frontend with npm."""
    if is_port_in_use(3000):
        print("[+] Frontend is already running on port 3000.")
        return

    print("[*] Starting Next.js Frontend on port 3000...")
    start_detached_console(
        "Locker - Frontend",
        ["npm.cmd", "run", "dev"],
        FRONTEND_DIR
    )
    wait_for_port(3000, timeout=45, desc="Frontend")


def start_ngrok():
    """Start ngrok tunnel if ngrok is available."""
    if is_port_in_use(4040):
        print("[+] Ngrok is already running on port 4040.")
        return

    # Check if ngrok is installed
    try:
        res = subprocess.run(["where", "ngrok"], capture_output=True, text=True)
        if res.returncode == 0:
            print("[*] Starting Ngrok Tunnel for port 8000...")
            start_detached_console("Locker - Ngrok Tunnel", ["ngrok", "http", "8000"], ROOT_DIR)
            time.sleep(3)
        else:
            print("[i] Ngrok is not in PATH. Skipping ngrok tunnel.")
    except Exception:
        pass


def get_ngrok_url() -> str | None:
    """Fetch public URL from Ngrok local API if active."""
    try:
        req = request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2)
        data = json.loads(req.read().decode())
        tunnels = data.get("tunnels", [])
        for t in tunnels:
            if "8000" in t.get("config", {}).get("addr", ""):
                return t.get("public_url")
    except Exception:
        return None
    return None


def main():
    print("=" * 60)
    print("      Starting Locker System (All Services)       ")
    print("=" * 60)

    # 1. Check MySQL
    check_mysql()

    # 2. MQTT Broker
    start_mqtt()

    # 3. FastAPI Backend
    start_backend()

    # 4. Next.js Frontend
    start_frontend()

    # 5. Optional Ngrok Tunnel
    start_ngrok()

    ngrok_url = get_ngrok_url()

    print("\n" + "=" * 60)
    print("All services have been initiated!")
    print("  - Frontend (PWA):      http://localhost:3000")
    print("  - API Documentation:   http://localhost:8000/docs")
    print("  - Backend Health Check: http://localhost:8000/health")
    if ngrok_url:
        print(f"  - Public Ngrok URL:    {ngrok_url}")
        print(f"  - Razorpay Webhook:    {ngrok_url}/v1/webhooks/razorpay")
    print("=" * 60)

    # 6. Automatically open the browser
    frontend_url = "http://localhost:3000"
    print(f"\nOpening {frontend_url} in your default browser...")
    webbrowser.open(frontend_url)


if __name__ == "__main__":
    main()
