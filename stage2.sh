#!/usr/bin/env bash
# Этап 2: роли серверов, потеря одного и двух серверов, эфемерный узел. Вывод -> outputs/stage2.txt
cd "$(dirname "$0")"
exec > >(tee outputs/stage2.txt) 2>&1
status() { for i in 1 2 3; do echo "--- zk$i: zkServer.sh status"; docker exec zk$i zkServer.sh status 2>&1 | grep -E 'Mode|Error|port found' || echo "(контейнер zk$i остановлен)"; done; }
cli() { local s=$1; shift; docker exec zk$s zkCli.sh -server localhost:218$s "$@" 2>&1 | grep -vE '^(Connecting|Welcome|JLine|WATCHER|WatchedEvent|$)| (INFO|DEBUG) |Exiting JVM'; }
wait_roles() { for t in $(seq 1 30); do n=0; for i in 1 2 3; do docker exec zk$i zkServer.sh status 2>&1 | grep -q Mode && n=$((n+1)); done; [ $n = 3 ] && return; sleep 1; done; }

echo "### $(date '+%F %T') запуск ансамбля: docker compose up -d (внутри: zkServer.sh start-foreground)"
docker compose up -d 2>&1 | tail -3; wait_roles
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
echo; echo "### роли серверов"; status
LEADER=$(for i in 1 2 3; do docker exec zk$i zkServer.sh status 2>&1 | grep -q leader && echo $i; done)
echo; echo "### mntr лидера zk$LEADER"; docker exec zk$LEADER bash -c "echo mntr | nc localhost 218$LEADER" | grep -E 'server_state|synced_followers|znode_count'

F1=$(for i in 1 2 3; do [ $i != $LEADER ] && echo $i; done | head -1)
echo; echo "### $(date '+%T') останавливаю один сервер: docker stop zk$F1"; docker stop zk$F1 >/dev/null; sleep 4
status
ALIVE=$(for i in 1 2 3; do [ $i != $F1 ] && echo $i; done | head -1)
echo "--- запись и чтение через zk$ALIVE при 2 из 3 серверов"
cli $ALIVE create /stage2-one-down "2 of 3 alive"
cli $ALIVE get /stage2-one-down

F2=$ALIVE; LAST=$(for i in 1 2 3; do [ $i != $F1 ] && [ $i != $F2 ] && echo $i; done)
echo; echo "### $(date '+%T') останавливаю второй: docker stop zk$F2 (остался только zk$LAST)"; docker stop zk$F2 >/dev/null; sleep 6
status
echo "--- ruok/srvr у zk$LAST"; docker exec zk$LAST bash -c "echo ruok | nc localhost 218$LAST; echo; echo srvr | nc localhost 218$LAST"
echo; echo "--- попытка клиента через zk$LAST (timeout 15 s)"
docker exec zk$LAST timeout 15 zkCli.sh -server localhost:218$LAST ls / 2>&1 | grep -oE 'KeeperErrorCode = [A-Za-z]+.*|Unable to read additional data from server sessionid 0x0|Session establishment|state:CONNECTING' | sort | uniq -c
echo "(клиент не получил ответ: без кворума 2 из 3 сервер не обслуживает запросы)"

echo; echo "### $(date '+%T') запускаю оба снова: docker start zk$F1 zk$F2"; docker start zk$F1 zk$F2 >/dev/null; wait_roles
status
cli 1 get /stage2-one-down
cli 1 delete /stage2-one-down

echo; echo "### эфемерный узел: окно 2 держит сессию zkCli, окно 1 проверяет"
( echo "create -e /ephemeral-demo from-window-2"; echo "stat /ephemeral-demo"; sleep 12; echo "quit" ) \
  | docker exec -i zk2 zkCli.sh -server localhost:2182 > outputs/stage2_window2.txt 2>&1 &
W2=$!; sleep 7
echo "--- $(date '+%T') окно 1, пока окно 2 открыто: ls / и stat"
cli 1 ls /
cli 1 stat /ephemeral-demo | grep -E 'ephemeralOwner|dataLength'
wait $W2
echo "--- $(date '+%T') окно 2 закрыто (quit). Вывод окна 2:"; grep -E 'Created|ephemeralOwner|quit' outputs/stage2_window2.txt
echo "--- окно 1 после закрытия: ls / и get"
cli 1 ls /
cli 1 get /ephemeral-demo
