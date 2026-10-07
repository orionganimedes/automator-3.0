"""
web - Capa web (Flask).

Estructura de Blueprints para migrar incrementalmente las rutas de app.py:

    web/routes/memory.py   -> /api/memory/*  (métricas de memoria)

Próximos blueprints sugeridos (siguiendo la organización del plan):
    web/routes/dashboard.py, comments.py, accounts.py, facebook.py,
    instagram.py, report.py, device.py

La fábrica `create_app()` se puede añadir aquí cuando app.py deje de
registrar rutas a nivel de módulo (refactor Fase 6 completo).
"""
