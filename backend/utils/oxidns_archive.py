"""Complete OxiDNS package using the shared bounded rule materializer."""
import yaml
from backend.utils.mosdns_archive import MosdnsArchiveError, build_mosdns_zip, _path
from backend.common.oxidns_native import validate_resource_files
from backend.common.oxidns_settings import OxidnsSettingsError

OxidnsArchiveError = MosdnsArchiveError


def build_oxidns_zip(config, yaml_content, downloads, custom_files, *, base_url=''):
    # OxiDNS uses the same domain/IP text and authenticated conversion callback.
    # Dynamic provider/SQLite/cache state is intentionally not part of the bundle.
    paths = {_path(item.get('local_path')) for item in downloads}
    paths.update(_path(item.get('path')) for item in custom_files)
    if any(path.startswith('state/') for path in paths):
        raise OxidnsArchiveError('OxiDNS 运行数据 state/ 不能包含在配置包中')
    document = yaml.safe_load(yaml_content)
    if not isinstance(document, dict):
        raise OxidnsArchiveError('OxiDNS 配置格式无效')
    if document.get('include'):
        raise OxidnsArchiveError('OxiDNS 完整配置包不支持外部 include')
    try:
        validate_resource_files(document, paths)
        return build_mosdns_zip(config, yaml_content, downloads, custom_files, base_url=base_url, validate_references=False)
    except OxidnsSettingsError as exc:
        raise OxidnsArchiveError(str(exc)) from None
    except MosdnsArchiveError as exc:
        raise OxidnsArchiveError(str(exc).replace('MosDNS', 'OxiDNS')) from None
