# Validación con el índice local

## Comprobación realizada el 12 de octubre de 2026

Se consultó `/rag/buscar` en el servidor local activo, sin reindexar ni
modificar el catálogo. Esta comprobación evalúa recuperación, no la precisión
de las respuestas generadas ni la vigencia de las páginas en internet.
El servidor estaba iniciado antes de los cambios de configuración.

| Pregunta | Fuente esperada del catálogo | Resultado observado |
|---|---|---|
| ¿Cómo acceder a la biblioteca de la UNAMBA? | `https://www.gob.pe/104377-acceder-a-la-biblioteca-de-la-unamba` | Recuperada; contexto de 2975 caracteres. |
| ¿Dónde encuentro información de admisión de la UNAMBA? | `https://www.unamba.edu.pe/admision/` | Recuperada junto con una convocatoria de examen; también incluyó un reglamento de admisión docente. Contexto de 1841 caracteres. |
| ¿Dónde encuentro el TUPA de la UNAMBA? | Catálogo institucional de TUPA en Gob.pe y sus publicaciones | Recuperados; contexto de 3403 caracteres. No se comprobó cuál publicación está vigente. |

La búsqueda de admisión muestra una oportunidad concreta de mejora:
distinguir ingreso de estudiantes de incorporación de docentes. Encontrar
una fuente adecuada no implica que todos los fragmentos sean pertinentes.

## Repetir y ampliar la revisión

Desde la raíz del repositorio, con Ollama y el servidor iniciados, usa primero
recuperación y luego generación. La instalación y el arranque se describen en
el [README general](../../README.md).

```powershell
.\venv\Scripts\python.exe scripts\servicios_web.py preguntar "¿Cómo acceder a la biblioteca de la UNAMBA?" --buscar --metricas
.\venv\Scripts\python.exe scripts\servicios_web.py preguntar "¿Cómo acceder a la biblioteca de la UNAMBA?" --metricas
```

Repite para las tres preguntas anteriores y estas variantes:

- ¿Dónde encuentro información para el examen de ingreso de estudiantes?
- ¿Dónde encuentro el reglamento de admisión a la carrera docente?
- ¿Qué requisitos indica la página guardada para acceder a la biblioteca?
- ¿Cuál es el enlace del TUPA? (Debe distinguir enlace de contenido leído.)
- ¿Cuál será el costo de matrícula en 2040? (No debe inventar una cifra.)

Para cada caso registra pregunta, fuentes recuperadas, respuesta, tiempo y
si cada afirmación está sustentada por el texto guardado. Compara los casos
de biblioteca con la página del visor y los de admisión con sus respectivos
documentos del catálogo. No des por correcta una respuesta solo porque cita
un dominio oficial. Si el dato solo está en un PDF no leído, la respuesta
debe reconocer esa limitación.

Las respuestas generadas y los casos adicionales quedan pendientes de
evaluación; no forman parte de las 12 pruebas unitarias aprobadas.
