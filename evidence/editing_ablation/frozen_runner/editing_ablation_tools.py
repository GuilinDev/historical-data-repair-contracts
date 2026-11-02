"""Isolated editor intervention; frozen resource and original runners unchanged."""
import copy
import hashlib
from pathlib import Path

from freeze_and_build import write_json
from model_pilot import schema
from resource_task_tools import ResourceGateway, TOOLS


def version(text):
    return 'new' if text is None else hashlib.sha256(text.encode('utf-8')).hexdigest()


def tools_for(arm):
    tools = copy.deepcopy(TOOLS)
    for tool in tools:
        if tool['function']['name'] == 'read_source':
            tool['function']['description'] += ' Includes source_version, a SHA-256 of the complete normalized UTF-8 text.'
    if arm == 'exact':
        return tools
    assert arm == 'span'
    tools = [t for t in tools if t['function']['name'] != 'apply_edits']
    tools.append(schema('apply_edits',
        'Atomically replace inclusive 1-based line spans. Every source_version must equal the current file hash returned by read_source. '
        'All spans in one file refer to the same original version and must not overlap. new is the full replacement text, including needed newlines. '
        'Use start_line=end_line=0 and source_version="new" only to create an absent authorized file. '
        'Only task-authorized source and tests/test_regression.py may change; all changed Python files must parse.',
        {'edits': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'path': {'type': 'string'}, 'source_version': {'type': 'string'},
            'start_line': {'type': 'integer'}, 'end_line': {'type': 'integer'},
            'new': {'type': 'string'}},
            'required': ['path', 'source_version', 'start_line', 'end_line', 'new'],
            'additionalProperties': False}}}, ['edits']))
    return tools


class AblationGateway(ResourceGateway):
    def __init__(self, dataset, family, out, *, arm, resume=False):
        self.arm = arm
        super().__init__(dataset, family, out, resume=resume)

    def call(self, name, args):
        if name == 'read_source':
            result = super().call(name, args)
            path = self.source / result['path']
            result['source_version'] = version(path.read_text(encoding='utf-8'))
            return result
        if name != 'apply_edits' or self.arm == 'exact':
            return super().call(name, args)
        grouped = {}
        for edit in args['edits']:
            rel = edit['path']
            if rel not in self.task['allowed'] + ['tests/test_regression.py']:
                raise ValueError('Unauthorized edit path')
            path = (self.source / rel).resolve()
            if not path.is_relative_to(self.source.resolve()):
                raise ValueError('Unauthorized edit path')
            grouped.setdefault(rel, []).append(edit)
        pending = {}
        for rel, edits in grouped.items():
            path = self.source / rel
            text = path.read_text(encoding='utf-8') if path.exists() else None
            for edit in edits:
                if edit['source_version'] != version(text):
                    raise ValueError('Stale source_version; read the current source before editing')
                if type(edit['start_line']) is not int or type(edit['end_line']) is not int:
                    raise ValueError('Integer line coordinates required')
            if text is None:
                if len(edits) != 1 or edits[0]['start_line'] != 0 or edits[0]['end_line'] != 0:
                    raise ValueError('New file requires exactly one 0..0 span')
                changed = edits[0]['new']
            else:
                lines = text.splitlines(keepends=True)
                last = 0
                ordered = sorted(edits, key=lambda e: e['start_line'])
                for edit in ordered:
                    first, end = edit['start_line'], edit['end_line']
                    if not 1 <= first <= end <= len(lines):
                        raise ValueError('Line span outside current file')
                    if first <= last:
                        raise ValueError('Overlapping line spans')
                    last = end
                for edit in reversed(ordered):
                    lines[edit['start_line'] - 1:edit['end_line']] = [edit['new']]
                changed = ''.join(lines)
            compile(changed, str(path), 'exec')
            pending[rel] = changed
        for rel, text in pending.items():
            path = self.source / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        self.edits.extend(copy.deepcopy(args['edits']))
        write_json(self.out / 'applied_edits.json', self.edits)
        return {'applied_files': list(pending), 'syntax': 'valid'}
