"""Gera um roteiro de TikTok de 30 segundos com uma IA de texto."""

from __future__ import annotations

import json
import logging
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
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = "gemini-3.8-flash"
LOGGER = logging.getLogger(__name__)

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

Não use hashtags nem emojis na narração.

palavras_chave: exatamente 5 termos de busca em inglês, visuais e concretos, de uma a três palavras, para achar vídeos de fundo. Sem hashtags, sem frases e sem repetir o tema inteiro.

hashtags: exatamente 5 hashtags em português do Brasil para publicar no primeiro comentário. Cada uma nasce do assunto deste vídeo, é curta, específica e sem espaço. Não repita hashtag. Não entregue só fyp, viral ou parati.
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
            "hashtags": {
                "type": "array",
                "description": "Cinco hashtags em português para o primeiro comentário.",
                "minItems": 5,
                "maxItems": 5,
                "items": {"type": "string", "minLength": 2, "maxLength": 40},
            },
        },
        "required": ["narração", "palavras_chave", "hashtags"],
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
    """Narração de 30 segundos, termos de fundo e hashtags do comentário."""

    narração: str
    palavras_chave: tuple[str, ...]
    hashtags: tuple[str, ...]

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


_CONTEXT_LIMITS = {
    "tema": 300,
    "publico": 500,
    "tom": 300,
    "objetivo": 500,
    "detalhes": 4000,
}


def context_from_mapping(data: dict) -> VideoContext:
    """Monta o contexto a partir das chaves tema, publico, tom, objetivo e detalhes."""
    if not isinstance(data, dict):
        raise ValueError("O contexto precisa ser um objeto JSON.")

    values = {name: "" for name in ("tema", "publico", "tom", "objetivo", "detalhes")}
    for key, value in data.items():
        field = _CONTEXT_FIELDS.get(str(key).strip().lower())
        if field is None:
            raise ValueError(f"Campo desconhecido no contexto: {key}")
        if not isinstance(value, str):
            raise ValueError(f"O campo {key} do contexto precisa ser texto.")
        cleaned = value.strip()
        if len(cleaned) > _CONTEXT_LIMITS[field]:
            raise ValueError(f"O campo {key} passou de {_CONTEXT_LIMITS[field]} caracteres.")
        values[field] = cleaned
    if not values["tema"]:
        raise ValueError("O contexto precisa do campo tema.")
    return VideoContext(**values)


