"""Merge graphify chunks -> cache -> semantic -> extract."""
import json
import glob
from pathlib import Path

# Step B3: collect chunks
chunks = sorted(glob.glob('graphify-out/.graphify_chunk_*.json'))
all_nodes, all_edges, all_hyperedges = [], [], []
total_in, total_out = 0, 0
for c in chunks:
    d = json.loads(Path(c).read_text(encoding='utf-8'))
    all_nodes += d.get('nodes', [])
    all_edges += d.get('edges', [])
    all_hyperedges += d.get('hyperedges', [])
    total_in += d.get('input_tokens', 0)
    total_out += d.get('output_tokens', 0)

Path('graphify-out/.graphify_semantic_new.json').write_text(
    json.dumps({
        'nodes': all_nodes,
        'edges': all_edges,
        'hyperedges': all_hyperedges,
        'input_tokens': total_in,
        'output_tokens': total_out,
    }, indent=2, ensure_ascii=False),
    encoding='utf-8',
)
print(f'Merged {len(chunks)} chunks: {len(all_nodes)} nodes, {len(all_edges)} edges, {len(all_hyperedges)} hyperedges')

# Merge cached + new
cached_path = Path('graphify-out/.graphify_cached.json')
cached = {'nodes': [], 'edges': [], 'hyperedges': []}
if cached_path.exists():
    cached = json.loads(cached_path.read_text(encoding='utf-8'))

new = json.loads(Path('graphify-out/.graphify_semantic_new.json').read_text(encoding='utf-8'))
combined_nodes = cached.get('nodes', []) + new.get('nodes', [])
combined_edges = cached.get('edges', []) + new.get('edges', [])
combined_hyper = cached.get('hyperedges', []) + new.get('hyperedges', [])

seen = set()
deduped = []
for n in combined_nodes:
    if n['id'] not in seen:
        seen.add(n['id'])
        deduped.append(n)

merged = {
    'nodes': deduped,
    'edges': combined_edges,
    'hyperedges': combined_hyper,
    'input_tokens': new.get('input_tokens', 0),
    'output_tokens': new.get('output_tokens', 0),
}
Path('graphify-out/.graphify_semantic.json').write_text(
    json.dumps(merged, indent=2, ensure_ascii=False),
    encoding='utf-8',
)
print(f'Semantic merge: {len(deduped)} unique nodes ({len(cached.get("nodes",[]))} cached + {len(new.get("nodes",[]))} new), {len(combined_edges)} edges')

# Step C: merge AST + semantic
ast = json.loads(Path('graphify-out/.graphify_ast.json').read_text(encoding='utf-8'))
sem = merged

seen2 = {n['id'] for n in ast['nodes']}
final_nodes = list(ast['nodes'])
for n in sem['nodes']:
    if n['id'] not in seen2:
        final_nodes.append(n)
        seen2.add(n['id'])

final_edges = ast['edges'] + sem['edges']
final_hyper = sem.get('hyperedges', [])
final = {
    'nodes': final_nodes,
    'edges': final_edges,
    'hyperedges': final_hyper,
    'input_tokens': sem.get('input_tokens', 0),
    'output_tokens': sem.get('output_tokens', 0),
}
Path('graphify-out/.graphify_extract.json').write_text(
    json.dumps(final, indent=2, ensure_ascii=False),
    encoding='utf-8',
)
print(f'Final extract: {len(final_nodes)} nodes ({len(ast["nodes"])} AST + {len(sem["nodes"])} semantic), {len(final_edges)} edges')
