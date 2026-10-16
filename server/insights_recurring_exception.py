"""One explicitly authorized early attempt; ordinary scheduling stays unchanged.

The old grant and evidence are read-only. Only --run-exception may reserve the
named early attempt, under the existing exclusive lock. Its receipt consumes
the normal quota even on failure. Later history validates the exception at the
receipt time, not the current time, so expiry cannot invalidate a completed run.
"""
import secrets
import time
import types


PREVIOUS_BUNDLE = 'f2e0ccb521779ed13940fe5384d8a72932e043782b813c7d9f0da32f371df8f2'
PREVIOUS_RUN = '000003'
PREVIOUS_TIP = '6abea0d49cf391db83a59844cca702461e4b97030194eabc565dca828a88816e'
EXCEPTION_RUN = '000004'
AUTHORIZATION = 'interval-exception-20261016-authorization.json'
MAX_WINDOW_SECONDS = 1800
PREVIOUS_RECORD_PINS = {
    'runtime-recovery-20261016-review.json': 'd0528a627de99e3abf63851880e0bcac56e1fb4fa1e78eb4b533ea22634fa846',
    'runtime-recovery-20261016-activation.json': 'b5fda87b1b476119ecaeb9542971f0fb3ed372151e63a5e61ed9e90da944fbfd',
}


def require(condition, code='recurring_exception_authorization_changed'):
    if not condition:
        raise ValueError(code)


def authorization_body(bundle, issued, expires, next_scheduled, revision):
    """Pure data construction only; the runner cannot create its own approval."""
    return {'schema_version': 1, 'scope': 'recurring_interval_exception',
        'original_authorization_sha256': revision.LEGACY_AUTHORIZATION,
        'previous_bundle_sha256': PREVIOUS_BUNDLE, 'bundle_sha256': bundle,
        'previous_run_id': PREVIOUS_RUN, 'previous_terminal_sha256': PREVIOUS_TIP,
        'previous_record_sha256': PREVIOUS_RECORD_PINS, 'run_id': EXCEPTION_RUN,
        'issued_unix': issued, 'not_before_unix': issued, 'expires_unix': expires,
        'next_scheduled_unix': next_scheduled, 'max_attempts': 1,
        'max_model_turns': 1, 'max_reports': 1, 'retries_allowed': False,
        'previous_attempt_remains_consumed': True, 'schedule_changed': False}


def validate_authorization(value, bundle, grant, revision, storage):
    require(type(value) is dict)
    for field in ('schema_version', 'issued_unix', 'not_before_unix', 'expires_unix',
                  'next_scheduled_unix', 'max_attempts', 'max_model_turns', 'max_reports'):
        require(type(value.get(field)) is int)
    require(value.get('retries_allowed') is False and value.get('schedule_changed') is False
            and value.get('previous_attempt_remains_consumed') is True)
    require(value == authorization_body(bundle, value['issued_unix'], value['expires_unix'],
                                        value['next_scheduled_unix'], revision))
    require(storage.digest(grant) == revision.LEGACY_AUTHORIZATION
            and grant['not_before_unix'] <= value['issued_unix'] <= time.time()
            and storage.MAX_SECONDS < value['expires_unix'] - value['issued_unix'] <= MAX_WINDOW_SECONDS
            and value['expires_unix'] < grant['expires_unix']
            and value['expires_unix'] + storage.INTERVAL_SECONDS < value['next_scheduled_unix']
            and value['next_scheduled_unix'] + storage.MAX_SECONDS < grant['expires_unix']
            and int(EXCEPTION_RUN) == int(PREVIOUS_RUN) + 1 <= grant['max_runs'])


def spacing_allowed(receipt, history, authorization, storage):
    if receipt['created_unix'] - history['created'] >= storage.INTERVAL_SECONDS:
        return True
    return (receipt['run_id'] == EXCEPTION_RUN and history['count'] == int(PREVIOUS_RUN)
            and history['previous'] == PREVIOUS_TIP
            and receipt['previous_terminal_sha256'] == PREVIOUS_TIP
            and authorization['not_before_unix'] <= receipt['created_unix']
            and receipt['created_unix'] + storage.MAX_SECONDS < authorization['expires_unix'])


