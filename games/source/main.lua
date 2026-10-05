import "CoreLibs/graphics"

local gfx = playdate.graphics

function playdate.update()
    -- 1. Limpiar toda la pantalla en BLANCO
    gfx.clear(gfx.kColorWhite)

    local texto = "¡HOLA, PLAYDATE!"
    local x = 100
    local y = 100
    
    -- 2. Obtener el ancho y alto exacto del texto
    local width, height = gfx.getTextSize(texto)
    
    -- 3. Dibujar un rectángulo negro detrás del texto
    -- Se le suma un pequeño margen (+10) para que no quede tan ajustado
    gfx.setColor(gfx.kColorBlack)
    gfx.fillRect(x - 5, y - 5, width + 10, height + 10)

    -- 4. Configurar el modo de dibujo a BLANCO y dibujar el texto
    gfx.setImageDrawMode(gfx.kDrawModeFillWhite)
    gfx.drawText(texto, x, y)
end