import json
from pathlib import Path
from collections import defaultdict

detect = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8'))
files_by_type = detect['files']

image_files = files_by_type.get('image', [])
doc_files = files_by_type.get('document', []) + files_by_type.get('paper', [])

print(f'Semantic targets: {len(doc_files)} docs/papers, {len(image_files)} images')

def top_dir(p):
    # Normalize separators and grab directory just above the file
    rel = p.replace('\\', '/')
    parts = rel.rsplit('/', 2)
    if len(parts) >= 2:
        return parts[-2]
    return 'root'

groups = defaultdict(list)
for f in doc_files:
    groups[top_dir(f)].append(f)

chunks = []
current = []
for d, files in sorted(groups.items()):
    for f in files:
        current.append(f)
        if len(current) >= 22:
            chunks.append(current)
            current = []
if current:
    chunks.append(current)

for img in image_files:
    chunks.append([img])

print(f'Total chunks: {len(chunks)}')
print(f'  doc chunks: {len(chunks) - len(image_files)}')
print(f'  image chunks: {len(image_files)}')

Path('graphify-out/.graphify_chunks.json').write_text(
    json.dumps(chunks, ensure_ascii=False), encoding='utf-8'
)
print(f'Estimated time: ~{len(chunks) * 45}s if serial, ~{max(1,(len(chunks)+4)//5) * 45}s with 5-parallel')
