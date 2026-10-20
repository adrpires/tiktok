"""Abre o TikTok num navegador visível e grava a sessão depois do login."""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DESTINO = Path(__file__).resolve().parent / "TIKTOK_STORAGE_STATE.json"


def _esta_logado(context) -> bool:
    return any(
        cookie.get("name") == "sessionid" and cookie.get("value")
        for cookie in context.cookies()
    )


def capturar_sessao() -> None:
    print(f"A sessão será gravada em {DESTINO}")
    print("Entre na sua conta. Deixe o navegador aberto. Ele fecha sozinho depois do login.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")
        while True:
            try:
                if page.is_closed() or not browser.is_connected():
                    print("O navegador foi fechado antes do login. Nenhum arquivo foi gravado.")
                    return
                if _esta_logado(context):
                    context.storage_state(path=str(DESTINO))
                    print(f"Login encontrado. Arquivo gravado em {DESTINO}")
                    browser.close()
                    return
            except Exception as exc:
                print(f"Não foi possível gravar a sessão: {exc}")
                return
            time.sleep(1)


if __name__ == "__main__":
    capturar_sessao()
