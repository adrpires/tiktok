"""Gera um roteiro de TikTok de 30 segundos com o Grok, pela API da xAI."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

from openai import OpenAI

# O site é https://x.ai. O SDK da OpenAI anexa /responses a este prefixo,
# então a chamada chega em https://api.x.ai/v1/responses.
XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.7"

_INSTRUCTIONS = """
Você escreve roteiros falados para o TikTok, com duração de 30 segundos.

Devolva:
- script: o roteiro em português do Brasil, pronto para gravar.
- keywords: exatamente 5 termos de busca em inglês para achar vídeos de fundo.

Formato do roteiro, com estes três blocos e nada além deles:
0–3s | Gancho
Fala: uma frase que prende nos primeiros segundos
Tela: texto curto para aparecer na tela

3–22s | Desenvolvimento
Fala: o conteúdo principal, em frases curtas
Tela: texto curto para aparecer na tela

22–30s | Fechamento
Fala: conclusão e chamada para ação
Tela: texto curto para aparecer na tela

A fala completa deve ter entre 70 e 90 palavras, no ritmo de uma narração de 30 segundos.
Não use hashtags nem emojis.

As keywords são termos visuais e concretos, em inglês, de uma a três palavras,
adequados para buscar filmagens de fundo. Sem frases, sem hashtags e sem repetir o tema inteiro.
""".strip()

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "tiktok_script",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "Roteiro falado de 30 segundos, formatado para o TikTok.",
            },
            "keywords": {
                "type": "array",
                "description": "Cinco termos de busca em inglês para vídeos de fundo.",
                "minItems": 5,
                "maxItems": 5,
                "items": {"type": "string", "minLength": 2, "maxLength": 40},
            },
        },
        "required": ["script", "keywords"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class TikTokScript:
    """Roteiro de 30 segundos e termos em inglês para buscar o fundo do vídeo."""

    script: str
    keywords: tuple[str, ...]


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
    """Pede ao Grok um roteiro de 30 segundos e 5 palavras-chave em inglês.

    O roteiro sai em português, com gancho, desenvolvimento e fechamento.
    As palavras-chave servem para buscar vídeos de fundo.
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

    script = data.get("script")
    keywords = data.get("keywords")
    if not isinstance(script, str) or not script.strip():
        raise RuntimeError("A resposta não trouxe um roteiro.")
    if (
        not isinstance(keywords, list)
        or len(keywords) != 5
        or not all(isinstance(item, str) and item.strip() for item in keywords)
    ):
        raise RuntimeError("A resposta não trouxe exatamente 5 palavras-chave.")

    return TikTokScript(
        script=script.strip(),
        keywords=tuple(item.strip() for item in keywords),
    )


def main() -> None:
    if len(sys.argv) < 2:
        print('Uso: python script_generator.py "tema do vídeo"', file=sys.stderr)
        raise SystemExit(2)

    result = generate_script(" ".join(sys.argv[1:]))
    print(result.script)
    print()
    print("Palavras-chave:", ", ".join(result.keywords))


if __name__ == "__main__":
    main()
