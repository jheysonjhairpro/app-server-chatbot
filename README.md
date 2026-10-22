# Chatbot UNAMBA — servidor local y vista en XAMPP

El servidor responde preguntas sobre el portal institucional y documentos PDF con fuentes y streaming. La vista del usuario es una aplicación PHP independiente servida por Apache en XAMPP.

## Iniciar el servidor

Haz doble clic en `iniciar.bat` de esta carpeta. Inicia la API del chatbot, el panel de documentos y, si hace falta, Ollama. Abre automáticamente la página del panel cuando está disponible. Apache se inicia por separado desde XAMPP.

El lanzador prepara Python y las dependencias de `requirements.txt`, crea el `.env` si falta, descarga los modelos de Ollama que falten y comprueba los índices del portal y PDF. Si faltan índices, intenta reconstruirlos con los documentos disponibles. Reutiliza las instalaciones y los servicios que ya estén listos. La primera preparación requiere Internet y puede tardar.

La API queda en `http://127.0.0.1:5000`; su comprobación de disponibilidad es `http://127.0.0.1:5000/salud`. Las respuestas se obtienen por POST a `/chat/web` y `/chat/docs`, con `X-Token` y JSON con `message` y `session`. El navegador usa el puente PHP de la vista; no debe conocer el token.

Para detener el servidor con doble clic, ejecuta **`detener.bat`**. También puedes usar la consola:

```bat
iniciar.bat detener
```

Cerrar la consola no detiene los procesos. Un Ollama que ya estaba funcionando se conserva al detener el servidor. Apache se administra de forma independiente desde XAMPP.

## Vista del usuario en XAMPP

1. Coloca la vista en `C:\xampp\htdocs\app-chatbot-first-commit`.
2. En el `.env` del servidor, configura `UNAMBA_UI_DIR` con esa carpeta. Si usas otra ubicación, cambia ese valor.
3. Ejecuta `iniciar.bat` del servidor. Si encuentra la vista, genera `api/servidor.local.php`, que contiene únicamente la ubicación del `.env` del servidor.
4. Inicia Apache desde el panel de XAMPP.
5. Abre `http://localhost/app-chatbot-first-commit/`.

PHP necesita cURL y mbstring; las funciones académicas pueden necesitar sus extensiones y servicios externos. Ollama y los índices son locales. Historial académico y constancias dependen del sistema académico externo.

Apache lee el puerto y el token del `.env` del servidor a través del enlace local. Las claves no se copian al JavaScript ni se entregan al navegador. El `.env` debe permanecer fuera de `htdocs` y ser legible por el usuario que ejecuta Apache. Si trasladas el servidor, vuelve a ejecutar su lanzador para actualizar el enlace. También puedes configurar `UNAMBA_ENV_FILE` en el entorno de Apache como alternativa al enlace generado.

La API puede funcionar sin la vista instalada: `UNAMBA_UI_DIR` se usa solo para enlazarla si existe. El servidor no depende de XAMPP ni de PHP para iniciar. Si Apache usa otro puerto o cambias el nombre de la carpeta, ajusta la URL del navegador.

## Uso del chatbot en la vista

La vista dispone de Portal Web, Normativas / PDF y Consultas universitarias. Las respuestas de portal y PDF aparecen en streaming. Las cuatro preguntas destacadas del portal priorizan carreras, Beca Permanencia, trámites y pagos virtuales.

En PDF, pulsa **Ver documentos disponibles**, selecciona un documento por su nombre y escribe tu pregunta. La lista usa títulos del índice, evita nombres repetidos y confirma el documento elegido. Si no hay título, utiliza un nombre legible derivado del archivo. Los comandos de selección se envían internamente; también puedes escribir `/pdfs`, `/pdf N`, `/pdf 0` o `/todos`. Los números corresponden al listado actual y pueden cambiar tras actualizar los documentos. **Consultar todos los documentos** vuelve a la búsqueda global.

El punto de estado aparece en la cabecera y la burbuja: verde indica que la API responde, gris indica desconexión o fallo de comprobación y amarillo indica la comprobación inicial. Se actualiza cada 8 segundos con la pestaña visible y al volver a ella. Comprueba `/salud`; no garantiza que los modelos, índices o servicios académicos externos estén listos.

