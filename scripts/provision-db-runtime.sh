#!/bin/sh
# Cria/atualiza o papel usado pela aplicação sem expor a credencial
# administrativa ao contêiner web. O mesmo script roda no Compose local, no
# VPS e no banco efêmero da suíte.

set -eu

: "${DB_HOST:?DB_HOST ausente}"
: "${DB_PORT:?DB_PORT ausente}"
: "${DB_NAME:?DB_NAME ausente}"
: "${DB_ADMIN_USER:?DB_ADMIN_USER ausente}"
: "${DB_ADMIN_PASSWORD_FILE:?DB_ADMIN_PASSWORD_FILE ausente}"
: "${DB_APP_USER:?DB_APP_USER ausente}"
: "${DB_APP_PASSWORD_FILE:?DB_APP_PASSWORD_FILE ausente}"

read_secret() {
    arquivo=$1
    [ -r "$arquivo" ] || {
        echo "segredo ausente ou ilegível: $arquivo" >&2
        exit 1
    }
    valor=$(tr -d '\r\n' < "$arquivo")
    [ -n "$valor" ] || {
        echo "segredo vazio: $arquivo" >&2
        exit 1
    }
    printf '%s' "$valor"
}

admin_password=$(read_secret "$DB_ADMIN_PASSWORD_FILE")
app_password=$(read_secret "$DB_APP_PASSWORD_FILE")

# A senha é escrita em arquivo temporário no tmpfs, não na linha de comando.
# O escape cobre senhas manuais além das geradas pelo provisionamento padrão.
sql_file=$(mktemp /tmp/provision-db.XXXXXX)
trap 'rm -f "$sql_file"' EXIT HUP INT TERM
escaped_app_password=$(printf '%s' "$app_password" | sed "s/'/''/g")
cat > "$sql_file" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DB_APP_USER') THEN
        CREATE ROLE "$DB_APP_USER" LOGIN;
    END IF;
END
\$\$;
ALTER ROLE "$DB_APP_USER"
    LOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS
    PASSWORD '$escaped_app_password';
GRANT CONNECT ON DATABASE "$DB_NAME" TO "$DB_APP_USER";
REVOKE CREATE ON DATABASE "$DB_NAME" FROM "$DB_APP_USER";
GRANT USAGE ON SCHEMA public TO "$DB_APP_USER";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "$DB_APP_USER";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "$DB_APP_USER";
REVOKE CREATE ON SCHEMA public FROM "$DB_APP_USER";
ALTER DEFAULT PRIVILEGES FOR ROLE "$DB_ADMIN_USER" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "$DB_APP_USER";
ALTER DEFAULT PRIVILEGES FOR ROLE "$DB_ADMIN_USER" IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO "$DB_APP_USER";
SQL

PGPASSWORD=$admin_password \
    psql --host="$DB_HOST" --port="$DB_PORT" --username="$DB_ADMIN_USER" \
    --dbname="$DB_NAME" --file="$sql_file" --set=ON_ERROR_STOP=1 >/dev/null

echo "papel de runtime provisionado: $DB_APP_USER"
