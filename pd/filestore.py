"""filestore.py — playdate.file and playdate.datastore.

Per-game sandbox: `<game>/.pd_data/`. `file.open` returns a File object
(userdata, see pd/luaobj.py) with :read/:readline/:write/:seek/:tell/:close.
"""

import json
import os
import time

from .luaobj import Permissive

kFileRead = 1
kFileWrite = 2
kFileAppend = 3
kSeekSet = 0
kSeekFromCurrent = 1
kSeekFromEnd = 2

_MODE = {1: "rb", 2: "wb", 3: "ab"}


class File(Permissive):
    def __init__(self, path, mode=kFileRead):
        self.path = path
        self._f = open(path, _MODE.get(int(mode), "rb"))

    def read(self, n=None):
        data = self._f.read() if n is None else self._f.read(int(n))
        return data if data else None            # Playdate returns nil at EOF

    def readline(self):
        line = self._f.readline()
        return line if line else None

    def write(self, s):
        self._f.write(s if isinstance(s, (bytes, bytearray)) else str(s).encode())

    def close(self):
        try:
            self._f.close()
        except Exception:  # noqa: BLE001
            pass

    def flush(self):
        try:
            self._f.flush()
        except Exception:  # noqa: BLE001
            pass

    def seek(self, offset, whence=0):
        try:
            return self._f.seek(int(offset), int(whence))
        except Exception:  # noqa: BLE001
            return 0

    def tell(self):
        try:
            return self._f.tell()
        except Exception:  # noqa: BLE001
            return 0

    def getSize(self):
        try:
            pos = self._f.tell()
            self._f.seek(0, 2)
            size = self._f.tell()
            self._f.seek(pos)
            return size
        except Exception:  # noqa: BLE001
            return 0


class FileSystem:
    """playdate.file, rooted in the game sandbox."""

    def __init__(self, root):
        self.root = root
        if root and not os.path.isdir(root):
            try:
                os.makedirs(root, exist_ok=True)
            except OSError:
                pass

    def _p(self, path):
        return os.path.join(self.root, str(path).lstrip("/")) if self.root else str(path)

    # --- API -----------------------------------------------------------
    def open(self, path, mode=kFileRead):
        try:
            return File(self._p(path), mode)
        except OSError:
            return None, "could not open"

    def exists(self, path):
        return os.path.exists(self._p(path))

    def isdir(self, path):
        return os.path.isdir(self._p(path))

    def listFiles(self, path="", show_hidden=False):
        try:
            items = sorted(os.listdir(self._p(path)))
        except OSError:
            return []
        if not show_hidden:
            items = [i for i in items if not i.startswith(".")]
        return items

    def mkdir(self, path):
        try:
            os.makedirs(self._p(path), exist_ok=True)
            return True
        except OSError:
            return False

    def delete(self, path, recursive=False):
        p = self._p(path)
        try:
            if os.path.isdir(p):
                if not recursive:
                    os.rmdir(p)
                else:
                    import shutil
                    shutil.rmtree(p)
            else:
                os.remove(p)
            return True
        except OSError:
            return False

    def getSize(self, path):
        try:
            return os.path.getsize(self._p(path))
        except OSError:
            return 0

    def modtime(self, path):
        try:
            return int(os.path.getmtime(self._p(path)))
        except OSError:
            return 0

    def getType(self, path):
        p = self._p(path)
        if os.path.isdir(p):
            return "directory"
        if os.path.isfile(p):
            return "file"
        return None

    def rename(self, path, new_path):
        try:
            os.rename(self._p(path), self._p(new_path))
            return True
        except OSError:
            return False


class DataStore:
    """playdate.datastore (a fixed file inside the sandbox).

    The console stores datastores as JSON: `read` DESERIALIZES and `write`
    SERIALIZES. Returning the raw text breaks the game as soon as it indexes
    it (`attempt to index a string value`).
    """

    def __init__(self, fs):
        self.fs = fs

    @staticmethod
    def _to_python(v, depth=0):
        """Lua table -> Python dict/list (so it can be serialized to JSON)."""
        if depth > 8:
            return None
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        if isinstance(v, (list, tuple)):
            return [DataStore._to_python(x, depth + 1) for x in v]
        try:
            keys = list(v.keys())
        except Exception:  # noqa: BLE001
            return str(v)
        if not keys:
            return {}
        out = {}
        for k in keys:
            try:
                out[str(k)] = DataStore._to_python(v[k], depth + 1)
            except Exception:  # noqa: BLE001
                pass
        return out

    def read(self, filename="data"):
        f = self.fs.open(filename, kFileRead)
        if not isinstance(f, File):
            return None
        try:
            text = f.read()
        finally:
            f.close()
        # File.read() opens in "rb" -> returns BYTES. If it is not decoded, the
        # json.loads never runs and this returns a string (which the game treats
        # as "there is data" and then explodes with "attempt to index a string
        # value"). The cause of Fishing Simulator crash at gameData.lua:28.
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except Exception:  # noqa: BLE001
                # No-JSON = corrupt datastore -> nil, so the game treats it as
                # "no data". A string is worse than nil: the game does
                # `if not data then data = {} end` and a string is truthy.
                return None
        return None

    def write(self, data, filename="data", *a):
        f = self.fs.open(filename, kFileWrite)
        if not isinstance(f, File):
            return False
        try:
            if isinstance(data, str):
                f.write(data)
            else:
                f.write(json.dumps(self._to_python(data)))
            return True
        finally:
            f.close()

    def delete(self, filename="data"):
        return self.fs.delete(filename)
