import sys
import cv2
import mediapipe as mp
import pygame
import random
import math
import time

from PyQt5.QtGui import (
    QImage, QPixmap, QGuiApplication, QPainter, QPen, QColor, QFont
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QPushButton, QStatusBar, QComboBox
)
from PyQt5.QtCore import QTimer, QRect, Qt, QPoint

class SkeletonOverlay(QLabel):
    """
    A transparent overlay widget that draws the hand skeleton on top of everything (piano + camera).
    """
    def __init__(self, parent, width, height):
        super().__init__(parent)
        self.setGeometry(0, 0, width, height)
        self.setStyleSheet("background: transparent;")
        # Let mouse events pass through
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.skeleton_data = []  # line segments: [((x1, y1), (x2, y2)), ...]
        self.points_data = []    # list of (x, y)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Skeleton lines in white
        pen_line = QPen(QColor(255, 255, 255, 200), 3)
        painter.setPen(pen_line)
        for line in self.skeleton_data:
            (x1, y1), (x2, y2) = line
            painter.drawLine(QPoint(x1, y1), QPoint(x2, y2))

        # Landmarks in red
        pen_points = QPen(QColor(255, 0, 0, 200), 6)
        painter.setPen(pen_points)
        for (px, py) in self.points_data:
            painter.drawPoint(QPoint(px, py))

class FlyingNote(QLabel):
    """
    Displays a random note image that moves in a random direction, then self-destructs after ~1 second.
    """
    def __init__(self, parent, pixmap, start_x, start_y):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setPixmap(pixmap)
        self.move(start_x, start_y)
        self.show()

        # 1-second lifetime
        self.lifetime_ms = 1000
        self.elapsed_ms = 0

        # Random velocity
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(1.0, 3.0)
        self.vx = speed * math.cos(angle)
        self.vy = speed * math.sin(angle)

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_position)
        self.update_timer.start(33)  # ~30fps

    def update_position(self):
        self.elapsed_ms += 33
        if self.elapsed_ms >= self.lifetime_ms:
            self.update_timer.stop()
            self.deleteLater()
            return

        nx = self.x() + self.vx
        ny = self.y() + self.vy
        self.move(int(nx), int(ny))

class EffectOverlay(QLabel):
    """
    Transparent overlay above skeleton to host FlyingNote objects so they appear on top.
    """
    def __init__(self, parent, width, height):
        super().__init__(parent)
        self.setGeometry(0, 0, width, height)
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

class FallingTile(QLabel):
    """
    A square tile that drops from above to the key region.
    - If multiple tiles for the same note occur quickly, each is horizontally offset so the user sees them distinctly.
    - Once it collides with the key region, it triggers (or records) the note collision time if auto_play_muted is True.
    - Has a visible border for clarity.
    """
    def __init__(self, parent, piano, note_name, key_rect, fall_speed, x_offset=0):
        super().__init__(parent)
        self.piano = piano
        self.note_name = note_name
        self.key_rect = key_rect
        self.fall_speed = fall_speed
        self.is_finished = False

        # We'll use ~70% of key width for a square tile
        tile_side = int(self.key_rect.width() * 0.7)
        self.resize(tile_side, tile_side)

        # Start from above
        start_x = self.key_rect.x() + x_offset + (self.key_rect.width() - tile_side)//2
        start_y = -tile_side
        self.move(start_x, start_y)

        # Fill + border
        self.fill_color = QColor(0, 180, 255, 180)
        self.border_color = QColor(0, 0, 0, 220)
        self.border_width = 3

        self.show()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pen = QPen(self.border_color, self.border_width)
        painter.setPen(pen)
        painter.setBrush(self.fill_color)
        painter.drawRect(self.rect())

    def update_position(self):
        if self.is_finished:
            return

        nx = self.x()
        ny = self.y() + self.fall_speed
        self.move(nx, ny)

        # Check if we intersect the key
        tile_bottom = ny + self.height()
        key_top = self.key_rect.y()

        if tile_bottom >= key_top:
            current_ms = int(time.time() * 1000)
            if not self.piano.auto_play_muted:
                self.piano.trigger_note_by_name(self.note_name, from_tile=True)
            else:
                # Record collision time so user can get points if they manually hit soon
                self.piano.last_tile_collision_time_for_note[self.note_name] = current_ms

            self.is_finished = True

