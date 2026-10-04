"""apidbg.py — debugger for missing APIs.

Problem: when the game is missing an API, Lua dies with an opaque message
("attempt to index a nil value") and you don't know WHICH function to implement.

Solution: install a PROXY over the `playdate` tree that
  * records every access to a MISSING field/method (with its full path),
  * returns a chainable stub so the game KEEPS running,
  * isolates errors with pcall to discover MANY missing APIs in a single
    run instead of one at a time.

Usage from Python:
    from pd.apidbg import install, report, safe_caller
    install(rt)                       # after rt.load(...)  (or before the frames)
    ...  # run frames
    missing, errors = report(rt)      # dicts {path: count}
"""

TRACKER_LUA = r"""
__pd_missing = {}
__pd_errors = {}

local function stub(path)
  local t = setmetatable({}, {
    __index = function(tbl, k)
      local p = path .. "." .. tostring(k)
      __pd_missing[p] = (__pd_missing[p] or 0) + 1
      local c = stub(p); rawset(tbl, k, c); return c
    end,
    __call = function(_, ...)
      local p = path .. "()"
      __pd_missing[p] = (__pd_missing[p] or 0) + 1
      return stub(p)
    end,
  })
  return t
end

local function guard(t, name, depth)
  if type(t) ~= "table" or getmetatable(t) then return end
  setmetatable(t, {
    __index = function(tbl, k)
      local p = name .. "." .. tostring(k)
      __pd_missing[p] = (__pd_missing[p] or 0) + 1
      local c = stub(p); rawset(tbl, k, c); return c
    end,
  })
  if depth and depth > 0 then
    for k, v in pairs(t) do
      guard(v, name .. "." .. tostring(k), depth - 1)
    end
  end
end

-- playdate and its sub-tables (2 levels)
guard(playdate, "playdate", 2)

-- runs a callback isolating errors (so we don't die on the first one)
function __pd_safe(f)
  if not f then return end
  local ok, err = pcall(f)
  if not ok then
    local e = tostring(err)
    __pd_errors[e] = (__pd_errors[e] or 0) + 1
  end
end

-- same for import()
if import then
  local real_import = import
  import = function(n, ...)
    local ok, err = pcall(real_import, n, ...)
    if not ok then
      local e = "import " .. tostring(n) .. " -> " .. tostring(err)
      __pd_errors[e] = (__pd_errors[e] or 0) + 1
    end
  end
end
"""


def install(rt):
    """Enables missing-API tracking on the already-loaded runtime."""
    rt.lua.execute(TRACKER_LUA)
    rt.tracked = True
    # if the game registered setUpdateCallback/setDrawCallback, wrap them too
    pd = rt.playdate
    if pd._update_cb is not None and _needs_wrap(pd._update_cb):
        pass  # they are wrapped via __pd_safe when invoked (see safe_caller)


def _needs_wrap(_fn):
    return False  # the callbacks are already isolated with safe_caller()


def safe_caller(rt):
    """Returns a Python function that invokes a Lua callback isolating errors."""
    return rt.lua.eval("__pd_safe")


def _to_pydict(tbl):
    out = {}
    if tbl is None:
        return out
    for k in tbl.keys():
        out[str(k)] = tbl[k]
    return out


def report(rt):
    """Returns (missing: {path: count}, errors: {message: count})."""
    g = rt.lua.globals()
    return _to_pydict(g["__pd_missing"]), _to_pydict(g["__pd_errors"])


def rawget(rt, tbl, key):
    """Reads a key from a Lua table WITHOUT triggering the proxy (avoids false positives)."""
    return rt.lua.eval("rawget")(tbl, key)


def api_suggestions(missing):
    """Turns missing paths into actionable suggestions grouped by module."""
    groups = {}
    for path, count in missing.items():
        parts = path.split(".")
        if parts[-1].endswith("()"):
            parts[-1] = parts[-1][:-2]
        key = ".".join(parts[:3]) if len(parts) > 3 else ".".join(parts[:2])
        groups.setdefault(key, []).append((path, count))
    for g in groups.values():
        g.sort(key=lambda x: -x[1])
    return groups
