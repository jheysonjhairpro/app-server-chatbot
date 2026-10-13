# IA UNAMBA — Chatbots de documentos y páginas web

Proyecto local con dos motores RAG que utilizan Ollama y ChromaDB:

- **Documentos:** consulta PDF indexados, conserva sesiones y permite seleccionar documentos.
- **Web:** consulta páginas oficiales previamente rastreadas. Cada pregunta es independiente; no conserva historial conversacional.

El servidor Flask ofrece una entrada común para ambos modos. El motor web corre en un proceso FastAPI independiente. Su interfaz HTML es un **explorador del índice**, no una pantalla de conversación.

Este es el README general y `requirements.txt`, en esta misma carpeta, es el único archivo de dependencias Python del proyecto. Todos los comandos siguientes se ejecutan desde la raíz del repositorio en PowerShell, salvo que se indique otra cosa.

## 1. Requisitos e instalación

- Python 3.12 o superior. El entorno local actual se verificó con Python 3.14.7.
- Ollama activo, normalmente en `http://127.0.0.1:11434`.
- Los modelos instalados que utiliza cada motor:
  - Embeddings de ambos: `qwen3-embedding:0.6b`.
  - Generación PDF por defecto: `qwen2.5:7b`.
  - Generación web por defecto: `qwen3:8b`; puede cambiarse con `CHAT_MODEL` en su `.env`.
- Los índices existentes, o una indexación inicial. Instalar paquetes no reconstruye las bases de datos.

```powershell
# Solo si todavía no existe el entorno:
python -m venv venv

.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip check
ollama list
```

Los servidores no descargan modelos automáticamente. Para ingerir nuevos PDF también hacen falta Poppler y el modelo OCR `seriouswebby/paddleocr-vl-1.6:latest`. No son necesarios para consultar PDF ya indexados.

### Configurar el motor web

Si no existe su configuración local:

```powershell
if (-not (Test-Path motores\chatbot_web\.env)) {
    Copy-Item motores\chatbot_web\.env.example motores\chatbot_web\.env
}
```

En `motores/chatbot_web/.env`, establece estas rutas para la estructura integrada:

```dotenv
CHROMA_PATH=../../chroma_db/web
STATE_PATH=../../datos/web/web.sqlite3
WEB_COLLECTION=unamba_web
```

El ejemplo original tiene rutas internas al motor: hay que sustituirlas al preparar una instalación integrada. Las rutas relativas se resuelven desde `motores/chatbot_web`, independientemente de la carpeta desde la que ejecutes Python. También se admiten rutas absolutas. Conserva el `.env` existente si ya está configurado.

## 2. Iniciar el proyecto

```powershell
.\iniciar_integrado.bat
```

Arranca ambos servicios:

| Servicio | Dirección predeterminada | Función |
|---|---|---|
| Servidor compartido Flask | `http://127.0.0.1:5000` | API de documentos y adaptador web |
| Motor web FastAPI | `http://127.0.0.1:8002` | Recuperación, generación y explorador |
| Documentación de la API web | `http://127.0.0.1:8002/docs` | Contratos de los endpoints nativos |

El motor web escucha en `127.0.0.1`; Flask escucha por defecto en `0.0.0.0`. Puedes configurar `UNAMBA_HOST`, `UNAMBA_PORT`, `UNAMBA_WEB_PORT` y `UNAMBA_TOKEN` en el entorno antes de arrancar. Los clientes del servidor compartido deben enviar ese token en `X-Token`.

Pulsa Ctrl+C para detener el lanzador. En Windows cierra los árboles de procesos que él mismo inició. Si un puerto ya está ocupado, avisa sin detener el servicio ajeno. No ejecutes el integrado y el motor web independiente simultáneamente con el mismo puerto.

### Qué hace cada archivo .bat

| Archivo | Qué ejecuta | Cuándo utilizarlo |
|---|---|---|
| `iniciar_integrado.bat` | Flask y FastAPI web | Arranque habitual del proyecto completo |
| `iniciar_web.bat` | Solo FastAPI web, puerto 8002 | Consultas web y explorador sin iniciar el modo PDF |
| `preguntar_web.bat` | Cliente de terminal interactivo | Hacer preguntas a un motor web que ya está activo |
| `reindexar_web.bat` | Rastreador e indexador web | Actualizar el índice; detener primero el motor web |

Los cuatro lanzadores tienen funciones distintas. Los dos primeros comparten el arranque web, pero tienen alcances diferentes: usa `iniciar_integrado.bat` para el proyecto completo e `iniciar_web.bat` para trabajar únicamente con el motor web.

