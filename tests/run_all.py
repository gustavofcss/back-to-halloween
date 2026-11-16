"""Roda todas as suítes de teste do projeto e mostra um resumo.

Uso, a partir da raiz do repositório:

    python tests/run_all.py

Cada arquivo `test_*.py` é um script independente que recebe a raiz do projeto
como argumento e imprime uma linha por verificação (`OK ...` ou `FALHOU ...`).
Não precisam de banco de dados: o acesso ao Postgres e às APIs externas
(Mercado Pago, Brevo, Google) é simulado dentro de cada teste.

Requer as dependências de `requirements.txt` instaladas.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TESTES = sorted(Path(__file__).resolve().parent.glob("test_*.py"))


def rodar(teste: Path) -> tuple[int, int, list[str]]:
    ambiente = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(teste), str(RAIZ)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=ambiente, cwd=str(RAIZ),
    )
    linhas = proc.stdout.splitlines()
    ok = sum(1 for linha in linhas if linha.startswith("OK"))
    falhas = [linha for linha in linhas if linha.startswith("FALHOU")]
    if any(linha.startswith("PULADO") for linha in linhas):
        return -1, 0, [linha for linha in linhas if linha.startswith("PULADO")]
    if proc.returncode != 0 and not falhas:
        falhas.append("o script terminou com erro (código %d)" % proc.returncode)
    return ok, len(falhas), falhas


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    total_ok = total_falhas = pulados = 0
    for teste in TESTES:
        ok, falhas, detalhes = rodar(teste)
        if ok == -1:
            pulados += 1
            print(f"pulado  {teste.name:45} {detalhes[0]}")
            continue
        total_ok += ok
        total_falhas += falhas
        situacao = "ok" if not falhas else "FALHOU"
        print(f"{situacao:7} {teste.name:45} {ok} verificações")
        for linha in detalhes:
            print("        " + linha)
    print(f"\n{total_ok} verificações passaram, {total_falhas} falharam.")
    return 1 if total_falhas else 0


if __name__ == "__main__":
    sys.exit(main())
