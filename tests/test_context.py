"""Contexto do vídeo no lugar de um tema avulso."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from script_generator import (
    LlmSettings,
    VideoContext,
    _complete,
    generate_script,
    llm_settings,
    context_from_mapping,
    load_context_file,
    resolve_context,
)

NARRACAO = "\n".join(
    [
        "Gancho (0-2s): Você acorda cansado?",
        "Problema (2-4s): A primeira hora some na tela.",
        "Solução (4-8s): Beba água e solte o pescoço.",
        "CTA (8-10s): Salve e tente amanhã.",
    ]
)


class _Resposta:
    output_text = json.dumps(
        {
            "narração": NARRACAO,
            "palavras_chave": ["morning water", "sunlit window", "neck stretch", "alarm clock", "kitchen counter"],
            "hashtags": ["rotina matinal", "#água", "acorda cedo", "habitos", "alongamento"],
        }
    )


class _Responses:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _Resposta()


class _Client:
    def __init__(self) -> None:
        self.responses = _Responses()


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            "CONTEXT_PATH": os.environ.get("CONTEXT_PATH"),
            "TIKTOK_THEME": os.environ.get("TIKTOK_THEME"),
        }
        os.environ.pop("CONTEXT_PATH", None)
        os.environ.pop("TIKTOK_THEME", None)

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_texto_omite_campos_vazios(self) -> None:
        texto = VideoContext(tema="  rotina  ", tom="direto").texto()
        self.assertEqual(texto, "Tema: rotina\nTom: direto")

    def test_arquivo_aceita_publico_com_acento(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contexto.json"
            path.write_text(
                json.dumps({"tema": "água", "público": "quem acorda cedo", "tom": "leve"}),
                encoding="utf-8",
            )
            context = load_context_file(path)
        self.assertEqual(context.publico, "quem acorda cedo")
        self.assertIn("Público: quem acorda cedo", context.texto())

    def test_arquivo_rejeita_campo_desconhecido_e_tema_vazio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contexto.json"
            path.write_text(json.dumps({"tema": "x", "hashtags": "nao"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_context_file(path)
            path.write_text(json.dumps({"publico": "alguém"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_context_file(path)

    def test_arquivo_existe_substitui_o_tema_e_e_lido_de_novo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contexto.json"
            path.write_text(json.dumps({"tema": "primeiro", "objetivo": "salvar"}), encoding="utf-8")
            os.environ["CONTEXT_PATH"] = str(path)
            self.assertEqual(resolve_context("tema antigo").tema, "primeiro")
            path.write_text(json.dumps({"tema": "segundo", "detalhes": "sem marca"}), encoding="utf-8")
            atualizado = resolve_context("tema antigo")
        self.assertEqual(atualizado.tema, "segundo")
        self.assertEqual(atualizado.detalhes, "sem marca")

    def test_sem_arquivo_o_tema_avulso_continua_valendo(self) -> None:
        os.environ["CONTEXT_PATH"] = "/tmp/contexto-que-nao-existe.json"
        context = resolve_context("rotina matinal")
        self.assertEqual(context.tema, "rotina matinal")
        self.assertEqual(context.publico, "")

    def test_contexto_explicito_nao_e_trocado_pelo_arquivo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contexto.json"
            path.write_text(json.dumps({"tema": "do arquivo"}), encoding="utf-8")
            os.environ["CONTEXT_PATH"] = str(path)
            explicito = resolve_context(VideoContext(tema="direto", tom="leve"))
        self.assertEqual(explicito.tema, "direto")
        self.assertEqual(explicito.tom, "leve")

    def test_grok_recebe_o_contexto_inteiro(self) -> None:
        client = _Client()
        context = VideoContext(
            tema="rotina matinal",
            publico="quem acorda cedo",
            tom="direto",
            objetivo="salvar o vídeo",
            detalhes="não citar marcas",
        )
        script = generate_script(context, client=client)
        pedido = client.responses.kwargs["input"]
        self.assertIn("Tema: rotina matinal", pedido)
        self.assertIn("Público: quem acorda cedo", pedido)
        self.assertIn("Tom: direto", pedido)
        self.assertIn("Objetivo: salvar o vídeo", pedido)
        self.assertIn("Detalhes: não citar marcas", pedido)
        self.assertIn("público", client.responses.kwargs["instructions"].lower())
        self.assertIn("10 segundos", client.responses.kwargs["instructions"])
        self.assertIn("emocionada", client.responses.kwargs["instructions"])
        self.assertIn("Gancho (0-2s)", client.responses.kwargs["instructions"])
        self.assertEqual(script.palavras_chave[0], "morning water")

        client_tema = _Client()
        generate_script("rotina matinal", client=client_tema)
        self.assertEqual(client_tema.responses.kwargs["input"], "Tema: rotina matinal")

    def test_gemini_gratuito_tem_prioridade_e_recebe_o_contexto(self) -> None:
        previous = {name: os.environ.get(name) for name in ("SCRIPT_PROVIDER", "GEMINI_API_KEY", "XAI_API_KEY")}
        os.environ.pop("SCRIPT_PROVIDER", None)
        os.environ["GEMINI_API_KEY"] = '"AIza-teste"'
        os.environ["XAI_API_KEY"] = "xai-teste"
        try:
            settings = llm_settings()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.assertEqual(settings.provider, "gemini")
        self.assertEqual(settings.model, "gemini-3.8-flash")
        self.assertEqual(settings.api_key, "AIza-teste")
        self.assertFalse(settings.use_responses)

        class _Message:
            content = "```json\n" + _Resposta.output_text + "\n```"

        class _Choice:
            message = _Message()

        class _Completions:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                class _Response:
                    choices = [_Choice()]
                return _Response()

        class _Chat:
            def __init__(self) -> None:
                self.completions = _Completions()

        class _Gemini:
            def __init__(self) -> None:
                self.chat = _Chat()

        client = _Gemini()
        script = _complete(
            client,
            LlmSettings("gemini", "gemini-3.8-flash", "AIza-teste", "https://example/", False),
            "Tema: rotina",
        )
        self.assertIn("Tema: rotina", client.chat.completions.kwargs["messages"][1]["content"])
        self.assertEqual(client.chat.completions.kwargs["model"], "gemini-3.8-flash")
        from script_generator import _parse_script

        parsed = _parse_script(script)
        self.assertEqual(parsed.palavras_chave[0], "morning water")
        self.assertEqual(
            parsed.hashtags,
            ("#rotinamatinal", "#água", "#acordacedo", "#habitos", "#alongamento"),
        )

    def test_agora_recebe_o_tema_sem_entrar_na_agenda(self) -> None:
        import scheduler

        called: dict[str, str] = {}

        def fake(theme: str) -> Path:
            called["theme"] = theme
            return Path("/tmp/tiktok.mp4")

        original = scheduler.run_once
        argv = sys.argv[:]
        scheduler.run_once = fake
        sys.argv = ["scheduler.py", "--agora", "rotina", "matinal"]
        try:
            scheduler.main()
        finally:
            scheduler.run_once = original
            sys.argv = argv
        self.assertEqual(called["theme"], "rotina matinal")

    def test_sem_tema_e_sem_arquivo_encerra(self) -> None:
        import scheduler

        argv = sys.argv[:]
        sys.argv = ["scheduler.py"]
        try:
            with self.assertRaises(SystemExit) as saida:
                scheduler.main()
        finally:
            sys.argv = argv
        self.assertEqual(saida.exception.code, 2)

    def test_exemplo_do_repositorio(self) -> None:
        exemplo = Path(__file__).resolve().parents[1] / "contexto.example.json"
        context = load_context_file(exemplo)
        self.assertTrue(context.tema)
        self.assertTrue(context.publico)
        self.assertTrue(context.tom)
        self.assertTrue(context.objetivo)
        self.assertTrue(context.detalhes)
        self.assertEqual(context.tamanho_mb, 12)
        self.assertEqual(context.duracao, 10)
        self.assertNotIn("12", context.texto())

    def test_tamanho_nao_entra_no_roteiro_e_rejeita_fora_da_faixa(self) -> None:
        context = context_from_mapping({"tema": "água", "tamanho_mb": "8,5"})
        self.assertEqual(context.tamanho_mb, 8.5)
        self.assertNotIn("8,5", context.texto())
        with self.assertRaises(ValueError):
            context_from_mapping({"tema": "água", "tamanho_mb": 2})
        with self.assertRaises(ValueError):
            context_from_mapping({"tema": "água", "tamanho_mb": 200})
        curto = context_from_mapping({"tema": "água"})
        self.assertEqual(curto.duracao, 10)
        longo = context_from_mapping({"tema": "água", "duração": "30 segundos"})
        self.assertEqual(longo.duracao, 30)
        with self.assertRaises(ValueError):
            context_from_mapping({"tema": "água", "duracao": 12})

    def test_bitrate_cabe_no_tamanho_pedido(self) -> None:
        from video_editor import format_mb, resolved_max_mb, video_bitrate

        rate = video_bitrate(30, 12)
        kbps = int(rate[:-1])
        audio_mb = 96 * 30 / 8 / 1024
        video_mb = kbps * 30 / 8 / 1024
        self.assertLess(audio_mb + video_mb, 12)
        self.assertGreater(kbps, 400)
        self.assertEqual(format_mb(1024 * 1024), "1,0 MB")
        previous = os.environ.pop("VIDEO_MAX_MB", None)
        try:
            self.assertEqual(resolved_max_mb(None), 12)
            self.assertIsNone(resolved_max_mb(0))
            os.environ["VIDEO_MAX_MB"] = "20"
            self.assertEqual(resolved_max_mb(None), 20)
            self.assertEqual(resolved_max_mb(8), 8)
        finally:
            if previous is None:
                os.environ.pop("VIDEO_MAX_MB", None)
            else:
                os.environ["VIDEO_MAX_MB"] = previous

    def test_roteiro_de_trinta_segundos_continua_valendo(self) -> None:
        from script_generator import _parse_script, instructions_for

        texto = instructions_for(30)
        self.assertIn("30 segundos", texto)
        self.assertIn("CTA (25-30s)", texto)
        narracao = "\n".join(
            [
                "Gancho (0-3s): Você acorda cansado todo dia de novo?",
                "Problema (3-10s): A primeira hora some em café e tela e o corpo continua pesado.",
                "Solução (10-25s): Beba água, abra a janela e solte o pescoço por dois minutos. Sem pressa e sem lista enorme.",
                "CTA (25-30s): Salve este vídeo e tente esses três passos amanhã.",
            ]
        )
        parsed = _parse_script(
            json.dumps({"narração": narracao, "palavras_chave": ["a", "b", "c", "d", "e"], "hashtags": ["um", "dois", "tres", "quatro", "cinco"]}),
            30,
        )
        self.assertIn("Salve este vídeo", parsed.narração)
        with self.assertRaises(RuntimeError):
            _parse_script(
                json.dumps({"narração": narracao, "palavras_chave": ["a", "b", "c", "d", "e"], "hashtags": ["um", "dois", "tres", "quatro", "cinco"]}),
                10,
            )

    def test_voz_masculina_grave_e_pausada(self) -> None:
        import video_editor

        captured: dict[str, str] = {}

        class _Voz:
            def __init__(self, text: str, voice: str, rate: str | None = None, pitch: str | None = None) -> None:
                captured["text"] = text
                captured["voice"] = voice
                captured["rate"] = rate or ""
                captured["pitch"] = pitch or ""

            async def save(self, path: str) -> None:
                Path(path).write_bytes(b"audio")

        original = video_editor.edge_tts.Communicate
        video_editor.edge_tts.Communicate = _Voz
        previous = os.environ.pop("GEMINI_API_KEY", None)
        destination = Path(tempfile.mkdtemp()) / "fala.mp3"
        try:
            video_editor.synthesize_narration("A paz esteja com você.", destination)
        finally:
            video_editor.edge_tts.Communicate = original
            if previous is not None:
                os.environ["GEMINI_API_KEY"] = previous
        self.assertEqual(captured["voice"], "pt-BR-AntonioNeural")
        self.assertEqual(captured["rate"], "-12%")
        self.assertEqual(captured["pitch"], "-4Hz")
        self.assertGreater(destination.stat().st_size, 0)

    def test_voz_emocionada_usa_o_gemini(self) -> None:
        import base64

        import video_editor

        captured: dict[str, object] = {}
        wav = b"RIFF" + b"\x00" * 40

        class _Resposta:
            status_code = 200

            def json(self) -> dict[str, object]:
                return {
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "audio", "data": base64.b64encode(wav).decode()}],
                        }
                    ]
                }

        def _post(url: str, headers: dict[str, str], json: dict[str, object], timeout: int) -> _Resposta:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return _Resposta()

        original = video_editor.requests.post
        video_editor.requests.post = _post
        previous = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = '"AIza-teste"'
        os.environ.pop("GEMINI_TTS_MODEL", None)
        os.environ.pop("GEMINI_TTS_VOICE", None)
        destination = Path(tempfile.mkdtemp()) / "fala.mp3"
        try:
            result = video_editor.synthesize_narration("A paz esteja com você.", destination)
        finally:
            video_editor.requests.post = original
            if previous is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous
        body = captured["json"]
        assert isinstance(body, dict)
        self.assertEqual(result.suffix, ".wav")
        self.assertEqual(result.read_bytes(), wav)
        self.assertEqual(captured["url"], video_editor.GEMINI_TTS_URL)
        headers = captured["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["x-goog-api-key"], "AIza-teste")
        self.assertEqual(body["model"], "gemini-3.8-flash-tts")
        self.assertEqual(body["generation_config"]["speech_config"][0]["voice"], "Charon")
        spoken = body["input"][0]["content"][0]
        self.assertEqual(spoken["text"], "A paz esteja com você.")
        self.assertEqual(spoken["annotations"][0]["style"], video_editor.GEMINI_STYLE)
        self.assertNotIn("AIza", spoken["text"])

    def test_falha_do_gemini_nao_volta_para_a_voz_plana(self) -> None:
        import video_editor

        called = {"edge": False}

        class _Resposta:
            status_code = 400

            def json(self) -> dict[str, object]:
                return {"error": {"message": "chave recusada"}}

        class _Voz:
            def __init__(self, text: str, voice: str, rate: str | None = None, pitch: str | None = None) -> None:
                called["edge"] = True

            async def save(self, path: str) -> None:
                Path(path).write_bytes(b"audio")

        original_post = video_editor.requests.post
        original_voice = video_editor.edge_tts.Communicate
        video_editor.requests.post = lambda *args, **kwargs: _Resposta()
        video_editor.edge_tts.Communicate = _Voz
        previous = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "AIza-teste"
        destination = Path(tempfile.mkdtemp()) / "fala.mp3"
        try:
            with self.assertRaises(RuntimeError) as captured:
                video_editor.synthesize_narration("A paz esteja com você.", destination)
        finally:
            video_editor.requests.post = original_post
            video_editor.edge_tts.Communicate = original_voice
            if previous is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous
        self.assertIn("emocionada", str(captured.exception))
        self.assertIn("chave recusada", str(captured.exception))
        self.assertFalse(called["edge"])
        self.assertFalse(destination.with_suffix(".wav").exists())


if __name__ == "__main__":
    unittest.main()