class TileOverlay(QLabel):
    """
    Overlay for auto-play notes.
    Multiple spawns for the same note appear with horizontal offsets.
    """
    def __init__(self, parent, width, height, piano):
        super().__init__(parent)
        self.setGeometry(0, 0, width, height)
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.piano = piano
        self.tiles = []

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_tiles)
        self.update_timer.start(33)

    def spawnTile(self, note_name, fall_speed=4):
        """
        If multiple spawns for the same note happen quickly,
        shift horizontally by 20px per existing tile.
        """
        active_count = sum(1 for t in self.tiles if t.note_name == note_name and not t.is_finished)
        x_offset = active_count * 20

        # Find key geometry
        key_rect = None
        for (btn, nm) in self.piano.keys_info:
            if nm == note_name:
                key_rect = btn.geometry()
                break
        if not key_rect:
            return

        tile = FallingTile(self, self.piano, note_name, key_rect, fall_speed, x_offset)
        self.tiles.append(tile)

    def update_tiles(self):
        for t in self.tiles[:]:
            t.update_position()
            if t.is_finished:
                self.tiles.remove(t)
                t.deleteLater()

class ARPiano(QMainWindow):
    """
    AR Piano Teaching Machine:
    - Touch-lift approach for AR fingertip triggers
    - Flying notes on triggered keys
    - Skeleton overlay
    - "Teach You To Play" toggle for auto-play tile collisions
    - 'Happy Birthday' or 'Interstellar' auto-play with smaller square tiles (with border)
    - Multiple spawns for the same note horizontally offset
    - Score system: +10 if user hits the correct note within ~300 ms of tile collision
      (only if "Teach You To Play" is ON => i.e. auto-play is OFF for tiles).
    """
    FINGER_TIPS =  [8, 12, 16, 20]   # index, middle, ring, pinky
    FINGER_PIPS =  [6, 10, 14, 18]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AR Piano Teaching Machine")

        # 1) Initialize Pygame
        pygame.init()
        pygame.mixer.init()

        # 2) Dictionary for {note_name: Sound}
        self.sounds = {}

        # 3) Screen size
        screen = QGuiApplication.primaryScreen()
        rect = screen.availableGeometry()
        self.screen_w = rect.width()
        self.screen_h = rect.height()

        # 4) Fill window
        self.setGeometry(0, 0, self.screen_w, self.screen_h)

        # 5) Camera
        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            print("Warning: Could not open camera.")
        else:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.screen_w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.screen_h)

        # 6) Camera label
        self.camera_label = QLabel(self)
        self.camera_label.setGeometry(0, 0, self.screen_w, self.screen_h)
        self.camera_label.setStyleSheet("background-color: black;")

        # 7) Load sounds & note images
        self.load_sounds()
        self.load_note_images()

        # 8) Create piano keys
        self.keys_info = []
        self.create_white_keys()
        self.create_black_keys()

        # 9) Status bar
        self.setStatusBar(QStatusBar(self))

        # 10) MediaPipe
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.HAND_CONNECTIONS = self.mp_hands.HAND_CONNECTIONS

        # 11) Touch-lift approach
        self.is_held = {}
        for (_, nm) in self.keys_info:
            self.is_held[nm] = False

        # 12) Skeleton overlay
        self.skeleton_overlay = SkeletonOverlay(self, self.screen_w, self.screen_h)
        self.skeleton_overlay.show()

        # 13) Effects overlay (FlyingNotes)
        self.effects_overlay = EffectOverlay(self, self.screen_w, self.screen_h)
        self.effects_overlay.raise_()
        self.effects_overlay.show()

        # 14) Tile overlay
        self.tile_overlay = TileOverlay(self, self.screen_w, self.screen_h, piano=self)
        self.tile_overlay.raise_()
        self.tile_overlay.show()

        # 15) Timer for camera
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_camera)
        self.timer.start(30)

        # 16) "Teach You To Play" toggle
        self.auto_play_muted = False
        self.createTeachToggleButton()

        # 17) "Show Notes" toggle
        self.show_notes = True
        self.createShowNotesToggleButton()

        # 18) Score system
        self.score = 0
        self.scoreLabel = QLabel("Score: 0", self)
        self.scoreLabel.setStyleSheet("color: white; font-size: 20px;")
        self.scoreLabel.setGeometry(20, 10, 200, 40)
        self.scoreLabel.show()

        # For tile collisions if auto_play_muted
        self.last_tile_collision_time_for_note = {}
        self.tile_score_window_ms = 300

        # 19) Music selection UI
        self.createMusicSelection()

    def load_sounds(self):
        base = "C:/Users/LLR User/Desktop/music/Sounds/"
        white_notes = ["c4","d4","e4","f4","g4","a4","b4","c5","d5","e5","f5","g5","a5","b5"]
        black_notes = ["c40","d40","f40","g40","a40","c50","d50","f50","g50","a50"]

        for note in white_notes + black_notes:
            try:
                self.sounds[note] = pygame.mixer.Sound(base + f"{note}.wav")
            except pygame.error:
                print(f"Could not load sound: {note}.wav")

    def load_note_images(self):
        self.notePixmaps = []
        for i in range(1, 6):
            path = f"Assets/note{i}.png"
            pix = QPixmap(path)
            if not pix.isNull():
                self.notePixmaps.append(pix)
            else:
                print(f"Warning: Could not load {path}")

    def create_white_keys(self):
        white_keys = [
            ("c4", "Q"), ("d4", "W"), ("e4", "E"), ("f4", "R"),
            ("g4", "T"), ("a4", "Y"), ("b4", "U"),
            ("c5", "I"), ("d5", "O"), ("e5", "P"), ("f5", "["),
            ("g5", "]"), ("a5", "\\"), ("b5", "1")
        ]
        num_white = len(white_keys)
        key_w = self.screen_w / 15.0
        key_h = self.screen_h / 3.0
        total_w = num_white * key_w
        x_start = (self.screen_w - total_w) / 2

        # Store piano layout for alignment purposes
        self.piano_x_start = x_start
        self.piano_total_w = total_w

        margin_bottom = self.screen_h * 0.20
        y_pos = self.screen_h - key_h - margin_bottom

        for i, (note_name, shortcut) in enumerate(white_keys):
            x = x_start + i * key_w
            btn = QPushButton(note_name.upper(), self)
            btn.setObjectName(note_name)
            btn.setShortcut(shortcut)
            btn.setGeometry(QRect(int(x), int(y_pos), int(key_w), int(key_h)))
            btn.setStyleSheet("""
                background-color: rgba(255, 255, 255, 220);
                border: 1px solid black;
                font-weight: bold;
                font-size: 24px;
            """)
            btn.clicked.connect(self.play_sound)
            self.keys_info.append((btn, note_name))

            # Note: Removed separate QLabel for note names to avoid duplication

    def create_black_keys(self):
        black_keys = [
            ("c40", "2"), ("d40", "3"), ("f40", "5"), ("g40", "6"), ("a40", "7"),
            ("c50", "8"), ("d50", "9"), ("f50", "0"), ("g50", "-"), ("a50", "=")
        ]
        white_key_w = self.screen_w / 15.0
        black_key_w = white_key_w * 0.5
        black_key_h = self.screen_h / 4.5

        num_white = 14
        total_white_w = num_white * white_key_w
        x_white_start = (self.screen_w - total_white_w) / 2
        margin_bottom = self.screen_h * 0.20
        y_white_pos = self.screen_h - (self.screen_h / 3.0) - margin_bottom

        offsets = [
            white_key_w*0.7,  white_key_w*1.7,  white_key_w*3.7,
            white_key_w*4.7,  white_key_w*5.7,  white_key_w*7.7,
            white_key_w*8.7,  white_key_w*10.7, white_key_w*11.7,
            white_key_w*12.7,
        ]
        for i, (note_name, shortcut) in enumerate(black_keys):
            x = x_white_start + offsets[i]
            btn = QPushButton(note_name.upper(), self)
            btn.setObjectName(note_name)
            btn.setShortcut(shortcut)
            btn.setGeometry(QRect(int(x), int(y_white_pos), int(black_key_w), int(black_key_h)))
            btn.setStyleSheet("""
                background-color: rgba(0, 0, 0, 220);
                border: 1px solid black;
                color: white;
                font-weight: bold;
                font-size: 20px;
            """)
            btn.clicked.connect(self.play_sound)
            self.keys_info.append((btn, note_name))

            # Note: Removed separate QLabel for note names to avoid duplication

    def createTeachToggleButton(self):
        """
        Creates the "Teach You To Play" toggle button positioned aligned with the right edge of the piano.
        Slightly shifted to the right by 10 pixels for better alignment.
        """
        self.teachButton = QPushButton("Teach You To Play: ON", self)
        btn_w, btn_h = 200, 30  # Same size as Play Selected Music button
        padding_right = 20
        padding_between_buttons = 10
        shift_right = 10  # Shift to the right by 10 pixels

        # Align with the right edge of the piano and shift right
        x_pos = self.piano_x_start + self.piano_total_w - btn_w - padding_right + shift_right
        y_buttons = int(0.82 * self.screen_h)  # Positioned below the piano

        self.teachButton.setGeometry(int(x_pos), int(y_buttons), btn_w, btn_h)
        self.teachButton.setStyleSheet("""
            background-color: rgba(200, 200, 200, 200);
            font-size: 14px;
            font-weight: bold;
        """)
        self.teachButton.clicked.connect(self.toggleTeach)
        self.auto_play_muted = False  # Initially ON

    def createShowNotesToggleButton(self):
        """
        Creates the "Show Notes" toggle button positioned below the "Teach You To Play" button aligned with the piano's right edge.
        Slightly shifted to the right by 10 pixels for better alignment.
        """
        self.showNotesButton = QPushButton("Show Notes: ON", self)
        btn_w, btn_h = 200, 30  # Same size as Play Selected Music button
        padding_right = 20
        padding_between_buttons = 10
        shift_right = 10  # Shift to the right by 10 pixels

        # Align with the right edge of the piano and shift right
        x_pos = self.piano_x_start + self.piano_total_w - btn_w - padding_right + shift_right
        y_buttons = int(0.82 * self.screen_h) + 40 + padding_between_buttons  # Below Teach button

        self.showNotesButton.setGeometry(int(x_pos), int(y_buttons), btn_w, btn_h)
        self.showNotesButton.setStyleSheet("""
            background-color: rgba(200, 200, 200, 200);
            font-size: 14px;
            font-weight: bold;
        """)
        self.showNotesButton.clicked.connect(self.toggleShowNotes)
        self.show_notes = True  # Initially ON

    def toggleTeach(self):
        """
        Toggles the "Teach You To Play" mode.
        """
        self.auto_play_muted = not self.auto_play_muted
        if self.auto_play_muted:
            self.teachButton.setText("Teach You To Play: OFF")
        else:
            self.teachButton.setText("Teach You To Play: ON")

    def toggleShowNotes(self):
        """
        Toggles the visibility of note names on the piano keys by hiding or showing the button texts.
        """
        self.show_notes = not self.show_notes
        if self.show_notes:
            self.showNotesButton.setText("Show Notes: ON")
        else:
            self.showNotesButton.setText("Show Notes: OFF")
        for (btn, nm) in self.keys_info:
            if self.show_notes:
                btn.setText(nm.upper())
            else:
                btn.setText("")

    def createMusicSelection(self):
        """
        Adds a combo box and 'Play Selected Music' button to choose and play music.
        Positioned below the piano centered.
        """
        self.musicBox = QComboBox(self)
        self.musicBox.addItems(["Happy Birthday (basic)", "Interstellar (basic)"])
        box_w, box_h = 200, 30
        play_btn_w, play_btn_h = 200, 30
        total_width = box_w + 10 + play_btn_w
        x_box = (self.screen_w - total_width) / 2
        y_buttons = int(0.82 * self.screen_h)  # Positioned below the piano

        self.musicBox.setGeometry(int(x_box), int(y_buttons), box_w, box_h)

        self.playMusicButton = QPushButton("Play Selected Music", self)
        self.playMusicButton.setGeometry(int(x_box + box_w + 10), int(y_buttons), play_btn_w, play_btn_h)
        self.playMusicButton.setStyleSheet("""
            background-color: rgba(200, 200, 200, 200);
            font-size: 14px;
            font-weight: bold;
        """)
        self.playMusicButton.clicked.connect(self.onPlayMusicClicked)

    def onPlayMusicClicked(self):
        """
        When user clicks 'Play Selected Music', spawn the chosen piece's tiles.
        """
        selection = self.musicBox.currentText()
        if "Happy Birthday" in selection:
            self.spawnHappyBirthdayTiles()
        elif "Interstellar" in selection:
            self.spawnInterstellarTiles()
        else:
            print("Unknown selection. No tiles spawned.")

    def spawnHappyBirthdayTiles(self):
        """
        Spawns a basic sequence of tiles for 'Happy Birthday'.
        Each tile is smaller square with a border, offset horizontally if repeated quickly.
        """
        melody = [
            ('g4', 0),    ('g4', 800),  ('a4', 800), ('g4', 800), ('c5', 800), ('b4', 1000),
            ('g4', 800),  ('g4', 800),  ('a4', 800), ('g4', 800), ('d5', 800), ('c5', 1000),
            ('g4', 800),  ('g4', 800),  ('g5', 800), ('e5', 800), ('c5', 800), ('b4', 800), ('a4', 1200),
            ('f5', 800),  ('f5', 800),  ('e5', 800), ('c5', 800), ('d5', 800), ('c5', 1000)
        ]
        current_time = 0
        for (note_name, delta) in melody:
            current_time += delta
            QTimer.singleShot(current_time, lambda n=note_name: self.tile_overlay.spawnTile(n, fall_speed=4))

    def spawnInterstellarTiles(self):
        """
        Example 'Interstellar' basic sequence.
        """
        melody = [
            ('c4', 0), ('g4', 800), ('g4', 800), ('a4', 1000),
            ('g4', 800), ('f4', 800), ('e4', 1200), ('e4', 800),
            ('g4', 800), ('g4', 1000)
        ]
        current_time = 0
        for (note_name, delta) in melody:
            current_time += delta
            QTimer.singleShot(current_time, lambda n=note_name: self.tile_overlay.spawnTile(n, fall_speed=4))

    def play_sound(self):
        """Called when user clicks on a key (mouse or keyboard)."""
        note_name = self.sender().objectName()
        self.trigger_note_by_name(note_name)

    def trigger_note_by_name(self, note_name, from_tile=False):
        """
        Actually play the note + spawn flying note effect.
        from_tile=True => respect self.auto_play_muted (no sound if muted).
        Checks for scoring if tile was recently collided (auto_play_muted).
        """
        current_ms = int(time.time() * 1000)
        if from_tile and self.auto_play_muted:
            return

        # If auto_play_muted is True and user hits note within tile_score_window_ms => +10
        if (not from_tile) and self.auto_play_muted:
            collision_time = self.last_tile_collision_time_for_note.get(note_name, None)
            if collision_time is not None:
                if current_ms - collision_time <= self.tile_score_window_ms:
                    self.addScore(10)
                # Remove collision time so it can't be reused
                del self.last_tile_collision_time_for_note[note_name]

        if note_name in self.sounds:
            self.sounds[note_name].play()

        # Spawn flying note
        for (btn, nm) in self.keys_info:
            if nm == note_name:
                self.spawnFlyingNote(btn)
                break

    def addScore(self, points):
        """
        Adds points to the score and updates the score label.
        """
        self.score += points
        self.scoreLabel.setText(f"Score: {self.score}")

    def spawnFlyingNote(self, btn):
        """
        Spawns a FlyingNote effect above the given button.
        """
        if not self.notePixmaps:
            return
        pix_original = random.choice(self.notePixmaps)
        scale_factor = random.uniform(0.5, 1.2)
        sw = int(pix_original.width() * scale_factor)
        sh = int(pix_original.height() * scale_factor)
        pix_scaled = pix_original.scaled(sw, sh, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        rect = btn.geometry()
        cx = rect.x() + rect.width()//2
        cy = rect.y() + rect.height()//2
        offx = random.randint(-rect.width()//2, rect.width()//2)
        offy = random.randint(-rect.height()//2, rect.height()//2)
        sx = cx + offx - sw//2
        sy = cy + offy - sh//2

        note_label = FlyingNote(self.effects_overlay, pix_scaled, sx, sy)
        self.effects_overlay.raise_()

    def update_camera(self):
        """
        1) Capture webcam
        2) Flip horizontally
        3) Mediapipe -> find hands
        4) For each fingertip, if extended, see if it's in a bounding box
        5) "Touch-lift" approach: only trigger a note once on entry, release on exit
        6) Update skeleton overlay, display feed
        """
        ret, frame = self.cap.read()
        if not ret:
            return

        frame = cv2.flip(frame, 1)
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = frame_rgb.shape

        results = self.hands.process(frame_rgb)

        all_lines = []
        all_points = []
        touched_now = set()

        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                pts = []
                for lm in hand_landmarks.landmark:
                    px = int(lm.x * w)
                    py = int(lm.y * h)
                    pts.append((px, py))

                # Build skeleton lines
                for (start_idx, end_idx) in self.HAND_CONNECTIONS:
                    sx, sy = pts[start_idx]
                    ex, ey = pts[end_idx]
                    all_lines.append([(sx, sy), (ex, ey)])
                all_points.extend(pts)

                # Check fingertips
                for tip_idx, pip_idx in zip(self.FINGER_TIPS, self.FINGER_PIPS):
                    tip_x, tip_y = pts[tip_idx]
                    pip_x, pip_y = pts[pip_idx]
                    if tip_y < pip_y:
                        for (btn, nm) in self.keys_info:
                            r = btn.geometry()
                            if (r.left() <= tip_x <= r.right() and
                                r.top() <= tip_y <= r.bottom()):
                                touched_now.add(nm)

        # "Touch-lift" approach
        for (btn, nm) in self.keys_info:
            if nm in touched_now:
                if not self.is_held[nm]:
                    self.trigger_note_by_name(nm)
                    self.is_held[nm] = True
            else:
                if self.is_held[nm]:
                    self.is_held[nm] = False

        # Update skeleton overlay
        self.skeleton_overlay.skeleton_data = all_lines
        self.skeleton_overlay.points_data = all_points
        self.skeleton_overlay.update()

        # Show camera
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        qt_img = QImage(frame_bgr.data, w, h, w*3, QImage.Format_BGR888)
        pixmap = QPixmap.fromImage(qt_img)
        pixmap = pixmap.scaled(
            self.camera_label.width(),
            self.camera_label.height(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation
        )
        self.camera_label.setPixmap(pixmap)

    def spawnHappyBirthdayTiles(self):
        """
        Spawns a basic sequence of tiles for 'Happy Birthday'.
        Each tile is smaller square with a border, offset horizontally if repeated quickly.
        """
        melody = [
            ('g4', 0),    ('g4', 800),  ('a4', 800), ('g4', 800), ('c5', 800), ('b4', 1000),
            ('g4', 800),  ('g4', 800),  ('a4', 800), ('g4', 800), ('d5', 800), ('c5', 1000),
            ('g4', 800),  ('g4', 800),  ('g5', 800), ('e5', 800), ('c5', 800), ('b4', 800), ('a4', 1200),
            ('f5', 800),  ('f5', 800),  ('e5', 800), ('c5', 800), ('d5', 800), ('c5', 1000)
        ]
        current_time = 0
        for (note_name, delta) in melody:
            current_time += delta
            QTimer.singleShot(current_time, lambda n=note_name: self.tile_overlay.spawnTile(n, fall_speed=4))

    def spawnInterstellarTiles(self):
        """
        Example 'Interstellar' basic sequence.
        """
        melody = [
            ('c4', 0), ('g4', 800), ('g4', 800), ('a4', 1000),
            ('g4', 800), ('f4', 800), ('e4', 1200), ('e4', 800),
            ('g4', 800), ('g4', 1000)
        ]
        current_time = 0
        for (note_name, delta) in melody:
            current_time += delta
            QTimer.singleShot(current_time, lambda n=note_name: self.tile_overlay.spawnTile(n, fall_speed=4))

    def closeEvent(self, event):
        """
        Handles application closing: releases camera and quits Pygame.
        """
        if self.cap.isOpened():
            self.cap.release()
        self.hands.close()
        pygame.quit()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    window = ARPiano()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
