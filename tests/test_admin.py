"""Página de administração do contexto e do disparo manual."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path

import admin
import scheduler
from script_generator import VideoContext, save_context_file


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._env = {
            "CONTEXT_PATH": os.environ.get("CONTEXT_PATH"),
            "ADMIN_PASSWORD": os.environ.get("ADMIN_PASSWORD"),
        }
        cls._directory = tempfile.TemporaryDirectory()
        cls.context_file = Path(cls._directory.name) / "contexto.json"
        os.environ["CONTEXT_PATH"] = str(cls.context_file)
        save_context_file(
            VideoContext(tema="rotina", publico="quem acorda cedo", tom="direto"),
            cls.context_file,
        )
        cls.server = admin.make_server("senha-teste")
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._directory.cleanup()
        for key, value in cls._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self) -> None:
        self.server.failures.clear()
        scheduler.reset_run_state()
        save_context_file(
            VideoContext(tema="rotina", publico="quem acorda cedo", tom="direto"),
            self.context_file,
        )
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def test_pagina_abre_e_exige_senha(self) -> None:
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Publicar agora", page)
        self.assertIn("Relatório", page)
        self.assertIn("Vídeo gerado", page)
        self.assertNotIn("senha-teste", page)
        status, payload = self.request("/api/estado")
        self.assertEqual(status, 401)
        self.assertEqual(payload["erro"], "Entre com a senha.")

    def test_senha_errada_e_depois_o_contexto_salvo(self) -> None:
        status, payload = self.request("/entrar", {"senha": "outra"})
        self.assertEqual(status, 401)
        status, _ = self.request("/entrar", {"senha": "senha-teste"})
        self.assertEqual(status, 204)
        status, payload = self.request("/api/estado")
        self.assertEqual(status, 200)
        self.assertEqual(payload["contexto"]["tema"], "rotina")
        self.assertEqual(payload["contexto"]["publico"], "quem acorda cedo")
        self.assertEqual(payload["horarios"], ["09:00", "15:00", "21:00"])
        self.assertIn(payload["proximo"], {"09:00", "15:00", "21:00"})

    def test_salvar_grava_o_json_e_a_leitura_seguinte_ve_o_texto(self) -> None:
        self.entrar()
        status, payload = self.request(
            "/api/contexto",
            {
                "tema": "água ao acordar",
                "publico": "quem trabalha cedo",
                "tom": "leve",
                "objetivo": "salvar o vídeo",
                "detalhes": "não citar marcas",
            },
        )
        self.assertEqual(status, 200)
        saved = json.loads(self.context_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["tema"], "água ao acordar")
        self.assertEqual(saved["detalhes"], "não citar marcas")
        self.assertEqual(payload["contexto"]["objetivo"], "salvar o vídeo")

    def test_tema_vazio_nao_grava(self) -> None:
        self.entrar()
        before = self.context_file.read_text(encoding="utf-8")
        status, payload = self.request("/api/contexto", {"tema": "  ", "publico": "alguém"})
        self.assertEqual(status, 400)
        self.assertIn("tema", payload["erro"])
        self.assertEqual(self.context_file.read_text(encoding="utf-8"), before)

    def test_publicar_dispara_o_fluxo_uma_vez(self) -> None:
        self.entrar()
        calls: list[str | None] = []

        def fake(source: str | None = None) -> Path:
            calls.append(source)
            time.sleep(0.8)
            return Path("/tmp/tiktok.mp4")

        original = scheduler.run_flow
        scheduler.run_flow = fake
        try:
            self.request(
                "/api/contexto",
                {"tema": "teste da página", "publico": "", "tom": "", "objetivo": "", "detalhes": ""},
            )
            status, _ = self.request("/api/gerar", {})
            self.assertEqual(status, 202)
            self._estado_na_mesma_conexao()
            status, again = self.request("/api/gerar", {})
            self.assertEqual(status, 409)
            self.assertIn("andamento", again["erro"])
            deadline = time.time() + 3
            while time.time() < deadline and scheduler.snapshot()["executando"]:
                time.sleep(0.05)
        finally:
            scheduler.run_flow = original
        self.assertEqual(calls, [None])
        ultimo = scheduler.snapshot()["ultimo"]
        self.assertIsInstance(ultimo, dict)
        self.assertTrue(ultimo["ok"])
        self.assertEqual(ultimo["mensagem"], "Vídeo enviado.")

    def test_muitas_senhas_erradas_bloqueiam(self) -> None:
        for _ in range(admin._FAILURE_LIMIT):
            admin._note_failure(self.server, "127.0.0.1")
        status, payload = self.request("/entrar", {"senha": "senha-teste"})
        self.assertEqual(status, 429)
        self.assertIn("Muitas tentativas", payload["erro"])

    def test_origem_de_outro_site_e_recusada(self) -> None:
        self.entrar()
        before = self.context_file.read_text(encoding="utf-8")
        status, payload = self.request("/api/contexto", {"tema": "x"}, origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertEqual(self.context_file.read_text(encoding="utf-8"), before)
        self.assertIn("Origem", payload["erro"])

    def test_relatorio_separa_video_publicado_e_erro(self) -> None:
        self.entrar()
        video = Path(self.context_file.parent) / "tiktok.mp4"
        video.write_bytes(b"video")
        scheduler._mark_running()
        scheduler._record_failure(
            RuntimeError("O vídeo foi publicado, mas o comentário não foi enviado."),
            video,
        )
        scheduler._mark_finished(False, "comentário")
        status, payload = self.request("/api/estado")
        self.assertEqual(status, 200)
        report = payload["relatorio"]
        self.assertTrue(report["gerado"])
        self.assertTrue(report["postado"])
        self.assertFalse(report["comentario"])
        self.assertIn("comentário", report["erro"])
        self.assertFalse(payload["executando"])
        self.assertFalse(payload["ultimo"]["ok"])

        scheduler.reset_run_state()
        scheduler._mark_running()
        scheduler._record_failure(RuntimeError("Incorrect API key"), None)
        report = scheduler.snapshot()["relatorio"]
        self.assertFalse(report["gerado"])
        self.assertIsNone(report["postado"])
        self.assertIsNone(report["comentario"])
        self.assertEqual(report["erro"], "Incorrect API key")

        scheduler.reset_run_state()
        scheduler._mark_running()
        scheduler._record_failure(
            RuntimeError("O TikTok não mostrou o botão de publicar. Botões na tela: Comentários."),
            video,
        )
        report = scheduler.snapshot()["relatorio"]
        self.assertTrue(report["gerado"])
        self.assertFalse(report["postado"])
        self.assertIsNone(report["comentario"])

    def test_proximo_horario(self) -> None:
        self.assertEqual(scheduler.proximo_horario(datetime(2026, 10, 21, 8, 0)), "09:00")
        self.assertEqual(scheduler.proximo_horario(datetime(2026, 10, 21, 9, 0)), "15:00")
        self.assertEqual(scheduler.proximo_horario(datetime(2026, 10, 21, 15, 30)), "21:00")
        self.assertEqual(scheduler.proximo_horario(datetime(2026, 10, 21, 22, 0)), "09:00")

    def test_sem_senha_a_pagina_nao_sobe(self) -> None:
        previous = os.environ.pop("ADMIN_PASSWORD", None)
        try:
            self.assertFalse(admin.start_admin())
        finally:
            if previous is not None:
                os.environ["ADMIN_PASSWORD"] = previous

    def _estado_na_mesma_conexao(self) -> None:
        cookie = ""
        for item in self.jar:
            if item.name == "sessao":
                cookie = f"sessao={item.value}"
        host, port = self.server.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=5)
        connection.request(
            "POST",
            "/api/gerar",
            body=b"{}",
            headers={"Content-Type": "application/json", "Cookie": cookie},
        )
        first = connection.getresponse()
        first.read()
        self.assertEqual(first.status, 409)
        connection.request("GET", "/api/estado", headers={"Cookie": cookie})
        second = connection.getresponse()
        body = second.read()
        self.assertEqual(second.status, 200, body)
        connection.close()

    def entrar(self) -> None:
        status, _ = self.request("/entrar", {"senha": "senha-teste"})
        self.assertEqual(status, 204)

    def request(
        self,
        path: str,
        payload: dict | None = None,
        origin: str | None = None,
    ) -> tuple[int, dict | str]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        method = "GET" if payload is None else "POST"
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if origin:
            req.add_header("Origin", origin)
        try:
            with self.opener.open(req) as response:
                body = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            status = exc.code
        if path == "/":
            return status, body
        return status, json.loads(body) if body else {}


if __name__ == "__main__":
    unittest.main()
