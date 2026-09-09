"""Parada cooperativa no Windows, sem servidor, arquivo de polling ou vigia.

O evento existe somente enquanto o coletor está vivo. A espera pelo próximo
ciclo dorme no kernel e acorda imediatamente quando o comando Stop o sinaliza.
No Linux, o Event em memória permite exercitar o mesmo ciclo nos testes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import threading
from pathlib import Path


def event_name(project_dir: Path, destination: str) -> str:
    if destination not in {"local", "remote"}:
        raise ValueError("Destino inválido.")
    identity = os.path.normcase(str(project_dir.resolve())).encode()
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return rf"Local\ControleRendaVariavel-{digest}-{destination}"


class CollectorStop:
    def __init__(self, project_dir: Path, destination: str) -> None:
        self._event = threading.Event()
        self._handle = None
        if os.name == "nt":
            import win32event

            self._handle = win32event.CreateEvent(
                None, True, False, event_name(project_dir, destination)
            )

    def __enter__(self) -> CollectorStop:
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._handle is not None:
            self._handle.Close()

    def wait(self, seconds: float) -> bool:
        if self._handle is None:
            return self._event.wait(seconds)
        import win32event

        result = win32event.WaitForSingleObject(self._handle, max(0, int(seconds * 1000)))
        return result == win32event.WAIT_OBJECT_0

    def running(self) -> bool:
        return not self.wait(0)

    def stop(self) -> None:
        if self._handle is None:
            self._event.set()
        else:
            import win32event

            win32event.SetEvent(self._handle)


def request_stop(project_dir: Path, destination: str) -> bool:
    import pywintypes
    import win32event

    try:
        handle = win32event.OpenEvent(0x0002, False, event_name(project_dir, destination))
    except pywintypes.error as exc:
        if exc.winerror == 2:
            return False
        raise
    try:
        win32event.SetEvent(handle)
    finally:
        handle.Close()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Encerra somente o coletor escolhido.")
    parser.add_argument("destination", choices=("local", "remote"))
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Este controle exige Windows.")
    stopped = request_stop(Path(__file__).resolve().parents[2], args.destination)
    print("stopping" if stopped else "stopped")


if __name__ == "__main__":
    main()
