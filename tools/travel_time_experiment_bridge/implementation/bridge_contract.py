"""Offline contract v1. The caller, never the browser, supplies the registry snapshot."""
import copy
import hashlib
import json
import re
from datetime import date
from dataclasses import dataclass

class ContractError(ValueError):
    pass

@dataclass(frozen=True)
class ValidatedJob:
    run_id: str
    digest: str
    payload_json: str
    snapshot_json: str

TOP = {'schema_version', 'run_id', 'source', 'branch', 'day_type', 'season', 'service_dates', 'engine_version', 'policy_version', 'selection_trip_ids', 'trips'}
SNAP = TOP - {'run_id', 'selection_trip_ids'}
IDENTITY = {'id', 'operator', 'makat', 'line_number', 'direction', 'alternative'}
TRIP = IDENTITY | {'departure', 'before_arrival', 'after_arrival'}
EVIDENCE = {'status', 'recommended_arrival', 'verified_service_dates', 'verified_day_type', 'reason'}

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def keys(value, expected, where):
    if type(value) is not dict or set(value) != expected:
        raise ContractError(f'{where}: exact keys required')

def string(value, where):
    if type(value) is not str or not value or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise ContractError(f'{where}: nonempty bounded text required')

def minutes(value):
    if type(value) is not int or not 0 <= value <= 4320:
        raise ContractError('service minutes must be integers in [0,4320]')

def dates(values):
    if type(values) is not list or not values or len(values) != len(set(map(str, values))):
        raise ContractError('unique nonempty service dates required')
    for value in values:
        if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ContractError('ISO service date required')
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ContractError('invalid service date') from exc

def common(value):
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ContractError('unsupported schema_version')
    keys(value['source'], {'dataset_id', 'schedule_id', 'revision', 'sha256'}, 'source')
    for item in value['source'].values():
        string(item, 'source')
    if not re.fullmatch('[0-9a-f]{64}', value['source']['sha256']):
        raise ContractError('source sha256 must be lowercase hex')
    for field in ('branch', 'day_type', 'season', 'engine_version', 'policy_version'):
        string(value[field], field)
    dates(value['service_dates'])

def trip_map(trips, trusted):
    if type(trips) is not list or not trips:
        raise ContractError('complete nonempty trip list required')
    result = {}
    for trip in trips:
        keys(trip, (TRIP - {'after_arrival'} | {'evidence'}) if trusted else TRIP, 'trip')
        for field in IDENTITY:
            string(trip[field], field)
        for field in ('departure', 'before_arrival') if trusted else ('departure', 'before_arrival', 'after_arrival'):
            minutes(trip[field])
        if trip['before_arrival'] < trip['departure']:
            raise ContractError('negative original duration')
        if trip['id'] in result:
            raise ContractError('duplicate trip id')
        result[trip['id']] = trip
        if trusted:
            evidence = trip['evidence']
            keys(evidence, EVIDENCE, 'evidence')
            if evidence['status'] not in ('change_allowed', 'review', 'blocked'):
                raise ContractError('unknown evidence status')
            minutes(evidence['recommended_arrival'])
            dates(evidence['verified_service_dates'])
            string(evidence['verified_day_type'], 'verified_day_type')
            string(evidence['reason'], 'reason')
    return result

def validate_job(payload: dict, trusted_snapshot: dict) -> ValidatedJob:
    """Validate untrusted complete output against a server-owned authoritative record.

    Snapshot provenance/authentication is the integration caller's responsibility.
    Hashes bind bytes and evidence, and do not confer permission to execute.
    """
    keys(payload, TOP, 'payload')
    keys(trusted_snapshot, SNAP, 'snapshot')
    common(payload)
    common(trusted_snapshot)
    string(payload['run_id'], 'run_id')
    for field in SNAP - {'trips'}:
        if (sorted(payload[field]) if field == 'service_dates' else payload[field]) != (sorted(trusted_snapshot[field]) if field == 'service_dates' else trusted_snapshot[field]):
            raise ContractError(f'server snapshot mismatch: {field}')
    proposed = trip_map(payload['trips'], False)
    original = trip_map(trusted_snapshot['trips'], True)
    if proposed.keys() != original.keys():
        raise ContractError('added or deleted trips')
    selection = payload['selection_trip_ids']
    if type(selection) is not list or any(type(v) is not str for v in selection):
        raise ContractError('selection must contain trip ids')
    selection_set = set(selection)
    if len(selection) != len(selection_set) or not selection_set <= original.keys():
        raise ContractError('duplicate or unknown selection')
    for tid, trip in proposed.items():
        before = original[tid]
        for field in IDENTITY | {'departure', 'before_arrival'}:
            if trip[field] != before[field]:
                raise ContractError(f'trip identity or original time mismatch: {tid}')
        if trip['after_arrival'] < trip['before_arrival']:
            raise ContractError('decreases forbidden')
        if trip['after_arrival'] == trip['before_arrival']:
            continue
        if tid not in selection_set:
            raise ContractError('change outside browser selection')
        if trip['line_number'] == '277' and trip['direction'] == '1':
            raise ContractError('277/1 is frozen')
        evidence = before['evidence']
        if evidence['status'] != 'change_allowed':
            raise ContractError('only change_allowed may change')
        if trip['after_arrival'] != evidence['recommended_arrival']:
            raise ContractError('not the authoritative recommendation')
        if evidence['verified_day_type'] != payload['day_type'] or not set(payload['service_dates']) <= set(evidence['verified_service_dates']):
            raise ContractError('target-date/day evidence missing')
    # Stable trip and date ordering makes semantically identical resubmissions idempotent.
    p, s = copy.deepcopy(payload), copy.deepcopy(trusted_snapshot)
    for item in (p, s):
        item['trips'].sort(key=lambda t: t['id'])
        item['service_dates'].sort()
    p['selection_trip_ids'].sort()
    for trip in s['trips']:
        trip['evidence']['verified_service_dates'].sort()
    pj, sj = canonical(p), canonical(s)
    digest = hashlib.sha256(canonical({'payload': p, 'trusted_snapshot': s}).encode()).hexdigest()
    return ValidatedJob(payload['run_id'], digest, pj, sj)

def synthetic_fixture():
    """Two full trips; only the 24+ trip is selected. No production data."""
    snapshot = dict(schema_version=1, source=dict(dataset_id='synthetic-dataset', schedule_id='synthetic-schedule', revision='r1', sha256='a'*64), branch='synthetic', day_type='weekday', season='autumn', service_dates=['2026-09-30'], engine_version='engine-fixture-1', policy_version='policy-1', trips=[])
    for tid, line, departure, arrival in [('late', '100', 1500, 1540), ('frozen', '277', 600, 640)]:
        snapshot['trips'].append(dict(id=tid, operator='synthetic-operator', makat='10' + line, line_number=line, direction='1', alternative='0', departure=departure, before_arrival=arrival, evidence=dict(status='change_allowed', recommended_arrival=arrival+5, verified_service_dates=['2026-09-30'], verified_day_type='weekday', reason='synthetic test evidence')))
    payload = copy.deepcopy(snapshot)
    payload.update(run_id='synthetic-run-1', selection_trip_ids=['late'])
    for trip in payload['trips']:
        trip.pop('evidence')
        trip['after_arrival'] = trip['before_arrival'] + (5 if trip['id'] == 'late' else 0)
    return payload, snapshot
