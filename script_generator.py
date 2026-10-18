"""Gera um roteiro de TikTok de 30 segundos com o Grok, pela API da xAI."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass

from openai import OpenAI

# O site é https://x.ai. O SDK da OpenAI anexa /responses a este prefixo,
# então a chamada chega em https://api.x.ai/v1/responses.
XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.7"

_INSTRUCTIONS = """
Você escreve a narração falada de um TikTok de 30 segundos, em português do Brasil.

A narração deve seguir estritamente esta estrutura de retenção, nesta ordem, com estes tempos e estes rótulos. Não crie outras seções, não junte seções e não mude os tempos.

Gancho (0-3s): uma frase que interrompe o scroll. De 8 a 12 palavras.
Problema (3-10s): o problema concreto de quem assiste. De 15 a 20 palavras.
Solução (10-25s): como resolver, em frases curtas. De 35 a 45 palavras.
CTA (25-30s): uma única chamada para ação. De 10 a 15 palavras.

Escreva exatamente quatro linhas, uma por seção, neste formato e sem texto antes ou depois:
Gancho (0-3s): fala do gancho
Problema (3-10s): fala do problema
Solução (10-25s): fala da solução
CTA (25-30s): fala da chamada para ação

Não use hashtags nem emojis.

palavras_chave: exatamente 5 termos de busca em inglês, visuais e concretos, de uma a três palavras, para achar vídeos de fundo. Sem hashtags, sem frases e sem repetir o tema inteiro.
""".strip()

_NARRATION = re.compile(
    r"Gancho \(0-3s\):\s*(?P<gancho>\S.*)\n"
    r"Problema \(3-10s\):\s*(?P<problema>\S.*)\n"
    r"Solução \(10-25s\):\s*(?P<solucao>\S.*)\n"
    r"CTA \(25-30s\):\s*(?P<cta>\S.*)\Z"
)

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "tiktok_script",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "narração": {
                "type": "string",
                "description": (
                    "Quatro linhas: Gancho (0-3s), Problema (3-10s), "
                    "Solução (10-25s) e CTA (25-30s)."
                ),
            },
            "palavras_chave": {
                "type": "array",
                "description": "Cinco termos de busca em inglês para vídeos de fundo.",
                "minItems": 5,
                "maxItems": 5,
                "items": {"type": "string", "minLength": 2, "maxLength": 40},
            },
        },
        "required": ["narração", "palavras_chave"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class TikTokScript:
    """Narração de 30 segundos e termos em inglês para buscar o fundo do vídeo."""

    narração: str
    palavras_chave: tuple[str, ...]

    @property
    def script(self) -> str:
        return self.narração

    @property
    def keywords(self) -> tuple[str, ...]:
        return self.palavras_chave


def create_client(api_key: str | None = None) -> OpenAI:
    """Abre um cliente da OpenAI apontando para a API da xAI."""
    key = (api_key if api_key is not None else os.environ.get("XAI_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("Defina XAI_API_KEY com a chave da API da xAI.")
    return OpenAI(api_key=key, base_url=XAI_BASE_URL)


def generate_script(
    theme: str,
    *,
    client: OpenAI | None = None,
    model: str = DEFAULT_MODEL,
) -> TikTokScript:
    """Pede ao Grok uma narração de 30 segundos e 5 palavras-chave em inglês.

    A narração segue Gancho (0-3s), Problema (3-10s), Solução (10-25s) e CTA (25-30s).
    O JSON da resposta usa as chaves ``narração`` e ``palavras_chave``.
    """
    cleaned_theme = theme.strip()
    if not cleaned_theme:
        raise ValueError("Informe um tema para o roteiro.")

    grok = client or create_client()
    response = grok.responses.create(
        model=model,
        instructions=_INSTRUCTIONS,
        input=cleaned_theme,
        store=False,
        reasoning={"effort": "low"},
        text={"format": _RESPONSE_FORMAT},
    )
    return _parse_script(response.output_text)


def _parse_script(payload: str) -> TikTokScript:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("A API da xAI não devolveu JSON válido.") from exc

    narration = data.get("narração")
    keywords = data.get("palavras_chave")
    if not isinstance(narration, str) or _NARRATION.fullmatch(_normalize(narration)) is None:
        raise RuntimeError(
            "A narração não segue Gancho (0-3s), Problema (3-10s), "
            "Solução (10-25s) e CTA (25-30s)."
        )
    if (
        not isinstance(keywords, list)
        or len(keywords) != 5
        or not all(isinstance(item, str) and item.strip() for item in keywords)
    ):
        raise RuntimeError("A resposta não trouxe exatamente 5 palavras-chave.")

    return TikTokScript(
        narração=_normalize(narration),
        palavras_chave=tuple(item.strip() for item in keywords),
    )


def _normalize(narration: str) -> str:
    return "\n".join(line.strip() for line in narration.splitlines() if line.strip())


def main() -> None:
    if len(sys.argv) < 2:
        print('Uso: python script_generator.py "tema do vídeo"', file=sys.stderr)
        raise SystemExit(2)

    result = generate_script(" ".join(sys.argv[1:]))
    print(result.narração)
    print()
    print("Palavras-chave:", ", ".join(result.palavras_chave))


if __name__ == "__main__":
    main()
