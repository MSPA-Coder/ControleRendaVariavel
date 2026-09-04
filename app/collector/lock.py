"""Exclusão interprocesso para o processo local de coleta RTD."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class CollectorAlreadyRunningError(RuntimeError):
    """Indica que outra instância já detém o lock do coletor."""


def _lock_path(project_dir: Path) -> Path:
    return project_dir / ".docker-local" / "rtd-collector.lock"


def _acquire(lock_file: BinaryIO, *, wait: bool) -> None:
    if os.name == "nt":
        import msvcrt

        # ``msvcrt.locking`` exige um byte existente e o ponteiro no início.
        # O segundo processo não deve escrever em uma região que o primeiro
        # já possa ter trancado: somente inicialize o arquivo quando vazio.
        while True:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                try:
                    lock_file.write(b"\0")
                    lock_file.flush()
                except PermissionError as exc:
                    if not wait:
                        raise CollectorAlreadyRunningError(
                            "O coletor RTD local já está em execução."
                        ) from exc
                    time.sleep(1)
                    continue
            # A escrita acima move o cursor; ``locking`` sempre deve receber o
            # offset zero, inclusive nas tentativas subsequentes.
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if not wait:
                    raise CollectorAlreadyRunningError(
                        "O coletor RTD local já está em execução."
                    ) from exc
                time.sleep(1)

    import fcntl

    flags = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(lock_file.fileno(), flags)
    except BlockingIOError as exc:
        raise CollectorAlreadyRunningError("O coletor RTD local já está em execução.") from exc


def _release(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def collector_process_lock(project_dir: Path, *, wait: bool = False) -> Iterator[None]:
    """Mantém uma única coleta local ativa para este projeto.

    O arquivo é somente um identificador persistente; a exclusão é mantida
    pelo sistema operacional e é liberada automaticamente quando o processo
    termina, inclusive em caso de crash. O agente remoto não usa este caminho.
    """

    path = _lock_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r+b") as lock_file:
        _acquire(lock_file, wait=wait)
        try:
            yield
        finally:
            _release(lock_file)
