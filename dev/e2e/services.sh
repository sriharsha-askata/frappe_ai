#!/bin/bash
# Start the e2e services (idempotent). Usage: services.sh
cd /var/tmp/e2e
if ! mariadb --defaults-file=mariadb.cnf -uroot -proot -e "select 1" >/dev/null 2>&1; then
  chown -R mysql:mysql mariadb run
  (setsid nohup mysqld_safe --defaults-file=/var/tmp/e2e/mariadb.cnf --user=mysql > mariadb.log 2>&1 &)
fi
for p in 11000 11001 11002; do
  redis-cli -p $p ping >/dev/null 2>&1 || (setsid nohup redis-server --port $p --save "" --appendonly no > redis-$p.log 2>&1 &)
done
if ! pg_isready -q -h 127.0.0.1 -p 5439; then
  su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/tmp/pgvt -o '-p 5439 -k /tmp -c listen_addresses=127.0.0.1' -l /var/tmp/pgvt/log -w start" >/dev/null 2>&1
fi
for i in $(seq 1 40); do mariadb --defaults-file=mariadb.cnf -uroot -proot -e "select 1" >/dev/null 2>&1 && break; sleep 1; done
echo "mariadb: $(mariadb -h127.0.0.1 -P3307 -uroot -proot -N -e 'select version()' 2>&1 | tail -1)"
echo "redis: $(for p in 11000 11001 11002; do redis-cli -p $p ping; done | tr '\n' ' ')"
echo "postgres: $(pg_isready -h 127.0.0.1 -p 5439)"
