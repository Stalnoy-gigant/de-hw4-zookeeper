"""Сценарий этапов 3-4: две копии координатора, SIGSTOP лидеру посреди расчёта, SIGCONT после
завершения запасного. Время смены лидера = (A приостановлен) -> (B: «СТАЛ ЛИДЕРОМ»).

    python src/scenario.py nofence            # этап 3, FENCING=0
    python src/scenario.py fence --fencing    # этап 4, FENCING=1
"""
import argparse, os, re, signal, subprocess, sys, time
from datetime import datetime
from kazoo.client import KazooClient

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "..", "logs")
ZK = "localhost:2181,localhost:2182,localhost:2183"

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--fencing", action="store_true")
ap.add_argument("--stop-after", type=int, default=8, help="приостановить лидера после N чанков")
ap.add_argument("--delay", type=float, default=1.0)
a = ap.parse_args()

out = open(os.path.join(LOGS, f"{a.run}_scenario.log"), "w", encoding="utf-8")


def say(msg):
    now = time.time()
    line = f"{datetime.fromtimestamp(now):%Y-%m-%d %H:%M:%S.%f}"[:-3] + f" [scenario] {msg}  (t={now:.3f})"
    print(line, flush=True)
    out.write(line + "\n"); out.flush()
    return now


def log_text(cid):
    p = os.path.join(LOGS, f"{a.run}_{cid}.log")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def wait_for(cid, pattern, timeout=180):
    t = time.time()
    while time.time() - t < timeout:
        m = re.search(pattern + r".*\(t=([\d.]+)\)", log_text(cid))
        if m:
            return float(m.group(1))
        time.sleep(0.1)
    raise TimeoutError(f"{cid}: не дождался «{pattern}»")


# чистое состояние прогона
for cid in ("A", "B"):
    p = os.path.join(LOGS, f"{a.run}_{cid}.log")
    if os.path.exists(p):
        os.remove(p)
zk = KazooClient(hosts=ZK); zk.start()
zk.delete(f"/hw4/{a.run}", recursive=True)
zk.stop()

env = dict(os.environ, FENCING="1" if a.fencing else "0")
say(f"прогон {a.run}: FENCING={env['FENCING']}, приостановка лидера после {a.stop_after} чанков")
cmd = lambda cid: [sys.executable, os.path.join(HERE, "coordinator.py"), "--id", cid, "--run", a.run,
                   "--delay", str(a.delay)]
pa = subprocess.Popen(cmd("A"), env=env, stdout=subprocess.DEVNULL)
wait_for("A", "СТАЛ ЛИДЕРОМ")
pb = subprocess.Popen(cmd("B"), env=env, stdout=subprocess.DEVNULL)
wait_for("B", "запасной")
say(f"запущены координатор A (pid {pa.pid}, лидер) и B (pid {pb.pid}, запасной)")

wait_for("A", f"чанк {a.stop_after}/")
t_stop = say(f"kill -STOP {pa.pid}: лидер A приостановлен посреди расчёта")
os.kill(pa.pid, signal.SIGSTOP)

t_lead = wait_for("B", "СТАЛ ЛИДЕРОМ")
say(f"B стал лидером через {t_lead - t_stop:.2f} s после приостановки A (тайм-аут сессии 10 s)")
t_done = wait_for("B", "указатель .*version=")
say(f"B завершил расчёт и опубликовал результат через {t_done - t_stop:.2f} s после приостановки A")
time.sleep(max(0.0, 40 - (time.time() - t_stop)))   # держим A на паузе ~40 s, но не меньше работы B
t_cont = say(f"kill -CONT {pa.pid}: A возобновлён, был приостановлен {time.time() - t_stop:.1f} s")
os.kill(pa.pid, signal.SIGCONT)

pa.wait(); pb.wait()
say(f"оба координатора завершились (A rc={pa.returncode}, B rc={pb.returncode})")
for cid in ("A", "B"):
    for line in log_text(cid).splitlines():
        if re.search(r"записал результат|ЗАПИСЬ ОТКЛОНЕНА", line):
            say(f"итог {cid}: {line.split('] ', 1)[1].rsplit('  (t=', 1)[0]}")
say(f"ИТОГО: время смены лидера {t_lead - t_stop:.2f} s")
