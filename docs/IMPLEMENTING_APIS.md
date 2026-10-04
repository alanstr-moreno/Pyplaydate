# Guide: implementing the rest of the Playdate API

Working document for completing the emulator. The minimal example already works
(`games/hello` runs); here is **how to add everything else**.

The signatures below are verified against **Inside Playdate 3.1.2** (taken from
the official index, not from memory). If something changes between SDK
versions, the authority is the `Inside Playdate.html` of the SDK you have.

---

## 1. The 3 golden rules

### Rule 1 — 2-step pattern
To add any API:

1. **Implement** the logic in Python (in `pd/graphics.py`, `pd/input.py`, or
   whichever class it belongs to).
2. **Register** the name in `Runtime._build_api()` (`pd/runtime.py`), adding it
   to the corresponding dict:
   ```python
   gfx = T({ ..., "drawLine": g.drawLine })
   ```

That's all. The Lua table is rebuilt when the game loads.

### Rule 2 — NEVER expose nested Python objects to Lua
This is the bug that already bit us: **lupa 2.8 + Lua 5.5 + Python 3.14 corrupts
attribute access on nested Python objects from Lua after ~77 reads**.

Design consequence, and the most important thing in this whole guide:
**the "objects" of Playdate (image, sprite, tilemap, font, file...) are NOT
exposed Python objects**. They are **Lua tables** with an `__id` field; the real
state lives in Python, in a *registry* (a dict `id → object`). Lua never touches
the Python object.

```python
# on Runtime
self._registry = {}          # id -> Python object (Surface, Sprite, file...)
self._next_id = 1

def _handle(self, obj):
    """Creates a Lua table representing a Python object (handle pattern)."""
    hid = self._next_id
    self._next_id += 1
    self._registry[hid] = obj
    return self.lua.table_from({"__id": hid})

def _deref(self, tbl):
    """Gets the Python object back from a Lua handle."""
    return self._registry[tbl["__id"]]
```

Usage: `gfx.image.new(path)` returns `self._handle(surface)`. And
`sprite:setImage(img)` does `self._deref(img)` to get the `Surface`.

### Rule 3 — Return values
- `unpack_returned_tuples=True`: a Python `tuple` comes out as **several return
  values** in Lua (`return x, y`). If you want ONE value, return a list or wrap
  it.
- A Python `list`/`dict` is **NOT** converted to a Lua table: Lua sees it as
  *userdata*. To return a real table use the recursive converter:

```python
def _to_lua(self, obj):
    """Recursively converts Python dict/list into Lua tables."""
    if isinstance(obj, dict):
        return self.lua.table_from({k: self._to_lua(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return self.lua.table_from([self._to_lua(v) for v in obj])
    return obj
```
(Needed because `table_from` does **not** recurse into nested levels by itself.)

---

## 2. The tool that tells you what's missing

Do not guess. Scan your target game:

```sh
python tools/api_coverage.py /path/to/game
```

It reports which `playdate.*` / `gfx.*` functions the game uses that the
emulator does not yet implement, grouped by module.

**Limitation**: it does not follow aliases of local variables.
`local s = gfx.sprite.new(); s:moveTo()` does not detect `moveTo` (it does
detect `sprite.new`). For those, grep the game by hand:

```sh
grep -rhoE ':[a-zA-Z]+' /path/to/game/*.lua | sort -u
```

And to validate that a new API breaks nothing:

```sh
python tools/headless_test.py /path/to/game -n 300 --keys "10:A:5,40:Left:25"
```

---

## 3. Recommended attack order

Don't implement everything: implement by *tiers* and validate with the scanner.

| Tier | Modules | What it unlocks |
|------|---------|-----------------|
| **1 — playable MVP** | core input/time · `display` · `graphics` (primitives + state) · `image` · `json` · `timer` · `datastore` | Most simple games |
| **2 — full game** | `sprite` · `tilemap` · `font` · `geometry` · `easingFunctions` · `ui` (menu/splash) | "Real" games |
| **3 — polish** | `sound` · `menu` · `keyboard` · `accelerometer` · `animator`/`animation` · `pathfinder` · `perlin` | Specific cases |

**Practical rule**: pure logic → implement it **in Lua** (a `lib/*.lua` you load
at startup). Rendering/hardware/input → **in Python**. `geometry`,
`easingFunctions`, `json`, `timer`, `pathfinder`, `animator` are natural Lua
candidates, and so you avoid the bridge entirely.

