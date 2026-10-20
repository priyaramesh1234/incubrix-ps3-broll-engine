
import asyncio
import re
import subprocess
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


VOICE = "en-IN-NeerjaNeural"
WIDTH = 1280
HEIGHT = 720


async def generate_narration(text, audio_path):
    voice = edge_tts.Communicate(text, voice=VOICE)
    await voice.save(str(audio_path))


def get_audio_duration(audio_path) -> float:
    """Get audio duration in seconds using FFmpeg."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    result = subprocess.run(
        [ffmpeg, "-i", str(audio_path)],
        capture_output=True,
        text=True
    )

    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        result.stderr
    )

    if not match:
        raise ValueError("Could not determine audio duration.")

    hours, minutes, seconds = match.groups()

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )


def create_scene_image(text: str, image_path: Path):
    """Create a background image containing the scene text."""
    image = Image.new("RGB", (WIDTH, HEIGHT), "#17324d")
    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype(
            "C:/Windows/Fonts/arial.ttf", 42
        )
    except OSError:
        font = ImageFont.load_default()

    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        candidate = f"{current_line} {word}".strip()

        if draw.textbbox((0, 0), candidate, font=font)[2] < WIDTH - 120:
            current_line = candidate
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    line_height = 65
    y = max(60, (HEIGHT - len(lines) * line_height) // 2)

    for line in lines:
        draw.text((60, y), line, fill="white", font=font)
        y += line_height

    image.save(image_path)


def get_scene_durations(scenes: list[dict], total_duration: float) -> list[float]:
    """Allocate video duration to scenes based on their word counts."""
    if not scenes:
        raise ValueError("At least one scene is required.")

    word_counts = [
        max(1, len(scene["text"].split()))
        for scene in scenes
    ]

    total_words = sum(word_counts)

    return [
        total_duration * count / total_words
        for count in word_counts
    ]


def generate_test_video(text: str, output_path: str) -> str:
    """Generate an MP4 with text and spoken narration."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    image_path = output.parent / "video_frame.png"
    audio_path = output.parent / "narration.mp3"

    asyncio.run(generate_narration(text, audio_path))
    create_scene_image(text, image_path)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    command = [
        ffmpeg, "-y",
        "-loop", "1",
        "-framerate", "30",
        "-i", str(image_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output)
    ]

    subprocess.run(
        command, check=True, capture_output=True, text=True
    )

    return str(output.resolve())



def generate_scene_video(
    scenes: list[dict],
    output_path: str,
    input_audio_path: str | None = None,
) -> str:
    """Generate a scene-based video using local B-roll clips and narration."""
    if not scenes:
        raise ValueError("At least one scene is required.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    # Locate the B-roll folder in the project root.
    project_root = Path(__file__).resolve().parent.parent
    broll_folder = project_root / "assets" / "broll"

    broll_videos = [
        broll_folder / "video1.mp4",  # Saving money in a piggy bank
        broll_folder / "video2.mp4",  # Counting money with a calculator
        broll_folder / "video3.mp4",  # Writing down money details
    ]

    # Check that all three video files exist.
    for clip in broll_videos:
        if not clip.is_file():
            raise FileNotFoundError(
                f"B-roll video not found: {clip}"
            )

    # Generate one continuous narration for the entire script.
    full_script = " ".join(
        scene["text"].strip() for scene in scenes
    )

    audio_path = output.parent / "narration.mp3"

    if input_audio_path:
        import shutil
        shutil.copy2(input_audio_path, audio_path)
    else:
        asyncio.run(generate_narration(full_script, audio_path))

    audio_duration = get_audio_duration(audio_path)
    durations = get_scene_durations(scenes, audio_duration)

    # Match scene text to a suitable B-roll video.
    def select_broll(scene: dict, scene_index: int) -> Path:
        text = (
            scene.get("text", "") + " "
            + scene.get("visual_hint", "")
        ).lower()

        keywords = {
            0: [
                "save", "saving", "piggy", "bank", "deposit",
                "money", "future", "goal", "fund"
            ],
            1: [
                "count", "counting", "calculate", "calculator",
                "budget", "expense", "spend", "payment",
                "cost", "amount", "total"
            ],
            2: [
                "write", "writing", "note", "record", "track",
                "list", "plan", "recording", "expense",
                "financial", "account"
            ],
        }

        scores = []

        for clip_index in range(3):
            score = sum(
                1 for word in keywords[clip_index]
                if word in text
            )
            scores.append(score)

        # Use the best keyword match.
        best_score = max(scores)

        if best_score > 0:
            best_index = scores.index(best_score)
        else:
            # No matching keyword: cycle through the clips.
            best_index = scene_index % len(broll_videos)

        return broll_videos[best_index]

    video_parts = []

    # Create one processed video clip for each scene.
    for index, scene in enumerate(scenes):
        clip_path = select_broll(scene, index)
        scene_video_path = (
            output.parent / f"scene_{index + 1}.mp4"
        )

        duration = durations[index]

        command = [
            ffmpeg, "-y",
            "-stream_loop", "-1",
            "-i", str(clip_path),
            "-t", f"{duration:.6f}",
            "-an",
            "-vf",
                (
                    f"scale={WIDTH}:{HEIGHT}:"
                    "force_original_aspect_ratio=decrease,"
                    f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:"
                    "color=black,"
                    "fps=30,setsar=1"
                ),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-t", f"{duration:.6f}",
            "-movflags", "+faststart",
            str(scene_video_path),
        ]

        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )

        video_parts.append(scene_video_path)

    # Create a list of processed clips for FFmpeg concatenation.
    list_path = output.parent / "scene_list.txt"

    with open(list_path, "w", encoding="utf-8") as file:
        for clip in video_parts:
            safe_path = (
                clip.resolve().as_posix().replace("'", "'\\''")
            )
            file.write(f"file '{safe_path}'\n")

    silent_video = output.parent / "silent_video.mp4"

    # Join all B-roll clips into one video.
    subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            str(silent_video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Add the narration and match the final duration to the audio.
    subprocess.run(
        [
            ffmpeg, "-y",
            "-i", str(silent_video),
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "128k",
            "-t", f"{audio_duration:.6f}",
            "-movflags", "+faststart",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    return str(output.resolve())




def generate_srt(scenes: list[dict], output_path: str):
    """Create non-overlapping subtitles using estimated scene timings."""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    audio_path = output_file.parent / "narration.mp3"

    if not audio_path.exists():
        raise FileNotFoundError(
            f"Narration audio not found: {audio_path}"
        )

    audio_duration = get_audio_duration(audio_path)
    durations = get_scene_durations(scenes, audio_duration)

    def format_time(seconds: float) -> str:
        total_ms = max(0, round(seconds * 1000))
        hours = total_ms // 3_600_000
        minutes = (total_ms % 3_600_000) // 60_000
        seconds_part = (total_ms % 60_000) // 1000
        milliseconds = total_ms % 1000

        return (
            f"{hours:02}:{minutes:02}:"
            f"{seconds_part:02},{milliseconds:03}"
        )

    current_time = 0.0

    with open(output_file, "w", encoding="utf-8") as file:
        for index, scene in enumerate(scenes, start=1):
            start = current_time

            if index == len(scenes):
                end = audio_duration
            else:
                end = min(
                    audio_duration,
                    current_time + durations[index - 1]
                )

            if end > start:
                file.write(f"{index}\n")
                file.write(
                    f"{format_time(start)} --> {format_time(end)}\n"
                )
                file.write(f"{scene['text']}\n\n")

            current_time = end