Después de actualizar la vista, usa **Ctrl + F5**. Si cambias código Python o el `.env`, detén y vuelve a iniciar el servidor.

## Estructura

```text
app-server-chatbot-version/
  iniciar.bat                   Arranque de API, panel y Ollama
  detener.bat                   Detención de API y panel con doble clic
  .env                          Configuración privada
  .env.example                  Plantilla sin secretos
  requirements.txt              Dependencias Python de todo el proyecto
  README.md
  base de datos/
    portal/                     Índice Chroma del portal
    pdf/                        Índice Chroma de normativas
  servidor/                     API, streaming y adaptadores
  motores/
    portal_unamba/              Extracción, chunking y respuestas del portal
    pdf/                        Recuperación y respuestas de PDF
  helper/documentos/            Panel documental opcional, con sus recursos web
  compartido/documentos.py      Almacenamiento, respaldos y bloqueo compartidos
  configuracion/                Carga del .env y configuración de los componentes
  scripts/                      Arranque, operaciones del portal e indexación PDF
  datos/
    portal/documentos/          Documentos Markdown del portal
    portal/admin/               Estado del panel, versiones y respaldos
    portal/crawl/               Resultados de rastreo, cuando se ejecuta
    pdf/originales/             PDF originales
    pdf/texto_extraido/         Texto, informe y cachés de OCR y embeddings
  herramientas/poppler/         Utilidades para procesar PDF
  registros/                    Diagnóstico y estado de procesos, regenerables

C:/xampp/htdocs/app-chatbot-first-commit/
  index.php                     Vista del usuario
  api/                          Puente PHP y servicios académicos
  api/servidor.local.php         Enlace local al .env, generado por el servidor
  assets/                       Recursos locales de la vista
```

El servidor web Python/PHP-CGI y el lanzador `.bat` de la vista se retiraron porque Apache sirve la vista. El código anterior y registros históricos se conservan en un respaldo externo, no en la distribución activa.

## Configuración

Edita únicamente `.env` en la raíz del servidor para configurar modelos, puertos, token, rutas y opciones del portal y PDF. Reinicia el servidor tras editarlo. La plantilla `.env.example` no contiene secretos; si falta `.env`, el lanzador lo crea y genera un token privado.

- `UNAMBA_PORT`: puerto de la API, por defecto 5000.
- `UNAMBA_TOKEN`: token de la API, compartido únicamente con el puente PHP.
- `UNAMBA_UI_DIR`: ubicación de la vista en `htdocs`; solo se usa para enlazar su configuración.
- `UNAMBA_ADMIN_PORT`: puerto del panel documental, por defecto 8765.
- `OLLAMA_BASE_URL`: dirección HTTP local de Ollama, sin formato Markdown.
- `UNAMBA_CHAT_MODEL`, `UNAMBA_EMBEDDING_MODEL`: modelos de PDF.
- `RAG_LLM_MODEL`, `RAG_EMBED_MODEL`: modelos del portal.
- `PDF_OCR_MODEL`: modelo opcional para PDF escaneados.
- `FIRECRAWL_API_KEY`: clave necesaria para nuevas extracciones del portal.
- `PDF_*`, `RAG_*`, `PORTAL_*`: rutas y opciones de indexación, recuperación y extracción documentadas por sus nombres en la plantilla.

Las variables del entorno tienen prioridad sobre el `.env`. Los argumentos de consola explícitos tienen prioridad en su operación. Las rutas relativas se resuelven desde la carpeta del servidor. El puerto y token que lea Apache deben coincidir con los efectivos del servidor: si usas variables de proceso para sobreescribirlos, configura también Apache; se recomienda usar el `.env` para ambos.

Se retiraron `UNAMBA_UI_PORT`, `UNAMBA_UI_URL`, `PHP_EXE` y `XAMPP_ROOT`: Apache y PHP se configuran desde XAMPP. La API conserva acceso solo por localhost. Cambiar un modelo de embeddings requiere reconstruir el índice correspondiente.

## Administración opcional

