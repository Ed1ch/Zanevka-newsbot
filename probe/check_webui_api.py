"""Offline API/SSE/process checks; stdlib unittest, no live mail or publication."""

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prelaunch
import webui_server as api
from werkzeug.security import generate_password_hash

PASSWORD = "offline-admin-fixture"
HASH = generate_password_hash(PASSWORD)


class APIChecks(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.flags = self.root / "flags"
        self.flags.mkdir()
        shutil.copyfile(ROOT / "config.example.py", self.root / "config.py")
        shutil.copytree(ROOT / "webui", self.root / "webui")
        for name, value in {"FLAGS_DIR": self.flags, "LOCK_FILE": self.flags / ".main.lock",
                            "RUNNING_FILE": self.flags / ".newsbot.running"}.items():
            self.stack.enter_context(patch.object(prelaunch, name, value))
        self.cfg = {"username": "админ", "password_hash": HASH, "session_key": "a" * 64,
                    "external_https": False, "trusted_proxies": [], "prefix": "", "session_hours": 1}
        self.app = api.create_app(self.cfg, root=self.root, start_reader=False)
        self.client = self.app.test_client()
        self.ext = self.app.extensions["newsbot"]
        self.token = self.client.get("/api/session").json["csrf_token"]

    def mutation(self, path, data=None, method="POST", token=None):
        return self.client.open(path, method=method, json={} if data is None else data,
            headers={"X-CSRF-Token": self.token if token is None else token})

    def login(self):
        response = self.mutation("/api/login", {"username": "админ", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.data)
        self.token = response.json["csrf_token"]

    def test_auth_csrf_login_logout_and_static_boundary(self):
        self.assertEqual(self.client.get("/api/settings").status_code, 401)
        self.assertEqual(self.client.get("/api/events").status_code, 401)
        self.assertEqual(self.mutation("/api/login", {}, token="bad").status_code, 403)
        self.assertEqual(self.mutation("/api/login", {"username":"админ", "password":"bad"}).status_code, 401)
        self.assertEqual(self.client.get("/").location, "/login")
        with self.client.get("/login") as response:
            self.assertEqual(response.status_code, 200)
        self.login()
        for path in ("/config.py", "/flags/.main.lock", "/index_files/../config.py", "/index_files/AGENTS.md"):
            self.assertEqual(self.client.get(path).status_code, 404)
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mutation("/api/run", token="bad").status_code, 403)
        self.assertEqual(self.mutation("/api/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_settings_preserve_comments_secrets_revision_and_failure(self):
        self.login()
        path = self.root / "config.py"
        path.write_bytes(path.read_bytes() + b'\nEXTRA = 42 # keep this\n')
        initial = self.client.get("/api/settings").json
        self.assertNotIn("IMAP_PASSWORD", initial["values"])
        self.assertNotIn("your_application_password", json.dumps(initial))
        response = self.mutation("/api/settings", {"revision":initial["revision"],
            "values":{"IMAP_PORT":1993,"IMAP_PASSWORD":"","WP_APP_PASSWORD":"new-offline-value"}}, "PUT")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('IMAP_PASSWORD = "YOUR_IMAP_PASSWORD"', path.read_text(encoding="utf-8-sig"))
        self.assertIn('EXTRA = 42 # keep this', path.read_text(encoding="utf-8-sig"))
        self.assertNotIn('new-offline-value', response.get_data(as_text=True))
        self.assertEqual(self.mutation("/api/settings", {"revision":initial["revision"],"values":{}}, "PUT").status_code, 409)
        revision = response.json["revision"]
        before = path.read_bytes()
        with patch.object(api.os, "replace", side_effect=OSError("offline disk error")):
            self.assertEqual(self.mutation("/api/settings", {"revision":revision,"values":{"IMAP_PORT":2993}}, "PUT").status_code, 503)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".config.py*")))
        for values in ({"IMAP_PORT":True}, {"IMAP_PORT":0}, {"DEMO_INTERVAL":2},
                       {"WP_APP_PASSWORD":"***"}, {"IMAP_OUTPUT_DIR":"../other"}, {"POST_STATUS":"delete"}):
            self.assertEqual(self.mutation("/api/settings", {"revision":revision,"values":values}, "PUT").status_code, 400)
        self.assertEqual(path.read_bytes(), before)

    def test_settings_never_execute_python_and_reject_missing(self):
        self.login()
        path = self.root / "config.py"
        path.write_text("raise Exception('must not execute')\n", encoding="utf-8")
        self.assertEqual(self.client.get("/api/settings").status_code, 503)
        path.unlink()
        self.assertEqual(self.client.get("/api/settings").status_code, 503)

    def test_debug_preserves_lock_inode_removes_nested_and_does_not_follow_links(self):
        self.login()
        lock = prelaunch.acquire_process_lock()
        prelaunch.release_process_lock(lock)
        inode = prelaunch.LOCK_FILE.stat().st_ino
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("keep")
        (self.flags / "link").symlink_to(outside, target_is_directory=True)
        (self.flags / "nested").mkdir()
        (self.flags / "nested" / "data").write_text("delete")
        for name in ("delayed.publish", ".newsbot.running", ".newsbot.disabled", ".custom"):
            (self.flags / name).write_text("0")
        response = self.mutation("/api/debug/reset-flags")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(list(self.flags.iterdir()), [prelaunch.LOCK_FILE])
        self.assertEqual(prelaunch.LOCK_FILE.stat().st_ino, inode)
        self.assertTrue((outside / "keep").is_file())
        lock = prelaunch.acquire_process_lock()
        self.assertIsNotNone(lock)
        prelaunch.release_process_lock(lock)

    def test_debug_contended_lock_unknown_and_partial_errors(self):
        self.login()
        flag = self.flags / "keep"
        flag.write_text("keep")
        lock = prelaunch.acquire_process_lock()
        try:
            self.assertEqual(self.mutation("/api/debug/reset-flags").status_code, 409)
            self.assertTrue(flag.exists())
        finally:
            prelaunch.release_process_lock(lock)
        with patch.object(prelaunch, "check_running", return_value="unknown"):
            self.assertEqual(self.mutation("/api/debug/reset-flags").status_code, 503)
            self.assertEqual(self.mutation("/api/run").status_code, 503)
        with patch.object(api.Path, "unlink", side_effect=PermissionError("offline access")):
            result = self.mutation("/api/debug/reset-flags")
        self.assertEqual(result.status_code, 500)
        self.assertFalse(result.json["reset"])
        self.assertIn("keep", result.json["errors"])

    def test_run_start_gap_single_child_and_cooldown(self):
        self.login()
        class Child:
            done = False
            def poll(self): return 0 if self.done else None
        child = Child()
        with patch.object(api.subprocess, "Popen", return_value=child) as popen, patch.object(api.BotControl, "_watch"):
            first = self.mutation("/api/run")
            self.assertEqual(first.status_code, 202, first.data)
            self.assertEqual(self.client.get("/api/status").json["process_state"], "starting")
            self.assertEqual(self.mutation("/api/run").status_code, 409)
            self.assertEqual(self.mutation("/api/debug/reset-flags").status_code, 409)
            self.assertEqual(popen.call_count, 1)
            command = popen.call_args.args[0]
            self.assertEqual(command, [sys.executable, "-u", str(self.root / "prelaunch.py")])
            self.assertNotIn("shell", popen.call_args.kwargs)
            child.done = True
            state = self.client.get("/api/status").json
            self.assertEqual(state["process_state"], "idle")
            self.assertFalse(state["can_run"])
            self.ext["control"].cooldown = 0
            self.assertTrue(self.client.get("/api/status").json["can_run"])

    def test_schedule_windows_and_unknown_crontab(self):
        self.login()
        with patch.object(api.shutil, "which", return_value=None):
            result = self.mutation("/api/schedule", {"enabled":False}, "PUT")
            self.assertEqual(result.status_code, 200)
            self.assertTrue((self.flags / ".newsbot.disabled").exists())
            self.assertEqual(result.json["task_state"], "unknown")
            result = self.mutation("/api/schedule", {"enabled":True}, "PUT")
            self.assertFalse((self.flags / ".newsbot.disabled").exists())
        with patch.object(api.os, "name", "nt"):
            self.assertEqual(self.mutation("/api/schedule", {"enabled":True}, "PUT").status_code, 400)
        self.assertFalse((self.flags / ".newsbot.disabled").exists())

    def test_proxy_https_prefix_cookies_and_untrusted_headers(self):
        cfg = self.cfg | {"prefix":"/newsbot", "external_https":True,"trusted_proxies":["127.0.0.1"]}
        app = api.create_app(cfg, root=self.root, start_reader=False)
        client = app.test_client()
        self.assertEqual(client.get("/newsbot/api/session").status_code, 400)
        headers = {"X-Forwarded-Proto":"https","X-Forwarded-Host":"newsbot.example"}
        response = client.get("/newsbot/api/session", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/newsbot", headers=headers).location, "/newsbot/")
        cookie = response.headers["Set-Cookie"]
        for value in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/newsbot/"):
            self.assertIn(value, cookie)
        self.assertEqual(client.get("/newsbot/", headers=headers).location, "/newsbot/login")
        self.assertEqual(client.get("/newsbot/api/session", headers=headers,
            environ_overrides={"REMOTE_ADDR":"192.0.2.1"}).status_code, 400)
        self.assertEqual(client.get("/api/session", headers=headers).status_code, 404)

    def test_sse_logout_and_expiry_release_subscription(self):
        self.login()
        hub = self.ext["hub"]
        response = self.client.get("/api/events?cursor=" + hub.cursor(), buffered=False)
        iterator = iter(response.response)
        self.assertIn(b"connected", next(iterator))
        self.assertEqual(len(hub.subscribers), 1)
        self.mutation("/api/logout")
        with self.assertRaises(StopIteration): next(iterator)
        response.close()
        self.assertFalse(hub.subscribers)
        self.token = self.client.get("/api/session").json["csrf_token"]
        self.login()
        response = self.client.get("/api/events?cursor=" + hub.cursor(), buffered=False)
        iterator = iter(response.response)
        next(iterator)
        for entry in self.ext["sessions"].values(): entry["expires"] = 0
        with self.assertRaises(StopIteration): next(iterator)
        response.close()
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_password_rotation_revokes_live_session(self):
        path = self.root / "webui_server.local.json"
        cfg = self.cfg | {"host":"127.0.0.1","port":8081,"threads":12}
        path.write_text(json.dumps(cfg), encoding="utf-8")
        app = api.create_app(cfg, root=self.root, config_path=path, start_reader=False)
        client = app.test_client()
        token = client.get("/api/session").json["csrf_token"]
        response = client.post("/api/login", json={"username":"админ","password":PASSWORD}, headers={"X-CSRF-Token":token})
        self.assertEqual(response.status_code, 200)
        cfg["session_key"] = "b" * 64
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(client.get("/api/status").status_code, 401)

    def test_local_admin_initialization_and_password_change_no_default(self):
        path = self.root / "webui_server.local.json"
        with patch("builtins.input", return_value="local-admin"), patch.object(api.getpass, "getpass", side_effect=[PASSWORD, PASSWORD]), patch("sys.stdout", io.StringIO()):
            api.admin_command(path)
        cfg = api.load_server_config(path)
        self.assertNotEqual(cfg["password_hash"], PASSWORD)
        self.assertNotIn(PASSWORD, path.read_text())
        previous = cfg["session_key"]
        with patch.object(api.getpass, "getpass", side_effect=[PASSWORD, PASSWORD]), patch("sys.stdout", io.StringIO()):
            api.admin_command(path, change=True)
        self.assertNotEqual(api.load_server_config(path)["session_key"], previous)
        with self.assertRaises(RuntimeError): api.admin_command(path)

    def test_cron_exact_job_current_user_and_explicit_timezone(self):
        control = self.ext["control"]
        task = f'0 */2 * * * /bin/sh "{self.root}/cron-newsbot.sh"'
        result = subprocess.CompletedProcess([], 0, "CRON_TZ=UTC\n" + task + "\n", "")
        with patch.object(api.shutil, "which", return_value="/usr/bin/crontab"), patch.object(api.subprocess, "run", return_value=result) as run:
            state = control.schedule(refresh=True)
            self.assertEqual(state["task_state"], "present")
            self.assertEqual(state["timezone"], "UTC")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/crontab", "-l"])
            result.stdout = f'0 */2 * * * echo "{self.root}/cron-newsbot.sh"\n'
            self.assertEqual(control.schedule(refresh=True)["task_state"], "missing")


class ReaderChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = api.LogHub(self.root / "logs", lambda: {"process_state":"idle"})
        self.addCleanup(self.hub.close)

    def journal(self, number=0):
        self.hub.directory.mkdir(exist_ok=True)
        return self.hub.directory / f"newsbot_20261023_120000_000000_{number:012x}.txt"

    def test_missing_directory_latest_ten_and_stable_identity_after_restart(self):
        self.hub.poll()
        self.assertEqual(self.hub.snapshot()["lines"], [])
        for i in range(12): self.journal(i).write_text(f"2026-10-23 12:00:00 [INFO] file {i}\n", encoding="utf-8")
        self.hub.poll()
        snapshot = self.hub.snapshot()
        self.assertEqual(len(snapshot["files"]), 10)
        self.assertEqual(snapshot["lines"][0]["text"], "2026-10-23 12:00:00 [INFO] file 2")
        other = api.LogHub(self.hub.directory, lambda: {})
        other.poll()
        self.assertEqual([l["identity"] for l in snapshot["lines"]], [l["identity"] for l in other.snapshot()["lines"]])
        subscriber = other.subscribe(snapshot["cursor"])
        self.assertEqual(subscriber.get_nowait()["kind"], "resync")
        other.unsubscribe(subscriber)

    def test_split_utf8_immediate_details_context_history_race_replay_and_delete(self):
        path = self.journal()
        path.write_bytes(b"2026-10-23 12:00:00 [WARNING] header\n")
        self.hub.poll()
        initial = self.hub.snapshot()
        raw = "\tПривет <script>safe</script>\n".encode()
        with path.open("ab") as stream: stream.write(raw[:3])
        self.hub.poll()
        self.assertEqual(len(self.hub.snapshot()["lines"]), 1)
        with path.open("ab") as stream: stream.write(raw[3:])
        self.hub.poll()
        subscriber = self.hub.subscribe(initial["cursor"])
        event = subscriber.get_nowait()
        self.assertEqual(event["kind"], "line")
        self.assertEqual(event["data"]["text"], raw.decode().rstrip("\n"))
        self.assertEqual(event["data"]["time"], "2026-10-23 12:00:00")
        self.assertEqual(event["data"]["level"], "WARNING")
        self.assertEqual(event["data"]["block"], initial["lines"][0]["block"])
        self.hub.unsubscribe(subscriber)
        cursor = self.hub.cursor()
        reconnect = self.hub.subscribe(cursor)
        self.assertTrue(reconnect.empty())
        path.unlink()
        self.hub.poll()
        self.assertEqual(reconnect.get_nowait()["kind"], "resync")
        self.hub.unsubscribe(reconnect)

    def test_slow_subscriber_is_bounded_and_does_not_block_others(self):
        slow = self.hub.subscribe(self.hub.cursor())
        for i in range(300): self.hub.emit("diagnostic", {"text":str(i)})
        self.assertTrue(slow.overflow)
        self.assertEqual(slow.qsize(), 1)
        self.assertEqual(slow.get_nowait()["kind"], "resync")
        fast = self.hub.subscribe(self.hub.cursor())
        self.hub.emit("diagnostic", {"text":"new"})
        self.assertEqual(fast.get_nowait()["data"]["text"], "new")
        self.hub.unsubscribe(slow)
        self.hub.unsubscribe(fast)
        self.assertFalse(self.hub.subscribers)

    def test_large_initial_history_reads_once_in_order_with_bounded_memory(self):
        first = self.journal(1)
        first.write_text("2026-10-23 12:00:00 [WARNING] old header\n" + "\tlong detail " + "x" * 500 + "\n", encoding="utf-8")
        with first.open("ab") as stream:
            for i in range(11000): stream.write(("\tolder " + str(i) + " " + "x"*500 + "\n").encode())
        second = self.journal(2)
        second.write_text("2026-10-23 12:00:01 [OK] newer\n", encoding="utf-8")
        self.hub.poll()
        snapshot = self.hub.snapshot()
        self.assertTrue(snapshot["truncated"])
        self.assertLessEqual(len(snapshot["lines"]), 10000)
        self.assertLessEqual(self.hub.bytes, 4*1024*1024)
        self.assertEqual(snapshot["lines"][-1]["text"], "2026-10-23 12:00:01 [OK] newer")
        self.assertEqual(snapshot["lines"][0]["level"], "WARNING")
        self.assertEqual(snapshot["lines"][0]["time"], "2026-10-23 12:00:00")
        cursor = self.hub.cursor()
        self.hub.poll()
        self.assertEqual(self.hub.cursor(), cursor)


class ProcessChecks(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Unix cron wrapper")
    def test_cron_wrapper_absolute_paths_disabled_and_manual_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(ROOT / "cron-newsbot.sh", root / "cron-newsbot.sh")
            (root / "prelaunch.py").write_text('from pathlib import Path\nPath(__file__).with_name("called").touch()\n')
            command = ["/bin/sh", str(root / "cron-newsbot.sh")]
            env = os.environ | {"NEWSBOT_PYTHON":sys.executable}
            result = subprocess.run(command, cwd="/", env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "called").exists())
            (root / "called").unlink()
            (root / "flags").mkdir()
            (root / "flags" / ".newsbot.disabled").touch()
            result = subprocess.run(command, cwd="/", env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "called").exists())

    def prepare_fixture(self, root):
        for name in ("prelaunch.py", "log.py"):
            shutil.copyfile(ROOT / name, root / name)
        shutil.copyfile(ROOT / "config.example.py", root / "config.py")
        (root / "main.py").write_text('import log,time\nfrom pathlib import Path\n'
            'def main(**options):\n log.block("offline child")\n time.sleep(1)\n'
            ' Path(__file__).with_name("completed").touch()\n return False\n')

    def test_parent_exit_does_not_stop_child_and_new_controller_sees_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare_fixture(root)
            script = root / "parent.py"
            script.write_text('import sys\nfrom pathlib import Path\n'
                f'sys.path.insert(0,{str(ROOT)!r})\nimport prelaunch,webui_server as api\n'
                'root=Path(__file__).parent\nprelaunch.FLAGS_DIR=root/"flags"\n'
                'prelaunch.LOCK_FILE=prelaunch.FLAGS_DIR/".main.lock"\n'
                'prelaunch.RUNNING_FILE=prelaunch.FLAGS_DIR/".newsbot.running"\n'
                'control=api.BotControl(root,api.Settings(root/"config.py"))\ncontrol.run()\n')
            result = subprocess.run([sys.executable, str(script)], cwd="/", capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            deadline = time.monotonic() + 5
            while not (root / "flags" / ".newsbot.running").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            status = subprocess.run([sys.executable, "-c", 'import prelaunch; assert prelaunch.check_running()=="running"'], cwd=root, capture_output=True)
            self.assertEqual(status.returncode, 0, status.stderr)
            while not (root / "completed").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((root / "completed").exists())
            while (root / "flags" / ".newsbot.running").exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse((root / "flags" / ".newsbot.running").exists())

    def test_shared_status_probe_cannot_make_owner_skip_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare_fixture(root)
            flags = root / "flags"
            flags.mkdir()
            marker = flags / ".newsbot.running"
            marker.write_bytes(b"0")
            with marker.open("r+b") as reader:
                prelaunch._lock(reader, shared=True)
                child = subprocess.Popen([sys.executable, str(root / "prelaunch.py")], cwd="/", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 3
                    while not (flags / ".main.lock").exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertIsNone(child.poll())
                finally:
                    reader.close()
                _, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr)
            self.assertTrue((root / "completed").exists())

    def test_configuration_load_bypasses_same_size_timestamp_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.py"
            with patch.object(prelaunch, "__file__", str(Path(temporary) / "prelaunch.py")):
                path.write_text("VALUE = 111\n")
                stamp = path.stat()
                self.assertEqual(prelaunch.load_configuration().VALUE, 111)
                path.write_text("VALUE = 222\n")
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                self.assertEqual(prelaunch.load_configuration().VALUE, 222)

    def test_real_process_lock_and_status_during_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(ROOT / "prelaunch.py", root / "prelaunch.py")
            script = root / "owner.py"
            script.write_text('import prelaunch, time\nlock=prelaunch.acquire_process_lock()\n'
                'assert lock is not None\nm=open(prelaunch.RUNNING_FILE,"a+b")\nm.write(b"0");m.flush()\n'
                'prelaunch._lock(m,blocking=True)\nprint("ready",flush=True)\ntime.sleep(30)\n')
            child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, cwd="/")
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                contender = subprocess.run([sys.executable, "-c", 'import prelaunch; assert prelaunch.acquire_process_lock() is None; assert prelaunch.check_running()=="running"'], cwd=root, capture_output=True, text=True)
                self.assertEqual(contender.returncode, 0, contender.stderr)
                duplicate = subprocess.run([sys.executable, str(root / "prelaunch.py")], cwd="/", capture_output=True)
                self.assertEqual(duplicate.returncode, 0)
                self.assertEqual(duplicate.stdout + duplicate.stderr, b"")
                self.assertFalse((root / "logs").exists())
            finally:
                child.kill(); child.wait(); child.stdout.close()
            stale = subprocess.run([sys.executable, "-c", 'import prelaunch; assert prelaunch.check_running()=="idle"; assert prelaunch.RUNNING_FILE.exists()'], cwd=root, capture_output=True)
            self.assertEqual(stale.returncode, 0, stale.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