Para usar únicamente el visor de lectura, incluso mientras el chatbot está activo:

```powershell
.\venv\Scripts\python.exe scripts\servicios_web.py visor
```

Abre `http://127.0.0.1:8003`. El puerto se configura con `UNAMBA_VISOR_PORT`; consultar el catálogo no requiere Ollama.

## 3. Consultar los chatbots

### API compartida

| Método | Ruta | Autenticación y función |
|---|---|---|
| GET | `/salud` | Pública; indica que Flask responde |
| GET | `/salud/web` | `X-Token`; comprueba Ollama y el índice web |
| POST | `/chat/docs` | `X-Token`; consulta documentos y mantiene sesión |
| POST | `/chat/web` | `X-Token`; consulta el motor web y añade fuentes al texto progresivo |

Ambas rutas de chat reciben este formato:

```json
{"message": "Tu pregunta", "session": "usuario-1"}
```

En web, `session` se admite por compatibilidad, pero no añade memoria conversacional. `/salud` no comprueba los modelos ni sustituye a `/salud/web`.

Comandos del modo documentos:

| Mensaje | Acción |
|---|---|
| `/pdfs` | Listar los PDF indexados |
| `/pdf N` | Seleccionar un documento de la lista |
| `/pdf N pregunta` | Seleccionar y consultar ese documento |
| `/todos` | Volver a consultar todos los documentos |
| `/completo` o `/auto` | Cambiar el modo de consulta |
| `/recargar` | Recargar el catálogo de la sesión |

### Terminal y API nativa web

Con el motor web activo en otra consola:

```powershell
.\preguntar_web.bat

# Una pregunta y terminar:
.\venv\Scripts\python.exe scripts\servicios_web.py preguntar "¿Qué servicios ofrece la biblioteca?"

# Recuperar contexto sin generar una respuesta:
.\venv\Scripts\python.exe scripts\servicios_web.py preguntar "¿Dónde encuentro el TUPA?" --buscar --metricas
```

En la consola interactiva, escribe `salir` para cerrar. El cliente utiliza `UNAMBA_WEB_PORT` cuando se invoca mediante estos lanzadores.

FastAPI ofrece `/health`, `/rag/buscar` y `/rag/preguntar`. Los dos últimos reciben `pregunta` y una `categoria` opcional; `/rag/preguntar` transmite eventos SSE por defecto, o devuelve JSON con `"streaming": false`. Estas rutas nativas locales no pasan por la autenticación de Flask.

### Explorador del índice

La interfaz del puerto 8002 permite buscar, filtrar por portal, categoría, fechas y documentos enlazados, alternar tarjetas/tabla y exportar todos los resultados filtrados como CSV. Las alertas señalan títulos o fechas ausentes, textos vacíos/breves y títulos o contenidos repetidos.

Endpoints de lectura: `/indice/resumen`, `/indice/portales`, `/indice/paginas`, `/indice/pagina?url=...` y `/indice/exportar`.

El rastreador web indexa HTML. Los PDF, Word y hojas de cálculo encontrados allí se guardan como **enlaces**, sin leer su contenido. El motor PDF tiene su propio proceso de ingesta. Las preguntas web consultan el índice guardado; no realizan un rastreo nuevo en cada consulta.

## 4. Actualizar los índices

### Páginas web

Mantén Ollama activo y detén el motor web antes de reindexar. El servidor y el indexador utilizan un bloqueo exclusivo sobre el estado SQLite.

```powershell
.\reindexar_web.bat --verificar
.\reindexar_web.bat
.\reindexar_web.bat --resume

# Ejemplo con límites:
.\reindexar_web.bat --max-pages 100 --max-seconds 300
```

`--verificar` solo muestra fuentes y rutas. `--resume` continúa un rastreo interrumpido. El informe del lanzador se escribe en `datos/web/actualizacion.json`. Las fuentes, dominios y límites están en `motores/chatbot_web/indexing/settings.py` y se pueden configurar mediante el `.env` del motor. El rastreador respeta `robots.txt` y evita recalcular embeddings de contenido sin cambios.

Para habilitar renderizado JavaScript opcional:

```powershell
.\venv\Scripts\python.exe -m pip install playwright
.\venv\Scripts\python.exe -m playwright install chromium
```

Añade `JAVASCRIPT_ENABLED=true` al `.env` web. Playwright es opcional y no garantiza extraer todos los portales dinámicos.

### Documentos PDF

La ingesta está en `scripts/ingest_pdf.py`. Con Ollama, los modelos OCR/embeddings y Poppler disponibles, ejecuta indicando las rutas del repositorio para evitar sus defaults históricos:

