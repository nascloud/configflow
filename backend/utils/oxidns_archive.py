"""Complete OxiDNS package using the shared bounded rule materializer."""
import yaml
from backend.utils.mosdns_archive import MosdnsArchiveError, build_mosdns_zip, _path

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
    for plugin in document.get('plugins', []):
        args = plugin.get('args') or {}
        if not isinstance(args, dict):
            continue
        references = []
        # Binary geosite/geoip and custom file-based providers use singular file.
        if args.get('file'):
            references.append(args['file'])
        persistent = args.get('persistent', {})
        if isinstance(persistent, dict):
            refs = persistent.get('files', [])
            if not isinstance(refs, list):
                raise OxidnsArchiveError('OxiDNS 文件引用必须为列表')
            references.extend(refs)
        if any(_path(path) not in paths for path in references):
            raise OxidnsArchiveError('OxiDNS 配置引用了未打包的规则文件')
    try:
        return build_mosdns_zip(config, yaml_content, downloads, custom_files, base_url=base_url)
    except MosdnsArchiveError as exc:
        raise OxidnsArchiveError(str(exc).replace('MosDNS', 'OxiDNS')) from None