---

## 4. Module by module

### 4.1 core — `playdate.*` (input, time, state)

Relevant signatures:
```
playdate.getTime()                       -- ms since epoch (float)
playdate.getCurrentTimeMilliseconds()
playdate.getElapsedTime()  /  playdate.resetElapsedTime()
playdate.getFPS()  /  playdate.drawFPS([x, y])
playdate.buttonIsPressed(button)
playdate.buttonJustPressed(button)
playdate.buttonJustReleased(button)
playdate.getButtonState()                -- bit mask
playdate.getCrankPosition()              -- degrees
playdate.getCrankChange()                -- delta since the previous frame
playdate.isCrankDocked()
playdate.getCrankTicks(ticksPerRevolution)
playdate.getSystemLanguage()
playdate.getLocalizedText(key, [language])
playdate.setAutoLockDisabled(disable)
playdate.isSimulator()
```
Button constants: `playdate.kButtonA/kButtonB/kButtonUp/kButtonDown/kButtonLeft/kButtonRight`.

**Implementation** (delegate to your Button/Crank objects):
```python
def buttonIsPressed(self, b):   return self.button.isPressed(_BTN[b])
def buttonJustPressed(self, b): return self.button.wasPressed(_BTN[b])
def getFPS(self):               return self._emu.fps
def getTime(self):              return time.time() * 1000.0
def isSimulator(self):          return True
def getBatteryPercentage(self): return 100  # stub; on the Pi you can read /sys
```
`_BTN` maps the `kButton*` constants (which you define as integers) to the
`"A"/"B"/"Up"/...` names of `pd/input.py`.

`getCrankPosition()` / `getCrankChange()` → add a `Crank` class to `pd/input.py`
with `rotation` and `delta`, fed each frame from `emulator.py` (see §4.2).

Registration (in the root table): `"getTime": pd.getTime, "getFPS": pd.getFPS, ...`
Constants: `api["kButtonA"] = 1`, etc. (or `T({"kButtonA": 1, ...})`).

---

### 4.2 `playdate.display`

Signatures:
```
playdate.display.getWidth()  /  getHeight()
playdate.display.getRect()            -- (x, y, w, h)
playdate.display.setRefreshRate(rate) / getRefreshRate()
playdate.display.setScale(scale)      / getScale()
playdate.display.setOffset(x, y)      / getOffset()
playdate.display.setInverted(flag)    / getInverted()
playdate.display.setFlipped(x, y)     / getFlipped()
playdate.display.setMosaic(x, [y])    / getMosaic()   -- dev effect
playdate.display.flush()
```
⚠️ **The API uses `getWidth()`/`getHeight()`, NOT `getScreenWidth()`** (already
fixed in `runtime.py`, which keeps `getScreenWidth` as an alias for convenience).

`setInverted(true)` → draw in negative: in `Screen.render`, if `inverted`, use
`pygame.transform` or invert the canvas with `canvas.fill` + `BLEND_SUB`.

---

### 4.3 `graphics` — primitives

Exact signatures (all on the `Graphics` canvas):
```
gfx.clear([color])
gfx.drawPixel(x, y)
gfx.drawRect(r)                | gfx.drawRect(x, y, w, h)
gfx.fillRect(r)                | gfx.fillRect(x, y, width, height)
gfx.drawLine(ls)               | gfx.drawLine(x1, y1, x2, y2)
gfx.drawRoundRect(x, y, w, h, radius) / fillRoundRect(...)
gfx.drawTriangle(x1,y1,x2,y2,x3,y3) / fillTriangle(...)
gfx.drawPolygon(p) / fillPolygon(p)          -- polygon: point table (see geometry)
gfx.drawCircleAtPoint(x, y, radius) / fillCircleAtPoint(...)
gfx.drawCircleInRect(x, y, width, height) / fillCircleInRect(...)
gfx.drawEllipseInRect(x, y, width, height, [startAngle, endAngle]) / fillEllipseInRect(...)
gfx.drawArc(x, y, radius, startAngle, endAngle)
gfx.drawSineWave(startX, startY, endX, endY, startAmp, endAmp, period, [phaseShift])
gfx.setPixel(x,y,color) / getPixel(x,y)
gfx.setRect(x, y, w, h, color) / getRect(x, y, w, h)
```
Semantics notes:
- `*InRect` does **NOT** use center semantics: `x,y,width,height` is the
  bounding box. `drawCircleInRect(x,y,w,h)` draws the inscribed circle.
