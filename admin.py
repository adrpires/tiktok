"""Página para editar o contexto e publicar um vídeo sem usar o terminal."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from script_generator import context_from_mapping, context_path, load_context_file, save_context_file

LOGGER = logging.getLogger("tiktok.admin")
PAGE = Path(__file__).with_name("admin_page.html").read_text(encoding="utf-8")
_SESSION_SECONDS = 14 * 24 * 60 * 60
_MAX_BODY = 20_000
_FAILURE_WINDOW = 10 * 60
_FAILURE_LIMIT = 8


class AdminServer(ThreadingHTTPServer):
    allow_reuse_address = True
    password: str
    sessions: dict[str, float]
    session_lock: threading.Lock
    failures: dict[str, list[float]]


class AdminHandler(BaseHTTPRequestHandler):
    server: AdminServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/", "/index.html"}:
            self._html(PAGE)
            return
        if path == "/favicon.ico":
            self._empty(204)
            return
        if path == "/api/estado":
            if not self._authenticated():
                self._json(401, {"erro": "Entre com a senha."})
                return
            self._json(200, _estado())
            return
        self._json(404, {"erro": "Não encontrado."})

    def do_POST(self) -> None:
        try:
            self._raw_body = self._read_body_bytes()
            self._post()
        except (ValueError, json.JSONDecodeError) as exc:
            self.close_connection = True
            self._json(400, {"erro": str(exc) or "Pedido inválido."})
        except Exception:
            LOGGER.exception("Falha na página de administração.")
            self.close_connection = True
            self._json(500, {"erro": "Falha interna."})

    def log_message(self, fmt: str, *args: object) -> None:
        message = fmt % args
        if "/api/estado" in message and " 200 " in message:
            return
        LOGGER.info("%s - %s", self.address_string(), message)

    def _post(self) -> None:
        if not self._origin_ok():
            self._json(403, {"erro": "Origem recusada."})
            return
        path = self.path.split("?", 1)[0]
        if path == "/entrar":
            self._entrar()
            return
        if path == "/sair":
            self._sair()
            return
        if not self._authenticated():
            self._json(401, {"erro": "Entre com a senha."})
            return
        if path == "/api/contexto":
            context = context_from_mapping(self._read_json())
            destination = save_context_file(context)
            self._json(200, {"ok": True, "arquivo": str(destination), "contexto": _context_payload()})
            return
        if path == "/api/gerar":
            from scheduler import start_background

            if not start_background(""):
                self._json(409, {"erro": "Já existe um vídeo em andamento."})
                return
            self._json(202, {"ok": True})
            return
        self._json(404, {"erro": "Não encontrado."})

    def _entrar(self) -> None:
        ip = self.address_string()
        if _too_many_failures(self.server, ip):
            self._json(429, {"erro": "Muitas tentativas. Espere alguns minutos."})
            return
        payload = self._read_json()
        senha = payload.get("senha", "")
        if not isinstance(senha, str) or not _password_ok(senha, self.server.password):
            _note_failure(self.server, ip)
            time.sleep(0.4)
            self._json(401, {"erro": "Senha incorreta."})
            return
        with self.server.session_lock:
            self.server.failures.pop(ip, None)
            if len(self.server.sessions) > 100:
                oldest = min(self.server.sessions, key=self.server.sessions.get)
                self.server.sessions.pop(oldest, None)
            token = secrets.token_urlsafe(32)
            self.server.sessions[token] = time.time() + _SESSION_SECONDS
        self._empty(
            204,
            cookie=(
                f"sessao={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={_SESSION_SECONDS}"
            ),
        )

    def _sair(self) -> None:
        token = self._cookies().get("sessao", "")
        with self.server.session_lock:
            self.server.sessions.pop(token, None)
        self._empty(204, cookie="sessao=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")

    def _authenticated(self) -> bool:
        token = self._cookies().get("sessao", "")
        if not token:
            return False
        with self.server.session_lock:
            expires = self.server.sessions.get(token)
            if not expires or expires < time.time():
                self.server.sessions.pop(token, None)
                return False
            return True

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin in {f"http://{host}", f"https://{host}"}

    def _cookies(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            found[key.strip()] = value.strip()
        return found

    def _read_body_bytes(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Pedido inválido.") from exc
        if length < 0 or length > _MAX_BODY:
            raise ValueError("Pedido grande demais.")
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict:
        raw = self._raw_body
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("O pedido precisa ser um objeto JSON.")
        return data

    def _html(self, page: str) -> None:
        body = page.encode("utf-8")
        self.send_response(200)
        self._headers(len(body), "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers(len(body), "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, status: int, cookie: str | None = None) -> None:
        self.send_response(status)
        self._headers(0, "text/plain; charset=utf-8", cookie=cookie)
        self.end_headers()

    def _headers(self, length: int, content_type: str, cookie: str | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        if cookie:
            self.send_header("Set-Cookie", cookie)


def start_admin() -> bool:
    """Sobe a página em ADMIN_PORT se ADMIN_PASSWORD estiver definida."""
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password.strip():
        LOGGER.info(
            "Página de administração desligada. Defina ADMIN_PASSWORD para abrir no navegador."
        )
        return False
    raw_port = os.environ.get("ADMIN_PORT", "8080").strip() or "8080"
    try:
        port = int(raw_port)
    except ValueError:
        LOGGER.error("ADMIN_PORT inválida: %s", raw_port)
        return False
    host = os.environ.get("ADMIN_HOST", "0.0.0.0").strip() or "0.0.0.0"
    try:
        server = make_server(password, host, port)
    except OSError as exc:
        LOGGER.error("Não foi possível abrir a página na porta %s: %s", port, exc)
        return False
    threading.Thread(target=server.serve_forever, name="admin", daemon=True).start()
    LOGGER.info("Página de administração em http://%s:%s", host, port)
    return True


def make_server(password: str, host: str = "127.0.0.1", port: int = 0) -> AdminServer:
    server = AdminServer((host, port), AdminHandler)
    server.password = password
    server.sessions = {}
    server.session_lock = threading.Lock()
    server.failures = {}
    return server


def _estado() -> dict:
    from scheduler import snapshot

    state = snapshot()
    path = context_path()
    state["arquivo"] = str(path) if path is not None else ""
    state["contexto"] = _context_payload()
    return state


def _context_payload() -> dict:
    empty = {"tema": "", "publico": "", "tom": "", "objetivo": "", "detalhes": ""}
    path = context_path()
    if path is None:
        return {**empty, "erro": "CONTEXT_PATH não está definido."}
    if not path.is_file():
        return empty
    try:
        context = load_context_file(path)
    except ValueError as exc:
        return {**empty, "erro": str(exc)}
    return {
        "tema": context.tema,
        "publico": context.publico,
        "tom": context.tom,
        "objetivo": context.objetivo,
        "detalhes": context.detalhes,
    }


def _password_ok(provided: str, expected: str) -> bool:
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return secrets.compare_digest(left, right)


def _too_many_failures(server: AdminServer, ip: str) -> bool:
    now = time.time()
    with server.session_lock:
        recent = [moment for moment in server.failures.get(ip, []) if now - moment < _FAILURE_WINDOW]
        server.failures[ip] = recent
        return len(recent) >= _FAILURE_LIMIT


def _note_failure(server: AdminServer, ip: str) -> None:
    with server.session_lock:
        server.failures.setdefault(ip, []).append(time.time())
