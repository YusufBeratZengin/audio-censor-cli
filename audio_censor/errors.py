"""Exception types used across the package."""

from __future__ import annotations


class AudioCensorError(Exception):
    """Base class for every error raised on purpose by this package."""


class TranscriptError(AudioCensorError):
    """Raised when a transcript file cannot be parsed."""


class ConfigError(AudioCensorError):
    """Raised for invalid user supplied options or word lists."""


class FFmpegError(AudioCensorError):
    """Raised when FFmpeg/FFprobe is missing or fails."""

    def __init__(self, message: str, *, command: list[str] | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.command = command or []
        self.stderr = stderr
