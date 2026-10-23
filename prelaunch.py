"""Single external entry point; keep one OS lock for the whole pipeline."""

import os
from pathlib import Path


FLAGS_DIR = Path(__file__).resolve().parent / "flags"
DELAYED_PUBLISH_FILE = FLAGS_DIR / "delayed.publish"
LOCK_FILE = FLAGS_DIR / ".main.lock"


def acquire_process_lock():
    """
    Захватывает системный lock для единственного экземпляра main.py.

    Сам файл flags/.main.lock может оставаться на диске после завершения.
    Важен не факт его существования, а системная блокировка файла,
    которая автоматически освобождается ОС при завершении процесса.
    """
    Path(LOCK_FILE).parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(
        LOCK_FILE,
        "a+",
        encoding="utf-8",
    )

    lock_file.seek(0, os.SEEK_END)

    if lock_file.tell() == 0:
        lock_file.write("0")
        lock_file.flush()

    lock_file.seek(0)

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

    except (OSError, IOError):
        lock_file.close()
        return None

    return lock_file


def release_process_lock(lock_file):
    if lock_file is None:
        return

    try:
        lock_file.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN,
            )
    finally:
        lock_file.close()


def main():
    lock_file = acquire_process_lock()
    if lock_file is None:
        return

    try:
        import main as pipeline

        pipeline.main(
            defer_if_incomplete=not DELAYED_PUBLISH_FILE.exists(),
            delayed_publish_file=DELAYED_PUBLISH_FILE,
        )
    finally:
        release_process_lock(lock_file)


if __name__ == "__main__":
    main()
