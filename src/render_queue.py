from pathlib import Path
import json, subprocess, sys
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"

def render_queue(queue_path):
    queue = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    generated = []
    for item in queue:
        payload_path = OUTPUT / f"payload_{item['slot_order']:02d}.json"
        payload_path.write_text(json.dumps(item["payload"], ensure_ascii=False, indent=2), encoding="utf-8")
        out_name = f"{item['date']}_{item['slot_order']:02d}_{item['content_id']}.png"
        cmd = [sys.executable, str(ROOT / 'src/render.py'), '--template', item['template'], '--data', str(payload_path), '--output', out_name]
        subprocess.run(cmd, check=True, cwd=str(ROOT))
        generated.append(str(OUTPUT / out_name))
    return generated

if __name__ == '__main__':
    queue_files = sorted(OUTPUT.glob('queue_*.json'))
    if not queue_files:
        raise SystemExit('No queue file found in output/')
    latest = queue_files[-1]
    files = render_queue(latest)
    print('
'.join(files))
