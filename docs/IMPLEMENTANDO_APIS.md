# Guía: implementar el resto de la API de Playdate

Documento de trabajo para que tú completes el emulador. El ejemplo mínimo ya
funciona (`games/hello` corre); aquí está **cómo agregar todo lo demás**.

Las firmas que aparecen abajo están verificadas contra **Inside Playdate 3.1.2**
(las saqué del índice oficial, no de memoria). Si algo cambia entre versiones del
SDK, la autoridad es `Inside Playdate.html` del SDK que tengas.

---

## 1. Las 3 reglas de oro

### Regla 1 — Patrón de 2 pasos
Para agregar cualquier API:

1. **Implementa** la lógica en Python (en `pd/graphics.py`, `pd/input.py`, o la clase que toque).
2. **Registra** el nombre en `Runtime._build_api()` (`pd/runtime.py`), añadiéndolo al dict correspondiente:
   ```python
   gfx = T({ ..., "drawLine": g.drawLine })
   ```

Nada más. La tabla Lua se reconstruye al cargar el juego.

### Regla 2 — NUNCA exponer objetos Python anidados a Lua
Es el bug que ya nos mordió: **lupa 2.8 + Lua 5.5 + Python 3.14 corrompe el acceso
a atributos de objetos Python anidados desde Lua tras ~77 lecturas**.

Consecuencia de diseño, y esto es lo más importante de toda la guía:
**los "objetos" de Playdate (image, sprite, tilemap, font, file…) NO son objetos
Python expuestos**. Son **tablas Lua** con un campo `__id`; el estado real vive en
Python, en un *registry* (un `dict` id → objeto). Lua nunca toca el objeto Python.

```python
# en Runtime
self._registry = {}          # id -> objeto Python (Surface, Sprite, archivo…)
self._next_id = 1

def _handle(self, obj):
    """Crea una tabla Lua que representa un objeto Python (patrón handle)."""
    hid = self._next_id
    self._next_id += 1
    self._registry[hid] = obj
    return self.lua.table_from({"__id": hid})

def _deref(self, tbl):
    """Recupera el objeto Python a partir del handle Lua."""
    return self._registry[tbl["__id"]]
```

Uso: `gfx.image.new(path)` devuelve `self._handle(surface)`. Y `sprite:setImage(img)`
hace `self._deref(img)` para obtener el `Surface`.

### Regla 3 — Los valores de retorno
- `unpack_returned_tuples=True`: un `tuple` de Python sale como **varios valores de
  retorno** en Lua (`return x, y`). Si quieres UN valor, devuelve lista o envuelve.
- Una `list`/`dict` de Python **NO** se convierte en tabla Lua: Lua la ve como
  *userdata*. Para devolver una tabla real usa el conversor recursivo:

```python
def _to_lua(self, obj):
    """Convierte recursivamente dict/list de Python en tablas Lua."""
    if isinstance(obj, dict):
        return self.lua.table_from({k: self._to_lua(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return self.lua.table_from([self._to_lua(v) for v in obj])
    return obj
```
(Necesario porque `table_from` **no** recursiona solo en los niveles anidados.)

---

## 2. La herramienta que te dice qué falta

No adivines. Escanea tu juego objetivo:

```sh
python tools/api_coverage.py /ruta/al/juego
```

Reporta qué funciones `playdate.*` / `gfx.*` usa el juego que el emulador todavía no
implementa, agrupadas por módulo.

**Limitación**: no sigue alias de variables locales. `local s = gfx.sprite.new(); s:moveTo()`
no detecta `moveTo` (sí detecta `sprite.new`). Para esos, gripea el juego a mano:

```sh
grep -rhoE ':[a-zA-Z]+' /ruta/al/juego/*.lua | sort -u
```

Y para validar que una API nueva no rompe nada:

```sh
python tools/headless_test.py /ruta/al/juego -n 300 --keys "10:A:5,40:Left:25"
```

---

## 3. Orden de ataque recomendado

No implementes todo: implementa por *tiers* y valida con el scanner.

