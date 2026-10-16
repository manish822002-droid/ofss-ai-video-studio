
import os
import re
import asyncio
import tempfile
import textwrap
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, vfx

app = FastAPI(title="OFSS AI Video Studio API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    script: str = Field(min_length=10, max_length=12000)
    voice: str = "hi-IN-SwaraNeural"
    format: str = "landscape"

@app.get("/")
def home():
    return {
        "service": "OFSS AI Video Studio API",
        "status": "ok",
        "endpoint": "/api/video"
    }

def split_script(script):
    parts = re.split(r'(?<=[.!?।])\s+', script.strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts or [script.strip()]

def make_scene(text, number, size, folder):
    width, height = size
    image = Image.new("RGB", size, (15, 23, 42))
    draw = ImageDraw.Draw(image)

    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, max(22, width // 28))
        small_font = ImageFont.truetype(font_path, max(18, width // 45))
    except OSError:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    draw.text((width // 12, height // 10),
              "OFSS AI VIDEO STUDIO",
              fill=(80, 200, 255), font=small_font)

    draw.text((width // 12, height // 5),
              f"SCENE {number:02d}",
              fill=(255, 190, 80), font=small_font)

    max_chars = max(18, width // max(12, width // 35))
    lines = textwrap.wrap(text, width=max_chars) or [text]
    max_lines = 7 if height > width else 12
    lines = lines[:max_lines]

    line_height = max(35, height // 14)
    y = height // 3

    for line in lines:
        draw.text((width // 12, y), line, fill="white", font=font)
        y += line_height

    path = os.path.join(folder, f"scene_{number:03d}.jpg")
    image.save(path, quality=88)
    return path


def camera_motion(clip, duration, scene_number):
    zoom = 1.0 + (0.08 * min(duration, 5) / 5)

    if scene_number % 2 == 0:
        animated = clip.resized(
            lambda t: 1.0 + (zoom - 1.0) * t / max(duration, 0.1)
        )
    else:
        animated = clip.resized(
            lambda t: zoom - (zoom - 1.0) * t / max(duration, 0.1)
        )

    return animated.with_position("center")

@app.post("/api/video")
async def create_video(request: VideoRequest):
    script = request.script.strip()

    if len(script) < 10:
        raise HTTPException(status_code=400, detail="Script is too short.")

    if request.format not in ("landscape", "portrait"):
        raise HTTPException(status_code=400, detail="Invalid video format.")

    workdir = tempfile.mkdtemp(prefix="ofss_video_")
    audio_path = os.path.join(workdir, "voice.mp3")
    output_path = os.path.join(workdir, "ofss_video.mp4")

    try:
        communicate = edge_tts.Communicate(
            script,
            request.voice,
            rate="+0%"
        )
        await communicate.save(audio_path)

        scenes = split_script(script)[:30]
        size = (1280, 720) if request.format == "landscape" else (720, 1280)

        image_paths = [
            make_scene(text, i + 1, size, workdir)
            for i, text in enumerate(scenes)
        ]

        audio = AudioFileClip(audio_path)
        weights = [max(1, len(s.split())) for s in scenes]
        total_weight = sum(weights)
        clips = []

        
for i, path in enumerate(image_paths):
    duration = audio.duration * weights[i] / total_weight
    clip = ImageClip(path).with_duration(duration)
    clip = camera_motion(clip, duration, i + 1)
    clips.append(clip)


        video = concatenate_videoclips(clips, method="compose")
        video = video.with_audio(audio)

        video.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None
        )

        video.close()
        audio.close()

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename="ofss-ai-video.mp4",
            background=None
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Video creation failed: {str(exc)[:300]}"
        )
