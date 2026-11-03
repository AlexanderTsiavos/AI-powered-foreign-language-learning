#!/usr/bin/env python3
"""Start the private Codespaces pilot without blocking the editor lifecycle.

Uses only the Python standard library. Does not create a Codespace or a tunnel.
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]


def healthy(host: str, port: int) -> bool:
    connection = http.client.HTTPConnection(host, port, timeout=1)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        payload = json.loads(response.read(4096))
        return (response.status == 200 and isinstance(payload, dict)
                and payload.get("status") == "ok"
                and payload.get("teacherMode") == "scripted-demo")
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def port_occupied(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except ConnectionRefusedError:
        return False
    except OSError as exc:
        raise RuntimeError("Δεν ήταν δυνατός ο έλεγχος της θύρας.") from exc


def main() -> int:
    if sys.version_info < (3, 12):
        print("Χρειάζεται Python 3.12 ή νεότερη.", file=sys.stderr)
        return 1
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        print("Η PORT πρέπει να είναι αριθμός από 1 έως 65535.", file=sys.stderr)
        return 1

    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    if healthy(probe_host, port):
        print("Η εφαρμογή είναι ήδη διαθέσιμη. Χρησιμοποίησε τη θύρα από το Ports.")
        return 0
    if port_occupied(probe_host, port):
        print("Η θύρα χρησιμοποιείται από άλλη υπηρεσία. Δεν διακόπηκε καμία διεργασία.", file=sys.stderr)
        return 1

    default_data = ROOT.parent / "academy-data" / "academy.sqlite3"
    db_path = Path(os.environ.get("ACADEMY_DB", str(default_data))).expanduser().resolve()
    default_runtime = Path(tempfile.gettempdir()) / "ai-language-academy-runtime"
    runtime = Path(os.environ.get("ACADEMY_RUNTIME_DIR", str(default_runtime))).expanduser().resolve()
    if runtime.is_relative_to(ROOT) or db_path.is_relative_to(ROOT):
        print("Η βάση και τα αρχεία λειτουργίας πρέπει να βρίσκονται έξω από το αποθετήριο.", file=sys.stderr)
        return 1
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    child_env = dict(os.environ)
    child_env["HOST"] = host
    child_env["PORT"] = str(port)
    child_env["ACADEMY_DB"] = str(db_path)
    child_env["ACADEMY_SECURE_COOKIES"] = "1" if os.environ.get("CODESPACES", "").lower() == "true" else "0"
    child_env["PYTHONUNBUFFERED"] = "1"

    with (runtime / "server.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py")], cwd=ROOT, env=child_env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (runtime / "server.pid").write_text(str(process.pid), encoding="ascii")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print("Η εφαρμογή δεν ξεκίνησε. Έλεγξε το server.log στον φάκελο λειτουργίας.", file=sys.stderr)
            return 1
        if healthy(probe_host, port):
            print("Η εφαρμογή είναι διαθέσιμη. Στο Ports έλεγξε ότι η ορατότητα είναι Private και επίλεξε Open in Browser.")
            return 0
        time.sleep(0.15)
    # Only stop the child this invocation created; never interrupt a port owner.
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    print("Η εφαρμογή δεν απάντησε εγκαίρως. Έλεγξε το server.log στον φάκελο λειτουργίας.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError):
        print("Η εκκίνηση δεν ολοκληρώθηκε. Έλεγξε την πρόσβαση στους φακέλους και στη θύρα.", file=sys.stderr)
        raise SystemExit(1)