| Tier | Módulos | Qué desbloquea |
|------|---------|----------------|
| **1 — MVP jugable** | core input/tiempo · `display` · `graphics` (primitivas + estado) · `image` · `json` · `timer` · `datastore` | La mayoría de juegos simples |
| **2 — juego completo** | `sprite` · `tilemap` · `font` · `geometry` · `easingFunctions` · `ui` (menú/splash) | Juegos "de verdad" |
| **3 — pulido** | `sound` · `menu` · `keyboard` · `accelerometer` · `animator`/`animation` · `pathfinder` · `perlin` | Casos específicos |

**Regla práctica**: lógica pura → impleméntala **en Lua** (un `lib/*.lua` que cargas
al arrancar). Render/hardware/entrada → en **Python**. `geometry`, `easingFunctions`,
`json`, `timer`, `pathfinder`, `animator` son candidatos naturales a Lua puro y así
evitas el puente por completo.

---

## 4. Módulo por módulo

### 4.1 core — `playdate.*` (entrada, tiempo, estado)

Firmas relevantes:
```
playdate.getTime()                       -- ms desde epoch (float)
playdate.getCurrentTimeMilliseconds()
playdate.getElapsedTime()  /  playdate.resetElapsedTime()
playdate.getFPS()  /  playdate.drawFPS([x, y])
playdate.buttonIsPressed(button)
playdate.buttonJustPressed(button)
playdate.buttonJustReleased(button)
playdate.getButtonState()                -- máscara de bits
playdate.getCrankPosition()              -- grados
playdate.getCrankChange()                -- delta desde el frame anterior
playdate.isCrankDocked()
playdate.getCrankTicks(ticksPerRevolution)
playdate.getSystemLanguage()
playdate.getLocalizedText(key, [language])
playdate.setAutoLockDisabled(disable)
playdate.isSimulator()
```
Constantes de botón: `playdate.kButtonA/kButtonB/kButtonUp/kButtonDown/kButtonLeft/kButtonRight`.

**Implementación** (delegan en tus objetos Button/Crank):
```python
def buttonIsPressed(self, b):   return self.button.isPressed(_BTN[b])
def buttonJustPressed(self, b): return self.button.wasPressed(_BTN[b])
def getFPS(self):               return self._emu.fps
def getTime(self):              return time.time() * 1000.0
def isSimulator(self):          return True
def getBatteryPercentage(self): return 100  # stub; en la Pi puedes leer /sys
```
`_BTN` traduce las constantes `kButton*` (que defines como enteros) a los nombres
`"A"/"B"/"Up"/…` de `pd/input.py`.

`getCrankPosition()` / `getCrankChange()` → añade a `pd/input.py` una clase `Crank`
con `rotation` y `delta`, alimentada cada frame desde `emulator.py` (ver §4.2).

Registro (en la tabla raíz): `"getTime": pd.getTime, "getFPS": pd.getFPS, ...`
Constantes: `api["kButtonA"] = 1`, etc. (o `T({"kButtonA": 1, ...})`).

---

### 4.2 `playdate.display`

Firmas:
```
playdate.display.getWidth()  /  getHeight()
playdate.display.getRect()            -- (x, y, w, h)
playdate.display.setRefreshRate(rate) / getRefreshRate()
playdate.display.setScale(scale)      / getScale()
playdate.display.setOffset(x, y)      / getOffset()
playdate.display.setInverted(flag)    / getInverted()
playdate.display.setFlipped(x, y)     / getFlipped()
playdate.display.setMosaic(x, [y])    / getMosaic()   -- efecto dev
playdate.display.flush()
```
⚠️ **La API usa `getWidth()`/`getHeight()`, NO `getScreenWidth()`** (ya lo corregí en
`runtime.py`, que dejó `getScreenWidth` como alias por comodidad).

`setInverted(true)` → dibuja en negativo: en `Screen.render`, si `inverted`, usa
`pygame.transform` o invierte el canvas con `canvas.fill` + `BLEND_SUB`.

---

### 4.3 `graphics` — primitivas

