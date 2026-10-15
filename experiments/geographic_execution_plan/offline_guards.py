"""Process-local offline test guards; install before pytest collection.

These are an accidental-side-effect boundary, not a hostile-code sandbox.
Python children inherit the runner's sitecustomize bootstrap and the same roots.
"""
from __future__ import annotations
import importlib.abc
import importlib.machinery
import json
import os
import re
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit

ACTIVE = None
TARGETS = {
    'audit_agent.remote_inference': {'RemoteInferenceClient': ('audit_text','analyze_image','transcribe','mms_transcribe','translate','ocr_image')},
    'audit_agent.qwen_client': {'QwenClient': ('audit_text','analyze_image','_post_chat')},
    'investigation.qwen_chat_tool_client': {'QwenChatToolClient': ('complete',)},
    'reporting.qwen_report_client': {'QwenReportClient': ('generate_structured','_request')},
    'audit_agent.crawler_adapter': {'MediaCrawlerAdapter': ('run_search','run_creator','run_detail','_run_command','_run_command_locked','run_xhs_search')},
    'task_admission.scheduler': {None: ('dispatch',)},
}

class OfflineAccessBlocked(RuntimeError):
    pass

class _Loader(importlib.abc.Loader):
    def __init__(self, original, guard): self.original, self.guard = original, guard
    def create_module(self, spec):
        return self.original.create_module(spec) if hasattr(self.original,'create_module') else None
    def exec_module(self, module):
        self.original.exec_module(module)
        self.guard.patch_module(module)

class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, guard): self.guard=guard
    def find_spec(self, fullname, path=None, target=None):
        if not any(fullname==name or fullname=='backend.'+name for name in TARGETS): return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path)
        if spec is not None and spec.loader is not None: spec.loader=_Loader(spec.loader,self.guard)
        return spec

