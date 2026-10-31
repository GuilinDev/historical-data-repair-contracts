"""Frozen pure tools; evaluation is supplied by the local Docker adapter."""
import ast, hashlib, json, pickletools, shutil
from pathlib import Path
from .common import write_json
def schema(name, desc, props=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': {'type': 'object', 'properties': props or {}, 'required': required or [], 'additionalProperties': False}}}
CORE_TOOLS = [schema('list_sources', "List this candidate's Python source files."), schema('read_source', 'Read source lines from the candidate.', {'path': {'type': 'string'}, 'start_line': {'type': 'integer'}, 'line_count': {'type': 'integer'}}, ['path']), schema('search_source', 'Literal source search, up to 16 matches.', {'query': {'type': 'string'}}, ['query']), schema('source_symbols', 'Find source definitions by optional name substring, with line spans. Useful for generated reader factories.', {'path': {'type': 'string'}, 'query': {'type': 'string'}}, ['path']), schema('historical_cases', 'Describe the public historical data examples and their producer versions.'), schema('historical_artifact', "Inspect a PUBLIC example's exact pickle opcode/state structure without loading it. Hidden inputs are unavailable.", {'id': {'type': 'string'}}, ['id']), schema('apply_edits', 'Atomically apply exact text replacements. Only task-authorized source and tests/test_regression.py may change. Empty old text creates a NEW file only.', {'edits': {'type': 'array', 'items': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'old': {'type': 'string'}, 'new': {'type': 'string'}}, 'required': ['path', 'old', 'new'], 'additionalProperties': False}}}, ['edits']), schema('run_checks', 'Actually execute public checks inside a 1-CPU,1GB,no-network container. current checks current behavior; legacy checks historical examples and current behavior. Hidden inputs are not accessible.', {'suite': {'type': 'string', 'enum': ['current', 'legacy']}}, ['suite'])]
TRANSFER_TOOLS = CORE_TOOLS + [schema('read_historical_source', 'Read immutable producer-version source, not a fixed reader. Same line limits as read_source.', {'version': {'type': 'string'}, 'path': {'type': 'string'}, 'start_line': {'type': 'integer'}, 'line_count': {'type': 'integer'}}, ['version', 'path']), schema('search_historical_source', 'Literal search of immutable old producer source, up to16matches.', {'version': {'type': 'string'}, 'query': {'type': 'string'}}, ['version', 'query'])]
TOOLS = [t for t in TRANSFER_TOOLS if t['function']['name'] != 'list_sources'] + [schema('list_sources', 'List up to200 source paths; optional prefix or substring query, with total match count.', {'prefix': {'type': 'string'}, 'query': {'type': 'string'}})]

class RepairGateway:

    def call(self, name, args):
        if name == 'list_sources':
            return [p.relative_to(self.source).as_posix() for p in sorted(self.source.rglob('*.py'))]
        if name == 'historical_cases':
            rows = json.loads((DATA / 'bundle' / 'fixtures' / self.family / 'visible.json').read_text(encoding='utf-8'))
            return [{k: r[k] for k in ('id', 'group', 'producer', 'input', 'warm', 'artifact') if k in r} for r in rows]
        if name == 'historical_artifact':
            rows = json.loads((DATA / 'bundle' / 'fixtures' / self.family / 'visible.json').read_text(encoding='utf-8'))
            matches = [r for r in rows if r['id'] == args['id'] and r['group'] == 'historical']
            if len(matches) != 1:
                raise ValueError('Only public historical example IDs may be inspected')
            row = matches[0]
            p = DATA / 'bundle' / 'fixtures' / row['artifact'].removeprefix('/fixtures/')
            raw = p.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == row['sha256']
            return {'id': row['id'], 'producer': row['producer'], 'input': row['input'], 'sha256': row['sha256'], 'opcodes': [{'offset': pos, 'opcode': op.name, 'argument': repr(arg)[:180]} for op, arg, pos in pickletools.genops(raw)]}
        if name == 'source_symbols':
            p = (self.source / args['path']).resolve()
            if not p.is_relative_to(self.source.resolve()) or p.suffix != '.py':
                raise ValueError('Source path outside task')
            tree = ast.parse(p.read_text(encoding='utf-8'))
            result = []
            query = args.get('query', '').lower()

            def visit(node, scope):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = '.'.join(scope + [node.name])
                    if query in name.lower():
                        result.append({'name': name, 'line': node.lineno, 'end_line': node.end_lineno})
                    scope = scope + [node.name]
                for child in ast.iter_child_nodes(node):
                    visit(child, scope)
            visit(tree, [])
            return result
        if name == 'read_source':
            path = (self.source / args['path']).resolve()
            if not path.is_relative_to(self.source.resolve()) or path.suffix != '.py':
                raise ValueError('Source path outside task')
            lines = path.read_text(encoding='utf-8').splitlines()
            start = max(1, int(args.get('start_line', 1)))
            count = min(160, max(1, int(args.get('line_count', 100))))
            return {'path': path.relative_to(self.source).as_posix(), 'total_lines': len(lines), 'lines': [{'line': i + 1, 'text': lines[i]} for i in range(start - 1, min(len(lines), start - 1 + count))]}
        if name == 'search_source':
            query = str(args['query']).lower()
            if not query or len(query) > 100:
                raise ValueError('Query length 1-100 required')
            matches = []
            for p in sorted(self.source.rglob('*.py')):
                for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
                    if query in line.lower():
                        matches.append({'path': p.relative_to(self.source).as_posix(), 'line': i, 'text': line[:500]})
                        if len(matches) == 16:
                            return matches
            return matches
        if name == 'apply_edits':
            pending = {}
            for edit in args['edits']:
                rel = edit['path']
                if rel not in TASKS[self.family]['allowed'] + ['tests/test_regression.py']:
                    raise ValueError('Unauthorized edit path')
                p = self.source / rel
                text = pending.get(rel, p.read_text(encoding='utf-8') if p.exists() else None)
                if edit['old'] == '':
                    if text is not None:
                        raise ValueError('Empty old allowed only for a new file')
                    changed = edit['new']
                else:
                    if text is None or text.count(edit['old']) != 1:
                        raise ValueError('Old text must match exactly once')
                    changed = text.replace(edit['old'], edit['new'], 1)
                compile(changed, str(p), 'exec')
                pending[rel] = changed
            for rel, text in pending.items():
                p = self.source / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding='utf-8', newline='\n')
            self.edits.extend(args['edits'])
            write_json(self.out / 'applied_edits.json', self.edits)
            return {'applied_files': list(pending), 'syntax': 'valid'}
        if name == 'run_checks':
            self.checks += 1
            manifest = 'current_visible.json' if args['suite'] == 'current' else 'visible.json'
            result = self.evaluate(manifest, self.out / f'check_{self.checks:02d}')
            return {'execution_status': result['execution_status'], 'passed': result['passed'], 'n': result['n'], 'groups': result['groups'], 'failures': [r for r in result['records'] if not r['passed']][:4], 'submitted_tests': result['submitted_tests']}
        raise ValueError('Unknown tool')