Firmas exactas (todas sobre el canvas del `Graphics`):
```
gfx.clear([color])
gfx.drawPixel(x, y)
gfx.drawRect(r)                | gfx.drawRect(x, y, w, h)
gfx.fillRect(r)                | gfx.fillRect(x, y, width, height)
gfx.drawLine(ls)               | gfx.drawLine(x1, y1, x2, y2)
gfx.drawRoundRect(x, y, w, h, radius) / fillRoundRect(...)
gfx.drawTriangle(x1,y1,x2,y2,x3,y3) / fillTriangle(...)
gfx.drawPolygon(p) / fillPolygon(p)          -- polígono: tabla de puntos (ver geometry)
gfx.drawCircleAtPoint(x, y, radius) / fillCircleAtPoint(...)
gfx.drawCircleInRect(x, y, width, height) / fillCircleInRect(...)
gfx.drawEllipseInRect(x, y, width, height, [startAngle, endAngle]) / fillEllipseInRect(...)
gfx.drawArc(x, y, radius, startAngle, endAngle)
gfx.drawSineWave(startX, startY, endX, endY, startAmp, endAmp, period, [phaseShift])
gfx.setPixel(x,y,color) / getPixel(x,y)
gfx.setRect(x, y, w, h, color) / getRect(x, y, w, h)
```
Notas de semántica:
- `*InRect` **NO** usa semántica de centro: `x,y,width,height` es el rectángulo
  contenedor. `drawCircleInRect(x,y,w,h)` dibuja el círculo inscrito.
- **Colores**: `0` = blanco (fondo), `1` = negro (tinta), más
  `kColorClear`, `kColorXOR`. Exponlos y define `setColor`/`getColor` (§4.4).
- **Ángulos** (`drawArc`, `drawEllipseInRect` con ángulos): grados, **0° = arriba**,
  sentido **horario**. pygame mide radianes desde +x, antihorario → convierte.

**Implementación** (con `pygame.draw`):
```python
def drawLine(self, x1, y1, x2, y2):
    pygame.draw.line(self._canvas, self._ink(), (x1+self._ox, y1+self._oy),
                     (x2+self._ox, y2+self._oy), self._line_width)

def fillCircleInRect(self, x, y, w, h):
    pygame.draw.ellipse(self._canvas, self._ink(),
                        pygame.Rect(x+self._ox, y+self._oy, w, h))

def drawEllipseInRect(self, x, y, w, h, *a):
    pygame.draw.ellipse(self._canvas, self._ink(),
                        pygame.Rect(x+self._ox, y+self._oy, w, h), 1)
```
(`self._ink()` devuelve el color actual; `self._ox/_oy` es el draw-offset de §4.4.)

Registro: añade cada nombre al dict `gfx` de `_build_api()`.

---

### 4.4 `graphics` — estado de dibujo (lo que hay que diseñar bien)

Firmas:
```
gfx.setColor(color) / getColor()
gfx.setBackgroundColor(color) / getBackgroundColor()
gfx.setFont(font, [variant]) / getFont([variant]) / getSystemFont([variant])
gfx.setFontTracking(pixels) / getFontTracking()
gfx.setLineWidth(width) / getLineWidth()
gfx.setLineCapStyle(style) / setStrokeLocation(location)
gfx.setClipRect(x, y, width, height) | (rect)   / clearClipRect() / getClipRect()
gfx.setScreenClipRect(x, y, width, height) | (rect) / getScreenClipRect()
gfx.setDrawOffset(x, y) / getDrawOffset()
gfx.pushContext([image]) / popContext()
gfx.lockFocus([image]) / unlockFocus() / getWorkingImage()
gfx.setImageDrawMode(mode) / getImageDrawMode()
gfx.setDitherPattern(alpha, [ditherType])
gfx.setStencilImage(image, [tile]) / setStencilPattern(...) / clearStencil()
gfx.setPattern(pattern, [x, y])
```
Diseño recomendado — mantén **estado en el objeto `Graphics`** y rutea TODO por él:

```python
self._color   = 1          # tinta actual
self._line_width = 1
self._ox = self._oy = 0    # draw offset (sumar a cada coordenada)
self._ctx_stack = []       # pila para pushContext/popContext
self._target = self._canvas  # lockFocus cambia el destino
```
- `setClipRect` → `self._target.set_clip(pygame.Rect(...))`, `clearClipRect` → `set_clip(None)`.
- `setDrawOffset(x,y)` → guarda offset; **cada primitiva suma `_ox/_oy`**.
- `pushContext()` → `self._ctx_stack.append((color, offset, clip, font, target))`;
  `popContext()` → restaura. Es como se hacen las "cámaras" y los HUD.
- `lockFocus(img)` → `self._target = deref(img)`; `unlockFocus()` → vuelve al canvas.
  (Así se dibuja off-screen: crear una image y lockFocus sobre ella.)

---

### 4.5 `graphics.image` + `graphics.imagetable`

Firmas:
```
gfx.image.new(path) | gfx.image.new(width, height, [data])
gfx.imageSizeAtPath(path)
img:draw(x, y, [flip, [sourceRect]]) | img:draw(p, [flip, [sourceRect]])
img:drawCentered(x, y, [flip]) | img:drawAnchored(x,y,ax,ay,[flip])
img:drawRotated(x, y, angle, [scale, [yscale]]) | img:drawScaled(x, y, scale, [yscale])
img:drawTiled(x, y, width, height, [flip]) | img:drawFaded(x, y, alpha, ditherType, [flip])
img:getSize() | img:copy() | img:sample(x, y) | img:setInverted(flag)
img:setMaskImage(mask) / addMask([opaque]) / getMaskImage()

gfx.imagetable.new(path)
itbl:getImage(n) | itbl:getImage(x, y) | itbl:drawImage(n, x, y, [flip])
itbl:getLength() | itbl:getSize() | itbl:setImage(n, image)
```
**Patrón handle** (regla 2): `image.new` carga un `pygame.Surface` y devuelve
`self._handle(surface)`. `draw` hace `self._deref(img)`. Ojo: en Playdate las
imágenes son **1 bit + máscara**; un PNG con alpha se maneja con `setMaskImage`.
Para empezar, carga el PNG y usa su canal alpha como máscara.

```python
def image_new(self, path):
    surf = pygame.image.load(path).convert_alpha()
    return self._handle(surf)

def image_draw(self, img_tbl, x, y, *args):
    surf = self._deref(img_tbl)
    self._target.blit(surf, (x + self._ox, y + self._oy))
```
Registro: `gfx["image"] = T({"new": self.image_new, "draw": self.image_draw, ...})`.
Colon vs punto: `img:draw(...)` en Lua pasa `img` como 1er argumento → tu función
Python debe recibirlo primero (`img_tbl`). Por eso `image_draw(self, img_tbl, x, y)`.

---

### 4.6 `graphics.sprite` (la clase más grande: 43 métodos)

Firmas principales:
```
gfx.sprite.new([image_or_tilemap])       -- ¡es ".", no ":"
gfx.sprite.update()                      -- actualiza y redibuja toda la lista
gfx.sprite.setBackgroundDrawingCallback(cb)
gfx.sprite.addEmptyCollisionSprite(x, y, w, h)
gfx.sprite.addWallSprites(tilemap, emptyIDs, [xOffset, yOffset])
spr:add() / spr:remove()
spr:moveTo(x, y) / moveBy(x, y)
spr:setImage(image, [flip, [scale, [yscale]]]) / getImage()
spr:setCenter(x, y) / getCenter() / getCenterPoint()
spr:setBounds(x,y,w,h)|(rect) / getBounds() / getBoundsRect()
spr:setSize(width, height) / getSize()
spr:setZIndex(z) / getZIndex() / setVisible(flag) / isVisible()
spr:setUpdatesEnabled(flag) / updatesEnabled()
spr:setRotation(angle, [sx, [sy]]) / setScale(s) / setImageFlip(flip)
spr:setCollideRect(rect)|(x,y,w,h) / getCollideRect() / clearCollideRect()
spr:moveWithCollisions(goalX, goalY) / checkCollisions(x, y) / overlappingSprites()
spr:setTag(tag) / getTag() / setOpaque(flag) / markDirty([rect])
spr:setTilemap(tilemap) / setStencilImage(...) / setClipRect(...)
```
**Modelo**: una lista global de sprites; `sprite.update()` recorre, llama `:update()`
(si el sprite lo define) y **dibuja ordenando por z-index** tras el background.

