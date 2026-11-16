"""Эмбеддинги документов своей области -> Qdrant (коллекция chunks_500).

Самодостаточный шаг: читает .md из локальной папки documents/<область>/ (без MinIO),
режет на чанки (окно 500 символов, перекрытие 40), считает эмбеддинги MiniLM (384d)
и пишет в Qdrant коллекцию chunks_500. Коллекция пересоздаётся, чтобы в ней не оставалось
чужих документов. Файлы не вида NN-название.md (напр. README.md) пропускаются.

Запуск (внутри python-контейнера):
  docker exec sr-py python /app/embed.py --domain oil-gas

Первый запуск качает модель MiniLM (~470 МБ) в кэш hf-cache — разово.
"""
import argparse
import os
import re

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

QDRANT_HOST, QDRANT_PORT = "host.docker.internal", 6334
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384
CHUNK_SIZE = 500
OVERLAP = 40
COLLECTION = f"chunks_{CHUNK_SIZE}"


def chunk(text, size, overlap):
    """Режем текст на куски по `size` символов с перекрытием `overlap`."""
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + size])
        start += size - overlap
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True, help="oil-gas | metallurgy | chemical | cs")
    args = ap.parse_args()

    folder = f"/app/documents/{args.domain}"
    if not os.path.isdir(folder):
        print(f"[embed] папка {folder} не найдена")
        return
    files = sorted(f for f in os.listdir(folder) if f.endswith(".md"))
    if not files:
        print(f"[embed] в {folder} нет .md")
        return

    print("[embed] загружаю модель MiniLM (первый запуск качает веса)...")
    model = SentenceTransformer(MODEL_NAME)

    qd = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    if qd.collection_exists(COLLECTION):
        qd.delete_collection(COLLECTION)
    qd.create_collection(COLLECTION,
                         vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))

    points, chunk_id, used = [], 1, 0
    for name in files:
        m = re.match(r"^(\d+)", name)
        if not m:
            print(f"[embed] пропущен (имя не вида NN-название.md): {name}")
            continue
        used += 1
        doc_id = int(m.group(1))
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            text = f.read()
        for i, piece in enumerate(chunk(text, CHUNK_SIZE, OVERLAP)):
            points.append(PointStruct(
                id=chunk_id,
                vector=model.encode(piece).tolist(),
                payload={
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "chunk_text": piece,
                    "num_chars": len(piece),
                },
            ))
            chunk_id += 1

    qd.upsert(collection_name=COLLECTION, points=points)
    print(f"[embed] {chunk_id - 1} чанков из {used} документов -> Qdrant '{COLLECTION}'")


if __name__ == "__main__":
    main()
