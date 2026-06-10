"""Step 6: generate HTML viz."""
import json
from pathlib import Path
from graphify.build import build_from_json
from graphify.export import to_html

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding='utf-8'))
analysis = json.loads(Path('graphify-out/.graphify_analysis.json').read_text(encoding='utf-8'))
labels = json.loads(Path('graphify-out/.graphify_labels.json').read_text(encoding='utf-8'))
labels = {int(k): v for k, v in labels.items()}

G = build_from_json(extraction)
communities = {int(k): v for k, v in analysis['communities'].items()}
member_counts = {cid: len(members) for cid, members in communities.items()}

# Auto-aggregate if too large
to_html(G, communities, 'graphify-out/graph.html', community_labels=labels, member_counts=member_counts, node_limit=5000)
print(f'HTML written: graphify-out/graph.html ({Path("graphify-out/graph.html").stat().st_size:,} bytes)')
