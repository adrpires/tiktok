"""Baixa vídeos de fundo curtos e verticais na API gratuita do Pexels."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import requests

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
# 9:16. O Pexels só filtra retrato, paisagem ou quadrado; a proporção é conferida aqui.
TARGET_RATIO = 9 / 16
RATIO_TOLERANCE = 0.02
MAX_DURATION_SECONDS = 30
MIN_HEIGHT = 1280
TARGET_HEIGHT = 1920
KEYWORDS_PER_VIDEO = 5


@dataclass(frozen=True)
class BackgroundVideos:
    """Pasta temporária e os 5 vídeos baixados, na ordem das palavras-chave."""

    directory: Path
    files: tuple[Path, ...]


def download_backgrounds(
    keywords: Sequence[str],
    *,
    api_key: str | None = None,
    directory: Path | None = None,
    session: requests.Session | None = None,
) -> BackgroundVideos:
    """Baixa um vídeo curto 9:16 para cada uma das 5 palavras-chave.

    As palavras-chave são as mesmas devolvidas por ``generate_script``.
    Os arquivos ficam em uma pasta temporária até o chamador apagá-la.
    A chave da API gratuita do Pexels vem de ``PEXELS_API_KEY``.
    """
    cleaned = _clean_keywords(keywords)
    key = _api_key(api_key)
    folder = directory or Path(tempfile.mkdtemp(prefix="tiktok-backgrounds-"))
    folder.mkdir(parents=True, exist_ok=True)
    http = session or requests.Session()

    files: list[Path] = []
    used_ids: set[int] = set()
    for index, keyword in enumerate(cleaned, start=1):
        video_id, link = _find_video(http, key, keyword, used_ids)
        used_ids.add(video_id)
        filename = f"{index:02d}-{_slug(keyword)}-{video_id}.mp4"
        files.append(_download(http, link, folder / filename))

    return BackgroundVideos(directory=folder, files=tuple(files))


def _clean_keywords(keywords: Sequence[str]) -> tuple[str, ...]:
    if isinstance(keywords, str) or not isinstance(keywords, Sequence):
        raise TypeError("Informe a lista de 5 palavras-chave.")
    cleaned = tuple(item.strip() for item in keywords)
    if len(cleaned) != KEYWORDS_PER_VIDEO or not all(cleaned):
        raise ValueError("São necessárias exatamente 5 palavras-chave.")
    return cleaned


def _api_key(api_key: str | None) -> str:
    key = (api_key if api_key is not None else os.environ.get("PEXELS_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("Defina PEXELS_API_KEY com a chave gratuita da API do Pexels.")
    return key


def _find_video(
    session: requests.Session,
    api_key: str,
    keyword: str,
    used_ids: set[int],
) -> tuple[int, str]:
    for page in (1, 2):
        payload = _search(session, api_key, keyword, page)
        chosen = _select_video(payload.get("videos") or [], used_ids)
        if chosen is not None:
            return chosen
        if not payload.get("next_page"):
            break
    raise RuntimeError(
        f"O Pexels não tem um vídeo curto 9:16 para a palavra-chave {keyword!r}."
    )


def _search(
    session: requests.Session,
    api_key: str,
    keyword: str,
    page: int,
) -> dict:
    response = session.get(
        PEXELS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={
            "query": keyword,
            "orientation": "portrait",
            "size": "small",
            "locale": "en-US",
            "page": page,
            "per_page": 80,
        },
        timeout=30,
    )
    if response.status_code in {401, 403}:
        raise RuntimeError("A chave da API do Pexels foi recusada.")
    if response.status_code == 429:
        raise RuntimeError("O Pexels limitou as requisições. Tente de novo mais tarde.")
    if response.status_code != 200:
        raise RuntimeError(f"A busca no Pexels falhou com o status {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("A busca no Pexels não devolveu JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("A busca no Pexels não devolveu JSON.")
    return payload


def _select_video(videos: list[dict], used_ids: set[int]) -> tuple[int, str] | None:
    ranked: list[tuple[int, int, str]] = []
    for video in videos:
        video_id = video.get("id")
        duration = video.get("duration")
        if not isinstance(video_id, int) or video_id in used_ids:
            continue
        if not isinstance(duration, int) or duration < 1 or duration > MAX_DURATION_SECONDS:
            continue
        link = _best_file(video.get("video_files") or [])
        if link is not None:
            ranked.append((duration, video_id, link))
    if not ranked:
        return None
    _, video_id, link = min(ranked)
    return video_id, link


def _best_file(files: list[dict]) -> str | None:
    candidates: list[tuple[int, int, str]] = []
    for item in files:
        if item.get("file_type") != "video/mp4":
            continue
        width = item.get("width") or 0
        height = item.get("height") or 0
        link = item.get("link")
        if not isinstance(link, str) or not link:
            continue
        if not isinstance(width, int) or not isinstance(height, int):
            continue
        if height < MIN_HEIGHT or not _is_nine_by_sixteen(width, height):
            continue
        candidates.append((abs(height - TARGET_HEIGHT), height, link))
    if not candidates:
        return None
    return min(candidates)[2]


def _is_nine_by_sixteen(width: int, height: int) -> bool:
    if width <= 0 or height <= width:
        return False
    return abs((width / height) - TARGET_RATIO) <= RATIO_TOLERANCE


def _download(session: requests.Session, link: str, destination: Path) -> Path:
    partial = destination.with_suffix(".part")
    try:
        with session.get(link, stream=True, timeout=120) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"O download do vídeo falhou com o status {response.status_code}."
                )
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        handle.write(chunk)
        if partial.stat().st_size == 0:
            raise RuntimeError("O Pexels devolveu um vídeo vazio.")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _slug(keyword: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    return slug or "video"


def main() -> None:
    if len(sys.argv) != KEYWORDS_PER_VIDEO + 1:
        print(
            'Uso: python downloader.py "kw1" "kw2" "kw3" "kw4" "kw5"',
            file=sys.stderr,
        )
        raise SystemExit(2)

    result = download_backgrounds(sys.argv[1:])
    print(result.directory)
    for path in result.files:
        print(path)


if __name__ == "__main__":
    main()
