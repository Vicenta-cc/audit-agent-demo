"""Content-addressed geographic execution records in the existing SQLite transaction.

An execution row contains the roots of its current state and compact query
summaries. Operation payloads and immutable results live in a separate ledger.
A changed candidate replaces only its path to the root. Unchanged evidence,
including the administrative catalog, keeps its original content address.
"""
from collections.abc import ItemsView, MutableMapping, MutableSequence, ValuesView
import hashlib
import json

from .geographic_query_summary import make_query_summary


FORMAT = 'geographic-records-v1'
_OPAQUE = frozenset({'admin_catalog', 'catalog', 'risk_config', 'config', 'mention_profile'})


def init_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS geographic_objects '
               '(hash TEXT PRIMARY KEY, body BLOB NOT NULL) WITHOUT ROWID')
    db.execute('CREATE TABLE IF NOT EXISTS geographic_operations '
               '(execution_id TEXT NOT NULL, revision INTEGER NOT NULL, operation_id TEXT NOT NULL, '
               'entry_hash TEXT NOT NULL, PRIMARY KEY(execution_id,revision))')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS geographic_operation_keys '
               'ON geographic_operations(execution_id,operation_id) WHERE revision>0')
    db.execute('CREATE TABLE IF NOT EXISTS geographic_legacy_backups '
               '(execution_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)')


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def is_segmented(db, execution_id):
    # Only examine the short format prefix. A legacy row can exceed 200 MiB.
    row = db.execute('SELECT rowid FROM geographic_executions WHERE execution_id=?',
                     (execution_id,)).fetchone()
    if row is None:
        return False
    with db.blobopen('geographic_executions', 'snapshot', row[0], readonly=True) as blob:
        return blob.read(48).startswith(b'{"storage_format":"geographic-records-v1",')


def header(db, execution_id):
    row = db.execute('SELECT snapshot FROM geographic_executions WHERE execution_id=?',
                     (execution_id,)).fetchone()
    if row is None:
        raise ValueError('unknown execution')
    data = json.loads(row[0])
    checksum = data.pop('header_digest', None)
    if (data.get('storage_format') != FORMAT or data.get('execution_id') != execution_id
            or checksum != _hash(_bytes(data))):
        raise ValueError('snapshot binding or integrity mismatch')
    return data


def write_header(db, h):
    body = _bytes({**h, 'header_digest': _hash(_bytes(h))}).decode()
    db.execute('INSERT INTO geographic_executions VALUES(?,?) '
               'ON CONFLICT(execution_id) DO UPDATE SET snapshot=excluded.snapshot',
               (h['execution_id'], body))


def materialize(value):
    if isinstance(value, dict):
        return {k: materialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [materialize(v) for v in value]
    if isinstance(value, tuple):
        return tuple(materialize(v) for v in value)
    return value


class _Link:
    __slots__ = ('hash',)
    def __init__(self, checksum):
        self.hash = checksum


class _Node:
    def _bind(self, tree, path, parent, origin):
        self.tree = tree
        self.path = path
        self.parent_changed = parent
        self.origin = origin
        self.dirty = False

    def _changed(self):
        if not self.dirty:
            self.dirty = True
            if self.parent_changed is not None:
                self.parent_changed()

    def _value(self, key, value):
        path = (*self.path, key)
        if isinstance(value, _Link):
            return self.tree.node(value.hash, path, self._changed)
        if type(value) is dict or type(value) is list:
            return self.tree.wrap(value, path, self._changed, None)
        return value

    def __deepcopy__(self, memo):
        return materialize(self)

    def __eq__(self, other):
        if self is other:
            return True
        return materialize(self) == materialize(other)

    def __ne__(self, other):
        return not self == other


class _NodeDict(_Node, dict):
    # Use the mapping operations that dispatch through __set/delitem__, so
    # changes cannot silently bypass ancestor dirty tracking.
    pop = MutableMapping.pop
    popitem = MutableMapping.popitem
    clear = MutableMapping.clear

    def __ior__(self, other):
        self.update(other)
        return self

    def __iter__(self):
        # dict(node) and {**node} must resolve links through __getitem__ too.
        return dict.__iter__(self)

    def copy(self):
        return {key: self[key] for key in self}

    def __getitem__(self, key):
        current = dict.__getitem__(self, key)
        value = self._value(key, current)
        if value is not current:
            dict.__setitem__(self, key, value)
        return value

    def __setitem__(self, key, value):
        self._changed()
        # Transitions own their input values; detach references to other nodes.
        dict.__setitem__(self, key, materialize(value) if isinstance(value, _Node) else value)

    def __delitem__(self, key):
        self._changed()
        dict.__delitem__(self, key)

    def get(self, key, default=None):
        return self[key] if key in self else default

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def items(self):
        return ItemsView(self)

    def values(self):
        return ValuesView(self)

    def __or__(self, other):
        return materialize(self) | materialize(other)

    def __ror__(self, other):
        return materialize(other) | materialize(self)

    def summary(self, qid):
        if self.path != ('queries',):
            raise ValueError('summary requested for non-query collection')
        return self.tree.summary(self, qid)

    @property
    def verified_occurrences(self):
        return self.path == ('queries',)


class _NodeList(_Node, list):
    pop = MutableSequence.pop
    remove = MutableSequence.remove
    clear = MutableSequence.clear
    extend = MutableSequence.extend
    reverse = MutableSequence.reverse
    __iadd__ = MutableSequence.__iadd__

    def __setitem__(self, key, value):
        self._changed()
        list.__setitem__(self, key, materialize(value))

    def __delitem__(self, key):
        self._changed()
        list.__delitem__(self, key)

    def insert(self, index, value):
        self._changed()
        list.insert(self, index, materialize(value))

    def __imul__(self, n):
        self._changed()
        return list.__imul__(self, n)

    def sort(self, **kwargs):
        self[:] = sorted(self, **kwargs)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return [self[i] for i in range(*key.indices(len(self)))]
        current = list.__getitem__(self, key)
        value = self._value(key, current)
        if value is not current:
            list.__setitem__(self, key, value)
        return value

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]

    def __contains__(self, obj):
        return any(v == obj for v in self)

    def append(self, value):
        self._changed()
        list.append(self, value)


