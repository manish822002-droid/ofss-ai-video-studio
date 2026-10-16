
import os
import re
import tempfile
import textwrap

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips

app = FastAPI(title="OFSS AI Video Studio API", version="2.0.0")

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


def split_script(script, max_scenes=30):
    script = re.sub(r"\s+", " ", script.strip())
    sentences = re.split(r'(?<=[.!?।])\s+', script)
    sentences = [s.strip() for s in sentences if s.strip()]

    scenes = []
    current = ""

    for sentence in sentences:
        if current and len(current) + len(sentence) > 260:
            scenes.append(current)
            current = sentence
        else:
            current = (current + " " + sentence).strip()

    if current:
        scenes.append(current)

    if len(scenes) > max_scenes:
        merged = []
        for i in range(max_scenes):
            start = round(i * len(scenes) / max_scenes)
            end = round((i + 1) * len(scenes) / max_scenes)
            merged.append(" ".join(scenes[start:end]))
        scenes = merged

    return scenes or [script]


def get_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
    ]

    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass

    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = (current + " " + word).strip()
        box = draw.textbbox((0, 0), test, font=font)

        if box[2] <= max_width or not current:
            current = test
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def make_scene(text, number, size, folder):
    width, height = size
    image = Image.new("RGB", size, (12, 22, 40))
    draw = ImageDraw.Draw(image)

    # Cinematic gradient background
    for y in range(height):
        ratio = y / max(1, height)
        color = (
            int(20 - 12 * ratio),
            int(48 - 35 * ratio),
            int(75 - 48 * ratio)
        )
        draw.line((0, y, width, y), fill=color)

    # Decorative circles
    draw.ellipse(
        (int(width * 0.65), int(height * 0.08),
         int(width * 1.05), int(height * 0.48)),
        outline=(45, 125, 155),
        width=5
    )

    title_font = get_font(max(18, width // 45), True)
    scene_font = get_font(max(18, width // 55), True)
    body_font = get_font(max(22, width // 30))
    footer_font = get_font(max(14, width // 70))

    draw.text(
        (int(width * 0.08), int(height * 0.09)),
        "OFSS AI VIDEO STUDIO",
        fill=(95, 215, 255),
        font=title_font
    )

    draw.text(
        (int(width * 0.08), int(height * 0.20)),
        f"SCENE {number:02d}",
        fill=(255, 190, 90),
        font=scene_font
    )

    lines = wrap_text(
        draw,
        text,
        body_font,
        int(width * 0.82)
    )

    max_lines = 8 if width > height else 15
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1] + "..."

    line_height = max(35, height // 14)
    y = int(height * 0.34)

    for line in lines:
        draw.text(
            (int(width * 0.08) + 2, y + 2),
            line,
            fill=(0, 0, 0),
            font=body_font
        )
        draw.text(
            (int(width * 0.08), y),
            line,
            fill=(255, 255, 255),
            font=body_font
        )
        y += line_height

    draw.text(
        (int(width * 0.08), int(height * 0.93)),
        "EDUCATIONAL DOCUMENTARY",
        fill=(165, 185, 205),
        font=footer_font
    )

    path = os.path.join(folder, f"scene_{number:03d}.jpg")
    image.save(path, quality=90)
    return path


def camera_motion(clip, duration, scene_number):
    zoom = 0.06

    if scene_number % 2 == 0:
        animated = clip.resized(
            lambda t: 1 + zoom * min(1, t / max(duration, 0.1))
        )
    else:
        animated = clip.resized(
            lambda t: 1 + zoom * (
                1 - min(1, t / max(duration, 0.1))
            )
        )

    return animated.with_position("center")


@app.post("/api/video")
async def create_video(request: VideoRequest):
    script = request.script.strip()

    if len(script) < 10:
        raise HTTPException(
            status_code=400,
            detail="Script is too short."
        )

    if request.format not in ("landscape", "portrait"):
        raise HTTPException(
            status_code=400,
            detail="Choose landscape or portrait."
        )

    workdir = tempfile.mkdtemp(prefix="ofss_video_")
    audio_path = os.path.join(workdir, "voice.mp3")
    output_path = os.path.join(workdir, "ofss_video.mp4")

    audio = None
    video = None
    clips = []

    try:
        # Generate AI voice
        communicate = edge_tts.Communicate(
            script,
            request.voice,
            rate="+0%"
        )
        await communicate.save(audio_path)

        scenes = split_script(script, 30)

        size = (
            (1280, 720)
            if request.format == "landscape"
            else (720, 1280)
        )

        image_paths = [
            make_scene(text, i + 1, size, workdir)
            for i, text in enumerate(scenes)
        ]

        audio = AudioFileClip(audio_path)

        weights = [
            max(1, len(scene.split()))
            for scene in scenes
        ]
        total_weight = sum(weights) or 1

        for i, path in enumerate(image_paths):
            duration = (
                audio.duration * weights[i] / total_weight
            )

            clip = ImageClip(path).with_duration(
                max(0.1, duration)
            )

            clip = camera_motion(
                clip,
                duration,
                i + 1
            )

            clips.append(clip)

        video = concatenate_videoclips(
            clips,
            method="compose"
        )

        video = video.with_audio(audio)

        video.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            logger=None
        )

        video.close()
        video = None

        audio.close()
        audio = None

        for clip in clips:
            clip.close()

        clips = []

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename="ofss-ai-video.mp4"
        )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Video creation failed: {str(exc)[:300]}"
        )

    finally:
        if video is not None:
            video.close()

        if audio is not None:
            audio.close()

        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
