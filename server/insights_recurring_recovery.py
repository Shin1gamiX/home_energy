"""Read-only review of one exact pre-model failure; never reset run evidence.

The original grant, two successful runs, failed run and earlier repair records
remain immutable. A separate review and activation bind the corrected bundle.
All later incomplete/uncertain attempts still require administrator review.
"""
import hashlib
import json
import time
import types


PREVIOUS_BUNDLE = '5e5d80cb0b44231212cadb67a50e848ce8834237291e3bd54238511d10fed7ca'
PREVIOUS_RECORD_PINS = {
    'runtime-repair-20261016-approval.json': '7496caea0ce54f25db0304e7505232e46ebe5274ac950442c1b75bcc886f80b1',
    'runtime-repair-20261016-activation.json': '438ba4340312d295d46b2773eb20fd2616ddcf76acd97b0a593afaefa9b57d6e',
}
FAILED_RUN = '000003'
FAILURE_PINS = {
    'receipt': '224b21db674f3e29bf84e1311ba7f8b67ca16fd69c3f972fc9de563a1499af3a',
    'worker': '224b21db674f3e29bf84e1311ba7f8b67ca16fd69c3f972fc9de563a1499af3a',
    'audit': '911d19f01cb3002f3cb0d3aff5a6abf1d9ae19a904ff2b25813d1ec566461d24',
    'terminal': 'ed57e3762ebb9492ec01aae9aa2c2ca96cd59e62f5dc4421e0a9a94c11a9a589',
}
FAILURE_LOG_PINS = {
    'startup-20261016T090010504462Z-81acdccdc612b3d6.json': 'e040ba5f37d0bf94ccc99c3ef3a262e1f13c4681a23e58039106bdc5ed285610',
    'startup-20261016T090010907253Z-8d8d7c5ec366b288.json': 'dea13f70056c50cfca7b264494d38f36e52c69b94f81a0f467312f753881e106',
}


def require(condition, code='recurring_recovery_evidence_changed'):
    if not condition:
        raise ValueError(code)


def record_hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    return hashlib.sha256(raw).hexdigest()


def review_body(bundle, approved_at, revision):
    return {'schema_version': 1, 'scope': 'recurring_pre_model_failure_review',
        'original_authorization_sha256': revision.LEGACY_AUTHORIZATION,
        'previous_bundle_sha256': PREVIOUS_BUNDLE, 'bundle_sha256': bundle,
        'failed_run_id': FAILED_RUN, 'failed_run_sha256': FAILURE_PINS,
        'startup_log_sha256': FAILURE_LOG_PINS, 'previous_record_sha256': PREVIOUS_RECORD_PINS,
        'approved_unix': approved_at, 'attempt_consumed': True,
        'requires_separate_activation': True}


def validate_review(value, bundle, grant, revision):
    require(type(value) is dict and type(value.get('schema_version')) is int
            and type(value.get('approved_unix')) is int
            and value.get('attempt_consumed') is True
            and value.get('requires_separate_activation') is True
            and grant['issued_unix'] <= value['approved_unix'] <= time.time()
            and value['approved_unix'] < grant['expires_unix']
            and value == review_body(bundle, value['approved_unix'], revision),
            'recurring_recovery_review_changed')


def validate_activation(value, review, bundle, grant, storage):
    if value is None:
        return False
    require(type(value) is dict and type(value.get('schema_version')) is int
            and type(value.get('activated_unix')) is int
            and review['approved_unix'] <= value['activated_unix'] <= time.time()
            and value['activated_unix'] < grant['expires_unix']
            and value == {'schema_version': 1, 'scope': 'recurring_recovery_activation',
                'review_sha256': storage.digest(review), 'bundle_sha256': bundle,
                'activated_unix': value['activated_unix']},
            'recurring_recovery_activation_changed')
    return True


def verify_failed(storage, store, receipt, terminal, kinds, review):
    """Only this pinned, unarmed attempt is reviewable; it stays uncertain."""
    require(kinds == set(FAILURE_PINS) == {'receipt', 'worker', 'audit', 'terminal'})
    for kind, expected in FAILURE_PINS.items():
        with store.files.open(storage.filename(kind, FAILED_RUN)) as stream:
            raw = stream.read(storage.trial.MAX_DATA + 1)
        require(len(raw) <= storage.trial.MAX_DATA and hashlib.sha256(raw).hexdigest() == expected)
    require(store.files.read(storage.filename('worker', FAILED_RUN)) == receipt)
    completed = terminal.get('completed_unix')
    require(type(completed) is int and receipt['created_unix'] <= completed
            < receipt['created_unix'] + storage.MAX_SECONDS
            and completed <= review['approved_unix'])
    require(terminal == receipt | {'outcome': 'uncertain', 'completed_unix': completed,
                                   'evidence_sha256': {}})
    rows = storage.validate_audit(store._audit_bytes(FAILED_RUN))
    require([(row['event'], row['method'], row['attempt'], row['code']) for row in rows]
            == [('reserved', 'none', 0, 'none'), ('worker_claimed', 'none', 0, 'none'),
                ('closed', 'none', 0, 'uncertain')])