class TransferGateway(RepairGateway):

    def __init__(self, dataset, family, out, *, resume=False):
        self.dataset = dataset
        self.family = family
        self.task = dataset.tasks[family]
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.source = self.out / 'candidate'
        assert not resume, 'New frozen transfer attempts do not resume explored model conversations'
        shutil.copytree(dataset.bundle / 'packages' / family / 'base', self.source)
        self.edits = []
        self.checks = 0

    def call(self, name, args):
        if name in ('list_sources', 'read_source', 'search_source', 'source_symbols'):
            return super().call(name, args)
        if name in ('read_historical_source', 'search_historical_source'):
            version = args['version']
            if version not in self.task['producers']:
                raise ValueError('Only declared historical producers are available')
            root = self.dataset.bundle / 'producers' / self.family / version
            if name == 'read_historical_source':
                path = (root / args['path']).resolve()
                if not path.is_relative_to(root.resolve()) or path.suffix != '.py':
                    raise ValueError('Historical source path outside task')
                lines = path.read_text(encoding='utf-8').splitlines()
                start = max(1, int(args.get('start_line', 1)))
                count = min(160, max(1, int(args.get('line_count', 100))))
                return {'version': version, 'path': args['path'], 'total_lines': len(lines), 'lines': [{'line': i + 1, 'text': lines[i]} for i in range(start - 1, min(len(lines), start - 1 + count))]}
            query = str(args['query']).lower()
            if not query or len(query) > 100:
                raise ValueError('Search query length1-100')
            result = []
            for path in sorted(root.rglob('*.py')):
                for i, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                    if query in line.lower():
                        result.append({'version': version, 'path': path.relative_to(root).as_posix(), 'line': i, 'text': line[:500]})
                        if len(result) == 16:
                            return result
            return result
        visible = json.loads((self.dataset.bundle / 'fixtures' / self.family / 'visible.json').read_text(encoding='utf-8'))
        if name == 'historical_cases':
            return [{k: r[k] for k in ('id', 'group', 'producer', 'input', 'artifact') if k in r} for r in visible]
        if name == 'historical_artifact':
            rows = [r for r in visible if r['id'] == args['id'] and r['group'] == 'historical']
            if len(rows) != 1:
                raise ValueError('Only public historical artifact IDs may be inspected')
            row = rows[0]
            path = self.dataset.bundle / 'fixtures' / Path(row['artifact'].removeprefix('/fixtures/'))
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == row['sha256']
            if self.family == 'jsonpickle':
                state = {'json': json.loads(raw.decode('utf-8'))}
            else:
                state = {'opcodes': [{'offset': pos, 'opcode': op.name, 'argument': repr(arg)[:180]} for op, arg, pos in pickletools.genops(raw)]}
            return {'id': row['id'], 'producer': row['producer'], 'sha256': row['sha256'], **state}
        if name == 'apply_edits':
            pending = {}
            for edit in args['edits']:
                rel = edit['path']
                if rel not in self.task['allowed'] + ['tests/test_regression.py']:
                    raise ValueError('Unauthorized edit path')
                path = self.source / rel
                text = pending.get(rel, path.read_text(encoding='utf-8') if path.exists() else None)
                if edit['old'] == '':
                    if text is not None:
                        raise ValueError('Empty old text is only valid for new files')
                    changed = edit['new']
                else:
                    if text is None or text.count(edit['old']) != 1:
                        raise ValueError('Old text must match exactly once')
                    changed = text.replace(edit['old'], edit['new'], 1)
                compile(changed, str(path), 'exec')
                pending[rel] = changed
            for rel, text in pending.items():
                path = self.source / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8', newline='\n')
            self.edits.extend(args['edits'])
            write_json(self.out / 'applied_edits.json', self.edits)
            return {'applied_files': list(pending), 'syntax': 'valid'}
        if name == 'run_checks':
            self.checks += 1
            manifest = 'current_visible.json' if args['suite'] == 'current' else 'visible.json'
            result = self.evaluate(manifest, self.out / f'check_{self.checks:02d}')
            return {'execution_status': result['execution_status'], 'passed': result['passed'], 'n': result['n'], 'groups': result['groups'], 'failures': [r for r in result['records'] if not r['passed']][:4], 'submitted_tests': result['submitted_tests']}
        raise ValueError('Unknown tool')

