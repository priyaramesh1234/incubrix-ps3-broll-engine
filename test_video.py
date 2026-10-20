
import asyncio
import subprocess
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


# Create output folder
output_dir = Path("outputs")
output_dir.mkdir(exist_ok=True)

# Output files
image_path = output_dir / "frame.png"
audio_path = output_dir / "narration.mp3"
video_path = output_dir / "test_video.mp4"

# Video settings
width = 1280
height = 720
fps = 30

# Text to speak and display
text = (
    "Hello! This is a test of the spoken narration. "
    "Saving money starts with a budget."
)


# Generate spoken narration
async def generate_narration():
    voice = edge_tts.Communicate(
        text,
        voice="en-IN-NeerjaNeural"
    )
    await voice.save(str(audio_path))


asyncio.run(generate_narration())


# Create video background
image = Image.new("RGB", (width, height), "#17324d")
draw = ImageDraw.Draw(image)

try:
    font = ImageFont.truetype(
        "C:/Windows/Fonts/arial.ttf", 42
    )
except OSError:
    font = ImageFont.load_default()

# Wrap the text to fit the video
words = text.split()
lines = []
current_line = ""

for word in words:
    candidate = f"{current_line} {word}".strip()

    if draw.textbbox((0, 0), candidate, font=font)[2] < width - 120:
        current_line = candidate
    else:
        if current_line:
            lines.append(current_line)
        current_line = word

if current_line:
    lines.append(current_line)

# Draw text in the center
line_height = 65
y = max(60, (height - len(lines) * line_height) // 2)

for line in lines:
    draw.text(
        (60, y),
        line,
        fill="white",
        font=font
    )
    y += line_height

image.save(image_path)


# Combine the image and spoken audio using FFmpeg
ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

ffmpeg_command = [
    ffmpeg_path,
    "-y",
    "-loop", "1",
    "-framerate", str(fps),
    "-i", str(image_path),
    "-i", str(audio_path),
    "-map", "0:v:0",
    "-map", "1:a:0",
    "-c:v", "libx264",
    "-tune", "stillimage",
    "-pix_fmt", "yuv420p",
    "-r", str(fps),
    "-c:a", "aac",
    "-ar", "44100",
    "-ac", "2",
    "-b:a", "128k",
    "-shortest",
    "-movflags", "+faststart",
    str(video_path)
]

subprocess.run(
    ffmpeg_command,
    check=True
)

print("Video created successfully!")
print("Video:", video_path.resolve())
print("Narration:", audio_path.resolve())
