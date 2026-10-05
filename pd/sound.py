"""sound.py — playdate.sound with REAL AUDIO (pygame.mixer).

Decodes .pda (PCM/ADPCM) and plays with pygame.mixer. The objects stay userdata
(Permissive) so `type(v) == "table"` does not take the wrong branch, but the
playback methods make real sound.
"""

import os
import re
import struct

from .luaobj import Permissive

_mixer = None
_mixer_ready = False

import time as _time
_CLOCK_START = _time.monotonic()


def get_current_time():
    """playdate.sound.getCurrentTime(): seconds since the engine started."""
    return _time.monotonic() - _CLOCK_START


# --- note scheduler (one background thread instead of one per note) -----
# A sequence can schedule many hundreds of notes (Kickflip: 952). Spawning a
# threading.Timer per note would exhaust the OS (and crash a Raspberry Pi), so
# all scheduled notes go into a single heap drained by one worker thread.
import heapq as _heapq
import threading as _threading

_SCHED = []                      # heap: (when, seq, synth, freq, dur, vol)
_SCHED_LOCK = _threading.Lock()
_SCHED_WORKER = None
_SCHED_SEQ = 0


def _ensure_scheduler():
    global _SCHED_WORKER
    if _SCHED_WORKER is not None:
        return
    def _run():
        while True:
            now = get_current_time()
            due = []
            with _SCHED_LOCK:
                while _SCHED and _SCHED[0][0] <= now:
                    due.append(_heapq.heappop(_SCHED))
                nxt = _SCHED[0][0] if _SCHED else None
            for _when, _s, synth, freq, dur, vol in due:
                try:
                    synth._play_now(freq, dur, vol)
                except Exception:  # noqa: BLE001
                    pass
            # Adaptive sleep: wake up right before the next note, capped so that
            # new notes or stop() are picked up quickly. Keeps the CPU near idle
            # on low-resource hardware (Raspberry Pi Zero 2 W).
            if due:
                _time.sleep(0.002)
            elif nxt is None:
                _time.sleep(0.02)
            else:
                _time.sleep(min(0.02, max(0.002, nxt - get_current_time())))
    _SCHED_WORKER = _threading.Thread(target=_run, daemon=True)
    _SCHED_WORKER.start()


def _schedule_note(when, synth, freq, dur, vol):
    global _SCHED_SEQ
    _ensure_scheduler()
    _SCHED_SEQ += 1
    with _SCHED_LOCK:
        _heapq.heappush(_SCHED, (float(when), _SCHED_SEQ, synth, freq, dur, vol))


def _clear_scheduled(synth=None):
    """Drops pending notes (optionally only one synth's) — used by noteOff/stop."""
    with _SCHED_LOCK:
        if synth is None:
            _SCHED.clear()
        else:
            _SCHED[:] = [n for n in _SCHED if n[2] is not synth]
            _heapq.heapify(_SCHED)


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
        # synth.new([waveform]): the game passes the waveform constant
        # (kWaveSquare=0, Triangle=1, Sine=2, Noise=3, Sawtooth=4). IGNORING it
        # (as before) made every synth use the default sawtooth, so the noise
        # percussions (kWaveNoise) never sounded like noise.
        self._waveform = 4          # default = SAWTOOTH (bright instrumental music of the original)
        if args and args[0] is not None:
            try:
                self._waveform = int(args[0])
            except (TypeError, ValueError):
                pass
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
        now = get_current_time()
        target = float(when) if when else now
        if target <= now:
            self._play_now(self._freq, dur, self._volume)
        else:
            _schedule_note(target, self, self._freq, dur, self._volume)

    def _play_now(self, freq, dur, vol):
        """Renders and plays a tone immediately (called by the scheduler)."""
        s = self._make_tone(freq, dur)
        if s is not None:
            try:
                s.set_volume(float(vol))
            except Exception:  # noqa: BLE001
                pass
            s.play()
        self._sound = s
        self._playing = True

    def playMIDINote(self, note, vol=1.0, *a):
        freq = 440.0 * (2.0 ** ((int(note) - 69) / 12.0))
        self.playNote(freq, vol)

    def noteOff(self, *a):
        _clear_scheduled(self)
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


