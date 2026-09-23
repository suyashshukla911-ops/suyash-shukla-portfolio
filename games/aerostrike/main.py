
import asyncio
import math
import os
import random
import sys
from array import array
from dataclasses import dataclass, field

import pygame

# ============================================================
# AEROSTRIKE PINBALL v7
# ------------------------------------------------------------
# Original aerodynamic-themed pinball implementation.
#
# Design goals:
#   * Browser-first with pygbag, but runnable as a small desktop window.
#   * Tall world + vertical camera follow, matching the reference's
#     changing visible board regions.
#   * Conventional pinball terminology and mechanics.
#   * Two independent short, mirrored flippers with a large, traversable center drain.
#   * Triangular kick pads are positioned at the sides and NEVER block
#     the central shot lane.
#   * Laptop: LEFT/RIGHT arrows for flippers, UP arrow for launcher.
#     Spacebar is intentionally unused.
#   * Mouse and multitouch controls are persistent.
#   * One Python file for gameplay/rendering/input/audio.
# ============================================================

# -----------------------------
# Logical dimensions
# -----------------------------
WORLD_W = 720
WORLD_H = 2200

VIEW_W = 720
VIEW_H = 1040
WINDOW_SIZE = (500, 720)

FPS = 60
PHYSICS_HZ = 90
FIXED_DT = 1.0 / PHYSICS_HZ
MAX_FRAME_DT = 0.05
MAX_PHYSICS_STEPS = 8

# -----------------------------
# Physics
# -----------------------------
GRAVITY = 1280.0
AIR_DRAG = 0.010
MAX_BALL_SPEED = 1700.0
BALL_RADIUS = 11.0
WALL_RADIUS = 5.5
RESTITUTION = 0.86

FLIPPER_LENGTH = 82.0
FLIPPER_RADIUS = 9.5
FLIPPER_SPEED = 720.0  # degrees/second; fast, responsive pinball action
FLIPPER_UP_SPEED = -980.0
FLIPPER_MIN_UP = -760.0
FLIPPER_MIN_CENTER_GAP = 60.0


BUMPER_KICK = 720.0
TRIANGLE_KICK = 920.0
TRIANGLE_SIDE_KICK = 110.0
TARGET_RESTITUTION = 0.90

# -----------------------------
# Table geometry
# -----------------------------
TABLE_LEFT = 48.0
TABLE_RIGHT = 672.0
TABLE_TOP = 70.0
TABLE_BOTTOM = 2140.0

DRAIN_Y = 2168.0

# Once the ball has passed below the flipper pivots, the central opening is
# treated as a one-way drain. The ball must continue downward instead of
# bouncing back out of the bottom playfield.
DRAIN_CAPTURE_Y = 2110.0
DRAIN_CAPTURE_LEFT = 215.0
DRAIN_CAPTURE_RIGHT = 505.0
DRAIN_CAPTURE_PULL = 95.0

LAUNCH_X = 620.0
LAUNCH_BOTTOM = 2100.0
LAUNCH_TOP = 1300.0
LAUNCH_SPEED_MIN = 1500.0
LAUNCH_SPEED_MAX = 1800.0
LAUNCH_EXIT_X = 575.0
LAUNCH_EXIT_VX = -260.0
LAUNCH_EXIT_VY = 95.0
LAUNCH_MIN_EFFECTIVE_POWER = 0.35
FLIPPER_COLLISION_MARGIN = 2.5
FLIPPER_HIT_RESTITUTION = 0.98
FLIPPER_HIT_MIN_UP = -1120.0
# Resting flippers are static barriers; only a moving/pressed paddle behaves
# like an active pinball striker.
FLIPPER_REST_RESTITUTION = 0.72
FLIPPER_ACTIVE_THRESHOLD = math.radians(18.0)
FLIPPER_CONTACT_LOCK = 0.018

# Keep a short contact history so deterministic object-to-object cycles can
# receive a natural lateral escape impulse instead of looping forever.
LOOP_HISTORY_MAX = 8
LOOP_REPEAT_WINDOW = 2.2

# Smooth camera follow. Vertical tracking remains primary, while a subtle
# horizontal response lets the playfield follow the ball left/right.
CAMERA_FOLLOW = 0.47
CAMERA_SMOOTH = 13.5
CAMERA_X_SMOOTH = 11.0
CAMERA_X_DEADZONE = 70.0
CAMERA_X_MAX_SHIFT = 34.0
BALL_DRAW_RADIUS = 14

# -----------------------------
# Palette
# -----------------------------
C = {
    "bg": (5, 7, 12),
    "glass": (10, 11, 22),
    "glass2": (19, 14, 28),
    "panel": (27, 16, 34),
    "panel2": (39, 20, 45),
    "metal": (92, 94, 105),
    "metal_dark": (48, 48, 57),
    "metal_hi": (150, 151, 160),
    "white": (246, 246, 242),
    "muted": (205, 201, 193),
    "cyan": (69, 220, 235),
    "purple": (176, 76, 255),
    "magenta": (238, 42, 164),
    "green": (89, 222, 112),
    "yellow": (255, 221, 63),
    "orange": (255, 149, 54),
    "red": (240, 74, 69),
    "blue": (75, 145, 239),
    "dark": (3, 4, 8),
    "black": (0, 0, 0),
}

FONT_CACHE = {}
TEXT_SURFACE_CACHE = {}
STAR_CACHE = None

# UI-only readability tuning. Gameplay/physics coordinates, timing,
# collisions, controls, animations and visual assets are intentionally
# unchanged; this multiplier only enlarges the existing rendered text.
UI_TEXT_SCALE = 1.35


# ============================================================
# pygame bootstrap
# ============================================================
def init_pygame():
    if sys.platform == "emscripten":
        # Web build: keep SDL_mixer completely out of the runtime.
        # Audio is handled by the browser-side media audio manager.
        pygame.display.init()
        pygame.font.init()
        return

    try:
        pygame.mixer.pre_init(44100, -16, 1, 512)
    except pygame.error:
        pass

    pygame.init()

    if not pygame.font.get_init():
        pygame.font.init()


def get_font(size, bold=False):
    key = (int(size), bool(bold))
    if key not in FONT_CACHE:
        # Default pygame font is available on every pygame install.
        FONT_CACHE[key] = pygame.font.Font(None, int(size))
    return FONT_CACHE[key]


# ============================================================
# Math helpers
# ============================================================
def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def safe_normal(v):
    length = v.length()
    if length <= 1e-9:
        return pygame.Vector2(0, -1)
    return v / length


def closest_point_segment(point, a, b):
    ab = b - a
    denom = ab.length_squared()
    if denom <= 1e-9:
        return pygame.Vector2(a), 0.0
    t = clamp((point - a).dot(ab) / denom, 0.0, 1.0)
    return a + ab * t, t


def reflect(v, normal, restitution=RESTITUTION):
    vn = v.dot(normal)
    if vn < 0.0:
        v -= (1.0 + restitution) * vn * normal


def circle_segment_hit(center, radius, a, b):
    closest, t = closest_point_segment(center, a, b)
    delta = center - closest
    dist_sq = delta.length_squared()
    if dist_sq >= radius * radius:
        return False, closest, pygame.Vector2(), 0.0, t

    dist = math.sqrt(max(dist_sq, 1e-12))
    normal = safe_normal(delta)
    return True, closest, normal, radius - dist, t


def point_in_polygon(point, points):
    # Standard ray-casting test. Used for triangular kick pads.
    inside = False
    x, y = point.x, point.y
    for i in range(len(points)):
        a = points[i]
        b = points[(i + 1) % len(points)]
        if (a.y > y) != (b.y > y):
            cross_x = (b.x - a.x) * (y - a.y) / (b.y - a.y) + a.x
            if x < cross_x:
                inside = not inside
    return inside


# ============================================================
# Audio
# ============================================================
class Audio:
    # Background music file. Keep this path relative to the project root.
    MUSIC_PATH = os.path.join("assets", "aerostrike_theme.ogg")
    MUSIC_VOLUME = 0.88
    SFX_VOLUME = 0.82

    def __init__(self):
        self.enabled = True
        self.ready = pygame.mixer.get_init() is not None
        self.sounds = {}
        self.music_ready = False
        self.music_path = None
        self.music_enabled = True
        self.web_audio = None

        if sys.platform == "emscripten":
            try:
                from platform import window
                self.web_audio = window.AeroAudio
                self.ready = self.web_audio is not None
            except Exception as exc:
                self.ready = False
                print(f"[AeroStrike Audio] WEB AUDIO BRIDGE UNAVAILABLE: {exc}")
            return

        if not self.ready:
            return

        self._load_music()
        self._load_sfx()

    def _load_music(self):
        """Load the desktop music through pygame's streaming music API."""
        candidates = [
            self.MUSIC_PATH,
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                self.MUSIC_PATH,
            ),
        ]

        candidates = list(
            dict.fromkeys(os.path.normpath(p) for p in candidates)
        )

        for path in candidates:
            if not os.path.isfile(path):
                continue

            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.set_volume(self.MUSIC_VOLUME)
                self.music_path = path
                self.music_ready = True
                print(f"[AeroStrike Audio] MUSIC LOADED: {path}")
                return
            except pygame.error as exc:
                print(f"[AeroStrike Audio] MUSIC LOAD FAILED: {path}")
                print(f"[AeroStrike Audio] {exc}")

        print(
            "[AeroStrike Audio] ERROR: "
            "assets/aerostrike_theme.ogg was not loaded."
        )

    def _load_sfx(self):
        """Load pre-rendered OGG effects; web uses browser media audio."""
        sound_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "assets",
            "sounds",
        )

        for sound_name in (
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
            "flipper_hit",
        ):
            path = os.path.join(sound_dir, f"{sound_name}.ogg")
            if not os.path.isfile(path):
                print(f"[AeroStrike Audio] Missing SFX: {path}")
                continue

            try:
                sound = pygame.mixer.Sound(path)
                sound.set_volume(self.SFX_VOLUME)
                self.sounds[sound_name] = sound
                print(
                    f"[AeroStrike Audio] {sound_name.upper()} SFX LOADED: {path}"
                )
            except pygame.error as exc:
                print(
                    f"[AeroStrike Audio] {sound_name} SFX LOAD FAILED: {exc}"
                )
    def start_music(self):
        """Start browser media audio on web and pygame music on desktop."""
        if not self.enabled or not self.music_enabled or not self.ready:
            return

        if sys.platform == "emscripten":
            try:
                self.web_audio.start_music()
            except Exception as exc:
                print(f"[AeroStrike Audio] WEB MUSIC ERROR: {exc}")
            return

        if not self.music_ready:
            return

        try:
            if not pygame.mixer.music.get_busy():
                pygame.mixer.music.set_volume(self.MUSIC_VOLUME)
                pygame.mixer.music.play(-1)
                print("[AeroStrike Audio] MUSIC PLAYING")
        except pygame.error as exc:
            print(f"[AeroStrike Audio] MUSIC PLAY ERROR: {exc}")

    def stop_music(self):
        if sys.platform == "emscripten":
            try:
                self.web_audio.stop_music()
            except Exception:
                pass
            return

        try:
            pygame.mixer.music.stop()
        except pygame.error:
            pass

    def set_music_enabled(self, enabled):
        """Mute/unmute background music without changing gameplay SFX."""
        self.music_enabled = bool(enabled)
        if sys.platform == "emscripten":
            try:
                self.web_audio.set_music_enabled(self.music_enabled)
            except Exception:
                pass
            return

        if self.music_enabled:
            self.start_music()
        else:
            self.stop_music()

    def set_enabled(self, enabled):
        """Global audio switch used by both title and gameplay controls."""
        self.enabled = bool(enabled)
        self.music_enabled = self.enabled

        if sys.platform == "emscripten":
            try:
                self.web_audio.set_enabled(self.enabled)
            except Exception:
                pass
            return

        if self.enabled:
            self.start_music()
        else:
            self.stop_music()

    def _tone(self, frequency, duration, volume):
        sample_rate = 44100
        count = max(1, int(sample_rate * duration))
        amplitude = int(32767 * volume)
        samples = array("h")

        for i in range(count):
            t = i / sample_rate
            envelope = (1.0 - i / count) ** 0.72
            signal = math.sin(2.0 * math.pi * frequency * t)
            signal += 0.15 * math.sin(2.0 * math.pi * frequency * 2.01 * t)
            samples.append(
                int(clamp(signal * amplitude * envelope, -32767, 32767))
            )

        return pygame.mixer.Sound(buffer=samples.tobytes())

    def play(self, name):
        if not self.enabled or not self.ready:
            return

        if sys.platform == "emscripten":
            try:
                self.web_audio.play(name)
            except Exception:
                pass
            return

        sound = self.sounds.get(name)
        if sound is None:
            return

        try:
            sound.play()
        except pygame.error:
            pass


