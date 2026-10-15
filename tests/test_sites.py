from __future__ import annotations

import pytest

from gsc_mcp.errors import GuardError, SiteNotFoundError
from gsc_mcp.sites import Property, Resolution, SiteResolver, exact_match, match, same_url

DOMAIN = Property("sc-domain:example.com", "siteOwner")
WWW = Property("https://www.example.com/", "siteFullUser")
BLOG = Property("https://www.example.com/blog/", "siteFullUser")
SHOP = Property("https://shop.other.org/", "siteOwner")
ALL = [DOMAIN, WWW, BLOG, SHOP]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("sc-domain:example.com", DOMAIN),
        ("https://www.example.com/", WWW),
        ("https://www.example.com", WWW),
        ("HTTPS://WWW.EXAMPLE.COM/", WWW),
        ("  https://www.example.com/blog/  ", BLOG),
        ("example.com", DOMAIN),
        ("www.example.com", DOMAIN),
        ("shop.other.org", SHOP),
        ("https://shop.other.org", SHOP),
    ],
)
def test_match(requested: str, expected: Property) -> None:
    assert match(requested, ALL) == expected


@pytest.mark.parametrize(
    ("requested", "properties", "expected"),
    [
        # A domain property owns every URL under it, so it wins, as for bare domains.
        ("https://www.example.com/blog/post-1", ALL, DOMAIN),
        ("http://example.com/calendar", ALL, DOMAIN),
        # Otherwise the URL-prefix property with the longest matching path.
        ("https://www.example.com/blog/post-1", [WWW, BLOG, SHOP], BLOG),
        ("https://www.example.com/calendar?x=1", [WWW, BLOG, SHOP], WWW),
        ("www.example.com/calendar", [WWW, SHOP], WWW),
    ],
)
def test_page_url_resolves_to_the_property_that_owns_it(
    requested: str, properties: list[Property], expected: Property
) -> None:
    assert match(requested, properties) == expected


def test_page_url_outside_every_property_lists_them() -> None:
    with pytest.raises(SiteNotFoundError, match="Candidates") as raised:
        match("http://www.example.com/a", [WWW, SHOP])
    assert "'https://www.example.com/'" in str(raised.value)


def test_resolution_note_warns_that_a_page_url_means_the_whole_property() -> None:
    assert Resolution("sc-domain:example.com", "sc-domain:example.com").note is None
    site = Resolution("sc-domain:example.com", "example.com").note
    assert site == "Resolved 'example.com' to the property 'sc-domain:example.com'."
    page = Resolution("sc-domain:example.com", "https://example.com/calendar").note
    assert page is not None
    assert "whole property" in page


def test_ambiguous_url_prefixes() -> None:
    a = Property("https://example.net/", "siteOwner")
    b = Property("http://example.net/", "siteOwner")
    with pytest.raises(GuardError, match="several properties"):
        match("example.net", [a, b])


@pytest.mark.parametrize("requested", ["sc-domain:nope.com", "nope.com", "", "notexample.com"])
def test_not_found(requested: str) -> None:
    with pytest.raises(GuardError):
        match(requested, ALL)


def test_not_found_without_properties_explains_service_accounts() -> None:
    with pytest.raises(SiteNotFoundError, match="service account"):
        match("example.com", [])


@pytest.mark.parametrize(
    ("prop", "url", "inside"),
    [
        (DOMAIN, "https://example.com/a", True),
        (DOMAIN, "http://deep.sub.example.com/", True),
        (DOMAIN, "https://notexample.com/", False),
        (DOMAIN, "ftp://example.com/", False),
        (WWW, "https://www.example.com/a", True),
        (WWW, "HTTPS://WWW.EXAMPLE.COM/a", True),
        (WWW, "http://www.example.com/a", False),
        (BLOG, "https://www.example.com/blog/x", True),
        (BLOG, "https://www.example.com/Blog/x", False),
    ],
)
def test_contains(prop: Property, url: str, inside: bool) -> None:
    assert prop.contains(url) is inside


@pytest.mark.anyio
async def test_resolver_caches_and_refetches_on_a_miss() -> None:
    calls = 0
    available = [DOMAIN]

    async def fetch() -> list[Property]:
        nonlocal calls
        calls += 1
        return list(available)

    resolver = SiteResolver(fetch)
    assert (await resolver.resolve("example.com")).site_url == DOMAIN.site_url
    assert (await resolver.resolve("sc-domain:example.com")).changed is False
    assert calls == 1

    available.append(SHOP)  # a property added since the cache was filled
    assert (await resolver.resolve("shop.other.org")).site_url == SHOP.site_url
    assert calls == 2


@pytest.mark.anyio
async def test_resolver_expires_its_cache() -> None:
    now = 0.0
    calls = 0

    async def fetch() -> list[Property]:
        nonlocal calls
        calls += 1
        return [DOMAIN]

    resolver = SiteResolver(fetch, ttl_seconds=60, clock=lambda: now)
    await resolver.properties()
    now = 61.0
    await resolver.properties()
    assert calls == 2


@pytest.mark.parametrize(
    ("prop", "url", "inside"),
    [
        (WWW, "https://www.example.com:443/a", True),
        (WWW, "https://user@www.example.com/a", True),
        (WWW, "https://www.example.com./a", True),
        (WWW, "https://www.example.com:8443/a", False),
        (Property("https://bücher.example/", "siteOwner"), "https://xn--bcher-kva.example/x", True),
        (DOMAIN, "https://[example.com/b", False),
    ],
)
def test_contains_normalizes_urls(prop: Property, url: str, inside: bool) -> None:
    assert prop.contains(url) is inside


def test_bare_host_never_silently_narrows_to_a_subfolder() -> None:
    with pytest.raises(SiteNotFoundError, match="blog"):
        match("example.org", [Property("https://example.org/blog/", "siteOwner")])


def test_most_specific_domain_property_wins() -> None:
    shop = Property("sc-domain:shop.example.com", "siteOwner")
    assert match("a.shop.example.com", [DOMAIN, shop]) == shop


def test_malformed_url_is_a_guard_error() -> None:
    with pytest.raises(GuardError):
        match("https://[example.com/b", ALL)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("sc-domain:example.com", DOMAIN),
        ("https://www.example.com", WWW),
        ("HTTPS://WWW.EXAMPLE.COM/", WWW),
        ("example.com", None),
        ("www.example.com", None),
        ("http://www.example.com/", None),
    ],
)
def test_exact_match(requested: str, expected: Property | None) -> None:
    assert exact_match(requested, ALL) == expected


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("https://example.com", "https://example.com/", True),
        ("HTTPS://Example.com:443/a", "https://example.com/a", True),
        ("https://example.com/a", "https://example.com/a/", False),
        ("http://example.com/a", "https://example.com/a", False),
        ("https://example.com/a?x=1", "https://example.com/a", False),
        ("https://example.com/a#top", "https://example.com/a", True),
    ],
)
def test_same_url(a: str, b: str, same: bool) -> None:
    assert same_url(a, b) is same
