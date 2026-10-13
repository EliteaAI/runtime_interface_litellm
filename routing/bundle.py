"""Load an installed calibration snapshot selected by trusted deployment config."""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'calibration'


def snapshot(profile, revision):
    if not isinstance(profile, str) or not isinstance(revision, str):
        raise ValueError('Invalid calibration profile binding')
    try:
        manifest = json.loads((ROOT/'manifest.json').read_text())
        if manifest['revision'] != revision or profile not in manifest['profiles']:
            raise ValueError('Calibration profile is not installed')
        entry = manifest['profiles'][profile]
        filename = entry['file']
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.json'):
            raise ValueError('Invalid installed profile path')
        path = ROOT/filename
        if path.resolve().parent != ROOT.resolve():
            raise ValueError('Profile escaped installed bundle')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('Installed calibration snapshot changed')
        value = json.loads(raw)
        if not value.get('request_scope') or not value.get('forecast_validation_required'):
            raise ValueError('Installed snapshot lacks bounded validated scope')
        return value, entry['sha256']
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Calibration bundle is unavailable or malformed') from exc


def runtime_settings(settings, deployment, project_id):
    """Deployment owner opts a project in; request bodies cannot select a bundle.

    This never enables routing. Existing platform/project gates remain required.
    Changing a profile, snapshot or revision also revokes old signed pins.
    """
    result = copy.deepcopy(settings)
    deployment = deployment if isinstance(deployment, dict) else {}
    bindings = deployment.get('auto_routing_calibration_profiles', {})
    if not isinstance(bindings, dict):
        raise ValueError('Invalid deployment calibration bindings')
    selected = bindings.get(str(project_id))
    if selected is None:
        return result
    if (not isinstance(selected, dict) or not {'profile', 'revision'} <= set(selected)
            or set(selected) - {'profile', 'revision', 'coverage_fallback_variant', 'coverage_fallback_native', 'selection_policy'}):
        raise ValueError('Invalid project calibration binding')
    fallback = selected.get('coverage_fallback_variant')
    if fallback is not None and (not isinstance(fallback, str) or not fallback or len(fallback) > 256):
        raise ValueError('Invalid calibration coverage fallback')
    value, expected = snapshot(selected['profile'], selected['revision'])
    if fallback is not None and fallback not in value['variants']:
        raise ValueError('Coverage fallback is not in the installed profile')
    native = selected.get('coverage_fallback_native')
    if native is not None:
        from .fallback_config import validate
        native = validate(native)
        if fallback is not None:
            raise ValueError('Only one coverage fallback may be configured')
    selection_policy = selected.get('selection_policy')
    if selection_policy is not None:
        from .selection_policy import validate
        selection_policy = validate(selection_policy)
    result.update(calibration_profile=selected['profile'], calibration_revision=selected['revision'],
                  calibration_snapshot_sha256=expected, calibration_task_contract=value['request_scope']['id'],
                  calibration_coverage_fallback_variant=fallback,
                  calibration_coverage_fallback_native=native,
                  calibration_selection_policy=selection_policy)
    result['revision'] = hashlib.sha256(json.dumps({'gate': settings['revision'], 'binding': selected,
        'snapshot_sha256':expected}, sort_keys=True).encode()).hexdigest()
    return result