# ----------------------------------------------------------------------
# Sequencer: playdate.sound.sequence / playdate.sound.track
#
# Real games (e.g. Kickflip Coast) build their music with a sequencer:
#   local seq = playdate.sound.sequence.new()
#   local tr  = seq:addTrack()            -- no arg -> new track
#   tr:setInstrument(synth)
#   tr:addNote(step, note, length, velocity)
#   seq:setTempo(stepsPerSecond)          -- NOTE: steps per second, not BPM
#   seq:play()
#
# Notes are scheduled through the track's Synth using absolute engine time
# (Synth.playNote already handles the `when` absolute scheduling).

_NOTE_SEMITONE = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def _note_to_freq(note):
    """MIDI note number or note name ("C4", "Db3") -> frequency in Hz."""
    if isinstance(note, str):
        m = re.match(r"\s*([A-Ga-g])([#b]?)(-?\d+)", note)
        if not m:
            return 440.0
        letter, acc, octv = m.group(1).lower(), m.group(2), int(m.group(3))
        semi = _NOTE_SEMITONE.get(letter, 0)
        if acc == "#":
            semi += 1
        elif acc == "b":
            semi -= 1
        midi = (octv + 1) * 12 + semi
    else:
        try:
            midi = int(note)
        except (TypeError, ValueError):
            return 440.0
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _note_fields(obj):
    """Reads step/note/length/velocity from a dict or table-like object."""
    def g(k, d=None):
        try:
            return obj[k]
        except Exception:  # noqa: BLE001
            try:
                return getattr(obj, k, d)
            except Exception:  # noqa: BLE001
                return d
    return g("step", 0), g("note", 0), g("length", 1), g("velocity", 1.0)


class Track(Permissive):
    """playdate.sound.track: a list of note events played by an instrument."""

    def __init__(self, *args):
        self.notes = []          # (step, note, length, velocity)
        self.instrument = None   # usually a Synth
        self.muted = False
        self.enabled = True
        self.volume = 1.0

    def addNote(self, *a):
        if len(a) == 1 and a[0] is not None and not isinstance(a[0], (int, float, str)):
            self.notes.append(_note_fields(a[0]))     # table/dict form
        elif a:
            step = a[0]
            note = a[1] if len(a) > 1 else 0
            length = a[2] if len(a) > 2 else 1
            vel = a[3] if len(a) > 3 and a[3] is not None else 1.0
            self.notes.append((step, note, length, vel))

    def addNotes(self, notes, *a):
        for n in (notes or []):
            try:
                self.addNote(n)
            except Exception:  # noqa: BLE001
                pass

    def setNotes(self, notes, *a):
        self.notes = []
        self.addNotes(notes)

    def getNotes(self, *a):
        return [{"step": s, "note": n, "length": l, "velocity": v}
                for (s, n, l, v) in self.notes]

    def setInstrument(self, inst, *a):
        self.instrument = inst

    def getInstrument(self, *a):
        return self.instrument

    def setMuted(self, m, *a):
        self.muted = bool(m)

    def isMuted(self, *a):
        return self.muted

    def setEnabled(self, e, *a):
        self.enabled = bool(e)

    def isEnabled(self, *a):
        return self.enabled

    def setVolume(self, v, *a):
        try:
            self.volume = float(v)
        except (TypeError, ValueError):
            pass

    def getVolume(self, *a):
        return self.volume

    def getLength(self, *a):
        end = 0
        for step, _n, length, _v in self.notes:
            try:
                end = max(end, float(step) + float(length))
            except (TypeError, ValueError):
                pass
        return end

    def getNotesActive(self, *a):
        return 0


