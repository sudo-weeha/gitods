#!/bin/sh
# PostgreSQL 容器首次初始化：创建应用专用非超级用户角色与数据库。
# 开发环境的测试建库走独立角色（CREATEDB），与此处生产角色分开。
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gitods_app') THEN
            CREATE ROLE gitods_app LOGIN PASSWORD '${GITODS_APP_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
        ELSE
            ALTER ROLE gitods_app WITH LOGIN PASSWORD '${GITODS_APP_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
        END IF;
    END
    \$\$;
    SELECT 'CREATE DATABASE gitods OWNER gitods_app'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'gitods')\gexec
    GRANT ALL PRIVILEGES ON DATABASE gitods TO gitods_app;
EOSQL
