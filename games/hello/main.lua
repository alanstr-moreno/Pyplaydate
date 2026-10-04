-- Juego de ejemplo minimo. Usa solo el subconjunto implementado:
--   playdate.getGraphics(): clear/fillRect/drawRect/drawText/drawCenteredText
--   playdate.setUpdateCallback / setDrawCallback
--   playdate.button: wasPressed/isPressed  (A, B, Up, Down, Left, Right)
--   playdate.getTimer()

local gfx = playdate.getGraphics()
gfx.setFont("System", 20)

local x = 160
local y = 160
local hops = 0

-- estado del stick/cuadrado
gfx.clear(0)

function playdate.update()
    local speed = 3
    if playdate.button.isPressed('Left')  then x = x - speed end
    if playdate.button.isPressed('Right') then x = x + speed end
    if playdate.button.isPressed('Up')    then y = y - speed end
    if playdate.button.isPressed('Down')  then y = y + speed end

    if playdate.button.wasPressed('A') then hops = hops + 1 end
    if playdate.button.wasPressed('B') then hops = 0 end

    -- limita a la pantalla
    if x < 12 then x = 12 end
    if x > 308 then x = 308 end
    if y < 12 then y = 12 end
    if y > 308 then y = 308 end
end

function playdate.draw()
    gfx.clear(0)
    gfx.drawText("Playdate Pi", 10, 10)
    gfx.drawCenteredText("A: +1   B: reset", 40)
    gfx.drawCenteredText("A saltos: " .. hops, 60)
    gfx.drawCenteredText(string.format("t = %.1fs", playdate.getTimer()), 80)

    -- el "jugador": un cuadrado
    gfx.fillRect(x - 10, y - 10, 20, 20)
end
