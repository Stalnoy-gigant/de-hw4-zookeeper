"""Координатор расчёта сочетаний навыков (ДЗ 3, вариант 1: 1С) под управлением ZooKeeper.

Запускаются две копии с разными --id. Выбор лидера: эфемерные последовательные узлы
/hw4/<run>/election/n_*, лидер - владелец наименьшего номера, остальные следят за предшественником.
Новый лидер увеличивает эпоху (/hw4/<run>/epoch), обрабатывает чанки JSONL из RustFS и после
каждого чанка пишет чекпоинт: частичный Counter в S3 + узел /hw4/<run>/checkpoint/chunk_NNNN.
Запасной, став лидером, пропускает чанки из чекпоинта. Результат: CSV в S3 + указатель
/hw4/<run>/result. При FENCING=1 указатель меняется только в транзакции с проверкой версии эпохи.
"""
import argparse, io, itertools, json, os, re, threading, time
from collections import Counter
from datetime import datetime
from kazoo.client import KazooClient, KazooState
from kazoo.exceptions import ConnectionLoss, NodeExistsError, NoNodeError, SessionExpiredError
from common import *

ZK_HOSTS = "localhost:2181,localhost:2182,localhost:2183"
SESSION_TIMEOUT = 10.0
SEP = " | "
FENCING = os.environ.get("FENCING", "0") == "1"
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")

_name_re = re.compile(NAME_RE, re.I)
_skill_re = re.compile(SKILL_RE, re.I)


class Log:
    def __init__(self, path, who):
        self.f = open(path, "a", encoding="utf-8")
        self.who = who

    def __call__(self, msg):
        now = time.time()
        line = f"{datetime.fromtimestamp(now):%Y-%m-%d %H:%M:%S.%f}"[:-3] + f" [{self.who}] {msg}  (t={now:.3f})"
        print(line, flush=True)
        self.f.write(line + "\n"); self.f.flush()


def is_variant(rec):
    if rec["role"] != ROLE:
        return False
    return bool(_name_re.search(rec["name"])) or any(_skill_re.match(s) for s in rec["skills"])


def read_chunk(c, start, end, size):
    """Строки, НАЧИНАЮЩИЕСЯ в [start, end) - та же логика, что в ДЗ 3 (src/mr.py)."""
    lo = max(0, start - 1)
    hi = min(size, end + (1 << 20)) - 1      # запас 1 МиБ на дочитывание последней строки
    data = c.get_object(Bucket=BUCKET, Key=CLEAN_KEY, Range=f"bytes={lo}-{hi}")["Body"].read()
    off = start - lo
    if start > 0 and data[0:1] != b"\n":
        off = data.find(b"\n") + 1            # огрызок строки дочитает предыдущий чанк
    out, pos, limit = [], off, end - lo
    while pos < min(limit, len(data)):
        nl = data.find(b"\n", pos)
        if nl < 0:
            if hi + 1 >= size:
                nl = len(data)
            else:
                data += c.get_object(Bucket=BUCKET, Key=CLEAN_KEY,
                                     Range=f"bytes={hi+1}-{min(size-1, hi+(4<<20))}")["Body"].read()
                hi = min(size - 1, hi + (4 << 20))
                continue
        out.append(data[pos:nl])
        pos = nl + 1
    return out


def map_chunk(c, start, end, size):
    cnt = [Counter(), Counter(), Counter()]
    n_lines = n_var = 0
    for line in read_chunk(c, start, end, size):
        if not line:
            continue
        n_lines += 1
        rec = json.loads(line)
        if not is_variant(rec):
            continue
        n_var += 1
        sk = sorted({s.lower() for s in rec["skills"]})
        for k in (1, 2, 3):
            cnt[k - 1].update(SEP.join(t) for t in itertools.combinations(sk, k))
    return {"lines": n_lines, "variant": n_var, "cnt": [dict(x) for x in cnt]}


