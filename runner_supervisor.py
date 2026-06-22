import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify
from waitress import serve


load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

SUPERVISOR_PORT = int(os.getenv("SUPERVISOR_PORT", "5090"))
RUNNER_REPOS_BASE = Path(os.getenv("RUNNER_REPOS_BASE", r"C:\repos"))


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def runner_configs() -> list[dict[str, Any]]:
    return [
        {
            "runnerId": "RECEITABX",
            "displayName": "ReceitaBX",
            "port": 5050,
            "baseUrl": os.getenv("RECEITABX_BASE_URL", "http://localhost:5050"),
            "repoDir": RUNNER_REPOS_BASE / "scriptreceitabx",
            "startScript": RUNNER_REPOS_BASE / "scriptreceitabx" / "run-receitabx-runner.ps1",
            "jobsDir": Path(r"C:\RunnerPAD\jobs"),
            "currentRunFile": Path(r"C:\RunnerPAD\current_run.txt"),
            "busyFile": Path(r"C:\RunnerPAD\jobs\runner_busy.lock"),
            "serviceName": "ScriptReceitaBXRunner",
        },
        {
            "runnerId": "ECAC",
            "displayName": "eCAC",
            "port": 5060,
            "baseUrl": os.getenv("ECAC_BASE_URL", "http://localhost:5060"),
            "repoDir": RUNNER_REPOS_BASE / "scriptecac",
            "startScript": RUNNER_REPOS_BASE / "scriptecac" / "run-ecac-runner.ps1",
            "jobsDir": Path(r"C:\RunnerECAC\jobs"),
            "currentRunFile": Path(r"C:\RunnerECAC\current_run"),
            "busyFile": Path(r"C:\RunnerECAC\jobs\runner_busy.lock"),
            "serviceName": "ScriptECACRunner",
        },
        {
            "runnerId": "ESOCIAL",
            "displayName": "eSocial",
            "port": 5080,
            "baseUrl": os.getenv("ESOCIAL_BASE_URL", "http://localhost:5080"),
            "repoDir": RUNNER_REPOS_BASE / "scriptesocial",
            "startScript": RUNNER_REPOS_BASE / "scriptesocial" / "run-esocial-runner.ps1",
            "jobsDir": Path(r"C:\RunnerESocial\jobs"),
            "currentRunFile": Path(r"C:\RunnerESocial\current_run"),
            "busyFile": Path(r"C:\RunnerESocial\jobs\runner_busy.lock"),
            "serviceName": "ScriptESocialRunner",
        },
    ]


def normalize_runner_id(runner_id: str) -> str:
    value = (runner_id or "").strip().upper()
    if value in {"RECEITA", "RECEITA_BX", "M1"}:
        return "RECEITABX"
    return value


def config_for(runner_id: str) -> dict[str, Any]:
    normalized = normalize_runner_id(runner_id)
    for config in runner_configs():
        if config["runnerId"] == normalized:
            return config
    raise ValueError(f"Runner desconhecido: {runner_id}")


def read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def runner_status(config: dict[str, Any]) -> dict[str, Any]:
    checked_at = now_iso()
    raw: dict[str, Any] = {}
    last_error = ""
    online = False

    try:
        response = requests.get(f"{config['baseUrl']}/status", timeout=(0.8, 1.5))
        response.raise_for_status()
        raw = response.json()
        online = True
    except Exception as exc:
        last_error = str(exc)

    current = raw.get("currentRun") if isinstance(raw.get("currentRun"), dict) else {}
    current_run_id = str(current.get("runId") or "").strip()
    busy = bool(current.get("busy")) or bool(current_run_id)
    queue_size = int(raw.get("queueSize") or 0) if online else 0
    state = "OFFLINE" if not online else ("ONLINE_BUSY" if busy else "ONLINE_IDLE")

    return {
        "runnerId": config["runnerId"],
        "displayName": config["displayName"],
        "port": config["port"],
        "baseUrl": config["baseUrl"],
        "state": state,
        "online": online,
        "busy": busy,
        "queueSize": queue_size,
        "currentRunId": current_run_id,
        "currentRunFile": str(config["currentRunFile"]),
        "jobsDir": str(config["jobsDir"]),
        "serviceName": config["serviceName"],
        "checkedAt": checked_at,
        "lastError": last_error,
        "rawStatus": raw,
    }


def safe_runner_status(config: dict[str, Any]) -> dict[str, Any]:
    future = status_executor.submit(runner_status, config)
    try:
        return future.result(timeout=2.5)
    except TimeoutError:
        return offline_status(config, "Timeout ao consultar runner")


def offline_status(config: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "runnerId": config["runnerId"],
        "displayName": config["displayName"],
        "port": config["port"],
        "baseUrl": config["baseUrl"],
        "state": "OFFLINE",
        "online": False,
        "busy": False,
        "queueSize": 0,
        "currentRunId": "",
        "currentRunFile": str(config["currentRunFile"]),
        "jobsDir": str(config["jobsDir"]),
        "serviceName": config["serviceName"],
        "checkedAt": now_iso(),
        "lastError": error,
        "rawStatus": {},
    }


