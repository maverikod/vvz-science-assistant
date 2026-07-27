"""Stable, command-oriented facade over astroquery and TAP services."""

from __future__ import annotations

from typing import Any

import astropy.units as u  # type: ignore[import-not-found]
from astropy.coordinates import SkyCoord  # type: ignore[import-not-found]
from astropy.table import Table  # type: ignore[import-not-found]


def _position(target: str | None, ra_deg: float | None, dec_deg: float | None) -> Any:
    """Return a target name or construct an ICRS coordinate.

    Args:
        target: Service-resolvable target name.
        ra_deg: ICRS right ascension in decimal degrees.
        dec_deg: ICRS declination in decimal degrees.

    Returns:
        The target string or an ICRS ``SkyCoord``.
    """
    if ra_deg is not None or dec_deg is not None:
        if ra_deg is None or dec_deg is None:
            raise ValueError("ra_deg and dec_deg must be supplied together")
        return SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    if target:
        return target
    raise ValueError("Provide target or both ra_deg and dec_deg")


def query_catalog(
    *,
    catalog: str,
    table: str | None,
    columns: list[str] | None,
    constraints: dict[str, Any] | None,
    row_limit: int,
) -> Any:
    """Query a VizieR catalog.

    Args:
        catalog: VizieR catalog identifier.
        table: Optional table selector reserved by the command facade.
        columns: Requested VizieR columns.
        constraints: Optional VizieR query constraints.
        row_limit: Maximum number of rows, or ``-1`` for no limit.

    Returns:
        The table collection returned by VizieR.
    """

    from astroquery.vizier import Vizier  # type: ignore[import-not-found]

    client = Vizier(columns=columns or ["*"], row_limit=row_limit)
    if constraints:
        result = client.query_constraints(catalog=catalog, **constraints)
    else:
        result = client.get_catalogs(catalog)
    if result is None or len(result) == 0:
        raise LookupError(f"VizieR returned no tables for {catalog}")
    return result


def query_object(
    *,
    service: str,
    target: str | None,
    ra_deg: float | None,
    dec_deg: float | None,
    radius_arcmin: float | None,
    catalog: str | None,
    columns: list[str] | None,
    row_limit: int,
) -> Any:
    """Query an object or cone region through the selected service.

    Args:
        service: Service name such as SIMBAD, NED, VizieR, HEASARC, or IRSA.
        target: Optional service-resolvable target name.
        ra_deg: Optional ICRS right ascension in decimal degrees.
        dec_deg: Optional ICRS declination in decimal degrees.
        radius_arcmin: Optional cone radius in arcminutes.
        catalog: Optional service catalog or mission identifier.
        columns: Optional result columns.
        row_limit: Maximum number of rows, or ``-1`` for no limit.

    Returns:
        The table or table collection returned by the selected service.
    """
    service = service.lower()
    position = _position(target, ra_deg, dec_deg)

    radius = radius_arcmin * u.arcmin if radius_arcmin is not None else None

    if service == "simbad":
        from astroquery.simbad import Simbad  # type: ignore[import-not-found]

        client = Simbad()
        if columns:
            client.add_votable_fields(*columns)
        if radius is not None:
            return client.query_region(position, radius=radius)
        if not target:
            raise ValueError("SIMBAD object query without radius requires target")
        return client.query_object(target)

    if service == "ned":
        from astroquery.ipac.ned import Ned  # type: ignore[import-not-found]

        if radius is not None:
            if isinstance(position, str):
                position = SkyCoord.from_name(position)
            return Ned.query_region(position, radius=radius)
        if not target:
            raise ValueError("NED object query without radius requires target")
        return Ned.query_object(target)

    if service == "vizier":
        from astroquery.vizier import Vizier  # type: ignore[import-not-found]

        client = Vizier(columns=columns or ["*"], row_limit=row_limit)
        if radius is not None:
            return client.query_region(position, radius=radius, catalog=catalog)
        if not target:
            raise ValueError("VizieR object query without radius requires target")
        return client.query_object(target, catalog=catalog)

    if service == "heasarc":
        from astroquery.heasarc import Heasarc  # type: ignore[import-not-found]

        if not catalog:
            raise ValueError("HEASARC requires catalog/mission")
        if radius is not None:
            return Heasarc.query_region(
                position=position,
                catalog=catalog,
                radius=radius,
                columns=columns,
                maxrec=None if row_limit < 0 else row_limit,
            )
        if not target:
            raise ValueError("HEASARC object query without radius requires target")
        return Heasarc.query_object(target, mission=catalog)

    if service == "irsa":
        from astroquery.ipac.irsa import Irsa  # type: ignore[import-not-found]

        if not catalog:
            raise ValueError("IRSA requires catalog")
        radius_value = radius if radius is not None else 10 * u.arcsec
        columns_value = ",".join(columns) if columns else "*"
        query = Irsa.query_region(
            position,
            catalog=catalog,
            spatial="Cone",
            radius=radius_value,
            columns=columns_value,
            get_query_payload=True,
        )
        maxrec = None if row_limit < 0 else row_limit
        return Irsa.query_tap(query=query, maxrec=maxrec).to_table()

    raise ValueError(f"Unsupported service: {service}")


def query_adql(*, service: str, query: str, tap_url: str | None) -> Table:
    """Run Gaia or custom TAP ADQL.

    Args:
        service: ``gaia`` or ``custom``.
        query: ADQL query string.
        tap_url: TAP endpoint required for a custom service.

    Returns:
        Query results as an Astropy table.
    """
    service = service.lower()
    if service == "gaia":
        from astroquery.gaia import Gaia  # type: ignore[import-not-found]

        return Gaia.launch_job_async(query, dump_to_file=False).get_results()
    if service == "custom":
        if not tap_url:
            raise ValueError("tap_url is required for custom TAP service")
        import pyvo  # type: ignore[import-not-found]

        return pyvo.dal.TAPService(tap_url).run_async(query).to_table()
    raise ValueError(f"Unsupported ADQL service: {service}")
