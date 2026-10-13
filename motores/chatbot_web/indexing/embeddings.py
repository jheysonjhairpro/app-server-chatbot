async def embed_chunks(ollama, chunks, batch_size):
    vectors = []
    for i in range(0, len(chunks), batch_size):
        vectors.extend(
            await ollama.embed([c["texto"] for c in chunks[i : i + batch_size]])
        )
    return vectors
