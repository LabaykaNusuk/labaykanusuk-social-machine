from pathlib import Path
import argparse, subprocess, shutil, json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"

def make_reel(source, output, duration=12.0, fps=30):
    src = Path(source)
    if not src.is_absolute():
        src = (ROOT / src).resolve()
    out = Path(output)
    if not out.is_absolute():
        out = (OUTPUT / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    frames = int(duration * fps)
    # Subtle cinematic zoom (Ken Burns). Full frame stays vertical 1080x1920.
    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"zoompan=z='min(zoom+0.00010,1.035)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps={fps},"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg","-y","-loop","1","-i",str(src),
        "-vf",vf,"-t",str(duration),"-r",str(fps),
        "-c:v","libx264","-preset","medium","-crf","18",
        "-pix_fmt","yuv420p","-movflags","+faststart",
        str(out)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--duration", type=float, default=12.0)
    args = ap.parse_args()
    out = make_reel(args.source,args.output,args.duration)
    print(out)