def recovered_store(storage, revision, bundle, old_approval, review):
    old_type = revision.revised_store(storage, PREVIOUS_BUNDLE, old_approval)

    class Store(storage.Store):
        def __init__(self, current_bundle, run_id=None, *, files=None):
            require(current_bundle == bundle, 'recurring_recovery_review_changed')
            super().__init__(current_bundle, run_id, files=files)

        def _grant(self):
            require(storage.filename('authorization') in self.files.names(), 'recurring_not_authorized')
            grant = self.files.read(storage.filename('authorization'), 4096)
            storage.validate_authorization(grant, revision.LEGACY_BUNDLE, time.time())
            revision.validate_approval(old_approval, PREVIOUS_BUNDLE, grant, storage)
            validate_review(review, self.bundle, grant, revision)
            return grant

        def _receipt(self, run_id, grant):
            if int(run_id) <= int(FAILED_RUN):
                previous = old_type(PREVIOUS_BUNDLE, files=self.files)
                try:
                    return previous._receipt(run_id, grant)
                finally:
                    previous.close()
            receipt = super()._receipt(run_id, grant)
            require(receipt['created_unix'] >= review['approved_unix'], 'recurring_receipt_changed')
            return receipt

        def history(self, grant):
            inventory = {}
            for name in self.files.names():
                parsed = storage.validate_name(name)
                if parsed:
                    run_id, kind = parsed
                    inventory.setdefault(run_id, set()).add(kind)
            require(int(FAILED_RUN) == revision.LEGACY_RUNS + 1
                    and int(FAILED_RUN) <= len(inventory) <= grant['max_runs'],
                    'recurring_prior_run_needs_review')
            previous = storage.ZERO_HASH
            created = completed = None
            for index, (run_id, kinds) in enumerate(sorted(inventory.items()), 1):
                require(run_id == f'{index:06d}', 'recurring_prior_run_needs_review')
                receipt = self._receipt(run_id, grant)
                require(receipt['previous_terminal_sha256'] == previous, 'recurring_evidence_chain_changed')
                require(created is None or receipt['created_unix'] - created >= storage.INTERVAL_SECONDS,
                        'recurring_evidence_spacing')
                terminal = self.files.read(storage.filename('terminal', run_id))
                if run_id == FAILED_RUN:
                    verify_failed(storage, self, receipt, terminal, kinds, review)
                else:
                    require(kinds == storage.KINDS and terminal.get('outcome') == 'completed',
                            'recurring_prior_run_needs_review')
                    ended = terminal.get('completed_unix')
                    require(type(ended) is int and receipt['created_unix'] <= ended
                            < receipt['created_unix'] + storage.MAX_SECONDS, 'recurring_completion_time')
                    require(terminal == receipt | {'outcome': 'completed', 'completed_unix': ended,
                        'evidence_sha256': self._completed_evidence(run_id, receipt)},
                        'recurring_evidence_changed')
                previous = storage.digest(terminal)
                if index == revision.LEGACY_RUNS:
                    require(previous == revision.LEGACY_TIP, 'recurring_repair_history_changed')
                created, completed = receipt['created_unix'], terminal['completed_unix']
            return {'count': len(inventory), 'previous': previous, 'created': created,
                    'completed': completed}

    return Store


def bind(context, startup, *, active_run=None, token=None, role=None):
    """Discovery validates the worker handoff, not a not-yet-created arm marker.

Actual prepare/publish remain gated by Store.claim/active: matching worker,
armed and model markers are required before any SSH operation is constructed.
"""
    require((role is None and active_run is None and token is None)
            or (role in ('worker', 'mcp') and active_run is not None and token is not None),
            'recurring_recovery_handoff_changed')
    storage, revision = context.original_storage, context.revision
    old_approval = startup.read_record(startup.APPROVAL)
    old_activation = startup.read_record(startup.ACTIVATION)
    for name, value in ((startup.APPROVAL, old_approval), (startup.ACTIVATION, old_activation)):
        require(value is not None and record_hash(value) == PREVIOUS_RECORD_PINS[name],
                'recurring_recovery_prior_repair_changed')
    for name, pin in FAILURE_LOG_PINS.items():
        require(record_hash(startup.read_record(name)) == pin)
    review = startup.read_record(startup.RECOVERY_REVIEW)
    require(review is not None, 'recurring_recovery_review_required')
    store_type = recovered_store(storage, revision, context.bundle, old_approval, review)
    store = store_type(context.bundle, active_run)
    try:
        grant = store._grant()
        require(revision.validate_activation(old_activation, old_approval, PREVIOUS_BUNDLE, grant, storage),
                'recurring_recovery_prior_repair_changed')
        if role is None:
            history = store.history(grant)
        else:
            require(int(active_run) > int(FAILED_RUN), 'recurring_recovery_handoff_changed')
            history = revision.handoff_history(storage, store, grant, token, armed=False)
            if role == 'mcp':
                require(store.read('worker') == store.active(token, armed=False),
                        'recurring_recovery_handoff_changed')
    finally:
        store.close()
    activation = startup.read_record(startup.RECOVERY_ACTIVATION)
    context.repair_active = validate_activation(activation, review, context.bundle, grant, storage)
    context.storage = types.SimpleNamespace(Store=store_type, digest=storage.digest,
        run_identifier=storage.run_identifier)
    return {'recovery_review_present': True, 'repair_active': context.repair_active,
        'attempts_consumed': history['count'], 'completed_attempts': history['count'] - 1,
        'reviewed_failed_attempts': 1, 'remaining_attempts': grant['max_runs'] - history['count'],
        'authorization_expires_unix': grant['expires_unix'], 'bundle_sha256': context.bundle}
