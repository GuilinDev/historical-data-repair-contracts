"""Unchanged frozen host-only comparison functions; no oracle writer."""
import copy, json
from collections import defaultdict
class TransferDataset:

    def judge(self, family, actual, manifest):
        reference = json.loads((self.path / 'oracle' / (family + '.json')).read_text(encoding='utf-8'))
        selected = {r['id'] for r in json.loads((self.bundle / 'fixtures' / family / manifest).read_text(encoding='utf-8'))}
        found = {r['id']: r for r in actual.get('records', [])}
        records = []
        groups = defaultdict(lambda: {'passed': 0, 'n': 0})
        for expected in reference['records']:
            if expected['id'] not in selected:
                continue
            row = found.get(expected['id'])
            initial = bool(row and row.get('status') == 'observed' and (row.get('semantics') == expected['semantics']))
            restart = bool(row and row.get('restart_semantics') == expected['restart_semantics'])
            result = {'id': expected['id'], 'group': expected['group'], 'initial_semantics_passed': initial, 'restart_semantics_passed': restart, 'passed': initial and restart, 'actual': row}
            records.append(result)
            groups[result['group']]['n'] += 1
            groups[result['group']]['passed'] += result['passed']
        return {'execution_status': actual['status'], 'passed': sum((r['passed'] for r in records)), 'n': len(records), 'groups': dict(groups), 'records': records}

class ResourceDataset(TransferDataset):

    @staticmethod
    def normalize_numeric(row, metadata):
        result = copy.deepcopy(row)
        for key in ('semantics', 'restart_semantics'):
            for snapshot in result.get(key, {}).values():
                if not isinstance(snapshot, dict) or 'dtypes' not in snapshot:
                    continue
                for name in metadata.get('ambiguous_columns', []):
                    dtype = snapshot['dtypes'].get(name)
                    original = metadata['input']['numeric'][name]['dtype']
                    minimum = int(''.join((c for c in original if c.isdigit()))) // 8
                    if dtype and dtype['itemsize'] >= minimum:
                        dtype['itemsize'] = 'permitted_widening'
        return result

    def judge(self, task, actual, manifest):
        if task != 'jsonpickle_dtype_634':
            return super().judge(task, actual, manifest)
        metadata = {r['id']: r for r in json.loads((self.bundle / 'fixtures' / task / 'all.json').read_text(encoding='utf-8'))}
        reference = json.loads((self.path / 'oracle' / (task + '.json')).read_text(encoding='utf-8'))
        normalized_actual = {**actual, 'records': [self.normalize_numeric(r, metadata[r['id']]) for r in actual.get('records', [])]}
        selected = {r['id'] for r in json.loads((self.bundle / 'fixtures' / task / manifest).read_text(encoding='utf-8'))}
        found = {r['id']: r for r in normalized_actual['records']}
        records = []
        groups = {}
        for raw in reference['records']:
            if raw['id'] not in selected:
                continue
            expected = self.normalize_numeric(raw, metadata[raw['id']])
            row = found.get(raw['id'])
            initial = bool(row and row.get('status') == 'observed' and (row.get('semantics') == expected['semantics']))
            restart = bool(row and row.get('restart_semantics') == expected['restart_semantics'])
            record = {'id': raw['id'], 'group': raw['group'], 'initial_semantics_passed': initial, 'restart_semantics_passed': restart, 'passed': initial and restart, 'actual': actual.get('records', [])[next((i for i, r in enumerate(actual['records']) if r['id'] == raw['id']))] if row else None}
            records.append(record)
            group = groups.setdefault(raw['group'], {'n': 0, 'passed': 0})
            group['n'] += 1
            group['passed'] += record['passed']
        return {'execution_status': actual['status'], 'passed': sum((r['passed'] for r in records)), 'n': len(records), 'groups': groups, 'records': records}
