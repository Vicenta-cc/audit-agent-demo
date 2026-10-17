"""Crawler ports for the shared geographic executor, after Run/Job verification."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path

from .crawler_adapter import CrawlOutput, CrawlerAuthenticationError, CrawlerVerificationError
from .geographic_bridge import GeographicBridge
from .geographic_delivery import GeographicDelivery
from .geographic_execution_store import GeographicExecutionStore
from .crawler_route import CollectionRouteError
from .geographic_recovery import GeographicCaptureDeferred
from .ingestion import content_identity, comment_content_identity
from .scoped_execution import ScopedExecution
from .scoped_selection import digest


class GeographicPipeline:
    def __init__(self, *, plan, binding, crawler, ingestion, state_path, save_root,
                 risk_client, geo_client, accounts, lease_fence, stop, route_operation,
                 on_delivery=lambda refs: None, retire=lambda account, error: None,
                 started_callback=None, progress_callback=None, on_progress=lambda report: None,
                 state_lock=None):
        self.crawler, self.ingestion = crawler, ingestion
        self.root = Path(save_root) / 'geographic'
        self.stop, self.on_delivery, self.retire = stop, on_delivery, retire
        self.route_operation = route_operation
        self._detail_route = ExitStack()
        self.started_callback, self.progress_callback = started_callback, progress_callback
        self.on_progress = on_progress
        self.accounts = {a['id']: deepcopy(a) for a in accounts if a.get('id')}
        if not self.accounts:
            raise ValueError('geographic execution requires leased account identities')
        self.cursor = 0
        self.bridge = GeographicBridge(plan,
            expected_identity={k: binding[k] for k in ('owner', 'session_id', 'draft_id', 'revision')},
            expected_plan_digest=binding['p2_digest'],
            execution_id=digest({'run_id': binding['run_id'], 'job_id': binding['job_id'],
                                 'p2_digest': binding['p2_digest']}),
            run_id=binding['run_id'], job_id=binding['job_id'], config_hash=binding['config_hash'],
            risk_client=risk_client, geo_client=geo_client)
        projection = self.bridge.plan
        self.store = GeographicExecutionStore(state_path,
            projection['binding'] | projection['runtime_binding'], projection['plan_digest'], lease_fence,
            state_lock=state_lock)
        self.sink = GeographicDelivery(ingestion, self.store, projection, self.root / 'delivered')
        self.execution = ScopedExecution(self.store, projection, capture=self.capture,
            risk=self.risk, geo=self.geo, detail=self.detail, review=self.review,
            deliver=self.deliver, validate_geo=self.bridge.validate_geo,
            account=self.account, rotate=self.rotate, stop=stop, checkpoint=self.progress,
            durable_geo_attempts=True)

    def progress(self, stage, payload):
        if stage == 'after_settle':
            review = self.execution.snapshot()['reviews'].get(payload['attempt_id'], {})
            if not review.get('allowed') or self.stop():
                self._detail_route.close()
        self.on_progress(self.execution.report() | {'stage': stage, 'progress': deepcopy(payload)})

    def fence(self):
        # A fresh transaction rejects a stale worker before each external action.
        self.store.load(self.execution.execution_id, self.bridge.plan['plan_digest'])

    def account(self, query):
        ids = list(self.accounts)
        if not ids:
            raise CrawlerAuthenticationError('geographic account pool exhausted')
        selected = ids[self.cursor % len(ids)]
        self.cursor += 1
        return selected

    def rotate(self, query, account_id, error):
        self.fence()
        self._retire_account(account_id, error)
        error.triage_rotation_stop = True  # Never restart all queries via the outer legacy loop.
        return self.account(query) if self.accounts else None

    def _retire_account(self, account_id, error):
        removed = self.accounts.pop(account_id, None)
        if removed:
            self.retire(removed, error)
            if isinstance(error, CrawlerVerificationError) and removed.get('proxy_id'):
                # A shared exit pause cannot be bypassed by its other accounts.
                self.accounts = {key: account for key, account in self.accounts.items()
                                 if account.get('proxy_id') != removed['proxy_id']}

    @staticmethod
    def original(item, key, keyword):
        # Only crawler identity/context aliases are supplied here. Text, comments,
        # verification and follower fields remain byte-for-byte values from capture.
        item = deepcopy(item)
        if item.get('post_id') not in (None, '', key):
            raise ValueError('crawler post identity conflict')
        item.update(post_id=key, source_keyword=keyword)
        return item

    def capture(self, plan, query):
        self._detail_route.close()
        collection = self.bridge.p2.collection
        params = collection.effective_parameters
        account_id = self.account(query)
        self.progress('capture_started', {'query_id': query['query_id'], 'query': query['query']})
        for attempt in range(2):
            self.fence()
            if self.stop():
                raise RuntimeError('geographic capture incomplete: stopped')
            account = self.accounts[account_id]
            try:
                with self._acquire_route(account_id) as route_context:
                    self.fence()
                    output = self.crawler.run_search(platform=plan['platform'], keyword=query['query'],
                        start_page=params.start_page, max_notes=collection.candidate_count,
                        max_total_notes=collection.candidate_count, max_comments=collection.candidate_comment_limit,
                        max_concurrency=params.max_concurrency, max_items_per_minute=params.max_items_per_minute,
                        get_sub_comment=False, collect_comments=collection.collect_comments and collection.candidate_comment_limit > 0,
                        collect_media=False, save_root=self.root/'candidates'/query['query_id']/str(attempt),
                        fetch_author_profile=True,
                        skip_profile_verify_regex='|'.join(plan['risk_config']['parameters']['official_verify_patterns']),
                        stream_items=False, content_callback=None, stop_checker=self.stop,
                        auth_state=account['auth_state'], account_id=account_id, route_context=route_context,
                        started_callback=self.started_callback, search_sort=params.search_sort,
                        reusable_content_db=None, current_task_id=plan['runtime_binding']['job_id'])
                self.started_callback = None
            except (CrawlerAuthenticationError, CrawlerVerificationError) as error:
                replacement = self.rotate(query, account_id, error)
                if attempt or replacement is None:
                    raise
                account_id = replacement
                continue
            self.fence()
            if self.stop():
                raise RuntimeError('geographic capture incomplete: stopped')
            keys = [content_identity(item, plan['platform']) for item in output.contents]
            if any(not key for key in keys):
                raise ValueError('capture contains unidentified content')
            audited = self.ingestion.analyzed_content_keys(plan['platform'], keys)
            occurrences = []
            for rank, (key, item) in enumerate(zip(keys, output.contents), 1):
                comments = [c for c in output.comments if comment_content_identity(c, plan['platform']) == key]
                occurrence = self.bridge.occurrence(query['query_id'], key, rank,
                    self.original(item, key, query['query']), comments)
                occurrence['already_audited'] = key in audited
                occurrences.append(occurrence)
            return {'occurrences': occurrences, 'account_id': account_id,
                    'capture_parameters': deepcopy(query['capture_parameters']),
                    'observed_parameters': {'max_notes': collection.candidate_count,
                        'max_comments': collection.candidate_comment_limit,
                        'collect_comments': collection.collect_comments and collection.candidate_comment_limit > 0,
                        'collect_media': False, 'get_sub_comment': False,
                        'start_page': params.start_page, 'search_sort': params.search_sort,
                        'max_concurrency': params.max_concurrency}}

    def risk(self, plan, query, occurrence):
        self.fence()
        self.progress('risk_started', {'query_id': query['query_id'], 'query': query['query'],
            'occurrence_id': occurrence['occurrence_id'], 'rank': occurrence['rank']})
        return self.bridge.risk(plan, query, occurrence)

    def geo(self, plan, query, occurrence, *, attempt_state=None, on_attempt=None):
        self.fence()
        self.progress('geo_started', {'query_id': query['query_id'], 'query': query['query'],
            'occurrence_id': occurrence['occurrence_id'], 'rank': occurrence['rank']})
        return self.bridge.geo(plan, query, occurrence, attempt_state=attempt_state, on_attempt=on_attempt)

    def review(self, plan, occurrence, content):
        self.fence()
        return self.bridge.review(plan, occurrence, content)

    def detail(self, occurrence, attempt, *, account_id, content_callback):
        self.fence()
        if account_id not in self.accounts:
            # An account used by the old worker is not automatically authorized
            # or leased by its replacement. Preserve uncertainty for reconciliation.
            error = CrawlerAuthenticationError('saved detail account is not leased')
            error.triage_rotation_stop = True
            raise error
        params = self.bridge.p2.collection.effective_parameters
        # Retain only this attempt's source route through approved delivery.
        # Rejected/empty earlier details cannot accumulate account/exit leases.
        self._detail_route.close()
        try:
            route_context = self._detail_route.enter_context(self._acquire_route(account_id))
            self.fence()
            output = self.crawler.run_detail(platform=occurrence['platform'], content_id=occurrence['content_key'],
                source_keyword=occurrence['source_keyword'], max_comments=self.bridge.p2.collection.detail_comment_limit,
                max_concurrency=params.max_concurrency, max_items_per_minute=params.max_items_per_minute,
                get_sub_comment=params.get_sub_comment, collect_comments=params.collect_comments,
                collect_media=params.collect_media, save_root=self.root/'details'/attempt['attempt_id'],
                progress_callback=self.progress_callback, content_callback=content_callback,
                stream_items=False, stop_checker=self.stop,
                auth_state=self.accounts[account_id]['auth_state'], account_id=account_id,
                route_context=route_context, started_callback=None)
        except (CrawlerAuthenticationError, CrawlerVerificationError) as error:
            self._detail_route.close()
            self.fence()
            # Both the first failure and the exhausted retry retire their account.
            # The shared executor alone owns whether the query gets one retry.
            self._retire_account(account_id, error)
            error.triage_rotation_stop = True
            raise
        except BaseException:
            self._detail_route.close()
            raise
        self.fence()
        if self.stop():
            raise RuntimeError('geographic detail incomplete: stopped')
        if not output.contents:
            self._detail_route.close()
            return None
        if len(output.contents) != 1 or content_identity(output.contents[0], occurrence['platform']) != occurrence['content_key']:
            raise ValueError('detail returned unexpected content identity')
        comments = [c for c in output.comments if comment_content_identity(c, occurrence['platform']) == occurrence['content_key']]
        return self.bridge.normalize({'platform': occurrence['platform'], 'content_key': occurrence['content_key'],
            'post': self.original(output.contents[0], occurrence['content_key'], occurrence['source_keyword']),
            'comments': deepcopy(comments), 'source_queries': [occurrence['source_keyword']]})

    def deliver(self, content, *, idempotency_key, binding):
        try:
            receipt = self.sink.deliver(content, idempotency_key=idempotency_key, binding=binding)
            self.on_delivery(receipt['refs'])
            return receipt
        finally:
            self._detail_route.close()

    @contextmanager
    def _acquire_route(self, account_id):
        # Only entry failures prove the crawler has not started. Never reclassify
        # an identical error raised by the crawler body or route cleanup.
        with ExitStack() as stack:
            try:
                route = stack.enter_context(self.route_operation(account_id))
            except CollectionRouteError as exc:
                if exc.code in {"PROXY_BUSY", "SUPPLEMENT_DEFERRED"}:
                    raise GeographicCaptureDeferred(exc.code) from exc
                raise
            yield route

    def run(self):
        try:
            report = self.execution.run()
        finally:
            self._detail_route.close()
        _, refs = self.ingestion.validated_selection_for_task(self.bridge.plan['runtime_binding']['job_id'])
        # Read only durable approved membership; never scan candidate/detail folders.
        output = CrawlOutput(self.bridge.plan['platform'], [r['item'] for r in refs],
                             [c for r in refs for c in r.get('comments', [])], self.root)
        return report, output
