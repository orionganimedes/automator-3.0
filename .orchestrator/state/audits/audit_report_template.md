# Reporte de Auditoría: [ID_TAREA]

- **Fecha**: YYYY-MM-DD HH:MM
- **Worker auditado**: [ADB / Visión / Workflows / Web]
- **Archivos auditados**:
  - `ruta/al/archivo_1.py`
- **Resultado de compilación**: [PASSED / FAILED]
- **Veredicto**: [STATUS: APPROVED / STATUS: REJECTED]

---

### Checklist ejecutado

- [ ] `python3 -m py_compile` sin errores
- [ ] Sin imports circulares ni dependencias faltantes
- [ ] Sin rutas absolutas hardcodeadas
- [ ] Sin `time.sleep(N)` con N constante
- [ ] Sin `subprocess.run` sin `timeout`
- [ ] Sin parseos JSON inline fuera de `_clean_and_parse_json()`
- [ ] Sin URL Ollama hardcodeada
- [ ] Cero emojis en comentarios generados
- [ ] Máximo 3 líneas por comentario
- [ ] Sin primera persona en comentarios

---

### Observaciones y Hallazgos

1. [Criticidad Alta/Media/Baja]: descripción técnica del problema o confirmación de buenas prácticas.

---

### Directivas para el Worker

- **[Si REJECTED]**: instrucciones exactas de refactorización requeridas antes de volver a auditar.
- **[Si APPROVED]**: autorización para commit y paso a la siguiente tarea del `plan_maestro.md`.
