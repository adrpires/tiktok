"""Abre o TikTok num navegador visível e grava a sessão depois do login."""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DESTINO = Path(__file__).resolve().parent / "TIKTOK_STORAGE_STATE.json"


def capturar_sessao() -> None:
    print("Abrindo o navegador. Entre na sua conta do TikTok.")
    print("Quando o seu perfil aparecer, feche a janela do navegador.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")
        while True:
            try:
                if page.is_closed() or not browser.is_connected():
                    break
            except Exception:
                break
            time.sleep(1)
        context.storage_state(path=str(DESTINO))
        print(f"Arquivo gravado em {DESTINO}")
        browser.close()


if __name__ == "__main__":
    capturar_sessao()