def to_csv(counter):
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    body = io.StringIO()
    body.write('"skills",count\n')
    for key, v in items:
        body.write('"' + key.replace('"', '""') + f'",{v}\n')
    return body.getvalue().encode(), items


class Coordinator:
    def __init__(self, cid, run, chunk_mb, delay):
        self.id, self.run, self.delay = cid, run, delay
        self.chunk = chunk_mb << 20
        self.root = f"/hw4/{run}"
        self.log = Log(os.path.join(LOG_DIR, f"{run}_{cid}.log"), cid)
        self.s3 = s3()
        self.zk = KazooClient(hosts=ZK_HOSTS, timeout=SESSION_TIMEOUT)
        self.closing = False
        self.zk.add_listener(self.on_state)

    def on_state(self, state):
        if self.closing:
            return
        msg = {KazooState.SUSPENDED: "связь с ZooKeeper потеряна (SUSPENDED)",
               KazooState.LOST: "сессия ZooKeeper истекла (LOST): эфемерные узлы удалены",
               KazooState.CONNECTED: "подключён к ZooKeeper (CONNECTED)"}[state]
        self.log(msg)

    def retry(self, fn, *a, **kw):
        return self.zk.retry(fn, *a, **kw)

    # ---------- выбор лидера ----------
    def elect(self):
        el = f"{self.root}/election"
        me = self.zk.create(f"{el}/n_", self.id.encode(), ephemeral=True, sequence=True)
        my = me.rsplit("/", 1)[1]
        self.log(f"зарегистрировался в выборах: {me}, session=0x{self.zk.client_id[0]:x}, timeout={SESSION_TIMEOUT:.0f}s")
        waited = time.time()
        while True:
            kids = sorted(self.zk.get_children(el))
            if kids[0] == my:
                break
            prev = kids[kids.index(my) - 1]
            ev = threading.Event()
            try:
                owner = self.zk.get(f"{el}/{prev}", watch=lambda e: ev.set())[0].decode()
            except NoNodeError:
                continue
            self.log(f"запасной: лидер {owner} ({prev}), жду исчезновения его узла")
            ev.wait()
            self.log(f"узел {prev} исчез - перевыборы")
        data, st = self.zk.get(f"{self.root}/epoch")
        self.epoch = int(data) + 1
        st = self.zk.set(f"{self.root}/epoch", str(self.epoch).encode(), version=st.version)
        self.epoch_version = st.version
        self.log(f"СТАЛ ЛИДЕРОМ, эпоха {self.epoch} (версия узла epoch {self.epoch_version}), "
                 f"ожидал {time.time()-waited:.2f}s")

    # ---------- расчёт с чекпоинтами ----------
    def work(self):
        size = self.s3.head_object(Bucket=BUCKET, Key=CLEAN_KEY)["ContentLength"]
        tasks = [(i, s, min(s + self.chunk, size)) for i, s in enumerate(range(0, size, self.chunk))]
        cp = f"{self.root}/checkpoint"
        done = set(self.retry(self.zk.get_children, cp))
        self.log(f"объект {CLEAN_KEY}: {size:,} байт, {len(tasks)} чанков, в чекпоинте уже {len(done)}")
        for i, s, e in tasks:
            node = f"chunk_{i:04d}"
            if node in done or self.retry(self.zk.exists, f"{cp}/{node}"):
                who = self.retry(self.zk.get, f"{cp}/{node}")[0].decode()
                self.log(f"чанк {i+1}/{len(tasks)} уже в чекпоинте ({who}) - пропуск")
                continue
            t = time.time()
            part = map_chunk(self.s3, s, e, size)
            key = f"hw4/{self.run}/partial/{node}.json"
            self.s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(part, ensure_ascii=False).encode())
            try:
                self.retry(self.zk.create, f"{cp}/{node}", f"{self.id} epoch={self.epoch}".encode())
            except NodeExistsError:
                pass
            self.log(f"чанк {i+1}/{len(tasks)} обработан: lines={part['lines']} variant={part['variant']} "
                     f"-> чекпоинт {node} ({time.time()-t:.2f}s)")
            time.sleep(self.delay)        # искусственное замедление, чтобы успеть приостановить лидера
        # reduce: частичные результаты всех чанков (своих и чужих) из чекпоинта
        total = [Counter(), Counter(), Counter()]
        lines = variant = 0
        for i, _, _ in tasks:
            part = json.loads(self.s3.get_object(Bucket=BUCKET, Key=f"hw4/{self.run}/partial/chunk_{i:04d}.json")["Body"].read())
            lines += part["lines"]; variant += part["variant"]
            for k in range(3):
                total[k].update(part["cnt"][k])
        self.log(f"reduce завершён: lines={lines:,} variant_vacancies={variant:,}")
        keys, control = [], {"lines": lines, "variant": variant}
        for k in range(3):
            body, items = to_csv(total[k])
            key = f"hw4/{self.run}/result_{self.id}_e{self.epoch}_k{k+1}.csv"
            self.s3.put_object(Bucket=BUCKET, Key=key, Body=body)
            ref = self.s3.get_object(Bucket=BUCKET, Key=f"{RESULT_PREFIX}mr_k{k+1}.csv")["Body"].read()
            same = "СОВПАЛО с ДЗ 3" if ref == body else "НЕ СОВПАЛО с ДЗ 3"
            self.log(f"k={k+1}: distinct={len(items):,} total_occurrences={sum(total[k].values()):,} "
                     f"top={items[:3]} | сверка с results/mr_k{k+1}.csv: {same}")
            keys.append(key)
            control[f"k{k+1}"] = len(items)
        return keys, control

    # ---------- публикация результата ----------
    def publish(self, keys, control):
        ptr = f"{self.root}/result"
        data = json.dumps({"owner": self.id, "epoch": self.epoch, "keys": keys, "control": control,
                           "ts": datetime.now().isoformat(timespec="milliseconds")}, ensure_ascii=False).encode()
        while True:
            try:
                if not FENCING:
                    st = self.zk.set(ptr, data)
                    self.log(f"записал результат: {ptr} -> версия {st.version} (без проверки эпохи)")
                    return
                tx = self.zk.transaction()
                tx.check(f"{self.root}/epoch", version=self.epoch_version)
                tx.set_data(ptr, data)
                res = tx.commit()
                break
            except (ConnectionLoss, SessionExpiredError) as e:
                self.log(f"публикация: {type(e).__name__}, повтор")
                time.sleep(0.5)
        err = [r for r in res if isinstance(r, Exception)]
        if err:
            cur, st = self.zk.get(f"{self.root}/epoch")
            self.log(f"ЗАПИСЬ ОТКЛОНЕНА: {type(err[0]).__name__} - моя эпоха {self.epoch} "
                     f"(версия {self.epoch_version}), текущая эпоха {cur.decode()} (версия {st.version})")
        else:
            self.log(f"записал результат: {ptr} -> версия {res[1].version} (эпоха {self.epoch} проверена)")

    def main(self):
        self.log(f"старт pid={os.getpid()} run={self.run} FENCING={int(FENCING)} zk={ZK_HOSTS}")
        self.zk.start()
        for p in ("election", "checkpoint"):
            self.zk.ensure_path(f"{self.root}/{p}")
        for p, init in (("epoch", b"0"), ("result", b"")):
            try:
                self.zk.create(f"{self.root}/{p}", init)
            except NodeExistsError:
                pass
        self.elect()
        keys, control = self.work()
        self.publish(keys, control)
        data, st = self.retry(self.zk.get, f"{self.root}/result")
        self.log(f"указатель {self.root}/result: version={st.version} owner={json.loads(data)['owner']}")
        self.closing = True
        self.zk.stop()
        self.log("сессия закрыта, завершение")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--chunk-mb", type=int, default=4)
    ap.add_argument("--delay", type=float, default=1.0)
    a = ap.parse_args()
    Coordinator(a.id, a.run, a.chunk_mb, a.delay).main()
