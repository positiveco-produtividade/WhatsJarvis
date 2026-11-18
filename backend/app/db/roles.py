"""Confere o papel do banco com que o app conecta (porta-fechada.2, PA-12, D2).

Em producao o app deve usar `jarvis_app`, que so le e grava dados. `role_problems` lista o
que torna o papel atual improprio; o lifespan recusa subir se a lista nao estiver vazia.
As mensagens citam o nome do papel e os motivos, nunca a URL nem a senha.
"""

from typing import Any

JARVIS_SCHEMAS = ("access", "chat", "ops", "content")

_CHECK = """
SELECT current_user::text AS role,
       r.rolsuper AS superuser,
       d.datdba = r.oid AS db_owner,
       EXISTS (
         SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = ANY($1::text[]) AND c.relowner = r.oid
       ) OR EXISTS (
         SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = ANY($1::text[]) AND p.proowner = r.oid
       ) AS owns_objects,
       EXISTS (
         SELECT 1 FROM unnest($1::text[]) AS s(name)
         WHERE has_schema_privilege(r.oid, s.name, 'CREATE')
       ) AS can_create,
       d.datdba <> r.oid AND pg_has_role(r.oid, d.datdba, 'MEMBER') AS owner_member
FROM pg_roles r, pg_database d
WHERE r.rolname = current_user AND d.datname = current_database()
"""

_REASONS = (
    ("superuser", "superusuario"),
    ("db_owner", "dono do banco"),
    ("owns_objects", "dono de tabelas ou funcoes"),
    ("can_create", "pode criar objetos nos schemas do Jarvis"),
    ("owner_member", "membro do papel dono do banco"),
)


async def role_problems(conn: Any) -> tuple[str, list[str]]:
    """Devolve (papel atual, motivos). Lista vazia = papel adequado ao app em producao."""
    row = await conn.fetchrow(_CHECK, list(JARVIS_SCHEMAS))
    return str(row["role"]), [text for key, text in _REASONS if row[key]]


async def assert_app_role(conn: Any) -> None:
    """Levanta RuntimeError se o papel atual nao puder ser o do app em producao."""
    role, problems = await role_problems(conn)
    if problems:
        raise RuntimeError(
            f"papel do banco {role!r} nao pode ser usado pelo app em producao: "
            f"{', '.join(problems)}. Use jarvis_app em DATABASE_URL e o dono so em "
            "MIGRATION_DATABASE_URL (deploy/RUNBOOK.md)"
        )
