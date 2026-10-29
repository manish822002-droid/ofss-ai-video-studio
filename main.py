
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import edge_tts
import imageio_ffmpeg

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


app = FastAPI(
    title="OFSS AI Video Studio API",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

VOICES = {
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-US-JennyNeural",
    "en-US-ChristopherNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "hi-IN-SwaraNeural",
    "hi-IN-MadhurNeural",
}


@app.get("/")
def home():
    return {
        "service": "OFSS AI Video Studio API",
        "version": "2.1.0",
        "status": "ok",
        "endpoint": "/api/video",
        "docs": "/docs",
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "ffmpeg": "available"}


def run_ffmpeg(args):
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "FFmpeg failed")[-2000:]
        )
    return result


def audio_duration(path):
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    text = (result.stderr or "") + (result.stdout or "")
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        text,
    )
    if not match:
        raise RuntimeError("Could not determine audio duration.")

    h, m, s = match.groups()
    duration = int(h) * 3600 + int(m) * 60 + float(s)

    if duration <= 0:
        raise RuntimeError("Audio duration is zero.")

    return duration


@app.post("/api/video")
async def create_video(
    images: list[UploadFile] = File(
        ...,
        description="Select multiple JPG, PNG, or WebP images",
    ),
    script: str = Form(""),
    voice: str = Form("en-US-AriaNeural"),
    aspect_ratio: str = Form("16:9"),
    seconds_per_image: int = Form(8),
):
    if not images or len(images) > 40:
        raise HTTPException(
            400, "Upload between 1 and 40 images."
        )

    if voice not in VOICES:
        raise HTTPException(400, "Unsupported voice.")

    if aspect_ratio not in ("16:9", "9:16"):
        raise HTTPException(400, "Choose 16:9 or 9:16.")

    if not 3 <= seconds_per_image <= 30:
        raise HTTPException(
            400, "Seconds per image must be between 3 and 30."
        )

    if len(script) > 12000:
        raise HTTPException(
            400, "Script must be under 12,000 characters."
        )

    workdir = Path(tempfile.mkdtemp(prefix="ofss_video_"))
    output = workdir / "OFSS-AI-Video.mp4"

    try:
        valid_images = []

        for index, upload in enumerate(images):
            try:
                if upload.content_type not in {
                    "image/jpeg", "image/png", "image/webp"
                }:
                    continue

                data = await upload.read()

                if not data or len(data) > 12 * 1024 * 1024:
                    continue

                suffix = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                }[upload.content_type]

                path = workdir / f"image_{index:03d}{suffix}"
                path.write_bytes(data)
                valid_images.append(path)

            finally:
                await upload.close()

        if not valid_images:
            raise HTTPException(
                400, "No valid images. Use JPG, PNG, or WebP."
            )

        voice_path = workdir / "voice.mp3"
        duration = 0.0

        if script.strip():
            await edge_tts.Communicate(
                script.strip(), voice, rate="+0%"
            ).save(str(voice_path))
            duration = audio_duration(voice_path)

        if duration <= 0:
            duration = len(valid_images) * seconds_per_image

        if aspect_ratio == "16:9":
            width, height = 1280, 720
        else:
            width, height = 720, 1280

        fps = 24
        per_image = duration / len(valid_images)
        segments = []

        for index, image_path in enumerate(valid_images):
            segment = workdir / f"segment_{index:03d}.mp4"

            video_filter = (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                "zoompan=z='min(zoom+0.0005,1.08)':"
                f"d=1:s={width}x{height}:fps={fps},"
                "format=yuv420p"
            )

            run_ffmpeg([
                "-loop", "1",
                "-framerate", str(fps),
                "-i", str(image_path),
                "-t", str(per_image),
                "-vf", video_filter,
                "-an",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                str(segment),
            ])

            if not segment.exists() or segment.stat().st_size == 0:
                raise RuntimeError(
                    f"Could not create video segment {index + 1}."
                )

            segments.append(segment)

        concat_path = workdir / "segments.txt"
        concat_path.write_text(
            "".join(
                f"file '{p.as_posix()}'\n" for p in segments
            ),
            encoding="utf-8",
        )

        silent_video = workdir / "silent.mp4"

        run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_path),
            "-c", "copy",
            str(silent_video),
        ])

        if script.strip():
            run_ffmpeg([
                "-i", str(silent_video),
                "-i", str(voice_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                str(output),
            ])
        else:
            shutil.copy2(silent_video, output)

        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("MP4 output was not created.")

        return FileResponse(
            path=str(output),
            media_type="video/mp4",
            filename="OFSS-AI-Video.mp4",
            background=BackgroundTask(
                shutil.rmtree,
                str(workdir),
                ignore_errors=True,
            ),
        )

    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            500,
            f"Video generation failed: {str(exc)[:700]}",
        )