```python
# pd/sprite.py
class Sprite:
    def __init__(self, image=None):
        self.x = self.y = 0.0; self.z = 0; self.visible = True
        self.image_id = image; self.rotation = 0; self.scale = 1
        self.collide = None; self.tag = None; self.update_fn = None
```
`moveWithCollisions` es lo complicado: empieza con un AABB simple (mueve en X,
revierte si choca; luego en Y) contra los rects de colisión de `getCollisionRects`
del tilemap + otros sprites. Es el "sliding" clásico.

El fondo: el runtime debe dibujar `setBackgroundDrawingCallback` antes de los sprites.

---

### 4.7 `graphics.tilemap`

```
gfx.tilemap.new() | tilemap:setImageTable(imagetable) | tilemap:setSize(w, h)
tilemap:setTiles(data, width) | getTileAtPosition(x, y) | setTileAtPosition(x, y, i)
tilemap:draw(x, y, [sourceRect]) | drawIgnoringOffset(x, y, [sourceRect])
tilemap:getCollisionRects(emptyIDs) | getTileSize() / getPixelSize() / getSize()
```
`data` = array 1D de índices de tile. `draw` recorre y blitea cada tile desde la
imagetable. `getCollisionRects(emptyIDs)` devuelve los rects de tiles cuyo índice
**no** está en `emptyIDs` — alimenta a `moveWithCollisions`.

---

### 4.8 `graphics.font`

```
gfx.font.new(path) | font:drawText(text, x, y, [w, h], ...)
font:getTextWidth(text) | font:getHeight() | font:getLeading() / setLeading(px)
font:getTracking() / setTracking(px) | font:getGlyph(ch)
gfx.getSystemFont([variant]) | gfx.getTextSize(str, [fontFamily, [leadingAdjustment]])
```
Playdate usa un formato de fuente propio; lo pragmático es mapear a `pygame.font.Font`
con un TTF. Para fidelidad pixel-perfect, extrae la fuente del SDK
(`.../Resources/fonts/`) y métela en `assets/fonts/`.

---

### 4.9 `geometry` (candidato a Lua puro)

```
playdate.geometry.rect.new(x, y, w, h)  -- .x .y .width .height
  :offsetBy(dx,dy) :insetBy(dx,dy) :containsPoint(p|x,y) :intersection(r2)
  :intersects(r2) :union(r2) :centerPoint() :toPolygon() :unpack()
playdate.geometry.vector2D.new(x, y) | vector2D.newPolar(angle, r)
  :magnitude() :normalize() :scaledBy(s) :dotProduct(v) :angleBetween(v) :unpack()
playdate.geometry.point.new(x, y) :offsetBy(dx,dy) :distanceToPoint(p) :unpack()
playdate.geometry.lineSegment.new(x1,y1,x2,y2) :length() :midPoint() :unpack()
playdate.geometry.polygon.new(x1,y1, x2,y2, ...) :count() :containsPoint(...)
playdate.geometry.arc / size / affineTransform
playdate.geometry.distanceToPoint(x1,y1,x2,y2) / squaredDistanceToPoint(...)
```
Es matemática pura. **Recomendación: impleméntalo en Lua** (`lib/geometry.lua`) y
cárgalo al arrancar; así cada objeto es una tabla Lua con campos y métodos y no
cruzas el puente Python ni una vez. Los métodos se escriben como
`function Rect:offsetBy(dx, dy) self.x = self.x + dx ... end`, y `:unpack()`
devuelve múltiples valores con `return self.x, self.y, self.width, self.height`.

---

### 4.10 `timer` / `frameTimer` / `easingFunctions` (Lua puro)

