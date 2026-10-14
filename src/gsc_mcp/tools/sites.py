"""Sites: list and get properties; add and delete them when writes are enabled."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlsplit

from pydantic import Field

from gsc_mcp.errors import GuardError
from gsc_mcp.models import SiteInfo, SiteList, WriteResult
from gsc_mcp.sites import DOMAIN_PREFIX, Property
from gsc_mcp.tools._common import DESTRUCTIVE, READ_ONLY, WRITE, Deps, SiteUrl

if TYPE_CHECKING:
    from mcp.server import MCPServer


def site_info(prop: Property) -> SiteInfo:
    return SiteInfo(
        site_url=prop.site_url,
        permission_level=prop.permission_level,
        property_type="domain" if prop.is_domain else "url_prefix",
    )


def register(mcp: MCPServer, deps: Deps) -> None:
    @mcp.tool(title="List Search Console properties", annotations=READ_ONLY)
    async def list_sites() -> SiteList:
        """List every Search Console property this account can access, with its exact
        site_url and permission level. Call this first: every other tool needs a site_url."""
        properties = await deps.sites.properties(refresh=True)
        notes: list[str] = []
        if not properties:
            notes.append(
                "No properties. For a service account, add its email as a user on the "
                "property in Search Console > Settings > Users and permissions."
            )
        if any(p.permission_level == "siteUnverifiedUser" for p in properties):
            notes.append(
                "siteUnverifiedUser properties are not verified for this account; their data "
                "cannot be queried until ownership is verified in Search Console."
            )
        ordered = sorted(properties, key=lambda p: (not p.is_domain, p.site_url))
        return SiteList(sites=[site_info(p) for p in ordered], notes=notes)

    @mcp.tool(title="Get a Search Console property", annotations=READ_ONLY)
    async def get_site(site_url: SiteUrl) -> SiteInfo:
        """Get one property's exact site_url, type and this account's permission level."""
        resolution = await deps.sites.resolve(site_url)
        data = await deps.client.get_site(resolution.site_url)
        return site_info(
            Property(data.get("siteUrl", resolution.site_url), data.get("permissionLevel", ""))
        )

    if not deps.settings.enable_writes:
        return

    @mcp.tool(title="Add a Search Console property", annotations=WRITE)
    async def add_site(
        site_url: Annotated[
            str,
            Field(
                description="New property: 'sc-domain:example.com' or a URL prefix such as "
                "'https://www.example.com/'."
            ),
        ],
    ) -> WriteResult:
        """Add a property to this account. It starts unverified: ownership must be verified
        in the Search Console web interface before any data is available."""
        normalized = normalize_new_site(site_url)
        await deps.client.add_site(normalized)
        deps.sites.invalidate()
        return WriteResult(
            action="add_site",
            site_url=normalized,
            target=normalized,
            done=True,
            notes=["Verify ownership in Search Console before querying this property."],
        )

    @mcp.tool(title="Delete a Search Console property", annotations=DESTRUCTIVE)
    async def delete_site(
        site_url: Annotated[
            str, Field(description="The property's exact site_url, as listed by list_sites.")
        ],
    ) -> WriteResult:
        """Remove a property from this account. This removes access for this account only;
        it does not delete the site or other users' access. Confirm with the user first."""
        prop = await deps.sites.exact(site_url)
        await deps.client.delete_site(prop.site_url)
        deps.sites.invalidate()
        return WriteResult(
            action="delete_site",
            site_url=prop.site_url,
            target=prop.site_url,
            done=True,
        )


def normalize_new_site(value: str) -> str:
    value = value.strip()
    if value.startswith(DOMAIN_PREFIX):
        domain = value.removeprefix(DOMAIN_PREFIX).strip().lower()
        if not domain or "/" in domain or "." not in domain:
            raise GuardError(
                f"{value!r} is not a valid domain property; use 'sc-domain:example.com'."
            )
        return DOMAIN_PREFIX + domain
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise GuardError(
            f"{value!r} is not a valid property. Use 'sc-domain:example.com' or a URL prefix "
            "such as 'https://www.example.com/'."
        )
    return value if value.endswith("/") else value + "/"
