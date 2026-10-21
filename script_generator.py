"""Gera um roteiro de TikTok de 30 segundos com o Grok, pela API da xAI."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

# O site é https://x.ai. O SDK da OpenAI anexa /responses a este prefixo,
# então a chamada chega em https://api.x.ai/v1/responses.
XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.7"

_INSTRUCTIONS = """
Você escreve a narração falada de um TikTok de 30 segundos, em português do Brasil.

O pedido traz o contexto do vídeo. O tema é o assunto. O público diz com quem se fala. O tom define a voz. O objetivo define o que a pessoa deve fazer ao terminar. Os detalhes limitam o que entra e o que fica de fora. Use todo campo que vier preenchido. Não invente público, promessa ou produto que o contexto não trouxe.

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


_CONTEXT_FIELDS = {
    "tema": "tema",
    "publico": "publico",
    "público": "publico",
    "tom": "tom",
    "objetivo": "objetivo",
    "detalhes": "detalhes",
}


@dataclass(frozen=True)
class VideoContext:
    """Assunto do vídeo e as condições em que a narração deve ser escrita."""

    tema: str
    publico: str = ""
    tom: str = ""
    objetivo: str = ""
    detalhes: str = ""

    def texto(self) -> str:
        """Monta o pedido enviado ao Grok, só com os campos preenchidos."""
        tema = self.tema.strip()
        if not tema:
            raise ValueError("O contexto precisa de um tema.")
        linhas = [f"Tema: {tema}"]
        opcionais = (
            ("Público", self.publico),
            ("Tom", self.tom),
            ("Objetivo", self.objetivo),
            ("Detalhes", self.detalhes),
        )
        for rotulo, valor in opcionais:
            limpo = valor.strip()
            if limpo:
                linhas.append(f"{rotulo}: {limpo}")
        return "\n".join(linhas)


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


def context_path() -> Path | None:
    """Caminho do JSON de contexto, vindo de CONTEXT_PATH."""
    raw = os.environ.get("CONTEXT_PATH", "").strip()
    if not raw:
        return None
    return Path(raw)


def load_context_file(path: Path) -> VideoContext:
    """Lê tema, público, tom, objetivo e detalhes de um JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"O contexto em {path} não é um JSON válido.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"O contexto em {path} precisa ser um objeto JSON.")

    values = {name: "" for name in ("tema", "publico", "tom", "objetivo", "detalhes")}
    for key, value in data.items():
        field = _CONTEXT_FIELDS.get(str(key).strip().lower())
        if field is None:
            raise ValueError(f"Campo desconhecido no contexto: {key}")
        if not isinstance(value, str):
            raise ValueError(f"O campo {key} do contexto precisa ser texto.")
        values[field] = value.strip()
    if not values["tema"]:
        raise ValueError("O contexto precisa do campo tema.")
    return VideoContext(**values)


def resolve_context(source: VideoContext | str | None = None) -> VideoContext:
    """Escolhe o contexto desta execução.

    Um ``VideoContext`` já montado vale como está. Se ``CONTEXT_PATH`` aponta
    para um arquivo, ele é lido de novo e substitui um tema avulso. Sem
    arquivo, a string recebida ou ``TIKTOK_THEME`` vira um contexto só com tema.
    """
    if isinstance(source, VideoContext):
        source.texto()
        return source

    path = context_path()
    if path is not None and path.is_file():
        return load_context_file(path)

    theme = source.strip() if isinstance(source, str) else ""
    if not theme:
        theme = os.environ.get("TIKTOK_THEME", "").strip()
    if theme:
        return VideoContext(tema=theme)
    if path is not None:
        raise FileNotFoundError(f"Arquivo de contexto não encontrado: {path}")
    raise ValueError("Informe um contexto ou um tema.")


def create_client(api_key: str | None = None) -> OpenAI:
    """Abre um cliente da OpenAI apontando para a API da xAI."""
    key = (api_key if api_key is not None else os.environ.get("XAI_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("Defina XAI_API_KEY com a chave da API da xAI.")
    return OpenAI(api_key=key, base_url=XAI_BASE_URL)


def generate_script(
    context: VideoContext | str,
    *,
    client: OpenAI | None = None,
    model: str = DEFAULT_MODEL,
) -> TikTokScript:
    """Pede ao Grok uma narração de 30 segundos e 5 palavras-chave em inglês.

    ``context`` pode ser um ``VideoContext`` ou, como antes, só o tema.
    A narração segue Gancho (0-3s), Problema (3-10s), Solução (10-25s) e CTA (25-30s).
    O JSON da resposta usa as chaves ``narração`` e ``palavras_chave``.
    """
    video_context = context if isinstance(context, VideoContext) else VideoContext(tema=context)
    pedido = video_context.texto()

    grok = client or create_client()
    response = grok.responses.create(
        model=model,
        instructions=_INSTRUCTIONS,
        input=pedido,
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
        print(
            'Uso: python script_generator.py "tema do vídeo"\n'
            "     python script_generator.py contexto.json",
            file=sys.stderr,
        )
        raise SystemExit(2)

    argument = " ".join(sys.argv[1:])
    path = Path(argument)
    context: VideoContext | str = load_context_file(path) if path.is_file() else argument
    result = generate_script(context)
    print(result.narração)
    print()
    print("Palavras-chave:", ", ".join(result.palavras_chave))


if __name__ == "__main__":
    main()