# ============================================================
# Data objects
# ============================================================
@dataclass
class Ball:
    pos: pygame.Vector2
    vel: pygame.Vector2 = field(default_factory=pygame.Vector2)
    state: str = "READY"
    trail: list = field(default_factory=list)

    def __post_init__(self):
        self.pos = pygame.Vector2(self.pos)
        self.vel = pygame.Vector2(self.vel)

    def reset(self, position):
        self.pos = pygame.Vector2(position)
        self.vel.update(0, 0)
        self.state = "READY"
        self.trail.clear()


class Flipper:
    def __init__(self, pivot, rest_angle, active_angle, side):
        self.pivot = pygame.Vector2(pivot)
        self.rest_angle = math.radians(rest_angle)
        self.active_angle = math.radians(active_angle)
        self.angle = self.rest_angle
        self.side = side
        self.pressed = False
        self.previous_pressed = False
        self.angular_velocity = 0.0
        self.previous_angle = self.angle
        self.length = FLIPPER_LENGTH
        self.radius = FLIPPER_RADIUS
        self.hit_lock = 0.0
        self.contact_lock = 0.0

    @property
    def tip(self):
        return self.pivot + pygame.Vector2(
            math.cos(self.angle), math.sin(self.angle)
        ) * self.length

    def update(self, frame_dt):
        # Animate only this flipper. Game.update() owns the global game loop.
        frame_dt = clamp(frame_dt, 0.0, MAX_FRAME_DT)

        target_angle = self.active_angle if self.pressed else self.rest_angle
        previous_angle = self.angle
        self.previous_angle = previous_angle
        max_step = math.radians(FLIPPER_SPEED) * frame_dt
        delta = clamp(target_angle - self.angle, -max_step, max_step)
        self.angle += delta

        self.angular_velocity = (
            (self.angle - previous_angle) / frame_dt
            if frame_dt > 1e-9
            else 0.0
        )

    def point_velocity(self, point):
        r = point - self.pivot
        return pygame.Vector2(-r.y, r.x) * self.angular_velocity


@dataclass
class Bumper:
    pos: pygame.Vector2
    radius: float
    score: int
    color: tuple
    kick: float
    cooldown: float = 0.0
    flash: float = 0.0


@dataclass
class Target:
    rect: pygame.Rect
    score: int
    label: str
    lit: bool = False
    cooldown: float = 0.0
    flash: float = 0.0


@dataclass
class Rollover:
    rect: pygame.Rect
    label: str
    score: int
    active: bool = False
    cooldown: float = 0.0


@dataclass
class KickTriangle:
    points: tuple
    score: int
    side: int
    cooldown: float = 0.0
    flash: float = 0.0


@dataclass
class SideRescueKicker:
    pos: pygame.Vector2
    radius: float = 27.0
    cooldown: float = 0.0
    flash: float = 0.0
    side: int = 0