class Sequence(Permissive):
    """playdate.sound.sequence: schedules its tracks' notes over time."""

    def __init__(self, *args):
        self.tracks = []
        self.tempo = 4.0            # steps per second (playdate setTempo)
        self.playing = False
        self.loop_start = 0
        self.loop_end = 0
        self.loop_count = 0
        self._start = 0.0
        self._step = 0
        self._loop_timer = None

    # -- structure --
    def addTrack(self, *a):
        if a and a[0] is not None:
            self.tracks.append(a[0])
            return a[0]
        t = Track()                 # no arg -> create and return a new track
        self.tracks.append(t)
        return t

    def getTrackCount(self, *a):
        return len(self.tracks)

    def getTrackAtIndex(self, i, *a):
        try:
            return self.tracks[int(i)]
        except (IndexError, TypeError, ValueError):
            return None

    def setTrackAtIndex(self, i, track, *a):
        try:
            self.tracks[int(i)] = track
        except (IndexError, TypeError, ValueError):
            pass

    def removeTrackAtIndex(self, i, *a):
        try:
            del self.tracks[int(i)]
        except (IndexError, TypeError, ValueError):
            pass

    # -- timing --
    def setTempo(self, sps, *a):
        try:
            self.tempo = float(sps) or 4.0
        except (TypeError, ValueError):
            pass

    def getTempo(self, *a):
        return self.tempo

    def setLoops(self, *a):
        # setLoops(startStep, endStep, [loopCount]) or setLoops(loopCount)
        if len(a) >= 2:
            self.loop_start = int(a[0] or 0)
            self.loop_end = int(a[1] or 0)
            self.loop_count = int(a[2]) if len(a) > 2 and a[2] is not None else 0
        elif a:
            self.loop_end = int(self.getLength())
            self.loop_count = int(a[0] or 0)
        else:
            self.loop_end = int(self.getLength())
            self.loop_count = 0

    def getLength(self, *a):
        n = 0
        for tr in self.tracks:
            try:
                n = max(n, tr.getLength())
            except Exception:  # noqa: BLE001
                pass
        return n

    def getCurrentStep(self, *a):
        return self._step

    def goToStep(self, step, play=None, *a):
        try:
            self._step = int(step or 0)
        except (TypeError, ValueError):
            self._step = 0

    # -- playback --
    def play(self, *a):
        self.playing = True
        self._start = get_current_time()
        self._schedule_pass(self._start)
        if self.loop_end > self.loop_start and self.tempo > 0:
            self._schedule_loop()

    def _schedule_pass(self, base):
        sec_per_step = (1.0 / self.tempo) if self.tempo > 0 else 0.25
        for tr in self.tracks:
            if getattr(tr, "muted", False) or not getattr(tr, "enabled", True):
                continue
            inst = getattr(tr, "instrument", None)
            if inst is None:
                continue
            for step, note, length, vel in getattr(tr, "notes", []):
                try:
                    when = base + float(step) * sec_per_step
                    dur = max(0.02, float(length) * sec_per_step)
                    vol = float(vel) * float(getattr(tr, "volume", 1.0))
                    inst.playNote(_note_to_freq(note), vol, dur, when)
                except Exception:  # noqa: BLE001
                    pass

    def _schedule_loop(self):
        import threading
        period = max(0.05, (self.loop_end - self.loop_start) / self.tempo)

        def _tick():
            if not self.playing:
                return
            self._schedule_pass(get_current_time())
            self._loop_timer = threading.Timer(period, _tick)
            self._loop_timer.daemon = True
            self._loop_timer.start()

        self._loop_timer = threading.Timer(period, _tick)
        self._loop_timer.daemon = True
        self._loop_timer.start()

    def stop(self, *a):
        self.playing = False
        if self._loop_timer is not None:
            self._loop_timer.cancel()
            self._loop_timer = None
        self.allNotesOff()

    def isPlaying(self, *a):
        return self.playing

    def allNotesOff(self, *a):
        for tr in self.tracks:
            inst = getattr(tr, "instrument", None)
            if inst is not None:
                try:
                    inst.noteOff()
                except Exception:  # noqa: BLE001
                    pass
