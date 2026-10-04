"""luaobj.py — base para objetos que Lua debe ver como USERDATA (no tablas).

Por que importa: en la consola, los objetos de Playdate (sampleplayer, image,
sprite...) son userdata. Un juego que haga:

    for k, v in pairs(cosas) do
      if type(v) == "table" then <recorrer> else v:metodo() end
    end

se comporta DISTINTO segun eso. Si nuestros objetos fueran tablas Lua, el juego
entra a "recorrer" sus metodos (que son funciones) y explota con cosas como
`'function' object has no attribute 'setVolume'`. Ese fue el snag de Smolitaire.

Exponiendolos como objetos Python, lupa los entrega como `type() == "userdata"`,
el Dispatch de metodos con `:` funciona, y el juego no los confunde con tablas.
"""


_REPORTED = set()


class Permissive:
    """Devuelve no-ops para metodos/campos inexistentes (evita que el juego muera).

    Los dunders NO se interceptan a proposito: lupa sondea `__getitem__`/`__len__`
    para decidir como accede al objeto, y si devolvieramos un no-op para ellos
    creeria que el objeto es subscriptable y fallaria.

    IMPORTANTE: cada metodo que NO tenemos se AVISA una vez. Un no-op silencioso
    deja al juego "corriendo sin errores y sin pintar nada" -- asi se escondieron
    durante toda una sesion el cursor y el menu de Smolitaire.
    """

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        key = f"{type(self).__name__}.{name}"
        if key not in _REPORTED:
            _REPORTED.add(key)
            print(f"[permisivo] {key} -> no-op (API no implementada?)")

        def _noop(*a, **k):
            return None

        return _noop
