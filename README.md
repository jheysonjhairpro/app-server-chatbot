# IA UNAMBA - Sistema RAG Local con Ollama y ChromaDB

Sistema de Inteligencia Artificial desarrollado para la ingestión, procesamiento y consulta de documentos administrativos y normativos de la universidad, utilizando modelos locales con **Ollama**, embeddings con **Qwen**, y almacenamiento vectorial con **ChromaDB**.

---

## 🚀 Características principales
- **RAG (Retrieval-Augmented Generation) local**: Búsqueda semántica sobre documentos PDF oficiales.
- **Motor de Embeddings**: Configurado con modelos de Qwen optimizados para recuperación de documentos.
- **Base de datos vectorial**: Almacenamiento local persistente utilizando **ChromaDB**.
- **Integración Web**: Capacidades para rastreo de dominios institucionales autorizados.

---

## 🛠️ Requisitos previos
Asegúrate de tener instalado en tu entorno:
- Python 3.10 o superior.
- [Ollama](https://ollama.com/) corriendo localmente con los modelos requeridos descargados.

---

## ⚙️ Instalación y Configuración

1. **Clonar el repositorio** e ingresar a la carpeta del proyecto.

2. **Crear y activar el entorno virtual**:
   ```bash
   python -m venv venv
   # En Windows:
   venv\Scripts\activate
   # En Linux/Mac:
   source venv/bin/activate