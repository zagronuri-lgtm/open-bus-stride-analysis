"""Constrained Claude Code advisory. Cannot execute Optibus or change job state."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile
import hashlib

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'digest': {'type': 'string'},
        'status': {'type': 'string', 'enum': ['advisory_only']},
        'summary_he': {'type': 'string'},
        'next_step_he': {'type': 'string'},
    },
    'required': ['digest', 'status', 'summary_he', 'next_step_he'],
}

def metadata_from_review_package(packet):
    """Validate a review envelope, then discard all free text before Claude sees it."""
    if type(packet) is not dict or packet.get('package_kind') != 'review_only' or packet.get('authorization') != 'none' or type(packet.get('schema_version')) is not int or packet['schema_version'] != 1:
        raise ValueError('review-only package required')
    trips, selected, candidates = (packet.get(k) for k in ('trips', 'selected_trip_ids', 'candidates'))
    if type(trips) is not list or not 0 < len(trips) <= 100000 or type(selected) is not list or type(candidates) is not list:
        raise ValueError('invalid review lists')
    ids = set()
    for trip in trips:
        if type(trip) is not dict or type(trip.get('id')) is not str or not trip['id'] or trip['id'] in ids:
            raise ValueError('invalid trip identity')
        ids.add(trip['id'])
        values = [trip.get(k) for k in ('departure','before_arrival','after_arrival')]
        if any(type(v) is not int or not 0 <= v <= 4320 for v in values) or values[0] > values[1] or values[1] != values[2]:
            raise ValueError('review package must preserve source times')
    if any(type(x) is not str for x in selected) or len(selected) != len(set(selected)) or not set(selected) <= ids:
        raise ValueError('invalid selection')
    candidate_ids = [c.get('trip_id') if type(c) is dict else None for c in candidates]
    if any(type(x) is not str for x in candidate_ids) or len(candidate_ids) != len(set(candidate_ids)) or set(candidate_ids) != set(selected):
        raise ValueError('candidate selection mismatch')
    digest = hashlib.sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    # No proposed times or source strings are sent to the model; it cannot adjudicate them.
    return {'digest': digest, 'trip_count': len(trips), 'changed_count': 0}

def review(metadata, executable, timeout=120):
    """Send only bounded, derived metadata; validate the response independently."""
    import re
    if set(metadata) != {'digest', 'trip_count', 'changed_count'}:
        raise ValueError('metadata fields invalid')
    if not isinstance(metadata['digest'], str) or not re.fullmatch('[0-9a-f]{64}', metadata['digest']):
        raise ValueError('digest invalid')
    if any(type(metadata[k]) is not int or not 0 <= metadata[k] <= 100000 for k in ('trip_count', 'changed_count')):
        raise ValueError('counts invalid')
    if metadata['changed_count'] > metadata['trip_count']:
        raise ValueError('changed_count invalid')
    prompt = ('כתוב בעברית תקציר חבילת הכנה בלבד. אין לך הרשאה או כלים לבצע פעולה. '
              'החבילה טרם הועלתה ולא הורצה באופטיבוס. הסבר שהשלב הבא הוא אימות עותק '
              'ותבנית יצוא בידי המפעיל לפני החלה. החזר digest ללא שינוי ו-status advisory_only. '
              'אין להציע זמני נסיעה או אישור סטטיסטי. הנתונים: ' + json.dumps(metadata))
    # Empty cwd/settings, no tools/MCP/skills/hooks: neither job text nor repo code runs.
    with tempfile.TemporaryDirectory(prefix='mad-zmanim-claude-') as cwd:
        cmd = [str(Path(executable).resolve()), '-p', '--tools', '',
               '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
               '--disable-slash-commands', '--no-chrome', '--setting-sources', '',
               '--settings', '{"disableAllHooks":true}', '--permission-mode', 'dontAsk',
               '--no-session-persistence', '--output-format', 'json',
               '--json-schema', json.dumps(SCHEMA),
               '--system-prompt', 'ענה בעברית בלבד. אתה מסכם חבילת הכנה, ואינך מפעיל מערכות.']
        proc = subprocess.run(cmd, input=prompt, text=True, cwd=cwd,
                              capture_output=True, timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError(f'Claude Code failed (exit {proc.returncode}); no retry')
    response = json.loads(proc.stdout)
    if not isinstance(response, dict):
        raise ValueError('response must be an object')
    if response.get('is_error'):
        raise RuntimeError('Claude Code reported failure; no retry')
    result = response.get('structured_output')
    if not isinstance(result, dict) or set(result) != set(SCHEMA['required']):
        raise ValueError('missing structured result')
    if result['digest'] != metadata['digest'] or result['status'] != 'advisory_only':
        raise ValueError('response identity/status mismatch')
    if any(not isinstance(result[k], str) or not 0 < len(result[k]) <= 2000
           for k in ('summary_he', 'next_step_he')):
        raise ValueError('response text invalid')
    # Model prose is retained for peer review only, never rendered as execution status.
    cost = response.get('total_cost_usd')
    cost = cost if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0 else None
    duration = response.get('duration_ms')
    duration = duration if type(duration) is int and duration >= 0 else None
    return {'untrusted_advisory': result, 'executed_optibus': False,
            'display_status_he': 'הוכנה חבילה בלבד. לא הועלתה ולא הורצה באופטיבוס.',
            'duration_ms': duration, 'cost_usd': cost}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='סיכום חבילת הכנה בלבד באמצעות Claude Code')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--metadata', type=Path)
    group.add_argument('--review-package', type=Path)
    parser.add_argument('--claude', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    metadata = metadata_from_review_package(json.loads(args.review_package.read_text())) if args.review_package else json.loads(args.metadata.read_text())
    result = review(metadata, args.claude)
    with args.output.open('x') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
