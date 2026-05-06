import os
import aoss_chat_bedrock as m

host = f"{os.environ['AOSS_ID']}.{os.environ['AWS_REGION']}.aoss.amazonaws.com:443"
index_name = os.environ['AOSS_INDEX_NAME']

qa = m.build_chain(host, index_name)
result = m.run_chain(qa, "explain Generic Process Model", [])

print("ANSWER_OK", bool(result.get("answer")))
sources = result.get("source_documents", [])
print("SOURCES", len(sources))

for i, d in enumerate(sources[:10], 1):
    md = getattr(d, "metadata", {}) or {}
    print(
        i,
        md.get("source"),
        "chunk=", md.get("chunk"),
        "img=", len(md.get("image_uris") or []),
        "chunk_img=", len(md.get("chunk_image_uris") or []),
    )
