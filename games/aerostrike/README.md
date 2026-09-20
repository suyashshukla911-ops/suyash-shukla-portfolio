# AeroStrike Pinball — Web Audio Final

This package keeps the game in Python/Pygame. The gameplay, physics, table layout, scoring, controls, camera, UI states and rendering are unchanged.

## Browser audio fix

The Emscripten/web build does not use SDL_mixer for audio. It uses a small browser media-audio bridge (`web_audio_manager.js`) with ordinary HTML audio elements. This is intentional: pygbag's documentation notes browser sound distortion can be worked around with an external JavaScript sound manager, and pygbag issue #19 documents the same focused-tab sizzling/static symptom.

## Folder

```text
AeroStrike_Pinball_Web_Smooth_Audio_Final/
├── main.py
├── requirements.txt
├── web_audio_manager.js
├── postbuild.py
├── build_web.bat
├── run_web.bat
├── run_desktop.bat
├── .gitignore
└── assets/
    ├── aerostrike_theme.ogg
    └── sounds/
        ├── click.ogg
        ├── launch.ogg
        ├── bumper.ogg
        ├── target.ogg
        ├── kick.ogg
        ├── rollover.ogg
        ├── wall.ogg
        ├── drain.ogg
        ├── combo.ogg
        ├── flipper_move.ogg
        └── flipper_hit.ogg
```

## Build and test

Open PowerShell in this folder and run:

```powershell
$env:PYTHONUTF8="1"
python -m pip install -r requirements.txt
python -m pygbag --build .
python postbuild.py
```

Then serve the generated static build:

```powershell
python -m http.server 8080 --directory build/web --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:8080/
```

Or simply run `run_web.bat`, which performs the build/post-build step and then serves `build/web` on port 8080.

## Important

Do **not** run `python -m pygbag .` after `postbuild.py` and then expect the custom browser-audio bridge to still be present: a fresh pygbag run regenerates `build/web/index.html`. Use `run_web.bat`, or run `postbuild.py` again after every fresh pygbag build.

For GitHub Pages, commit the generated `build/web` folder together with this game source. The website can later load `games/aerostrike/build/web/index.html` in an iframe; the Python source remains `main.py`.


Final refinements: pause menu supports TAB / Shift+TAB navigation with Enter/Space activation, and web audio is slightly increased (music 0.88, SFX 0.82). Gameplay/physics logic is unchanged.
