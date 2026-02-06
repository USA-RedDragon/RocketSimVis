"""Video recorder for RocketSimVis - captures OpenGL frames to .mp4

Records frames only when new UDP data arrives. In headless mode,
stops recording and exits on receipt of an episode_end signal.
Uses ffmpeg subprocess for reliable H.264 encoding.
"""

import os
import time
import subprocess
import shutil
import numpy as np


class VideoRecorder:
    """Records OpenGL framebuffer frames to an MP4 video file.
    
    Pipes raw RGB frames to ffmpeg for H.264 encoding.
    Only writes frames when new game state data has been received,
    avoiding frozen frames during PPO training pauses.
    """

    WIDTH = 2560
    HEIGHT = 1440
    FPS = 60

    def __init__(self, output_dir: str, name: str = ""):
        """
        Args:
            output_dir: Directory where .mp4 files will be saved.
            name: Optional name prefix for the recording file.
        """
        self.output_dir = output_dir
        self._name = name
        self._proc = None
        self.is_recording = False
        self.frame_count = 0
        self.recording_path = None
        self._last_recv_time = -1  # Track recv_time to detect new data
        self._fractional_frames = 0.0  # Accumulated fractional frames for timing

    def _ensure_dir(self):
        os.makedirs(self.output_dir, exist_ok=True)

    def _generate_filename(self):
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        if self._name:
            return f"{self._name}_{timestamp}.mp4"
        return f"recording_{timestamp}.mp4"

    def start(self):
        """Start a new recording."""
        if self.is_recording:
            return

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            print("[VideoRecorder] ERROR: ffmpeg not found in PATH", flush=True)
            return

        self._ensure_dir()
        filename = self._generate_filename()
        self.recording_path = os.path.join(self.output_dir, filename)

        cmd = [
            ffmpeg,
            "-y",                       # Overwrite output
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{self.WIDTH}x{self.HEIGHT}",
            "-r", str(self.FPS),
            "-i", "-",                   # Read from stdin
            "-c:v", "libx265",
            "-vf", "scale=1920:1080:flags=lanczos",
            "-preset", "slow",
            "-x265-params", "aq-mode=2:aq-strength=0.8:psy-rd=0:psy-rdoq=0:deblock=0,0",
            "-crf", "25",               # Good quality
            "-pix_fmt", "yuv420p",      # Widely compatible pixel format
            "-tag:v", "hvc1",           # Apple/messaging app compatibility tag
            "-movflags", "+faststart",  # Web-friendly: moov atom at start
            self.recording_path
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            print(f"[VideoRecorder] ERROR: Failed to start ffmpeg: {e}", flush=True)
            self._proc = None
            return

        self.is_recording = True
        self.frame_count = 0
        self._fractional_frames = 0.0
        print(f"[VideoRecorder] Started recording: {self.recording_path}", flush=True)

    def stop(self):
        """Stop the current recording and finalize the video file."""
        if not self.is_recording:
            return

        self.is_recording = False
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self._proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None

        duration = self.frame_count / self.FPS if self.FPS > 0 else 0
        print(f"[VideoRecorder] Stopped recording: {self.recording_path}", flush=True)
        print(f"[VideoRecorder] {self.frame_count} frames, {duration:.1f}s duration", flush=True)
        self.recording_path = None
        self.frame_count = 0

    def has_new_data(self, recv_time: float) -> bool:
        """Check if recv_time indicates new data since last check.
        
        Args:
            recv_time: The recv_time from the current game state.
            
        Returns:
            True if this is a new frame (recv_time changed), False otherwise.
        """
        if recv_time <= 0:
            return False
        if recv_time != self._last_recv_time:
            self._last_recv_time = recv_time
            return True
        return False

    def calc_frame_count(self, delta_time: float) -> int:
        """Calculate how many video frames a game state with the given delta_time should produce.
        
        Accumulates fractional frames across calls to prevent timing drift.
        
        Args:
            delta_time: Game time elapsed for this state (seconds).
            
        Returns:
            Number of video frames to render and write for this state (>= 1).
        """
        if delta_time > 0:
            self._fractional_frames += delta_time * self.FPS
            num_frames = int(self._fractional_frames)
            self._fractional_frames -= num_frames
            return max(num_frames, 1)
        return 1

    def write_single_frame(self, pixels: np.ndarray):
        """Write exactly one frame to the video file.
        
        Args:
            pixels: numpy array of shape (HEIGHT, WIDTH, 3) in RGB format.
        """
        if not self.is_recording or self._proc is None:
            return

        try:
            self._proc.stdin.write(pixels.tobytes())
            self.frame_count += 1
        except (BrokenPipeError, OSError):
            print("[VideoRecorder] ERROR: ffmpeg pipe broken, stopping recording", flush=True)
            self.is_recording = False
            self._proc = None

    def write_frame(self, pixels: np.ndarray, delta_time: float = 0.0):
        """Write frame(s) to the video file, duplicating as needed for real-time playback.
        
        Uses delta_time to determine how many video frames this game state
        should produce. For example, with tickSkip=8 at 120Hz physics,
        delta_time=0.0667s, which at 60fps video = ~4 frames per state.
        
        Args:
            pixels: numpy array of shape (HEIGHT, WIDTH, 3) in RGB format.
            delta_time: Game time elapsed for this state (seconds). If 0,
                        writes exactly one frame (fallback).
        """
        if not self.is_recording or self._proc is None:
            return

        if delta_time > 0:
            # Accumulate fractional frames: delta_time * FPS
            self._fractional_frames += delta_time * self.FPS
            num_frames = int(self._fractional_frames)
            self._fractional_frames -= num_frames
            # Always write at least 1 frame per state
            num_frames = max(num_frames, 1)
        else:
            num_frames = 1

        frame_bytes = pixels.tobytes()
        try:
            for _ in range(num_frames):
                self._proc.stdin.write(frame_bytes)
                self.frame_count += 1
        except (BrokenPipeError, OSError):
            print("[VideoRecorder] ERROR: ffmpeg pipe broken, stopping recording", flush=True)
            self.is_recording = False
            self._proc = None

    def __del__(self):
        self.stop()
