"""Monta o vídeo final do TikTok com MoviePy e narração em português do Brasil."""

from __future__ import annotations

import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from gtts import gTTS
from gtts.tts import gTTSError
from moviepy import AudioFileClip, VideoFileClip, concatenate_videoclips, vfx

from downloader import BackgroundVideos

TIKTOK_WIDTH = 1080
TIKTOK_HEIGHT = 1920
TIKTOK_FPS = 30
_FALA = re.compile(r"(?im)^fala:\s*(.+)$")
Synthesizer = Callable[[str, Path], Path]


def edit_video(
    videos: BackgroundVideos | Path | str | Sequence[Path | str],
    script: str | object,
    output: Path | str,
    *,
    synthesizer: Synthesizer | None = None,
    fps: int = TIKTOK_FPS,
) -> Path:
    """Junta os vídeos de fundo, narra o roteiro e exporta um MP4 1080×1920.

    Cada clipe ocupa uma fatia igual da narração, então os cinco fundos
    aparecem no vídeo. O áudio original dos clipes fica de fora; a trilha
    é a narração gerada com gTTS em português do Brasil.
    """
    destination = Path(output)
    paths = tuple(
        path for path in _video_paths(videos) if path.resolve() != destination.resolve()
    )
    if not paths:
        raise ValueError("Nenhum vídeo para montar.")
    spoken = _spoken_text(_script_text(script))
    destination.parent.mkdir(parents=True, exist_ok=True)
    speak = synthesizer or synthesize_narration

    narration_path = _temporary_audio_path()
    temp_audio = _temporary_audio_path(".m4a")
    sources: list[VideoFileClip] = []
    audio = None
    try:
        speak(spoken, narration_path)
        audio = AudioFileClip(str(narration_path))
        if audio.duration is None or audio.duration <= 0:
            raise RuntimeError("A narração foi gerada sem duração.")
        segment = audio.duration / len(paths)
        pieces = [
            _segment(_open_background(path, sources), segment, fps) for path in paths
        ]
        video = concatenate_videoclips(pieces, method="chain")
        if video.duration > audio.duration:
            video = video.subclipped(0, audio.duration)
        final = video.with_audio(audio)
        final.write_videofile(
            destination,
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            preset="veryfast",
            pixel_format="yuv420p",
            temp_audiofile=temp_audio,
            remove_temp=True,
            logger=None,
        )
    finally:
        for source in sources:
            source.close()
        if audio is not None:
            audio.close()
        narration_path.unlink(missing_ok=True)
        temp_audio.unlink(missing_ok=True)
    return destination


def synthesize_narration(text: str, destination: Path) -> Path:
    """Grava a narração com o sotaque de português do Brasil."""
    try:
        gTTS(text, lang="pt", tld="com.br").save(str(destination))
    except gTTSError as exc:
        raise RuntimeError("Não foi possível gerar a narração em português.") from exc
    return destination


def _script_text(script: str | object) -> str:
    if isinstance(script, str):
        return script
    text = getattr(script, "script", None)
    if isinstance(text, str):
        return text
    raise TypeError("Informe o roteiro em texto.")


def _spoken_text(script: str) -> str:
    lines = [match.group(1).strip() for match in _FALA.finditer(script)]
    spoken = " ".join(line for line in lines if line)
    if not spoken:
        spoken = script.strip()
    if not spoken:
        raise ValueError("O roteiro não tem texto para narrar.")
    return spoken


def _video_paths(
    videos: BackgroundVideos | Path | str | Sequence[Path | str],
) -> tuple[Path, ...]:
    if isinstance(videos, BackgroundVideos):
        paths = tuple(Path(path) for path in videos.files)
    elif isinstance(videos, (str, Path)):
        folder = Path(videos)
        if not folder.is_dir():
            raise ValueError(f"A pasta de vídeos não existe: {folder}")
        paths = tuple(sorted(path for path in folder.glob("*.mp4") if path.is_file()))
    elif isinstance(videos, Sequence):
        paths = tuple(Path(path) for path in videos)
    else:
        raise TypeError("Informe a pasta temporária ou a lista de vídeos.")
    if not paths:
        raise ValueError("Nenhum vídeo para montar.")
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Vídeo não encontrado: {missing[0]}")
    return paths


def _open_background(path: Path, sources: list[VideoFileClip]) -> VideoFileClip:
    clip = VideoFileClip(str(path), audio=False)
    sources.append(clip)
    if clip.duration is None or clip.duration <= 0:
        raise RuntimeError(f"O vídeo não tem duração: {path}")
    return clip


def _segment(clip: VideoFileClip, duration: float, fps: int) -> VideoFileClip:
    framed = _cover_vertical(clip).with_fps(fps)
    if framed.duration < duration:
        return framed.with_effects([vfx.Loop(duration=duration)])
    if duration < framed.duration:
        return framed.subclipped(0, duration)
    return framed


def _cover_vertical(clip: VideoFileClip) -> VideoFileClip:
    width, height = clip.size
    scale = max(TIKTOK_WIDTH / width, TIKTOK_HEIGHT / height)
    new_width = max(TIKTOK_WIDTH, math.ceil(width * scale))
    new_height = max(TIKTOK_HEIGHT, math.ceil(height * scale))
    x1 = (new_width - TIKTOK_WIDTH) // 2
    y1 = (new_height - TIKTOK_HEIGHT) // 2
    return clip.resized(new_size=(new_width, new_height)).cropped(
        x1=x1,
        y1=y1,
        width=TIKTOK_WIDTH,
        height=TIKTOK_HEIGHT,
    )


def _temporary_audio_path(suffix: str = ".mp3") -> Path:
    descriptor, name = tempfile.mkstemp(prefix="tiktok-narration-", suffix=suffix)
    os.close(descriptor)
    return Path(name)


def main() -> None:
    if len(sys.argv) != 4:
        print(
            "Uso: python video_editor.py <pasta-dos-videos> <roteiro.txt> <saida.mp4>",
            file=sys.stderr,
        )
        raise SystemExit(2)

    folder, script_path, output = sys.argv[1:]
    result = edit_video(folder, Path(script_path).read_text(encoding="utf-8"), output)
    print(result)


if __name__ == "__main__":
    main()
