# Diffs de Tareas

Cada Worker guarda aquí el diff de sus cambios antes de enviarlo al Auditor.

## Convención de nombres

```
<ID_TAREA>_<worker>_<YYYY-MM-DD>.diff
```

Ejemplo: `F1-01_adb_2026-10-19.diff`

## Cómo generar el diff

```bash
git diff HEAD -- <archivo_modificado> > .orchestrator/state/diffs/F1-01_adb_2026-10-19.diff
```

El Auditor lee el diff antes de ejecutar el checklist de compilación.