```
playdate.timer.new(duration, callback, [args])                -- callback al terminar
playdate.timer.new(duration, [startValue, endValue, [easing]])
playdate.timer.performAfterDelay(delay, callback, [args])
playdate.timer.keyRepeatTimer([delay], [repeatDelay], callback, [args])
playdate.timer.updateTimers()      -- el runtime lo llama solo, cada frame
timer:start() :pause() :reset() :remove()
timer.value / duration / timeLeft / paused / repeats / discardOnCompletion / easingFunction
easingFunctions.linear / inQuad / outQuad / inOutQuad / … (todas las familias)
math.lerp(min, max, t)
```
Impleméntalos en `lib/timer.lua`: una lista de timers, y el emulador llama
`playdate.timer.updateTimers()` (y frameTimer) **automáticamente** tras `update()`
del juego, para que el juego no tenga que hacerlo.

---

### 4.11 `json`

`playdate.json.encode(tbl) -> string` · `playdate.json.decode(str) -> table`.

Usa el `json` de Python + tu `_to_lua()` recursivo (regla 3) para el decode.

---

### 4.12 `datastore` / `file`

```
playdate.datastore.read([filename]) / write(table|string, [filename], [pretty])
playdate.datastore.delete([filename]) / readImage(path) / writeImage(image, path)
playdate.file.open(path, [mode]) -> handle: :read([n]) :readline() :write(s) :close()
                                                  :seek(offset, [whence]) :tell() :flush()
playdate.file.listFiles(path, [showhidden]) / exists(p) / delete(p) / mkdir(p)
playdate.file.getSize(p) / getType(p) / modtime(p) / rename(p, np) / isdir(p)
playdate.file.load(path) / run(path) / kFileRead / kFileWrite / kFileAppend
```
Sandbox por juego: usa `<game_dir>/.pd_data/`. `file.open` devuelve un **handle**
(tabla `__id` → objeto `file` de Python). `datastore` es un `file.open` fijo
(`data.json`).

---

### 4.13 `sound` (mínimo viable)

Es enorme (fileplayer, sampleplayer, sample, synth, effect, channel, sequence,
track, instrument, lfo, envelope, signal, filtros, micinput…). **No lo implementes
completo**: haz un shim.

Mínimo útil:
```
playdate.sound.fileplayer.new([path]) :play([n]) :stop() :pause() :setVolume(l,r)
                                       :setRate(r) :isPlaying() :setOffset(s) :getLength()
playdate.sound.sampleplayer.new([sample]) :play([n],[r]) :stop() :setVolume(l,r)
playdate.sound.sample.new(path) :play([n],[r]) :getLength()
playdate.sound.synth.new([waveform]) :playNote(freq,[vol],[len]) :noteOff() :setADSR(...)
playdate.sound.setOutputsActive(headphones, speaker) / playdate.sound.setVolume(...)
```
Sobre `pygame.mixer`. En la Pi, si no hay audio, inicializa el mixer en modo dummy
para que las llamadas no revienten. Nota: los `.pda`/ADPCM de Playdate necesitan
decoder; de entrada soporta WAV/OGG (lo que `pygame.mixer` ya entiende).

---

### 4.14 `ui`

Lo más común:
```
playdate.ui.gridview  (11 métodos): :new() :setNumberOfSections/… :drawInRect(...)
                                     :selectNextRow(...) :getSelection() …
playdate.ui.crankIndicator :new() :draw([xOffset,yOffset]) :getBounds()
playdate.getSystemMenu() -> menu/playdate.menu:
   menu:addMenuItem(title, cb) | addCheckmarkMenuItem(t,cb) | addOptionsMenuItem(t, opts, cb)
   menu:removeMenuItem(mi) | removeAllMenuItems() | getMenuItems()
   item:setTitle(t) | setValue(v) | setCallback(cb) | getTitle() | getValue()
```
`gridview` y `crankIndicator` son dibujables: implementa su `drawInRect` con tus
primitivas. El menú lo pinta **el emulador** (tecla `Esc` lo abre) para que los tests
y el juego tengan menú.

---

### 4.15 Resto (bajo prioridad)

- **keyboard**: `playdate.keyboard.show([text]) / hide() / .text / isVisible()` + callbacks
  (`keyboardDidShowCallback`, `textChangedCallback`, `keyboardDidHideCallback`). En el
  emulador → captura texto real del teclado en un buffer y dibújalo.
