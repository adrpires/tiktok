"""Agenda o fluxo completo três vezes ao dia e publica no TikTok."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import schedule
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from downloader import download_backgrounds
from script_generator import VideoContext, context_path, generate_script, resolve_context
from video_editor import _spoken_text, edit_video

RUN_TIMES = ("09:00", "15:00", "21:00")
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
_POST_BUTTON = re.compile(r"^(post|postar|publicar)( vídeo| video)?$", re.IGNORECASE)
_POST_READY_MS = 180_000
LOGGER = logging.getLogger(__name__)
_state_lock = threading.Lock()
_logs: list[str] = []
_state: dict[str, object] = {
    "executando": False,
    "ultimo": {"ok": None, "mensagem": "Nenhum vídeo nesta sessão.", "quando": ""},
}


class _RingHandler(logging.Handler):
    """Guarda as últimas linhas de log para a página de administração."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("tiktok.admin"):
            return
        try:
            line = self.format(record)
        except Exception:
            return
        if len(line) > 2000:
            line = line[:2000] + "…"
        with _state_lock:
            _logs.append(line)
            del _logs[:-40]


def run_flow(source: VideoContext | str | None = None) -> Path:
    """Gera o roteiro, baixa os fundos, monta o vídeo e envia ao TikTok.

    Se ``CONTEXT_PATH`` aponta para um JSON, esse arquivo é lido de novo
    nesta execução. Um tema em texto só entra quando o arquivo não existe.
    """
    context = resolve_context(source)
    LOGGER.info("Contexto: %s", context.texto().replace("\n", " | "))
    script = generate_script(context)
    backgrounds = download_backgrounds(script.palavras_chave)
    output = Path(tempfile.mkdtemp(prefix="tiktok-final-")) / "tiktok.mp4"
    try:
        edit_video(backgrounds, script, output)
        upload_to_tiktok(output, _spoken_text(script.narração), script.hashtags)
    finally:
        shutil.rmtree(backgrounds.directory, ignore_errors=True)
    return output