def selected_files(storage, files, include):
    """Read-only inventory view; no evidence is moved, rewritten or deleted."""
    def names():
        result = set()
        for name in files.names():
            parsed = storage.validate_name(name)
            if parsed is None or include(parsed[0]):
                result.add(name)
        return result
    return types.SimpleNamespace(names=names, read=files.read, open=files.open)


def exception_store(storage, revision, recovery, bundle, old_approval, review, authorization):
    previous_type = recovery.recovered_store(storage, revision, PREVIOUS_BUNDLE, old_approval, review)

    class Store(storage.Store):
        def __init__(self, current_bundle, run_id=None, *, files=None):
            require(current_bundle == bundle)
            super().__init__(current_bundle, run_id, files=files)

        def _grant(self):
            previous = previous_type(PREVIOUS_BUNDLE, files=self.files)
            try:
                grant = previous._grant()
            finally:
                previous.close()
            validate_authorization(authorization, self.bundle, grant, revision, storage)
            return grant

        def _receipt(self, run_id, grant):
            if int(run_id) <= int(PREVIOUS_RUN):
                previous = previous_type(PREVIOUS_BUNDLE, files=self.files)
                try:
                    return previous._receipt(run_id, grant)
                finally:
                    previous.close()
            receipt = super()._receipt(run_id, grant)
            require(receipt['created_unix'] >= authorization['issued_unix'], 'recurring_receipt_changed')
            return receipt

        def history(self, grant):
            inventory = {}
            for name in self.files.names():
                parsed = storage.validate_name(name)
                if parsed:
                    run_id, kind = parsed
                    inventory.setdefault(run_id, set()).add(kind)
            require(int(PREVIOUS_RUN) <= len(inventory) <= grant['max_runs'],
                    'recurring_prior_run_needs_review')
            require(sorted(inventory) == [f'{index:06d}' for index in range(1, len(inventory) + 1)],
                    'recurring_prior_run_needs_review')
            prefix = selected_files(storage, self.files, lambda run: int(run) <= int(PREVIOUS_RUN))
            previous = previous_type(PREVIOUS_BUNDLE, files=prefix)
            try:
                history = previous.history(grant)
            finally:
                previous.close()
            require(history['count'] == int(PREVIOUS_RUN) and history['previous'] == PREVIOUS_TIP,
                    'recurring_exception_history_changed')
            for run_id, kinds in sorted(inventory.items()):
                if int(run_id) <= int(PREVIOUS_RUN):
                    continue
                require(kinds == storage.KINDS, 'recurring_prior_run_needs_review')
                receipt = self._receipt(run_id, grant)
                require(receipt['previous_terminal_sha256'] == history['previous'],
                        'recurring_evidence_chain_changed')
                require(receipt['created_unix'] >= history['completed']
                        and spacing_allowed(receipt, history, authorization, storage),
                        'recurring_evidence_spacing')
                terminal = self.files.read(storage.filename('terminal', run_id))
                require(terminal.get('outcome') == 'completed', 'recurring_prior_run_needs_review')
                completed = terminal.get('completed_unix')
                require(type(completed) is int and receipt['created_unix'] <= completed
                        < receipt['created_unix'] + storage.MAX_SECONDS, 'recurring_completion_time')
                require(terminal == receipt | {'outcome': 'completed', 'completed_unix': completed,
                        'evidence_sha256': self._completed_evidence(run_id, receipt)},
                        'recurring_evidence_changed')
                history = {'count': int(run_id), 'previous': storage.digest(terminal),
                           'created': receipt['created_unix'], 'completed': completed}
            return history

        def reserve_exception(self):
            """The only early-reservation entry; no retries, rollback or quota refund."""
            self._ready()
            require(self.run_id is None and self.lock is None, 'recurring_supervisor_only')
            self._grant()
            self.lock = self.files.open(storage.filename('lock'), 'lock')
            grant = self._grant()
            history = self.history(grant)
            require(history['count'] == int(PREVIOUS_RUN) and history['previous'] == PREVIOUS_TIP,
                    'recurring_exception_consumed')
            now = int(time.time())
            require(now >= history['completed'], 'recurring_clock_reversed')
            require(authorization['not_before_unix'] <= now
                    and now + storage.MAX_SECONDS < authorization['expires_unix'],
                    'recurring_exception_expired')
            require(history['count'] < grant['max_runs'], 'recurring_run_limit')
            require(now + storage.MAX_SECONDS < grant['expires_unix'], 'recurring_authorization_ending')
            self.run_id = EXCEPTION_RUN
            token = secrets.token_hex(32)
            receipt = {'schema_version': 1, 'scope': 'production', 'run_id': self.run_id,
                'bundle_sha256': self.bundle, 'authorization_sha256': storage.digest(grant),
                'token_sha256': storage.trial.token_hash(token), 'created_unix': now,
                'previous_terminal_sha256': history['previous'], 'outcome': 'pending'}
            self.files.create(self._name('receipt'), receipt)
            self.token = token
            with self.files.open(self._name('audit'), 'create') as stream:
                storage.trial._persist(stream, b'')
            self.record('reserved')
            return {'status': 'reserved', 'run_id': self.run_id, 'token': token}

    return Store


