"""sound.py — playdate.sound with REAL AUDIO (pygame.mixer).

Decodes .pda (PCM/ADPCM) and plays with pygame.mixer. The objects stay userdata
(Permissive) so `type(v) == "table"` does not take the wrong branch, but the
playback methods make real sound.
"""

import os
import struct

from .luaobj import Permissive

_mixer = None
_mixer_ready = False

import time as _time
_CLOCK_START = _time.monotonic()


def get_current_time():
    """playdate.sound.getCurrentTime(): seconds since the engine started."""
    return _time.monotonic() - _CLOCK_START


def _ensure_mixer():
    """Initializes pygame.mixer once (44100 Hz, 16-bit, stereo)."""
    global _mixer, _mixer_ready
    if _mixer_ready:
        return _mixer is not None
    _mixer_ready = True
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.set_num_channels(32)
        _mixer = pygame.mixer
        return True
    except Exception:
        return False


def _resolve_pda(asset_dir, path):
    """Resolves the real .pda path (the game asks for the name without extension)."""
    if not asset_dir or not path:
        return None
    name = str(path).lstrip("/")
    base, ext = os.path.splitext(name)
    cands = [name, base]
    for cand in cands:
        p = os.path.join(asset_dir, cand + ".pda")
        if os.path.isfile(p):
            return p
        if os.path.isfile(cand):
            return cand
    return None


def _samples_to_sound(rate, samples):
    """Converts mono float samples to a pygame.mixer.Sound (16-bit mono)."""
    if not _ensure_mixer():
        return None
    import array
    buf = array.array("h")
    for x in samples:
        v = int(max(-1.0, min(1.0, x)) * 32767)
        buf.append(v)
    try:
        return _mixer.Sound(buffer=buf.tobytes())
    except Exception:
        return None


class SoundPlayer(Permissive):
    """sampleplayer / fileplayer / sample: plays a .pda."""

    def __init__(self, *args):
        self.path = args[0] if args else None
        self.asset_dir = args[1] if len(args) > 1 else None
        self._sound = None
        self._loaded = False
        self._volume = 1.0
        self._rate = 1.0
        self._loop = False
        self._playing = False

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        p = _resolve_pda(self.asset_dir, self.path)
        if not p:
            return
        try:
            from .pda import decode_pda
            with open(p, "rb") as f:
                data = f.read()
            rate, samples = decode_pda(data)
            self._sound = _samples_to_sound(rate, samples)
            if self._sound is not None:
                self._sound.set_volume(self._volume)
        except Exception:
            self._sound = None

    def play(self, *a):
        self._load()
        if self._sound is None:
            return
        self._sound.play(loops=-1 if self._loop else 0)
        self._playing = True

    def playAt(self, x, y=None, *a):
        # panning based on the X position (0..400 -> -1..+1)
        self._load()
        if self._sound is None:
            return
        try:
            pan = max(-1.0, min(1.0, (float(x) / 200.0) - 1.0))
            self._sound.set_volume(self._volume * (1.0 - abs(pan)))
        except Exception:
            pass
        self._sound.play(loops=-1 if self._loop else 0)
        self._playing = True

    def stop(self):
        if self._sound is not None:
            self._sound.stop()
        self._playing = False

    def pause(self):
        if self._sound is not None:
            self._sound.pause()
        self._playing = False

    def isPlaying(self):
        if self._sound is not None:
            try:
                return self._sound.get_num_channels() > 0
            except Exception:
                pass
        return self._playing

    def setPaused(self, paused):
        if paused:
            self.pause()
        else:
            self.play()

    def setVolume(self, v, *a):
        self._volume = float(v or 0)
        if self._sound is not None:
            self._sound.set_volume(self._volume)

    def getVolume(self):
        return self._volume

    def setRate(self, r, *a):
        self._rate = float(r or 1.0)

    def getRate(self):
        return self._rate

    def setLoopRange(self, *a):
        self._loop = True

    def setPlayRange(self, *a):
        pass

    def getLength(self):
        if self._sound is not None:
            return self._sound.get_length()
        return 0.0

    def getOffset(self):
        return 0.0

    def setOffset(self, *a):
        pass

    def setFinishCallback(self, *a):
        pass

    def setLoopCallback(self, *a):
        pass

    def setRateMod(self, *a):
        pass

    def setVolumeMod(self, *a):
        pass


