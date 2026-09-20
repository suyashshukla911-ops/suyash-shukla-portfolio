from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build" / "web"
INDEX = BUILD / "index.html"
AUDIO_OUT = BUILD / "audio"
JS_SOURCE = ROOT / "web_audio_manager.js"

if not INDEX.exists():
    raise SystemExit(f"Build index not found: {INDEX}\nRun pygbag --build first.")

AUDIO_OUT.mkdir(parents=True, exist_ok=True)

# Direct browser-accessible audio files. Pygbag also keeps its normal packaged
# copies inside the .tar.gz/.apk; these direct copies are solely for Web Audio.
shutil.copy2(ROOT / "assets" / "aerostrike_theme.ogg", AUDIO_OUT / "aerostrike_theme.ogg")
for source in sorted((ROOT / "assets" / "sounds").glob("*.ogg")):
    shutil.copy2(source, AUDIO_OUT / source.name)

manager = JS_SOURCE.read_text(encoding="utf-8")
start_marker = "<!-- AEROSTRIKE_WEB_AUDIO_START -->"
end_marker = "<!-- AEROSTRIKE_WEB_AUDIO_END -->"
block = f"\n{start_marker}\n<script>\n{manager}\n</script>\n{end_marker}\n"

html = INDEX.read_text(encoding="utf-8")
start = html.find(start_marker)
end = html.find(end_marker)
if start != -1 and end != -1:
    end += len(end_marker)
    html = html[:start] + block.strip("\n") + html[end:]
else:
    marker = "</head>"
    pos = html.lower().find(marker)
    if pos == -1:
        raise SystemExit("Could not find </head> in generated pygbag index.html")
    html = html[:pos] + block + html[pos:]

INDEX.write_text(html, encoding="utf-8")
print(f"Patched: {INDEX}")
print(f"Copied browser audio to: {AUDIO_OUT}")
