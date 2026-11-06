# Asistente FUT

Este módulo crea borradores del Formulario Único de Trámite (FUT) en PDF. Usa
`qwen3-embedding:0.6b` para buscar tipos de trámite y funcionarios en la colección Chroma
`Fut_UnambaDB` y `qwen2.5:7b` para proponer una redacción formal de la
fundamentación. Ambos modelos corren en Ollama local.

- `catalogo.json`: tipos de trámite y descripciones; no contiene autoridades de ejemplo.
- `catalogo.py`: sincroniza el catálogo con `chroma/`.
- `funcionarios.py`: indexa nombres y cargos publicados en gob.pe.
- `agent.py`: diálogo por sesión, búsqueda de autoridades, validación y descarga temporal.
- `pdf.py`: rellena la plantilla oficial proporcionada sin cambiar su diseño.

Para añadir o actualizar tipos de trámite, edita `catalogo.json` y ejecuta:

```powershell
.\.venv\Scripts\python.exe -m motores.fut_unamba.catalogo
```

Los nombres y cargos se consultan desde el índice de funcionarios y se confirman con el usuario antes de usarlos.

Los datos personales de cada borrador y los PDF generados se conservan en
memoria durante la sesión (una hora) y 15 minutos para descargar,
respectivamente. No se indexan en Chroma.

# Funcionarios oficiales

Desde la raíz del servidor, actualiza los nombres y cargos de las cuatro páginas de gob.pe con:

```powershell
.\.venv\Scripts\python.exe -m motores.fut_unamba.funcionarios
```

El indexador guarda los 77 registros actuales en `Fut_UnambaDB`, junto con los cuatro tipos de FUT. Consulta únicamente la lista de funcionarios de UNAMBA, no los perfiles individuales. Si alguna página falla, conserva el índice anterior. Ejecuta el comando nuevamente cuando cambie el directorio; el asistente pide confirmar el destinatario antes de usarlo.

El PDF generado utiliza `assets/FUT UNAMBA.pdf` como plantilla y rellena sus espacios sin rediseñar el formulario. Los datos personales de los estudiantes permanecen en la sesión temporal y no se guardan en Chroma.
