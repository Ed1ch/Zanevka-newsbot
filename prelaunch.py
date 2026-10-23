"""Single external entry point; keep one OS lock for the whole pipeline."""

import errno
import os
import shutil
import sys
import types
from pathlib import Path


FLAGS_DIR = Path(__file__).resolve().parent / "flags"
DELAYED_PUBLISH_FILE = FLAGS_DIR / "delayed.publish"
LOCK_FILE = FLAGS_DIR / ".main.lock"
RUNNING_FILE = FLAGS_DIR / ".newsbot.running"


def _lock(handle, *, blocking=False, shared=False):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        mode = (msvcrt.LK_RLCK if blocking else msvcrt.LK_NBRLCK) if shared else (
            msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK)
        msvcrt.locking(handle.fileno(), mode, 1)
    else:
        import fcntl
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(handle.fileno(), mode | (0 if blocking else fcntl.LOCK_NB))


def _busy(error):
    return error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)


def _is_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _open_status_marker():
    if os.name != "nt":
        return open(RUNNING_FILE, "r+b")
    # A Windows observer must allow the owner to delete the marker after close.
    # Normal Python open() does not request FILE_SHARE_DELETE.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(RUNNING_FILE), 0x80000000 | 0x40000000, 1 | 2 | 4, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except OSError:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise
    return os.fdopen(descriptor, "r+b")


def check_running():
    """Never create/truncate a marker. A free crash remnant means idle."""
    try:
        if _is_link(RUNNING_FILE) or _is_link(RUNNING_FILE.parent):
            return "unknown"
        with _open_status_marker() as marker:
            try:
                _lock(marker, shared=True)
            except OSError as error:
                if _busy(error):
                    return "running"
                raise
            # Closing releases the short shared probe. The owner waits for it,
            # so a probe can never make a newly acquired main lock skip a run.
        return "idle"
    except FileNotFoundError:
        return "idle"
    except OSError:
        return "unknown"


def reset_flags():
    """Administrative reset under the *same* lock; never run the pipeline."""
    lock_file = acquire_process_lock()
    if lock_file is None:
        return None
    deleted, errors = [], {}
    try:
        if FLAGS_DIR.is_symlink() or (hasattr(FLAGS_DIR, "is_junction") and FLAGS_DIR.is_junction()):
            raise OSError("Каталог flags не должен быть ссылкой.")
        for path in sorted(FLAGS_DIR.iterdir()):
            if path.name == ".main.lock":
                continue
            try:
                if path.is_symlink():
                    path.unlink()
                elif hasattr(path, "is_junction") and path.is_junction():
                    path.rmdir()
                elif path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted.append(path.name)
            except OSError:
                errors[path.name] = "Не удалось удалить запись; проверьте права и открытые файлы."
        return {"reset": not errors, "deleted": deleted, "errors": errors}
    finally:
        release_process_lock(lock_file)


def acquire_process_lock():
    """
    Захватывает системный lock для единственного экземпляра main.py.

    Сам файл flags/.main.lock может оставаться на диске после завершения.
    Важен не факт его существования, а системная блокировка файла,
    которая автоматически освобождается ОС при завершении процесса.
    """
    if _is_link(Path(LOCK_FILE)) or _is_link(Path(LOCK_FILE).parent):
        raise OSError("Блокировка и каталог flags не должны быть ссылками.")
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

    except OSError as error:
        lock_file.close()
        if _busy(error):
            return None
        raise

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


def load_configuration():
    """Load trusted local Python without reusing a stale compiled cache."""
    config_path = Path(__file__).resolve().parent / "config.py"
    config = types.ModuleType("config")
    config.__file__ = str(config_path)
    exec(compile(config_path.read_bytes(), str(config_path), "exec"), config.__dict__)
    return config


def main():
    lock_file = acquire_process_lock()
    if lock_file is None:
        return

    marker = None
    journal = None
    empty = False
    try:
        marker = open(RUNNING_FILE, "a+b")
        if marker.seek(0, os.SEEK_END) == 0:
            marker.write(b"0")
            marker.flush()
        _lock(marker, blocking=True)
        # Read exact source, bypassing timestamp/size based .pyc reuse after
        # rapid API saves. Only this trusted local launcher executes config.
        config = load_configuration()
        sys.modules["config"] = config
        import log
        journal = log
        log.setup(prune=False)
        log.activate()
        log.cleanup_empty_journals()
        import main as pipeline

        empty = pipeline.main(
            defer_if_incomplete=not DELAYED_PUBLISH_FILE.exists(),
            delayed_publish_file=DELAYED_PUBLISH_FILE,
        )
    finally:
        try:
            if journal is not None:
                path = journal.current_path()
                journal.close()
                if empty and not journal.has_problems():
                    journal.mark_empty(path)
        finally:
            try:
                if marker is not None:
                    marker.close()
                    RUNNING_FILE.unlink(missing_ok=True)
            finally:
                release_process_lock(lock_file)


if __name__ == "__main__":
    main()