- **Colors**: `0` = white (background), `1` = black (ink), plus `kColorClear`,
  `kColorXOR`. Expose them and define `setColor`/`getColor` (§4.4).
- **Angles** (`drawArc`, `drawEllipseInRect` with angles): degrees, **0° = up**,
  **clockwise**. pygame measures radians from +x counter-clockwise → convert.

**Implementation** (with `pygame.draw`):
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
(`self._ink()` returns the current color; `self._ox/_oy` is the draw-offset of §4.4.)

Registration: add each name to the `gfx` dict of `_build_api()`.

---

### 4.4 `graphics` — drawing state (what needs good design)

Signatures:
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
Recommended design — keep **state on the `Graphics` object** and route EVERYTHING
through it:

```python
self._color   = 1          # current ink
self._line_width = 1
self._ox = self._oy = 0    # draw offset (added to every coordinate)
self._ctx_stack = []       # stack for pushContext/popContext
self._target = self._canvas  # lockFocus changes the destination
```
- `setClipRect` → `self._target.set_clip(pygame.Rect(...))`, `clearClipRect` →
  `set_clip(None)`.
- `setDrawOffset(x,y)` → stores offset; **every primitive adds `_ox/_oy`**.
- `pushContext()` → `self._ctx_stack.append((color, offset, clip, font, target))`;
  `popContext()` → restores. This is how "cameras" and HUDs are made.
- `lockFocus(img)` → `self._target = deref(img)`; `unlockFocus()` → back to the
  canvas. (This is how you draw off-screen: create an image and lockFocus on it.)

---

### 4.5 `graphics.image` + `graphics.imagetable`

Signatures:
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
**Handle pattern** (rule 2): `image.new` loads a `pygame.Surface` and returns
`self._handle(surface)`. `draw` does `self._deref(img)`. Note: on Playdate
images are **1 bit + mask**; a PNG with alpha is handled with `setMaskImage`.
To start, load the PNG and use its alpha channel as the mask.

```python
def image_new(self, path):
    surf = pygame.image.load(path).convert_alpha()
    return self._handle(surf)

def image_draw(self, img_tbl, x, y, *args):
    surf = self._deref(img_tbl)
    self._target.blit(surf, (x + self._ox, y + self._oy))
```
Registration: `gfx["image"] = T({"new": self.image_new, "draw": self.image_draw, ...})`.
Colon vs dot: `img:draw(...)` in Lua passes `img` as 1st argument → your Python
function must receive it first (`img_tbl`). That's why `image_draw(self, img_tbl, x, y)`.

---

### 4.6 `graphics.sprite` (the biggest class: 43 methods)

Main signatures:
```
gfx.sprite.new([image_or_tilemap])       -- it's ".", not ":"
gfx.sprite.update()                      -- updates and redraws the whole list
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
**Model**: a global list of sprites; `sprite.update()` iterates, calls `:update()`
(if the sprite defines it) and **draws ordered by z-index** after the background.

```python
# pd/sprite.py
class Sprite:
    def __init__(self, image=None):
        self.x = self.y = 0.0; self.z = 0; self.visible = True
        self.image_id = image; self.rotation = 0; self.scale = 1
        self.collide = None; self.tag = None; self.update_fn = None
