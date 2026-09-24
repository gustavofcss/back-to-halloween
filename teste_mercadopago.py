"""Script ISOLADO pra testar a API de relatorio de liberacoes (Releases
Report) do Mercado Pago -- NAO faz parte do sistema principal
(pedidos.py/admin.py nem tocam aqui) e NAO cria/altera nenhum pagamento.
So gera/consulta um relatorio ja existente na conta e mostra o que vem
nele, pra descobrir se da pra identificar um Pix recebido direto (sem
Checkout) so pelos dados do relatorio -- ver
Horror_Night_Party_Pix_Mercado_Pago(1).md, secoes 9-12.

IMPORTANTE (achado real, 2026-09-24): a API descrita no MD original
(`/v1/account/settlement_report`, "Settlement Report") esta DESATIVADA --
o POST de criacao devolve 404 real (confirmado testando direto, nao so
lendo doc). A Mercado Pago substituiu por uma API nova, "Releases Report"
(`/v1/account/release_report*`), que e o que este script usa.

Uso:
    python teste_mercadopago.py [dias_pra_tras]

    dias_pra_tras (opcional, padrao 30): quantos dias pra tras a partir de
    agora o relatorio deve cobrir.
"""
from __future__ import annotations

import csv
import io
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent / "app"))
import config  # noqa: E402

BASE = "https://api.mercadopago.com/v1/account/release_report"
TIMEOUT = 30

# Status intermediarios observados num teste real (fila assincrona da MP):
# pending -> data-check-ready -> processing -> data-pending -> data-ready.
# Nenhum desses aparece na doc (que so menciona "processed") -- so o item
# aparecer no /list, com file_name preenchido, indica que ta pronto pra
# baixar de verdade (achado real, testado).


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.MERCADOPAGO_ACCESS_TOKEN}"}


def criar_relatorio(inicio: datetime, fim: datetime) -> dict:
    resp = requests.post(
        BASE,
        headers=_headers(),
        json={
            "begin_date": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": fim.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        timeout=TIMEOUT,
    )
    if not resp.ok:
        print(f"Erro ao criar relatorio ({resp.status_code}): {resp.text}")
    resp.raise_for_status()
    return resp.json()


def listar_relatorios() -> list[dict]:
    resp = requests.get(f"{BASE}/list", headers=_headers(), timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def baixar_relatorio(file_name: str) -> str:
    resp = requests.get(f"{BASE}/download/{file_name}", headers=_headers(), timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def analisar_csv(texto: str) -> None:
    print("\n--- Conteudo cru ---")
    print(texto)

    amostra = texto[:2000]
    try:
        delimitador = csv.Sniffer().sniff(amostra, delimiters=";,\t").delimiter
    except csv.Error:
        delimitador = ";"

    leitor = csv.DictReader(io.StringIO(texto), delimiter=delimitador)
    linhas = [linha for linha in leitor if any(v.strip() for v in linha.values() if v)]
    print(f"\n{len(linhas)} linha(s) de movimentacao real no periodo.")
    if not linhas:
        return

    print("\nColunas disponiveis:")
    for coluna in linhas[0].keys():
        print(f"  - {coluna}")

    print("\nLinhas:")
    for linha in linhas:
        print(dict(linha))


def main() -> None:
    if not config.MERCADOPAGO_ACCESS_TOKEN:
        print("MERCADOPAGO_ACCESS_TOKEN nao configurado no .env -- abortando.")
        return

    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(days=dias)

    print(f"1. Criando relatorio de {inicio.isoformat()} ate {fim.isoformat()}...")
    tarefa = criar_relatorio(inicio, fim)
    task_id = tarefa.get("id")
    print(f"   task id: {task_id}, status inicial: {tarefa.get('status')}")

    print("\n2. Aguardando o relatorio aparecer no /list (pode levar ~1min)...")
    file_name = None
    for tentativa in range(1, 13):
        time.sleep(10)
        relatorios = listar_relatorios()
        achado = next((r for r in relatorios if r.get("date_created") and True), None)
        # Pega sempre o mais recente da lista (ordem nao documentada,
        # assume-se o ultimo criado = o que acabamos de pedir).
        if relatorios:
            achado = sorted(relatorios, key=lambda r: r.get("date_created", ""))[-1]
            file_name = achado.get("file_name")
        print(f"   tentativa {tentativa}: {len(relatorios)} relatorio(s) no /list"
              + (f", ultimo file_name={file_name}" if file_name else ""))
        if file_name:
            break
    else:
        print("   Relatorio nao apareceu no /list a tempo -- tente rodar de novo.")
        return

    print(f"\n3. Baixando '{file_name}'...")
    csv_texto = baixar_relatorio(file_name)

    print("\n4. Analisando conteudo...")
    analisar_csv(csv_texto)


if __name__ == "__main__":
    main()