class Tree:
    def __init__(self, db, summaries=None):
        self.db = db
        self.summaries = dict(summaries or {})
        self._summary_cache = {}

    def put(self, body):
        data = _bytes(body)
        key = _hash(data)
        self.db.execute('INSERT OR IGNORE INTO geographic_objects VALUES(?,?)', (key, data))
        return key

    def body(self, key):
        row = self.db.execute('SELECT body FROM geographic_objects WHERE hash=?', (key,)).fetchone()
        if row is None or _hash(row[0]) != key:
            raise ValueError('snapshot binding or integrity mismatch')
        return json.loads(row[0])

    def wrap(self, value, path, parent, origin):
        if isinstance(value, dict):
            node = _NodeDict(value)
        elif isinstance(value, list):
            node = _NodeList(value)
        else:
            return value
        node._bind(self, path, parent, origin)
        return node

    @staticmethod
    def _decode_member(wire):
        if not isinstance(wire, list):
            return wire
        tag, data = wire
        if tag == 'ref':
            return _Link(data)
        if tag == 'literal':
            return data
        raise ValueError('invalid geographic storage record')

    def node(self, key, path=(), parent=None):
        kind, payload = self.body(key)
        if kind == 'map':
            value = {k: self._decode_member(v) for k, v in payload.items()}
        elif kind == 'list':
            value = [self._decode_member(v) for v in payload]
        elif kind == 'literal':
            value = payload
        else:
            raise ValueError('invalid geographic storage record')
        return self.wrap(value, path, parent, key)

    def pack(self, value, field=None):
        if isinstance(value, _Link):
            return value.hash
        if isinstance(value, _Node) and value.origin and not value.dirty:
            return value.origin
        if field in _OPAQUE or not isinstance(value, (dict, list, tuple)):
            return self.put(['literal', materialize(value)])
        if isinstance(value, dict):
            members = dict.items(value)
            return self.put(['map', {k: self._member(v, k) for k, v in members}])
        members = list.__iter__(value) if isinstance(value, list) else iter(value)
        return self.put(['list', [self._member(v, None) for v in members]])

    def _member(self, value, field):
        if isinstance(value, (_Link, _Node)):
            return ['ref', self.pack(value, field)]
        if not isinstance(value, (dict, tuple, list)):
            return value
        # Inline empty collections and small scalar vectors. Their owner tracks
        # later edits, but a simple progress read needs no extra SQLite lookup.
        if not value or (isinstance(value, (tuple, list)) and len(value) <= 8
                         and all(not isinstance(v, (dict, tuple, list)) for v in value)):
            return ['literal', value]
        return ['ref', self.pack(value, field)]

    @staticmethod
    def _query_hash(queries, qid):
        value = dict.__getitem__(queries, qid)
        if isinstance(value, _Link):
            return value.hash
        if isinstance(value, _Node) and not value.dirty:
            return value.origin
        return None

    def summary(self, queries, qid):
        ref = self._query_hash(queries, qid)
        if not ref:
            raise ValueError('query summary is not committed')
        key = self.summaries[qid]
        if key not in self._summary_cache:
            bound, compact = self.body(key)
            if bound != ref:
                raise ValueError('query summary binding mismatch')
            self._summary_cache[key] = compact
        return self._summary_cache[key]

    def save(self, snap):
        queries = snap['queries']
        for qid in queries:
            if qid in self.summaries and self._query_hash(queries, qid):
                continue
            query = queries[qid]
            ref = self.pack(query)
            summary = make_query_summary(snap['plan'], qid, query)
            self.summaries[qid] = self.put([ref, summary])
            # Install the exact persisted root even for an inline empty value.
            if isinstance(queries, _NodeDict):
                dict.__setitem__(queries, qid, _Link(ref))
        return self.pack(snap)


def new_header(execution_id):
    return {'storage_format': FORMAT, 'execution_id': execution_id, 'revision': -1,
            'root': '', 'summaries': {}, 'ledger': ''}


def append_entry(tree, h, revision, operation_id, payload, fingerprint, result):
    entry = {'execution_id': h['execution_id'], 'revision': revision, 'operation_id': operation_id,
             'previous': h['ledger'], 'payload': tree.pack(payload), 'fingerprint': fingerprint,
             'result': tree.pack(result)}
    key = tree.put(entry)
    tree.db.execute('INSERT INTO geographic_operations VALUES(?,?,?,?)',
                    (h['execution_id'], revision, operation_id, key))
    h['ledger'] = key
    h['revision'] = revision


def checked_entry(tree, key, execution_id, revision, operation_id):
    entry = tree.body(key)
    if (entry['execution_id'], entry['revision'], entry['operation_id']) != (execution_id, revision, operation_id):
        raise ValueError('operation binding mismatch')
    return entry
