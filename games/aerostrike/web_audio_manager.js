/*
 * AeroStrike browser audio bridge.
 *
 * The game itself remains Pygame/Python.  On Emscripten this bridge deliberately
 * bypasses SDL_mixer and uses plain HTMLMediaElement audio instead of the Web
 * Audio API.  This avoids the active-tab sizzling/static problem observed with
 * SDL/WebAudio browser builds while retaining clean, low-latency short SFX.
 */
(function () {
    "use strict";

    const MUSIC_URL = "audio/aerostrike_theme.ogg";
    const SFX_BASE = "audio/";
    const SOUND_NAMES = [
        "click",
        "launch",
        "bumper",
        "target",
        "kick",
        "rollover",
        "wall",
        "drain",
        "combo",
        "flipper_move",
        "flipper_hit"
    ];

    // A few ready-to-play copies prevent rapid repeated collisions from
    // cutting each other off.  The effects are tiny, so this is inexpensive.
    const VOICES_PER_SFX = 4;

    let enabled = true;
    let musicEnabled = true;
    let music = null;
    const pools = Object.create(null);
    const poolIndex = Object.create(null);
    let prepared = false;
    let preparePromise = null;
    let userActivated = false;

    function makeAudio(url, volume, loop) {
        const element = new window.Audio();
        element.preload = "auto";
        element.src = url;
        element.loop = !!loop;
        element.autoplay = false;
        element.volume = volume;
        element.playsInline = true;
        element.setAttribute("playsinline", "");
        return element;
    }

    function buildAudioGraph() {
        if (prepared) {
            return;
        }

        music = makeAudio(MUSIC_URL, 0.88, true);

        for (const name of SOUND_NAMES) {
            pools[name] = [];
            poolIndex[name] = 0;

            for (let i = 0; i < VOICES_PER_SFX; i += 1) {
                const element = makeAudio(`${SFX_BASE}${name}.ogg`, 0.82, false);
                pools[name].push(element);
            }
        }

        prepared = true;
    }

    function loadElement(element) {
        try {
            element.load();
        } catch (_) {
            // Browser may already be loading it.
        }
    }

    async function prepare() {
        buildAudioGraph();

        if (!preparePromise) {
            preparePromise = Promise.resolve().then(() => {
                loadElement(music);
                for (const name of SOUND_NAMES) {
                    for (const element of pools[name]) {
                        loadElement(element);
                    }
                }
                return true;
            });
        }

        return preparePromise;
    }

    function startMusicDirect() {
        buildAudioGraph();
        if (!enabled || !musicEnabled || !music) {
            return;
        }

        music.volume = 0.88;
        const promise = music.play();
        if (promise && typeof promise.catch === "function") {
            promise.catch(() => {
                // Autoplay policy may reject until a real gesture arrives.
            });
        }
    }

    async function startMusic() {
        await prepare();
        if (!userActivated) {
            return;
        }
        startMusicDirect();
    }

    function stopMusic() {
        if (!music) {
            return;
        }
        try {
            music.pause();
            music.currentTime = 0;
        } catch (_) {
            // Ignore media shutdown errors.
        }
    }

    function play(name) {
        if (!enabled || !SOUND_NAMES.includes(name)) {
            return;
        }

        buildAudioGraph();
        const pool = pools[name];
        if (!pool || pool.length === 0) {
            return;
        }

        const index = poolIndex[name] % pool.length;
        poolIndex[name] = (index + 1) % pool.length;
        const sound = pool[index];

        try {
            sound.pause();
            sound.currentTime = 0;
            sound.volume = 0.82;
            const promise = sound.play();
            if (promise && typeof promise.catch === "function") {
                promise.catch(() => {});
            }
        } catch (_) {
            // Ignore an individual effect failure; gameplay must continue.
        }
    }

    function setEnabled(value) {
        enabled = !!value;

        if (!enabled) {
            stopMusic();
            for (const name of SOUND_NAMES) {
                const pool = pools[name] || [];
                for (const sound of pool) {
                    try { sound.pause(); } catch (_) {}
                }
            }
            return;
        }

        if (musicEnabled && userActivated) {
            startMusicDirect();
        }
    }

    function setMusicEnabled(value) {
        musicEnabled = !!value;
        if (!musicEnabled) {
            stopMusic();
        } else if (enabled && userActivated) {
            startMusicDirect();
        }
    }

    // This listener is intentionally plain HTML media.  It gives the browser
    // a real user activation before the Python/WebAssembly layer asks for
    // music/SFX playback.
    function userGesture() {
        userActivated = true;
        buildAudioGraph();

        if (enabled && musicEnabled) {
            startMusicDirect();
        }
    }

    document.addEventListener("pointerdown", userGesture, {
        capture: true,
        passive: true
    });
    document.addEventListener("keydown", userGesture, {
        capture: true,
        passive: true
    });
    document.addEventListener("touchstart", userGesture, {
        capture: true,
        passive: true
    });

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && enabled && musicEnabled && userActivated) {
            startMusicDirect();
        }
    });

    buildAudioGraph();

    window.AeroAudio = {
        prepare,
        start_music: startMusic,
        stop_music: stopMusic,
        play,
        set_enabled: setEnabled,
        set_music_enabled: setMusicEnabled
    };
})();
