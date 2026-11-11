"""A trilha de auditoria aceita inserção; alterar e apagar ficam com o administrador.

O papel da aplicação (`investimentos_app`) tinha UPDATE e DELETE em
`audit_log`: o `db-provision` concede DML em todas as tabelas a cada subida,
então um REVOKE não duraria. Nenhum código da aplicação nem da CLI altera ou
apaga a trilha (`accounts/auditoria.py` só insere; `cli.py` só lê), mas um
defeito, ou quem tomasse a sessão da aplicação, podia apagar o próprio rastro.

O gatilho recusa UPDATE e DELETE de quem não é superusuário. Uma exceção: a
chave `user_id` é `ON DELETE SET NULL`, e apagar um usuário anula a referência
nos registros dele. Essa alteração, e só ela, passa; `entity`, `action`,
`details` e o resto continuam intocáveis. (Na prática a ação referencial roda
com o dono da tabela, que é superusuário, e o gatilho a deixaria passar de
qualquer jeito; a exceção explícita não depende disso.)

O papel administrativo (o do `migrate`, do backup e da suíte) é superusuário e
não é afetado: expurgo e correção da trilha continuam possíveis, por ele.

Idempotente: `CREATE OR REPLACE` e `DROP TRIGGER IF EXISTS`.
"""

from __future__ import annotations

from alembic import op

revision = "20261111_0044"
down_revision = "20261111_0043"
branch_labels = None
depends_on = None

FUNCAO = """
CREATE OR REPLACE FUNCTION public.audit_log_somente_insercao() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.user_id IS NULL
            AND to_jsonb(NEW) - 'user_id' = to_jsonb(OLD) - 'user_id' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_log aceita apenas insercao (papel %)', current_user
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

GATILHO = """
CREATE TRIGGER audit_log_somente_insercao
    BEFORE UPDATE OR DELETE ON public.audit_log
    FOR EACH ROW EXECUTE FUNCTION public.audit_log_somente_insercao()
"""


def upgrade() -> None:
    op.execute(FUNCAO)
    op.execute("DROP TRIGGER IF EXISTS audit_log_somente_insercao ON public.audit_log")
    op.execute(GATILHO)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_somente_insercao ON public.audit_log")
    op.execute("DROP FUNCTION IF EXISTS public.audit_log_somente_insercao()")
