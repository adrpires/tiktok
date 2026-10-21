"""O clique de publicar espera o TikTok liberar o botão."""

from __future__ import annotations

import unittest

from playwright.sync_api import sync_playwright

import scheduler


class UploadButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._playwright = sync_playwright().start()
        cls._browser = cls._playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._browser.close()
        cls._playwright.stop()

    def test_clica_quando_publicar_video_deixa_de_estar_desativado(self) -> None:
        page = self._browser.new_page()
        try:
            page.set_content(
                """
                <button data-e2e="post_video_button" disabled
                        onclick="this.dataset.clicked='sim'">Publicar vídeo</button>
                <script>
                  setTimeout(() => {
                    document.querySelector("button").disabled = false;
                  }, 400);
                </script>
                """
            )
            scheduler._click_post(page, visible_timeout=2_000, ready_timeout=5_000)
            self.assertEqual(page.locator("button").get_attribute("data-clicked"), "sim")
        finally:
            page.close()

    def test_explica_quando_o_botao_nao_aparece(self) -> None:
        page = self._browser.new_page()
        try:
            page.set_content("<button>Descartar</button>")
            with self.assertRaises(RuntimeError) as saida:
                scheduler._click_post(page, visible_timeout=500, ready_timeout=500)
        finally:
            page.close()
        self.assertIn("Descartar", str(saida.exception))
        self.assertIn("não mostrou", str(saida.exception))

    def test_publica_cinco_hashtags_no_comentario(self) -> None:
        page = self._browser.new_page()
        try:
            page.set_content(
                """
                <div data-e2e="comment-input" contenteditable="true"></div>
                <button data-e2e="comment-post"
                        onclick="this.dataset.texto=document.querySelector('[data-e2e=comment-input]').innerText">
                  Publicar
                </button>
                """
            )
            scheduler._post_comment(
                page,
                ("#RotinaMatinal", "#Agua", "#AcordaCedo", "#Habitos", "#Alongamento"),
            )
            texto = page.locator("[data-e2e=comment-post]").get_attribute("data-texto") or ""
        finally:
            page.close()
        for tag in ("#RotinaMatinal", "#Agua", "#AcordaCedo", "#Habitos", "#Alongamento"):
            self.assertIn(tag, texto)

    def test_encontra_o_id_do_video_na_resposta(self) -> None:
        found = scheduler._extract_video_id(
            {"single_post_resp_list": [{"item_id": "7123456789012345678", "status": 0}]}
        )
        self.assertEqual(found, "7123456789012345678")


if __name__ == "__main__":
    unittest.main()