def handoff_history(storage, store, grant, token, authorization):
    require(int(store.run_id) > int(PREVIOUS_RUN), 'recurring_exception_handoff_changed')
    receipt = store.active(token, armed=False)
    files = store.files
    try:
        store.files = selected_files(storage, files, lambda run: run != store.run_id)
        history = store.history(grant)
    finally:
        store.files = files
    require(store.run_id == f"{history['count'] + 1:06d}"
            and receipt['previous_terminal_sha256'] == history['previous']
            and receipt['created_unix'] >= history['completed']
            and spacing_allowed(receipt, history, authorization, storage),
            'recurring_exception_handoff_changed')
    return history


def bind(context, startup, *, active_run=None, token=None, role=None):
    require((role is None and active_run is None and token is None)
            or (role in ('worker', 'mcp') and active_run is not None and token is not None),
            'recurring_exception_handoff_changed')
    storage, revision, recovery = context.original_storage, context.revision, context.recovery
    records = {}
    pins = recovery.PREVIOUS_RECORD_PINS | recovery.FAILURE_LOG_PINS | PREVIOUS_RECORD_PINS
    for name, pin in pins.items():
        value = startup.read_record(name)
        require(value is not None and recovery.record_hash(value) == pin,
                'recurring_exception_prior_records_changed')
        records[name] = value
    authorization = startup.read_record(AUTHORIZATION)
    require(authorization is not None, 'recurring_exception_authorization_required')
    old_approval, old_activation = records[startup.APPROVAL], records[startup.ACTIVATION]
    review, activation = records[startup.RECOVERY_REVIEW], records[startup.RECOVERY_ACTIVATION]
    store_type = exception_store(storage, revision, recovery, context.bundle,
                                 old_approval, review, authorization)
    store = store_type(context.bundle, active_run)
    try:
        grant = store._grant()
        require(revision.validate_activation(old_activation, old_approval, recovery.PREVIOUS_BUNDLE,
                                             grant, storage)
                and recovery.validate_activation(activation, review, PREVIOUS_BUNDLE, grant, storage)
                and activation['activated_unix'] <= authorization['issued_unix'],
                'recurring_exception_prior_records_changed')
        if role is None:
            history = store.history(grant)
        else:
            history = handoff_history(storage, store, grant, token, authorization)
            if role == 'mcp':
                require(store.read('worker') == store.active(token, armed=False),
                        'recurring_exception_handoff_changed')
    finally:
        store.close()
    context.repair_active = True
    context.storage = types.SimpleNamespace(Store=store_type, digest=storage.digest,
                                           run_identifier=storage.run_identifier)
    return {'repair_active': True, 'exception_authorization_present': True,
        'exception_run_id': EXCEPTION_RUN, 'exception_consumed': history['count'] >= int(EXCEPTION_RUN),
        'exception_expires_unix': authorization['expires_unix'], 'attempts_consumed': history['count'],
        'completed_attempts': history['count'] - 1, 'reviewed_failed_attempts': 1,
        'remaining_attempts': grant['max_runs'] - history['count'],
        'authorization_expires_unix': grant['expires_unix'], 'bundle_sha256': context.bundle}
