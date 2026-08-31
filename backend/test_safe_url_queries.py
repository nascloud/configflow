from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from backend.common import profile_context
from backend.converters.mihomo import generate_mihomo_config, get_mihomo_provider_downloads
from backend.converters.mosdns import get_mosdns_ruleset_downloads
from backend.converters.surge import convert_proxy_group_to_surge, generate_surge_config


SPECIAL_TOKEN = "token /&?"


def _provider_config(profile_id=None):
    config = {
        "system_config": {
            "server_domain": "https://config.test",
            "config_token": SPECIAL_TOKEN,
            "rule_proxy_token": "internal /&?",
        },
        "subscriptions": [{"id": "sub-1", "name": "Primary", "enabled": True}],
        "subscription_aggregations": [{
            "id": "agg-1",
            "name": "Combined",
            "enabled": True,
            "subscriptions": [],
        }],
        "proxy_groups": [{
            "id": "group-1",
            "name": "Proxy",
            "type": "select",
            "enabled": True,
            "subscriptions": ["sub-1"],
            "aggregations": ["agg-1"],
        }],
        "nodes": [],
        "rule_configs": [],
    }
    if profile_id:
        config["profile_id"] = profile_id
    return config


def _assert_query(generated_url, **expected):
    assert parse_qs(urlsplit(generated_url).query, keep_blank_values=True) == {
        key: [value] for key, value in expected.items()
    }


def test_append_url_query_encodes_values_and_preserves_existing_query():
    url = profile_context.append_url_query(
        "https://config.test/path?existing=first%20value",
        {"token": SPECIAL_TOKEN, "format": "surge"},
    )

    _assert_query(url, existing="first value", token=SPECIAL_TOKEN, format="surge")


@pytest.mark.parametrize("profile_id", [None, "alpha"], ids=["legacy", "profile"])
def test_mihomo_provider_and_aggregation_urls_round_trip_special_token(profile_id):
    config = _provider_config(profile_id)

    generated = yaml.safe_load(generate_mihomo_config(config))
    generated_urls = [provider["url"] for provider in generated["proxy-providers"].values()]
    download_urls = [item["url"] for item in get_mihomo_provider_downloads(config)]

    assert len(generated_urls) == len(download_urls) == 2
    for url in generated_urls + download_urls:
        _assert_query(url, token=SPECIAL_TOKEN)


@pytest.mark.parametrize("profile_id", [None, "alpha"], ids=["legacy", "profile"])
def test_surge_managed_subscription_and_aggregation_urls_round_trip_special_token(profile_id):
    config = _provider_config(profile_id)
    group = config["proxy_groups"][0]

    managed_url = generate_surge_config(config).splitlines()[0].split()[1]
    group_line = convert_proxy_group_to_surge(group, config)
    policy_urls = [
        part.split(",", 1)[0]
        for part in group_line.split("policy-path = ")[1:]
    ]

    _assert_query(managed_url, token=SPECIAL_TOKEN)
    assert len(policy_urls) == 2
    for url in policy_urls:
        _assert_query(url, token=SPECIAL_TOKEN, format="surge")


@pytest.mark.parametrize("profile_id", [None, "alpha"], ids=["legacy", "profile"])
def test_mosdns_rule_proxy_url_round_trips_remote_url_and_special_token(profile_id):
    config = _provider_config(profile_id)
    remote_url = "https://rules.test/list?a=1&b=2"
    config["mosdns"] = {"direct_rulesets": ["rules-1"], "proxy_rulesets": []}
    config["rule_configs"] = [{
        "id": "rules-1",
        "name": "Rules",
        "itemType": "ruleset",
        "url": remote_url,
    }]

    generated_url = get_mosdns_ruleset_downloads(config)[0]["url"]

    _assert_query(generated_url, url=remote_url, token="internal /&?")
