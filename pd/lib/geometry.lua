-- pd/lib/geometry.lua — playdate.geometry implementado en Lua puro.
--
-- Se ejecuta al arrancar el emulador; runtime.py toma el global __pd_geometry
-- y lo asigna a playdate.geometry. Al ser Lua "de verdad", los campos
-- (rect.x, rect.width...) y los metodos quedan consistentes: el juego puede
-- leer/escribir campos y llamar metodos sin pasar por el puente Python.

local geometry = {}

-- ===================== vector2D =====================
local V2 = {}
V2.__index = V2
function V2:magnitude() return math.sqrt(self.x * self.x + self.y * self.y) end
function V2:magnitudeSquared() return self.x * self.x + self.y * self.y end
function V2:normalized()
  local m = self:magnitude()
  if m == 0 then return geometry.vector2D.new(0, 0) end
  return geometry.vector2D.new(self.x / m, self.y / m)
end
function V2:normalize()
  local m = self:magnitude()
  if m ~= 0 then self.x = self.x / m; self.y = self.y / m end
  return self
end
function V2:scaledBy(s) return geometry.vector2D.new(self.x * s, self.y * s) end
function V2:scale(s) self.x = self.x * s; self.y = self.y * s; return self end
function V2:addVector(v) return geometry.vector2D.new(self.x + v.x, self.y + v.y) end
function V2:dotProduct(v) return self.x * v.x + self.y * v.y end
function V2:angleBetween(v)
  local m = self:magnitude() * v:magnitude()
  if m == 0 then return 0 end
  local c = self:dotProduct(v) / m
  return math.deg(math.acos(math.max(-1, math.min(1, c))))
end
function V2:leftNormal() return geometry.vector2D.new(self.y, -self.x) end
function V2:rightNormal() return geometry.vector2D.new(-self.y, self.x) end
function V2:unpack() return self.x, self.y end
function V2:copy() return geometry.vector2D.new(self.x, self.y) end

geometry.vector2D = {
  new = function(x, y)
    return setmetatable({ x = tonumber(x) or 0, y = tonumber(y) or 0 }, V2)
  end,
  newPolar = function(angle, radius)          -- angulo en grados, 0 = arriba
    local r = math.rad(angle)
    return setmetatable({ x = radius * math.sin(r), y = -radius * math.cos(r) }, V2)
  end,
}

-- ===================== point =====================
local P = {}
P.__index = P
function P:offsetBy(dx, dy) self.x = self.x + dx; self.y = self.y + dy; return self end
function P:offset(dx, dy) return geometry.point.new(self.x + dx, self.y + dy) end
function P:distanceToPoint(p) return math.sqrt((p.x - self.x) ^ 2 + (p.y - self.y) ^ 2) end
function P:squaredDistanceToPoint(p) return (p.x - self.x) ^ 2 + (p.y - self.y) ^ 2 end
function P:unpack() return self.x, self.y end
function P:copy() return geometry.point.new(self.x, self.y) end
geometry.point = {
  new = function(x, y) return setmetatable({ x = x or 0, y = y or 0 }, P) end,
}

-- ===================== size =====================
local S = {}
S.__index = S
function S:unpack() return self.width, self.height end
function S:copy() return geometry.size.new(self.width, self.height) end
geometry.size = {
  new = function(w, h) return setmetatable({ width = w or 0, height = h or 0 }, S) end,
}

-- ===================== rect =====================
local R = {}
R.__index = R
function R:offsetBy(dx, dy) self.x = self.x + dx; self.y = self.y + dy; return self end
function R:offset(dx, dy)
  return geometry.rect.new(self.x + dx, self.y + dy, self.width, self.height)
end
function R:insetBy(dx, dy)
  self.x = self.x + dx; self.y = self.y + dy
  self.width = self.width - 2 * dx; self.height = self.height - 2 * dy
  return self
end
function R:containsPoint(a, b)
  local px, py = a, b
  if type(a) == "table" then px, py = a.x, a.y end
  return px >= self.x and px <= self.x + self.width
     and py >= self.y and py <= self.y + self.height
end
function R:intersects(r2)
  return not (self.x + self.width <= r2.x or r2.x + r2.width <= self.x
          or self.y + self.height <= r2.y or r2.y + r2.height <= self.y)
end
function R:intersection(r2)
  local x1 = math.max(self.x, r2.x)
  local y1 = math.max(self.y, r2.y)
  local x2 = math.min(self.x + self.width, r2.x + r2.width)
  local y2 = math.min(self.y + self.height, r2.y + r2.height)
  if x2 <= x1 or y2 <= y1 then return nil end
  return geometry.rect.new(x1, y1, x2 - x1, y2 - y1)
end
function R:union(r2)
  local x1 = math.min(self.x, r2.x)
  local y1 = math.min(self.y, r2.y)
  local x2 = math.max(self.x + self.width, r2.x + r2.width)
  local y2 = math.max(self.y + self.height, r2.y + r2.height)
  return geometry.rect.new(x1, y1, x2 - x1, y2 - y1)
end
function R:centerPoint() return geometry.point.new(self.x + self.width / 2, self.y + self.height / 2) end
function R:isEmpty() return self.width <= 0 or self.height <= 0 end
function R:isEqual(r2)
  return self.x == r2.x and self.y == r2.y and self.width == r2.width and self.height == r2.height
end
function R:unpack() return self.x, self.y, self.width, self.height end
function R:copy() return geometry.rect.new(self.x, self.y, self.width, self.height) end
function R:toPolygon()
  return geometry.polygon.new(self.x, self.y,
    self.x + self.width, self.y,
    self.x + self.width, self.y + self.height,
    self.x, self.y + self.height)
end
geometry.rect = {
  new = function(x, y, w, h)
    return setmetatable({ x = x or 0, y = y or 0, width = w or 0, height = h or 0 }, R)
  end,
}

-- ===================== lineSegment =====================
local L = {}
L.__index = L
function L:length() return math.sqrt((self.x2 - self.x1) ^ 2 + (self.y2 - self.y1) ^ 2) end
function L:midPoint()
  return geometry.point.new((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)
end
function L:unpack() return self.x1, self.y1, self.x2, self.y2 end
function L:copy()
  return geometry.lineSegment.new(self.x1, self.y1, self.x2, self.y2)
end
geometry.lineSegment = {
  new = function(x1, y1, x2, y2)
    return setmetatable({ x1 = x1, y1 = y1, x2 = x2, y2 = y2 }, L)
  end,
}

-- ===================== polygon =====================
local PG = {}
PG.__index = PG
function PG:count() return #self.points // 2 end
function PG:unpack() return table.unpack(self.points) end
function PG:copy()
  local pts = {}
  for i = 1, #self.points do pts[i] = self.points[i] end
  return setmetatable({ points = pts }, PG)
end
geometry.polygon = {
  new = function(...)
    return setmetatable({ points = { ... } }, PG)
  end,
}

-- ===================== helpers =====================
function geometry.distanceToPoint(x1, y1, x2, y2)
  return math.sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2)
end
function geometry.squaredDistanceToPoint(x1, y1, x2, y2)
  return (x2 - x1) ^ 2 + (y2 - y1) ^ 2
end

geometry.kUnflipped = 0
geometry.kFlippedX = 1
geometry.kFlippedY = 2
geometry.kFlippedXY = 3

__pd_geometry = geometry
