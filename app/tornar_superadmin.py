"""Script de migração pontual -- adiciona 'superadm' ao CHECK da coluna
hierarquia.hierarquia (bancos criados antes dessa mudança só aceitavam
'cliente'/'adm') e promove um usuário específico a SUPERADM.

Só existe um SUPERADM: promovido uma vez, direto no banco, igual o primeiro
admin é criado via tornar_admin.py -- nenhuma rota HTTP faz isso, por design.
SUPERADM tem acesso igual a admin (admin_required aceita os dois), mas nunca
pode ser rebaixado a cliente por ninguém (ver hierarquia_rebaixar em
admin.py) -- só rodando este script direto no banco de novo, manualmente.

Uso: python tornar_superadmin.py email@exemplo.com"""
from __future__ import annotations

import sys

import db


def tornar_superadmin(email: str) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario FROM login_usuario WHERE email = %s", (email,))
            usuario = cur.fetchone()
            if not usuario:
                print(f"Nenhum usuário encontrado com o e-mail {email}.")
                return

            cur.execute("ALTER TABLE hierarquia DROP CONSTRAINT IF EXISTS hierarquia_hierarquia_check")
            cur.execute(
                "ALTER TABLE hierarquia ADD CONSTRAINT hierarquia_hierarquia_check "
                "CHECK (hierarquia IN ('cliente', 'adm', 'superadm'))"
            )
            cur.execute(
                "UPDATE hierarquia SET hierarquia = 'superadm' WHERE id_usuario = %s",
                (usuario["id_usuario"],),
            )
    print(f"Constraint atualizada. Usuário {email} agora é SUPERADM.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python tornar_superadmin.py email@exemplo.com")
        sys.exit(1)
    tornar_superadmin(sys.argv[1].strip().lower())