```powershell
.\venv\Scripts\python.exe scripts\ingest_pdf.py `
    --docs .\datos\pdfs `
    --db .\chroma_db `
    --output .\datos\texto_extraido `
    --poppler .\herramientas\poppler\Library\bin
```

Adapta `--poppler` si la instalación está en otra ubicación. Consulta `--help` para los parámetros de OCR y división de documentos. Detén las consultas durante el mantenimiento del índice.

## 5. Organización y configuración

| Ruta | Responsabilidad |
|---|---|
| `servidor/app.py` | Aplicación compartida, autenticación y registro único de rutas |
| `servidor/app_integrada.py` | Entrada del lanzador integrado con host/puerto del entorno |
| `servidor/config.py` | Configuración HTTP y compatibilidad de nombres PDF anteriores |
| `servidor/config_docs.py` | Valores del modo documentos, índice y colección |
| `servidor/modos/docs.py` | Adaptador PDF y sesiones |
| `servidor/modos/web.py` | Adaptador HTTP del motor web y conversión de streaming |
| `servidor/streaming_stdout.py` | Transmisión de la salida impresa por el PDF; `comun.py` mantiene imports anteriores |
| `motores/consultar_pdfs.py` | Consulta PDF; utiliza `motor_contexto.py` y `motor_bibliotecario.py` |
| `motores/consultar_pdfs_gobpe.py` | Búsqueda de enlaces documentales en Gob.pe |
| `motores/chatbot_web/main.py`, `config.py` | Aplicación FastAPI y configuración local web |
| `motores/chatbot_web/api/` | Rutas nativas de consultas y explorador |
| `motores/chatbot_web/services/` | Preparación/cierre de recursos y filtros del catálogo |
| `motores/chatbot_web/crawler/` | Descubrimiento, descarga y extracción HTML |
| `motores/chatbot_web/indexing/` | Clasificación, fragmentación y actualización incremental |
| `motores/chatbot_web/rag/` | Recuperación, cachés, contexto, respuestas y cliente Ollama |
| `motores/chatbot_web/storage/` | Estado SQLite del rastreo y catálogo |
| `motores/chatbot_web/web/` | HTML, JavaScript y CSS del explorador |
| `scripts/servicios_web.py` | Coordinación de servicios, cliente, visor e indexador web |
| `scripts/ingest_pdf.py` | OCR e indexación de documentos PDF |

Los argumentos de `motores/consultar_pdfs.py` siguen determinando el modelo y la conexión Ollama del modo PDF. Los valores de Ollama conservados en `config_docs.py` no sustituyen automáticamente esos argumentos. El modo web usa exclusivamente su propia configuración para modelos e indexación; las antiguas constantes web de `servidor/config.py` ya no se utilizan.

Ejecutar directamente `servidor/app.py` o `servidor/app_integrada.py` inicia solo Flask. Ejecutar `main.py` o `ver_indice.py` dentro del motor web usa los puertos independientes 8000 y 8001; los lanzadores de la raíz usan 8002 y 8003.

### Datos persistentes

| Ubicación integrada | Contenido |
|---|---|
| `chroma_db/` | Índice PDF, colección `documentos_unamba_qwen3` |
| `chroma_db/web/` | Índice web independiente, colección `unamba_web` |
| `datos/web/web.sqlite3` | Catálogo y estado del rastreador web |
| `datos/web/actualizacion.json` | Último informe de indexación web mediante el lanzador |
| `datos/pdfs/`, `datos/texto_extraido/` | Documentos de entrada y resultados de extracción PDF |

Para una copia de seguridad consistente, detén los servicios y los indexadores y conserva el catálogo SQLite y el índice Chroma web juntos. Las copias de esta integración son independientes del proyecto anterior.

## 6. Verificar el proyecto

```powershell
.\venv\Scripts\python.exe -m pip check
.\venv\Scripts\python.exe -m unittest discover -s tests -v

Push-Location motores\chatbot_web
..\..\venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location
```

Las suites se ejecutan en procesos separados porque los dos servicios conservan módulos llamados `config`. Las pruebas del servidor simulan el adaptador PDF y el transporte HTTP web; comprueban autenticación, registro único de rutas, validación, streaming, compatibilidad y cierre de procesos. Las pruebas web cubren configuración, catálogo y recuperación. No requieren un rastreo real ni modelos generativos activos.

Las pruebas automatizadas no certifican la precisión ni la vigencia de las respuestas institucionales. El informe [VALIDACION.md](motores/chatbot_web/VALIDACION.md) conserva la revisión de recuperación y los casos para evaluarla con el índice local.