class Synth(Permissive):
    """synth: generates a tone with an ADSR envelope (pygame.mixer).

    Playdate's default is NOISE (not sine): Kickflip's effects are
    percussion/cymbals (white noise with a short ADSR), not soft beeps. The
    noise wave uses the frequency to control the character (higher = brighter).
    The ADSR envelope the game configures with setADSR is applied.
    """

    def __init__(self, *args):
        self._waveform = 4          # default = SAWTOOTH (bright instrumental music of the original)
        self._volume = 1.0
        self._freq = 440.0
        self._playing = False
        self._sound = None
        self._adsr = (0.0, 0.0, 1.0, 0.0)   # attack, decay, sustain, release

    def _osc(self, t, freq):
        # Waveform numbering matches the SDK enum SoundWaveform (C:
        # kWaveformSquare=0, Triangle=1, Sine=2, Noise=3, Sawtooth=4). The
        # constants exposed to Lua (playdate.sound.kWave*) use the same numbers,
        # so a game that passes kWaveTriangle gets a triangle here.
        import math
        ph = 2 * math.pi * freq * t
        w = self._waveform
        if w == 4:      # sawtooth (bright, many harmonics)
            return 2.0 * ((freq * t) % 1.0) - 1.0
        if w == 3:      # noise
            import random
            return random.uniform(-1.0, 1.0)
        if w == 1:      # triangle
            return 2 / math.pi * math.asin(math.sin(ph))
        if w == 2:      # sine
            return math.sin(ph)
        return 1.0 if math.sin(ph) >= 0 else -1.0   # square (0)

    def _make_tone(self, freq, dur):
        if not _ensure_mixer():
            return None
        import array
        rate = 44100
        n = int(rate * dur)
        atk, dec, sus, rel = self._adsr
        buf = array.array("h")
        for i in range(n):
            t = i / rate
            # ADSR envelope
            if t < atk and atk > 0:
                env = t / atk
            elif t < atk + dec:
                env = 1.0 - (1.0 - sus) * ((t - atk) / dec) if dec > 0 else sus
            elif t < dur - rel:
                env = sus
            else:
                env = sus * max(0.0, (dur - t) / rel) if rel > 0 else sus
            v = self._osc(t, freq) * env
            buf.append(int(max(-1.0, min(1.0, v)) * 32767 * 0.5))
        try:
            return _mixer.Sound(buffer=buf.tobytes())
        except Exception:
            return None

    def playNote(self, freq, vol=1.0, length=None, when=None, *a):
        """playNote(pitch, volume, length, when): schedules the note.

        `when` is the time (seconds) at which it should sound, relative to NOW;
        Kickflip's music schedules a melody with notes at 0.13, 0.26, 0.38...
        seconds. Without respecting it, all notes sounded at once (jumbled
        noise). `length` is the note duration.
        """
        self._freq = float(freq)
        self._volume = float(vol or 1.0)
        atk, dec, sus, rel = self._adsr
        dur = float(length) if length else max(0.1, atk + dec + rel + 0.05)
        # `when` is ABSOLUTE on the sound engine clock (getCurrentTime), NOT
        # relative to now. Kickflip's music schedules notes at absolute song
        # times; if interpreted as "from now", the instruments bunch up.
        # delay = when - current_clock.
        now = get_current_time()
        target = float(when) if when else now
        delay = max(0.0, target - now)

        def _play():
            self._sound = self._make_tone(self._freq, dur)
            if self._sound is not None:
                self._sound.set_volume(self._volume)
                self._sound.play()
            self._playing = True

        if delay > 0:
            import threading
            threading.Timer(delay, _play).start()
        else:
            _play()

    def playMIDINote(self, note, vol=1.0, *a):
        freq = 440.0 * (2.0 ** ((int(note) - 69) / 12.0))
        self.playNote(freq, vol)

    def noteOff(self, *a):
        if self._sound is not None:
            self._sound.stop()
        self._playing = False

    def stop(self):
        self.noteOff()

    def isPlaying(self):
        return self._playing

    def setWaveform(self, w, *a):
        self._waveform = int(w or 0)

    def setVolume(self, v, *a):
        self._volume = float(v or 0)

    def getVolume(self):
        return self._volume

    def setADSR(self, attack, decay, sustain, release, *a):
        self._adsr = (float(attack or 0), float(decay or 0),
                      float(sustain if sustain is not None else 1.0),
                      float(release or 0))

    def setAttack(self, v, *a):
        a, d, s, r = self._adsr
        self._adsr = (float(v or 0), d, s, r)

    def setDecay(self, v, *a):
        a, d, s, r = self._adsr
        self._adsr = (a, float(v or 0), s, r)

    def setSustain(self, v, *a):
        a, d, s, r = self._adsr
        self._adsr = (a, d, float(v if v is not None else 1.0), r)

    def setRelease(self, v, *a):
        a, d, s, r = self._adsr
        self._adsr = (a, d, s, float(v or 0))

    def setFrequencyMod(self, *a):
        pass


class Instrument(Permissive):
    def __init__(self, *args):
        self._voices = []

    def addVoice(self, *a):
        pass

    def playNote(self, *a):
        pass

    def playMIDINote(self, *a):
        pass

    def noteOff(self, *a):
        pass


class Channel(Permissive):
    def __init__(self, *args):
        pass

    def addSource(self, *a):
        pass

    def removeSource(self, *a):
        pass

    def addEffect(self, *a):
        pass

    def setVolume(self, *a):
        pass

    def setPan(self, *a):
        pass
