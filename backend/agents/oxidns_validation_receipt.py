"""Server-owned evidence that the exact candidate passed the loaded rule set."""
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import tarfile

from .deployment_bundle import DeploymentPreparationError, deployment_content_fingerprint


@lru_cache(maxsize=1)
def rules_fingerprint():
    """Invalidate staged approvals on schema or validator implementation updates.

    Rules are immutable during a server process, just like the validator's
    cached schemas. Deploying a new build starts a new process/fingerprint.
    """
    root = Path(__file__).resolve().parents[1]
    paths = [*sorted((root / 'schemas' / 'oxidns').glob('*.json')),
             *(root / 'common' / name for name in (
                 'oxidns_validation.py', 'oxidns_global_constraints.py',
                 'oxidns_plugin_constraints.py', 'oxidns_reference_validation.py',
                 'oxidns_native.py'))]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def validate_bundle(agent_id, bundle):
    """Inspect final packaged bytes, never the possibly newer profile draft."""
    from backend.common.oxidns_native import load_native_yaml
    from backend.common.oxidns_validation import require_valid_document

    manifest = bundle.manifest
    fingerprint = deployment_content_fingerprint(manifest)
    if manifest['service_type'] != 'oxidns' or hashlib.sha256(bundle.archive).hexdigest() != bundle.sha256:
        raise DeploymentPreparationError('Invalid OxiDNS deployment archive digest')
    main = next(item for item in manifest['files'] if item['role'] == 'config')
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle.archive), mode='r:gz') as archive:
            packed_manifest = archive.extractfile('manifest.json')
            member = archive.getmember('files/' + manifest['config_path'])
            if (packed_manifest is None or json.load(packed_manifest) != manifest
                    or not member.isfile() or member.size != main['size']):
                raise DeploymentPreparationError('OxiDNS deployment manifest does not match its archive')
            content = archive.extractfile(member).read()
        if hashlib.sha256(content).hexdigest() != main['sha256']:
            raise DeploymentPreparationError('OxiDNS configuration digest does not match its manifest')
        document = load_native_yaml(content.decode('utf-8'))
    except (tarfile.TarError, KeyError, UnicodeError, json.JSONDecodeError, EOFError, OSError) as exc:
        raise DeploymentPreparationError('Invalid OxiDNS deployment archive') from exc
    # Every ConfigFlow publication expands includes while capturing its profile
    # snapshot. Refuse a raw include here rather than attest only its root file.
    if document.get('include'):
        raise DeploymentPreparationError('OxiDNS 发布配置必须先展开 include 后重新推送')
    require_valid_document(document)
    return {'version': 1, 'rules_sha256': rules_fingerprint(), 'agent_id': agent_id,
            'deployment_id': manifest['deployment_id'], 'profile_id': manifest['profile_id'],
            'service_type': 'oxidns', 'sha256': bundle.sha256, 'fingerprint': fingerprint}


def receipt_matches(agent, state):
    receipt = state.get('oxidns_validation_receipt')
    if not isinstance(receipt, dict):
        return False
    expected = {'version': 1, 'rules_sha256': rules_fingerprint(), 'agent_id': agent['id'],
                'deployment_id': state['deployment_id'], 'profile_id': state.get('profile_id'),
                'service_type': 'oxidns', 'sha256': state.get('sha256'),
                'fingerprint': (state.get('prepared_content') or {}).get('fingerprint')}
    return (agent.get('service_type') == 'oxidns' and state.get('service_type') == 'oxidns'
            and state.get('profile_id') == agent.get('profile_id', 'default')
            and bool(expected['sha256']) and bool(expected['fingerprint']) and receipt == expected)