def upload_to_tiktok(
    video: Path,
    caption: str,
    hashtags: tuple[str, ...] = (),
    *,
    upload_url: str = TIKTOK_UPLOAD_URL,
) -> None:
    """Abre o Chromium oculto, envia o MP4 e publica as hashtags no comentário.

    A sessão autenticada vem de ``TIKTOK_STORAGE_STATE``, um arquivo
    ``storage_state`` exportado pelo Playwright depois do login.
    """
    if not video.is_file():
        raise FileNotFoundError(f"Vídeo não encontrado: {video}")
    storage_state = os.environ.get("TIKTOK_STORAGE_STATE", "").strip()
    if storage_state and not Path(storage_state).is_file():
        raise FileNotFoundError(f"Sessão do TikTok não encontrada: {storage_state}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context_options: dict[str, object] = {"viewport": {"width": 1440, "height": 900}}
        if storage_state:
            context_options["storage_state"] = storage_state
        context = browser.new_context(**context_options)
        page = context.new_page()
        try:
            page.goto(upload_url, wait_until="domcontentloaded", timeout=60_000)
            if _login_required(page):
                raise RuntimeError(
                    "A sessão do TikTok expirou. Entre de novo e atualize TIKTOK_STORAGE_STATE."
                )
            page.locator('input[type="file"]').first.wait_for(
                state="attached", timeout=30_000
            )
            page.locator('input[type="file"]').first.set_input_files(str(video.resolve()))
            _fill_caption(page, caption)
            video_id = _watch_video_id(page)
            _click_post(page)
            if hashtags:
                _post_comment(page, hashtags, video_id["id"])
        finally:
            context.close()
            browser.close()


def build_scheduler(theme: str = "") -> schedule.Scheduler:
    """Cria a agenda diária das 09:00, 15:00 e 21:00."""
    scheduler = schedule.Scheduler()
    for moment in RUN_TIMES:
        scheduler.every().day.at(moment).do(_safe_run, theme)
    return scheduler


def run_scheduler(theme: str) -> None:
    """Espera os três horários e executa o fluxo em cada um."""
    _require_source(theme)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    _install_log_ring()
    from admin import start_admin

    start_admin()
    path = context_path()
    if path is not None:
        LOGGER.info(
            "Cada horário lê o contexto em %s. Sem esse arquivo, o tema fica: %s",
            path,
            theme.strip() or "(nenhum)",
        )
    else:
        LOGGER.info("Tema fixo: %s", theme.strip())
    scheduler = build_scheduler(theme.strip())
    LOGGER.info("Fluxo agendado para %s.", ", ".join(RUN_TIMES))
    while True:
        scheduler.run_pending()
        time.sleep(1)


def run_once(theme: str) -> Path:
    """Gera e publica um vídeo agora, com o contexto atual."""
    _require_source(theme)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    output = run_flow(theme or None)
    LOGGER.info("Vídeo enviado: %s", output)
    return output


def _require_source(theme: str) -> None:
    path = context_path()
    if theme.strip() or os.environ.get("TIKTOK_THEME", "").strip():
        return
    if path is not None and path.is_file():
        return
    raise ValueError("Informe um contexto ou um tema.")


def proximo_horario(now: datetime | None = None) -> str:
    """Devolve o próximo entre 09:00, 15:00 e 21:00."""
    current = now or datetime.now()
    upcoming: list[tuple[datetime, str]] = []
    for moment in RUN_TIMES:
        hour, minute = (int(part) for part in moment.split(":"))
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(days=1)
        upcoming.append((candidate, moment))
    return min(upcoming)[1]


def snapshot() -> dict[str, object]:
    """Estado visto pela página: se há vídeo em andamento, o último resultado e o log."""
    with _state_lock:
        ultimo = _state["ultimo"]
        return {
            "executando": _state["executando"],
            "horarios": list(RUN_TIMES),
            "proximo": proximo_horario(),
            "ultimo": dict(ultimo) if isinstance(ultimo, dict) else {},
            "log": list(_logs),
        }


def start_background(theme: str = "") -> bool:
    """Dispara o fluxo numa thread. Devolve False se já houver um vídeo em andamento."""
    if not _mark_running():
        return False
    threading.Thread(target=_execute, args=(theme,), name="tiktok-flow", daemon=True).start()
    return True


def reset_run_state() -> None:
    """Limpa o estado em memória. Usado pelos testes."""
    with _state_lock:
        _state["executando"] = False
        _state["ultimo"] = {"ok": None, "mensagem": "Nenhum vídeo nesta sessão.", "quando": ""}
        _logs.clear()


def _install_log_ring() -> None:
    root = logging.getLogger()
    if any(isinstance(handler, _RingHandler) for handler in root.handlers):
        return
    handler = _RingHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(handler)


def _mark_running() -> bool:
    with _state_lock:
        if _state["executando"]:
            return False
        _state["executando"] = True
        return True


def _mark_finished(ok: bool, message: str) -> None:
    with _state_lock:
        _state["executando"] = False
        _state["ultimo"] = {
            "ok": ok,
            "mensagem": message,
            "quando": datetime.now().strftime("%d/%m %H:%M"),
        }


def _execute(theme: str) -> None:
    LOGGER.info("Geração iniciada.")
    try:
        output = run_flow(theme or None)
    except Exception as exc:
        LOGGER.exception("O fluxo do TikTok falhou.")
        _mark_finished(False, str(exc).strip()[:500] or exc.__class__.__name__)
        return
    LOGGER.info("Vídeo enviado: %s", output)
    _mark_finished(True, "Vídeo enviado.")


def _safe_run(theme: str) -> None:
    if not _mark_running():
        LOGGER.info("Já existe um vídeo em andamento. Este horário foi pulado.")
        return
    _execute(theme)


def _click_post(
    page: Page,
    *,
    visible_timeout: int = 45_000,
    ready_timeout: int = _POST_READY_MS,
) -> None:
    """Clica em Publicar quando o TikTok termina de receber o vídeo.

    O botão existe com o nome Post, Postar ou Publicar, e também com o
    marcador estável do Studio. Ele fica desativado enquanto o arquivo sobe.
    """
    button = page.locator('[data-e2e="post_video_button"]').or_(
        page.get_by_role("button", name=_POST_BUTTON)
    ).first
    LOGGER.info("Aguardando o TikTok liberar o botão de publicar.")
    try:
        button.wait_for(state="visible", timeout=visible_timeout)
    except PlaywrightTimeout as exc:
        raise _publish_failure(
            page,
            "O TikTok não mostrou o botão de publicar.",
        ) from exc
    try:
        button.click(timeout=ready_timeout)
    except PlaywrightTimeout as exc:
        raise _publish_failure(
            page,
            "O botão de publicar continuou desativado. O vídeo ainda estava em processamento.",
        ) from exc


def _publish_failure(page: Page, reason: str) -> RuntimeError:
    labels: list[str] = []
    try:
        for text in page.get_by_role("button").all_inner_texts():
            clean = " ".join(text.split())
            if clean and clean not in labels:
                labels.append(clean)
    except Exception:
        labels = []
    shown = ", ".join(labels[:6]) or "nenhum"
    return RuntimeError(f"{reason} Botões na tela: {shown}.")


def _post_comment(page: Page, hashtags: tuple[str, ...], video_id: str = "") -> None:
    """Publica as cinco hashtags no primeiro comentário do vídeo."""
    text = " ".join(hashtags)
    LOGGER.info("Comentário com hashtags: %s", text)
    box = page.locator('[data-e2e="comment-input"]').first
    try:
        box.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeout:
        _open_video(page, video_id)
        box = page.locator('[data-e2e="comment-input"]').first
        try:
            box.wait_for(state="visible", timeout=60_000)
        except PlaywrightTimeout as exc:
            raise RuntimeError(
                f"O vídeo foi publicado, mas a caixa de comentário não apareceu. Hashtags: {text}."
            ) from exc
    editor = box.locator('[contenteditable="true"]')
    target = editor.first if editor.count() else box
    target.click()
    target.fill(text)
    send = page.locator('[data-e2e="comment-post"]').first
    try:
        send.click(timeout=30_000)
    except PlaywrightTimeout as exc:
        raise RuntimeError(
            f"O vídeo foi publicado, mas o comentário não foi enviado. Hashtags: {text}."
        ) from exc
    LOGGER.info("Hashtags publicadas no comentário.")


def _open_video(page: Page, video_id: str) -> None:
    if "tiktokstudio/content" not in page.url:
        page.goto(
            "https://www.tiktok.com/tiktokstudio/content",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
    selector = f'a[href*="{video_id}"]' if video_id else 'a[href*="/video/"]'
    link = page.locator(selector).first
    link.wait_for(state="visible", timeout=60_000)
    link.click()
    page.wait_for_load_state("domcontentloaded")


def _watch_video_id(page: Page) -> dict[str, str]:
    found = {"id": ""}

    def remember(response) -> None:
        if found["id"]:
            return
        try:
            payload = response.json()
        except Exception:
            return
        item_id = _extract_video_id(payload)
        if item_id:
            found["id"] = item_id

    page.on("response", remember)
    return found


def _extract_video_id(payload: object, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(payload, dict):
        for key in ("item_id", "itemId", "aweme_id", "awemeId"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).isdigit() and len(str(value)) >= 8:
                return str(value)
        for value in payload.values():
            found = _extract_video_id(value, depth + 1)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload[:20]:
            found = _extract_video_id(value, depth + 1)
            if found:
                return found
    return ""


def _fill_caption(page: Page, caption: str) -> None:
    text = caption.strip()[:4000]
    editable = page.locator('[contenteditable="true"]').first
    try:
        editable.wait_for(state="visible", timeout=120_000)
    except PlaywrightTimeout:
        editable = page.get_by_role("textbox").first
    editable.fill(text)


def _login_required(page: Page) -> bool:
    url = page.url.lower()
    return "tiktok.com" in url and "login" in url


def main() -> None:
    args = sys.argv[1:]
    once = bool(args and args[0] == "--agora")
    if once:
        args = args[1:]
    theme = " ".join(args).strip() or os.environ.get("TIKTOK_THEME", "").strip()
    try:
        _require_source(theme)
    except ValueError:
        print(
            'Uso: python scheduler.py "tema do vídeo"\n'
            "     python scheduler.py --agora\n"
            "Ou defina CONTEXT_PATH com um JSON: tema, publico, tom, objetivo, detalhes.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if once:
        run_once(theme)
        return
    run_scheduler(theme)


if __name__ == "__main__":
    main()