```
`moveWithCollisions` is the tricky one: start with a simple AABB (move in X,
revert if it hits; then in Y) against the collision rects of `getCollisionRects`
of the tilemap + other sprites. That is the classic "sliding".

The background: the runtime must draw `setBackgroundDrawingCallback` before the
sprites.

---

### 4.7 `graphics.tilemap`

```
gfx.tilemap.new() | tilemap:setImageTable(imagetable) | tilemap:setSize(w, h)
tilemap:setTiles(data, width) | getTileAtPosition(x, y) | setTileAtPosition(x, y, i)
tilemap:draw(x, y, [sourceRect]) | drawIgnoringOffset(x, y, [sourceRect])
tilemap:getCollisionRects(emptyIDs) | getTileSize() / getPixelSize() / getSize()
```
`data` = 1D array of tile indices. `draw` iterates and blits each tile from the
imagetable. `getCollisionRects(emptyIDs)` returns the rects of tiles whose index
is **not** in `emptyIDs` — feeds `moveWithCollisions`.

---

### 4.8 `graphics.font`

```
gfx.font.new(path) | font:drawText(text, x, y, [w, h], ...)
font:getTextWidth(text) | font:getHeight() | font:getLeading() / setLeading(px)
font:getTracking() / setTracking(px) | font:getGlyph(ch)
gfx.getSystemFont([variant]) | gfx.getTextSize(str, [fontFamily, [leadingAdjustment]])
```
Playdate uses its own font format; the pragmatic thing is to map to
`pygame.font.Font` with a TTF. For pixel-perfect fidelity, extract the font from
the SDK (`.../Resources/fonts/`) and put it in `assets/fonts/`.

---

### 4.9 `geometry` (a Lua-pure candidate)

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
It's pure math. **Recommendation: implement it in Lua** (`lib/geometry.lua`) and
load it at startup; then every object is a Lua table with fields and methods and
you never cross the Python bridge once. Methods are written as
`function Rect:offsetBy(dx, dy) self.x = self.x + dx ... end`, and `:unpack()`
returns multiple values with `return self.x, self.y, self.width, self.height`.

---

### 4.10 `timer` / `frameTimer` / `easingFunctions` (pure Lua)

```
playdate.timer.new(duration, callback, [args])                -- callback on finish
playdate.timer.new(duration, [startValue, endValue, [easing]])
playdate.timer.performAfterDelay(delay, callback, [args])
playdate.timer.keyRepeatTimer([delay], [repeatDelay], callback, [args])
playdate.timer.updateTimers()      -- the runtime calls it itself, each frame
timer:start() :pause() :reset() :remove()
timer.value / duration / timeLeft / paused / repeats / discardOnCompletion / easingFunction
easingFunctions.linear / inQuad / outQuad / inOutQuad / … (all the families)
math.lerp(min, max, t)
```
Implement them in `lib/timer.lua`: a list of timers, and the emulator calls
`playdate.timer.updateTimers()` (and frameTimer) **automatically** after the
game's `update()`, so the game doesn't have to.

---

### 4.11 `json`

`playdate.json.encode(tbl) -> string` · `playdate.json.decode(str) -> table`.

Use Python's `json` + your recursive `_to_lua()` (rule 3) for decode.

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
Per-game sandbox: use `<game_dir>/.pd_data/`. `file.open` returns a **handle**
(`__id` table → Python `file` object). `datastore` is a fixed `file.open`
(`data.json`).

---

### 4.13 `sound` (minimum viable)

It is enormous (fileplayer, sampleplayer, sample, synth, effect, channel,
sequence, track, instrument, lfo, envelope, signal, filters, micinput...). **Do
not implement it fully**: make a shim.

Minimum useful:
```
playdate.sound.fileplayer.new([path]) :play([n]) :stop() :pause() :setVolume(l,r)
                                       :setRate(r) :isPlaying() :setOffset(s) :getLength()
playdate.sound.sampleplayer.new([sample]) :play([n],[r]) :stop() :setVolume(l,r)
playdate.sound.sample.new(path) :play([n],[r]) :getLength()
playdate.sound.synth.new([waveform]) :playNote(freq,[vol],[len]) :noteOff() :setADSR(...)
playdate.sound.setOutputsActive(headphones, speaker) / playdate.sound.setVolume(...)
```
On top of `pygame.mixer`. On the Pi, if there's no audio, initialize the mixer
in dummy mode so the calls don't crash. Note: Playdate's `.pda`/ADPCM need a
decoder; to begin, it supports WAV/OGG (what `pygame.mixer` already understands).

---

### 4.14 `ui`

The most common:
```
playdate.ui.gridview  (11 methods): :new() :setNumberOfSections/… :drawInRect(...)
                                     :selectNextRow(...) :getSelection() …
playdate.ui.crankIndicator :new() :draw([xOffset,yOffset]) :getBounds()
playdate.getSystemMenu() -> menu/playdate.menu:
   menu:addMenuItem(title, cb) | addCheckmarkMenuItem(t,cb) | addOptionsMenuItem(t, opts, cb)
   menu:removeMenuItem(mi) | removeAllMenuItems() | getMenuItems()
   item:setTitle(t) | setValue(v) | setCallback(cb) | getTitle() | getValue()