def run_powershell(script: str) -> None:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        output = (completed.stdout or "") + (completed.stderr or "")
        raise RuntimeError(output.strip() or f"PowerShell falhou com codigo {completed.returncode}")


def stop_by_port(port: int) -> None:
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$pids = @()
try {{
    $pids = @(Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}} catch {{
    $pids = @()
}}

foreach ($processId in $pids) {{
    if ($processId) {{
        try {{
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }} catch {{}}
    }}
}}
exit 0
"""
    run_powershell(script)


def stop_runner(config: dict[str, Any]) -> None:
    repo_dir = str(config["repoDir"]).replace("'", "''")
    start_script = str(config["startScript"]).replace("'", "''")
    port = int(config["port"])
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$targets = @()

try {{
    Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {{ if ($_ -and $_ -gt 0) {{ $targets += [int]$_ }} }}
}} catch {{}}

$repoNeedle = '{repo_dir}'
$scriptNeedle = '{start_script}'
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {{
        $_.CommandLine -and (
            $_.CommandLine -like "*$repoNeedle*" -or
            $_.CommandLine -like "*$scriptNeedle*"
        )
    }} |
    ForEach-Object {{ $targets += [int]$_.ProcessId }}

$all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
$changed = $true
while ($changed) {{
    $changed = $false
    foreach ($process in $all) {{
        if ($process.ParentProcessId -in $targets -and $process.ProcessId -notin $targets) {{
            $targets += [int]$process.ProcessId
            $changed = $true
        }}
    }}
}}

foreach ($processId in ($targets | Select-Object -Unique | Sort-Object -Descending)) {{
    try {{
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }} catch {{}}
}}
exit 0
"""
    run_powershell(script)


def start_runner(config: dict[str, Any]) -> None:
    start_script = config["startScript"]
    repo_dir = config["repoDir"]
    if not start_script.exists():
        raise FileNotFoundError(f"Script nao encontrado: {start_script}")
    script = (
        "Start-Process powershell.exe -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{start_script}\"' "
        f"-WorkingDirectory '{repo_dir}'"
    )
    run_powershell(script)


def wait_online(config: dict[str, Any], attempts: int = 15) -> dict[str, Any]:
    status = runner_status(config)
    for _ in range(attempts):
        if status["online"]:
            return status
        time.sleep(1)
        status = runner_status(config)
    return status


def remove_if_exists(path: Path, removed: list[str]) -> None:
    try:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    except Exception as exc:
        raise RuntimeError(f"Falha ao remover {path}: {exc}") from exc


def remove_queue_files(config: dict[str, Any], removed: list[str], include_locks: bool = False) -> None:
    jobs_dir = config["jobsDir"]
    if not jobs_dir.exists():
        return

    protected = set()
    if not include_locks:
        protected.add(config["busyFile"].resolve())

    for item in jobs_dir.iterdir():
        if not item.is_file():
            continue
        try:
            resolved = item.resolve()
        except Exception:
            resolved = item
        if resolved in protected:
            continue
        remove_if_exists(item, removed)


def clear_local_state(config: dict[str, Any], removed: list[str], include_queue: bool = False) -> None:
    remove_if_exists(config["currentRunFile"], removed)
    remove_if_exists(config["busyFile"], removed)
    remove_if_exists(config["jobsDir"] / "pending_downloads.json", removed)
    if include_queue:
        remove_queue_files(config, removed, include_locks=True)


def clear_runner_queue(config: dict[str, Any]) -> tuple[int, str, bool]:
    removed_files: list[str] = []
    try:
        response = requests.post(f"{config['baseUrl']}/clear-queue", timeout=(1.5, 5))
        response.raise_for_status()
        data = response.json() if response.content else {}
        removed = int(data.get("removed") or 0)
        message = str(data.get("message") or "Fila limpa.")
        remove_queue_files(config, removed_files)
        if removed_files:
            message += " Arquivos locais removidos: " + ", ".join(removed_files)
        return removed + len(removed_files), message, True
    except Exception as exc:
        route_error = str(exc)
        try:
            stop_runner(config)
            time.sleep(2)
            remove_queue_files(config, removed_files)
            start_runner(config)
            final_status = wait_online(config)
            success = final_status["online"] and final_status["queueSize"] == 0 and not final_status["busy"]
            if success:
                return (
                    len(removed_files),
                    "Runner nao possui rota de limpar fila; processo reiniciado para limpar fila em memoria.",
                    True,
                )
            return (
                len(removed_files),
                "Runner reiniciado para limpar fila, mas ainda reportou ocupado ou com fila. Erro original: " + route_error,
                False,
            )
        except Exception as control_exc:
            try:
                remove_queue_files(config, removed_files)
            except Exception as remove_exc:
                return 0, f"Falha ao limpar fila. Rota: {route_error}. Controle: {control_exc}. Remocao: {remove_exc}", False
            return (
                len(removed_files),
                "Arquivos de fila removidos, mas nao foi possivel reiniciar o runner. "
                f"Rota: {route_error}. Controle: {control_exc}",
                bool(removed_files),
            )


def action_response(config: dict[str, Any], action: str, success: bool, message: str) -> dict[str, Any]:
    return {
        "runnerId": config["runnerId"],
        "action": action,
        "success": success,
        "message": message,
        "status": runner_status(config),
    }


app = Flask(__name__)
status_executor = ThreadPoolExecutor(max_workers=6)


@app.errorhandler(Exception)
def handle_error(exc: Exception):
    return jsonify({"success": False, "message": str(exc), "error": type(exc).__name__}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok", "port": SUPERVISOR_PORT, "reposBase": str(RUNNER_REPOS_BASE)})


@app.get("/runners/control")
def status_all():
    return jsonify([safe_runner_status(config) for config in runner_configs()])


@app.get("/runners/control/<runner_id>")
def status_one(runner_id: str):
    return jsonify(safe_runner_status(config_for(runner_id)))


@app.post("/runners/control/<runner_id>/start")
def start(runner_id: str):
    config = config_for(runner_id)
    status = runner_status(config)
    if status["online"]:
        return jsonify(action_response(config, "START", True, "Runner ja esta online."))
    start_runner(config)
    final_status = wait_online(config)
    return jsonify({
        "runnerId": config["runnerId"],
        "action": "START",
        "success": final_status["online"],
        "message": "Runner iniciado." if final_status["online"] else "Runner iniciado, mas health ainda nao respondeu.",
        "status": final_status,
    })


@app.post("/runners/control/<runner_id>/restart")
def restart(runner_id: str):
    config = config_for(runner_id)
    stop_runner(config)
    time.sleep(2)
    start_runner(config)
    final_status = wait_online(config)
    return jsonify({
        "runnerId": config["runnerId"],
        "action": "RESTART",
        "success": final_status["online"],
        "message": "Runner reiniciado." if final_status["online"] else "Restart enviado, mas health ainda nao respondeu.",
        "status": final_status,
    })


@app.post("/runners/control/<runner_id>/unlock")
def unlock(runner_id: str):
    config = config_for(runner_id)
    removed: list[str] = []
    clear_local_state(config, removed, include_queue=False)
    message = "Nenhuma trava local encontrada." if not removed else "Travas removidas: " + ", ".join(removed)
    return jsonify(action_response(config, "UNLOCK", True, message))


@app.post("/runners/control/<runner_id>/clear-queue")
def clear_queue(runner_id: str):
    config = config_for(runner_id)
    removed, message, success = clear_runner_queue(config)
    return jsonify(action_response(config, "CLEAR_QUEUE", success, f"{message} Itens removidos: {removed}."))


@app.post("/runners/control/<runner_id>/restart-and-unlock")
def restart_and_unlock(runner_id: str):
    config = config_for(runner_id)
    stop_runner(config)
    time.sleep(2)
    removed: list[str] = []
    clear_local_state(config, removed, include_queue=False)
    start_runner(config)
    final_status = wait_online(config)
    message = "Runner reiniciado."
    if removed:
        message += " Travas removidas: " + ", ".join(removed)
    return jsonify({
        "runnerId": config["runnerId"],
        "action": "RESTART_AND_UNLOCK",
        "success": final_status["online"],
        "message": message,
        "status": final_status,
    })


@app.post("/runners/control/<runner_id>/reset-total")
def reset_total(runner_id: str):
    config = config_for(runner_id)
    removed: list[str] = []
    errors: list[str] = []
    try:
        stop_runner(config)
        time.sleep(2)
    except Exception as exc:
        errors.append(f"falha ao parar runner: {exc}")
    try:
        clear_local_state(config, removed, include_queue=True)
    except Exception as exc:
        errors.append(f"falha ao limpar arquivos: {exc}")
    try:
        start_runner(config)
    except Exception as exc:
        errors.append(f"falha ao iniciar runner: {exc}")
    final_status = wait_online(config)
    success = not errors and final_status["online"] and not final_status["busy"] and final_status["queueSize"] == 0
    if success:
        message = "Reset total concluido."
    else:
        message = "Reset total executado, mas ainda ha pendencia."
        if errors:
            message += " " + " | ".join(errors)
    if removed:
        message += " Arquivos removidos: " + ", ".join(removed)
    return jsonify({
        "runnerId": config["runnerId"],
        "action": "RESET_TOTAL",
        "success": success,
        "message": message,
        "status": final_status,
    })


if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=SUPERVISOR_PORT, threads=8)
