"""apidbg.py — depurador de APIs faltantes.

Problema: cuando al juego le falta una API, Lua muere con un mensaje opaco
("attempt to index a nil value") y no sabes QUE funcion implementar.

Solucion: instalar un PROXY sobre el arbol `playdate` que
  * registra cada acceso a un campo/metodo INEXISTENTE (con su ruta completa),
  * devuelve un stub encadenable para que el juego SIGA corriendo,
  * aisla los errores con pcall para descubrir MUCHAS APIs faltantes en una sola
    corrida en vez de una por vez.

Uso desde Python:
    from pd.apidbg import install, report, safe_caller
    install(rt)                       # tras rt.load(...)  (o antes de los frames)
    ...  # correr frames
    missing, errors = report(rt)      # dicts {ruta: conteo}
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

-- playdate y sus sub-tablas (2 niveles)
guard(playdate, "playdate", 2)

-- ejecuta un callback aislando errores (para no morir en el primero)
function __pd_safe(f)
  if not f then return end
  local ok, err = pcall(f)
  if not ok then
    local e = tostring(err)
    __pd_errors[e] = (__pd_errors[e] or 0) + 1
  end
end

-- igual para import()
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
    """Activa el rastreo de APIs faltantes sobre el runtime ya cargado."""
    rt.lua.execute(TRACKER_LUA)
    rt.tracked = True
    # si el juego registro setUpdateCallback/setDrawCallback, envolverlos tambien
    pd = rt.playdate
    if pd._update_cb is not None and _needs_wrap(pd._update_cb):
        pass  # se envuelven via __pd_safe al invocar (ver safe_caller)


def _needs_wrap(_fn):
    return False  # los callbacks se aislaron ya con safe_caller()


def safe_caller(rt):
    """Devuelve una funcion Python que invoca un callback Lua aislando errores."""
    return rt.lua.eval("__pd_safe")


def _to_pydict(tbl):
    out = {}
    if tbl is None:
        return out
    for k in tbl.keys():
        out[str(k)] = tbl[k]
    return out


def report(rt):
    """Devuelve (missing: {ruta: conteo}, errors: {mensaje: conteo})."""
    g = rt.lua.globals()
    return _to_pydict(g["__pd_missing"]), _to_pydict(g["__pd_errors"])


def rawget(rt, tbl, key):
    """Lee una clave de una tabla Lua SIN disparar el proxy (evita falsos positivos)."""
    return rt.lua.eval("rawget")(tbl, key)


def api_suggestions(missing):
    """Convierte rutas faltantes en sugerencias accionables agrupadas por modulo."""
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
