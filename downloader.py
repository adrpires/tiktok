"""Baixa vídeos de fundo verticais na API do Pixabay."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import requests

PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"
RENDITIONS = ("large", "medium", "small", "tiny")
MAX_DURATION_SECONDS = 30
TARGET_HEIGHT = 1920
KEYWORDS_PER_VIDEO = 5
PER_PAGE = 50


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
    """Baixa um MP4 vertical do Pixabay para cada uma das 5 palavras-chave.

    A busca usa ``video_type="film"`` e ``orientation="vertical"``.
    Os arquivos ficam na pasta temporária do sistema. No container Docker,
    essa pasta é ``/tmp``. A chave vem de ``PIXABAY_API_KEY``.
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
    key = (api_key if api_key is not None else os.environ.get("PIXABAY_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("Defina PIXABAY_API_KEY com a chave da API do Pixabay.")
    return key


def _find_video(
    session: requests.Session,
    api_key: str,
    keyword: str,
    used_ids: set[int],
) -> tuple[int, str]:
    for page in (1, 2):
        payload = _search(session, api_key, keyword, page)
        chosen = _select_video(payload.get("hits") or [], used_ids)
        if chosen is not None:
            return chosen
        if not _has_next_page(payload, page):
            break
    raise RuntimeError(
        f"O Pixabay não tem um vídeo vertical para a palavra-chave {keyword!r}."
    )


def _search(
    session: requests.Session,
    api_key: str,
    keyword: str,
    page: int,
) -> dict:
    response = session.get(
        PIXABAY_SEARCH_URL,
        params={
            "key": api_key,
            "q": keyword,
            "video_type": "film",
            "orientation": "vertical",
            "lang": "en",
            "safesearch": "true",
            "page": page,
            "per_page": PER_PAGE,
        },
        timeout=30,
    )
    if response.status_code in {400, 401, 403}:
        raise RuntimeError("A chave da API do Pixabay foi recusada.")
    if response.status_code == 429:
        raise RuntimeError("O Pixabay limitou as requisições. Tente de novo mais tarde.")
    if response.status_code != 200:
        raise RuntimeError(f"A busca no Pixabay falhou com o status {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("A busca no Pixabay não devolveu JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("A busca no Pixabay não devolveu JSON.")
    return payload


def _has_next_page(payload: dict, page: int) -> bool:
    total = payload.get("totalHits")
    hits = payload.get("hits") or []
    if not hits:
        return False
    if not isinstance(total, int):
        return len(hits) == PER_PAGE
    return page * PER_PAGE < total


def _select_video(hits: list[dict], used_ids: set[int]) -> tuple[int, str] | None:
    ranked: list[tuple[int, int, str]] = []
    for hit in hits:
        video_id = hit.get("id")
        if not isinstance(video_id, int) or video_id in used_ids:
            continue
        if hit.get("type") not in (None, "film"):
            continue
        link = _best_mp4(hit.get("videos") or {})
        if link is None:
            continue
        duration = hit.get("duration")
        if not isinstance(duration, int) or duration < 1:
            duration = 10**9
        ranked.append((duration, video_id, link))
    if not ranked:
        return None
    short = [item for item in ranked if item[0] <= MAX_DURATION_SECONDS]
    _, video_id, link = min(short or ranked)
    return video_id, link


def _best_mp4(videos: dict) -> str | None:
    candidates: list[tuple[int, int, str]] = []
    for name in RENDITIONS:
        item = videos.get(name) or {}
        url = item.get("url")
        width = item.get("width") or 0
        height = item.get("height") or 0
        if not isinstance(url, str) or not url.split("?", 1)[0].lower().endswith(".mp4"):
            continue
        if not isinstance(width, int) or not isinstance(height, int):
            continue
        if height <= width:
            continue
        candidates.append((abs(height - TARGET_HEIGHT), height, url))
    if not candidates:
        return None
    return min(candidates)[2]


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
            raise RuntimeError("O Pixabay devolveu um vídeo vazio.")
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
