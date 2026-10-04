-- pd/lib/playdate_ext.lua — extensiones que el runtime de Playdate agrega a las
-- librerias ESTANDAR de Lua (no estan en Lua 5.4 vanilla, no vienen en CoreLibs).
-- Se ejecuta al arrancar el emulador.

-- El runtime de la consola invoca playdate.update() DENTRO de una corrutina
-- (lo dice la doc oficial: "playdate.update() is invoked as a coroutine").
-- Asi el juego puede usar coroutine.yield() para trocear trabajo largo y
-- continuar en el frame siguiente: la corrutina es PERSISTENTE y envuelve un
-- bucle que llama al callback y cede al sistema al final de cada frame.
--
-- OJO: la corrutina se guarda y se reanuda ENTERAMENTE en Lua. Si se devolviera
-- el thread a Python y se pasara de vuelta a coroutine.resume, lupa lo entrega
-- como otra cosa y Lua protesta con "thread expected, got function".
local __pd_updater = nil
function __pd_run_update(fn)
  if __pd_updater == nil or coroutine.status(__pd_updater) == "dead" then
    -- Si el callback murio por un error, la corrutina queda muerta: hay que
    -- rehacerla, o el siguiente resume falla con "cannot resume dead coroutine".
    __pd_updater = coroutine.create(function()
      while true do
        -- xpcall + debug.traceback dentro de la corrutina: asi un error del
        -- juego sube con la cadena de llamadas completa, que es lo que hace
        -- util el diagnostico (es lo que se lee en tools/menu_probe.py).
        local ok, err = xpcall(fn, debug.traceback)
        if not ok then error(err, 0) end
        coroutine.yield()
      end
    end)
  end
  local ok, err = coroutine.resume(__pd_updater)
  if not ok then error(err, 0) end
end

-- Despacho de input al handler de arriba de la pila, ENTERAMENTE en Lua.
--
-- Por que no se hace desde Python: el handler de un juego es un objeto CoreLibs
-- con metatable __index, y leerle un campo desde Python (`h["AButtonDown"]`)
-- dispara codigo Lua en mitad de una llamada de lupa -> reentrada -> SIGSEGV.
-- Regla general: desde Python solo se ESCRIBEN campos de tablas Lua, nunca se
-- leen campos que puedan pasar por un metametodo.
local __pd_btn_prefix = {
  { "a", "AButton" }, { "b", "BButton" },
  { "up", "upButton" }, { "down", "downButton" },
  { "left", "leftButton" }, { "right", "rightButton" },
}
function __pd_dispatch_input(h, pressed, justpressed, justreleased)
  local P = _G.playdate
  for i = 1, #__pd_btn_prefix do
    local btn, pre = __pd_btn_prefix[i][1], __pd_btn_prefix[i][2]
    -- Si hay handler en la pila, su metodo manda; si no lo define, se cae al
    -- global `playdate.AButtonDown` etc. Es lo que hace CoreLibs: muchos juegos
    -- definen los globales directamente y no usan inputHandlers.
    -- `type(f) == "function"` filtra los no-ops de Python (que son userdata).
    local fn = (h and h[pre .. "Down"]) or P[pre .. "Down"]
    if type(fn) == "function" and justpressed(btn) then fn() end
    fn = (h and h[pre .. "Up"]) or P[pre .. "Up"]
    if type(fn) == "function" and justreleased(btn) then fn() end
    fn = (h and h[pre .. "Held"]) or P[pre .. "Held"]
    if type(fn) == "function" and pressed(btn) then fn() end
  end
end

-- playdate.cranked(delta, accelerated): el juego lo define como global.
function __pd_call_crank(delta, accelerated)
  local f = _G.playdate.cranked
  if type(f) == "function" then f(delta, accelerated) end
end

-- Callbacks de un sprite, invocados desde Lua a proposito: leer el handle desde
-- Python dispararia metatables y puede segfaultear lupa (ver la skill).
-- `type(f) == "function"` filtra los no-ops de Python (que son userdata).
function __pd_sprite_update_cb(h)
  local f = h.update
  if type(f) == "function" then f(h) end
end

-- Un sprite SIN imagen solo se pinta por su callback `draw` (asi dibujan el
-- cursor, los menus y los efectos los juegos). Si no hay callback, nada.
function __pd_sprite_draw_cb(h, x, y, w, hh)
  -- La API llama al callback draw del sprite como f(sprite, x, y, w, h): el rect
  -- sucio que hay que repintar. CoreLibs lo envuelve para el fondo:
  --   bgsprite.draw = function(s, x, y, w, h) drawCallback(x, y, w, h) end
  -- y ese callback hace setClipRect(x,y,w,h) + dibujar el fondo. Si solo se pasa
  -- el sprite, x/y/w/h llegan nil y el recorte se pierde (el fondo del menu no
  -- se repinta). Sin argumentos -> nil, que es lo que la API documenta.
  local f = h.draw
  if type(f) == "function" then f(h, x, y, w, hh) end
end

-- extensiones de table
function table.indexOfElement(t, x)
  for i = 1, #t do
    if t[i] == x then return i end
  end
  return nil
end

function table.shallowcopy(t)
  local out = {}
  for k, v in pairs(t) do out[k] = v end
  return out
end

function table.deepcopy(t)
  local seen = {}
  local function cp(x)
    if type(x) ~= "table" then return x end
    if seen[x] then return seen[x] end
    local o = {}
    seen[x] = o
    for k, v in pairs(x) do o[cp(k)] = cp(v) end
    return o
  end
  return cp(t)
end

-- extensiones de string
function string.trimWhitespace(s)
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end
function string.trimLeadingWhitespace(s)
  return (s:gsub("^%s+", ""))
end
function string.trimTrailingWhitespace(s)
  return (s:gsub("%s+$", ""))
end
function string.UUID(length)
  local hex = "0123456789abcdef"
  local out = {}
  for i = 1, (length or 36) do
    local r = math.random(1, 16)
    out[i] = hex:sub(r, r)
  end
  return table.concat(out)
end
function string.split(s, sep)
  local out = {}
  for part in string.gmatch(s, "([^" .. (sep or "%s") .. "]+)") do
    out[#out + 1] = part
  end
  return out
end

-- math.lerp (Playdate lo agrega)
if not math.lerp then
  function math.lerp(a, b, t) return a + (b - a) * t end
end


-- Handle de audio que responde a CUALQUIER metodo con un no-op. Un juego que
-- usa el secuenciador (sequence/track/synth) no puede recibir nil del
-- constructor: indexaria nil y moriria antes de dibujar nada.
function __pd_audio_stub(h)
  return setmetatable(h, {
    __index = function(t, k)
      return function(...) return nil end
    end,
  })
end
-- Resuelve el tipo de colision del sprite `h` ante `other` (moveWithCollisions).
-- `h.collisionResponse` puede ser:
--   * un NUMERO   -> ese kCollisionType (SCM: player.collisionResponse = Bounce)
--   * una FUNCION -> se llama f(h, other) y devuelve el tipo (FlippyFish)
--   * nil         -> nil, el python usa el default (Slide)
function __pd_sprite_response_type(h, other)
  local r = h.collisionResponse
  local t = type(r)
  if t == "number" then return r end
  if t == "function" then
    local ok, v = pcall(r, h, other)
    if ok and type(v) == "number" then return v end
    return nil
  end
  return nil
end