class ResourceGateway(TransferGateway):

    def initial(self, condition):
        task = {'request': self.task['request'], 'allowed_source_paths': self.task['allowed'], 'optional_regression_test_path': 'tests/test_regression.py', 'historical_source_versions': self.task['producers'], 'rules': 'Implement a general library repair. Do not hardcode artifact values, IDs, application class names or property names. Only authorized source and optional regression test may change; fixtures, historical source, expected answers and harness are immutable.', 'source_entrypoints': self.task['allowed'], 'source_tree': self.call('list_sources', {'prefix': self.task['package'] + '/', 'query': '__init__'}), 'public_historical_examples': self.call('historical_cases', {})}
        if self.family == 'attrs_slot_state_1085':
            task['application_classes'] = (self.dataset.bundle / 'harness/support/compat_models.py').read_text(encoding='utf-8')
        if self.family == 'sqlalchemy_mutable_8133':
            task['application_classes'] = (self.dataset.bundle / 'harness/support/compat_records.py').read_text(encoding='utf-8')
        return task

    def call(self, name, args):
        if name == 'list_sources':
            prefix = args.get('prefix', '')
            query = args.get('query', '').lower()
            paths = [p.relative_to(self.source).as_posix() for p in sorted(self.source.rglob('*')) if p.is_file() and p.suffix in ('.py', '.pyi', '.pyx', '.pxd')]
            paths = [p for p in paths if p.startswith(prefix) and query in p.lower()]
            return {'matching_files': len(paths), 'paths': paths[:200], 'truncated': len(paths) > 200}
        if name == 'read_source':
            path = (self.source / args['path']).resolve()
            if not path.is_relative_to(self.source.resolve()) or path.suffix not in ('.py', '.pyi', '.pyx', '.pxd'):
                raise ValueError('Source path outside declared candidate')
            lines = path.read_text(encoding='utf-8').splitlines()
            start = max(1, int(args.get('start_line', 1)))
            count = min(160, max(1, int(args.get('line_count', 100))))
            return {'path': path.relative_to(self.source).as_posix(), 'total_lines': len(lines), 'lines': [{'line': i + 1, 'text': lines[i]} for i in range(start - 1, min(len(lines), start - 1 + count))]}
        if name == 'historical_artifact':
            rows = json.loads((self.dataset.bundle / 'fixtures' / self.family / 'visible.json').read_text(encoding='utf-8'))
            selected = [r for r in rows if r['id'] == args['id'] and r['group'] == 'historical']
            if len(selected) != 1:
                raise ValueError('Only public historical artifact IDs may be inspected')
            row = selected[0]
            path = self.dataset.bundle / 'fixtures' / Path(row['artifact'].removeprefix('/fixtures/'))
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == row['sha256']
            if self.task['artifact_format'] == 'json':
                state = {'json': json.loads(raw.decode('utf-8'))}
            elif self.task['artifact_format'] == 'joblib':
                state = {'format': 'joblib array container', 'byte_length': len(raw), 'prefix_hex': raw[:192].hex()}
            else:
                state = {'opcodes': [{'offset': pos, 'opcode': op.name, 'argument': repr(arg)[:180]} for op, arg, pos in pickletools.genops(raw)]}
            return {'id': row['id'], 'producer': row['producer'], 'sha256': row['sha256'], **state}
        return super().call(name, args)
