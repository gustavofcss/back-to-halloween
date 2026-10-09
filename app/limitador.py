"""Limite de requisições por origem, em memória, por processo.

O site não tinha limite nenhum: dava pra martelar o login (senha mínima de 6
caracteres), disparar e-mail de cadastro/recuperação pra qualquer endereço até
estourar a cota do Brevo, e cada hash scrypt custa ~190 ms de CPU e ~32 MiB de RAM
(com o plano gratuito do Render, 8 threads anônimas bastavam pra travar o site).

É propositalmente simples: janela deslizante por (escopo, IP) na memória do worker.
Com 2 workers o limite efetivo é o dobro, e reiniciar o processo zera a conta --
suficiente contra martelada, sem depender de banco nem de dependência nova.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from functools import wraps

from flask import render_template, request

_eventos: dict[str, deque] = {}
_trava = threading.Lock()
_MAX_CHAVES = 5000


def ip_do_cliente() -> str:
    """IP de quem chamou. Atrás do Cloudflare/Render o `remote_addr` é o do proxy:
    `CF-Connecting-IP` (posto pelo Cloudflare) e depois `X-Forwarded-For`."""
    return (
        request.headers.get("CF-Connecting-IP")
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or request.remote_addr
        or "?"
    )


def estourou(chave: str, maximo: int, janela: float) -> bool:
    """Registra uma requisição e diz se passou do limite nos últimos `janela` s."""
    agora = time.monotonic()
    with _trava:
        fila = _eventos.get(chave)
        if fila is None:
            if len(_eventos) >= _MAX_CHAVES:
                # limpeza: some quem já não tem nada dentro de nenhuma janela razoável
                for k in [k for k, f in _eventos.items() if not f or agora - f[-1] > 3600]:
                    del _eventos[k]
            fila = _eventos[chave] = deque()
        while fila and agora - fila[0] > janela:
            fila.popleft()
        if len(fila) >= maximo:
            return True
        fila.append(agora)
        return False


def limpar() -> None:
    with _trava:
        _eventos.clear()


def limitar(escopo: str, maximo: int, janela: float = 60.0):
    """Decorator: no máximo `maximo` POSTs por `janela` s por IP nesse `escopo`
    (GET nunca é limitado). Passou: página 429."""

    def deco(view):
        @wraps(view)
        def envolvida(*args, **kwargs):
            if request.method == "POST" and estourou(f"{escopo}:{ip_do_cliente()}", maximo, janela):
                return render_template("limite_requisicoes.html"), 429
            return view(*args, **kwargs)

        return envolvida

    return deco
