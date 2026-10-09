"""Script auxiliar de bootstrap -- marca um usuário como admin direto no banco.

Uso: python tornar_admin.py email@exemplo.com

Só serve pra criar o primeiro admin (nenhuma rota HTTP faz isso, por design --
promover/rebaixar pela UI exige admin_required, que travaria sem já existir
um admin)."""
from __future__ import annotations

import sys

import db


def tornar_admin(email: str) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario FROM login_usuario WHERE email = %s", (email,))
            usuario = cur.fetchone()
            if not usuario:
                print(f"Nenhum usuário encontrado com o e-mail {email}.")
                return
            cur.execute(
                "UPDATE hierarquia SET hierarquia = 'adm' WHERE id_usuario = %s",
                (usuario["id_usuario"],),
            )
            if cur.rowcount == 0:
                print(f"O usuário {email} existe, mas não tem linha na tabela hierarquia; nada foi alterado.")
                return
    print(f"Usuário {email} agora é admin.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python tornar_admin.py email@exemplo.com")
        sys.exit(1)
    tornar_admin(sys.argv[1].strip().lower())