- **accelerometer**: `startAccelerometer() / readAccelerometer() -> x,y,z / stopAccelerometer()`.
  Stub devolviendo `(0,0,0)`, o mapea al joystick.
- **power**: `getBatteryPercentage() / getBatteryVoltage() / getPowerStatus()` → stub;
  en la Pi lee `/sys/class/power_supply/`.
- **animator / animation**: interpola props de sprite en el tiempo → Lua puro.
- **pathfinder**: grafo + A* → Lua puro.
- **graphics.perlin**: ruido Perlin → Python o Lua.
- **network (http/tcp)**: probablemente ni lo toques en la Pi.

---

## 5. Cheat-sheet pygame

| Playdate | pygame |
|---|---|
| `drawLine` | `pygame.draw.line` |
| `fillRect` / `drawRect` | `pygame.draw.rect` (fill / `width=1`) |
| `fillCircleInRect` / `drawEllipseInRect` | `pygame.draw.ellipse` (fill / `width=1`) |
| `fillCircleAtPoint` | `pygame.draw.circle` |
| `drawPolygon` / `fillPolygon` | `pygame.draw.polygon` |
| `drawArc` | `pygame.draw.arc` (⚠️ convierte ángulo: 0°=arriba, horario) |
| `setClipRect` / `clearClipRect` | `Surface.set_clip` / `set_clip(None)` |
| `pushContext` / `popContext` | tu propia pila de estado |
| `setDrawOffset` | offset propio sumado a cada coordenada |
| `image.new` / `img:draw` | `pygame.image.load` / `Surface.blit` |
| `drawRotated` / `drawScaled` | `pygame.transform.rotate` / `.scale` |
| `font.new` / `drawText` | `pygame.font.Font` / `font.render` |
| `sound.*` | `pygame.mixer` |
| `buttonIsPressed` | `pygame.key` / `pygame.joystick` |
| `display.setRefreshRate` | `clock.tick()` objetivo |

---

## 6. Gotchas de lupa (los que te van a morder)

1. **Nunca objetos Python anidados** (regla 2). Síntoma si lo olvidas: tras ~77
   lecturas una función empieza a devolver *otra cosa* (vimos `button.isPressed`
   devolviendo `Graphics.drawText`) y el juego explota en un frame raro.
2. **`:` vs `.`**: `obj:metodo(a)` en Lua llama a `metodo(obj, a)`. Si tu función
   Python es un campo de la tabla, recibe el handle como **primer** parámetro.
   Defínelo así: `def metodo(self, handle_tbl, a):`. Los métodos de módulo
   (`gfx.fillRect`) se llaman con `.` y no reciben handle.
3. **Tuplas → múltiples retornos** (`unpack_returned_tuples=True`).
4. **list/dict → userdata**, no tabla. Usa `_to_lua()`.
5. **`table_from` no recursiona**: los dicts anidados quedan como objetos Python.
6. **`None` → `nil`**; bools van bien.
7. **Callbacks**: guardas un `lupa.Function` y TÚ decides cuándo invocarlo
   (`update`, `draw`, timers, `setCrankCallback`). El juego no los llama; los llama el runtime.

---

## 7. Definición de "terminado" para cada API

Para cada función que agregues:

1. `python tools/api_coverage.py <juego>` no la lista como faltante.
2. `python tools/headless_test.py <juego> -n 300` pasa sin errores.
3. Si dibuja: `--save /tmp/f.png` y **mira el PNG** (un frame real, no asumas).
4. Si tiene estado (clip, offset, color): un test que dibuje *y* vuelva a dibujar
   tras `popContext`, para pillar fugas de estado.

---

## 8. Notas para la Pi Zero 2 W

- Todo esto corre igual; instala `pygame-ce` + `lupa` con pip.
- Cuello de botella real: el render escalado y el intérprete Lua. 320×320 a 30 FPS
  sobra. Si aprieta: `--scale 1`, o `pygame.display.set_mode(..., pygame.SCALED)`.
- Fidelidad Lua: Playdate usa **LuaJIT** (5.1 + ops bit a bit). Aquí es **Lua 5.5**.
  La mayoría de juegos van; para máxima fidelidad compila LuaJIT y lupa contra él.
