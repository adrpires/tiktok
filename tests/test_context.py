"""Contexto do vídeo no lugar de um tema avulso."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from script_generator import (
    VideoContext,
    generate_script,
    load_context_file,
    resolve_context,
)

NARRACAO = "\n".join(
    [
        "Gancho (0-3s): Você acorda cansado todo dia de novo?",
        "Problema (3-10s): A primeira hora some em café e tela e o corpo continua pesado.",
        "Solução (10-25s): Beba água, abra a janela e solte o pescoço por dois minutos. Sem pressa e sem lista enorme.",
        "CTA (25-30s): Salve este vídeo e tente esses três passos amanhã.",
    ]
)


class _Resposta:
    output_text = json.dumps(
        {
            "narração": NARRACAO,
            "palavras_chave": ["morning water", "sunlit window", "neck stretch", "alarm clock", "kitchen counter"],
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
        self.assertEqual(script.palavras_chave[0], "morning water")

        client_tema = _Client()
        generate_script("rotina matinal", client=client_tema)
        self.assertEqual(client_tema.responses.kwargs["input"], "Tema: rotina matinal")

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


if __name__ == "__main__":
    unittest.main()