```
`gridview` and `crankIndicator` are drawable: implement their `drawInRect` with
your primitives. The menu is drawn by **the emulator** (the `Esc` key opens it)
so tests and the game have a menu.

---

### 4.15 Others (low priority)

- **keyboard**: `playdate.keyboard.show([text]) / hide() / .text / isVisible()` +
  callbacks (`keyboardDidShowCallback`, `textChangedCallback`,
  `keyboardDidHideCallback`). In the emulator → capture real keyboard text into
  a buffer and draw it.
- **accelerometer**: `startAccelerometer() / readAccelerometer() -> x,y,z /
  stopAccelerometer()`. Stub returning `(0,0,0)`, or map to the joystick.
- **power**: `getBatteryPercentage() / getBatteryVoltage() / getPowerStatus()` →
  stub; on the Pi read `/sys/class/power_supply/`.
- **animator / animation**: interpolates sprite props over time → pure Lua.
- **pathfinder**: graph + A* → pure Lua.
- **graphics.perlin**: Perlin noise → Python or Lua.
- **network (http/tcp)**: probably don't touch on the Pi.

---

## 5. pygame cheat-sheet

| Playdate | pygame |
|---|---|
| `drawLine` | `pygame.draw.line` |
| `fillRect` / `drawRect` | `pygame.draw.rect` (fill / `width=1`) |
| `fillCircleInRect` / `drawEllipseInRect` | `pygame.draw.ellipse` (fill / `width=1`) |
| `fillCircleAtPoint` | `pygame.draw.circle` |
| `drawPolygon` / `fillPolygon` | `pygame.draw.polygon` |
| `drawArc` | `pygame.draw.arc` (⚠️ convert angle: 0°=up, clockwise) |
| `setClipRect` / `clearClipRect` | `Surface.set_clip` / `set_clip(None)` |
| `pushContext` / `popContext` | your own state stack |
| `setDrawOffset` | an offset of your own added to every coordinate |
| `image.new` / `img:draw` | `pygame.image.load` / `Surface.blit` |
| `drawRotated` / `drawScaled` | `pygame.transform.rotate` / `.scale` |
| `font.new` / `drawText` | `pygame.font.Font` / `font.render` |
| `sound.*` | `pygame.mixer` |
| `buttonIsPressed` | `pygame.key` / `pygame.joystick` |
| `display.setRefreshRate` | target `clock.tick()` |

---

## 6. lupa gotchas (the ones that will bite you)

1. **Never nested Python objects** (rule 2). Symptom if you forget: after ~77
   reads a function starts returning *something else* (we saw
   `button.isPressed` returning `Graphics.drawText`) and the game explodes on a
   weird frame.
2. **`:` vs `.`**: `obj:method(a)` in Lua calls `method(obj, a)`. If your Python
   function is a field of the table, it receives the handle as the **first**
   parameter. Define it: `def method(self, handle_tbl, a):`. Module methods
   (`gfx.fillRect`) are called with `.` and receive no handle.
3. **Tuples → multiple returns** (`unpack_returned_tuples=True`).
4. **list/dict → userdata**, not table. Use `_to_lua()`.
5. **`table_from` does not recurse**: nested dicts stay as Python objects.
6. **`None` → `nil`**; bools are fine.
7. **Callbacks**: you store a `lupa.Function` and YOU decide when to invoke it
   (`update`, `draw`, timers, `setCrankCallback`). The game doesn't call them;
   the runtime does.

---

## 7. Definition of "done" for each API

For every function you add:

1. `python tools/api_coverage.py <game>` no longer lists it as missing.
2. `python tools/headless_test.py <game> -n 300` passes without errors.
3. If it draws: `--save /tmp/f.png` and **look at the PNG** (a real frame, don't
   assume).
4. If it has state (clip, offset, color): a test that draws *and* redraws after
   `popContext`, to catch state leaks.

---

## 8. Notes for the Pi Zero 2 W

- All this runs the same; install `pygame-ce` + `lupa` with pip.
- Real bottleneck: scaled rendering and the Lua interpreter. 320×320 at 30 FPS
  is plenty. If it's tight: `--scale 1`, or
  `pygame.display.set_mode(..., pygame.SCALED)`.
- Lua fidelity: Playdate uses **LuaJIT** (5.1 + bitwise ops). Here it is
  **Lua 5.5**. Most games work; for maximum fidelity compile LuaJIT and lupa
  against it.
