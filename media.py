"""What's playing (title, artist, album art) and media control through Windows' own
media session API - the same one behind the media flyout next to the volume overlay.

Runs its own asyncio loop on a background thread. Results are delivered through
``on_change(NowPlaying or None)``; commands return immediately.
"""

import asyncio
import io
import threading
from dataclasses import dataclass
from typing import Optional

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status,
    )
    from winrt.windows.storage.streams import DataReader
    AVAILABLE = True
except Exception:   # package missing / not Windows
    AVAILABLE = False


@dataclass
class NowPlaying:
    title: str
    artist: str
    album: str
    app: str
    playing: bool
    shuffle: Optional[bool]
    art: Optional[bytes]        # encoded image (PNG/JPEG) or None

    @property
    def key(self):
        return (self.title, self.artist, self.album, self.app)


def _app_name(aumid: str) -> str:
    """'Spotify.exe' / 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify' -> 'Spotify'."""
    name = (aumid or "").split("!")[-1]
    name = name.rsplit("\\", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.split(".")[-1] if "." in name else name


class MediaWatcher:
    POLL_S = 1.0

    def __init__(self, on_change):
        self.on_change = on_change
        self.current: Optional[NowPlaying] = None
        self._loop = None
        self._manager = None
        self._art_key = None
        self._art = None
        self._stop = False

    def start(self):
        if AVAILABLE:
            threading.Thread(target=lambda: asyncio.run(self._main()), name="media", daemon=True).start()

    def stop(self):
        self._stop = True

    # ---- commands (any thread)
    def play_pause(self):
        return self._command("try_toggle_play_pause_async")

    def next(self):
        return self._command("try_skip_next_async")

    def previous(self):
        return self._command("try_skip_previous_async")

    def toggle_shuffle(self):
        return self._command("shuffle")

    def _command(self, name):
        """True if a media session took the command (False -> caller can fall back to media keys)."""
        if not (AVAILABLE and self._loop and self._manager):
            return False
        fut = asyncio.run_coroutine_threadsafe(self._run(name), self._loop)
        try:
            return fut.result(2.0)
        except Exception:
            return False

    async def _run(self, name):
        session = self._manager.get_current_session()
        if session is None:
            return False
        if name == "shuffle":
            info = session.get_playback_info()
            current = bool(info.is_shuffle_active)
            return bool(await session.try_change_shuffle_active_async(not current))
        ok = await getattr(session, name)()
        asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(self._poll()))
        return bool(ok)

    # ---- watching
    async def _main(self):
        self._loop = asyncio.get_running_loop()
        try:
            self._manager = await _Manager.request_async()
        except Exception:
            return
        while not self._stop:
            await self._poll()
            await asyncio.sleep(self.POLL_S)

    async def _poll(self):
        try:
            now = await self._read()
        except Exception:
            now = None
        before = self.current
        if (now is None) != (before is None) or (now and before and
                                                   (now.key, now.playing, now.shuffle, now.art is None)
                                                   != (before.key, before.playing, before.shuffle, before.art is None)):
            self.current = now
            self.on_change(now)

    async def _read(self) -> Optional[NowPlaying]:
        session = self._manager.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        title = props.title or ""
        if not title:
            return None
        key = (title, props.artist, props.album_title)
        if key != self._art_key:
            self._art_key, self._art = key, await self._read_art(props.thumbnail)
        shuffle = info.is_shuffle_active
        return NowPlaying(title=title, artist=props.artist or "", album=props.album_title or "",
                          app=_app_name(session.source_app_user_model_id),
                          playing=info.playback_status == _Status.PLAYING,
                          shuffle=None if shuffle is None else bool(shuffle), art=self._art)

    @staticmethod
    async def _read_art(thumb) -> Optional[bytes]:
        if thumb is None:
            return None
        try:
            stream = await thumb.open_read_async()
            size = int(stream.size)
            if not size:
                return None
            reader = DataReader(stream)
            await reader.load_async(size)
            return bytes(reader.read_buffer(size))
        except Exception:
            return None


def art_image(data: Optional[bytes]):
    """PIL image from album art bytes (None if missing/unreadable)."""
    if not data:
        return None
    try:
        from PIL import Image
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