El panel se inicia junto con el servidor y abre automáticamente su página en el navegador. **`detener.bat` cierra la API y el panel iniciados por el lanzador**. Cerrar la pestaña del panel no detiene los procesos.

Operaciones adicionales desde consola:

```bat
iniciar.bat preparar
iniciar.bat panel
iniciar.bat chatbot --show-sources
iniciar.bat indexar
iniciar.bat extraer
iniciar.bat enlaces
iniciar.bat limpiar
iniciar.bat limpiar --apply
iniciar.bat indexar-pdf --solo-texto
```

`preparar` comprueba dependencias, modelos e índices sin abrir la API. `panel` abre la gestión documental en `http://127.0.0.1:8765`; permite crear, revisar, editar, eliminar, extraer, limpiar con vista previa y respaldo, y volver a indexar documentos del portal. El panel iniciado como operación de consola se detiene con Ctrl+C en esa consola. Chromium se prepara para las funciones de extracción del panel. El panel iniciado automáticamente queda registrado por el lanzador; la operación manual `panel` conserva su consola.

El rastreo admite solamente `www.unamba.edu.pe` y `unamba.edu.pe`, y páginas accesibles desde los enlaces de su interfaz. Los enlaces externos presentes pueden mostrarse como referencias sin extraer su contenido. La limpieza conserva encabezados, listas, tablas y separación útil de párrafos. Tras cambiar documentos, vuelve a indexar y reinicia el servidor o usa `/recargar` en el modo portal.

El portal utiliza Markdown Header Chunking y recuperación híbrida. Los índices iniciales contienen 1031 fragmentos del portal y 676 de PDF, de 97 documentos del portal y 64 PDF completos. Otros 6 PDF tienen páginas OCR pendientes; consulta `datos/pdf/texto_extraido/resultado.json`. Estos valores cambian al reindexar. `indexar-pdf --solo-texto` utiliza texto completo existente o texto nativo; no presenta PDF incompletos como completos. El OCR opcional utiliza el modelo configurado para documentos escaneados.

## Trasladar a otro equipo

El lanzador automático está diseñado para Windows 10/11 y Python de 64 bits compatible con las versiones de `requirements.txt`. Instala manualmente Python y Ollama si no tienes winget. La vista requiere una instalación de XAMPP preparada por separado.

Copia el servidor con `datos/`, `base de datos/`, `herramientas/` y tu `.env` privado. Excluye `.venv`, `.git`, `__pycache__` y `registros/`; se recrea el entorno en cada equipo. `.git` es una referencia de un worktree de esta computadora y no forma parte de la aplicación portable. Los modelos Ollama están fuera del proyecto y se descargan si faltan.

Copia la vista a `htdocs`, ajusta `UNAMBA_UI_DIR` y ejecuta `iniciar.bat` para regenerar el enlace local. Los identificadores de los índices son relativos; mover la carpeta no cambia la identidad de los documentos. Algunos informes históricos de OCR pueden describir ubicaciones antiguas, pero no controlan las rutas activas.

## Diagnóstico

Consulta `registros/arranque_api.log` y `registros/arranque_api.error.log`. Si el lanzador inicia Ollama, genera también sus registros. `servicios.json` identifica los procesos propios y `entorno_listo.json` evita reinstalaciones innecesarias. Conserva estos dos archivos durante el uso; para limpiar registros, detén primero el servidor.

Si la vista indica que falta configuración, comprueba `UNAMBA_UI_DIR` y vuelve a ejecutar el lanzador. Si no conecta, comprueba `/salud`, Apache y cURL/mbstring. Un puerto ocupado por otro programa se informa sin cerrar ese programa. No borres índices, documentos originales ni respaldos para solucionar problemas de arranque.

## Instalar desde el repositorio

El repositorio contiene código y documentación. El `.env` privado, los índices, los documentos PDF, el corpus del portal y sus cachés se conservan fuera de Git. Antes de iniciar una copia nueva, restaura `datos/` y `base de datos/` desde tu respaldo local; como alternativa, incorpora los documentos y ejecuta las operaciones de extracción e indexación correspondientes. El arranque comprueba ambos índices y no podrá completar su preparación si faltan los documentos necesarios para reconstruirlos. Usa `.env.example` como plantilla; no publiques credenciales.
