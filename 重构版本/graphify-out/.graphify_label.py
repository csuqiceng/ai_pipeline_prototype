"""Step 5: regenerate report with community labels."""
import json
from pathlib import Path
from graphify.build import build_from_json
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding='utf-8'))
detection = json.loads(Path('graphify-out/.graphify_detect.json').read_text(encoding='utf-8'))
analysis = json.loads(Path('graphify-out/.graphify_analysis.json').read_text(encoding='utf-8'))

G = build_from_json(extraction)
communities = {int(k): v for k, v in analysis['communities'].items()}
cohesion = {int(k): v for k, v in analysis['cohesion'].items()}
tokens = {'input': extraction.get('input_tokens', 0), 'output': extraction.get('output_tokens', 0)}

# Meaningful labels for the largest 55 communities (covers ~85% of nodes)
labels = {cid: f'Community {cid}' for cid in communities}
named = {
    0: 'System Config & Precheck',
    1: 'Agent Templates & Compound Cmds',
    2: 'Voice NLP Adapter Core',
    3: 'Operator UI Formatting',
    4: 'Address Resolver',
    5: 'Agent Runtime & LangChain',
    6: 'Safety Review Agent',
    7: 'Alarm & Execution Monitor',
    8: 'Alarm Advice Book',
    9: 'Fixed VR Command & Flow Registry',
    10: 'Local Tool Registry',
    11: 'Qt Widgets Shell',
    12: 'Operator UI Chat & Position',
    13: 'Mock Controller',
    14: 'Compound Command Coordinator',
    15: 'Six-Axis Exceptions',
    16: 'Response Builder',
    17: 'Alarm Explanation Agent',
    18: 'Operator Agent Bridge',
    19: 'Broadcast Queue',
    20: 'Feedback Learner',
    21: 'Engineer Voice Commands',
    22: 'Atomic Template & Memory',
    23: 'Orchestrator & Chat Explanation',
    24: 'Memory Manager & Position Registry',
    25: 'Flow Store & Query Table',
    26: 'Operator UI Cmd Handling',
    27: 'ZAUXDLL Python Bindings',
    28: 'Confirmation Agent',
    29: 'Voice IPC',
    30: 'Flow Registry & Management',
    31: 'DeepSeek Tool Decider',
    32: 'Tool Runner Init',
    33: 'GUI Main Boot',
    34: 'ZMotion Client Models',
    35: 'API Client',
    36: 'L1/L2 Safety Runners',
    37: 'App State & Qt Main Window',
    38: 'Operator UI Cards & Chat Rows',
    39: 'Doubao Streaming ASR',
    40: 'Precheck Helpers Tests',
    41: 'Flow Tools',
    42: 'Clarification & Draft Editor',
    43: 'Operator UI NLP Execution',
    44: 'Knowledge Base Assistant',
    45: 'Voice NLP Adapter Class',
    46: 'Command Tools',
    47: 'LLM Fallback Agent',
    48: 'Atomic Template Resolver',
    49: 'Mock ZMotion VR Client',
    50: 'Voice NLP Schema',
    51: 'Memory Setting Agent',
    52: 'Orchestrator Result & Plan Adapter',
    53: 'JSON Schema & Dashboard Push',
    54: 'Six-Axis Command Model',
}
labels.update(named)

questions = suggest_questions(G, communities, labels)
report = generate(
    G, communities, cohesion, labels,
    analysis['gods'], analysis['surprises'], detection, tokens, '.',
    suggested_questions=questions,
)
Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
Path('graphify-out/.graphify_labels.json').write_text(
    json.dumps({str(k): v for k, v in labels.items()}, ensure_ascii=False),
    encoding='utf-8',
)
print(f'Report updated. Named {len(named)} of {len(communities)} communities.')
