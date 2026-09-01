from pathlib import Path


def test_agent_profile_select_teleports_dropdown_to_document_body():
    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "Agents.vue").read_text(encoding="utf-8")
    marker = '配置 Profile'
    start = source.index(marker)
    select_start = source.index('<el-select', start)
    select_end = source.index('>', select_start)
    select_tag = source[select_start:select_end]

    assert ':teleported="false"' not in select_tag
    assert 'teleported="false"' not in select_tag


def test_agent_profile_dropdown_has_visible_teleported_option_text():
    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "Agents.vue").read_text(encoding="utf-8")
    marker = '配置 Profile'
    start = source.index(marker)
    select_start = source.index('<el-select', start)
    select_end = source.index('>', select_start)
    select_tag = source[select_start:select_end]

    assert 'popper-class="agent-profile-select-popper"' in select_tag
    assert ':global(.agent-profile-select-popper .el-select-dropdown__item)' in source
    assert ':global(.agent-profile-select-popper .el-select-dropdown__item span)' in source