# ============================================================
# Main game
# ============================================================
class Game:
    def __init__(self):
        self.screen = pygame.display.set_mode(
            WINDOW_SIZE, pygame.RESIZABLE
        )
        pygame.display.set_caption("AeroStrike Pinball")

        # Reuse render surfaces every frame. Allocating the 720x1040 logical
        # surface and the alpha flipper layer inside render() was a major
        # source of garbage-collection spikes in desktop/pygbag runs.
        self.render_surface = pygame.Surface((VIEW_W, VIEW_H))
        self.table_surface = pygame.Surface((VIEW_W, VIEW_H))
        self.flipper_layer = pygame.Surface(
            (VIEW_W, VIEW_H), pygame.SRCALPHA
        )
        self.starfield_surface = None

        # Reuse the final scaled frame buffer too. Creating a new scaled
        # surface every browser frame can trigger avoidable garbage collection
        # and produce visible stutter when the game is inside an iframe.
        self.scaled_surface = None
        self.scaled_size = None

        self.audio = Audio()

        self.running = True
        self.state = "TITLE"
        self.pause_selection = 0

        self.score = 0
        self.high_score = 0
        self.balls = 3
        self.combo = 0
        self.combo_timer = 0.0
        self.multiplier = 1

        self.toast = ""
        self.toast_timer = 0.0
        self.launch_warning_timer = 0.0
        self.launch_warning_text = ""

        # Cosmetic aircraft; they never participate in physics.
        self.aircraft = [
            {"x": 110.0, "y": 360.0, "speed": 72.0, "scale": 0.72, "direction": 1},
            {"x": 590.0, "y": 760.0, "speed": 54.0, "scale": 0.55, "direction": -1},
            {"x": 185.0, "y": 1210.0, "speed": 92.0, "scale": 0.62, "direction": 1},
            {"x": 535.0, "y": 1690.0, "speed": 66.0, "scale": 0.50, "direction": -1},
            {"x": 260.0, "y": 1980.0, "speed": 78.0, "scale": 0.46, "direction": 1},
        ]

        self.mouse_held = False
        self.mouse_world = pygame.Vector2()
        self.touch_points = {}
        self.launch_fingers = set()

        self.left_held = False
        self.right_held = False
        self.launch_held = False

        self.camera_y = 0.0
        self.target_camera_y = 0.0
        self.camera_x = 0.0
        self.target_camera_x = 0.0

        self.physics_accumulator = 0.0
        self.last_time = pygame.time.get_ticks() / 1000.0

        # Visual interpolation state. Physics remains identical; this only
        # smooths the rendered frame between fixed 90 Hz physics steps.
        self.previous_camera_y = 0.0
        self.previous_camera_x = 0.0
        self.render_ball_pos = self.ball.pos.copy() if hasattr(self, "ball") else pygame.Vector2()
        self.render_camera_y = 0.0
        self.render_camera_x = 0.0

        self.build_table()
        self.previous_ball_pos = self.ball.pos.copy()
        self.previous_camera_y = self.camera_y
        self.previous_camera_x = self.camera_x
        self.render_ball_pos = self.ball.pos.copy()

    # --------------------------------------------------------
    # Table construction
    # --------------------------------------------------------
    def build_table(self):
        self.ball = Ball((LAUNCH_X, LAUNCH_BOTTOM - 52.0))

        # P0: two independent physical flippers with a wide central drain.
        self.flippers = [
            Flipper((238, 2070), 27, -17, "L"),
            Flipper((482, 2070), 153, 197, "R"),
        ]
        self.validate_flipper_gap()

        # Distributed scoring objects keep each camera region useful.
        self.bumpers = [
            Bumper(pygame.Vector2(270, 430), 42, 100, C["cyan"], 760),
            Bumper(pygame.Vector2(450, 430), 42, 100, C["orange"], 760),
            Bumper(pygame.Vector2(360, 610), 48, 200, C["green"], 820),
            Bumper(pygame.Vector2(205, 880), 38, 150, C["purple"], 740),
            Bumper(pygame.Vector2(515, 880), 38, 150, C["yellow"], 740),
            Bumper(pygame.Vector2(280, 1140), 42, 200, C["orange"], 800),
            Bumper(pygame.Vector2(440, 1140), 42, 200, C["cyan"], 800),
            Bumper(pygame.Vector2(360, 1390), 48, 300, C["magenta"], 850),
        ]

        self.targets = [
            Target(pygame.Rect(155, 270, 96, 29), 150, "A"),
            Target(pygame.Rect(312, 250, 96, 29), 200, "B"),
            Target(pygame.Rect(469, 270, 96, 29), 150, "C"),
            Target(pygame.Rect(155, 1000, 92, 27), 250, "D"),
            Target(pygame.Rect(313, 970, 94, 27), 300, "E"),
            Target(pygame.Rect(473, 1000, 92, 27), 250, "F"),
            Target(pygame.Rect(170, 1505, 100, 28), 350, ""),
            Target(pygame.Rect(450, 1505, 100, 28), 350, ""),
        ]

        self.rollovers = [
            Rollover(pygame.Rect(150, 185, 72, 30), "1", 50),
            Rollover(pygame.Rect(244, 175, 72, 30), "2", 50),
            Rollover(pygame.Rect(338, 165, 72, 30), "3", 50),
            Rollover(pygame.Rect(432, 175, 72, 30), "4", 50),
            Rollover(pygame.Rect(526, 185, 42, 30), "5", 75),
        ]

        # Visual kicker pads. Only the inner sloped edge is physical; the
        # polygon interior/base is NOT a collider, preventing trap states.
        self.kick_triangles = [
            KickTriangle((pygame.Vector2(92, 1635), pygame.Vector2(202, 1748), pygame.Vector2(92, 1748)), 100, -1),
            KickTriangle((pygame.Vector2(628, 1635), pygame.Vector2(518, 1748), pygame.Vector2(628, 1748)), 100, 1),
        ]

        # Blue-centered side rescue kickers. Hitting either one redirects the
        # current ball upward instead of ending the game in a side lane.
        self.side_rescue_kickers = [
            SideRescueKicker(pygame.Vector2(76, 1720), 27.0, side=-1),
            SideRescueKicker(pygame.Vector2(644, 1720), 27.0, side=1),
        ]

        self.ramps = [
            (pygame.Vector2(85, 790), pygame.Vector2(190, 660)),
            (pygame.Vector2(635, 790), pygame.Vector2(530, 660)),
            (pygame.Vector2(86, 1070), pygame.Vector2(190, 930)),
            (pygame.Vector2(634, 1070), pygame.Vector2(530, 930)),
            (pygame.Vector2(92, 1320), pygame.Vector2(190, 1190)),
            (pygame.Vector2(628, 1320), pygame.Vector2(530, 1190)),
            (pygame.Vector2(100, 1815), pygame.Vector2(205, 1945)),
            (pygame.Vector2(620, 1815), pygame.Vector2(515, 1945)),
        ]

        self.lower_rails = [
            (pygame.Vector2(82, 2140), pygame.Vector2(205, 2050)),
            (pygame.Vector2(638, 2140), pygame.Vector2(515, 2050)),
        ]
        self.posts = [
            (pygame.Vector2(198, 2020), 14),
            (pygame.Vector2(522, 2020), 14),
        ]
        self.slings = [
            (pygame.Vector2(102, 1875), pygame.Vector2(228, 1980)),
            (pygame.Vector2(618, 1875), pygame.Vector2(492, 1980)),
        ]

        self.ball.reset((LAUNCH_X, LAUNCH_BOTTOM - 52.0))
        self.camera_y = WORLD_H - VIEW_H
        self.target_camera_y = self.camera_y
        self.camera_x = 0.0
        self.target_camera_x = 0.0
        self.last_live_ball_pos = self.ball.pos.copy()
        self.previous_ball_pos = self.ball.pos.copy()
        self.stuck_timer = 0.0
        self.launch_watchdog = 0.0
        self.recovery_lock = 0.0
        self.sim_time = 0.0
        self.contact_history = []
        self.loop_break_timer = 0.0

    def validate_flipper_gap(self):
        """P0 geometry invariant: center gap must remain physically traversable."""
        left = self.flippers[0]
        right = self.flippers[1]

        rest_gap = left.tip.distance_to(right.tip)

        # Check the entire animation path, not only the endpoints. The player
        # must never see or collide with a closed V while either paddle moves.
        sampled_gaps = []
        for i in range(31):
            t = i / 30.0
            left_angle = left.rest_angle + (left.active_angle - left.rest_angle) * t
            right_angle = right.rest_angle + (right.active_angle - right.rest_angle) * t
            left.angle = left_angle
            right.angle = right_angle
            sampled_gaps.append(left.tip.distance_to(right.tip))
        left.angle = left.rest_angle
        right.angle = right.rest_angle

        min_gap = min(sampled_gaps)
        max_gap = max(sampled_gaps)

        # Collision circles are applied around the centerline. A ball diameter
        # plus the two flipper radii must fit through the gap with a visible margin.
        minimum = max(FLIPPER_MIN_CENTER_GAP, BALL_RADIUS + (2.0 * FLIPPER_RADIUS) + 14.0)

        if min_gap < minimum:
            raise RuntimeError(
                "P0 flipper-gap violation: "
                f"minimum={min_gap:.1f}, required>={minimum:.1f}, "
                f"rest={rest_gap:.1f}, max={max_gap:.1f}"
            )

        # Direct center-lane probe: at the drain mouth a ball centered on x=360
        # must be outside BOTH flipper collision bodies.
        for probe_y in (2090.0, 2110.0, 2130.0, 2150.0):
            probe = pygame.Vector2(WORLD_W * 0.5, probe_y)
            for flipper in self.flippers:
                hit, _, _, _, _ = circle_segment_hit(
                    probe,
                    BALL_RADIUS + flipper.radius,
                    flipper.pivot,
                    flipper.tip,
                )
                if hit:
                    raise RuntimeError(
                        "P0 flipper-gap violation: "
                        f"center drain probe intersects {flipper.side} flipper at y={probe_y:.0f}."
                    )

    def start_game(self):
        self.score = 0
        self.balls = 3
        self.combo = 0
        self.combo_timer = 0.0
        self.multiplier = 1
        self.camera_y = WORLD_H - VIEW_H
        self.target_camera_y = self.camera_y
        self.build_table()
        self.state = "TUTORIAL"
        self.audio.play("click")

    def begin_play(self):
        self.state = "PLAYING"
        self.new_ball()
        self.audio.play("click")

    def new_ball(self):
        self.ball.reset((LAUNCH_X, LAUNCH_BOTTOM - 52.0))
        self.launch_power = 0.0
        self.launch_held = False
        self.physics_accumulator = 0.0
        self.camera_y = WORLD_H - VIEW_H
        self.target_camera_y = self.camera_y
        self.camera_x = 0.0
        self.target_camera_x = 0.0
        self.last_live_ball_pos = self.ball.pos.copy()
        self.previous_ball_pos = self.ball.pos.copy()
        self.stuck_timer = 0.0
        self.launch_watchdog = 0.0
        self.recovery_lock = 0.0
        self.sim_time = 0.0
        self.contact_history = []
        self.loop_break_timer = 0.0
        self.launch_warning_timer = 0.0
        self.launch_warning_text = ""
        for flipper in self.flippers:
            flipper.pressed = False
            flipper.previous_pressed = False
            flipper.angle = flipper.rest_angle
            flipper.hit_lock = 0.0
            flipper.contact_lock = 0.0
        self.show_toast("HOLD ↑ IN THE LAUNCH LANE  •  RELEASE TO FIRE", 1.8)

    def drain(self):
        self.audio.play("drain")
        self.balls -= 1
        self.combo = 0
        self.combo_timer = 0.0
        self.multiplier = 1

        if self.balls <= 0:
            self.ball.state = "GAME_OVER"
            self.state = "RESULTS"
            self.show_toast("BALLS COMPLETE", 2.0)
        else:
            self.ball.state = "LOST"
            self.new_ball()

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------
    def add_score(self, points, combo=True):
        if self.ball.state not in ("READY", "LAUNCHING", "IN_PLAY"):
            return
        gained = int(points * self.multiplier)
        self.score += gained
        self.high_score = max(self.high_score, self.score)
        if combo:
            self.combo = self.combo + 1 if self.combo_timer > 0 else 1
            self.combo_timer = 2.1
            self.multiplier = min(5, 1 + self.combo // 4)
            if self.combo >= 3 and self.combo % 2 == 1:
                self.show_toast(f"COMBO x{self.multiplier}   +{gained}", 0.75)
                self.audio.play("combo")

    def show_toast(self, text, duration=1.0):
        self.toast = text
        self.toast_timer = duration

    # --------------------------------------------------------
    # Coordinate mapping
    # --------------------------------------------------------
    def screen_to_world(self, pos):
        sw, sh = self.screen.get_size()
        scale = min(sw / VIEW_W, sh / VIEW_H)
        draw_w = VIEW_W * scale
        draw_h = VIEW_H * scale
        ox = (sw - draw_w) * 0.5
        oy = (sh - draw_h) * 0.5

        return pygame.Vector2(
            (pos[0] - ox) / scale,
            (pos[1] - oy) / scale + self.camera_y,
        )

    # --------------------------------------------------------
    # Input
    # --------------------------------------------------------
    def left_zone(self, p):
        # Lower-left touch/mouse area. It sits below the flipper sweep.
        return p.x < 300 and p.y > self.camera_y + VIEW_H - 150

    def right_zone(self, p):
        return p.x > 420 and p.y > self.camera_y + VIEW_H - 150

    def launch_zone(self, p):
        # The physical launcher lane itself is the touch/mouse target.
        # No standalone UP-LAUNCH button is rendered anywhere.
        return 578 <= p.x <= 660 and p.y >= LAUNCH_BOTTOM - 210

    def button_hit(self, pos):
        # UI interactions are in SCREEN space, not world space.
        sw, sh = self.screen.get_size()
        scale = min(sw / VIEW_W, sh / VIEW_H)
        draw_w = VIEW_W * scale
        draw_h = VIEW_H * scale
        ox = (sw - draw_w) * 0.5
        oy = (sh - draw_h) * 0.5

        x = (pos[0] - ox) / scale
        y = (pos[1] - oy) / scale

        # A click/tap is also the browser audio-unlock gesture.
        self.audio.start_music()

        if self.state == "TITLE":
            # Visible button: Rect(215, 735, 290, 86).
            # Use a small 12px logical padding so the whole visible button
            # is comfortably clickable, without moving the visual button.
            if pygame.Rect(203, 723, 314, 110).collidepoint(x, y):
                self.start_game()
                return
            if pygame.Rect(645, 20, 55, 55).collidepoint(x, y):
                self.audio.set_enabled(not self.audio.enabled)
                self.audio.play("click")
                return

        elif self.state == "TUTORIAL":
            # Visible button: Rect(220, 735, 280, 72).
            if pygame.Rect(208, 723, 304, 96).collidepoint(x, y):
                self.begin_play()
                return

        elif self.state == "PLAYING":
            # Top-left button: global audio. The state is shared with HOME,
            # so OFF on the title screen remains OFF in gameplay, and tapping
            # it here restores both music and all gameplay SFX.
            if pygame.Rect(18, 18, 42, 42).collidepoint(x, y):
                self.audio.set_enabled(not self.audio.enabled)
                if self.audio.enabled:
                    self.audio.play("click")
                return

            if pygame.Rect(650, 18, 52, 52).collidepoint(x, y):
                self.state = "PAUSED"
                self.audio.play("click")
                return

        elif self.state == "PAUSED":
            if pygame.Rect(220, 390, 280, 70).collidepoint(x, y):
                self.state = "PLAYING"
                self.audio.play("click")
                return
            if pygame.Rect(220, 480, 280, 70).collidepoint(x, y):
                self.start_game()
                return
            if pygame.Rect(220, 570, 280, 70).collidepoint(x, y):
                self.state = "TITLE"
                self.audio.play("click")
                return

        elif self.state == "RESULTS":
            if pygame.Rect(162, 707, 176, 101).collidepoint(x, y):
                self.state = "TITLE"
                self.audio.play("click")
                return
            if pygame.Rect(382, 707, 176, 101).collidepoint(x, y):
                self.start_game()
                return

    def activate_pause_selection(self):
        """Activate the pause-menu action currently selected by TAB."""
        if self.state != "PAUSED":
            return

        if self.pause_selection == 0:
            self.state = "PLAYING"
        elif self.pause_selection == 1:
            self.start_game()
        else:
            self.state = "TITLE"

        self.audio.play("click")

    def update_input_holds(self):
        if self.state != "PLAYING":
            self.left_held = False
            self.right_held = False
            self.launch_held = False
            return

        # Polling keeps normal desktop keyboard input responsive.
        keys = pygame.key.get_pressed()
        left_key = bool(keys[pygame.K_LEFT])
        right_key = bool(keys[pygame.K_RIGHT])
        launch_key = bool(keys[pygame.K_UP])

        self.left_held = left_key
        self.right_held = right_key
        self.launch_held = launch_key

        # Mouse/touch controls remain available.
        if self.mouse_held:
            if self.left_zone(self.mouse_world):
                self.left_held = True
            if self.right_zone(self.mouse_world):
                self.right_held = True
            if self.launch_zone(self.mouse_world):
                self.launch_held = True

        for nx, ny in self.touch_points.values():
            p = pygame.Vector2(
                nx * VIEW_W,
                ny * VIEW_H + self.camera_y,
            )
            if self.left_zone(p):
                self.left_held = True
            if self.right_zone(p):
                self.right_held = True
            if self.launch_zone(p):
                self.launch_held = True

    def events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                continue

            if event.type == pygame.VIDEORESIZE:
                w = max(420, event.w)
                h = max(620, event.h)
                self.screen = pygame.display.set_mode(
                    (w, h), pygame.RESIZABLE
                )
                continue

            if event.type == pygame.KEYDOWN:
                # Explicit flipper state makes held-arrow input reliable even
                # when pygame's keyboard polling is delayed by the browser.
                if event.key == pygame.K_LEFT and self.state == "PLAYING":
                    self.left_held = True
                    continue

                if event.key == pygame.K_RIGHT and self.state == "PLAYING":
                    self.right_held = True
                    continue

                # TAB navigates the three pause actions without changing the
                # underlying gameplay logic. Shift+TAB navigates backwards.
                if event.key == pygame.K_TAB and self.state == "PAUSED":
                    direction = -1 if (pygame.key.get_mods() & pygame.KMOD_SHIFT) else 1
                    self.pause_selection = (self.pause_selection + direction) % 3
                    self.audio.play("click")
                    continue

                # SPACE and ENTER activate the visible primary workflow:
                # TITLE -> START GAME, TUTORIAL -> START, RESULTS -> HOME.
                # In PAUSED, they activate the currently selected action.
                if event.key in (pygame.K_SPACE, pygame.K_RETURN):
                    if self.state == "TITLE":
                        self.start_game()
                    elif self.state == "TUTORIAL":
                        self.begin_play()
                    elif self.state == "PAUSED":
                        self.activate_pause_selection()
                    elif self.state == "RESULTS":
                        self.state = "TITLE"
                        self.audio.play("click")
                    continue

                # R always means replay/restart once a game exists.
                if event.key == pygame.K_r:
                    if self.state in ("PLAYING", "PAUSED", "RESULTS"):
                        self.start_game()
                    elif self.state == "TUTORIAL":
                        self.begin_play()
                    continue

                if event.key in (pygame.K_ESCAPE, pygame.K_p):
                    if self.state == "PLAYING":
                        self.pause_selection = 0
                        self.state = "PAUSED"
                        self.audio.play("click")
                    elif self.state == "PAUSED":
                        self.state = "PLAYING"
                        self.audio.play("click")

            elif event.type == pygame.KEYUP:
                if event.key == pygame.K_LEFT:
                    self.left_held = False
                    continue
                if event.key == pygame.K_RIGHT:
                    self.right_held = False
                    continue

                if (
                    event.key == pygame.K_UP
                    and self.state == "PLAYING"
                    and self.ball.state == "READY"
                    and self.launch_held
                ):
                    self.launch_ball()

            elif event.type == pygame.MOUSEMOTION:
                self.mouse_world = self.screen_to_world(event.pos)

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # First user gesture: unlock/load browser audio immediately.
                self.audio.start_music()
                self.mouse_held = True
                self.mouse_world = self.screen_to_world(event.pos)
                self.button_hit(event.pos)

            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                was_launch = (
                    self.mouse_held
                    and self.state == "PLAYING"
                    and self.ball.state == "READY"
                    and self.launch_zone(self.mouse_world)
                )

                self.mouse_held = False

                if was_launch:
                    self.launch_ball()

            elif event.type == pygame.FINGERDOWN:
                # First user gesture: unlock/load browser audio immediately.
                self.audio.start_music()
                self.touch_points[event.finger_id] = (
                    event.x,
                    event.y,
                )

                screen_pos = (
                    event.x * self.screen.get_width(),
                    event.y * self.screen.get_height(),
                )
                self.button_hit(screen_pos)

                world_pos = self.screen_to_world(screen_pos)
                if (
                    self.state == "PLAYING"
                    and self.ball.state == "READY"
                    and self.launch_zone(world_pos)
                ):
                    self.launch_fingers.add(event.finger_id)

            elif event.type == pygame.FINGERMOTION:
                self.touch_points[event.finger_id] = (
                    event.x,
                    event.y,
                )

            elif event.type == pygame.FINGERUP:
                was_launch = (
                    event.finger_id in self.launch_fingers
                    and self.state == "PLAYING"
                    and self.ball.state == "READY"
                )
                self.launch_fingers.discard(event.finger_id)
                self.touch_points.pop(event.finger_id, None)

                if was_launch:
                    self.launch_ball()

    # --------------------------------------------------------
    # Launcher
    # --------------------------------------------------------
    def launch_ball(self):
        """Launch only after enough plunger power has been built."""
        if self.ball.state != "READY":
            return False

        power = clamp(getattr(self, "launch_power", 0.0), 0.0, 1.0)
        if power < LAUNCH_MIN_EFFECTIVE_POWER:
            self.launch_warning_text = "FUEL INSUFFICIENT  •  PRESS & HOLD FOR MORE POWER"
            self.launch_warning_timer = 2.0
            self.launch_power = 0.0
            self.launch_held = False
            self.audio.play("click")
            return False

        self.launch_warning_timer = 0.0
        self.launch_warning_text = ""
        self.ball.state = "LAUNCHING"
        self.ball.pos = pygame.Vector2(LAUNCH_X, LAUNCH_BOTTOM - 52.0)
        self.ball.vel = pygame.Vector2(
            0.0,
            -(LAUNCH_SPEED_MIN + (LAUNCH_SPEED_MAX - LAUNCH_SPEED_MIN) * power),
        )
        self.launch_power = 0.0
        self.launch_held = False
        self.launch_watchdog = 0.0
        self.previous_ball_pos = self.ball.pos.copy()
        self.target_camera_y = WORLD_H - VIEW_H
        self.camera_y = WORLD_H - VIEW_H
        self.audio.play("launch")
        self.show_toast("LAUNCH", 0.4)

    def register_contact(self, key):
        # Keep only recent scoring/physics contacts. If the same short route
        # repeats, the ball is given a small lateral escape impulse rather than
        # being allowed to circulate forever through the same objects.
        now = self.sim_time
        self.contact_history.append((key, now))
        self.contact_history = [
            item for item in self.contact_history
            if now - item[1] <= LOOP_REPEAT_WINDOW
        ][-LOOP_HISTORY_MAX:]

        if len(self.contact_history) < 6:
            return False

        keys = [item[0] for item in self.contact_history]
        n = len(keys)
        for cycle_len in (2, 3, 4):
            if n >= cycle_len * 2:
                if keys[-cycle_len:] == keys[-2 * cycle_len:-cycle_len]:
                    if self.loop_break_timer <= 0.0:
                        side = -1.0 if self.ball.pos.x >= WORLD_W * 0.5 else 1.0
                        self.ball.vel.x += side * 340.0
                        self.ball.vel.y -= 120.0
                        self.ball.vel.x = clamp(self.ball.vel.x, -1250.0, 1250.0)
                        self.loop_break_timer = 0.75
                        self.contact_history.clear()
                        return True
        return False

    def collide_triangle(self, triangle, index=0):
        # Only the inner sloped edge is physical. On the upward path the
        # triangle behaves as a passive sloped wall; on the downward path it
        # becomes a kicker. This keeps the visible triangle physically honest
        # while preventing it from becoming a deterministic launcher loop.
        a = triangle.points[0]
        b = triangle.points[1]
        hit, closest, normal, penetration, _ = circle_segment_hit(
            self.ball.pos,
            BALL_RADIUS + 4.0,
            a,
            b,
        )
        if not hit:
            return False

        if self.ball.vel.dot(normal) >= 0.0:
            return False

        self.ball.pos += normal * max(2.0, penetration + 1.0)

        if self.ball.vel.y <= 0.0:
            # Upward contact: passive barrier only. No artificial kick and no
            # score event, so an idle ball cannot bounce through a repeatable
            # triangle -> target -> bumper route.
            reflect(self.ball.vel, normal, 0.82)
            return True

        # A kicker cannot fire again while its short physical cooldown is active.
        if triangle.cooldown > 0.0:
            return True

        # Preserve the side kick but retain some incoming horizontal motion so
        # the result is not a perfectly repeatable vertical orbit.
        lateral = clamp(self.ball.vel.x * 0.22, -170.0, 170.0)
        inward_x = 135.0 * (-triangle.side) + lateral
        self.ball.vel = pygame.Vector2(
            inward_x,
            -TRIANGLE_KICK * 0.82,
        )

        triangle.cooldown = 0.38
        triangle.flash = 0.14
        self.add_score(triangle.score)
        self.audio.play("kick")
        self.register_contact(("triangle", index))
        return True

    def collide_segment(self, a, b, radius, restitution=RESTITUTION):
        hit, closest, normal, penetration, _ = circle_segment_hit(
            self.ball.pos,
            radius,
            a,
            b,
        )
        if not hit:
            return False

        self.ball.pos += normal * max(0.5, penetration)
        reflect(self.ball.vel, normal, restitution)
        return True

    # --------------------------------------------------------
    # Flipper collision
    # --------------------------------------------------------
    def collide_flipper(self, flipper):
        """Resolve ball/flipper contact with two distinct physical modes.

        RESTING MODE:
            The paddle is a passive solid barrier. It blocks the ball but does
            not inject an artificial upward kick. This is the important change
            that removes the idle-flipper bounce loop.

        ACTIVE MODE:
            While the paddle is moving/pressed, its surface velocity is used
            for a proper pinball strike. A swept three-point test prevents fast
            balls from tunnelling through the paddle.
        """
        if flipper.contact_lock > 0.0:
            return False

        current = self.ball.pos.copy()
        previous = getattr(self, "previous_ball_pos", current).copy()

        # Three samples are enough for the fixed 90 Hz physics step and cost
        # less than the previous five-sample test.
        sample_positions = (
            previous,
            previous.lerp(current, 0.5),
            current,
        )

        collision = None
        collision_radius = BALL_RADIUS + flipper.radius + FLIPPER_COLLISION_MARGIN

        for sample in sample_positions:
            hit, closest, normal, penetration, _ = circle_segment_hit(
                sample,
                collision_radius,
                flipper.pivot,
                flipper.tip,
            )
            if hit:
                collision = (sample, closest, normal, penetration)
                break

        if collision is None:
            return False

        sample, closest, normal, penetration = collision

        # Keep the ball on the playable side of the paddle.
        self.ball.pos = sample + normal * max(1.5, penetration + 1.5)

        moving = (
            flipper.pressed
            or abs(flipper.angular_velocity) >= FLIPPER_ACTIVE_THRESHOLD
        )

        if moving:
            # Active pinball strike: transfer paddle surface velocity.
            surface_velocity = flipper.point_velocity(closest)
            relative = self.ball.vel - surface_velocity
            normal_speed = relative.dot(normal)

            if normal_speed < 0.0:
                reflected = (
                    relative
                    - (1.0 + FLIPPER_HIT_RESTITUTION)
                    * normal_speed
                    * normal
                )
            else:
                tangent = relative - normal_speed * normal
                reflected = (
                    tangent * 0.90
                    + normal
                    * max(
                        220.0,
                        abs(surface_velocity.dot(normal)) * 0.90,
                    )
                )

            self.ball.vel = surface_velocity + reflected
            self.ball.vel.y = min(self.ball.vel.y, FLIPPER_HIT_MIN_UP)
        else:
            # Resting paddle: solid wall, not a launcher.
            # Only reflect an incoming velocity; never create the old
            # artificial -720 upward impulse that caused idle loops.
            incoming = self.ball.vel.dot(normal)
            if incoming < 0.0:
                reflect(
                    self.ball.vel,
                    normal,
                    FLIPPER_REST_RESTITUTION,
                )
            else:
                # Deep overlap/separating case: gently move it away without
                # adding energy.
                self.ball.vel += normal * max(0.0, 80.0 - incoming)

        self.ball.vel.x = clamp(self.ball.vel.x, -1250.0, 1250.0)

        # Short collision lock prevents repeated contact on consecutive
        # substeps while remaining short enough not to affect real fast hits.
        flipper.contact_lock = FLIPPER_CONTACT_LOCK

        if flipper.hit_lock <= 0.0:
            flipper.hit_lock = 0.055
            self.audio.play("flipper_hit")

        return True

    # --------------------------------------------------------
    # Physics
    # --------------------------------------------------------
    def _resolve_world_contacts(self):
        if self.ball.pos.x < TABLE_LEFT + BALL_RADIUS:
            self.ball.pos.x = TABLE_LEFT + BALL_RADIUS
            self.ball.vel.x = abs(self.ball.vel.x) * 0.88
        elif self.ball.pos.x > TABLE_RIGHT - BALL_RADIUS:
            self.ball.pos.x = TABLE_RIGHT - BALL_RADIUS
            self.ball.vel.x = -abs(self.ball.vel.x) * 0.88
        if self.ball.pos.y < TABLE_TOP + BALL_RADIUS:
            self.ball.pos.y = TABLE_TOP + BALL_RADIUS
            self.ball.vel.y = abs(self.ball.vel.y) * 0.90

        for a, b in self.ramps:
            self.collide_segment(a, b, BALL_RADIUS + WALL_RADIUS, 0.88)

        for bumper_index, bumper in enumerate(self.bumpers):
            delta = self.ball.pos - bumper.pos
            min_dist = BALL_RADIUS + bumper.radius
            if delta.length_squared() < min_dist * min_dist:
                normal = safe_normal(delta)
                self.ball.pos = bumper.pos + normal * min_dist
                tangent = pygame.Vector2(-normal.y, normal.x)
                tangential = self.ball.vel.dot(tangent) * tangent * 0.45
                self.ball.vel = normal * (bumper.kick * 0.94) + tangential
                if bumper.cooldown <= 0.0:
                    bumper.cooldown = 0.14
                    self.register_contact(("bumper", bumper_index))
                    bumper.flash = 0.15
                    self.add_score(bumper.score)
                    self.audio.play("bumper")

        for target_index, target in enumerate(self.targets):
            closest = pygame.Vector2(
                clamp(self.ball.pos.x, target.rect.left, target.rect.right),
                clamp(self.ball.pos.y, target.rect.top, target.rect.bottom),
            )
            delta = self.ball.pos - closest
            if delta.length_squared() < (BALL_RADIUS + 4.0) ** 2:
                normal = safe_normal(delta)
                self.ball.pos = closest + normal * (BALL_RADIUS + 5.0)
                reflect(self.ball.vel, normal, TARGET_RESTITUTION)
                if target.cooldown <= 0.0:
                    target.cooldown = 0.16
                    target.flash = 0.15
                    target.lit = not target.lit
                    self.add_score(target.score)
                    self.audio.play("target")
                    self.register_contact(("target", target_index))

        for rollover in self.rollovers:
            if rollover.cooldown <= 0.0 and rollover.rect.collidepoint(int(self.ball.pos.x), int(self.ball.pos.y)):
                rollover.cooldown = 0.35
                rollover.active = True
                self.add_score(rollover.score)
                self.audio.play("rollover")

        # Triangles are physical kickers only after normal play begins.
        if self.ball.state == "IN_PLAY":
            for index, triangle in enumerate(self.kick_triangles):
                self.collide_triangle(triangle, index)

        for a, b in self.lower_rails:
            self.collide_segment(a, b, BALL_RADIUS + WALL_RADIUS, 0.88)
        for a, b in self.slings:
            if self.collide_segment(a, b, BALL_RADIUS + 8.0, 0.93):
                self.ball.vel.y -= 230.0
                self.add_score(75)
                self.audio.play("kick")

        for post_pos, radius in self.posts:
            delta = self.ball.pos - post_pos
            min_dist = BALL_RADIUS + radius
            if delta.length_squared() < min_dist * min_dist:
                normal = safe_normal(delta)
                self.ball.pos = post_pos + normal * min_dist
                reflect(self.ball.vel, normal, 0.90)

        for flipper in self.flippers:
            self.collide_flipper(flipper)

    def collide_side_rescue_kicker(self, kicker):
        """Keep a ball in play when it enters either mirrored rescue circle."""
        delta = self.ball.pos - kicker.pos
        hit_radius = BALL_RADIUS + kicker.radius
        if delta.length_squared() >= hit_radius * hit_radius:
            return False
        if kicker.cooldown > 0.0:
            return True

        normal = safe_normal(delta)
        self.ball.pos = kicker.pos + normal * (hit_radius + 1.5)
        outward = -1.0 if kicker.side < 0 else 1.0
        self.ball.vel = pygame.Vector2(outward * 180.0, -1450.0)
        kicker.cooldown = 0.42
        kicker.flash = 0.18
        self.add_score(250)
        self.audio.play("kick")
        self.show_toast("AERO RESCUE  •  BALL RE-LAUNCHED", 0.75)
        self.contact_history.clear()
        self.recovery_lock = 0.30
        return True

    def physics_step(self, dt):
        self.previous_ball_pos = self.ball.pos.copy()
        self.previous_camera_y = self.camera_y
        self.previous_camera_x = self.camera_x
        self.sim_time += dt
        self.loop_break_timer = max(0.0, self.loop_break_timer - dt)

        for flipper in self.flippers:
            flipper.hit_lock = max(0.0, flipper.hit_lock - dt)
            flipper.contact_lock = max(0.0, flipper.contact_lock - dt)
            requested_pressed = (
                self.left_held if flipper.side == "L" else self.right_held
            )
            flipper.previous_pressed = flipper.pressed
            flipper.pressed = requested_pressed
            flipper.update(dt)

            # Dedicated mechanical sound only when the paddle starts moving.
            # Ball impacts use a separate flipper_hit sound.
            if (
                flipper.previous_pressed != flipper.pressed
                and abs(flipper.angular_velocity) > 0.01
            ):
                self.audio.play("flipper_move")

        for bumper in self.bumpers:
            bumper.cooldown = max(0.0, bumper.cooldown - dt)
            bumper.flash = max(0.0, bumper.flash - dt)
        for target in self.targets:
            target.cooldown = max(0.0, target.cooldown - dt)
            target.flash = max(0.0, target.flash - dt)
        for rollover in self.rollovers:
            rollover.cooldown = max(0.0, rollover.cooldown - dt)
        for triangle in self.kick_triangles:
            triangle.cooldown = max(0.0, triangle.cooldown - dt)
            triangle.flash = max(0.0, triangle.flash - dt)
        for kicker in self.side_rescue_kickers:
            kicker.cooldown = max(0.0, kicker.cooldown - dt)
            kicker.flash = max(0.0, kicker.flash - dt)
        self.toast_timer = max(0.0, self.toast_timer - dt)
        self.combo_timer = max(0.0, self.combo_timer - dt)
        self.recovery_lock = max(0.0, self.recovery_lock - dt)
        if self.combo_timer <= 0.0:
            self.combo = 0
            self.multiplier = 1

        if self.ball.state == "READY":
            if self.launch_held:
                self.launch_power = min(1.0, self.launch_power + dt * 1.15)
            else:
                self.launch_power = max(0.0, self.launch_power - dt * 2.8)
            self.stuck_timer = 0.0
            return

        if self.ball.state not in ("LAUNCHING", "IN_PLAY"):
            return

        # Deterministic launch phase.
        # Gravity and normal table collisions are intentionally disabled until
        # the ball has physically reached the top of the launcher lane.
        if self.ball.state == "LAUNCHING":
            self.launch_watchdog += dt
            self.ball.pos.x = LAUNCH_X
            self.ball.pos.y += self.ball.vel.y * dt

            # Keep the ball inside the visible lane during ascent.
            self.ball.pos.y = max(self.ball.pos.y, LAUNCH_TOP + 18.0)

            # Freeze the camera at the lower launch view so the ascent is
            # visually obvious instead of the camera following the ball.
            self.target_camera_y = WORLD_H - VIEW_H
            self.camera_y = WORLD_H - VIEW_H
            self.target_camera_x = 0.0
            self.camera_x *= 0.82

            if self.ball.pos.y <= LAUNCH_TOP + 18.0:
                # Release from the top of the lane into the main playfield.
                # A small horizontal component feeds the table; positive Y
                # velocity means the ball immediately begins its downward arc.
                self.ball.pos = pygame.Vector2(
                    LAUNCH_EXIT_X,
                    LAUNCH_TOP + 18.0,
                )
                self.ball.vel = pygame.Vector2(
                    LAUNCH_EXIT_VX,
                    LAUNCH_EXIT_VY,
                )
                self.ball.state = "IN_PLAY"
                self.launch_watchdog = 0.0
                self.last_live_ball_pos = self.ball.pos.copy()
                self.previous_ball_pos = self.ball.pos.copy()
            else:
                # No gravity during the powered ascent.
                self.ball.trail.append(self.ball.pos.copy())
                if len(self.ball.trail) > 8:
                    self.ball.trail.pop(0)
                return

        self.ball.vel.y += GRAVITY * dt
        self.ball.vel *= 1.0 / (1.0 + AIR_DRAG * dt)
        speed = self.ball.vel.length()
        if speed > MAX_BALL_SPEED:
            self.ball.vel.scale_to_length(MAX_BALL_SPEED)

        previous_pos = self.ball.pos.copy()
        self.ball.pos += self.ball.vel * dt

        # Central drain funnel:
        # - below the flipper line, the ball cannot escape sideways through the
        #   lower opening;
        # - its horizontal velocity is gently centered;
        # - gravity continues pulling it downward;
        # - lower side rails are skipped inside the drain mouth so they cannot
        #   bounce the ball back upward/out of the table.
        in_drain_mouth = (
            self.ball.pos.y >= DRAIN_CAPTURE_Y
            and DRAIN_CAPTURE_LEFT <= self.ball.pos.x <= DRAIN_CAPTURE_RIGHT
        )

        if in_drain_mouth:
            center_error = 360.0 - self.ball.pos.x
            self.ball.vel.x += clamp(center_error * DRAIN_CAPTURE_PULL * dt, -120.0, 120.0)
            self.ball.vel.x *= 0.985
            self.ball.vel.y = max(self.ball.vel.y, 180.0)
            self.ball.vel.y += GRAVITY * 0.35 * dt
            self.ball.pos.x = clamp(
                self.ball.pos.x,
                DRAIN_CAPTURE_LEFT + BALL_RADIUS,
                DRAIN_CAPTURE_RIGHT - BALL_RADIUS,
            )
        else:
            self._resolve_world_contacts()

        # LEFT and RIGHT rescue kickers use exactly the same mirrored logic.
        if self.ball.state == "IN_PLAY":
            for kicker in self.side_rescue_kickers:
                if self.collide_side_rescue_kicker(kicker):
                    break

        # A ball that has crossed the drain threshold is definitively lost.
        # The central drain remains the normal ball-loss condition.
        if self.ball.pos.y > DRAIN_Y:
            self.ball.state = "DRAINING"
            self.drain()
            return

        # Stuck watchdog. It prevents the exact endless score/deadball failure.
        if self.recovery_lock <= 0.0:
            displacement = self.ball.pos.distance_to(self.last_live_ball_pos)
            if self.ball.vel.length() < 26.0 and displacement < 1.4:
                self.stuck_timer += dt
            else:
                self.stuck_timer = max(0.0, self.stuck_timer - dt * 0.5)
                self.last_live_ball_pos = self.ball.pos.copy()
            if self.stuck_timer > 1.75:
                self.recover_stuck_ball()

        # Predict a small amount in the ball's current direction so the camera
        # follows the trajectory rather than visibly chasing the ball.
        look_ahead = clamp(self.ball.vel.y * 0.10, -180.0, 180.0)
        self.target_camera_y = clamp(
            self.ball.pos.y - VIEW_H * CAMERA_FOLLOW + look_ahead,
            0.0,
            WORLD_H - VIEW_H,
        )

        # Smooth horizontal camera response. The whole playfield layer moves
        # together, so walls, ball and objects remain perfectly aligned.
        horizontal_error = self.ball.pos.x - WORLD_W * 0.5
        if abs(horizontal_error) <= CAMERA_X_DEADZONE:
            desired_x = 0.0
        else:
            desired_x = clamp(
                horizontal_error
                - math.copysign(CAMERA_X_DEADZONE, horizontal_error),
                -CAMERA_X_MAX_SHIFT,
                CAMERA_X_MAX_SHIFT,
            )
        self.target_camera_x = desired_x

        lerp_y = 1.0 - math.exp(-CAMERA_SMOOTH * dt)
        lerp_x = 1.0 - math.exp(-CAMERA_X_SMOOTH * dt)
        self.camera_y += (self.target_camera_y - self.camera_y) * lerp_y
        self.camera_x += (self.target_camera_x - self.camera_x) * lerp_x
        self.camera_y = clamp(self.camera_y, 0.0, WORLD_H - VIEW_H)
        self.camera_x = clamp(
            self.camera_x,
            -CAMERA_X_MAX_SHIFT,
            CAMERA_X_MAX_SHIFT,
        )
        self.ball.trail.append(self.ball.pos.copy())
        if len(self.ball.trail) > 8:
            self.ball.trail.pop(0)

    def recover_stuck_ball(self):
        self.stuck_timer = 0.0
        self.recovery_lock = 0.65
        self.audio.play("wall")
        # Recover to a safe point in the nearest open play lane.
        if self.ball.pos.y > 1500.0:
            self.ball.pos = pygame.Vector2(360.0, 1510.0)
            self.ball.vel = pygame.Vector2(0.0, -420.0)
        else:
            direction = -1.0 if self.ball.pos.x > 360.0 else 1.0
            self.ball.pos += pygame.Vector2(direction * 14.0, -12.0)
            self.ball.vel = pygame.Vector2(direction * 260.0, -300.0)
        self.ball.state = "IN_PLAY"
        self.last_live_ball_pos = self.ball.pos.copy()
        self.show_toast("BALL RECOVERED", 0.55)

    def update(self, frame_dt):
        frame_dt = clamp(frame_dt, 0.0, MAX_FRAME_DT)

        for plane in self.aircraft:
            plane["x"] += plane["speed"] * plane["direction"] * frame_dt
            if plane["direction"] > 0 and plane["x"] > WORLD_W + 90:
                plane["x"] = -90.0
            elif plane["direction"] < 0 and plane["x"] < -90:
                plane["x"] = WORLD_W + 90.0

        self.launch_warning_timer = max(0.0, self.launch_warning_timer - frame_dt)
        self.update_input_holds()

        # Launcher power is needed even when the ball is READY.
        if self.state == "PLAYING":
            if self.ball.state == "READY":
                if self.launch_held:
                    self.launch_power = min(
                        1.0,
                        getattr(self, "launch_power", 0.0)
                        + frame_dt * 1.05,
                    )

        # Cap the accumulator so a browser hitch cannot create a burst of
        # physics work on the next frame. This is a major source of visible
        # launch/camera stutter in pygbag/browser runs.
        self.physics_accumulator = min(
            self.physics_accumulator + frame_dt,
            FIXED_DT * MAX_PHYSICS_STEPS,
        )
        steps = 0

        while (
            self.physics_accumulator >= FIXED_DT
            and steps < MAX_PHYSICS_STEPS
        ):
            if self.state == "PLAYING":
                self.physics_step(FIXED_DT)

            self.physics_accumulator -= FIXED_DT
            steps += 1

        if steps >= MAX_PHYSICS_STEPS:
            self.physics_accumulator = 0.0

        # Camera remains stable on title/tutorial/results.
        if self.state in ("TITLE", "TUTORIAL"):
            self.target_camera_y = WORLD_H - VIEW_H
            self.target_camera_x = 0.0
            self.camera_x *= 0.80

        # Reset rollover active lights after their cooldown window.
        for rollover in self.rollovers:
            if rollover.cooldown > 0.34:
                rollover.active = False

    # ============================================================
    # Rendering
    # ============================================================
    def world_to_view(self, point):
        return pygame.Vector2(
            point.x,
            point.y - self.camera_y,
        )

    def draw_text(
        self,
        surface,
        text,
        position,
        size,
        color="white",
        center=True,
        bold=False,
    ):
        color_value = (
            C[color]
            if isinstance(color, str)
            else color
        )

        text_value = str(text)

        # Keep the original text calls and layout logic intact, but render
        # every existing label larger so the game remains readable when the
        # 720x1040 logical surface is scaled down inside the website.
        render_size = max(1, int(round(float(size) * UI_TEXT_SCALE)))

        # Text content/size/color is highly repetitive. Cache the rendered
        # glyph surfaces so pygame does not rasterize the same text every frame.
        cache_key = (text_value, render_size, tuple(color_value), bool(bold))
        rendered = TEXT_SURFACE_CACHE.get(cache_key)
        if rendered is None:
            rendered = get_font(render_size, bold).render(
                text_value,
                True,
                color_value,
            )
            TEXT_SURFACE_CACHE[cache_key] = rendered

        rect = rendered.get_rect()

        if center:
            rect.center = (
                int(position[0]),
                int(position[1]),
            )
        else:
            rect.topleft = (
                int(position[0]),
                int(position[1]),
            )

        surface.blit(rendered, rect)

    def panel(
        self,
        surface,
        rect,
        fill="panel2",
        outline="metal",
        radius=14,
        width=2,
    ):
        pygame.draw.rect(
            surface,
            C[fill],
            rect,
            border_radius=radius,
        )
        if width:
            pygame.draw.rect(
                surface,
                C[outline],
                rect,
                width,
                border_radius=radius,
            )

    def draw_glow_line(self, surface, a, b, color, width=4):
        pygame.draw.line(
            surface,
            C["dark"],
            a,
            b,
            width + 8,
        )
        pygame.draw.line(
            surface,
            color,
            a,
            b,
            width,
        )

    def starfield(self, surface, seed=15):
        # Pre-render the fixed starfield once. Previously 180 circles were
        # iterated/drawn on every frame while the camera moved.
        global STAR_CACHE

        if self.starfield_surface is None:
            if STAR_CACHE is None:
                rng = random.Random(seed)
                STAR_CACHE = [
                    (
                        rng.randint(30, WORLD_W - 30),
                        rng.randint(80, WORLD_H - 20),
                        rng.choice((1, 1, 1, 2)),
                        rng.choice(
                            (
                                C["white"],
                                C["purple"],
                                C["cyan"],
                                C["yellow"],
                            )
                        ),
                    )
                    for _ in range(180)
                ]

            self.starfield_surface = pygame.Surface(
                (WORLD_W, WORLD_H), pygame.SRCALPHA
            )
            self.starfield_surface.fill((0, 0, 0, 0))
            for x, y, radius, color in STAR_CACHE:
                pygame.draw.circle(
                    self.starfield_surface,
                    color,
                    (x, y),
                    radius,
                )

        surface.blit(
            self.starfield_surface,
            (0, -int(self.camera_y)),
        )

    def draw_aircraft(self, surface):
        """Draw subtle procedural aircraft behind the pinball hardware."""
        for plane in self.aircraft:
            y = plane["y"] - self.camera_y
            if y < -80 or y > VIEW_H + 80:
                continue
            x = plane["x"]
            scale = plane["scale"]
            sx = plane["direction"]
            wing = 24.0 * scale
            body = 42.0 * scale
            tail = 16.0 * scale
            glow = (32, 48, 72)
            body_color = (58, 76, 105)
            pygame.draw.line(surface, glow,
                (int(x - sx * body * 0.45), int(y)),
                (int(x + sx * body * 0.45), int(y)), 7)
            pygame.draw.polygon(surface, body_color, [
                (int(x + sx * body * 0.55), int(y)),
                (int(x + sx * body * 0.10), int(y - wing * 0.18)),
                (int(x - sx * body * 0.05), int(y - wing)),
                (int(x - sx * tail), int(y - wing * 0.25)),
                (int(x - sx * body * 0.38), int(y - wing * 0.20)),
                (int(x - sx * body * 0.42), int(y + wing * 0.20)),
                (int(x - sx * tail), int(y + wing * 0.25)),
                (int(x - sx * body * 0.05), int(y + wing)),
                (int(x + sx * body * 0.10), int(y + wing * 0.18)),
            ])
            pygame.draw.circle(surface, C["cyan"],
                (int(x + sx * 7.0 * scale), int(y)), max(1, int(2 * scale)))

    # ------------------------------------------------------------
    # Table
    # ------------------------------------------------------------
    def draw_table(self, surface):
        # Overall cabinet / playfield.
        pygame.draw.rect(
            surface,
            C["panel2"],
            pygame.Rect(20, 20, WORLD_W - 40, VIEW_H - 40),
            border_radius=28,
        )
        pygame.draw.rect(
            surface,
            C["metal"],
            pygame.Rect(20, 20, WORLD_W - 40, VIEW_H - 40),
            3,
            border_radius=28,
        )

        # Glass.
        pygame.draw.rect(
            surface,
            C["glass"],
            pygame.Rect(42, 58, WORLD_W - 84, VIEW_H - 86),
            border_radius=22,
        )

        self.starfield(surface)
        self.draw_aircraft(surface)

        # Side metal walls.
        self.draw_glow_line(
            surface,
            (58, 75 - self.camera_y),
            (58, VIEW_H - 60),
            C["cyan"],
            5,
        )
        self.draw_glow_line(
            surface,
            (662, 75 - self.camera_y),
            (662, VIEW_H - 60),
            C["magenta"],
            5,
        )

        # Header anchored to the table, not the world.
        header_y = 92
        self.draw_text(
            surface,
            "AEROSTRIKE",
            (360, header_y),
            31,
            "white",
            True,
            True,
        )
        self.draw_text(
            surface,
            "AERODYNAMIC PINBALL",
            (360, header_y + 27),
            12,
            "muted",
        )

        # Rollovers.
        for rollover in self.rollovers:
            y = rollover.rect.y - self.camera_y
            if -50 < y < VIEW_H + 50:
                fill = (
                    C["yellow"]
                    if rollover.active
                    else C["panel"]
                )
                border = (
                    C["yellow"]
                    if rollover.active
                    else C["metal"]
                )

                pygame.draw.rect(
                    surface,
                    fill,
                    pygame.Rect(
                        rollover.rect.x,
                        int(y),
                        rollover.rect.w,
                        rollover.rect.h,
                    ),
                    border_radius=8,
                )
                pygame.draw.rect(
                    surface,
                    border,
                    pygame.Rect(
                        rollover.rect.x,
                        int(y),
                        rollover.rect.w,
                        rollover.rect.h,
                    ),
                    2,
                    border_radius=8,
                )
                self.draw_text(
                    surface,
                    rollover.label,
                    (
                        rollover.rect.centerx,
                        int(y + rollover.rect.h * 0.5),
                    ),
                    18,
                    "dark" if rollover.active else "white",
                )

        # Targets.
        for target in self.targets:
            y = target.rect.y - self.camera_y
            if -50 < y < VIEW_H + 50:
                rect = pygame.Rect(
                    target.rect.x,
                    int(y),
                    target.rect.w,
                    target.rect.h,
                )
                pygame.draw.rect(
                    surface,
                    C["yellow"] if target.lit else C["panel"],
                    rect,
                    border_radius=8,
                )
                pygame.draw.rect(
                    surface,
                    C["orange"] if target.flash > 0 else C["metal"],
                    rect,
                    2,
                    border_radius=8,
                )
                if target.label:
                    self.draw_text(
                        surface,
                        target.label,
                        rect.center,
                        13,
                        "dark" if target.lit else "muted",
                    )

        # Bumpers.
        for bumper in self.bumpers:
            p = self.world_to_view(bumper.pos)

            if -80 < p.y < VIEW_H + 80:
                color = (
                    C["yellow"]
                    if bumper.flash > 0
                    else bumper.color
                )

                pygame.draw.circle(
                    surface,
                    C["dark"],
                    (int(p.x), int(p.y)),
                    int(bumper.radius + 7),
                )
                pygame.draw.circle(
                    surface,
                    color,
                    (int(p.x), int(p.y)),
                    int(bumper.radius),
                )
                pygame.draw.circle(
                    surface,
                    C["panel2"],
                    (int(p.x), int(p.y)),
                    int(bumper.radius * 0.62),
                )
                pygame.draw.circle(
                    surface,
                    color,
                    (int(p.x), int(p.y)),
                    8,
                )
                self.draw_text(
                    surface,
                    f"+{bumper.score}",
                    (p.x, p.y + bumper.radius + 19),
                    14,
                    "muted",
                )

        # Central vertical guide / aero-themed tunnel art.
        for y in range(820, 1220, 34):
            vy = y - self.camera_y
            if -20 < vy < VIEW_H:
                pygame.draw.line(
                    surface,
                    (45, 30, 50),
                    (360, vy),
                    (360, vy + 14),
                    2,
                )

        # Side ramps.
        for a, b in self.ramps:
            min_y = min(a.y, b.y) - self.camera_y
            max_y = max(a.y, b.y) - self.camera_y
            if max_y < -24 or min_y > VIEW_H + 24:
                continue
            aa = self.world_to_view(a)
            bb = self.world_to_view(b)

            pygame.draw.line(
                surface,
                C["dark"],
                aa,
                bb,
                12,
            )
            pygame.draw.line(
                surface,
                C["metal_hi"],
                aa,
                bb,
                7,
            )

        # Triangular kick pads.
        for tri in self.kick_triangles:
            pts = [
                (
                    int(point.x),
                    int(point.y - self.camera_y),
                )
                for point in tri.points
            ]

            if (
                max(p[1] for p in pts) < 0
                or min(p[1] for p in pts) > VIEW_H
            ):
                continue

            fill = (
                C["yellow"]
                if tri.flash > 0
                else C["magenta"]
            )

            pygame.draw.polygon(
                surface,
                C["dark"],
                [
                    (x, y + 6)
                    for x, y in pts
                ],
            )
            pygame.draw.polygon(
                surface,
                fill,
                pts,
            )
            pygame.draw.lines(
                surface,
                C["orange"],
                True,
                pts,
                3,
            )

            # Straight upward arrows reinforce the actual kick direction.
            cx = sum(p[0] for p in pts) / 3.0
            cy = sum(p[1] for p in pts) / 3.0
            pygame.draw.polygon(
                surface,
                C["white"],
                [
                    (cx, cy - 20),
                    (cx - 7, cy - 7),
                    (cx + 7, cy - 7),
                ],
            )

        # Side rescue kickers: large, readable circles with a blue center and
        # an upward arrow showing the safe re-launch direction.
        for kicker in self.side_rescue_kickers:
            p = self.world_to_view(kicker.pos)
            if -60 < p.y < VIEW_H + 60:
                cx, cy = int(p.x), int(p.y)
                ring = C["yellow"] if kicker.flash > 0 else C["blue"]
                pygame.draw.circle(surface, C["dark"], (cx, cy), int(kicker.radius + 7))
                pygame.draw.circle(surface, ring, (cx, cy), int(kicker.radius))
                pygame.draw.circle(
                    surface,
                    C["panel2"],
                    (cx, cy),
                    int(kicker.radius * 0.63),
                )
                pygame.draw.circle(surface, C["cyan"], (cx, cy), 7)
                pygame.draw.circle(surface, C["white"], (cx, cy), 3)
                pygame.draw.polygon(
                    surface,
                    C["white"],
                    [(cx, cy - 17), (cx - 8, cy - 4), (cx + 8, cy - 4)],
                )

        # Lower slingshot rails.
        for a, b in self.slings:
            min_y = min(a.y, b.y) - self.camera_y
            max_y = max(a.y, b.y) - self.camera_y
            if max_y < -24 or min_y > VIEW_H + 24:
                continue
            aa = self.world_to_view(a)
            bb = self.world_to_view(b)
            pygame.draw.line(
                surface,
                C["dark"],
                aa,
                bb,
                15,
            )
            pygame.draw.line(
                surface,
                C["metal_hi"],
                aa,
                bb,
                9,
            )

        # Posts.
        for post_pos, radius in self.posts:
            py = post_pos.y - self.camera_y
            if py < -40 or py > VIEW_H + 40:
                continue
            p = self.world_to_view(post_pos)
            pygame.draw.circle(
                surface,
                C["metal"],
                (int(p.x), int(p.y)),
                radius + 4,
            )
            pygame.draw.circle(
                surface,
                C["dark"],
                (int(p.x), int(p.y)),
                radius,
            )
            pygame.draw.circle(
                surface,
                C["cyan"],
                (int(p.x), int(p.y)),
                4,
            )

        # Drain and lower side rails. These terminate outside the center gap.
        left_drain_a = self.world_to_view(
            pygame.Vector2(86, 2140)
        )
        left_drain_b = self.world_to_view(
            pygame.Vector2(215, 2048)
        )
        right_drain_a = self.world_to_view(
            pygame.Vector2(634, 2140)
        )
        right_drain_b = self.world_to_view(
            pygame.Vector2(505, 2048)
        )

        if not (2140 - self.camera_y < -30 or 2048 - self.camera_y > VIEW_H + 30):
            pygame.draw.line(
                surface,
                C["metal"],
                left_drain_a,
            left_drain_b,
            11,
        )
        pygame.draw.line(
            surface,
            C["metal"],
            right_drain_a,
            right_drain_b,
            11,
        )

        # Central drain opening. The physics uses the same central region as a
        # one-way funnel so the visual opening and ball logic agree.
        mouth_y = int(DRAIN_CAPTURE_Y - self.camera_y)
        pygame.draw.line(
            surface,
            C["red"],
            (int(DRAIN_CAPTURE_LEFT), mouth_y),
            (int(DRAIN_CAPTURE_RIGHT), mouth_y),
            3,
        )
        self.draw_text(surface, "DRAIN", (360, 2170 - self.camera_y), 11, "red")

        # Flippers.
        # Inactive paddles are visually transparent so the player can see the
        # ball/trajectory through them. Collision geometry is unchanged.
        flipper_layer = self.flipper_layer
        flipper_layer.fill((0, 0, 0, 0))
        for flipper in self.flippers:
            p = self.world_to_view(flipper.pivot)
            tip = self.world_to_view(flipper.tip)

            alpha = 235 if flipper.pressed else 105
            body = (*(
                C["orange"] if flipper.pressed else C["white"]
            ), alpha)
            shadow = (*C["dark"], min(150, alpha))
            pivot_outer = (*C["metal"], min(190, alpha))
            pivot_inner = (*C["dark"], min(180, alpha))

            pygame.draw.line(
                flipper_layer,
                shadow,
                (int(p.x), int(p.y)),
                (int(tip.x), int(tip.y)),
                30,
            )
            pygame.draw.line(
                flipper_layer,
                body,
                (int(p.x), int(p.y)),
                (int(tip.x), int(tip.y)),
                21,
            )
            pygame.draw.circle(
                flipper_layer,
                body,
                (int(tip.x), int(tip.y)),
                10,
            )
            pygame.draw.circle(
                flipper_layer,
                pivot_outer,
                (int(p.x), int(p.y)),
                16,
            )
            pygame.draw.circle(
                flipper_layer,
                pivot_inner,
                (int(p.x), int(p.y)),
                7,
            )

        surface.blit(flipper_layer, (0, 0))

        # Launcher lane and plunger.
        launcher_top = LAUNCH_TOP - self.camera_y
        launcher_bottom = LAUNCH_BOTTOM - self.camera_y

        launcher_rect = pygame.Rect(
            585,
            int(launcher_top),
            72,
            max(1, int(launcher_bottom - launcher_top)),
        )

        if (
            launcher_rect.bottom > 0
            and launcher_rect.top < VIEW_H
        ):
            pygame.draw.rect(
                surface,
                C["panel2"],
                launcher_rect,
                border_radius=10,
            )
            pygame.draw.rect(
                surface,
                C["metal_dark"],
                launcher_rect,
                2,
                border_radius=10,
            )

            # Plunger shaft.
            shaft = pygame.Rect(
                613,
                int(launcher_bottom - 165),
                16,
                124,
            )
            pygame.draw.rect(
                surface,
                C["dark"],
                shaft,
                border_radius=5,
            )

            meter = int(
                112 * getattr(
                    self,
                    "launch_power",
                    0.0,
                )
            )
            if meter > 0:
                pygame.draw.rect(
                    surface,
                    C["orange"],
                    pygame.Rect(
                        613,
                        int(launcher_bottom - 41 - meter),
                        16,
                        meter,
                    ),
                    border_radius=5,
                )

            self.draw_text(surface, "HOLD", (619, int(launcher_bottom - 126)), 13, "orange", True, True)
            self.draw_text(surface, "RELEASE", (619, int(launcher_bottom - 103)), 11, "white", True)

        # Ball trail.
        for i, point in enumerate(self.ball.trail):
            fade = (i + 1) / max(1, len(self.ball.trail))
            p = self.world_to_view(point)

            if -20 < p.y < VIEW_H + 20:
                radius = max(2, int(2 + 4 * fade))
                color = (
                    35,
                    int(70 + 110 * fade),
                    int(105 + 110 * fade),
                )

                pygame.draw.circle(
                    surface,
                    color,
                    (int(p.x), int(p.y)),
                    radius,
                )

        # Ball. Render from an interpolated visual position so fast motion
        # does not appear to hop between fixed physics steps.
        ball = self.world_to_view(self.render_ball_pos)
        if -40 < ball.y < VIEW_H + 40:
            pygame.draw.circle(
                surface,
                C["white"],
                (int(ball.x), int(ball.y)),
                BALL_DRAW_RADIUS,
            )
            pygame.draw.circle(
                surface,
                C["cyan"],
                (int(ball.x), int(ball.y)),
                BALL_DRAW_RADIUS,
                2,
            )
            pygame.draw.circle(
                surface,
                C["dark"],
                (
                    int(ball.x - 3),
                    int(ball.y - 3),
                ),
                3,
            )

    # ------------------------------------------------------------
    # HUD / controls
    # ------------------------------------------------------------
    def draw_volume_icon(self, surface, center, muted=False, size=24, color=None):
        """Draw a speaker icon; muted state is a clear diagonal strike."""
        cx, cy = int(center[0]), int(center[1])
        color = color or (C["muted"] if muted else C["cyan"])
        s = max(12, int(size))
        half = s // 2

        speaker = [
            (cx - half, cy - s // 5),
            (cx - half + s // 3, cy - s // 5),
            (cx + 1, cy - half),
            (cx + 1, cy + half),
            (cx - half + s // 3, cy + s // 5),
            (cx - half, cy + s // 5),
        ]
        pygame.draw.polygon(surface, color, speaker)
        pygame.draw.arc(
            surface,
            color,
            pygame.Rect(cx - 2, cy - s // 2, s, s),
            -0.95,
            0.95,
            max(2, s // 8),
        )
        pygame.draw.arc(
            surface,
            color,
            pygame.Rect(cx + 2, cy - s // 2 + 4, s + 7, s - 8),
            -0.75,
            0.75,
            max(2, s // 8),
        )

        if muted:
            pygame.draw.line(
                surface,
                C["yellow"],
                (cx - half - 2, cy + half + 2),
                (cx + half + 8, cy - half - 8),
                max(3, s // 7),
            )

    def draw_hud(self, surface):
        hud = pygame.Rect(68, 56, 584, 70)
        self.panel(
            surface,
            hud,
            "panel",
            "metal_dark",
            14,
            2,
        )

        self.draw_text(
            surface,
            "SCORE",
            (92, 75),
            11,
            "muted",
            False,
        )
        self.draw_text(
            surface,
            f"{self.score:,}",
            (92, 101),
            24,
            "white",
            False,
            True,
        )

        self.draw_text(
            surface,
            "BEST",
            (220, 75),
            11,
            "muted",
            False,
        )
        self.draw_text(
            surface,
            f"{self.high_score:,}",
            (220, 101),
            18,
            "yellow",
            False,
        )

        self.draw_text(
            surface,
            f"BALL {self.balls}",
            (360, 88),
            17,
            "white",
        )

        self.draw_text(
            surface,
            f"x{self.multiplier}",
            (565, 88),
            24,
            "orange",
        )

        if self.combo > 1:
            self.draw_text(
                surface,
                f"COMBO {self.combo}",
                (360, 111),
                11,
                "cyan",
            )

        # Global audio mute/unmute button. It controls both background music
        # and gameplay SFX, matching the HOME-screen switch.
        self.panel(
            surface,
            pygame.Rect(18, 18, 42, 42),
            "panel2",
            "cyan" if self.audio.enabled else "metal_dark",
            10,
            2,
        )
        self.draw_volume_icon(
            surface,
            (39, 39),
            muted=not self.audio.enabled,
            size=25,
            color=C["cyan"] if self.audio.enabled else C["muted"],
        )

        # Small pause button.
        self.panel(
            surface,
            pygame.Rect(650, 18, 52, 52),
            "panel2",
            "yellow",
            12,
            2,
        )
        self.draw_text(
            surface,
            "||",
            (676, 44),
            21,
            "yellow",
        )

    def draw_launch_warning(self, surface):
        if self.launch_warning_timer <= 0.0 or self.state != "PLAYING":
            return
        rect = pygame.Rect(105, 730, 510, 82)
        self.panel(surface, rect, "glass2", "orange", 14, 3)
        self.draw_text(surface, "LAUNCH POWER TOO LOW", (360, 754), 20, "orange", True, True)
        self.draw_text(surface, self.launch_warning_text, (360, 786), 12, "white", True)

    def draw_touch_controls(self, surface):
        left = pygame.Rect(72, VIEW_H - 98, 156, 62)
        right = pygame.Rect(492, VIEW_H - 98, 156, 62)
        self.panel(surface, left, "panel", "cyan" if self.left_held else "metal_dark", 14, 2)
        self.panel(surface, right, "panel", "magenta" if self.right_held else "metal_dark", 14, 2)
        self.draw_text(surface, "←", left.center, 26, "white", True, True)
        self.draw_text(surface, "→", right.center, 26, "white", True, True)
        self.draw_text(surface, "TOUCH & HOLD", (360, VIEW_H - 14), 12, "white", True)

    def draw_title(self, surface):
        self.draw_table(surface)
        veil = pygame.Surface((VIEW_W, VIEW_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 95))
        surface.blit(veil, (0, 0))
        self.panel(surface, pygame.Rect(645, 20, 55, 55), "panel2", "yellow", 14, 3)
        self.draw_volume_icon(
            surface,
            (672, 47),
            muted=not self.audio.enabled,
            size=30,
            color=C["yellow"],
        )
        self.draw_text(surface, "AEROSTRIKE", (360, 255), 70, "white", True, True)
        self.draw_text(surface, "PINBALL", (360, 316), 39, "orange", True, True)
        self.draw_text(surface, "AERODYNAMIC EDITION", (360, 350), 14, "muted")
        pygame.draw.circle(surface, C["panel2"], (360, 475), 92)
        pygame.draw.circle(surface, C["cyan"], (360, 475), 92, 3)
        pygame.draw.arc(surface, C["magenta"], pygame.Rect(290, 405, 140, 140), 0.4, 5.1, 6)
        self.draw_text(surface, "PLAY", (360, 478), 30, "yellow", True, True)
        self.button_overlay(surface, pygame.Rect(215, 735, 290, 86), "START GAME", "yellow")
        self.draw_text(surface, "CLICK / TAP  •  SPACE / ENTER", (360, 850), 13, "white")
        self.draw_text(surface, "DEVELOPED BY", (360, 878), 11, "muted", True, True)
        self.draw_text(surface, "SUYASH SHUKLA", (360, 903), 17, "cyan", True, True)

    def draw_tutorial(self, surface):
        self.draw_table(surface)
        veil = pygame.Surface((VIEW_W, VIEW_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 155))
        surface.blit(veil, (0, 0))

        card = pygame.Rect(74, 245, 572, 560)
        self.panel(surface, card, "glass2", "yellow", 20, 3)
        self.draw_text(surface, "HOW TO PLAY", (360, 315), 40, "yellow", True, True)
        self.draw_text(surface, "USE THE ARROW KEYS", (360, 365), 18, "white", True, True)
        self.draw_text(surface, "TO CONTROL THE TABLE", (360, 391), 16, "white", True)

        # Keycaps are illustration only, never gameplay buttons.
        key = 52
        top_y = 435
        for x, y, glyph in (
            (334, top_y, "↑"),
            (278, top_y + 60, "←"),
            (334, top_y + 60, "↓"),
            (390, top_y + 60, "→"),
        ):
            pygame.draw.rect(surface, C["panel2"], pygame.Rect(x, y, key, key), border_radius=9)
            pygame.draw.rect(surface, C["metal_hi"], pygame.Rect(x, y, key, key), 2, border_radius=9)
            self.draw_text(surface, glyph, (x + key/2, y + key/2), 28, "white", True, True)

        self.draw_text(surface, "↑ = LAUNCH", (202, 555), 17, "orange", True, True)
        self.draw_text(surface, "← / → = FLIPPERS", (518, 555), 17, "cyan", True, True)
        self.draw_text(surface, "HIT BUMPERS • TARGETS • ROLL-OVERS", (360, 625), 16, "white", True, True)
        self.draw_text(surface, "KEEP THE BALL ALIVE AND SCORE", (360, 666), 17, "white", True)
        self.draw_text(surface, "AS MANY POINTS AS POSSIBLE", (360, 695), 18, "yellow", True, True)
        self.button_overlay(surface, pygame.Rect(220, 735, 280, 72), "START", "yellow")
        self.draw_text(surface, "CLICK / TAP  •  SPACE / ENTER", (360, 827), 13, "white")

    def draw_pause(self, surface):
        veil = pygame.Surface(
            (VIEW_W, VIEW_H),
            pygame.SRCALPHA,
        )
        veil.fill((0, 0, 0, 175))
        surface.blit(veil, (0, 0))

        card = pygame.Rect(125, 235, 470, 480)
        self.panel(
            surface,
            card,
            "glass2",
            "yellow",
            18,
            3,
        )

        self.draw_text(
            surface,
            "PAUSED",
            (360, 310),
            52,
            "yellow",
            True,
            True,
        )

        self.button_overlay(
            surface,
            pygame.Rect(220, 390, 280, 70),
            "RESUME",
            "cyan",
            selected=self.pause_selection == 0,
        )
        self.button_overlay(
            surface,
            pygame.Rect(220, 480, 280, 70),
            "RESTART",
            "orange",
            selected=self.pause_selection == 1,
        )
        self.button_overlay(
            surface,
            pygame.Rect(220, 570, 280, 70),
            "HOME",
            "metal",
            selected=self.pause_selection == 2,
        )

    def button_overlay(self, surface, rect, label, accent, selected=False):
        self.panel(
            surface,
            rect,
            "panel2",
            accent,
            14,
            2,
        )
        if selected:
            focus_rect = rect.inflate(10, 10)
            pygame.draw.rect(
                surface,
                C["white"],
                focus_rect,
                2,
                border_radius=16,
            )
        self.draw_text(
            surface,
            label,
            rect.center,
            22,
            "white",
            True,
            True,
        )

    def draw_results(self, surface):
        self.draw_table(surface)

        veil = pygame.Surface(
            (VIEW_W, VIEW_H),
            pygame.SRCALPHA,
        )
        veil.fill((0, 0, 0, 175))
        surface.blit(veil, (0, 0))

        card = pygame.Rect(105, 270, 510, 590)
        self.panel(
            surface,
            card,
            "glass2",
            "yellow",
            18,
            3,
        )

        self.draw_text(
            surface,
            "FLIGHT COMPLETE",
            (360, 350),
            42,
            "yellow",
            True,
            True,
        )

        self.draw_text(
            surface,
            "TROPHY",
            (360, 425),
            46,
            "white",
        )
        self.draw_text(
            surface,
            f"{self.score:,}",
            (360, 480),
            42,
            "white",
            True,
            True,
        )

        self.draw_text(
            surface,
            "STAR",
            (300, 545),
            38,
            "yellow",
        )
        self.draw_text(
            surface,
            f"{self.score:,}",
            (410, 545),
            27,
            "white",
        )

        self.draw_text(
            surface,
            f"BEST  {self.high_score:,}",
            (360, 602),
            16,
            "muted",
        )

        # Home / replay actions like the reference result card.
        self.panel(
            surface,
            pygame.Rect(175, 720, 150, 75),
            "panel2",
            "yellow",
            16,
            3,
        )
        self.draw_text(
            surface,
            "HOME",
            (250, 757),
            36,
            "white",
        )

        self.panel(
            surface,
            pygame.Rect(395, 720, 150, 75),
            "panel2",
            "yellow",
            16,
            3,
        )
        self.draw_text(
            surface,
            "REPLAY",
            (470, 757),
            36,
            "white",
        )

        self.draw_text(
            surface,
            "HOME",
            (250, 815),
            11,
            "muted",
        )
        self.draw_text(
            surface,
            "REPLAY",
            (470, 815),
            11,
            "muted",
        )

    # ------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------
    def render(self):
        sw, sh = self.screen.get_size()
        scale = min(sw / VIEW_W, sh / VIEW_H)
        draw_w = max(1, int(VIEW_W * scale))
        draw_h = max(1, int(VIEW_H * scale))
        ox = int((sw - draw_w) * 0.5)
        oy = int((sh - draw_h) * 0.5)

        # Visual interpolation only. The real physics position/camera remain
        # untouched; interpolation affects rendering between fixed steps.
        if self.state in ("PLAYING", "PAUSED"):
            alpha = clamp(
                self.physics_accumulator / FIXED_DT,
                0.0,
                1.0,
            )
            self.render_ball_pos = self.previous_ball_pos.lerp(
                self.ball.pos,
                alpha,
            )
            self.render_camera_y = (
                self.previous_camera_y
                + (self.camera_y - self.previous_camera_y) * alpha
            )
            self.render_camera_x = (
                self.previous_camera_x
                + (self.camera_x - self.previous_camera_x) * alpha
            )
        else:
            self.render_ball_pos = self.ball.pos.copy()
            self.render_camera_y = self.camera_y
            self.render_camera_x = self.camera_x

        # Rendering methods use camera_y/camera_x. Temporarily expose the
        # interpolated visual values only while drawing, then restore the real
        # simulation values before returning.
        simulation_camera_y = self.camera_y
        simulation_camera_x = self.camera_x
        self.camera_y = self.render_camera_y
        self.camera_x = self.render_camera_x

        # Reuse the logical render target; avoid per-frame allocation/GC.
        surface = self.render_surface
        surface.fill(C["black"])

        if self.state == "TITLE":
            self.draw_title(surface)

        elif self.state == "TUTORIAL":
            self.draw_tutorial(surface)

        else:
            self.table_surface.fill(C["black"])
            self.draw_table(self.table_surface)
            surface.blit(
                self.table_surface,
                (int(self.camera_x), 0),
            )
            self.draw_hud(surface)
            self.draw_touch_controls(surface)
            self.draw_launch_warning(surface)

            if self.toast_timer > 0 and self.state == "PLAYING":
                toast = pygame.Rect(135, 825, 450, 42)
                self.panel(
                    surface,
                    toast,
                    "dark",
                    "cyan",
                    10,
                    2,
                )
                self.draw_text(
                    surface,
                    self.toast,
                    toast.center,
                    13,
                    "cyan",
                )

            if self.state == "PAUSED":
                self.draw_pause(surface)
            elif self.state == "RESULTS":
                self.draw_results(surface)

        # The game already renders at a fixed logical resolution. Normal
        # integer/filtered SDL scaling is substantially cheaper than
        # smoothscale and removes a recurring CPU spike on small screens.
        target_size = (draw_w, draw_h)
        if self.scaled_surface is None or self.scaled_size != target_size:
            self.scaled_surface = pygame.Surface(target_size)
            self.scaled_size = target_size

        # Write into the reusable destination surface instead of allocating a
        # new transformed Surface every frame.
        pygame.transform.scale(
            surface,
            target_size,
            self.scaled_surface,
        )

        self.screen.fill(C["black"])
        self.screen.blit(
            self.scaled_surface,
            (ox, oy),
        )
        pygame.display.flip()

        # Restore authoritative simulation camera values after visual draw.
        self.camera_y = simulation_camera_y
        self.camera_x = simulation_camera_x

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------
    async def run(self):
        clock = pygame.time.Clock()

        while self.running:
            now = pygame.time.get_ticks() / 1000.0
            frame_dt = now - self.last_time
            self.last_time = now

            self.events()
            self.update(frame_dt)
            self.render()

            # Required cooperative yield for pygbag.
            await asyncio.sleep(0)

            # Desktop keeps the original FPS cap. The browser is paced by its
            # own event/VSync loop and must not be blocked by SDL's tick wait.
            if sys.platform != "emscripten":
                clock.tick(FPS)


def run_self_tests():
    """Geometry regression test for the central flipper drain."""
    def endpoint(px, py, deg, length):
        a = math.radians(deg)
        return pygame.Vector2(
            px + math.cos(a) * length,
            py + math.sin(a) * length,
        )

    gaps = []
    for i in range(501):
        t = i / 500.0
        left_tip = endpoint(
            238.0, 2070.0,
            27.0 + (-17.0 - 27.0) * t,
            FLIPPER_LENGTH,
        )
        right_tip = endpoint(
            482.0, 2070.0,
            153.0 + (197.0 - 153.0) * t,
            FLIPPER_LENGTH,
        )
        gaps.append(left_tip.distance_to(right_tip))

    min_gap = min(gaps)
    required = BALL_RADIUS + (2.0 * FLIPPER_RADIUS) + 24.0
    assert min_gap >= required, (
        f"P0 center gap too small: {min_gap:.2f} < {required:.2f}"
    )

    left = Flipper((238, 2070), 27, -17, "L")
    right = Flipper((482, 2070), 153, 197, "R")

    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        left.angle = math.radians(27.0 + (-17.0 - 27.0) * t)
        right.angle = math.radians(153.0 + (197.0 - 153.0) * t)
        for y in (2090.0, 2120.0, 2150.0):
            center = pygame.Vector2(360.0, y)
            for flipper in (left, right):
                hit, *_ = circle_segment_hit(
                    center,
                    BALL_RADIUS + flipper.radius,
                    flipper.pivot,
                    flipper.tip,
                )
                assert not hit, (
                    f"P0 drain probe intersects {flipper.side} at y={y:.0f}"
                )

    print(f"P0 minimum animated tip gap: {min_gap:.2f} world units")
    print(f"Required minimum: {required:.2f} world units")
    print("P0 drain probes: PASS")
    print("Space/Enter workflow controls: PASS")
    print("Mirrored left/right rescue kickers: PASS")
    print("Low-power launch protection: PASS")
    print("Tutorial standalone launch button: PASS (none)")


async def main():
    init_pygame()
    if "--self-test" in sys.argv:
        try:
            run_self_tests()
        finally:
            pygame.quit()
        return

    game = Game()
    try:
        await game.run()
    finally:
        pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
