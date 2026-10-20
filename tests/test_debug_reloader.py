"""端到端验证：调试重载器下调度器只会启动一次。

`flask run --debug` 会派生父/子两个进程，两者都会导入 main.py 并执行引导逻辑。
如果判断不严，父进程也会启动一个调度器 → 同一批源被抓取两次、通知重复发送。

用法：
    python tests/test_debug_reloader.py [项目根目录]
输出调度器启动次数；1 = 正确，2 = 重复启动（缺陷）。
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.abspath(REPO)
PYTHON = sys.executable
PORT = 5094


def free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PORT = free_port()

workdir = tempfile.mkdtemp(prefix="rssagg-reloader-")
db_path = os.path.join(workdir, "reloader.db").replace("\\", "/")
config_path = os.path.join(workdir, "config.yaml")
with open(config_path, "w", encoding="utf-8") as fh:
    fh.write(
        "app:\n  name: ReloaderProbe\n"
        f'database:\n  url: "sqlite:///{db_path}"\n'
        "scheduler:\n  enabled: true\n  default_interval: 30\n"
        f"server:\n  host: 127.0.0.1\n  port: {PORT}\n  debug: false\n"
        "logging:\n  level: INFO\n  file: reloader.log\n"
    )

env = dict(
    os.environ,
    RSS_AGGREGATOR_CONFIG=config_path,
    PYTHONPATH=REPO,
    PYTHONUTF8="1",
    PYTHONUNBUFFERED="1",
)
env.pop("WERKZEUG_RUN_MAIN", None)
env.pop("RSS_AGGREGATOR_SCHEDULER", None)

proc = subprocess.Popen(
    [PYTHON, "-m", "flask", "--app", "main", "run", "--debug",
     "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=workdir, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
)

output = []


def drain():
    for line in proc.stdout:
        output.append(line)


import threading  # noqa: E402

threading.Thread(target=drain, daemon=True).start()

started = False
try:
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=2) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.4)

    if not started:
        print("服务未在调试重载器下启动，输出如下：")
        print("".join(output)[-3000:])
        raise SystemExit(2)

    time.sleep(3)  # 留出时间让两个进程都完成引导
finally:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   capture_output=True, check=False)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

log_path = os.path.join(workdir, "reloader.log")
log = ""
if os.path.exists(log_path):
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        log = fh.read()

starts = len(re.findall(r"调度器已启动", log))
skips = len(re.findall(r"调度器未启动", log))

print(f"项目目录        : {REPO}")
print(f"调度器已启动次数: {starts}")
print(f"调度器跳过次数  : {skips}")
print("--- 日志摘录 ---")
for line in log.splitlines():
    if "调度器" in line:
        print("   ", line.strip())

if starts == 1:
    print("\n结果: 正确（重载器下只启动一次调度器）")
    raise SystemExit(0)
print(f"\n结果: 缺陷（预期 1 次，实际 {starts} 次 → 重复抓取、重复通知）")
raise SystemExit(1)
