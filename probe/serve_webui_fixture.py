"""Disposable offline browser fixture. No production config or mail is used."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prelaunch
from webui_server import create_app
from werkzeug.security import generate_password_hash
from waitress import serve

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="newsbot-browser-") as temporary:
        root = Path(temporary)
        shutil.copytree(ROOT / "webui", root / "webui")
        for name in ("prelaunch.py", "log.py"):
            shutil.copyfile(ROOT / name, root / name)
        config = (ROOT / "config.example.py").read_text(encoding="utf-8-sig")
        (root / "config.py").write_text(config, encoding="utf-8")
        (root / "main.py").write_text('''import log, time
def main(**options):
    log.block("OFFLINE BROWSER RUN")
    time.sleep(0.8)
    log.detail("Живая строка <script>window.injected=true</script>")
    time.sleep(0.8)
    log.block("OFFLINE COMPLETE", level="OK")
    return False
''', encoding="utf-8")
        prelaunch.FLAGS_DIR = root / "flags"
        prelaunch.LOCK_FILE = prelaunch.FLAGS_DIR / ".main.lock"
        prelaunch.RUNNING_FILE = prelaunch.FLAGS_DIR / ".newsbot.running"
        cfg = {"username":"browser-admin", "password_hash":generate_password_hash("browser-offline-fixture"),
            "session_key":"browser-session-offline-fixture", "external_https":False,
            "trusted_proxies":[], "prefix":args.prefix, "session_hours":1}
        app = create_app(cfg, root=root)
        print(json.dumps({"root":str(root), "port":args.port}), flush=True)
        serve(app, host="127.0.0.1", port=args.port, threads=12, clear_untrusted_proxy_headers=False)

if __name__ == "__main__":
    main()