class OfflineGuards:
    def __init__(self, *, experiment_root: Path, allowed_roots=(), event_log: Path | None=None):
        self.experiment_root=Path(experiment_root).resolve()
        self.allowed_roots=tuple(Path(p).resolve() for p in (tempfile.gettempdir(),'/tmp','/var/tmp',*allowed_roots,self.experiment_root))
        self.event_log=Path(event_log) if event_log else None
        self.attempts=[]
        self._patches=[]
        self.finder=_Finder(self)
    def block(self, kind, target):
        event={'pid':os.getpid(),'kind':kind,'target':str(target),'blocked':True,'actual_succeeded':False}
        self.attempts.append(event)
        if self.event_log:
            with self.event_log.open('a',encoding='utf-8') as stream: stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        raise OfflineAccessBlocked('OFFLINE_BLOCKED:'+kind+':'+str(target))
    def _patch(self, obj, name, replacement):
        self._patches.append((obj,name,getattr(obj,name)))
        setattr(obj,name,replacement)
    def patch_module(self,module):
        name=module.__name__.removeprefix('backend.')
        for owner,methods in TARGETS.get(name,{}).items():
            target=getattr(module,owner) if owner else module
            for method in methods:
                original=getattr(target,method)
                def denied(*args,_target=name+'.'+(owner+'.' if owner else '')+method,
                           _original=original,_module=module,_method=method,**kwargs):
                    # Preserve the existing explicit no-provider local mock contract.
                    # Enabled providers and every real transport entry remain blocked.
                    if (_module.__name__.endswith('audit_agent.qwen_client') and _method=='audit_text'
                            and args and args[0] is not None
                            and not _module.settings.use_remote_llm and args[0].enabled is False):
                        return _original(*args,**kwargs)
                    return self.block('model' if ('qwen' in name or 'remote_inference' in name) else 'collection' if 'crawler' in name else 'dispatch',_target)
                denied._offline_guard=True
                self._patch(target,method,denied)
    def install(self):
        original=sqlite3.connect
        def check_database(database):
            value=os.fsdecode(database)
            if value!=':memory:':
                # SQLite URI filenames must obey the same resolved-path boundary.
                if value.startswith('file:'):
                    parsed=urlsplit(value)
                    if parsed.netloc not in ('','localhost'): self.block('sqlite',value)
                    value=unquote(parsed.path)
                path=Path(value).resolve()
                if not any(path.is_relative_to(root) for root in self.allowed_roots): self.block('sqlite',path)
        @contextmanager
        def bound_attachment(connection, sql, parameters):
            match=re.match(r'^\s*ATTACH\s+(?:DATABASE\s+)?(\?\d*|[:@$]\w+)\s+AS\b',sql,re.I)
            previous=getattr(connection,'_offline_attach_depth',0)
            if match:
                token=match[1]
                if token.startswith('?'):
                    value=parameters[int(token[1:])-1 if token[1:] else 0]
                else:
                    value=parameters[token[1:]]
                check_database(value)
                connection._offline_attach_depth=previous+1
            try: yield
            finally: connection._offline_attach_depth=previous
        cursor_types={}
        def guarded_cursor_type(base):
            if base not in cursor_types:
                class GuardedCursor(base):
                    def execute(cursor,sql,parameters=()):
                        with bound_attachment(cursor.connection,sql,parameters):
                            return super().execute(sql,parameters)
                    def executemany(cursor,sql,parameters):
                        if re.match(r'^\s*ATTACH\b',sql,re.I):
                            self.block('sqlite','ATTACH executemany unsupported')
                        return super().executemany(sql,parameters)
                cursor_types[base]=GuardedCursor
            return cursor_types[base]
        connection_types={}
        def guarded_connection_type(base):
            if base not in connection_types:
                class GuardedConnection(base):
                    def execute(connection,sql,parameters=()):
                        with bound_attachment(connection,sql,parameters):
                            return super().execute(sql,parameters)
                    def executemany(connection,sql,parameters):
                        if re.match(r'^\s*ATTACH\b',sql,re.I):
                            self.block('sqlite','ATTACH executemany unsupported')
                        return super().executemany(sql,parameters)
                    def cursor(connection,factory=sqlite3.Cursor):
                        return super().cursor(factory=guarded_cursor_type(factory))
                connection_types[base]=GuardedConnection
            return connection_types[base]
        def connect(database,*args,**kwargs):
            check_database(database)
            positional=list(args)
            if len(positional)>=5:
                positional[4]=guarded_connection_type(positional[4])
            else:
                kwargs['factory']=guarded_connection_type(kwargs.get('factory',sqlite3.Connection))
            connection=original(database,*positional,**kwargs)
            def authorize(action, first, second, db_name, trigger):
                if action==sqlite3.SQLITE_ATTACH:
                    try:
                        if first is None:
                            if not getattr(connection,'_offline_attach_depth',0):
                                self.block('sqlite','unverified dynamic ATTACH')
                        else: check_database(first)
                    except OfflineAccessBlocked: return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            connection.set_authorizer(authorize)
            return connection
        self._patch(sqlite3,'connect',connect)
        self._patch(sqlite3.dbapi2,'connect',connect)
        def network(*args,**kwargs): return self.block('network','socket operation')
        for obj,method in ((socket,'create_connection'),(socket,'getaddrinfo'),(socket.socket,'connect'),(socket.socket,'connect_ex'),(socket.socket,'sendto')):
            self._patch(obj,method,network)
        sys.meta_path.insert(0,self.finder)
        for module in list(sys.modules.values()):
            if module is not None and module.__name__.removeprefix('backend.') in TARGETS: self.patch_module(module)
        return self
    def uninstall(self):
        if self.finder in sys.meta_path: sys.meta_path.remove(self.finder)
        for obj,name,original in reversed(self._patches): setattr(obj,name,original)
        self._patches.clear()


def install_from_environment():
    global ACTIVE
    if ACTIVE is None and os.environ.get('GEO_OFFLINE_OUTPUT'):
        # A collected legacy test changes TMPDIR. Preserve one explicit test root
        # across parent/child imports so immutable fixture paths remain valid.
        if root := os.environ.get('GEO_OFFLINE_TEMP_ROOT'):
            root_path=Path(root).resolve()
            if not root_path.is_dir(): raise ValueError('MISSING_OFFLINE_TEMP_ROOT')
            tempfile.tempdir=str(root_path)
        output=Path(os.environ['GEO_OFFLINE_OUTPUT'])
        ACTIVE=OfflineGuards(experiment_root=output,event_log=output/'guard-attempts.jsonl').install()
    return ACTIVE
