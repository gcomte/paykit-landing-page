"""Ignored observations only: never publication approval or lease authority.

All modifications are serialized independently of the pipeline lease lock. State
is the durable source; reports are recoverable projections, including post-expiry.
"""
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

PHASES = ('generation_build', 'independent_review', 'correction_retest', 'exports_qa_deploy')


def number(value, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('Budget ' + label + ' must be a finite nonnegative number')
    return value


def validate(policy, run_cap):
    number(run_cap, 'runtimeSeconds')
    keys = {'schemaVersion', 'implementationCheckpointSeconds', 'phasePlanningSeconds',
            'requireEveryRunReport', 'reportNoChange'}
    if not isinstance(policy, dict) or set(policy) != keys:
        raise ValueError('Budget policy requires exactly the documented keys')
    if type(policy['schemaVersion']) is not int or policy['schemaVersion'] != 1:
        raise ValueError('Budget schemaVersion must be integer 1')
    number(policy['implementationCheckpointSeconds'], 'implementationCheckpointSeconds')
    if policy['requireEveryRunReport'] is not True or policy['reportNoChange'] is not True:
        raise ValueError('Budget reporting flags must both be true booleans')
    phases = policy['phasePlanningSeconds']
    if not isinstance(phases, dict) or set(phases) != set(PHASES):
        raise ValueError('Budget phasePlanningSeconds requires exactly the four phase keys')
    for phase, value in phases.items():
        number(value, phase)
    if sum(phases.values()) != run_cap:
        raise ValueError('Budget phase allocations must sum to runtimeSeconds')
    return json.loads(json.dumps(policy, allow_nan=False))


def _directory(cache, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id):
        raise ValueError('Unsafe budget run ID')
    return Path(cache) / 'runs' / run_id


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    try:
        temporary.write_text(value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def _locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'budget.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _read(directory):
    return json.loads((directory / 'budget-state.json').read_text())


def _persist(directory, state):
    _write(directory / 'budget-state.json', json.dumps(state, indent=2, allow_nan=False) + '\n')
    return _reports(directory, state)


def published_tail(cache, run_id, operation, action):
    """Only for telemetry after the caller's successful authoritative cleanup.

    This is not admission/acceptance authority. Never wrap work or cleanup here.
    Retain the latest actual state-I/O diagnosis separately, without inventing
    observation completion. If that diagnostic also cannot persist, recovery
    still detects the incomplete observation state against the terminal record.
    """
    try:
        return action()
    except OSError as error:
        directory = _directory(cache, run_id)
        loss = {'file': 'budget-state.json', 'operation': operation,
                'errorType': type(error).__name__, 'error': str(error),
                'observedAt': time.time()}
        try:
            with _locked(directory):
                _write(directory / 'budget-state-loss.json', json.dumps(loss, indent=2) + '\n')
        except OSError:
            # A diagnostic is not allowed to revoke the already accepted action.
            pass


def create(cache, record, policy=None):
    directory = _directory(cache, record['id'])
    state = {'schemaVersion': 1, 'runId': record['id'], 'startedAt': record['started'],
             'leaseAcquiredAt': record.get('leaseAcquiredAt'),
             'deadline': record['deadline'], 'endedAt': None, 'outcome': 'running',
             'policySnapshot': policy, 'runCapSeconds': record['deadline'] - record['started'],
             'observations': [], 'phaseAssessments': {}}
    with _locked(directory):
        return _persist(directory, state)


def default_assessment():
    return {'moreAllocationUseful': 'unknown', 'constraint': 'not-assessed',
            'remainingCoverage': None, 'evidence': [], 'rationale': 'Not assessed',
            'suggestedExtraSeconds': None}


def validate_assessment(value):
    if not isinstance(value, dict) or set(value) != set(default_assessment()):
        raise ValueError('Assessment requires exactly the documented fields')
    choice = value['moreAllocationUseful']
    if choice not in ('yes', 'no', 'unknown'):
        raise ValueError('Assessment moreAllocationUseful must be yes/no/unknown')
    if value['constraint'] not in ('time', 'tooling', 'access', 'dependency', 'quality', 'none', 'unknown'):
        raise ValueError('Assessment constraint is invalid')
    if not isinstance(value['rationale'], str) or not value['rationale'].strip():
        raise ValueError('Assessment rationale is required')
    coverage = value['remainingCoverage']
    evidence = value['evidence']
    if (coverage is not None and (not isinstance(coverage, str) or not coverage.strip())
            or not isinstance(evidence, list) or not all(isinstance(item, str) and item.strip() for item in evidence)):
        raise ValueError('Assessment coverage/evidence is malformed')
    extra = value['suggestedExtraSeconds']
    if choice != 'unknown' and (coverage is None or not evidence):
        raise ValueError('Assessment yes/no needs specific coverage and evidence')
    if choice == 'yes':
        if value['constraint'] != 'time' or extra is None or number(extra, 'suggested extra seconds') == 0:
            raise ValueError('Assessment extra allocation needs an evidenced time constraint and positive extra seconds')
    elif extra is not None:
        raise ValueError('Assessment extra seconds must be null unless extra allocation is justified')
    return json.loads(json.dumps(value, allow_nan=False))


def assess(cache, run_id, assessment, phase=None, observation=None):
    assessment = validate_assessment(assessment)
    if (phase is None) == (observation is None) or (phase is not None and phase not in PHASES):
        raise ValueError('Assessment needs one valid phase or observation')
    directory = _directory(cache, run_id)
    with _locked(directory):
        state = _read(directory)
        if phase is not None:
            state['phaseAssessments'][phase] = assessment
        else:
            row = next(item for item in state['observations'] if item['id'] == observation)
            row['assessment'] = assessment
        return _persist(directory, state)


def start(cache, run_id, name, phase=None, kind='stage', role='pipeline', allocation=None, task_id=None, started_at=None):
    if phase is not None and phase not in PHASES:
        raise ValueError('Unknown budget phase')
    if kind not in ('stage', 'worker', 'coordinator'):
        raise ValueError('Unknown budget work kind')
    if allocation is not None:
        number(allocation, 'worker allocation')
    if started_at is not None:
        number(started_at, 'observed start')
    if not isinstance(role, str) or not role.strip() or role != role.strip() or not role.isprintable():
        raise ValueError('Budget role is required')
    if kind != 'stage' and (not isinstance(task_id, str) or not task_id.strip() or task_id != task_id.strip() or not task_id.isprintable()):
        raise ValueError('Actual task/session ID is required for budget work')
    directory = _directory(cache, run_id)
    with _locked(directory):
        state = _read(directory)
        if state['endedAt'] is not None:
            raise ValueError('Budget run is closed; cannot start work')
        recorded_at = time.time()
        if started_at is not None and not state['startedAt'] <= started_at <= recorded_at:
            raise ValueError('Budget observed start must be within this run and not in the future')
        active = [row for row in state['observations'] if row['endedAt'] is None]
        if kind == 'worker' and sum(row['kind'] == 'worker' for row in active) >= 2:
            raise ValueError('Two worker limit reached')
        if task_id and any(row['taskId'] == task_id for row in active):
            raise ValueError('Task/session ID already has active budget work')
        row = {'id': uuid.uuid4().hex, 'kind': kind, 'name': name, 'phase': phase,
               'role': role, 'taskId': task_id, 'allocatedSeconds': allocation,
               'startedAt': recorded_at if started_at is None else started_at,
               'recordedAt': recorded_at, 'startSource': 'coordinator-observed' if started_at is not None else 'registration-clock',
               'endedAt': None, 'status': 'unknown',
               'assessment': default_assessment(), 'details': None}
        state['observations'].append(row)
        _persist(directory, state)
        return row['id']


def finish(cache, run_id, observation, status='completed', details=None, assessment=None):
    if status not in ('completed', 'failed', 'blocked', 'unknown'):
        raise ValueError('Invalid budget completion status')
    if assessment is not None:
        assessment = validate_assessment(assessment)
    directory = _directory(cache, run_id)
    with _locked(directory):
        state = _read(directory)
        row = next(item for item in state['observations'] if item['id'] == observation)
        if row['endedAt'] is not None:
            raise ValueError('Budget observation already finished')
        row.update(endedAt=time.time(), status=status, details=details)
        # A containing pipeline span may finish while unwinding finalization.
        # This records its real reporting tail, never changes the hard deadline.
        if state['endedAt'] is not None:
            state['endedAt'] = max(state['endedAt'], row['endedAt'])
        if assessment is not None:
            row['assessment'] = assessment
        result = _persist(directory, state)
        row = next(item for item in result['observations'] if item['id'] == observation)
        if result['projectionErrors']:
            return {**row, 'projectionErrors': result['projectionErrors']}
        return row


def _union(intervals):
    end, total = None, 0.0
    for start_at, end_at in sorted(intervals):
        if end is None or start_at > end:
            total += end_at - start_at
        elif end_at > end:
            total += end_at - end
        end = end_at if end is None else max(end, end_at)
    return total


def _metrics(rows, allocation):
    intervals = [(row['startedAt'], row['endedAt']) for row in rows if row['endedAt'] is not None]
    known = _union(intervals)
    complete = bool(rows) and all(row['endedAt'] is not None for row in rows)
    elapsed = known if complete else None
    status = 'not-run' if not rows else ('unknown' if not complete or any(row['status'] == 'unknown' for row in rows) else 'completed')
    for failed in ('blocked', 'failed'):
        if any(row['status'] == failed for row in rows):
            status = failed
    return {'status': status, 'allocatedSeconds': allocation, 'elapsedSeconds': elapsed,
            'elapsedLowerBoundSeconds': known,
            'remainingSeconds': max(0, allocation - elapsed) if allocation is not None and elapsed is not None else None,
            'overrunSeconds': max(0, elapsed - allocation) if allocation is not None and elapsed is not None else None}


def _reports(directory, state, published=False):
    if published:
        # The terminal action record supplies outcome, NEVER missing timings.
        state = {**state, 'outcome': 'published'}
    through = state['endedAt'] if state['endedAt'] is not None else (None if published else time.time())
    wall = max(0, through - state['startedAt']) if through is not None else None
    outcome = 'expired' if state['endedAt'] is None and through is not None and through >= state['deadline'] else state['outcome']
    phases = {}
    allocations = (state['policySnapshot'] or {}).get('phasePlanningSeconds', {})
    for phase in PHASES:
        rows = [row for row in state['observations'] if row['phase'] == phase]
        phases[phase] = {**_metrics(rows, allocations.get(phase)),
                         'assessment': state['phaseAssessments'].get(phase, default_assessment())}
    intervals = [(row['startedAt'], row['endedAt']) for row in state['observations'] if row['endedAt'] is not None]
    agents = [row for row in state['observations'] if row['kind'] in ('worker', 'coordinator')]
    known_agent = sum(row['endedAt'] - row['startedAt'] for row in agents if row['endedAt'] is not None)
    aggregate = known_agent if agents and all(row['endedAt'] is not None for row in agents) else None
    observations = [{**row, **_metrics([row], row['allocatedSeconds'])} for row in state['observations']]
    agents = [row for row in observations if row['kind'] in ('worker', 'coordinator')]
    workers = [row for row in agents if row['kind'] == 'worker']
    missing = [phase for phase in PHASES if phase not in state['phaseAssessments']]
    missing += [row['id'] for row in state['observations'] if row['assessment']['constraint'] == 'not-assessed']
    result = {**state, 'outcome': outcome, 'observedThrough': through, 'wallClockSeconds': wall, 'phases': phases,
              'runRemainingSeconds': max(0, state['deadline'] - through) if through is not None else None,
              'runOverrunSeconds': max(0, through - state['deadline']) if through is not None else None,
              'publicationApprovalGranted': False,
              'agentTimingScope': 'Only explicitly bracketed agent intervals; untracked agent time is unknown.',
              'admissionQueueSeconds': max(0, state['leaseAcquiredAt'] - state['startedAt']) if state['leaseAcquiredAt'] is not None else None,
              'trackedWallSeconds': _union(intervals), 'unattributedWallSeconds': max(0, wall - _union(intervals)) if wall is not None else None,
              'aggregateAgentSeconds': aggregate, 'aggregateAgentLowerBoundSeconds': known_agent, 'workers': workers,
              'agents': agents, 'observations': observations,
              'missingAssessments': missing,
              'timingCaveat': 'Closed intervals are measured; missing/open work and unbracketed model time are unknown. Unattributed wall includes gaps and open intervals, not assumed idle time. Phase wall unions may overlap; never sum them as run wall time. Statuses describe observed work, not mandatory coverage; telemetry never grants publication approval.'}
    loss = directory / 'budget-state-loss.json'
    result['stateErrors'] = [json.loads(loss.read_text())] if loss.is_file() else []
    partial = state['endedAt'] is None or any(row['endedAt'] is None for row in state['observations'])
    result['timingAvailability'] = 'partial' if partial else 'available'
    if published and partial and not result['stateErrors']:
        result['stateErrors'].append({'file': 'budget-state.json', 'errorType': 'IncompleteTelemetryState',
            'error': 'Published action is authoritative; terminal telemetry is incomplete. Missing endpoints remain unknown.'})
    result['reportingAvailability'] = 'degraded' if result['stateErrors'] else 'available'
    return _project(directory, result)


def _project(directory, result):
    result['projectionErrors'] = []
    failed = set()
    def project(suffix):
        path = directory / ('budget-report.' + suffix)
        value = text(result) if suffix == 'txt' else json.dumps(result, indent=2, allow_nan=False) + '\n'
        try:
            _write(path, value)
        except OSError as error:
            # The observation state was persisted separately. Losing a JSON or
            # text projection must not change admission, work or closure.
            failed.add(suffix)
            result['projectionErrors'].append({'file': path.name, 'errorType': type(error).__name__, 'error': str(error)})
    for suffix in ('txt', 'json'):
        project(suffix)
    # Keep a surviving projection honest about the other file's loss.
    if failed:
        for suffix in ('txt', 'json'):
            if suffix not in failed:
                project(suffix)
    return result


def text(report):
    diagnostics = [f"Budget telemetry state degraded ({error['file']}): {error['errorType']}: {error['error']}"
                   for error in report.get('stateErrors', [])]
    diagnostics += [f"Budget report projection failed ({error['file']}): {error['errorType']}: {error['error']}"
                    for error in report.get('projectionErrors', [])]
    if report['outcome'] == 'unavailable' or report.get('timingAvailability') == 'unavailable':
        return '\n'.join([f"Run {report['runId']}: {report['outcome']}",
                          'Telemetry unavailable: ' + report['reason'], *diagnostics]) + '\n'
    def seconds(value):
        return 'unknown' if value is None else f'{value:.3f}s'
    lines = [f"Run {report['runId']}: {report['outcome']}",
             'Wall clock: ' + seconds(report['wallClockSeconds']),
             'Run remaining: ' + seconds(report['runRemainingSeconds']) + '; overrun: ' + seconds(report['runOverrunSeconds']),
             'Aggregate bracketed agent time: ' + seconds(report['aggregateAgentSeconds']),
             'Unattributed wall time: ' + seconds(report['unattributedWallSeconds'])]
    lines.extend(diagnostics)
    for name, row in report['phases'].items():
        lines.append(f"{name}: {row['status']}; elapsed {seconds(row['elapsedSeconds'])}, allocated {seconds(row['allocatedSeconds'])}, remaining {seconds(row['remainingSeconds'])}, overrun {seconds(row['overrunSeconds'])}; more allocation: {row['assessment']['moreAllocationUseful']} ({row['assessment']['rationale']})")
    for row in report['agents']:
        lines.append(f"{row['kind'].title()} {row['taskId']} ({row['role']}, {row['phase']}): {row['status']}; elapsed {seconds(row['elapsedSeconds'])}, allocated {seconds(row['allocatedSeconds'])}, remaining {seconds(row['remainingSeconds'])}, overrun {seconds(row['overrunSeconds'])}; more allocation: {row['assessment']['moreAllocationUseful']} ({row['assessment']['rationale']})")
    for row in report['observations']:
        if row['kind'] == 'stage':
            lines.append(f"Stage {row['name']} ({row['phase']}): {row['status']}; elapsed {seconds(row['elapsedSeconds'])}")
    for name, assessment in [(name, row['assessment']) for name, row in report['phases'].items()] + [(row['taskId'], row['assessment']) for row in report['agents']]:
        lines.append(f"Assessment {name}: constraint={assessment['constraint']}; remaining coverage={assessment['remainingCoverage']}; evidence={json.dumps(assessment['evidence'])}; suggested extra={seconds(assessment['suggestedExtraSeconds'])}")
    lines.append('Missing assessments: ' + ', '.join(report['missingAssessments']))
    lines.append(report['timingCaveat'])
    return '\n'.join(lines) + '\n'


def report(cache, run_id):
    directory = _directory(cache, run_id)
    # Read-only recovery of this run's action outcome, not acceptance or a
    # lease guard. A later run may already have replaced last-success.json.
    try:
        record = json.loads((directory / 'run.json').read_text())
    except FileNotFoundError:
        record = {}
    published = record.get('id') == run_id and record.get('status') == 'published'
    def unavailable(error=None):
        result = {'schemaVersion': 1, 'runId': run_id, 'outcome': 'unavailable', 'policySnapshot': None,
                  'reason': 'No recorded observations; historical budget telemetry is unavailable, not backfilled.',
                  'phases': {phase: {**_metrics([], None), 'status': 'unknown', 'assessment': default_assessment()} for phase in PHASES}}
        if not published:
            return result
        result.update(outcome='published', reportingAvailability='unavailable', timingAvailability='unavailable',
                      endedAt=None, wallClockSeconds=None, observations=[], publicationApprovalGranted=False,
                      reason='Authoritative publication retained; durable telemetry state is unavailable, not backfilled.',
                      stateErrors=[{'file': 'budget-state.json', 'errorType': type(error).__name__ if error else 'StateUnavailable',
                                    'error': str(error) if error else 'Recorded telemetry state is not a readable file.'}])
        return _project(directory, result)
    if not (directory / 'budget-state.json').is_file():
        return unavailable()
    try:
        with _locked(directory):
            return _reports(directory, _read(directory), published=published)
    except OSError as error:
        if not published:
            raise
        return unavailable(error)


def close(cache, run_id, outcome):
    directory = _directory(cache, run_id)
    with _locked(directory):
        state = _read(directory)
        state.update(endedAt=time.time(), outcome=outcome)
        return _persist(directory, state)