def load_context_file(path: Path) -> VideoContext:
    """Lê tema, público, tom, objetivo e detalhes de um JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"O contexto em {path} não é um JSON válido.") from exc
    return context_from_mapping(data)


def save_context_file(context: VideoContext, path: Path | None = None) -> Path:
    """Grava o contexto em CONTEXT_PATH, ou no caminho informado."""
    destination = path if path is not None else context_path()
    if destination is None:
        raise RuntimeError("Defina CONTEXT_PATH para gravar o contexto.")
    context.texto()
    payload = {
        "tema": context.tema.strip(),
        "publico": context.publico.strip(),
        "tom": context.tom.strip(),
        "objetivo": context.objetivo.strip(),
        "detalhes": context.detalhes.strip(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        temporary.replace(destination)
    except OSError:
        destination.write_text(text, encoding="utf-8")
        temporary.unlink(missing_ok=True)
    return destination


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


@dataclass(frozen=True)
class LlmSettings:
    """Provedor usado para escrever o roteiro."""

    provider: str
    model: str
    api_key: str
    base_url: str
    use_responses: bool


def create_client(api_key: str | None = None) -> OpenAI:
    """Abre um cliente da OpenAI apontando para a API da xAI."""
    key = _clean_secret(api_key if api_key is not None else os.environ.get("XAI_API_KEY", ""))
    if not key:
        raise RuntimeError("Defina XAI_API_KEY com a chave da API da xAI.")
    return OpenAI(api_key=key, base_url=XAI_BASE_URL)


def llm_settings() -> LlmSettings:
    """Escolhe Gemini, um endpoint compatível ou a xAI.

    Com ``GEMINI_API_KEY`` definida e sem ``SCRIPT_PROVIDER``, o roteiro sai
    pela cota do Google AI Studio. ``SCRIPT_PROVIDER=xai`` força o Grok.
    """
    provider = os.environ.get("SCRIPT_PROVIDER", "").strip().lower()
    gemini_key = _clean_secret(os.environ.get("GEMINI_API_KEY", ""))
    script_key = _clean_secret(os.environ.get("SCRIPT_API_KEY", ""))
    xai_key = _clean_secret(os.environ.get("XAI_API_KEY", ""))
    if not provider:
        if gemini_key:
            provider = "gemini"
        elif script_key and os.environ.get("SCRIPT_BASE_URL", "").strip():
            provider = "compativel"
        else:
            provider = "xai"

    if provider == "gemini":
        if not gemini_key:
            raise RuntimeError(
                "Defina GEMINI_API_KEY com a chave gratuita criada em aistudio.google.com/apikey."
            )
        return LlmSettings(
            provider="gemini",
            model=os.environ.get("GEMINI_MODEL", "").strip() or GEMINI_MODEL,
            api_key=gemini_key,
            base_url=GEMINI_BASE_URL,
            use_responses=False,
        )
    if provider in {"compativel", "openai"}:
        base_url = os.environ.get("SCRIPT_BASE_URL", "").strip().rstrip("/")
        model = os.environ.get("SCRIPT_MODEL", "").strip()
        if not script_key or not base_url or not model:
            raise RuntimeError("Defina SCRIPT_API_KEY, SCRIPT_BASE_URL e SCRIPT_MODEL.")
        return LlmSettings(
            provider="compativel",
            model=model,
            api_key=script_key,
            base_url=base_url + "/",
            use_responses=False,
        )
    if provider != "xai":
        raise RuntimeError("SCRIPT_PROVIDER deve ser gemini, compativel ou xai.")
    if not xai_key:
        raise RuntimeError("Defina XAI_API_KEY com a chave da API da xAI.")
    return LlmSettings(
        provider="xai",
        model=os.environ.get("XAI_MODEL", "").strip() or DEFAULT_MODEL,
        api_key=xai_key,
        base_url=XAI_BASE_URL,
        use_responses=True,
    )


def generate_script(
    context: VideoContext | str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> TikTokScript:
    """Pede uma narração de 30 segundos e 5 palavras-chave em inglês.

    ``context`` pode ser um ``VideoContext`` ou, como antes, só o tema.
    A narração segue Gancho (0-3s), Problema (3-10s), Solução (10-25s) e CTA (25-30s).
    O JSON da resposta usa as chaves ``narração`` e ``palavras_chave``.
    """
    video_context = context if isinstance(context, VideoContext) else VideoContext(tema=context)
    pedido = video_context.texto()
    if client is not None:
        settings = LlmSettings(
            provider="teste",
            model=model or DEFAULT_MODEL,
            api_key="",
            base_url="",
            use_responses=True,
        )
        llm = client
    else:
        settings = llm_settings()
        if model:
            settings = LlmSettings(
                provider=settings.provider,
                model=model,
                api_key=settings.api_key,
                base_url=settings.base_url,
                use_responses=settings.use_responses,
            )
        llm = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
    LOGGER.info("Pedindo roteiro a %s (%s).", settings.provider, settings.model)
    return _parse_script(_complete(llm, settings, pedido))


def _complete(client: OpenAI, settings: LlmSettings, pedido: str) -> str:
    if settings.use_responses:
        response = client.responses.create(
            model=settings.model,
            instructions=_INSTRUCTIONS,
            input=pedido,
            store=False,
            reasoning={"effort": "low"},
            text={"format": _RESPONSE_FORMAT},
        )
        return response.output_text
    response = client.chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": _INSTRUCTIONS},
            {"role": "user", "content": pedido},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": _RESPONSE_FORMAT["name"],
                "strict": True,
                "schema": _RESPONSE_FORMAT["schema"],
            },
        },
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("O modelo não devolveu o roteiro.")
    return content


def _clean_secret(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def _parse_script(payload: str) -> TikTokScript:
    try:
        data = json.loads(_json_text(payload))
    except json.JSONDecodeError as exc:
        raise RuntimeError("O modelo não devolveu JSON válido.") from exc

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
        hashtags=_hashtags(data.get("hashtags")),
    )


def _json_text(payload: str) -> str:
    text = payload.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _hashtags(values: object) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) != 5:
        raise RuntimeError("A resposta não trouxe exatamente 5 hashtags.")
    tags: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise RuntimeError("Cada hashtag precisa ser texto.")
        tag = _one_hashtag(item)
        key = tag.casefold()
        if key in seen:
            raise RuntimeError("As hashtags estão repetidas.")
        seen.add(key)
        tags.append(tag)
    return tuple(tags)


def _one_hashtag(value: str) -> str:
    raw = re.sub(r"\s+", "", value.strip().lstrip("#"))
    if not raw or len(raw) > 40 or re.fullmatch(r"[\w]+", raw, flags=re.UNICODE) is None:
        raise RuntimeError(f"Hashtag inválida: {value}")
    return f"#{raw}"


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
    print("Hashtags:", " ".join(result.hashtags))


if __name__ == "__main__":
    main()
